import pytest
from conftest import WORKBOOK, course, faculty, lecture, practical, pref, problem, scholar, tutorial

from allocator.domain import IC_TARGET, Lock, SectionKind
from allocator.ingest import UploadFormat, detect_format, import_upload
from allocator.ingest.template import (
    TemplateError,
    _data_sheet,
    _instructions_sheet,
    read_template,
    write_template,
)
from allocator.ingest.template_spec import COURSES, FACULTY, SCHOLARS, SECTIONS, SHEETS
from allocator.ingest.xlsx import read_workbook
from allocator.ingest.xlsx_writer import write_workbook


def sample_problem():
    p = problem(
        [faculty("ana", psrn="H0001"), faculty("bo"), scholar("cy", supervisor="ana")],
        [
            course("ME F219", lecture("L1"), tutorial("T1"), practical("P1", scholar_min=1, scholar_max=2)),
            course("ME F220", lecture("L1"), lecture("L2")),
        ],
        [
            pref("ana", "ME F219", rank=1, taught_consecutively=True),
            pref("ana", "ME F220", rank=2),
            pref("bo", "ME F220", rank=1),
            pref("bo", "ME F219", SectionKind.TUTORIAL, rank=1),
            pref("cy", "ME F219", SectionKind.PRACTICAL, rank=1),
            pref("cy", "ME F219", SectionKind.TUTORIAL, rank=1),
        ],
        locks=[Lock(person_id="bo", course_code="ME F220", target="L2"),
               Lock(person_id="ana", course_code="ME F219", target=IC_TARGET)],
    )
    for c in p.courses:
        c.title = {"ME F219": "Manufacturing Processes", "ME F220": "Heat Transfer"}[c.code]
    return p


def upload(rows: dict[str, list[list]]) -> bytes:
    """Build a template workbook from raw rows, to test what people type."""
    sheets = [_instructions_sheet("ME")]
    for spec in SHEETS:
        sheets.append(_data_sheet(spec, rows.get(spec.name, [])))
    return write_workbook(sheets)


def test_blank_template_has_every_sheet_and_header():
    data = write_template(department="ME")
    sheets = read_workbook(data)
    for spec in SHEETS:
        headers = [sheets[spec.name].get(1, c) for c in range(1, len(spec.columns) + 1)]
        assert headers == spec.headers()
    assert sheets["Lists"].hidden
    assert detect_format(data) == UploadFormat.TEMPLATE


def test_blank_template_upload_is_rejected_with_reasons():
    with pytest.raises(TemplateError) as exc:
        read_template(write_template(department="ME"))
    messages = {str(i) for i in exc.value.issues}
    assert "Courses: No courses listed." in messages
    assert "Faculty: No faculty listed." in messages


def test_filled_template_reads_back_the_same_problem():
    original = sample_problem()
    back = read_template(write_template(original), department="ME")

    assert {c.code: [(s.id, s.faculty_max, s.scholar_min, s.scholar_max) for s in c.sections] for c in back.courses} == \
           {c.code: [(s.id, s.faculty_max, s.scholar_min, s.scholar_max) for s in c.sections] for c in original.courses}
    names = {p.id: p.name for p in back.people}
    got = {(names[p.person_id], p.course_code, p.kind, p.rank, p.taught_consecutively) for p in back.preferences}
    assert ("ANA", "ME F219", SectionKind.LECTURE, 1, True) in got
    assert ("BO", "ME F219", SectionKind.TUTORIAL, 1, None) in got
    assert ("CY", "ME F219", SectionKind.PRACTICAL, 1, None) in got
    locks = {(names[l.person_id], l.course_code, l.target) for l in back.locks}
    assert locks == {("BO", "ME F220", "L2"), ("ANA", "ME F219", IC_TARGET)}
    cy = next(p for p in back.people if p.name == "CY")
    assert names[cy.supervisor] == "ANA"
    assert next(p for p in back.people if p.name == "ANA").psrn == "H0001"


def test_mistakes_are_reported_by_sheet_row_and_column():
    blank_course = [None] * len(COURSES.columns)
    data = upload({
        COURSES.name: [
            ["ME F219", "Manufacturing Processes", 3, 1, 4, 180, "CDC", "maybe"] + blank_course[8:],
            ["Robotics", "No code"] + blank_course[2:],
        ],
        SECTIONS.name: [
            ["ME F219", "L1", 1, None, "Nobody Known"],
            ["ME F219", "X1"],
            ["ME F999", "L1"],
            ["ME F219", "T1", 1, "two"],
        ],
        FACULTY.name: [["Ana Rao"], ["Ana Rao"]],
        SCHOLARS.name: [["Cy", None, None, "Unknown Prof"]],
    })
    with pytest.raises(TemplateError) as exc:
        read_template(data)
    where = {(i.sheet, i.row, i.column) for i in exc.value.issues if i.severity == "error"}
    assert ("Courses", 2, "Service Course") in where
    assert ("Courses", 3, "Course Number") in where
    assert ("Sections", 2, "Pre-assigned Instructor 1") in where
    assert ("Sections", 3, "Section Number") in where
    assert ("Sections", 4, "Course Number") in where
    assert ("Sections", 5, "Scholars Needed") in where
    assert ("Faculty", 3, "Faculty Name") in where
    warnings = {(i.sheet, i.column) for i in exc.value.issues if i.severity == "warning"}
    assert ("Research Scholars", "Supervisor's Name") in warnings


def test_course_not_offered_is_a_warning_not_an_error():
    cols = [None] * len(FACULTY.columns)
    row = ["Ana Rao", *cols[1:]]
    row[FACULTY.headers().index("LECTURE Preference 1")] = "ME F414 - Fuel Cells"
    data = upload({
        COURSES.name: [["ME F219", "Manufacturing Processes", 3, 1]],
        SECTIONS.name: [["ME F219", "L1"]],
        FACULTY.name: [row],
    })
    p = read_template(data)
    assert not p.preferences
    assert any("ME F414" in n for n in p.notes)


@pytest.mark.skipif(not WORKBOOK.exists(), reason="ME workbook not in data/raw")
def test_legacy_workbook_survives_a_trip_through_the_template(settings):
    from allocator.solver import solve

    legacy, fmt = import_upload(WORKBOOK.read_bytes(), department="ME")
    assert fmt == UploadFormat.LEGACY
    back = read_template(write_template(legacy), department="ME")
    assert len(back.people) == len(legacy.people)
    assert sum(len(c.sections) for c in back.courses) == sum(len(c.sections) for c in legacy.courses)
    offered = {c.code for c in legacy.courses}
    form = {(p.person_id, p.course_code, p.kind) for p in legacy.preferences
            if p.source != "additional" and p.course_code in offered}
    assert form <= {(p.person_id, p.course_code, p.kind) for p in back.preferences}
    settings.time_limit_seconds, settings.num_workers = 20, 8
    assert solve(back, settings).allocation is not None

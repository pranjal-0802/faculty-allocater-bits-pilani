import pytest
from conftest import WORKBOOK, course, faculty, lecture, practical, pref, problem, tutorial

from allocator.domain import Lock, SectionKind
from allocator.ingest import UploadFormat, detect_format, import_upload
from allocator.ingest.template import (
    TemplateError,
    _data_sheet,
    _instructions_sheet,
    read_template,
    write_template,
)
from allocator.ingest.template_spec import COURSES, FACULTY, SECTIONS, SHEETS
from allocator.ingest.xlsx import read_workbook
from allocator.ingest.xlsx_writer import write_workbook


def sample_problem():
    p = problem(
        [faculty("ana", psrn="H0001"), faculty("bo"), faculty("cy", teaching_percent=70)],
        [
            course("ME F219", lecture("L1"), tutorial("T1"), practical("P1", faculty_min=2, faculty_max=2)),
            course("ME F220", lecture("L1"), lecture("L2")),
        ],
        [
            pref("ana", "ME F219", rank=1, consecutive_count=3, taught_consecutively=True),
            pref("ana", "ME F220", rank=2),
            pref("bo", "ME F220", rank=1),
            pref("bo", "ME F219", SectionKind.TUTORIAL, rank=1),
            pref("cy", "ME F219", SectionKind.PRACTICAL, rank=1),
            pref("cy", "ME F219", SectionKind.TUTORIAL, rank=1, taught_consecutively=True),
        ],
        locks=[Lock(person_id="bo", course_code="ME F220", target="L2")],
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

    assert {c.code: [(s.id, s.faculty_max) for s in c.sections] for c in back.courses} == \
           {c.code: [(s.id, s.faculty_max) for s in c.sections] for c in original.courses}
    names = {p.id: p.name for p in back.people}
    got = {(names[p.person_id], p.course_code, p.kind, p.rank, p.consecutive_count, p.taught_consecutively)
           for p in back.preferences}
    assert ("ANA", "ME F219", SectionKind.LECTURE, 1, 3, True) in got
    assert ("BO", "ME F219", SectionKind.TUTORIAL, 1, None, None) in got
    assert ("CY", "ME F219", SectionKind.PRACTICAL, 1, None, None) in got
    assert ("CY", "ME F219", SectionKind.TUTORIAL, 1, None, True) in got  # Yes, no count
    locks = {(names[l.person_id], l.course_code, l.target) for l in back.locks}
    assert locks == {("BO", "ME F220", "L2")}
    assert [(c.code, c.category.value, c.level.value) for c in back.courses] == [
        ("ME F219", "CDC", "FD"), ("ME F220", "CDC", "FD")]
    assert next(p for p in back.people if p.name == "CY").teaching_percent == 70
    assert next(p for p in back.people if p.name == "ANA").psrn == "H0001"


def test_mistakes_are_reported_by_sheet_row_and_column():
    blank_course = [None] * len(COURSES.columns)
    data = upload({
        COURSES.name: [
            ["ME F219", "Manufacturing Processes", 3, 1, 4, 180, "CDC", None, "maybe"] + blank_course[9:],
            ["Robotics", "No code"] + blank_course[2:],
            ["ME G515", "CFD", 3, 2, 5, 20, "HD"] + blank_course[7:],
        ],
        SECTIONS.name: [
            ["ME F219", "L1", 1, "Nobody Known"],
            ["ME F219", "X1"],
            ["ME F999", "L1"],
            ["ME F219", "T1", "two"],
        ],
        FACULTY.name: [["Ana Rao"], ["Ana Rao"], consecutive_row("Bo Das", "ME F219", "often")],
    })
    with pytest.raises(TemplateError) as exc:
        read_template(data)
    where = {(i.sheet, i.row, i.column) for i in exc.value.issues if i.severity == "error"}
    assert ("Courses", 2, "Service Course") in where
    assert ("Courses", 3, "Course Number") in where
    assert ("Courses", 4, "Category") in where  # HD is a level, not a category
    assert ("Sections", 2, "Pre-assigned Instructor 1") in where
    assert ("Sections", 3, "Section Number") in where
    assert ("Sections", 4, "Course Number") in where
    assert ("Sections", 5, "Faculty Needed") in where
    assert ("Faculty", 3, "Faculty Name") in where
    assert ("Faculty", 4, "LECTURE Taken Consecutively 1") in where


def consecutive_row(name, code, value):
    row = [name] + [None] * (len(FACULTY.columns) - 1)
    row[FACULTY.headers().index("LECTURE Preference 1")] = code
    row[FACULTY.headers().index("LECTURE Taken Consecutively 1")] = value
    return row


def test_taken_consecutively_reads_counts_and_old_yes_no():
    data = upload({
        COURSES.name: [["ME F219", "Manufacturing Processes", 3, 1, None, None, "CDC"],
                       ["ME F220", "Heat Transfer", 3, 0, None, None, "CDC"],
                       ["ME F221", "Mechanisms", 3, 0, None, None, "CDC"]],
        SECTIONS.name: [["ME F219", "L1"], ["ME F220", "L1"], ["ME F221", "L1"]],
        FACULTY.name: [consecutive_row("Ana", "ME F219", 3), consecutive_row("Bo", "ME F220", "Yes"),
                       consecutive_row("Cy", "ME F221", "No")],
    })
    p = read_template(data)
    got = {p.course_code: (p.consecutive_count, p.taught_consecutively) for p in p.preferences}
    assert got == {"ME F219": (3, True), "ME F220": (None, True), "ME F221": (0, False)}


def test_old_template_with_a_scholar_sheet_is_accepted_with_a_warning():
    from allocator.ingest.template_spec import SheetSpec, Column

    old = SheetSpec(name="Research Scholars", purpose="", columns=[Column("Name", "text")])
    sheets = [_instructions_sheet("ME")]
    rows = {COURSES.name: [["ME F219", "Manufacturing Processes", 3, 1, None, None, "CDC"]], SECTIONS.name: [["ME F219", "L1"]],
            FACULTY.name: [["Ana"]]}
    for spec in SHEETS:
        sheets.append(_data_sheet(spec, rows[spec.name]))
    sheets.append(_data_sheet(old, [["Cy"]]))
    p = read_template(write_workbook(sheets))
    assert [x.name for x in p.people] == ["Ana"]
    assert any("Research scholars are not part" in n for n in p.notes)


def test_course_not_offered_is_a_warning_not_an_error():
    cols = [None] * len(FACULTY.columns)
    row = ["Ana Rao", *cols[1:]]
    row[FACULTY.headers().index("LECTURE Preference 1")] = "ME F414 - Fuel Cells"
    data = upload({
        COURSES.name: [["ME F219", "Manufacturing Processes", 3, 1, None, None, "CDC"]],
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

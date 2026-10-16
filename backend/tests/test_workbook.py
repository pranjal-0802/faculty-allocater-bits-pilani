"""Import the real Mechanical workbook, when it is present."""

import pytest
from conftest import WORKBOOK

from allocator.domain import IC_TARGET, SectionKind
from allocator.ingest.workbook import ImportOptions, import_workbook

pytestmark = pytest.mark.skipif(not WORKBOOK.exists(), reason="ME workbook not in data/raw")


@pytest.fixture(scope="module")
def me():
    return import_workbook(str(WORKBOOK), ImportOptions(department="ME"))


def test_people_and_courses(me):
    # Faculty only: the research-scholar form is not read, and scholars on
    # the section sheet are left out (and do not match faculty by initials).
    assert 35 <= len(me.people) <= 40
    assert all(p.id.startswith("f-") for p in me.people)
    assert all(a.person_id.startswith("f-") for a in me.reference.assignments)
    assert any("not department faculty" in n for n in me.notes)
    codes = {c.code for c in me.courses}
    assert {"BITS F103", "BITS F111", "ME F219", "ME F220", "ME F341"} <= codes


def test_section_structure(me):
    f219 = me.course("ME F219")
    ids = {s.id for s in f219.sections}
    assert {"L1", "L2", "T1", "T2", "P1", "P2", "P3", "P4"} <= ids
    # P1 and P2 were staffed by scholars only: listed, with no faculty needed.
    assert [(s.faculty_min, s.faculty_max) for s in f219.sections if s.id in ("P1", "P2")] == [(0, 0), (0, 0)]
    f103 = me.course("BITS F103")
    assert len([s for s in f103.sections if s.kind == SectionKind.PRACTICAL]) == 10
    assert f103.service


def test_faculty_preferences_are_read_with_rank_and_kind(me):
    abhishek = next(p for p in me.people if p.name.startswith("Abhishek"))
    prefs = {(x.course_code, x.kind, x.rank) for x in me.preferences if x.person_id == abhishek.id}
    assert ("ME F221", SectionKind.LECTURE, 1) in prefs
    assert ("ME F221", SectionKind.TUTORIAL, 1) in prefs


def test_consecutive_yes_no_from_the_old_form(me):
    yes = [p for p in me.preferences if p.taught_consecutively]
    no = [p for p in me.preferences if p.taught_consecutively is False]
    assert yes and all(p.consecutive_count is None for p in yes)
    assert no and all(p.consecutive_count == 0 for p in no)


def test_hand_allocation_breaks_the_one_cdc_rule(me):
    from allocator.evaluation import report
    from allocator.settings import SolverSettings

    # Two first-degree pairs; with HD CDCs counted, also ME F219 + MSE G512.
    assert report(me, me.reference, SolverSettings()).cdc_breaches == 2
    assert report(me, me.reference, SolverSettings(hd_cdcs_count=True)).cdc_breaches == 3


def test_cdc_and_elective_from_the_department_sheets(me):
    cat = {c.code: (c.category.value, c.level.value) for c in me.courses}
    assert cat["ME F219"] == ("CDC", "FD")
    assert cat["ME F423"] == ("ELECTIVE", "FD")
    assert cat["ME G611"] == ("CDC", "HD")  # CDC on both IC summary and programme sheet
    assert cat["ME G515"] == ("CDC", "HD")  # CDC for M.E. Thermal, elective elsewhere
    assert cat["ME G538"] == ("ELECTIVE", "HD")
    assert cat["ME F266"][0] == "PROJECT"





def test_reference_is_kept_apart_and_matches_the_sheet(me):
    ref = {(a.person_id, a.course_code, a.target) for a in me.reference.assignments}
    nsk = next(p for p in me.people if p.name.startswith("N Suresh"))
    assert (nsk.id, "ME F219", "L1") in ref
    ics = [a for a in me.reference.assignments if a.target == IC_TARGET]
    assert len({a.course_code for a in ics}) == len(ics)  # one IC per course


def test_wilp_becomes_external_load(me):
    brajesh = next(p for p in me.people if p.name.startswith("Brajesh"))
    assert brajesh.external_load == 3

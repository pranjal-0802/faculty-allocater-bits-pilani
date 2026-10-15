"""Import the real Mechanical workbook, when it is present."""

import pytest
from conftest import WORKBOOK

from allocator.domain import IC_TARGET, PersonKind, SectionKind
from allocator.ingest.workbook import ImportOptions, import_workbook

pytestmark = pytest.mark.skipif(not WORKBOOK.exists(), reason="ME workbook not in data/raw")


@pytest.fixture(scope="module")
def me():
    return import_workbook(str(WORKBOOK), ImportOptions(department="ME"))


def test_people_and_courses(me):
    faculty = [p for p in me.people if p.kind == PersonKind.FACULTY]
    scholars = [p for p in me.people if p.kind == PersonKind.SCHOLAR]
    assert len(faculty) >= 35
    assert len(scholars) >= 60
    codes = {c.code for c in me.courses}
    assert {"BITS F103", "BITS F111", "ME F219", "ME F220", "ME F341"} <= codes


def test_section_structure(me):
    f219 = me.course("ME F219")
    ids = {s.id for s in f219.sections}
    assert {"L1", "L2", "T1", "T2", "P1", "P4"} <= ids
    f103 = me.course("BITS F103")
    assert len([s for s in f103.sections if s.kind == SectionKind.PRACTICAL]) == 10
    assert f103.service


def test_faculty_preferences_are_read_with_rank_and_kind(me):
    abhishek = next(p for p in me.people if p.name.startswith("Abhishek"))
    prefs = {(x.course_code, x.kind, x.rank) for x in me.preferences if x.person_id == abhishek.id}
    assert ("ME F221", SectionKind.LECTURE, 1) in prefs
    assert ("ME F221", SectionKind.TUTORIAL, 1) in prefs


def test_reference_is_kept_apart_and_matches_the_sheet(me):
    ref = {(a.person_id, a.course_code, a.target) for a in me.reference.assignments}
    nsk = next(p for p in me.people if p.name.startswith("N Suresh"))
    assert (nsk.id, "ME F219", "L1") in ref
    ics = [a for a in me.reference.assignments if a.target == IC_TARGET]
    assert len({a.course_code for a in ics}) == len(ics)  # one IC per course


def test_wilp_becomes_external_load(me):
    brajesh = next(p for p in me.people if p.name.startswith("Brajesh"))
    assert brajesh.external_load == 3

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from allocator.domain import (  # noqa: E402
    Course,
    Person,
    PersonKind,
    Preference,
    Problem,
    Section,
    SectionKind,
)
from allocator.settings import SolverSettings  # noqa: E402

WORKBOOK = ROOT / "data" / "raw" / "Course allocation sem-II, 2025-26.xlsx"


def faculty(pid: str, **kw) -> Person:
    return Person(id=pid, name=pid.upper(), kind=PersonKind.FACULTY, **kw)


def scholar(pid: str, **kw) -> Person:
    return Person(id=pid, name=pid.upper(), kind=PersonKind.SCHOLAR, **kw)


def lecture(sid: str = "L1", **kw) -> Section:
    return Section(id=sid, kind=SectionKind.LECTURE, **kw)


def tutorial(sid: str = "T1", **kw) -> Section:
    kw.setdefault("scholar_min", 1)
    kw.setdefault("scholar_max", 1)
    return Section(id=sid, kind=SectionKind.TUTORIAL, **kw)


def practical(sid: str = "P1", **kw) -> Section:
    kw.setdefault("scholar_min", 1)
    kw.setdefault("scholar_max", 1)
    return Section(id=sid, kind=SectionKind.PRACTICAL, **kw)


def course(code: str, *sections: Section, **kw) -> Course:
    kw.setdefault("lecture_units", 3)
    kw.setdefault("lab_units", 1)
    return Course(code=code, title=code, sections=list(sections), **kw)


def pref(pid: str, code: str, kind: SectionKind | None = SectionKind.LECTURE, rank: int = 1, **kw) -> Preference:
    return Preference(person_id=pid, course_code=code, kind=kind, rank=rank, **kw)


def problem(people, courses, prefs=(), **kw) -> Problem:
    return Problem(id="t", name="t", people=list(people), courses=list(courses), preferences=list(prefs), **kw)


@pytest.fixture
def settings() -> SolverSettings:
    # Small problems solve to optimality instantly; one worker keeps tests
    # deterministic.
    return SolverSettings(time_limit_seconds=10, num_workers=1)

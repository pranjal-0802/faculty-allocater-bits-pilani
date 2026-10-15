"""
Teaching load.

Faculty load follows the rule the timetable app already uses for its course
load report (ttauto server/controllers/courseLoadController.js):

* a lecture section counts the course's lecture units, divided among the
  faculty who co-teach it;
* a practical counts the course's lab units, undivided;
* a tutorial counts the course's tutorial units (normally 1), undivided,
  since tutorials always have a scholar on them;
* being IC counts the course's `ic_units` (only projects carry any).

Scholar load is in contact hours per week, which is how the research-scholar
form states it: a tutorial is an hour, a lab is two hours per lab unit.

A "HALF" note on an assignment halves it: the person covers the section for
half the semester. The solver never produces one, but manual allocations do.

The solver encodes the same rule as linear constraints; this module is the
plain-Python version used to report and compare allocations, and the solver's
tests check that the two agree.
"""

from __future__ import annotations

from collections import defaultdict

from allocator.domain import (
    IC_TARGET,
    Assignment,
    Course,
    Person,
    PersonKind,
    Problem,
    SectionKind,
)
from allocator.settings import LoadBand, SolverSettings


def section_kind(course: Course, section_id: str) -> SectionKind | None:
    section = next((s for s in course.sections if s.id == section_id), None)
    return section.kind if section else None


def assignment_load(
    person: Person,
    course: Course,
    target: str,
    faculty_on_section: int,
    settings: SolverSettings,
    note: str = "",
) -> float:
    """Load one assignment adds to one person."""
    share = 0.5 if "HALF" in note.upper() else 1.0

    if target == IC_TARGET:
        return course.ic_units if person.kind == PersonKind.FACULTY else 0.0

    kind = section_kind(course, target)
    if kind is None:
        return 0.0

    if person.kind == PersonKind.FACULTY:
        if kind == SectionKind.LECTURE:
            return course.lecture_units / max(1, faculty_on_section) * share
        if kind == SectionKind.PRACTICAL:
            return course.lab_units * share
        return course.tutorial_units * share

    if kind == SectionKind.PRACTICAL:
        return course.lab_units * settings.scholar_hours_per_lab_unit * share
    if kind == SectionKind.TUTORIAL:
        return settings.scholar_tutorial_hours * share
    return 0.0


def annotate_loads(
    problem: Problem, assignments: list[Assignment], settings: SolverSettings
) -> list[Assignment]:
    """Return copies of `assignments` with `load` filled in."""
    people = {p.id: p for p in problem.people}
    courses = {c.code: c for c in problem.courses}

    faculty_count: dict[tuple[str, str], int] = defaultdict(int)
    for a in assignments:
        person = people.get(a.person_id)
        if person and person.kind == PersonKind.FACULTY and a.target != IC_TARGET:
            faculty_count[(a.course_code, a.target)] += 1

    out = []
    for a in assignments:
        person = people.get(a.person_id)
        course = courses.get(a.course_code)
        load = 0.0
        if person and course:
            load = assignment_load(
                person,
                course,
                a.target,
                faculty_count[(a.course_code, a.target)],
                settings,
                a.note,
            )
        out.append(a.model_copy(update={"load": round(load, 3)}))
    return out


def person_loads(
    problem: Problem, assignments: list[Assignment], settings: SolverSettings
) -> dict[str, float]:
    """Total load per person, including external load."""
    totals = {p.id: p.external_load for p in problem.people}
    for a in annotate_loads(problem, assignments, settings):
        if a.person_id in totals:
            totals[a.person_id] += a.load
    return {k: round(v, 3) for k, v in totals.items()}


def load_band(person: Person, settings: SolverSettings) -> LoadBand:
    """
    The load range a person should end up in.

    A per-person override wins; then a band configured for the person's
    teaching percentage; then the default for their kind.
    """
    default = (
        settings.faculty_load
        if person.kind == PersonKind.FACULTY
        else settings.scholar_load
    )
    band = default
    if person.teaching_percent is not None:
        key = _percent_key(person.teaching_percent)
        band = settings.teaching_percent_bands.get(key, default)
    return LoadBand(
        min=person.load_min if person.load_min is not None else band.min,
        max=person.load_max if person.load_max is not None else band.max,
    )


def _percent_key(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)

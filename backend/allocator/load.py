"""
Teaching load, and each person's target.

Load is scored exactly as the timetable app's course-load report does it
(ttauto server/controllers/courseLoadController.js):

* a lecture section counts the course's lecture units, divided among the
  faculty on it;
* a practical counts the course's lab units, undivided;
* a tutorial counts 1 (2 for the app's "double tutorial" courses, which the
  course's `tutorial_units` carries), undivided;
* a WILP course counts 3 units, divided among its faculty; it arrives as
  fixed `external_load`.

A "HALF" note on an assignment halves it: the person covers the section for
half the semester. The solver never produces one, but manual allocations do.

Targets follow "Teaching Allocation" on the department form. The standard
load (the baseline) is the department's total teaching units divided by its
number of faculty, the average load. Someone at 50% should carry the
baseline; everyone else scales with their percentage, so 70% asks for 1.4
times the baseline and 40% for 0.8 times. A blank Teaching Allocation counts
as 50%. The target is a ceiling to stay under; the band runs from a little
below it up to it.

The solver encodes the load rule as linear constraints; this module is the
plain-Python version used to report and compare allocations, and the solver's
tests check that the two agree.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from pydantic import BaseModel, Field

from allocator.domain import Assignment, Course, Problem, SectionKind
from allocator.settings import BaselineMode, LoadBand, SolverSettings


def section_kind(course: Course, section_id: str) -> SectionKind | None:
    section = course.section(section_id)
    return section.kind if section else None


def assignment_load(course: Course, target: str, faculty_on_section: int, note: str = "") -> float:
    """Load one assignment adds to the person holding it."""
    share = 0.5 if "HALF" in note.upper() else 1.0
    kind = section_kind(course, target)
    if kind == SectionKind.LECTURE:
        return course.lecture_units / max(1, faculty_on_section) * share
    if kind == SectionKind.PRACTICAL:
        return course.lab_units * share
    if kind == SectionKind.TUTORIAL:
        return course.tutorial_units * share
    return 0.0


def annotate_loads(problem: Problem, assignments: list[Assignment]) -> list[Assignment]:
    """Return copies of `assignments` with `load` filled in."""
    people = {p.id for p in problem.people}
    courses = {c.code: c for c in problem.courses}

    on_section: dict[tuple[str, str], int] = defaultdict(int)
    for a in assignments:
        if a.person_id in people:
            on_section[(a.course_code, a.target)] += 1

    out = []
    for a in assignments:
        course = courses.get(a.course_code)
        load = 0.0
        if a.person_id in people and course:
            load = assignment_load(course, a.target, on_section[(a.course_code, a.target)], a.note)
        out.append(a.model_copy(update={"load": round(load, 3)}))
    return out


def person_loads(problem: Problem, assignments: list[Assignment]) -> dict[str, float]:
    """Total load per person, including external load."""
    totals = {p.id: p.external_load for p in problem.people}
    for a in annotate_loads(problem, assignments):
        if a.person_id in totals:
            totals[a.person_id] += a.load
    return {k: round(v, 3) for k, v in totals.items()}


# ---- targets ------------------------------------------------------------------


def department_work(problem: Problem) -> float:
    """
    Units of teaching the problem asks for, at each section's minimum
    staffing, plus everyone's external load (WILP).
    """
    total = sum(p.external_load for p in problem.people)
    for course in problem.courses:
        for s in course.sections:
            if s.faculty_min <= 0:
                continue
            if s.kind == SectionKind.LECTURE:
                total += course.lecture_units  # shared or not, the lecture's units in all
            elif s.kind == SectionKind.PRACTICAL:
                total += course.lab_units * s.faculty_min
            else:
                total += course.tutorial_units * s.faculty_min
    return round(total, 3)


class LoadTargets(BaseModel):
    #: Load at `baseline_percent` Teaching Allocation (the standard load).
    baseline: float
    #: "set" (from the settings), "average" or "fair_share".
    baseline_source: str
    baseline_percent: float
    #: Total units of work in the problem, external load included.
    work: float
    #: Sum of everyone's target; below `work` when most opt for under 50%.
    target_total: float = 0.0
    #: Teaching Allocation assumed for people who left it blank.
    default_percent: float
    #: Teaching Allocation used for each person.
    percent: dict[str, float] = Field(default_factory=dict)
    #: People whose Teaching Allocation was assumed.
    assumed: list[str] = Field(default_factory=list)
    #: Each person's band; its max is their target.
    bands: dict[str, LoadBand] = Field(default_factory=dict)


def load_targets(problem: Problem, settings: SolverSettings) -> LoadTargets:
    stated = [p.teaching_percent for p in problem.people if p.teaching_percent is not None]
    default = settings.default_teaching_percent
    if default is None:
        default = statistics.median(stated) if stated else settings.baseline_percent

    percent = {
        p.id: default if p.teaching_percent is None else p.teaching_percent for p in problem.people
    }
    work = department_work(problem)

    if settings.load_baseline is not None:
        baseline, source = settings.load_baseline, "set"
    elif settings.baseline_mode == BaselineMode.AVERAGE:
        baseline = work / len(problem.people) if problem.people else 0.0
        source = "average"
    else:
        # People with a fixed ceiling take exactly that; the rest share what
        # is left in proportion to their percentage.
        fixed = sum(p.load_max for p in problem.people if p.load_max is not None)
        weight = sum(
            percent[p.id] / settings.baseline_percent
            for p in problem.people
            if p.load_max is None
        )
        baseline = max(0.0, work - fixed) / weight if weight else 0.0
        source = "fair_share"

    bands = {}
    for p in problem.people:
        target = baseline * percent[p.id] / settings.baseline_percent
        if settings.target_rounding > 0:
            step = settings.target_rounding
            target = round(target / step) * step
        hi = p.load_max if p.load_max is not None else target
        lo = p.load_min if p.load_min is not None else max(0.0, hi - settings.load_tolerance_below)
        bands[p.id] = LoadBand(min=round(min(lo, hi), 2), max=round(hi, 2))

    return LoadTargets(
        baseline=round(baseline, 2),
        baseline_source=source,
        baseline_percent=settings.baseline_percent,
        work=work,
        target_total=round(sum(b.max for b in bands.values()), 2),
        default_percent=default,
        percent=percent,
        assumed=[p.id for p in problem.people if p.teaching_percent is None],
        bands=bands,
    )

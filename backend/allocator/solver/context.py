"""
Solver context: the CP-SAT model, its decision variables, and lookups shared
by the constraint and objective builders.

Decision variables
------------------
x[person, course, section]   person staffs that section            (bool)
unfilled[course, section]    faculty the section still lacks        (int)

Instructors-in-charge are not decided here: the department picks one from
each course's instructors after the allocation.

Derived variables (all exact, defined by equalities, not one-sided bounds, so
any objective term may use them in either direction):

teaches[person, course, kind]  person holds at least one section of that kind
in_course[person, course]      person holds any section of the course
shared[course, section]        a lecture section has two faculty on it
load[person]                   total load, scaled by SCALE to stay integral

The rotation rule is applied here, by never creating a variable for a section
a person is rotated out of (unless a lock requires it).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from allocator.domain import (
    Course,
    LockMode,
    Person,
    Preference,
    Problem,
    Section,
    SectionKind,
)
from allocator.load import LoadTargets, load_targets
from allocator.rules import RotationBar, is_barred, rotation_bars
from allocator.settings import EligibilityMode, SolverSettings

#: Loads are fractional (a shared 3-unit lecture is 1.5 each), CP-SAT is
#: integral. Every load is multiplied by this before it enters the model.
SCALE = 10


def scaled(value: float) -> int:
    return int(round(value * SCALE))


XKey = tuple[str, str, str]  # person id, course code, section id


@dataclass
class ModelContext:
    model: cp_model.CpModel
    problem: Problem
    settings: SolverSettings

    people: dict[str, Person] = field(default_factory=dict)
    courses: dict[str, Course] = field(default_factory=dict)
    #: Preferences grouped by (person, course).
    prefs: dict[tuple[str, str], list[Preference]] = field(default_factory=dict)
    targets: LoadTargets | None = None
    bars: dict[tuple[str, str], list[RotationBar]] = field(default_factory=dict)

    x: dict[XKey, cp_model.IntVar] = field(default_factory=dict)
    unfilled: dict[tuple[str, str], cp_model.IntVar] = field(default_factory=dict)
    teaches: dict[tuple[str, str, SectionKind], cp_model.IntVar] = field(default_factory=dict)
    in_course: dict[tuple[str, str], cp_model.IntVar] = field(default_factory=dict)
    shared: dict[tuple[str, str], cp_model.IntVar] = field(default_factory=dict)
    load: dict[str, cp_model.IntVar] = field(default_factory=dict)

    # ---- lookups ------------------------------------------------------------

    def section_vars(self, course: Course, section: Section) -> list[tuple[str, cp_model.IntVar]]:
        return [
            (pid, var)
            for (pid, code, sid), var in self.x.items()
            if code == course.code and sid == section.id
        ]

    def listed(self, person_id: str, course_code: str) -> bool:
        return (person_id, course_code) in self.prefs

    def locked(self, person_id: str, course_code: str, target: str) -> LockMode | None:
        for lock in self.problem.locks:
            if (
                lock.person_id == person_id
                and lock.course_code == course_code
                and lock.target == target
            ):
                return lock.mode
        return None

    def band(self, person_id: str):
        return self.targets.bands[person_id]


def build_context(problem: Problem, settings: SolverSettings) -> ModelContext:
    """Create the model and every decision and derived variable."""
    ctx = ModelContext(model=cp_model.CpModel(), problem=problem, settings=settings)
    ctx.people = {p.id: p for p in problem.people}
    ctx.courses = {c.code: c for c in problem.courses}
    ctx.targets = load_targets(problem, settings)
    ctx.bars = rotation_bars(problem, settings)

    grouped: dict[tuple[str, str], list[Preference]] = defaultdict(list)
    for pref in problem.preferences:
        if pref.person_id in ctx.people and pref.course_code in ctx.courses:
            grouped[(pref.person_id, pref.course_code)].append(pref)
    ctx.prefs = dict(grouped)

    _create_section_vars(ctx)
    _create_unfilled_vars(ctx)
    _create_teaches_vars(ctx)
    _create_in_course_vars(ctx)
    _create_shared_vars(ctx)
    return ctx


def _allowed(ctx: ModelContext, person: Person, course: Course, section: Section) -> bool:
    """Whether the hard rules let the person hold the section at all."""
    mode = ctx.locked(person.id, course.code, section.id)
    if mode == LockMode.FORBID:
        return False
    if mode == LockMode.REQUIRE:
        # A lock is the department's own decision and wins over rotation.
        return True
    return not is_barred(ctx.bars, person.id, course.code, section.kind)


def _volunteer(ctx: ModelContext, person: Person, course: Course, section: Section) -> bool:
    """Whether a person is a natural candidate (asked, duty or locked)."""
    if ctx.locked(person.id, course.code, section.id) == LockMode.REQUIRE:
        return True
    if ctx.settings.eligibility == EligibilityMode.PENALISED:
        return True
    return course.service or ctx.listed(person.id, course.code)


def _create_section_vars(ctx: ModelContext) -> None:
    shortlist = ctx.settings.eligibility == EligibilityMode.SHORTLIST
    for course in ctx.problem.courses:
        for section in course.sections:
            slots = section.faculty_max
            if slots <= 0:
                continue
            allowed = [p for p in ctx.problem.people if _allowed(ctx, p, course, section)]
            chosen = [p for p in allowed if _volunteer(ctx, p, course, section)]
            if shortlist and len(chosen) < slots + ctx.settings.shortlist_spare:
                # Too few volunteers: open the section to everyone the hard
                # rules allow, and let the unlisted penalty decide.
                chosen = allowed
            for person in chosen:
                ctx.x[(person.id, course.code, section.id)] = ctx.model.NewBoolVar(
                    f"x|{person.id}|{course.code}|{section.id}"
                )


def _create_unfilled_vars(ctx: ModelContext) -> None:
    for course in ctx.problem.courses:
        for section in course.sections:
            if section.faculty_min > 0:
                ctx.unfilled[(course.code, section.id)] = ctx.model.NewIntVar(
                    0, section.faculty_min, f"unfilled|{course.code}|{section.id}"
                )


def _create_teaches_vars(ctx: ModelContext) -> None:
    grouped: dict[tuple[str, str, SectionKind], list] = defaultdict(list)
    for (pid, code, sid), var in ctx.x.items():
        grouped[(pid, code, ctx.courses[code].section(sid).kind)].append(var)
    for key, vars_ in grouped.items():
        if len(vars_) == 1:
            ctx.teaches[key] = vars_[0]
            continue
        flag = ctx.model.NewBoolVar(f"teaches|{key[0]}|{key[1]}|{key[2].value}")
        ctx.model.AddMaxEquality(flag, vars_)
        ctx.teaches[key] = flag


def _create_in_course_vars(ctx: ModelContext) -> None:
    grouped: dict[tuple[str, str], list] = defaultdict(list)
    for (pid, code, _), var in ctx.teaches.items():
        grouped[(pid, code)].append(var)
    for key, vars_ in grouped.items():
        if len(vars_) == 1:
            ctx.in_course[key] = vars_[0]
            continue
        flag = ctx.model.NewBoolVar(f"in_course|{key[0]}|{key[1]}")
        ctx.model.AddMaxEquality(flag, vars_)
        ctx.in_course[key] = flag


def _create_shared_vars(ctx: ModelContext) -> None:
    """
    Flag co-taught lecture sections, so the lecture's units can be split.

    Only lecture load is divided among co-teachers, and only sections that
    allow two or more faculty can be co-taught.
    """
    for course in ctx.problem.courses:
        for section in course.sections:
            if section.kind != SectionKind.LECTURE or section.faculty_max < 2:
                continue
            vars_ = [v for _, v in ctx.section_vars(course, section)]
            if len(vars_) < 2:
                continue
            flag = ctx.model.NewBoolVar(f"shared|{course.code}|{section.id}")
            ctx.model.Add(sum(vars_) >= 2).OnlyEnforceIf(flag)
            ctx.model.Add(sum(vars_) <= 1).OnlyEnforceIf(flag.Not())
            ctx.shared[(course.code, section.id)] = flag

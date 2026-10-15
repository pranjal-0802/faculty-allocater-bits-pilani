"""
Solver context: the CP-SAT model, its decision variables, and lookups shared
by the constraint and objective builders.

Decision variables
------------------
x[person, course, section]   person staffs that section            (bool)
ic[person, course]           person is the course's IC             (bool)
unfilled[course, section, k] staff of kind k the section still lacks (int)

Derived variables (all exact, defined by equalities, not one-sided bounds, so
any objective term may use them in either direction):

teaches[person, course, kind]  person holds at least one section of that kind
in_course[person, course]      person holds any role in the course
shared[course, section]        a lecture section has two faculty on it
load[person]                   total load, scaled by SCALE to stay integral
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from allocator.domain import (
    IC_TARGET,
    Course,
    LockMode,
    Person,
    PersonKind,
    Preference,
    Problem,
    Section,
    SectionKind,
)
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

    x: dict[XKey, cp_model.IntVar] = field(default_factory=dict)
    ic: dict[tuple[str, str], cp_model.IntVar] = field(default_factory=dict)
    unfilled: dict[tuple[str, str, PersonKind], cp_model.IntVar] = field(
        default_factory=dict
    )
    teaches: dict[tuple[str, str, SectionKind], cp_model.IntVar] = field(
        default_factory=dict
    )
    in_course: dict[tuple[str, str], cp_model.IntVar] = field(default_factory=dict)
    shared: dict[tuple[str, str], cp_model.IntVar] = field(default_factory=dict)
    load: dict[str, cp_model.IntVar] = field(default_factory=dict)

    #: Courses that need an IC but have nobody who could be one.
    ic_without_candidates: list[str] = field(default_factory=list)

    # ---- lookups ------------------------------------------------------------

    def section_vars(
        self, course: Course, section: Section, kind: PersonKind
    ) -> list[tuple[str, cp_model.IntVar]]:
        return [
            (pid, var)
            for (pid, code, sid), var in self.x.items()
            if code == course.code
            and sid == section.id
            and self.people[pid].kind == kind
        ]

    def person_vars(self, person_id: str) -> list[tuple[XKey, cp_model.IntVar]]:
        return [(k, v) for k, v in self.x.items() if k[0] == person_id]

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


def build_context(problem: Problem, settings: SolverSettings) -> ModelContext:
    """Create the model and every decision and derived variable."""
    ctx = ModelContext(model=cp_model.CpModel(), problem=problem, settings=settings)
    ctx.people = {p.id: p for p in problem.people}
    ctx.courses = {c.code: c for c in problem.courses}

    grouped: dict[tuple[str, str], list[Preference]] = defaultdict(list)
    for pref in problem.preferences:
        if pref.person_id in ctx.people and pref.course_code in ctx.courses:
            grouped[(pref.person_id, pref.course_code)].append(pref)
    ctx.prefs = dict(grouped)

    _create_section_vars(ctx)
    _create_ic_vars(ctx)
    _create_unfilled_vars(ctx)
    _create_teaches_vars(ctx)
    _create_in_course_vars(ctx)
    _create_shared_vars(ctx)
    return ctx


def _eligible(ctx: ModelContext, person: Person, course: Course, target: str) -> bool:
    """Whether a person is a volunteer (or forced) candidate for a target."""
    mode = ctx.locked(person.id, course.code, target)
    if mode == LockMode.FORBID:
        return False
    if mode == LockMode.REQUIRE:
        return True
    if ctx.settings.eligibility == EligibilityMode.PENALISED:
        return True
    return course.service or ctx.listed(person.id, course.code)


def _create_section_vars(ctx: ModelContext) -> None:
    shortlist = ctx.settings.eligibility == EligibilityMode.SHORTLIST
    for course in ctx.problem.courses:
        for section in course.sections:
            for kind, slots in (
                (PersonKind.FACULTY, section.faculty_max),
                (PersonKind.SCHOLAR, section.scholar_max),
            ):
                if slots <= 0:
                    continue
                people = [p for p in ctx.problem.people if p.kind == kind]
                chosen = [p for p in people if _eligible(ctx, p, course, section.id)]
                if shortlist and len(chosen) < slots + ctx.settings.shortlist_spare:
                    # Too few volunteers: open the section to everyone not
                    # explicitly forbidden, and let the penalty decide.
                    chosen = [
                        p
                        for p in people
                        if ctx.locked(p.id, course.code, section.id) != LockMode.FORBID
                    ]
                for person in chosen:
                    ctx.x[(person.id, course.code, section.id)] = ctx.model.NewBoolVar(
                        f"x|{person.id}|{course.code}|{section.id}"
                    )


def _create_ic_vars(ctx: ModelContext) -> None:
    for course in ctx.problem.courses:
        if not course.ic_required:
            continue
        has_lectures = any(s.kind == SectionKind.LECTURE for s in course.sections)
        for person in ctx.problem.people:
            if person.kind != PersonKind.FACULTY:
                continue
            lock = ctx.locked(person.id, course.code, IC_TARGET)
            if lock == LockMode.FORBID:
                continue
            can_lecture = any(
                (person.id, course.code, s.id) in ctx.x
                for s in course.sections
                if s.kind == SectionKind.LECTURE
            )
            if ctx.settings.ic_must_lecture and has_lectures:
                # Whoever may lecture may be IC; nobody else, bar a lock.
                if not (can_lecture or lock == LockMode.REQUIRE):
                    continue
            elif not _eligible(ctx, person, course, IC_TARGET):
                continue
            ctx.ic[(person.id, course.code)] = ctx.model.NewBoolVar(
                f"ic|{person.id}|{course.code}"
            )
        if not any(code == course.code for _, code in ctx.ic):
            ctx.ic_without_candidates.append(course.code)


def _create_unfilled_vars(ctx: ModelContext) -> None:
    for course in ctx.problem.courses:
        for section in course.sections:
            for kind, need in (
                (PersonKind.FACULTY, section.faculty_min),
                (PersonKind.SCHOLAR, section.scholar_min),
            ):
                if need > 0:
                    ctx.unfilled[(course.code, section.id, kind)] = ctx.model.NewIntVar(
                        0, need, f"unfilled|{course.code}|{section.id}|{kind.value}"
                    )


def _create_teaches_vars(ctx: ModelContext) -> None:
    grouped: dict[tuple[str, str, SectionKind], list] = defaultdict(list)
    for (pid, code, sid), var in ctx.x.items():
        kind = next(s.kind for s in ctx.courses[code].sections if s.id == sid)
        grouped[(pid, code, kind)].append(var)
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
    for key, var in ctx.ic.items():
        grouped[key].append(var)
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
            vars_ = [v for _, v in ctx.section_vars(course, section, PersonKind.FACULTY)]
            if len(vars_) < 2:
                continue
            flag = ctx.model.NewBoolVar(f"shared|{course.code}|{section.id}")
            ctx.model.Add(sum(vars_) >= 2).OnlyEnforceIf(flag)
            ctx.model.Add(sum(vars_) <= 1).OnlyEnforceIf(flag.Not())
            ctx.shared[(course.code, section.id)] = flag

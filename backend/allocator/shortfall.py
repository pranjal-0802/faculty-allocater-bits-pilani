"""
Courses with fewer volunteers than places.

When a course needs two lecturers and only one person asked to lecture it,
the solver still staffs it (by pulling in someone who did not ask, or by
giving the one volunteer both sections), but the department should know
before it trusts the result. These are flagged separately from ordinary
warnings, before the run and again in its result with who ended up taking
the places.

A volunteer for a kind of section is someone who listed the course for that
kind (or for any part of it). For tutorials, the course's would-be lecturers
count too, since lecturers take their course's tutorials. People rotated out
of the course are not volunteers, nor are people locked away from it; people
pre-assigned to it are. Service
courses are department duties staffed from everyone, so they are never short.
"""

from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel, Field

from allocator.domain import Allocation, LockMode, Problem, SectionKind
from allocator.rules import is_barred, rotation_bars
from allocator.settings import SolverSettings

KIND_NAMES = {
    SectionKind.LECTURE: "lecture",
    SectionKind.TUTORIAL: "tutorial",
    SectionKind.PRACTICAL: "practical",
}


class Shortfall(BaseModel):
    course_code: str
    title: str = ""
    #: Kind of section short of volunteers.
    kind: SectionKind
    #: Faculty places to fill (sum of each section's minimum).
    places: int
    sections: list[str] = Field(default_factory=list)
    volunteers: list[str] = Field(default_factory=list)
    #: People who asked for it but are rotated out.
    rotated_out: list[str] = Field(default_factory=list)
    #: "none" (nobody volunteered) or "short" (fewer than the places).
    severity: str
    message: str
    #: After a run: who took each place and whether they had asked.
    outcome: list[str] = Field(default_factory=list)


def find_shortfalls(problem: Problem, settings: SolverSettings) -> list[Shortfall]:
    people = {p.id: p for p in problem.people}
    bars = rotation_bars(problem, settings)

    listed: dict[tuple[str, SectionKind | None], set[str]] = defaultdict(set)
    for pref in problem.preferences:
        if pref.person_id in people:
            listed[(pref.course_code, pref.kind)].add(pref.person_id)

    locked: dict[tuple[str, str], set[str]] = defaultdict(set)
    forbidden: dict[tuple[str, str], set[str]] = defaultdict(set)
    for lock in problem.locks:
        target = forbidden if lock.mode == LockMode.FORBID else locked
        target[(lock.course_code, lock.target)].add(lock.person_id)

    out: list[Shortfall] = []
    for course in problem.courses:
        if course.service:
            continue
        any_part = listed.get((course.code, None), set())
        by_kind: dict[SectionKind, list] = defaultdict(list)
        for s in course.sections:
            if s.faculty_min > 0:
                by_kind[s.kind].append(s)

        groups = [
            (kind, sum(s.faculty_min for s in secs), [s.id for s in secs])
            for kind, secs in by_kind.items()
        ]

        for kind, places, section_ids in groups:
            asked = set(any_part) | listed.get((course.code, kind), set())
            if kind == SectionKind.TUTORIAL:
                asked |= listed.get((course.code, SectionKind.LECTURE), set())
            for sid in section_ids:
                asked -= forbidden.get((course.code, sid), set())

            rotated = {pid for pid in asked if is_barred(bars, pid, course.code, kind)}
            volunteers = asked - rotated
            for sid in section_ids:
                volunteers |= locked.get((course.code, sid), set())

            if len(volunteers) >= places:
                continue
            what = KIND_NAMES[kind]
            names = sorted(people[p].name for p in volunteers if p in people)
            if not volunteers:
                severity = "none"
                message = f"{course.code}: nobody volunteered for the {what} ({places} place{'s' * (places != 1)})."
            else:
                severity = "short"
                message = (
                    f"{course.code}: {places} {what} place{'s' * (places != 1)} "
                    f"but only {len(volunteers)} volunteer{'s' * (len(volunteers) != 1)} "
                    f"({', '.join(names)})."
                )
            if rotated:
                message += f" {len(rotated)} more asked but are rotated out."
            out.append(Shortfall(
                course_code=course.code,
                title=course.title,
                kind=kind,
                places=places,
                sections=section_ids,
                volunteers=names,
                rotated_out=sorted(people[p].name for p in rotated if p in people),
                severity=severity,
                message=message,
            ))
    return out


def with_outcome(
    shortfalls: list[Shortfall], problem: Problem, allocation: Allocation
) -> list[Shortfall]:
    """Copies of `shortfalls` saying who took the places in `allocation`."""
    from allocator.evaluation import classify

    people = {p.id: p for p in problem.people}
    courses = {c.code: c for c in problem.courses}
    held: dict[tuple[str, str], list[str]] = defaultdict(list)
    for a in allocation.assignments:
        held[(a.course_code, a.target)].append(a.person_id)
    unfilled = {(u.course_code, u.section_id): u.missing for u in allocation.unfilled}

    out = []
    for sf in shortfalls:
        course = courses.get(sf.course_code)
        lines = []
        for sid in sf.sections:
            for pid in held.get((sf.course_code, sid), []):
                how = classify(problem, pid, course, sf.kind) if course else ""
                label = {"cross_kind": "asked for another part", "unlisted": "did not ask"}.get(
                    how, "asked" if how.startswith("rank") else how
                )
                lines.append(f"{sid}: {people[pid].name if pid in people else pid} ({label})")
            if unfilled.get((sf.course_code, sid)):
                lines.append(f"{sid}: {unfilled[(sf.course_code, sid)]} place(s) unfilled")
        out.append(sf.model_copy(update={"outcome": lines}))
    return out

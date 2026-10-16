"""
The admin's hard rules, in one place.

The solver encodes them as constraints; validation, the shortfall check and
the report use these same functions to explain them, so the four never
disagree about who is barred from what.

Rotation
    A course taken `rotation_limit` (3) semesters in a row may not be taken
    again. The count comes from the "Taken Consecutively" column of the
    preference form, so it is known only for courses a person listed.

One CDC
    If two courses are assigned to a faculty member they cannot both be CDCs,
    so nobody holds more than one CDC. Any section counts (lecture,
    tutorial, practical). Service courses (BITS F103, BITS F111: first-year
    duties shared out across the department) do not, and higher-degree CDCs
    count unless `hd_cdcs_count` is off.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from allocator.domain import Allocation, Course, CourseLevel, Preference, Problem, SectionKind
from allocator.settings import RotationScope, SolverSettings


def counts_as_cdc(course: Course, settings: SolverSettings) -> bool:
    """Whether holding the course uses up a person's one CDC."""
    if not course.counts_as_cdc:
        return False
    return settings.hd_cdcs_count or course.level == CourseLevel.FD


@dataclass(frozen=True)
class RotationBar:
    person_id: str
    course_code: str
    #: Kind barred; None bars the whole course.
    kind: SectionKind | None
    count: int | None
    preference: Preference

    def covers(self, target_kind: SectionKind | None) -> bool:
        return self.kind is None or self.kind == target_kind

    def describe(self, names: dict[str, str]) -> str:
        what = self.course_code if self.kind is None else f"{self.course_code} ({self.kind.name.lower()})"
        times = f"{self.count} semesters" if self.count is not None else "consecutively (Yes, no count)"
        return f"{names.get(self.person_id, self.person_id)} has taught {what} {times}: rotated out."


def rotation_bars(problem: Problem, settings: SolverSettings) -> dict[tuple[str, str], list[RotationBar]]:
    """(person, course) -> the bars on it."""
    out: dict[tuple[str, str], list[RotationBar]] = defaultdict(list)
    limit = settings.rotation_limit
    if limit <= 0:
        return {}
    for pref in problem.preferences:
        reached = pref.consecutive_count is not None and pref.consecutive_count >= limit
        if (
            not reached
            and pref.consecutive_count is None
            and pref.taught_consecutively
            and settings.rotation_yes_without_count
        ):
            reached = True
        if not reached:
            continue
        kind = None if settings.rotation_scope == RotationScope.COURSE else pref.kind
        out[(pref.person_id, pref.course_code)].append(
            RotationBar(pref.person_id, pref.course_code, kind, pref.consecutive_count, pref)
        )
    return dict(out)


def is_barred(
    bars: dict[tuple[str, str], list[RotationBar]],
    person_id: str,
    course_code: str,
    kind: SectionKind | None,
) -> bool:
    return any(b.covers(kind) for b in bars.get((person_id, course_code), []))


def uncounted_consecutive(problem: Problem, settings: SolverSettings) -> list[Preference]:
    """
    Preferences marked "taken consecutively: Yes" with no count. The rotation
    rule cannot tell whether these have reached the limit, so they are
    flagged for the department to fill in.
    """
    if settings.rotation_limit <= 0 or settings.rotation_yes_without_count:
        return []
    return [
        p for p in problem.preferences
        if p.taught_consecutively and p.consecutive_count is None
    ]


def cdc_holdings(
    problem: Problem, allocation: Allocation, settings: SolverSettings
) -> dict[str, list[str]]:
    """Person -> the CDCs they hold any role in, for people holding two or more."""
    courses = {c.code: c for c in problem.courses}
    held: dict[str, set[str]] = defaultdict(set)
    for a in allocation.assignments:
        course = courses.get(a.course_code)
        if course and counts_as_cdc(course, settings):
            held[a.person_id].add(course.code)
    return {pid: sorted(codes) for pid, codes in held.items() if len(codes) > 1}


def rotation_breaches(
    problem: Problem, allocation: Allocation, settings: SolverSettings
) -> list[tuple[RotationBar, str]]:
    """Assignments that break the rotation rule, with the target they hold."""
    bars = rotation_bars(problem, settings)
    courses = {c.code: c for c in problem.courses}
    out = []
    for a in allocation.assignments:
        course = courses.get(a.course_code)
        if not course:
            continue
        section = course.section(a.target)
        for bar in bars.get((a.person_id, a.course_code), []):
            if section and bar.covers(section.kind):
                out.append((bar, a.target))
                break
    return out

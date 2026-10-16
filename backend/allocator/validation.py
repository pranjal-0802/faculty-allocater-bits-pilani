"""
Problem validation.

Returns human-readable warnings rather than raising: a problem with a typo in
one preference should still solve, and the user should see what was ignored.
Courses short of volunteers are reported separately (shortfall.py).
"""

from __future__ import annotations

from collections import Counter, defaultdict

from allocator.domain import CourseCategory, LockMode, Problem, SectionKind
from allocator.rules import counts_as_cdc, is_barred, rotation_bars, uncounted_consecutive
from allocator.settings import SolverSettings


def validate_problem(problem: Problem, settings: SolverSettings | None = None) -> list[str]:
    settings = settings or SolverSettings()
    warnings: list[str] = []
    people = {p.id: p for p in problem.people}
    courses = {c.code: c for c in problem.courses}

    for pid, n in Counter(p.id for p in problem.people).items():
        if n > 1:
            warnings.append(f"Person id {pid!r} appears {n} times; only one is used.")
    for code, n in Counter(c.code for c in problem.courses).items():
        if n > 1:
            warnings.append(f"Course {code} appears {n} times; only one is used.")

    unknown_people = {p.person_id for p in problem.preferences if p.person_id not in people}
    unknown_courses = {p.course_code for p in problem.preferences if p.course_code not in courses}
    if unknown_people:
        warnings.append(
            f"{len(unknown_people)} preference(s) name people not in the problem: "
            + ", ".join(sorted(unknown_people)[:10])
        )
    if unknown_courses:
        warnings.append(
            f"Preferences name {len(unknown_courses)} course(s) not being staffed "
            f"this round; those preferences are ignored: "
            + ", ".join(sorted(unknown_courses)[:15])
        )

    for course in problem.courses:
        if not course.sections and course.category != CourseCategory.PROJECT:
            warnings.append(f"{course.code} has no sections to staff.")
        for s in course.sections:
            if s.faculty_min > s.faculty_max:
                warnings.append(f"{course.code} {s.id}: the minimum exceeds the maximum.")
            if s.kind == SectionKind.LECTURE and s.faculty_max > 2:
                warnings.append(
                    f"{course.code} {s.id}: more than two co-teachers; lecture load is "
                    f"only split two ways in the model."
                )

    for lock in problem.locks:
        if lock.person_id not in people or lock.course_code not in courses:
            warnings.append(
                f"Lock on {lock.person_id} / {lock.course_code} names an unknown "
                f"person or course."
            )
        elif courses[lock.course_code].section(lock.target) is None:
            warnings.append(f"Lock on {lock.course_code} {lock.target}: the course has no such section.")

    warnings += _rule_warnings(problem, settings)

    if not problem.people:
        warnings.append("The problem has no faculty.")
    return warnings


def _rule_warnings(problem: Problem, settings: SolverSettings) -> list[str]:
    out: list[str] = []
    people = {p.id: p for p in problem.people}
    courses = {c.code: c for c in problem.courses}
    name = lambda pid: people[pid].name if pid in people else pid  # noqa: E731

    # Locks that override a hard rule.
    bars = rotation_bars(problem, settings)
    cdc_locks: dict[str, set[str]] = defaultdict(set)
    for lock in problem.locks:
        course = courses.get(lock.course_code)
        if lock.mode != LockMode.REQUIRE or not course:
            continue
        section = course.section(lock.target)
        if section and is_barred(bars, lock.person_id, course.code, section.kind):
            out.append(
                f"{name(lock.person_id)} is pre-assigned to {course.code} {lock.target} although "
                f"rotated out of it (taught {settings.rotation_limit}+ semesters running); the lock wins."
            )
        if counts_as_cdc(course, settings):
            cdc_locks[lock.person_id].add(course.code)
    if settings.one_cdc_per_faculty:
        for pid, codes in cdc_locks.items():
            if len(codes) > 1:
                out.append(
                    f"{name(pid)} is pre-assigned to {len(codes)} CDCs "
                    f"({', '.join(sorted(codes))}); that wins over the one-CDC rule."
                )

    # "Taken consecutively: Yes" without a count.
    uncounted = [p for p in uncounted_consecutive(problem, settings) if p.course_code in courses]
    if uncounted:
        sample = ", ".join(f"{name(p.person_id)} ({p.course_code})" for p in uncounted[:6])
        out.append(
            f"{len(uncounted)} preference(s) say 'taken consecutively: Yes' without saying how many "
            f"semesters, so the rotation rule ({settings.rotation_limit} in a row) cannot be applied "
            f"to them: {sample}{'...' if len(uncounted) > 6 else ''}. Ask for the number of semesters."
        )

    # Teaching Allocation left blank.
    blank = [p.name for p in problem.people if p.teaching_percent is None and p.load_max is None]
    if blank and len(blank) < len(problem.people):
        out.append(
            f"{len(blank)} faculty left Teaching Allocation blank and are taken at the "
            f"department's usual figure: {', '.join(blank[:8])}{'...' if len(blank) > 8 else ''}."
        )
    return out

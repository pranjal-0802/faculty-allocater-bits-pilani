"""
Problem validation.

Returns human-readable warnings rather than raising: a problem with a typo in
one preference should still solve, and the user should see what was ignored.
"""

from __future__ import annotations

from collections import Counter

from allocator.domain import CourseCategory, PersonKind, Problem, SectionKind


def validate_problem(problem: Problem) -> list[str]:
    warnings: list[str] = []
    people = {p.id for p in problem.people}
    courses = {c.code for c in problem.courses}

    for pid, n in Counter(p.id for p in problem.people).items():
        if n > 1:
            warnings.append(f"Person id {pid!r} appears {n} times; only one is used.")
    for code, n in Counter(c.code for c in problem.courses).items():
        if n > 1:
            warnings.append(f"Course {code} appears {n} times; only one is used.")

    unknown_people = {p.person_id for p in problem.preferences if p.person_id not in people}
    unknown_courses = {
        p.course_code for p in problem.preferences if p.course_code not in courses
    }
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
            if s.faculty_min > s.faculty_max or s.scholar_min > s.scholar_max:
                warnings.append(f"{course.code} {s.id}: a minimum exceeds its maximum.")
            if s.kind == SectionKind.LECTURE and s.scholar_max > 0:
                warnings.append(
                    f"{course.code} {s.id}: scholars are allowed on a lecture section."
                )
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

    faculty = [p for p in problem.people if p.kind == PersonKind.FACULTY]
    if not faculty:
        warnings.append("The problem has no faculty.")
    return warnings

"""
Turn an allocation into files people use.

* CSV: one row per assignment, for checking in a spreadsheet.
* Timetable-app JSON: per course, the IC and each section's instructor list,
  in the shape of the timetable app's course documents (`ic`,
  `sections[].section`, `sections[].instructors`), so it can be imported there
  once people are matched to PSRNs.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict

from allocator.domain import IC_TARGET, Allocation, PersonKind, Problem
from allocator.evaluation import classify


def to_csv(problem: Problem, allocation: Allocation) -> str:
    people = {p.id: p for p in problem.people}
    courses = {c.code: c for c in problem.courses}
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["Course", "Title", "Section", "Kind", "Person", "Person type", "PSRN",
         "Load", "Preference match", "Note"]
    )
    for a in allocation.assignments:
        person, course = people.get(a.person_id), courses.get(a.course_code)
        if not person or not course:
            continue
        section = next((s for s in course.sections if s.id == a.target), None)
        kind = section.kind.value if section else "IC"
        match = classify(problem, person.id, course, section.kind) if section else ""
        writer.writerow(
            [course.code, course.title, a.target, kind, person.name, person.kind.value,
             person.psrn or "", f"{a.load:g}", match, a.note]
        )
    for u in allocation.unfilled:
        writer.writerow([u.course_code, courses[u.course_code].title if u.course_code in courses else "",
                         u.section_id, "", "UNFILLED", u.kind.value, "", "", "", f"{u.missing} missing"])
    return buf.getvalue()


def people_csv(problem: Problem, rows) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Name", "Type", "Load", "Band min", "Band max", "Status", "External", "Roles"])
    for r in rows:
        writer.writerow([r.name, r.kind.value, f"{r.load:g}", f"{r.band_min:g}", f"{r.band_max:g}",
                         r.status, f"{r.external_load:g}", "; ".join(r.roles)])
    return buf.getvalue()


def to_timetable_json(problem: Problem, allocation: Allocation) -> list[dict]:
    people = {p.id: p for p in problem.people}
    ics: dict[str, dict] = {}
    sections: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for a in allocation.assignments:
        person = people.get(a.person_id)
        if not person:
            continue
        ref = {"name": person.name, "psrn": person.psrn, "type": person.kind.value}
        if a.target == IC_TARGET:
            ics[a.course_code] = ref
        else:
            # Faculty first: the timetable app treats the first instructor of
            # a section as its principal instructor (PI) when exporting to ERP.
            sections[a.course_code][a.target].append(ref)

    out = []
    for course in problem.courses:
        secs = sections.get(course.code, {})
        out.append(
            {
                "courseCode": course.code,
                "name": course.title,
                "ic": ics.get(course.code),
                "sections": [
                    {
                        "section": s.id,
                        "instructors": sorted(
                            secs.get(s.id, []),
                            key=lambda r: r["type"] != PersonKind.FACULTY.value,
                        ),
                    }
                    for s in course.sections
                ],
            }
        )
    return out

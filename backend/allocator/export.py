"""
Turn an allocation into files people use.

Every export walks the courses' defined section sets (L1, L2..., T1...,
P1...) in order, so each section appears exactly once, with who takes it,
whether or not anyone was needed or found:

* sections.csv / allocation.xlsx "Sections": one row per section; the
  department can read straight down it, and it maps one-to-one onto the
  timetable app's `sections[].instructors`.
* allocation.xlsx "Faculty": one row per faculty member, with their
  Teaching Allocation, target, load and the sections they hold.
* assignments.csv: one row per (person, section), for checking.
* timetable.json: per course, each section's instructors (name and PSRN),
  in the shape of the timetable app's course documents.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict

from allocator.domain import Allocation, Problem, SectionKind
from allocator.evaluation import Report, classify

KIND_NAME = {SectionKind.LECTURE: "Lecture", SectionKind.TUTORIAL: "Tutorial", SectionKind.PRACTICAL: "Practical"}
CATEGORY_NAME = {"CDC": "CDC", "ELECTIVE": "EL", "PROJECT": "PROJECT"}
#: Faculty columns per section in the section-by-section exports.
MAX_STAFF = 3


def section_rows(problem: Problem, allocation: Allocation) -> list[dict]:
    """One row per defined section, in course and section order."""
    people = {p.id: p for p in problem.people}
    staff: dict[tuple[str, str], list[str]] = defaultdict(list)
    for a in allocation.assignments:
        staff[(a.course_code, a.target)].append(a.person_id)
    unfilled = {(u.course_code, u.section_id): u.missing for u in allocation.unfilled}

    rows = []
    for course in problem.courses:
        for s in course.sections:
            holders = [people[pid] for pid in staff.get((course.code, s.id), []) if pid in people]
            if s.faculty_max == 0:
                status = "no faculty needed" + (f" ({', '.join(s.external_staff)})" if s.external_staff else "")
            elif unfilled.get((course.code, s.id)):
                status = f"{unfilled[(course.code, s.id)]} faculty missing"
            else:
                status = "staffed"
            rows.append({
                "course": course.code,
                "title": course.title,
                "category": CATEGORY_NAME[course.category.value],
                "level": course.level.value,
                "section": s.id,
                "kind": KIND_NAME[s.kind],
                "needed": s.faculty_max,
                "faculty": [(p.name, p.psrn or "") for p in holders],
                "status": status,
            })
    return rows


def _section_header() -> list[str]:
    head = ["Course", "Title", "CDC/EL", "FD/HD", "Section", "Kind", "Faculty needed"]
    for i in range(1, MAX_STAFF + 1):
        head += [f"Faculty {i}", f"PSRN {i}"]
    return head + ["Status"]


def _section_values(row: dict) -> list:
    out = [row["course"], row["title"], row["category"], row["level"], row["section"], row["kind"], row["needed"]]
    faculty = row["faculty"] + [("", "")] * (MAX_STAFF - len(row["faculty"]))
    for name, psrn in faculty[:MAX_STAFF]:
        out += [name, psrn]
    extra = row["faculty"][MAX_STAFF:]
    status = row["status"] + (f"; also {', '.join(n for n, _ in extra)}" if extra else "")
    return out + [status]


def sections_csv(problem: Problem, allocation: Allocation) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_section_header())
    for row in section_rows(problem, allocation):
        writer.writerow(_section_values(row))
    return buf.getvalue()


def to_csv(problem: Problem, allocation: Allocation) -> str:
    people = {p.id: p for p in problem.people}
    courses = {c.code: c for c in problem.courses}
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["Course", "Title", "CDC/EL", "Section", "Kind", "Faculty", "PSRN",
         "Load", "Preference match", "Note"]
    )
    for a in allocation.assignments:
        person, course = people.get(a.person_id), courses.get(a.course_code)
        if not person or not course:
            continue
        section = course.section(a.target)
        kind = KIND_NAME[section.kind] if section else ""
        match = classify(problem, person.id, course, section.kind) if section else ""
        writer.writerow(
            [course.code, course.title, CATEGORY_NAME[course.category.value], a.target, kind, person.name,
             person.psrn or "", f"{a.load:g}", match, a.note]
        )
    for u in allocation.unfilled:
        title = courses[u.course_code].title if u.course_code in courses else ""
        writer.writerow([u.course_code, title, "", u.section_id, "", "UNFILLED", "", "", "",
                         f"{u.missing} missing"])
    return buf.getvalue()


def _faculty_rows(problem: Problem, allocation: Allocation, report: Report) -> list[list]:
    people = {p.id: p for p in problem.people}
    courses = {c.code: c for c in problem.courses}
    held: dict[str, dict[SectionKind, list[str]]] = defaultdict(lambda: defaultdict(list))
    for a in allocation.assignments:
        course = courses.get(a.course_code)
        if not course:
            continue
        section = course.section(a.target)
        if section:
            held[a.person_id][section.kind].append(f"{course.code} {section.id}")
    rows = []
    for r in report.people:
        p = people[r.person_id]
        rows.append([
            p.name, p.psrn or "", p.designation or "",
            f"{r.teaching_percent:g}" + (" (blank)" if r.percent_assumed else ""),
            r.target, r.load, r.status, r.external_load,
            ", ".join(held[p.id][SectionKind.LECTURE]),
            ", ".join(held[p.id][SectionKind.TUTORIAL]),
            ", ".join(held[p.id][SectionKind.PRACTICAL]),
            ", ".join(r.cdcs),
        ])
    return rows


_FACULTY_HEADER = ["Faculty", "PSRN", "Designation", "Teaching Allocation %", "Target", "Load", "Status",
                   "External (WILP)", "Lectures", "Tutorials", "Practicals", "Two CDCs"]


def people_csv(problem: Problem, allocation: Allocation, report: Report) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_FACULTY_HEADER)
    for row in _faculty_rows(problem, allocation, report):
        writer.writerow(row)
    return buf.getvalue()


def allocation_xlsx(problem: Problem, allocation: Allocation, report: Report) -> bytes:
    from allocator.ingest.xlsx_writer import HEADER, SheetData, write_workbook

    def sheet(name: str, header: list[str], rows: list[list], widths: list[int]) -> SheetData:
        data = SheetData(name=name, freeze_rows=1, freeze_cols=1)
        for c, (text, width) in enumerate(zip(header, widths), 1):
            data.set(1, c, text, HEADER)
            data.widths[c] = width
        for r, values in enumerate(rows, 2):
            for c, value in enumerate(values, 1):
                if value not in ("", None):
                    data.set(r, c, value)
        return data

    sections = [_section_values(r) for r in section_rows(problem, allocation)]
    section_widths = [11, 34, 8, 7, 8, 10, 9] + [26, 9] * MAX_STAFF + [30]
    faculty_widths = [28, 9, 22, 11, 8, 8, 8, 9, 30, 22, 30, 20]
    return write_workbook([
        sheet("Sections", _section_header(), sections, section_widths),
        sheet("Faculty", _FACULTY_HEADER, _faculty_rows(problem, allocation, report), faculty_widths),
    ])


def to_timetable_json(problem: Problem, allocation: Allocation) -> list[dict]:
    """Per course: every defined section's instructors."""
    people = {p.id: p for p in problem.people}
    sections: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for a in allocation.assignments:
        person = people.get(a.person_id)
        if person:
            sections[a.course_code][a.target].append({"name": person.name, "psrn": person.psrn})

    out = []
    for course in problem.courses:
        secs = sections.get(course.code, {})
        out.append(
            {
                "courseCode": course.code,
                "name": course.title,
                "category": CATEGORY_NAME[course.category.value],
                "offeredTo": course.level.value,
                "sections": [{"section": s.id, "instructors": secs.get(s.id, [])} for s in course.sections],
            }
        )
    return out

"""
Import a department's course-allocation workbook.

The workbook is what a department already produces each semester. The one we
have (Mechanical, Sem II 2025-26) carries, on separate sheets:

* the faculty preference form: three lecture, two practical and three
  tutorial choices each, with "taken consecutively" flags;
* the research-scholar form: two tutorial and two practical choices each;
* the section-level allocation the department made by hand (who took L1, T3,
  P7...), which also tells us which sections exist and how they are staffed;
* a course / IC summary.

Sheets are found by their header text, not their names, so a renamed tab
still imports. Inputs to the solver come only from the forms and the shape of
the sections; who actually held each section is kept apart as the
`reference` allocation, which the solver never sees.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from allocator.domain import (
    IC_TARGET,
    Allocation,
    Assignment,
    Course,
    CourseCategory,
    Person,
    PersonKind,
    Preference,
    Problem,
    Section,
    SectionKind,
)
from allocator.ingest.matching import CourseResolver, NameMatcher, extract_code, normalise
from allocator.ingest.xlsx import Sheet, read_workbook

DEPARTMENTS_DIR = Path(__file__).resolve().parents[3] / "data" / "departments"


def load_department_config(department: str) -> dict:
    path = DEPARTMENTS_DIR / f"{department.upper()}.json"
    if not path.exists():
        return {"department": department.upper()}
    return json.loads(path.read_text())


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


@dataclass
class ImportOptions:
    department: str = "ME"
    name: str = ""
    semester: str = ""
    #: Derive each section's staffing (how many faculty and scholars) from the
    #: department's own allocation. That is the department's decision about
    #: the shape of the work, not about who does it. When False, sections get
    #: the default templates instead.
    staffing_from_reference: bool = True
    config: dict | None = None


@dataclass
class _Builder:
    options: ImportOptions
    config: dict
    notes: list[str] = field(default_factory=list)
    people: dict[str, Person] = field(default_factory=dict)
    courses: dict[str, Course] = field(default_factory=dict)
    preferences: list[Preference] = field(default_factory=list)
    reference: list[Assignment] = field(default_factory=list)
    #: (person id, text, kind, rank, consecutive, source): preference text,
    #: resolved to course codes once every course is known.
    raw_prefs: list[tuple] = field(default_factory=list)

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)


# ---- sheet detection -----------------------------------------------------------


def _header(sheet: Sheet, row: int) -> dict[int, str]:
    return {c: v for (r, c), v in sheet.cells.items() if r == row}


def _find_sheet(sheets: dict[str, Sheet], *needles: str, rows: int = 6) -> tuple[Sheet, int] | None:
    """First sheet with a row (among the first `rows`) containing every needle."""
    for sheet in sheets.values():
        for row in range(1, rows + 1):
            text = " | ".join(_header(sheet, row).values()).upper()
            if all(n.upper() in text for n in needles):
                return sheet, row
    return None


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


# ---- entry point ----------------------------------------------------------------


def import_workbook(source: bytes | str, options: ImportOptions | None = None) -> Problem:
    options = options or ImportOptions()
    config = options.config or load_department_config(options.department)
    sheets = read_workbook(source)
    b = _Builder(options=options, config=config)

    faculty_form = _find_sheet(sheets, "FACULTY NAME", "LECTURE PREFERENCE")
    scholar_form = _find_sheet(sheets, "TUTE-1", "PRACTICAL-1")
    section_sheet = _find_sheet(sheets, "COURSE NUMBER", "SECTION NUMBER")
    load_sheet = _find_sheet(sheets, "FACULTY NAME", "LECTURE  1") or _find_sheet(
        sheets, "FACULTY NAME", "LECTURE 1"
    )
    ic_sheet = next((s for n, s in sheets.items() if "IC" in n.upper() and "SUMMARY" in n.upper()), None)

    if faculty_form is None:
        raise ValueError(
            "No faculty preference sheet found (expected a header row with "
            "'Faculty Name' and 'LECTURE Preference 1')."
        )
    if section_sheet is None:
        raise ValueError(
            "No section sheet found (expected a header row with 'Course Number' "
            "and 'Section Number'); without it there is nothing to staff."
        )

    _read_faculty(b, *faculty_form)
    if load_sheet:
        _read_extra_faculty(b, *load_sheet)
    if scholar_form:
        _read_scholars(b, *scholar_form)
    else:
        b.note("No research-scholar form found; only faculty will be allocated.")

    resolver = CourseResolver({}, config.get("course_aliases", {}))
    _read_sections(b, *section_sheet, resolver)
    if ic_sheet:
        _read_ic_summary(b, ic_sheet, resolver)
    _add_projects(b)
    _resolve_preferences(b, resolver)
    _finalise_courses(b)

    department = config.get("department", options.department.upper())
    name = options.name or f"{department} allocation"
    problem_id = slug(f"{department}-{options.semester or 'semester'}-{name}")[:60]
    return Problem(
        id=problem_id,
        name=name,
        department=department,
        semester=options.semester,
        people=list(b.people.values()),
        courses=list(b.courses.values()),
        preferences=b.preferences,
        reference=Allocation(assignments=b.reference),
        notes=b.notes,
    )


# ---- faculty -----------------------------------------------------------------------

_PREF_HEADER = re.compile(r"(LECTURE|PRACTICAL|TUTORIAL)\s+PREFERENCE\s+(\d)", re.I)
_CONSEC_HEADER = re.compile(r"(LECTURE|PRACTICAL|TUTORIAL)\s+TAKEN\s+CONSECUTIVELY\s+(\d)", re.I)
_KIND = {"LECTURE": SectionKind.LECTURE, "PRACTICAL": SectionKind.PRACTICAL, "TUTORIAL": SectionKind.TUTORIAL}

def _read_faculty(b: _Builder, sheet: Sheet, header_row: int) -> None:
    header = _header(sheet, header_row)
    name_col = next(c for c, v in header.items() if "FACULTY NAME" in v.upper())
    percent_col = next((c for c, v in header.items() if "TEACHING ALLOCATION" in v.upper()), None)
    extra_col = next((c for c, v in header.items() if "WILP" in v.upper() or "ADDITIONAL" in v.upper()), None)

    pref_cols: dict[int, tuple[SectionKind, int]] = {}
    consec_cols: dict[tuple[SectionKind, int], int] = {}
    for col, text in header.items():
        if m := _PREF_HEADER.search(text):
            pref_cols[col] = (_KIND[m.group(1).upper()], int(m.group(2)))
        if m := _CONSEC_HEADER.search(text):
            consec_cols[(_KIND[m.group(1).upper()], int(m.group(2)))] = col

    for row in sheet.rows():
        if row <= header_row:
            continue
        name = (sheet.get(row, name_col) or "").strip()
        if not name or _number(name) is not None:
            continue
        pid = "f-" + slug(name)
        person = Person(
            id=pid,
            name=name,
            kind=PersonKind.FACULTY,
            department=b.config.get("department", ""),
            teaching_percent=_number(sheet.get(row, percent_col)) if percent_col else None,
        )
        b.people[pid] = person

        for col, (kind, rank) in pref_cols.items():
            text = sheet.get(row, col)
            if not text:
                continue
            flag_col = consec_cols.get((kind, rank))
            flag = (sheet.get(row, flag_col) or "").strip().upper() if flag_col else ""
            consecutive = True if flag == "YES" else False if flag == "NO" else None
            b.raw_prefs.append((pid, text, kind, rank, consecutive, "faculty form"))

        if extra_col:
            _read_additional(b, person, sheet.get(row, extra_col) or "")


#: A fragment that only names section kinds: "L", "L&P", "LTP", "Tute", "2L".
_KIND_ONLY = re.compile(r"^\s*\d?\s*(L|T|P|LAB|TUTE|TUT|LECTURE|PRACTICAL)?(\s*[&/]?\s*(L|T|P))*\s*$", re.I)


def _kinds_in(text: str) -> list[SectionKind | None]:
    """Section kinds a suffix or fragment names; [None] when it names none."""
    upper = text.upper()
    kinds: list[SectionKind | None] = []
    if re.search(r"LAB|PRACTICAL|-\s*\d?P\b|\bP\b|&P|LTP|L&P", upper):
        kinds.append(SectionKind.PRACTICAL)
    if re.search(r"LECTURE|-\s*\d?L\b|\b\d?L\b|LTP|L&P", upper):
        kinds.append(SectionKind.LECTURE)
    if re.search(r"TUTE|TUTORIAL|-\s*T\b|\bT\b|LTP", upper):
        kinds.append(SectionKind.TUTORIAL)
    return kinds or [None]


def _read_additional(b: _Builder, person: Person, text: str) -> None:
    """
    The free-text 'WILP / Additional' column.

    WILP teaching is fixed load decided outside this process. Anything else
    that names a course ("EDP-P", "CFD-lab") is read as a first-rank wish for
    that course, of the kinds its suffix names. A trailing fragment that only
    names kinds ("Wind Energy Systems Modelling, L&P") applies to the course
    before it.
    """
    if not text.strip():
        return
    parts = [p.strip() for p in re.split(r"[,;]", text) if p.strip()]
    entries: list[tuple[str, list[SectionKind | None]]] = []
    for part in parts:
        if "WILP" in part.upper():
            _add_wilp(b, person, "workbook form")
            continue
        if _KIND_ONLY.match(part) and entries:
            entries[-1] = (entries[-1][0], _kinds_in(part))
            continue
        if _KIND_ONLY.match(part):
            continue
        entries.append((part, _kinds_in(part)))
    for course_text, kinds in entries:
        for kind in kinds:
            b.raw_prefs.append((person.id, course_text, kind, 1, None, "additional"))


def _add_wilp(b: _Builder, person: Person, source: str) -> None:
    units = float(b.config.get("wilp_units", 3))
    if any("WILP" in n for n in person.external_notes):
        return
    person.external_load += units
    person.external_notes.append(f"WILP ({units:g} units, from {source})")


def _read_extra_faculty(b: _Builder, sheet: Sheet, header_row: int) -> None:
    """Faculty on the per-person load sheet who never filled in the form."""
    header = _header(sheet, header_row)
    name_col = next(c for c, v in header.items() if "FACULTY NAME" in v.upper())
    matcher = _faculty_matcher(b)
    for row in sheet.rows():
        if row <= header_row:
            continue
        name = (sheet.get(row, name_col) or "").strip()
        if not name or _number(name) is not None:
            continue
        if matcher.match(name):
            continue
        clean = re.sub(r"^(Prof\.|Dr\.)\s*", "", name).strip()
        pid = "f-" + slug(clean)
        b.people[pid] = Person(
            id=pid, name=clean, kind=PersonKind.FACULTY, department=b.config.get("department", "")
        )
        b.note(f"{clean} is on the load sheet but did not fill in the preference form.")


# ---- scholars ----------------------------------------------------------------------


def _read_scholars(b: _Builder, sheet: Sheet, header_row: int) -> None:
    header = {c: v.upper() for c, v in _header(sheet, header_row).items()}

    def col(*needles: str) -> int | None:
        return next((c for c, v in header.items() if all(n in v for n in needles)), None)

    name_col = col("NAME") if col("NAME") != col("SUPERVISOR") else None
    name_col = next(
        (c for c, v in header.items() if v.strip() == "NAME"), name_col
    )
    email_col = col("EMAIL")
    category_col = col("I AM")
    supervisor_col = col("SUPERVISOR")
    pref_cols = {
        col("TUTE-1"): (SectionKind.TUTORIAL, 1),
        col("TUTE-2"): (SectionKind.TUTORIAL, 2),
        col("PRACTICAL-1"): (SectionKind.PRACTICAL, 1),
        col("PRACTICAL-2"): (SectionKind.PRACTICAL, 2),
    }
    pref_cols.pop(None, None)
    faculty = _faculty_matcher(b)

    for row in sheet.rows():
        if row <= header_row:
            continue
        name = (sheet.get(row, name_col) or "").strip() if name_col else ""
        if not name:
            continue
        pid = "s-" + slug(name)
        if pid in b.people:
            continue
        supervisor_text = (sheet.get(row, supervisor_col) or "") if supervisor_col else ""
        supervisor = faculty.match(supervisor_text) if supervisor_text else None
        if supervisor_text and not supervisor:
            b.note(f"Supervisor {supervisor_text.strip()!r} of scholar {name} not matched to a faculty member.")
        b.people[pid] = Person(
            id=pid,
            name=name,
            kind=PersonKind.SCHOLAR,
            department=b.config.get("department", ""),
            email=(sheet.get(row, email_col) or None) if email_col else None,
            category=(sheet.get(row, category_col) or None) if category_col else None,
            supervisor=supervisor,
        )
        has_prefs = False
        for c, (kind, rank) in pref_cols.items():
            text = sheet.get(row, c)
            if text and len(text.strip()) > 1:
                b.raw_prefs.append((pid, text, kind, rank, None, "scholar form"))
                has_prefs = True
        if not has_prefs:
            b.note(f"Scholar {name} has no preferences on the form.")


# ---- sections (demand + reference) -------------------------------------------------------


def _faculty_matcher(b: _Builder) -> NameMatcher:
    return NameMatcher(
        [(p.id, p.name, p.aliases) for p in b.people.values() if p.kind == PersonKind.FACULTY],
        b.config.get("name_aliases", {}),
    )


def _scholar_matcher(b: _Builder) -> NameMatcher:
    return NameMatcher(
        [(p.id, p.name, p.aliases) for p in b.people.values() if p.kind == PersonKind.SCHOLAR],
        b.config.get("name_aliases", {}),
    )


def _section_id(text: str) -> tuple[str, SectionKind] | None:
    m = re.fullmatch(r"\s*([LTP])\s*(\d*)\s*", text.upper())
    if not m:
        return None
    return f"{m.group(1)}{m.group(2) or 1}", SectionKind(m.group(1))


@dataclass
class _Staff:
    faculty: list[tuple[str, str]] = field(default_factory=list)  # (pid, note)
    scholars: list[tuple[str, str]] = field(default_factory=list)
    external: list[str] = field(default_factory=list)


#: Words that mark a cell as an audience label ("A4", "ME Mechanical, Design",
#: "Open EL", "cdc-thermal") rather than a person. Elective rows put who the
#: course is offered to in the instructor columns.
_AUDIENCE_WORDS = {"EL", "CDC", "ME", "MECHANICAL", "DESIGN", "MANUFACTURING", "THERMAL", "OPEN"}


def _looks_like_person(text: str) -> bool:
    if re.search(r"\d", text):
        return False
    words = set(normalise(text).split())
    return bool(words) and not (words & _AUDIENCE_WORDS)


def _classify_staff(b: _Builder, texts: list[str]) -> _Staff:
    """Sort one row's instructor cells into faculty, scholars and outsiders."""
    staff = _Staff()
    faculty, scholars = _faculty_matcher(b), _scholar_matcher(b)
    placeholders = {normalise(p) for p in b.config.get("placeholder_staff", [])}
    for raw in texts:
        for text in re.split(r"\n", raw):
            text = text.strip()
            if not text:
                continue
            note = "HALF" if "HALF" in text.upper() else ""
            if normalise(text) in placeholders:
                staff.external.append(text)
                continue
            f, s = faculty.match(text), scholars.match(text)
            if f and s:
                # Both rosters claim it: trust the better score.
                if faculty.best_score(text) >= scholars.best_score(text):
                    s = None
                else:
                    f = None
            if f:
                staff.faculty.append((f, note))
            elif s:
                staff.scholars.append((s, note))
            elif not _looks_like_person(text):
                continue
            else:
                staff.external.append(text)
                b.note(f"Section staff {text!r} matched nobody in the department; kept as external.")
    return staff


def _read_sections(b: _Builder, sheet: Sheet, header_row: int, resolver: CourseResolver) -> None:
    filled = sheet.filled()
    header = _header(sheet, header_row)
    col = lambda needle: next((c for c, v in header.items() if needle in v.upper()), None)  # noqa: E731
    code_col, title_col = col("COURSE NUMBER"), col("COURSE TITLE")
    section_col = col("SECTION")
    credits_col = col("CREDITS")
    room_col = col("ROOM")
    staff_cols = [c for c, v in header.items() if "INSTRUCTOR" in v.upper()]
    # Instructor cells run from the first instructor column to the room column.
    first_staff = min(staff_cols) if staff_cols else (section_col or 6) + 2
    last_staff = (room_col - 1) if room_col else first_staff + 3
    strength_col = None
    for r in (header_row, header_row + 1):
        strength_col = strength_col or next(
            (c for (rr, c), v in sheet.cells.items() if rr == r and "STRENGTH" in v.upper()), None
        )
    lecture_col, lab_col = credits_col, (credits_col + 1 if credits_col else None)

    current: str | None = None
    for row in sheet.rows():
        if row <= header_row + 1:
            continue
        code_text = filled.get(row, code_col) or ""
        code = extract_code(code_text)
        if code:
            current = code
            title = (filled.get(row, title_col) or "").strip()
            course = b.courses.get(code)
            if course is None:
                course = Course(code=code, title=title, department=b.config.get("department", ""))
                b.courses[code] = course
                resolver.add(code, title)
                lec = _number(sheet.get(row, lecture_col)) if lecture_col else None
                lab = _number(sheet.get(row, lab_col)) if lab_col else None
                if lec is not None:
                    course.lecture_units = lec
                if lab is not None:
                    course.lab_units = lab
                strength = _number(sheet.get(row, strength_col)) if strength_col else None
                if strength:
                    course.strength = int(strength)
        elif code_text.strip():
            current = None  # a note row ends the previous course
        if current is None:
            _read_wilp_row(b, sheet, row, first_staff, last_staff, section_col)
            continue

        course = b.courses[current]
        section_text = (sheet.get(row, section_col) or "").strip()
        staff_texts = [sheet.get(row, c) or "" for c in range(first_staff, last_staff + 1)]

        if section_text.upper() == "IC":
            staff = _classify_staff(b, staff_texts)
            for pid, _ in staff.faculty[:1]:
                _add_reference(b, pid, current, IC_TARGET)
            continue

        parsed = _section_id(section_text) if section_text else None
        if parsed is None:
            if not section_text and current in b.config.get("project_courses", []):
                staff = _classify_staff(b, staff_texts)
                for pid, _ in staff.faculty[:1]:
                    _add_reference(b, pid, current, IC_TARGET)
            continue

        section_id, kind = parsed
        if any(s.id == section_id for s in course.sections):
            b.note(f"{current} {section_id} appears twice in the section sheet; second row ignored.")
            continue
        staff = _classify_staff(b, staff_texts)

        for pid, note in staff.faculty:
            _add_reference(b, pid, current, section_id, note)
        for pid, note in staff.scholars:
            _add_reference(b, pid, current, section_id, note)
        _mark_ic_from_cells(b, current, staff_texts)

        course.sections.append(_make_section(b, section_id, kind, staff))


def _read_wilp_row(
    b: _Builder, sheet: Sheet, row: int, first: int, last: int, section_col: int | None
) -> None:
    """
    Rows after the course list that pair a WILP course title with faculty
    ("PRODUCT DESIGN | Nitin Kotkunde"). Those faculty carry WILP load.

    Only read when the row has no course code and no section, its first staff
    cell is a title rather than a person, and at least one other cell names a
    faculty member.
    """
    if section_col and (sheet.get(row, section_col) or "").strip():
        return
    cells = [(c, (sheet.get(row, c) or "").strip()) for c in range(first, last + 2)]
    cells = [(c, v) for c, v in cells if v]
    if len(cells) < 2:
        return
    faculty = _faculty_matcher(b)
    title = cells[0][1]
    if faculty.match(title) or len(title) < 5:
        return
    for _, text in cells[1:]:
        pid = faculty.match(text)
        if pid:
            _add_wilp(b, b.people[pid], f"section sheet: {title.title()}")


def _mark_ic_from_cells(b: _Builder, code: str, texts: list[str]) -> None:
    faculty = _faculty_matcher(b)
    for text in texts:
        if re.search(r"-\s*IC\b", text, re.I):
            pid = faculty.match(text)
            if pid:
                _add_reference(b, pid, code, IC_TARGET)


def _make_section(b: _Builder, section_id: str, kind: SectionKind, staff: _Staff) -> Section:
    if b.options.staffing_from_reference:
        n_fac, n_sch = len(staff.faculty), len(staff.scholars)
        if kind == SectionKind.LECTURE:
            n_fac, n_sch = max(1, n_fac), 0
        return Section(
            id=section_id,
            kind=kind,
            faculty_min=n_fac,
            faculty_max=n_fac,
            scholar_min=n_sch,
            scholar_max=n_sch,
            external_staff=staff.external,
        )
    templates = {
        SectionKind.LECTURE: (1, 1, 0, 0),
        SectionKind.TUTORIAL: (1, 1, 1, 2),
        SectionKind.PRACTICAL: (1, 1, 1, 2),
    }
    fmin, fmax, smin, smax = templates[kind]
    return Section(
        id=section_id, kind=kind, faculty_min=fmin, faculty_max=fmax,
        scholar_min=smin, scholar_max=smax, external_staff=staff.external,
    )


def _add_reference(b: _Builder, pid: str, code: str, target: str, note: str = "") -> None:
    for a in b.reference:
        if a.person_id == pid and a.course_code == code and a.target == target:
            return
    if target == IC_TARGET:
        # One IC per course: the first source to name one wins.
        if any(a.course_code == code and a.target == IC_TARGET for a in b.reference):
            return
    b.reference.append(Assignment(person_id=pid, course_code=code, target=target, note=note))


# ---- IC summary, projects, WILP ------------------------------------------------------------


def _read_ic_summary(b: _Builder, sheet: Sheet, resolver: CourseResolver) -> None:
    """
    Rows of: serial, code, title, IC, FD/HD, CDC/EL.

    Names the IC of each course (reference) and its category. Courses here but
    not in the section sheet are projects or courses run elsewhere.
    """
    faculty = _faculty_matcher(b)
    for row in sheet.rows():
        cells = {c: v for (r, c), v in sheet.cells.items() if r == row}
        code = next((extract_code(v) for v in cells.values() if extract_code(v)), None)
        if not code:
            continue
        values = list(cells.values())
        title = next((v for v in values if v != code and not extract_code(v) and len(v) > 4 and not v.upper().startswith("PROF")), "")
        ic_text = next((v for v in values if re.match(r"\s*(PROF|DR)\.?", v, re.I)), None)
        tags = {v.strip().upper() for v in values}

        course = b.courses.get(code)
        if course is None:
            if code in b.config.get("project_courses", []) or "PROJECT" in title.upper():
                course = Course(code=code, title=title, department=b.config.get("department", ""),
                                category=CourseCategory.PROJECT)
                b.courses[code] = course
                resolver.add(code, title)
            else:
                b.note(f"{code} is on the IC summary but has no sections in the section sheet; not staffed.")
                continue
        if course.category != CourseCategory.PROJECT:
            if "HD" in tags:
                course.category = CourseCategory.HIGHER_DEGREE
            elif "EL" in tags:
                course.category = CourseCategory.ELECTIVE
        if ic_text:
            pid = faculty.match(ic_text)
            if pid:
                _add_reference(b, pid, code, IC_TARGET)
            else:
                b.note(f"IC {ic_text.strip()!r} of {code} not matched to a faculty member.")


def _add_projects(b: _Builder) -> None:
    for code in b.config.get("project_courses", []):
        course = b.courses.get(code)
        if course is None:
            continue
        course.category = CourseCategory.PROJECT
        course.sections = []
        course.service = True
        course.ic_units = float(b.config.get("project_ic_units", 1))


def _resolve_preferences(b: _Builder, resolver: CourseResolver) -> None:
    unresolved: dict[str, int] = defaultdict(int)
    seen: set[tuple] = set()
    for pid, text, kind, rank, consecutive, source in getattr(b, "raw_prefs", []):
        code = resolver.resolve(text)
        if code is None:
            unresolved[text.strip()] += 1
            continue
        key = (pid, code, kind)
        if key in seen:
            # Same course listed twice for one kind: keep the better rank,
            # which is the first seen since columns run rank 1 upward.
            continue
        seen.add(key)
        b.preferences.append(
            Preference(
                person_id=pid, course_code=code, kind=kind, rank=rank,
                taught_consecutively=consecutive, source=source,
            )
        )
    if unresolved:
        b.note(
            "Preference text not matched to any course: "
            + "; ".join(f"{t!r}" for t in sorted(unresolved)[:20])
        )


def _finalise_courses(b: _Builder) -> None:
    defaults = b.config.get("default_units", {"lecture": 3, "lab": 1})
    overrides = b.config.get("unit_overrides", {})
    service = set(b.config.get("service_courses", []))
    for code, course in b.courses.items():
        if code in service:
            course.service = True
        if course.category == CourseCategory.PROJECT:
            continue
        if code in overrides:
            course.lecture_units = float(overrides[code].get("lecture", course.lecture_units))
            course.lab_units = float(overrides[code].get("lab", course.lab_units))
        kinds = {s.kind for s in course.sections}
        if SectionKind.LECTURE in kinds and not course.lecture_units:
            course.lecture_units = float(defaults.get("lecture", 3))
            b.note(f"{code}: no lecture units recorded; using {course.lecture_units:g}.")
        if SectionKind.PRACTICAL in kinds and not course.lab_units:
            course.lab_units = float(defaults.get("lab", 1))
            b.note(f"{code}: no lab units recorded; using {course.lab_units:g}.")
        if not any(a.course_code == code and a.target == IC_TARGET for a in b.reference):
            b.note(f"{code}: the workbook names no IC.")

"""
Write and read the department upload template (see template_spec.py).

Reading is strict. Every problem in the upload is collected with its sheet,
row and column, and if any is an error nothing is imported: a half-imported
allocation round is worse than a rejected one, because nobody notices what
was left out. Warnings (a preference for a course not on offer this
semester, say) are kept as notes on the imported problem.
"""

from __future__ import annotations

import re
from collections import defaultdict

from pydantic import BaseModel

from allocator.domain import (
    IC_TARGET,
    Course,
    CourseCategory,
    Lock,
    LockMode,
    Person,
    PersonKind,
    Preference,
    Problem,
    Section,
    SectionKind,
)
from allocator.ingest.matching import NameMatcher, extract_code, name_tokens, normalise
from allocator.ingest.template_spec import (
    CATEGORIES,
    COURSES,
    FACULTY,
    INSTRUCTIONS,
    REQUIRED_SHEETS,
    SCHOLAR_CATEGORIES,
    SCHOLARS,
    SECTIONS,
    SHEETS,
    TEMPLATE_MARKER,
    TEMPLATE_VERSION,
    YES_NO,
    Column,
    SheetSpec,
)
from allocator.ingest.xlsx import Sheet, read_workbook
from allocator.ingest.xlsx_writer import (
    HEADER,
    REQUIRED_HEADER,
    TITLE,
    WRAP,
    Formula,
    SheetData,
    Validation,
    col_letter,
    write_workbook,
)

_CATEGORY_TO_DOMAIN = {
    "CDC": CourseCategory.CDC,
    "EL": CourseCategory.ELECTIVE,
    "ELECTIVE": CourseCategory.ELECTIVE,
    "HD": CourseCategory.HIGHER_DEGREE,
    "PROJECT": CourseCategory.PROJECT,
}
_CATEGORY_TO_TEMPLATE = {
    CourseCategory.CDC: "CDC",
    CourseCategory.ELECTIVE: "EL",
    CourseCategory.HIGHER_DEGREE: "HD",
    CourseCategory.PROJECT: "PROJECT",
}
_PREF_COLUMN = re.compile(r"^(LECTURE|PRACTICAL|TUTORIAL) PREFERENCE (\d)$")
_CONSEC_COLUMN = "{kind} TAKEN CONSECUTIVELY {rank}"
_SCHOLAR_PREFS = {
    "Tute-1": (SectionKind.TUTORIAL, 1),
    "Tute-2": (SectionKind.TUTORIAL, 2),
    "Practical-1": (SectionKind.PRACTICAL, 1),
    "Practical-2": (SectionKind.PRACTICAL, 2),
}
_KIND_WORD = {SectionKind.LECTURE: "LECTURE", SectionKind.PRACTICAL: "PRACTICAL", SectionKind.TUTORIAL: "TUTORIAL"}
_SLOTS = {SectionKind.LECTURE: 3, SectionKind.PRACTICAL: 2, SectionKind.TUTORIAL: 3}

#: Rows the drop-down ranges cover in a blank template.
_ROOM = 500


# ============================================================================
# Writing
# ============================================================================


def write_template(problem: Problem | None = None, department: str = "") -> bytes:
    """
    The upload template as .xlsx bytes.

    With a problem, the sheets are pre-filled from it, so a department can
    start from last semester's courses and roster instead of a blank sheet.
    """
    department = department or (problem.department if problem else "")
    rows = _rows_from_problem(problem) if problem else {s.name: [] for s in SHEETS}

    sheets = [_instructions_sheet(department)]
    for spec in SHEETS:
        sheets.append(_data_sheet(spec, rows[spec.name]))
    sheets.append(_lists_sheet())
    _add_validations(sheets, max(_ROOM, max(len(r) for r in rows.values()) + 100))
    return write_workbook(sheets)


def _instructions_sheet(department: str) -> SheetData:
    sheet = SheetData(name="Instructions", widths={1: 30, 2: 12, 3: 110})
    sheet.set(1, 1, TEMPLATE_MARKER, TITLE)
    sheet.set(2, 1, f"Version {TEMPLATE_VERSION}")
    if department:
        sheet.set(2, 3, f"Department: {department}")
    row = 4
    for line in INSTRUCTIONS:
        sheet.set(row, 1, line, TITLE if line in ("How to fill this template", "Rules") else None)
        row += 1
    row += 1
    for spec in SHEETS:
        sheet.set(row, 1, f"Sheet: {spec.name}", TITLE)
        row += 1
        sheet.set(row, 1, spec.purpose, WRAP)
        sheet.row_heights[row] = 45
        row += 1
        sheet.set(row, 1, "Column", HEADER)
        sheet.set(row, 2, "Required", HEADER)
        sheet.set(row, 3, "Meaning", HEADER)
        row += 1
        for column in spec.columns:
            sheet.set(row, 1, column.header)
            sheet.set(row, 2, "Yes" if column.required else "")
            meaning = column.help + (f" Default: {column.default}." if column.default else "")
            sheet.set(row, 3, meaning.strip(), WRAP)
            row += 1
        row += 1
    return sheet


def _data_sheet(spec: SheetSpec, rows: list[list[object]]) -> SheetData:
    sheet = SheetData(name=spec.name, freeze_rows=1, freeze_cols=spec.freeze_cols)
    sheet.row_heights[1] = 48
    for col, column in enumerate(spec.columns, 1):
        sheet.set(1, col, column.header, REQUIRED_HEADER if column.required else HEADER)
        sheet.widths[col] = column.width
    for r, values in enumerate(rows, 2):
        for col, value in enumerate(values, 1):
            sheet.set(r, col, value)
    return sheet


def _lists_sheet() -> SheetData:
    """
    Hidden sheet feeding the drop-downs.

    Course choices are formulas over the Courses sheet ("ME F219 - Manufacturing
    Processes"), so a department that adds a course gets it in every
    preference drop-down without touching this sheet.
    """
    sheet = SheetData(name="Lists", hidden=True)
    sheet.set(1, 1, "Course choices")
    sheet.set(1, 2, "Yes/No")
    sheet.set(1, 3, "Categories")
    sheet.set(1, 4, "Scholar categories")
    sheet.set(1, 5, "People")
    for r in range(2, _ROOM + 2):
        sheet.set(r, 1, Formula(f'IF(Courses!A{r}="","",Courses!A{r}&" - "&Courses!B{r})'))
    for i, v in enumerate(YES_NO, 2):
        sheet.set(i, 2, v)
    for i, v in enumerate(CATEGORIES, 2):
        sheet.set(i, 3, v)
    for i, v in enumerate(SCHOLAR_CATEGORIES, 2):
        sheet.set(i, 4, v)
    for r in range(2, _ROOM + 2):
        sheet.set(r, 5, Formula(f'IF(Faculty!A{r}="","",Faculty!A{r})'))
        sheet.set(_ROOM + r, 5, Formula(f"IF('Research Scholars'!A{r}=\"\",\"\",'Research Scholars'!A{r})"))
    return sheet


def _add_validations(sheets: list[SheetData], room: int) -> None:
    by_name = {s.name: s for s in sheets}
    sources = {
        "course": ("Lists!$A$2:$A$%d" % (_ROOM + 1), "warning"),
        "yesno": ("Lists!$B$2:$B$3", "stop"),
        "category": ("Lists!$C$2:$C$%d" % (len(CATEGORIES) + 1), "stop"),
        "scholar_category": ("Lists!$D$2:$D$%d" % (len(SCHOLAR_CATEGORIES) + 1), "warning"),
        "faculty": ("Faculty!$A$2:$A$%d" % (_ROOM + 1), "warning"),
        "person": ("Lists!$E$2:$E$%d" % (2 * _ROOM + 1), "warning"),
    }
    for spec in SHEETS:
        sheet = by_name[spec.name]
        for col, column in enumerate(spec.columns, 1):
            if spec is SECTIONS and column.header == "Course Number":
                source = ("Courses!$A$2:$A$%d" % (_ROOM + 1), "warning")
            else:
                source = sources.get(column.kind)
            if source:
                letter = col_letter(col)
                sheet.validations.append(Validation(f"{letter}2:{letter}{room}", source[0], source[1]))


def _rows_from_problem(problem: Problem) -> dict[str, list[list[object]]]:
    people = {p.id: p for p in problem.people}
    titles = {c.code: c.title for c in problem.courses}
    label = lambda code: f"{code} - {titles.get(code, '')}".rstrip(" -")  # noqa: E731

    ic_locks = {l.course_code: people[l.person_id].name for l in problem.locks
                if l.target == IC_TARGET and l.mode == LockMode.REQUIRE and l.person_id in people}
    section_locks: dict[tuple[str, str], list[str]] = defaultdict(list)
    for l in problem.locks:
        if l.target != IC_TARGET and l.mode == LockMode.REQUIRE and l.person_id in people:
            section_locks[(l.course_code, l.target)].append(people[l.person_id].name)

    courses = [
        [c.code, c.title, c.lecture_units or 0, c.lab_units or 0, (c.lecture_units or 0) + (c.lab_units or 0),
         c.strength or None, _CATEGORY_TO_TEMPLATE[c.category], "Yes" if c.service else "No",
         "Yes" if c.ic_required else "No", ic_locks.get(c.code), c.ic_units or None, None]
        for c in problem.courses
    ]
    sections = []
    for c in problem.courses:
        for s in c.sections:
            scholars = str(s.scholar_max) if s.scholar_min == s.scholar_max else f"{s.scholar_min}-{s.scholar_max}"
            fixed = section_locks.get((c.code, s.id), [])[:3]
            sections.append([c.code, s.id, s.faculty_max, scholars, *(fixed + [None] * (3 - len(fixed))), None, None])

    prefs: dict[str, list[Preference]] = defaultdict(list)
    for p in problem.preferences:
        prefs[p.person_id].append(p)

    def ordered(person_id: str, kind: SectionKind, slots: int) -> tuple[list[Preference], list[Preference]]:
        """A person's choices of one kind, best first, one per course: the
        first `slots` fill the form's columns, the rest overflow."""
        seen, chosen = set(), []
        # The person's own ranked choices keep their columns; wishes read from
        # free text ("additional") fill whatever is left.
        mine = (p for p in prefs.get(person_id, []) if p.kind == kind)
        for p in sorted(mine, key=lambda p: (p.source == "additional", p.rank)):
            if p.course_code not in seen:
                seen.add(p.course_code)
                chosen.append(p)
        return chosen[:slots], chosen[slots:]

    faculty, scholars = [], []
    for person in problem.people:
        if person.kind == PersonKind.FACULTY:
            cells, extra = [], []
            for kind in (SectionKind.LECTURE, SectionKind.PRACTICAL, SectionKind.TUTORIAL):
                kept, overflow = ordered(person.id, kind, _SLOTS[kind])
                extra += [f"{label(p.course_code)} ({kind.value})" for p in overflow]
                for i in range(_SLOTS[kind]):
                    p = kept[i] if i < len(kept) else None
                    cells.append(label(p.course_code) if p else None)
                    cells.append(None if not p or p.taught_consecutively is None
                                 else ("Yes" if p.taught_consecutively else "No"))
            extra += [f"{label(p.course_code)} (any part)" for p in prefs.get(person.id, []) if p.kind is None]
            faculty.append([
                person.name, person.psrn, person.email, person.designation, person.teaching_percent,
                person.load_min, person.load_max, person.external_load or 0,
                "; ".join(person.external_notes) or None, *cells,
                ("Also wants: " + "; ".join(extra)) if extra else None,
            ])
        else:
            tutes, _ = ordered(person.id, SectionKind.TUTORIAL, 2)
            pracs, _ = ordered(person.id, SectionKind.PRACTICAL, 2)
            pick = lambda xs, i: label(xs[i].course_code) if i < len(xs) else None  # noqa: E731
            supervisor = people[person.supervisor].name if person.supervisor in people else None
            scholars.append([
                person.name, person.email, person.category, supervisor, person.load_max,
                pick(tutes, 0), pick(tutes, 1), pick(pracs, 0), pick(pracs, 1), None,
            ])
    return {COURSES.name: courses, SECTIONS.name: sections, FACULTY.name: faculty, SCHOLARS.name: scholars}


# ============================================================================
# Reading
# ============================================================================


class TemplateIssue(BaseModel):
    severity: str  # error / warning
    sheet: str
    row: int | None = None
    column: str | None = None
    message: str

    def __str__(self) -> str:
        where = self.sheet + (f" row {self.row}" if self.row else "") + (f", {self.column}" if self.column else "")
        return f"{where}: {self.message}"


class TemplateError(ValueError):
    def __init__(self, issues: list[TemplateIssue]):
        self.issues = issues
        errors = [i for i in issues if i.severity == "error"]
        super().__init__(f"The upload has {len(errors)} error(s); nothing was imported.")


def is_template(sheets: dict[str, Sheet]) -> bool:
    instructions = sheets.get("Instructions")
    if instructions and TEMPLATE_MARKER in (instructions.get(1, 1) or "").upper():
        return True
    return REQUIRED_SHEETS <= set(sheets) and _header_map(sheets[FACULTY.name]).get("FACULTY NAME") is not None


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().upper())


def _header_map(sheet: Sheet) -> dict[str, int]:
    return {_key(v): c for (r, c), v in sheet.cells.items() if r == 1}


class _Reader:
    def __init__(self, sheets: dict[str, Sheet]):
        self.sheets = sheets
        self.issues: list[TemplateIssue] = []
        #: Where things came from, for error messages: course code -> row,
        #: (course, section) -> (row, pre-assigned names).
        self.course_rows: dict[str, int] = {}
        self.section_meta: dict[tuple[str, str], tuple[int, list[str]]] = {}

    def error(self, sheet: str, row: int | None, column: str | None, message: str) -> None:
        self.issues.append(TemplateIssue(severity="error", sheet=sheet, row=row, column=column, message=message))

    def warn(self, sheet: str, row: int | None, column: str | None, message: str) -> None:
        self.issues.append(TemplateIssue(severity="warning", sheet=sheet, row=row, column=column, message=message))

    def rows(self, spec: SheetSpec) -> list[tuple[int, dict[str, str]]]:
        """Data rows as {header: raw text}, with missing columns reported."""
        sheet = self.sheets.get(spec.name)
        if sheet is None:
            if spec.name in REQUIRED_SHEETS:
                self.error(spec.name, None, None, "Sheet is missing. Do not rename the template's sheets.")
            return []
        headers = _header_map(sheet)
        cols: dict[str, int] = {}
        for column in spec.columns:
            col = headers.get(_key(column.header))
            if col is None:
                if column.required:
                    self.error(spec.name, 1, column.header, "Required column is missing or its header was changed.")
                continue
            cols[column.header] = col
        out = []
        for row in sheet.rows():
            if row == 1:
                continue
            values = {h: (sheet.get(row, c) or "").strip() for h, c in cols.items()}
            if not any(values.values()):
                continue
            out.append((row, values))
        return out

    # ---- cell parsers -----------------------------------------------------------

    def number(self, sheet: str, row: int, column: Column, text: str) -> float | None:
        text = text or (column.default or "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            self.error(sheet, row, column.header, f"{text!r} is not a number.")
            return None

    def yesno(self, sheet: str, row: int, column: Column, text: str) -> bool | None:
        text = (text or column.default or "").strip().upper()
        if not text:
            return None
        if text in ("Y", "YES", "TRUE", "1"):
            return True
        if text in ("N", "NO", "FALSE", "0"):
            return False
        self.error(sheet, row, column.header, f"{text!r}: write Yes or No.")
        return None


def _col(spec: SheetSpec, header: str) -> Column:
    return next(c for c in spec.columns if c.header == header)


def read_template(data: bytes | str, department: str = "", name: str = "", semester: str = "") -> Problem:
    """Read an upload made from the template; raise TemplateError on any error."""
    from allocator.ingest.workbook import slug

    sheets = read_workbook(data)
    r = _Reader(sheets)

    courses = _read_courses(r)
    _read_sections(r, courses)
    people, prefs, roster = _read_faculty(r, courses)
    scholar_people, scholar_prefs = _read_scholars(r, courses, roster)
    people.update(scholar_people)
    prefs += scholar_prefs
    locks = _read_locks(r, courses, people)

    errors = [i for i in r.issues if i.severity == "error"]
    if errors:
        raise TemplateError(r.issues)

    department = (department or "").upper()
    problem_name = name or f"{department or 'Department'} allocation"
    return Problem(
        id=slug(f"{department}-{semester or 'semester'}-{problem_name}")[:60] or "allocation",
        name=problem_name,
        department=department,
        semester=semester,
        people=list(people.values()),
        courses=list(courses.values()),
        preferences=prefs,
        locks=locks,
        notes=[str(i) for i in r.issues],
    )


def _read_courses(r: _Reader) -> dict[str, Course]:
    spec = COURSES
    courses: dict[str, Course] = {}
    for row, v in r.rows(spec):
        code = extract_code(v.get("Course Number", ""))
        if not code:
            r.error(spec.name, row, "Course Number", f"{v.get('Course Number')!r} is not a course number like ME F219.")
            continue
        if code in courses:
            r.error(spec.name, row, "Course Number", f"{code} is listed twice.")
            continue
        title = v.get("Course Title", "")
        if not title:
            r.error(spec.name, row, "Course Title", "Title is required.")
        category_text = (v.get("Category") or "CDC").upper()
        category = _CATEGORY_TO_DOMAIN.get(category_text)
        if category is None:
            r.error(spec.name, row, "Category", f"{category_text!r}: use one of {', '.join(CATEGORIES)}.")
            category = CourseCategory.CDC
        lec = r.number(spec.name, row, _col(spec, "L"), v.get("L", "")) or 0.0
        lab = r.number(spec.name, row, _col(spec, "P"), v.get("P", "")) or 0.0
        total = r.number(spec.name, row, _col(spec, "U"), v.get("U", ""))
        if total is not None and abs(total - lec - lab) > 1e-9:
            r.warn(spec.name, row, "U", f"U is {total:g} but L + P is {lec + lab:g}; L and P are used.")
        strength = r.number(spec.name, row, _col(spec, "Strength"), v.get("Strength", ""))
        service = r.yesno(spec.name, row, _col(spec, "Service Course"), v.get("Service Course", ""))
        ic_required = r.yesno(spec.name, row, _col(spec, "IC Required"), v.get("IC Required", ""))
        ic_units = r.number(spec.name, row, _col(spec, "IC Units"), v.get("IC Units", ""))
        courses[code] = Course(
            code=code,
            title=title,
            category=category,
            lecture_units=lec,
            lab_units=lab,
            strength=int(strength or 0),
            service=bool(service) or category == CourseCategory.PROJECT,
            ic_required=True if ic_required is None else ic_required,
            ic_units=ic_units if ic_units is not None else (1.0 if category == CourseCategory.PROJECT else 0.0),
        )
        r.course_rows[code] = row
    if not courses:
        r.error(spec.name, None, None, "No courses listed.")
    return courses


def _parse_range(text: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+))?\s*", text)
    if not m:
        return None
    lo = int(m.group(1))
    hi = int(m.group(2)) if m.group(2) else lo
    return (lo, hi) if lo <= hi else None


def _read_sections(r: _Reader, courses: dict[str, Course]) -> None:
    spec = SECTIONS
    for row, v in r.rows(spec):
        code = extract_code(v.get("Course Number", ""))
        if not code or code not in courses:
            r.error(spec.name, row, "Course Number", f"{v.get('Course Number')!r} is not on the Courses sheet.")
            continue
        course = courses[code]
        sid = re.sub(r"\s+", "", v.get("Section Number", "")).upper()
        if not re.fullmatch(r"[LTP]\d+", sid):
            r.error(spec.name, row, "Section Number", f"{v.get('Section Number')!r}: write L1, T2, P3 and so on.")
            continue
        if any(s.id == sid for s in course.sections):
            r.error(spec.name, row, "Section Number", f"{code} {sid} is listed twice.")
            continue
        kind = SectionKind(sid[0])
        if course.category == CourseCategory.PROJECT:
            r.warn(spec.name, row, "Course Number", f"{code} is a PROJECT course; its sections are ignored.")
            continue
        faculty = r.number(spec.name, row, _col(spec, "Faculty Needed"), v.get("Faculty Needed", ""))
        faculty_n = int(faculty if faculty is not None else 1)
        scholars_text = v.get("Scholars Needed", "")
        if scholars_text:
            span = _parse_range(scholars_text)
            if span is None:
                r.error(spec.name, row, "Scholars Needed", f"{scholars_text!r}: write a number or a range like 1-2.")
                span = (0, 0)
        else:
            span = (0, 0) if kind == SectionKind.LECTURE else (1, 1)
        if kind == SectionKind.LECTURE and span[1] > 0:
            r.warn(spec.name, row, "Scholars Needed", "Scholars on a lecture section are unusual; check this is intended.")
        if kind == SectionKind.LECTURE and faculty_n < 1:
            r.error(spec.name, row, "Faculty Needed", "A lecture section needs at least one faculty member.")
        if kind == SectionKind.PRACTICAL and not course.lab_units:
            r.warn(spec.name, row, "Section Number", f"{code} has a practical section but no lab units (P) on the Courses sheet.")
        if kind == SectionKind.LECTURE and not course.lecture_units:
            r.warn(spec.name, row, "Section Number", f"{code} has a lecture section but no lecture units (L) on the Courses sheet.")
        course.sections.append(Section(
            id=sid, kind=kind,
            faculty_min=faculty_n, faculty_max=faculty_n,
            scholar_min=span[0], scholar_max=span[1],
        ))
        r.section_meta[(code, sid)] = (row, [v.get(f"Pre-assigned Instructor {i}", "") for i in (1, 2, 3)])
    for course in courses.values():
        if not course.sections and course.category != CourseCategory.PROJECT:
            r.warn(COURSES.name, r.course_rows.get(course.code), "Course Number",
                   f"{course.code} has no rows on the Sections sheet; only its IC will be chosen.")


def _resolve_course(r: _Reader, sheet: str, row: int, column: str, text: str, courses: dict[str, Course]) -> str | None:
    if not text:
        return None
    code = extract_code(text)
    if code and code in courses:
        return code
    title = normalise(text)
    by_title = [c.code for c in courses.values() if normalise(c.title) == title]
    if len(by_title) == 1:
        return by_title[0]
    if code:
        r.warn(sheet, row, column, f"{code} is not on the Courses sheet (not offered this semester?); ignored.")
    else:
        r.warn(sheet, row, column, f"{text!r} is not a course on the Courses sheet; ignored.")
    return None


def _read_faculty(r: _Reader, courses: dict[str, Course]):
    from allocator.ingest.workbook import slug

    spec = FACULTY
    people: dict[str, Person] = {}
    prefs: list[Preference] = []
    psrns: dict[str, int] = {}
    for row, v in r.rows(spec):
        name = v.get("Faculty Name", "")
        if not name:
            r.error(spec.name, row, "Faculty Name", "Name is required.")
            continue
        pid = "f-" + slug(name)
        if pid in people:
            r.error(spec.name, row, "Faculty Name", f"{name} is listed twice.")
            continue
        psrn = v.get("PSRN") or None
        if psrn:
            if psrn in psrns:
                r.error(spec.name, row, "PSRN", f"PSRN {psrn} is also on row {psrns[psrn]}.")
            psrns[psrn] = row
        external = r.number(spec.name, row, _col(spec, "External Load"), v.get("External Load", "")) or 0.0
        details = v.get("External Load Details", "")
        lo = r.number(spec.name, row, _col(spec, "Min Load"), v.get("Min Load", ""))
        hi = r.number(spec.name, row, _col(spec, "Max Load"), v.get("Max Load", ""))
        if lo is not None and hi is not None and lo > hi:
            r.error(spec.name, row, "Min Load", f"Min Load {lo:g} is above Max Load {hi:g}.")
        people[pid] = Person(
            id=pid, name=name, kind=PersonKind.FACULTY, psrn=psrn,
            email=v.get("Email") or None, designation=v.get("Designation") or None,
            teaching_percent=r.number(spec.name, row, _col(spec, "Teaching Allocation"), v.get("Teaching Allocation", "")),
            load_min=lo, load_max=hi, external_load=external,
            external_notes=[details] if details else [],
        )
        seen: set[tuple[str, SectionKind]] = set()
        for column in spec.columns:
            m = _PREF_COLUMN.match(column.header.upper())
            if not m:
                continue
            kind = {"LECTURE": SectionKind.LECTURE, "PRACTICAL": SectionKind.PRACTICAL, "TUTORIAL": SectionKind.TUTORIAL}[m.group(1)]
            rank = int(m.group(2))
            code = _resolve_course(r, spec.name, row, column.header, v.get(column.header, ""), courses)
            if not code:
                continue
            if (code, kind) in seen:
                r.warn(spec.name, row, column.header, f"{code} is already a higher {kind.name.lower()} choice; this repeat is ignored.")
                continue
            seen.add((code, kind))
            consec_header = f"{m.group(1)} Taken Consecutively {rank}"
            consec = r.yesno(spec.name, row, _col(spec, consec_header), v.get(consec_header, ""))
            prefs.append(Preference(person_id=pid, course_code=code, kind=kind, rank=rank,
                                    taught_consecutively=consec, source="faculty sheet"))
    if not people:
        r.error(spec.name, None, None, "No faculty listed.")
    roster = NameMatcher([(p.id, p.name, []) for p in people.values()])
    return people, prefs, roster


def _read_scholars(r: _Reader, courses: dict[str, Course], faculty: NameMatcher):
    from allocator.ingest.workbook import slug

    spec = SCHOLARS
    people: dict[str, Person] = {}
    prefs: list[Preference] = []
    for row, v in r.rows(spec):
        name = v.get("Name", "")
        if not name:
            r.error(spec.name, row, "Name", "Name is required.")
            continue
        pid = "s-" + slug(name)
        if pid in people:
            r.error(spec.name, row, "Name", f"{name} is listed twice.")
            continue
        supervisor = None
        sup_text = v.get("Supervisor's Name", "")
        if sup_text:
            supervisor = _exact_or_matched(faculty, sup_text)
            if supervisor is None:
                r.warn(spec.name, row, "Supervisor's Name", f"{sup_text!r} is not on the Faculty sheet; supervisor left blank.")
        people[pid] = Person(
            id=pid, name=name, kind=PersonKind.SCHOLAR,
            email=v.get("Email Address") or None,
            category=v.get("I am ______ in the next semester") or None,
            supervisor=supervisor,
            load_max=r.number(spec.name, row, _col(spec, "Max Hours"), v.get("Max Hours", "")),
        )
        seen: set[tuple[str, SectionKind]] = set()
        for header, (kind, rank) in _SCHOLAR_PREFS.items():
            code = _resolve_course(r, spec.name, row, header, v.get(header, ""), courses)
            if not code or (code, kind) in seen:
                continue
            seen.add((code, kind))
            prefs.append(Preference(person_id=pid, course_code=code, kind=kind, rank=rank, source="scholar sheet"))
    return people, prefs


def _exact_or_matched(matcher: NameMatcher, text: str) -> str | None:
    key = " ".join(name_tokens(text))
    for pid, name, _ in matcher.roster:
        if " ".join(name_tokens(name)) == key:
            return pid
    return matcher.match(text)


def _read_locks(r: _Reader, courses: dict[str, Course], people: dict[str, Person]) -> list[Lock]:
    matcher = NameMatcher([(p.id, p.name, []) for p in people.values()])
    faculty = NameMatcher([(p.id, p.name, []) for p in people.values() if p.kind == PersonKind.FACULTY])
    locks: list[Lock] = []

    sheet = r.sheets.get(COURSES.name)
    if sheet is not None:
        for row, v in r.rows(COURSES):
            code = extract_code(v.get("Course Number", ""))
            text = v.get("Pre-assigned IC", "")
            if not code or code not in courses or not text:
                continue
            pid = _exact_or_matched(faculty, text)
            if pid is None:
                r.error(COURSES.name, row, "Pre-assigned IC", f"{text!r} is not on the Faculty sheet.")
                continue
            locks.append(Lock(person_id=pid, course_code=code, target=IC_TARGET, note="pre-assigned on upload"))

    for course in courses.values():
        for section in course.sections:
            row, names = r.section_meta.get((course.code, section.id), (None, []))
            n_fac = n_sch = 0
            for i, text in enumerate(names, 1):
                if not text:
                    continue
                pid = _exact_or_matched(matcher, text)
                column = f"Pre-assigned Instructor {i}"
                if pid is None:
                    r.error(SECTIONS.name, row, column, f"{text!r} is not on the Faculty or Research Scholars sheet.")
                    continue
                if people[pid].kind == PersonKind.FACULTY:
                    n_fac += 1
                else:
                    n_sch += 1
                locks.append(Lock(person_id=pid, course_code=course.code, target=section.id, note="pre-assigned on upload"))
            if n_fac > section.faculty_max:
                r.error(SECTIONS.name, row, "Faculty Needed",
                        f"{n_fac} faculty are pre-assigned but Faculty Needed is {section.faculty_max}.")
            if n_sch > section.scholar_max:
                r.error(SECTIONS.name, row, "Scholars Needed",
                        f"{n_sch} scholars are pre-assigned but Scholars Needed allows {section.scholar_max}.")
    return locks

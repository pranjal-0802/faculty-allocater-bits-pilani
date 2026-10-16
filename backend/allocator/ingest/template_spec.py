"""
The department upload template: one definition, used everywhere.

Departments cannot be asked to upload "whatever document they have": every
department lays out its form differently, and a parser that guesses gets
people and courses wrong without saying so. Instead they fill in a fixed
template. Its sheets and headers follow the Mechanical Sem II 2025-26
workbook the process grew out of ("LECTURE Preference 1", "LECTURE Taken
Consecutively 1", "Tute-1", "Section Number", ...), so it reads as the same
form, made strict.

This module is the single source of truth. The template writer builds the
workbook from it, the reader checks uploads against it, the API publishes it
at /api/schema, and docs/upload-format.md describes it. Change a column here
and all four follow.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

TEMPLATE_VERSION = "3"

#: Marker written on the Instructions sheet so an upload can be recognised as
#: this template rather than a free-form workbook.
TEMPLATE_MARKER = "FACULTY ALLOCATION TEMPLATE"


@dataclass(frozen=True)
class Column:
    header: str
    #: text / number / count / yesno / course / course_code / faculty / section / category / level
    kind: str
    required: bool = False
    help: str = ""
    width: int = 16
    #: Value used when the cell is blank, as the department would write it.
    default: str | None = None


@dataclass(frozen=True)
class SheetSpec:
    name: str
    purpose: str
    columns: list[Column] = field(default_factory=list)
    #: Columns frozen on the left when scrolling sideways.
    freeze_cols: int = 1

    def headers(self) -> list[str]:
        return [c.header for c in self.columns]


def _faculty_preference_columns() -> list[Column]:
    cols: list[Column] = []
    for kind, label, count in (("LECTURE", "lecture", 3), ("PRACTICAL", "practical (lab)", 2), ("TUTORIAL", "tutorial", 3)):
        for rank in range(1, count + 1):
            cols.append(Column(
                f"{kind} Preference {rank}", "course",
                help=f"Choice {rank} for a {label} section. Pick from the list (course number - title).",
                width=34,
            ))
            cols.append(Column(
                f"{kind} Taken Consecutively {rank}", "count",
                help=(
                    "How many semesters in a row you have taught this course up to now "
                    "(0 if you did not teach it last time). At 3 the course rotates to "
                    "someone else this semester. Yes/No is accepted, but only a number "
                    "lets the rotation rule apply."
                ),
                width=12,
            ))
    return cols


COURSES = SheetSpec(
    name="Courses",
    purpose=(
        "Every course the department must staff this semester, one row each. "
        "Credits are units as recorded in ERP: L = lecture units, P = lab units "
        "(1 lab unit = 2 contact hours), U = total."
    ),
    columns=[
        Column("Course Number", "course_code", True, "e.g. ME F219. Department code, space, number.", 14),
        Column("Course Title", "text", True, "", 36),
        Column("L", "number", False, "Lecture units.", 6, "0"),
        Column("P", "number", False, "Lab (practical) units.", 6, "0"),
        Column("U", "number", False, "Total units (checked against L + P, informational).", 6),
        Column("Strength", "number", False, "Expected registrations.", 10),
        Column("Category", "category", True,
               "CDC if the course is compulsory for any programme (offeredAs C in the timetable), "
               "EL for a discipline, humanities or open elective (D / H), PROJECT for study, lab "
               "and design projects. A course that is a CDC for one programme and an elective for "
               "another is a CDC. Nobody is given two CDCs.", 10),
        Column("Level", "level", False, "FD (first degree) or HD (higher degree). Blank: HD for G-numbered courses.", 8),
        Column("Service Course", "yesno", False,
               "Yes if its sections are shared out as a department duty "
               "(e.g. BITS F103 labs): nobody is penalised for being given one unasked, "
               "and it does not count as a CDC for the one-CDC rule.", 10, "No"),
        Column("Remarks", "text", False, "", 30),
    ],
)

SECTIONS = SheetSpec(
    name="Sections",
    purpose=(
        "Every section to staff: one row per lecture (L1, L2...), tutorial (T1...) "
        "and practical (P1...). Project courses need no sections. Pre-assigned "
        "faculty are fixed before the solver runs; half-semester splits of a "
        "section between two faculty are arranged by hand, not by the solver."
    ),
    columns=[
        Column("Course Number", "course_code", True, "Must appear on the Courses sheet.", 14),
        Column("Section Number", "section", True, "L1, L2, T1, P3 ... The letter gives the kind.", 10),
        Column("Faculty Needed", "number", False,
               "How many faculty staff this section. Default 1. 0 for a section run by "
               "research scholars only: it is still listed in the result.", 10, "1"),
        Column("Pre-assigned Instructor 1", "faculty", False,
               "Faculty member fixed to this section. Must be a name on the Faculty sheet.", 26),
        Column("Pre-assigned Instructor 2", "faculty", False, "", 26),
        Column("Pre-assigned Instructor 3", "faculty", False, "", 26),
        Column("Room No.", "text", False, "For lab-based courses.", 10),
        Column("Remarks", "text", False, "", 30),
    ],
)

FACULTY = SheetSpec(
    name="Faculty",
    purpose=(
        "Every faculty member available to teach, one row each, with the same "
        "preference columns as the department's faculty form."
    ),
    columns=[
        Column("Faculty Name", "text", True, "Full name, as it should appear on the allocation.", 30),
        Column("PSRN", "text", False, "e.g. H0093. Needed to send the allocation to ERP / the timetable app.", 10),
        Column("Email", "text", False, "", 28),
        Column("Designation", "text", False, "Professor, Associate Professor, Assistant Professor, ...", 18),
        Column("Teaching Allocation", "number", False,
               "Percentage of time opted for teaching, as on the department form. 50 earns the "
               "department's standard load and the target scales with it (70 = 1.4 times, "
               "40 = 0.8 times). Blank = the department's usual figure.", 10),
        Column("Min Load", "number", False, "Override the lowest acceptable load, in units. Blank = from Teaching Allocation.", 8),
        Column("Max Load", "number", False, "Override the target (highest) load, in units. Blank = from Teaching Allocation.", 8),
        Column("External Load", "number", False, "Units already carried outside this allocation (WILP = 3).", 9, "0"),
        Column("External Load Details", "text", False, "What the external load is, e.g. WILP - Product Design.", 26),
        *_faculty_preference_columns(),
        Column("Remarks", "text", False, "Anything else: additional courses, constraints, requests.", 34),
    ],
)

SHEETS = [COURSES, SECTIONS, FACULTY]
REQUIRED_SHEETS = {COURSES.name, SECTIONS.name, FACULTY.name}

CATEGORIES = ["CDC", "EL", "PROJECT"]
LEVELS = ["FD", "HD"]
YES_NO = ["Yes", "No"]

INSTRUCTIONS = [
    "How to fill this template",
    "",
    "1. Courses: list every course the department staffs this semester, with its units and whether it is a CDC or an elective.",
    "2. Sections: list every lecture, tutorial and practical section the timetable defines for those courses (L1, T1, P1...) and how many faculty each needs.",
    "   Pre-assign anyone who is already fixed (they will not be moved). Half-semester splits are arranged by hand afterwards.",
    "3. Faculty: one row per faculty member, with their preferences in the same columns as the usual faculty form.",
    "   Research scholars are not part of this allocation; leave them out.",
    "",
    "Rules",
    "- Do not rename sheets or column headers, and do not add columns before the existing ones.",
    "- Preference cells: pick from the drop-down list (course number - title). A typed course number like ME F219 also works.",
    "- Names on the Sections sheet must match a name on the Faculty sheet.",
    "- ICs are not part of this allocation: the department picks them afterwards, from each course's instructors.",
    "- Taken Consecutively: write the number of semesters in a row (0, 1, 2, 3...). At 3 the course rotates to someone else.",
    "- Nobody is given two CDCs. A course with fewer volunteers than places is flagged before the run.",
    "- Leave a cell blank rather than writing NA, -, nil.",
    "- Yes/No columns accept Yes, No, Y or N.",
    "",
    "On upload every row is checked; problems are reported by sheet, row and column, and nothing is half-imported.",
]


def spec_as_dict() -> dict:
    return {
        "version": TEMPLATE_VERSION,
        "required_sheets": sorted(REQUIRED_SHEETS),
        "sheets": [
            {"name": s.name, "purpose": s.purpose, "columns": [asdict(c) for c in s.columns]}
            for s in SHEETS
        ],
        "categories": CATEGORIES,
        "levels": LEVELS,
    }

"""
Domain model for faculty allocation.

A *problem* is one department's staffing round for one semester: the faculty
who can teach, the courses and sections that need staff, and what each
faculty member said they would like to teach. The solver turns it into an
*allocation*: who takes which section.

Instructors-in-charge are out of scope: the department picks a course's IC
from among its instructors once the allocation is made.

Only faculty are allocated. Research scholars (teaching assistants) are
assigned separately and are out of scope.

These models are both the internal representation and the API schema, so a
problem can be written by hand as JSON or produced by a workbook importer,
and the solver sees the same thing.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class SectionKind(str, Enum):
    """The three kinds of class section at BITS."""

    LECTURE = "L"
    TUTORIAL = "T"
    PRACTICAL = "P"


class CourseCategory(str, Enum):
    """
    Whether a course is compulsory.

    CDC (Compulsory Discipline Course): compulsory for the students of at
    least one programme ("C" in the timetable app). ELECTIVE: a discipline
    (DEL, "D"), humanities (HEL, "H") or open elective. PROJECT: study, lab
    and design projects, which have no sections and are only supervised. The
    department states it on upload.

    Higher-degree courses are CDCs or electives like any other; the level is
    recorded separately (`Course.level`). A course can be compulsory for one
    programme and an elective for another (ME G515 is a CDC for M.E. Thermal
    and an elective for M.E. Design); it is then a CDC.
    """

    CDC = "CDC"
    ELECTIVE = "ELECTIVE"
    PROJECT = "PROJECT"


class CourseLevel(str, Enum):
    """Who the course is offered to: first degree, higher degree or PhD."""

    FD = "FD"
    HD = "HD"
    PHD = "PhD"


class Person(BaseModel):
    """A faculty member who can be given teaching duties."""

    id: str
    name: str
    department: str = ""
    psrn: str | None = None
    email: str | None = None
    designation: str | None = None
    #: "Teaching Allocation" on the department form: the share of time the
    #: person opts to give to teaching. 50 is the standard load; the target
    #: load scales with it (70 asks for 1.4 times the standard). See load.py.
    teaching_percent: float | None = None
    #: Per-person override of the target load band, in units.
    load_min: float | None = None
    load_max: float | None = None
    #: Load the person carries from outside this problem (WILP, other
    #: departments' courses). It counts towards their target but the solver
    #: cannot move it.
    external_load: float = 0.0
    external_notes: list[str] = Field(default_factory=list)
    #: Other spellings of the name seen in source data.
    aliases: list[str] = Field(default_factory=list)


class Section(BaseModel):
    """
    One section of a course, and how many faculty it needs.

    Staffing is a range so a department can say "one or two faculty". A slot
    the solver cannot fill is reported as unfilled rather than making the
    whole run infeasible.
    """

    id: str
    kind: SectionKind
    faculty_min: int = 1
    faculty_max: int = 1
    #: Staff outside this problem already on the section (a scholar, someone
    #: from another department). Informational only.
    external_staff: list[str] = Field(default_factory=list)


class Course(BaseModel):
    """A course and the sections it needs staffed."""

    code: str
    title: str = ""
    department: str = ""
    category: CourseCategory = CourseCategory.CDC
    level: CourseLevel = CourseLevel.FD
    #: Where the category came from (the upload, a department sheet),
    #: and anything worth knowing about it, e.g. which programmes take it as
    #: a CDC. Shown next to the category so a classification can be checked.
    category_source: str = ""
    lecture_units: float = 0.0
    lab_units: float = 0.0
    #: Load credited for one tutorial section: 1, or 2 for the timetable
    #: app's "double tutorial" courses.
    tutorial_units: float = 1.0
    strength: int = 0
    #: A service course is staffed as a department-wide duty (for example the
    #: first-year design-and-prototyping labs). Assigning someone who did not
    #: ask for it is not penalised, tutorials need not be taken by the
    #: course's lecturers, and it does not count towards the one-CDC rule.
    service: bool = False
    #: Every section the course runs, as the department lists them: L1, L2...,
    #: T1..., P1.... A section that needs no faculty (scholar-run) is still
    #: listed, with faculty_min = faculty_max = 0.
    sections: list[Section] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _old_categories(cls, data):
        """Older files had category "HD"; map it across."""
        if isinstance(data, dict) and data.get("category") == "HD":
            data = {**data, "category": "ELECTIVE", "level": data.get("level", "HD")}
        return data

    @property
    def counts_as_cdc(self) -> bool:
        """Whether the course counts towards the one-CDC-per-faculty rule."""
        return self.category == CourseCategory.CDC and not self.service

    def section(self, section_id: str) -> Section | None:
        return next((s for s in self.sections if s.id == section_id), None)


class Preference(BaseModel):
    """
    One stated preference: person wants to teach `kind` of `course_code`.

    `kind` None means any part of the course. Rank 1 is the strongest.
    """

    person_id: str
    course_code: str
    kind: SectionKind | None = None
    rank: int = 1
    #: How many semesters in a row the person has taught this course up to
    #: now. At the rotation limit (3) the course must go to someone else.
    consecutive_count: int | None = None
    #: The old form's Yes/No "taken consecutively", kept when no count was
    #: given. A Yes without a count does not trigger the rotation rule unless
    #: SolverSettings.rotation_yes_without_count says it should.
    taught_consecutively: bool | None = None
    source: str = ""


class LockMode(str, Enum):
    REQUIRE = "require"
    FORBID = "forbid"


#: What older files used as the target of an IC lock or assignment. ICs are
#: out of scope, so such entries are dropped when a problem is loaded.
IC_TARGET = "IC"


class Lock(BaseModel):
    """
    A manual decision the solver must respect.

    `target` is a section id. A REQUIRE lock overrides the rotation and
    one-CDC rules; validation says when it does.
    """

    person_id: str
    course_code: str
    target: str
    mode: LockMode = LockMode.REQUIRE
    note: str = ""


class Assignment(BaseModel):
    """One person on one section."""

    person_id: str
    course_code: str
    #: Section id.
    target: str
    #: Load this assignment contributes, in units (filled in by the load
    #: calculator).
    load: float = 0.0
    #: Free-text qualifier from the source, e.g. "HALF".
    note: str = ""


class UnfilledSlot(BaseModel):
    course_code: str
    section_id: str
    missing: int


class Allocation(BaseModel):
    assignments: list[Assignment] = Field(default_factory=list)
    unfilled: list[UnfilledSlot] = Field(default_factory=list)


class Problem(BaseModel):
    """Everything the solver needs for one department and semester."""

    id: str
    name: str
    department: str = ""
    semester: str = ""
    people: list[Person] = Field(default_factory=list)
    courses: list[Course] = Field(default_factory=list)
    preferences: list[Preference] = Field(default_factory=list)
    locks: list[Lock] = Field(default_factory=list)
    #: The allocation the department actually made by hand, when known. Used
    #: only for comparison, never as an input to the solver.
    reference: Allocation | None = None
    #: Warnings raised while building the problem, e.g. names that could not
    #: be matched. Shown to the user so nothing is dropped silently.
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _drop_scholars(cls, data):
        """
        Problems saved before scholars were taken out of scope list them as
        people with kind "scholar". Drop them, and everything that names
        them, so an old file still loads as a faculty-only problem.
        """
        if not isinstance(data, dict):
            return data
        people = data.get("people") or []
        gone = {
            p.get("id") for p in people if isinstance(p, dict) and p.get("kind") == "scholar"
        }
        if not gone:
            return data
        data = dict(data)
        data["people"] = [p for p in people if not (isinstance(p, dict) and p.get("id") in gone)]
        for key in ("preferences", "locks"):
            data[key] = [x for x in data.get(key) or [] if x.get("person_id") not in gone]
        ref = data.get("reference")
        if isinstance(ref, dict):
            ref = dict(ref)
            ref["assignments"] = [
                a for a in ref.get("assignments") or [] if a.get("person_id") not in gone
            ]
            ref["unfilled"] = [
                u for u in ref.get("unfilled") or [] if u.get("kind", "faculty") == "faculty"
            ]
            data["reference"] = ref
        return data

    @model_validator(mode="after")
    def _drop_ics(self):
        """ICs are out of scope: drop IC locks and IC entries from older files."""
        self.locks = [l for l in self.locks if l.target != IC_TARGET]
        if self.reference:
            self.reference.assignments = [a for a in self.reference.assignments if a.target != IC_TARGET]
            self.reference.unfilled = [u for u in self.reference.unfilled if u.section_id != IC_TARGET]
        return self

    def person(self, person_id: str) -> Person | None:
        return next((p for p in self.people if p.id == person_id), None)

    def course(self, code: str) -> Course | None:
        return next((c for c in self.courses if c.code == code), None)

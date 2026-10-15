"""
Domain model for faculty allocation.

A *problem* is one department's staffing round for one semester: the people
who can teach, the courses and sections that need staff, and what each person
said they would like to teach. The solver turns it into an *allocation*: who
takes which section, and who is the instructor-in-charge (IC) of each course.

These models are both the internal representation and the API schema, so a
problem can be written by hand as JSON, produced by a workbook importer, or
(later) pulled from the timetable database, and the solver sees the same thing.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class PersonKind(str, Enum):
    """Who a person is for staffing purposes."""

    FACULTY = "faculty"
    #: Research scholars (PhD students) who assist with tutorials and labs.
    SCHOLAR = "scholar"


class SectionKind(str, Enum):
    """The three kinds of class section at BITS."""

    LECTURE = "L"
    TUTORIAL = "T"
    PRACTICAL = "P"


class CourseCategory(str, Enum):
    """
    How a course is offered.

    PROJECT covers study, lab and design projects: they have no contact hours,
    only an IC, and count a fixed amount of load.
    """

    CDC = "CDC"
    ELECTIVE = "ELECTIVE"
    HIGHER_DEGREE = "HD"
    PROJECT = "PROJECT"


class Person(BaseModel):
    """A faculty member or research scholar who can be given teaching duties."""

    id: str
    name: str
    kind: PersonKind
    department: str = ""
    psrn: str | None = None
    email: str | None = None
    designation: str | None = None
    #: Share of the person's time set aside for teaching, as recorded on the
    #: department's form. Its exact effect on load is pending confirmation from
    #: the admin, so it is carried but only used through `load_min`/`load_max`
    #: or the settings' `teaching_percent_bands`.
    teaching_percent: float | None = None
    #: For scholars: the id of the supervising faculty member, if known.
    supervisor: str | None = None
    #: For scholars: funding category such as "SRF/JRF" or "Institute Scholar".
    category: str | None = None
    #: Per-person override of the target load band. Faculty load is in units,
    #: scholar load in contact hours per week.
    load_min: float | None = None
    load_max: float | None = None
    #: Load the person carries from outside this problem (WILP, other
    #: departments' courses). It counts against their band but the solver
    #: cannot move it.
    external_load: float = 0.0
    external_notes: list[str] = Field(default_factory=list)
    #: Other spellings of the name seen in source data.
    aliases: list[str] = Field(default_factory=list)


class Section(BaseModel):
    """
    One section of a course, and how many people it needs.

    Staffing is a range so a department can say "one faculty member, and one or
    two scholars". A slot the solver cannot fill is reported as unfilled rather
    than making the whole run infeasible.
    """

    id: str
    kind: SectionKind
    faculty_min: int = 1
    faculty_max: int = 1
    scholar_min: int = 0
    scholar_max: int = 0
    #: Slots already held by people outside this problem, e.g. a scholar lent
    #: by another department. Informational; they are not part of min/max.
    external_staff: list[str] = Field(default_factory=list)


class Course(BaseModel):
    """A course and the sections it needs staffed."""

    code: str
    title: str = ""
    department: str = ""
    category: CourseCategory = CourseCategory.CDC
    lecture_units: float = 0.0
    lab_units: float = 0.0
    #: Load credited to a faculty member for one tutorial section.
    tutorial_units: float = 1.0
    strength: int = 0
    ic_required: bool = True
    #: Extra load credited to the IC for being IC (projects carry all of their
    #: load this way, since they have no sections).
    ic_units: float = 0.0
    #: A service course is staffed as a department-wide duty (for example the
    #: first-year design-and-prototyping labs). Assigning someone who did not
    #: ask for it is not penalised, and tutorials need not be taken by the
    #: course's lecturers.
    service: bool = False
    sections: list[Section] = Field(default_factory=list)


class Preference(BaseModel):
    """
    One stated preference: person wants to teach `kind` of `course_code`.

    `kind` None means any part of the course. Rank 1 is the strongest.
    """

    person_id: str
    course_code: str
    kind: SectionKind | None = None
    rank: int = 1
    #: Whether the person has taught this course in consecutive semesters. Its
    #: meaning for allocation (continuity vs rotation) is pending confirmation,
    #: so the solver weighs it with a configurable signed weight.
    taught_consecutively: bool | None = None
    source: str = ""


class LockMode(str, Enum):
    REQUIRE = "require"
    FORBID = "forbid"


#: Target value of a lock or assignment that refers to the IC role rather than
#: to a section.
IC_TARGET = "IC"


class Lock(BaseModel):
    """
    A manual decision the solver must respect.

    `target` is a section id, or "IC" for the instructor-in-charge role.
    """

    person_id: str
    course_code: str
    target: str
    mode: LockMode = LockMode.REQUIRE
    note: str = ""


class Assignment(BaseModel):
    """One person on one section, or one person as IC of one course."""

    person_id: str
    course_code: str
    #: Section id, or "IC".
    target: str
    #: Load this assignment contributes, in the person's unit (filled in by
    #: the load calculator).
    load: float = 0.0
    #: Free-text qualifier from the source, e.g. "HALF".
    note: str = ""


class UnfilledSlot(BaseModel):
    course_code: str
    section_id: str
    kind: PersonKind
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

    def person(self, person_id: str) -> Person | None:
        return next((p for p in self.people if p.id == person_id), None)

    def course(self, code: str) -> Course | None:
        return next((c for c in self.courses if c.code == code), None)

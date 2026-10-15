"""
Solver settings.

Everything that is policy rather than mechanism lives here, because several
rules are still to be confirmed by the BITS admin: what "Teaching Allocation"
percentages mean for load, whether "taken consecutively" should favour
continuity or rotation, and how hard the tutorial-follows-lecture rule is.
Each of those is a weight or a switch, so confirming a rule is a settings
change, not a code change.

Rewards are positive, penalties are written as positive numbers and subtracted.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class RuleMode(str, Enum):
    OFF = "off"
    #: Priced in the objective: holds unless breaking it buys something more.
    SOFT = "soft"
    #: A hard constraint: the solver may not break it.
    HARD = "hard"


class EligibilityMode(str, Enum):
    #: People are considered for courses they listed, service courses and
    #: locked sections. A section with too few such candidates is opened to
    #: everyone of the right kind (at `unlisted_penalty`), so nothing becomes
    #: unfillable for want of a volunteer. The default: it keeps the model to
    #: the choices a department would actually consider.
    SHORTLIST = "shortlist"
    #: Anyone may be put on any section; sections a person did not ask for
    #: cost `unlisted_penalty`. Largest model, slowest to converge.
    PENALISED = "penalised"
    #: Only courses a person listed, service courses and locked sections.
    STRICT = "strict"


class LoadBand(BaseModel):
    min: float
    max: float


class SolverSettings(BaseModel):
    # ---- Preferences -------------------------------------------------------
    #: Reward for giving a faculty member a course/kind they ranked, by rank.
    faculty_rank_weights: dict[int, int] = Field(
        default_factory=lambda: {1: 100, 2: 70, 3: 45}
    )
    #: Reward for giving a scholar a course/kind they ranked, by rank.
    scholar_rank_weights: dict[int, int] = Field(
        default_factory=lambda: {1: 60, 2: 45}
    )
    #: Reward when the person listed the course, but for another kind of
    #: section (wanted the lecture, got the lab).
    cross_kind_weight: int = 25
    #: Cost of giving someone a course they did not list at all.
    unlisted_penalty: int = 80
    #: Cost of an unlisted service-course duty (normally zero: those are
    #: shared out as a department duty).
    unlisted_service_penalty: int = 0
    #: Signed weight on preferences marked "taken consecutively". Positive
    #: favours continuity, negative favours rotation. Pending admin answer.
    consecutive_weight: int = 0
    #: Reward for putting a scholar on a section their supervisor also takes.
    supervisor_bonus: int = 0

    # ---- Load ---------------------------------------------------------------
    #: Default target band for faculty load, in units.
    faculty_load: LoadBand = Field(default_factory=lambda: LoadBand(min=5, max=7))
    #: Default target band for scholar load, in contact hours per week.
    scholar_load: LoadBand = Field(default_factory=lambda: LoadBand(min=2, max=4))
    #: Optional band per "Teaching Allocation" percentage, e.g. {"70": {..}}.
    #: Empty until the admin confirms what the percentage means.
    teaching_percent_bands: dict[str, LoadBand] = Field(default_factory=dict)
    #: Cost per unit (or hour) below / above a person's band.
    underload_penalty: int = 60
    overload_penalty: int = 120
    #: Cost per unit of the single largest overload, to spread pain evenly.
    max_overload_penalty: int = 80
    #: Absolute ceilings no allocation may cross.
    faculty_load_cap: float = 10.0
    scholar_load_cap: float = 8.0
    #: Contact hours a scholar spends per tutorial section, and per lab unit.
    scholar_tutorial_hours: float = 1.0
    scholar_hours_per_lab_unit: float = 2.0

    # ---- Structure ----------------------------------------------------------
    #: A tutorial's faculty member should be one of the course's lecturers.
    tutorial_follows_lecture: RuleMode = RuleMode.SOFT
    tutorial_follows_lecture_penalty: int = 150
    #: Each lecturer of a course with tutorials should take at least one.
    lecturer_takes_tutorial: RuleMode = RuleMode.SOFT
    lecturer_takes_tutorial_penalty: int = 40
    #: The IC must teach a lecture section of the course (when it has any).
    ic_must_lecture: bool = True
    #: Soft cap on distinct courses per faculty member, and its cost per extra.
    faculty_max_courses: int = 3
    extra_course_penalty: int = 40
    #: Cost of each section slot left unfilled. Far above everything else so a
    #: slot is only left open when nobody at all can take it.
    unfilled_penalty: int = 100_000

    eligibility: EligibilityMode = EligibilityMode.SHORTLIST
    #: In shortlist mode, a section is opened to everyone when it has fewer
    #: than (slots + this) volunteers of a kind.
    shortlist_spare: int = 2

    # ---- Search -------------------------------------------------------------
    #: Identical sections of a course (BITS F103 P1..P10) are interchangeable;
    #: fix an order among them so the search does not revisit relabellings.
    #: Off by default: CP-SAT detects this symmetry itself, and on the ME
    #: replay the explicit ordering slowed the proof from 23s to 51-73s by
    #: fighting the greedy hint.
    symmetry_breaking: bool = False
    #: Start the search from a greedy allocation instead of from nothing.
    greedy_hint: bool = True
    time_limit_seconds: float = 120.0
    #: CP-SAT runs a portfolio of strategies, one per worker, and only at 8
    #: or more does it include the bound-tightening ones that prove
    #: optimality. On the ME replay 4 workers stopped ~7% short of optimal at
    #: 30s with seed-to-seed spread; 8 workers prove the optimum in 20-60s.
    num_workers: int = 8
    random_seed: int = 0

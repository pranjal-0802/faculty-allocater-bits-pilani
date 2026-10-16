"""
Solver settings.

Everything that is policy rather than mechanism lives here, so a rule can be
tightened or relaxed without touching the model. The defaults follow the BITS
admin's answers:

* Load follows "Teaching Allocation": the standard load is the department's
  total teaching units divided by its faculty, a person's target is that
  times percent / 50, and the target is a ceiling to stay under (priced
  heavily, not hard). A blank Teaching Allocation counts as 50.
* A faculty member may hold at most one first-degree CDC (hard). Service
  courses such as the first-year BITS F103 labs are department duties and do
  not count, and neither do higher-degree courses.
* A course taken three semesters in a row may not be taken again (hard).
* ICs are none of the allocator's business: the department picks one from
  each course's instructors after the allocation.
* Courses with fewer volunteers than places are flagged (see shortfall.py).

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
    #: everyone (at `unlisted_penalty`), so nothing becomes unfillable for
    #: want of a volunteer. The default: it keeps the model to the choices a
    #: department would actually consider.
    SHORTLIST = "shortlist"
    #: Anyone may be put on any section; sections a person did not ask for
    #: cost `unlisted_penalty`. Largest model, slowest to converge.
    PENALISED = "penalised"
    #: Only courses a person listed, service courses and locked sections.
    STRICT = "strict"


class RotationScope(str, Enum):
    #: A course taken `rotation_limit` times in a row is barred entirely:
    #: every section of it.
    COURSE = "course"
    #: Only the kind of section the count was recorded against is barred
    #: (a lecture count bars lectures).
    KIND = "kind"


class BaselineMode(str, Enum):
    #: The department's total teaching units divided by its number of
    #: faculty: the average load. Someone at `baseline_percent` gets exactly
    #: this. Because most people opt for less than 50, the targets can add up
    #: to less than the work, and the rest is spread as evenly as possible.
    AVERAGE = "average"
    #: The load at which everyone's targets add up exactly to the work.
    FAIR_SHARE = "fair_share"


class LoadBand(BaseModel):
    min: float
    max: float


class SolverSettings(BaseModel):
    # ---- Preferences -------------------------------------------------------
    #: Reward for giving a faculty member a course/kind they ranked, by rank.
    rank_weights: dict[int, int] = Field(default_factory=lambda: {1: 100, 2: 70, 3: 45})
    #: Reward when the person listed the course, but for another kind of
    #: section (wanted the lecture, got the lab).
    cross_kind_weight: int = 25
    #: Cost of giving someone a course they did not list at all.
    unlisted_penalty: int = 80
    #: Cost of an unlisted service-course duty (normally zero: those are
    #: shared out as a department duty).
    unlisted_service_penalty: int = 0

    # ---- Load (Teaching Allocation) ------------------------------------------
    #: Load, in units, of someone at `baseline_percent` Teaching Allocation.
    #: None: worked out from the problem, as `baseline_mode` says.
    load_baseline: float | None = None
    baseline_mode: BaselineMode = BaselineMode.AVERAGE
    #: The Teaching Allocation that earns exactly the baseline load.
    baseline_percent: float = 50.0
    #: Teaching Allocation assumed for someone who left it blank. None: the
    #: median of what everyone else wrote.
    default_teaching_percent: float | None = 50.0
    #: Round targets to the nearest multiple of this. 0 (default) keeps them
    #: exact: 70% of a 5.42-unit standard load is a 7.59-unit target. Exact
    #: targets take longer to prove optimal (about 12 minutes on the ME
    #: replay, against under a minute at 0.5).
    target_rounding: float = 0.0
    #: The target is a ceiling. The band runs from this far below it up to it.
    load_tolerance_below: float = 1.0
    #: Cost per unit below the band / above the target. Overload is priced
    #: well above any preference so the target holds unless there is no
    #: other way to staff a section.
    underload_penalty: int = 60
    overload_penalty: int = 300
    #: Extra cost per unit of overload beyond this many units, so that when
    #: overload cannot be avoided it is shared out rather than piled on a few.
    overload_steep_after: float = 1.0
    overload_steep_penalty: int = 300
    #: Cost per unit of the single largest overload, to spread it evenly.
    max_overload_penalty: int = 100
    #: Absolute ceiling no allocation may cross (raised for anyone whose own
    #: target or external load is higher).
    load_cap: float = 10.0

    # ---- Hard rules from the admin ------------------------------------------
    #: A faculty member may hold at most one CDC (any role in it: lecture,
    #: tutorial or practical). Service courses do not count.
    one_cdc_per_faculty: bool = True
    #: Semesters in a row after which a course must rotate to someone else.
    #: 0 turns the rule off.
    rotation_limit: int = 3
    rotation_scope: RotationScope = RotationScope.COURSE
    #: How to read a "Taken Consecutively: Yes" with no count (the old form).
    #: False: not enough to bar anyone; the import flags it. True: treat it
    #: as having reached the limit.
    rotation_yes_without_count: bool = False
    #: Whether higher-degree CDCs count towards the one-CDC rule. Off: only
    #: first-degree CDCs do.
    hd_cdcs_count: bool = False

    # ---- Structure ----------------------------------------------------------
    #: A tutorial's faculty member should be one of the course's lecturers.
    tutorial_follows_lecture: RuleMode = RuleMode.SOFT
    tutorial_follows_lecture_penalty: int = 150
    #: Each lecturer of a course with tutorials should take at least one.
    lecturer_takes_tutorial: RuleMode = RuleMode.SOFT
    lecturer_takes_tutorial_penalty: int = 40
    #: Soft cap on distinct courses per faculty member, and its cost per extra.
    max_courses: int = 3
    extra_course_penalty: int = 40
    #: Cost of each section slot left unfilled. Far above everything else so a
    #: slot is only left open when nobody at all can take it.
    unfilled_penalty: int = 100_000

    eligibility: EligibilityMode = EligibilityMode.SHORTLIST
    #: In shortlist mode, a section is opened to everyone when it has fewer
    #: than (slots + this) volunteers.
    shortlist_spare: int = 2

    # ---- Search -------------------------------------------------------------
    #: Identical sections of a course (BITS F103 P1..P10) are interchangeable;
    #: fix an order among them so the search does not revisit relabellings.
    #: Off by default: CP-SAT detects this symmetry itself, and on the ME
    #: replay the explicit ordering slowed the proof by fighting the hint.
    symmetry_breaking: bool = False
    #: Start the search from a greedy allocation instead of from nothing.
    greedy_hint: bool = True
    #: Time is not a constraint: the solver stops as soon as it has proved the
    #: optimum, and this is only a ceiling.
    time_limit_seconds: float = 1800.0
    #: CP-SAT runs a portfolio of strategies, one per worker, and only at 8
    #: or more does it include the bound-tightening ones that prove
    #: optimality.
    num_workers: int = 8
    random_seed: int = 0

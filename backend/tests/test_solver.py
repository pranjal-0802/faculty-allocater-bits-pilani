from conftest import course, elective, faculty, lecture, practical, pref, problem, tutorial

from allocator.domain import IC_TARGET, Lock, LockMode, Problem, SectionKind
from allocator.load import load_targets, person_loads
from allocator.settings import EligibilityMode, RotationScope, RuleMode
from allocator.shortfall import find_shortfalls
from allocator.solver import solve
from allocator.validation import validate_problem


def held(result, person_id):
    return {(a.course_code, a.target) for a in result.allocation.assignments if a.person_id == person_id}


def courses_of(result, person_id):
    return {code for code, _ in held(result, person_id)}


# ---- basics -------------------------------------------------------------------------


def test_each_faculty_gets_their_first_choice_when_they_differ(settings):
    p = problem(
        [faculty("a"), faculty("b")],
        [elective("X", lecture()), elective("Y", lecture())],
        [pref("a", "X"), pref("a", "Y", rank=2), pref("b", "Y"), pref("b", "X", rank=2)],
    )
    result = solve(p, settings)
    assert result.status == "OPTIMAL"
    assert ("X", "L1") in held(result, "a")
    assert ("Y", "L1") in held(result, "b")


def test_every_section_is_staffed_within_its_limits(settings):
    p = problem(
        [faculty("a"), faculty("b")],
        [course("X", lecture(), tutorial("T1"), practical("P1", faculty_min=1, faculty_max=2))],
        [pref("a", "X"), pref("b", "X", SectionKind.PRACTICAL)],
    )
    result = solve(p, settings)
    staff = {}
    for a in result.allocation.assignments:
        staff.setdefault(a.target, []).append(a.person_id)
    assert len(staff["L1"]) == 1 and len(staff["T1"]) == 1
    assert 1 <= len(staff["P1"]) <= 2
    assert not result.allocation.unfilled


def test_unfillable_slot_is_reported_not_infeasible(settings):
    settings.eligibility = EligibilityMode.STRICT
    p = problem([faculty("a")], [elective("X", lecture()), elective("Y", lecture())], [pref("a", "X")])
    result = solve(p, settings)
    assert result.allocation is not None
    assert ("Y", "L1") in {(u.course_code, u.section_id) for u in result.allocation.unfilled}














def test_require_and_forbid_locks(settings):
    p = problem(
        [faculty("a"), faculty("b")],
        [elective("X", lecture()), elective("Y", lecture())],
        [pref("a", "X"), pref("b", "Y")],
        locks=[
            Lock(person_id="b", course_code="X", target="L1", mode=LockMode.REQUIRE),
            Lock(person_id="b", course_code="Y", target="L1", mode=LockMode.FORBID),
        ],
    )
    result = solve(p, settings)
    assert ("X", "L1") in held(result, "b")
    assert ("Y", "L1") not in held(result, "b")


def test_lock_on_impossible_role_is_reported(settings):
    p = problem(
        [faculty("a")],
        [course("X", lecture())],
        locks=[Lock(person_id="a", course_code="X", target="L9")],
    )
    result = solve(p, settings)
    assert result.status == "INVALID"
    assert "Lock cannot be honoured" in result.error


def test_cotaught_lecture_splits_load_and_matches_load_module(settings):
    p = problem(
        [faculty("a"), faculty("b")],
        [course("X", lecture(faculty_min=2, faculty_max=2), lecture_units=3)],
        [pref("a", "X"), pref("b", "X")],
    )
    result = solve(p, settings)
    loads = person_loads(p, result.allocation.assignments)
    assert loads == {"a": 1.5, "b": 1.5}
    assert {r.person_id: r.load for r in result.report.people} == loads


def test_hard_tutorial_follows_lecture(settings):
    settings.tutorial_follows_lecture = RuleMode.HARD
    p = problem(
        [faculty("a"), faculty("b")],
        [course("X", lecture(), tutorial("T1"), tutorial("T2"))],
        [pref("a", "X"), pref("b", "X", SectionKind.TUTORIAL)],
    )
    result = solve(p, settings)
    lecturer = next(a.person_id for a in result.allocation.assignments if a.target == "L1")
    tutors = {a.person_id for a in result.allocation.assignments if a.target.startswith("T")}
    assert tutors <= {lecturer}


def test_shortlist_opens_a_section_nobody_volunteered_for(settings):
    p = problem(
        [faculty("a"), faculty("b"), faculty("c")],
        [elective("X", lecture()), elective("Y", lecture())],
        [pref("a", "X"), pref("b", "X"), pref("c", "X")],
    )
    result = solve(p, settings)
    assert not result.allocation.unfilled
    assert any(a.course_code == "Y" and a.target == "L1" for a in result.allocation.assignments)


def test_shortlist_keeps_unrequested_courses_out_when_volunteers_suffice(settings):
    from allocator.solver.context import build_context

    settings.shortlist_spare = 1
    p = problem(
        [faculty("a"), faculty("b"), faculty("c")],
        [course("X", lecture())],
        [pref("a", "X"), pref("b", "X")],
    )
    ctx = build_context(p, settings)
    assert {pid for pid, _, _ in ctx.x} == {"a", "b"}


def test_hint_and_symmetry_breaking_do_not_change_the_optimum(settings):
    people = [faculty(f"f{i}") for i in range(5)]
    labs = [practical(f"P{i}") for i in range(1, 6)]
    p = problem(
        people,
        [course("X", lecture(), *labs, service=True), elective("Y", lecture())],
        [pref("f0", "X"), pref("f1", "Y"), pref("f2", "X", SectionKind.PRACTICAL)],
    )
    objectives = set()
    for hint in (False, True):
        for sym in (False, True):
            settings.greedy_hint, settings.symmetry_breaking = hint, sym
            objectives.add(solve(p, settings).objective)
    assert len(objectives) == 1


def test_old_problem_files_with_scholars_still_load():
    data = {
        "id": "t", "name": "t",
        "people": [{"id": "a", "name": "A", "kind": "faculty"}, {"id": "s", "name": "S", "kind": "scholar"}],
        "preferences": [{"person_id": "s", "course_code": "X"}, {"person_id": "a", "course_code": "X"}],
        "reference": {"assignments": [{"person_id": "s", "course_code": "X", "target": "T1"}],
                      "unfilled": [{"course_code": "X", "section_id": "T1", "kind": "scholar", "missing": 1}]},
    }
    p = Problem.model_validate(data)
    assert [x.id for x in p.people] == ["a"]
    assert [x.person_id for x in p.preferences] == ["a"]
    assert not p.reference.assignments and not p.reference.unfilled


# ---- Teaching Allocation targets ------------------------------------------------------


def test_ic_data_from_older_files_is_dropped(settings):
    from allocator.domain import Allocation, Assignment

    p = problem([faculty("a")], [course("X", lecture())], [pref("a", "X")],
                locks=[Lock(person_id="a", course_code="X", target=IC_TARGET)],
                reference=Allocation(assignments=[Assignment(person_id="a", course_code="X", target="IC"),
                                                  Assignment(person_id="a", course_code="X", target="L1")]))
    assert p.locks == [] and [a.target for a in p.reference.assignments] == ["L1"]
    result = solve(p, settings)
    assert [a.target for a in result.allocation.assignments] == ["L1"]


def test_wilp_is_external_load():
    from allocator.domain import Allocation, Assignment

    p = problem([faculty("a", external_load=1.5)], [course("X", lecture(), lab_units=1)])
    alloc = Allocation(assignments=[Assignment(person_id="a", course_code="X", target="L1")])
    assert person_loads(p, alloc.assignments) == {"a": 4.5}


def test_standard_load_is_the_department_average(settings):
    # 12 units of lectures over 3 faculty: 4 each at 50%. Blank counts as 50%.
    p = problem(
        [faculty("a", teaching_percent=50), faculty("b", teaching_percent=70), faculty("c")],
        [elective(c, lecture()) for c in "WXYZ"],
    )
    t = load_targets(p, settings)
    assert (t.baseline_source, t.baseline, t.work) == ("average", 4.0, 12)
    assert t.bands["a"].max == 4.0 and t.bands["c"].max == 4.0
    assert t.bands["b"].max == 5.6  # 1.4 x 4, exact
    settings.target_rounding = 0.5
    assert load_targets(p, settings).bands["b"].max == 5.5  # rounded to half units
    assert t.default_percent == 50 and t.assumed == ["c"]





def test_target_scales_with_teaching_allocation(settings):
    settings.load_baseline = 5
    p = problem(
        [faculty("a", teaching_percent=50), faculty("b", teaching_percent=70),
         faculty("c", teaching_percent=40), faculty("d")],
        [],
    )
    t = load_targets(p, settings)
    assert t.bands["a"].max == 5.0
    assert t.bands["b"].max == 7.0  # 1.4 x 5
    assert t.bands["c"].max == 4.0  # 0.8 x 5
    # Left blank: 50%.
    assert t.bands["d"].max == 5.0 and t.assumed == ["d"]
    assert t.bands["b"].min == 6.0  # one unit of tolerance below the target


def test_fair_share_baseline_makes_targets_cover_the_work(settings):
    from allocator.settings import BaselineMode

    settings.baseline_mode = BaselineMode.FAIR_SHARE
    p = problem(
        [faculty("a", teaching_percent=50), faculty("b", teaching_percent=100)],
        [elective(c, lecture()) for c in "WXYZ"],  # 12 units of lectures, no ICs units
    )
    t = load_targets(p, settings)
    assert t.baseline_source == "fair_share"
    assert t.work == 12
    assert t.bands["a"].max == 4.0 and t.bands["b"].max == 8.0


def test_higher_teaching_allocation_gets_more_load(settings):
    settings.load_baseline = 7.5  # targets 6 and 12
    p = problem(
        [faculty("a", teaching_percent=40), faculty("b", teaching_percent=80)],
        [elective(c, lecture()) for c in "UVWXYZ"],  # 18 units: targets 6 and 12
        [pref(pid, c) for pid in "ab" for c in "UVWXYZ"],
    )
    settings.load_cap = 12
    result = solve(p, settings)
    loads = person_loads(p, result.allocation.assignments)
    assert loads == {"a": 6.0, "b": 12.0}


def test_load_is_balanced_when_preferences_tie(settings):
    p = problem(
        [faculty("a"), faculty("b")],
        [elective(c, lecture()) for c in "WXYZ"],
        [pref(pid, c) for pid in "ab" for c in "WXYZ"],
    )
    result = solve(p, settings)
    assert person_loads(p, result.allocation.assignments) == {"a": 6.0, "b": 6.0}


def test_target_is_a_ceiling_over_a_first_choice(settings):
    settings.load_baseline = 3
    p = problem(
        [faculty("a", external_load=3), faculty("b")],
        [elective("X", lecture())],
        [pref("a", "X"), pref("b", "X", rank=2)],
    )
    result = solve(p, settings)
    # a is already at target with WILP; the lecture goes to b despite rank 2.
    assert ("X", "L1") in held(result, "b")


# ---- one CDC per faculty -------------------------------------------------------------------


def test_nobody_holds_two_cdcs(settings):
    p = problem(
        [faculty("a"), faculty("b")],
        [course("C1", lecture()), course("C2", lecture())],
        # a wants both CDCs first; b would rather not teach either.
        [pref("a", "C1"), pref("a", "C2"), pref("b", "C1", rank=3), pref("b", "C2", rank=3)],
    )
    settings.load_baseline = 6
    result = solve(p, settings)
    assert len(courses_of(result, "a")) == 1
    assert len(courses_of(result, "b")) == 1
    assert result.report.cdc_breaches == 0


def test_cdc_and_elective_or_two_electives_are_fine(settings):
    settings.load_baseline = 6
    p = problem(
        [faculty("a")],
        [course("C1", lecture()), elective("E1", lecture()), elective("E2", lecture(), tutorial())],
        [pref("a", "C1"), pref("a", "E1"), pref("a", "E2")],
    )
    settings.load_cap = 12
    result = solve(p, settings)
    assert courses_of(result, "a") == {"C1", "E1", "E2"}


def test_any_role_in_a_cdc_counts(settings):
    settings.load_baseline = 6
    p = problem(
        [faculty("a"), faculty("b")],
        [course("C1", lecture()), course("C2", lecture(), practical("P1"))],
        [pref("a", "C1"), pref("a", "C2", SectionKind.PRACTICAL), pref("b", "C2")],
    )
    result = solve(p, settings)
    # a lectures C1, so cannot also take the C2 lab: b does it.
    assert ("C2", "P1") in held(result, "b")
    assert courses_of(result, "a") == {"C1"}


def test_hd_cdcs_do_not_count_unless_switched_on(settings):
    from allocator.domain import CourseLevel

    settings.load_baseline = 6
    p = problem(
        [faculty("a"), faculty("b", external_load=6)],
        [course("C1", lecture()), course("H1", lecture(), level=CourseLevel.HD)],
        [pref("a", "C1"), pref("a", "H1"), pref("b", "C1", rank=3), pref("b", "H1", rank=3)],
    )
    assert courses_of(solve(p, settings), "a") == {"C1", "H1"}
    settings.hd_cdcs_count = True
    assert len(courses_of(solve(p, settings), "a")) == 1





def test_service_courses_do_not_count_as_cdcs(settings):
    settings.load_baseline = 6
    p = problem(
        [faculty("a")],
        [course("C1", lecture()), course("BITS F103", practical("P1"), service=True)],
        [pref("a", "C1")],
    )
    result = solve(p, settings)
    assert courses_of(result, "a") == {"C1", "BITS F103"}


def test_hand_allocation_with_two_cdcs_is_reported(settings):
    from allocator.domain import Allocation, Assignment
    from allocator.evaluation import report

    p = problem([faculty("a")], [course("C1", lecture()), course("C2", lecture())])
    alloc = Allocation(assignments=[Assignment(person_id="a", course_code="C1", target="L1"),
                                    Assignment(person_id="a", course_code="C2", target="L1")])
    rep = report(p, alloc, settings)
    assert rep.cdc_breaches == 1
    assert "holds 2 CDCs" in rep.rule_breaches[0]


def test_locks_onto_two_cdcs_win_with_a_warning(settings):
    p = problem(
        [faculty("a"), faculty("b")],
        [course("C1", lecture()), course("C2", lecture())],
        [pref("a", "C1"), pref("a", "C2")],
        locks=[Lock(person_id="a", course_code="C1", target="L1"),
               Lock(person_id="a", course_code="C2", target="L1")],
    )
    result = solve(p, settings)
    assert courses_of(result, "a") == {"C1", "C2"}
    assert any("2 CDCs" in w for w in result.warnings)


# ---- rotation -----------------------------------------------------------------------------


def test_course_taken_three_times_running_rotates(settings):
    p = problem(
        [faculty("a"), faculty("b")],
        [elective("X", lecture())],
        [pref("a", "X", consecutive_count=3), pref("b", "X", rank=3)],
    )
    result = solve(p, settings)
    assert ("X", "L1") in held(result, "b")
    assert courses_of(result, "a") == set()


def test_two_times_running_is_allowed(settings):
    p = problem(
        [faculty("a"), faculty("b")],
        [elective("X", lecture())],
        [pref("a", "X", consecutive_count=2), pref("b", "X", rank=3)],
    )
    assert ("X", "L1") in held(solve(p, settings), "a")


def test_rotation_bars_the_whole_course_by_default(settings):
    p = problem(
        [faculty("a"), faculty("b")],
        [elective("X", lecture(), tutorial())],
        [pref("a", "X", consecutive_count=3), pref("a", "X", SectionKind.TUTORIAL), pref("b", "X")],
    )
    assert courses_of(solve(p, settings), "a") == set()
    # With kind scope, only the lecture is barred.
    settings.rotation_scope = RotationScope.KIND
    settings.tutorial_follows_lecture = RuleMode.OFF
    result = solve(p, settings)
    assert held(result, "a") == {("X", "T1")}


def test_rotated_out_person_is_not_used_even_when_nobody_else_asked(settings):
    p = problem(
        [faculty("a"), faculty("b")],
        [elective("X", lecture())],
        [pref("a", "X", consecutive_count=4)],
    )
    result = solve(p, settings)
    assert ("X", "L1") in held(result, "b")


def test_yes_without_a_count_is_flagged_not_enforced(settings):
    p = problem(
        [faculty("a"), faculty("b")],
        [elective("X", lecture())],
        [pref("a", "X", taught_consecutively=True), pref("b", "X", rank=3)],
    )
    result = solve(p, settings)
    assert ("X", "L1") in held(result, "a")
    assert any("without saying how many" in w for w in result.warnings)
    settings.rotation_yes_without_count = True
    assert ("X", "L1") in held(solve(p, settings), "b")


def test_lock_overrides_rotation_with_a_warning(settings):
    p = problem(
        [faculty("a"), faculty("b")],
        [elective("X", lecture())],
        [pref("a", "X", consecutive_count=3)],
        locks=[Lock(person_id="a", course_code="X", target="L1")],
    )
    result = solve(p, settings)
    assert ("X", "L1") in held(result, "a")
    assert any("rotated out" in w for w in validate_problem(p, settings))


# ---- shortfalls --------------------------------------------------------------------------


def test_course_needing_two_with_one_volunteer_is_flagged(settings):
    p = problem(
        [faculty("a"), faculty("b"), faculty("c")],
        [elective("X", lecture("L1"), lecture("L2")), elective("Y", lecture())],
        [pref("a", "X"), pref("b", "Y"), pref("c", "Y")],
    )
    flags = find_shortfalls(p, settings)
    assert [(f.course_code, f.kind, f.places, f.volunteers, f.severity) for f in flags] == [
        ("X", SectionKind.LECTURE, 2, ["A"], "short")
    ]
    result = solve(p, settings)
    assert len(result.shortfalls) == 1 and len(result.shortfalls[0].outcome) == 2


def test_nobody_volunteering_and_rotation_are_flagged(settings):
    p = problem(
        [faculty("a"), faculty("b")],
        [elective("X", lecture()), elective("Y", lecture())],
        [pref("a", "Y", consecutive_count=3), pref("b", "Y")],
    )
    flags = {f.course_code: f for f in find_shortfalls(p, settings)}
    assert flags["X"].severity == "none"
    assert "Y" not in flags  # b still volunteers
    p.preferences = [pref("a", "Y", consecutive_count=3)]
    flags = {f.course_code: f for f in find_shortfalls(p, settings)}
    assert flags["Y"].rotated_out == ["A"] and flags["Y"].severity == "none"


def test_sections_needing_no_faculty_are_kept_and_never_short(settings):
    p = problem([faculty("a")], [course("X", lecture(), practical("P1", faculty_min=0, faculty_max=0))],
                [pref("a", "X")])
    result = solve(p, settings)
    assert not result.allocation.unfilled
    assert [s.id for s in p.courses[0].sections] == ["L1", "P1"]
    assert find_shortfalls(p, settings) == []


def test_lecturers_count_as_tutorial_volunteers_and_service_is_never_short(settings):
    p = problem(
        [faculty("a")],
        [course("X", lecture(), tutorial()), course("S", practical("P1"), practical("P2"), service=True)],
        [pref("a", "X")],
    )
    assert find_shortfalls(p, settings) == []

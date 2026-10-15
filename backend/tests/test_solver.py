from conftest import course, faculty, lecture, practical, pref, problem, scholar, tutorial

from allocator.domain import IC_TARGET, Lock, LockMode, PersonKind, SectionKind
from allocator.load import person_loads
from allocator.settings import EligibilityMode, LoadBand, RuleMode
from allocator.solver import solve


def held(result, person_id):
    return {(a.course_code, a.target) for a in result.allocation.assignments if a.person_id == person_id}


def test_each_faculty_gets_their_first_choice_when_they_differ(settings):
    p = problem(
        [faculty("a"), faculty("b")],
        [course("X", lecture()), course("Y", lecture())],
        [pref("a", "X"), pref("a", "Y", rank=2), pref("b", "Y"), pref("b", "X", rank=2)],
    )
    settings.faculty_load = LoadBand(min=0, max=3)
    result = solve(p, settings)
    assert result.status == "OPTIMAL"
    assert ("X", "L1") in held(result, "a")
    assert ("Y", "L1") in held(result, "b")


def test_every_section_is_staffed_within_its_limits(settings):
    p = problem(
        [faculty("a"), faculty("b"), scholar("s"), scholar("t")],
        [course("X", lecture(), tutorial("T1"), practical("P1", scholar_min=1, scholar_max=2))],
        [pref("a", "X"), pref("s", "X", SectionKind.TUTORIAL), pref("t", "X", SectionKind.PRACTICAL)],
    )
    result = solve(p, settings)
    staff = {}
    for a in result.allocation.assignments:
        staff.setdefault(a.target, []).append(a.person_id)
    assert len([x for x in staff["L1"] if x in ("a", "b")]) == 1
    assert any(x in ("s", "t") for x in staff["T1"])
    assert 1 <= len([x for x in staff["P1"] if x in ("s", "t")]) <= 2
    assert not result.allocation.unfilled


def test_unfillable_slot_is_reported_not_infeasible(settings):
    settings.eligibility = EligibilityMode.STRICT
    p = problem(
        [faculty("a")],
        [course("X", lecture()), course("Y", lecture())],
        [pref("a", "X")],
    )
    result = solve(p, settings)
    assert result.allocation is not None
    missing = {(u.course_code, u.section_id) for u in result.allocation.unfilled}
    assert ("Y", "L1") in missing


def test_ic_teaches_a_lecture_of_the_course(settings):
    p = problem(
        [faculty("a"), faculty("b")],
        [course("X", lecture("L1"), lecture("L2"))],
        [pref("a", "X"), pref("b", "X")],
    )
    result = solve(p, settings)
    ic = next(a.person_id for a in result.allocation.assignments if a.target == IC_TARGET)
    assert any(t.startswith("L") for _, t in held(result, ic))


def test_require_and_forbid_locks(settings):
    p = problem(
        [faculty("a"), faculty("b")],
        [course("X", lecture()), course("Y", lecture())],
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
        [faculty("a"), scholar("s")],
        [course("X", lecture())],
        locks=[Lock(person_id="s", course_code="X", target="L1")],
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
    loads = person_loads(p, result.allocation.assignments, settings)
    assert loads == {"a": 1.5, "b": 1.5}
    reported = {r.person_id: r.load for r in result.report.people}
    assert reported == loads


def test_load_is_balanced_when_preferences_tie(settings):
    # Four 3-unit lectures, two people who both want all of them: an even
    # split (6 / 6) beats 9 / 3 because of the band 5-7.
    p = problem(
        [faculty("a"), faculty("b")],
        [course(c, lecture()) for c in "WXYZ"],
        [pref(pid, c) for pid in "ab" for c in "WXYZ"],
    )
    result = solve(p, settings)
    loads = person_loads(p, result.allocation.assignments, settings)
    assert loads == {"a": 6.0, "b": 6.0}


def test_external_load_counts_against_the_band(settings):
    settings.faculty_load = LoadBand(min=0, max=3)
    p = problem(
        [faculty("a", external_load=3), faculty("b")],
        [course("X", lecture())],
        [pref("a", "X"), pref("b", "X", rank=2)],
    )
    result = solve(p, settings)
    # a is already full with WILP; the lecture goes to b despite rank 2.
    assert ("X", "L1") in held(result, "b")


def test_hard_tutorial_follows_lecture(settings):
    settings.tutorial_follows_lecture = RuleMode.HARD
    p = problem(
        [faculty("a"), faculty("b"), scholar("s"), scholar("t")],
        [course("X", lecture(), tutorial("T1"), tutorial("T2"))],
        [pref("a", "X"), pref("b", "X", SectionKind.TUTORIAL), pref("s", "X", None), pref("t", "X", None)],
    )
    result = solve(p, settings)
    lecturer = next(a.person_id for a in result.allocation.assignments if a.target == "L1")
    tutors = {
        a.person_id
        for a in result.allocation.assignments
        if a.target.startswith("T") and p.person(a.person_id).kind == PersonKind.FACULTY
    }
    assert tutors <= {lecturer}


def test_shortlist_opens_a_section_nobody_volunteered_for(settings):
    settings.eligibility = EligibilityMode.SHORTLIST
    p = problem(
        [faculty("a"), faculty("b"), faculty("c")],
        [course("X", lecture()), course("Y", lecture())],
        [pref("a", "X"), pref("b", "X"), pref("c", "X")],
    )
    result = solve(p, settings)
    # Nobody asked for Y, so it is opened to everyone rather than left empty.
    assert not result.allocation.unfilled
    assert any(a.course_code == "Y" and a.target == "L1" for a in result.allocation.assignments)


def test_shortlist_keeps_unrequested_courses_out_when_volunteers_suffice(settings):
    from allocator.solver.context import build_context

    settings.eligibility = EligibilityMode.SHORTLIST
    settings.shortlist_spare = 1
    p = problem(
        [faculty("a"), faculty("b"), faculty("c")],
        [course("X", lecture())],
        [pref("a", "X"), pref("b", "X")],
    )
    ctx = build_context(p, settings)
    assert {pid for pid, _, _ in ctx.x} == {"a", "b"}


def test_hint_and_symmetry_breaking_do_not_change_the_optimum(settings):
    people = [faculty(f"f{i}") for i in range(4)] + [scholar(f"s{i}") for i in range(6)]
    labs = [practical(f"P{i}", scholar_min=1, scholar_max=2) for i in range(1, 6)]
    p = problem(
        people,
        [course("X", lecture(), *labs, service=True), course("Y", lecture())],
        [pref("f0", "X"), pref("f1", "Y"), pref("s0", "X", SectionKind.PRACTICAL), pref("s1", "X", SectionKind.PRACTICAL, rank=2)],
    )
    objectives = set()
    for hint in (False, True):
        for sym in (False, True):
            settings.greedy_hint, settings.symmetry_breaking = hint, sym
            objectives.add(solve(p, settings).objective)
    assert len(objectives) == 1


def test_scholar_hours_follow_lab_units(settings):
    p = problem(
        [faculty("a"), scholar("s")],
        [course("X", practical("P1"), lab_units=2, ic_required=False)],
        [pref("s", "X", SectionKind.PRACTICAL)],
    )
    result = solve(p, settings)
    loads = person_loads(p, result.allocation.assignments, settings)
    assert loads["s"] == 4.0  # 2 lab units x 2 hours
    assert loads["a"] == 2.0

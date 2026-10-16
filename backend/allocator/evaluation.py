"""
Measure an allocation, and compare two.

The same report runs on the solver's output and on the allocation the
department made by hand, using the same load rule, the same targets and the
same reading of preferences, so the two can be set side by side honestly.
It also lists any breach of the admin's hard rules (two CDCs, a course taken
past the rotation limit): the solver never makes one, but a hand allocation
or a lock can.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from pydantic import BaseModel, Field

from allocator.domain import Allocation, Course, Problem, SectionKind
from allocator.load import annotate_loads, load_targets
from allocator.rules import cdc_holdings, rotation_breaches
from allocator.settings import SolverSettings

#: How an assignment relates to what the person asked for.
MATCH_ORDER = ["rank1", "rank2", "rank3", "rank4+", "cross_kind", "service", "unlisted"]


def classify(problem: Problem, person_id: str, course: Course, kind: SectionKind) -> str:
    prefs = [
        p for p in problem.preferences if p.person_id == person_id and p.course_code == course.code
    ]
    exact = [p for p in prefs if p.kind in (kind, None)]
    if exact:
        rank = min(p.rank for p in exact)
        return f"rank{rank}" if rank <= 3 else "rank4+"
    if prefs:
        return "cross_kind"
    return "service" if course.service else "unlisted"


class PersonRow(BaseModel):
    person_id: str
    name: str
    #: Teaching Allocation used, and whether it was assumed (left blank).
    teaching_percent: float
    percent_assumed: bool = False
    load: float
    external_load: float
    band_min: float
    #: The person's target: the top of their band.
    target: float
    status: str  # under / ok / over
    roles: list[str] = Field(default_factory=list)
    matches: dict[str, int] = Field(default_factory=dict)
    #: CDCs held, when more than one.
    cdcs: list[str] = Field(default_factory=list)


class LoadSummary(BaseModel):
    count: int = 0
    mean: float = 0.0
    stdev: float = 0.0
    min: float = 0.0
    max: float = 0.0
    under: int = 0
    over: int = 0
    #: Total units above people's targets.
    excess: float = 0.0
    #: The largest single overload.
    max_over: float = 0.0


class TargetInfo(BaseModel):
    baseline: float
    baseline_source: str
    baseline_percent: float
    work: float
    target_total: float = 0.0
    #: Units that must go over someone's target whatever the allocation:
    #: the work less everyone's targets.
    unavoidable_excess: float = 0.0
    default_percent: float
    assumed: int


class Report(BaseModel):
    people: list[PersonRow] = Field(default_factory=list)
    #: How many (person, course, kind) holdings fall in each preference bucket.
    preference_matches: dict[str, int] = Field(default_factory=dict)
    #: Share of faculty who listed a first lecture choice and got to lecture it.
    first_lecture_choice_rate: float | None = None
    load: LoadSummary = Field(default_factory=LoadSummary)
    targets: TargetInfo | None = None
    unfilled_slots: int = 0
    #: Breaches of the hard rules, as sentences.
    rule_breaches: list[str] = Field(default_factory=list)
    cdc_breaches: int = 0
    rotation_breaches: int = 0


def report(problem: Problem, allocation: Allocation, settings: SolverSettings) -> Report:
    people = {p.id: p for p in problem.people}
    courses = {c.code: c for c in problem.courses}
    assignments = annotate_loads(problem, allocation.assignments)
    targets = load_targets(problem, settings)

    loads = {p.id: p.external_load for p in problem.people}
    roles: dict[str, list[str]] = defaultdict(list)
    holdings: set[tuple[str, str, SectionKind]] = set()

    for a in assignments:
        if a.person_id not in people or a.course_code not in courses:
            continue
        loads[a.person_id] += a.load
        suffix = f" ({a.note})" if a.note else ""
        roles[a.person_id].append(f"{a.course_code} {a.target}{suffix}")
        section = next((s for s in courses[a.course_code].sections if s.id == a.target), None)
        if section:
            holdings.add((a.person_id, a.course_code, section.kind))

    per_person: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    totals = {m: 0 for m in MATCH_ORDER}
    for pid, code, kind in holdings:
        bucket = classify(problem, pid, courses[code], kind)
        per_person[pid][bucket] += 1
        totals[bucket] += 1

    cdcs = cdc_holdings(problem, allocation, settings)
    rows = []
    for person in problem.people:
        band = targets.bands[person.id]
        load = round(loads[person.id], 2)
        status = "under" if load < band.min - 1e-9 else "over" if load > band.max + 1e-9 else "ok"
        rows.append(
            PersonRow(
                person_id=person.id,
                name=person.name,
                teaching_percent=targets.percent[person.id],
                percent_assumed=person.id in targets.assumed,
                load=load,
                external_load=person.external_load,
                band_min=band.min,
                target=band.max,
                status=status,
                roles=sorted(roles[person.id]),
                matches=dict(per_person[person.id]),
                cdcs=cdcs.get(person.id, []),
            )
        )

    values = [r.load for r in rows]
    summary = LoadSummary()
    if values:
        summary = LoadSummary(
            count=len(values),
            mean=round(statistics.fmean(values), 2),
            stdev=round(statistics.pstdev(values), 2),
            min=min(values),
            max=max(values),
            under=sum(1 for r in rows if r.status == "under"),
            over=sum(1 for r in rows if r.status == "over"),
            excess=round(sum(max(0.0, r.load - r.target) for r in rows), 2),
            max_over=round(max(max(0.0, r.load - r.target) for r in rows), 2),
        )

    name = lambda pid: people[pid].name if pid in people else pid  # noqa: E731
    names = {pid: p.name for pid, p in people.items()}
    fixed = _fixed_courses(problem)
    breaches = [
        f"{name(pid)} holds {len(codes)} CDCs: {', '.join(codes)}."
        + (" (Fixed by the department's pre-assignments.)"
           if sum(c in fixed.get(pid, ()) for c in codes) > 1 else "")
        for pid, codes in cdcs.items()
    ]
    rotation = rotation_breaches(problem, allocation, settings)
    breaches += [f"{bar.describe(names)} Holds {target}." for bar, target in rotation]

    return Report(
        people=rows,
        preference_matches=totals,
        first_lecture_choice_rate=_first_lecture_choice_rate(problem, holdings),
        load=summary,
        targets=TargetInfo(
            baseline=targets.baseline,
            baseline_source=targets.baseline_source,
            baseline_percent=targets.baseline_percent,
            work=targets.work,
            target_total=targets.target_total,
            unavoidable_excess=round(max(0.0, targets.work - targets.target_total), 2),
            default_percent=targets.default_percent,
            assumed=len(targets.assumed),
        ),
        unfilled_slots=sum(u.missing for u in allocation.unfilled),
        rule_breaches=breaches,
        cdc_breaches=len(cdcs),
        rotation_breaches=len(rotation),
    )


def _fixed_courses(problem: Problem) -> dict[str, set[str]]:
    """Person -> courses the department pre-assigned them to."""
    from allocator.domain import LockMode

    out: dict[str, set[str]] = defaultdict(set)
    for lock in problem.locks:
        if lock.mode == LockMode.REQUIRE:
            out[lock.person_id].add(lock.course_code)
    return out


def _first_lecture_choice_rate(
    problem: Problem, holdings: set[tuple[str, str, SectionKind]]
) -> float | None:
    staffed = {c.code for c in problem.courses}
    wanted = {
        p.person_id: p.course_code
        for p in problem.preferences
        if p.rank == 1 and p.kind == SectionKind.LECTURE and p.course_code in staffed
    }
    if not wanted:
        return None
    got = sum(1 for pid, code in wanted.items() if (pid, code, SectionKind.LECTURE) in holdings)
    return round(got / len(wanted), 3)


# ---- comparison -------------------------------------------------------------


class SectionDiff(BaseModel):
    course_code: str
    target: str
    solver: list[str]
    reference: list[str]
    match: bool


class Comparison(BaseModel):
    #: Share of the reference's (person, course, section) assignments the
    #: solver reproduced.
    recall: float | None = None
    #: Share of reference lecture-section faculty the solver reproduced.
    lecture_recall: float | None = None
    #: Share of sections whose staff the two allocations agree on exactly.
    exact_sections: float | None = None
    reference_report: Report | None = None
    sections: list[SectionDiff] = Field(default_factory=list)


def compare(
    problem: Problem,
    allocation: Allocation,
    reference: Allocation,
    settings: SolverSettings,
) -> Comparison:
    people = {p.id: p for p in problem.people}
    courses = {c.code: c for c in problem.courses}

    def staff(alloc: Allocation) -> dict[tuple[str, str], set[str]]:
        out: dict[tuple[str, str], set[str]] = defaultdict(set)
        for a in alloc.assignments:
            if a.person_id in people and a.course_code in courses:
                out[(a.course_code, a.target)].add(a.person_id)
        return out

    mine, theirs = staff(allocation), staff(reference)
    keys = sorted(set(mine) | set(theirs))

    def share(hits: int, total: int) -> float | None:
        return round(hits / total, 3) if total else None

    ref_pairs = [(k, p) for k, ps in theirs.items() for p in ps]
    recall = share(sum(1 for k, p in ref_pairs if p in mine.get(k, set())), len(ref_pairs))
    section_keys = keys
    exact = share(
        sum(1 for k in section_keys if mine.get(k, set()) == theirs.get(k, set())),
        len(section_keys),
    )

    lecture_pairs = [
        (k, p)
        for k, ps in theirs.items()
        if any(s.id == k[1] and s.kind == SectionKind.LECTURE for s in courses[k[0]].sections)
        for p in ps
    ]
    lecture_recall = share(
        sum(1 for k, p in lecture_pairs if p in mine.get(k, set())), len(lecture_pairs)
    )


    name = lambda pid: people[pid].name if pid in people else pid  # noqa: E731
    diffs = [
        SectionDiff(
            course_code=k[0],
            target=k[1],
            solver=sorted(name(p) for p in mine.get(k, set())),
            reference=sorted(name(p) for p in theirs.get(k, set())),
            match=mine.get(k, set()) == theirs.get(k, set()),
        )
        for k in keys
    ]
    return Comparison(
        recall=recall,
        lecture_recall=lecture_recall,
        exact_sections=exact,
        reference_report=report(problem, reference, settings),
        sections=diffs,
    )

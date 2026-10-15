"""
Measure an allocation, and compare two.

The same report runs on the solver's output and on the allocation the
department made by hand, using the same load rule and the same reading of
preferences, so the two can be set side by side honestly.
"""

from __future__ import annotations

import statistics
from collections import defaultdict

from pydantic import BaseModel, Field

from allocator.domain import (
    IC_TARGET,
    Allocation,
    Course,
    Person,
    PersonKind,
    Problem,
    SectionKind,
)
from allocator.load import annotate_loads, load_band
from allocator.settings import SolverSettings

#: How an assignment relates to what the person asked for.
MATCH_ORDER = ["rank1", "rank2", "rank3", "rank4+", "cross_kind", "service", "unlisted"]


def classify(problem: Problem, person_id: str, course: Course, kind: SectionKind) -> str:
    prefs = [
        p
        for p in problem.preferences
        if p.person_id == person_id and p.course_code == course.code
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
    kind: PersonKind
    load: float
    external_load: float
    band_min: float
    band_max: float
    status: str  # under / ok / over
    roles: list[str] = Field(default_factory=list)
    matches: dict[str, int] = Field(default_factory=dict)


class LoadSummary(BaseModel):
    count: int = 0
    mean: float = 0.0
    stdev: float = 0.0
    min: float = 0.0
    max: float = 0.0
    under: int = 0
    over: int = 0


class Report(BaseModel):
    people: list[PersonRow] = Field(default_factory=list)
    #: Per person-kind, how many (person, course, kind) holdings fall in each
    #: preference bucket.
    preference_matches: dict[str, dict[str, int]] = Field(default_factory=dict)
    #: Share of faculty who listed a first lecture choice and got to lecture it.
    first_lecture_choice_rate: float | None = None
    load: dict[str, LoadSummary] = Field(default_factory=dict)
    unfilled_slots: int = 0
    courses_without_ic: list[str] = Field(default_factory=list)


def report(problem: Problem, allocation: Allocation, settings: SolverSettings) -> Report:
    people = {p.id: p for p in problem.people}
    courses = {c.code: c for c in problem.courses}
    assignments = annotate_loads(problem, allocation.assignments, settings)

    loads = {p.id: p.external_load for p in problem.people}
    roles: dict[str, list[str]] = defaultdict(list)
    holdings: set[tuple[str, str, SectionKind]] = set()
    ics: set[str] = set()

    for a in assignments:
        if a.person_id not in people or a.course_code not in courses:
            continue
        loads[a.person_id] += a.load
        suffix = f" ({a.note})" if a.note else ""
        roles[a.person_id].append(f"{a.course_code} {a.target}{suffix}")
        if a.target == IC_TARGET:
            ics.add(a.course_code)
            continue
        section = next((s for s in courses[a.course_code].sections if s.id == a.target), None)
        if section:
            holdings.add((a.person_id, a.course_code, section.kind))

    per_person_matches: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_kind: dict[str, dict[str, int]] = {
        k.value: {m: 0 for m in MATCH_ORDER} for k in PersonKind
    }
    for pid, code, kind in holdings:
        bucket = classify(problem, pid, courses[code], kind)
        per_person_matches[pid][bucket] += 1
        by_kind[people[pid].kind.value][bucket] += 1

    rows = []
    for person in problem.people:
        band = load_band(person, settings)
        load = round(loads[person.id], 2)
        status = "under" if load < band.min - 1e-9 else "over" if load > band.max + 1e-9 else "ok"
        rows.append(
            PersonRow(
                person_id=person.id,
                name=person.name,
                kind=person.kind,
                load=load,
                external_load=person.external_load,
                band_min=band.min,
                band_max=band.max,
                status=status,
                roles=sorted(roles[person.id]),
                matches=dict(per_person_matches[person.id]),
            )
        )

    summaries = {}
    for kind in PersonKind:
        values = [r.load for r in rows if r.kind == kind]
        if not values:
            continue
        summaries[kind.value] = LoadSummary(
            count=len(values),
            mean=round(statistics.fmean(values), 2),
            stdev=round(statistics.pstdev(values), 2),
            min=min(values),
            max=max(values),
            under=sum(1 for r in rows if r.kind == kind and r.status == "under"),
            over=sum(1 for r in rows if r.kind == kind and r.status == "over"),
        )

    first_choice = _first_lecture_choice_rate(problem, holdings)
    return Report(
        people=rows,
        preference_matches=by_kind,
        first_lecture_choice_rate=first_choice,
        load=summaries,
        unfilled_slots=sum(u.missing for u in allocation.unfilled),
        courses_without_ic=sorted(
            c.code for c in problem.courses if c.ic_required and c.code not in ics
        ),
    )


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
    got = sum(
        1 for pid, code in wanted.items() if (pid, code, SectionKind.LECTURE) in holdings
    )
    return round(got / len(wanted), 3)


# ---- comparison -------------------------------------------------------------


class SectionDiff(BaseModel):
    course_code: str
    target: str
    kind: PersonKind
    solver: list[str]
    reference: list[str]
    match: bool


class Comparison(BaseModel):
    #: Share of the reference's (person, course, section) assignments the
    #: solver reproduced, per person kind.
    recall: dict[str, float] = Field(default_factory=dict)
    #: Share of reference lecture-section faculty the solver reproduced.
    lecture_recall: float | None = None
    ic_agreement: float | None = None
    #: Share of sections whose staff the two allocations agree on exactly.
    exact_sections: dict[str, float] = Field(default_factory=dict)
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

    def staff(alloc: Allocation) -> dict[tuple[str, str, PersonKind], set[str]]:
        out: dict[tuple[str, str, PersonKind], set[str]] = defaultdict(set)
        for a in alloc.assignments:
            person = people.get(a.person_id)
            if person and a.course_code in courses:
                out[(a.course_code, a.target, person.kind)].add(a.person_id)
        return out

    mine, theirs = staff(allocation), staff(reference)
    keys = sorted(set(mine) | set(theirs), key=lambda k: (k[0], k[1], k[2].value))

    recall: dict[str, float] = {}
    exact: dict[str, float] = {}
    for kind in PersonKind:
        ref_pairs = [(k, p) for k, ps in theirs.items() if k[2] == kind for p in ps]
        if ref_pairs:
            hit = sum(1 for k, p in ref_pairs if p in mine.get(k, set()))
            recall[kind.value] = round(hit / len(ref_pairs), 3)
        kind_keys = [k for k in keys if k[2] == kind and k[1] != IC_TARGET]
        if kind_keys:
            same = sum(1 for k in kind_keys if mine.get(k, set()) == theirs.get(k, set()))
            exact[kind.value] = round(same / len(kind_keys), 3)

    lecture_keys = [
        k
        for k in theirs
        if k[2] == PersonKind.FACULTY
        and k[1] != IC_TARGET
        and any(
            s.id == k[1] and s.kind == SectionKind.LECTURE
            for s in courses[k[0]].sections
        )
    ]
    lecture_pairs = [(k, p) for k in lecture_keys for p in theirs[k]]
    lecture_recall = (
        round(sum(1 for k, p in lecture_pairs if p in mine.get(k, set())) / len(lecture_pairs), 3)
        if lecture_pairs
        else None
    )

    ic_keys = [k for k in theirs if k[1] == IC_TARGET]
    ic_agreement = (
        round(sum(1 for k in ic_keys if mine.get(k) == theirs[k]) / len(ic_keys), 3)
        if ic_keys
        else None
    )

    name = lambda pid: people[pid].name if pid in people else pid  # noqa: E731
    diffs = [
        SectionDiff(
            course_code=k[0],
            target=k[1],
            kind=k[2],
            solver=sorted(name(p) for p in mine.get(k, set())),
            reference=sorted(name(p) for p in theirs.get(k, set())),
            match=mine.get(k, set()) == theirs.get(k, set()),
        )
        for k in keys
    ]
    return Comparison(
        recall=recall,
        lecture_recall=lecture_recall,
        ic_agreement=ic_agreement,
        exact_sections=exact,
        reference_report=report(problem, reference, settings),
        sections=diffs,
    )

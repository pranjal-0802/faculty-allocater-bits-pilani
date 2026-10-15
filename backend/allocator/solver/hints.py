"""
A greedy starting allocation, handed to CP-SAT as a hint.

The solver begins from a complete, sensible allocation instead of from
nothing, so its first solution is already decent and the time limit goes on
improving it. The hint need not be feasible: CP-SAT repairs or discards it.

The greedy pass staffs the scarcest sections first (fewest candidates per
slot), and for each slot takes the candidate who most wants it, discounted by
how full they already are. It then picks each course's IC among its
lecturers, best preference first.
"""

from __future__ import annotations

from collections import defaultdict

from allocator.domain import PersonKind, SectionKind
from allocator.load import load_band
from allocator.solver.context import ModelContext


def add_greedy_hint(ctx: ModelContext) -> dict[tuple, int]:
    s = ctx.settings
    load = {pid: p.external_load for pid, p in ctx.people.items()}
    band = {pid: load_band(p, s) for pid, p in ctx.people.items()}
    chosen: set[tuple[str, str, str]] = set()

    candidates: dict[tuple[str, str, PersonKind], list[str]] = defaultdict(list)
    for pid, code, sid in ctx.x:
        candidates[(code, sid, ctx.people[pid].kind)].append(pid)

    slots = []
    for course in ctx.problem.courses:
        for section in course.sections:
            for kind, need in (
                (PersonKind.FACULTY, section.faculty_min),
                (PersonKind.SCHOLAR, section.scholar_min),
            ):
                pool = candidates.get((course.code, section.id, kind), [])
                if need > 0 and pool:
                    slots.append((len(pool) / need, course, section, kind, need, pool))
    slots.sort(key=lambda t: t[0])

    for _, course, section, kind, need, pool in slots:
        contribution = _contribution(ctx, course, section, kind)

        def score(pid: str) -> float:
            want = _preference_points(ctx, pid, course, section.kind)
            b = band[pid]
            after = load[pid] + contribution
            if after > b.max:
                fit = -s.overload_penalty * (after - b.max)
            elif load[pid] < b.min:
                fit = s.underload_penalty * min(contribution, b.min - load[pid])
            else:
                fit = 0.0
            return want + fit

        for pid in sorted(pool, key=score, reverse=True)[:need]:
            chosen.add((pid, course.code, section.id))
            load[pid] += contribution

    ic_choice: dict[str, str] = {}
    for course in ctx.problem.courses:
        options = [pid for (pid, code) in ctx.ic if code == course.code]
        lecturers = [
            pid for pid in options
            if any((pid, course.code, sec.id) in chosen for sec in course.sections if sec.kind == SectionKind.LECTURE)
        ]
        pool = lecturers or options
        if pool:
            ic_choice[course.code] = max(
                pool, key=lambda pid: _preference_points(ctx, pid, course, SectionKind.LECTURE)
            )

    hint = {}
    for key, var in ctx.x.items():
        value = int(key in chosen)
        ctx.model.AddHint(var, value)
        hint[key] = value
    for (pid, code), var in ctx.ic.items():
        ctx.model.AddHint(var, int(ic_choice.get(code) == pid))
    return hint


def _contribution(ctx: ModelContext, course, section, kind: PersonKind) -> float:
    s = ctx.settings
    if kind == PersonKind.FACULTY:
        if section.kind == SectionKind.LECTURE:
            return course.lecture_units / max(1, section.faculty_min)
        if section.kind == SectionKind.PRACTICAL:
            return course.lab_units
        return course.tutorial_units
    if section.kind == SectionKind.PRACTICAL:
        return course.lab_units * s.scholar_hours_per_lab_unit
    if section.kind == SectionKind.TUTORIAL:
        return s.scholar_tutorial_hours
    return 0.0


def _preference_points(ctx: ModelContext, pid: str, course, kind: SectionKind) -> float:
    s = ctx.settings
    person = ctx.people[pid]
    weights = s.faculty_rank_weights if person.kind == PersonKind.FACULTY else s.scholar_rank_weights
    prefs = ctx.prefs.get((pid, course.code), [])
    exact = [p for p in prefs if p.kind in (kind, None)]
    if exact:
        return weights.get(min(p.rank for p in exact), min(weights.values(), default=0))
    if prefs:
        return s.cross_kind_weight
    return -(s.unlisted_service_penalty if course.service else s.unlisted_penalty)

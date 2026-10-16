"""
A greedy starting allocation, handed to CP-SAT as a hint.

The solver begins from a complete, sensible allocation instead of from
nothing, so its first solution is already decent and the time limit goes on
improving it. The hint need not be feasible: CP-SAT repairs or discards it.

The greedy pass staffs the scarcest sections first (fewest candidates per
slot), and for each slot takes the candidate who most wants it, discounted by
how far it would push them past their target, and avoiding a second CDC.
"""

from __future__ import annotations

from collections import defaultdict

from allocator.domain import SectionKind
from allocator.rules import counts_as_cdc
from allocator.solver.context import ModelContext

#: Score that rules a candidate out unless nobody else is left.
_AVOID = 10**6


def add_greedy_hint(ctx: ModelContext) -> dict[tuple, int]:
    s = ctx.settings
    load = {pid: p.external_load for pid, p in ctx.people.items()}
    cdcs: dict[str, set[str]] = defaultdict(set)
    chosen: set[tuple[str, str, str]] = set()
    filled: dict[tuple[str, str], int] = defaultdict(int)

    candidates: dict[tuple[str, str], list[str]] = defaultdict(list)
    for pid, code, sid in ctx.x:
        candidates[(code, sid)].append(pid)

    def take(pid, course, section):
        chosen.add((pid, course.code, section.id))
        filled[(course.code, section.id)] += 1
        load[pid] += _contribution(course, section)
        if counts_as_cdc(course, s):
            cdcs[pid].add(course.code)

    slots = []
    for course in ctx.problem.courses:
        for section in course.sections:
            pool = candidates.get((course.code, section.id), [])
            if section.faculty_min > 0 and pool:
                slots.append((len(pool) / section.faculty_min, course, section, pool))
    slots.sort(key=lambda t: t[0])

    for _, course, section, pool in slots:
        need = section.faculty_min - filled[(course.code, section.id)]
        if need <= 0:
            continue
        contribution = _contribution(course, section)

        def score(pid: str) -> float:
            want = _preference_points(ctx, pid, course, section.kind)
            band = ctx.band(pid)
            after = load[pid] + contribution
            if after > band.max:
                fit = -s.overload_penalty * (after - band.max)
            elif load[pid] < band.min:
                fit = s.underload_penalty * min(contribution, band.min - load[pid])
            else:
                fit = 0.0
            if s.one_cdc_per_faculty and counts_as_cdc(course, s) and cdcs[pid] - {course.code}:
                fit -= _AVOID
            return want + fit

        free = [pid for pid in pool if (pid, course.code, section.id) not in chosen]
        for pid in sorted(free, key=score, reverse=True)[:need]:
            take(pid, course, section)

    hint = {}
    for key, var in ctx.x.items():
        value = int(key in chosen)
        ctx.model.AddHint(var, value)
        hint[key] = value
    return hint


def _contribution(course, section) -> float:
    if section.kind == SectionKind.LECTURE:
        return course.lecture_units / max(1, section.faculty_min)
    if section.kind == SectionKind.PRACTICAL:
        return course.lab_units
    return course.tutorial_units


def _preference_points(ctx: ModelContext, pid: str, course, kind: SectionKind) -> float:
    s = ctx.settings
    weights = s.rank_weights
    prefs = ctx.prefs.get((pid, course.code), [])
    exact = [p for p in prefs if p.kind in (kind, None)]
    if exact:
        return weights.get(min(p.rank for p in exact), min(weights.values(), default=0))
    if prefs:
        return s.cross_kind_weight
    return -(s.unlisted_service_penalty if course.service else s.unlisted_penalty)

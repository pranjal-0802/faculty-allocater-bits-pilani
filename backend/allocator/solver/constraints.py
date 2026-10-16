"""
Hard constraints.

Staffing is deliberately *not* hard: a section that nobody can take gets an
`unfilled` count, priced so high it is only used as a last resort, and the
result lists it. A department would rather see "ME F220 P3 needs one more
faculty member" than "infeasible". The hard rules are the ones a person would
never break by hand: a section has no more staff than it allows, locks are
honoured, nobody holds two CDCs, nobody exceeds the absolute load cap.
Rotation is hard too, but it is applied when variables are created
(context.py): a rotated-out section simply has no variable.
"""

from __future__ import annotations

from collections import defaultdict

from allocator.domain import LockMode, SectionKind
from allocator.rules import counts_as_cdc
from allocator.settings import RuleMode
from allocator.solver.context import ModelContext, scaled


class ConstraintError(ValueError):
    """The problem asks for something the model cannot express, e.g. a lock
    on a person who cannot hold that section."""


def build_constraints(ctx: ModelContext) -> None:
    _staffing(ctx)
    _locks(ctx)
    _one_cdc(ctx)
    _tutorial_follows_lecture_hard(ctx)
    _load(ctx)
    if ctx.settings.symmetry_breaking:
        _break_section_symmetry(ctx)


def _break_section_symmetry(ctx: ModelContext) -> None:
    """
    Order interchangeable sections.

    BITS F103 has ten labs that differ only in their number. Any allocation
    can be relabelled ten-factorial ways without changing a single term of
    the objective, and the search otherwise wanders through those copies. We
    give each section a key, the weighted sum of who staffs it, and require
    keys to be non-increasing along the section list: every allocation still
    has exactly as good a relabelled twin that satisfies the order.

    Sections only count as interchangeable when everything the model can see
    about them matches: kind, staffing range and candidate list, and neither
    is named by a lock.
    """
    locked = {(l.course_code, l.target) for l in ctx.problem.locks}
    order = {pid: i + 1 for i, pid in enumerate(ctx.people)}

    for course in ctx.problem.courses:
        groups: dict[tuple, list] = {}
        for section in course.sections:
            if (course.code, section.id) in locked:
                continue
            candidates = frozenset(
                pid for (pid, code, sid) in ctx.x if code == course.code and sid == section.id
            )
            if not candidates:
                continue
            signature = (
                section.kind,
                section.faculty_min,
                section.faculty_max,
                len(section.external_staff),
                candidates,
            )
            groups.setdefault(signature, []).append(section)

        for sections in groups.values():
            if len(sections) < 2:
                continue
            keys = []
            for section in sections:
                terms = [
                    order[pid] * var
                    for (pid, code, sid), var in ctx.x.items()
                    if code == course.code and sid == section.id
                ]
                key = ctx.model.NewIntVar(0, sum(order.values()), f"symkey|{course.code}|{section.id}")
                ctx.model.Add(key == sum(terms))
                keys.append(key)
            for earlier, later in zip(keys, keys[1:]):
                ctx.model.Add(earlier >= later)


def _staffing(ctx: ModelContext) -> None:
    for course in ctx.problem.courses:
        for section in course.sections:
            vars_ = [v for _, v in ctx.section_vars(course, section)]
            if vars_:
                ctx.model.Add(sum(vars_) <= section.faculty_max)
            gap = ctx.unfilled.get((course.code, section.id))
            if gap is not None:
                ctx.model.Add(sum(vars_) + gap >= section.faculty_min)


def _locks(ctx: ModelContext) -> None:
    for lock in ctx.problem.locks:
        if lock.mode != LockMode.REQUIRE:
            continue
        var = ctx.x.get((lock.person_id, lock.course_code, lock.target))
        if var is None:
            person = ctx.people.get(lock.person_id)
            who = person.name if person else lock.person_id
            raise ConstraintError(
                f"Lock cannot be honoured: {who} on {lock.course_code} "
                f"{lock.target}. Check the person, the course, and that the "
                f"section exists and takes faculty."
            )
        ctx.model.Add(var == 1)


def _one_cdc(ctx: ModelContext) -> None:
    """
    At most one CDC per faculty member, counting any section of it.

    Locks win: someone pre-assigned to two CDCs keeps them (validation
    warns), and may not be given a third.
    """
    if not ctx.settings.one_cdc_per_faculty:
        return
    fixed: dict[str, set[str]] = defaultdict(set)
    for lock in ctx.problem.locks:
        course = ctx.courses.get(lock.course_code)
        if lock.mode == LockMode.REQUIRE and course and counts_as_cdc(course, ctx.settings):
            fixed[lock.person_id].add(course.code)

    by_person: dict[str, list] = defaultdict(list)
    for (pid, code), var in ctx.in_course.items():
        if counts_as_cdc(ctx.courses[code], ctx.settings) and code not in fixed.get(pid, ()):
            by_person[pid].append(var)
    for pid in set(by_person) | set(fixed):
        already = len(fixed.get(pid, ()))
        limit = max(1, already) - already  # CDCs still open to this person
        vars_ = by_person.get(pid, [])
        if vars_ and len(vars_) > limit:
            ctx.model.Add(sum(vars_) <= limit)


def _tutorial_follows_lecture_hard(ctx: ModelContext) -> None:
    if ctx.settings.tutorial_follows_lecture != RuleMode.HARD:
        return
    for (pid, code, sid), var in ctx.x.items():
        course = ctx.courses[code]
        if course.service or course.section(sid).kind != SectionKind.TUTORIAL:
            continue
        if not any(s.kind == SectionKind.LECTURE for s in course.sections):
            continue
        lectures = ctx.teaches.get((pid, code, SectionKind.LECTURE))
        if lectures is None:
            ctx.model.Add(var == 0)
        else:
            ctx.model.AddImplication(var, lectures)


def _load(ctx: ModelContext) -> None:
    """
    Define each person's load exactly, and cap it.

    A co-taught lecture splits its units in two. With `shared` true for the
    section, a person on it is credited U - U/2 = U/2, using
    both = x AND shared, which is pinned in both directions.
    """
    terms: dict[str, list] = {pid: [] for pid in ctx.people}

    for (pid, code, sid), var in ctx.x.items():
        course = ctx.courses[code]
        section = course.section(sid)
        if section.kind == SectionKind.LECTURE:
            units = scaled(course.lecture_units)
            terms[pid].append(units * var)
            flag = ctx.shared.get((code, sid))
            if flag is not None:
                both = ctx.model.NewBoolVar(f"cotaught|{pid}|{code}|{sid}")
                ctx.model.AddBoolAnd([var, flag]).OnlyEnforceIf(both)
                ctx.model.AddBoolOr([var.Not(), flag.Not()]).OnlyEnforceIf(both.Not())
                terms[pid].append(-(units // 2) * both)
        elif section.kind == SectionKind.PRACTICAL:
            terms[pid].append(scaled(course.lab_units) * var)
        else:
            terms[pid].append(scaled(course.tutorial_units) * var)

    for pid, person in ctx.people.items():
        external = scaled(person.external_load)
        # Never cap below what the person already carries or is asked for.
        cap = max(scaled(ctx.settings.load_cap), external, scaled(ctx.band(pid).max))
        load = ctx.model.NewIntVar(0, cap, f"load|{pid}")
        ctx.model.Add(load == external + sum(terms[pid]))
        ctx.load[pid] = load

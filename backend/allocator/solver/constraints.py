"""
Hard constraints.

Staffing is deliberately *not* hard: a section that nobody can take gets an
`unfilled` count, priced so high it is only used as a last resort, and the
result lists it. A department would rather see "ME F220 P3 needs one more
scholar" than "infeasible". The hard rules are the ones a person would never
break by hand: a section has no more staff than it allows, a course has one
IC, locks are honoured, and nobody exceeds the absolute load cap.
"""

from __future__ import annotations

from allocator.domain import IC_TARGET, LockMode, PersonKind, SectionKind
from allocator.load import load_band
from allocator.settings import RuleMode
from allocator.solver.context import ModelContext, scaled


class ConstraintError(ValueError):
    """The problem asks for something the model cannot express, e.g. a lock
    on a person who cannot hold that role."""


def build_constraints(ctx: ModelContext) -> None:
    _staffing(ctx)
    _ic(ctx)
    _locks(ctx)
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
                section.faculty_min, section.faculty_max,
                section.scholar_min, section.scholar_max,
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
            for kind, lo, hi in (
                (PersonKind.FACULTY, section.faculty_min, section.faculty_max),
                (PersonKind.SCHOLAR, section.scholar_min, section.scholar_max),
            ):
                vars_ = [v for _, v in ctx.section_vars(course, section, kind)]
                if vars_:
                    ctx.model.Add(sum(vars_) <= hi)
                gap = ctx.unfilled.get((course.code, section.id, kind))
                if gap is not None:
                    ctx.model.Add(sum(vars_) + gap >= lo)


def _ic(ctx: ModelContext) -> None:
    for course in ctx.problem.courses:
        if not course.ic_required:
            continue
        candidates = [(pid, v) for (pid, code), v in ctx.ic.items() if code == course.code]
        if not candidates:
            continue
        ctx.model.AddExactlyOne(v for _, v in candidates)

        has_lectures = any(s.kind == SectionKind.LECTURE for s in course.sections)
        if not (ctx.settings.ic_must_lecture and has_lectures):
            continue
        for pid, var in candidates:
            lectures = ctx.teaches.get((pid, course.code, SectionKind.LECTURE))
            if lectures is None:
                # Only reachable through a REQUIRE lock on the IC role for
                # someone who cannot lecture it; the lock wins, and the
                # result will show the IC teaching no lecture.
                continue
            ctx.model.AddImplication(var, lectures)


def _locks(ctx: ModelContext) -> None:
    for lock in ctx.problem.locks:
        if lock.mode != LockMode.REQUIRE:
            continue
        if lock.target == IC_TARGET:
            var = ctx.ic.get((lock.person_id, lock.course_code))
        else:
            var = ctx.x.get((lock.person_id, lock.course_code, lock.target))
        if var is None:
            person = ctx.people.get(lock.person_id)
            who = person.name if person else lock.person_id
            raise ConstraintError(
                f"Lock cannot be honoured: {who} on {lock.course_code} "
                f"{lock.target}. Check the person, the course and that the "
                f"section takes {person.kind.value if person else 'this kind of'} staff."
            )
        ctx.model.Add(var == 1)


def _tutorial_follows_lecture_hard(ctx: ModelContext) -> None:
    if ctx.settings.tutorial_follows_lecture != RuleMode.HARD:
        return
    for (pid, code, sid), var in ctx.x.items():
        course = ctx.courses[code]
        if course.service or ctx.people[pid].kind != PersonKind.FACULTY:
            continue
        section = next(s for s in course.sections if s.id == sid)
        if section.kind != SectionKind.TUTORIAL:
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
    s = ctx.settings
    terms: dict[str, list] = {pid: [] for pid in ctx.people}

    for (pid, code, sid), var in ctx.x.items():
        person = ctx.people[pid]
        course = ctx.courses[code]
        section = next(sec for sec in course.sections if sec.id == sid)

        if person.kind == PersonKind.FACULTY:
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
        else:
            if section.kind == SectionKind.PRACTICAL:
                hours = course.lab_units * s.scholar_hours_per_lab_unit
            elif section.kind == SectionKind.TUTORIAL:
                hours = s.scholar_tutorial_hours
            else:
                hours = 0.0
            terms[pid].append(scaled(hours) * var)

    for (pid, code), var in ctx.ic.items():
        units = scaled(ctx.courses[code].ic_units)
        if units:
            terms[pid].append(units * var)

    for pid, person in ctx.people.items():
        external = scaled(person.external_load)
        cap_units = (
            s.faculty_load_cap if person.kind == PersonKind.FACULTY else s.scholar_load_cap
        )
        band = load_band(person, s)
        # Never cap below what the person already carries or is asked for.
        cap = max(scaled(cap_units), external, scaled(band.max))
        load = ctx.model.NewIntVar(0, cap, f"load|{pid}")
        ctx.model.Add(load == external + sum(terms[pid]))
        ctx.load[pid] = load

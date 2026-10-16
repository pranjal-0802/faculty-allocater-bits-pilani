"""
Objective: one weighted sum, maximised.

Preference rewards are paid once per (person, course, kind), not once per
section. Paying per section would let the solver please someone by handing
them every section of their favourite course, which is the opposite of what a
department wants; load is governed by the person's target instead.

Load terms are in scaled units (see SCALE), so their weights are per tenth of
a unit; every other term is multiplied by SCALE to match. A weight in the
settings therefore means the same thing whichever term it sits on: points per
whole unit of load, or points per occurrence.
"""

from __future__ import annotations

from collections import defaultdict

from allocator.domain import SectionKind
from allocator.settings import RuleMode
from allocator.solver.context import SCALE, ModelContext, scaled

Term = tuple[object, int]


class ObjectiveBuilder:
    def __init__(self, ctx: ModelContext):
        self.ctx = ctx
        self.model = ctx.model
        self.s = ctx.settings
        #: Each term's contribution, by name, for reporting.
        self.groups: dict[str, list[Term]] = defaultdict(list)

    def build(self) -> None:
        self._preferences()
        self._load_target()
        self._tutorial_follows_lecture_soft()
        self._lecturer_takes_tutorial()
        self._distinct_courses()
        self._unfilled()

        terms = [t for group in self.groups.values() for t in group]
        if terms:
            self.model.Maximize(sum(coef * var for var, coef in terms))

    # ---- preferences --------------------------------------------------------

    def _preferences(self) -> None:
        weights = self.s.rank_weights
        for (pid, code, kind), var in self.ctx.teaches.items():
            course = self.ctx.courses[code]
            prefs = self.ctx.prefs.get((pid, code), [])
            exact = [p for p in prefs if p.kind in (kind, None)]

            if exact:
                rank = min(p.rank for p in exact)
                reward = weights.get(rank, min(weights.values(), default=0))
                self.groups["preference"].append((var, SCALE * reward))
            elif prefs:
                self.groups["preference"].append((var, SCALE * self.s.cross_kind_weight))
            else:
                penalty = (
                    self.s.unlisted_service_penalty if course.service else self.s.unlisted_penalty
                )
                if penalty:
                    self.groups["unlisted"].append((var, -SCALE * penalty))

    # ---- load ---------------------------------------------------------------

    def _load_target(self) -> None:
        """
        Keep each person inside their band: [target - tolerance, target].

        The target is a ceiling ("try not to violate"), so going over it costs
        far more than falling short. Overload past `overload_steep_after`
        costs more again, and the single worst overload costs extra, so that
        when the targets add up to less than the work (most people opt for
        under 50%) the excess is shared out instead of piled on a few.
        """
        steep_after = scaled(self.s.overload_steep_after)
        worst = self.model.NewIntVar(0, 10**6, "max_overload")
        for pid, load in self.ctx.load.items():
            band = self.ctx.band(pid)
            lo, hi = scaled(band.min), scaled(band.max)

            under = self.model.NewIntVar(0, max(lo, 0), f"under|{pid}")
            over = self.model.NewIntVar(0, 10**6, f"over|{pid}")
            # One-sided: both are penalised, so the solver drives each down
            # to the true shortfall or excess on its own.
            self.model.Add(under >= lo - load)
            self.model.Add(over >= load - hi)
            self.model.Add(worst >= over)
            if self.s.overload_steep_penalty:
                steep = self.model.NewIntVar(0, 10**6, f"steep_over|{pid}")
                self.model.Add(steep >= over - steep_after)
                self.groups["overload"].append((steep, -self.s.overload_steep_penalty))

            self.groups["underload"].append((under, -self.s.underload_penalty))
            self.groups["overload"].append((over, -self.s.overload_penalty))
        self.groups["max_overload"].append((worst, -self.s.max_overload_penalty))

    # ---- structure ----------------------------------------------------------

    def _tutorial_follows_lecture_soft(self) -> None:
        if self.s.tutorial_follows_lecture != RuleMode.SOFT:
            return
        for (pid, code, sid), var in self.ctx.x.items():
            course = self.ctx.courses[code]
            if course.service:
                continue
            section = next(s for s in course.sections if s.id == sid)
            if section.kind != SectionKind.TUTORIAL:
                continue
            if not any(s.kind == SectionKind.LECTURE for s in course.sections):
                continue
            lectures = self.ctx.teaches.get((pid, code, SectionKind.LECTURE))
            stray = self.model.NewBoolVar(f"stray_tutorial|{pid}|{code}|{sid}")
            if lectures is None:
                self.model.Add(stray == var)
            else:
                # stray >= var - lectures; stray is penalised so it settles at
                # exactly "takes this tutorial without lecturing the course".
                self.model.Add(stray >= var - lectures)
            self.groups["stray_tutorial"].append(
                (stray, -SCALE * self.s.tutorial_follows_lecture_penalty)
            )

    def _lecturer_takes_tutorial(self) -> None:
        if self.s.lecturer_takes_tutorial == RuleMode.OFF:
            return
        for course in self.ctx.problem.courses:
            if course.service:
                continue
            has_tutorial_slots = any(
                s.kind == SectionKind.TUTORIAL and s.faculty_max > 0 for s in course.sections
            )
            if not has_tutorial_slots:
                continue
            for (pid, code, kind), lectures in self.ctx.teaches.items():
                if code != course.code or kind != SectionKind.LECTURE:
                    continue
                tutorials = self.ctx.teaches.get((pid, code, SectionKind.TUTORIAL))
                if self.s.lecturer_takes_tutorial == RuleMode.HARD:
                    if tutorials is None:
                        self.model.Add(lectures == 0)
                    else:
                        self.model.AddImplication(lectures, tutorials)
                    continue
                missing = self.model.NewBoolVar(f"no_tutorial|{pid}|{code}")
                if tutorials is None:
                    self.model.Add(missing == lectures)
                else:
                    self.model.Add(missing >= lectures - tutorials)
                self.groups["lecturer_without_tutorial"].append(
                    (missing, -SCALE * self.s.lecturer_takes_tutorial_penalty)
                )

    def _distinct_courses(self) -> None:
        limit = self.s.max_courses
        if limit <= 0 or not self.s.extra_course_penalty:
            return
        by_person: dict[str, list] = defaultdict(list)
        for (pid, _), var in self.ctx.in_course.items():
            by_person[pid].append(var)
        for pid, vars_ in by_person.items():
            if len(vars_) <= limit:
                continue
            extra = self.model.NewIntVar(0, len(vars_), f"extra_courses|{pid}")
            self.model.Add(extra >= sum(vars_) - limit)
            self.groups["extra_courses"].append((extra, -SCALE * self.s.extra_course_penalty))

    def _unfilled(self) -> None:
        for var in self.ctx.unfilled.values():
            self.groups["unfilled"].append((var, -SCALE * self.s.unfilled_penalty))


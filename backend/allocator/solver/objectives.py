"""
Objective: one weighted sum, maximised.

Preference rewards are paid once per (person, course, kind), not once per
section. Paying per section would let the solver please someone by handing
them every section of their favourite course, which is the opposite of what a
department wants; load is governed by the load band instead.

Load terms are in scaled units (see SCALE), so their weights are per tenth of
a unit; every other term is multiplied by SCALE to match. A weight in the
settings therefore means the same thing whichever term it sits on: points per
whole unit of load, or points per occurrence.
"""

from __future__ import annotations

from collections import defaultdict

from allocator.domain import PersonKind, SectionKind
from allocator.load import load_band
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
        self._supervisor()
        self._load_band()
        self._tutorial_follows_lecture_soft()
        self._lecturer_takes_tutorial()
        self._distinct_courses()
        self._unfilled()

        terms = [t for group in self.groups.values() for t in group]
        if terms:
            self.model.Maximize(sum(coef * var for var, coef in terms))

    # ---- preferences --------------------------------------------------------

    def _preferences(self) -> None:
        for (pid, code, kind), var in self.ctx.teaches.items():
            person = self.ctx.people[pid]
            course = self.ctx.courses[code]
            weights = (
                self.s.faculty_rank_weights
                if person.kind == PersonKind.FACULTY
                else self.s.scholar_rank_weights
            )
            prefs = self.ctx.prefs.get((pid, code), [])
            exact = [p for p in prefs if p.kind in (kind, None)]

            if exact:
                best = min(exact, key=lambda p: p.rank)
                reward = weights.get(best.rank, min(weights.values(), default=0))
                self.groups["preference"].append((var, SCALE * reward))
                if best.taught_consecutively and self.s.consecutive_weight:
                    self.groups["consecutive"].append(
                        (var, SCALE * self.s.consecutive_weight)
                    )
            elif prefs:
                self.groups["preference"].append((var, SCALE * self.s.cross_kind_weight))
            else:
                penalty = (
                    self.s.unlisted_service_penalty
                    if course.service
                    else self.s.unlisted_penalty
                )
                if penalty:
                    self.groups["unlisted"].append((var, -SCALE * penalty))

    def _supervisor(self) -> None:
        if not self.s.supervisor_bonus:
            return
        for (pid, code, sid), var in self.ctx.x.items():
            person = self.ctx.people[pid]
            if person.kind != PersonKind.SCHOLAR or not person.supervisor:
                continue
            boss = self.ctx.x.get((person.supervisor, code, sid))
            if boss is None:
                continue
            together = self.model.NewBoolVar(f"with_supervisor|{pid}|{code}|{sid}")
            self.model.AddImplication(together, var)
            self.model.AddImplication(together, boss)
            self.groups["supervisor"].append((together, SCALE * self.s.supervisor_bonus))

    # ---- load ---------------------------------------------------------------

    def _load_band(self) -> None:
        worst = {
            PersonKind.FACULTY: self.model.NewIntVar(0, 10**6, "max_overload|faculty"),
            PersonKind.SCHOLAR: self.model.NewIntVar(0, 10**6, "max_overload|scholar"),
        }
        for pid, load in self.ctx.load.items():
            person = self.ctx.people[pid]
            band = load_band(person, self.s)
            lo, hi = scaled(band.min), scaled(band.max)

            under = self.model.NewIntVar(0, max(lo, 0), f"under|{pid}")
            over = self.model.NewIntVar(0, 10**6, f"over|{pid}")
            # One-sided: both are penalised, so the solver drives each down
            # to the true shortfall or excess on its own.
            self.model.Add(under >= lo - load)
            self.model.Add(over >= load - hi)
            self.model.Add(worst[person.kind] >= over)

            self.groups["underload"].append((under, -self.s.underload_penalty))
            self.groups["overload"].append((over, -self.s.overload_penalty))

        for var in worst.values():
            self.groups["max_overload"].append((var, -self.s.max_overload_penalty))

    # ---- structure ----------------------------------------------------------

    def _tutorial_follows_lecture_soft(self) -> None:
        if self.s.tutorial_follows_lecture != RuleMode.SOFT:
            return
        for (pid, code, sid), var in self.ctx.x.items():
            course = self.ctx.courses[code]
            if course.service or self.ctx.people[pid].kind != PersonKind.FACULTY:
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
                s.kind == SectionKind.TUTORIAL and s.faculty_max > 0
                for s in course.sections
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
        limit = self.s.faculty_max_courses
        if limit <= 0 or not self.s.extra_course_penalty:
            return
        by_person: dict[str, list] = defaultdict(list)
        for (pid, _), var in self.ctx.in_course.items():
            if self.ctx.people[pid].kind == PersonKind.FACULTY:
                by_person[pid].append(var)
        for pid, vars_ in by_person.items():
            if len(vars_) <= limit:
                continue
            extra = self.model.NewIntVar(0, len(vars_), f"extra_courses|{pid}")
            self.model.Add(extra >= sum(vars_) - limit)
            self.groups["extra_courses"].append(
                (extra, -SCALE * self.s.extra_course_penalty)
            )

    def _unfilled(self) -> None:
        for var in self.ctx.unfilled.values():
            self.groups["unfilled"].append((var, -SCALE * self.s.unfilled_penalty))

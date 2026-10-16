"""
Run the allocation model end to end: build, solve, extract.
"""

from __future__ import annotations

import time

from ortools.sat.python import cp_model
from pydantic import BaseModel, Field

from allocator.domain import Allocation, Assignment, Problem, UnfilledSlot
from allocator.evaluation import Comparison, Report, compare, report
from allocator.load import annotate_loads
from allocator.settings import SolverSettings
from allocator.shortfall import Shortfall, find_shortfalls, with_outcome
from allocator.solver.constraints import ConstraintError, build_constraints
from allocator.solver.context import SCALE, build_context
from allocator.solver.hints import add_greedy_hint
from allocator.solver.objectives import ObjectiveBuilder
from allocator.validation import validate_problem


class SolveResult(BaseModel):
    status: str
    error: str | None = None
    allocation: Allocation | None = None
    report: Report | None = None
    comparison: Comparison | None = None
    objective: float | None = None
    best_bound: float | None = None
    #: Realised value of each objective group, in settings points.
    objective_breakdown: dict[str, float] = Field(default_factory=dict)
    build_seconds: float = 0.0
    solve_seconds: float = 0.0
    model_stats: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    #: Courses with fewer volunteers than places, and who took the places.
    shortfalls: list[Shortfall] = Field(default_factory=list)


def solve(problem: Problem, settings: SolverSettings | None = None) -> SolveResult:
    settings = settings or SolverSettings()
    warnings = validate_problem(problem, settings)
    shortfalls = find_shortfalls(problem, settings)

    started = time.perf_counter()
    try:
        ctx = build_context(problem, settings)
        build_constraints(ctx)
        objective = ObjectiveBuilder(ctx)
        objective.build()
        if settings.greedy_hint:
            add_greedy_hint(ctx)
    except ConstraintError as exc:
        return SolveResult(status="INVALID", error=str(exc), warnings=warnings, shortfalls=shortfalls)
    built = time.perf_counter()

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = settings.time_limit_seconds
    solver.parameters.num_search_workers = max(1, settings.num_workers)
    solver.parameters.random_seed = settings.random_seed
    status = solver.Solve(ctx.model)
    status_name = solver.StatusName(status)

    stats = {
        "decision_vars": len(ctx.x),
        "constraints": len(ctx.model.Proto().constraints),
        "variables": len(ctx.model.Proto().variables),
    }
    timing = {
        "build_seconds": round(built - started, 3),
        "solve_seconds": round(solver.WallTime(), 3),
    }

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        message = {
            cp_model.INFEASIBLE: (
                "No allocation satisfies the hard rules. With soft staffing this "
                "only happens through locks, load caps or a hard structural rule: "
                "check locks first (onto two CDCs, a rotated-out course, or past "
                "someone's load cap)."
            ),
            cp_model.MODEL_INVALID: f"Invalid model: {ctx.model.Validate()}",
        }.get(status, f"Solver stopped without a solution ({status_name}).")
        return SolveResult(
            status=status_name, error=message, model_stats=stats, warnings=warnings,
            shortfalls=shortfalls, **timing,
        )

    allocation = _extract(ctx, solver, problem)
    breakdown = {
        name: round(sum(solver.Value(v) * c for v, c in terms) / SCALE, 2)
        for name, terms in objective.groups.items()
    }
    comparison = (
        compare(problem, allocation, problem.reference, settings) if problem.reference else None
    )
    return SolveResult(
        status=status_name,
        allocation=allocation,
        report=report(problem, allocation, settings),
        comparison=comparison,
        objective=solver.ObjectiveValue() / SCALE,
        best_bound=solver.BestObjectiveBound() / SCALE,
        objective_breakdown=breakdown,
        model_stats=stats,
        warnings=warnings,
        shortfalls=with_outcome(shortfalls, problem, allocation),
        **timing,
    )


def _extract(ctx, solver, problem: Problem) -> Allocation:
    assignments = [
        Assignment(person_id=pid, course_code=code, target=sid)
        for (pid, code, sid), var in ctx.x.items()
        if solver.Value(var)
    ]
    unfilled = [
        UnfilledSlot(course_code=code, section_id=sid, missing=solver.Value(var))
        for (code, sid), var in ctx.unfilled.items()
        if solver.Value(var) > 0
    ]
    order = {c.code: i for i, c in enumerate(problem.courses)}
    sections = {(c.code, s.id): i for c in problem.courses for i, s in enumerate(c.sections)}
    assignments.sort(key=lambda a: (order.get(a.course_code, 0), sections.get((a.course_code, a.target), -1), a.person_id))
    return Allocation(assignments=annotate_loads(problem, assignments), unfilled=unfilled)

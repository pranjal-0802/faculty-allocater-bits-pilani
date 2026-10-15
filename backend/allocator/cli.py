"""
Command line.

    python -m allocator.cli import WORKBOOK.xlsx --department ME --semester "2025-26 Sem II" -o problem.json
    python -m allocator.cli solve problem.json [--time-limit 30] [-o result.json]
    python -m allocator.cli reference problem.json

`reference` scores the department's own allocation with the same report the
solver's output gets, which is the baseline every run is judged against.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from allocator.domain import Allocation, Problem
from allocator.evaluation import Report, report
from allocator.ingest.workbook import ImportOptions, import_workbook
from allocator.settings import SolverSettings
from allocator.solver import solve


def _load_problem(path: str) -> Problem:
    return Problem.model_validate_json(Path(path).read_text())


def _print_report(title: str, rep: Report) -> None:
    print(f"\n== {title}")
    for kind, s in rep.load.items():
        print(
            f"  {kind:8} load mean {s.mean:5.2f}  sd {s.stdev:4.2f}  "
            f"range {s.min:g}-{s.max:g}  under {s.under}  over {s.over}  (n={s.count})"
        )
    for kind, buckets in rep.preference_matches.items():
        shown = ", ".join(f"{k} {v}" for k, v in buckets.items() if v)
        print(f"  {kind:8} preference matches: {shown or '-'}")
    if rep.first_lecture_choice_rate is not None:
        print(f"  faculty who got their first lecture choice: {rep.first_lecture_choice_rate:.0%}")
    print(f"  unfilled slots: {rep.unfilled_slots}   courses without IC: {len(rep.courses_without_ic)}")


def cmd_import(args: argparse.Namespace) -> int:
    problem = import_workbook(
        args.workbook,
        ImportOptions(
            department=args.department,
            name=args.name or "",
            semester=args.semester or "",
            staffing_from_reference=not args.default_staffing,
        ),
    )
    text = problem.model_dump_json(indent=2)
    if args.output:
        Path(args.output).write_text(text)
    scholars = sum(1 for p in problem.people if p.kind.value == "scholar")
    sections = sum(len(c.sections) for c in problem.courses)
    print(
        f"{problem.id}: {len(problem.people) - scholars} faculty, {scholars} scholars, "
        f"{len(problem.courses)} courses, {sections} sections, "
        f"{len(problem.preferences)} preferences, "
        f"{len(problem.reference.assignments) if problem.reference else 0} reference assignments"
    )
    if problem.notes:
        print(f"\n{len(problem.notes)} note(s):")
        for note in problem.notes:
            print(f"  - {note}")
    return 0


def cmd_reference(args: argparse.Namespace) -> int:
    problem = _load_problem(args.problem)
    if not problem.reference:
        print("This problem carries no reference allocation.")
        return 1
    _print_report("Department's own allocation", report(problem, problem.reference, SolverSettings()))
    return 0


def cmd_solve(args: argparse.Namespace) -> int:
    problem = _load_problem(args.problem)
    settings = SolverSettings()
    if args.settings:
        settings = SolverSettings.model_validate_json(Path(args.settings).read_text())
    if args.time_limit:
        settings.time_limit_seconds = args.time_limit
    result = solve(problem, settings)
    print(
        f"status {result.status}  objective {result.objective}  bound {result.best_bound}  "
        f"build {result.build_seconds}s  solve {result.solve_seconds}s  {result.model_stats}"
    )
    for w in result.warnings:
        print(f"  ! {w}")
    if result.error:
        print(result.error)
        return 1
    print("objective breakdown:", json.dumps(result.objective_breakdown))
    _print_report("Solver", result.report)
    if result.comparison:
        c = result.comparison
        _print_report("Department's own allocation", c.reference_report)
        print("\n== Agreement with the department")
        print(f"  recall {c.recall}  lecture recall {c.lecture_recall}  "
              f"IC agreement {c.ic_agreement}  identical sections {c.exact_sections}")
    if args.output:
        Path(args.output).write_text(result.model_dump_json(indent=2))
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    """
    Solve the same problem with several seeds and report how stable it is.

    A well-converged model gives nearly the same objective and the same
    headline numbers whatever the seed; a wide spread means the time limit
    ends the search long before it settles.
    """
    problem = _load_problem(args.problem)
    base = SolverSettings()
    if args.settings:
        base = SolverSettings.model_validate_json(Path(args.settings).read_text())
    overrides = json.loads(args.set) if args.set else {}
    print(f"{'seed':>4} {'status':9} {'objective':>10} {'bound':>10} {'gap':>6} "
          f"{'1st-lec':>7} {'f-sd':>5} {'f-out':>5} {'unlisted':>8} {'build':>6} {'solve':>6}")
    for seed in range(args.seeds):
        settings = base.model_copy(update={
            **overrides,
            "random_seed": seed,
            "time_limit_seconds": args.time_limit or base.time_limit_seconds,
        })
        r = solve(problem, settings)
        if r.error:
            print(f"{seed:>4} {r.status:9} {r.error}")
            continue
        gap = (r.best_bound - r.objective) / max(abs(r.best_bound), 1)
        f = r.report.load["faculty"]
        unlisted = sum(m.get("unlisted", 0) for m in r.report.preference_matches.values())
        print(f"{seed:>4} {r.status:9} {r.objective:>10.0f} {r.best_bound:>10.0f} {gap:>6.1%} "
              f"{r.report.first_lecture_choice_rate:>7.0%} {f.stdev:>5.2f} {f.under + f.over:>5} "
              f"{unlisted:>8} {r.build_seconds:>6.2f} {r.solve_seconds:>6.1f}")
    print(f"model: {r.model_stats}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="allocator")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import", help="Build a problem from a department workbook")
    p.add_argument("workbook")
    p.add_argument("--department", default="ME")
    p.add_argument("--name")
    p.add_argument("--semester")
    p.add_argument("--default-staffing", action="store_true",
                   help="Use default section templates instead of the workbook's staffing")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("solve", help="Solve a problem JSON")
    p.add_argument("problem")
    p.add_argument("--settings", help="SolverSettings JSON")
    p.add_argument("--time-limit", type=float)
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_solve)

    p = sub.add_parser("bench", help="Solve with several seeds to measure stability")
    p.add_argument("problem")
    p.add_argument("--seeds", type=int, default=3)
    p.add_argument("--time-limit", type=float)
    p.add_argument("--settings", help="SolverSettings JSON file")
    p.add_argument("--set", help='Inline overrides, e.g. \'{"eligibility": "penalised"}\'')
    p.set_defaults(func=cmd_bench)

    p = sub.add_parser("reference", help="Score the department's own allocation")
    p.add_argument("problem")
    p.set_defaults(func=cmd_reference)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

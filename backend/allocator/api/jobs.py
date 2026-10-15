"""
Background solving.

A solve can take a minute and pins CPU cores, so it runs in a separate
process, as the timetable autoscheduler does. The run's state lives in the
store, not in memory: a finished run is readable after a restart, and a run
that was in flight when the server died is marked failed on the next start
rather than reported as running forever.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import multiprocessing
import os

from allocator.domain import Problem
from allocator.settings import SolverSettings
from allocator.store import RunRecord, Store, now

logger = logging.getLogger("allocator.jobs")

_executor: concurrent.futures.ProcessPoolExecutor | None = None


def executor() -> concurrent.futures.ProcessPoolExecutor:
    global _executor
    if _executor is None:
        # Spawn, not fork: the server is multi-threaded and forking it can
        # deadlock the child. Spawned workers import `allocator` afresh, so
        # it must be importable from PYTHONPATH (scripts/dev.sh sets it).
        # One run at a time by default: each already uses `num_workers`
        # cores (8), and two at once would fight over a 12-core machine.
        # Further runs queue.
        _executor = concurrent.futures.ProcessPoolExecutor(
            max_workers=int(os.environ.get("ALLOCATOR_PARALLEL_RUNS", "1")),
            mp_context=multiprocessing.get_context("spawn"),
        )
    return _executor


def shutdown() -> None:
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)


def _solve_in_process(problem_json: str, settings_json: str) -> str:
    # Imported here so the worker process pays the import, not the server.
    from allocator.solver import solve

    problem = Problem.model_validate_json(problem_json)
    settings = SolverSettings.model_validate_json(settings_json)
    return solve(problem, settings).model_dump_json()


async def run_job(store: Store, run: RunRecord, problem: Problem) -> None:
    from allocator.solver.allocator import SolveResult

    run.status = "running"
    store.save_run(run)
    loop = asyncio.get_running_loop()
    try:
        raw = await loop.run_in_executor(
            executor(), _solve_in_process, problem.model_dump_json(), run.settings.model_dump_json()
        )
        result = SolveResult.model_validate_json(raw)
        run.result = result
        run.status = "completed" if result.allocation else "failed"
        run.error = result.error
    except Exception as exc:  # the run must end in a recorded state
        logger.exception("Run %s failed", run.id)
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"
    run.finished_at = now()
    store.save_run(run)


def mark_interrupted(store: Store) -> int:
    count = 0
    for summary in store.list_runs():
        if summary.status in ("pending", "running"):
            run = store.get_run(summary.id)
            if run is None:
                continue
            run.status = "failed"
            run.error = "Interrupted: the server stopped while this run was in progress."
            run.finished_at = now()
            store.save_run(run)
            count += 1
    return count

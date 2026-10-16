"""
File-backed storage for problems and runs.

One JSON file per problem and per run under data/store/. Plain files keep a
run inspectable and diffable without a database, survive restarts, and are
easy to hand to someone. Swap for Mongo when this joins the timetable app.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from allocator.domain import Problem
from allocator.settings import SolverSettings
from allocator.solver.allocator import SolveResult

DEFAULT_ROOT = Path(
    os.environ.get(
        "ALLOCATOR_STORE", Path(__file__).resolve().parents[2] / "data" / "store"
    )
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RunRecord(BaseModel):
    id: str
    problem_id: str
    created_at: str
    finished_at: str | None = None
    #: pending / running / completed / failed
    status: str = "pending"
    label: str = ""
    settings: SolverSettings = Field(default_factory=SolverSettings)
    result: SolveResult | None = None
    error: str | None = None


class ProblemSummary(BaseModel):
    id: str
    name: str
    department: str
    semester: str
    faculty: int
    courses: int
    sections: int
    preferences: int
    locks: int
    has_reference: bool
    notes: int
    updated_at: str


class Store:
    def __init__(self, root: Path = DEFAULT_ROOT):
        self.root = Path(root)
        self.problems_dir = self.root / "problems"
        self.runs_dir = self.root / "runs"
        self.problems_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ---- problems --------------------------------------------------------------

    def save_problem(self, problem: Problem) -> Problem:
        self._write(self.problems_dir / f"{problem.id}.json", problem.model_dump(mode="json"))
        return problem

    def get_problem(self, problem_id: str) -> Problem | None:
        path = self._safe(self.problems_dir, problem_id)
        if not path.exists():
            return None
        return Problem.model_validate_json(path.read_text())

    def delete_problem(self, problem_id: str) -> bool:
        path = self._safe(self.problems_dir, problem_id)
        if not path.exists():
            return False
        path.unlink()
        for run in self.list_runs(problem_id):
            self._safe(self.runs_dir, run.id).unlink(missing_ok=True)
        return True

    def list_problems(self) -> list[ProblemSummary]:
        out = []
        for path in sorted(self.problems_dir.glob("*.json")):
            try:
                p = Problem.model_validate_json(path.read_text())
            except ValueError:
                continue
            out.append(
                ProblemSummary(
                    id=p.id,
                    name=p.name,
                    department=p.department,
                    semester=p.semester,
                    faculty=len(p.people),
                    courses=len(p.courses),
                    sections=sum(len(c.sections) for c in p.courses),
                    preferences=len(p.preferences),
                    locks=len(p.locks),
                    has_reference=bool(p.reference and p.reference.assignments),
                    notes=len(p.notes),
                    updated_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
                )
            )
        return sorted(out, key=lambda s: s.updated_at, reverse=True)

    # ---- runs ------------------------------------------------------------------

    def new_run(self, problem_id: str, settings: SolverSettings, label: str = "") -> RunRecord:
        run = RunRecord(
            id=uuid.uuid4().hex[:12],
            problem_id=problem_id,
            created_at=now(),
            settings=settings,
            label=label,
        )
        self.save_run(run)
        return run

    def save_run(self, run: RunRecord) -> RunRecord:
        self._write(self.runs_dir / f"{run.id}.json", run.model_dump(mode="json"))
        return run

    def get_run(self, run_id: str) -> RunRecord | None:
        path = self._safe(self.runs_dir, run_id)
        if not path.exists():
            return None
        return RunRecord.model_validate_json(path.read_text())

    def delete_run(self, run_id: str) -> bool:
        path = self._safe(self.runs_dir, run_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def list_runs(self, problem_id: str | None = None) -> list[RunRecord]:
        runs = []
        for path in self.runs_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text())
            except ValueError:
                continue
            if problem_id and data.get("problem_id") != problem_id:
                continue
            # Listing does not need the (large) result body.
            data["result"] = None
            runs.append(RunRecord.model_validate(data))
        return sorted(runs, key=lambda r: r.created_at, reverse=True)

    # ---- helpers ----------------------------------------------------------------

    def _safe(self, folder: Path, ident: str) -> Path:
        if not ident or "/" in ident or "\\" in ident or ident.startswith("."):
            raise ValueError(f"Bad id: {ident!r}")
        return folder / f"{ident}.json"

    def _write(self, path: Path, data: dict) -> None:
        tmp = path.with_suffix(".json.tmp")
        with self._lock:
            tmp.write_text(json.dumps(data, indent=2))
            tmp.replace(path)

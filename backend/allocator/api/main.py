"""
HTTP API and static frontend.

    uvicorn allocator.api.main:app --port 8100

Workbooks are uploaded as multipart form data (field `file`) or as the raw
request body (Content-Type application/octet-stream).
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from allocator import __version__
from allocator.api import jobs
from allocator.domain import Lock, Problem
from allocator.evaluation import Report, report
from allocator.export import allocation_xlsx, people_csv, sections_csv, to_csv, to_timetable_json
from allocator.ingest import TemplateError, UploadFormat, import_upload
from allocator.ingest.template import write_template
from allocator.ingest.template_spec import spec_as_dict
from allocator.ingest.workbook import DEPARTMENTS_DIR
from allocator.load import LoadTargets, load_targets
from allocator.settings import SolverSettings
from allocator.shortfall import Shortfall, find_shortfalls
from allocator.store import ProblemSummary, RunRecord, Store
from allocator.validation import validate_problem

logger = logging.getLogger("allocator.api")
FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

store = Store()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO)
    interrupted = jobs.mark_interrupted(store)
    if interrupted:
        logger.warning("Marked %d interrupted run(s) as failed", interrupted)
    yield
    jobs.shutdown()


app = FastAPI(title="Faculty Allocator", version=__version__, lifespan=lifespan)


def _problem_or_404(problem_id: str) -> Problem:
    try:
        problem = store.get_problem(problem_id)
    except ValueError:
        problem = None
    if problem is None:
        raise HTTPException(404, f"Problem not found: {problem_id}")
    return problem


def _run_or_404(run_id: str) -> RunRecord:
    try:
        run = store.get_run(run_id)
    except ValueError:
        run = None
    if run is None:
        raise HTTPException(404, f"Run not found: {run_id}")
    return run


# ---- meta -----------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/api/departments")
def departments() -> list[dict]:
    out = []
    for path in sorted(DEPARTMENTS_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        out.append({"code": data.get("department", path.stem), "name": data.get("name", path.stem)})
    return out


@app.get("/api/settings/defaults")
def default_settings() -> SolverSettings:
    return SolverSettings()


# ---- problems -------------------------------------------------------------------


@app.get("/api/problems")
def list_problems() -> list[ProblemSummary]:
    return store.list_problems()


@app.post("/api/problems", status_code=201)
def create_problem(problem: Problem) -> ProblemSummary:
    store.save_problem(problem)
    return next(s for s in store.list_problems() if s.id == problem.id)


@app.post("/api/problems/import", status_code=201)
async def import_problem(
    request: Request,
    department: str = Query("ME"),
    name: str = Query(""),
    semester: str = Query(""),
    format: UploadFormat = Query(UploadFormat.AUTO),
    default_staffing: bool = Query(False),
) -> dict:
    """
    Import an .xlsx, sent either as a multipart form field named `file` (what
    a browser form or `curl -F file=@...` sends) or as the raw request body.

    `format=auto` reads the upload template strictly when the file is one,
    and otherwise falls back to the legacy department-workbook reader. A
    template with errors is rejected whole, with every issue listed.
    """
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None or isinstance(upload, str):
            await form.close()
            raise HTTPException(400, "Multipart upload needs a file field named 'file'.")
        body = await upload.read()
        await upload.close()
    else:
        body = await request.body()
    if not body:
        raise HTTPException(400, "Send the .xlsx file as a 'file' form field or as the request body.")
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Workbook is larger than 20 MB.")
    try:
        problem, used = import_upload(
            body, format, department=department, name=name,
            semester=semester, default_staffing=default_staffing,
        )
    except TemplateError as exc:
        return JSONResponse(
            status_code=422,
            content={"detail": {"message": str(exc), "issues": [i.model_dump() for i in exc.issues]}},
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    store.save_problem(problem)
    return {
        "id": problem.id,
        "format": used.value,
        "notes": problem.notes,
        "warnings": validate_problem(problem),
        "shortfalls": [s.model_dump(mode="json") for s in find_shortfalls(problem, SolverSettings())],
    }


@app.get("/api/template.xlsx")
def download_template(department: str = Query(""), problem_id: str | None = Query(None)) -> Response:
    """
    The upload template. With `problem_id`, pre-filled from that problem
    (courses, sections, roster and preferences) as a starting point for the
    next semester.
    """
    problem = _problem_or_404(problem_id) if problem_id else None
    data = write_template(problem, department=department.upper())
    stem = f"allocation-template-{(department or (problem.department if problem else '')).lower() or 'blank'}"
    if problem:
        stem += f"-{problem.id}"
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{stem}.xlsx"'},
    )


@app.get("/api/schema")
def schema() -> dict:
    """The upload template's sheets and columns, and the JSON Problem schema."""
    return {"template": spec_as_dict(), "problem": Problem.model_json_schema()}


@app.get("/api/problems/{problem_id}")
def get_problem(problem_id: str) -> Problem:
    return _problem_or_404(problem_id)


@app.put("/api/problems/{problem_id}")
def replace_problem(problem_id: str, problem: Problem) -> Problem:
    _problem_or_404(problem_id)
    if problem.id != problem_id:
        raise HTTPException(400, "Problem id in the body does not match the URL.")
    return store.save_problem(problem)


@app.delete("/api/problems/{problem_id}")
def delete_problem(problem_id: str) -> dict:
    _problem_or_404(problem_id)
    store.delete_problem(problem_id)
    return {"deleted": problem_id}


@app.put("/api/problems/{problem_id}/locks")
def set_locks(problem_id: str, locks: list[Lock]) -> list[Lock]:
    problem = _problem_or_404(problem_id)
    problem.locks = locks
    store.save_problem(problem)
    return problem.locks


class PersonPatch(BaseModel):
    load_min: float | None = None
    load_max: float | None = None
    external_load: float | None = None
    teaching_percent: float | None = None
    psrn: str | None = None
    #: Fields to reset to "not set" (only meaningful for the optional ones).
    clear: list[str] = []


@app.patch("/api/problems/{problem_id}/people/{person_id}")
def patch_person(problem_id: str, person_id: str, patch: PersonPatch) -> dict:
    problem = _problem_or_404(problem_id)
    person = problem.person(person_id)
    if person is None:
        raise HTTPException(404, f"Person not found: {person_id}")
    for field, value in patch.model_dump(exclude={"clear"}, exclude_none=True).items():
        setattr(person, field, value)
    for field in patch.clear:
        if field in ("load_min", "load_max", "teaching_percent", "psrn"):
            setattr(person, field, None)
    store.save_problem(problem)
    return person.model_dump(mode="json")


@app.get("/api/problems/{problem_id}/validation")
def problem_validation(problem_id: str) -> dict:
    problem = _problem_or_404(problem_id)
    settings = SolverSettings()
    return {
        "warnings": validate_problem(problem, settings),
        "notes": problem.notes,
        "shortfalls": [s.model_dump(mode="json") for s in find_shortfalls(problem, settings)],
    }


@app.post("/api/problems/{problem_id}/shortfalls")
def problem_shortfalls(problem_id: str, settings: SolverSettings | None = None) -> list[Shortfall]:
    """Courses with fewer volunteers than places, under the given settings."""
    return find_shortfalls(_problem_or_404(problem_id), settings or SolverSettings())


@app.post("/api/problems/{problem_id}/targets")
def problem_targets(problem_id: str, settings: SolverSettings | None = None) -> LoadTargets:
    """Each person's Teaching Allocation and load target, and the baseline."""
    return load_targets(_problem_or_404(problem_id), settings or SolverSettings())


@app.post("/api/problems/{problem_id}/reference-report")
def reference_report(problem_id: str, settings: SolverSettings | None = None) -> Report:
    problem = _problem_or_404(problem_id)
    if not problem.reference:
        raise HTTPException(404, "This problem has no reference allocation.")
    return report(problem, problem.reference, settings or SolverSettings())


# ---- runs ------------------------------------------------------------------------


class RunRequest(BaseModel):
    settings: SolverSettings = SolverSettings()
    label: str = ""


@app.post("/api/problems/{problem_id}/runs", status_code=202)
def start_run(problem_id: str, body: RunRequest, background: BackgroundTasks) -> RunRecord:
    problem = _problem_or_404(problem_id)
    run = store.new_run(problem_id, body.settings, body.label)
    background.add_task(jobs.run_job, store, run, problem)
    return run


@app.get("/api/problems/{problem_id}/runs")
def list_runs(problem_id: str) -> list[RunRecord]:
    _problem_or_404(problem_id)
    return store.list_runs(problem_id)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> RunRecord:
    return _run_or_404(run_id)


@app.get("/api/runs/{run_id}/status")
def run_status(run_id: str) -> dict:
    run = _run_or_404(run_id)
    return {"id": run.id, "status": run.status, "error": run.error}


@app.delete("/api/runs/{run_id}")
def delete_run(run_id: str) -> dict:
    _run_or_404(run_id)
    store.delete_run(run_id)
    return {"deleted": run_id}


def _completed(run_id: str) -> tuple[Problem, RunRecord]:
    run = _run_or_404(run_id)
    if not run.result or not run.result.allocation:
        raise HTTPException(409, "Run has no allocation to export.")
    return _problem_or_404(run.problem_id), run


@app.get("/api/runs/{run_id}/export/assignments.csv")
def export_assignments(run_id: str) -> PlainTextResponse:
    problem, run = _completed(run_id)
    return PlainTextResponse(
        to_csv(problem, run.result.allocation),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="allocation-{run_id}.csv"'},
    )


@app.get("/api/runs/{run_id}/export/sections.csv")
def export_sections(run_id: str) -> PlainTextResponse:
    """One row per defined section: who takes L1, L2, T1, P1... of every course."""
    problem, run = _completed(run_id)
    return PlainTextResponse(
        sections_csv(problem, run.result.allocation),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="sections-{run_id}.csv"'},
    )


@app.get("/api/runs/{run_id}/export/allocation.xlsx")
def export_xlsx(run_id: str) -> Response:
    """Sections sheet (one row per section) and Faculty sheet (one row per person)."""
    problem, run = _completed(run_id)
    return Response(
        allocation_xlsx(problem, run.result.allocation, run.result.report),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="allocation-{run_id}.xlsx"'},
    )


@app.get("/api/runs/{run_id}/export/people.csv")
def export_people(run_id: str) -> PlainTextResponse:
    problem, run = _completed(run_id)
    return PlainTextResponse(
        people_csv(problem, run.result.allocation, run.result.report),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="loads-{run_id}.csv"'},
    )


@app.get("/api/runs/{run_id}/export/timetable.json")
def export_timetable(run_id: str) -> Response:
    problem, run = _completed(run_id)
    return JSONResponse(
        to_timetable_json(problem, run.result.allocation),
        headers={"Content-Disposition": f'attachment; filename="timetable-{run_id}.json"'},
    )


# ---- frontend -------------------------------------------------------------------

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

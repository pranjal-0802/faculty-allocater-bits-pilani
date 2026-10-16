# Faculty Allocator

Each semester, every department at BITS Pilani Hyderabad sends out a form. Faculty rank the lectures, tutorials and labs they want to teach, and research scholars do the same for tutorials and labs. The department then decides by hand who takes which section and who is IC of each course. This project automates that decision with Google OR-Tools' CP-SAT solver, the same engine the timetable autoscheduler uses.

Input: a department's allocation workbook. Output: who staffs every section, who is IC, each person's load, and how that compares with what the department did by hand.

```
data/raw/*.xlsx ──import──▶ Problem (people, courses, sections, preferences)
                                 │
                       SolverSettings (weights, rules)
                                 ▼
                         CP-SAT model ──▶ Allocation ──▶ report, comparison, CSV / JSON
```

## Quick start

```bash
scripts/dev.sh import      # read data/raw/<workbook> into data/examples/me-2025-26-sem2.json
scripts/dev.sh solve       # solve it and print the report next to the department's own allocation
scripts/dev.sh serve       # API + web app on http://localhost:8100
scripts/dev.sh test        # test suite
```

The virtualenv is `.venv/` (Python 3.12). Dependencies are declared in `pyproject.toml` and pinned in `requirements.lock`. Recreate the environment with:

```bash
uv venv --python 3.12 .venv && uv pip sync --python .venv/bin/python requirements.lock
# after changing pyproject.toml:
uv pip compile pyproject.toml --extra dev --python-version 3.12 -o requirements.lock
```

**Spreadsheets:** `.xlsx` files are read and written with the standard library (`ingest/xlsx.py`, `ingest/xlsx_writer.py`), so there's no spreadsheet dependency at run time. openpyxl is a test dependency only. `tests/test_xlsx_crosscheck.py` checks that openpyxl reads every generated template identically, and agrees with the stdlib reader on every cell of the ME workbook. The one exception is date cells, which the stdlib reader returns as Excel serial numbers; no template or importer column uses dates.

**Web app:** plain HTML/JS served by the API, with no build step.

## Layout

```
backend/allocator/
  domain.py          Problem, Person, Course, Section, Preference, Lock, Allocation (also the API schema)
  settings.py        every weight and policy switch, with defaults
  load.py            the load rule (units for faculty, contact hours for scholars)
  solver/
    context.py       decision variables and derived variables
    constraints.py   hard rules
    objectives.py    weighted objective
    hints.py         greedy starting allocation handed to CP-SAT
    allocator.py     build → solve → extract; SolveResult
  evaluation.py      report on any allocation; comparison of two
  ingest/
    template_spec.py the upload template: sheets, columns, help (single source of truth)
    template.py      write the template (blank or pre-filled); read uploads strictly
    workbook.py      legacy department workbook → Problem (+ the hand allocation as `reference`)
    xlsx.py          stdlib .xlsx reader
    xlsx_writer.py   stdlib .xlsx writer (styles, frozen panes, drop-downs)
    matching.py      name and course-code resolution
  export.py          CSV, and timetable-app-shaped JSON
  store.py           JSON files under data/store/
  api/main.py        FastAPI; api/jobs.py runs solves in a spawned process pool
  cli.py             import / solve / reference
backend/tests/       solver, matching, workbook import, API
frontend/            static single-page app
data/departments/    per-department knowledge: aliases, service courses, unit fixes
data/raw/            department workbooks (git-ignored)
```

## Upload format

Departments upload a fixed **allocation template**, not their own workbooks. Free-form documents can't be parsed reliably: every department lays its form out differently, and a parser that guesses assigns people to the wrong courses without saying so.

The template keeps the original form's sheets and headers ("LECTURE Preference 1", "LECTURE Taken Consecutively 1", "Tute-1", "Section Number"…), so it reads as the same form, made strict. It has four sheets: Courses, Sections, Faculty and Research Scholars.

- **Drop-downs:** course choices come from the Courses sheet; Yes/No, category, supervisor and pre-assigned names also have lists.
- **Strict checking:** an upload with any error is rejected whole, with every issue listed by sheet, row and column. Warnings, such as a preference for a course not offered, become notes on the problem.
- **Pre-filled copies:** `GET /api/template.xlsx?problem_id=…` writes an existing problem back out as a filled template, ready to adjust for the next semester.
- **One definition:** `ingest/template_spec.py` defines the template once. The writer, the reader, `/api/schema` and [docs/upload-format.md](docs/upload-format.md) (the guide for departments) all follow from it.

The legacy reader for the Mechanical Sem II 2025-26 workbook is kept for replaying past semesters. `format=auto` detects which kind an upload is.

Splitting a section half-and-half between two faculty is deliberately left to people: the solver never splits. A department can pre-assign both people, or arrange the split after the run.

## The model

**Decision variables**
- `x[person, course, section]`: the person staffs that section.
- `ic[person, course]`: the person is the course's IC.
- `unfilled[course, section, kind]`: how many staff the section still lacks.

**Hard rules**
- Each section has no more faculty or scholars than it allows.
- Each course that needs an IC gets exactly one.
- The IC lectures the course, when the course has lectures.
- Locks are honoured.
- Nobody goes over the absolute load cap.

**Who is considered for a section** (`eligibility`):
- **shortlist** (default): people who listed the course, plus service courses and locks. A section with fewer than (slots + 2) volunteers is opened to everyone of the right kind, at the unlisted penalty.
- **penalised**: everyone is considered for everything.
- **strict**: volunteers only.

The IC is chosen among the people who may lecture the course.

Staffing minimums are deliberately soft. A slot nobody can fill costs 100,000 points and is reported as unfilled. A department would rather read "ME F220 P3 needs one more scholar" than "infeasible". This follows the timetable solver's habit of pricing a rule instead of forbidding it whenever a hard rule could make the whole run infeasible.

**Objective (maximised)**

| Term | What it does |
|---|---|
| preference | 100 / 70 / 45 points (faculty) or 60 / 45 (scholars) for a ranked course and kind. Paid once per course and kind, not per section, so pleasing someone never means giving them every section of one course. |
| cross-kind | +25 when the person listed the course, but for a different part of it. |
| unlisted | −80 for a course the person never asked for. Service courses (BITS F103 labs, BITS F111 tutorials) are exempt, since they're shared out as a department duty. |
| load band | −60 per unit under the band, −120 per unit over it, plus −80 per unit of the single worst overload. |
| tutorial follows lecture | −150 when a tutorial goes to a faculty member who doesn't lecture that course. |
| lecturer takes tutorial | −40 for a lecturer with no tutorial in a course that has them. |
| distinct courses | −40 per course beyond 3 for one faculty member. |
| consecutive | Signed weight on "taken consecutively". Defaults to 0 until the admin rules on it. |
| supervisor | Bonus when a scholar shares a section with their supervisor. Defaults to 0. |

**Load** follows the timetable app's course-load report (`courseLoadController.js`):
- A lecture counts its lecture units, split between co-teachers.
- A lab counts its lab units.
- A tutorial counts 1.
- A project IC counts 1.
- WILP is fixed external load of 3.
- Scholar load is in contact hours: a tutorial is 1 hour, and each lab unit is 2 hours.

## Pending the BITS admin

Each of these is a setting, so confirming a rule means changing a setting, not code:

| Question | Where it goes |
|---|---|
| What "Teaching Allocation" % (20/30/40/60/70) means for load | `teaching_percent_bands`, `load_min`/`load_max` per person |
| Whether "taken consecutively" favours continuity or rotation | `consecutive_weight` (positive or negative) |
| Whether scholar allocation is in scope, and with what hour targets | `scholar_load`; or drop scholars from the problem |
| Target load band per designation | `faculty_load`, per-person overrides |
| How strict tutorial-follows-lecture and IC-must-lecture are | `tutorial_follows_lecture`, `ic_must_lecture` |

## The Mechanical Sem II 2025-26 replay

The workbook in `data/raw` includes the allocation the department actually made, which gives a ground truth to measure against. The solver never sees it: it's kept as `Problem.reference` and used only for comparison.

Default settings, 36 faculty, 69 scholars, 100 sections. The solver proves optimality in 10–30 seconds and gives the same answer for every seed:

| | Department (by hand) | Solver (proven optimal) |
|---|---|---|
| Faculty load, mean ± sd | 5.47 ± 2.00 | 5.53 ± 0.73 |
| Faculty outside the 5–7 band (under / over) | 7 / 5 | 2 / 0 |
| Faculty first-lecture-choice rate | 66% | 75% |
| Faculty holdings that were a 1st choice | 57 | 73 |
| Faculty holdings nobody asked for | 6 | 2 |
| Scholar holdings that were a 1st choice | 52 | 63 |
| Scholar holdings nobody asked for | 21 | 8 |
| Unfilled slots / courses without IC | 0 / 1 | 0 / 0 |

Agreement with the department: 42% of faculty assignments reproduced, 62% of lecturers, 42% of ICs. That's expected for now. The department weighed things the forms don't record, and the weights haven't been tuned to the admin's answers yet.

## Convergence

`python -m allocator.cli bench PROBLEM --seeds N --time-limit S [--set '{...}']` solves the same problem with several seeds and reports objective, bound, gap and headline numbers per seed.

On the ME replay, with a 120-second limit:

| Configuration | Result |
|---|---|
| 4 workers, everyone eligible (first version) | 45–48% gap at 30 s; first-lecture-choice rate ranged 59–72% across seeds |
| 8 workers, everyone eligible | optimal in 55 s |
| 8 workers, shortlist + greedy hint (**default**) | optimal in 10–30 s, every seed |
| 8 workers, shortlist + hint + explicit symmetry breaking | optimal in 51–73 s |
| 8 workers, shortlist + explicit symmetry breaking, no hint | optimal in 92 s |
| 8 workers, everyone eligible + hint + symmetry breaking | 0.8% gap at 120 s |

What this shows:
- **Worker count matters most.** CP-SAT runs one strategy per worker, and below 8 it drops the strategies that tighten the bound.
- **Shortlisting costs nothing here.** Considering only volunteers, and opening a section to everyone only when it's short, cut the model from 8,538 to 4,888 decision variables and reached the same optimum.
- **Explicit symmetry breaking is off by default.** CP-SAT already detects that the ten BITS F103 labs are interchangeable, and the extra ordering constraints fought the greedy hint.
- **Runs are queued, one at a time.** Each run uses 8 cores, so the API runs one solve at a time (`ALLOCATOR_PARALLEL_RUNS=1`).

## API

| Method and path | Purpose |
|---|---|
| `POST /api/problems/import?department=ME&name=&semester=&format=auto` | Multipart field `file` (`curl -F file=@x.xlsx`) or the raw `.xlsx` as the body. 422 with `issues` if a template has errors. |
| `GET /api/template.xlsx?department=ME[&problem_id=]` | Blank or pre-filled upload template |
| `GET /api/schema` | Template sheets and columns, plus the JSON Problem schema |
| `GET /api/problems`, `GET/PUT/DELETE /api/problems/{id}` | List, read, replace, delete |
| `PUT /api/problems/{id}/locks` | Replace locks |
| `PATCH /api/problems/{id}/people/{person}` | Load band overrides, external load, PSRN |
| `POST /api/problems/{id}/reference-report` | Score the hand allocation |
| `POST /api/problems/{id}/runs` | `{settings, label}`; returns 202 and a run record |
| `GET /api/runs/{id}`, `GET /api/runs/{id}/status` | Result or progress |
| `GET /api/runs/{id}/export/{assignments.csv, people.csv, timetable.json}` | Exports |
| `GET /api/settings/defaults` | Default settings |

## Not done yet

- Reading courses, sections and faculty PSRNs from the timetable app's Mongo database (`course1/2/3`, `faculty`) instead of from the workbook, and writing allocations back through its IMS route.
- Half-semester splits are done by hand by design (see Upload format); the solver only assigns whole sections.
- More than two co-teachers on one lecture. Load is only split two ways.

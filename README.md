# Faculty Allocator

Each semester, every department at BITS Pilani Hyderabad sends out a form. Faculty rank the lectures, tutorials and labs they want to teach, and say how much of their time they give to teaching ("Teaching Allocation"). The department then decides by hand who takes which section. This project automates that decision with Google OR-Tools' CP-SAT solver, the same engine the timetable autoscheduler uses.

**Input:** one upload per department: its courses (CDC or elective), the sections each course runs, and the faculty with their preferences. The app needs nothing else; it doesn't read the timetable database.

**Output:** for every section the department lists (L1, L2…, T1…, P1…), which professor takes it. Also each person's load against their target, courses short of volunteers, and how all of that compares with what the department did by hand.

Only faculty are allocated. Research scholars (the RS sheet) are assigned separately and are out of scope, and so are instructors-in-charge: the department picks each course's IC from its instructors once the allocation is made.

```
upload (template, or legacy workbook) ──▶ Problem ──▶ CP-SAT ──▶ Allocation
                                                              │
                    sections.csv / allocation.xlsx / timetable.json ◀──┘
```

## Quick start

```bash
scripts/dev.sh import      # ME Sem II workbook → data/examples/me-2025-26-sem2.json
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

**Spreadsheets:** `.xlsx` files are read and written with the standard library (`ingest/xlsx.py`, `ingest/xlsx_writer.py`), so there's no spreadsheet dependency at run time. openpyxl is a test dependency only. `tests/test_xlsx_crosscheck.py` checks that openpyxl reads every generated template identically, and agrees with the stdlib reader on every cell of the ME workbook.

**Web app:** plain HTML/JS served by the API, with no build step.

## Layout

```
backend/allocator/
  domain.py          Problem, Person, Course, Section, Preference, Lock, Allocation (also the API schema)
  settings.py        every weight and policy switch, with defaults
  load.py            unit scoring (as the timetable app does it), standard load, targets
  rules.py           the admin's hard rules: one CDC each, rotation after 3 semesters
  shortfall.py       courses with fewer volunteers than places
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
  export.py          section-by-section CSV and xlsx, faculty loads, timetable-app JSON
  store.py           JSON files under data/store/
  api/main.py        FastAPI; api/jobs.py runs solves in a spawned process pool
  cli.py             import / solve / reference / bench
backend/tests/       solver and rules, matching, workbook import, template, API
frontend/            static single-page app
data/departments/    per-department knowledge: aliases, service courses, unit fixes
data/raw/            department workbooks (git-ignored)
```

## Where each fact comes from

Everything comes from the department's upload:

| Fact | Where it is stated |
|---|---|
| Which sections a course has (L1, L2, T1, P1…) and how many faculty each needs | Sections sheet. `0` for a section run by scholars: it stays in the result as "no faculty needed". |
| CDC or elective | Courses sheet, **Category** (required): `CDC`, `EL` or `PROJECT`. A course compulsory for any programme is a CDC. FD/HD is a separate **Level** column. |
| Units, double tutorials | Courses sheet: lecture and lab units, tutorial units |
| PSRN, designation | Faculty sheet |
| WILP and other outside teaching | Faculty sheet: External Load, with details |
| Preferences, Teaching Allocation, Taken Consecutively | Faculty sheet |

## The rules

| Rule | How it is enforced | Setting |
|---|---|---|
| **Load follows Teaching Allocation.** The standard load is the department's total teaching units divided by its number of faculty. Someone at 50% gets it, 70% gets 1.4 times it, 40% gets 0.8 times. A blank Teaching Allocation counts as 50%. | Soft, with the target as a ceiling: 300 points per unit over (more than any preference is worth), another 300 per unit past the first, and 60 per unit more than a unit under. | `load_baseline`, `baseline_mode`, `default_teaching_percent`, `overload_*` |
| **At most one CDC each.** Two courses can be CDC + elective or elective + elective, never CDC + CDC. | Hard. Any section of a CDC counts. Only first-degree CDCs count: service courses (BITS F103, BITS F111) and higher-degree courses don't. | `one_cdc_per_faculty`, `hd_cdcs_count` |
| **Rotation.** A course taken 3 semesters running may not be taken again. | Hard: the person gets no variable for any section of that course. | `rotation_limit`, `rotation_scope`, `rotation_yes_without_count` |
| **Underflow is flagged.** A course with fewer volunteers than places (one person asked, two lecture sections) is reported separately. | Before the run (problem overview, `/validation`) and in every result, with who ended up taking the places. | — |

**Unit scoring** follows the timetable app's course-load report (`courseLoadController.js`):
- A lecture counts its lecture units, divided among the faculty on it.
- A practical counts its lab units, undivided.
- A tutorial counts 1, or 2 for a "double tutorial" course.
- A WILP course counts 3 units, divided among its faculty.

**When targets can't all be met.** Most ME faculty opted for 40%, so their targets add up to less than the work. For Sem II, 195 units of work against 168 units of targets means 27 units must go over someone's target whatever the allocation. The solver reports that figure and spreads the excess as evenly as it can.

**Pre-assignments win.** A pre-assignment is the department's decision, so it overrides the CDC and rotation rules. Validation says when it does, and the report marks such a breach as fixed by the department.

**Instructors-in-charge are out of scope.** The department picks a course's IC from among its instructors after the allocation. The solver never sees ICs, and IC entries in older files are dropped on load.

**Old-form "Yes".** The ME form records only Yes/No for "taken consecutively". A Yes doesn't say whether it's the 2nd or the 3rd semester, so by default it doesn't trigger rotation, and the run lists every such preference (40 in ME). The template asks for the number of semesters instead.

## Upload format

Departments upload a fixed **allocation template**, not their own workbooks. Free-form documents can't be parsed reliably: every department lays its form out differently, and a parser that guesses assigns people to the wrong courses without saying so.

The template keeps the original form's headers ("LECTURE Preference 1", "LECTURE Taken Consecutively 1", "Teaching Allocation", "Section Number"…), so it reads as the same form, made strict. It has three sheets: Courses, Sections and Faculty.

- **Pre-filled copies:** `GET /api/template.xlsx?problem_id=…` writes an existing allocation back out as a filled template (courses, sections, faculty, preferences), ready to adjust for the next semester.
- **Categories:** CDC, EL or PROJECT, with FD/HD as a separate Level column. "HD" in the Category column is an error.
- **Taken Consecutively is a count:** the number of semesters running (0, 1, 2, 3…), which the rotation rule needs. Yes/No is still accepted; a bare Yes is flagged.
- **Strict checking:** an upload with any error is rejected whole, with every issue listed by sheet, row and column. Warnings, such as a preference for a course not offered, become notes on the problem.
- **One definition:** `ingest/template_spec.py` defines the template once. The writer, the reader, `/api/schema` and [docs/upload-format.md](docs/upload-format.md) (the guide for departments) all follow from it.

The legacy reader for the Mechanical Sem II 2025-26 workbook is kept for replaying past semesters, and `format=auto` detects which kind an upload is:
- It reads CDC/EL and FD/HD from the course summary and the programme sheet (the summary's IC column is ignored). The department's programme sheet is what makes ME G515 a CDC (compulsory for M.E. Thermal) and ME G538 an elective.
- It doesn't read the RS sheet. Scholars named on the section sheet are left out, and sections run only by scholars are kept with no faculty needed.
- Lecture and lab units that the workbook omits come from `data/departments/ME.json`.
- The hand allocation in the workbook is kept as `Problem.reference`, for comparison only.

Splitting a section half-and-half between two faculty is deliberately left to people: the solver never splits.

## Output

Every export walks the defined section sets in order, so each section appears exactly once:

| Export | What it is |
|---|---|
| `sections.csv`, `allocation.xlsx` › Sections | One row per section: course, title, CDC/EL, FD/HD, section, kind, faculty needed, faculty 1–3 with PSRNs, status (staffed / *n* faculty missing / no faculty needed) |
| `allocation.xlsx` › Faculty, `people.csv` | One row per faculty member: Teaching Allocation, target, load, status, WILP, lectures, tutorials and practicals held |
| `timetable.json` | Per course: `sections[].instructors` (name, PSRN), the shape of the timetable app's course documents |
| `assignments.csv` | One row per (person, section), with load and how it matched their preferences |

## The model

**Decision variables**
- `x[person, course, section]`: the person staffs that section.
- `unfilled[course, section]`: how many faculty the section still lacks.

**Hard rules**
- Each section has no more faculty than it allows.
- Locks are honoured.
- Nobody holds two CDCs.
- Nobody takes a course they're rotated out of.
- Nobody goes over the absolute load cap: 10 units, or their target if higher.

**Who is considered for a section** (`eligibility`):
- **shortlist** (default): people who listed the course, plus service courses and pre-assignments. A section with fewer than (slots + 2) volunteers is opened to everyone the hard rules allow, at the unlisted penalty.
- **penalised**: everyone is considered for everything.
- **strict**: volunteers only.

Staffing minimums are deliberately soft. A slot nobody can fill costs 100,000 points and is reported as unfilled, because a department would rather read "ME F220 P3 needs one more faculty member" than "infeasible".

**Objective (maximised)**

| Term | What it does |
|---|---|
| preference | 100 / 70 / 45 points for a ranked course and kind, paid once per course and kind, not per section |
| cross-kind | +25 when the person listed the course, but for a different part of it |
| unlisted | −80 for a course the person never asked for (service courses exempt) |
| load target | −300 per unit over target, −300 more per unit past the first, −60 per unit more than a unit under, −100 per unit of the single worst overload |
| tutorial follows lecture | −150 when a tutorial goes to a faculty member who doesn't lecture that course |
| lecturer takes tutorial | −40 for a lecturer with no tutorial in a course that has them |
| distinct courses | −40 per course beyond 3 for one faculty member |

## The Mechanical Sem II 2025-26 replay

The workbook in `data/raw` has the department's preference form and the allocation it actually made. The solver never sees the latter; it's kept as `Problem.reference` and used only for comparison. Both are scored with the same targets and rules.

The problem has 36 faculty, 37 courses and 100 sections. The standard load is 5.42 units: 195 units over 36 faculty. The solver proves optimality (about 12 minutes with exact targets) and gives the same answer for every seed:

| | Department (by hand) | Solver (proven optimal) |
|---|---|---|
| Two-CDC breaches | 2: ME F219 + F315, ME F220 + F320 | 0 |
| Units over target (27 unavoidable) | 43, worst +6; 4 people more than a unit under | 27, worst +1.5; nobody under |
| Load, mean ± sd | 5.36 ± 2.07 | 5.42 ± 0.95 |
| First-lecture-choice rate | 66% | 72% |
| Holdings that were a 1st choice / nobody asked for | 57 / 6 | 65 / 3 |
| Unfilled places | 0 | 0 |

Two courses are flagged as short of volunteers: ME G624 (two lecture places, one volunteer) and ME F315 (nobody asked to lecture it, so it goes to someone who didn't fill in the form).

Agreement with the department: 36% of assignments and 53% of lecturers reproduced. The department's allocation breaks the one-CDC rule twice, so differences are expected.

## Convergence

`python -m allocator.cli bench PROBLEM --seeds N --time-limit S [--set '{...}']` solves the same problem with several seeds and reports objective, bound, gap and headline numbers per seed.

| Configuration (ME replay, 120 s limit) | Result |
|---|---|
| 4 workers, everyone eligible (first version) | 45–48% gap at 30 s; first-lecture-choice rate ranged 59–72% across seeds |
| 8 workers, everyone eligible | optimal in 55 s |
| 8 workers, shortlist + greedy hint | optimal in 10–30 s, every seed |
| + admin's rules, exact fractional targets | 5–9% gap at 120 s, a different answer per seed |
| + targets rounded to half units | optimal in 12–22 s, identical across seeds |
| + average standard load, no ICs, HD CDCs not counted, targets rounded | optimal in 15–30 s on an idle machine; a few minutes when busy |
| same, exact targets (**default**: time is not a constraint) | optimal in about 12 minutes |
| current + 12 workers | no faster |
| current + explicit symmetry breaking | slower |

What this shows:
- **Worker count matters most.** CP-SAT runs one strategy per worker, and below 8 it drops the strategies that tighten the bound.
- **Shortlisting costs nothing here.** Considering only volunteers, and opening a section to everyone only when it's short, shrinks the model and reaches the same optimum.
- **Exact targets are expensive to prove, and are the default.** Loads come in half units, so a target like 7.59 leaves the solver proving how to pack fractions. Since time isn't a constraint, targets are kept exact and the limit is 30 minutes; `target_rounding: 0.5` brings a run under a minute at the cost of rounding everyone's target.
- **Explicit symmetry breaking is off by default.** CP-SAT already detects that the ten BITS F103 labs are interchangeable.
- **Solve time depends on the machine being free.** The same problem took 15–30 s on an idle machine and up to 116 s while other work ran. More threads than 8 didn't help.
- **Runs are queued, one at a time.** Each run uses 8 cores, so the API runs one solve at a time (`ALLOCATOR_PARALLEL_RUNS=1`).

## API

| Method and path | Purpose |
|---|---|
| `POST /api/problems/import?department=ME&name=&semester=&format=auto` | Multipart field `file` (`curl -F file=@x.xlsx`) or the raw `.xlsx` as the body. 422 with `issues` if a template has errors. |
| `GET /api/template.xlsx?department=ME[&problem_id=]` | Blank template, or one pre-filled from an existing allocation |
| `GET /api/schema` | Template sheets and columns, plus the JSON Problem schema |
| `GET /api/problems`, `GET/PUT/DELETE /api/problems/{id}` | List, read, replace, delete |
| `PUT /api/problems/{id}/locks` | Replace locks |
| `PATCH /api/problems/{id}/people/{person}` | Teaching Allocation, load overrides, external load, PSRN |
| `GET /api/problems/{id}/validation` | Warnings, import notes and shortfalls |
| `POST /api/problems/{id}/targets` | Standard load, each person's Teaching Allocation and target (body: optional settings) |
| `POST /api/problems/{id}/shortfalls` | Courses short of volunteers under the given settings |
| `POST /api/problems/{id}/reference-report` | Score the hand allocation |
| `POST /api/problems/{id}/runs` | `{settings, label}`; returns 202 and a run record |
| `GET /api/runs/{id}`, `GET /api/runs/{id}/status` | Result or progress |
| `GET /api/runs/{id}/export/{sections.csv, allocation.xlsx, people.csv, timetable.json, assignments.csv}` | Exports |
| `GET /api/settings/defaults` | Default settings |

## Not done yet

- **The timetable database.** The app is self-contained. Reading section sets and CDC/elective standing straight from the timetable app's database, and writing allocations back through its IMS route, would save departments typing them in; it isn't built.
- **Half-semester splits** are done by hand by design; the solver only assigns whole sections.
- **Co-teaching.** Load is split at most two ways on one lecture.
- **Rotation counts** come from the form. Reading them from previous semesters' allocations would make them automatic.

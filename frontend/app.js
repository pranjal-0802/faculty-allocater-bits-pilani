/* Faculty Allocator: a dependency-free single-page app.
 *
 * Routes (hash based, so the backend only serves static files):
 *   #/                 allocations
 *   #/new              start a new allocation (template, upload)
 *   #/guide            how it works
 *   #/p/<id>/<tab>     one allocation: overview, faculty, courses, preferences, locks, runs
 *   #/r/<id>/<tab>     one run: summary, allocation, faculty, compare
 */
"use strict";

const $app = document.getElementById("app");
const $crumbs = document.getElementById("crumbs");
let pollTimer = null;
let tickTimer = null;

// ---- helpers ---------------------------------------------------------------------

async function api(path, opts = {}) {
  const init = { ...opts, headers: { ...(opts.headers || {}) } };
  if (opts.json !== undefined) {
    init.body = JSON.stringify(opts.json);
    init.headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, init);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const detail = data && data.detail ? data.detail : text || res.statusText;
    const message = typeof detail === "string" ? detail : detail.message || JSON.stringify(detail);
    const err = new Error(message);
    err.detail = detail;
    throw err;
  }
  return data;
}

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

function toast(message, error = false) {
  const t = document.getElementById("toast");
  t.textContent = message;
  t.className = "toast" + (error ? " error" : "");
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { t.hidden = true; }, error ? 7000 : 2800);
}

const fmt = (n, d = 2) => (n === null || n === undefined || Number.isNaN(n) ? "—" : Number(n).toFixed(d).replace(/\.?0+$/, ""));
const pct = (n) => (n === null || n === undefined ? "—" : `${Math.round(n * 100)}%`);
const when = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—");
const enc = encodeURIComponent;
const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;
const runName = (r) => r.label || `Run of ${when(r.created_at)}`;

function crumbs(...items) {
  const parts = [];
  items.forEach((it, i) => {
    if (i) parts.push(h("span", { class: "sep" }, "›"));
    parts.push(i === items.length - 1 && typeof it === "string" ? h("span", { class: "here" }, it) : it);
  });
  $crumbs.replaceChildren(...parts);
}

function setNav(key) {
  for (const a of document.querySelectorAll("#nav a")) a.classList.toggle("active", a.dataset.nav === key);
}

function tabs(base, active, items) {
  return h("div", { class: "tabs", role: "tablist" },
    items.map(([key, label, count]) => h("a", { href: `${base}/${key}`, class: key === active ? "active" : "", role: "tab" },
      label, count !== undefined && count !== null ? h("span", { class: "count" }, count) : null)));
}

function table(headers, rows, opts = {}) {
  return h("div", { class: "table-wrap" },
    h("table", { class: opts.class || "" },
      h("thead", {}, h("tr", {}, headers.map((x) => h("th", { class: x.num ? "num" : "" }, x.label ?? x)))),
      h("tbody", {}, rows.length ? rows : h("tr", {}, h("td", { colspan: headers.length, class: "empty" }, opts.empty || "Nothing here.")))));
}

function kpi(label, value, sub, cls = "") {
  return h("div", { class: `kpi ${cls}` }, h("div", { class: "v" }, value), h("div", { class: "k" }, label), sub ? h("div", { class: "s" }, sub) : null);
}

function btn(label, attrs = {}) {
  return h("a", { ...attrs, class: `btn ${attrs.class || ""}` }, label);
}

function callout(kind, title, ...body) {
  return h("div", { class: `callout ${kind}` }, title ? h("b", {}, title) : null, ...body);
}

function fold(title, body, open = false) {
  return h("details", { class: "fold", open: open || null }, h("summary", {}, title), h("div", { class: "fold-body" }, body));
}

function catBadge(course) {
  const cat = course.category === "ELECTIVE" ? "EL" : course.category;
  return [
    h("span", { class: `badge ${cat.toLowerCase()}`, title: course.category_source || "" }, cat),
    course.level && course.level !== "FD" ? h("span", { class: "badge hd" }, course.level) : null,
  ];
}

// ---- domain helpers (mirror backend/allocator/evaluation.py) -----------------------

function indexProblem(problem) {
  const people = new Map(problem.people.map((p) => [p.id, p]));
  const courses = new Map(problem.courses.map((c) => [c.code, c]));
  const prefs = new Map();
  for (const p of problem.preferences) {
    const k = `${p.person_id}|${p.course_code}`;
    if (!prefs.has(k)) prefs.set(k, []);
    prefs.get(k).push(p);
  }
  return { people, courses, prefs };
}

function classify(idx, personId, course, kind) {
  const list = idx.prefs.get(`${personId}|${course.code}`) || [];
  const exact = list.filter((p) => p.kind === kind || p.kind === null);
  if (exact.length) {
    const r = Math.min(...exact.map((p) => p.rank));
    return r <= 3 ? `rank${r}` : "rank4+";
  }
  if (list.length) return "cross_kind";
  return course.service ? "service" : "unlisted";
}

const sectionOf = (course, id) => course.sections.find((s) => s.id === id);

const MATCH_LABEL = {
  rank1: "1st choice", rank2: "2nd choice", rank3: "3rd choice", "rank4+": "lower choice",
  cross_kind: "asked for another part", service: "service duty", unlisted: "did not ask",
};
const KIND = { L: "Lecture", T: "Tutorial", P: "Practical" };

function legend() {
  return h("div", { class: "legend" },
    Object.entries(MATCH_LABEL).filter(([k]) => k !== "rank4+").map(([k, v]) => h("span", { class: `chip ${k}` }, h("span", { class: "dot" }), v)));
}

// ---- router --------------------------------------------------------------------------

async function render() {
  clearInterval(pollTimer);
  clearInterval(tickTimer);
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  window.scrollTo(0, 0);
  try {
    if (parts[0] === "p" && parts[1]) { setNav("home"); await viewProblem(parts[1], parts[2] || "overview"); }
    else if (parts[0] === "r" && parts[1]) { setNav("home"); await viewRun(parts[1], parts[2] || "summary"); }
    else if (parts[0] === "new") { setNav("new"); await viewNew(); }
    else if (parts[0] === "guide") { setNav("guide"); viewGuide(); }
    else { setNav("home"); await viewHome(); }
  } catch (err) {
    console.error(err);
    $app.replaceChildren(callout("bad", "Something went wrong", h("p", {}, err.message), h("a", { href: "#/" }, "Back to allocations")));
  }
}
window.addEventListener("hashchange", render);

// ---- home -------------------------------------------------------------------------------

async function viewHome() {
  crumbs("Allocations");
  const problems = await api("/api/problems");
  const runsByProblem = new Map(await Promise.all(problems.map(async (p) => [p.id, await api(`/api/problems/${enc(p.id)}/runs`).catch(() => [])])));

  const head = h("div", { class: "page-head" },
    h("div", {}, h("h1", {}, "Allocations"),
      h("div", { class: "sub" }, "Each allocation is one department's staffing round: its faculty, its courses' sections, and what everyone asked for.")),
    h("div", { class: "actions" }, btn("+ New allocation", { href: "#/new", class: "primary big" })));

  if (!problems.length) {
    $app.replaceChildren(head, h("div", { class: "card" },
      h("h3", {}, "Nothing here yet"),
      h("p", { class: "muted" }, "Start a new allocation: download the upload template, fill in the department's courses, sections and faculty preferences, and upload it."),
      steps(), h("div", { style: "margin-top:16px" }, btn("Start a new allocation", { href: "#/new", class: "primary" }))));
    return;
  }

  const cards = problems.map((p) => {
    const runs = runsByProblem.get(p.id) || [];
    const last = runs[0];
    const go = () => { location.hash = `#/p/${enc(p.id)}/overview`; };
    return h("div", { class: "card p-card", onclick: go, tabindex: "0", role: "link", onkeydown: (e) => { if (e.key === "Enter") go(); } },
      h("div", { class: "row between" },
        h("div", { class: "name" }, p.name),
        h("span", { class: "badge" }, p.department || "—")),
      h("div", { class: "muted small" }, p.semester || "Semester not set"),
      h("div", { class: "meta" },
        h("span", {}, h("b", {}, p.faculty), " faculty"),
        h("span", {}, h("b", {}, p.courses), " courses"),
        h("span", {}, h("b", {}, p.sections), " sections"),
        h("span", {}, h("b", {}, p.preferences), " preferences")),
      h("div", { class: "foot" },
        h("span", {}, last ? h("span", {}, "Last run ", h("span", { class: `badge ${last.status}` }, last.status)) : "Not run yet"),
        h("span", {}, `Updated ${when(p.updated_at)}`)));
  });
  $app.replaceChildren(head, h("div", { class: "grid three" }, cards));
}

function steps() {
  const step = (n, title, text) => h("div", { class: "step" }, h("div", { class: "n" }, n), h("h3", {}, title), h("p", { class: "muted small" }, text));
  return h("div", { class: "steps" },
    step(1, "Get the template", "A blank template, or last semester's allocation as a pre-filled one. It has a sheet each for courses (CDC or elective), sections and faculty."),
    step(2, "Fill in preferences", "Faculty fill the usual form columns: lecture, practical and tutorial choices, Teaching Allocation, and how many semesters running they have taught each course."),
    step(3, "Upload and run", "The upload is checked row by row. The solver then finds the best allocation under the rules, and you download who takes which section."));
}

// ---- new allocation ----------------------------------------------------------------------

async function viewNew() {
  crumbs(h("a", { href: "#/" }, "Allocations"), "New allocation");
  const departments = await api("/api/departments").catch(() => []);
  const deptOptions = departments.length ? departments.map((d) => h("option", { value: d.code }, `${d.code} — ${d.name}`)) : [h("option", { value: "ME" }, "ME")];

  const dept = h("select", {}, deptOptions);
  const problems = await api("/api/problems").catch(() => []);
  const blankLink = btn("⬇ Blank template", { class: "primary" });
  const previous = h("select", {}, h("option", { value: "" }, "Start from a previous allocation…"),
    problems.map((p) => h("option", { value: p.id }, `${p.name}${p.semester ? ` (${p.semester})` : ""}`)));
  const prevLink = btn("⬇ Pre-filled template");
  const link = () => {
    blankLink.href = `/api/template.xlsx?department=${enc(dept.value)}`;
    prevLink.href = previous.value ? `/api/template.xlsx?problem_id=${enc(previous.value)}` : "#/new";
    prevLink.style.opacity = previous.value ? "1" : ".5";
  };
  dept.onchange = link; previous.onchange = link; link();

  const file = h("input", { type: "file", accept: ".xlsx", hidden: true });
  const emptyText = () => h("div", {}, h("div", { class: "big-ico" }, "⇪"), h("b", {}, "Drop the filled-in template here"), h("div", { class: "muted small" }, "or click to choose an .xlsx file"));
  const zoneText = h("div", {}, emptyText());
  const zone = h("div", { class: "dropzone", onclick: () => file.click() }, zoneText);
  const showFile = () => {
    const f = file.files[0];
    zone.classList.toggle("has-file", !!f);
    zoneText.replaceChildren(f
      ? h("div", {}, h("div", { class: "big-ico" }, "✓"), h("b", {}, f.name), h("div", { class: "muted small" }, `${(f.size / 1024).toFixed(0)} KB · click to choose another`))
      : emptyText());
  };
  file.onchange = showFile;
  zone.ondragover = (e) => { e.preventDefault(); zone.classList.add("over"); };
  zone.ondragleave = () => zone.classList.remove("over");
  zone.ondrop = (e) => { e.preventDefault(); zone.classList.remove("over"); if (e.dataTransfer.files.length) { file.files = e.dataTransfer.files; showFile(); } };

  const name = h("input", { placeholder: "e.g. ME Sem II 2026-27", style: "min-width:240px" });
  const semester = h("input", { placeholder: "e.g. 2026-27 Sem II" });
  const format = h("select", {},
    h("option", { value: "auto" }, "Detect automatically"),
    h("option", { value: "template" }, "Upload template"),
    h("option", { value: "legacy" }, "Legacy department workbook"));
  const issuesBox = h("div");
  const go = h("button", { class: "primary big" }, "Upload and check");
  go.onclick = async () => {
    if (!file.files[0]) return toast("Choose the filled-in template first.", true);
    go.disabled = true; go.textContent = "Checking…";
    issuesBox.replaceChildren();
    try {
      const q = new URLSearchParams({ department: dept.value, name: name.value, semester: semester.value, format: format.value });
      const form = new FormData();
      form.append("file", file.files[0]);
      const res = await api(`/api/problems/import?${q}`, { method: "POST", body: form });
      toast(`Imported ${res.format === "legacy" ? "legacy workbook" : "template"} with ${plural(res.notes.length, "note")}.`);
      location.hash = `#/p/${enc(res.id)}/overview`;
    } catch (err) {
      go.disabled = false; go.textContent = "Upload and check";
      if (err.detail && err.detail.issues) issuesBox.replaceChildren(issueTable(err.detail.message, err.detail.issues));
      else toast(err.message, true);
    }
  };

  $app.replaceChildren(
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "New allocation"),
      h("div", { class: "sub" }, "Three steps: get the department's template, have the faculty preferences filled in, upload it."))),
    h("div", { class: "card" },
      h("div", { class: "card-head" }, h("h3", {}, "1 · Get the template")),
      h("p", { class: "muted" }, "The template has a sheet each for courses (with whether each is a CDC or an elective), their lecture, tutorial and practical sections, and the faculty with their preferences. Start blank, or from a previous allocation: its courses, sections and faculty come pre-filled, ready to adjust for the new semester."),
      h("div", { class: "row" },
        h("label", { class: "field" }, "Department", dept),
        h("div", { class: "row", style: "align-self:flex-end" }, blankLink)),
      h("div", { class: "row", style: "margin-top:10px" },
        h("label", { class: "field" }, "Or start from", previous),
        h("div", { class: "row", style: "align-self:flex-end" }, prevLink))),
    h("div", { class: "card" },
      h("div", { class: "card-head" }, h("h3", {}, "2 · Fill in preferences")),
      h("p", { class: "muted" }, "Courses: each course with its units and whether it is a CDC or an elective (EL). Sections: every L1, T1, P1… and how many faculty it needs. Faculty: lecture, practical and tutorial choices (from the drop-downs), Teaching Allocation (50 = the standard load), and for each choice how many semesters in a row the person has taught it. At 3 the course rotates to someone else."),
      h("a", { href: "#/guide" }, "What the allocator does with each column →")),
    h("div", { class: "card" },
      h("div", { class: "card-head" }, h("h3", {}, "3 · Upload")),
      file, zone,
      h("div", { class: "row", style: "margin:14px 0 6px" },
        h("label", { class: "field" }, "Name", name),
        h("label", { class: "field" }, "Semester label", semester)),
      fold("Advanced", h("div", { class: "row" }, h("label", { class: "field" }, "File format", format),
        h("span", { class: "muted small" }, "A legacy workbook (like ME's Sem II 2025-26 allocation sheet) is read best-effort, for replaying past semesters."))),
      h("div", { class: "row" }, go, h("span", { class: "muted small" }, "Every row is checked; if anything is wrong nothing is imported and every problem is listed.")),
      issuesBox));
}

function issueTable(message, issues) {
  const errors = issues.filter((i) => i.severity === "error");
  const rows = issues.slice().sort((a, b) => (a.severity === b.severity ? 0 : a.severity === "error" ? -1 : 1))
    .map((i) => h("tr", {},
      h("td", {}, h("span", { class: `badge ${i.severity === "error" ? "over" : "under"}` }, i.severity)),
      h("td", {}, i.sheet), h("td", { class: "num" }, i.row ?? ""), h("td", {}, i.column ?? ""), h("td", {}, i.message)));
  return h("div", { style: "margin-top:16px" },
    callout("bad", message, h("span", {}, `Fix the ${plural(errors.length, "error")} below and upload again.`)),
    table(["", "Sheet", { label: "Row", num: 1 }, "Column", "Problem"], rows));
}

// ---- guide -------------------------------------------------------------------------------

function viewGuide() {
  crumbs("How it works");
  const rule = (title, text) => h("div", { class: "card", style: "margin:0" }, h("h3", {}, title), h("p", { class: "muted", style: "margin:0" }, text));
  $app.replaceChildren(
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "How it works"),
      h("div", { class: "sub" }, "The allocator decides which professor takes each lecture, tutorial and practical section, using Google OR-Tools' CP-SAT solver: the same engine as the timetable scheduler."))),
    h("div", { class: "card" }, steps()),
    h("h2", {}, "The rules"),
    h("div", { class: "grid two" },
      rule("Load follows Teaching Allocation", "The standard load is the department's total teaching units divided by its number of faculty. Someone at 50% gets the standard load, 70% asks for 1.4 times it, 40% for 0.8 times; a blank counts as 50%. The target is a ceiling: when the targets add up to less than the work, the excess is spread as evenly as possible."),
      rule("At most one CDC each", "Two courses can be a CDC and an elective, or two electives, never two CDCs. Any section of a CDC counts. Only first-degree CDCs count: service courses (BITS F103, BITS F111) and higher-degree courses don't. The department marks each course CDC or EL on upload."),
      rule("Rotation after three semesters", "A course someone has taught three semesters running goes to someone else."),
      rule("Courses short of volunteers are flagged", "If two lecture sections have only one volunteer, or nobody asked for a lab, the course is flagged before the run and again in the result, with who ended up taking the places."),
      rule("Preferences", "First choices earn the most, then second and third. Someone given a course they didn't list costs points, except for service duties. Tutorials go to the course's lecturers where possible."),
      rule("Units, as the timetable app counts them", "A lecture counts its lecture units, shared between co-teachers; a practical its lab units; a tutorial 1 (2 for double-tutorial courses). A WILP course is 3 units shared among its faculty.")),
    h("h2", {}, "Not part of the allocation"),
    h("div", { class: "grid two" },
      rule("Instructors-in-charge", "The department picks each course's IC from its instructors after the allocation."),
      rule("Research scholars and half-semester splits", "Scholar-run sections are listed with no faculty needed. Splitting a section half-and-half between two faculty is arranged by hand; pre-assign both people if it is already decided.")));
}

// ---- allocation (problem) --------------------------------------------------------------------

async function viewProblem(id, tab) {
  const problem = await api(`/api/problems/${enc(id)}`);
  const [validation, targets] = await Promise.all([
    api(`/api/problems/${enc(id)}/validation`),
    api(`/api/problems/${enc(id)}/targets`, { method: "POST" }),
  ]);
  const base = `#/p/${enc(id)}`;
  crumbs(h("a", { href: "#/" }, "Allocations"), problem.name);
  const idx = indexProblem(problem);
  const sections = problem.courses.flatMap((c) => c.sections);
  const places = sections.reduce((n, s) => n + s.faculty_min, 0);
  const attention = validation.shortfalls.length + validation.warnings.length;

  const body = h("div");
  $app.replaceChildren(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, problem.name), h("div", { class: "sub" }, [problem.department, problem.semester].filter(Boolean).join(" · "))),
      h("div", { class: "actions" },
        btn("⬇ As template", { href: `/api/template.xlsx?problem_id=${enc(problem.id)}`, title: "This allocation as a filled-in upload template: a starting point for next semester" }),
        h("button", { class: "danger", onclick: async () => {
          if (!confirm(`Delete "${problem.name}" and all its runs?`)) return;
          await api(`/api/problems/${enc(problem.id)}`, { method: "DELETE" });
          location.hash = "#/";
        } }, "Delete"),
        btn("▶ Run allocation", { href: `${base}/runs`, class: "primary" }))),
    h("div", { class: "kpis" },
      kpi("Faculty", problem.people.length),
      kpi("Courses", problem.courses.length, `${problem.courses.filter((c) => c.category === "CDC").length} CDC · ${problem.courses.filter((c) => c.category === "ELECTIVE").length} EL`),
      kpi("Sections", sections.length, `${places} faculty places`),
      kpi("Standard load", fmt(targets.baseline), `units at ${fmt(targets.baseline_percent)}% Teaching Allocation`, "accent"),
      kpi("Must exceed targets", fmt(Math.max(0, targets.work - targets.target_total)), `units: ${fmt(targets.work)} of work, targets add to ${fmt(targets.target_total)}`),
      kpi("Needs attention", attention, `${plural(validation.shortfalls.length, "shortfall")} · ${plural(validation.warnings.length, "warning")}`, attention ? "bad" : "good")),
    tabs(base, tab, [["overview", "Overview"], ["faculty", "Faculty", problem.people.length], ["courses", "Courses", problem.courses.length],
      ["preferences", "Preferences", problem.preferences.length], ["locks", "Pre-assignments", problem.locks.length], ["runs", "Runs"]]),
    body);
  const views = { overview: problemOverview, faculty: problemFaculty, courses: problemCourses, preferences: problemPreferences, locks: problemLocks, runs: problemRuns };
  await (views[tab] || problemOverview)(problem, idx, body, { validation, targets });
}

async function problemOverview(problem, idx, body, { validation }) {
  body.append(
    h("h2", { style: "margin-top:0" }, "Short of volunteers"),
    shortfallList(validation.shortfalls),
    h("h2", {}, "Warnings"),
    validation.warnings.length
      ? h("div", { class: "card" }, h("ul", { class: "notes" }, validation.warnings.map((n) => h("li", {}, n))))
      : callout("good", null, "No problems found in the data."));

  if (problem.reference && problem.reference.assignments.length) {
    const rep = await api(`/api/problems/${enc(problem.id)}/reference-report`, { method: "POST" });
    body.append(h("h2", {}, "The department's own allocation"),
      h("p", { class: "muted" }, "The allocation the department made by hand, scored with the same rules. The solver never sees it; runs are compared against it."),
      reportKpis(rep), breaches(rep));
  }
  body.append(h("h2", {}, "Import notes"),
    problem.notes.length ? fold(`${plural(problem.notes.length, "note")} from the import`, h("ul", { class: "notes small" }, problem.notes.map((n) => h("li", {}, n)))) : h("p", { class: "muted" }, "None."));
}

function shortfallList(list, withOutcome = false) {
  if (!list.length) return callout("good", null, "Every course has at least as many volunteers as places.");
  const byCourse = new Map();
  for (const f of list) {
    if (!byCourse.has(f.course_code)) byCourse.set(f.course_code, []);
    byCourse.get(f.course_code).push(f);
  }
  const cards = [...byCourse.values()].map((parts) => {
    const none = parts.every((f) => f.severity === "none");
    return h("div", { class: `callout ${none ? "bad" : "warn"}`, style: "margin:0" },
      h("div", { class: "row between" },
        h("span", {}, h("span", { class: "mono", style: "font-weight:700" }, parts[0].course_code), " ", h("span", { class: "muted" }, parts[0].title)),
        h("span", { class: `badge ${none ? "none" : "short"}` }, none ? "no volunteers" : "short")),
      parts.map((f) => h("div", { style: "margin-top:6px" },
        h("b", { style: "display:inline" }, `${KIND[f.kind] || f.kind}: `),
        `${plural(f.places, "place")}, ${f.volunteers.length ? `volunteers: ${f.volunteers.join(", ")}` : "nobody asked"}.`,
        f.rotated_out.length ? h("div", { class: "small muted" }, `Rotated out: ${f.rotated_out.join(", ")}`) : null,
        withOutcome && f.outcome.length ? h("div", { class: "small" }, f.outcome.map((o) => h("div", { class: /did not ask|unfilled/.test(o) ? "nomatch" : "" }, "→ ", o))) : null)));
  });
  return h("div", {},
    h("p", { class: "muted small" }, "The solver still staffs these, by giving a volunteer more than one section or bringing in someone who didn't ask. Check them by hand."),
    h("div", { class: "grid two" }, cards));
}

function breaches(rep) {
  if (!rep.rule_breaches.length) return callout("good", null, "No hard rule broken: nobody holds two CDCs, and nobody teaches a course they are rotated out of.");
  return callout("bad", "Hard rules broken", h("ul", { class: "notes" }, rep.rule_breaches.map((b) => h("li", {}, b))));
}

function reportKpis(rep) {
  const f = rep.load || {};
  const m = rep.preference_matches || {};
  const total = Object.values(m).reduce((a, b) => a + b, 0);
  return h("div", { class: "kpis" },
    kpi("Load, mean ± sd", `${fmt(f.mean)} ± ${fmt(f.stdev)}`, `range ${fmt(f.min)}–${fmt(f.max)} units`),
    kpi("Over target", `+${fmt(f.excess)}`, `units in all; worst +${fmt(f.max_over)}`),
    kpi("First lecture choice", pct(rep.first_lecture_choice_rate)),
    kpi("1st-choice holdings", m.rank1 ?? 0, `of ${total}`),
    kpi("Not asked for", m.unlisted ?? 0),
    kpi("Two CDCs", rep.cdc_breaches, rep.rotation_breaches ? `${rep.rotation_breaches} rotation breaches` : "", rep.cdc_breaches ? "bad" : "good"),
    kpi("Unfilled places", rep.unfilled_slots, "", rep.unfilled_slots ? "bad" : "good"));
}

function problemFaculty(problem, idx, body, { targets }) {
  const search = h("input", { type: "search", placeholder: "Search faculty" });
  const holder = h("div");
  const draw = () => {
    const q = search.value.toLowerCase();
    const rows = problem.people.filter((p) => p.name.toLowerCase().includes(q)).map((p) => {
      const tr = h("tr");
      const dirty = () => tr.classList.add("dirty");
      const pctIn = h("input", { type: "number", step: "10", value: p.teaching_percent ?? "", placeholder: "50", oninput: dirty });
      const lo = h("input", { type: "number", step: "0.5", value: p.load_min ?? "", placeholder: "auto", oninput: dirty });
      const hi = h("input", { type: "number", step: "0.5", value: p.load_max ?? "", placeholder: "auto", oninput: dirty });
      const ext = h("input", { type: "number", step: "0.5", value: p.external_load, oninput: dirty });
      const save = h("button", { class: "ghost", onclick: async () => {
        const clear = [];
        const patch = { external_load: Number(ext.value || 0) };
        if (pctIn.value === "") clear.push("teaching_percent"); else patch.teaching_percent = Number(pctIn.value);
        if (lo.value === "") clear.push("load_min"); else patch.load_min = Number(lo.value);
        if (hi.value === "") clear.push("load_max"); else patch.load_max = Number(hi.value);
        await api(`/api/problems/${enc(problem.id)}/people/${enc(p.id)}`, { method: "PATCH", json: { ...patch, clear } });
        toast(`Saved ${p.name}`);
        render();
      } }, "Save");
      const band = targets.bands[p.id];
      const n = problem.preferences.filter((x) => x.person_id === p.id).length;
      tr.append(
        h("td", {}, h("b", {}, p.name), h("div", { class: "muted small" }, [p.designation, p.psrn].filter(Boolean).join(" · "))),
        h("td", {}, pctIn, targets.assumed.includes(p.id) ? h("div", { class: "faint small" }, "blank: 50%") : null),
        h("td", { class: "num" }, h("b", {}, band ? fmt(band.max) : "—")),
        h("td", { class: "num" }, n),
        h("td", {}, h("div", { class: "row" }, lo, "–", hi)),
        h("td", {}, ext, p.external_notes.length ? h("div", { class: "faint small" }, p.external_notes.join("; ")) : null),
        h("td", {}, save));
      return tr;
    });
    holder.replaceChildren(table(["Faculty", "Teaching Allocation %", { label: "Target", num: 1 }, { label: "Prefs", num: 1 }, "Override target band", "External (WILP)", ""], rows));
  };
  search.oninput = draw;
  body.append(
    callout("info", null, `Standard load ${fmt(targets.baseline)} units = ${fmt(targets.work)} units of work ÷ ${problem.people.length} faculty. A target is the standard load × Teaching Allocation ÷ 50. Edit a row, press Save, then start a new run.`),
    h("div", { class: "row", style: "margin-bottom:12px" }, search), holder);
  draw();
}

function problemCourses(problem, idx, body) {
  const filter = h("select", {}, h("option", { value: "" }, "All courses"), h("option", { value: "CDC" }, "CDCs"), h("option", { value: "ELECTIVE" }, "Electives"), h("option", { value: "HD" }, "Higher degree"));
  const search = h("input", { type: "search", placeholder: "Search courses" });
  const demand = (code, kind) => new Set(problem.preferences.filter((p) => p.course_code === code && (p.kind === kind || p.kind === null)).map((p) => p.person_id)).size;
  const holder = h("div");
  const draw = () => {
    const q = search.value.toLowerCase();
    const rows = problem.courses
      .filter((c) => !filter.value || (filter.value === "HD" ? c.level === "HD" : c.category === filter.value))
      .filter((c) => (c.code + c.title).toLowerCase().includes(q))
      .map((c) => h("tr", {},
        h("td", {}, h("span", { class: "mono", style: "font-weight:700" }, c.code), h("div", { class: "muted small" }, c.title)),
        h("td", {}, catBadge(c), c.service ? h("div", { class: "faint small" }, "service duty") : null,
          c.category_source ? h("div", { class: "faint small", style: "max-width:260px" }, c.category_source) : null),
        h("td", { class: "num nowrap" }, `${fmt(c.lecture_units)} / ${fmt(c.lab_units)}`),
        h("td", {}, c.sections.length ? c.sections.map((s) => h("span", { class: `chip ${s.faculty_max ? "" : "none"}`, title: s.external_staff.join(", ") || "" },
          h("b", {}, s.id), s.faculty_max ? ` · ${s.faculty_max}F` : " · scholars")) : h("span", { class: "faint small" }, "no sections")),
        h("td", { class: "num" }, demand(c.code, "L")), h("td", { class: "num" }, demand(c.code, "T")), h("td", { class: "num" }, demand(c.code, "P"))));
    holder.replaceChildren(table(["Course", "Category", { label: "L / P units", num: 1 }, "Sections (faculty needed)",
      { label: "Want L", num: 1 }, { label: "Want T", num: 1 }, { label: "Want P", num: 1 }], rows));
  };
  filter.onchange = draw; search.oninput = draw;
  body.append(h("div", { class: "row", style: "margin-bottom:12px" }, search, filter,
    h("span", { class: "muted small" }, "Sections as the department listed them; \"scholars\" means no faculty needed. Hover a category for where it came from.")), holder);
  draw();
}

function problemPreferences(problem, idx, body) {
  const search = h("input", { type: "search", placeholder: "Filter by course or person" });
  const holder = h("div");
  const draw = () => {
    const q = search.value.toLowerCase();
    const rows = [];
    for (const c of problem.courses) {
      const prefs = problem.preferences.filter((p) => p.course_code === c.code);
      const text = (c.code + c.title + prefs.map((p) => idx.people.get(p.person_id)?.name || "").join(" ")).toLowerCase();
      if (q && !text.includes(q)) continue;
      const cells = ["L", "T", "P"].map((k) => h("td", {}, prefs.filter((p) => p.kind === k || p.kind === null).sort((a, b) => a.rank - b.rank).map((p) => {
        const person = idx.people.get(p.person_id);
        const n = p.consecutive_count;
        const known = n !== null && n !== undefined;
        const rotated = known && n >= 3;
        const mark = known && n ? ` ↻${n}` : !known && p.taught_consecutively ? " ↻?" : "";
        return h("span", { class: `chip rank${Math.min(p.rank, 3)} ${rotated ? "ref-miss" : ""}`,
          title: `${p.source}${known ? ` · taught ${n} semesters running${rotated ? " (rotated out)" : ""}` : p.taught_consecutively ? " · taught consecutively, count unknown" : ""}` },
          h("span", { class: "dot" }), `${person ? person.name : p.person_id} (${p.rank})${mark}`);
      })));
      rows.push(h("tr", {}, h("td", {}, h("span", { class: "mono", style: "font-weight:700" }, c.code), " ", catBadge(c), h("div", { class: "muted small" }, c.title)), cells));
    }
    holder.replaceChildren(table(["Course", "Lecture", "Tutorial", "Practical"], rows));
  };
  search.oninput = draw;
  body.append(
    h("div", { class: "row", style: "margin-bottom:12px" }, search,
      h("span", { class: "muted small" }, "Number in brackets: the rank. ↻n: taught n semesters running (red at 3: rotated out). ↻?: \"Yes\" without a count.")),
    holder);
  draw();
}

async function saveLocks(problem, locks) {
  problem.locks = await api(`/api/problems/${enc(problem.id)}/locks`, { method: "PUT", json: locks });
}

function problemLocks(problem, idx, body) {
  const person = h("select", {}, problem.people.map((p) => h("option", { value: p.id }, p.name)));
  const course = h("select", {}, problem.courses.filter((c) => c.sections.some((s) => s.faculty_max > 0)).map((c) => h("option", { value: c.code }, `${c.code} — ${c.title}`)));
  const target = h("select");
  const mode = h("select", {}, h("option", { value: "require" }, "must take"), h("option", { value: "forbid" }, "must not take"));
  const fillTargets = () => {
    const c = idx.courses.get(course.value);
    target.replaceChildren(...(c ? c.sections.filter((s) => s.faculty_max > 0).map((s) => h("option", { value: s.id }, s.id)) : []));
  };
  course.onchange = fillTargets; fillTargets();
  const add = h("button", { class: "primary", onclick: async () => {
    await saveLocks(problem, [...problem.locks, { person_id: person.value, course_code: course.value, target: target.value, mode: mode.value, note: "" }]);
    toast("Pre-assignment added"); render();
  } }, "Add");

  const rows = problem.locks.map((l, i) => h("tr", {},
    h("td", {}, idx.people.get(l.person_id)?.name || l.person_id),
    h("td", {}, h("span", { class: `badge ${l.mode === "require" ? "ok" : "over"}` }, l.mode === "require" ? "must take" : "must not take")),
    h("td", { class: "mono" }, `${l.course_code} ${l.target}`), h("td", { class: "small muted" }, l.note || ""),
    h("td", {}, h("button", { class: "ghost danger", onclick: async () => { await saveLocks(problem, problem.locks.filter((_, j) => j !== i)); render(); } }, "Remove"))));
  body.append(
    h("p", { class: "muted" }, "Decisions already made, which the solver must respect: someone must (or must not) take a section. A pre-assignment wins over the one-CDC and rotation rules; the run will say when it does. You can also pin or forbid straight from a run's Allocation tab."),
    h("div", { class: "card" }, h("div", { class: "row" }, person, mode, course, target, add)),
    table(["Faculty", "Rule", "Section", "Note", ""], rows, { empty: "No pre-assignments." }),
    problem.locks.length ? h("div", { style: "margin-top:12px" }, h("button", { class: "danger", onclick: async () => {
      if (!confirm("Remove every pre-assignment?")) return; await saveLocks(problem, []); render();
    } }, "Remove all")) : null);
}

// ---- settings form ---------------------------------------------------------------------------

const SETTINGS_META = [
  ["Rules", true, [
    ["one_cdc_per_faculty", "At most one CDC per faculty", "Any section of a CDC counts. Service courses don't."],
    ["hd_cdcs_count", "Higher-degree CDCs count too", "Off (default): only first-degree CDCs count towards the one-CDC rule."],
    ["rotation_limit", "Rotate after this many semesters running", "0 turns rotation off."],
    ["rotation_scope", "Rotation bars", "course: every section of it. kind: only the kind the count was recorded for."],
    ["rotation_yes_without_count", "Treat a bare \"Yes\" as at the limit", "The old form only said Yes/No."],
  ]],
  ["Load", true, [
    ["load_baseline", "Standard load (units)", "Empty: worked out as below."],
    ["baseline_mode", "Standard load is", "average: total units ÷ faculty. fair_share: the load at which all targets add up to the work."],
    ["default_teaching_percent", "Blank Teaching Allocation counts as", ""],
    ["load_cap", "Absolute load cap (units)", ""],
  ]],
  ["Preferences", false, [
    ["rank_weights", "Reward by rank", "Points for a 1st, 2nd, 3rd choice."],
    ["cross_kind_weight", "Asked for another part", "Listed the course, but for a different kind of section."],
    ["unlisted_penalty", "Not asked for (penalty)", ""],
    ["unlisted_service_penalty", "Unlisted service duty (penalty)", "Normally 0."],
  ]],
  ["Weights and structure", false, [
    ["overload_penalty", "Over-target penalty / unit", ""],
    ["overload_steep_after", "Overload gets steeper after (units)", ""],
    ["overload_steep_penalty", "…extra penalty / unit", ""],
    ["underload_penalty", "Under-band penalty / unit", ""],
    ["max_overload_penalty", "Worst-overload penalty / unit", ""],
    ["load_tolerance_below", "Band below target (units)", ""],
    ["target_rounding", "Round targets to (units)", "0 (default) keeps them exact; 0.5 is much faster but approximate."],
    ["tutorial_follows_lecture", "Tutorials go to the course's lecturers", ""],
    ["tutorial_follows_lecture_penalty", "…penalty when not", ""],
    ["lecturer_takes_tutorial", "Each lecturer takes a tutorial", ""],
    ["lecturer_takes_tutorial_penalty", "…penalty when not", ""],
    ["max_courses", "Distinct courses per faculty (soft cap)", ""],
    ["extra_course_penalty", "…penalty per extra course", ""],
    ["eligibility", "Who may be placed where", "shortlist: volunteers, opened up when a section is short. penalised: anyone. strict: volunteers only."],
    ["shortlist_spare", "Shortlist spare volunteers", ""],
  ]],
  ["Search", false, [
    ["time_limit_seconds", "Time limit (seconds)", ""],
    ["num_workers", "Solver threads", "8 or more lets CP-SAT prove optimality."],
    ["greedy_hint", "Start from a greedy allocation", ""],
    ["symmetry_breaking", "Order identical sections", "Off by default; CP-SAT finds this itself."],
    ["random_seed", "Random seed", ""],
  ]],
];
const ENUMS = {
  tutorial_follows_lecture: ["off", "soft", "hard"], lecturer_takes_tutorial: ["off", "soft", "hard"],
  eligibility: ["shortlist", "penalised", "strict"], rotation_scope: ["course", "kind"], baseline_mode: ["average", "fair_share"],
};
const NULLABLE = new Set(["load_baseline", "default_teaching_percent"]);

function settingsForm(defaults) {
  const getters = {};
  const groups = SETTINGS_META.map(([title, open, fields]) => fold(title,
    h("div", { class: "settings-grid" }, fields.filter(([key]) => key in defaults).map(([key, label, help]) => {
      const value = defaults[key];
      let input;
      if (ENUMS[key]) {
        input = h("select", {}, ENUMS[key].map((o) => h("option", { value: o, selected: o === value ? true : null }, o)));
        getters[key] = () => input.value;
      } else if (typeof value === "boolean") {
        const box = h("input", { type: "checkbox", checked: value ? true : null });
        const text = h("span", {}, value ? "on" : "off");
        input = h("span", { class: "switch" }, box, text);
        box.onchange = () => { text.textContent = box.checked ? "on" : "off"; };
        getters[key] = () => box.checked;
      } else if (NULLABLE.has(key)) {
        input = h("input", { type: "number", value: value ?? "", step: "any", placeholder: "auto" });
        getters[key] = () => (input.value === "" ? null : Number(input.value));
      } else if (value && typeof value === "object") {
        const inputs = Object.entries(value).map(([rank, w]) => [rank, h("input", { type: "number", value: w, style: "width:70px" })]);
        input = h("div", { class: "row" }, inputs.map(([rank, el]) => h("span", { class: "small muted" }, `${rank}: `, el)));
        getters[key] = () => Object.fromEntries(inputs.map(([rank, el]) => [rank, Number(el.value)]));
      } else {
        input = h("input", { type: "number", value, step: "any" });
        getters[key] = () => Number(input.value);
      }
      return h("label", { class: "field" }, label, input, help ? h("span", { class: "help" }, help) : null);
    })), open));
  return { el: h("div", {}, groups), read: () => ({ ...defaults, ...Object.fromEntries(Object.entries(getters).map(([k, g]) => [k, g()])) }) };
}

async function problemRuns(problem, idx, body) {
  const [runs, defaults] = await Promise.all([api(`/api/problems/${enc(problem.id)}/runs`), api("/api/settings/defaults")]);
  const form = settingsForm(defaults);
  const label = h("input", { placeholder: "Label (optional), e.g. \"HD CDCs not counted\"", style: "min-width:300px" });
  const start = h("button", { class: "primary big" }, "▶ Run allocation");
  start.onclick = async () => {
    let settings;
    try { settings = form.read(); } catch (err) { return toast(`Settings: ${err.message}`, true); }
    start.disabled = true; start.textContent = "Starting…";
    try {
      const run = await api(`/api/problems/${enc(problem.id)}/runs`, { method: "POST", json: { settings, label: label.value } });
      location.hash = `#/r/${run.id}/summary`;
    } catch (err) { toast(err.message, true); start.disabled = false; start.textContent = "▶ Run allocation"; }
  };
  const rows = runs.map((r) => h("tr", {},
    h("td", {}, h("a", { href: `#/r/${r.id}/summary` }, h("b", {}, runName(r)))),
    h("td", {}, h("span", { class: `badge ${r.status}` }, r.status)),
    h("td", { class: "small" }, when(r.created_at)),
    h("td", { class: "small muted" }, r.error || ""),
    h("td", {}, h("button", { class: "ghost danger", onclick: async () => { await api(`/api/runs/${r.id}`, { method: "DELETE" }); render(); } }, "Delete"))));
  body.append(
    h("div", { class: "card" },
      h("div", { class: "card-head" }, h("h3", {}, "New run"), h("span", { class: "muted small" }, `The solver stops as soon as it has proved the best allocation: usually a few minutes, at most ${fmt(defaults.time_limit_seconds / 60, 0)} minutes.`)),
      h("p", { class: "muted" }, "The defaults follow the admin's rules. Change anything below to try a variant; each run is kept so you can compare."),
      form.el,
      h("div", { class: "row" }, start, label)),
    h("h2", {}, "Previous runs"),
    table(["Run", "Status", "Started", "", ""], rows, { empty: "No runs yet." }));
}

// ---- run ----------------------------------------------------------------------------------------

async function viewRun(id, tab) {
  const run = await api(`/api/runs/${enc(id)}`);
  const problem = await api(`/api/problems/${enc(run.problem_id)}`);
  const base = `#/r/${enc(id)}`;
  crumbs(h("a", { href: "#/" }, "Allocations"), h("a", { href: `#/p/${enc(problem.id)}/runs` }, problem.name), runName(run));

  if (run.status === "pending" || run.status === "running") {
    const limit = run.settings.time_limit_seconds;
    const started = new Date(run.created_at).getTime();
    const bar = h("div", { style: "width:0%" });
    const clock = h("b", {}, "0 s");
    const tick = () => {
      const s = Math.max(0, (Date.now() - started) / 1000);
      clock.textContent = `${Math.round(s)} s`;
      bar.style.width = `${Math.min(100, (s / limit) * 100)}%`;
    };
    tick();
    $app.replaceChildren(h("div", { class: "card", style: "max-width:640px;margin:40px auto;text-align:center;padding:32px" },
      h("div", { style: "display:grid;place-items:center;margin-bottom:16px" }, h("div", { class: "spinner" })),
      h("h1", {}, "Finding the best allocation…"),
      h("p", { class: "muted", style: "margin:10px 0 20px" }, "Elapsed ", clock, `. The solver stops as soon as it has proved no better allocation exists, and at ${fmt(limit, 0)} seconds at the latest.`),
      h("div", { class: "progress" }, bar)));
    tickTimer = setInterval(tick, 500);
    pollTimer = setInterval(async () => {
      const s = await api(`/api/runs/${enc(id)}/status`).catch(() => null);
      if (s && s.status !== "pending" && s.status !== "running") { clearInterval(pollTimer); clearInterval(tickTimer); render(); }
    }, 1500);
    return;
  }

  const result = run.result;
  if (!result || !result.allocation) {
    $app.replaceChildren(callout("bad", "This run did not produce an allocation",
      h("p", {}, run.error || result?.error || "No allocation was produced."),
      (result?.warnings || []).length ? h("ul", { class: "notes" }, result.warnings.map((w) => h("li", {}, w))) : null,
      h("a", { href: `#/p/${enc(problem.id)}/locks` }, "Check the pre-assignments →")));
    return;
  }
  const idx = indexProblem(problem);
  const body = h("div");
  const optimal = result.status === "OPTIMAL";
  $app.replaceChildren(
    h("div", { class: "page-head" },
      h("div", {}, h("h1", {}, runName(run)),
        h("div", { class: "sub" }, h("span", { class: `badge ${result.status}` }, optimal ? "Proven optimal" : result.status), ` · solved in ${fmt(result.solve_seconds, 1)} s`)),
      h("div", { class: "actions" },
        btn("⬇ Allocation (xlsx)", { href: `/api/runs/${run.id}/export/allocation.xlsx`, class: "primary", download: "" }),
        btn("Sections CSV", { href: `/api/runs/${run.id}/export/sections.csv`, download: "" }),
        btn("Faculty CSV", { href: `/api/runs/${run.id}/export/people.csv`, download: "" }),
        btn("Timetable JSON", { href: `/api/runs/${run.id}/export/timetable.json`, download: "" }))),
    tabs(base, tab, [["summary", "Summary"], ["allocation", "Allocation"], ["faculty", "Faculty"], ...(result.comparison ? [["compare", "vs department"]] : [])]),
    body);
  const views = { summary: runSummary, allocation: runAllocation, faculty: runFaculty, compare: runCompare };
  (views[tab] || runSummary)(run, problem, idx, body);
}

function runSummary(run, problem, idx, body) {
  const r = run.result;
  const t = r.report.targets;
  const ref = r.comparison?.reference_report;
  body.append(reportKpis(r.report));
  if (t) body.append(callout("info", null,
    `Standard load ${fmt(t.baseline)} units at ${fmt(t.baseline_percent)}% Teaching Allocation. The work is ${fmt(t.work)} units and the targets add up to ${fmt(t.target_total)}, so ${fmt(t.unavoidable_excess)} units have to go over someone's target whatever the allocation.`));
  body.append(breaches(r.report));

  if (ref) {
    const m = (rep) => rep.preference_matches || {};
    const lines = [
      ["Two-CDC breaches", ref.cdc_breaches, r.report.cdc_breaches, "low"],
      ["Units over target", ref.load.excess, r.report.load.excess, "low"],
      ["Worst single overload", ref.load.max_over, r.report.load.max_over, "low"],
      ["Load spread (sd)", ref.load.stdev, r.report.load.stdev, "low"],
      ["People well under target", ref.load.under, r.report.load.under, "low"],
      ["First lecture choice", ref.first_lecture_choice_rate, r.report.first_lecture_choice_rate, "high", pct],
      ["1st-choice holdings", m(ref).rank1 ?? 0, m(r.report).rank1 ?? 0, "high"],
      ["Holdings nobody asked for", m(ref).unlisted ?? 0, m(r.report).unlisted ?? 0, "low"],
      ["Unfilled places", ref.unfilled_slots, r.report.unfilled_slots, "low"],
    ].map(([label, theirs, mine, good, f = fmt]) => {
      const better = good === "low" ? mine < theirs : mine > theirs;
      const worse = good === "low" ? mine > theirs : mine < theirs;
      return h("tr", {}, h("td", {}, label), h("td", { class: "num" }, f(theirs)), h("td", { class: `num ${better ? "better" : worse ? "worse" : ""}` }, f(mine)));
    });
    const c = r.comparison;
    body.append(h("h2", {}, "Against the department's own allocation"),
      h("div", { class: "grid two" },
        table(["", { label: "Department (by hand)", num: 1 }, { label: "This run", num: 1 }], lines, { class: "cmp" }),
        h("div", { class: "card", style: "margin:0" }, h("h3", {}, "Agreement"),
          h("p", { class: "muted small" }, "How much of the department's choice this run reproduces. Differences aren't errors: the department weighed things the forms don't record, and the run enforces rules that a by-hand allocation rarely manages."),
          h("div", { class: "kpis", style: "margin:0" },
            kpi("Assignments", pct(c.recall)), kpi("Lecturers", pct(c.lecture_recall)), kpi("Identical sections", pct(c.exact_sections))))));
  }

  body.append(h("h2", {}, "Short of volunteers"), shortfallList(r.shortfalls || [], true));
  if (r.warnings.length) body.append(h("h2", {}, "Warnings"), h("div", { class: "card" }, h("ul", { class: "notes" }, r.warnings.map((w) => h("li", {}, w)))));
  const breakdown = Object.entries(r.objective_breakdown).map(([k, v]) => h("tr", {}, h("td", {}, k.replaceAll("_", " ")), h("td", { class: "num" }, fmt(v, 1))));
  body.append(h("div", { style: "margin-top:20px" }, fold("Solver details", h("div", { class: "grid two" },
    table(["Objective term", { label: "Points", num: 1 }], breakdown),
    table(["Model", { label: "", num: 1 }], Object.entries(r.model_stats).map(([k, v]) => h("tr", {}, h("td", {}, k.replaceAll("_", " ")), h("td", { class: "num" }, v))))))));
}

function runAllocation(run, problem, idx, body) {
  const alloc = run.result.allocation;
  const staff = new Map();
  for (const a of alloc.assignments) {
    const k = `${a.course_code}|${a.target}`;
    if (!staff.has(k)) staff.set(k, []);
    staff.get(k).push(a);
  }
  const refStaff = new Map();
  for (const a of problem.reference?.assignments || []) {
    const k = `${a.course_code}|${a.target}`;
    if (!refStaff.has(k)) refStaff.set(k, []);
    refStaff.get(k).push(a.person_id);
  }
  const hasRef = refStaff.size > 0;
  const missing = new Map(alloc.unfilled.map((u) => [`${u.course_code}|${u.section_id}`, u.missing]));
  const isLocked = (pid, code, target) => problem.locks.some((l) => l.person_id === pid && l.course_code === code && l.target === target && l.mode === "require");

  const addLock = async (pid, code, target, mode) => {
    const locks = problem.locks.filter((l) => !(l.person_id === pid && l.course_code === code && l.target === target));
    locks.push({ person_id: pid, course_code: code, target, mode, note: `from run ${run.id}` });
    await saveLocks(problem, locks);
    toast(`${mode === "require" ? "Pinned" : "Ruled out"}: ${idx.people.get(pid)?.name} on ${code} ${target}. Start a new run to apply.`);
    render();
  };

  const chip = (a, course, section) => {
    const cls = classify(idx, a.person_id, course, section.kind);
    return h("span", { class: `chip ${cls} ${isLocked(a.person_id, course.code, section.id) ? "locked" : ""}`, title: `${MATCH_LABEL[cls]} · ${fmt(a.load)} units` },
      h("span", { class: "dot" }), idx.people.get(a.person_id)?.name || a.person_id,
      h("button", { class: "mini", title: "Pin: keep this in the next run", onclick: () => addLock(a.person_id, course.code, section.id, "require") }, "pin"),
      h("button", { class: "mini", title: "Rule out: not this person here", onclick: () => addLock(a.person_id, course.code, section.id, "forbid") }, "✕"));
  };

  const search = h("input", { type: "search", placeholder: "Search course or faculty" });
  const cat = h("select", {}, h("option", { value: "" }, "All courses"), h("option", { value: "CDC" }, "CDCs"), h("option", { value: "ELECTIVE" }, "Electives"));
  const diffOnly = h("input", { type: "checkbox" });
  const holder = h("div");
  const draw = () => {
    const q = search.value.toLowerCase();
    const cards = [];
    for (const c of problem.courses) {
      if (!c.sections.length) continue;
      if (cat.value && c.category !== cat.value) continue;
      const names = c.sections.flatMap((s) => (staff.get(`${c.code}|${s.id}`) || []).map((a) => idx.people.get(a.person_id)?.name || ""));
      if (q && !(c.code + " " + c.title + " " + names.join(" ")).toLowerCase().includes(q)) continue;
      const rows = c.sections.map((s) => {
        const mine = staff.get(`${c.code}|${s.id}`) || [];
        const theirs = refStaff.get(`${c.code}|${s.id}`) || [];
        const same = mine.length === theirs.length && mine.every((a) => theirs.includes(a.person_id));
        if (diffOnly.checked && hasRef && same) return null;
        const gap = missing.get(`${c.code}|${s.id}`);
        return h("tr", {},
          h("td", { class: "sec" }, s.id),
          h("td", { class: "muted small" }, KIND[s.kind]),
          h("td", {}, s.faculty_max === 0
            ? h("span", { class: "chip none", title: s.external_staff.join(", ") }, "no faculty needed")
            : [mine.map((a) => chip(a, c, s)), gap ? h("span", { class: "chip missing" }, `${gap} faculty missing`) : null]),
          hasRef ? h("td", { class: "small" }, theirs.length ? theirs.map((pid) => h("span", { class: `chip ${mine.some((a) => a.person_id === pid) ? "" : "ref-miss"}` }, idx.people.get(pid)?.name || pid)) : h("span", { class: "faint" }, "—")) : null);
      }).filter(Boolean);
      if (!rows.length) continue;
      cards.push(h("div", { class: "course-card" },
        h("header", {}, h("span", { class: "code" }, c.code), h("span", { class: "title" }, c.title), catBadge(c), c.service ? h("span", { class: "badge" }, "service") : null),
        h("table", {}, h("thead", {}, h("tr", {}, h("th", { style: "width:70px" }, "Section"), h("th", { style: "width:90px" }, "Kind"), h("th", {}, "Faculty"), hasRef ? h("th", { style: "width:32%" }, "Department chose") : null)),
          h("tbody", {}, rows))));
    }
    holder.replaceChildren(...(cards.length ? cards : [h("div", { class: "card empty" }, "No sections match.")]));
  };
  search.oninput = draw; cat.onchange = draw; diffOnly.onchange = draw;
  body.append(
    h("div", { class: "row", style: "margin-bottom:10px" }, search, cat,
      hasRef ? h("label", { class: "switch" }, diffOnly, "Only where the department chose differently") : null),
    legend(),
    h("p", { class: "muted small" }, "\"pin\" keeps someone on a section in the next run, ✕ rules them out. In the department column, red marks someone this run did not choose."),
    holder);
  draw();
}

function runFaculty(run, problem, idx, body) {
  const people = run.result.report.people;
  const refRows = new Map((run.result.comparison?.reference_report?.people || []).map((p) => [p.person_id, p]));
  const held = new Map();
  for (const a of run.result.allocation.assignments) {
    if (!held.has(a.person_id)) held.set(a.person_id, []);
    held.get(a.person_id).push(a);
  }
  const sort = h("select", {}, h("option", { value: "name" }, "Sort by name"), h("option", { value: "over" }, "Sort by over target"), h("option", { value: "load" }, "Sort by load"));
  const search = h("input", { type: "search", placeholder: "Search faculty" });
  const holder = h("div");
  const draw = () => {
    const q = search.value.toLowerCase();
    const list = people.filter((p) => p.name.toLowerCase().includes(q));
    if (sort.value === "load") list.sort((a, b) => b.load - a.load);
    else if (sort.value === "over") list.sort((a, b) => (b.load - b.target) - (a.load - a.target));
    else list.sort((a, b) => a.name.localeCompare(b.name));
    const top = Math.max(...people.map((p) => Math.max(p.load, p.target)), 1) * 1.08;
    const rows = list.map((p) => {
      const ref = refRows.get(p.person_id);
      const over = p.load - p.target;
      const sections = (held.get(p.person_id) || []).map((a) => {
        const course = idx.courses.get(a.course_code);
        const s = course ? sectionOf(course, a.target) : null;
        const cls = course && s ? classify(idx, p.person_id, course, s.kind) : "";
        return h("span", { class: `chip ${cls}`, title: MATCH_LABEL[cls] || "" }, h("span", { class: "dot" }), `${a.course_code} ${a.target}`);
      });
      return h("tr", {},
        h("td", {}, h("b", {}, p.name), p.cdcs.length ? h("div", { class: "nomatch small" }, `two CDCs: ${p.cdcs.join(", ")}`) : null),
        h("td", { class: "num" }, `${fmt(p.teaching_percent)}%`, p.percent_assumed ? h("div", { class: "faint small" }, "blank") : null),
        h("td", { class: "num" }, fmt(p.target)),
        h("td", { class: "num" }, h("b", {}, fmt(p.load)), over > 0.001 ? h("div", { class: "nomatch small" }, `+${fmt(over)}`) : null),
        h("td", {}, h("div", { class: "bar", title: `target ${fmt(p.target)} · load ${fmt(p.load)}` },
          h("div", { class: "band", style: `left:${(p.band_min / top) * 100}%;width:${((p.target - p.band_min) / top) * 100}%` }),
          h("div", { class: `fill ${over > 1.001 ? "over" : over > 0.001 ? "slight" : p.status === "under" ? "under" : ""}`, style: `width:calc(${(p.load / top) * 100}% - 4px)` }))),
        h("td", { class: "small" }, sections),
        refRows.size ? h("td", { class: "num muted" }, ref ? fmt(ref.load) : "—") : null);
    });
    holder.replaceChildren(table(["Faculty", { label: "Teaching %", num: 1 }, { label: "Target", num: 1 }, { label: "Load", num: 1 }, "Load against target", "Sections",
      ...(refRows.size ? [{ label: "Dept. load", num: 1 }] : [])], rows));
  };
  sort.onchange = draw; search.oninput = draw;
  body.append(h("div", { class: "row", style: "margin-bottom:10px" }, search, sort,
    h("span", { class: "muted small" }, "The green band runs up to each person's target; the bar is their load: gold up to a unit over, red beyond.")), legend(), holder);
  draw();
}

function runCompare(run, problem, idx, body) {
  const c = run.result.comparison;
  const onlyDiff = h("input", { type: "checkbox", checked: true });
  const holder = h("div");
  const draw = () => {
    const rows = c.sections.filter((s) => !onlyDiff.checked || !s.match).map((s) => h("tr", {},
      h("td", { class: "mono" }, `${s.course_code} ${s.target}`),
      h("td", {}, s.solver.join(", ") || h("span", { class: "faint" }, "—")),
      h("td", {}, s.reference.join(", ") || h("span", { class: "faint" }, "—")),
      h("td", {}, s.match ? h("span", { class: "match" }, "same") : h("span", { class: "nomatch" }, "differs"))));
    holder.replaceChildren(table(["Section", "This run", "Department", ""], rows, { empty: "No differences." }));
  };
  onlyDiff.onchange = draw;
  body.append(
    h("p", { class: "muted" }, "Section by section, where this run and the department's hand allocation disagree."),
    h("div", { class: "row", style: "margin-bottom:10px" }, h("label", { class: "switch" }, onlyDiff, "Only differences")), holder);
  draw();
}

// ---- boot -------------------------------------------------------------------------------------

api("/api/health").then((x) => {
  document.getElementById("health").textContent = `Connected · v${x.version}`;
  document.getElementById("health-dot").className = "status-dot ok";
}).catch(() => {
  document.getElementById("health").textContent = "Server unreachable";
  document.getElementById("health-dot").className = "status-dot bad";
});
render();

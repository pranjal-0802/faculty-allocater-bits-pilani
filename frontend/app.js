/* Faculty Allocator frontend: a dependency-free single-page app.
 *
 * Routes (hash based so the backend only has to serve static files):
 *   #/                     problems and workbook import
 *   #/p/<id>/<tab>         one problem: overview, people, courses, preferences, locks, runs
 *   #/r/<id>/<tab>         one run: summary, sections, people, compare
 */
"use strict";

const $app = document.getElementById("app");
const $crumbs = document.getElementById("crumbs");
let pollTimer = null;

// ---- helpers -------------------------------------------------------------------

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
    else if (k === "html") el.innerHTML = v;
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
  toast._t = setTimeout(() => { t.hidden = true; }, error ? 6000 : 2500);
}

const fmt = (n, d = 2) => (n === null || n === undefined ? "—" : Number(n).toFixed(d).replace(/\.?0+$/, ""));
const pct = (n) => (n === null || n === undefined ? "—" : `${Math.round(n * 100)}%`);
const when = (iso) => (iso ? new Date(iso).toLocaleString() : "—");

function setCrumbs(...items) {
  $crumbs.replaceChildren(...items.flatMap((it, i) => [i ? h("span", {}, "/") : null, it]));
}

function tabs(base, active, names) {
  return h("div", { class: "tabs" },
    names.map(([key, label]) => h("a", { href: `${base}/${key}`, class: key === active ? "active" : "" }, label)));
}

function table(headers, rows, opts = {}) {
  return h("div", { class: "table-wrap" },
    h("table", {},
      h("thead", {}, h("tr", {}, headers.map((x) => h("th", { class: x.num ? "num" : "" }, x.label ?? x)))),
      h("tbody", {}, rows.length ? rows : h("tr", {}, h("td", { colspan: headers.length, class: "empty" }, opts.empty || "Nothing here.")))));
}

function stat(label, value, sub) {
  return h("div", { class: "stat" }, h("div", { class: "v" }, value, sub ? h("small", {}, " ", sub) : null), h("div", { class: "k" }, label));
}

function download(href, label) {
  return h("a", { class: "button-link", href, download: "" }, label);
}

// ---- domain helpers (mirror backend/allocator/evaluation.py) --------------------

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

const MATCH_LABEL = {
  rank1: "1st choice", rank2: "2nd choice", rank3: "3rd choice", "rank4+": "lower choice",
  cross_kind: "asked for another part", service: "service duty", unlisted: "not asked for",
};

function legend() {
  return h("div", { class: "legend" },
    Object.entries(MATCH_LABEL).filter(([k]) => k !== "rank4+").map(([k, v]) => h("span", { class: `chip ${k}` }, h("span", { class: "dot" }), v)),
    h("span", { class: "chip scholar" }, "dashed = scholar"));
}

function sectionKind(course, target) {
  const s = course.sections.find((x) => x.id === target);
  return s ? s.kind : null;
}

// ---- router ----------------------------------------------------------------------

async function render() {
  clearInterval(pollTimer);
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  $app.replaceChildren(h("div", { class: "empty" }, "Loading…"));
  try {
    if (parts[0] === "p" && parts[1]) await viewProblem(parts[1], parts[2] || "overview");
    else if (parts[0] === "r" && parts[1]) await viewRun(parts[1], parts[2] || "summary");
    else await viewHome();
  } catch (err) {
    $app.replaceChildren(h("div", { class: "card" }, h("h1", {}, "Something went wrong"), h("p", { class: "nomatch" }, err.message)));
  }
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", render);

// ---- home --------------------------------------------------------------------------

async function viewHome() {
  setCrumbs();
  const [problems, departments] = await Promise.all([api("/api/problems"), api("/api/departments")]);

  const file = h("input", { type: "file", accept: ".xlsx" });
  const dept = h("select", {}, departments.length
    ? departments.map((d) => h("option", { value: d.code }, `${d.code} — ${d.name}`))
    : h("option", { value: "ME" }, "ME"));
  const name = h("input", { placeholder: "e.g. ME Sem II 2025-26" });
  const semester = h("input", { placeholder: "e.g. 2025-26 Sem II" });
  const defaults = h("input", { type: "checkbox" });
  const format = h("select", {},
    h("option", { value: "auto" }, "Detect automatically"),
    h("option", { value: "template" }, "Upload template"),
    h("option", { value: "legacy" }, "Legacy department workbook"));
  const issuesBox = h("div");
  const go = h("button", { class: "primary" }, "Import");
  go.onclick = async () => {
    if (!file.files[0]) return toast("Choose an .xlsx file first.", true);
    go.disabled = true; go.textContent = "Importing…";
    issuesBox.replaceChildren();
    try {
      const q = new URLSearchParams({ department: dept.value, name: name.value, semester: semester.value, format: format.value, default_staffing: defaults.checked });
      const form = new FormData();
      form.append("file", file.files[0]);
      const res = await api(`/api/problems/import?${q}`, { method: "POST", body: form });
      toast(`Imported (${res.format}) with ${res.notes.length} note(s).`);
      location.hash = `#/p/${encodeURIComponent(res.id)}/overview`;
    } catch (err) {
      go.disabled = false; go.textContent = "Import";
      if (err.detail && err.detail.issues) {
        issuesBox.replaceChildren(issueTable(err.detail.message, err.detail.issues));
      } else toast(err.message, true);
    }
  };
  const templateLink = h("a", { class: "button-link", href: `/api/template.xlsx?department=${encodeURIComponent(dept.value)}` }, "Download blank template");
  dept.onchange = () => { templateLink.href = `/api/template.xlsx?department=${encodeURIComponent(dept.value)}`; };

  const rows = problems.map((p) => h("tr", {},
    h("td", {}, h("a", { href: `#/p/${encodeURIComponent(p.id)}/overview` }, p.name), h("div", { class: "muted small mono" }, p.id)),
    h("td", {}, p.department), h("td", {}, p.semester || "—"),
    h("td", { class: "num" }, p.faculty), h("td", { class: "num" }, p.scholars),
    h("td", { class: "num" }, p.courses), h("td", { class: "num" }, p.sections), h("td", { class: "num" }, p.preferences),
    h("td", {}, p.has_reference ? "yes" : "—"), h("td", { class: "small muted" }, when(p.updated_at)),
    h("td", {}, h("button", { class: "ghost danger", onclick: async () => {
      if (!confirm(`Delete ${p.name} and all its runs?`)) return;
      await api(`/api/problems/${encodeURIComponent(p.id)}`, { method: "DELETE" });
      render();
    } }, "Delete"))));

  $app.replaceChildren(
    h("h1", {}, "Allocation problems"),
    h("p", { class: "muted" }, "Each problem is one department's staffing round: who can teach, what needs staff, and what everyone asked for."),
    table(["Name", "Dept", "Semester", { label: "Faculty", num: 1 }, { label: "Scholars", num: 1 }, { label: "Courses", num: 1 },
      { label: "Sections", num: 1 }, { label: "Prefs", num: 1 }, "Hand allocation", "Updated", ""], rows,
    { empty: "No problems yet. Import a department workbook below." }),
    h("div", { class: "card", style: "margin-top:16px" },
      h("div", { class: "row between" },
        h("h2", { style: "margin:0" }, "Import"),
        h("div", { class: "row" }, templateLink, h("a", { class: "button-link", href: "/api/schema", target: "_blank" }, "Schema (JSON)"))),
      h("p", { class: "muted small" },
        "Departments fill in the upload template: Courses, Sections, Faculty (with the usual preference columns) and Research Scholars. ",
        "It is checked row by row and rejected whole if anything is wrong, with every problem listed. ",
        "Legacy department workbooks (like ME Sem II 2025-26) are still read best-effort for replaying past semesters; their hand allocation is kept for comparison only."),
      h("div", { class: "row" },
        h("label", { class: "field" }, "Workbook", file),
        h("label", { class: "field" }, "Format", format),
        h("label", { class: "field" }, "Department", dept),
        h("label", { class: "field" }, "Name", name),
        h("label", { class: "field" }, "Semester", semester)),
      h("label", { class: "row small muted", style: "margin:10px 0" }, defaults, "Legacy workbooks only: use default section staffing instead of the workbook's"),
      go, issuesBox));
}

function issueTable(message, issues) {
  const errors = issues.filter((i) => i.severity === "error");
  const rows = issues
    .slice()
    .sort((a, b) => (a.severity === b.severity ? 0 : a.severity === "error" ? -1 : 1))
    .map((i) => h("tr", {},
      h("td", {}, h("span", { class: `badge ${i.severity === "error" ? "over" : "under"}` }, i.severity)),
      h("td", {}, i.sheet), h("td", { class: "num" }, i.row ?? ""), h("td", {}, i.column ?? ""), h("td", {}, i.message)));
  return h("div", { style: "margin-top:14px" },
    h("p", { class: "nomatch" }, message, ` Fix the ${errors.length} error(s) below and upload again.`),
    table(["", "Sheet", { label: "Row", num: 1 }, "Column", "Problem"], rows));
}

// ---- problem -------------------------------------------------------------------------

async function viewProblem(id, tab) {
  const problem = await api(`/api/problems/${encodeURIComponent(id)}`);
  const base = `#/p/${encodeURIComponent(id)}`;
  setCrumbs(h("a", { href: "#/" }, "Problems"), h("a", { href: `${base}/overview` }, problem.name));
  const idx = indexProblem(problem);
  const body = h("div");
  $app.replaceChildren(
    h("div", { class: "row between" },
      h("div", {}, h("h1", {}, problem.name), h("div", { class: "muted" }, [problem.department, problem.semester].filter(Boolean).join(" · "))),
      h("div", { class: "row" },
        h("a", { class: "button-link", href: `/api/template.xlsx?problem_id=${encodeURIComponent(problem.id)}`, title: "This problem as a filled-in upload template: a starting point for next semester" }, "Download as template"),
        h("a", { class: "button-link", href: `${base}/runs` }, "Runs →"))),
    tabs(base, tab, [["overview", "Overview"], ["people", "People"], ["courses", "Courses"], ["preferences", "Preferences"], ["locks", `Locks (${problem.locks.length})`], ["runs", "Runs"]]),
    body);
  const views = { overview: problemOverview, people: problemPeople, courses: problemCourses, preferences: problemPreferences, locks: problemLocks, runs: problemRuns };
  await (views[tab] || problemOverview)(problem, idx, body);
}

async function problemOverview(problem, idx, body) {
  const faculty = problem.people.filter((p) => p.kind === "faculty");
  const scholars = problem.people.filter((p) => p.kind === "scholar");
  const sections = problem.courses.flatMap((c) => c.sections);
  const slots = (kind) => sections.reduce((n, s) => n + (kind === "f" ? s.faculty_min : s.scholar_min), 0);
  const validation = await api(`/api/problems/${encodeURIComponent(problem.id)}/validation`);

  body.append(
    h("div", { class: "grid stats" },
      stat("Faculty", faculty.length), stat("Scholars", scholars.length), stat("Courses", problem.courses.length),
      stat("Sections", sections.length), stat("Faculty slots", slots("f")), stat("Scholar slots", slots("s")),
      stat("Preferences", problem.preferences.length), stat("External load", fmt(faculty.reduce((n, p) => n + p.external_load, 0)), "units")));

  if (problem.reference && problem.reference.assignments.length) {
    const rep = await api(`/api/problems/${encodeURIComponent(problem.id)}/reference-report`, { method: "POST" });
    body.append(h("h2", {}, "The department's own allocation"), reportCards(rep));
  }
  body.append(
    h("h2", {}, "Import notes"),
    problem.notes.length ? h("div", { class: "card" }, h("ul", { class: "notes" }, problem.notes.map((n) => h("li", {}, n)))) : h("p", { class: "muted" }, "None."),
    h("h2", {}, "Validation"),
    validation.warnings.length ? h("div", { class: "card" }, h("ul", { class: "notes" }, validation.warnings.map((n) => h("li", {}, n)))) : h("p", { class: "muted" }, "No problems found."));
}

function reportCards(rep, label = "") {
  const f = rep.load.faculty || {}, s = rep.load.scholar || {};
  const fm = rep.preference_matches.faculty || {}, sm = rep.preference_matches.scholar || {};
  return h("div", { class: "grid stats" },
    stat(`${label}Faculty load`, fmt(f.mean), `± ${fmt(f.stdev)}`),
    stat(`${label}Faculty range`, `${fmt(f.min)}–${fmt(f.max)}`),
    stat(`${label}Faculty under / over band`, `${f.under ?? 0} / ${f.over ?? 0}`),
    stat(`${label}First lecture choice`, pct(rep.first_lecture_choice_rate)),
    stat(`${label}Faculty 1st-choice holdings`, fm.rank1 ?? 0, `of ${Object.values(fm).reduce((a, b) => a + b, 0)}`),
    stat(`${label}Faculty unlisted`, fm.unlisted ?? 0),
    stat(`${label}Scholar hours`, fmt(s.mean), `± ${fmt(s.stdev)}`),
    stat(`${label}Scholar 1st-choice holdings`, sm.rank1 ?? 0, `of ${Object.values(sm).reduce((a, b) => a + b, 0)}`),
    stat(`${label}Unfilled slots`, rep.unfilled_slots),
    stat(`${label}Courses without IC`, rep.courses_without_ic.length));
}

function problemPeople(problem, idx, body) {
  const kind = h("select", {}, h("option", { value: "" }, "Everyone"), h("option", { value: "faculty" }, "Faculty"), h("option", { value: "scholar" }, "Scholars"));
  const search = h("input", { placeholder: "Search names" });
  const holder = h("div");
  const draw = () => {
    const q = search.value.toLowerCase();
    const rows = problem.people
      .filter((p) => (!kind.value || p.kind === kind.value) && p.name.toLowerCase().includes(q))
      .map((p) => {
        const lo = h("input", { type: "number", step: "0.5", value: p.load_min ?? "", placeholder: "default" });
        const hi = h("input", { type: "number", step: "0.5", value: p.load_max ?? "", placeholder: "default" });
        const ext = h("input", { type: "number", step: "0.5", value: p.external_load });
        const save = h("button", { onclick: async () => {
          const clear = [];
          const patch = { external_load: Number(ext.value || 0) };
          if (lo.value === "") clear.push("load_min"); else patch.load_min = Number(lo.value);
          if (hi.value === "") clear.push("load_max"); else patch.load_max = Number(hi.value);
          await api(`/api/problems/${encodeURIComponent(problem.id)}/people/${encodeURIComponent(p.id)}`, { method: "PATCH", json: { ...patch, clear } });
          Object.assign(p, patch); if (clear.includes("load_min")) p.load_min = null; if (clear.includes("load_max")) p.load_max = null;
          toast(`Saved ${p.name}`);
        } }, "Save");
        const n = problem.preferences.filter((x) => x.person_id === p.id).length;
        const sup = p.supervisor && idx.people.get(p.supervisor);
        return h("tr", {},
          h("td", {}, p.name, p.category ? h("div", { class: "muted small" }, p.category) : null),
          h("td", {}, p.kind), h("td", { class: "num" }, p.teaching_percent ?? "—"),
          h("td", { class: "small" }, sup ? sup.name : "—"),
          h("td", { class: "num" }, n),
          h("td", {}, h("div", { class: "row" }, lo, "–", hi)),
          h("td", {}, ext, p.external_notes.length ? h("div", { class: "muted small" }, p.external_notes.join("; ")) : null),
          h("td", {}, save));
      });
    holder.replaceChildren(table(["Name", "Type", { label: "Teaching %", num: 1 }, "Supervisor", { label: "Prefs", num: 1 }, "Load band override", "External load", ""], rows));
  };
  kind.onchange = draw; search.oninput = draw;
  body.append(
    h("p", { class: "muted small" }, "Leave the band empty to use the run's default (faculty in units, scholars in contact hours). External load is teaching outside this round, such as WILP."),
    h("div", { class: "row", style: "margin-bottom:10px" }, kind, search), holder);
  draw();
}

function problemCourses(problem, idx, body) {
  const demand = (code, kind) => problem.preferences.filter((p) => p.course_code === code && (p.kind === kind || p.kind === null)).length;
  const rows = problem.courses.map((c) => h("tr", {},
    h("td", {}, h("b", {}, c.code), h("div", { class: "muted small" }, c.title)),
    h("td", {}, c.category, c.service ? h("div", { class: "small muted" }, "service") : null),
    h("td", { class: "num" }, fmt(c.lecture_units)), h("td", { class: "num" }, fmt(c.lab_units)), h("td", { class: "num" }, c.strength || "—"),
    h("td", {}, c.sections.length ? c.sections.map((s) => h("span", { class: "chip", title: s.external_staff.join(", ") },
      `${s.id} · ${s.faculty_min === s.faculty_max ? s.faculty_max : `${s.faculty_min}-${s.faculty_max}`}F${s.scholar_max ? ` + ${s.scholar_min === s.scholar_max ? s.scholar_max : `${s.scholar_min}-${s.scholar_max}`}S` : ""}`))
      : h("span", { class: "muted small" }, c.category === "PROJECT" ? `IC only · ${fmt(c.ic_units)} unit` : "no sections")),
    h("td", { class: "num" }, demand(c.code, "L")), h("td", { class: "num" }, demand(c.code, "T")), h("td", { class: "num" }, demand(c.code, "P"))));
  body.append(
    h("p", { class: "muted small" }, "Staffing per section: F = faculty, S = scholars. The last three columns count how many people asked for the lecture, tutorial and lab."),
    table(["Course", "Category", { label: "L units", num: 1 }, { label: "Lab units", num: 1 }, { label: "Strength", num: 1 }, "Sections",
      { label: "Want L", num: 1 }, { label: "Want T", num: 1 }, { label: "Want P", num: 1 }], rows));
}

function problemPreferences(problem, idx, body) {
  const search = h("input", { placeholder: "Filter courses or people" });
  const holder = h("div");
  const draw = () => {
    const q = search.value.toLowerCase();
    const rows = [];
    for (const c of problem.courses) {
      const prefs = problem.preferences.filter((p) => p.course_code === c.code);
      const cells = ["L", "T", "P"].map((k) => h("td", {}, prefs.filter((p) => p.kind === k || p.kind === null).sort((a, b) => a.rank - b.rank).map((p) => {
        const person = idx.people.get(p.person_id);
        return h("span", { class: `chip rank${Math.min(p.rank, 3)} ${person?.kind === "scholar" ? "scholar" : ""}`, title: `${p.source}${p.taught_consecutively ? " · taught consecutively" : ""}` },
          h("span", { class: "dot" }), `${person ? person.name : p.person_id} (${p.rank})${p.taught_consecutively ? " ↻" : ""}`);
      })));
      const text = (c.code + c.title + prefs.map((p) => idx.people.get(p.person_id)?.name || "").join(" ")).toLowerCase();
      if (q && !text.includes(q)) continue;
      rows.push(h("tr", {}, h("td", {}, h("b", {}, c.code), h("div", { class: "muted small" }, c.title)), cells));
    }
    holder.replaceChildren(table(["Course", "Lecture", "Tutorial", "Practical"], rows));
  };
  search.oninput = draw;
  body.append(h("p", { class: "muted small" }, "Who asked for what, with their rank. ↻ marks a course the person has taught in consecutive semesters. Dashed chips are scholars."),
    h("div", { class: "row", style: "margin-bottom:10px" }, search), holder);
  draw();
}

async function saveLocks(problem, locks) {
  problem.locks = await api(`/api/problems/${encodeURIComponent(problem.id)}/locks`, { method: "PUT", json: locks });
}

function problemLocks(problem, idx, body) {
  const person = h("select", {}, problem.people.map((p) => h("option", { value: p.id }, `${p.name} (${p.kind})`)));
  const course = h("select", {}, problem.courses.map((c) => h("option", { value: c.code }, c.code)));
  const target = h("select");
  const mode = h("select", {}, h("option", { value: "require" }, "must take"), h("option", { value: "forbid" }, "must not take"));
  const fillTargets = () => {
    const c = idx.courses.get(course.value);
    target.replaceChildren(h("option", { value: "IC" }, "IC"), ...(c ? c.sections.map((s) => h("option", { value: s.id }, s.id)) : []));
  };
  course.onchange = fillTargets; fillTargets();
  const add = h("button", { class: "primary", onclick: async () => {
    await saveLocks(problem, [...problem.locks, { person_id: person.value, course_code: course.value, target: target.value, mode: mode.value, note: "" }]);
    toast("Lock added"); render();
  } }, "Add lock");

  const rows = problem.locks.map((l, i) => h("tr", {},
    h("td", {}, idx.people.get(l.person_id)?.name || l.person_id), h("td", {}, l.mode === "require" ? "must take" : "must not take"),
    h("td", {}, `${l.course_code} ${l.target}`), h("td", { class: "small muted" }, l.note || ""),
    h("td", {}, h("button", { class: "ghost danger", onclick: async () => {
      await saveLocks(problem, problem.locks.filter((_, j) => j !== i)); render();
    } }, "Remove"))));
  body.append(
    h("p", { class: "muted small" }, "Locks are decisions the solver must respect: a person must (or must not) take a section or the IC role. Add them here or from a run's Sections tab, then start a new run."),
    h("div", { class: "card" }, h("div", { class: "row" }, person, mode, course, target, add)),
    table(["Person", "Rule", "Section", "Note", ""], rows, { empty: "No locks." }),
    problem.locks.length ? h("div", { style: "margin-top:10px" }, h("button", { class: "danger", onclick: async () => {
      if (!confirm("Remove every lock?")) return; await saveLocks(problem, []); render();
    } }, "Clear all locks")) : null);
}

// ---- settings form -------------------------------------------------------------------

const SETTINGS_META = [
  ["Preferences", [
    ["faculty_rank_weights", "Faculty reward by rank", "Points for giving a faculty member a course/kind they ranked 1st, 2nd, 3rd."],
    ["scholar_rank_weights", "Scholar reward by rank", "Same, for scholars."],
    ["cross_kind_weight", "Asked for another part", "Listed the course, but for a different kind of section."],
    ["unlisted_penalty", "Not asked for (penalty)", "Cost of giving someone a course they did not list."],
    ["unlisted_service_penalty", "Unlisted service duty (penalty)", "Cost of an unrequested service-course duty; normally 0."],
    ["consecutive_weight", "Taught consecutively", "Positive favours continuity, negative favours rotation. Pending the admin's answer."],
    ["supervisor_bonus", "Scholar with supervisor", "Reward for a scholar sharing a section with their supervisor."],
  ]],
  ["Load", [
    ["faculty_load", "Faculty target band (units)", ""],
    ["scholar_load", "Scholar target band (hours)", ""],
    ["underload_penalty", "Underload penalty / unit", ""],
    ["overload_penalty", "Overload penalty / unit", ""],
    ["max_overload_penalty", "Worst-overload penalty / unit", "Spreads overload so no single person carries it all."],
    ["faculty_load_cap", "Faculty absolute cap", ""],
    ["scholar_load_cap", "Scholar absolute cap", ""],
    ["scholar_tutorial_hours", "Scholar hours per tutorial", ""],
    ["scholar_hours_per_lab_unit", "Scholar hours per lab unit", ""],
    ["teaching_percent_bands", "Band per Teaching Allocation %", "JSON such as {\"70\": {\"min\": 6, \"max\": 8}}. Empty until the admin confirms what the percentage means."],
  ]],
  ["Structure", [
    ["tutorial_follows_lecture", "Tutorials go to the course's lecturers", ""],
    ["tutorial_follows_lecture_penalty", "…penalty when broken", ""],
    ["lecturer_takes_tutorial", "Each lecturer takes a tutorial", ""],
    ["lecturer_takes_tutorial_penalty", "…penalty when broken", ""],
    ["ic_must_lecture", "IC must lecture the course", ""],
    ["faculty_max_courses", "Distinct courses per faculty (soft cap)", ""],
    ["extra_course_penalty", "…penalty per extra course", ""],
    ["eligibility", "Who may be placed where", "shortlist: volunteers, opened to everyone when a section is short. penalised: anyone, at a cost. strict: volunteers only."],
    ["shortlist_spare", "Shortlist spare volunteers", "A section with fewer than slots + this many volunteers is opened to everyone."],
  ]],
  ["Search", [
    ["time_limit_seconds", "Time limit (s)", ""],
    ["num_workers", "Solver threads", "8 or more lets CP-SAT prove optimality; fewer leaves a gap."],
    ["greedy_hint", "Start from a greedy allocation", ""],
    ["symmetry_breaking", "Order identical sections", "Off by default; CP-SAT finds this symmetry itself."],
    ["random_seed", "Random seed", ""],
  ]],
];
const ENUMS = { tutorial_follows_lecture: ["off", "soft", "hard"], lecturer_takes_tutorial: ["off", "soft", "hard"], eligibility: ["shortlist", "penalised", "strict"] };

function settingsForm(defaults) {
  const getters = {};
  const groups = SETTINGS_META.map(([title, fields]) => h("details", { open: title !== "Search" && title !== "Structure" ? true : null },
    h("summary", {}, title),
    h("div", { class: "settings-grid", style: "margin:10px 0 16px" }, fields.map(([key, label, help]) => {
      const value = defaults[key];
      let input;
      if (ENUMS[key]) {
        input = h("select", {}, ENUMS[key].map((o) => h("option", { value: o, selected: o === value ? true : null }, o)));
        getters[key] = () => input.value;
      } else if (typeof value === "boolean") {
        input = h("input", { type: "checkbox", checked: value ? true : null });
        getters[key] = () => input.checked;
      } else if (key === "teaching_percent_bands") {
        input = h("textarea", { rows: 2 }, JSON.stringify(value));
        getters[key] = () => JSON.parse(input.value || "{}");
      } else if (value && typeof value === "object" && "min" in value) {
        const lo = h("input", { type: "number", step: "0.5", value: value.min });
        const hi = h("input", { type: "number", step: "0.5", value: value.max });
        input = h("div", { class: "row" }, lo, "–", hi);
        getters[key] = () => ({ min: Number(lo.value), max: Number(hi.value) });
      } else if (value && typeof value === "object") {
        const inputs = Object.entries(value).map(([rank, w]) => [rank, h("input", { type: "number", value: w, title: `rank ${rank}` })]);
        input = h("div", { class: "row" }, inputs.map(([rank, el]) => h("span", { class: "small muted" }, `${rank}:`, el)));
        getters[key] = () => Object.fromEntries(inputs.map(([rank, el]) => [rank, Number(el.value)]));
      } else {
        input = h("input", { type: "number", value, step: "any" });
        getters[key] = () => Number(input.value);
      }
      return h("label", { class: "field" }, label, input, help ? h("span", { class: "help" }, help) : null);
    }))));
  return { el: h("div", {}, groups), read: () => ({ ...defaults, ...Object.fromEntries(Object.entries(getters).map(([k, g]) => [k, g()])) }) };
}

async function problemRuns(problem, idx, body) {
  const [runs, defaults] = await Promise.all([api(`/api/problems/${encodeURIComponent(problem.id)}/runs`), api("/api/settings/defaults")]);
  const form = settingsForm(defaults);
  const label = h("input", { placeholder: "Label (optional)" });
  const start = h("button", { class: "primary" }, "Start run");
  start.onclick = async () => {
    let settings;
    try { settings = form.read(); } catch (err) { return toast(`Settings: ${err.message}`, true); }
    start.disabled = true;
    try {
      const run = await api(`/api/problems/${encodeURIComponent(problem.id)}/runs`, { method: "POST", json: { settings, label: label.value } });
      location.hash = `#/r/${run.id}/summary`;
    } catch (err) { toast(err.message, true); start.disabled = false; }
  };
  const rows = runs.map((r) => h("tr", {},
    h("td", {}, h("a", { href: `#/r/${r.id}/summary` }, r.label || r.id)), h("td", {}, h("span", { class: `badge ${r.status}` }, r.status)),
    h("td", { class: "small" }, when(r.created_at)), h("td", { class: "small" }, when(r.finished_at)),
    h("td", { class: "small muted" }, r.error || ""),
    h("td", {}, h("button", { class: "ghost danger", onclick: async () => { await api(`/api/runs/${r.id}`, { method: "DELETE" }); render(); } }, "Delete"))));
  body.append(
    table(["Run", "Status", "Started", "Finished", "", ""], rows, { empty: "No runs yet." }),
    h("div", { class: "card", style: "margin-top:16px" },
      h("h2", { style: "margin-top:0" }, "New run"),
      h("p", { class: "muted small" }, "Weights are in points: preference rewards per course held, load penalties per unit outside a person's band. Rules still awaiting the BITS admin are switches here."),
      form.el, h("div", { class: "row" }, label, start)));
}

// ---- run -------------------------------------------------------------------------------

async function viewRun(id, tab) {
  const run = await api(`/api/runs/${encodeURIComponent(id)}`);
  const problem = await api(`/api/problems/${encodeURIComponent(run.problem_id)}`);
  const base = `#/r/${encodeURIComponent(id)}`;
  setCrumbs(h("a", { href: "#/" }, "Problems"), h("a", { href: `#/p/${encodeURIComponent(problem.id)}/runs` }, problem.name), h("span", {}, run.label || run.id));

  if (run.status === "pending" || run.status === "running") {
    $app.replaceChildren(h("div", { class: "card" }, h("h1", {}, "Solving…"),
      h("p", { class: "muted" }, `Started ${when(run.created_at)}. Time limit ${run.settings.time_limit_seconds}s. This page refreshes on its own.`)));
    pollTimer = setInterval(async () => {
      const s = await api(`/api/runs/${encodeURIComponent(id)}/status`);
      if (s.status !== "pending" && s.status !== "running") { clearInterval(pollTimer); render(); }
    }, 1500);
    return;
  }
  const result = run.result;
  if (!result || !result.allocation) {
    $app.replaceChildren(h("div", { class: "card" }, h("h1", {}, "Run failed"),
      h("p", { class: "nomatch" }, run.error || result?.error || "No allocation was produced."),
      (result?.warnings || []).length ? h("ul", { class: "notes" }, result.warnings.map((w) => h("li", {}, w))) : null,
      h("a", { href: `#/p/${encodeURIComponent(problem.id)}/locks` }, "Check locks")));
    return;
  }
  const idx = indexProblem(problem);
  const body = h("div");
  $app.replaceChildren(
    h("div", { class: "row between" },
      h("div", {}, h("h1", {}, run.label || `Run ${run.id}`),
        h("div", { class: "muted" }, h("span", { class: `badge ${result.status}` }, result.status), ` objective ${fmt(result.objective, 1)} · bound ${fmt(result.best_bound, 1)} · ${fmt(result.solve_seconds, 1)}s`)),
      h("div", { class: "row" },
        download(`/api/runs/${run.id}/export/assignments.csv`, "Assignments CSV"),
        download(`/api/runs/${run.id}/export/people.csv`, "Loads CSV"),
        download(`/api/runs/${run.id}/export/timetable.json`, "Sections JSON"))),
    tabs(base, tab, [["summary", "Summary"], ["sections", "Sections"], ["people", "People"], ...(result.comparison ? [["compare", "vs department"]] : [])]),
    body);
  const views = { summary: runSummary, sections: runSections, people: runPeople, compare: runCompare };
  (views[tab] || runSummary)(run, problem, idx, body);
}

function runSummary(run, problem, idx, body) {
  const r = run.result;
  body.append(h("h2", {}, "This run"), reportCards(r.report));
  if (r.comparison) {
    const c = r.comparison;
    body.append(h("h2", {}, "The department's own allocation"), reportCards(c.reference_report),
      h("h2", {}, "Agreement with the department"),
      h("div", { class: "grid stats" },
        stat("Faculty assignments reproduced", pct(c.recall.faculty)), stat("Scholar assignments reproduced", pct(c.recall.scholar)),
        stat("Lecturers reproduced", pct(c.lecture_recall)), stat("Same IC", pct(c.ic_agreement)),
        stat("Identical faculty staffing", pct(c.exact_sections.faculty))));
  }
  const breakdown = Object.entries(r.objective_breakdown).map(([k, v]) => h("tr", {}, h("td", {}, k.replaceAll("_", " ")), h("td", { class: "num" }, fmt(v, 1))));
  body.append(
    h("div", { class: "grid cols-2", style: "margin-top:16px" },
      h("div", {}, h("h3", {}, "Objective breakdown"), table(["Term", { label: "Points", num: 1 }], breakdown)),
      h("div", {}, h("h3", {}, "Model"), table(["", { label: "", num: 1 }], Object.entries(r.model_stats).map(([k, v]) => h("tr", {}, h("td", {}, k.replaceAll("_", " ")), h("td", { class: "num" }, v)))))),
    r.warnings.length ? h("div", {}, h("h3", {}, "Warnings"), h("div", { class: "card" }, h("ul", { class: "notes" }, r.warnings.map((w) => h("li", {}, w))))) : null);
}

function runSections(run, problem, idx, body) {
  const alloc = run.result.allocation;
  const byKey = new Map();
  for (const a of alloc.assignments) {
    const k = `${a.course_code}|${a.target}`;
    if (!byKey.has(k)) byKey.set(k, []);
    byKey.get(k).push(a);
  }
  const refKey = new Map();
  for (const a of problem.reference?.assignments || []) {
    const k = `${a.course_code}|${a.target}`;
    if (!refKey.has(k)) refKey.set(k, []);
    refKey.get(k).push(a.person_id);
  }
  const missing = new Map(alloc.unfilled.map((u) => [`${u.course_code}|${u.section_id}|${u.kind}`, u.missing]));
  const isLocked = (pid, code, target) => problem.locks.some((l) => l.person_id === pid && l.course_code === code && l.target === target && l.mode === "require");

  const addLock = async (pid, code, target, mode) => {
    const locks = problem.locks.filter((l) => !(l.person_id === pid && l.course_code === code && l.target === target));
    locks.push({ person_id: pid, course_code: code, target, mode, note: `from run ${run.id}` });
    await saveLocks(problem, locks);
    toast(`${mode === "require" ? "Pinned" : "Forbidden"}: ${idx.people.get(pid)?.name} on ${code} ${target}. Start a new run to apply.`);
    render();
  };

  const chip = (pid, course, target) => {
    const person = idx.people.get(pid);
    const kind = sectionKind(course, target);
    const cls = kind ? classify(idx, pid, course, kind) : "";
    const a = (byKey.get(`${course.code}|${target}`) || []).find((x) => x.person_id === pid);
    return h("span", { class: `chip ${cls} ${person?.kind === "scholar" ? "scholar" : ""} ${isLocked(pid, course.code, target) ? "locked" : ""}`, title: `${MATCH_LABEL[cls] || "IC"}${a ? ` · load ${fmt(a.load)}` : ""}` },
      kind ? h("span", { class: "dot" }) : null, person ? person.name : pid,
      h("button", { title: "Pin: must keep this", onclick: () => addLock(pid, course.code, target, "require") }, "📌"),
      h("button", { title: "Forbid: must not have this", onclick: () => addLock(pid, course.code, target, "forbid") }, "✕"));
  };

  const filter = h("input", { placeholder: "Filter courses" });
  const holder = h("div");
  const hasRef = !!(problem.reference && problem.reference.assignments.length);
  const draw = () => {
    const q = filter.value.toLowerCase();
    const rows = [];
    for (const c of problem.courses) {
      if (q && !(c.code + c.title).toLowerCase().includes(q)) continue;
      rows.push(h("tr", { class: "group" }, h("td", { colspan: hasRef ? 4 : 3 }, `${c.code} — ${c.title}`, h("span", { class: "muted small" }, ` · ${c.category}${c.service ? " · service" : ""}`))));
      const targets = [...(c.ic_required ? [{ id: "IC", kind: "IC" }] : []), ...c.sections];
      for (const s of targets) {
        const staff = (byKey.get(`${c.code}|${s.id}`) || []).map((a) => a.person_id);
        const gaps = ["faculty", "scholar"].map((k) => missing.get(`${c.code}|${s.id}|${k}`)).map((n, i) => n ? h("span", { class: "chip missing" }, `${n} ${i ? "scholar" : "faculty"} missing`) : null);
        const ref = refKey.get(`${c.code}|${s.id}`) || [];
        rows.push(h("tr", {},
          h("td", { class: "mono" }, s.id),
          h("td", { class: "small muted" }, s.kind === "IC" ? "" : `${s.faculty_max}F${s.scholar_max ? `+${s.scholar_max}S` : ""}${s.external_staff?.length ? ` +${s.external_staff.length} ext` : ""}`),
          h("td", {}, staff.map((pid) => chip(pid, c, s.id)), gaps),
          hasRef ? h("td", { class: "small" }, ref.map((pid) => h("span", { class: `chip ${idx.people.get(pid)?.kind === "scholar" ? "scholar" : ""} ${staff.includes(pid) ? "" : "nomatch"}` }, idx.people.get(pid)?.name || pid))) : null));
      }
    }
    holder.replaceChildren(table(["Section", "Needs", "This run", ...(hasRef ? ["Department"] : [])], rows));
  };
  filter.oninput = draw;
  body.append(legend(), h("p", { class: "muted small" }, "📌 pins a person to a section for the next run, ✕ forbids it. Department names in red are people the department chose that this run did not."),
    h("div", { class: "row", style: "margin-bottom:10px" }, filter), holder);
  draw();
}

function runPeople(run, problem, idx, body) {
  const people = run.result.report.people;
  const refRows = new Map((run.result.comparison?.reference_report?.people || []).map((p) => [p.person_id, p]));
  const kind = h("select", {}, h("option", { value: "faculty" }, "Faculty"), h("option", { value: "scholar" }, "Scholars"));
  const sort = h("select", {}, h("option", { value: "name" }, "Sort by name"), h("option", { value: "load" }, "Sort by load"), h("option", { value: "status" }, "Sort by status"));
  const holder = h("div");
  const draw = () => {
    const list = people.filter((p) => p.kind === kind.value).slice();
    if (sort.value === "load") list.sort((a, b) => b.load - a.load);
    else if (sort.value === "status") list.sort((a, b) => a.status.localeCompare(b.status));
    else list.sort((a, b) => a.name.localeCompare(b.name));
    const top = Math.max(...list.map((p) => Math.max(p.load, p.band_max)), 1) * 1.1;
    const rows = list.map((p) => {
      const ref = refRows.get(p.person_id);
      const m = p.matches || {};
      return h("tr", {},
        h("td", {}, p.name),
        h("td", { class: "num" }, fmt(p.load)),
        h("td", {}, h("div", { class: "bar", title: `band ${fmt(p.band_min)}–${fmt(p.band_max)}` },
          h("div", { class: "band", style: `left:${(p.band_min / top) * 100}%;width:${((p.band_max - p.band_min) / top) * 100}%` }),
          h("div", { class: `fill ${p.status}`, style: `width:${(p.load / top) * 100}%` }))),
        h("td", {}, h("span", { class: `badge ${p.status}` }, p.status)),
        h("td", { class: "small" }, Object.entries(m).map(([k, v]) => h("span", { class: `chip ${k}` }, h("span", { class: "dot" }), `${v}`))),
        h("td", { class: "small" }, p.roles.join(", ")),
        refRows.size ? h("td", { class: "num small muted" }, ref ? fmt(ref.load) : "—") : null);
    });
    holder.replaceChildren(table(["Name", { label: "Load", num: 1 }, "vs band", "", "Matches", "Roles", ...(refRows.size ? [{ label: "Dept. load", num: 1 }] : [])], rows));
  };
  kind.onchange = draw; sort.onchange = draw;
  body.append(legend(), h("div", { class: "row", style: "margin:10px 0" }, kind, sort), holder);
  draw();
}

function runCompare(run, problem, idx, body) {
  const c = run.result.comparison;
  const onlyDiff = h("input", { type: "checkbox", checked: true });
  const kind = h("select", {}, h("option", { value: "faculty" }, "Faculty"), h("option", { value: "scholar" }, "Scholars"));
  const holder = h("div");
  const draw = () => {
    const rows = c.sections.filter((s) => s.kind === kind.value && (!onlyDiff.checked || !s.match)).map((s) => h("tr", {},
      h("td", { class: "mono" }, `${s.course_code} ${s.target}`),
      h("td", {}, s.solver.join(", ") || "—"), h("td", {}, s.reference.join(", ") || "—"),
      h("td", {}, s.match ? h("span", { class: "match" }, "same") : h("span", { class: "nomatch" }, "differs"))));
    holder.replaceChildren(table(["Section", "This run", "Department", ""], rows, { empty: "No differences." }));
  };
  onlyDiff.onchange = draw; kind.onchange = draw;
  body.append(
    h("p", { class: "muted small" }, "Where this run and the department's hand allocation disagree. Differences are not errors: the department weighed things the forms do not record, and the run weighs load balance the department may not have checked."),
    h("div", { class: "row", style: "margin-bottom:10px" }, kind, h("label", { class: "row small" }, onlyDiff, "only differences")), holder);
  draw();
}

// ---- boot --------------------------------------------------------------------------------

api("/api/health").then((x) => { document.getElementById("health").textContent = `v${x.version}`; }).catch(() => {
  document.getElementById("health").textContent = "API unreachable";
});
render();

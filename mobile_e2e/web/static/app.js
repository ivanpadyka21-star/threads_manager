"use strict";

const t = (k) => window.I18N.t(k);
const tf = (k, params) => { let s = t(k); for (const [p, v] of Object.entries(params || {})) s = s.replace("{" + p + "}", v); return s; };
const PINK = "#ff3d7f", TEAL = "#2dd4bf", BLUE = "#6f8bff", VIOLET = "#b24bff";
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

async function api(path, opts) {
  const res = await fetch(path, opts);
  let body = null;
  try { body = await res.json(); } catch { /* no body */ }
  if (!res.ok) throw new Error((body && body.error) || res.status);
  return body;
}
const jpost = (path, data) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data || {}) });

function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v);
  }
  for (const kid of kids) if (kid != null) n.append(kid);
  return n;
}
function badge(status) { return el("span", { class: "badge " + status, text: t("status." + status) }); }
function fmtDate(s) { return s ? s.replace("T", " ").slice(0, 16) : "—"; }
function headRow(keys) { const tr = el("tr"); keys.forEach(k => tr.append(el("th", { text: t(k) }))); return el("thead", {}, tr); }
function td(text) { return el("td", { text: String(text) }); }
function tdLimit(used, limit) {
  const pct = limit ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  const cell = el("td", {});
  cell.append(el("div", { class: "pill", text: `${used}/${limit}` }));
  const bar = el("div", { class: "limit-bar" }); const fill = el("i"); fill.style.width = pct + "%";
  bar.append(fill); cell.append(bar); return cell;
}

// --- tabs ------------------------------------------------------------------
const TAB_KEYS = { dashboard: "nav.dashboard", accounts: "nav.accounts", tasks: "nav.tasks", runs: "nav.runs", stats: "nav.stats", analytics: "nav.analytics", audit: "nav.audit", settings: "nav.settings" };
let currentTab = "dashboard";
function renderTab(name) {
  if (name === "dashboard") loadDashboard();
  if (name === "accounts") loadAccounts();
  if (name === "tasks") { loadAccountOptions(); loadTasks(); }
  if (name === "stats") loadStats();
  if (name === "analytics") loadAnalytics(true);
  if (name === "audit") loadAudit();
  if (name === "settings") loadThreadsStatus();
}
function showTab(name) {
  currentTab = name;
  $$(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach(s => s.classList.toggle("active", s.id === "tab-" + name));
  $("#page-title").textContent = t(TAB_KEYS[name] || name);
  $("#page-desc").textContent = t("desc." + name);
  renderTab(name);
}
$$(".nav-item").forEach(b => b.addEventListener("click", () => showTab(b.dataset.tab)));

// --- language --------------------------------------------------------------
window.I18N.apply();
window.I18N.onChange = () => {
  $("#page-title").textContent = t(TAB_KEYS[currentTab]);
  $("#page-desc").textContent = t("desc." + currentTab);
  renderTab(currentTab); loadNotifications();
};
$$("#lang-toggle button").forEach(b => b.addEventListener("click", () => {
  window.I18N.setLang(b.dataset.lang);
  $$("#lang-toggle button").forEach(x => x.classList.toggle("active", x.dataset.lang === b.dataset.lang));
}));
$$("#lang-toggle button").forEach(x => x.classList.toggle("active", x.dataset.lang === window.I18N.lang));

// --- live health -----------------------------------------------------------
const liveEl = $("#live");
async function pingHealth() {
  try { const b = await api("/api/health"); const ok = b && b.status === "ok";
    liveEl.className = "live " + (ok ? "online" : "offline"); liveEl.textContent = ok ? "● live" : "● offline";
  } catch { liveEl.className = "live offline"; liveEl.textContent = "● offline"; }
}
pingHealth(); setInterval(pingHealth, 3000);

// --- notifications ---------------------------------------------------------
const NOTIF_ICON = { reminder: "🔔", deadline: "⏰", problem: "⚠" };
async function loadNotifications() {
  let data;
  try { data = await api("/api/notifications"); } catch { return; }
  const badge = $("#bell-badge");
  if (data.count > 0) { badge.hidden = false; badge.textContent = data.count > 99 ? "99+" : data.count; }
  else { badge.hidden = true; }
  const list = $("#notif-list"); list.innerHTML = "";
  if (!data.items.length) { list.innerHTML = `<div class="notif-empty">${t("notif.empty")}</div>`; return; }
  data.items.slice(0, 30).forEach(it => {
    const row = el("div", { class: "notif-item " + it.level });
    row.append(el("span", { class: "ni-ic", text: NOTIF_ICON[it.type] || "•" }));
    const body = el("div", { class: "ni-body" });
    body.append(el("div", { class: "ni-title", text: `${t("notif." + it.type)}: ${it.title || ""}` }));
    const meta = [it.account, (it.when || "").replace("T", " ").slice(0, 16)].filter(Boolean).join(" · ");
    body.append(el("div", { class: "ni-meta", text: meta }));
    row.append(body);
    list.append(row);
  });
}
$("#bell").addEventListener("click", (e) => {
  e.stopPropagation();
  const p = $("#notif-panel"); p.hidden = !p.hidden; if (!p.hidden) loadNotifications();
});
document.addEventListener("click", (e) => {
  const p = $("#notif-panel");
  if (!p.hidden && !p.contains(e.target) && e.target.id !== "bell") p.hidden = true;
});
loadNotifications(); setInterval(loadNotifications, 5000);

// --- dashboard -------------------------------------------------------------
async function loadDashboard() {
  const [stats, analytics, trend, reminders] = await Promise.all([
    api("/api/stats"), api("/api/analytics?hours=24"),
    api("/api/analytics/trend?days=30"), api("/api/reminders"),
  ]);
  const tk = stats.tasks || {};
  const totalTasks = Object.values(tk).reduce((a, b) => a + b, 0);
  const pendingPct = totalTasks ? Math.round(100 * (tk.pending || 0) / totalTasks) : 0;
  const accs = analytics.accounts || [];
  const used = accs.reduce((a, r) => a + (r.used_today || 0), 0);
  const limit = accs.reduce((a, r) => a + (r.daily_limit || 0), 0);
  const loadPct = limit ? Math.round(100 * used / limit) : 0;
  const score = analytics.project ? analytics.project.score : null;
  const problems = analytics.project ? analytics.project.problems : 0;

  const wrap = $("#summary-cards"); wrap.innerHTML = "";
  wrap.append(metricCard({ label: t("m.accounts"), value: stats.accounts, sub: tf("m.accounts.sub", { n: (analytics.active || []).length }), color: TEAL, tip: t("m.accounts.tip") }));
  wrap.append(metricCard({ label: t("m.pending"), value: tk.pending || 0, sub: tf("m.pending.sub", { n: pendingPct }), color: PINK, accent: true, tip: t("m.pending.tip") }));
  wrap.append(metricCard({ label: t("m.eff"), value: score == null ? "—" : score, unit: score == null ? "" : "%", sub: tf("m.eff.sub", { n: problems }), color: scoreColor(score), tip: t("m.eff.tip") }));
  wrap.append(metricCard({ label: t("m.load"), value: loadPct, unit: "%", sub: tf("m.load.sub", { a: used, b: limit }), color: BLUE, tip: t("m.load.tip") }));

  $("#dash-activity").innerHTML = "";
  $("#dash-activity").append(areaChart((analytics.activity || []).map(d => d.total), { color: PINK, h: 150 }));
  $("#dash-trend").innerHTML = "";
  $("#dash-trend").append(areaChart(trend.map(d => d.score), { color: TEAL, h: 150, maxY: 100 }));

  renderReminders(reminders);
  renderAccountsOverview(accs);
}
function renderReminders(rows) {
  const wrap = $("#reminders-list");
  if (!rows.length) { wrap.innerHTML = `<div class="empty">${t("empty.reminders")}</div>`; return; }
  const now = new Date();
  const table = el("table");
  table.append(headRow(["col.reminder", "col.task", "col.account", "col.stage"]));
  rows.forEach(r => {
    const due = r.reminder && new Date(r.reminder) <= now;
    const timeCell = el("td", {}, el("span", { text: "🔔 " + fmtDate(r.reminder) }));
    if (due) timeCell.append(el("span", { class: "due-tag", text: " " + t("reminder.due") }));
    const row = el("tr", { class: due ? "due" : "" }, timeCell,
      td(r.title || "—"), td(r.account_name || "—"), el("td", {}, badge(r.status)));
    table.append(row);
  });
  wrap.innerHTML = ""; wrap.append(table);
}
function renderAccountsOverview(rows) {
  const wrap = $("#dashboard-accounts");
  if (!rows.length) { wrap.innerHTML = `<div class="empty">${t("empty.accountsShort")}</div>`; return; }
  const table = el("table");
  table.append(headRow(["col.account", "col.handle", "col.today", "col.score", "col.state"]));
  rows.forEach(r => {
    const score = el("td", {}); const chip = el("span", { class: "score-chip", text: r.score == null ? "—" : r.score + "%" });
    chip.style.color = scoreColor(r.score); score.append(chip);
    table.append(el("tr", {}, td(r.name), td(r.handle || "—"), tdLimit(r.used_today, r.daily_limit),
      score, el("td", {}, el("span", { class: "state " + r.state, text: t("state." + r.state) }))));
  });
  wrap.innerHTML = ""; wrap.append(table);
}

// --- accounts --------------------------------------------------------------
async function loadAccounts() {
  const rows = await api("/api/accounts");
  const wrap = $("#accounts-table");
  if (!rows.length) { wrap.innerHTML = `<div class="empty">${t("empty.accounts")}</div>`; return; }
  const table = el("table");
  table.append(headRow(["col.name", "col.handle", "col.proxy", "col.limit", "col.today", "col.tone", "col.actions"]));
  rows.forEach(r => {
    const del = el("button", { class: "mini danger", text: t("btn.delete"), onclick: async () => {
      if (!confirm(`${t("confirm.delete")} "${r.name}"?`)) return;
      await api("/api/accounts/" + r.id, { method: "DELETE" }); loadAccounts();
    }});
    table.append(el("tr", {}, td(r.name), td(r.handle || "—"), td(r.proxy_string ? "•••" : "—"),
      td(String(r.daily_limit)), tdLimit(r.used_today, r.daily_limit), td(r.tone || "—"),
      el("td", { class: "actions" }, del)));
  });
  wrap.innerHTML = ""; wrap.append(table);
}
$("#account-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target).entries());
  const msg = $("#account-msg");
  try { await jpost("/api/accounts", data); e.target.reset(); msg.className = "hint"; msg.textContent = t("msg.added"); loadAccounts(); }
  catch (err) { msg.className = "hint error"; msg.textContent = String(err); }
});

// --- tasks -----------------------------------------------------------------
async function loadAccountOptions() {
  const rows = await api("/api/accounts");
  ["#task-account", "#batch-account"].forEach(selId => {
    const sel = $(selId); if (!sel) return; sel.innerHTML = "";
    if (!rows.length) { sel.append(el("option", { value: "", text: "—" })); return; }
    rows.forEach(r => sel.append(el("option", { value: String(r.id), text: r.name + (r.handle ? ` (${r.handle})` : "") })));
  });
}

// --- batch of scheduled posts ----------------------------------------------
$("#batch-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const briefs = (fd.get("briefs") || "").split("\n").map(s => s.trim()).filter(Boolean).slice(0, 10);
  if (!briefs.length) { alert("Add at least one topic line"); return; }
  const data = {
    account_id: fd.get("account_id") ? Number(fd.get("account_id")) : null,
    briefs, language: fd.get("language"), style: fd.get("style"),
    interval_minutes: Number(fd.get("interval_minutes")) || 60,
    start_at: fd.get("start_at") || null,
  };
  const btn = e.target.querySelector("button[type=submit]");
  btn.disabled = true; const label = btn.textContent; btn.textContent = "…";
  try {
    const res = await jpost("/api/batches", data);
    renderBatchPreview(res.batch_id, res.tasks);
  } catch (err) { alert("Error: " + err); }
  btn.disabled = false; btn.textContent = label;
});

function renderBatchPreview(batchId, tasks) {
  const wrap = $("#batch-preview"); wrap.innerHTML = "";
  wrap.append(el("div", { class: "det-title", text: t("batch.preview") + " (" + tasks.length + ")" }));
  tasks.forEach(tk => {
    const when = (tk.scheduled_for || "").replace("T", " ");
    const card = el("div", { class: "batch-card" });
    card.append(el("div", { class: "bc-when", text: "🕒 " + when }));
    card.append(el("div", { class: "bc-text", text: tk.result || "(no draft)" }));
    wrap.append(card);
  });
  const approve = el("button", { class: "ok", text: t("batch.approve"), onclick: async () => {
    const r = await jpost(`/api/batches/${batchId}/approve`);
    wrap.innerHTML = `<div class="hint">OK — ${tf("batch.scheduled", { n: r.scheduled })}</div>`;
    loadTasks();
  }});
  const bar = el("div", { class: "form-actions", style: "margin-top:12px" }); bar.append(approve);
  wrap.append(bar);
}
async function loadTasks() {
  const status = $("#task-filter-status").value;
  const [rows, accounts] = await Promise.all([
    api("/api/tasks" + (status ? "?status=" + status : "")), api("/api/accounts"),
  ]);
  const wrap = $("#tasks-table");
  if (!rows.length) { wrap.innerHTML = `<div class="empty">${t("empty.tasks")}</div>`; return; }
  const nameById = Object.fromEntries(accounts.map(a => [a.id, a.name]));
  const table = el("table");
  table.append(headRow(["col.title", "col.account", "col.type", "col.deadline", "col.reminder", "col.status", "col.actions"]));
  rows.forEach(tk => {
    const actions = el("td", { class: "actions" });
    if (tk.status === "pending") {
      actions.append(el("button", { class: "mini ok", text: t("btn.approve"), onclick: () => approveTask(tk.id) }));
      actions.append(el("button", { class: "mini danger", text: t("btn.reject"), onclick: () => setTaskStatus(tk.id, "rejected") }));
    } else if (tk.status === "approved") {
      actions.append(el("button", { class: "mini grad", text: t("btn.execute"), onclick: () => executeTask(tk.id) }));
      actions.append(el("button", { class: "mini ok", text: t("btn.publish"), onclick: () => publishTask(tk.id) }));
      actions.append(el("button", { class: "mini", text: t("btn.done"), onclick: () => setTaskStatus(tk.id, "done") }));
    }
    actions.append(el("button", { class: "mini danger", text: "✕", title: t("btn.delete"), onclick: async () => {
      await api("/api/tasks/" + tk.id, { method: "DELETE" }); loadTasks(); if (currentTab === "dashboard") loadDashboard();
    }}));
    const rem = tk.reminder ? el("span", { class: "bell", text: "🔔 " + fmtDate(tk.reminder) }) : el("span", { class: "pill", text: "—" });
    table.append(el("tr", {}, td(tk.title || "—"), td(nameById[tk.account_id] || "—"),
      td(t("task.kind." + ({post:"post",reply:"reply",ai_generate:"ai",custom:"custom"}[tk.kind] || "custom"))),
      td(fmtDate(tk.deadline)), el("td", {}, rem), el("td", {}, badge(tk.status)), actions));
  });
  wrap.innerHTML = ""; wrap.append(table);
}
async function approveTask(id) {
  try { await jpost(`/api/tasks/${id}/approve`); loadTasks(); if (currentTab === "dashboard") loadDashboard(); }
  catch (err) { alert(err); }
}
async function setTaskStatus(id, status) { await jpost(`/api/tasks/${id}/status`, { status }); loadTasks(); }
async function publishTask(id) {
  try {
    const res = await jpost(`/api/tasks/${id}/publish`);
    alert(t("th.published") + " (id " + res.published_id + ")");
  } catch (err) { alert("⚠ " + err); }
  loadTasks(); if (currentTab === "dashboard") loadDashboard();
}
async function loadThreadsStatus() {
  const wrap = $("#threads-status"); if (!wrap) return;
  let s; try { s = await api("/api/threads/status"); } catch { return; }
  wrap.innerHTML = "";
  const head = el("div", { class: "th-badge " + (s.configured ? "ok" : "no"), text: s.configured ? t("th.configured") : t("th.not") });
  wrap.append(head);
  const ul = el("ul", { class: "th-list" });
  if (s.configured) { ul.append(el("li", { class: "ok", text: "✓ " + t("th.ready") })); }
  else {
    if (s.missing_env && s.missing_env.length) ul.append(el("li", { text: "• " + t("th.missing_env") + " " + s.missing_env.join(", ") }));
    if (!s.ssl_ok) ul.append(el("li", { text: "• " + t("th.ssl") }));
    if (!s.has_credentials) ul.append(el("li", { text: "• " + t("th.creds") }));
    ul.append(el("li", { class: "steps", text: t("th.steps") }));
  }
  wrap.append(ul);
}
async function executeTask(id) {
  try {
    const res = await jpost(`/api/tasks/${id}/execute`);
    alert(t("an.draft") + ":\n\n" + (res.result || ""));
  } catch (err) {
    // On AI failure the backend auto-flags the task as a problem.
    alert("⚠ " + err);
  }
  loadTasks(); if (currentTab === "dashboard") loadDashboard();
}
$("#task-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target).entries());
  if (data.account_id) data.account_id = Number(data.account_id);
  await jpost("/api/tasks", data); e.target.reset(); loadTasks();
});
$("#task-filter-status").addEventListener("change", loadTasks);

// --- statistics ------------------------------------------------------------
async function loadStats() {
  const rows = await api("/api/stats/accounts");
  const wrap = $("#stats-table");
  if (!rows.length) { wrap.innerHTML = `<div class="empty">${t("empty.data")}</div>`; return; }
  const table = el("table");
  table.append(headRow(["col.account", "col.total", "col.pending", "col.approved", "col.done", "col.rejected", "col.today"]));
  rows.forEach(r => table.append(el("tr", {}, td(r.name), td(String(r.total_tasks)),
    td(String(r.counts.pending || 0)), td(String(r.counts.approved || 0)), td(String(r.counts.done || 0)),
    td(String(r.counts.rejected || 0)), tdLimit(r.used_today, r.daily_limit))));
  wrap.innerHTML = ""; wrap.append(table);
}

// --- audit (grouped by day) ------------------------------------------------
function dayLabel(dateStr) {
  const d = new Date(dateStr);
  const today = new Date(); const yst = new Date(); yst.setDate(today.getDate() - 1);
  const same = (a, b) => a.toDateString() === b.toDateString();
  if (same(d, today)) return t("day.today");
  if (same(d, yst)) return t("day.yesterday");
  return d.toLocaleDateString(window.I18N.lang, { day: "numeric", month: "long", year: "numeric" });
}
async function loadAudit() {
  const rows = await api("/api/audit");
  const wrap = $("#audit-list");
  if (!rows.length) { wrap.innerHTML = `<div class="empty">${t("empty.audit")}</div>`; return; }
  wrap.innerHTML = "";
  let lastDay = null;
  rows.forEach(r => {
    const label = dayLabel(r.ts);
    if (label !== lastDay) { wrap.append(el("div", { class: "day-head", text: label })); lastDay = label; }
    wrap.append(el("div", { class: "audit-row" },
      el("span", { class: "audit-time", text: (r.ts || "").slice(11, 16) }),
      el("code", { class: "audit-action", text: r.action }),
      el("span", { class: "audit-detail", text: r.detail || "" }),
      el("span", { class: "audit-acc", text: r.account_id != null ? "#" + r.account_id : "" })));
  });
}

// --- charts (inline SVG, dependency-free) ----------------------------------
function svg(tag, attrs) { const n = document.createElementNS("http://www.w3.org/2000/svg", tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); return n; }
function barChart(values, w = 220, h = 42) {
  const max = Math.max(1, ...values);
  const n = values.length; const gap = 2; const bw = (w - gap * (n - 1)) / n;
  const s = svg("svg", { viewBox: `0 0 ${w} ${h}`, width: w, height: h, class: "chart" });
  values.forEach((v, i) => {
    const bh = Math.max(v > 0 ? 3 : 1, Math.round((v / max) * (h - 4)));
    const x = i * (bw + gap); const y = h - bh;
    s.append(svg("rect", { x: x.toFixed(1), y: y.toFixed(1), width: bw.toFixed(1), height: bh, rx: 1.5, class: v > 0 ? "bar on" : "bar off" }));
  });
  return s;
}
function scoreColor(score) { if (score == null) return "var(--dim)"; if (score >= 85) return "var(--ok)"; if (score >= 60) return "var(--warn)"; return "var(--err)"; }
function smoothPath(pts) {
  if (pts.length < 2) return "";
  let d = `M${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i], p1 = pts[i], p2 = pts[i + 1], p3 = pts[i + 2] || p2;
    const c1x = p1[0] + (p2[0] - p0[0]) / 6, c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6, c2y = p2[1] - (p3[1] - p1[1]) / 6;
    d += ` C${c1x.toFixed(1)} ${c1y.toFixed(1)}, ${c2x.toFixed(1)} ${c2y.toFixed(1)}, ${p2[0].toFixed(1)} ${p2[1].toFixed(1)}`;
  }
  return d;
}
function areaChart(values, { w = 440, h = 150, color = PINK, maxY = null } = {}) {
  const n = values.length;
  const nz = values.filter(v => v != null);
  const mx = maxY != null ? maxY : Math.max(1, ...nz);
  const pts = [];
  values.forEach((v, i) => { if (v != null) pts.push([n === 1 ? w / 2 : (i / (n - 1)) * (w - 10) + 5, h - 12 - (v / mx) * (h - 24)]); });
  const s = svg("svg", { viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: "none", width: "100%", height: h, class: "area-chart" });
  [0, 0.5, 1].forEach(g => { const y = (h - 12 - g * (h - 24)).toFixed(1); s.append(svg("line", { x1: 0, x2: w, y1: y, y2: y, class: "grid" })); });
  if (pts.length < 2) return s;
  const gid = "g" + Math.random().toString(36).slice(2, 7);
  const defs = svg("defs", {}); const lg = svg("linearGradient", { id: gid, x1: 0, y1: 0, x2: 0, y2: 1 });
  lg.append(svg("stop", { offset: "0%", "stop-color": color, "stop-opacity": "0.45" }));
  lg.append(svg("stop", { offset: "100%", "stop-color": color, "stop-opacity": "0.02" }));
  defs.append(lg); s.append(defs);
  const line = smoothPath(pts);
  s.append(svg("path", { d: line + ` L${pts[pts.length - 1][0].toFixed(1)} ${h - 12} L${pts[0][0].toFixed(1)} ${h - 12} Z`, fill: `url(#${gid})`, stroke: "none" }));
  s.append(svg("path", { d: line, fill: "none", stroke: color, "stroke-width": "2.5", "stroke-linejoin": "round" }));
  s.append(svg("circle", { cx: pts[pts.length - 1][0].toFixed(1), cy: pts[pts.length - 1][1].toFixed(1), r: 3.5, fill: color }));
  return s;
}
function metricCard({ label, value, unit, sub, color, accent, tip }) {
  const card = el("div", { class: "metric" + (accent ? " accent" : ""), title: tip || "" });
  card.append(el("div", { class: "m-k", text: label }));
  const v = el("div", { class: "m-v" });
  const num = el("span", { class: "m-num", text: String(value) });
  if (color) num.style.color = color;
  v.append(num);
  if (unit) v.append(el("span", { class: "m-unit", text: unit }));
  card.append(v);
  card.append(el("div", { class: "m-sub", text: sub || "" }));
  return card;
}
function lineChart(points, w = 900, h = 120) {
  // points: [{date, score|null}]. Draws an area+line for available scores.
  const s = svg("svg", { viewBox: `0 0 ${w} ${h}`, preserveAspectRatio: "none", width: "100%", height: h, class: "line-chart" });
  const n = points.length; if (!n) return s;
  const xs = (i) => (n === 1 ? w / 2 : (i / (n - 1)) * (w - 8) + 4);
  const ys = (v) => h - 8 - (v / 100) * (h - 16);
  // gridlines at 0/50/100
  [0, 50, 100].forEach(g => s.append(svg("line", { x1: 0, x2: w, y1: ys(g), y2: ys(g), class: "grid" })));
  const pts = points.map((p, i) => (p.score == null ? null : [xs(i), ys(p.score)])).filter(Boolean);
  if (pts.length) {
    const d = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
    const area = `M${pts[0][0].toFixed(1)} ${h - 8} ` + pts.map(p => "L" + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ") + ` L${pts[pts.length - 1][0].toFixed(1)} ${h - 8} Z`;
    s.append(svg("path", { d: area, class: "area" }));
    s.append(svg("path", { d, class: "line" }));
    pts.forEach(p => s.append(svg("circle", { cx: p[0].toFixed(1), cy: p[1].toFixed(1), r: 2.5, class: "dot" })));
  }
  return s;
}
function heatmap(cells) {
  // cells: [{date,total,errors}] -> weekday-aligned grid, intensity by activity.
  const max = Math.max(1, ...cells.map(c => c.total));
  const wrap = el("div", { class: "heat" });
  if (cells.length) {
    const first = new Date(cells[0].date);
    let lead = (first.getDay() + 6) % 7; // Mon=0
    for (let i = 0; i < lead; i++) wrap.append(el("span", { class: "hc blank" }));
  }
  cells.forEach(c => {
    let lvl = 0;
    if (c.total > 0) lvl = c.total >= max * 0.66 ? 3 : c.total >= max * 0.33 ? 2 : 1;
    const cell = el("span", { class: "hc l" + lvl + (c.errors ? " err" : ""), title: `${c.date}: ${c.total}${c.errors ? " · errors " + c.errors : ""}` });
    wrap.append(cell);
  });
  return wrap;
}

// --- analytics -------------------------------------------------------------
let analyticsHours = 24;
async function loadAnalytics(full) {
  const [a, trend] = await Promise.all([
    api("/api/analytics?hours=" + analyticsHours),
    api("/api/analytics/trend?days=30"),
  ]);
  renderAnalyticsCards(a);
  renderTrend(trend);
  renderAnalyticsAccounts(a.accounts);
  if (full) renderPicker(a.accounts);
}
function renderTrend(points) {
  const wrap = $("#analytics-trend"); wrap.innerHTML = "";
  const withData = points.filter(p => p.score != null);
  if (withData.length < 2) { wrap.innerHTML = `<div class="empty">${t("an.no_trend")}</div>`; return; }
  wrap.append(lineChart(points, 900, 130));
  const scale = el("div", { class: "trend-scale" });
  scale.append(el("span", { text: points[0].date.slice(5) }));
  scale.append(el("span", { text: points[points.length - 1].date.slice(5) }));
  wrap.append(scale);
}
const winSel = $("#analytics-window");
if (winSel) winSel.addEventListener("change", () => { analyticsHours = Number(winSel.value); loadAnalytics(false); });
function renderAnalyticsCards(a) {
  const p = a.project || {};
  const cards = [
    { k: "an.score", v: p.score == null ? "—" : p.score + "%", color: scoreColor(p.score), accent: true },
    { k: "an.problems", v: p.problems || 0 },
    { k: "an.ai_rate", v: a.ai && a.ai.success_rate != null ? a.ai.success_rate + "%" : "—" },
    { k: "an.active", v: (a.active || []).length + " / " + (a.accounts || []).length },
  ];
  const wrap = $("#analytics-cards"); wrap.innerHTML = "";
  cards.forEach(c => {
    const v = el("div", { class: "v", text: String(c.v) });
    if (c.color) v.style.color = c.color;
    wrap.append(el("div", { class: "card" + (c.accent ? " accent" : "") }, el("div", { class: "k", text: t(c.k) }), v));
  });
}
function renderAnalyticsAccounts(rows) {
  const wrap = $("#analytics-accounts");
  if (!rows.length) { wrap.innerHTML = `<div class="empty">${t("empty.data")}</div>`; return; }
  const table = el("table");
  table.append(headRow(["col.account", "col.state", "col.score", "col.problems", "col.events", "col.today", "col.activity"]));
  const tbody = el("tbody");
  rows.forEach(r => {
    const score = el("td", {}); const chip = el("span", { class: "score-chip", text: r.score == null ? "—" : r.score + "%" });
    chip.style.color = scoreColor(r.score); score.append(chip);
    const spark = el("td", {}); spark.append(barChart(r.sparkline || [], 200, 34));
    const tr = el("tr", { class: "an-row", onclick: () => toggleAccountDetail(tr, r.id) },
      td(r.name), el("td", {}, el("span", { class: "state " + r.state, text: t("state." + r.state) })),
      score, td(String(r.problems)), td(String(r.events)), tdLimit(r.used_today, r.daily_limit), spark);
    tbody.append(tr);
  });
  table.append(tbody); wrap.innerHTML = ""; wrap.append(table);
}
async function toggleAccountDetail(tr, id) {
  const next = tr.nextElementSibling;
  if (next && next.classList.contains("detail-row")) { next.remove(); return; }
  const data = await api(`/api/accounts/${id}/activity?days=30`);
  const cell = el("td", { colspan: 7, class: "detail-cell" });
  cell.append(el("div", { class: "det-title", text: t("an.calendar") }));
  cell.append(heatmap(data.activity || []));
  const errs = (data.effectiveness && data.effectiveness.recent_errors) || [];
  if (errs.length) {
    cell.append(el("div", { class: "det-title", text: t("an.recent_errors") }));
    errs.slice(0, 6).forEach(e => cell.append(el("div", { class: "det-err", text: `${(e.ts||'').slice(0,16)} · ${e.action} · ${e.detail||''}` })));
  }
  const dr = el("tr", { class: "detail-row" }, cell);
  tr.after(dr);
}
function renderPicker(rows) {
  const wrap = $("#an-picker"); wrap.innerHTML = "";
  rows.forEach(r => {
    const id = "pk_" + r.id;
    const chip = el("label", { class: "chip" }, el("input", { type: "checkbox", value: String(r.id), id }), document.createTextNode(" " + r.name));
    wrap.append(chip);
  });
}
function pickedAccounts() { return $$("#an-picker input:checked").map(i => Number(i.value)); }
async function runAnalyticsAI(useAI) {
  const out = $("#an-output"); out.textContent = useAI ? t("an.thinking") : "…";
  const body = { account_ids: pickedAccounts(), question: $("#an-question").value };
  try {
    const res = await jpost(useAI ? "/api/analytics/ai" : "/api/analytics/summary", body);
    out.textContent = useAI ? ((res.answer || res.error || "") + "\n\n— — —\n" + (res.data || "")) : (res.data || "");
  } catch (err) { out.textContent = String(err); }
}
$("#an-run-ai").addEventListener("click", () => runAnalyticsAI(true));
$("#an-run-data").addEventListener("click", () => runAnalyticsAI(false));

// Real-time refresh while on the Analytics tab.
setInterval(() => {
  if (currentTab === "analytics" && $("#analytics-auto") && $("#analytics-auto").checked) loadAnalytics(false);
}, 4000);

// --- runs (workflow) -------------------------------------------------------
let polling = null;
const logEl = $("#log"), statusEl = $("#status"), runBtn = $("#run"), resultBadge = $("#result-badge");
function setStatus(key, c) { statusEl.textContent = t(key); statusEl.className = "status " + c; }
function appendLog(line) { logEl.textContent += line + "\n"; logEl.scrollTop = logEl.scrollHeight; }
function setLog(lines) { logEl.textContent = lines.join("\n") + (lines.length ? "\n" : ""); logEl.scrollTop = logEl.scrollHeight; }
function runData() { return Object.fromEntries(new FormData($("#workflow-form")).entries()); }

$("#preview-proxy").addEventListener("click", async () => {
  const out = $("#proxy-preview"); out.className = "hint"; out.textContent = "…";
  try { const b = await jpost("/api/preview-proxy", { proxy_string: runData().proxy_string || "" }); out.textContent = b.proxy; }
  catch (err) { out.className = "hint error"; out.textContent = String(err); }
});
$("#clear-log").addEventListener("click", () => { logEl.textContent = ""; resultBadge.textContent = ""; resultBadge.className = ""; });

$("#workflow-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (polling) return;
  resultBadge.textContent = ""; resultBadge.className = "";
  runBtn.disabled = true; setStatus("status.pending", "running"); appendLog(t("run.starting"));
  let jobId;
  try { jobId = (await jpost("/api/run", runData())).job_id; }
  catch (err) { appendLog("Error: " + err); setStatus("status.idle", "idle"); runBtn.disabled = false; return; }
  polling = setInterval(async () => {
    try {
      const job = await api("/api/jobs/" + jobId);
      setLog([t("run.starting"), ...job.logs]);
      if (job.status === "done" || job.status === "error") {
        clearInterval(polling); polling = null; runBtn.disabled = false;
        if (job.status === "error") { setStatus("status.failed", "error"); resultBadge.className = "err"; resultBadge.textContent = job.error || "failed"; }
        else { const r = job.result || {};
          if (r.ok) { setStatus("status.done", "done"); resultBadge.className = "ok"; resultBadge.textContent = "OK — " + JSON.stringify(r.response); }
          else { setStatus("status.failed", "error"); resultBadge.className = "err"; resultBadge.textContent = r.error || "failed"; }
        }
      }
    } catch (err) { clearInterval(polling); polling = null; runBtn.disabled = false; setStatus("status.failed", "error"); appendLog("Polling failed: " + err); }
  }, 700);
});

// --- boot ------------------------------------------------------------------
showTab("dashboard");

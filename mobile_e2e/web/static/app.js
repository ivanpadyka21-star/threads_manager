"use strict";

const t = (k) => window.I18N.t(k);
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
const TAB_KEYS = { dashboard: "nav.dashboard", accounts: "nav.accounts", tasks: "nav.tasks", runs: "nav.runs", stats: "nav.stats", audit: "nav.audit", settings: "nav.settings" };
let currentTab = "dashboard";
function renderTab(name) {
  if (name === "dashboard") loadDashboard();
  if (name === "accounts") loadAccounts();
  if (name === "tasks") { loadAccountOptions(); loadTasks(); }
  if (name === "stats") loadStats();
  if (name === "audit") loadAudit();
}
function showTab(name) {
  currentTab = name;
  $$(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach(s => s.classList.toggle("active", s.id === "tab-" + name));
  $("#page-title").textContent = t(TAB_KEYS[name] || name);
  renderTab(name);
}
$$(".nav-item").forEach(b => b.addEventListener("click", () => showTab(b.dataset.tab)));

// --- language --------------------------------------------------------------
window.I18N.apply();
window.I18N.onChange = () => { $("#page-title").textContent = t(TAB_KEYS[currentTab]); renderTab(currentTab); };
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

// --- dashboard -------------------------------------------------------------
async function loadDashboard() {
  const [stats, accounts, reminders] = await Promise.all([
    api("/api/stats"), api("/api/stats/accounts"), api("/api/reminders"),
  ]);
  const tk = stats.tasks || {};
  const cards = [
    { k: "card.accounts", v: stats.accounts },
    { k: "card.pending", v: tk.pending || 0, accent: true },
    { k: "card.approved", v: tk.approved || 0 },
    { k: "card.done", v: tk.done || 0 },
  ];
  const wrap = $("#summary-cards"); wrap.innerHTML = "";
  cards.forEach(c => wrap.append(el("div", { class: "card" + (c.accent ? " accent" : "") },
    el("div", { class: "k", text: t(c.k) }), el("div", { class: "v", text: String(c.v) }))));
  renderReminders(reminders);
  renderAccountsOverview(accounts);
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
  table.append(headRow(["col.account", "col.handle", "col.today", "col.pending", "col.done"]));
  rows.forEach(r => table.append(el("tr", {}, td(r.name), td(r.handle || "—"),
    tdLimit(r.used_today, r.daily_limit), td(String(r.counts.pending || 0)), td(String(r.counts.done || 0)))));
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
  const sel = $("#task-account"); sel.innerHTML = "";
  if (!rows.length) { sel.append(el("option", { value: "", text: "—" })); return; }
  rows.forEach(r => sel.append(el("option", { value: String(r.id), text: r.name + (r.handle ? ` (${r.handle})` : "") })));
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

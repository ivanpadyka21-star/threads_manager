"use strict";

// --- helpers ---------------------------------------------------------------
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

async function api(path, opts) {
  const res = await fetch(path, opts);
  let body = null;
  try { body = await res.json(); } catch { /* no body */ }
  if (!res.ok) throw new Error((body && body.error) || res.status);
  return body;
}
function jpost(path, data) {
  return api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data || {}) });
}
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
function badge(status) { return el("span", { class: "badge " + status, text: status }); }
function fmtDate(s) { return s ? s.replace("T", " ").slice(0, 16) : "—"; }

// --- tab navigation --------------------------------------------------------
const TITLES = {
  dashboard: "Dashboard", accounts: "Accounts", tasks: "Tasks",
  runs: "Runs", stats: "Statistics", audit: "Audit", settings: "Settings",
};
function showTab(name) {
  $$(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach(s => s.classList.toggle("active", s.id === "tab-" + name));
  $("#page-title").textContent = TITLES[name] || name;
  if (name === "dashboard") loadDashboard();
  if (name === "accounts") loadAccounts();
  if (name === "tasks") { loadAccountOptions(); loadTasks(); }
  if (name === "stats") loadStats();
  if (name === "audit") loadAudit();
}
$$(".nav-item").forEach(b => b.addEventListener("click", () => showTab(b.dataset.tab)));

// --- live health -----------------------------------------------------------
const liveEl = $("#live");
async function pingHealth() {
  try {
    const b = await api("/api/health");
    const ok = b && b.status === "ok";
    liveEl.className = "live " + (ok ? "online" : "offline");
    liveEl.textContent = ok ? "● live" : "● offline";
  } catch { liveEl.className = "live offline"; liveEl.textContent = "● offline"; }
}
pingHealth(); setInterval(pingHealth, 3000);

// --- dashboard -------------------------------------------------------------
async function loadDashboard() {
  const [stats, accounts] = await Promise.all([api("/api/stats"), api("/api/stats/accounts")]);
  const t = stats.tasks || {};
  const cards = [
    { k: "Accounts", v: stats.accounts, accent: false },
    { k: "Pending approval", v: t.pending || 0, accent: true },
    { k: "Approved", v: t.approved || 0 },
    { k: "Done", v: t.done || 0 },
  ];
  const wrap = $("#summary-cards"); wrap.innerHTML = "";
  cards.forEach(c => wrap.append(
    el("div", { class: "card" + (c.accent ? " accent" : "") },
      el("div", { class: "k", text: c.k }), el("div", { class: "v", text: String(c.v) }))
  ));
  renderAccountsOverview(accounts);
}
function renderAccountsOverview(rows) {
  const wrap = $("#dashboard-accounts");
  if (!rows.length) { wrap.innerHTML = '<div class="empty">No accounts yet — add one in the Accounts tab.</div>'; return; }
  const table = el("table");
  table.append(headRow(["Account", "Handle", "Today", "Pending", "Done"]));
  rows.forEach(r => table.append(el("tr", {},
    td(r.name), td(r.handle || "—"), tdLimit(r.used_today, r.daily_limit),
    td(String(r.counts.pending || 0)), td(String(r.counts.done || 0)),
  )));
  wrap.innerHTML = ""; wrap.append(table);
}

// --- accounts --------------------------------------------------------------
async function loadAccounts() {
  const rows = await api("/api/accounts");
  const wrap = $("#accounts-table");
  if (!rows.length) { wrap.innerHTML = '<div class="empty">No accounts yet.</div>'; return; }
  const table = el("table");
  table.append(headRow(["Name", "Handle", "Proxy", "Daily limit", "Today", "Tone", ""]));
  rows.forEach(r => {
    const del = el("button", { class: "mini danger", text: "Delete", onclick: async () => {
      if (!confirm(`Delete account "${r.name}"?`)) return;
      await api("/api/accounts/" + r.id, { method: "DELETE" }); loadAccounts();
    }});
    table.append(el("tr", {},
      td(r.name), td(r.handle || "—"), td(r.proxy_string ? "•••" : "—"),
      td(String(r.daily_limit)), tdLimit(r.used_today, r.daily_limit),
      td(r.tone || "—"), el("td", { class: "actions" }, del),
    ));
  });
  wrap.innerHTML = ""; wrap.append(table);
}
$("#account-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target).entries());
  const msg = $("#account-msg");
  try {
    await jpost("/api/accounts", data);
    e.target.reset(); msg.className = "hint"; msg.textContent = "Account added.";
    loadAccounts();
  } catch (err) { msg.className = "hint error"; msg.textContent = String(err); }
});

// --- tasks -----------------------------------------------------------------
async function loadAccountOptions() {
  const rows = await api("/api/accounts");
  const sel = $("#task-account"); sel.innerHTML = "";
  if (!rows.length) { sel.append(el("option", { value: "", text: "— no accounts —" })); return; }
  rows.forEach(r => sel.append(el("option", { value: String(r.id), text: r.name + (r.handle ? ` (${r.handle})` : "") })));
}
async function loadTasks() {
  const status = $("#task-filter-status").value;
  const rows = await api("/api/tasks" + (status ? "?status=" + status : ""));
  const wrap = $("#tasks-table");
  if (!rows.length) { wrap.innerHTML = '<div class="empty">No tasks in the queue.</div>'; return; }
  const accounts = await api("/api/accounts");
  const nameById = Object.fromEntries(accounts.map(a => [a.id, a.name]));
  const table = el("table");
  table.append(headRow(["Title", "Account", "Type", "Deadline", "Status", "Actions"]));
  rows.forEach(t => {
    const actions = el("td", { class: "actions" });
    if (t.status === "pending") {
      actions.append(el("button", { class: "mini ok", text: "Approve", onclick: () => approveTask(t.id) }));
      actions.append(el("button", { class: "mini danger", text: "Reject", onclick: () => setTaskStatus(t.id, "rejected") }));
    } else if (t.status === "approved") {
      actions.append(el("button", { class: "mini", text: "Mark done", onclick: () => setTaskStatus(t.id, "done") }));
    } else {
      actions.append(el("span", { class: "pill", text: "—" }));
    }
    table.append(el("tr", {},
      td(t.title || "(untitled)"), td(nameById[t.account_id] || "—"),
      td(t.kind), td(fmtDate(t.deadline)), el("td", {}, badge(t.status)), actions,
    ));
  });
  wrap.innerHTML = ""; wrap.append(table);
}
async function approveTask(id) {
  try { await jpost(`/api/tasks/${id}/approve`); loadTasks(); }
  catch (err) { alert("Cannot approve: " + err); }
}
async function setTaskStatus(id, status) {
  await jpost(`/api/tasks/${id}/status`, { status }); loadTasks();
}
$("#task-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target).entries());
  if (data.account_id) data.account_id = Number(data.account_id);
  await jpost("/api/tasks", data);
  e.target.reset(); loadTasks();
});
$("#task-filter-status").addEventListener("change", loadTasks);

// --- statistics ------------------------------------------------------------
async function loadStats() {
  const rows = await api("/api/stats/accounts");
  const wrap = $("#stats-table");
  if (!rows.length) { wrap.innerHTML = '<div class="empty">No data yet.</div>'; return; }
  const table = el("table");
  table.append(headRow(["Account", "Total", "Pending", "Approved", "Done", "Rejected", "Today"]));
  rows.forEach(r => table.append(el("tr", {},
    td(r.name), td(String(r.total_tasks)), td(String(r.counts.pending || 0)),
    td(String(r.counts.approved || 0)), td(String(r.counts.done || 0)),
    td(String(r.counts.rejected || 0)), tdLimit(r.used_today, r.daily_limit),
  )));
  wrap.innerHTML = ""; wrap.append(table);
}

// --- audit -----------------------------------------------------------------
async function loadAudit() {
  const rows = await api("/api/audit");
  const wrap = $("#audit-table");
  if (!rows.length) { wrap.innerHTML = '<div class="empty">No activity yet.</div>'; return; }
  const table = el("table");
  table.append(headRow(["Time", "Action", "Detail", "Account"]));
  rows.forEach(r => table.append(el("tr", {},
    td(fmtDate(r.ts)), el("td", {}, el("code", { text: r.action })),
    td(r.detail || "—"), td(r.account_id != null ? "#" + r.account_id : "—"),
  )));
  wrap.innerHTML = ""; wrap.append(table);
}

// --- table cell helpers ----------------------------------------------------
function headRow(cols) { const tr = el("tr"); cols.forEach(c => tr.append(el("th", { text: c }))); return el("thead", {}, tr); }
function td(text) { return el("td", { text: String(text) }); }
function tdLimit(used, limit) {
  const pct = limit ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  const cell = el("td", {});
  cell.append(el("div", { class: "pill", text: `${used}/${limit}` }));
  const bar = el("div", { class: "limit-bar" }); const fill = el("i"); fill.style.width = pct + "%";
  bar.append(fill); cell.append(bar);
  return cell;
}

// --- runs (workflow) -------------------------------------------------------
let polling = null;
const logEl = $("#log"), statusEl = $("#status"), runBtn = $("#run"), resultBadge = $("#result-badge");
function setStatus(t, c) { statusEl.textContent = t; statusEl.className = "status " + c; }
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
  runBtn.disabled = true; setStatus("Running…", "running"); appendLog("--- starting workflow ---");
  let jobId;
  try { jobId = (await jpost("/api/run", runData())).job_id; }
  catch (err) { appendLog("Error: " + err); setStatus("Idle", "idle"); runBtn.disabled = false; return; }
  polling = setInterval(async () => {
    try {
      const job = await api("/api/jobs/" + jobId);
      setLog(["--- starting workflow ---", ...job.logs]);
      if (job.status === "done" || job.status === "error") {
        clearInterval(polling); polling = null; runBtn.disabled = false;
        if (job.status === "error") { setStatus("Error", "error"); resultBadge.className = "err"; resultBadge.textContent = job.error || "failed"; }
        else {
          const r = job.result || {};
          if (r.ok) { setStatus("Done", "done"); resultBadge.className = "ok"; resultBadge.textContent = "OK — " + JSON.stringify(r.response); }
          else { setStatus("Failed", "error"); resultBadge.className = "err"; resultBadge.textContent = r.error || "workflow failed"; }
        }
      }
    } catch (err) { clearInterval(polling); polling = null; runBtn.disabled = false; setStatus("Error", "error"); appendLog("Polling failed: " + err); }
  }, 700);
});

// --- boot ------------------------------------------------------------------
showTab("dashboard");

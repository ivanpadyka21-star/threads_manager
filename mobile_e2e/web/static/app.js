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

// --- toasts ----------------------------------------------------------------
const TOAST_ICON = { success: "✓", error: "⚠", warn: "!", info: "•" };
function toast(message, type = "info", ms = 3400) {
  const c = $("#toast-container"); if (!c) return;
  const el_ = el("div", { class: "toast " + type },
    el("span", { class: "toast-ic", text: TOAST_ICON[type] || "•" }),
    el("span", { class: "toast-msg", text: String(message) }));
  c.append(el_);
  requestAnimationFrame(() => el_.classList.add("show"));
  const close = () => { el_.classList.remove("show"); setTimeout(() => el_.remove(), 220); };
  el_.addEventListener("click", close);
  setTimeout(close, ms);
}

// --- modal -----------------------------------------------------------------
function modal({ title, body, actions }) {
  const root = $("#modal-root");
  const box = el("div", { class: "modal-box" });
  if (title) box.append(el("div", { class: "modal-head", text: title }));
  const bodyEl = el("div", { class: "modal-body" });
  if (typeof body === "string") bodyEl.textContent = body; else if (body) bodyEl.append(body);
  box.append(bodyEl);
  const foot = el("div", { class: "modal-foot" });
  const overlay = el("div", { class: "modal-overlay" }, box);
  const close = (val) => { overlay.classList.remove("show"); setTimeout(() => overlay.remove(), 180); overlay._resolve && overlay._resolve(val); };
  (actions || [{ label: "OK", value: true }]).forEach(a => {
    foot.append(el("button", { class: "mini " + (a.class || ""), text: a.label, onclick: () => close(a.value) }));
  });
  box.append(foot);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(undefined); });
  root.append(overlay);
  requestAnimationFrame(() => overlay.classList.add("show"));
  return new Promise(res => { overlay._resolve = res; });
}
function confirmDialog(message, title) {
  return modal({ title: title || t("confirm.title"), body: message, actions: [
    { label: t("btn.cancel"), value: false, class: "" },
    { label: t("btn.ok"), value: true, class: "danger" },
  ]}).then(v => v === true);
}
function infoDialog(title, text) {
  const pre = el("pre", { class: "modal-pre", text: text });
  return modal({ title, body: pre, actions: [{ label: t("btn.close"), value: true }] });
}

// --- skeletons & empty states ----------------------------------------------
function showSkeleton(wrap, rows = 3, kind = "row") {
  if (!wrap) return;
  wrap.innerHTML = "";
  const box = el("div", { class: "skel-box" });
  for (let i = 0; i < rows; i++) box.append(el("div", { class: "skel skel-" + kind }));
  wrap.append(box);
}
function emptyState(msg, hint, icon = "∅") {
  const wrap = el("div", { class: "empty" });
  wrap.append(el("div", { class: "empty-ic", text: icon }));
  wrap.append(el("div", { class: "empty-msg", text: msg }));
  if (hint) wrap.append(el("div", { class: "empty-hint", text: hint }));
  return wrap;
}

// --- tabs ------------------------------------------------------------------
const TAB_KEYS = { dashboard: "nav.dashboard", kpi: "nav.kpi", accounts: "nav.accounts", warmup: "nav.warmup", followups: "nav.followups", photos: "nav.photos", drops: "nav.drops", daily: "nav.daily", legends: "nav.legends", calendar: "nav.calendar", runs: "nav.runs", stats: "nav.stats", analytics: "nav.analytics", structure: "nav.structure", audit: "nav.audit", settings: "nav.settings" };
let currentTab = "dashboard";
function renderTab(name) {
  if (name === "dashboard") loadDashboard();
  if (name === "kpi") loadKPI();
  if (name === "accounts") loadAccounts();
  if (name === "warmup") loadWarmup();
  if (name === "followups") loadFollowups();
  if (name === "photos") loadPhotos();
  if (name === "drops") loadDrops();
  if (name === "daily") loadDaily();
  if (name === "legends") loadLegends();
  if (name === "calendar") loadCalendar();
  if (name === "stats") loadStats();
  if (name === "analytics") loadAnalytics(true);
  if (name === "structure") renderStructure();
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
async function loadDailyBrief() {
  const box = $("#da-brief"); const btn = $("#da-run"); if (!box) return;
  let d; try { d = await api("/api/daily-analysis"); } catch { return; }
  if (btn) {
    btn.disabled = !!d.running;
    btn.textContent = d.running ? t("da.running") : t("da.run");
  }
  box.innerHTML = "";
  // Post reminder — always visible so posts never get lost.
  const ps = d.posts || {};
  box.append(el("div", { class: "da-reminder" },
    el("span", { class: "da-rem-i", text: "📮" }),
    el("b", { text: ps.published_today || 0 }), el("span", { text: t("da.rem.out") }),
    el("b", { text: ps.scheduled || 0 }), el("span", { text: t("da.rem.queue") }),
    el("b", { class: (ps.pending_approval ? "warn" : ""), text: ps.pending_approval || 0 }),
    el("span", { text: t("da.rem.pending") })));
  if (!d.brief) { box.append(el("div", { class: "da-empty", text: t("da.empty") })); return; }
  const b = d.brief;
  box.append(el("div", { class: "da-when",
    text: t("da.done").replace("{ago}", d.age == null ? "" : fmtAgo(d.age))
      + (b.drop_label ? ` · ${b.drop_label} → #${b.drop_id}` : "") }));
  if (b.verdict) {
    const low = b.verdict.toLowerCase();
    const bad = /провал|нет цифр|нет росту|нема цифр|падени|слаб/.test(low);
    const good = /рост|есть цифр|сильн|легенд/.test(low);
    box.append(el("div", { class: "da-verdict " + (bad ? "bad" : good ? "good" : "mid") },
      el("span", { class: "da-v-i", text: bad ? "🔴" : good ? "🟢" : "🟡" }),
      el("b", { text: b.verdict })));
  }
  if (b.analysis) box.append(el("div", { class: "da-block" },
    el("div", { class: "da-lbl", text: t("da.analysis") }), el("p", { text: b.analysis })));
  if (b.signals && b.signals.length) {
    const blk = el("div", { class: "da-block" }, el("div", { class: "da-lbl", text: t("da.signals") }));
    const ul = el("ul", { class: "da-list signals" });
    b.signals.forEach(s => ul.append(el("li", { text: s })));
    blk.append(ul); box.append(blk);
  }
  if (b.strategy) box.append(el("div", { class: "da-block" },
    el("div", { class: "da-lbl", text: t("da.strategy") }), el("p", { text: b.strategy })));
  if (b.hypotheses && b.hypotheses.length) {
    const blk = el("div", { class: "da-block" }, el("div", { class: "da-lbl", text: t("da.hypotheses") }));
    const ul = el("ul", { class: "da-list hyp" });
    b.hypotheses.forEach(h => ul.append(el("li", { text: h })));
    blk.append(ul); box.append(blk);
  }
  if (b.drop_id) box.append(el("div", { class: "da-drop" },
    el("span", { text: (b.drop_label || t("da.drop").replace("{n}", b.posts)) + ` — ${b.posts} ${t("stats.posts")}` }),
    el("button", { class: "mini", text: t("da.open"), onclick: () => showTab("drops") })));
}
const daRun = $("#da-run");
if (daRun) daRun.addEventListener("click", async () => {
  daRun.disabled = true; daRun.textContent = t("da.running");
  try {
    await jpost("/api/daily-analysis", {});
    toast(t("da.started"), "success", 5000);
    let n = 0; const poll = setInterval(async () => {
      const d = await api("/api/daily-analysis").catch(() => ({}));
      if (!d.running || ++n > 60) { clearInterval(poll); loadDailyBrief(); loadDrops && loadDrops(); }
    }, 4000);
  } catch (e) { toast(String(e), "error", 6000); daRun.disabled = false; daRun.textContent = t("da.run"); }
});
async function loadDashboard() {
  loadDailyBrief();
  loadDashGoals();
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

// --- accounts (cards) ------------------------------------------------------
const AVATAR_GRAD = [
  "linear-gradient(135deg,#ff318c,#b24bff)", "linear-gradient(135deg,#257bff,#2dd4bf)",
  "linear-gradient(135deg,#ff9d2e,#ff318c)", "linear-gradient(135deg,#b24bff,#257bff)",
  "linear-gradient(135deg,#2dd4bf,#257bff)", "linear-gradient(135deg,#ff5470,#ff9d2e)",
];
function initials(name) { return (name || "?").trim().slice(0, 2).toUpperCase(); }

async function loadAccounts() {
  const wrap = $("#accounts-table");
  showSkeleton(wrap, 3, "card");
  const [rows, analytics] = await Promise.all([api("/api/accounts"), api("/api/analytics").catch(() => ({ accounts: [] }))]);
  if (!rows.length) { wrap.innerHTML = ""; wrap.append(emptyState(t("empty.accounts"), t("empty.accounts.hint"), "◉")); return; }
  const anaById = Object.fromEntries((analytics.accounts || []).map(a => [a.id, a]));
  const grid = el("div", { class: "acct-grid" });
  rows.forEach((r, i) => {
    const a = anaById[r.id] || {};
    const card = el("div", { class: "acct-card" });
    // header: avatar + name + status dot
    const av = el("div", { class: "acct-av", text: initials(r.name) });
    av.style.background = AVATAR_GRAD[i % AVATAR_GRAD.length];
    const head = el("div", { class: "acct-head" }, av,
      el("div", { class: "acct-id" },
        el("div", { class: "acct-name", text: r.name }),
        el("div", { class: "acct-handle", text: r.handle || "—" })),
      el("span", { class: "dot " + (a.state || "new"), title: t("state." + (a.state || "new")) }));
    card.append(head);
    // sparkline
    if (a.sparkline) { const sp = el("div", { class: "acct-spark" }); sp.append(barChart(a.sparkline, 240, 34)); card.append(sp); }
    // meta row: limit bar + score
    const meta = el("div", { class: "acct-meta" });
    const lim = el("div", { class: "acct-lim" });
    lim.append(el("div", { class: "pill", text: `${r.used_today}/${r.daily_limit}` }));
    const bar = el("div", { class: "limit-bar" }); const fill = el("i");
    fill.style.width = (r.daily_limit ? Math.min(100, Math.round(r.used_today / r.daily_limit * 100)) : 0) + "%";
    bar.append(fill); lim.append(bar); meta.append(lim);
    const sc = el("span", { class: "score-chip", text: a.score == null ? "—" : a.score + "%" });
    sc.style.color = scoreColor(a.score); meta.append(sc);
    card.append(meta);
    // footer: tone + proxy + delete
    const foot = el("div", { class: "acct-foot" });
    foot.append(el("span", { class: "acct-tone", text: r.tone || "—" }));
    foot.append(el("span", { class: "acct-proxy " + (r.proxy_string ? "on" : ""), text: r.proxy_string ? "proxy" : "" }));
    foot.append(el("button", { class: "mini danger", text: t("btn.delete"), onclick: async () => {
      if (!await confirmDialog(`${t("confirm.delete")} "${r.name}"?`)) return;
      await api("/api/accounts/" + r.id, { method: "DELETE" });
      toast(t("msg.deleted"), "success"); loadAccounts();
    }}));
    card.append(foot);
    grid.append(card);
  });
  wrap.innerHTML = ""; wrap.append(grid);
}
$("#account-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target).entries());
  try { await jpost("/api/accounts", data); e.target.reset(); toast(t("msg.added"), "success"); loadAccounts(); }
  catch (err) { toast(String(err), "error"); }
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

// --- AI quota (local estimate of Gemini usage) -----------------------------
async function loadAiUsage() {
  const wrap = $("#ai-quota"); if (!wrap) return;
  let u; try { u = await api("/api/ai/usage"); } catch { return; }
  const dayLow = u.remaining_today <= 0, minLow = u.remaining_minute <= 0;
  wrap.className = "ai-quota" + (dayLow ? " danger" : (minLow ? " warn" : ""));
  wrap.textContent =
    `${t("ai.quota")}: ${t("ai.today")} ${u.used_today}/${u.rpd} · ${t("ai.min")} ${u.used_minute}/${u.rpm}`;
}

// --- batch of scheduled posts ----------------------------------------------
$("#batch-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(e.target);
  const briefs = (fd.get("briefs") || "").split("\n").map(s => s.trim()).filter(Boolean).slice(0, 10);
  if (!briefs.length) { toast(t("batch.need_briefs"), "warn"); return; }
  const data = {
    account_id: fd.get("account_id") ? Number(fd.get("account_id")) : null,
    briefs, language: fd.get("language"), style: fd.get("style"),
    interval_minutes: Number(fd.get("interval_minutes")) || 60,
    start_at: fd.get("start_at") || null,
    max_chars: fd.get("max_chars") ? Number(fd.get("max_chars")) : null,
  };
  const btn = e.target.querySelector("button[type=submit]");
  const label = btn.textContent; btn.disabled = true; btn.textContent = "…";
  const wrap = $("#batch-preview");
  let res;
  try { res = await jpost("/api/batches", data); }
  catch (err) { toast(String(err), "error"); btn.disabled = false; btn.textContent = label; return; }
  btn.disabled = false; btn.textContent = label;
  // Drafts generate in the background — poll for progress.
  const total = res.tasks.length;
  wrap.innerHTML = `<div class="hint">${tf("batch.generating", { d: 0, n: total })}</div>`;
  const poll = setInterval(async () => {
    let b; try { b = await api("/api/batches/" + res.batch_id); } catch { return; }
    const drafted = (b.status && b.status.drafted) || 0;
    if (b.generating) { wrap.innerHTML = `<div class="hint">${tf("batch.generating", { d: drafted, n: total })}</div>`; }
    else {
      clearInterval(poll);
      renderBatchPreview(res.batch_id, b.tasks);
      toast(tf("batch.drafted", { n: drafted }), "success");
      loadAiUsage();
    }
  }, 1500);
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
    wrap.innerHTML = "";
    toast(tf("batch.scheduled", { n: r.scheduled }), "success");
    loadTasks();
  }});
  const bar = el("div", { class: "form-actions", style: "margin-top:12px" }); bar.append(approve);
  wrap.append(bar);
}
async function loadTasks() {
  const status = $("#task-filter-status").value;
  const wrap = $("#tasks-table");
  showSkeleton(wrap, 4, "row");
  const [rows, accounts] = await Promise.all([
    api("/api/tasks" + (status ? "?status=" + status : "")), api("/api/accounts"),
  ]);
  if (!rows.length) { wrap.innerHTML = ""; wrap.append(emptyState(t("empty.tasks"), null, "☑")); return; }
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
      if (!await confirmDialog(`${t("confirm.delete.task")} "${tk.title || "—"}"?`)) return;
      await api("/api/tasks/" + tk.id, { method: "DELETE" }); toast(t("msg.deleted"), "success");
      loadTasks(); if (currentTab === "dashboard") loadDashboard();
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
  catch (err) { toast(String(err), "error"); }
}
async function setTaskStatus(id, status) { await jpost(`/api/tasks/${id}/status`, { status }); loadTasks(); }
async function publishTask(id) {
  try {
    const res = await jpost(`/api/tasks/${id}/publish`);
    toast(t("th.published") + " (id " + res.published_id + ")", "success");
  } catch (err) { toast(String(err), "error", 6000); }
  loadTasks(); loadAiUsage(); if (currentTab === "dashboard") loadDashboard();
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
    await infoDialog(t("an.draft"), res.result || "");
  } catch (err) {
    // On AI failure the backend auto-flags the task as a problem.
    toast(String(err), "error", 6000);
  }
  loadTasks(); loadAiUsage(); if (currentTab === "dashboard") loadDashboard();
}
$("#task-form")?.addEventListener("submit", async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target).entries());
  if (data.account_id) data.account_id = Number(data.account_id);
  await jpost("/api/tasks", data); e.target.reset(); loadTasks();
});
$("#task-filter-status")?.addEventListener("change", loadTasks);

// --- Structure diagram (SVG architecture map) ------------------------------
const STRUCT_CAT = {
  ui: "#6f8bff", ai: "#b24bff", agent: "#ff3d7f", data: "#2dd4bf",
  action: "#ffb020", publish: "#3ddc84", analytics: "#257bff", ext: "#8a8a97",
};
function hexShade(hex, f) { // f<1 darken, f>1 lighten
  const n = parseInt(hex.slice(1), 16);
  const cl = (v) => Math.max(0, Math.min(255, Math.round(v)));
  const r = cl(((n >> 16) & 255) * f), g = cl(((n >> 8) & 255) * f), b = cl((n & 255) * f);
  return "#" + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
}
function renderStructure() {
  const L = window.I18N.lang === "en" ? "en" : "ru";
  const tx = (o) => o[L];
  // id: [x, y, w, h, cat, icon, {ru,en title}, {ru,en sub}]
  const N = {
    user:      [388,  22, 224, 66, "ui",       "🧑‍💼", { ru: "СММ-специалист", en: "SMM specialist" }, { ru: "промты · одобрение", en: "prompts · approval" }],
    claude:    [372, 150, 256, 66, "agent",    "🧠", { ru: "Claude · оркестратор", en: "Claude · orchestrator" }, { ru: "через API · направляет агента", en: "via API · directs the agent" }],
    agent:     [352, 280, 296, 96, "agent",    "🤖", { ru: "Агент-стратег", en: "Strategist agent" }, { ru: "учится на метриках → промты", en: "learns from metrics → prompts" }],
    gemini:    [388, 452, 224, 68, "ext",      "✍️", { ru: "Gemini · writer", en: "Gemini · writer" }, { ru: "flash → lite (свап)", en: "flash → lite (fallback)" }],
    dash:      [56, 150, 236, 62, "ui",        "🖥️", { ru: "Дашборд (Flask + UI)", en: "Dashboard (Flask + UI)" }, { ru: "вкладки · роуты · i18n", en: "tabs · routes · i18n" }],
    studio:    [52, 288, 244, 66, "ai",        "✨", { ru: "Студия / Планировщик", en: "Studio / Planner" }, { ru: "промт → темы → задачи", en: "prompt → topics → tasks" }],
    analytics: [712, 284, 252, 68, "analytics","📊", { ru: "Аналитика 24/7", en: "Analytics 24/7" }, { ru: "метрики·тренды·разведка", en: "metrics·trends·feed intel" }],
    store:     [352, 566, 296, 62, "data",     "🗄️", { ru: "Хранилище (SQLite)", en: "Store (SQLite)" }, { ru: "аккаунты·задачи·метрики", en: "accounts·tasks·metrics" }],
    approval:  [104, 566, 210, 60, "action",   "✅", { ru: "Одобрение", en: "Approval" }, { ru: "человек в цикле", en: "human-in-the-loop" }],
    scheduler: [700, 566, 224, 60, "ai",       "⏱️", { ru: "Планировщик (фон)", en: "Scheduler (bg)" }, { ru: "Киев · лимиты · догон", en: "Kyiv · limits · catch-up" }],
    threads:   [352, 660, 296, 60, "publish",  "🧵", { ru: "Threads API", en: "Threads API" }, { ru: "реальная публикация", en: "real publishing" }],
    runs:      [716, 452, 232, 60, "ext",      "📱", { ru: "Запуски (Appium)", en: "Runs (Appium)" }, { ru: "UI-автоматизация · позже", en: "UI automation · later" }],
  };
  const anchor = (id, side) => {
    const [x, y, w, h] = N[id];
    if (side === "b") return [x + w / 2, y + h];
    if (side === "t") return [x + w / 2, y];
    if (side === "l") return [x, y + h / 2];
    if (side === "r") return [x + w, y + h / 2];
    return [x + w / 2, y + h / 2];
  };
  const s = svg("svg", { viewBox: "0 0 1000 760", width: "100%", class: "struct-svg" });
  const defs = svg("defs", {});
  // arrowheads
  const mk = (id, color) => { const m = svg("marker", { id, markerWidth: 8, markerHeight: 8, refX: 6, refY: 3, orient: "auto", markerUnits: "strokeWidth" }); m.append(svg("path", { d: "M0,0 L6,3 L0,6 z", fill: color })); return m; };
  defs.append(mk("ah", "#5b5b6b"), mk("ahd", "#b14bff"), mk("ahc", "#ff2e7e"));
  // face gradient (dark glass slab)
  const fg = svg("linearGradient", { id: "faceGrad", x1: 0, y1: 0, x2: 0, y2: 1 });
  fg.append(svg("stop", { offset: "0%", "stop-color": "#24242e" }), svg("stop", { offset: "100%", "stop-color": "#14141b" }));
  defs.append(fg);
  // main-chain connector gradient (pink→violet→teal)
  const cg = svg("linearGradient", { id: "chainGrad", x1: 0, y1: 0, x2: 0, y2: 1 });
  cg.append(svg("stop", { offset: "0%", "stop-color": "#ff2e7e" }), svg("stop", { offset: "55%", "stop-color": "#b14bff" }), svg("stop", { offset: "100%", "stop-color": "#22d3c5" }));
  defs.append(cg);
  // glow filters
  const glow = svg("filter", { id: "glow", x: "-60%", y: "-60%", width: "220%", height: "220%" });
  glow.append(svg("feGaussianBlur", { stdDeviation: 5, result: "b" }));
  const gm = svg("feMerge", {}); gm.append(svg("feMergeNode", { in: "b" }), svg("feMergeNode", { in: "SourceGraphic" })); glow.append(gm);
  defs.append(glow);
  const sh = svg("filter", { id: "slabsh", x: "-40%", y: "-40%", width: "180%", height: "200%" });
  sh.append(svg("feDropShadow", { dx: 0, dy: 10, stdDeviation: 12, "flood-color": "#000", "flood-opacity": "0.55" }));
  defs.append(sh);
  s.append(defs);

  // -- connectors (drawn first, under nodes) --------------------------------
  const link = (from, to, opts = {}) => {
    const [x1, y1] = anchor(from, opts.fs || "b"), [x2, y2] = anchor(to, opts.ts || "t");
    const midY = (y1 + y2) / 2, curve = Math.abs(x1 - x2) > 30;
    const d = curve && (opts.fs === "b" || opts.ts === "t" || !opts.fs)
      ? `M${x1},${y1} C${x1},${midY} ${x2},${midY} ${x2},${y2}`
      : `M${x1},${y1} L${x2},${y2}`;
    const main = opts.main, dash = opts.dash;
    const stroke = main ? "url(#chainGrad)" : (dash ? "#8a5bd0" : "#4a4a58");
    // base line
    s.append(svg("path", { d, fill: "none", stroke, "stroke-width": main ? 4.5 : 2, "stroke-linecap": "round",
      "stroke-dasharray": dash && !main ? "5 5" : "0", opacity: main ? 0.95 : 0.75,
      "marker-end": main ? "url(#ahc)" : (dash ? "url(#ahd)" : "url(#ah)"), filter: main ? "url(#glow)" : "" }));
    // animated flowing dots along the path
    if (main || opts.flow) {
      const flow = svg("path", { d, fill: "none", stroke: main ? "#fff" : "#c9a6ff", "stroke-width": main ? 2.4 : 1.6,
        "stroke-linecap": "round", "stroke-dasharray": "1 16", opacity: main ? 0.9 : 0.6 });
      const an = svg("animate", { attributeName: "stroke-dashoffset", from: "34", to: "0", dur: main ? "1.1s" : "1.6s", repeatCount: "indefinite" });
      flow.append(an); s.append(flow);
    }
    if (opts.label) {
      const lx = (x1 + x2) / 2, ly = (y1 + y2) / 2 - 5;
      const g = svg("g", {});
      const tw = tx(opts.label).length * 6.2 + 12;
      g.append(svg("rect", { x: lx - tw / 2, y: ly - 12, width: tw, height: 17, rx: 8, fill: "#0c0c12", stroke: "rgba(255,255,255,.1)", "stroke-width": 1 }));
      const txt = svg("text", { x: lx, y: ly, class: "struct-alabel", "text-anchor": "middle" }); txt.textContent = tx(opts.label);
      g.append(txt); s.append(g);
    }
  };
  // main hierarchy chain (the spotlight): You → Claude → Agent → Gemini
  link("user", "claude", { main: true, label: { ru: "чат", en: "chat" } });
  link("claude", "agent", { main: true, label: { ru: "направляет", en: "directs" } });
  link("agent", "gemini", { main: true, label: { ru: "промты", en: "prompts" } });
  // supporting pipeline
  link("user", "dash", { fs: "l", ts: "t" });
  link("dash", "studio");
  link("studio", "gemini", { fs: "b", ts: "l", dash: true, label: { ru: "ИИ", en: "AI" } });
  link("studio", "store", { fs: "b", ts: "l", label: { ru: "задачи", en: "tasks" } });
  link("agent", "store", { fs: "b", ts: "t" });
  link("store", "approval", { fs: "l", ts: "t", label: { ru: "одобрить", en: "approve" } });
  link("store", "scheduler", { fs: "r", ts: "t" });
  link("scheduler", "threads", { fs: "b", ts: "r" });
  link("approval", "threads", { fs: "b", ts: "l" });
  link("store", "analytics", { fs: "r", ts: "b" });
  link("analytics", "agent", { fs: "l", ts: "r", dash: true, flow: true, label: { ru: "что работает", en: "what works" } });

  // -- 3D slab nodes --------------------------------------------------------
  for (const [id, [x, y, w, h, cat, icon, title, sub]] of Object.entries(N)) {
    const color = STRUCT_CAT[cat], hero = id === "agent", dashed = id === "runs";
    const node = svg("g", { class: "struct-node" + (hero ? " hero" : "") });
    const rx = 16;
    // ground shadow
    node.append(svg("ellipse", { cx: x + w / 2, cy: y + h + 10, rx: w * 0.42, ry: 7, fill: "#000", opacity: 0.35, filter: "url(#glow)" }));
    // extruded depth: two stacked darker slabs behind the face
    node.append(svg("rect", { x: x + 6, y: y + 9, width: w, height: h, rx, fill: hexShade(color, 0.35), opacity: 0.85 }));
    node.append(svg("rect", { x: x + 3, y: y + 4.5, width: w, height: h, rx, fill: hexShade(color, 0.55), opacity: 0.9 }));
    // face
    node.append(svg("rect", { x, y, width: w, height: h, rx, fill: "url(#faceGrad)",
      stroke: color, "stroke-width": hero ? 2 : 1.4, "stroke-dasharray": dashed ? "6 5" : "0",
      opacity: dashed ? 0.8 : 1, filter: "url(#slabsh)" }));
    // top sheen highlight
    node.append(svg("rect", { x: x + 1, y: y + 1, width: w - 2, height: h * 0.42, rx: rx - 2, fill: "#fff", opacity: 0.05 }));
    // coloured accent bar (left)
    node.append(svg("rect", { x, y: y + 8, width: 4, height: h - 16, rx: 2, fill: color, filter: hero ? "url(#glow)" : "" }));
    // hero pulsing ring around the agent avatar
    const iconCx = x + 30, iconCy = y + h / 2;
    if (hero) {
      const ring = svg("circle", { cx: iconCx, cy: iconCy, r: 21, fill: "none", stroke: color, "stroke-width": 2, opacity: 0.6 });
      ring.append(svg("animate", { attributeName: "r", values: "20;26;20", dur: "2.6s", repeatCount: "indefinite" }));
      ring.append(svg("animate", { attributeName: "opacity", values: "0.6;0;0.6", dur: "2.6s", repeatCount: "indefinite" }));
      node.append(ring);
    }
    // icon avatar disc
    node.append(svg("circle", { cx: iconCx, cy: iconCy, r: hero ? 19 : 15, fill: hexShade(color, 0.25), stroke: color, "stroke-width": 1.4 }));
    const ic = svg("text", { x: iconCx, y: iconCy + (hero ? 6 : 5), "text-anchor": "middle", "font-size": hero ? 20 : 15 }); ic.textContent = icon;
    node.append(ic);
    // texts
    const tX = x + (hero ? 60 : 52);
    const tEl = svg("text", { x: tX, y: y + (hero ? h / 2 - 6 : 25), class: "struct-t" + (hero ? " hero" : "") }); tEl.textContent = tx(title); tEl.setAttribute("fill", "#f2f2f7");
    const sEl = svg("text", { x: tX, y: y + (hero ? h / 2 + 14 : 43), class: "struct-s" }); sEl.textContent = tx(sub);
    node.append(tEl, sEl);
    if (hero) {
      const crown = svg("text", { x: x + w - 20, y: y + 24, "text-anchor": "middle", "font-size": 16 }); crown.textContent = "👑";
      node.append(crown);
    }
    s.append(node);
  }
  const wrap = $("#structure"); wrap.innerHTML = ""; wrap.append(s);

  // legend
  const legend = $("#struct-legend"); legend.innerHTML = "";
  const cats = { ui: { ru: "Интерфейс", en: "UI" }, ai: { ru: "ИИ / планирование", en: "AI / planning" }, agent: { ru: "Агент", en: "Agent" }, data: { ru: "Данные", en: "Data" }, action: { ru: "Человек", en: "Human" }, publish: { ru: "Публикация", en: "Publishing" }, analytics: { ru: "Аналитика", en: "Analytics" }, ext: { ru: "Внешнее", en: "External" } };
  for (const [c, lbl] of Object.entries(cats)) {
    const item = el("span", { class: "leg" });
    const dot = el("span", { class: "leg-dot" }); dot.style.background = STRUCT_CAT[c];
    item.append(dot, document.createTextNode(tx(lbl)));
    legend.append(item);
  }
}

// --- Warmup (engagement branch) -------------------------------------------
async function loadWarmup() {
  const sel = $("#wu-account");
  if (sel && !sel.children.length) {
    const accs = await api("/api/accounts");
    sel.innerHTML = accs.map(a => `<option value="${a.id}">${a.name}</option>`).join("");
  }
  const d = await api("/api/warmup/actions");
  const st = d.stats || {};
  $("#wu-stats").innerHTML =
    `<span class="chip">💬 ${t("wu.st.replies")}: ${(st.reply && st.reply.pending) || 0}</span>` +
    `<span class="chip">❤ ${t("wu.st.likes")}: ${(st.like && st.like.pending) || 0}</span>` +
    `<span class="chip">➕ ${t("wu.st.follows")}: ${(st.follow && st.follow.pending) || 0}</span>` +
    `<span class="chip good">✓ ${t("wu.st.done")}: ${st.done || 0}</span>`;
  const actions = (d.actions || []).filter(a => a.status === "pending");
  renderWarmupReplies(actions.filter(a => a.kind === "reply"));
  renderWarmupManual(actions.filter(a => a.kind === "like" || a.kind === "follow"));
}
function warmupTargetLink(a) {
  return a.target_url
    ? el("a", { href: a.target_url, target: "_blank", class: "wu-link", text: "↗ пост" })
    : el("span", { class: "wu-nolink", text: (a.target_author ? "@" + a.target_author : "") });
}
function renderWarmupReplies(list) {
  const wrap = $("#wu-replies"); wrap.innerHTML = "";
  if (!list.length) { wrap.append(emptyState(t("wu.empty.replies"), t("wu.empty.hint"), "💬")); return; }
  list.forEach(a => {
    const card = el("div", { class: "wu-card" });
    card.append(el("div", { class: "wu-target", text: "→ " + (a.target_text || "").slice(0, 120) }));
    const draft = el("textarea", { class: "wu-draft", rows: 3 }); draft.value = a.draft || "";
    card.append(draft);
    const row = el("div", { class: "wu-actions" }, warmupTargetLink(a),
      el("button", { class: "mini", text: t("wu.publish"), onclick: async () => {
        const r = await jpost("/api/warmup/actions/" + a.id + "/approve", { draft: draft.value });
        if (r.published_id) toast(t("wu.published"), "success");
        else if (r.manual) toast(t("wu.manual_hint"), "warn", 7000);
        loadWarmup();
      } }),
      el("button", { class: "mini ghost", text: t("wu.skip"), onclick: async () => {
        await jpost("/api/warmup/actions/" + a.id + "/skip", {}); loadWarmup(); } }));
    card.append(row); wrap.append(card);
  });
}
function renderWarmupManual(list) {
  const wrap = $("#wu-manual"); wrap.innerHTML = "";
  if (!list.length) { wrap.append(emptyState(t("wu.empty.manual"), t("wu.empty.hint"), "✅")); return; }
  list.forEach(a => {
    const row = el("div", { class: "wu-manual-row" },
      el("span", { class: "wu-kind " + a.kind, text: a.kind === "like" ? "❤" : "➕" }),
      el("span", { class: "wu-manual-text", text: (a.target_author ? "@" + a.target_author + " · " : "") + (a.target_text || "").slice(0, 70) }),
      warmupTargetLink(a),
      el("button", { class: "mini", text: "✓", title: t("wu.mark_done"), onclick: async () => {
        await jpost("/api/warmup/actions/" + a.id + "/done", {}); loadWarmup(); } }));
    wrap.append(row);
  });
}
const wuAdd = $("#wu-add");
if (wuAdd) wuAdd.addEventListener("click", async () => {
  const text = ($("#wu-paste").value || "").trim();
  if (!text) { toast(t("wu.need_text"), "warn"); return; }
  wuAdd.disabled = true;
  try {
    await jpost("/api/warmup/add-target", { text, account_id: $("#wu-account").value || null });
    toast(t("wu.added"), "success");
    $("#wu-paste").value = "";
    setTimeout(loadWarmup, 3500);
  } catch (err) { toast(String(err), "error", 6000); }
  wuAdd.disabled = false;
});
const wuRun = $("#wu-run");
if (wuRun) wuRun.addEventListener("click", async () => {
  wuRun.disabled = true; const lbl = wuRun.textContent; wuRun.textContent = t("wu.running");
  try {
    await jpost("/api/warmup/run", { account_id: $("#wu-account").value || null });
    toast(t("wu.started"), "success");
    let tries = 0;
    const poll = setInterval(async () => {
      await loadWarmup();
      const d = await api("/api/warmup/actions");
      const pending = (d.actions || []).filter(a => a.status === "pending").length;
      if (pending > 0 || ++tries > 30) { clearInterval(poll); wuRun.disabled = false; wuRun.textContent = lbl; }
    }, 3000);
  } catch (err) { toast(String(err), "error", 6000); wuRun.disabled = false; wuRun.textContent = lbl; }
});

// --- KPI (account growth) --------------------------------------------------
function kpiHeroCard(value, label, icon, c1, c2) {
  const card = el("div", { class: "kpi-hc" });
  card.style.setProperty("--c1", c1); card.style.setProperty("--c2", c2);
  const v = el("b", {});
  card.append(el("span", { class: "kpi-hc-i", text: icon }), v,
    el("span", { class: "kpi-hc-l", text: label }));
  countUp(v, value || 0);
  return card;
}
function deltaBadge(n) {
  const cls = n > 0 ? "up" : n < 0 ? "down" : "flat";
  const sign = n > 0 ? "▲ +" : n < 0 ? "▼ " : "= ";
  return el("span", { class: "kpi-delta " + cls, text: sign + Math.abs(n) });
}
function sparkline(series, color) {
  const vals = (series || []).map(s => s.value);
  if (vals.length < 2 || !vals.some(v => v > 0)) return el("div", { class: "kpi-spark-empty" });
  return areaChart(vals, { w: 240, h: 46, color });
}
async function loadKPI() {
  let d; try { d = await api("/api/kpi"); } catch { return; }
  // hero aggregate cards
  const hero = $("#kpi-hero-cards"); if (hero) {
    hero.innerHTML = "";
    hero.append(
      kpiHeroCard(d.totals.followers, t("kpi.followers"), "👥", "#ff2e7e", "#b14bff"),
      kpiHeroCard(d.totals.profile_views, t("kpi.pviews"), "👁", "#7ee7ff", "#3b82f6"),
      kpiHeroCard(d.engagement, t("kpi.engagement"), "🔥", "#b14bff", "#22d3c5"),
      kpiHeroCard(d.totals.clicks, t("kpi.clicks"), "🔗", "#ffb020", "#ff2e7e"));
  }
  // priority growth goals (new vector): followers > likes > clicks
  const gw = $("#kpi-goals");
  if (gw && d.goals) {
    gw.innerHTML = "";
    const defs = [
      { k: "followers", lab: t("kpi.goal.followers"), c: "#ff2e7e" },
      { k: "likes", lab: t("kpi.goal.likes"), c: "#b14bff" },
      { k: "clicks", lab: t("kpi.goal.clicks"), c: "#22d3c5" },
    ];
    defs.forEach(def => {
      const g = d.goals[def.k]; if (!g) return;
      const row = el("div", { class: "kpi-goal" });
      row.append(el("div", { class: "kpi-goal-top" },
        el("span", { class: "kpi-goal-l", text: def.lab }),
        el("span", { class: "kpi-goal-v", text: `${fmtNum(g.cur)} / ${fmtNum(g.target)} · ${g.pct}%` })));
      const track = el("div", { class: "kpi-goal-track" });
      const fill = el("div", { class: "kpi-goal-fill" });
      fill.style.background = `linear-gradient(90deg, ${def.c}, ${def.c}88)`;
      if (window.__liveRefresh) fill.style.width = g.pct + "%";
      else setTimeout(() => { fill.style.width = g.pct + "%"; }, 60);
      track.append(fill); row.append(track); gw.append(row);
    });
  }
  const upd = $("#kpi-updated");
  if (upd) upd.textContent = d.updated_age == null ? t("kpi.never") : t("kpi.updated").replace("{ago}", fmtAgo(d.updated_age));

  // follower growth chart
  const g = $("#kpi-growth"); if (g) {
    g.innerHTML = "";
    const series = d.follower_series || [];
    const panel = el("div", { class: "panel" });
    panel.append(el("div", { class: "panel-head" }, el("h3", { text: t("kpi.growth") })));
    const vals = series.map(s => s.value);
    if (vals.length >= 2 && vals.some(v => v > 0)) panel.append(areaChart(vals, { color: "#ff2e7e", h: 150 }));
    else panel.append(el("div", { class: "kpi-growth-hint", text: t("kpi.growth_soon") }));
    g.append(panel);
  }

  // per-account cards
  const wrap = $("#kpi-accounts"); if (!wrap) return;
  wrap.innerHTML = "";
  (d.accounts || []).forEach((a, i) => {
    const card = el("div", { class: "kpi-card" });
    card.append(el("div", { class: "kpi-card-top" },
      el("span", { class: "kpi-rank", text: "#" + (i + 1) }),
      el("span", { class: "kpi-h", text: "@" + (a.account || "").replace(/^@/, "") })));
    // followers big + delta
    const fwrap = el("div", { class: "kpi-foll" });
    const fb = el("b", {}); countUp(fb, a.followers || 0);
    fwrap.append(fb, el("span", { class: "kpi-foll-l", text: t("kpi.followers") }), deltaBadge(a.followers_delta_period || 0));
    card.append(fwrap);
    // metric row
    card.append(el("div", { class: "kpi-metrics" },
      kpiMini("👁", a.profile_views, t("kpi.pviews")),
      kpiMini("🔗", a.clicks, t("kpi.clicks")),
      kpiMini("❤", a.likes, t("col.likes")),
      kpiMini("💬", a.replies, t("col.replies"))));
    // top clicked link (e.g. the bio Telegram link)
    const links = a.click_links || [];
    if (links.length && links[0].value > 0) {
      const short = links[0].url.replace(/^https?:\/\//, "").slice(0, 34);
      card.append(el("div", { class: "kpi-link" },
        el("span", { class: "kpi-link-i", text: "🔗" }),
        el("a", { href: links[0].url, target: "_blank", class: "kpi-link-u", text: short }),
        el("b", { class: "kpi-link-v", text: fmtNum(links[0].value) })));
    }
    // profile-views sparkline
    card.append(el("div", { class: "kpi-spark" }, sparkline(a.views_series, "#7ee7ff")));
    // demographics or locked
    card.append(a.demo_locked ? kpiDemoLocked(a) : kpiDemo(a.demographics));
    wrap.append(card);
  });
}
function kpiMini(icon, val, label) {
  return el("div", { class: "kpi-mini" },
    el("span", { class: "kpi-mini-i", text: icon }),
    el("b", { text: fmtNum(val || 0) }),
    el("span", { class: "kpi-mini-l", text: label }));
}
function kpiDemoLocked(a) {
  const box = el("div", { class: "kpi-demo locked" });
  const pct = Math.min(100, Math.round((a.followers || 0)));
  box.append(el("div", { class: "kpi-demo-h", text: "🔒 " + t("kpi.demo.locked") }));
  const track = el("div", { class: "kpi-demo-track" });
  track.append(el("div", { class: "kpi-demo-fill", style: `width:${pct}%` }));
  box.append(track, el("div", { class: "kpi-demo-sub", text: t("kpi.demo.need").replace("{n}", a.demo_needed) }));
  return box;
}
function kpiDemo(demo) {
  const box = el("div", { class: "kpi-demo" });
  if (!demo) { box.append(el("div", { class: "kpi-demo-sub", text: t("kpi.demo.none") })); return box; }
  const total = Object.values(demo).reduce((s, v) => s + v, 0) || 1;
  const male = (demo.M || demo.male || 0), female = (demo.F || demo.female || 0);
  const mp = Math.round(male / total * 100), fp = Math.round(female / total * 100);
  box.append(el("div", { class: "kpi-demo-h", text: "👥 " + t("kpi.demo.title") }));
  const bar = el("div", { class: "kpi-gender" });
  bar.append(el("div", { class: "kpi-gender-m", style: `width:${mp}%`, title: `♂ ${mp}%` }),
    el("div", { class: "kpi-gender-f", style: `width:${fp}%`, title: `♀ ${fp}%` }));
  box.append(bar, el("div", { class: "kpi-demo-sub", text: `♂ ${mp}% · ♀ ${fp}%` }));
  return box;
}
const kpiRefreshBtn = $("#kpi-refresh");
if (kpiRefreshBtn) kpiRefreshBtn.addEventListener("click", async () => {
  kpiRefreshBtn.disabled = true; const lbl = kpiRefreshBtn.textContent; kpiRefreshBtn.textContent = t("kpi.refreshing");
  try {
    await jpost("/api/kpi/refresh", {});
    setTimeout(async () => { await loadKPI(); kpiRefreshBtn.disabled = false; kpiRefreshBtn.textContent = lbl; }, 6000);
  } catch { kpiRefreshBtn.disabled = false; kpiRefreshBtn.textContent = lbl; }
});

// --- Photos (image posts) --------------------------------------------------
let photoAccounts = [];
async function loadPhotos() {
  if (!photoAccounts.length) {
    try { photoAccounts = await api("/api/accounts"); } catch { photoAccounts = []; }
  }
  const d = await api("/api/photos").catch(() => ({ photos: [] }));
  const grid = $("#photos-grid"); if (!grid) return;
  grid.innerHTML = "";
  if (!(d.photos || []).length) {
    grid.append(emptyState(t("ph.empty"), t("ph.empty_hint"), "📸")); return;
  }
  d.photos.forEach(p => grid.append(renderPhotoCard(p)));
}
function renderPhotoCard(p) {
  const card = el("div", { class: "photo-card" });
  const img = el("img", { class: "photo-img", src: p.url, alt: "", loading: "lazy" });
  card.append(img);
  const body = el("div", { class: "photo-body" });
  const ta = el("textarea", { class: "photo-cap", rows: 2, placeholder: t("ph.caption_ph") });
  const sel = el("select", { class: "photo-acc" });
  sel.innerHTML = photoAccounts.map(a => `<option value="${a.id}">${a.handle || a.name}</option>`).join("");
  const row = el("div", { class: "photo-row" }, sel,
    el("button", { class: "mini", text: t("ph.post"), onclick: async () => {
      if (!ta.value.trim() && !confirm(t("ph.no_caption"))) return;
      const r = await jpost("/api/photos/post", { photo: p.name, account_id: sel.value, caption: ta.value.trim() }).catch(e => ({ error: String(e) }));
      if (r.error) toast(r.error, "error", 6000);
      else toast(t("ph.posting"), "success", 4000);
    } }));
  body.append(ta, row);
  card.append(body);
  return card;
}
const phFile = $("#ph-file");
if (phFile) phFile.addEventListener("change", async () => {
  if (!phFile.files.length) return;
  const st = $("#ph-up-status"); if (st) st.textContent = t("ph.uploading");
  const fd = new FormData();
  [...phFile.files].forEach(f => fd.append("file", f));
  try {
    const r = await fetch("/api/photos", { method: "POST", body: fd });
    const j = await r.json();
    if (st) st.textContent = t("ph.uploaded").replace("{n}", (j.saved || []).length);
    phFile.value = ""; loadPhotos();
  } catch (e) { if (st) st.textContent = String(e); }
});

// --- Follow-ups (auto-comment under our own posts, no spam) ----------------
function fuStatCard(value, label, cls, dec) {
  const card = el("div", { class: "fu-stat " + (cls || "") });
  const v = el("b", {}); card.append(v, el("span", { class: "fu-stat-l", text: label }));
  countUp(v, value || 0, { dec: dec || 0 });
  return card;
}
async function loadFollowupStats() {
  const wrap = $("#fu-stats"); if (!wrap) return;
  let s; try { s = await api("/api/followups/stats"); } catch { return; }
  wrap.innerHTML = "";

  // top metric cards
  const cards = el("div", { class: "fu-stat-row" },
    fuStatCard(s.published, t("fu.st.published"), "accent"),
    fuStatCard(s.to_people, t("fu.st.people"), "pink"),
    fuStatCard(s.followups, t("fu.st.own"), ""),
    fuStatCard(s.today, t("fu.st.today"), "teal"),
    fuStatCard(s.pending, t("fu.st.pending"), "amber"));
  wrap.append(cards);

  // engagement panel: reply-back ring + dialog vs no-reply
  const eng = el("div", { class: "panel fu-eng" });
  const ringWrap = el("div", { class: "fu-ring" });
  const pctB = el("b", { text: "0" });
  ringWrap.append(progressRing(s.reply_back_rate || 0, { c1: "#ff2e7e", c2: "#7ee7ff" }),
    el("div", { class: "fu-ring-c" }, pctB, el("i", { text: "%" })));
  countUp(pctB, s.reply_back_rate || 0, { dec: 1 });
  const engRight = el("div", { class: "fu-eng-r" },
    el("h3", { text: t("fu.eng.title") }),
    el("div", { class: "fu-eng-rows" },
      el("div", { class: "fu-eng-row good" }, el("span", { text: t("fu.eng.dialog") }), el("b", { text: fmtNum(s.dialogs || 0) })),
      el("div", { class: "fu-eng-row dim" }, el("span", { text: t("fu.eng.noreply") }), el("b", { text: fmtNum(s.no_reply_back || 0) })),
      el("div", { class: "fu-eng-row" }, el("span", { text: t("fu.eng.reached") }), el("b", { text: fmtNum(s.people_reached || 0) }))),
    el("div", { class: "fu-eng-hint", text: t("fu.eng.hint") }));
  eng.append(ringWrap, engRight);
  wrap.append(eng);

  // per-day chart
  const days = s.by_day || [];
  if (days.length) {
    const chart = el("div", { class: "panel fu-daychart" });
    chart.append(el("div", { class: "panel-head" }, el("h3", { text: t("fu.day.title") })));
    const max = Math.max(1, ...days.map(d => d.count));
    const bars = el("div", { class: "fu-bars" });
    days.forEach(d => {
      const col = el("div", { class: "fu-bar-col" });
      const h = Math.round((d.count / max) * 100);
      const bar = el("div", { class: "fu-bar", title: `${d.date}: ${d.count}` });
      const people = el("div", { class: "fu-bar-people" });
      const ph = d.count ? Math.round((d.people / d.count) * 100) : 0;
      people.style.height = ph + "%";
      bar.append(people);
      setTimeout(() => { bar.style.height = Math.max(3, h) + "%"; }, 40);
      col.append(el("span", { class: "fu-bar-v", text: d.count || "" }), bar,
        el("span", { class: "fu-bar-d", text: d.date.slice(5) }));
      bars.append(col);
    });
    chart.append(bars);
    chart.append(el("div", { class: "fu-legend" },
      el("span", { class: "lg pink", text: t("fu.day.people") }),
      el("span", { class: "lg accent", text: t("fu.day.own") })));
    wrap.append(chart);
  }

  // per-account
  const accs = s.by_account || [];
  if (accs.length) {
    const box = el("div", { class: "panel fu-accs" });
    box.append(el("div", { class: "panel-head" }, el("h3", { text: t("fu.acc.title") })));
    accs.forEach(a => {
      const rate = a.published ? Math.round(a.dialogs / a.published * 100) : 0;
      box.append(el("div", { class: "fu-acc-row" },
        el("span", { class: "fu-acc-h", text: "@" + (a.account || "").replace(/^@/, "") }),
        el("span", { class: "fu-acc-m", text: `${a.published} ${t("fu.acc.replies")}` }),
        el("span", { class: "fu-acc-m pink", text: `${a.to_people} ${t("fu.acc.people")}` }),
        el("span", { class: "fu-acc-m good", text: `${a.dialogs} 💬 (${rate}%)` })));
    });
    wrap.append(box);
  }
}
function fmtAgo(sec) {
  if (sec == null) return t("fu.pulse.never");
  if (sec < 90) return t("fu.pulse.now");
  const m = Math.round(sec / 60);
  if (m < 60) return t("fu.pulse.min").replace("{n}", m);
  const h = Math.floor(m / 60);
  return t("fu.pulse.hr").replace("{h}", h).replace("{m}", m % 60);
}
function renderFuPulse(d) {
  const el0 = $("#fu-pulse"); if (!el0) return;
  const hb = d.heartbeat_age;
  const alive = hb != null && hb < 120;   // scheduler ticks every ~30s
  el0.className = "fu-pulse " + (alive ? "alive" : "down");
  const last = fmtAgo(d.last_pass_age);
  el0.textContent = alive
    ? "● " + t("fu.pulse.alive") + " · " + t("fu.pulse.last") + " " + last
    : "● " + t("fu.pulse.down");
  el0.title = alive ? t("fu.pulse.alive_hint") : t("fu.pulse.down_hint");
}
async function loadFollowups() {
  loadFollowupStats();
  const d = await api("/api/followups").catch(() => null);
  if (!d) return;
  renderFuPulse(d);
  const auto = $("#fu-auto");
  if (auto) {
    if (!window.__liveRefresh || document.activeElement !== auto) auto.checked = d.auto !== false;
    if (!auto.dataset.bound) {
      auto.dataset.bound = "1";
      auto.addEventListener("change", async () => {
        await jpost("/api/followups/auto", { on: auto.checked }).catch(() => {});
        toast(auto.checked ? t("fu.auto_on") : t("fu.auto_off"), "success", 2000);
      });
    }
  }
  const autopub = $("#fu-autopub");
  if (autopub) {
    if (!window.__liveRefresh || document.activeElement !== autopub) autopub.checked = d.autopublish === true;
    if (!autopub.dataset.bound) {
      autopub.dataset.bound = "1";
      autopub.addEventListener("change", async () => {
        await jpost("/api/followups/autopublish", { on: autopub.checked }).catch(() => {});
        toast(autopub.checked ? t("fu.autopub_on") : t("fu.autopub_off"), "success", 2500);
      });
    }
  }
  const intens = $("#fu-intensity");
  if (intens) {
    if (!window.__liveRefresh || document.activeElement !== intens) intens.checked = d.intensity === "max";
    if (!intens.dataset.bound) {
      intens.dataset.bound = "1";
      intens.addEventListener("change", async () => {
        await jpost("/api/followups/intensity", { on: intens.checked }).catch(() => {});
        if (intens.checked && autopub) autopub.checked = true;
        toast(intens.checked ? t("fu.max_on") : t("fu.max_off"), intens.checked ? "warn" : "success", 5000);
      });
    }
  }
  const cand = $("#fu-cand");
  if (cand) cand.textContent = d.candidates ? t("fu.cand").replace("{n}", d.candidates) : t("fu.cand0");
  const wrap = $("#followups-list"); if (!wrap) return; wrap.innerHTML = "";
  const pending = d.pending || [];
  if (!pending.length) {
    wrap.append(emptyState(t("fu.empty"), t("fu.empty_hint"), "💬"));
  } else {
    pending.forEach(a => wrap.append(renderFollowupCard(a)));
  }
  const pub = d.published || [];
  if (pub.length) {
    wrap.append(el("div", { class: "fu-done-head", text: t("fu.done_head").replace("{n}", pub.length) }));
    pub.forEach(a => {
      wrap.append(el("div", { class: "fu-done-row" },
        el("span", { class: "fu-done-post", text: "↳ " + (a.target_text || "").slice(0, 60) }),
        el("span", { class: "fu-done-draft", text: a.draft || "" })));
    });
  }
}
function renderFollowupCard(a) {
  const isPerson = a.kind === "comment_reply";
  const card = el("div", { class: "fu-card" + (isPerson ? " person" : "") });
  card.append(el("div", { class: "fu-post" },
    el("span", { class: "fu-badge" + (isPerson ? " person" : ""),
      text: isPerson ? "@" + (a.target_author || "someone").replace(/^@/, "")
                     : (a.target_author ? "@" + a.target_author.replace(/^@/, "") : t("fu.our_post")) }),
    el("span", { class: "fu-post-text", text: (a.target_text || "").slice(0, 140) })));
  const draft = el("textarea", { class: "fu-draft-in", rows: 2 }); draft.value = a.draft || "";
  card.append(el("div", { class: "fu-arrow", text: isPerson ? t("fu.arrow_person") : t("fu.arrow_own") }));
  card.append(draft);
  card.append(el("div", { class: "fu-actions" },
    el("button", { class: "mini", text: t("fu.publish"), onclick: async () => {
      const r = await jpost("/api/warmup/actions/" + a.id + "/approve", { draft: draft.value });
      if (r.published_id) toast(t("fu.published"), "success");
      else if (r.manual) toast(t("fu.manual"), "warn", 7000);
      else if (r.error) toast(r.error, "error", 6000);
      loadFollowups();
    } }),
    el("button", { class: "mini ghost", text: t("fu.skip"), onclick: async () => {
      await jpost("/api/warmup/actions/" + a.id + "/skip", {}); loadFollowups(); } })));
  return card;
}
const fuPeople = $("#fu-people");
if (fuPeople) fuPeople.addEventListener("click", async () => {
  fuPeople.disabled = true; const lbl = fuPeople.textContent; fuPeople.textContent = t("fu.reading");
  try {
    await jpost("/api/followups/reply-people", {});
    toast(t("fu.people_started"), "success", 4000);
    let tries = 0; const before = ($("#followups-list") || {}).childElementCount || 0;
    const poll = setInterval(async () => {
      await loadFollowups();
      if (($("#followups-list").childElementCount || 0) > before || ++tries > 24) {
        clearInterval(poll); fuPeople.disabled = false; fuPeople.textContent = lbl;
      }
    }, 3000);
  } catch (err) { toast(String(err), "error", 6000); fuPeople.disabled = false; fuPeople.textContent = lbl; }
});
const fuDraft = $("#fu-draft");
if (fuDraft) fuDraft.addEventListener("click", async () => {
  fuDraft.disabled = true; const lbl = fuDraft.textContent; fuDraft.textContent = t("fu.drafting");
  try {
    await jpost("/api/followups/draft", {});
    toast(t("fu.started"), "success");
    let tries = 0;
    const poll = setInterval(async () => {
      await loadFollowups();
      const d = await api("/api/followups").catch(() => null);
      if ((d && (d.pending || []).length) || ++tries > 20) {
        clearInterval(poll); fuDraft.disabled = false; fuDraft.textContent = lbl;
      }
    }, 3000);
  } catch (err) { toast(String(err), "error", 6000); fuDraft.disabled = false; fuDraft.textContent = lbl; }
});

// --- Drops (campaigns with a goal + evaluation) ----------------------------
const expandedDrops = new Set();  // remember which drops are expanded across live refreshes
function miniGoal(cur, target, pct, label, cls) {
  const p = pct == null ? 0 : Math.min(100, pct);
  const row = el("div", { class: "dg-row" });
  row.append(el("div", { class: "dg-top" },
    el("span", { class: "dg-l", text: label }),
    el("span", { class: "dg-v " + (pct >= 100 ? "hit" : ""), text: `${fmtNum(cur)} / ${fmtNum(target)} · ${pct == null ? "—" : pct + "%"}` })));
  const track = el("div", { class: "dg-track" });
  const fill = el("div", { class: "dg-fill " + cls });
  track.append(fill); row.append(track);
  if (window.__liveRefresh) { fill.style.transition = "none"; fill.style.width = p + "%"; }
  else { fill.style.width = "0%"; setTimeout(() => { fill.style.width = p + "%"; }, 60); }
  return row;
}
function renderDropCard(d) {
  const met = d.met;
  const total = (d.posts || []).length;
  const publishedCount = (d.posts || []).filter(p => p.published).length;
  // Status by FACT: still publishing → running; all out → finished (hit/miss);
  // cancelled → stopped. Never say "finished" while posts are still queued.
  let stCls, stText;
  if (d.status === "cancelled") { stCls = "stopped"; stText = t("drop.st.stopped"); }
  else if (publishedCount < total) { stCls = "run"; stText = t("drop.st.running"); }
  else if (met) { stCls = "met"; stText = t("drop.st.finished_hit"); }
  else { stCls = "notmet"; stText = t("drop.st.finished_miss"); }
  const card = el("div", { class: "panel drop-card " + stCls });
  card.append(el("div", { class: "drop-head" },
    el("div", {}, el("div", { class: "drop-label", text: d.label || ("#" + d.id) }),
      el("div", { class: "drop-when", text: (d.created_at || "").replace("T", " ").slice(0, 16) })),
    el("div", { class: "drop-head-r" },
      el("span", { class: "drop-count", text: `📤 ${publishedCount}/${total} ${t("drop.out")}` }),
      el("span", { class: "drop-badge " + stCls, text: stText }))));
  // goals
  const goals = el("div", { class: "drop-goals" });
  goals.append(miniGoal(d.views, d.goal_views, d.pct_views, t("col.views"), "v"));
  goals.append(miniGoal(d.comments, d.goal_comments, d.pct_comments, t("col.replies"), "c"));
  card.append(goals);
  // interesting metrics
  card.append(el("div", { class: "drop-metrics" },
    el("span", { class: "dm", text: `⌀ ${d.avg_views} ${t("drop.per")}` }),
    el("span", { class: "dm", text: `${d.reply_rate}‰ reply` }),
    el("span", { class: "dm", text: `❤ ${fmtNum(d.likes)}` }),
    el("span", { class: "dm", text: `${d.posts ? d.posts.length : 0} ${t("stats.posts")}` })));
  // per-account
  if (d.per_account && d.per_account.length) {
    const pa = el("div", { class: "drop-pa" });
    d.per_account.forEach(a => pa.append(el("span", { class: "pa-chip", text: `${a.account}: ${fmtNum(a.views)}👁 ${a.comments}💬` })));
    card.append(pa);
  }
  // expandable per-post breakdown: ⏳ ждёт / ✅ вышел-ок / ⚠️ вышел-слабо
  if (d.posts && d.posts.length) {
    const share = d.goal_views && d.posts.length ? d.goal_views / d.posts.length : null;
    const isOk = p => p.published && (share ? p.views >= share : p.views >= (d.avg_views || 0));
    const okCount = d.posts.filter(isOk).length;
    const waitCount = d.posts.filter(p => !p.published).length;
    const weakCount = d.posts.length - okCount - waitCount;
    const open = expandedDrops.has(d.id);
    const toggle = el("button", { class: "drop-toggle" },
      el("span", { class: "dt-arrow", text: open ? "▾" : "▸" }),
      el("span", { text: tf("drop.posts", { n: d.posts.length }) }),
      el("span", { class: "dt-ok", text: `✅ ${okCount} · ⚠️ ${weakCount} · ⏳ ${waitCount}` }));
    const list = el("div", { class: "drop-posts" + (open ? "" : " hidden") });
    d.posts.forEach(p => {
      const mark = !p.published ? "⏳" : (isOk(p) ? "✅" : "⚠️");
      const cls = !p.published ? "wait" : (isOk(p) ? "ok" : "bad");
      list.append(el("div", { class: "dp " + cls },
        el("span", { class: "dp-mark", text: mark }),
        el("span", { class: "dp-when", text: p.published ? t("drop.sent") : (p.when || "") }),
        el("span", { class: "dp-acc", text: p.account }),
        el("span", { class: "dp-stat", text: `${fmtNum(p.views)}👁` }),
        el("span", { class: "dp-stat", text: `${p.replies}💬` }),
        el("span", { class: "dp-stat", text: `${p.likes}❤` }),
        el("span", { class: "dp-rr", text: `${p.reply_rate}‰` }),
        el("span", { class: "dp-text", title: p.text, text: p.text })));
    });
    toggle.addEventListener("click", () => {
      const hidden = list.classList.toggle("hidden");
      toggle.querySelector(".dt-arrow").textContent = hidden ? "▸" : "▾";
      if (hidden) expandedDrops.delete(d.id); else expandedDrops.add(d.id);
    });
    card.append(toggle, list);
  }
  // verdict (strategist)
  if (d.verdict) {
    card.append(el("div", { class: "drop-verdict" },
      el("div", { class: "dv-h", text: "🧠 " + t("drop.verdict") }),
      el("div", { class: "dv-t", text: d.verdict })));
  }
  // actions
  const acts = el("div", { class: "drop-acts" },
    el("button", { class: "mini ghost", text: t("drop.measure"), onclick: async () => {
      await jpost("/api/drops/" + d.id + "/measure", {}); loadDrops(); } }),
    el("button", { class: "mini", text: d.verdict ? t("drop.reeval") : t("drop.eval"), onclick: async (e) => {
      e.target.disabled = true; e.target.textContent = t("drop.evaluating");
      await jpost("/api/drops/" + d.id + "/evaluate", {});
      let tries = 0; const poll = setInterval(async () => {
        const fresh = await api("/api/drops/" + d.id);
        if ((fresh.verdict && fresh.verdict !== d.verdict) || ++tries > 30) { clearInterval(poll); loadDrops(); }
      }, 2500);
    } }));
  card.append(acts);
  return card;
}
async function loadDrops() {
  const wrap = $("#drops-list"); if (!wrap) return;
  const rows = await api("/api/drops");
  wrap.innerHTML = "";
  if (!rows.length) { wrap.append(emptyState(t("drop.empty"), t("drop.empty.hint"), "🚀")); return; }
  rows.filter(Boolean).forEach(d => wrap.append(renderDropCard(d)));
}
const dropRun = $("#drop-run");
if (dropRun) dropRun.addEventListener("click", async () => {
  dropRun.disabled = true; const lbl = dropRun.textContent; dropRun.textContent = t("drop.running");
  try {
    await jpost("/api/drops", {
      goal_views: Number($("#drop-gv").value) || 0,
      goal_comments: Number($("#drop-gc").value) || 0,
      count: Number($("#drop-count").value) || 3,
      when: $("#drop-when").value,
      max_chars: $("#drop-short").checked ? 170 : 480,
    });
    toast(t("drop.started"), "success");
    let tries = 0; const before = (await api("/api/drops")).length;
    const poll = setInterval(async () => {
      const rows = await api("/api/drops");
      if (rows.length > before || ++tries > 40) { clearInterval(poll); loadDrops(); dropRun.disabled = false; dropRun.textContent = lbl; }
    }, 3000);
  } catch (err) { toast(String(err), "error", 6000); dropRun.disabled = false; dropRun.textContent = lbl; }
});

// --- Hall of Legends -------------------------------------------------------
function legendMetric(v, l) { return el("div", { class: "lg-metric" }, el("b", { class: "lg-mv", text: fmtNum(v) }), el("span", { class: "lg-ml", text: l })); }
function renderLegendCard(lg, rank, hero) {
  const card = el("div", { class: "panel legend-card" + (hero ? " hero" : "") });
  card.append(el("div", { class: "lg-rank", text: hero ? "👑" : "#" + rank }));
  card.append(el("div", { class: "lg-head" },
    el("span", { class: "lg-acc", text: lg.account }),
    el("span", { class: "lg-when", text: lg.when })));
  card.append(el("div", { class: "lg-text", text: lg.text }));
  const m = el("div", { class: "lg-metrics" },
    legendMetric(lg.views, "👁 " + t("col.views")),
    legendMetric(lg.replies, "💬 " + t("col.replies")),
    legendMetric(lg.likes, "❤ " + t("col.likes")),
    el("div", { class: "lg-metric" }, el("b", { class: "lg-mv grad-t", text: lg.reply_rate + "‰" }), el("span", { class: "lg-ml", text: t("stats.rr") })));
  card.append(m);
  const noteBox = el("div", { class: "lg-note" });
  if (lg.note) noteBox.append(el("div", { class: "lg-note-h", text: "🧠 " + t("leg.why") }), el("div", { class: "lg-note-t", text: lg.note }));
  else noteBox.append(el("button", { class: "mini", text: t("leg.explain"), onclick: async (e) => {
    e.target.disabled = true; e.target.textContent = t("leg.thinking");
    await jpost("/api/legends/" + encodeURIComponent(lg.published_id) + "/analyze", {});
    let n = 0; const poll = setInterval(async () => {
      const rows = await api("/api/legends");
      const fresh = rows.find(x => x.published_id === lg.published_id);
      if ((fresh && fresh.note) || ++n > 30) { clearInterval(poll); loadLegends(); }
    }, 2500);
  } }));
  card.append(noteBox);
  return card;
}
async function loadLegends() {
  const wrap = $("#legends-list"); if (!wrap) return;
  const rows = await api("/api/legends");
  wrap.innerHTML = "";
  if (!rows.length) { wrap.append(emptyState(t("leg.empty"), t("leg.empty.hint"), "🏆")); return; }
  rows.forEach((lg, i) => wrap.append(renderLegendCard(lg, i + 1, i === 0)));
}

// --- Daily close-out -------------------------------------------------------
function dayHeading(dateStr) {
  const d = new Date(dateStr + "T12:00:00");
  const today = new Date(), yst = new Date(); yst.setDate(today.getDate() - 1);
  const same = (a, b) => a.toDateString() === b.toDateString();
  if (same(d, today)) return t("day.today");
  if (same(d, yst)) return t("day.yesterday");
  return d.toLocaleDateString(window.I18N.lang, { weekday: "long", day: "numeric", month: "long" });
}
function dayTile(value, label, unit, dec) {
  const num = el("b", { text: "0" });
  countUp(num, value, { dec: dec || 0 });
  return el("div", { class: "dt-tile" }, el("div", { class: "dt-v" }, num, unit ? el("span", { class: "dt-u", text: unit }) : el("span")), el("div", { class: "dt-l", text: label }));
}
function renderDayCard(day) {
  const card = el("div", { class: "panel day-card" + (day.is_today ? " today" : "") });
  card.append(el("div", { class: "day-head" },
    el("div", {}, el("div", { class: "day-title", text: dayHeading(day.date) }),
      el("div", { class: "day-date", text: day.date })),
    day.is_today ? el("span", { class: "day-live", text: t("daily.inprogress") }) : el("span", { class: "day-done", text: t("daily.closed") })));
  card.append(el("div", { class: "day-tiles" },
    dayTile(day.views, t("col.views")), dayTile(day.comments, t("col.replies")),
    dayTile(day.likes, t("col.likes")), dayTile(day.posts, t("daily.posts")),
    dayTile(day.reply_rate, t("stats.rr"), "‰", 1), dayTile(day.warmup, t("daily.warmup"))));
  // drops of the day
  if (day.drops && day.drops.length) {
    const dr = el("div", { class: "day-drops" });
    day.drops.forEach(d => dr.append(el("div", { class: "day-drop " + (d.met ? "met" : "notmet") },
      el("span", { class: "dd-badge", text: d.met ? "✓" : "•" }),
      el("span", { class: "dd-label", text: d.label }),
      el("span", { class: "dd-res", text: `${fmtNum(d.views)}/${fmtNum(d.goal_views)}👁 · ${d.comments}/${d.goal_comments}💬` }))));
    card.append(el("div", { class: "day-section-l", text: t("daily.drops") }), dr);
  }
  // per-account
  if (day.per_account && day.per_account.length) {
    const pa = el("div", { class: "day-pa" });
    day.per_account.forEach(a => pa.append(el("span", { class: "pa-chip", text: `${a.account}: ${fmtNum(a.views)}👁 ${a.comments}💬 · ${a.posts}п` })));
    card.append(el("div", { class: "day-section-l", text: t("daily.byacc") }), pa);
  }
  // best post
  if (day.best) {
    card.append(el("div", { class: "day-best" },
      el("span", { class: "db-h", text: "★ " + t("daily.best") }),
      el("span", { class: "db-m", text: `${fmtNum(day.best.views)}👁 ${day.best.replies}💬 ${day.best.account}` }),
      el("span", { class: "db-t", text: day.best.text })));
  }
  return card;
}
async function loadDaily() {
  const wrap = $("#daily-list"); if (!wrap) return;
  const rows = await api("/api/daily");
  wrap.innerHTML = "";
  if (!rows.length) { wrap.append(emptyState(t("daily.empty"), t("daily.empty.hint"), "📅")); return; }
  rows.forEach(d => wrap.append(renderDayCard(d)));
}

// --- Studio (AI planner + editable post cards + saved prompts) -------------
async function loadStudio() {
  const rows = await api("/api/accounts");
  const sel = $("#studio-account"); sel.innerHTML = "";
  rows.forEach(r => sel.append(el("option", { value: String(r.id), text: r.name + (r.handle ? ` (${r.handle})` : "") })));
  loadStudioPrompts();
  loadStudioQuota();
  if (!$("#studio-cards").children.length) $("#studio-cards").innerHTML = `<div class="empty">${t("studio.empty")}</div>`;
}
async function loadStudioQuota() {
  const wrap = $("#studio-quota"); if (!wrap) return;
  try { const u = await api("/api/ai/usage");
    wrap.className = "ai-quota" + (u.remaining_today <= 0 ? " danger" : (u.remaining_minute <= 0 ? " warn" : ""));
    wrap.textContent = `${t("ai.quota")}: ${u.used_today}/${u.rpd} · ${u.used_minute}/${u.rpm}`;
  } catch {}
}
async function loadStudioPrompts() {
  const wrap = $("#studio-prompts");
  const rows = await api("/api/prompts");
  if (!rows.length) { wrap.innerHTML = `<div class="empty">${t("studio.no_prompts")}</div>`; return; }
  wrap.innerHTML = "";
  rows.forEach(p => {
    const item = el("div", { class: "prompt-item" },
      el("div", { class: "pi-body", onclick: () => { $("#studio-prompt").value = p.text; toast(t("studio.loaded"), "info", 1500); } },
        el("div", { class: "pi-name", text: p.name }),
        el("div", { class: "pi-text", text: p.text })),
      el("button", { class: "mini danger", text: "✕", onclick: async () => { await api("/api/prompts/" + p.id, { method: "DELETE" }); loadStudioPrompts(); } }));
    wrap.append(item);
  });
}
// editable "brief" card (before generation)
function studioBriefCard(text) {
  const card = el("div", { class: "post-card" });
  const ta = el("textarea", { class: "pc-text", rows: 2 }); ta.value = text || "";
  card.append(el("div", { class: "pc-head" }, el("span", { class: "pc-tag", text: t("studio.brief") }),
    el("button", { class: "mini danger", text: "✕", onclick: () => card.remove() })));
  card.append(ta);
  return card;
}
function renderPlanCards(briefs) {
  const wrap = $("#studio-cards"); wrap.innerHTML = "";
  briefs.forEach(b => wrap.append(studioBriefCard(b)));
  const bar = el("div", { class: "form-actions", style: "margin-top:12px" },
    el("button", { id: "studio-create", text: t("studio.create"), onclick: createStudioTasks }));
  wrap.append(bar);
}
$("#studio-add")?.addEventListener("click", () => {
  const wrap = $("#studio-cards");
  if (wrap.querySelector(".empty")) wrap.innerHTML = "";
  let bar = wrap.querySelector(".form-actions");
  const card = studioBriefCard("");
  if (bar) wrap.insertBefore(card, bar);
  else { wrap.append(card); wrap.append(el("div", { class: "form-actions", style: "margin-top:12px" }, el("button", { id: "studio-create", text: t("studio.create"), onclick: createStudioTasks }))); }
});
$("#studio-plan")?.addEventListener("click", async () => {
  const prompt = $("#studio-prompt").value.trim();
  if (!prompt) { toast(t("studio.need_prompt"), "warn"); return; }
  const btn = $("#studio-plan"); const label = btn.textContent; btn.disabled = true; btn.textContent = "…";
  try {
    const res = await jpost("/api/plan", { prompt });
    if (!res.briefs || !res.briefs.length) { toast(t("studio.no_plan"), "warn"); }
    else { renderPlanCards(res.briefs); toast(tf("studio.planned", { n: res.briefs.length }), "success"); }
  } catch (err) { toast(String(err), "error", 6000); }
  btn.disabled = false; btn.textContent = label; loadStudioQuota();
});
$("#studio-save-prompt")?.addEventListener("click", async () => {
  const text = $("#studio-prompt").value.trim();
  if (!text) { toast(t("studio.need_prompt"), "warn"); return; }
  const nameEl = el("input", { class: "modal-input", placeholder: t("studio.prompt_name") });
  const ok = await modal({ title: t("studio.save"), body: nameEl, actions: [
    { label: t("btn.cancel"), value: false }, { label: t("btn.ok"), value: true, class: "grad" }] });
  if (!ok || !nameEl.value.trim()) return;
  await jpost("/api/prompts", { name: nameEl.value.trim(), text });
  toast(t("studio.saved_ok"), "success"); loadStudioPrompts();
});
const seedViral = $("#seed-viral");
if (seedViral) seedViral.addEventListener("click", async () => {
  seedViral.disabled = true;
  try {
    const r = await jpost("/api/prompts/seed-viral", { lang: (window.I18N && window.I18N.lang) || "ru" });
    toast(tf("studio.seeded", { n: r.created }), "success");
    loadStudioPrompts();
  } catch (err) { toast(String(err), "error", 6000); }
  seedViral.disabled = false;
});
async function createStudioTasks() {
  const briefs = $$("#studio-cards .pc-text").map(t => t.value.trim()).filter(Boolean).slice(0, 10);
  if (!briefs.length) { toast(t("studio.need_briefs"), "warn"); return; }
  const data = {
    account_id: $("#studio-account").value ? Number($("#studio-account").value) : null,
    briefs, language: $("#studio-lang").value, style: $("#studio-style").value,
    interval_minutes: Number($("#studio-interval").value) || 90,
    start_at: $("#studio-start").value || null,
    max_chars: $("#studio-length").value ? Number($("#studio-length").value) : null,
  };
  const wrap = $("#studio-cards");
  let res; try { res = await jpost("/api/batches", data); }
  catch (err) { toast(String(err), "error"); return; }
  const total = res.tasks.length;
  wrap.innerHTML = `<div class="hint">${tf("batch.generating", { d: 0, n: total })}</div>`;
  const poll = setInterval(async () => {
    let b; try { b = await api("/api/batches/" + res.batch_id); } catch { return; }
    const drafted = (b.status && b.status.drafted) || 0;
    if (b.generating) wrap.innerHTML = `<div class="hint">${tf("batch.generating", { d: drafted, n: total })}</div>`;
    else { clearInterval(poll); renderDraftCards(res.batch_id, b.tasks); toast(tf("batch.drafted", { n: drafted }), "success"); loadStudioQuota(); }
  }, 1500);
}
// --- autopilot agent -------------------------------------------------------
const AGENT_TOOL_LABEL = { list_accounts: "📋", get_analytics: "📊", create_tasks: "✍" };
$("#agent-run")?.addEventListener("click", async () => {
  const instruction = $("#agent-instruction").value.trim();
  if (!instruction) { toast(t("agent.need"), "warn"); return; }
  const btn = $("#agent-run"); const label = btn.textContent; btn.disabled = true; btn.textContent = "…";
  const out = $("#agent-output"); out.innerHTML = `<div class="hint">${t("agent.working")}</div>`;
  try {
    const res = await jpost("/api/agent", { instruction, account_id: $("#studio-account").value ? Number($("#studio-account").value) : null });
    out.innerHTML = "";
    (res.steps || []).forEach(s => {
      const line = el("div", { class: "agent-step" },
        el("span", { class: "as-ic", text: AGENT_TOOL_LABEL[s.tool] || "•" }),
        el("span", { class: "as-name", text: s.tool }),
        el("span", { class: "as-res", text: s.result && s.result.created != null ? `+${s.result.created}` : "" }));
      out.append(line);
    });
    out.append(el("div", { class: "agent-answer", text: res.answer || "" }));
    toast(t("agent.done"), "success");
    loadTasks();
  } catch (err) { out.innerHTML = ""; toast(String(err), "error", 6000); }
  btn.disabled = false; btn.textContent = label; loadStudioQuota();
});

// editable draft card (after generation) — save via PATCH
function studioDraftCard(tk) {
  const card = el("div", { class: "post-card" });
  const max = tk.max_chars || 500;
  const head = el("div", { class: "pc-head" },
    el("span", { class: "pc-when", text: "🕒 " + fmtDate(tk.scheduled_for) }),
    el("span", { class: "pc-count" }));
  const ta = el("textarea", { class: "pc-text", rows: 4 }); ta.value = tk.result || tk.payload || "";
  const counter = head.querySelector(".pc-count");
  const upd = () => { counter.textContent = `${ta.value.length}/${max}`; counter.className = "pc-count" + (ta.value.length > max ? " over" : ""); };
  ta.addEventListener("input", upd); upd();
  const foot = el("div", { class: "pc-foot" },
    el("button", { class: "mini", text: t("btn.save"), onclick: async () => { await api("/api/tasks/" + tk.id, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ result: ta.value }) }); toast(t("msg.saved"), "success", 1500); } }),
    el("button", { class: "mini danger", text: "✕", onclick: async () => { await api("/api/tasks/" + tk.id, { method: "DELETE" }); card.remove(); } }));
  card.append(head, ta, foot);
  return card;
}
function renderDraftCards(batchId, tasks) {
  const wrap = $("#studio-cards"); wrap.innerHTML = "";
  tasks.forEach(tk => wrap.append(studioDraftCard(tk)));
  wrap.append(el("div", { class: "form-actions", style: "margin-top:12px" },
    el("button", { class: "ok", text: t("batch.approve"), onclick: async () => {
      const r = await jpost(`/api/batches/${batchId}/approve`);
      toast(tf("batch.scheduled", { n: r.scheduled }), "success");
      wrap.innerHTML = `<div class="empty">${t("studio.done")}</div>`; loadTasks();
    }})));
}

// --- content calendar (month view, phone-style) ----------------------------
function localKey(d) { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; }
let calMonth = (() => { const d = new Date(); d.setDate(1); d.setHours(0, 0, 0, 0); return d; })();
// A post lands on the day it went out (published) or, if still planned, its scheduled day.
function taskDayKey(tk) {
  if (tk.published_id && tk.updated_at) return tk.updated_at.slice(0, 10);
  if (tk.scheduled_for) return tk.scheduled_for.slice(0, 10);
  return null;
}
function taskTime(tk) { return ((tk.published_id ? tk.updated_at : tk.scheduled_for) || "").slice(11, 16); }
function taskIcon(tk) { return tk.published_id ? "✅" : (["failed", "error"].includes(tk.status) ? "⚠️" : "⏳"); }
function cleanPost(tk) { return (tk.result || tk.title || "—").replace(/^\[published[^\]]*\]\s*/, "").replace(/\n/g, " "); }
async function loadCalendar() {
  const rows = await api("/api/tasks");
  const posts = rows.filter(tk => (tk.kind || "post") === "post");
  const byDay = {};
  posts.forEach(tk => { const k = taskDayKey(tk); if (k) (byDay[k] = byDay[k] || []).push(tk); });

  const ctrl = $("#cal-controls"); ctrl.innerHTML = "";
  const mName = calMonth.toLocaleDateString(window.I18N.lang, { month: "long", year: "numeric" });
  const title = el("h2", { text: mName.charAt(0).toUpperCase() + mName.slice(1) });
  const nav = el("div", { class: "cal-nav" },
    el("button", { class: "mini ghost", text: "‹", onclick: () => { calMonth.setMonth(calMonth.getMonth() - 1); loadCalendar(); } }),
    el("button", { class: "mini ghost", text: t("cal.today"), onclick: () => { calMonth = new Date(); calMonth.setDate(1); calMonth.setHours(0, 0, 0, 0); loadCalendar(); } }),
    el("button", { class: "mini ghost", text: "›", onclick: () => { calMonth.setMonth(calMonth.getMonth() + 1); loadCalendar(); } }));
  ctrl.append(title, nav);

  const grid = el("div", { class: "cal-month" });
  const dayNames = window.I18N.lang === "en"
    ? ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] : ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
  dayNames.forEach(n => grid.append(el("div", { class: "cal-dow", text: n })));

  const y = calMonth.getFullYear(), mo = calMonth.getMonth();
  const startOffset = (new Date(y, mo, 1).getDay() + 6) % 7; // Monday = 0
  const daysInMonth = new Date(y, mo + 1, 0).getDate();
  const todayKey = localKey(new Date());
  for (let i = 0; i < startOffset; i++) grid.append(el("div", { class: "cal-cell empty" }));
  for (let day = 1; day <= daysInMonth; day++) {
    const date = new Date(y, mo, day), key = localKey(date);
    const items = (byDay[key] || []).sort((a, b) => taskTime(a).localeCompare(taskTime(b)));
    const published = items.filter(tk => tk.published_id).length;
    const planned = items.length - published;
    const cell = el("div", { class: "cal-cell" + (key === todayKey ? " today" : "") + (items.length ? " has" : "") });
    cell.append(el("div", { class: "cal-cell-num", text: String(day) }));
    if (items.length) {
      cell.onclick = () => showDayModal(date, items);
      const badges = el("div", { class: "cal-badges" });
      if (published) badges.append(el("span", { class: "cal-b pub", text: `✅ ${published}` }));
      if (planned) badges.append(el("span", { class: "cal-b plan", text: `⏳ ${planned}` }));
      cell.append(badges);
      const tv = items.reduce((s, tk) => s + (tk.views || 0), 0);
      if (tv) cell.append(el("div", { class: "cal-cell-views", text: `${fmtNum(tv)} 👁` }));
    }
    grid.append(cell);
  }
  $("#calendar").innerHTML = "";
  $("#calendar").append(grid, el("div", { class: "cal-hint", text: t("cal.tapday") }));
}
function showDayModal(date, items) {
  const published = items.filter(tk => tk.published_id).length;
  const totV = items.reduce((s, tk) => s + (tk.views || 0), 0);
  const totL = items.reduce((s, tk) => s + (tk.likes || 0), 0);
  const body = el("div", { class: "day-modal" });
  body.append(el("div", { class: "dm-sum" },
    el("b", { text: `${published}/${items.length} ${t("cal.out")}` }),
    el("span", { text: totV ? ` · ${fmtNum(totV)} 👁 · ${totL} ❤` : "" })));
  items.forEach(tk => {
    const row = el("div", { class: "dm-post " + (tk.published_id ? "pub" : tk.status), onclick: () => showTaskModal(tk) });
    row.append(el("span", { class: "dm-ic", text: taskIcon(tk) }));
    row.append(el("span", { class: "dm-time", text: taskTime(tk) }));
    const main = el("div", { class: "dm-main" });
    main.append(el("div", { class: "dm-ttl", text: cleanPost(tk).slice(0, 90) }));
    if (tk.published_id) main.append(el("div", { class: "dm-metrics", text: `${fmtNum(tk.views || 0)} 👁 · ${tk.likes || 0} ❤ · ${tk.replies || 0} 💬` }));
    row.append(main);
    body.append(row);
  });
  const dName = date.toLocaleDateString(window.I18N.lang, { day: "numeric", month: "long" });
  modal({ title: dName, body, actions: [{ label: t("btn.close"), value: true }] });
}
function showTaskModal(tk) {
  const body = el("div", {});
  const when = tk.published_id ? tk.updated_at : tk.scheduled_for;
  body.append(el("div", { class: "tm-meta", text: `${taskIcon(tk)} ${t("status." + tk.status)} · ${fmtDate(when)}` }));
  if (tk.published_id) body.append(el("div", { class: "tm-meta", text: `${fmtNum(tk.views || 0)} 👁 · ${tk.likes || 0} ❤ · ${tk.replies || 0} 💬` }));
  body.append(el("pre", { class: "modal-pre", text: cleanPost(tk) }));
  modal({ title: tk.title || "—", body, actions: [{ label: t("btn.close"), value: true }] });
}

// --- statistics ------------------------------------------------------------
// --- deep statistics -------------------------------------------------------
const STAT_COLORS = ["#ff2e7e", "#b14bff", "#22d3c5", "#3b82f6", "#ffb020"];
let statsData = null;
function fmtNum(v) { return Math.round(v).toLocaleString("ru-RU"); }
function countUp(node, target, { dur = 950, dec = 0 } = {}) {
  const fmt = v => dec ? v.toFixed(dec) : fmtNum(v);
  if (window.__liveRefresh) { node.textContent = fmt(target); return; }  // instant on live refresh
  const start = performance.now();
  (function tick(now) {
    const p = Math.min(1, (now - start) / dur), e = 1 - Math.pow(1 - p, 3);
    node.textContent = fmt(target * e);
    if (p < 1) requestAnimationFrame(tick); else node.textContent = fmt(target);
  })(start);
}
function progressRing(pct, { size = 118, stroke = 11, c1 = "#b14bff", c2 = "#22d3c5" } = {}) {
  pct = Math.max(0, Math.min(100, pct));
  const r = (size - stroke) / 2, circ = 2 * Math.PI * r, off = circ * (1 - pct / 100);
  const gid = "rg" + Math.random().toString(36).slice(2, 7);
  const s = svg("svg", { viewBox: `0 0 ${size} ${size}`, width: size, height: size, class: "ring" });
  const defs = svg("defs", {}), g = svg("linearGradient", { id: gid, x1: 0, y1: 0, x2: 1, y2: 1 });
  g.append(svg("stop", { offset: "0%", "stop-color": c1 }), svg("stop", { offset: "100%", "stop-color": c2 }));
  defs.append(g); s.append(defs);
  s.append(svg("circle", { cx: size / 2, cy: size / 2, r, fill: "none", stroke: "rgba(255,255,255,.08)", "stroke-width": stroke }));
  const fg = svg("circle", { cx: size / 2, cy: size / 2, r, fill: "none", stroke: `url(#${gid})`, "stroke-width": stroke, "stroke-linecap": "round", "stroke-dasharray": circ.toFixed(1), "stroke-dashoffset": circ.toFixed(1), transform: `rotate(-90 ${size / 2} ${size / 2})` });
  s.append(fg);
  if (window.__liveRefresh) { fg.setAttribute("stroke-dashoffset", off.toFixed(1)); }
  else { fg.style.transition = "stroke-dashoffset 1.1s cubic-bezier(.4,0,.2,1)"; setTimeout(() => fg.setAttribute("stroke-dashoffset", off.toFixed(1)), 70); }
  return s;
}
function goalCard({ title, cur, target, unit, sub, c1, c2 }) {
  const pct = target ? Math.round(cur / target * 100) : 0;
  const card = el("div", { class: "panel goal-card" });
  const pctNum = el("b", { text: "0" });
  const ringWrap = el("div", { class: "ring-wrap" }, progressRing(pct, { c1, c2 }),
    el("div", { class: "ring-pct" }, pctNum, el("span", { text: "%" })));
  const curNum = el("b", { text: "0" });
  const info = el("div", { class: "goal-info" },
    el("div", { class: "goal-title", text: title }),
    el("div", { class: "goal-big" }, curNum, el("span", { class: "goal-of", text: " / " + fmtNum(target) + (unit ? " " + unit : "") })),
    sub ? el("div", { class: "goal-sub", text: sub }) : el("span"));
  card.append(ringWrap, info);
  countUp(pctNum, pct); countUp(curNum, cur);
  return card;
}
// Deltas: было X → +Δ → стало Y, per priority metric, for a selectable period.
let deltaPeriod = "today";
let deltaData = null;
const DELTA_PERIODS = [
  { k: "today", lab: "delta.today" },
  { k: "yesterday", lab: "delta.yesterday" },
  { k: "week", lab: "delta.week" },
];
function renderDeltas(dx) {
  if (dx) deltaData = dx;
  const box = $("#stats-deltas"); if (!box) return;
  // period toggle
  const tabs = $("#stats-delta-tabs");
  if (tabs) {
    tabs.innerHTML = "";
    DELTA_PERIODS.forEach(p => tabs.append(el("button", {
      class: "delta-tab" + (p.k === deltaPeriod ? " active" : ""),
      text: t(p.lab),
      onclick: () => { deltaPeriod = p.k; renderDeltas(); },
    })));
  }
  box.innerHTML = "";
  const P = (deltaData && deltaData[deltaPeriod]) || { metrics: {}, posts: 0 };
  const m = P.metrics || {};
  const defs = [
    { k: "followers", lab: t("dg.followers"), icon: "➕", c: "#ff2e7e" },
    { k: "likes", lab: t("dg.likes"), icon: "❤", c: "#b14bff" },
    { k: "clicks", lab: t("dg.clicks"), icon: "🔗", c: "#22d3c5" },
    { k: "profile_views", lab: t("stats.profileviews"), icon: "👁", c: "#3b82f6" },
  ];
  const suffix = deltaPeriod === "week" ? t("delta.7d") : t("delta.day");
  defs.forEach(def => {
    const v = m[def.k] || { cur: 0, prev: 0, delta: 0 };
    const up = v.delta > 0, flat = v.delta === 0;
    const card = el("div", { class: "delta-card" });
    card.style.setProperty("--dc", def.c);
    card.append(el("div", { class: "delta-top" },
      el("span", { class: "delta-ic", text: def.icon }),
      el("span", { class: "delta-lab", text: def.lab })));
    card.append(el("div", { class: "delta-now", text: fmtNum(v.cur) }));
    card.append(el("div", { class: "delta-chg " + (up ? "up" : flat ? "flat" : "down") },
      el("span", { class: "delta-was", text: `${fmtNum(v.prev)} →` }),
      el("b", { text: `${up ? "+" : ""}${fmtNum(v.delta)}` })));
    box.append(card);
  });
  const card = el("div", { class: "delta-card" });
  card.style.setProperty("--dc", "#ffb020");
  card.append(el("div", { class: "delta-top" },
    el("span", { class: "delta-ic", text: "🚀" }),
    el("span", { class: "delta-lab", text: t("stats.posts") })));
  card.append(el("div", { class: "delta-now", text: fmtNum(P.posts || 0) }));
  card.append(el("div", { class: "delta-chg " + ((P.posts || 0) ? "up" : "flat") },
    el("b", { text: suffix })));
  box.append(card);
}
function statTile({ label, value, unit, sub, accent, dec }) {
  const num = el("span", { class: "m-num", text: "0" });
  const card = el("div", { class: "metric" + (accent ? " accent" : "") },
    el("div", { class: "m-k", text: label }),
    el("div", { class: "m-v" }, num, unit ? el("span", { class: "m-unit", text: unit }) : el("span")),
    el("div", { class: "m-sub", text: sub || "" }));
  countUp(num, value, { dec: dec || 0 });
  return card;
}
function accountStatCard(a, color) {
  const card = el("div", { class: "panel acct-card" });
  card.style.setProperty("--ac", color);
  card.append(el("div", { class: "acct-head" },
    el("span", { class: "acct-h", text: a.handle || a.name }),
    el("span", { class: "acct-state " + a.state, text: t("state." + a.state) })));
  const m = (v, l, dec) => { const n = el("b", { text: "0" }); countUp(n, v, { dec: dec || 0 }); return el("div", { class: "am" }, n, el("span", { class: "am-l", text: l })); };
  card.append(el("div", { class: "acct-metrics" },
    m(a.views, t("col.views")), m(a.replies, t("col.replies")),
    m(a.reply_rate, "‰", 1), m(a.likes, t("col.likes")), m(a.published, t("stats.posts"))));
  if (a.spark && a.spark.length) card.append(el("div", { class: "acct-spark" }, barChart(a.spark, 320, 42)));
  if (a.best) card.append(el("div", { class: "acct-best", title: a.best, text: "★ " + a.best }));
  return card;
}
function accMap() { return Object.fromEntries((statsData?.accounts || []).map(a => [a.id, a.handle])); }
function renderStatsTop() {
  const wrap = $("#stats-top"); if (!wrap || !statsData) return;
  const by = ($("#stats-top-by") && $("#stats-top-by").value) || "views";
  const rows = [...statsData.top].sort((x, y) => (y[by] || 0) - (x[by] || 0));
  const map = accMap();
  wrap.innerHTML = "";
  if (!rows.length) { wrap.append(emptyState(t("top.empty"), t("top.empty.hint"), "🏆")); return; }
  const list = el("ol", { class: "top-list" });
  rows.forEach(r => list.append(el("li", { class: "top-item" },
    el("span", { class: "top-metric" }, el("b", { text: fmtNum(r.views) }), el("span", { class: "tm-l", text: "👁" })),
    el("span", { class: "top-metric" }, el("b", { text: String(r.replies) }), el("span", { class: "tm-l", text: "💬" })),
    el("span", { class: "top-rr", text: r.reply_rate + "‰" }),
    el("span", { class: "top-acc", text: map[r.account_id] || "" }),
    el("span", { class: "top-text", title: r.text, text: r.text }))));
  wrap.append(list);
}
async function loadStats() {
  const d = await api("/api/stats/full"); statsData = d;
  const kpi = await api("/api/kpi").catch(() => null);
  // goals
  const g = $("#stats-goals"); g.innerHTML = "";
  g.append(
    goalCard({ title: t("goal.main.views"), cur: d.goal.views, target: d.goal.target_views, unit: t("unit.views"), sub: t("goal.window"), c1: "#b14bff", c2: "#22d3c5" }),
    goalCard({ title: t("goal.main.comments"), cur: d.goal.comments, target: d.goal.target_comments, unit: t("unit.comments"), sub: t("goal.window"), c1: "#ff2e7e", c2: "#b14bff" }),
  );
  if (d.day) {
    g.append(goalCard({ title: t("goal.drop"), cur: d.day.views, target: d.day.target_views || 1, unit: t("unit.views"), sub: `💬 ${d.day.comments}/${d.day.target_comments} · ❤ ${d.day.likes} · ${d.day.posts} ${t("stats.posts")}`, c1: "#22d3c5", c2: "#3b82f6" }));
  }
  renderDeltas(d.deltas);
  // overall
  const o = $("#stats-overall"); o.innerHTML = "";
  o.append(
    statTile({ label: t("stats.postviews"), value: d.overall.views, accent: true }),
    statTile({ label: t("stats.profileviews"), value: (kpi && kpi.totals) ? kpi.totals.profile_views : 0 }),
    statTile({ label: t("col.replies"), value: d.overall.replies }),
    statTile({ label: t("col.likes"), value: d.overall.likes }),
    statTile({ label: t("stats.rr"), value: d.overall.avg_reply_rate, unit: "‰", dec: 1 }),
    statTile({ label: t("stats.clicks"), value: (kpi && kpi.totals) ? kpi.totals.clicks : 0 }),
    statTile({ label: t("stats.published"), value: d.overall.published }),
    statTile({ label: t("stats.accounts"), value: d.overall.accounts }),
  );
  // trend
  const tr = $("#stats-trend"); tr.innerHTML = "";
  const vals = d.trend.map(x => x.views);
  if (vals.some(v => v > 0)) tr.append(areaChart(vals, { color: "#22d3c5", h: 150 }));
  else tr.append(el("div", { class: "empty", text: t("stats.notrend") }));
  // per-account
  const ac = $("#stats-accounts"); ac.innerHTML = "";
  d.accounts.forEach((a, i) => ac.append(accountStatCard(a, STAT_COLORS[i % STAT_COLORS.length])));
  // top + legend
  renderStatsTop();
  renderStatsLegend(d.accounts);
}
function renderStatsLegend(accounts) {
  const wrap = $("#stats-legend"); if (!wrap) return;
  wrap.innerHTML = "";
  const states = el("div", { class: "leg-row" },
    el("span", { class: "leg-chip warm", text: t("state.warm") + " — " + t("legend.warm") }),
    el("span", { class: "leg-chip warming", text: t("state.warming") + " — " + t("legend.warming") }),
    el("span", { class: "leg-chip cold", text: t("state.cold") + " — " + t("legend.cold") }));
  const metrics = el("div", { class: "leg-metrics", text: t("legend.metrics") });
  wrap.append(states, metrics);
}
const statsTopBy = $("#stats-top-by");
if (statsTopBy) statsTopBy.addEventListener("change", renderStatsTop);
async function loadDashGoals() {
  const g = $("#dash-goals"); if (!g) return;
  try {
    const d = await api("/api/stats/full");
    g.innerHTML = "";
    g.append(
      goalCard({ title: t("goal.main.views"), cur: d.goal.views, target: d.goal.target_views, unit: t("unit.views"), sub: t("goal.window"), c1: "#b14bff", c2: "#22d3c5" }),
      goalCard({ title: t("goal.main.comments"), cur: d.goal.comments, target: d.goal.target_comments, unit: t("unit.comments"), sub: t("goal.window"), c1: "#ff2e7e", c2: "#b14bff" }),
    );
    renderDailyGoal(d.daily_goal);
  } catch (e) { g.innerHTML = ""; }
}
// Daily stability goal: what we must hit EVERY day to grow (follows/likes/clicks).
function renderDailyGoal(dg) {
  const box = $("#daily-goal"); if (!box) return;
  if (!dg) { box.innerHTML = ""; return; }
  const defs = [
    { k: "followers", lab: t("dg.followers"), icon: "➕", c: "#ff2e7e" },
    { k: "likes", lab: t("dg.likes"), icon: "❤", c: "#b14bff" },
    { k: "clicks", lab: t("dg.clicks"), icon: "🔗", c: "#22d3c5" },
  ];
  const hit = defs.every(x => (dg[x.k] || {}).pct >= 100);
  box.innerHTML = "";
  box.append(el("div", { class: "dg-head" },
    el("span", { class: "dg-title", text: t("dg.title") }),
    el("span", { class: "dg-badge " + (hit ? "hit" : ""), text: hit ? t("dg.done") : t("dg.push") })));
  const row = el("div", { class: "dg-metrics" });
  defs.forEach(def => {
    const m = dg[def.k] || { cur: 0, target: 0, pct: 0 };
    const cell = el("div", { class: "dg-cell" });
    cell.append(el("div", { class: "dg-cell-top" },
      el("span", { class: "dg-ic", text: def.icon }),
      el("b", { class: "dg-cur", text: `${fmtNum(m.cur)} / ${fmtNum(m.target)}` }),
      el("span", { class: "dg-lab", text: def.lab })));
    const track = el("div", { class: "dg-bar" });
    const fill = el("div", { class: "dg-fill" });
    fill.style.background = `linear-gradient(90deg, ${def.c}, ${def.c}99)`;
    if (window.__liveRefresh) fill.style.width = m.pct + "%";
    else setTimeout(() => { fill.style.width = m.pct + "%"; }, 60);
    track.append(fill); cell.append(track); row.append(cell);
  });
  box.append(row);
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
  loadTopPosts();
  loadContentInsights();
  loadFeedSamples();
  loadCycle(full);
  if (full) renderPicker(a.accounts);
}

// -- daily strategy cycle ---------------------------------------------------
let cycleLoaded = false;
async function loadCycle(full) {
  if (!$("#cyc-runs")) return;
  // Only (re)fill the controls on a full load so typing isn't overwritten.
  if (full || !cycleLoaded) {
    const cfg = await api("/api/strategy/settings");
    if (!isTyping()) {
      $("#cyc-enabled").checked = !!cfg.enabled;
      $("#cyc-hour").value = cfg.hour; $("#cyc-count").value = cfg.count;
      $("#cyc-interval").value = cfg.interval_minutes; $("#cyc-lang").value = cfg.language || "Ukrainian";
      if ($("#cyc-goalv")) $("#cyc-goalv").value = cfg.goal_views;
      if ($("#cyc-goalc")) $("#cyc-goalc").value = cfg.goal_comments;
      if ($("#cyc-niche")) $("#cyc-niche").value = cfg.niche || "";
      if ($("#cyc-rules")) $("#cyc-rules").value = cfg.rules || "";
    }
    renderStrategistLessons(cfg);
    renderGoalBar(cfg);
    const sel = $("#cyc-account");
    if (sel && !sel.children.length) {
      const accs = await api("/api/accounts");
      sel.innerHTML = `<option value="">${t("cyc.all_accounts")}</option>` +
        accs.map(a => `<option value="${a.id}">${a.name}</option>`).join("");
      sel.value = cfg.account_id || "";
    }
    cycleLoaded = true;
  }
  renderBrain();
  renderCycleRuns(await api("/api/strategy/runs"));
}
async function renderBrain() {
  const wrap = $("#cyc-brain"); if (!wrap) return;
  try {
    const b = await api("/api/brain");
    const nice = p => p === "openai" ? "GPT" : (p === "gemini" ? "Gemini" : p);
    const chain = arr => (arr || []).map(nice).join(" → ");
    const model = b.strategist_model && b.strategist_model !== "gemini" ? ` · ${b.strategist_model}` : "";
    wrap.innerHTML =
      `<span class="chip ${b.openai ? "good" : ""}">🧠 ${t("brain.strategist")}: ${chain(b.strategist)}${model}</span>` +
      `<span class="chip">✍️ ${t("brain.writer")}: ${chain(b.writer)}</span>` +
      (b.openai ? "" : `<span class="brain-hint">${t("brain.addkey")}</span>`);
  } catch (e) { wrap.innerHTML = ""; }
}
function renderCycleRuns(runs) {
  const wrap = $("#cyc-runs"); if (!wrap) return;
  wrap.innerHTML = "";
  if (!runs.length) { wrap.append(emptyState(t("cyc.empty"), t("cyc.empty.hint"), "🕒")); return; }
  runs.slice(0, 8).forEach(r => {
    const card = el("div", { class: "cyc-run " + r.status });
    const head = el("div", { class: "cyc-run-head" },
      el("span", { class: "cyc-badge " + r.status, text: t("cyc.st." + r.status) || r.status }),
      el("span", { class: "cyc-when", text: (r.trigger === "daily" ? "⏰ " : "▶ ") + (r.created_at || "").replace("T", " ") }),
      el("span", { class: "cyc-count-badge", text: tf("cyc.made", { n: r.tasks_created }) }));
    card.append(head);
    if (r.thesis) card.append(el("div", { class: "cyc-thesis", text: r.thesis }));
    if (r.analysis) card.append(el("div", { class: "cyc-analysis", text: r.analysis }));
    if (r.error) card.append(el("div", { class: "cyc-error", text: "⚠ " + r.error }));
    // Planned posts (archetype · audience · theme)
    let plan = null; try { plan = JSON.parse(r.plan_json || "null"); } catch (e) {}
    if (plan && plan.posts && plan.posts.length) {
      const ol = el("ol", { class: "cyc-posts" });
      plan.posts.forEach(p => ol.append(el("li", {},
        el("span", { class: "cyc-tag", text: [p.archetype, p.audience, p.theme].filter(Boolean).join(" · ") }),
        el("span", { class: "cyc-brief", text: p.brief || "" }))));
      card.append(ol);
    }
    wrap.append(card);
  });
}
function renderGoalBar(cfg) {
  const wrap = $("#cyc-goalbar"); if (!wrap) return;
  const p = cfg.progress || { views: 0, replies: 0 };
  const gv = cfg.goal_views || 0, gc = cfg.goal_comments || 0;
  const pctV = gv ? Math.min(100, Math.round(p.views / gv * 100)) : 0;
  const pctC = gc ? Math.min(100, Math.round(p.replies / gc * 100)) : 0;
  wrap.innerHTML = "";
  const bar = (label, cur, goal, pct, cls) => {
    const row = el("div", { class: "goal-row" });
    row.append(el("div", { class: "goal-lbl", text: `${label}: ${cur.toLocaleString()} / ${goal.toLocaleString()} (${pct}%)` }));
    const track = el("div", { class: "goal-track" });
    const fill = el("div", { class: "goal-fill " + cls }); fill.style.width = pct + "%";
    track.append(fill); row.append(track); return row;
  };
  wrap.append(bar(t("cyc.g.views"), p.views, gv, pctV, "v"));
  wrap.append(bar(t("cyc.g.comments"), p.replies, gc, pctC, "c"));
}
const cycRulesSave = $("#cyc-rules-save");
if (cycRulesSave) cycRulesSave.addEventListener("click", async () => {
  await jpost("/api/strategy/settings", { rules: $("#cyc-rules").value });
  toast(t("cyc.rules.saved"), "success");
});
function renderStrategistLessons(cfg) {
  const body = $("#cyc-lessons-body"); if (!body) return;
  const age = $("#cyc-lessons-age");
  if (age) age.textContent = cfg.lessons_age == null ? t("cyc.lessons.never") : t("cyc.lessons.self").replace("{ago}", fmtAgo(cfg.lessons_age));
  const txt = (cfg.lessons || "").trim();
  if (!txt) { body.innerHTML = `<div class="cyc-lessons-empty">${t("cyc.lessons.empty")}</div>`; return; }
  body.innerHTML = "";
  txt.split(/\n+/).filter(l => l.trim()).forEach(l =>
    body.append(el("div", { class: "cyc-lesson", text: l.trim() })));
}
const cycEvolve = $("#cyc-evolve");
if (cycEvolve) cycEvolve.addEventListener("click", async () => {
  cycEvolve.disabled = true; const lbl = cycEvolve.textContent; cycEvolve.textContent = t("cyc.evolving");
  try {
    await jpost("/api/strategy/evolve", {});
    toast(t("cyc.evolved"), "success", 4000);
    setTimeout(async () => { const cfg = await api("/api/strategy/settings"); renderStrategistLessons(cfg); cycEvolve.disabled = false; cycEvolve.textContent = lbl; }, 6000);
  } catch { cycEvolve.disabled = false; cycEvolve.textContent = lbl; }
});
const cycSave = $("#cyc-save");
if (cycSave) cycSave.addEventListener("click", async () => {
  const cfg = await jpost("/api/strategy/settings", {
    enabled: $("#cyc-enabled").checked, hour: Number($("#cyc-hour").value),
    count: Number($("#cyc-count").value), interval_minutes: Number($("#cyc-interval").value),
    language: $("#cyc-lang").value.trim(), account_id: $("#cyc-account").value || "",
    goal_views: Number($("#cyc-goalv").value), goal_comments: Number($("#cyc-goalc").value),
    niche: $("#cyc-niche").value.trim(),
  });
  renderGoalBar(cfg);
  toast(t("cyc.saved"), "success");
});
const cycRun = $("#cyc-run");
if (cycRun) cycRun.addEventListener("click", async () => {
  cycRun.disabled = true; const lbl = cycRun.textContent; cycRun.textContent = t("cyc.running");
  try {
    await jpost("/api/strategy/run", {
      count: Number($("#cyc-count").value) || undefined,
      language: $("#cyc-lang").value.trim() || undefined,
      interval_minutes: Number($("#cyc-interval").value) || undefined,
      account_id: $("#cyc-account").value || "",
    });
    toast(t("cyc.started"), "success");
    // Poll for the fresh run (LLM plan + drafts take a bit).
    let tries = 0;
    const before = (await api("/api/strategy/runs")).length;
    const poll = setInterval(async () => {
      const runs = await api("/api/strategy/runs");
      renderCycleRuns(runs);
      if (runs.length > before || ++tries > 40) {
        clearInterval(poll); cycRun.disabled = false; cycRun.textContent = lbl;
        if (runs.length > before) { toast(t("cyc.done"), "success"); loadTasks(); }
      }
    }, 3000);
  } catch (err) { toast(String(err), "error", 6000); cycRun.disabled = false; cycRun.textContent = lbl; }
});
function postText(r) {
  let s = (r.result || r.title || "").replace(/^\[published [^\]]*\]\s*/, "");
  return s.length > 80 ? s.slice(0, 80) + "…" : s;
}
async function loadTopPosts() {
  const wrap = $("#top-posts"); if (!wrap) return;
  const by = ($("#top-by") && $("#top-by").value) || "views";
  const rows = await api("/api/analytics/top-posts?by=" + by + "&limit=10");
  if (!rows.length) { wrap.innerHTML = ""; wrap.append(emptyState(t("top.empty"), t("top.empty.hint"), "📈")); return; }
  const table = el("table");
  table.append(headRow(["col.post", "col.views", "col.likes", "col.replies"]));
  const tbody = el("tbody");
  rows.forEach(r => tbody.append(el("tr", {},
    el("td", { class: "top-text", title: (r.result || "").replace(/^\[published [^\]]*\]\s*/, ""), text: postText(r) }),
    td(String(r.views)), td(String(r.likes)), td(String(r.replies)))));
  table.append(tbody); wrap.innerHTML = ""; wrap.append(table);
}
const topBy = $("#top-by"); if (topBy) topBy.addEventListener("change", loadTopPosts);
const topRefresh = $("#top-refresh");
if (topRefresh) topRefresh.addEventListener("click", async () => {
  topRefresh.disabled = true; const lbl = topRefresh.textContent; topRefresh.textContent = "…";
  try {
    const r = await jpost("/api/threads/refresh-insights");
    toast(tf("top.updated", { n: r.updated }), "success");
    loadTopPosts();
  } catch (err) { toast(String(err), "error", 6000); }
  topRefresh.disabled = false; topRefresh.textContent = lbl;
});
async function loadContentInsights() {
  const wrap = $("#content-insights"); if (!wrap) return;
  let d;
  try { d = await api("/api/analytics/content-insights"); }
  catch (err) { wrap.innerHTML = ""; wrap.append(emptyState(String(err), "", "🧠")); return; }
  wrap.innerHTML = "";
  if (!d.sample_count) {
    wrap.append(emptyState(t("insights.empty"), t("insights.empty.hint"), "🧠"));
    return;
  }
  // Measured patterns as chips.
  const chips = el("div", { class: "insight-chips" });
  const p = d.patterns || {};
  const bestLen = Object.entries(p.by_length || {})
    .filter(([, v]) => v.avg_views != null)
    .sort((a, b) => b[1].avg_views - a[1].avg_views)[0];
  if (bestLen) chips.append(el("span", { class: "chip", text: tf("insights.length", { b: bestLen[0], v: bestLen[1].avg_views }) }));
  const q = p.question || {};
  if (q.with_avg_views != null && q.without_avg_views != null)
    chips.append(el("span", { class: "chip " + (q.with_avg_views >= q.without_avg_views ? "good" : "bad"),
      text: tf("insights.question", { a: q.with_avg_views, b: q.without_avg_views }) }));
  const g = p.greeting || {};
  if (g.with_avg_views != null && g.without_avg_views != null)
    chips.append(el("span", { class: "chip " + (g.without_avg_views > g.with_avg_views ? "bad" : "good"),
      text: tf("insights.greeting", { a: g.with_avg_views, b: g.without_avg_views }) }));
  if (p.avg_reply_rate != null)
    chips.append(el("span", { class: "chip", text: tf("insights.replyrate", { v: p.avg_reply_rate }) }));
  wrap.append(chips);
  // Reply drivers first — replies are the reach engine on Threads.
  const drivers = d.reply_drivers && d.reply_drivers.length ? d.reply_drivers : (d.top || []);
  wrap.append(el("div", { class: "insight-label", text: t("insights.drivers") }));
  const dlist = el("ol", { class: "insight-top" });
  drivers.forEach(x => dlist.append(el("li", {},
    el("span", { class: "insight-metric", text: `${x.reply_rate}‰ · ${x.replies}💬/${x.views}👁` }),
    el("span", { class: "insight-snippet", text: x.text }))));
  wrap.append(dlist);
  // Top by reach.
  wrap.append(el("div", { class: "insight-label", text: t("insights.reach") }));
  const list = el("ol", { class: "insight-top" });
  (d.top || []).forEach(x => list.append(el("li", {},
    el("span", { class: "insight-metric", text: `${x.views}👁 ${x.likes}❤ ${x.replies}💬` }),
    el("span", { class: "insight-snippet", text: x.text }))));
  wrap.append(list);
}
const insRefresh = $("#insights-refresh");
if (insRefresh) insRefresh.addEventListener("click", loadContentInsights);

// -- feed / competitor intelligence -----------------------------------------
function parseFeedLine(line) {
  // "text | views likes replies"  or  "text" — trailing numbers optional.
  const parts = line.split("|");
  const text = parts[0].trim();
  if (!text) return null;
  const nums = (parts[1] || "").trim().split(/\s+/).map(Number).filter(n => !isNaN(n));
  return { text, views: nums[0] || 0, likes: nums[1] || 0, replies: nums[2] || 0 };
}
async function loadFeedSamples() {
  const wrap = $("#feed-samples"); if (!wrap) return;
  const rows = await api("/api/feed/samples");
  wrap.innerHTML = "";
  if (!rows.length) { wrap.append(emptyState(t("feed.empty"), t("feed.empty.hint"), "🕵️")); return; }
  const table = el("table");
  table.append(headRow(["col.post", "col.views", "col.likes", "col.replies", ""]));
  const tbody = el("tbody");
  rows.forEach(r => tbody.append(el("tr", {},
    el("td", { class: "top-text", title: r.text, text: r.text.length > 70 ? r.text.slice(0, 70) + "…" : r.text }),
    td(String(r.views)), td(String(r.likes)), td(String(r.replies)),
    el("td", {}, el("button", { class: "mini danger", text: "✕", onclick: async () => {
      await api("/api/feed/samples/" + r.id, { method: "DELETE" }); loadFeedSamples(); } })))));
  table.append(tbody); wrap.append(table);
}
const feedAdd = $("#feed-add");
if (feedAdd) feedAdd.addEventListener("click", async () => {
  const examples = ($("#feed-input").value || "").split("\n").map(parseFeedLine).filter(Boolean);
  if (!examples.length) { toast(t("feed.need"), "warn"); return; }
  const r = await jpost("/api/feed/samples", { examples });
  toast(tf("feed.added", { n: r.added }), "success");
  $("#feed-input").value = ""; loadFeedSamples();
});
const feedSearch = $("#feed-search");
if (feedSearch) feedSearch.addEventListener("click", async () => {
  const keyword = ($("#feed-keyword").value || "").trim();
  if (!keyword) { toast(t("feed.need_kw"), "warn"); return; }
  feedSearch.disabled = true;
  try {
    const r = await jpost("/api/feed/search", { keyword });
    if (r.available === false) toast(t("feed.unavailable"), "warn", 7000);
    else { toast(tf("feed.found", { n: r.stored_new }), "success"); loadFeedSamples(); }
  } catch (err) { toast(String(err), "error", 6000); }
  feedSearch.disabled = false;
});
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

// True while the user is actively editing a field — background refreshes must
// never re-render underneath a focused input (that is what wiped typed text).
function isTyping() {
  const a = document.activeElement;
  return a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.isContentEditable);
}
// Real-time refresh while on the Analytics tab (paused whenever you are typing).
setInterval(() => {
  if (currentTab === "analytics" && $("#analytics-auto") && $("#analytics-auto").checked && !isTyping())
    loadAnalytics(false);
}, 4000);

// Real-time: keep the current data tab live (metrics climb on their own, server
// auto-refreshes insights). Numbers update instantly (no re-count) on live pulses.
setInterval(async () => {
  if (isTyping()) return;
  const live = { dashboard: loadDashGoals, stats: loadStats, daily: loadDaily, drops: loadDrops, followups: loadFollowups, kpi: loadKPI };
  const fn = live[currentTab];
  if (!fn) return;
  window.__liveRefresh = true;
  try { await fn(); } catch (e) { /* ignore */ } finally { window.__liveRefresh = false; }
}, 12000);

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

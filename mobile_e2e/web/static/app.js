"use strict";

const form = document.getElementById("workflow-form");
const logEl = document.getElementById("log");
const statusEl = document.getElementById("status");
const runBtn = document.getElementById("run");
const badge = document.getElementById("result-badge");
const proxyPreview = document.getElementById("proxy-preview");

let polling = null;

// --- Live server-health indicator ------------------------------------------
const liveEl = document.getElementById("live");
async function pingHealth() {
  try {
    const res = await fetch("/api/health", { cache: "no-store" });
    const ok = res.ok && (await res.json()).status === "ok";
    liveEl.className = "live " + (ok ? "online" : "offline");
    liveEl.textContent = ok ? "● live" : "● offline";
  } catch {
    liveEl.className = "live offline";
    liveEl.textContent = "● offline";
  }
}
pingHealth();
setInterval(pingHealth, 3000);

function setStatus(text, cls) {
  statusEl.textContent = text;
  statusEl.className = "status " + cls;
}

function appendLog(line) {
  logEl.textContent += line + "\n";
  logEl.scrollTop = logEl.scrollHeight;
}

function setLog(lines) {
  logEl.textContent = lines.join("\n") + (lines.length ? "\n" : "");
  logEl.scrollTop = logEl.scrollHeight;
}

function formData() {
  const data = {};
  new FormData(form).forEach((v, k) => { data[k] = v; });
  return data;
}

// --- Preview proxy ---------------------------------------------------------
document.getElementById("preview-proxy").addEventListener("click", async () => {
  proxyPreview.className = "hint";
  proxyPreview.textContent = "…";
  try {
    const res = await fetch("/api/preview-proxy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ proxy_string: formData().proxy_string || "" }),
    });
    const body = await res.json();
    if (res.ok) {
      proxyPreview.textContent = body.proxy;
    } else {
      proxyPreview.className = "hint error";
      proxyPreview.textContent = body.error || "Invalid proxy";
    }
  } catch (e) {
    proxyPreview.className = "hint error";
    proxyPreview.textContent = "Request failed: " + e;
  }
});

document.getElementById("clear-log").addEventListener("click", () => {
  logEl.textContent = "";
  badge.textContent = "";
  badge.className = "";
});

// --- Run workflow ----------------------------------------------------------
form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  if (polling) return;

  badge.textContent = "";
  badge.className = "";
  runBtn.disabled = true;
  setStatus("Running…", "running");
  appendLog("--- starting workflow ---");

  let jobId;
  try {
    const res = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(formData()),
    });
    const body = await res.json();
    if (!res.ok) {
      appendLog("Validation error: " + (body.error || res.status));
      setStatus("Idle", "idle");
      runBtn.disabled = false;
      return;
    }
    jobId = body.job_id;
  } catch (e) {
    appendLog("Request failed: " + e);
    setStatus("Idle", "idle");
    runBtn.disabled = false;
    return;
  }

  // Poll the job until it finishes.
  polling = setInterval(async () => {
    try {
      const res = await fetch("/api/jobs/" + jobId);
      const job = await res.json();
      setLog(["--- starting workflow ---", ...job.logs]);

      if (job.status === "done" || job.status === "error") {
        clearInterval(polling);
        polling = null;
        runBtn.disabled = false;

        if (job.status === "error") {
          setStatus("Error", "error");
          badge.className = "err";
          badge.textContent = job.error || "failed";
        } else {
          const r = job.result || {};
          if (r.ok) {
            setStatus("Done", "done");
            badge.className = "ok";
            badge.textContent = "OK — response: " + JSON.stringify(r.response);
          } else {
            setStatus("Failed", "error");
            badge.className = "err";
            badge.textContent = r.error || "workflow failed";
          }
        }
      }
    } catch (e) {
      clearInterval(polling);
      polling = null;
      runBtn.disabled = false;
      setStatus("Error", "error");
      appendLog("Polling failed: " + e);
    }
  }, 700);
});

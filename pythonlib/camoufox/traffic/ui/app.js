"use strict";

// ---- UI wiring ------------------------------------------------------------
const $ = (id) => document.getElementById(id);

const tabs = document.querySelectorAll(".tab");
tabs.forEach((t) =>
  t.addEventListener("click", () => {
    tabs.forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    document.querySelectorAll(".proxy-panel").forEach((p) => p.classList.remove("active"));
    $("panel-" + t.dataset.tab).classList.add("active");
  })
);

function setState(running) {
  $("start").disabled = running;
  $("stop").disabled = !running;
  $("runState").className = "dot " + (running ? "running" : $("g-launched").textContent*1 > 0 ? "stopped" : "");
}

$("start").addEventListener("click", startRun);
$("stop").addEventListener("click", stopRun);

async function startRun() {
  const body = {
    landing: $("landing").value.trim(),
    visitors: Number($("visitors").value),
    duration_hours: Number($("duration").value),
    max_concurrent: Number($("concurrent").value),
    gateway: $("gateway").value.trim(),
    proxy_file: $("proxy_file").value.trim(),
    proxy: $("proxy").value.trim(),
  };
  $("error").textContent = "";
  try {
    const res = await fetch("/api/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!data.ok) {
      $("error").textContent = data.error || "Failed to start.";
    } else {
      fetchState();
    }
  } catch (e) {
    $("error").textContent = "Network error: " + e.message;
  }
}

async function stopRun() {
  try {
    await fetch("/api/stop", { method: "POST" });
  } catch (e) { /* ignore */ }
}

// ---- live stats -----------------------------------------------------------
function renderTimeline(stats) {
  const planned = stats.total_planned || 0;
  const hours = stats.window_hours || 24;
  // we can't know true arrival histogram retroactively; approximate progress
  const bins = 24;
  const el = $("timeline");
  el.innerHTML = "";
  const done = (stats.completed||0) + (stats.failed||0) + (stats.cancelled||0);
  const frac = planned ? done / planned : 0;
  for (let i = 0; i < bins; i++) {
    const b = document.createElement("div");
    b.className = "bar";
    // show a sine-ish day curve as a backdrop, raised by actual progress
    const curve = 0.25 + 0.75 * Math.pow(Math.sin((i / bins) * Math.PI * 2 - 1), 2);
    b.style.height = Math.max(4, curve * 60 * (i < Math.floor(frac*bins) ? 1 : 0.45)) + "px";
    b.title = `hour ${i}: shape of expected human traffic`;
    el.appendChild(b);
  }
}

function renderChannels(stats) {
  const channels = stats.channels || {};
  const list = $("channelList");
  list.innerHTML = "";
  const labels = { direct: "Direct", search: "Search engine", social: "Social", referral: "Referral" };
  Object.entries(channels).forEach(([k, v]) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.innerHTML = `<b>${v}</b>${labels[k] || k}`;
    list.appendChild(chip);
  });
  if (!Object.keys(channels).length) {
    list.innerHTML = '<span class="chip" style="opacity:.6">No visitors yet</span>';
  }
}

function renderMeta(stats) {
  $("m-duration").textContent = stats.duration_hours ? `${stats.duration_hours} h` : "—";
  $("m-conc").textContent = stats.max_concurrent || "—";
  if (stats.dwell_seconds && stats.completed) {
    $("m-dwell").textContent = (stats.dwell_seconds / stats.completed).toFixed(0) + " s";
  } else {
    $("m-dwell").textContent = "—";
  }
  const p = stats.proxy_mode || "none";
  $("m-proxy").textContent = p;
}

function render(stats) {
  if (!stats || typeof stats !== "object") return;
  $("g-launched").textContent = stats.total_planned || stats.launched || 0;
  $("g-active").textContent = stats.active || 0;
  $("g-done").textContent = stats.completed || 0;
  $("g-failed").textContent = stats.failed || 0;
  $("g-pages").textContent = stats.total_pages || 0;
  if ("landing" in stats) $("windowLabel").textContent = `→ ${stats.landing}`;
  renderTimeline(stats);
  renderChannels(stats);
  renderMeta(stats);
  setState(!!stats.running);
}

// ---- fetch initial state, then subscribe to SSE ---------------------------
async function fetchState() {
  try {
    const res = await fetch("/api/state");
    render(await res.json());
  } catch (e) { /* server may be starting */ }
}

function connectSSE() {
  const es = new EventSource("/api/events");
  es.onmessage = (ev) => {
    try { render(JSON.parse(ev.data)); } catch (e) { /* ignore */ }
  };
  es.onerror = () => {
    es.close();
    setTimeout(connectSSE, 3000); // reconnect
  };
}

fetchState();
connectSSE();
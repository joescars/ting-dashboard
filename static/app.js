"use strict";
(() => {
  const $ = (id) => document.getElementById(id);
  const NOMINAL = { lo: 114, hi: 126 };
  const LIVE_WINDOW = 300; // seconds

  const st = {
    snap: null,
    station: localStorage.getItem("ting.station") || null,
    range: localStorage.getItem("ting.range") || "24h",
    recent: [],
    history: null,
    arrivals: [],
    drawQueued: false,
  };

  // ---------------------------------------------------------------- theme
  const THEMES = ["auto", "light", "dark"];
  const THEME_ICONS = {
    auto: ["M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18zm0-2a7 7 0 1 0 0-14 7 7 0 0 0 0 14z", "M12 5a7 7 0 0 1 0 14z"],
    light: ["M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10zM11 1h2v3h-2zm0 19h2v3h-2zM3.5 4.9l1.4-1.4 2.1 2.1-1.4 1.4zm13.1 13.1 1.4-1.4 2.1 2.1-1.4 1.4zM1 11h3v2H1zm19 0h3v2h-3zM3.5 19.1l2.1-2.1 1.4 1.4-2.1 2.1zM16.6 6l2.1-2.1 1.4 1.4L18 7.4z"],
    dark: ["M12.3 2a10 10 0 1 0 9.7 12.4A8 8 0 0 1 12.3 2z"],
  };
  function applyTheme(t) {
    if (t === "auto") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = t;
    const g = $("theme-icon");
    g.replaceChildren(...THEME_ICONS[t].map((d) => {
      const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
      p.setAttribute("d", d);
      return p;
    }));
    $("theme-btn").setAttribute("aria-label", `Theme: ${t}`);
    $("theme-btn").title = `Theme: ${t}`;
    requestDraw();
    if (st.history) drawHistory();
  }
  let theme = localStorage.getItem("ting.theme") || "auto";
  $("theme-btn").addEventListener("click", () => {
    theme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];
    localStorage.setItem("ting.theme", theme);
    applyTheme(theme);
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => applyTheme(theme));

  // ---------------------------------------------------------------- helpers
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const fmtV = (v) => (v == null || !isFinite(v) ? "—" : v.toFixed(1));
  const ago = (sec) => {
    if (sec == null || !isFinite(sec)) return "—";
    if (sec < 2) return "just now";
    if (sec < 60) return `${Math.round(sec)}s ago`;
    if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
    if (sec < 86400) return `${Math.round(sec / 3600)}h ago`;
    return `${Math.round(sec / 86400)}d ago`;
  };
  const fmtDate = (v) => {
    if (v == null) return "—";
    const d = typeof v === "number" ? new Date(v * 1000) : new Date(v);
    return isNaN(d) ? String(v) : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  };
  const severity = (level) => {
    const s = String(level || "").toLowerCase();
    if (!s) return "info";
    if (/(high|sever|crit|danger|alert)/.test(s)) return "err";
    if (/(mod|med|elev|warn)/.test(s)) return "warn";
    if (/(low|none|ok|normal|no hazard)/.test(s)) return "ok";
    return "info";
  };
  const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  async function getJSON(url) {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error(`${url} → HTTP ${r.status}`);
    return r.json();
  }
  const device = () => st.snap?.devices?.find((d) => d.serial === st.station) || null;

  // ---------------------------------------------------------------- icons
  const ICON = {
    fire: "M13.5.7s.7 2.6.7 4.8c0 2-1.3 3.7-3.4 3.7-2 0-3.5-1.7-3.5-3.7V5.2A13 13 0 0 0 4 13a8 8 0 0 0 16 0C20 7.6 17.4 3 13.5.7zM11.7 19c-1.8 0-3.2-1.4-3.2-3.1 0-1.6 1-2.8 2.8-3.1s3.6-1.2 4.6-2.6c.4 1.3.6 2.6.6 4 0 2.6-2.1 4.8-4.8 4.8z",
    bolt: "M7 2v11h3v9l7-12h-4l4-8z",
    pole: "M11 2h2v3h5v2h-5v3h3v2h-3v10h-2V12H8v-2h3V7H6V5h5z",
    wave: "M2 12c2-6 4-6 6 0s4 6 6 0 4-6 6 0l2-.5c-2.4-7.7-6.6-7.7-9 0-1.7 5.2-2.3 5.2-4 0C6.6 3.8 2.4 3.8 0 11.5z",
    snow: "M22 11h-4.2l3.2-3.2-1.4-1.4L15 11h-2V9l4.6-4.6-1.4-1.4L13 6.2V2h-2v4.2L7.8 3 6.4 4.4 11 9v2H9L4.4 6.4 3 7.8 6.2 11H2v2h4.2L3 16.2l1.4 1.4L9 13h2v2l-4.6 4.6L7.8 21l3.2-3.2V22h2v-4.2l3.2 3.2 1.4-1.4L13 15v-2h2l4.6 4.6 1.4-1.4-3.2-3.2H22z",
    school: "M12 3 1 9l11 6 9-4.9V17h2V9zM5 13.2v4L12 21l7-3.8v-4L12 17z",
    live: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM4.9 4.9 6.3 6.3a8 8 0 0 0 0 11.4l-1.4 1.4a10 10 0 0 1 0-14.2zm14.2 0a10 10 0 0 1 0 14.2l-1.4-1.4a8 8 0 0 0 0-11.4z",
    cloud: "M19.4 10A7.5 7.5 0 0 0 5.4 8 6 6 0 0 0 6 20h13a5 5 0 0 0 .4-10z",
  };
  const svgIcon = (d) => {
    const ns = "http://www.w3.org/2000/svg";
    const s = document.createElementNS(ns, "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("aria-hidden", "true");
    const p = document.createElementNS(ns, "path");
    p.setAttribute("d", d);
    s.appendChild(p);
    return s;
  };

  // ---------------------------------------------------------------- render state
  function renderState() {
    const s = st.snap;
    if (!s) return;
    const devices = s.devices || [];
    if (!devices.some((d) => d.serial === st.station)) {
      const prev = st.station;
      st.station = devices[0]?.serial || null;
      if (st.station !== prev) onStationChange();
    }
    // device picker
    const sel = $("device-select");
    $("device-picker").hidden = devices.length < 2;
    const want = devices.map((d) => d.serial).join("|");
    if (sel.dataset.v !== want) {
      sel.replaceChildren(...devices.map((d) => {
        const o = el("option", null, d.name);
        o.value = d.serial;
        return o;
      }));
      sel.dataset.v = want;
    }
    if (st.station) sel.value = st.station;

    const d = device();
    $("site-name").textContent = d ? [d.name, d.site_name].filter(Boolean).join(" · ") : "";
    document.title = d ? `Ting · ${d.name}` : "Ting Dashboard";

    renderConn();
    renderBanner();
    renderTiles();
    renderDevice();
  }

  function streamState() {
    return st.snap?.streams?.[st.station] || null;
  }

  function renderConn() {
    const chip = $("conn-chip");
    const label = chip.querySelector(".chip-label");
    const s = st.snap;
    const ss = streamState();
    const lastT = st.recent.length ? st.recent[st.recent.length - 1].t : null;
    const age = lastT ? Date.now() / 1000 - lastT : null;
    let cls = "", text = "Connecting…";
    if (!sse.ok) { cls = "err"; text = "Dashboard offline"; }
    else if (s?.auth?.state === "signed_out") { cls = "err"; text = "Signed out"; }
    else if (ss?.state === "live" && age != null && age < 10) { cls = "ok"; text = s.demo ? "Live · demo" : "Live"; }
    else if (ss?.state === "reconnecting") { cls = "warn"; text = "Reconnecting"; }
    else if (ss?.state === "live") { cls = "warn"; text = "Waiting for data"; }
    chip.className = `chip ${cls}`;
    label.textContent = text;
  }

  function renderBanner() {
    const s = st.snap;
    const b = $("banner");
    let kind = null, title = "", text = "";
    if (!sse.ok) { kind = "err"; title = "Lost connection to the dashboard server"; text = "Retrying automatically…"; }
    else if (s.demo) { kind = "info"; title = "Demo mode — synthetic data"; text = "Values are generated, not from your sensor. Unset TING_DEMO and sign in to see real data."; }
    else if (s.auth.state === "signed_out") { kind = "err"; title = "Not signed in to Ting"; text = s.auth.message || ""; }
    else if (s.poll_error) { kind = "warn"; title = "Ting cloud unavailable"; text = `${s.poll_error}. Showing last known status from ${ago(s.server_time - s.last_poll)}.`; }
    else if (streamState()?.state === "reconnecting") { kind = "warn"; title = "Live voltage stream reconnecting"; text = streamState().reason || ""; }
    b.hidden = !kind;
    if (kind) {
      b.className = `banner ${kind}`;
      $("banner-title").textContent = title;
      $("banner-text").textContent = text;
    }
  }

  function tile(icon, label, value, detail, cls) {
    const t = el("div", `tile ${cls}`);
    const top = el("div", "tile-top");
    const ic = el("div", "tile-icon");
    ic.appendChild(svgIcon(icon));
    top.append(ic, el("div", "tile-label", label));
    t.append(top, el("div", "tile-value", value));
    if (detail) t.append(el("div", "tile-detail", detail));
    return t;
  }

  function renderTiles() {
    const g = $("status-grid");
    const d = device();
    const s = st.snap;
    if (!d) {
      g.replaceChildren();
      return;
    }
    const ss = streamState();
    const pollAge = s.last_poll ? s.server_time - s.last_poll : null;
    g.replaceChildren(
      tile(ICON.fire, "Fire alert", d.is_fire ? "FIRE DETECTED" : "None", d.hazard_message, d.is_fire ? "err" : "ok"),
      tile(ICON.bolt, "Electrical fire hazard", d.efh.level || "—", d.efh.message, severity(d.efh.level)),
      tile(ICON.pole, "Utility fire hazard", d.ufh.level || "—", d.ufh.message, severity(d.ufh.level)),
      tile(ICON.wave, "Power quality", d.power_quality_hazard ? "Hazard" : "Normal", null, d.power_quality_hazard ? "warn" : "ok"),
      tile(ICON.snow, "Frozen pipe risk", d.has_frozen_pipe ? "At risk" : "No risk", null, d.has_frozen_pipe ? "warn" : "ok"),
      tile(ICON.school, "Learning mode", d.learning_mode ? "Learning" : "Complete",
        d.learning_mode ? "Ting is still learning your home" : null, d.learning_mode ? "info" : "ok"),
      tile(ICON.live, "Voltage stream",
        ss?.state === "live" ? "Connected" : ss?.state === "reconnecting" ? "Reconnecting" : "Connecting",
        ss?.reason || (ss?.since ? `since ${fmtDate(ss.since)}` : null),
        ss?.state === "live" ? "ok" : "warn"),
      tile(ICON.cloud, "Cloud status", s.poll_error ? "Error" : "OK",
        s.poll_error || `Updated ${ago(pollAge)}`, s.poll_error ? "warn" : "ok"),
    );
  }

  function renderDevice() {
    const d = device();
    const rows = d ? [
      ["Name", d.name], ["Serial", d.serial], ["Model", d.type], ["Firmware", d.firmware],
      ["Wi-Fi MAC", d.wifi_mac], ["Site", d.site_name],
      ["Subscription start", d.subscription_start ? fmtDate(d.subscription_start) : null],
      ["Account", st.snap.account?.email || st.snap.account?.first_name],
    ] : [];
    $("device-info").replaceChildren(...rows.filter((r) => r[1]).map(([k, v]) => {
      const w = el("div");
      w.append(el("dt", null, k), el("dd", null, String(v)));
      return w;
    }));
  }

  // ---------------------------------------------------------------- live
  function onSample(p) {
    if (p.st !== st.station) return;
    st.recent.push(p);
    const cutoff = p.t - LIVE_WINDOW;
    while (st.recent.length && st.recent[0].t < cutoff) st.recent.shift();
    const now = performance.now();
    st.arrivals.push(now);
    while (st.arrivals.length && now - st.arrivals[0] > 10000) st.arrivals.shift();
    renderHero();
    requestDraw();
  }

  function renderHero() {
    const p = st.recent[st.recent.length - 1];
    $("live-v").textContent = fmtV(p?.v);
    $("live-hi").textContent = p ? `${fmtV(p.hi)} V` : "—";
    $("live-lo").textContent = p ? `${fmtV(p.lo)} V` : "—";
    const rate = st.arrivals.length > 1
      ? (st.arrivals.length - 1) / ((st.arrivals[st.arrivals.length - 1] - st.arrivals[0]) / 1000) : null;
    $("live-rate").textContent = rate ? `${rate.toFixed(1)}/s` : "—";
    if (p) {
      const pct = Math.max(0, Math.min(100, ((p.v - 110) / 20) * 100));
      $("range-marker").style.left = `${pct}%`;
      $("range-note").textContent = p.v < NOMINAL.lo ? "Below ANSI C84.1 range A (low voltage)"
        : p.v > NOMINAL.hi ? "Above ANSI C84.1 range A (high voltage)" : "Within normal service range";
    }
  }

  setInterval(() => {
    const p = st.recent[st.recent.length - 1];
    $("live-age").textContent = p ? `Updated ${ago(Date.now() / 1000 - p.t)}` : "Waiting for data";
    renderConn();
  }, 1000);

  function requestDraw() {
    if (st.drawQueued) return;
    st.drawQueued = true;
    requestAnimationFrame(() => {
      st.drawQueued = false;
      const end = Date.now() / 1000;
      drawChart($("live-chart"), {
        x0: end - LIVE_WINDOW, x1: end,
        line: st.recent.map((p) => [p.t, p.v]),
        band: st.recent.map((p) => [p.t, p.lo, p.hi]),
        gapAfter: 5,
        tick: (t, step) => new Date(t * 1000).toLocaleTimeString([], step < 60
          ? { hour: "numeric", minute: "2-digit", second: "2-digit" } : { hour: "numeric", minute: "2-digit" }),
      });
    });
  }

  // ---------------------------------------------------------------- chart
  function drawChart(canvas, o) {
    const dpr = window.devicePixelRatio || 1;
    const W = canvas.clientWidth, H = canvas.clientHeight;
    if (!W || !H) return;
    if (canvas.width !== Math.round(W * dpr) || canvas.height !== Math.round(H * dpr)) {
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
    }
    const c = canvas.getContext("2d");
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, W, H);
    const font = getComputedStyle(document.body).fontFamily;
    c.font = `12px ${font}`;
    const pad = { l: 44, r: 8, t: 8, b: 24 };
    const pw = W - pad.l - pad.r, ph = H - pad.t - pad.b;

    let lo = Infinity, hi = -Infinity;
    for (const [, a, b] of o.band) { lo = Math.min(lo, a); hi = Math.max(hi, b); }
    for (const [, v] of o.line) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    if (!isFinite(lo)) { lo = 115; hi = 125; }
    const span = Math.max(hi - lo, 4);
    const mid = (hi + lo) / 2;
    lo = Math.floor(mid - span * 0.65); hi = Math.ceil(mid + span * 0.65);

    const X = (t) => pad.l + ((t - o.x0) / (o.x1 - o.x0)) * pw;
    const Y = (v) => pad.t + (1 - (v - lo) / (hi - lo)) * ph;

    // shade outside the ANSI range A band (only visible when voltage strays)
    c.fillStyle = cssVar("--chart-out");
    const yHi = Y(NOMINAL.hi), yLo = Y(NOMINAL.lo);
    if (yHi > pad.t) c.fillRect(pad.l, pad.t, pw, Math.min(yHi, pad.t + ph) - pad.t);
    if (yLo < pad.t + ph) c.fillRect(pad.l, Math.max(yLo, pad.t), pw, pad.t + ph - Math.max(yLo, pad.t));

    // grid + y labels
    c.strokeStyle = cssVar("--chart-grid");
    c.fillStyle = cssVar("--on-surface-variant");
    c.lineWidth = 1;
    c.textAlign = "right"; c.textBaseline = "middle";
    const ystep = niceStep((hi - lo) / 5);
    for (let v = Math.ceil(lo / ystep) * ystep; v <= hi; v += ystep) {
      const y = Math.round(Y(v)) + 0.5;
      c.beginPath(); c.moveTo(pad.l, y); c.lineTo(W - pad.r, y); c.stroke();
      c.fillText(`${+v.toFixed(1)}`, pad.l - 8, y);
    }
    // x labels on aligned, human-friendly intervals
    c.textAlign = "center"; c.textBaseline = "top";
    const maxTicks = Math.max(2, Math.floor(pw / 90));
    const STEPS = [15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 604800];
    const xstep = STEPS.find((s) => (o.x1 - o.x0) / s <= maxTicks) || 604800;
    const tz = new Date().getTimezoneOffset() * 60;
    const align = xstep >= 3600 ? tz : 0;
    for (let t = Math.ceil((o.x0 - align) / xstep) * xstep + align; t <= o.x1; t += xstep) {
      const x = X(t);
      if (x < pad.l + 16 || x > W - pad.r - 16) continue;
      c.fillText(o.tick(t, xstep), x, H - pad.b + 6);
    }

    // band (split into segments at gaps)
    const segments = (pts) => {
      const out = [];
      let cur = [];
      for (let i = 0; i < pts.length; i++) {
        if (cur.length && pts[i][0] - pts[i - 1][0] > o.gapAfter) { out.push(cur); cur = []; }
        cur.push(pts[i]);
      }
      if (cur.length) out.push(cur);
      return out;
    };
    c.save();
    c.beginPath(); c.rect(pad.l, pad.t, pw, ph); c.clip();
    c.fillStyle = cssVar("--chart-band");
    for (const seg of segments(o.band)) {
      c.beginPath();
      seg.forEach(([t, , b], i) => (i ? c.lineTo(X(t), Y(b)) : c.moveTo(X(t), Y(b))));
      for (let i = seg.length - 1; i >= 0; i--) c.lineTo(X(seg[i][0]), Y(seg[i][1]));
      c.closePath(); c.fill();
    }
    c.strokeStyle = cssVar("--chart-line");
    c.lineWidth = 2; c.lineJoin = "round";
    for (const seg of segments(o.line)) {
      c.beginPath();
      seg.forEach(([t, v], i) => (i ? c.lineTo(X(t), Y(v)) : c.moveTo(X(t), Y(v))));
      if (seg.length === 1) c.arc(X(seg[0][0]), Y(seg[0][1]), 1.5, 0, Math.PI * 2);
      c.stroke();
    }
    c.restore();
  }
  function niceStep(raw) {
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    const f = raw / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * p;
  }

  // ---------------------------------------------------------------- history
  async function loadHistory() {
    if (!st.station) return;
    try {
      st.history = await getJSON(`/api/history?station=${encodeURIComponent(st.station)}&range=${st.range}`);
    } catch (e) {
      st.history = null;
    }
    drawHistory();
  }

  function drawHistory() {
    const h = st.history;
    const pts = h?.points || [];
    $("hist-empty").hidden = pts.length > 0;
    const stats = h?.stats;
    const statEl = (label, val) => {
      const s = el("div", "stat");
      s.append(el("b", null, val), el("span", null, label));
      return s;
    };
    $("hist-stats").replaceChildren(...(stats ? [
      statEl("Average", `${fmtV(stats.avg)} V`),
      statEl("Min (avg)", `${fmtV(stats.min)} V`),
      statEl("Max (avg)", `${fmtV(stats.max)} V`),
      statEl("Lowest dip", `${fmtV(stats.trough_lo)} V`),
      statEl("Highest peak", `${fmtV(stats.peak_hi)} V`),
      statEl("Coverage", `${Math.round(stats.coverage * 100)}%`),
    ] : []));
    if (!h) return;
    drawChart($("hist-chart"), {
      x0: h.since, x1: Date.now() / 1000,
      line: pts.map((p) => [p.t + h.step / 2, p.avg]),
      band: pts.map((p) => [p.t + h.step / 2, p.lo, p.hi]),
      gapAfter: h.step * 3,
      tick: (t, step) => {
        const d = new Date(t * 1000);
        return step >= 86400 ? d.toLocaleDateString([], { month: "short", day: "numeric" })
          : d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
      },
    });
  }

  function setRange(r) {
    st.range = r;
    localStorage.setItem("ting.range", r);
    for (const b of $("range-seg").querySelectorAll("button")) {
      b.setAttribute("aria-checked", String(b.dataset.range === r));
    }
    loadHistory();
  }
  $("range-seg").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-range]");
    if (b) setRange(b.dataset.range);
  });

  // ---------------------------------------------------------------- events
  async function loadEvents() {
    let data;
    try {
      data = await getJSON(`/api/events${st.station ? `?station=${encodeURIComponent(st.station)}` : ""}`);
    } catch {
      return;
    }
    const empty = (msg) => [el("li", "empty-row", msg)];
    const notes = data.notifications.filter((n) => !n.serial || n.serial === st.station);
    $("events-list").replaceChildren(...(notes.length ? notes.map((n) => {
      const li = el("li");
      const text = `${n.title || n.type}${n.subtitle ? ` — ${n.subtitle}` : ""}`;
      li.append(el("span", `badge ${n.cleared ? "ok" : severity(n.category || n.type)}`),
        el("span", "title", text),
        el("span", "meta", [n.message, fmtDate(n.time), n.cleared ? "Cleared" : null].filter(Boolean).join(" · ")));
      return li;
    }) : empty("No alerts from Ting. Notifications appear here as Ting reports them.")));

    const ch = data.status_changes;
    $("changes-list").replaceChildren(...(ch.length ? ch.map((c) => {
      const li = el("li");
      const sev = c.is_fire ? "err" : [severity(c.efh), severity(c.ufh)].includes("err") ? "err"
        : [severity(c.efh), severity(c.ufh)].includes("warn") || c.pq || c.frozen_pipe ? "warn" : "ok";
      const flags = [c.pq && "power quality hazard", c.frozen_pipe && "frozen pipe risk",
        c.learning && "learning", c.is_fire && "FIRE"].filter(Boolean).join(", ");
      li.append(el("span", `badge ${sev}`),
        el("span", "title", `Electrical: ${c.efh || "—"} · Utility: ${c.ufh || "—"}`),
        el("span", "meta", [c.message, flags, fmtDate(c.ts)].filter(Boolean).join(" · ")));
      return li;
    }) : empty("No status recorded yet.")));
  }

  // ---------------------------------------------------------------- station change
  async function onStationChange() {
    if (st.station) localStorage.setItem("ting.station", st.station);
    st.recent = [];
    st.arrivals = [];
    renderHero();
    requestDraw();
    if (!st.station) return;
    try {
      st.recent = await getJSON(`/api/recent?station=${encodeURIComponent(st.station)}`);
    } catch { /* stream fills in */ }
    renderHero();
    requestDraw();
    loadHistory();
    loadEvents();
  }
  $("device-select").addEventListener("change", (e) => {
    st.station = e.target.value;
    renderState();
    onStationChange();
  });

  // ---------------------------------------------------------------- SSE
  const sse = { ok: true, es: null };
  function connect() {
    const es = new EventSource("/api/live");
    sse.es = es;
    es.addEventListener("open", () => { sse.ok = true; if (st.snap) renderState(); });
    es.addEventListener("state", (e) => {
      const first = !st.snap;
      st.snap = JSON.parse(e.data);
      sse.ok = true;
      renderState();
      if (first && st.station) onStationChange();
    });
    es.addEventListener("sample", (e) => onSample(JSON.parse(e.data)));
    es.addEventListener("error", () => {
      sse.ok = es.readyState === EventSource.OPEN;
      if (st.snap) { renderConn(); renderBanner(); }
    });
  }

  // ---------------------------------------------------------------- menu
  const sess = { password_required: false, setup_available: false, version: null };
  const menu = $("menu"), menuBtn = $("menu-btn");
  const closeMenu = () => { menu.hidden = true; menuBtn.setAttribute("aria-expanded", "false"); };
  menuBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const open = menu.hidden;
    menu.hidden = !open;
    menuBtn.setAttribute("aria-expanded", String(open));
  });
  document.addEventListener("click", (e) => { if (!menu.contains(e.target)) closeMenu(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });

  async function loadSession() {
    try {
      const s = await getJSON("/api/session");
      Object.assign(sess, s);
    } catch { /* offline */ }
    $("menu-logout").hidden = !sess.password_required;
    $("menu-setup").hidden = !sess.setup_available;
    $("menu-unlink").hidden = !!sess.demo;
    $("menu-meta").textContent = sess.demo
      ? "Demo mode — no Ting account linked."
      : sess.password_required
        ? "Dashboard password is set."
        : "Open access: anyone who can reach this port can view this dashboard. Set DASHBOARD_PASSWORD to require a sign-in.";
  }

  $("menu-logout").addEventListener("click", async () => {
    await fetch("/api/logout", { method: "POST" }).catch(() => {});
    location.href = "/login";
  });

  $("menu-unlink").addEventListener("click", async () => {
    closeMenu();
    if (!confirm("Unlink the Ting account? Live data stops until you link an account again.")) return;
    const r = await fetch("/api/unlink", { method: "POST" });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      alert(d.error || "Could not unlink");
      return;
    }
    location.href = "/setup";
  });

  // PWA install (Chromium only; iOS uses Share → Add to Home Screen)
  let installEvent = null;
  addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    installEvent = e;
    $("menu-install").hidden = false;
  });
  $("menu-install").addEventListener("click", async (e) => {
    e.preventDefault();
    closeMenu();
    if (!installEvent) return;
    installEvent.prompt();
    await installEvent.userChoice;
    installEvent = null;
    $("menu-install").hidden = true;
  });
  addEventListener("appinstalled", () => { $("menu-install").hidden = true; });

  async function loadMeta() {
    try {
      const v = await getJSON("/api/version");
      sess.version = v.version;
      $("version").textContent = `v${v.version}`;
    } catch { /* offline */ }
  }

  // ---------------------------------------------------------------- boot
  applyTheme(theme);
  setRange(st.range);
  loadSession();
  loadMeta();
  connect();
  setInterval(loadHistory, 60000);
  setInterval(loadEvents, 60000);
  new ResizeObserver(() => { requestDraw(); drawHistory(); }).observe(document.querySelector(".content"));
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) { loadHistory(); requestDraw(); }
  });
})();

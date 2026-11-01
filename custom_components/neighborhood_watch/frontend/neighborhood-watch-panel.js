// Neighborhood Watch sidebar panel: the local detector (vision service) and its training.
// Everything goes through the integration's websocket commands, so the browser never needs to
// reach the vision service itself.

const DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : "");
const hourName = (h) => new Date(2000, 0, 1, h).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const devName = (d) => ({ GPU: "Intel GPU", NPU: "NPU", CPU: "CPU", xpu: "GPU", cpu: "CPU" })[d] || d || "—";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const MENU = "M3,6H21V8H3V6M3,11H21V13H3V11M3,16H21V18H3V16Z";
const INTEGRATION = "/config/integrations/integration/neighborhood_watch";

const STYLE = `
  :host {
    display: block; min-height: 100%; background: var(--primary-background-color);
    color: var(--primary-text-color); font-family: "Google Sans", var(--ha-font-family-body, sans-serif);
    --nw-line: var(--divider-color, rgba(127, 127, 127, .25));
    --nw-accent: var(--primary-color); --nw-on-accent: var(--text-primary-color, #fff);
    --nw-good: var(--success-color, #2e7d32); --nw-warn: var(--warning-color, #b86e00); --nw-bad: var(--error-color, #c62828);
    --nw-chip: var(--secondary-background-color, rgba(127, 127, 127, .12));
  }
  * { box-sizing: border-box; }
  .toolbar {
    display: flex; align-items: center; gap: 4px; height: var(--header-height, 56px); padding: 0 12px;
    background: var(--app-header-background-color); color: var(--app-header-text-color, var(--primary-text-color));
    border-bottom: var(--app-header-border-bottom, none); font-size: 20px;
  }
  .toolbar .title { flex: 1; margin-left: 12px; }
  .icon-btn {
    width: 40px; height: 40px; border: 0; border-radius: 50%; background: none; color: inherit;
    display: grid; place-items: center; cursor: pointer;
  }
  .icon-btn svg { width: 24px; height: 24px; fill: currentColor; }
  main { max-width: 880px; margin: 0 auto; padding: 16px 16px 48px; }
  .card {
    background: var(--card-background-color); border: 1px solid var(--nw-line);
    border-radius: var(--ha-card-border-radius, 12px); padding: 16px 20px; margin-bottom: 16px;
  }
  h2 { font-size: 16px; font-weight: 500; margin: 0 0 12px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px 20px; }
  .label { color: var(--secondary-text-color); font-size: 13px; }
  .value { font-weight: 500; overflow-wrap: anywhere; }
  .head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
  .head h2 { margin: 0; flex: 1; }
  .chip {
    display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px; border-radius: 99px;
    background: var(--nw-chip); font-size: 13px; font-weight: 500; white-space: nowrap;
  }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--secondary-text-color); }
  .dot.good { background: var(--nw-good); } .dot.warn { background: var(--nw-warn); } .dot.bad { background: var(--nw-bad); }
  .row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .row + .row { margin-top: 14px; }
  .row > .label { min-width: 190px; font-size: 14px; color: var(--primary-text-color); }
  .group { display: inline-flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  @media (max-width: 520px) {
    .row > .label { min-width: 0; flex: 1 0 100%; }
    .row.inline > .label { flex: 1 1 auto; }
  }
  .hint { color: var(--secondary-text-color); font-size: 13px; margin-top: 8px; }
  .actions { display: flex; gap: 8px; flex-wrap: wrap; }
  button.btn, a.btn {
    font: inherit; font-size: 14px; font-weight: 500; border: 0; border-radius: 99px; padding: 8px 18px;
    background: var(--nw-accent); color: var(--nw-on-accent); cursor: pointer; text-decoration: none;
    display: inline-flex; align-items: center;
  }
  .btn.quiet { background: none; color: var(--nw-accent); border: 1px solid var(--nw-line); }
  button:disabled { opacity: .45; cursor: default; }
  button:focus-visible, a:focus-visible, select:focus-visible, input:focus-visible, .seg label:has(input:focus-visible) {
    outline: 2px solid var(--nw-accent); outline-offset: 2px;
  }
  select, input[type=number] {
    font: inherit; font-size: 14px; color: var(--primary-text-color); background: var(--card-background-color);
    border: 1px solid var(--nw-line); border-radius: 10px; padding: 6px 10px;
  }
  select option { background: var(--card-background-color); color: var(--primary-text-color); }
  input[type=number] { width: 90px; }
  .seg { display: inline-flex; border: 1px solid var(--nw-line); border-radius: 99px; overflow: hidden; }
  .seg label { padding: 6px 16px; cursor: pointer; font-size: 14px; font-weight: 500; }
  .seg label + label { border-left: 1px solid var(--nw-line); }
  .seg input { position: absolute; opacity: 0; pointer-events: none; }
  .seg label:has(input:checked) { background: var(--nw-accent); color: var(--nw-on-accent); }
  .switch { position: relative; width: 44px; height: 26px; flex: none; }
  .switch input { opacity: 0; width: 100%; height: 100%; margin: 0; position: absolute; cursor: pointer; z-index: 1; }
  .switch span { position: absolute; inset: 0; border-radius: 99px; background: var(--nw-line); transition: background .15s; }
  .switch span::after {
    content: ""; position: absolute; top: 3px; left: 3px; width: 20px; height: 20px; border-radius: 50%;
    background: #fff; transition: transform .15s;
  }
  .switch input:checked + span { background: var(--nw-accent); }
  .switch input:checked + span::after { transform: translateX(18px); }
  .switch input:focus-visible + span { outline: 2px solid var(--nw-accent); outline-offset: 2px; }
  .switch input:disabled + span { opacity: .45; }
  .bar { height: 6px; border-radius: 99px; background: var(--nw-chip); overflow: hidden; margin-top: 10px; }
  .bar div { height: 100%; background: var(--nw-accent); transition: width .4s; }
  .table-wrap { overflow-x: auto; }
  table { border-collapse: collapse; width: 100%; font-size: 14px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--nw-line); white-space: nowrap; }
  th { color: var(--secondary-text-color); font-weight: 500; font-size: 13px; }
  tr:last-child td { border-bottom: 0; }
  .better { color: var(--nw-good); } .worse { color: var(--nw-bad); }
  .muted { color: var(--secondary-text-color); }
  .error { color: var(--nw-bad); margin-top: 8px; font-size: 14px; }
  @media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
`;

class NeighborhoodWatchPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._data = null;
    this._busy = false;
    this._message = "";
    this._settingsError = "";
    this._onVisible = () => { if (!document.hidden) this._refresh(); };
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first && this.isConnected) this._refresh();
  }

  set narrow(value) {
    this._narrow = value;
    this._render();
  }

  set panel(panel) {
    const font = panel?.config?.font;
    if (font && !document.getElementById("nw-google-sans")) {
      // @font-face only works in the document, not inside a shadow root.
      const style = document.createElement("style");
      style.id = "nw-google-sans";
      style.textContent = `@font-face { font-family: "Google Sans"; src: url("${font}") format("woff2"); font-weight: 400 700; font-display: block; }`;
      document.head.append(style);
    }
  }

  connectedCallback() {
    document.addEventListener("visibilitychange", this._onVisible);
    if (this._hass) this._refresh();
    else this._render();
  }

  disconnectedCallback() {
    document.removeEventListener("visibilitychange", this._onVisible);
    clearTimeout(this._timer);
  }

  async _refresh() {
    clearTimeout(this._timer);
    if (!this._hass || !this.isConnected) return;
    try {
      this._data = await this._hass.callWS({ type: "neighborhood_watch/vision" });
    } catch (err) {
      this._data = { loaded: false, error: { code: "ws", message: err.message || String(err) } };
    }
    // Don't redraw under the user's typing.
    if (this.shadowRoot.activeElement?.id !== "min_new") this._render();
    if (!document.hidden) this._timer = setTimeout(() => this._refresh(), this._data?.train?.running ? 5000 : 30000);
  }

  _navigate(path) {
    history.pushState(null, "", path);
    window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
  }

  async _save(changes) {
    this._settingsError = "";
    try {
      const answer = await this._hass.callWS({ type: "neighborhood_watch/vision/settings", changes });
      if (this._data?.train) this._data.train.settings = answer.settings;
    } catch (err) {
      this._settingsError = err.message || String(err);
    }
    this._render();
    this._refresh();
  }

  async _train() {
    if (!confirm("Start a training round now? It takes from tens of minutes to a few hours.")) return;
    this._busy = true;
    this._message = "";
    this._render();
    try {
      await this._hass.callWS({ type: "neighborhood_watch/vision/train" });
    } catch (err) {
      this._message = err.message || String(err);
    }
    this._busy = false;
    this._refresh();
  }

  _toolbar() {
    const menu = this._narrow
      ? `<button class="icon-btn" id="menu" aria-label="Open the sidebar"><svg viewBox="0 0 24 24"><path d="${MENU}"/></svg></button>`
      : "";
    return `<div class="toolbar">${menu}<span class="title">Neighborhood Watch</span></div>`;
  }

  _problem(d) {
    if (!d) return `<div class="card"><span class="muted">Loading…</span></div>`;
    if (!d.loaded) {
      return `<div class="card"><h2>Neighborhood Watch isn't running</h2>
        <div class="hint">Set it up, or check its status, in the integration's settings.</div>
        <div class="actions" style="margin-top: 12px"><button class="btn" data-go="${INTEGRATION}">Open the integration</button></div></div>`;
    }
    if (d.error?.code === "not_configured") {
      return `<div class="card"><h2>No local detector yet</h2>
        <div class="hint">A local detector checks every frame before the AI does, and learns from your answers each week.
        Run the vision service on a NAS or any Docker host, then add its address under
        <b>Configure → Advanced → Local detector</b>.</div>
        <div class="actions" style="margin-top: 12px">
          <button class="btn" data-go="${INTEGRATION}">Open the integration</button>
          <a class="btn quiet" href="https://github.com/b2dmx/neighborhood-watch/blob/main/docs/reference.md" target="_blank" rel="noreferrer">How to set it up</a>
        </div></div>`;
    }
    if (d.error) {
      return `<div class="card"><h2>Can't reach the detector</h2>
        <div class="hint">${esc(d.url)} didn't answer: ${esc(d.error.message)}</div>
        <div class="actions" style="margin-top: 12px"><button class="btn" id="retry">Try again</button>
        <button class="btn quiet" data-go="${INTEGRATION}">Open the integration</button></div></div>`;
    }
    return "";
  }

  _stageText(t) {
    const st = t.status || {};
    const s = t.settings;
    if (t.running) {
      if (st.stage === "training" && st.epochs) return `Training: pass ${st.epoch} of ${st.epochs}`;
      if (st.stage === "labelling" && st.total) return `Marking animals in the photos: ${st.done} of ${st.total}`;
      return cap(st.stage || "starting");
    }
    const enabled = s ? s.train_enabled : t.enabled;
    let when = "Weekly training is off.";
    if (enabled && s) when = `Next round: ${cap(s.train_day)} from ${hourName(s.train_hour)}, once ${s.min_new} new photos are confirmed.`;
    else if (enabled) when = `Next round: ${cap(t.schedule)}.`;
    if (!st.state) return when;
    const last = { skipped: "Last check skipped", done: "Last round finished", error: "Last round failed" }[st.state] || "Last round";
    const at = st.finished || st.started;
    const date = at ? " " + new Date(at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "";
    return `${last}${date}: ${st.stage}. ${when}`;
  }

  _progress(t) {
    const st = t.status || {};
    if (t.running && st.stage === "training" && st.epochs) return st.epoch / st.epochs;
    if (t.running && st.stage === "labelling" && st.total) return st.done / st.total;
    return null;
  }

  _history(rounds) {
    if (!rounds) return "";
    const cell = (b, a, key) => {
      const x = b?.[key], y = a?.[key];
      if (y === undefined) return esc(x ?? "—");
      return `${esc(x)} → <span class="${y < x ? "better" : y > x ? "worse" : ""}">${esc(y)}</span>`;
    };
    const rows = rounds.length
      ? rounds.slice().reverse().map((r) => {
        const result = r.error ? `<span class="worse">Failed</span>` : r.promoted ? `<span class="better">New model</span>` : "Kept the old one";
        const date = r.time ? new Date(r.time).toLocaleDateString([], { dateStyle: "medium" }) : "—";
        const mins = r.minutes != null ? `${Math.round(r.minutes)} min` : "—";
        return `<tr><td>${esc(r.round)}</td><td>${date}</td><td>${esc(devName(r.device))}</td><td>${mins}</td>` +
          `<td>${cell(r.before, r.after, "misses")}</td><td>${cell(r.before, r.after, "false_alarms")}</td><td>${result}</td></tr>`;
      }).join("")
      : `<tr><td colspan="7" class="muted">No rounds yet.</td></tr>`;
    return `<section class="card"><h2>Past rounds</h2>
      <div class="hint" style="margin: -6px 0 10px">A new model replaces the current one only when it misses fewer animals and makes at least 10% fewer mistakes in all.</div>
      <div class="table-wrap"><table>
        <thead><tr><th>Round</th><th>Date</th><th>Ran on</th><th>Time</th><th>Missed animals</th><th>False alarms</th><th>Result</th></tr></thead>
        <tbody>${rows}</tbody></table></div></section>`;
  }

  _settings(s) {
    if (!s) {
      return `<section class="card"><h2>Settings</h2><div class="hint">Update the vision service (pull its image again) to change its training settings here.</div></section>`;
    }
    const off = s.train_enabled ? "" : "disabled";
    const days = DAYS.map((d) => `<option value="${d}" ${d === s.train_day ? "selected" : ""}>${cap(d)}</option>`).join("");
    const hours = Array.from({ length: 24 }, (_, h) => `<option value="${h}" ${h === s.train_hour ? "selected" : ""}>${hourName(h)}</option>`).join("");
    const dev = (v, label) => `<label><input type="radio" name="device" value="${v}" ${s.train_device === v ? "checked" : ""}>${label}</label>`;
    return `<section class="card"><h2>Settings</h2>
      <div class="row inline"><span class="label" id="l-enabled">Weekly training</span>
        <label class="switch"><input type="checkbox" id="enabled" aria-labelledby="l-enabled" ${s.train_enabled ? "checked" : ""}><span></span></label></div>
      <div class="row"><span class="label">When</span>
        <span class="group"><select id="day" aria-label="Training day" ${off}>${days}</select><span>from</span>
        <select id="hour" aria-label="Training hour" ${off}>${hours}</select></span></div>
      <div class="row"><span class="label" id="l-min">New confirmed photos needed</span>
        <input type="number" id="min_new" min="1" max="10000" value="${s.min_new}" aria-labelledby="l-min" ${off}></div>
      <div class="row"><span class="label" id="l-device">Train on</span>
        <span class="seg" role="radiogroup" aria-labelledby="l-device">${dev("auto", "GPU")}${dev("cpu", "CPU")}</span></div>
      <div class="hint">GPU uses the Intel GPU when it can, and the CPU if it can't. Changes apply to the next round.</div>
      ${this._settingsError ? `<div class="error">${esc(this._settingsError)}</div>` : ""}
    </section>`;
  }

  _render() {
    const d = this._data;
    const problem = this._problem(d);
    let body = problem;
    if (!problem) {
      const h = d.health, t = d.train;
      const f = this._progress(t);
      const chip = t.running ? ["Training", "warn"] : ["Ready", "good"];
      body = `
        <section class="card">
          <div class="head"><h2>Detector</h2><span class="chip"><span class="dot ${chip[1]}"></span>${chip[0]}</span></div>
          <div class="grid">
            <div><div class="label">Model</div><div class="value">${h.trained ? `Trained on your photos (${esc(h.model)})` : `MegaDetector (${esc(h.model)})`}</div></div>
            <div><div class="label">Runs on</div><div class="value">${esc(devName(h.device))}</div></div>
            <div><div class="label">Hardware found</div><div class="value">${esc((h.available || []).map(devName).join(", ") || "—")}</div></div>
            <div><div class="label">Right now</div><div class="value">${h.loaded ? "Awake" : "Asleep until the next photo"}</div></div>
          </div>
          <div class="actions" style="margin-top: 14px">
            <a class="btn quiet" href="${esc(d.url)}/" target="_blank" rel="noreferrer">Open the vision page</a>
            <button class="btn quiet" data-go="${INTEGRATION}">Integration settings</button>
          </div>
        </section>
        <section class="card">
          <div class="head"><h2>Training</h2>
            <button class="btn" id="train" ${t.running || this._busy || !t.dataset ? "disabled" : ""}
              title="${t.dataset ? "" : "No dataset folder is mounted"}">Train now</button></div>
          <div class="hint" style="margin-top: 0">${esc(this._stageText(t))}</div>
          ${f === null ? "" : `<div class="bar"><div style="width: ${Math.round(f * 100)}%"></div></div>`}
          ${this._message ? `<div class="error">${esc(this._message)}</div>` : ""}
        </section>
        ${this._settings(t.settings)}
        ${this._history(d.history)}`;
    }
    this.shadowRoot.innerHTML = `<style>${STYLE}</style>${this._toolbar()}<main>${body}</main>`;
    this._bind();
  }

  _bind() {
    const $ = (id) => this.shadowRoot.getElementById(id);
    $("menu")?.addEventListener("click", () => {
      this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true }));
    });
    $("retry")?.addEventListener("click", () => { this._data = null; this._render(); this._refresh(); });
    $("train")?.addEventListener("click", () => this._train());
    $("enabled")?.addEventListener("change", (e) => this._save({ train_enabled: e.target.checked }));
    $("day")?.addEventListener("change", (e) => this._save({ train_day: e.target.value }));
    $("hour")?.addEventListener("change", (e) => this._save({ train_hour: Number(e.target.value) }));
    $("min_new")?.addEventListener("change", (e) => {
      e.target.blur();
      this._save({ min_new: Math.round(Number(e.target.value)) });
    });
    for (const r of this.shadowRoot.querySelectorAll("input[name=device]")) {
      r.addEventListener("change", (e) => this._save({ train_device: e.target.value }));
    }
    for (const b of this.shadowRoot.querySelectorAll("[data-go]")) {
      b.addEventListener("click", () => this._navigate(b.dataset.go));
    }
  }
}

if (!customElements.get("neighborhood-watch-panel")) {
  customElements.define("neighborhood-watch-panel", NeighborhoodWatchPanel);
}

// Neighborhood Watch sidebar panel: the review queue, every setting, and the local detector.
//
// The settings are the integration's own settings forms (its options flow and the Add/Edit
// animal flow), run through Home Assistant's flow API and drawn with Home Assistant's form
// element. So they are exactly the settings of Configure, with the same checks and the same
// explanations, just laid out by topic instead of behind a menu. The detector's status and
// training go through the integration's websocket commands, so the browser never needs to
// reach the vision service itself.

const DOMAIN = "neighborhood_watch";
const DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : "");
const hourName = (h) => new Date(2000, 0, 1, h).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const devName = (d) => ({ GPU: "Intel GPU", NPU: "NPU", CPU: "CPU", xpu: "GPU", cpu: "CPU" })[d] || d || "—";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const MENU = "M3,6H21V8H3V6M3,11H21V13H3V11M3,16H21V18H3V16Z";
const BACK = "M20,11V13H8L13.5,18.5L12.08,19.92L4.16,12L12.08,4.08L13.5,5.5L8,11H20Z";
const CHEVRON = "M8.59,16.58L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.58Z";
const NEW = "add_new"; // the options flow's "+ Add ..." choice

// Tabs, and the settings forms on each: the options-flow steps to walk to reach a form.
const TABS = [
  { id: "review", label: "Review" },
  { id: "cameras", label: "Cameras" },
  { id: "animals", label: "Animals" },
  { id: "people", label: "People" },
  { id: "alerts", label: "Alerts" },
  { id: "ai", label: "AI" },
  { id: "detector", label: "Detector" },
];
const INTRO = {
  review:
    "Sightings the AI wasn't sure about, and the ones you haven't confirmed yet. Your answers fix tracking and become training data for the detector.",
  cameras:
    "Each camera is checked by the AI when its motion sensor fires (or on a timer). Open one to change what starts a check, how often, and what it does when a pest shows up. Below the list: the hours the cameras are watched.",
  animals:
    "Your own animals first: how to recognise each one and where it alerts. Below them: pests, and every other animal (wildlife, unknown cats and dogs, unsure motion). Those are recognised without adding them.",
  people: "Who gets alerts, on which phone, and when to keep quiet.",
  alerts: "Alerts on or off, how sure the AI has to be, and the picture link on phones. Who gets them is under People; where each animal alerts is under Animals.",
  ai: "Which AI models look at the photos, and what they're told.",
  detector:
    "An optional local detector that checks every frame before the AI does and learns from your answers every week.",
};
const FORMS = {
  cameras: [["schedule"]],
  animals: [["pests"], ["catch_alls"]],
  alerts: [["alerts"], ["advanced", "notifications"]],
  ai: [["models"], ["advanced", "identification"]],
  detector: [["advanced", "detector"], ["advanced", "training"]],
};

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
  header {
    position: sticky; top: 0; z-index: 2;
    background: var(--app-header-background-color); color: var(--app-header-text-color, var(--primary-text-color));
    border-bottom: var(--app-header-border-bottom, 1px solid var(--nw-line));
  }
  .toolbar { display: flex; align-items: center; gap: 4px; height: var(--header-height, 56px); padding: 0 12px; font-size: 20px; }
  .toolbar .title { flex: 1; margin-left: 12px; }
  .tabs { display: flex; gap: 4px; padding: 0 12px; overflow-x: auto; scrollbar-width: none; }
  .tabs::-webkit-scrollbar { display: none; }
  .tab {
    font: inherit; font-size: 14px; font-weight: 500; color: inherit; opacity: .75; background: none; border: 0;
    padding: 10px 14px 12px; cursor: pointer; white-space: nowrap; border-bottom: 3px solid transparent;
  }
  .tab[aria-selected=true] { opacity: 1; border-bottom-color: currentColor; }
  .icon-btn {
    width: 40px; height: 40px; border: 0; border-radius: 50%; background: none; color: inherit;
    display: grid; place-items: center; cursor: pointer; flex: none;
  }
  .icon-btn svg { width: 24px; height: 24px; fill: currentColor; }
  [hidden] { display: none !important; }
  main { max-width: 960px; margin: 0 auto; padding: 16px 16px 64px; }
  .intro { color: var(--secondary-text-color); font-size: 14px; margin: 4px 4px 16px; max-width: 70ch; }
  .card {
    background: var(--card-background-color); border: 1px solid var(--nw-line);
    border-radius: var(--ha-card-border-radius, 12px); padding: 16px 20px; margin-bottom: 16px;
  }
  .card.flush { padding: 0; overflow: hidden; }
  h2 { font-size: 18px; font-weight: 500; margin: 0 0 4px; }
  h3 { font-size: 15px; font-weight: 500; margin: 0 0 12px; }
  .desc { color: var(--secondary-text-color); font-size: 14px; margin-bottom: 12px; }
  .desc ha-markdown { --markdown-paragraph-margin: 0; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px 20px; }
  .label { color: var(--secondary-text-color); font-size: 13px; }
  .value { font-weight: 500; overflow-wrap: anywhere; }
  .head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
  .head h3 { margin: 0; flex: 1; }
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
  .actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .actions.end { justify-content: flex-end; margin-top: 16px; }
  .saved { color: var(--nw-good); font-size: 14px; margin-right: auto; }
  button.btn, a.btn {
    font: inherit; font-size: 14px; font-weight: 500; border: 0; border-radius: 99px; padding: 8px 18px;
    background: var(--nw-accent); color: var(--nw-on-accent); cursor: pointer; text-decoration: none;
    display: inline-flex; align-items: center; gap: 6px;
  }
  .btn.quiet { background: none; color: var(--nw-accent); border: 1px solid var(--nw-line); }
  .btn.danger { background: none; color: var(--nw-bad); border: 1px solid var(--nw-line); }
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
  .list { list-style: none; margin: 0; padding: 0; }
  .list li + li { border-top: 1px solid var(--nw-line); }
  .item {
    display: flex; align-items: center; gap: 14px; width: 100%; padding: 12px 16px; border: 0; background: none;
    font: inherit; color: inherit; text-align: left; cursor: pointer; min-height: 64px;
  }
  .item:hover { background: var(--nw-chip); }
  .item .name { font-weight: 500; }
  .item .sub { color: var(--secondary-text-color); font-size: 13px; }
  .item .text { flex: 1; min-width: 0; }
  .item svg.chev { width: 20px; height: 20px; fill: var(--secondary-text-color); flex: none; }
  .avatar {
    width: 44px; height: 44px; border-radius: 12px; background: var(--nw-chip); flex: none;
    display: grid; place-items: center; font-weight: 600; color: var(--secondary-text-color); overflow: hidden;
  }
  .avatar neighborhood-watch-sprite { width: 44px; height: 44px; }
  .avatar.wide { width: 78px; }
  .list-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 0 4px 12px; }
  .back { display: flex; align-items: center; gap: 4px; margin: -4px 0 8px -8px; }
  .back button { font: inherit; font-size: 14px; color: var(--nw-accent); background: none; border: 0; cursor: pointer; display: inline-flex; align-items: center; gap: 4px; padding: 6px 8px; border-radius: 8px; }
  .back svg { width: 18px; height: 18px; fill: currentColor; }
  ha-form { display: block; }
  @media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
`;

// ---- flows ------------------------------------------------------------------------------

// Starting values for a flow form, the way Home Assistant's own flow dialog fills them in.
function initialData(schema) {
  const data = {};
  for (const field of schema || []) {
    if (field.type === "expandable") {
      data[field.name] = initialData(field.schema);
      continue;
    }
    const suggested = field.description?.suggested_value;
    if (suggested !== undefined && suggested !== null) data[field.name] = suggested;
    else if ("default" in field) data[field.name] = field.default;
    else if (field.required && (field.type === "boolean" || field.selector?.boolean)) data[field.name] = false;
  }
  return data;
}

class Flow {
  constructor(hass, kind, entryId, subentryId) {
    this.hass = hass;
    this.base = kind === "animal" ? "config/config_entries/subentries/flow" : "config/config_entries/options/flow";
    this.start = kind === "animal"
      ? { handler: [entryId, "animal"], ...(subentryId ? { subentry_id: subentryId } : {}) }
      : { handler: entryId };
    this.id = null;
  }
  async begin() {
    const result = await this.hass.callApi("POST", this.base, this.start);
    this.id = result.flow_id;
    return result;
  }
  async step(input) {
    return this.hass.callApi("POST", `${this.base}/${this.id}`, input);
  }
  abort() {
    if (!this.id) return;
    const id = this.id;
    this.id = null;
    this.hass.callApi("DELETE", `${this.base}/${id}`).catch(() => {});
  }
}

// One settings form: a flow walked to its form, drawn with ha-form, saved with the flow.
class FlowForm {
  constructor(panel, { kind = "options", path = [], subentryId = null, heading = true, onDone = null }) {
    this.panel = panel;
    this.kind = kind;
    this.path = path;
    this.subentryId = subentryId;
    this.heading = heading;
    this.onDone = onDone;
    this.el = document.createElement("section");
    this.el.className = "card";
    this.el.innerHTML = `<span class="muted">Loading…</span>`;
    this.flow = null;
  }

  get prefix() {
    const step = this.result?.step_id;
    return this.kind === "animal"
      ? `component.${DOMAIN}.config_subentries.animal.step.${step}`
      : `component.${DOMAIN}.options.step.${step}`;
  }

  t(key, placeholders) {
    const text = this.panel.localize?.(key, placeholders);
    return text || "";
  }

  async open() {
    this.flow?.abort();
    this.flow = new Flow(this.panel._hass, this.kind, this.panel.entryId, this.subentryId);
    try {
      let result = await this.flow.begin();
      for (const step of this.path) {
        if (result.type !== "menu" && result.type !== "form") break;
        result = await this.flow.step(typeof step === "string" ? { next_step_id: step } : step);
      }
      this.show(result);
    } catch (err) {
      this.el.innerHTML = `<div class="error"></div>`;
      this.el.firstChild.textContent = err.message || err.body?.message || String(err);
    }
  }

  show(result) {
    this.result = result;
    if (result.type === "create_entry" || (result.type === "abort" && /successful/.test(result.reason || ""))) {
      this.flow.id = null; // finished: nothing to abort
      if (this.onDone) return this.onDone(result);
      this.saved = true;
      this.panel._afterSave();
      return this.open(); // a fresh form with the saved values
    }
    if (result.type === "abort") {
      this.flow.id = null;
      const reason = this.t(`component.${DOMAIN}.${this.kind === "animal" ? "config_subentries.animal" : "options"}.abort.${result.reason}`);
      this.el.innerHTML = `<div class="error"></div>`;
      this.el.firstChild.textContent = reason || result.reason;
      return;
    }
    if (result.type !== "form") {
      this.el.innerHTML = `<div class="error">Unexpected step: ${esc(result.type)}</div>`;
      return;
    }
    const placeholders = result.description_placeholders || {};
    const title = this.t(`${this.prefix}.title`, placeholders);
    const description = this.t(`${this.prefix}.description`, placeholders);
    this.data = initialData(result.data_schema);
    this.el.innerHTML = `
      ${this.heading && title ? `<h3></h3>` : ""}
      ${description ? `<div class="desc"><ha-markdown breaks></ha-markdown></div>` : ""}
      <div class="error base" hidden></div>
      <ha-form></ha-form>
      <div class="actions end">
        <span class="saved" ${this.saved ? "" : "hidden"}>Saved</span>
        ${this.kind === "animal" && this.subentryId ? `<button class="btn danger" data-act="remove">Remove animal</button>` : ""}
        <button class="btn" data-act="save">${result.last_step === false || result.step_id.endsWith("_add") ? "Next" : "Save"}</button>
      </div>`;
    if (this.heading && title) this.el.querySelector("h3").textContent = title;
    if (description) this.el.querySelector("ha-markdown").content = description;
    this.saved = false;
    const form = this.el.querySelector("ha-form");
    form.hass = this.panel._hass;
    form.schema = result.data_schema;
    form.data = this.data;
    form.error = this.errors(result.errors);
    form.localizeValue = (key) => this.t(`component.${DOMAIN}.selector.${key}`);
    form.computeLabel = (schema, _data, options) => this.label(schema, options, "data", "name");
    form.computeHelper = (schema, options) => this.label(schema, options, "data_description", "description");
    form.computeError = (error) =>
      this.t(`component.${DOMAIN}.${this.kind === "animal" ? "config_subentries.animal" : "options"}.error.${error}`) || error;
    form.addEventListener("value-changed", (ev) => {
      this.data = ev.detail.value;
      form.data = this.data;
      this.el.querySelector(".saved").hidden = true;
    });
    const base = result.errors?.base;
    if (base) {
      const box = this.el.querySelector(".error.base");
      box.hidden = false;
      box.textContent = form.computeError(base);
    }
    this.el.querySelector("[data-act=save]").addEventListener("click", () => this.submit());
    this.el.querySelector("[data-act=remove]")?.addEventListener("click", () => this.panel._removeAnimal(this.subentryId));
  }

  label(schema, options, field, sectionField) {
    const path = options?.path || [];
    if (schema.type === "expandable") return this.t(`${this.prefix}.sections.${schema.name}.${sectionField}`) || (field === "data" ? schema.name : "");
    const key = path.length ? `${this.prefix}.sections.${path[0]}.${field}.${schema.name}` : `${this.prefix}.${field}.${schema.name}`;
    return this.t(key) || (field === "data" ? schema.name : "");
  }

  errors(errors) {
    if (!errors) return {};
    const out = { ...errors };
    delete out.base;
    return out;
  }

  async submit() {
    const button = this.el.querySelector("[data-act=save]");
    button.disabled = true;
    try {
      this.show(await this.flow.step(this.data));
    } catch (err) {
      button.disabled = false;
      const box = this.el.querySelector(".error.base");
      box.hidden = false;
      box.textContent = err.body?.message || err.message || String(err);
    }
  }

  close() {
    this.flow?.abort();
  }
}

// ---- panel ------------------------------------------------------------------------------

class NeighborhoodWatchPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._tab = TABS.some((t) => `#${t.id}` === location.hash) ? location.hash.slice(1) : "review";
    this._forms = [];
    this._vision = null;
    this._busy = false;
    this._message = "";
    this._settingsError = "";
    this._onVisible = () => {
      if (!document.hidden && this._tab === "detector") this._refreshVision();
    };
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (this._card) this._card.hass = hass;
    if (first && this.isConnected) this._start();
  }

  set narrow(value) {
    if (this._narrow === value) return;
    this._narrow = value;
    const menu = this.shadowRoot.getElementById("menu");
    if (menu) menu.hidden = !value;
  }

  set panel(panel) {
    this._cardUrl = panel?.config?.card;
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
    if (this._hass) this._start();
  }

  disconnectedCallback() {
    document.removeEventListener("visibilitychange", this._onVisible);
    clearTimeout(this._timer);
    this._closeForms();
  }

  async _start() {
    this._shell();
    if (!this.localize) {
      // The forms' labels, explanations and choices are the integration's own translations.
      for (const category of ["options", "config_subentries", "selector"]) {
        try {
          this.localize = await this._hass.loadBackendTranslation(category, DOMAIN);
        } catch (err) {
          this.localize = this.localize || this._hass.localize;
        }
      }
    }
    await this._loadRoster();
    this._showTab();
  }

  async _loadRoster(afterSave = false) {
    // After a save the integration reloads with the new settings: wait for it to be back.
    for (let i = 0; i < (afterSave ? 20 : 1); i++) {
      if (i) await new Promise((r) => setTimeout(r, 500));
      try {
        this._roster = await this._hass.callWS({ type: `${DOMAIN}/roster` });
      } catch (err) {
        this._roster = { loaded: false };
      }
      if (this._roster.loaded) break;
    }
    this.entryId = this._roster.entry_id || this.entryId;
  }

  _shell() {
    if (this.shadowRoot.querySelector("header")) return;
    const tabs = TABS.map(
      (t) => `<button class="tab" role="tab" data-tab="${t.id}" aria-selected="${t.id === this._tab}">${t.label}</button>`,
    ).join("");
    this.shadowRoot.innerHTML = `<style>${STYLE}</style>
      <header>
        <div class="toolbar">
          <button class="icon-btn" id="menu" aria-label="Open the sidebar" ${this._narrow ? "" : "hidden"}><svg viewBox="0 0 24 24"><path d="${MENU}"/></svg></button>
          <span class="title">Neighborhood Watch</span>
        </div>
        <nav class="tabs" role="tablist">${tabs}</nav>
      </header>
      <main><span class="muted">Loading…</span></main>`;
    this.shadowRoot.getElementById("menu").addEventListener("click", () => {
      this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true }));
    });
    for (const b of this.shadowRoot.querySelectorAll(".tab")) {
      b.addEventListener("click", () => this._select(b.dataset.tab));
    }
  }

  _select(tab) {
    if (tab === this._tab && !this._editing) return;
    this._tab = tab;
    history.replaceState(history.state, "", `${location.pathname}#${tab}`);
    for (const b of this.shadowRoot.querySelectorAll(".tab")) b.setAttribute("aria-selected", String(b.dataset.tab === tab));
    this._showTab();
  }

  _closeForms() {
    for (const f of this._forms) f.close();
    this._forms = [];
  }

  _main() {
    return this.shadowRoot.querySelector("main");
  }

  _intro(main) {
    const p = document.createElement("p");
    p.className = "intro";
    p.textContent = INTRO[this._tab] || "";
    main.append(p);
  }

  _showTab() {
    clearTimeout(this._timer);
    this._closeForms();
    this._editing = null;
    this._card = null;
    const main = this._main();
    main.innerHTML = "";
    if (!this._roster?.loaded) {
      main.innerHTML = `<div class="card"><h2>Neighborhood Watch isn't running</h2>
        <div class="hint">Set it up, or check its status, under Settings → Devices &amp; services.</div></div>`;
      return;
    }
    this._intro(main);
    if (this._tab === "review") return this._showReview(main);
    if (this._tab === "cameras" || this._tab === "people" || this._tab === "animals") this._showList(main);
    if (this._tab === "detector") {
      this._visionBox = document.createElement("div");
      main.append(this._visionBox);
      this._renderVision();
      this._refreshVision();
    }
    for (const path of FORMS[this._tab] || []) this._addForm(main, { path });
  }

  _addForm(main, options) {
    const form = new FlowForm(this, options);
    this._forms.push(form);
    main.append(form.el);
    form.open();
    return form;
  }

  // ---- review ----

  _showReview(main) {
    if (!customElements.get("neighborhood-watch-card")) {
      main.insertAdjacentHTML("beforeend", `<div class="card muted">The review list is loading…</div>`);
      // The same address as Home Assistant's own copy, so it never loads twice.
      if (this._cardUrl) import(this._cardUrl).catch(() => {});
      customElements.whenDefined("neighborhood-watch-card").then(() => this._tab === "review" && this._showTab());
      return;
    }
    const card = document.createElement("neighborhood-watch-card");
    card.setConfig({ hide_title: true, sections: ["review"], review_limit: 30, empty: "Nothing to review. You're all caught up." });
    card.hass = this._hass;
    this._card = card;
    main.append(card);
  }

  // ---- cameras, people, animals ----

  _showList(main) {
    const kind = this._tab;
    const items = this._roster[kind] || [];
    const noun = { cameras: "camera", people: "person", animals: "animal" }[kind];
    const head = document.createElement("div");
    head.className = "list-head";
    head.innerHTML = `<h2>${kind === "animals" ? "Your animals" : cap(kind)}</h2><button class="btn">Add ${noun}</button>`;
    head.querySelector("button").addEventListener("click", () => this._edit(kind, null));
    main.append(head);
    const card = document.createElement("div");
    card.className = "card flush";
    if (!items.length) {
      card.innerHTML = `<div style="padding: 16px 20px" class="muted">No ${kind} yet.</div>`;
    } else {
      card.innerHTML = `<ul class="list">${items
        .map((it, i) => `<li><button class="item" data-i="${i}">
          <span class="avatar${kind === "cameras" ? " wide" : ""}">${kind === "animals" ? "" : esc((it.name || "?").charAt(0).toUpperCase())}</span>
          <span class="text"><div class="name">${esc(it.name)}</div><div class="sub">${esc(this._subtitle(kind, it))}</div></span>
          <svg class="chev" viewBox="0 0 24 24"><path d="${CHEVRON}"/></svg></button></li>`)
        .join("")}</ul>`;
      card.querySelectorAll(".item").forEach((b) => {
        const it = items[Number(b.dataset.i)];
        b.addEventListener("click", () => this._edit(kind, it));
        if (kind === "animals") this._sprite(b.querySelector(".avatar"), it);
        else this._picture(b.querySelector(".avatar"), kind === "cameras" ? it.camera : it.person);
      });
    }
    main.append(card);
  }

  _subtitle(kind, it) {
    if (kind === "cameras") return this._hass.states[it.camera]?.attributes?.friendly_name || it.camera || "";
    if (kind === "people") return it.person ? this._hass.states[it.person]?.attributes?.friendly_name || it.person : "Alerts only";
    return cap(it.species || "");
  }

  // A camera's latest picture, or a person's photo, when Home Assistant has one.
  _picture(box, entityId) {
    const url = entityId && this._hass.states[entityId]?.attributes?.entity_picture;
    if (!url) return;
    const img = document.createElement("img");
    img.alt = "";
    img.style.cssText = "width: 100%; height: 100%; object-fit: cover";
    img.onload = () => box.replaceChildren(img);
    img.src = url;
  }

  _sprite(box, animal) {
    // Cats are drawn from their look; one without a look set gets its initial.
    const plainCat = !animal.appearance && String(animal.species || "cat").toLowerCase() === "cat";
    if (plainCat || !customElements.get("neighborhood-watch-sprite")) {
      box.textContent = (animal.name || "?").charAt(0).toUpperCase();
      return;
    }
    const sprite = document.createElement("neighborhood-watch-sprite");
    try {
      sprite.setConfig(animal.appearance ? { appearance: animal.appearance, size: 44 } : { kind: cap(animal.species), size: 44 });
      box.append(sprite);
    } catch (err) {
      box.textContent = (animal.name || "?").charAt(0).toUpperCase();
    }
  }

  _edit(kind, item) {
    this._closeForms();
    const main = this._main();
    main.innerHTML = "";
    this._editing = { kind, item };
    const back = document.createElement("div");
    back.className = "back";
    back.innerHTML = `<button><svg viewBox="0 0 24 24"><path d="${BACK}"/></svg>All ${kind}</button>`;
    back.querySelector("button").addEventListener("click", () => this._showTab());
    main.append(back);
    const h = document.createElement("h2");
    h.textContent = item ? item.name : `New ${{ cameras: "camera", people: "person", animals: "animal" }[kind]}`;
    h.style.margin = "0 4px 12px";
    main.append(h);
    const done = async () => {
      await this._loadRoster(true);
      this._showTab();
    };
    if (kind === "animals") {
      this._addForm(main, { kind: "animal", subentryId: item?.id || null, heading: false, onDone: done });
    } else {
      const pick = kind === "cameras" ? { camera: item?.id || NEW } : { person: item?.id || NEW };
      this._addForm(main, { path: [kind, pick], heading: false, onDone: done });
    }
  }

  async _removeAnimal(subentryId) {
    const animal = (this._roster.animals || []).find((a) => a.id === subentryId);
    if (!confirm(`Remove ${animal?.name || "this animal"}? Its tracking and sensors go too.`)) return;
    await this._hass.callWS({ type: "config_entries/subentries/delete", entry_id: this.entryId, subentry_id: subentryId });
    await this._loadRoster(true);
    this._showTab();
  }

  async _afterSave() {
    await this._loadRoster(true);
    // The integration reloads with the new settings; the detector may have a new address.
    if (this._tab === "detector") setTimeout(() => this._refreshVision(), 1500);
  }

  // ---- detector ----

  async _refreshVision() {
    clearTimeout(this._timer);
    if (!this._hass || !this.isConnected || this._tab !== "detector") return;
    try {
      this._vision = await this._hass.callWS({ type: `${DOMAIN}/vision` });
    } catch (err) {
      this._vision = { loaded: false, error: { code: "ws", message: err.message || String(err) } };
    }
    // Don't redraw under the user's typing.
    if (this.shadowRoot.activeElement?.id !== "min_new") this._renderVision();
    if (!document.hidden) this._timer = setTimeout(() => this._refreshVision(), this._vision?.train?.running ? 5000 : 30000);
  }

  async _save(changes) {
    this._settingsError = "";
    try {
      const answer = await this._hass.callWS({ type: `${DOMAIN}/vision/settings`, changes });
      if (this._vision?.train) this._vision.train.settings = answer.settings;
    } catch (err) {
      this._settingsError = err.message || String(err);
    }
    this._renderVision();
    this._refreshVision();
  }

  async _train() {
    if (!confirm("Start a training round now? It takes from tens of minutes to a few hours.")) return;
    this._busy = true;
    this._message = "";
    this._renderVision();
    try {
      await this._hass.callWS({ type: `${DOMAIN}/vision/train` });
    } catch (err) {
      this._message = err.message || String(err);
    }
    this._busy = false;
    this._refreshVision();
  }

  _problem(d) {
    if (!d) return `<div class="card"><span class="muted">Loading…</span></div>`;
    if (d.error?.code === "not_configured") {
      return `<div class="card"><h3>No local detector yet</h3>
        <div class="desc">Run the vision service on a NAS or any Docker host, then enter its address below.
        It checks every frame before the AI does, and learns from your answers each week.</div>
        <a class="btn quiet" href="https://github.com/b2dmx/neighborhood-watch/blob/main/docs/reference.md#local-detector-optional" target="_blank" rel="noreferrer">How to set it up</a></div>`;
    }
    if (d.error) {
      return `<div class="card"><h3>Can't reach the detector</h3>
        <div class="desc">${esc(d.url)} didn't answer: ${esc(d.error.message)}</div>
        <div class="actions"><button class="btn" id="retry">Try again</button></div></div>`;
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
    return `<section class="card"><h3>Past rounds</h3>
      <div class="hint" style="margin: -6px 0 10px">A new model replaces the current one only when it misses fewer animals and makes at least 10% fewer mistakes in all.</div>
      <div class="table-wrap"><table>
        <thead><tr><th>Round</th><th>Date</th><th>Ran on</th><th>Time</th><th>Missed animals</th><th>False alarms</th><th>Result</th></tr></thead>
        <tbody>${rows}</tbody></table></div></section>`;
  }

  _settings(s) {
    if (!s) {
      return `<section class="card"><h3>Training settings</h3><div class="hint">Update the vision service (pull its image again) to change its training settings here.</div></section>`;
    }
    const off = s.train_enabled ? "" : "disabled";
    const days = DAYS.map((d) => `<option value="${d}" ${d === s.train_day ? "selected" : ""}>${cap(d)}</option>`).join("");
    const hours = Array.from({ length: 24 }, (_, h) => `<option value="${h}" ${h === s.train_hour ? "selected" : ""}>${hourName(h)}</option>`).join("");
    const dev = (v, label) => `<label><input type="radio" name="device" value="${v}" ${s.train_device === v ? "checked" : ""}>${label}</label>`;
    return `<section class="card"><h3>Training settings</h3>
      <div class="row inline"><span class="label" id="l-enabled">Weekly training</span>
        <label class="switch"><input type="checkbox" id="enabled" aria-labelledby="l-enabled" ${s.train_enabled ? "checked" : ""}><span></span></label></div>
      <div class="row"><span class="label">When</span>
        <span class="group"><select id="day" aria-label="Training day" ${off}>${days}</select><span>from</span>
        <select id="hour" aria-label="Training hour" ${off}>${hours}</select></span></div>
      <div class="row"><span class="label" id="l-min">New confirmed photos needed</span>
        <input type="number" id="min_new" min="1" max="10000" value="${s.min_new}" aria-labelledby="l-min" ${off}></div>
      <div class="row"><span class="label" id="l-device">Train on</span>
        <span class="seg" role="radiogroup" aria-labelledby="l-device">${dev("auto", "GPU")}${dev("cpu", "CPU")}</span></div>
      <div class="hint">These live on the vision service. GPU uses the Intel GPU when it can, and the CPU if it can't. Changes apply to the next round.</div>
      ${this._settingsError ? `<div class="error">${esc(this._settingsError)}</div>` : ""}
    </section>`;
  }

  _renderVision() {
    const box = this._visionBox;
    if (!box) return;
    const d = this._vision;
    let html = this._problem(d);
    if (!html) {
      const h = d.health, t = d.train;
      const f = this._progress(t);
      const chip = t.running ? ["Training", "warn"] : ["Ready", "good"];
      html = `
        <section class="card">
          <div class="head"><h3>Detector</h3><span class="chip"><span class="dot ${chip[1]}"></span>${chip[0]}</span></div>
          <div class="grid">
            <div><div class="label">Model</div><div class="value">${h.trained ? `Trained on your photos (${esc(h.model)})` : `MegaDetector (${esc(h.model)})`}</div></div>
            <div><div class="label">Runs on</div><div class="value">${esc(devName(h.device))}</div></div>
            <div><div class="label">Hardware found</div><div class="value">${esc((h.available || []).map(devName).join(", ") || "—")}</div></div>
            <div><div class="label">Right now</div><div class="value">${h.loaded ? "Awake" : "Asleep until the next photo"}</div></div>
          </div>
          <div class="actions" style="margin-top: 14px">
            <a class="btn quiet" href="${esc(d.url)}/" target="_blank" rel="noreferrer">Open the vision page</a>
          </div>
        </section>
        <section class="card">
          <div class="head"><h3>Training</h3>
            <button class="btn" id="train" ${t.running || this._busy || !t.dataset ? "disabled" : ""}
              title="${t.dataset ? "" : "No dataset folder is mounted"}">Train now</button></div>
          <div class="hint" style="margin-top: 0">${esc(this._stageText(t))}</div>
          ${f === null ? "" : `<div class="bar"><div style="width: ${Math.round(f * 100)}%"></div></div>`}
          ${this._message ? `<div class="error">${esc(this._message)}</div>` : ""}
        </section>
        ${this._settings(t.settings)}
        ${this._history(d.history)}`;
    }
    box.innerHTML = html;
    const $ = (id) => box.querySelector(`#${id}`);
    $("retry")?.addEventListener("click", () => { this._vision = null; this._renderVision(); this._refreshVision(); });
    $("train")?.addEventListener("click", () => this._train());
    $("enabled")?.addEventListener("change", (e) => this._save({ train_enabled: e.target.checked }));
    $("day")?.addEventListener("change", (e) => this._save({ train_day: e.target.value }));
    $("hour")?.addEventListener("change", (e) => this._save({ train_hour: Number(e.target.value) }));
    $("min_new")?.addEventListener("change", (e) => {
      e.target.blur();
      this._save({ min_new: Math.round(Number(e.target.value)) });
    });
    for (const r of box.querySelectorAll("input[name=device]")) {
      r.addEventListener("change", (e) => this._save({ train_device: e.target.value }));
    }
  }
}

if (!customElements.get("neighborhood-watch-panel")) {
  customElements.define("neighborhood-watch-panel", NeighborhoodWatchPanel);
}

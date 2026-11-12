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
// "Every day", "Weekdays", or the days themselves.
const dayList = (days) =>
  days.length === 7 ? "every day"
  : days.length === 5 && !days.includes("saturday") && !days.includes("sunday") ? "weekdays"
  : days.map((d) => cap(d).slice(0, 3)).join(", ");
const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : "");
const hourName = (h) => new Date(2000, 0, 1, h).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
const devName = (d) => ({ GPU: "Intel GPU", NPU: "NPU", CPU: "CPU", xpu: "GPU", cpu: "CPU" })[d] || d || "—";
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const MENU = "M3,6H21V8H3V6M3,11H21V13H3V11M3,16H21V18H3V16Z";
const BACK = "M20,11V13H8L13.5,18.5L12.08,19.92L4.16,12L12.08,4.08L13.5,5.5L8,11H20Z";
const CHEVRON = "M8.59,16.58L13.17,12L8.59,7.41L10,6L16,12L10,18L8.59,16.58Z";
const NEW = "add_new"; // the options flow's "+ Add ..." choice
// Training: the Photos for the first round default (an older service doesn't send it), and
// the hints that explain it. A stopped or failed round doesn't count as the first.
const MIN_FIRST = 40;
const firstRound = (rounds) => !(rounds || []).some((r) => r.confirmed != null && !r.error && !r.stopped);
const NO_DATASET =
  "The vision service can't see your saved photos. In its compose file, mount the folder from Detector → Saved photos at /dataset.";
// Training stages (vision/train.py) in plain words.
const STAGES = { starting: "Starting", labelling: "Marking animals in the photos", training: "Training" };
const stageName = (stage) => STAGES[stage] || cap(stage || "");
const SAVES = " Saves straight away.";
// The switches the page shows as toggles (switch.py), and what each does.
const CRITICAL_HINT = {
  animals: "Rings through silent mode at the cameras set below." + SAVES,
  cameras: "Everything seen here rings through silent mode, except what's listed below." + SAVES,
  people: "Off: their critical alerts arrive as normal ones." + SAVES,
};
const ALERTS_HINT = "Off: no alerts for this animal; it's still tracked." + SAVES;
const MISSING_HINT =
  "Lost pet? Every possible sighting, at any camera and any hour, rings at full volume for people with Gets missing-mode alerts." + SAVES;
const CATCH_ALL_HINT =
  "When on, these ring through silent mode at the cameras picked under Critical alerts at in Pests and Other animals and motion below. They're the same switches as on the Neighborhood Watch device." + SAVES;
const CATCH_ALLS = [["pest", "Pests"], ["unknown", "Unknown animals"], ["other", "Other wildlife"], ["motion", "Unsure motion"]];
const SCHEDULE_HINT =
  "Saved on the vision service itself, not in Home Assistant. The detector's address, mode and threshold are in Local detector above. Times are the NAS's local time." + SAVES;
const DETECT_HINT = "GPU uses the Intel GPU when it can, and the CPU if it can't (in Trains with too). The detector restarts on it.";
const LENGTH_HINT = "Longer can be a little more accurate but takes hours more.";
const SIZE_HINT = "Sharper finds small animals better but trains slower.";
// Detector settings typed into number boxes: the page doesn't redraw under them.
const NUMBER_BOXES = ["min_new", "min_first"];
const LEAVE = "Leave without saving? Your changes here will be lost.";
const RESTARTED = "Home Assistant restarted, so the form was reopened. Make your change again.";
// Form data compared whatever order its keys are in (has anything been changed?).
const stable = (v) =>
  JSON.stringify(v ?? {}, (_k, x) =>
    x && typeof x === "object" && !Array.isArray(x)
      ? Object.fromEntries(Object.entries(x).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
      : x);
const errText = (err) => err?.body?.message || err?.message || String(err);

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
    "Sightings the AI wasn't sure about, and the ones you haven't answered yet. Your answers fix tracking and are saved with the photos the detector learns from.",
  cameras:
    "Each camera is checked by the AI when its motion sensor fires (or on a timer). Open one to change what starts a check, how often, and what it does when a pest shows up. Below the list: the hours the cameras are watched.",
  animals:
    "Your own animals first: how to recognise each one and where it alerts. Below them: pests, and every other animal (wildlife, unknown cats and dogs, unsure motion). Those are recognised without adding them.",
  people: "Who gets alerts, on which phone, and when to keep quiet.",
  alerts: "Alerts on or off, how sure the AI has to be, and pictures in alerts. Who gets them is under People; where each animal alerts is under Animals.",
  ai: "Which AI models look at the photos, and what they're told.",
  detector:
    "An optional local detector that checks every frame before the AI does and learns from your answers every week.",
};
const FORMS = {
  cameras: [["schedule"]],
  animals: [["pests"], ["catch_alls"]],
  alerts: [["alerts"], ["notifications"]],
  ai: [["models"], ["advanced", "identification"]],
  detector: [["advanced", "detector"], ["advanced", "training"]],  // the vision status goes below them
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
  .tabs { position: relative; display: flex; gap: 4px; padding: 0 12px; overflow-x: auto; scrollbar-width: none; }
  /* More tabs off the right edge (phones): fade it out. */
  .tabs.more {
    -webkit-mask-image: linear-gradient(to right, #000 calc(100% - 32px), transparent);
    mask-image: linear-gradient(to right, #000 calc(100% - 32px), transparent);
  }
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
  .card > .hint + .row { margin-top: 14px; }
  .card > h4 { font-size: 13px; font-weight: 500; color: var(--secondary-text-color); margin: 20px 0 10px; }
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
  header button:focus-visible { outline: 2px solid currentColor; outline-offset: -3px; }
  .item:focus-visible { outline-offset: -2px; }
  h2[tabindex="-1"]:focus { outline: none; }
  select, input[type=number] {
    font: inherit; font-size: 14px; color: var(--primary-text-color); background: var(--card-background-color);
    border: 1px solid var(--nw-line); border-radius: 10px; padding: 6px 10px;
  }
  select option { background-color: var(--primary-background-color, #111); color: var(--primary-text-color); }
  input[type=number] { width: 90px; }
  .seg { display: inline-flex; border: 1px solid var(--nw-line); border-radius: 99px; overflow: hidden; }
  .seg label { padding: 6px 16px; cursor: pointer; font-size: 14px; font-weight: 500; }
  .seg label + label { border-left: 1px solid var(--nw-line); }
  .seg input { position: absolute; opacity: 0; pointer-events: none; }
  .seg label:has(input:checked) { background: var(--nw-accent); color: var(--nw-on-accent); }
  .seg.days { max-width: 100%; }
  .seg.days label { padding: 6px 10px; flex: 1 1 0; text-align: center; min-width: 0; }
  .switch { position: relative; width: 44px; height: 26px; flex: none; }
  .switch input { opacity: 0; width: 100%; height: 100%; margin: 0; position: absolute; cursor: pointer; z-index: 1; }
  .switch span {
    position: absolute; inset: 0; border-radius: 99px; transition: background .15s;
    /* Off: the track at half strength (the knob stays solid). */
    background: color-mix(in srgb, var(--switch-unchecked-track-color, var(--secondary-text-color)) 50%, transparent);
  }
  .switch span::after {
    content: ""; position: absolute; top: 3px; left: 3px; width: 20px; height: 20px; border-radius: 50%;
    background: var(--switch-unchecked-button-color, var(--primary-background-color)); transition: transform .15s;
  }
  .switch input:checked + span { background: var(--nw-accent); }
  .switch input:checked + span::after { transform: translateX(18px); background: var(--switch-checked-button-color, var(--nw-on-accent)); }
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
  .toggle { display: flex; align-items: center; gap: 12px; }
  .toggle .text { flex: 1; min-width: 0; }
  .toggle .hint { margin: 2px 0 0; }
  .card > .toggle + .toggle { margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--nw-line); }
  .chip.missing { color: var(--nw-bad); margin-top: 4px; }
  .chip.missing .dot { background: var(--nw-bad); }
  .grid .toggle { gap: 10px; font-size: 14px; }
  .preview { display: flex; justify-content: center; margin-bottom: 12px; }
  .preview neighborhood-watch-sprite { width: 96px; height: 96px; }
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
    this.closed = false;
  }
  async begin() {
    const result = await this.hass.callApi("POST", this.base, this.start);
    this.id = result.flow_id;
    if (this.closed) this.abort(); // closed while it was opening: don't leave the flow behind
    return result;
  }
  async step(input) {
    return this.hass.callApi("POST", `${this.base}/${this.id}`, input);
  }
  abort() {
    this.closed = true;
    if (!this.id) return;
    const id = this.id;
    this.id = null;
    this.hass.callApi("DELETE", `${this.base}/${id}`).catch(() => {});
  }
}

// One settings form: a flow walked to its form, drawn with ha-form, saved with the flow.
class FlowForm {
  constructor(panel, { kind = "options", path = [], subentryId = null, heading = true, onDone = null, preview = false }) {
    this.panel = panel;
    this.preview = preview; // the card picture, drawn live from the form
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

  // Changed since the form was drawn (and not saved yet).
  get dirty() {
    return this.result?.type === "form" && this._clean !== undefined && stable(this.data) !== this._clean;
  }

  async open() {
    this.flow?.abort();
    this.flow = new Flow(this.panel._hass, this.kind, this.panel.entryId, this.subentryId);
    this.result = null;
    this._clean = undefined;
    this._touched = false; // anything submitted that may change a setting
    try {
      const flow = this.flow;
      let result = await flow.begin();
      for (const step of this.path) {
        if (flow.closed) return; // the form was closed (another tab) while it was opening
        if (result.type !== "menu" && result.type !== "form") break;
        result = await flow.step(typeof step === "string" ? { next_step_id: step } : step);
      }
      if (flow.closed) return;
      this.show(result);
    } catch (err) {
      this.el.innerHTML = `<div class="error" role="alert"></div>
        <div class="actions" style="margin-top: 12px"><button class="btn quiet">Try again</button></div>`;
      this.el.firstChild.textContent = `Couldn't open these settings: ${errText(err)}`;
      this.el.querySelector("button").addEventListener("click", () => {
        this.el.innerHTML = `<span class="muted">Loading…</span>`;
        this.open();
      });
    }
  }

  show(result) {
    const before = this.result, typed = this.data;
    this.result = result;
    this._clean = undefined;
    if (result.type === "create_entry" || (result.type === "abort" && /successful/.test(result.reason || ""))) {
      this.flow.id = null; // finished: nothing to abort
      const changed = this._touched;
      if (this.onDone) return this.onDone(result, changed);
      this.saved = true;
      this.panel._afterSave(changed);
      return this.open(); // a fresh form with the saved values
    }
    if (result.type === "abort") {
      this.flow.id = null;
      const reason = this.t(`component.${DOMAIN}.${this.kind === "animal" ? "config_subentries.animal" : "options"}.abort.${result.reason}`);
      this.el.innerHTML = `<div class="error" role="alert"></div>`;
      this.el.firstChild.textContent = reason || result.reason;
      return;
    }
    if (result.type !== "form") {
      this.el.innerHTML = `<div class="error" role="alert">Unexpected step: ${esc(result.type)}</div>`;
      return;
    }
    const placeholders = result.description_placeholders || {};
    const title = this.t(`${this.prefix}.title`, placeholders);
    const description = this.t(`${this.prefix}.description`, placeholders);
    const fresh = initialData(result.data_schema);
    this._clean = stable(fresh);
    // The same step back with errors: keep what was typed (never a Remove that was asked for).
    const again = before?.type === "form" && before.step_id === result.step_id && Object.keys(result.errors || {}).length;
    if (again && typed) {
      const { remove, ...kept } = typed;
      this.data = { ...fresh, ...kept };
    } else this.data = fresh;
    // A camera's or person's Remove toggle is a button here (with a confirmation).
    const removable = this.kind === "options" && ["camera", "person"].includes(result.step_id) &&
      result.data_schema.some((f) => f.name === "remove");
    const remove = this.kind === "animal" && this.subentryId ? "Remove animal"
      : removable ? `Remove ${result.step_id}` : "";
    this.el.innerHTML = `
      ${this.heading && title ? `<h3></h3>` : ""}
      ${description ? `<div class="desc"><ha-markdown breaks></ha-markdown></div>` : ""}
      <div class="error base" role="alert" hidden></div>
      ${this.preview ? `<div class="preview" hidden></div>` : ""}
      <ha-form></ha-form>
      <div class="actions end">
        <span class="saved" role="status" ${this.saved ? "" : "hidden"}>Saved</span>
        ${remove ? `<button class="btn danger" data-act="remove">${remove}</button>` : ""}
        <button class="btn" data-act="save">${result.last_step === false || result.step_id.endsWith("_add") ? "Next" : "Save"}</button>
      </div>`;
    if (this.heading && title) this.el.querySelector("h3").textContent = title;
    if (description) this.el.querySelector("ha-markdown").content = description;
    this.saved = false;
    const form = this.el.querySelector("ha-form");
    form.hass = this.panel._hass;
    form.schema = removable ? result.data_schema.filter((f) => f.name !== "remove") : result.data_schema;
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
      this.drawPreview();
    });
    this._previewKey = null;
    this.drawPreview();
    const base = result.errors?.base;
    const box = this.el.querySelector(".error.base");
    if (base || this._notice) {
      box.hidden = false;
      box.textContent = base ? form.computeError(base) : this._notice;
    }
    this._notice = "";
    if (again) {
      // Show what needs fixing: the message at the top, or the first field with an error.
      const spot = () => {
        const field = !base && form.shadowRoot?.querySelector("ha-alert");
        (field || (base ? box : this.el)).scrollIntoView({ behavior: "smooth", block: "center" });
      };
      Promise.resolve(form.updateComplete).then(() => requestAnimationFrame(spot));
    }
    this.el.querySelector("[data-act=save]").addEventListener("click", () => this.submit());
    const removeBtn = this.el.querySelector("[data-act=remove]");
    removeBtn?.addEventListener("click", () =>
      removable ? this.panel._removeItem(this) : this.panel._removeAnimal(this.subentryId, removeBtn));
  }

  // The card picture for the coat or look picked so far (cats only: the pictures are cats).
  drawPreview() {
    const box = this.el.querySelector(".preview");
    if (!box) return;
    const d = this.data || {};
    const look = d.looks?.appearance;
    const appearance = look && typeof look === "object" && !Array.isArray(look) && Object.keys(look).length ? look : null;
    const cat = String(d.species || "cat").trim().toLowerCase() === "cat";
    const config = cat && (d.sprite || appearance)
      ? { ...(d.sprite ? { preset: d.sprite } : {}), ...(appearance ? { appearance } : {}), size: 96 }
      : null;
    if (config && !customElements.get("neighborhood-watch-sprite")) {
      this.panel._loadCard().then(() => this.drawPreview());
      return;
    }
    box.hidden = !config;
    const key = JSON.stringify(config);
    if (!config || key === this._previewKey) return;
    this._previewKey = key;
    const sprite = document.createElement("neighborhood-watch-sprite");
    try {
      sprite.setConfig(config);
      box.replaceChildren(sprite);
    } catch (err) {
      box.hidden = true;
    }
  }

  label(schema, options, field, sectionField) {
    const path = options?.path || [];
    if (schema.type === "expandable") return this.t(`${this.prefix}.sections.${schema.name}.${sectionField}`) || (field === "data" ? schema.name : "");
    const key = path.length ? `${this.prefix}.sections.${path[0]}.${field}.${schema.name}` : `${this.prefix}.${field}.${schema.name}`;
    // Field help can use the step's placeholders too (e.g. an example address).
    return this.t(key, this.result?.description_placeholders || {}) || (field === "data" ? schema.name : "");
  }

  errors(errors) {
    if (!errors) return {};
    const out = { ...errors };
    delete out.base;
    return out;
  }

  async submit(data = this.data) {
    const buttons = [...this.el.querySelectorAll("[data-act]")];
    for (const b of buttons) b.disabled = true;
    const r = this.result || {};
    const removing = data !== this.data && data.remove;
    // A Next step, a change, or a Remove: the integration may reload once it's saved.
    if (data !== this.data || this.dirty || r.last_step === false || r.step_id?.endsWith("_add")) this._touched = true;
    try {
      this.show(await this.flow.step(data));
    } catch (err) {
      for (const b of buttons) b.disabled = false;
      const message = errText(err);
      if (this.flow?.closed) return; // closed meanwhile: nothing to show, nothing to reopen
      if (err?.status_code === 404 || /invalid flow|unknown flow/i.test(message)) {
        // The flow is gone (Home Assistant restarted): start the form again.
        this.flow.id = null;
        this._notice = RESTARTED;
        return this.open();
      }
      if (removing) {
        alert(`Couldn't remove ${this.panel._editing?.item?.name || "it"}: ${message}`);
        return;
      }
      const box = this.el.querySelector(".error.base");
      box.hidden = false;
      box.textContent = message;
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
    this._toggles = []; // switch toggles on the page, kept in step with their switches
    this._chips = []; // the animals list's "Missing" chips, the same
    this._vision = null;
    this._busy = false;
    this._message = "";
    this._settingsError = "";
    this._onVisible = () => {
      if (!document.hidden && this._tab === "detector") this._refreshVision();
    };
    // Closing or reloading the page with unsaved changes: the browser asks first.
    this._onUnload = (ev) => {
      if (!this._forms.some((f) => f.dirty)) return;
      ev.preventDefault();
      ev.returnValue = "";
    };
    this._onTabsScroll = () => this._fadeTabs();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (this._card) this._card.hass = hass;
    for (const f of this._forms || []) {
      const form = f.el.querySelector("ha-form");
      if (form) form.hass = hass;
    }
    this._syncToggles();
    this._reviewCount();
    if (first && this.isConnected) this._start();
  }

  // "Review (N)" while sightings are waiting: the To review sensor's count, live.
  _reviewCount() {
    const tab = this.shadowRoot.querySelector('.tab[data-tab="review"]');
    if (!tab || !this._hass) return;
    const entities = this._hass.entities;
    if (entities !== this._entitiesRef) {
      this._entitiesRef = entities;
      this._reviewId = Object.values(entities || {}).find((e) => e.platform === DOMAIN && e.translation_key === "to_review")?.entity_id;
    }
    const n = Number(this._reviewId && this._hass.states[this._reviewId]?.state);
    const label = n > 0 ? `Review (${n})` : "Review";
    if (tab.textContent !== label) tab.textContent = label;
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
    window.addEventListener("beforeunload", this._onUnload);
    window.addEventListener("resize", this._onTabsScroll);
    if (this._hass) this._start();
  }

  disconnectedCallback() {
    document.removeEventListener("visibilitychange", this._onVisible);
    window.removeEventListener("beforeunload", this._onUnload);
    window.removeEventListener("resize", this._onTabsScroll);
    clearTimeout(this._timer);
    this._closeForms();
  }

  // Unsaved changes in a form here: ask before they're thrown away.
  _leaveOk() {
    return !this._forms.some((f) => f.dirty) || confirm(LEAVE);
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
    // After a save the integration reloads with the new settings: wait until it's back, and
    // is the new run (its marker changes), not the old one that hasn't stopped yet.
    const before = this._roster?.loaded_at;
    for (let i = 0; i < (afterSave ? 20 : 1); i++) {
      if (i) await new Promise((r) => setTimeout(r, 500));
      try {
        this._roster = await this._hass.callWS({ type: `${DOMAIN}/roster` });
      } catch (err) {
        this._roster = { loaded: false };
      }
      if (this._roster.loaded && (!afterSave || before === undefined || this._roster.loaded_at !== before)) break;
    }
    this.entryId = this._roster.entry_id || this.entryId;
  }

  _shell() {
    if (this.shadowRoot.querySelector("header")) return;
    const tabs = TABS.map((t) => {
      const on = t.id === this._tab;
      return `<button class="tab" role="tab" id="tab-${t.id}" aria-controls="nw-main" data-tab="${t.id}" aria-selected="${on}" tabindex="${on ? 0 : -1}">${t.label}</button>`;
    }).join("");
    this.shadowRoot.innerHTML = `<style>${STYLE}</style>
      <header>
        <div class="toolbar">
          <button class="icon-btn" id="menu" aria-label="Open the sidebar" ${this._narrow ? "" : "hidden"}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${MENU}"/></svg></button>
          <span class="title">Neighborhood Watch</span>
        </div>
        <nav class="tabs" role="tablist" aria-label="Neighborhood Watch">${tabs}</nav>
      </header>
      <main id="nw-main" role="tabpanel" aria-labelledby="tab-${this._tab}"><span class="muted">Loading…</span></main>`;
    this.shadowRoot.getElementById("menu").addEventListener("click", () => {
      this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true }));
    });
    for (const b of this.shadowRoot.querySelectorAll(".tab")) {
      b.addEventListener("click", () => this._select(b.dataset.tab));
    }
    // Arrow keys move between the tabs (one tab stop for the whole strip).
    const strip = this.shadowRoot.querySelector(".tabs");
    strip.addEventListener("keydown", (ev) => {
      const ids = TABS.map((t) => t.id);
      const at = ids.indexOf(ev.target.dataset?.tab);
      if (at < 0) return;
      const to = { ArrowLeft: at - 1, ArrowRight: at + 1, Home: 0, End: ids.length - 1 }[ev.key];
      if (to === undefined) return;
      ev.preventDefault();
      const next = ids[(to + ids.length) % ids.length];
      this._select(next);
      this.shadowRoot.getElementById(`tab-${this._tab}`)?.focus();
    });
    strip.addEventListener("scroll", this._onTabsScroll, { passive: true });
    this._reviewCount();
    requestAnimationFrame(() => this._showSelectedTab(false));
  }

  _select(tab) {
    if (tab === this._tab && !this._editing) return;
    if (!this._leaveOk()) return;
    this._tab = tab;
    history.replaceState(history.state, "", `${location.pathname}#${tab}`);
    for (const b of this.shadowRoot.querySelectorAll(".tab")) {
      const on = b.dataset.tab === tab;
      b.setAttribute("aria-selected", String(on));
      b.tabIndex = on ? 0 : -1;
    }
    this._main().setAttribute("aria-labelledby", `tab-${tab}`);
    this._showSelectedTab(true);
    this._showTab();
  }

  // Phones: the picked tab scrolled into the strip's view, and a fade while more are off the edge.
  _showSelectedTab(smooth) {
    const strip = this.shadowRoot.querySelector(".tabs");
    const b = strip?.querySelector(`#tab-${this._tab}`);
    if (b && strip.scrollWidth > strip.clientWidth) {
      const left = b.offsetLeft - (strip.clientWidth - b.offsetWidth) / 2;
      strip.scrollTo({ left: Math.max(0, left), behavior: smooth ? "smooth" : "auto" });
    }
    this._fadeTabs();
  }

  _fadeTabs() {
    const strip = this.shadowRoot.querySelector(".tabs");
    if (strip) strip.classList.toggle("more", strip.scrollLeft + strip.clientWidth < strip.scrollWidth - 2);
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
    this._toggles = [];
    this._chips = [];
    this._visionBox = null;
    const main = this._main();
    main.innerHTML = "";
    if (!this._roster?.loaded) {
      main.innerHTML = `<div class="card"><h2>Neighborhood Watch isn't running</h2>
        <div class="hint">Set it up, or check its status, under Settings → Devices &amp; services.</div>
        <div class="actions" style="margin-top: 12px"><button class="btn quiet">Try again</button></div></div>`;
      const retry = main.querySelector("button");
      retry.addEventListener("click", async () => {
        retry.disabled = true;
        await this._loadRoster();
        this._showTab();
      });
      return;
    }
    this._intro(main);
    if (this._tab === "review") return this._showReview(main);
    if (this._tab === "animals") this._secondOpinionNotice(main);
    if (this._tab === "cameras" || this._tab === "people" || this._tab === "animals") this._showList(main);
    if (this._tab === "animals") this._catchAllToggles(main);
    for (const path of FORMS[this._tab] || []) this._addForm(main, { path });
    if (this._tab === "detector") {
      // Below the Local detector and Saved photos forms.
      this._visionBox = document.createElement("div");
      main.append(this._visionBox);
      this._renderVision();
      this._refreshVision();
    }
  }

  // Back from an edit page: the list, with the item that was open focused.
  _backToList(id) {
    this._showTab();
    const main = this._main();
    const item = [...main.querySelectorAll(".item")].find((b) => b.dataset.id === String(id ?? ""));
    const target = item || main.querySelector(".list-head h2");
    if (!target) return;
    if (!item) target.tabIndex = -1;
    target.focus();
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
      if (this._cardFailed) {
        main.insertAdjacentHTML("beforeend", `<div class="card error" role="alert">The review list couldn't load. Reload the page; if it keeps happening, reinstall Neighborhood Watch.</div>`);
        return;
      }
      main.insertAdjacentHTML("beforeend", `<div class="card muted">The review list is loading…</div>`);
      this._loadCard();
      customElements.whenDefined("neighborhood-watch-card").then(() => this._tab === "review" && !this._editing && this._showTab());
      return;
    }
    const card = document.createElement("neighborhood-watch-card");
    const empty = this._roster.dataset === false
      ? "Photos aren't being saved, so there's nothing to review. Turn on Save checked photos under Detector → Saved photos."
      : "Nothing to review. You're all caught up.";
    // fold: false: the list is the whole page here, so it never folds away.
    card.setConfig({ hide_title: true, sections: ["review"], review_limit: 30, fold: false, empty });
    card.hass = this._hass;
    this._card = card;
    main.append(card);
  }

  // The dashboard card's script (the review list and the animal pictures).
  _loadCard() {
    // The same address as Home Assistant's own copy, so it never loads twice.
    if (!this._cardLoading && !customElements.get("neighborhood-watch-card")) {
      const failed = () => {
        this._cardFailed = true;
        if (this._tab === "review" && !this._editing) this._showTab();
      };
      this._cardLoading = this._cardUrl ? import(this._cardUrl).catch(failed) : Promise.resolve().then(failed);
    }
    return customElements.whenDefined("neighborhood-watch-sprite");
  }

  // ---- cameras, people, animals ----

  // One on/off row for one of the integration's switches, live from Home Assistant.
  _toggle(entityId, label, hint) {
    const row = document.createElement("div");
    row.className = "toggle";
    row.innerHTML = `<span class="text"><div></div>${hint ? `<div class="hint"></div>` : ""}</span>
      <label class="switch"><input type="checkbox"><span></span></label>`;
    row.querySelector(".text div").textContent = label;
    if (hint) row.querySelector(".hint").textContent = hint;
    const input = row.querySelector("input");
    input.setAttribute("aria-label", label);
    input.addEventListener("change", async () => {
      input._changed = Date.now();
      try {
        await this._hass.callService("switch", input.checked ? "turn_on" : "turn_off", { entity_id: entityId });
      } catch (err) {
        input._changed = 0;
        this._syncToggles(); // back to what the switch says
      }
      setTimeout(() => this._syncToggles(), 2100);
    });
    this._toggles.push({ input, entityId });
    this._syncToggles();
    return row;
  }

  _syncToggles() {
    for (const { input, entityId } of this._toggles || []) {
      const state = this._hass?.states[entityId]?.state;
      // Just flipped here: its own update is on the way, so other updates don't flip it back.
      if (Date.now() - (input._changed || 0) > 2000) input.checked = state === "on";
      input.disabled = state !== "on" && state !== "off";
    }
    // The animals list's "Missing" chips.
    for (const { el, entityId } of this._chips || []) el.hidden = this._hass?.states[entityId]?.state !== "on";
  }

  // The hub's critical switches, above the Pests and Other animals forms they go with.
  _catchAllToggles(main) {
    const ids = this._roster.catch_all_critical || {};
    const card = document.createElement("section");
    card.className = "card";
    card.innerHTML = `<h3>Critical alerts</h3>
      <div class="desc">${esc(CATCH_ALL_HINT)}</div><div class="grid"></div>`;
    const grid = card.querySelector(".grid");
    for (const [key, label] of CATCH_ALLS) if (ids[key]) grid.append(this._toggle(ids[key], label));
    if (grid.children.length) main.append(card);
  }

  _secondOpinionNotice(main) {
    const animals = this._roster.animals || [];
    if (this._roster.second_opinion || !animals.some((a) => a.needs_second_opinion)) return;
    const card = document.createElement("section");
    card.className = "card";
    card.innerHTML = `<div class="head" style="margin: 0"><span style="flex: 1">Some animals need a Second opinion model to be confirmed. Add one under AI → AI models.</span>
      <button class="btn quiet">Open AI</button></div>`;
    card.querySelector("button").addEventListener("click", () => this._select("ai"));
    main.append(card);
  }

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
        .map((it, i) => `<li><button class="item" data-i="${i}" data-id="${esc(it.id)}">
          <span class="avatar${kind === "cameras" ? " wide" : ""}">${kind === "animals" ? "" : esc((it.name || "?").charAt(0).toUpperCase())}</span>
          <span class="text"><div class="name">${esc(it.name)}</div><div class="sub">${esc(this._subtitle(kind, it))}</div>
            ${kind === "animals" && it.missing ? `<span class="chip missing" hidden><span class="dot"></span>Missing</span>` : ""}</span>
          <svg class="chev" viewBox="0 0 24 24" aria-hidden="true"><path d="${CHEVRON}"/></svg></button></li>`)
        .join("")}</ul>`;
      const sprites = [];
      card.querySelectorAll(".item").forEach((b) => {
        const it = items[Number(b.dataset.i)];
        b.addEventListener("click", () => this._edit(kind, it));
        const chip = b.querySelector(".chip.missing");
        if (chip) this._chips.push({ el: chip, entityId: it.missing });
        if (kind === "animals") sprites.push([b.querySelector(".avatar"), it]);
        else this._picture(b.querySelector(".avatar"), kind === "cameras" ? it.camera : it.person);
      });
      for (const [box, it] of sprites) this._sprite(box, it);
      // The pictures come with the card's script: draw them once it has loaded.
      if (sprites.length && !customElements.get("neighborhood-watch-sprite")) {
        this._loadCard().then(() => {
          for (const [box, it] of sprites) if (box.isConnected) this._sprite(box, it);
        });
      }
      this._syncToggles();
    }
    main.append(card);
  }

  _subtitle(kind, it) {
    if (kind === "cameras") return this._hass.states[it.camera]?.attributes?.friendly_name || it.camera || "";
    if (kind === "people") return it.person ? this._hass.states[it.person]?.attributes?.friendly_name || it.person : "No Home Assistant person picked";
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
      box.replaceChildren(sprite);
    } catch (err) {
      box.textContent = (animal.name || "?").charAt(0).toUpperCase();
    }
  }

  _edit(kind, item) {
    if (!this._leaveOk()) return;
    this._closeForms();
    const main = this._main();
    main.innerHTML = "";
    this._editing = { kind, item };
    this._chips = [];
    const back = document.createElement("div");
    back.className = "back";
    back.innerHTML = `<button><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${BACK}"/></svg>All ${kind}</button>`;
    back.querySelector("button").addEventListener("click", () => {
      if (this._leaveOk()) this._backToList(item?.id);
    });
    main.append(back);
    const h = document.createElement("h2");
    h.textContent = item ? item.name : `New ${{ cameras: "camera", people: "person", animals: "animal" }[kind]}`;
    h.style.margin = "0 4px 12px";
    h.tabIndex = -1;
    main.append(h);
    this._toggles = [];
    // The item's switches, as on its device page (they save straight away).
    const rows = {
      animals: [[item?.alerts, "Alerts", ALERTS_HINT], [item?.missing, "Missing mode", MISSING_HINT], [item?.critical, "Critical alerts", CRITICAL_HINT.animals]],
      cameras: [[item?.critical, "Critical alerts", CRITICAL_HINT.cameras]],
      people: [[item?.critical, "Gets critical alerts", CRITICAL_HINT.people]],
    }[kind].filter(([id]) => id);
    if (rows.length) {
      const card = document.createElement("section");
      card.className = "card";
      for (const [id, label, hint] of rows) card.append(this._toggle(id, label, hint));
      main.append(card);
    }
    const done = async (_result, changed = true) => {
      await this._loadRoster(changed);
      this._backToList(item?.id);
    };
    if (kind === "animals") {
      this._addForm(main, { kind: "animal", subentryId: item?.id || null, heading: false, onDone: done, preview: true });
    } else {
      const pick = kind === "cameras" ? { camera: item?.id || NEW } : { person: item?.id || NEW };
      this._addForm(main, { path: [kind, pick], heading: false, onDone: done });
    }
    h.focus();
  }

  async _removeAnimal(subentryId, button) {
    const animal = (this._roster.animals || []).find((a) => a.id === subentryId);
    const name = animal?.name || "this animal";
    if (!confirm(`Remove ${name}? Its tracking and sensors go too.`)) return;
    if (button) button.disabled = true;
    try {
      await this._hass.callWS({ type: "config_entries/subentries/delete", entry_id: this.entryId, subentry_id: subentryId });
    } catch (err) {
      if (button) button.disabled = false;
      alert(`Couldn't remove ${name}: ${errText(err)}`);
      return;
    }
    await this._loadRoster(true);
    this._backToList();
  }

  // A camera or person is removed by saving its form with Remove on, as Configure does.
  _removeItem(form) {
    const name = this._editing?.item?.name;
    const question = form.result.step_id === "camera"
      ? `Remove ${name || "this camera"}? It is no longer checked, and its sensors go too.`
      : `Remove ${name || "this person"}? They no longer get alerts.`;
    if (confirm(question)) form.submit({ ...form.data, remove: true });
  }

  async _afterSave(changed = true) {
    await this._loadRoster(changed);
    // The integration reloads with the new settings; the detector may have a new address.
    if (this._tab === "detector") setTimeout(() => this._refreshVision(), 1500);
  }

  // ---- detector ----

  async _refreshVision() {
    clearTimeout(this._timer);
    if (!this._hass || !this.isConnected || this._tab !== "detector") return;
    // Polls, saves and coming back to the tab all ask: only the newest answer is used, so an
    // older one can't put back what a save just changed.
    const mine = (this._visionAsk = (this._visionAsk || 0) + 1);
    let answer;
    try {
      answer = await this._hass.callWS({ type: `${DOMAIN}/vision` });
    } catch (err) {
      answer = { loaded: false, error: { code: "ws", message: err.message || String(err) } };
    }
    if (mine !== this._visionAsk) return;
    this._vision = answer;
    clearTimeout(this._timer);
    // Don't redraw under the user's typing.
    if (!NUMBER_BOXES.includes(this.shadowRoot.activeElement?.id)) this._renderVision();
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

  // Train now, Stop training and Undo the last round: confirm, then one websocket command.
  async _trainAction(command, question) {
    if (!confirm(question)) return;
    this._busy = true;
    this._message = "";
    this._renderVision();
    try {
      await this._hass.callWS({ type: `${DOMAIN}/vision/${command}` });
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
        <div class="desc">Run the vision service on a NAS or any Docker host, then enter its address in Local detector above.
        It checks every frame before the AI does, and learns from your answers every week.</div>
        <a class="btn quiet" href="https://github.com/b2dmx/neighborhood-watch/blob/main/docs/reference.md#local-detector-optional" target="_blank" rel="noreferrer">How to set it up</a></div>`;
    }
    if (d.error) {
      const retry = `<div class="actions"><button class="btn" id="retry">Try again</button></div>`;
      const message = esc(d.error.message);
      if (d.error.code === "refused") {
        return `<div class="card" role="alert"><h3>The detector refused</h3>
          <div class="desc">It answered with an error: ${message}</div>${retry}</div>`;
      }
      if (d.error.code === "ws") {
        return `<div class="card" role="alert"><h3>Couldn't get the detector's status from Home Assistant</h3>
          <div class="desc">${message}</div>${retry}</div>`;
      }
      return `<div class="card" role="alert"><h3>Can't reach the detector</h3>
        <div class="desc">${d.url ? esc(d.url) : "The detector"} didn't answer: ${message}</div>${retry}</div>`;
    }
    return "";
  }

  _stageText(t, rounds, outdated) {
    const st = t.status || {};
    const s = t.settings;
    if (t.running) {
      if (t.stopping) return "Stopping…";
      if (st.stage === "training" && st.epochs) return `Training: pass ${st.epoch ?? 0} of ${st.epochs}`;
      if (st.stage === "labelling" && st.total) return `Marking animals in the photos: ${st.done ?? 0} of ${st.total}`;
      return stageName(st.stage || "starting");
    }
    const enabled = s ? s.train_enabled : t.enabled;
    let when = "Training is off.";
    // Before the first round it waits for enough answered animal photos instead. (An out-of-date
    // service has no history, so whether it's the first round isn't known.)
    const need = firstRound(rounds) && !outdated
      ? `there are ${s?.min_first ?? MIN_FIRST} answered animal photos`
      : `${s?.min_new} new photos are answered`;
    const so = t.answered_new != null && !firstRound(rounds) ? ` (${t.answered_new} so far)` : "";
    if (enabled && s && s.train_when === "count") when = `Next round: as soon as ${need}${so}.`;
    else if (enabled && s) when = `Next round: ${dayList(s.train_days || [s.train_day])} from ${hourName(s.train_hour)}, once ${need}.`;
    else if (enabled) when = t.schedule ? `Next round: ${cap(t.schedule)}.` : "";
    if (!st.state) return when || "Training is on.";
    const last = {
      skipped: "Last round skipped", done: "Last round finished", error: "Last round failed", stopped: "Last round stopped",
    }[st.state] || "Last round";
    const at = st.finished || st.started;
    const date = at ? " " + new Date(at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "";
    const stage = st.stage && st.state !== "stopped" ? `: ${stageName(st.stage)}` : "";
    return `${last}${date}${stage}.${when ? ` ${when}` : ""}`;
  }

  _progress(t) {
    const st = t.status || {};
    if (t.running && st.stage === "training" && st.epochs) return st.epoch / st.epochs;
    if (t.running && st.stage === "labelling" && st.total) return st.done / st.total;
    return null;
  }

  _history(rounds) {
    if (!rounds) return "";
    // up: more is better (named right); otherwise fewer is (misses, false alarms).
    const cell = (b, a, key, up = false) => {
      const x = b?.[key], y = a?.[key];
      if (y === undefined) return esc(x ?? "—");
      const better = up ? y > x : y < x, worse = up ? y < x : y > x;
      return `${esc(x)} → <span class="${better ? "better" : worse ? "worse" : ""}">${esc(y)}</span>`;
    };
    const rows = rounds.length
      ? rounds.slice().reverse().map((r) => {
        const result = r.error ? `<span class="worse">Failed</span>` : r.stopped ? "Stopped"
          : r.promoted ? (r.undone ? "Undone" : `<span class="better">New model</span>`) : "Kept the old one";
        const date = r.time ? new Date(r.time).toLocaleDateString([], { dateStyle: "medium" }) : "—";
        const mins = r.minutes != null ? `${Math.round(r.minutes)} min` : "—";
        return `<tr><td>${esc(r.round)}</td><td>${date}</td><td>${esc(devName(r.device))}</td><td>${mins}</td>` +
          `<td>${cell(r.before, r.after, "named_right", true)}</td><td>${cell(r.before, r.after, "misses")}</td><td>${cell(r.before, r.after, "false_alarms")}</td><td>${result}</td></tr>`;
      }).join("")
      : `<tr><td colspan="8" class="muted">No rounds yet.</td></tr>`;
    return `<section class="card"><h3>Past rounds</h3>
      <div class="hint" style="margin: -6px 0 10px">A new model replaces the current one when it names more animals right without missing more, or when it misses fewer animals and makes at least 10% fewer mistakes in all.</div>
      <div class="table-wrap"><table>
        <thead><tr><th>Round</th><th>Date</th><th>Ran on</th><th>Time</th><th>Named right</th><th>Missed animals</th><th>False alarms</th><th>Result</th></tr></thead>
        <tbody>${rows}</tbody></table></div></section>`;
  }

  _settings(s) {
    if (!s) {
      return `<section class="card"><h3>Detector settings</h3><div class="hint">Update the vision service (pull its image again) to change its settings here.</div></section>`;
    }
    const off = s.train_enabled ? "" : "disabled";
    const days = DAYS.map((d) => `<option value="${d}" ${d === s.train_day ? "selected" : ""}>${cap(d)}</option>`).join("");
    const picked = s.train_days || [s.train_day];
    const dayChips = DAYS.map((d) => `<label><input type="checkbox" name="train_days" value="${d}" ${picked.includes(d) ? "checked" : ""} ${off}
        aria-label="${cap(d)}">${cap(d).slice(0, 3)}</label>`).join("");
    const counting = s.train_when === "count";
    const hours = Array.from({ length: 24 }, (_, h) => `<option value="${h}" ${h === s.train_hour ? "selected" : ""}>${hourName(h)}</option>`).join("");
    // A segmented control: one radio per choice, named after its setting. A service from before
    // a setting existed doesn't send it, and the control is left out.
    const seg = (key, label, choices) => s[key] === undefined ? "" : `<div class="row"><span class="label" id="l-${key}">${label}</span>
        <span class="seg" role="radiogroup" aria-labelledby="l-${key}">${choices.map(([v, name]) =>
          `<label><input type="radio" name="${key}" value="${v}" ${String(s[key]) === String(v) ? "checked" : ""}>${name}</label>`).join("")}</span></div>`;
    const hint = (key, text) => s[key] === undefined ? "" : `<div class="hint">${text}</div>`;
    const detecting = s.detect_device === undefined ? "" : `<h4>Detecting</h4>
      ${seg("detect_device", "Detects with", [["auto", "GPU"], ["cpu", "CPU"]])}
      <div class="hint">${DETECT_HINT}</div>`;
    return `<section class="card"><h3>Detector settings</h3>
      <div class="hint" style="margin: -6px 0 10px">${SCHEDULE_HINT}</div>
      ${detecting}
      <h4>Training</h4>
      <div class="row inline"><span class="label" id="l-enabled">Training</span>
        <label class="switch"><input type="checkbox" id="enabled" aria-labelledby="l-enabled" ${s.train_enabled ? "checked" : ""}><span></span></label></div>
      ${s.train_when === undefined
        ? `<div class="row"><span class="label">When</span>
        <span class="group"><select id="day" aria-label="Training day" ${off}>${days}</select><span>from</span>
        <select id="hour" aria-label="Training hour" ${off}>${hours}</select></span></div>`
        : `${seg("train_when", "Starts", [["schedule", "On a schedule"], ["count", "After new answers"]])}
      ${counting ? "" : `<div class="row"><span class="label" id="l-days">Days</span>
        <span class="seg days" role="group" aria-labelledby="l-days">${dayChips}</span></div>
      <div class="row"><span class="label" id="l-hour">From</span>
        <select id="hour" aria-labelledby="l-hour" ${off}>${hours}</select></div>`}`}
      ${s.min_first === undefined ? "" : `<div class="row"><span class="label" id="l-first">Photos for the first round</span>
        <input type="number" id="min_first" min="10" max="1000" value="${esc(s.min_first)}" aria-labelledby="l-first" ${off}></div>`}
      <div class="row"><span class="label" id="l-min">${counting
        ? "Train after this many new answered photos"
        : "New answered photos needed (after the first round)"}</span>
        <input type="number" id="min_new" min="1" max="10000" value="${esc(s.min_new)}" aria-labelledby="l-min" ${off}></div>
      ${seg("train_device", "Trains with", [["auto", "GPU"], ["cpu", "CPU"]])}
      ${seg("train_length", "Training length", [["quick", "Quick"], ["normal", "Normal"], ["thorough", "Thorough"]])}
      ${hint("train_length", LENGTH_HINT)}
      ${seg("train_imgsz", "Picture size", [[640, "Faster"], [960, "Normal"], [1280, "Sharper"]])}
      ${hint("train_imgsz", SIZE_HINT)}
      <div class="hint">${s.detect_device === undefined ? "GPU uses the Intel GPU when it can, and the CPU if it can't. " : ""}Changes apply to the next round.</div>
      ${this._settingsError ? `<div class="error" role="alert">${esc(this._settingsError)}</div>` : ""}
    </section>`;
  }

  // The Detector agreement sensor's week, in one line.
  _agreement(a) {
    if (!a) return "";
    const none = a.misses === 0 ? " · No misses: you can set Mode to Skip empty frames in Local detector above." : "";
    return `<div class="hint" style="margin-top: 14px">This week: ${esc(a.percent)}% agree with the AI · ${esc(a.misses)} missed animal${a.misses === 1 ? "" : "s"}${none}</div>`;
  }

  // What the detector can name: the names it's trusted with (no AI call), and its week.
  _naming(h, n) {
    const names = h.names || [];
    if (!names.length) return "";
    const sure = h.sure || {};
    const trusted = names.filter((x) => x in sure);
    const learning = names.filter((x) => !(x in sure));
    const parts = [
      trusted.length ? `Trusted without the AI: ${esc(trusted.join(", "))}` : "Not trusted with any name yet",
      learning.length ? `still learning: ${esc(learning.join(", "))}` : "",
    ].filter(Boolean);
    const week = n
      ? ` · This week: ${esc(n.saved)} of ${esc(n.named)} sightings named without an AI call` +
        (n.agree != null ? `, ${esc(n.agree)}% agree with the AI on the rest` : "")
      : "";
    return `<div class="hint" style="margin-top: 14px">Names it can give: ${parts.join(" · ")}${week}</div>`;
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
      // The round Undo the last round would undo (the service only offers it with no round running).
      const undo = !t.running && t.undo?.round != null && !d.outdated ? t.undo.round : null;
      this._undoRound = undo;
      html = `
        <section class="card">
          <div class="head"><h3>Detector</h3><span class="chip"><span class="dot ${chip[1]}"></span>${chip[0]}</span></div>
          <div class="grid">
            <div><div class="label">Model</div><div class="value">${h.trained ? `Trained on your photos (${esc(h.model)})${(h.names || []).length ? ", finds and names" : ", finds only"}` : `MegaDetector (${esc(h.model)}), finds only`}</div></div>
            <div><div class="label">Detects with</div><div class="value">${esc(devName(h.device))}</div></div>
            <div><div class="label">Hardware found</div><div class="value">${esc((h.available || []).map(devName).join(", ") || "—")}</div></div>
            <div><div class="label">Right now</div><div class="value">${h.loaded ? "Awake" : "Asleep until the next photo"}</div></div>
          </div>
          ${this._agreement(d.agreement)}
          ${this._naming(h, d.naming)}
          <div class="actions" style="margin-top: 14px">
            <a class="btn quiet" href="${esc(d.url)}/" target="_blank" rel="noreferrer">Open the vision page</a>
          </div>
        </section>
        <section class="card">
          <div class="head"><h3>Training</h3>
            ${undo ? `<button class="btn quiet" id="undo" ${this._busy ? "disabled" : ""}>Undo the last round</button>` : ""}
            ${t.running && "stopping" in t
              ? `<button class="btn danger" id="stop" ${this._busy || t.stopping ? "disabled" : ""}>Stop training</button>` : ""}
            <button class="btn" id="train" ${t.running || this._busy || !t.dataset ? "disabled" : ""}
              ${t.dataset ? "" : `aria-describedby="no-dataset"`}>Train now</button></div>
          <div class="hint" style="margin-top: 0">${esc(this._stageText(t, d.history, d.outdated))}</div>
          ${t.dataset ? "" : `<div class="hint" id="no-dataset">${esc(NO_DATASET)}</div>`}
          ${f === null ? "" : `<div class="bar"><div style="width: ${Math.round(f * 100)}%"></div></div>`}
          ${this._message ? `<div class="error" role="alert">${esc(this._message)}</div>` : ""}
        </section>
        ${d.outdated
          ? `<section class="card"><div class="hint" style="margin: 0">This vision service is out of date. Pull its image again to see training history and settings here.</div></section>`
          : this._settings(t.settings) + this._history(d.history)}`;
    }
    // Nothing changed (most polls): leave it be, so an open list stays open and nothing is
    // read out again. Otherwise the keyboard focus goes back to the same control.
    if (html === this._visionHtml && box.firstChild) return;
    this._visionHtml = html;
    const active = this.shadowRoot.activeElement;
    const focus = active && box.contains(active)
      ? active.id ? `#${CSS.escape(active.id)}` : active.name ? `input[name="${CSS.escape(active.name)}"][value="${CSS.escape(active.value)}"]` : null
      : null;
    box.innerHTML = html;
    if (focus) box.querySelector(focus)?.focus({ preventScroll: true });
    const $ = (id) => box.querySelector(`#${id}`);
    $("retry")?.addEventListener("click", () => { this._vision = null; this._renderVision(); this._refreshVision(); });
    $("train")?.addEventListener("click", () =>
      this._trainAction("train", "Start a training round now? It takes from tens of minutes to a few hours."));
    $("stop")?.addEventListener("click", () =>
      this._trainAction("train_stop", "Stop this training round? What it has done so far is thrown away, and the current model stays."));
    $("undo")?.addEventListener("click", () =>
      this._trainAction("train_undo", `Go back to the model from before round ${this._undoRound}?`));
    $("enabled")?.addEventListener("change", (e) => this._save({ train_enabled: e.target.checked }));
    $("day")?.addEventListener("change", (e) => this._save({ train_day: e.target.value }));
    const dayBoxes = [...box.querySelectorAll("input[name=train_days]")];
    for (const b of dayBoxes) {
      b.addEventListener("change", () => {
        const days = dayBoxes.filter((x) => x.checked).map((x) => x.value);
        if (!days.length) { b.checked = true; return; }  // at least one day
        this._save({ train_days: days });
      });
    }
    $("hour")?.addEventListener("change", (e) => this._save({ train_hour: Number(e.target.value) }));
    for (const key of NUMBER_BOXES) {
      $(key)?.addEventListener("change", (e) => {
        e.target.blur();
        this._save({ [key]: Math.round(Number(e.target.value)) });
      });
    }
    for (const key of ["train_when", "train_device", "detect_device", "train_length", "train_imgsz"]) {
      for (const r of box.querySelectorAll(`input[name=${key}]`)) {
        r.addEventListener("change", (e) => this._save({ [key]: key === "train_imgsz" ? Number(e.target.value) : e.target.value }));
      }
    }
  }
}

if (!customElements.get("neighborhood-watch-panel")) {
  customElements.define("neighborhood-watch-panel", NeighborhoodWatchPanel);
}

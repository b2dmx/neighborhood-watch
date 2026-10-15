/*
 * Neighborhood Watch card — ships with the integration and finds its own devices:
 * animals (photo, where and when, on camera now, missing mode), wildlife, the night
 * summary, each camera's last detection with Right / Wrong buttons, and the switches.
 * Every section can be turned off, devices hidden, and the layout tuned in the editor.
 */
const DOMAIN = "neighborhood_watch";
const SECTIONS = [
  { value: "review", label: "To review" },
  { value: "animals", label: "Animals" },
  { value: "wildlife", label: "Wildlife" },
  { value: "summary", label: "Night summary" },
  { value: "recent", label: "Recent detections" },
  { value: "switches", label: "Alert switches" },
  { value: "scan", label: "Scan a folder" },
];

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function ago(iso) {
  if (!iso || iso === "unknown" || iso === "unavailable") return "";
  const secs = (Date.now() - new Date(iso).getTime()) / 1000;
  if (Number.isNaN(secs)) return "";
  if (secs < 90) return "just now";
  const mins = secs / 60;
  if (mins < 90) return `${Math.round(mins)} min ago`;
  const hours = mins / 60;
  if (hours < 36) return `${Math.round(hours)} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

function clock(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

class NeighborhoodWatchCard extends HTMLElement {
  setConfig(config) {
    this._config = {
      title: "Neighborhood Watch",
      sections: SECTIONS.map((s) => s.value),
      hide: [],
      columns: 3,
      photo_height: 140,
      review_limit: 6,
      ...config,
    };
    this._sig = null;
    this._signed = this._signed || {};
    if (this._hass) this._render();
  }

  set hass(hass) {
    this._hass = hass;
    const sig = this._signature();
    if (sig !== this._sig) {
      this._sig = sig;
      this._render();
    }
  }

  getCardSize() {
    return 10;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6 };
  }

  static getStubConfig() {
    return {};
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "title", selector: { text: {} } },
        { name: "sections", selector: { select: { multiple: true, options: SECTIONS } } },
        { name: "hide", selector: { device: { multiple: true, filter: { integration: DOMAIN } } } },
        {
          type: "grid",
          name: "",
          schema: [
            { name: "columns", selector: { number: { min: 1, max: 6, mode: "box" } } },
            { name: "photo_height", selector: { number: { min: 60, max: 400, step: 10, mode: "box", unit_of_measurement: "px" } } },
            { name: "review_limit", selector: { number: { min: 1, max: 40, mode: "box" } } },
          ],
        },
      ],
      computeLabel: (s) =>
        ({
          title: "Title",
          sections: "Show",
          hide: "Hide these animals, wildlife or cameras",
          columns: "Animal columns",
          photo_height: "Photo height",
          review_limit: "Sightings to review at once",
        })[s.name],
    };
  }

  // --- data ------------------------------------------------------------------------

  _devices() {
    const hass = this._hass;
    const byDevice = {};
    for (const entry of Object.values(hass.entities || {})) {
      if (entry.platform !== DOMAIN || !entry.device_id) continue;
      (byDevice[entry.device_id] ||= {})[entry.translation_key] = entry.entity_id;
    }
    const hidden = new Set(this._config.hide || []);
    const out = { animals: [], wildlife: [], cameras: [], hub: null };
    for (const [id, keys] of Object.entries(byDevice)) {
      const device = hass.devices?.[id];
      if (!device) continue;
      const item = { id, name: device.name_by_user || device.name, keys };
      if (keys.night_summary) out.hub = item;
      else if (hidden.has(id)) continue;
      else if (keys.last_detection) {
        // A camera's entities live on the camera's own device; use our name for it.
        item.name = this._state(keys.last_detection)?.attributes?.camera_name || item.name;
        out.cameras.push(item);
      }
      else if (device.model === "Wildlife") out.wildlife.push(item);
      else out.animals.push(item);
    }
    for (const list of [out.animals, out.wildlife, out.cameras]) list.sort((a, b) => a.name.localeCompare(b.name));
    return out;
  }

  _signature() {
    if (!this._hass) return "";
    const ids = [];
    for (const entry of Object.values(this._hass.entities || {})) {
      if (entry.platform === DOMAIN) ids.push(entry.entity_id);
    }
    return ids.map((id) => `${id}:${this._hass.states[id]?.last_updated}`).join("|") + `|${Math.floor(Date.now() / 60000)}`;
  }

  _state(id) {
    return id ? this._hass.states[id] : undefined;
  }

  // --- render ----------------------------------------------------------------------

  _render() {
    if (!this._hass || !this._config) return;
    const typing = this.shadowRoot?.querySelector("[data-note-text]");
    if (typing && this._note) this._note.text = typing.value;
    const show = new Set(this._config.sections || []);
    const d = this._devices();
    const parts = [];
    if (show.has("review") && d.hub?.keys.to_review) parts.push(this._review(d));
    if (show.has("animals") && d.animals.length) parts.push(this._animals(d.animals));
    if (show.has("wildlife") && (d.wildlife.length || d.hub?.keys.wildlife)) parts.push(this._wildlife(d.wildlife, d.hub));
    if (show.has("summary") && d.hub) parts.push(this._summary(d.hub));
    if (show.has("recent") && d.cameras.length) parts.push(this._recent(d.cameras));
    if (show.has("switches")) parts.push(this._switches(d));
    if (show.has("scan") && d.hub?.keys.last_scan) parts.push(this._scan(d.hub));

    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>${STYLE.replace("__COLS__", this._config.columns || 3).replace("__PH__", this._config.photo_height || 140)}</style>
      <ha-card>
        ${this._config.title ? `<h1 class="title">${esc(this._config.title)}</h1>` : ""}
        ${parts.join("") || `<div class="empty">Nothing to show yet.</div>`}
      </ha-card>`;
    this.shadowRoot.querySelectorAll("[data-more]").forEach((el) =>
      el.addEventListener("click", () => this._more(el.dataset.more))
    );
    this.shadowRoot.querySelectorAll("[data-toggle]").forEach((el) =>
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        this._hass.callService("switch", "toggle", { entity_id: el.dataset.toggle });
      })
    );
    this.shadowRoot.querySelectorAll("[data-answer]").forEach((el) =>
      el.addEventListener("click", () => {
        el.disabled = true;
        this._answer(el.dataset.sid, el.dataset.answer);
      })
    );
    this.shadowRoot.querySelectorAll("select[data-sid]").forEach((el) =>
      el.addEventListener("change", () => {
        if (!el.value) return;
        el.disabled = true;
        this._answer(el.dataset.sid, el.value);
      })
    );
    const noteSave = this.shadowRoot.querySelector("[data-note-save]");
    if (noteSave)
      noteSave.addEventListener("click", () => {
        const text = (this.shadowRoot.querySelector("[data-note-text]")?.value || "").trim();
        const [kind, name] = (this.shadowRoot.querySelector("[data-note-about]")?.value || "").split("|");
        if (!text || !name) return;
        noteSave.disabled = true;
        Promise.resolve(this._hass.callService(DOMAIN, "add_note", { note: text, [kind]: name }))
          .then(() => {
            this._note = null;
            this._render();
          })
          .catch((err) => {
            noteSave.disabled = false;
            alert(`Note not saved: ${err.message || err}`);
          });
      });
    const noteSkip = this.shadowRoot.querySelector("[data-note-skip]");
    if (noteSkip)
      noteSkip.addEventListener("click", () => {
        this._note = null;
        this._render();
      });
    this._loadFrames();
    const scanButton = this.shadowRoot.querySelector("[data-scan]");
    if (scanButton)
      scanButton.addEventListener("click", () => {
        const input = this.shadowRoot.querySelector("input[data-folder]");
        const folder = (input?.value || "").trim();
        if (!folder) return;
        this._scanFolder = folder;
        scanButton.disabled = true;
        Promise.resolve(this._hass.callService(DOMAIN, "scan", { folder }))
          .catch((err) => alert(`Scan could not start: ${err.message || err}`))
          .finally(() => (scanButton.disabled = false));
      });
  }

  _answer(sid, answer) {
    this._hass.callService(DOMAIN, "correct", { answer, sighting_id: sid });
    // A mistake fixed: offer to tell the AI, where it will use it.
    if (["right", "unsure"].includes(answer)) return;
    const d = this._devices();
    const items = this._state(d.hub?.keys.to_review)?.attributes?.items || [];
    const it = items.find((x) => x.id === sid);
    if (!it) return;
    const known = new Set([...d.animals, ...d.wildlife].map((x) => x.name));
    const about = [];
    if (answer !== "wrong" && known.has(answer)) about.push(["animal", answer, `${answer}: how to recognise them`]);
    if (known.has(it.animal) && it.animal !== answer) about.push(["animal", it.animal, `${it.animal}: how to tell them apart`]);
    if (d.cameras.some((c) => c.name === it.where)) about.push(["camera", it.where, `${it.where} camera: what's in this view`]);
    if (!about.length) return;
    this._note = { said: it.animal, was: answer === "wrong" ? "not that" : answer, where: it.where, about, text: "" };
    this._render();
  }

  _noteBox() {
    const n = this._note;
    if (!n) return "";
    const opts = n.about.map(([k, name, label]) => `<option value="${k}|${esc(name)}">${esc(label)}</option>`).join("");
    return `<div class="note">
        <div><b>Add a note so it doesn't happen again?</b> <span class="muted">The AI said ${esc(n.said)}; it was ${esc(n.was)}.</span></div>
        <select data-note-about>${opts}</select>
        <textarea data-note-text rows="2" placeholder="e.g. looks very bright under strong infrared">${esc(n.text || "")}</textarea>
        <div class="acts"><button class="chip" data-note-save>Save note</button><button class="chip" data-note-skip>No thanks</button></div>
      </div>`;
  }

  _loadFrames() {
    this.shadowRoot.querySelectorAll("img[data-frame]").forEach((img) => {
      const sid = img.dataset.frame;
      if (this._signed[sid]) {
        img.src = this._signed[sid];
        return;
      }
      this._hass
        .callWS({ type: "auth/sign_path", path: `/api/${DOMAIN}/frame/${sid}`, expires: 86400 })
        .then((r) => {
          this._signed[sid] = r.path;
          img.src = r.path;
        })
        .catch(() => img.classList.add("broken"));
    });
  }

  _review(d) {
    const s = this._state(d.hub.keys.to_review);
    const items = s?.attributes?.items || [];
    if (!items.length) return `<div class="section"><h2>To review</h2>${this._noteBox()}<div class="muted">All caught up.</div></div>`;
    const limit = this._config.review_limit || 6;
    const names = [...d.animals, ...d.wildlife].map((x) => x.name);
    const options = ["Nothing", ...names].map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
    const rows = items.slice(0, limit).map((it) => {
      const sid = esc(it.id);
      return `<div class="rev">
          <img class="thumb" data-frame="${sid}" alt="">
          <div class="revbody">
            <div><b>${esc(it.animal)}</b> <span class="muted">(${esc(it.confidence)})</span></div>
            <div class="muted">${esc(it.where)} · ${clock(it.time)} · ${ago(it.time)}</div>
            <div class="acts">
              <button class="mini" data-answer="right" data-sid="${sid}" title="Right">✓</button>
              <button class="mini" data-answer="wrong" data-sid="${sid}" title="Wrong">✗</button>
              <button class="mini" data-answer="unsure" data-sid="${sid}" title="Not sure">?</button>
              <select data-sid="${sid}"><option value="">It was…</option>${options}</select>
            </div>
          </div>
        </div>`;
    });
    const more = items.length > limit ? `<div class="muted">${items.length - limit} more after these.</div>` : "";
    return `<div class="section"><h2>To review · ${esc(s.state)}</h2>${this._noteBox()}${rows.join("")}${more}</div>`;
  }

  _scan(hub) {
    const s = this._state(hub.keys.last_scan);
    const a = s?.attributes || {};
    let status = `<span class="muted">No scans yet.</span>`;
    if (s && !["unknown", "unavailable"].includes(s.state)) {
      const tally = Object.entries(a.tally || {})
        .sort((x, y) => y[1] - x[1])
        .map(([k, n]) => `${n} ${esc(k)}`)
        .join(", ");
      status = `<b>${esc(a.folder || "")}</b> · ${esc(s.state)}${tally ? ` · ${tally}` : ""}${
        a.report ? `<div class="muted">Report: ${esc(a.report)}/index.html</div>` : ""
      }${a.error ? `<div class="muted">${esc(a.error)}</div>` : ""}`;
    }
    return `<div class="section"><h2>Scan a folder</h2>
      <div class="status">${status}</div>
      <div class="acts"><input data-folder placeholder="Folder in the scan share, e.g. trailcam" value="${esc(this._scanFolder || "")}">
      <button class="chip" data-scan>Scan</button></div></div>`;
  }

  _ruleChips(k) {
    // An animal's Alerts switch, and its Critical switch when it has critical cameras.
    const out = [];
    const alerts = this._state(k.alerts);
    const crit = this._state(k.critical_alerts);
    if (alerts)
      out.push(
        `<button class="chip ${alerts.state === "on" ? "" : "dim"}" data-toggle="${esc(k.alerts)}">${alerts.state === "on" ? "Alerts" : "Alerts off"}</button>`
      );
    if (crit)
      out.push(`<button class="chip ${crit.state === "on" ? "alarm" : ""}" data-toggle="${esc(k.critical_alerts)}">Critical</button>`);
    return out;
  }

  _more(entityId) {
    this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
  }

  _animals(animals) {
    const tiles = animals.map((a) => {
      const k = a.keys;
      const photo = this._state(k.last_photo);
      const onCam = this._state(k.on_camera);
      const seen = this._state(k.last_seen);
      const where = this._state(k.location);
      const tonight = this._state(k.sightings_tonight);
      const missing = this._state(k.missing_mode);
      const absent = this._state(k.recently_absent);
      const live = onCam?.state === "on";
      let status;
      if (live) status = `<span class="live">● On camera — ${esc(onCam.attributes.camera || "")}</span>`;
      else if (seen && !["unknown", "unavailable"].includes(seen.state))
        status = `Last seen <b>${esc(where?.state)}</b> · ${ago(seen.state)}`;
      else status = `<span class="muted">Not seen yet</span>`;
      const img =
        photo && !["unknown", "unavailable"].includes(photo.state) && photo.attributes.entity_picture
          ? `<img src="${esc(photo.attributes.entity_picture)}" alt="${esc(a.name)}" data-more="${esc(k.last_photo)}">`
          : `<div class="nophoto" data-more="${esc(k.last_seen || "")}"><ha-icon icon="mdi:cat"></ha-icon></div>`;
      const chips = [];
      if (tonight && Number(tonight.state) > 0) chips.push(`<span class="chip">${esc(tonight.state)} tonight</span>`);
      if (absent?.state === "on") chips.push(`<span class="chip dim">away lately</span>`);
      chips.push(...this._ruleChips(k));
      if (missing)
        chips.push(
          `<button class="chip ${missing.state === "on" ? "alarm" : ""}" data-toggle="${esc(k.missing_mode)}">Missing mode ${missing.state === "on" ? "on" : "off"}</button>`
        );
      return `<div class="tile ${live ? "is-live" : ""}">${img}<div class="body"><div class="name">${esc(a.name)}</div><div class="status">${status}</div><div class="chips">${chips.join("")}</div></div></div>`;
    });
    return `<div class="section"><div class="grid">${tiles.join("")}</div></div>`;
  }

  _wildlife(list, hub) {
    const rows = list.map((w) => {
      const k = w.keys;
      const seen = this._state(k.last_seen);
      const where = this._state(k.location);
      const onCam = this._state(k.on_camera);
      const tonight = this._state(k.sightings_tonight);
      const live = onCam?.state === "on";
      const when = seen && !["unknown", "unavailable"].includes(seen.state) ? `${esc(where?.state)} · ${ago(seen.state)}` : "Not seen";
      return `<div class="row ${live ? "is-live" : ""}" data-more="${esc(k.last_photo || k.last_seen)}">
          <span class="rname">${esc(w.name)}</span>
          <span class="rinfo">${live ? `<span class="live">● ${esc(onCam.attributes.camera || "")}</span>` : when}</span>
          <span class="rcount">${tonight && Number(tonight.state) > 0 ? `${esc(tonight.state)} tonight` : ""}</span>
          ${this._ruleChips(k).join("")}
        </div>`;
    });
    // Every other kind lives on the hub's Wildlife sensor: no device per animal.
    const seen = hub?.keys.wildlife ? this._state(hub.keys.wildlife)?.attributes?.kinds || {} : {};
    const own = new Set(list.map((w) => w.name));
    for (const [name, k] of Object.entries(seen)) {
      if (own.has(name)) continue;
      const tag = k.pest ? `<span class="chip alarm">pest</span>` : "";
      const maybe = k.possible ? ` <span class="muted">(possible)</span>` : "";
      rows.push(`<div class="row" data-more="${esc(hub.keys.wildlife)}">
          <span class="rname">${esc(name)}${maybe}</span>
          <span class="rinfo">${esc(k.where || "")} · ${ago(k.last_seen)}</span>
          <span class="rcount">${k.tonight ? `${esc(k.tonight)} tonight` : ""}</span>${tag}
        </div>`);
    }
    if (!rows.length) return "";
    return `<div class="section"><h2>Wildlife</h2>${rows.join("")}</div>`;
  }

  _summary(hub) {
    const s = this._state(hub.keys.night_summary);
    const lines = s?.attributes?.lines || [];
    const body = lines.length ? lines.map((l) => `<div>${esc(l)}</div>`).join("") : `<div class="muted">No sightings last night.</div>`;
    return `<div class="section"><h2>Last night</h2>${body}</div>`;
  }

  _recent(cameras) {
    const rows = cameras
      .map((c) => ({ c, s: this._state(c.keys.last_detection) }))
      .filter(({ s }) => s && !["unknown", "unavailable"].includes(s.state))
      .sort((a, b) => String(b.s.attributes.time || "").localeCompare(String(a.s.attributes.time || "")))
      .map(({ c, s }) => {
        const a = s.attributes;
        const sid = a.id;
        const nothing = (a.animal || "") === "Nothing" || !a.animal;
        const buttons =
          sid && !nothing
            ? `<button class="mini" data-answer="right" data-sid="${esc(sid)}" title="Right">✓</button><button class="mini" data-answer="wrong" data-sid="${esc(sid)}" title="Wrong">✗</button>`
            : "";
        return `<div class="row"><span class="rname" data-more="${esc(c.keys.last_frame || "")}">${esc(c.name)}</span><span class="rinfo">${esc(s.state)}</span><span class="rcount">${clock(a.time)}</span>${buttons}</div>`;
      });
    return `<div class="section"><h2>Recent detections</h2>${rows.join("") || `<div class="muted">Nothing yet.</div>`}</div>`;
  }

  _switches(d) {
    const chips = d.cameras
      .filter((c) => c.keys.critical_alerts)
      .map((c) => {
        const s = this._state(c.keys.critical_alerts);
        return `<button class="chip ${s?.state === "on" ? "alarm" : ""}" data-toggle="${esc(c.keys.critical_alerts)}">${esc(c.name)}</button>`;
      });
    const hub = d.hub?.keys || {};
    for (const [key, label] of [["pest_critical", "Pests"], ["other_critical", "Other wildlife"], ["unknown_critical", "Unknown animals"], ["motion_critical", "Unsure motion"]]) {
      const s = hub[key] && this._state(hub[key]);
      if (s) chips.push(`<button class="chip ${s.state === "on" ? "alarm" : ""}" data-toggle="${esc(hub[key])}">${label}</button>`);
    }
    if (!chips.length) return "";
    return `<div class="section"><h2>Critical alerts</h2><div class="chips">${chips.join("")}</div></div>`;
  }
}

const STYLE = `
  ha-card { padding: 16px; }
  .title { font-size: 1.4em; font-weight: 500; margin: 0 0 12px; }
  h2 { font-size: 1em; font-weight: 600; margin: 0 0 8px; color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: .04em; }
  .section + .section { margin-top: 18px; }
  .grid { display: grid; grid-template-columns: repeat(__COLS__, minmax(0, 1fr)); gap: 12px; }
  .tile { border-radius: 16px; overflow: hidden; background: var(--secondary-background-color); border: 2px solid transparent; }
  .tile.is-live { border-color: var(--success-color, #43a047); }
  .tile img, .nophoto { display: block; width: 100%; height: __PH__px; object-fit: cover; cursor: pointer; }
  .nophoto { display: flex; align-items: center; justify-content: center; color: var(--disabled-text-color); --mdc-icon-size: 40px; }
  .body { padding: 8px 10px 10px; }
  .name { font-weight: 600; font-size: 1.05em; }
  .status { font-size: .9em; margin-top: 2px; color: var(--primary-text-color); }
  .muted, .dim { color: var(--secondary-text-color); }
  .live { color: var(--success-color, #43a047); font-weight: 600; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
  .chip { font: inherit; font-size: .8em; padding: 3px 10px; border-radius: 999px; border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); cursor: default; }
  button.chip { cursor: pointer; }
  .chip.alarm { background: var(--error-color, #db4437); border-color: var(--error-color, #db4437); color: var(--text-primary-color, #fff); }
  .chip.dim { opacity: .75; }
  .row { display: flex; align-items: center; gap: 10px; padding: 6px 0; border-bottom: 1px solid var(--divider-color); cursor: pointer; }
  .row:last-child { border-bottom: none; }
  .row.is-live .rname { color: var(--success-color, #43a047); }
  .rname { font-weight: 600; min-width: 90px; }
  .rinfo { flex: 1; font-size: .92em; }
  .rcount { font-size: .85em; color: var(--secondary-text-color); white-space: nowrap; }
  .mini { font: inherit; width: 30px; height: 30px; border-radius: 50%; border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); cursor: pointer; }
  .mini:disabled { opacity: .4; }
  .empty { color: var(--secondary-text-color); }
  .note { display: flex; flex-direction: column; gap: 6px; padding: 10px; margin-bottom: 8px; border-radius: 12px; background: var(--secondary-background-color); }
  .note select, .note textarea { font: inherit; padding: 6px; border-radius: 8px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); }
  .rev { display: flex; gap: 12px; padding: 8px 0; border-bottom: 1px solid var(--divider-color); }
  .rev:last-of-type { border-bottom: none; }
  .thumb { width: 132px; height: 88px; object-fit: cover; border-radius: 10px; background: var(--secondary-background-color); flex: none; }
  .thumb.broken { opacity: .3; }
  .revbody { flex: 1; display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .acts { display: flex; gap: 6px; align-items: center; margin-top: 4px; flex-wrap: wrap; }
  input[data-folder] { flex: 1; min-width: 180px; font: inherit; padding: 6px 12px; border-radius: 999px; border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); }
  select { font: inherit; padding: 4px 8px; border-radius: 999px; border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); }
  @media (max-width: 500px) { .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
`;

customElements.define("neighborhood-watch-card", NeighborhoodWatchCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "neighborhood-watch-card",
  name: "Neighborhood Watch",
  description: "Your animals, wildlife, last night and recent detections, with Right / Wrong buttons.",
  preview: true,
  documentationURL: "https://github.com/b2dmx/neighborhood-watch",
});

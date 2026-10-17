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
      photo_hours: 12,
      review_limit: 6,
      order: "recent",
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
          name: "order",
          selector: {
            select: {
              mode: "dropdown",
              options: [
                { value: "recent", label: "Most recently seen first" },
                { value: "name", label: "Alphabetical" },
              ],
            },
          },
        },
        {
          type: "grid",
          name: "",
          schema: [
            { name: "columns", selector: { number: { min: 1, max: 6, mode: "box" } } },
            { name: "photo_height", selector: { number: { min: 60, max: 400, step: 10, mode: "box", unit_of_measurement: "px" } } },
            { name: "review_limit", selector: { number: { min: 1, max: 40, mode: "box" } } },
            { name: "photo_hours", selector: { number: { min: 0, max: 720, mode: "box", unit_of_measurement: "h" } } },
          ],
        },
      ],
      computeLabel: (s) =>
        ({
          title: "Title",
          sections: "Show",
          hide: "Hide these animals, wildlife or cameras",
          order: "Animal order",
          columns: "Animal columns",
          photo_height: "Photo height",
          review_limit: "Sightings to review at once",
          photo_hours: "Photo shows for (0 = always)",
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
      else if (keys.last_seen) out.animals.push(item);
      // Anything else (a phone carrying a person's critical switch) isn't shown here.
    }
    for (const list of [out.animals, out.wildlife, out.cameras]) list.sort((a, b) => a.name.localeCompare(b.name));
    if (this._config.order !== "name") {
      // On camera now first, then the most recently seen; never-seen animals last.
      const rank = (a) => {
        if (this._state(a.keys.on_camera)?.state === "on") return Infinity;
        const t = new Date(this._state(a.keys.last_seen)?.state).getTime();
        return Number.isNaN(t) ? -Infinity : t;
      };
      out.animals.sort((a, b) => rank(b) - rank(a) || a.name.localeCompare(b.name));
    }
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
    // The shorter sections sit side by side on wide screens instead of each taking a row.
    const side = [];
    if (show.has("wildlife") && (d.wildlife.length || d.hub?.keys.wildlife)) side.push(this._wildlife(d.wildlife, d.hub));
    if (show.has("summary") && d.hub) side.push(this._summary(d.hub));
    if (show.has("recent") && d.cameras.length) side.push(this._recent(d.cameras));
    if (show.has("switches")) side.push(this._switches(d));
    if (show.has("scan") && d.hub?.keys.last_scan) side.push(this._scan(d.hub));
    const filled = side.filter(Boolean);
    if (filled.length) parts.push(`<div class="side">${filled.join("")}</div>`);

    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>${STYLE.replace("__COLS__", this._config.columns || 3).replace("__PH__", this._config.photo_height || 140)}</style>
      <ha-card>
        <div class="whole">
          <h2 class="title">${esc(this._config.title || "Neighborhood Watch")}</h2>
          <div class="inner">${parts.join("") || `<div class="empty">Nothing to show yet.</div>`}</div>
        </div>
      </ha-card>${this._lightbox()}`;
    // Every section folds: tap its title. "To review" starts folded; choices are remembered.
    this.shadowRoot.querySelectorAll(".section > h2, .whole > h2").forEach((h) => {
      const key = h.textContent.split(" · ")[0].trim();
      if (this._folded().has(key)) h.parentElement.classList.add("folded");
      h.addEventListener("click", () => {
        const folded = this._folded();
        folded.has(key) ? folded.delete(key) : folded.add(key);
        try {
          localStorage.setItem(FOLD_KEY, JSON.stringify([...folded]));
        } catch (e) {}
        h.parentElement.classList.toggle("folded");
      });
    });
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
    this._wireLightbox();
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

  _folded() {
    if (!this._foldSet) {
      let saved = null;
      try {
        saved = JSON.parse(localStorage.getItem(FOLD_KEY) || "null");
      } catch (e) {}
      this._foldSet = new Set(saved || ["To review"]);
    }
    return this._foldSet;
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
      // "crop": cut to the animal the local detector found; the full frame otherwise.
      const path = `/api/${DOMAIN}/frame/${img.dataset.frame}${img.dataset.crop ? "/crop" : ""}`;
      if (this._signed[path]) {
        img.src = this._signed[path];
        return;
      }
      this._hass
        .callWS({ type: "auth/sign_path", path, expires: 86400 })
        .then((r) => {
          this._signed[path] = r.path;
          img.src = r.path;
        })
        .catch(() => img.classList.add("broken"));
    });
  }

  _lightbox() {
    // The full frame, over everything. Tap the picture to zoom in where you tapped.
    if (!this._zoom) return "";
    return `<div class="lightbox" data-close>
        <button class="lbclose" data-close title="Close">✕</button>
        <img class="lbimg" data-frame="${esc(this._zoom)}" alt="">
      </div>`;
  }

  _wireLightbox() {
    this.shadowRoot.querySelectorAll("[data-zoom]").forEach((el) =>
      el.addEventListener("click", () => {
        this._zoom = el.dataset.zoom;
        this._render();
      })
    );
    const box = this.shadowRoot.querySelector(".lightbox");
    if (!box) return;
    box.addEventListener("click", (ev) => {
      const img = box.querySelector(".lbimg");
      if (ev.target === img && !img.classList.contains("zoomed")) {
        // First tap zooms in where you tapped; a tap on the zoomed picture closes it.
        const r = img.getBoundingClientRect();
        img.style.transformOrigin = `${((ev.clientX - r.left) / r.width) * 100}% ${((ev.clientY - r.top) / r.height) * 100}%`;
        img.classList.toggle("zoomed");
        return;
      }
      this._zoom = null;
      this._render();
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
          <img class="thumb" data-frame="${sid}" data-crop="1" data-zoom="${sid}" alt="" title="Tap to enlarge">
          <div class="revname"><b>${esc(it.animal)}</b> <span class="muted">${esc(it.confidence)}</span></div>
          <div class="muted small">${esc(it.where)} · ${clock(it.time)} · ${ago(it.time)}</div>
          <div class="acts">
            <button class="mini" data-answer="right" data-sid="${sid}" title="Right">✓</button>
            <button class="mini" data-answer="wrong" data-sid="${sid}" title="Wrong">✗</button>
            <button class="mini" data-answer="unsure" data-sid="${sid}" title="Not sure">?</button>
            <select data-sid="${sid}"><option value="">It was…</option>${options}</select>
          </div>
        </div>`;
    });
    const more = items.length > limit ? `<div class="muted">${items.length - limit} more after these.</div>` : "";
    return `<div class="section"><h2>To review · ${esc(s.state)}</h2>${this._noteBox()}<div class="revgrid">${rows.join("")}</div>${more}</div>`;
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

  _toggles(k) {
    // Icon toggles for an animal's Alerts, Critical alerts and Missing mode switches.
    const t = (id, on, icon, offIcon, label, alarm) => {
      const s = id && this._state(id);
      if (!s) return "";
      const isOn = s.state === "on";
      return `<button class="tog ${isOn ? (alarm ? "alarm" : "on") : ""}" data-toggle="${esc(id)}" title="${label}: ${isOn ? "on" : "off"}">
          <ha-icon icon="${isOn ? icon : offIcon}"></ha-icon><span>${label}</span></button>`;
    };
    return (
      t(k.alerts, true, "mdi:bell", "mdi:bell-off-outline", "Alerts") +
      t(k.critical_alerts, true, "mdi:bell-ring", "mdi:bell-ring-outline", "Critical", true) +
      t(k.missing_mode, true, "mdi:map-marker-alert", "mdi:map-marker-question-outline", "Missing", true)
    );
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
      if (live) status = `<span class="live">● On camera · ${esc(onCam.attributes.camera || "")}</span>`;
      else if (seen && !["unknown", "unavailable"].includes(seen.state))
        status = `${esc(where?.state)} · ${ago(seen.state)}`;
      else status = `Not seen yet`;
      // A sighting photo shows for photo_hours, then the tile goes back to the drawn face.
      const hours = Number(this._config.photo_hours ?? 12);
      const fresh = !hours || Date.now() - new Date(photo?.state).getTime() < hours * 3600e3;
      const hasPhoto =
        photo && !["unknown", "unavailable"].includes(photo.state) && photo.attributes.entity_picture && fresh;
      const img = hasPhoto
        ? `<img src="${esc(photo.attributes.entity_picture)}" alt="${esc(a.name)}">`
        : `<div class="nophoto">${catFace(seen?.attributes || {})}</div>`;
      const badges = [];
      if (tonight && Number(tonight.state) > 0) badges.push(`<span class="badge">${esc(tonight.state)} tonight</span>`);
      if (absent?.state === "on") badges.push(`<span class="badge dim">away lately</span>`);
      if (missing?.state === "on") badges.push(`<span class="badge alarm">MISSING</span>`);
      return `<div class="tile ${live ? "is-live" : ""} ${missing?.state === "on" ? "is-missing" : ""}">
          <div class="pic" data-more="${esc(hasPhoto ? k.last_photo : k.last_seen || "")}">${img}
            <div class="over"><div class="name">${esc(a.name)}</div><div class="status">${status}</div></div>
            <div class="badges">${badges.join("")}</div>
          </div>
          <div class="toggles">${this._toggles(k)}</div>
        </div>`;
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
      const tag = k.pest ? ` <span class="badge alarm">pest</span>` : "";
      const maybe = k.possible ? ` <span class="muted">(possible)</span>` : "";
      rows.push(`<div class="row" data-more="${esc(hub.keys.wildlife)}">
          <span class="rname">${esc(name)}${tag}${maybe}</span>
          <span class="rinfo">${esc(k.where || "")} · ${ago(k.last_seen)}</span>
          <span class="rcount">${k.tonight ? `${esc(k.tonight)} tonight` : ""}</span>
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

const FOLD_KEY = "neighborhood-watch-card-folded";

// A cat face drawn from how the animal is described: its coat pattern and main tone, and
// words in its description (colours, calico/tortie, tabby, spotted, tuxedo, bicolour/van,
// colourpoint/Siamese, long-haired, ear tufts, folded ears, hairless, eye colour). Shown
// until there's a recent photo.
function catFace({ pattern = "na", base = "na", looks = "" }) {
  const t = looks.toLowerCase();
  const has = (...w) => w.some((x) => t.includes(x));
  const tones = { white: "#f2efe9", grey: "#8b9099", black: "#1c1c1f", na: "#a59f96" };
  const WHITE = "#f4f1ea";
  let fur = tones[base] || tones.na;
  // Colour words refine the tone (a black coat stays black).
  if (base === "grey" && has("dark grey", "dark gray")) fur = "#4b4e56";
  if (base !== "black" && has("blue", "slate", "russian blue", "silver") && !has("blue eye", "blue-eye")) fur = "#7d8794";
  if (base !== "white" && has("brown", "chocolate")) fur = "#4a3f36";
  if (has("orange", "ginger", "red tabby", "marmalade")) fur = "#d98b45";
  if (has("cream", "buff", "fawn", "apricot")) fur = "#e3c79c";
  if (has("smoke")) fur = "#3a3c42";
  if (base === "white" && !has("calico", "tortie", "patch", "blotch", "bicolor", "bicolour", "van")) fur = tones.white;
  const hairless = has("hairless", "sphynx", "sphinx");
  if (hairless) fur = "#e9c3b5";
  const dark = ["#1c1c1f", "#3a3c42", "#4a3f36", "#4b4e56"].includes(fur) ? "#0e0e10" : "#00000055";

  const fluffy = !hairless && has("long-hair", "long hair", "longhair", "fluffy", "bushy", "maine coon", "norwegian", "persian", "ragdoll");
  const tufts = has("ear tuft", "lynx", "maine coon");
  const folded = has("folded ear", "scottish fold", "fold ears");
  const bib = has("bib", "white chest", "white upper chest", "tuxedo");
  const calico = has("calico") || (pattern === "blotchy" && base === "white");
  const tortie = !calico && (has("tortie", "tortoiseshell") || (pattern === "blotchy" && base !== "white"));
  const bicolor = has("bicolor", "bicolour", "van ", "white with ", "mostly white", "harlequin");
  const point = has("colorpoint", "colourpoint", "pointed", "siamese", "himalayan", "birman", "ragdoll", "balinese");
  const tabby = pattern === "striped" || has("tabby", "stripe", "mackerel");
  const spotted = has("spotted", "spots", "bengal", "leopard");

  const parts = [];
  if (fluffy)
    parts.push(`<path d="M18 62 L10 70 L22 70 L16 80 L30 76 L32 88 L50 82 L68 88 L70 76 L84 80 L78 70 L90 70 L82 62 Z" fill="${fur}"/>`);
  // Ears: pointed (or small and folded), pink inside unless dark-pointed.
  const earFill = point ? "#3d332e" : bicolor ? "#4a4a50" : fur;
  if (folded) parts.push(`<path d="M22 40 Q24 26 38 30 L34 38 Z" fill="${earFill}"/><path d="M78 40 Q76 26 62 30 L66 38 Z" fill="${earFill}"/>`);
  else {
    parts.push(`<path d="M22 46 L26 14 L44 32 Z" fill="${earFill}"/><path d="M78 46 L74 14 L56 32 Z" fill="${earFill}"/>`);
    if (!point) parts.push(`<path d="M27 40 L29 22 L39 33 Z" fill="#e8a0a8" opacity=".6"/><path d="M73 40 L71 22 L61 33 Z" fill="#e8a0a8" opacity=".6"/>`);
    if (tufts) parts.push(`<path d="M26 14 L24 4 M26 14 L29 5 M74 14 L76 4 M74 14 L71 5" stroke="${earFill}" stroke-width="1.6" stroke-linecap="round"/>`);
  }
  parts.push(`<ellipse cx="50" cy="55" rx="31" ry="27" fill="${fur}"/>`);
  if (calico)
    parts.push(
      `<path d="M30 34 Q46 30 44 50 Q34 56 24 50 Q22 40 30 34 Z" fill="#d9894a"/>`,
      `<path d="M56 30 Q72 28 78 44 L74 46 Q64 40 56 42 Z" fill="#4a4a50"/><path d="M60 30 L74 14 L78 44 Z" fill="#4a4a50"/>`
    );
  if (tortie)
    parts.push(
      `<path d="M28 36 Q40 30 42 44 Q32 50 24 46 Z" fill="#b8682e"/><path d="M58 60 Q70 54 76 64 Q66 72 58 68 Z" fill="#b8682e"/>`,
      `<path d="M52 30 Q62 34 58 42 Q50 40 52 30 Z" fill="#c98845"/>`
    );
  if (bicolor && !calico) parts.push(`<path d="M24 40 Q50 22 76 40 Q66 34 50 34 Q34 34 24 40 Z" fill="#4a4a50"/>`);
  if (tabby)
    parts.push(
      `<path d="M42 32 L44 42 M50 30 L50 42 M58 32 L56 42" stroke="${dark}" stroke-width="3" stroke-linecap="round"/>`,
      `<path d="M21 54 L30 56 M21 61 L30 61 M79 54 L70 56 M79 61 L70 61" stroke="${dark}" stroke-width="2.5" stroke-linecap="round"/>`
    );
  if (spotted)
    parts.push(
      [[34, 38], [44, 34], [56, 34], [66, 38], [27, 52], [73, 52], [30, 63], [70, 63]]
        .map(([x, y]) => `<circle cx="${x}" cy="${y}" r="2.6" fill="${dark}"/>`)
        .join("")
    );
  if (point) parts.push(`<ellipse cx="50" cy="63" rx="15" ry="12" fill="#3d332e" opacity=".85"/>`);
  else if (bib || base === "white" || calico || bicolor) parts.push(`<path d="M38 66 Q50 82 62 66 Q56 74 50 74 Q44 74 38 66 Z" fill="${WHITE}"/>`);
  if (bib) parts.push(`<path d="M34 78 Q50 96 66 78 L60 92 L40 92 Z" fill="${WHITE}"/>`);

  // Eyes: blue for colourpoints unless said otherwise; odd-eyed gets one of each.
  const named = has("green") ? "#a6b83a" : has("blue eye", "blue-eye") || (point && !has("green", "copper")) ? "#6aa7d8"
    : has("copper", "orange eye", "amber") ? "#d0802e" : "#d4b23a";
  const [left, right] = has("odd-eye", "odd eye", "heterochrom") ? ["#6aa7d8", "#d4b23a"] : [named, named];
  parts.push(
    `<ellipse cx="39" cy="52" rx="5" ry="6" fill="${left}"/><ellipse cx="61" cy="52" rx="5" ry="6" fill="${right}"/>`,
    `<ellipse cx="39" cy="53" rx="1.8" ry="5" fill="#111"/><ellipse cx="61" cy="53" rx="1.8" ry="5" fill="#111"/>`,
    `<path d="M46 62 L54 62 L50 67 Z" fill="#e48a95"/>`,
    `<path d="M50 67 Q46 72 42 70 M50 67 Q54 72 58 70" stroke="#3a3a3a" stroke-width="1.5" fill="none" stroke-linecap="round"/>`
  );
  if (!hairless) {
    const light = [tones.white, WHITE, "#e3c79c", "#e9c3b5"].includes(fur);
    parts.push(
      `<path d="M30 64 L12 61 M30 68 L13 70 M70 64 L88 61 M70 68 L87 70" stroke="${light ? "#9a958c" : "#e9e6df"}" stroke-width="1.2" stroke-linecap="round"/>`
    );
  }
  return `<svg viewBox="0 0 100 100" class="face" aria-hidden="true">${parts.join("")}</svg>`;
}

const STYLE = `
  .section > h2, .whole > h2 { cursor: pointer; user-select: none; }
  .section > h2::after { content: "▾"; margin-left: 6px; display: inline-block; transition: transform .15s; }
  .section.folded > h2::after { transform: rotate(-90deg); }
  .section.folded > :not(h2) { display: none; }
  ha-card { padding: 16px; }
  .title { font-size: 1.4em; font-weight: 500; margin: 0 0 12px; }
  h2 { font-size: 1em; font-weight: 600; margin: 0 0 8px; color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: .04em; }
  .section + .section, .section + .side, .side + .section { margin-top: 18px; }
  .side { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 18px 28px; align-items: start; }
  .side > .section { margin-top: 0; min-width: 0; }
  .grid { display: grid; grid-template-columns: repeat(__COLS__, minmax(0, 1fr)); gap: 12px; }
  .tile { border-radius: 18px; overflow: hidden; background: var(--secondary-background-color); border: 2px solid transparent; display: flex; flex-direction: column; }
  .tile.is-live { border-color: var(--success-color, #43a047); }
  .tile.is-missing { border-color: var(--error-color, #db4437); }
  .pic { position: relative; height: __PH__px; cursor: pointer; }
  .pic img, .nophoto { display: block; width: 100%; height: 100%; object-fit: cover; }
  .face { width: 62%; height: 78%; }
  .nophoto { display: flex; align-items: center; justify-content: center; color: var(--disabled-text-color); --mdc-icon-size: 44px;
    background: linear-gradient(135deg, var(--secondary-background-color), var(--card-background-color)); }
  .over { position: absolute; left: 0; right: 0; bottom: 0; padding: 22px 12px 8px; color: #fff;
    background: linear-gradient(to top, rgba(0,0,0,.72), rgba(0,0,0,0)); }
  .nophoto + .over { color: var(--primary-text-color); background: none; }
  .over .name { font-weight: 600; font-size: 1.1em; }
  .over .status { font-size: .85em; opacity: .9; }
  .over .live { color: #7CFC9A; }
  .badges { position: absolute; top: 8px; left: 8px; right: 8px; display: flex; flex-wrap: wrap; gap: 4px; }
  .badge { font-size: .72em; padding: 2px 8px; border-radius: 999px; background: rgba(0,0,0,.55); color: #fff; }
  .badge.dim { opacity: .8; }
  .badge.alarm { background: var(--error-color, #db4437); font-weight: 600; }
  .toggles { display: flex; gap: 4px; padding: 6px; }
  .tog { flex: 1; display: flex; flex-direction: column; align-items: center; gap: 1px; padding: 6px 2px; border: none; border-radius: 12px;
    background: transparent; color: var(--secondary-text-color); font: inherit; font-size: .7em; cursor: pointer; --mdc-icon-size: 20px; }
  .tog:hover { background: var(--card-background-color); }
  .tog.on { color: var(--primary-color); }
  .tog.alarm { color: var(--error-color, #db4437); }
  .whole > h2.title { font-size: 1.3em; font-weight: 500; text-transform: none; letter-spacing: 0; color: var(--primary-text-color); margin: 0; }
  .whole > .inner { margin-top: 12px; }
  .whole.folded > .inner { display: none; }
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
  .revgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 10px; }
  .rev { display: flex; flex-direction: column; gap: 3px; padding: 6px; border-radius: 14px; background: var(--secondary-background-color); min-width: 0; }
  .revname { font-size: .95em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .small { font-size: .8em; }
  .rev .acts { gap: 4px; }
  .rev .mini { width: 28px; height: 28px; }
  .rev select { flex: 1; min-width: 0; font-size: .85em; }
  .thumb { width: 100%; aspect-ratio: 4 / 3; object-fit: cover; border-radius: 10px; background: var(--card-background-color); cursor: zoom-in; display: block; }
  .lightbox { position: fixed; inset: 0; z-index: 10; background: rgba(0,0,0,.85); display: flex; align-items: center; justify-content: center; overflow: hidden; }
  .lbimg { max-width: 96vw; max-height: 92vh; border-radius: 8px; cursor: zoom-in; transition: transform .2s; }
  .lbimg.zoomed { transform: scale(2.6); cursor: zoom-out; }
  .lbclose { position: absolute; top: 12px; right: 14px; font-size: 22px; width: 40px; height: 40px; border-radius: 50%; border: none; background: rgba(255,255,255,.15); color: #fff; cursor: pointer; }
  .thumb.broken { opacity: .3; }
  .revbody { flex: 1; display: flex; flex-direction: column; gap: 2px; min-width: 0; }
  .acts { display: flex; gap: 6px; align-items: center; margin-top: 4px; flex-wrap: wrap; }
  input[data-folder] { flex: 1; min-width: 180px; font: inherit; padding: 6px 12px; border-radius: 999px; border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); }
  select { font: inherit; padding: 4px 8px; border-radius: 999px; border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); color-scheme: light dark; }
  select option { background: var(--card-background-color, #1c1c1f); color: var(--primary-text-color); }
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

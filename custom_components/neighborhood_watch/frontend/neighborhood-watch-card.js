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
  { value: "summary", label: "Last night" },
  { value: "recent", label: "Recent detections" },
  { value: "switches", label: "Critical alerts" },
  { value: "scan", label: "Scan a folder" },
];
// The entities whose state the card shows; anything else changing needs no redraw.
const SHOWN = new Set([
  "night_summary", "to_review", "wildlife", "last_scan", "pest_critical", "other_critical", "unknown_critical", "motion_critical",
  "last_seen", "last_photo", "location", "on_camera", "sightings_tonight", "recently_absent", "alerts", "critical_alerts", "missing_mode",
  "last_detection", "last_frame",
]);

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
      // Scanning needs the local detector, so that section is opt-in.
      sections: SECTIONS.map((s) => s.value).filter((v) => v !== "scan"),
      hide: [],
      columns: 3,
      photo_height: 140,
      photo_hours: 12,
      review_limit: 6,
      order: "recent",
      ...config,
    };
    this._sectionsSet = !!config && "sections" in config;
    this._sig = null;
    this._signed = this._signed || {};
    this._broken = this._broken || new Set();
    this._answered = this._answered || new Set();
    if (this._hass) this._render();
  }

  connectedCallback() {
    // A quiet system sends no updates, so refresh each minute to keep "x min ago" true.
    if (!this._tick) this._tick = setInterval(() => this._onTick(), 60000);
    if (!this._onKey) {
      this._onKey = (ev) => {
        if (ev.key !== "Escape" || !this._zoom) return;
        this._zoom = null;
        this._render(true);
      };
      window.addEventListener("keydown", this._onKey);
    }
    if (!this._onRelease) {
      // The press may end outside the card; the click (if any) fires before this timeout.
      this._onRelease = () => {
        if (!this._pressing) return;
        this._pressing = false;
        setTimeout(() => this._resume());
      };
      window.addEventListener("pointerup", this._onRelease);
      window.addEventListener("pointercancel", this._onRelease);
    }
  }

  disconnectedCallback() {
    this._stopSprites?.();
    this._stopSprites = null;
    clearInterval(this._tick);
    this._tick = null;
    window.removeEventListener("keydown", this._onKey);
    this._onKey = null;
    window.removeEventListener("pointerup", this._onRelease);
    window.removeEventListener("pointercancel", this._onRelease);
    this._onRelease = null;
  }

  _onTick() {
    // Only the "x min ago" text moves on its own; redraw everything just when data changed.
    // A full redraw at least hourly too, so photos older than photo_hours go back to the face.
    const sig = this._signature();
    const hour = Math.floor(Date.now() / 3600e3);
    if (this._pending || sig !== this._sig || hour !== this._hour) {
      this._sig = sig;
      this._hour = hour;
      return this._render();
    }
    this.shadowRoot?.querySelectorAll("[data-ago]").forEach((el) => (el.textContent = ago(el.dataset.ago)));
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
          sections: "Sections",
          hide: "Hide these animals or cameras",
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
    const out = { animals: [], cameras: [], hub: null };
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
    for (const list of [out.animals, out.cameras]) list.sort((a, b) => a.name.localeCompare(b.name));
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

  _names(d) {
    // Everything a sighting can be: the hub lists your animals and every wildlife kind;
    // older versions fall back to the animals and the wildlife seen so far.
    const choices = d.hub?.keys.to_review ? this._state(d.hub.keys.to_review)?.attributes?.choices : null;
    if (Array.isArray(choices) && choices.length) return choices;
    const kinds = d.hub?.keys.wildlife ? this._state(d.hub.keys.wildlife)?.attributes?.kinds || {} : {};
    return [...new Set([...d.animals.map((x) => x.name), ...Object.keys(kinds)])];
  }

  _signature() {
    if (!this._hass) return "";
    // The registry only changes when entities come or go: rebuild our list just then.
    const entities = this._hass.entities;
    if (!this._ids || entities !== this._entitiesRef) {
      this._entitiesRef = entities;
      this._ids = Object.values(entities || {})
        .filter((e) => e.platform === DOMAIN && SHOWN.has(e.translation_key))
        .map((e) => e.entity_id);
    }
    // Image entities get a new access token every few minutes; only a new picture (its state) counts.
    return this._ids
      .map((id) => {
        const s = this._hass.states[id];
        return `${id}:${id.startsWith("image.") ? s?.state : s?.last_updated}`;
      })
      .join("|");
  }

  _busy() {
    // Typing: a redraw now would undo it. (Not a list: closing one without choosing leaves
    // it focused.)
    const el = this.shadowRoot?.activeElement;
    return !!(el && !el.disabled && el.matches("input, textarea"));
  }

  _resume() {
    // Run the redraw that was held back, once nothing is focused or being pressed.
    if (this._pending && !this._pressing) this._render();
  }

  _state(id) {
    return id ? this._hass.states[id] : undefined;
  }

  // --- render ----------------------------------------------------------------------

  _render(force) {
    if (!this._hass || !this._config) return;
    // Held back while the user is busy; runs when they leave the field or close the frame,
    // or after 30 s regardless (what they typed is carried over).
    if (!force && this._busy() && !(this._pending && Date.now() - this._pendingSince > 30000)) {
      if (!this._pending) this._pendingSince = Date.now();
      this._pending = true;
      return;
    }
    this._pending = false;
    const root = this.shadowRoot;
    const typing = root?.querySelector("[data-note-text]");
    if (typing && this._note) this._note.text = typing.value;
    const about = root?.querySelector("[data-note-about]");
    if (about && this._note) this._note.pick = about.value;
    const folderInput = root?.querySelector("input[data-folder]");
    if (folderInput) this._scanFolder = folderInput.value;
    const show = new Set(this._config.sections || []);
    const d = this._devices();
    const parts = [];
    if (show.has("review") && d.hub?.keys.to_review) parts.push(this._review(d));
    if (show.has("animals") && d.animals.length) parts.push(this._animals(d.animals));
    else if (show.has("animals") && d.hub)
      parts.push(`<div class="section"><h2>Animals</h2><div class="muted">Add animals with Add animal on the Neighborhood Watch integration page.</div></div>`);
    // The shorter sections sit side by side on wide screens instead of each taking a row.
    const side = [];
    if (show.has("wildlife") && d.hub?.keys.wildlife) side.push(this._wildlife(d.hub));
    if (show.has("summary") && d.hub) side.push(this._summary(d.hub));
    if (show.has("recent") && d.cameras.length) side.push(this._recent(d.cameras));
    if (show.has("switches")) side.push(this._switches(d));
    // Scanning needs a detector: shown when asked for, or (on the default sections) once a scan has run.
    const scan = this._state(d.hub?.keys.last_scan);
    const scanned = scan && !["unknown", "unavailable"].includes(scan.state);
    if (scan && (show.has("scan") || (scanned && !this._sectionsSet))) side.push(this._scan(d.hub));
    const filled = side.filter(Boolean);
    if (filled.length) parts.push(`<div class="side">${filled.join("")}</div>`);

    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      // Leaving a field mid-press (tapping Scan or Save) waits for the click to land first.
      this.shadowRoot.addEventListener("pointerdown", () => (this._pressing = true));
      this.shadowRoot.addEventListener("focusout", () => setTimeout(() => this._resume()));
      // Enter or Space on a tappable picture, row or title works like a tap.
      this.shadowRoot.addEventListener("keydown", (ev) => {
        const el = ev.target;
        if ((ev.key !== "Enter" && ev.key !== " ") || el.tagName === "BUTTON" || el.getAttribute?.("role") !== "button") return;
        ev.preventDefault();
        el.click();
      });
    }
    const cols = this._config.columns || 3;
    const ph = this._config.photo_height || 140;
    if (this._styleKey !== `${cols}|${ph}`) {
      this._styleKey = `${cols}|${ph}`;
      this._style = STYLE.replace("__COLS__", cols).replace("__SMALLCOLS__", Math.min(cols, 2)).replace("__PH__", ph);
    }
    // Hold the card's height while it redraws and its pictures load, so the page doesn't
    // jump (scroll position snaps back when the card briefly gets shorter).
    const held = this.offsetHeight;
    if (held) {
      this.style.minHeight = `${held}px`;
      clearTimeout(this._release);
      this._release = setTimeout(() => (this.style.minHeight = ""), 1500);
    }
    this.shadowRoot.innerHTML = `
      <style>${this._style}</style>
      <ha-card>
        <div class="whole">
          <h2 class="title">${esc(this._config.title || "Neighborhood Watch")}</h2>
          <div class="inner">${parts.join("") || `<div class="empty">Nothing to show yet. On the Neighborhood Watch integration page, add cameras under Configure → Cameras and animals with Add animal.</div>`}</div>
        </div>
      </ha-card>${this._lightbox()}`;
    this._stopSprites?.();
    this._stopSprites = NWSprites.mountSprites(this.shadowRoot);
    this._checkInfrared();
    // Every section folds: tap its title. "To review" starts folded; choices are remembered.
    const tappable = (el) => {
      el.tabIndex = 0;
      el.setAttribute("role", "button");
    };
    this.shadowRoot.querySelectorAll(".section > h2, .whole > h2").forEach((h) => {
      const key = h.textContent.split(" · ")[0].trim();
      if (this._folded().has(key)) h.parentElement.classList.add("folded");
      tappable(h);
      h.setAttribute("aria-expanded", String(!this._folded().has(key)));
      h.addEventListener("click", () => {
        const folded = this._folded();
        folded.has(key) ? folded.delete(key) : folded.add(key);
        try {
          localStorage.setItem(FOLD_KEY, JSON.stringify([...folded]));
        } catch (e) {}
        h.setAttribute("aria-expanded", String(!h.parentElement.classList.toggle("folded")));
      });
    });
    this.shadowRoot.querySelectorAll("[data-more]").forEach((el) => {
      tappable(el);
      el.addEventListener("click", () => this._more(el.dataset.more));
    });
    this.shadowRoot.querySelectorAll("[data-toggle]").forEach((el) =>
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        this._hass.callService("switch", "toggle", { entity_id: el.dataset.toggle });
      })
    );
    const undoBtn = this.shadowRoot.querySelector("[data-undo]");
    if (undoBtn)
      undoBtn.addEventListener("click", () => {
        undoBtn.disabled = true;
        const id = this._state(this._devices().hub?.keys.to_review)?.attributes?.last_answer?.id;
        if (id) this._answered.delete(String(id));
        this._note = null;
        Promise.resolve(this._hass.callService(DOMAIN, "undo_last_answer", {}))
          .catch((err) => alert(`Not undone: ${err.message || err}`))
          .finally(() => this._render(true));
      });
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
            this._render(true);
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
        this._render(true);
      });
    this._loadFrames();
    // Opened from an alert's "Other…" button (page#review): open To review and show it, once.
    if (location.hash === "#review" && !this._reviewShown) {
      const h = [...this.shadowRoot.querySelectorAll(".section > h2")].find((x) => x.textContent.startsWith("To review"));
      if (h) {
        this._reviewShown = true;
        h.parentElement.classList.remove("folded");
        h.setAttribute("aria-expanded", "true");
        setTimeout(() => h.scrollIntoView({ behavior: "smooth", block: "start" }), 300);
      }
    } else if (location.hash !== "#review") this._reviewShown = false;
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
          .catch((err) => alert(err.message || err))
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
    // Remembered so the buttons stay off until the sighting leaves the list.
    this._answered.add(sid);
    Promise.resolve(this._hass.callService(DOMAIN, "correct", { answer, sighting_id: sid })).catch((err) => {
      this._answered.delete(sid);
      this._render(true);
      alert(`Answer not saved: ${err.message || err}`);
    });
    // A mistake fixed: offer to tell the AI, where it will use it.
    if (["right", "unsure"].includes(answer)) return;
    const d = this._devices();
    const items = this._state(d.hub?.keys.to_review)?.attributes?.items || [];
    const it = items.find((x) => x.id === sid);
    if (!it) return;
    // Notes are about an animal or a camera; wildlife kinds can't hold one.
    const known = new Set(d.animals.map((x) => x.name));
    const about = [];
    if (answer !== "wrong" && known.has(answer)) about.push(["animal", answer, `${answer}: how to recognise them`]);
    if (known.has(it.animal) && it.animal !== answer) about.push(["animal", it.animal, `${it.animal}: how to tell them apart`]);
    if (d.cameras.some((c) => c.name === it.where)) about.push(["camera", it.where, `${it.where} camera: what's in this view`]);
    if (!about.length) return;
    this._note = { said: it.animal, was: answer === "wrong" ? "not that" : answer, about, text: "" };
    this._render(true);
  }

  _noteBox() {
    const n = this._note;
    if (!n) return "";
    const opts = n.about
      .map(([k, name, label]) => {
        const sel = n.pick === `${k}|${name}` ? " selected" : "";
        return `<option value="${k}|${esc(name)}"${sel}>${esc(label)}</option>`;
      })
      .join("");
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
      // A frame that's gone stays marked broken (or shows its fallback picture); it isn't
      // asked for again on every redraw.
      const giveUp = () => {
        if (img.dataset.fallback && !img.dataset.fellBack) {
          img.dataset.fellBack = "1";
          img.src = img.dataset.fallback;
        } else img.classList.add("broken");
      };
      if (this._broken.has(path)) return giveUp();
      // A link that stops working (signature expired or revoked) is signed afresh, once.
      img.addEventListener("error", () => {
        if (img.dataset.fellBack) return img.classList.add("broken");
        if (img.dataset.retried) {
          this._broken.add(path);
          return giveUp();
        }
        img.dataset.retried = "1";
        delete this._signed[path];
        this._sign(img, path);
      });
      this._sign(img, path);
    });
  }

  _sign(img, path) {
    // Signed links last a day; sign again in the last hour so a thumbnail never goes stale.
    const cached = this._signed[path];
    if (cached && cached.exp - Date.now() > 3600e3) {
      img.src = cached.url;
      return;
    }
    this._hass
      .callWS({ type: "auth/sign_path", path, expires: 86400 })
      .then((r) => {
        const now = Date.now();
        for (const [p, c] of Object.entries(this._signed)) if (c.exp <= now) delete this._signed[p];
        this._signed[path] = { url: r.path, exp: now + 86400e3 };
        img.src = r.path;
      })
      .catch(() => img.classList.add("broken"));
  }

  _lightbox() {
    // The full frame, over everything. Tap the picture to zoom in where you tapped.
    if (!this._zoom) return "";
    return `<div class="lightbox" role="dialog" aria-modal="true" aria-label="${esc(this._zoomAlt || "Sighting")}">
        <button class="lbclose" title="Close" aria-label="Close">✕</button>
        <img class="lbimg" data-frame="${esc(this._zoom)}" alt="${esc(this._zoomAlt || "Sighting")}">
      </div>`;
  }

  _wireLightbox() {
    this.shadowRoot.querySelectorAll("[data-zoom]").forEach((el) => {
      el.tabIndex = 0;
      el.setAttribute("role", "button");
      el.addEventListener("click", () => {
        this._zoom = el.dataset.zoom;
        this._zoomAlt = el.alt;
        this._render(true);
      });
    });
    const box = this.shadowRoot.querySelector(".lightbox");
    if (!box) return;
    box.querySelector(".lbclose").focus();
    // The picture is already enlarged: a tap anywhere, on it or around it, closes it.
    box.addEventListener("click", () => {
      this._zoom = null;
      this._render(true);
    });
  }

  _review(d) {
    const s = this._state(d.hub.keys.to_review);
    const items = s?.attributes?.items || [];
    // The last answer (from here or a phone) can be taken back for an hour.
    const la = s?.attributes?.last_answer;
    const said = { right: "Right", unsure: "Not sure", other: "Other", wrong: "Wrong" };
    const undo =
      la && Date.now() - new Date(la.at).getTime() < 3600e3
        ? `<div class="undo muted">Last answer: ${esc(la.where || "")} · ${esc(said[String(la.answer).toLowerCase()] || la.answer)}` +
          ` <button class="chip" data-undo aria-label="Undo the last answer">Undo</button></div>`
        : "";
    // Nothing to review: no section at all (unless the note box or an undo is still offered).
    if (!items.length)
      return this._note || undo ? `<div class="section"><h2>To review</h2>${undo}${this._noteBox()}</div>` : "";
    const limit = this._config.review_limit || 6;
    const options = ["Nothing", ...this._names(d)].map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
    const rows = items.slice(0, limit).map((it) => {
      const sid = esc(it.id);
      const off = this._answered.has(String(it.id)) ? " disabled" : "";
      return `<div class="rev">
          <img class="thumb" data-frame="${sid}" data-crop="1" data-zoom="${sid}" alt="Sighting: ${esc(it.animal)} at ${esc(it.where)}" title="Tap to enlarge">
          <div class="revname"><b>${esc(it.animal)}</b> <span class="muted">${esc(it.confidence)}</span></div>
          <div class="muted small">${esc(it.where)} · ${clock(it.time)} · <span data-ago="${esc(it.time)}">${ago(it.time)}</span></div>
          <div class="acts">
            <button class="mini" data-answer="right" data-sid="${sid}" title="Right" aria-label="Right"${off}>✓</button>
            <button class="mini" data-answer="unsure" data-sid="${sid}" title="Not sure" aria-label="Not sure"${off}>?</button>
            <select data-sid="${sid}" aria-label="It was"${off}><option value="">It was…</option>${options}</select>
          </div>
        </div>`;
    });
    // The sensor lists at most 40; its state is the full count.
    const total = Number.isFinite(Number(s.state)) ? Number(s.state) : items.length;
    const more = total > limit ? `<div class="muted">${total - limit} more after these.</div>` : "";
    return `<div class="section"><h2>To review · ${esc(s.state)}</h2>${undo}${this._noteBox()}<div class="revgrid">${rows.join("")}</div>${more}</div>`;
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
      <div class="acts"><input data-folder placeholder="Folder in the detector's scan folder, e.g. trailcam" value="${esc(this._scanFolder || "")}">
      <button class="chip" data-scan>Scan</button></div></div>`;
  }

  _toggles(k) {
    // Icon toggles for an animal's Alerts, Critical alerts and Missing mode switches.
    // The name says what it is; aria-pressed says whether it's on.
    const t = (id, icon, offIcon, label, name, alarm) => {
      const s = id && this._state(id);
      if (!s) return "";
      const isOn = s.state === "on";
      return `<button class="tog ${isOn ? (alarm ? "alarm" : "on") : ""}" data-toggle="${esc(id)}" title="${name}" aria-label="${name}" aria-pressed="${isOn}">
          <ha-icon icon="${isOn ? icon : offIcon}"></ha-icon><span>${label}</span></button>`;
    };
    return (
      t(k.alerts, "mdi:bell", "mdi:bell-off-outline", "Alerts", "Alerts") +
      t(k.critical_alerts, "mdi:bell-ring", "mdi:bell-ring-outline", "Critical", "Critical alerts", true) +
      t(k.missing_mode, "mdi:map-marker-alert", "mdi:map-marker-question-outline", "Missing", "Missing mode", true)
    );
  }

  _checkInfrared() {
    // A collar shows grey when the animal's latest photo is night vision (no colour to go by).
    this._ir = this._ir || {};
    for (const a of this._devices().animals || []) {
      const seen = this._state(a.keys.last_seen);
      const url = this._state(a.keys.last_photo)?.attributes?.entity_picture;
      const look = seen?.attributes?.appearance;
      if (!look || !NWSprites.hasCollar(look) || !url || url in this._ir) continue;
      this._ir[url] = false;
      NWSprites.isInfrared(url).then((ir) => {
        this._ir[url] = ir;
        if (ir) this._render(true);
      });
    }
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
        status = `${esc(where?.state)} · <span data-ago="${esc(seen.state)}">${ago(seen.state)}</span>`;
      else status = `Not seen yet`;
      // A sighting photo shows for photo_hours, then the tile goes back to the drawn face.
      const hours = Number(this._config.photo_hours ?? 12);
      const fresh = !hours || Date.now() - new Date(photo?.state).getTime() < hours * 3600e3;
      const hasPhoto =
        photo && !["unknown", "unavailable"].includes(photo.state) && photo.attributes.entity_picture && fresh;
      // The photo's crop around the animal: smaller and quicker than the whole frame, which
      // stays the fallback (an older photo, or no training data saved).
      const cropId = seen?.attributes?.photo_id;
      const look = seen?.attributes?.appearance;
      const ir = look && NWSprites.hasCollar(look) && this._ir?.[photo?.attributes?.entity_picture];
      const sprite = look && { cat: { ...look, ...(ir ? { ir: true } : {}) }, ...(live ? { mode: "alert" } : {}) };
      const img = !hasPhoto
        ? `<div class="nophoto">${
            sprite
              ? `<canvas class="sprite" width="48" height="48" data-sprite="${esc(JSON.stringify(sprite))}" role="img" aria-label="${esc(a.name)}"></canvas>`
              : catFace(seen?.attributes || {})
          }</div>`
        : cropId
          ? `<img data-frame="${esc(cropId)}" data-crop="1" data-fallback="${esc(photo.attributes.entity_picture)}" alt="${esc(a.name)}">`
          : `<img src="${esc(photo.attributes.entity_picture)}" alt="${esc(a.name)}">`;
      const badges = [];
      const neverSeen = !seen || ["unknown", "unavailable"].includes(seen.state);
      if (tonight && Number(tonight.state) > 0) badges.push(`<span class="badge">${esc(tonight.state)} tonight</span>`);
      if (absent?.state === "on" && !neverSeen) badges.push(`<span class="badge dim">away lately</span>`);
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

  _wildlife(hub) {
    // Wildlife kinds live on the hub's Wildlife sensor: no device per animal.
    const rows = [];
    const seen = this._state(hub.keys.wildlife)?.attributes?.kinds || {};
    for (const [name, k] of Object.entries(seen)) {
      const tag = k.pest ? ` <span class="badge alarm">pest</span>` : "";
      const maybe = k.possible ? ` <span class="muted">(possible)</span>` : "";
      const kind = NWSprites.spriteKind(name);
      const fresh = k.last_seen && Date.now() - new Date(k.last_seen).getTime() < 15 * 60e3;
      const pic = kind
        ? `<canvas class="wsprite" width="48" height="48" data-sprite="${esc(JSON.stringify({ wild: kind, ...(fresh ? { mode: "walk" } : {}) }))}" aria-hidden="true"></canvas>`
        : "";
      rows.push(`<div class="row" data-more="${esc(hub.keys.wildlife)}">
          <span class="rname">${pic}${esc(name)}${tag}${maybe}</span>
          <span class="rinfo">${esc(k.where || "")} · <span data-ago="${esc(k.last_seen || "")}">${ago(k.last_seen)}</span></span>
          <span class="rcount">${k.tonight ? `${esc(k.tonight)} tonight` : ""}</span>
        </div>`);
    }
    if (!rows.length) return "";
    return `<div class="section"><h2>Wildlife</h2>${rows.join("")}</div>`;
  }

  _summary(hub) {
    const s = this._state(hub.keys.night_summary);
    const lines = s?.attributes?.lines || [];
    // A fresh install has no summary yet: say when the first one comes.
    const first = s?.attributes?.built_at ? "" : " The first summary arrives in the morning.";
    const body = lines.length ? lines.map((l) => `<div>${esc(l)}</div>`).join("") : `<div class="muted">No sightings last night.${first}</div>`;
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
        const off = this._answered.has(String(sid)) ? " disabled" : "";
        const buttons =
          sid && !nothing
            ? `<button class="mini" data-answer="right" data-sid="${esc(sid)}" title="Right" aria-label="Right"${off}>✓</button><button class="mini" data-answer="unsure" data-sid="${esc(sid)}" title="Not sure" aria-label="Not sure"${off}>?</button>`
            : "";
        return `<div class="row"><span class="rname" data-more="${esc(c.keys.last_frame || "")}">${esc(c.name)}</span><span class="rinfo">${esc(s.state)}</span><span class="rcount">${clock(a.time)}</span>${buttons}</div>`;
      });
    return `<div class="section"><h2>Recent detections</h2>${rows.join("") || `<div class="muted">Nothing yet — checks run when a camera sees motion.</div>`}</div>`;
  }

  _switches(d) {
    const chips = d.cameras
      .filter((c) => c.keys.critical_alerts)
      .map((c) => {
        const s = this._state(c.keys.critical_alerts);
        return `<button class="chip ${s?.state === "on" ? "alarm" : ""}" data-toggle="${esc(c.keys.critical_alerts)}" aria-pressed="${s?.state === "on"}">${esc(c.name)}</button>`;
      });
    const hub = d.hub?.keys || {};
    for (const [key, label] of [["pest_critical", "Pests"], ["other_critical", "Other wildlife"], ["unknown_critical", "Unknown animals"], ["motion_critical", "Unsure motion"]]) {
      const s = hub[key] && this._state(hub[key]);
      if (s) chips.push(`<button class="chip ${s.state === "on" ? "alarm" : ""}" data-toggle="${esc(hub[key])}" aria-pressed="${s.state === "on"}">${label}</button>`);
    }
    if (!chips.length) return "";
    return `<div class="section"><h2>Critical alerts</h2><div class="muted small">On = alerts ring even in silent mode.</div><div class="chips">${chips.join("")}</div></div>`;
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
  if (base !== "white" && has("brown", "chocolate")) fur = "#3f2c22";
  if (has("orange", "ginger", "red tabby", "marmalade")) fur = "#d98b45";
  if (has("cream", "buff", "fawn", "apricot")) fur = "#e3c79c";
  if (has("smoke")) fur = "#3a3c42";
  if (base === "white" && !has("calico", "tortie", "patch", "blotch", "bicolor", "bicolour", "van")) fur = tones.white;
  const hairless = has("hairless", "sphynx", "sphinx");
  if (hairless) fur = "#e9c3b5";
  const dark = ["#1c1c1f", "#3a3c42", "#3f2c22", "#4b4e56"].includes(fur) ? "#0e0e10" : "#00000055";

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
    parts.push(`<path d="M24 55 L13 61 L21 64.5 L15 71.5 L27 70.5 L50 73 L73 70.5 L85 71.5 L79 64.5 L87 61 L76 55 Z" fill="${fur}" stroke="${fur}" stroke-width="2" stroke-linejoin="round"/>`);
  // Ears: pointed (or small and folded), pink inside unless dark-pointed.
  const earFill = point ? "#3d332e" : bicolor ? "#4a4a50" : fur;
  const rightEar = calico ? "#4a4a50" : earFill;  // a calico's dark patch takes in her right ear
  if (folded) parts.push(`<path d="M26 42 Q23 28 36 29 Q40 30 38 35 Z" fill="${earFill}"/><path d="M74 42 Q77 28 64 29 Q60 30 62 35 Z" fill="${rightEar}"/>`);
  else {
    parts.push(
      `<path d="M26.5 44 C22.5 34 22.5 23 26 16.5 Q28 13.8 30.5 15.8 C35.5 20.5 40.5 26.5 45 33 Z" fill="${earFill}"/>`,
      `<path d="M73.5 44 C77.5 34 77.5 23 74 16.5 Q72 13.8 69.5 15.8 C64.5 20.5 59.5 26.5 55 33 Z" fill="${rightEar}"/>`
    );
    if (!point) parts.push(`<path d="M28.5 39 C26.5 32 27 26 29 21.5 C32.5 25 36 29 39.5 34 Z" fill="#e8a0a8" opacity=".55"/><path d="M71.5 39 C73.5 32 73 26 71 21.5 C67.5 25 64 29 60.5 34 Z" fill="#e8a0a8" opacity=".55"/>`);
    if (tufts) parts.push(`<path d="M26 14 L24 4 M26 14 L29 5 M74 14 L76 4 M74 14 L71 5" stroke="${earFill}" stroke-width="1.6" stroke-linecap="round"/>`);
  }
  const head = "M50 29.5 C64.5 29.5 76 38.5 77.5 51.5 C79 64.5 68.5 79 50 80.5 C31.5 79 21 64.5 22.5 51.5 C24 38.5 35.5 29.5 50 29.5 Z";
  const clip = `h${Math.random().toString(36).slice(2, 9)}`;
  parts.push(`<path d="${head}" fill="${fur}"/><g clip-path="url(#${clip})">`);
  if (calico)
    parts.push(
      `<path d="M30 34 Q46 30 44 50 Q34 56 24 50 Q22 40 30 34 Z" fill="#d9894a"/>`,
      `<path d="M54 26 Q74 26 84 44 L80 48 Q66 40 56 42 Z" fill="#4a4a50"/>`
    );
  if (tortie)
    parts.push(
      `<path d="M28 36 Q40 30 42 44 Q32 50 24 46 Z" fill="#b8682e"/><path d="M58 60 Q70 54 76 64 Q66 72 58 68 Z" fill="#b8682e"/>`,
      `<path d="M52 30 Q62 34 58 42 Q50 40 52 30 Z" fill="#c98845"/>`
    );
  if (bicolor && !calico) parts.push(`<path d="M24 40 Q50 22 76 40 Q66 34 50 34 Q34 34 24 40 Z" fill="#4a4a50"/>`);
  if (tabby)
    // A tabby's face: the "M" on the forehead with lines above it, a line from the outer
    // corner of each eye, and stripes curving across the cheeks.
    parts.push(
      `<g stroke="${dark}" fill="none" stroke-linecap="round" stroke-linejoin="round" opacity=".9">` +
        `<path d="M41 43.5 Q42 37.5 43.5 36.5 Q45.5 38.5 47 41.5 Q48.5 35.5 50 35 Q51.5 35.5 53 41.5 Q54.5 38.5 56.5 36.5 Q58 37.5 59 43.5" stroke-width="1.9"/>` +
        `<path d="M45.5 31.5 L46.5 34.5 M50 30 V33 M54.5 31.5 L53.5 34.5" stroke-width="1.8"/>` +
        `<path d="M33.5 50.5 Q29.5 50.5 26.5 53 M66.5 50.5 Q70.5 50.5 73.5 53" stroke-width="1.6"/>` +
        `<path d="M24 58.5 Q28 59 31.5 61.5 M76 58.5 Q72 59 68.5 61.5 M25.5 64.5 Q28.5 64.5 31 66.5 M74.5 64.5 Q71.5 64.5 69 66.5" stroke-width="1.6"/>` +
        `</g>`
    );
  if (spotted)
    parts.push(
      [[34, 38], [44, 34], [56, 34], [66, 38], [27, 52], [73, 52], [30, 63], [70, 63]]
        .map(([x, y]) => `<circle cx="${x}" cy="${y}" r="2.6" fill="${dark}"/>`)
        .join("")
    );
  if (point) parts.push(`<ellipse cx="50" cy="63" rx="15" ry="12" fill="#3d332e" opacity=".85"/>`);
  // A tuxedo (white bib) has a white muzzle: a narrow blaze from the forehead between the eyes, widening over the muzzle (the chin stays dark).
  else if (bib && base !== "white" && !calico)
    parts.push(`<path d="M50 42 Q47 49 45.5 56 Q42 64 34 72 L31.5 75.5 Q40 72.5 50 74 Q60 72.5 68.5 75.5 L66 72 Q58 64 54.5 56 Q53 49 50 42 Z" fill="${WHITE}"/>`);
  else if (bib || base === "white" || calico || bicolor) parts.push(`<path d="M38 66 Q50 82 62 66 Q56 74 50 74 Q44 74 38 66 Z" fill="${WHITE}"/>`);

  parts.push("</g>");

  // Eyes: blue for colourpoints unless said otherwise; odd-eyed gets one of each.
  const named = has("green") ? "#a6b83a" : has("blue eye", "blue-eye") || (point && !has("green", "copper")) ? "#6aa7d8"
    : has("copper", "orange eye", "amber") ? "#d0802e" : "#d4b23a";
  const [left, right] = has("odd-eye", "odd eye", "heterochrom") ? ["#6aa7d8", "#d4b23a"] : [named, named];
  const light = [tones.white, WHITE, "#e3c79c", "#e9c3b5"].includes(fur);
  const eye = (x, iris) =>
    `<ellipse cx="${x}" cy="52" rx="5.4" ry="5.8" fill="${iris}" stroke="#00000040" stroke-width=".6"/>` +
    `<ellipse cx="${x}" cy="52.3" rx="1.5" ry="4.4" fill="#111"/>` +
    `<circle cx="${x + 1.6}" cy="49.6" r="1.15" fill="#fff" opacity=".9"/>`;
  parts.push(
    eye(39, left),
    eye(61, right),
    `<path d="M46.4 61.6 Q50 60.4 53.6 61.6 Q52.4 64.6 50 65.6 Q47.6 64.6 46.4 61.6 Z" fill="#e48a95"/>`,
    `<path d="M50 65.6 V67.4 M50 67.4 Q47.6 70.4 44.6 69 M50 67.4 Q52.4 70.4 55.4 69" stroke="${light ? "#5a4f4a" : dark === "#0e0e10" ? "#8a8a90" : "#2a2a2a"}" stroke-width="1.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/>`
  );
  if (!hairless)
    parts.push(
      `<path d="M33 64 Q22 61.5 12 61.5 M33 67 Q22 67.5 13 70 M67 64 Q78 61.5 88 61.5 M67 67 Q78 67.5 87 70" stroke="${light ? "#9a958c" : "#e9e6df"}" stroke-width=".9" fill="none" stroke-linecap="round" opacity=".9"/>`
    );
  return `<svg viewBox="0 0 100 100" class="face" aria-hidden="true"><defs><clipPath id="${clip}"><path d="${head}"/></clipPath></defs>${parts.join("")}</svg>`;
}

const STYLE = `
  :host { display: block; container-type: inline-size; }
  .section > h2, .whole > h2 { cursor: pointer; user-select: none; }
  .section > h2::after { content: "▾"; margin-left: 6px; display: inline-block; transition: transform .15s; }
  .section.folded > h2::after { transform: rotate(-90deg); }
  .section.folded > :not(h2) { display: none; }
  ha-card { padding: 16px; }
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
  .nophoto { display: flex; align-items: center; justify-content: center; color: var(--disabled-text-color);
    background: linear-gradient(135deg, var(--secondary-background-color), var(--card-background-color)); }
  .over { position: absolute; left: 0; right: 0; bottom: 0; padding: 22px 12px 8px; color: #fff;
    background: linear-gradient(to top, rgba(0,0,0,.72), rgba(0,0,0,0)); }
  .nophoto + .over { color: var(--primary-text-color); background: none; }
  .nophoto canvas.sprite { width: 82%; height: 82%; object-fit: contain; image-rendering: pixelated; margin-bottom: 14%; }
  canvas.wsprite { width: 40px; height: 40px; image-rendering: pixelated; vertical-align: middle; margin-right: 6px; }
  .over .name { font-weight: 600; font-size: 1.1em; }
  .over .status { font-size: .85em; opacity: .9; color: inherit; }
  .over .live { color: var(--success-color, #7CFC9A); }
  .badges { position: absolute; top: 8px; left: 8px; right: 8px; display: flex; flex-wrap: wrap; gap: 4px; }
  .badge { font-size: .72em; padding: 2px 8px; border-radius: 999px; background: rgba(0,0,0,.55); color: #fff; }
  .badge.dim { opacity: .8; color: #fff; }
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
  .status { font-size: .9em; margin-top: 2px; color: var(--primary-text-color); }
  .muted, .dim { color: var(--secondary-text-color); }
  .live { color: var(--success-color, #43a047); font-weight: 600; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; }
  .chip { font: inherit; font-size: .8em; padding: 3px 10px; border-radius: 999px; border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); cursor: default; min-height: 32px; }
  button.chip { cursor: pointer; }
  .chip.alarm { background: var(--error-color, #db4437); border-color: var(--error-color, #db4437); color: var(--text-primary-color, #fff); }
  .row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; padding: 6px 0; border-bottom: 1px solid var(--divider-color); cursor: pointer; }
  .row:last-child { border-bottom: none; }
  .rname { font-weight: 600; min-width: 90px; }
  .rinfo { flex: 1; font-size: .92em; }
  .rcount { font-size: .85em; color: var(--secondary-text-color); white-space: nowrap; }
  .mini { font: inherit; width: 36px; height: 36px; flex: none; border-radius: 50%; border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); cursor: pointer; }
  .mini:disabled { opacity: .4; }
  .empty { color: var(--secondary-text-color); }
  .note { display: flex; flex-direction: column; gap: 6px; padding: 10px; margin-bottom: 8px; border-radius: 12px; background: var(--secondary-background-color); }
  .note select, .note textarea { font: inherit; padding: 6px; border-radius: 8px; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); }
  .revgrid { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 10px; }
  .undo { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin: 2px 0 8px; }
  .rev { display: flex; flex-direction: column; gap: 3px; padding: 6px; border-radius: 14px; background: var(--secondary-background-color); min-width: 0; }
  .revname { font-size: .95em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .small { font-size: .8em; }
  .rev .acts { gap: 4px; }
  .rev select { flex: 1; min-width: 0; font-size: .85em; }
  .thumb { width: 100%; aspect-ratio: 4 / 3; object-fit: cover; border-radius: 10px; background: var(--card-background-color); cursor: zoom-in; display: block; }
  .lightbox { position: fixed; inset: 0; z-index: 10; background: rgba(0,0,0,.85); display: flex; align-items: center; justify-content: center; overflow: hidden; }
  .lbimg { max-width: 96vw; max-height: 92vh; border-radius: 8px; cursor: zoom-out; }
  .lbclose { position: absolute; top: 12px; right: 14px; font-size: 22px; width: 40px; height: 40px; border-radius: 50%; border: none; background: rgba(255,255,255,.15); color: #fff; cursor: pointer; }
  .thumb.broken { opacity: .3; }
  .acts { display: flex; gap: 6px; align-items: center; margin-top: 4px; flex-wrap: wrap; }
  input[data-folder] { flex: 1; min-width: 180px; font: inherit; padding: 6px 12px; border-radius: 999px; border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); }
  select { font: inherit; padding: 4px 8px; border-radius: 999px; border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); color-scheme: light dark; min-height: 32px; }
  select option { background: var(--card-background-color, #1c1c1f); color: var(--primary-text-color); }
  @container (max-width: 500px) { .grid { grid-template-columns: repeat(__SMALLCOLS__, minmax(0, 1fr)); } }
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

// --- Pixel sprite engine ------------------------------------------------------------------------------
// Kept in its own scope so its helpers (grid, px, shade…) don't clash with other cards.
const NWSprites = (() => {
// Pixel-art sprites on a 48x48 grid. Shapes are laid out in 32-unit coordinates (scaled by
// S), then outlined; faces and markings are placed pixel by pixel on the fine grid.
const N = 48, S = 1.5;

function grid() {
  return Array.from({ length: N }, () => Array(N).fill(null));
}
function px(g, x, y, c) {
  if (x >= 0 && y >= 0 && x < N && y < N) g[y][x] = c;
}
function ellipse(g, cx, cy, rx, ry, c, test) {
  cx *= S; cy *= S; rx *= S; ry *= S;
  for (let y = 0; y < N; y++)
    for (let x = 0; x < N; x++) {
      const d = ((x + 0.5 - cx) / rx) ** 2 + ((y + 0.5 - cy) / ry) ** 2;
      if (d <= 1 && (!test || test(x, y))) g[y][x] = c;
    }
}
function tri(g, a, b, c2, col) {
  [a, b, c2] = [a, b, c2].map(([x, y]) => [x * S, y * S]);
  const area = (p, q, r) => (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
  for (let y = 0; y < N; y++)
    for (let x = 0; x < N; x++) {
      const p = [x + 0.5, y + 0.5];
      const d1 = area(a, b, p), d2 = area(b, c2, p), d3 = area(c2, a, p);
      if (!((d1 < 0 || d2 < 0 || d3 < 0) && (d1 > 0 || d2 > 0 || d3 > 0))) g[y][x] = col;
    }
}
function thick(g, pts, r, c) {
  for (let i = 0; i < pts.length - 1; i++) {
    const [x1, y1] = pts[i], [x2, y2] = pts[i + 1];
    const steps = Math.ceil(Math.hypot(x2 - x1, y2 - y1) * 4);
    for (let s = 0; s <= steps; s++) {
      const t = s / steps;
      ellipse(g, x1 + (x2 - x1) * t, y1 + (y2 - y1) * t, r, r, c);
    }
  }
}
const lum = (c) => { const n = parseInt((c.length === 4 ? c.replace(/#(.)(.)(.)/, "#$1$1$2$2$3$3") : c).slice(1), 16); return ((n >> 16) * 3 + ((n >> 8) & 255) * 6 + (n & 255)) / 10; };
function outline(g, col) {
  const o = grid();
  for (let y = 0; y < N; y++)
    for (let x = 0; x < N; x++) {
      if (g[y][x]) continue;
      if ([[1, 0], [-1, 0], [0, 1], [0, -1]].some(([dx, dy]) => g[y + dy]?.[x + dx])) o[y][x] = col;
    }
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) if (o[y][x]) g[y][x] = o[y][x];
}
function rects(g) {
  let r = "";
  for (let y = 0; y < N; y++)
    for (let x = 0; x < N; x++) if (g[y][x]) r += `<rect x="${x}" y="${y}" width="1.02" height="1.02" fill="${g[y][x]}"/>`;
  return r;
}
function svg(g) {
  return `<svg viewBox="0 0 ${N} ${N}" shape-rendering="crispEdges" class="sprite">${rects(g)}</svg>`;
}
// Frames shown one after another (a flip-book): [[grid, seconds], ...], looping. All the
// distinct frames sit side by side on one strip and a single animation slides the strip,
// so exactly one frame shows at any moment. Each sprite starts at a random point.
function animated(frames) {
  const keys = [], strips = [];
  const index = frames.map(([g]) => {
    const key = rects(g);
    let i = keys.indexOf(key);
    if (i < 0) {
      i = keys.push(key) - 1;
      strips.push(`<g transform="translate(${i * N} 0)">${key}</g>`);
    }
    return i;
  });
  if (reducedMotion()) return svg(frames[0][0]); // still, when motion is turned down
  const total = frames.reduce((t, [, d]) => t + d, 0);
  const start = -(Math.random() * total).toFixed(2);
  let at = 0;
  const times = [], values = [];
  frames.forEach(([, d], k) => {
    times.push((at / total).toFixed(4));
    values.push(`${-index[k] * N} 0`);
    at += d;
  });
  return `<svg viewBox="0 0 ${N} ${N}" shape-rendering="crispEdges" class="sprite" overflow="hidden"><g>` +
    `<animateTransform attributeName="transform" type="translate" dur="${total}s" begin="${start}s" repeatCount="indefinite" calcMode="discrete" keyTimes="${times.join(";")}" values="${values.join(";")}"/>` +
    `${strips.join("")}</g></svg>`;
}

function reducedMotion() {
  if (globalThis.NW_FORCE_MOTION) return false; // preview pages play regardless
  try { return matchMedia("(prefers-reduced-motion: reduce)").matches; } catch { return false; }
}
// Each sprite's timings vary a little, so a row of animals never moves in step.
function jitter(d) {
  return +(d * (0.75 + Math.random() * 0.5)).toFixed(2);
}
// Breathing: everything above the chest rises a pixel (the chest row stretches). Columns from
// keep on (a cat's tail) stay put.
function breathe(g, keep = N) {
  const rows = g.map((r, y) => (r.some((c) => c) ? y : -1)).filter((y) => y >= 0);
  const top = rows[0], mid = Math.round(top + (rows[rows.length - 1] - top) * 0.55);
  const out = g.map((r) => r.slice());
  for (let y = Math.max(top - 1, 0); y < mid; y++) out[y] = [...g[y + 1].slice(0, keep), ...g[y].slice(keep)];
  return out;
}
// The whole sprite lifted off the ground (a hop).
function lift(g, dy) {
  const out = grid();
  for (let y = dy; y < N; y++) out[y - dy] = g[y].slice();
  return out;
}

function pixelCat(o) {
  return svg(catGrid(o));
}
// Idle: the tail sways and curls, breathing, an ear flick, blinks (one slow), now and then
// a yawn or a paw lick.
function idleCat(o) {
  return animated(idleCatFrames(o));
}
function idleCatFrames(o) {
  if (!o.pawUp) return o.tailWrap ? [...idleFrames(o), ...wrapIn(o), ...idleFrames(o, true), ...wrapOut(o)] : idleFrames(o);
  const down = { ...o, pawUp: false };
  const shuffle = (t) => [[catGrid({ ...o, tail: t, pawShift: 1 }), 0.3], [catGrid({ ...o, tail: t }), 0.7], [catGrid({ ...o, tail: t, pawShift: 1 }), 0.25], [catGrid({ ...o, tail: t }), 0.5]];
  const lower = (t) => [[catGrid({ ...down, tail: t, lick: "up" }), 0.15]];
  const up = [...idleFrames(o).slice(0, 14), ...shuffle(0), ...idleFrames(o).slice(14)];
  if (!o.tailWrap) return [...up, ...lower(0), ...idleFrames(down), ...lower(0)];
  // With tailWrap the tail also spends a stretch curled round the paw: it wraps in, the paws
  // go down and up again with it wrapped, and it unwraps before the moves that swing it.
  return [
    ...up, ...lower(0), ...idleFrames(down),
    ...wrapIn(down), ...idleFrames(down, true), ...lower(7),
    ...shuffle(7), ...idleFrames(o, true), ...wrapOut(o), // the paw up about half the loop, the paws down the other half
  ];
}
// tailWrap: the tail sweeps down to the side, then onto the ground curled round the front paw
// (tail 5, 6, 7); unwrapping plays the same steps back.
const wrapIn = (o) => [[catGrid({ ...o, tail: 5 }), 0.22], [catGrid({ ...o, tail: 6 }), 0.22]];
const wrapOut = (o) => [[catGrid({ ...o, tail: 6 }), 0.22], [catGrid({ ...o, tail: 5 }), 0.22]];
// wrapped: the tail lies curled round the paw throughout; in place of the swings, only its tip moves.
function idleFrames(o, wrapped = false) {
  const t0 = wrapped ? 7 : 0, c = (f) => catGrid({ ...o, ...f });
  const rest = c({ tail: t0 });
  return [
    [rest, jitter(1.2)],
    [c({ tail: wrapped ? 8 : 1 }), 0.5],
    [c({ tail: wrapped ? 9 : 2 }), jitter(1.2)],
    [c({ tail: wrapped ? 8 : 1 }), 0.5],
    [breathe(rest, 36), 0.7],
    [rest, jitter(1)],
    [c({ tail: t0, blink: true }), 0.18],
    [rest, jitter(0.9)],
    [c({ tail: t0, earFlick: true }), 0.22],
    [rest, 0.3],
    [c({ tail: t0, earFlick: true }), 0.22],
    [breathe(rest, 36), 0.7],
    [rest, jitter(0.8)],
    [c({ tail: t0, whiskerTwitch: true }), 0.16],
    [rest, 0.25],
    [c({ tail: t0, whiskerTwitch: true }), 0.16],
    [c({ tail: wrapped ? 9 : 3 }), jitter(1.2)],
    [rest, jitter(0.8)],
    [c({ tail: t0, halfBlink: true }), 0.3],
    [c({ tail: t0, blink: true }), 0.6],
    [c({ tail: t0, halfBlink: true }), 0.3],
    [rest, jitter(1.4)],
    [breathe(rest, 36), 0.7],
    [rest, jitter(1)],
    [c({ tail: t0, yawn: 1 }), 0.25],
    [c({ tail: t0, yawn: 2 }), 1],
    [c({ tail: t0, yawn: 1 }), 0.25],
    [rest, jitter(1.6)],
    [c({ tail: t0, lick: "up" }), 0.16],
    [c({ tail: t0, lick: "mid", lickY: 14 }), 0.12],
    [c({ tail: t0, lick: "mid", lickY: 9 }), 0.12],
    [c({ tail: t0, lick: "mid", lickY: 4 }), 0.12],
    [c({ tail: t0, lick: 1 }), 0.28],
    [c({ tail: t0, lick: 2 }), 0.22],
    [c({ tail: t0, lick: 1 }), 0.22],
    [c({ tail: t0, lick: 2 }), 0.22],
    [c({ tail: t0, lick: 1 }), 0.22],
    [c({ tail: t0, lick: 2 }), 0.22],
    [c({ tail: t0, lick: 1 }), 0.22],
    [c({ tail: t0, lick: 2 }), 0.22],
    [c({ tail: t0, lick: "mid", lickY: 4 }), 0.12],
    [c({ tail: t0, lick: "mid", lickY: 9 }), 0.12],
    [c({ tail: t0, lick: "mid", lickY: 14 }), 0.12],
    [c({ tail: t0, lick: "up" }), 0.16],
    [rest, jitter(1.2)],
    [rest, jitter(1)],
  ].filter(Boolean);
}
// On alert: ears up, tail straight up, wide eyes; still apart from a blink.
function alertCat(o) {
  return animated(alertCatFrames(o));
}
function alertCatFrames(o) {
  const a = catGrid({ ...o, alert: true });
  return [[a, jitter(2.4)], [catGrid({ ...o, alert: true, blink: true }), 0.18], [a, 1.2]];
}
function catGrid(o) {
  const g = grid();
  const fur = o.fur, WHITE = "#f1ece3";
  const isFur = (c) => c === fur;
  const bodyRx = o.fluffy || o.big ? 8.4 : o.slim ? 6.3 : o.small ? 6.8 : 7.5; // long hair or a big frame; a small cat narrower
  const bodyCy = 22.5 - 1 / 3, bodyRy = 8 + 1 / 3; // the body's top a pixel higher (a shorter neck), its bottom where it was
  const tails = o.tailPaths || [
    [[23, 25.5], [26.5, 25.5], [28.5, 22.5], [28, 19], [27, 16.5]],
    [[23, 25.5], [26.5, 25.5], [28.8, 22.5], [29, 19], [28.6, 16.6]],
    [[23, 25.5], [26.5, 25.5], [29, 22.8], [29.8, 19.5], [30, 16.8]],
    [[23, 25.5], [26.5, 25.5], [28.5, 22.5], [28, 19], [27, 17], [25.8, 17.6]], // the tip curled over
    [[23, 25.5], [25.6, 24.8], [26.4, 21.5], [26.6, 17], [26.4, 13]], // straight up and close, on alert
    // Wrapping the tail round to the front: 5 swept low to the side, 6 on the ground at the
    // side, 7 lying on the ground curled round the front paw.
    [[23, 25.5], [26.5, 26.2], [29, 26.4], [30.6, 25.2]],
    [[23, 25.5], [26.2, 27.2], [27.6, 28.6], [26.4, 29.4], [24.6, 29.6]],
    [[23, 25.5], [25.8, 27.4], [26.4, 28.9], [24.6, 29.7], [21.6, 29.8], [18.8, 29.6], [17.4, 29]],
    // Still wrapped, the tip moves: 8 lifted a little, 9 curled up.
    [[23, 25.5], [25.8, 27.4], [26.4, 28.9], [24.6, 29.7], [21.6, 29.8], [19, 29.3], [18, 28.3]],
    [[23, 25.5], [25.8, 27.4], [26.4, 28.9], [24.6, 29.7], [21.6, 29.8], [19.2, 29.4], [18.3, 28.4], [18.9, 27.6]],
  ];
  // Resting tails (not long-haired ones): a gentle curve rising close beside the body, low at
  // the root, the tip easing in without a kink. 0 rest, 1 and 2 swaying out, 3 the tip curled over.
  const sTails = [
    [[23, 26.3], [26, 25.5], [27.8, 22.2], [27.4, 18.4], [26.2, 15.8], [25.7, 13.6]],
    [[23, 26.3], [26.2, 25.5], [28.2, 22.3], [28, 18.5], [27, 15.8], [26.7, 13.6]],
    [[23, 26.3], [26.4, 25.5], [28.6, 22.4], [28.6, 18.6], [27.8, 15.8], [27.6, 13.6]],
    o.curlOut
      ? [[23, 26.3], [26, 25.5], [27.8, 22.2], [27.6, 18.6], [28, 16.2], [29.2, 15], [30.2, 15.6]] // curlOut: the tip hooked away from the body
      : [[23, 26.3], [26, 25.5], [27.8, 22.2], [27.4, 18.4], [26.2, 16], [24.9, 15]], // the tip curled over, toward the body
  ];
  const sShape = !o.tailPaths && !o.fluffy && !o.alert && (o.tail || 0) <= 3;
  let tailPath = sShape ? sTails[o.tail || 0] : tails[o.alert ? 4 : o.tail || 0];
  // Up at whisker height the tail keeps clear of the whiskers: its upper points sit further out
  // (the root and the curve near the body stay where they are).
  if (!o.fluffy && !o.tailPaths && (o.alert || (o.tail || 0) <= 4))
    tailPath = tailPath.map(([x, y]) => [y < 21 ? x + Math.min(1.6, (21 - y) * 0.45) : x, y]);
  // The tail's root sits low, near the ground, so it doesn't float off the hip: the first two
  // points drop a little (not for a long-haired tail, nor for one wrapped round to the front).
  if (!sShape && !o.tailPaths && !o.fluffy && !o.tailTaper && (o.alert || (o.tail || 0) <= 5))
    tailPath = tailPath.map(([x, y], i) => (i < 2 ? [x, y + 1] : i === 2 ? [x, y + 0.5] : [x, y]));
  if (o.tall) {
    // A taller cat carries a longer tail, so the extra height isn't left empty beside it.
    const [a, b] = tailPath.slice(-2), l = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (b[1] < a[1]) tailPath = [...tailPath.slice(0, -1), [b[0] + ((b[0] - a[0]) / l) * 1.4, b[1] + ((b[1] - a[1]) / l) * 1.4]];
  }
  // A wrapped tail lies on the ground in front of the paws. The taller cat's body pushes it down by
  // as many rows as it grows, which would take its bottom edge (and outline) off the frame: so it
  // starts that much higher, and lands level with the paws.
  const wrapLift = o.tall && !o.alert && (o.tail || 0) >= 5 ? (o.tall === 2 ? 3 : 1) : 0; // in rows
  if (wrapLift) tailPath = tailPath.map(([x, y], i) => [x, y - (wrapLift / S) * Math.min(1, i / 2)]);
  if (o.longTail) {
    const [a, b] = tailPath.slice(-2);
    if (b[1] < a[1]) tailPath = [...tailPath, [b[0] + (b[0] - a[0]) * 1.3, b[1] + (b[1] - a[1]) * 1.3]]; // carried on further (not when curled over)
  }
  // Resting, swaying, curled and alert tails are drawn on their own layer at the end (tailLayer);
  // only the tail wrapped round to the front (5-9) is still drawn here.
  const ownTail = !o.fluffy && (o.alert || (o.tail || 0) <= 4); // a long-haired tail keeps its own drawing
  if (ownTail) tailPath = [[100, 100], [101, 100]];
  const w0 = o.tailW || (o.fluffy ? 1.6 : 1.1);
  const taper = o.tailTaper || (o.fluffy ? null : [w0 * 1.3, w0 * 0.9]); // every tail tapers toward the tip
  // Inside the frame, outline included: no point so far right or low that the tail (as thick as
  // it is there) runs off the edge and loses its outline. A taller cat's tail moves down later.
  if (!ownTail) {
    const segs = tailPath.slice(1).map((b, i) => Math.hypot(b[0] - tailPath[i][0], b[1] - tailPath[i][1])), len = segs.reduce((t, l) => t + l, 0) || 1;
    const down = o.tall ? (o.tall === 2 ? 3 : 1) : 0, lim = (N - 2) / S - 0.15;
    // The tail is drawn a little shorter (shrunk toward its root), so it keeps its shape.
    const [rx, ry] = tailPath[0];
    let run = 0, k = 1;
    tailPath.forEach(([x, y], i) => {
      if (i) run += segs[i - 1];
      const r = taper ? taper[0] + (taper[1] - taper[0]) * (run / len) : w0, mx = (o.tailTaper || o.fluffy ? lim : N / S - 0.35) - r, my = (o.tailTaper || o.fluffy ? lim : N / S) - r - down / S; // (the bottom row may hold the outline; a drawn-round fluffy tail spreads a little further)
      if (i && x > mx && x > rx) k = Math.min(k, (mx - rx) / (x - rx));
    });
    if (k < 1) tailPath = tailPath.map(([x, y]) => [rx + (x - rx) * k, ry + (y - ry) * k]);
    // Too low: those points rise just enough (a tail on the ground lies flat along it; shrinking
    // toward the root instead would lift its tip off the ground).
    run = 0;
    tailPath = tailPath.map(([x, y], i) => {
      if (i) run += segs[i - 1];
      const r = taper ? taper[0] + (taper[1] - taper[0]) * (run / len) : w0;
      return [x, Math.min(y, (o.tailTaper || o.fluffy ? lim : N / S) - r - down / S)];
    });
  }
  // The tail as a round tube: for a pixel near it, how far along it is (in pixels, and as a
  // fraction), and its surface normal, for rings that wrap round it and for its shading.
  const tPts = tailPath.map(([x, y]) => [x * S, y * S]);
  const tLens = tPts.slice(1).map((q, i) => Math.hypot(q[0] - tPts[i][0], q[1] - tPts[i][1]));
  const tailLen = tLens.reduce((t, l) => t + l, 0);
  const tubeMemo = new Map(); // each pixel worked out once
  const tailMask = new Set(); // the tail's own pixels (a tube tail), for its edges later
  const tube = (x, y) => {
    const key = y * N + x;
    if (tubeMemo.has(key)) return tubeMemo.get(key);
    let best = null, run = 0;
    tPts.slice(1).forEach((q, i) => {
      const a = tPts[i], l = tLens[i] || 1;
      const t = Math.max(0, Math.min(1, ((x + 0.5 - a[0]) * (q[0] - a[0]) + (y + 0.5 - a[1]) * (q[1] - a[1])) / (l * l)));
      const dx = x + 0.5 - (a[0] + (q[0] - a[0]) * t), dy = y + 0.5 - (a[1] + (q[1] - a[1]) * t), d = Math.hypot(dx, dy);
      if (!best || d < best.d) best = { d, dx, dy, s: run + l * t };
      run += l;
    });
    best.f = best.s / (tailLen || 1);
    best.r = (taper ? taper[0] + (taper[1] - taper[0]) * best.f : w0) * S;
    tubeMemo.set(key, best);
    return best;
  };
  if (taper && !o.tailTaper) {
    const pad = Math.ceil(taper[0] * S) + 1, xs = tPts.map((q) => q[0]), ys = tPts.map((q) => q[1]);
    for (let y = Math.max(0, Math.floor(Math.min(...ys)) - pad); y <= Math.min(N - 1, Math.ceil(Math.max(...ys)) + pad); y++)
      for (let x = Math.max(0, Math.floor(Math.min(...xs)) - pad); x <= Math.min(N - 1, Math.ceil(Math.max(...xs)) + pad); x++) {
        const t = tube(x, y);
        if (t.d <= t.r) { g[y][x] = fur; tailMask.add(y * N + x); }
      }
  } else if (taper) {
    const [r0, r1] = taper, segL = tailPath.slice(1).map((b, i) => Math.hypot(b[0] - tailPath[i][0], b[1] - tailPath[i][1]));
    const total = segL.reduce((t, l) => t + l, 0);
    let run = 0;
    tailPath.slice(1).forEach((b, i) => {
      const a = tailPath[i], steps = Math.ceil(segL[i] * 4);
      for (let k = 0; k <= steps; k++) {
        const t = k / steps, f = (run + segL[i] * t) / total, r = r0 + (r1 - r0) * f;
        ellipse(g, a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, r, r, fur); // fat at the base, tapering to the tip
      }
      run += segL[i];
    });
  } else thick(g, tailPath, w0, fur);
  ellipse(g, 16, bodyCy, bodyRx, bodyRy, fur);
  ellipse(g, 9.8, 28.5, 1.7, 1.2, fur); // back feet
  ellipse(g, 22.2, 28.5, 1.7, 1.2, fur);
  ellipse(g, 13, 28.4, 2.1, 1.5, fur); // front paws
  ellipse(g, 19, 28.4, 2.1, 1.5, fur);
  for (let y = 44; y < N; y++) for (let x = 0; x < N; x++) g[y][x] = null; // flat on the ground
  if (o.fluffy) ellipse(g, 16, 13.4, 8.4, 5, fur);
  ellipse(g, 16, 10, 7, 6, fur);
  const up = o.alert ? 0.8 : 0; // ears pricked
  tri(g, [9, 9], [9.5, 1.5 - up], [14.5, 5.5], o.ears || fur);
  if (o.earFlick) tri(g, [23, 9], [24.2, 2.4], [18, 6], o.rightEar || o.ears || fur); // turned out
  else tri(g, [23, 9], [22.5, 1.5 - up], [17.5, 5.5], o.rightEar || o.ears || fur);
  // Where the head meets the body.
  if (o.neck === "none") for (let y = 20; y <= 25; y++) for (let x = 14; x <= 33; x++) g[y][x] = fur; // straight sides
  if (o.neck === "short") for (let y = 21; y <= 24; y++) for (let x = 0; x < N; x++) if (Math.abs(x + 0.5 - 24) <= (y === 21 || y === 24 ? 9 : 8)) g[y][x] = fur; // a slight waist
  if (o.neck === "ruff") {
    for (let y = 20; y <= 25; y++) for (let x = 14; x <= 33; x++) g[y][x] = fur;
    for (const [a, b, c] of [[[9.6, 13], [8, 15.4], [10.4, 15.2]], [[9.8, 15], [8.6, 17.2], [10.8, 16.8]]]) {
      tri(g, a, b, c, fur); // cheek tufts
      tri(g, [31.33 - a[0], a[1]], [31.33 - b[0], b[1]], [31.33 - c[0], c[1]], fur);
    }
  }

  // Chest and belly colours stay between the front legs: the shoulder lines rise from row 29,
  // so from there down they keep to the gap between the legs' inner edges.
  const between = (x, y) => o.shoulders === false || y < 29 || (y <= 32 ? x >= 22 && x <= 25 : x >= 23 && x <= 24);
  const on = (fn, c) => {
    for (let y = 0; y < N; y++)
      for (let x = 0; x < N; x++) if (isFur(g[y][x]) && fn(x, y)) g[y][x] = typeof c === "function" ? c(x, y) : c;
  };
  const cx = 23.5;
  if (o.calico) {
    const black = "#323034", grey = black, tan = "#c9935c", orange = tan; // just white, black and orange (plus shading)
    // The face: a tan patch over one eye and down that cheek; a soft grey cap over the top and
    // the other brow; white from between the eyes down the muzzle.
    const cap = o.capGrey || grey;
    on((x, y) => x <= 22 && y >= 6 && y <= 18 && ((x - 17) ** 2) / 28 + ((y - 12.5) ** 2) / 34 <= 1 && !(x >= 20 && y >= 15) && !(x >= 21 && y >= 13), tan); // the tan patch; white at the eye's inner corner and below it
    on((x, y) => y === 11 && (x === 27 || x === 28), tan); // a tan fleck above the other eye
    for (const [x, y] of [[18, 5], [19, 6], [19, 7]]) if (g[y][x] === o.ears) g[y][x] = black; // a little black at the inner edge of the orange ear
    on((x, y) => y <= 9 && !(y >= 8 && x >= 23 && x <= 24), cap); // the cap
    on((x, y) => x >= 25 && (y <= 9 || (x >= 30 && y <= 13)), cap); // over the other brow, and at that eye's outer corner; white round the rest of it
    on((x, y) => x <= 16 && ((x - 13) ** 2) / 12 + ((y - 30) ** 2) / 12 <= 1, black); // back patch, just past the side
    on((x, y) => x >= 31 && ((x - 34) ** 2) / 12 + ((y - 36) ** 2) / 12 <= 1, tan); // flank patch, just past the other side
    // The tail: black with wavy streaks of orange running through it, a black tip.
    on((x, y) => x >= 36 || (x >= 34 && y <= 31), (x, y) => (y > 26 && Math.floor((y + Math.round(Math.sin(x * 1.3) * 1.5)) / 2) % 3 === 0 ? orange : black));
  }
  if (o.tabby) {
    // A mackerel tabby: the forehead "M", a line from each eye, stripes curving down the
    // flanks, necklace bars on the chest, barred legs, a ringed tail with a dark tip.
    const st = o.tabbyDark || shade(fur, 0.45), light = shade(fur, 1.15); // tabbyDark: the stripes' own colour
    const M = [[19, 11], [19, 10], [20, 9], [21, 10], [22, 11], [23, 10], [24, 10], [25, 11], [26, 10], [27, 9], [28, 10], [28, 11]];
    on((x, y) => M.some(([mx, my]) => mx === x && my === y), st);
    on((x, y) => y <= 8 && [21, 23, 24, 26].includes(x), st); // lines up the crown
    if (o.tabbyBold) {
      // Bolder: the crown lines doubled, bars over the temples and down the cheeks.
      on((x, y) => y <= 9 && [19, 20, 27, 28].includes(x) && y >= 4, st);
      on((x, y) => (y === 11 || y === 12) && (Math.abs(x - 15) <= 1 || Math.abs(x - 32) <= 1), st);
      on((x, y) => (y === 17 || y === 18) && (x <= 14 || x >= 33), st);
    }
    on((x, y) => (y === 14 && (x === 16 || x === 17)) || (y === 14 && (x === 30 || x === 31)), st); // from the eyes
    on((x, y) => y === 16 && (x <= 15 || x >= 32), st); // cheeks
    on((x, y) => y >= 16 && y <= 21 && Math.abs(x - cx) <= 4, light); // paler muzzle and throat
    // Flank stripes: thin, broken, arcing down the sides only; the chest stays clear.
    if (o.classicTabby) {
      // Classic (marbled) tabby instead: a bullseye swirl on each side (a dark centre, then
      // a dark ring), the dark bands edged in a paler tan as marbling is; a band over each
      // shoulder. Mirrored.
      const pale = shade(fur, 1.45), band = shade(fur, 0.45);
      const ring = (x, y) => {
        const m = Math.min(x, 47 - x);
        if (y < 23 || y > 41 || m > 18) return null;
        const r = Math.hypot(m - 12.5, (y - 31.5) * 0.8);
        if (r < 1.5 || (r > 3 && r < 4.6) || (r > 6.2 && r < 7.6)) return "dark";
        if ((r >= 1.5 && r < 2.1) || (r >= 4.6 && r < 5.2)) return "pale";
        return null;
      };
      on((x, y) => ring(x, y) === "pale", pale);
      on((x, y) => ring(x, y) === "dark", band);
      on((x, y) => y >= 23 && y <= 24 && Math.min(x, 47 - x) >= 15 && Math.min(x, 47 - x) <= 19, band); // a band over each shoulder
    } else if (o.tabbyBold) on((x, y) => y >= 23 && y <= 41 && (x <= 18 || x >= 29) && Math.floor(y - 0.6 * Math.abs(x - cx)) % 4 < 2, st); // broader, unbroken flank stripes
    else on((x, y) => y >= 25 && y <= 40 && (x <= 17 || x >= 30) && Math.floor(y - 0.6 * Math.abs(x - cx)) % 4 === 0 && hash(Math.min(x, 47 - x), y) % 4 !== 0, st);
    on((x, y) => (y === 36 || y === 39) && ((x >= 17 && x <= 21 && !o.lick && !o.pawUp) || (x >= 26 && x <= 30)), st); // barred legs (only a leg that is down, never across the belly)
    if (taper && !o.tailTaper) on((x, y) => x >= 33 && isFur(g[y][x]) && ((x + 0.5 - 24) / (bodyRx * S)) ** 2 + ((y + 0.5 - bodyCy * S) / (bodyRy * S)) ** 2 > 1 && (() => { const t = tube(x, y); return t.d <= t.r + 0.5 && (t.f > 0.88 || t.s % 4 < 2); })(), st); // rings round the tail, a dark tip
    else on((x, y) => (x >= 36 || (x >= 34 && y <= 31)) && (y % 4 < 2 || y <= 26), st); // tail rings, dark tip
  }
  // legBars: faint tabby bars across the front legs, on these rows.
  if (o.legBars) on((x, y) => o.legBars.includes(y) && ((x >= 17 && x <= 20 && !o.lick && !o.pawUp) || (x >= 27 && x <= 30)), o.legBarColor || shade(fur, 0.86)); // only on a leg that is down, never across the belly
  if (o.tuxedo) {
    // White muzzle and cheeks running down into the chest; only a small chin patch is black.
    on((x, y) => y >= 11 && y <= 17 && Math.abs(x - cx) <= (y - 10.5) * 0.8, WHITE);
    on((x, y) => y >= 17 && y <= 22 && Math.abs(x - cx) <= 7.5, WHITE);
    on((x, y) => between(x, y) && y >= 22 && y <= 34 && Math.abs(x - cx) <= Math.min(8 - (y - 22) * 0.55 - (y >= 31 ? (y - 30) * 0.4 : 0), 30.5 - y), WHITE); // narrowing a pixel a side per row into the gap between the legs // the bib: broad under the chin, narrowing to a rounded point above the legs
    for (let y = 20; y <= 22; y++) for (let x = 0; x < N; x++) if (Math.abs(x - cx) <= 2.5 - (y - 20) * 0.5 && g[y][x]) g[y][x] = fur; // black chin
  }
  if (o.faintTabby) {
    // Soft tabby ghosting: a faint forehead "M" and crown lines, a few broken flank bars.
    const st = shade(fur, o.contrast ? 0.66 : 0.8);
    const M = [[20, 10], [21, 9], [22, 10], [25, 10], [26, 9], [27, 10]];
    on((x, y) => M.some(([mx, my]) => mx === x && my === y), st);
    on((x, y) => y >= 5 && y <= 8 && [22, 25].includes(x), st);
    on((x, y) => y >= 29 && y <= 38 && (x <= 15 || x >= 32) && ((x + 0.5 - 24) / (bodyRx * S)) ** 2 + ((y + 0.5 - bodyCy * S) / (bodyRy * S)) ** 2 <= 1 && y % 4 === 0 && hash(Math.min(x, 47 - x), y) % 3 !== 0, shade(fur, o.contrast ? 0.74 : 0.88)); // mirrored, so both sides match; the sides only, never the front legs
  }
  if (o.tearMarks) on((x, y) => (x === 21 || x === 26) && (y === 12 || y === 13), shade(fur, 1.6)); // pale streaks by the inner eyes
  if (o.paleMuzzle) on((x, y) => y >= 17 && y <= 21 && Math.abs(x + 0.5 - 24) <= 4 - (y === 21 ? 1 : 0), o.pale || shade(fur, 1.3));
  if (o.bandedTail) {
    const outside = (x, y) => (x >= 35 || (y >= 16 && x >= 34)) && ((x + 0.5 - 24) / (bodyRx * S)) ** 2 + ((y + 0.5 - bodyCy * S) / (bodyRy * S)) ** 2 > 1; // the tail, not the head's edge
    const band = o.ringColor || shade(fur, o.contrast ? 0.42 : 0.55);
    if (o.rings) {
      // Distance along the tail's path for each tail pixel; a thin ring every so often.
      const pts = tailPath.map(([x, y]) => [x * S, y * S]);
      const segs = pts.slice(1).map((b, i) => [pts[i], b, Math.hypot(b[0] - pts[i][0], b[1] - pts[i][1])]);
      const total = segs.reduce((t, [, , l]) => t + l, 0);
      let flat = false; // whether the nearest stretch of tail runs more across than up
      const along = (x, y) => {
        let best = Infinity, at = 0, run = 0;
        for (const [a, b, l] of segs) {
          const t = Math.max(0, Math.min(1, ((x + 0.5 - a[0]) * (b[0] - a[0]) + (y + 0.5 - a[1]) * (b[1] - a[1])) / (l * l)));
          const d = Math.hypot(a[0] + (b[0] - a[0]) * t - x - 0.5, a[1] + (b[1] - a[1]) * t - y - 0.5);
          if (d < best) { best = d; at = run + t * l; flat = Math.abs(b[0] - a[0]) > Math.abs(b[1] - a[1]) || y >= 36; } // the base runs along the ground
          run += l;
        }
        return at;
      };
      const start = 0, step = total / o.rings; // spaced over the whole tail; the rings behind him stay hidden
      if (o.ringPeriod && taper && !o.tailTaper) {
        // Rings wrap round the tail: one pixel thick, one every ringPeriod pixels along it,
        // and the last stretch dark for the tip.
        on((x, y) => outside(x, y) && isFur(g[y][x]), (x, y) => {
          const t = tube(x, y);
          return t.f > 0.9 || (t.s > 2 && t.s % o.ringPeriod < 1) ? band : fur;
        });
      } else if (o.ringPeriod) {
        // Each ring is one straight pixel line across the tail (level where the tail rises, upright
        // where it runs along), one every ringPeriod pixels of tail; a ring that would touch the
        // previous one (at the bend) is left out, so they never merge.
        const isTail = (x, y) => outside(x, y) && isFur(g[y]?.[x]);
        let path = tailPath.map(([x, y]) => [x * S, y * S]);
        for (let it = 0; it < 3; it++) { // Chaikin smoothing, ends kept
          const next = [path[0]];
          for (let i = 0; i < path.length - 1; i++) {
            const [p0, p1] = [path[i], path[i + 1]];
            next.push([p0[0] * 0.75 + p1[0] * 0.25, p0[1] * 0.75 + p1[1] * 0.25], [p0[0] * 0.25 + p1[0] * 0.75, p0[1] * 0.25 + p1[1] * 0.75]);
          }
          next.push(path[path.length - 1]);
          path = next;
        }
        const last = path[path.length - 1], before = path[path.length - 2], ln = Math.hypot(last[0] - before[0], last[1] - before[1]);
        path.push([last[0] + ((last[0] - before[0]) / ln) * 1.5, last[1] + ((last[1] - before[1]) / ln) * 1.5]); // into the rounded tip
        let prev = [], walked = 0, next = o.ringPeriod;
        const touches = (cells) => cells.some(([x, y]) => prev.some(([px2, py]) => Math.abs(px2 - x) <= 1 && Math.abs(py - y) <= 1));
        for (let i = 0; i < path.length - 1; i++) {
          const [p0, p1] = [path[i], path[i + 1]], l = Math.hypot(p1[0] - p0[0], p1[1] - p0[1]);
          while (walked + l >= next) {
            const t = (next - walked) / l, cx2 = p0[0] + (p1[0] - p0[0]) * t, cy2 = p0[1] + (p1[1] - p0[1]) * t;
            next += o.ringPeriod;
            const ang = Math.round(Math.atan2(p1[0] - p0[0], -(p1[1] - p0[1])) / (Math.PI / 4)) * (Math.PI / 4); // across the tail, snapped
            const sx = Math.round(Math.cos(ang)), sy = Math.round(Math.sin(ang));
            const cells = [];
            for (let k = -5; k <= 5; k++) {
              const x = Math.floor(cx2) + k * sx, y = Math.floor(cy2) + k * sy;
              if (isTail(x, y)) cells.push([x, y]);
            }
            if (!cells.length || touches(cells)) continue;
            for (const [x, y] of cells) g[y][x] = band;
            prev = cells;
          }
          walked += l;
        }
      } else
      on((x, y) => outside(x, y), (x, y) => {
        const a = along(x, y);
        if (a >= total - step * 0.45) return band; // the dark tip is the last ring
        return a > start && (a - start) % step < 1 ? band : o.tailLight || fur;
      });
    } else {
      let top = N;
      for (let y = 0; y < N && top === N; y++) for (let x = 33; x < N; x++) if (isFur(g[y][x]) && outside(x, y)) { top = y; break; }
      on((x, y) => x >= 33 && outside(x, y) && (y <= top + 2 || (y >= 37 ? Math.floor(x / 2) % 2 === 1 : Math.floor(y / 2) % 2 === 1)), band); // straight rings all along, a short dark tip
    }
  }
  if (o.darkTailTip) on((x, y) => x >= 36 && y <= 27, shade(fur, 0.45));
  if (o.cap) {
    // A dark cap over the ears and both eyes, split by a white blaze; a dark spot on the nose.
    const k = o.cap;
    on((x, y) => y <= 16 && !(y >= 8 && Math.abs(x + 0.5 - 24) <= (y - 6) * 0.45), k);
    on((x, y) => y >= 15 && y <= 19 && x >= 20 && x <= 25 && ((x - 22.5) ** 2) / 9 + ((y - 17) ** 2) / 5 <= 1, k); // the nose spot, a little off to one side
  }
  if (o.sides) {
    on((x, y) => x <= 15 && y >= 24 && y <= 37 && ((x - 12) ** 2) / 14 + ((y - 30) ** 2) / 40 <= 1, o.sides); // the dark back, showing past each side
    on((x, y) => x >= 32 && y >= 24 && y <= 37 && ((x - 35) ** 2) / 14 + ((y - 30) ** 2) / 40 <= 1, o.sides);
  }
  if (o.tailColor) on((x, y) => x >= 33 && ((x + 0.5 - 33.3) / 2.8) ** 2 + ((y + 0.5 - 42.75) / 2.2) ** 2 > 1 && ((x + 0.5 - 24) / (bodyRx * S)) ** 2 + ((y + 0.5 - bodyCy * S) / (bodyRy * S)) ** 2 > 1, o.tailColor); // the whole tail
  // With a front leg lifted, the belly behind it shows: below the held-up paw, or the whole
  // leg's length while licking.
  const bareTop = o.lick === 1 || o.lick === 2 || o.lick === "mid" ? 33 : o.lick === "up" ? 41 : o.pawUp ? 40 + (o.pawShift || 0) : null;
  const bareLeft = { 33: 20, 34: 19, 35: 19, 36: 19, 37: 19, 38: 19, 39: 19, 40: 19, 41: 20, 42: 21, 43: 21 }; // rounded, narrowing to the ground
  const bare = (x, y) => bareTop !== null && y >= bareTop && x <= 22 && x >= (bareLeft[y] ?? N);
  if (o.spottedBelly) {
    const pale = o.bellyPale || o.pale || shade(fur, 1.35), spot = shade(fur, 0.82); // spots a soft shade, not too dark; bellyPale: a belly paler than the fur but not as pale as the muzzle
    const d = o.bellyDrop || 0, t = 29 + d; // bellyDrop: the belly starts that many rows lower
    const half = { 0: d ? 2 : 3, 1: 4, 2: 4, 3: 3 }; // a lowered belly's top is a narrow cap
    // A lowered belly is only a narrow strip: a lifted leg uncovers the fur of the side there, not a pale patch.
    const inBelly = (x, y) => (!d && bare(x, y)) || (between(x, y) && y >= t && (y <= t + 3 && y < 33 ? Math.abs(x + 0.5 - 24) <= half[y - t] : x >= 23 && x <= 24)); // never onto the legs // rounded, then down between the legs to the ground
    // Even single-pixel spots, mirrored and staggered at the top, then alternating down the
    // strip between the legs, and on any belly a lifted leg uncovers.
    const spots = d
      ? [[23, t + 1], [24, t + 3], [23, t + 5], [24, t + 7], [23, t + 9], [24, t + 11]] // a lowered belly: alternating down the strip
      : [[21, t + 1], [26, t + 1], [22, t + 3], [25, t + 3], [23, t + 6], [24, t + 9], [23, t + 12]];
    const spotted = (x, y) => spots.some(([sx, sy]) => sx === x && sy === y) || (!d && bare(x, y) && ((x === 20 && y % 6 === 1) || (x === 22 && y % 6 === 4)));
    if (o.softSpots) {
      // Soft spots: a mid tone between the belly and the fur, eased in by a lighter ring of
      // pixels round each one.
      const mix = (a, b, k) => { const p = (c) => [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16)), [A, B] = [p(a), p(b)]; return "#" + A.map((v, i) => Math.round(v + (B[i] - v) * k).toString(16).padStart(2, "0")).join(""); };
      const mid = mix(pale, fur, 0.5), edge = mix(pale, fur, 0.15);
      const nearSpot = (x, y) => [[0, 1], [0, -1]].some(([dx, dy]) => spotted(x + dx, y + dy));
      on(inBelly, (x, y) => (spotted(x, y) ? mid : nearSpot(x, y) ? edge : pale));
    } else on(inBelly, (x, y) => (!o.plainBelly && spotted(x, y) ? spot : pale)); // pale (spotted) belly, kept off the legs
  }
  if (o.whiteChest) on((x, y) => bare(x, y) || between(x, y) && y >= 24 && (y <= 32 ? ((x - cx) ** 2) / 20 + ((y - 29) ** 2) / 30 <= 1 : x >= 23 && x <= 24), WHITE); // white chest, down the belly between the legs
  for (const [colour, runs] of o.marks || []) {
    const set = new Set();
    for (const row of String(runs).split(";")) {
      const [y, xs] = row.split(":");
      for (const run of (xs || "").split(",")) {
        const [a, b] = run.split("-").map(Number);
        for (let x = a; x <= (Number.isNaN(b) || b === undefined ? a : b); x++) set.add(`${x},${y}`);
      }
    }
    on((x, y) => set.has(`${x},${y}`), colour); // extra markings, as data
  }
  if (o.chestPatch) on((x, y) => ((x - cx) ** 2) / 4 + ((y - 26) ** 2) / 6 <= 1, WHITE); // a small white chest patch
  if (o.tuxedo || o.whitePaws) on((x, y) => y >= 41 && ((x >= 16 && x <= 22 && !o.lick && !o.pawUp) || (x >= 25 && x <= 31)), WHITE);
  if (o.whiteBackPaws || o.whitePaws) on((x, y) => y >= 41 && (x <= 15 || (x >= 32 && x <= 35)), WHITE); // back feet only, not the tail beside them
  if (o.whiteBackLegs) on((x, y) => (x <= 15 || (x >= 32 && x <= 34)) && y >= (x === 15 || x === 32 ? 36 : x === 14 || x === 33 ? 37 : 38), WHITE); // the lower hind legs too, rounded where they meet the body

  // A one-row bump on the tail's outer edge gets the row above it too, so it isn't a lone step.
  if (tailMask.size) {
    const right = (y) => { for (let x = N - 1; x >= 33; x--) if (g[y][x] && tailMask.has(y * N + x)) return x; return -1; };
    for (let y = 2; y < 40; y++) {
      const r0 = right(y - 1), r1 = right(y), r2 = right(y + 1);
      if (r0 >= 0 && r1 > r0 && r1 > r2 && r2 >= 0) { g[y - 1][r1] = g[y - 1][r1 - 1]; tailMask.add((y - 1) * N + r1); }
    }
  }
  outline(g, o.outline || "#141416");
  // Where the tail lies against the body or head (no gap for the outline), its own edge is
  // outlined, so it always reads as a separate shape.
  if (tailMask.size) {
    const inBodyOrHead = (x, y) => ((x + 0.5 - 24) / (bodyRx * S)) ** 2 + ((y + 0.5 - bodyCy * S) / (bodyRy * S)) ** 2 <= 1 || ((x + 0.5 - 24) / 10.5) ** 2 + ((y + 0.5 - 15) / 9) ** 2 <= 1;
    const isTail = (x, y) => tailMask.has(y * N + x) && !inBodyOrHead(x, y);
    const edge = [];
    for (let y = 0; y < N; y++)
      for (let x = 33; x < N; x++)
        if (isTail(x, y) && g[y][x] !== (o.outline || "#141416") && [[1, 0], [-1, 0], [0, 1], [0, -1]].some(([dx, dy]) => { const c = g[y + dy]?.[x + dx]; return c && c !== (o.outline || "#141416") && !isTail(x + dx, y + dy); })) edge.push([x, y]);
    for (const [x, y] of edge) g[y][x] = o.outline || "#141416";
  }
  const line = o.outline || "#141416";
  // The body's right edge mirrors its left edge, drawn across the tail's root so the tail
  // comes from behind.
  for (let y = 24; y < 44; y++) {
    let x = g[y].findIndex((c) => c);
    if (x < 0 || x > 23) continue;
    for (; g[y][x] === line && x < 23; x++) if (g[y][47 - x]) g[y][47 - x] = line;
  }
  // The tail drawn here (wrapped round to the front, or long-haired) in its own colour: dark with
  // orange bands on a calico, rings round a banded one. Where it lies against the body it is a
  // shade darker, so the two read apart.
  if (tailMask.size) {
    const inBodyOrHead = (x, y) => ((x + 0.5 - 24) / (bodyRx * S)) ** 2 + ((y + 0.5 - bodyCy * S) / (bodyRy * S)) ** 2 <= 1 || ((x + 0.5 - 24) / 10.5) ** 2 + ((y + 0.5 - 15) / 9) ** 2 <= 1;
    const isTail = (x, y) => tailMask.has(y * N + x) && !inBodyOrHead(x, y) && g[y]?.[x] && g[y][x] !== line;
    const base = o.tailColor || (o.calico ? "#323034" : null);
    const band = o.ringColor || shade(fur, o.contrast ? 0.42 : 0.55);
    const ringed = o.bandedTail && o.ringPeriod && taper && !o.tailTaper && !ownTail;
    const cells = [];
    for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) if (isTail(x, y)) cells.push([x, y]);
    for (const [x, y] of cells) {
      if (!ownTail && base && (g[y][x] === fur || g[y][x] === WHITE)) g[y][x] = base;
      if (!ownTail && o.calico && taper && !o.tailTaper && Math.floor(tube(x, y).s / 2) % 3 === 0) g[y][x] = "#c9935c";
      if (ringed) { const t = tube(x, y); if (t.f > 0.9 || (t.s > 9 && t.s % o.ringPeriod < 1)) g[y][x] = band; }
    }
    const against = cells.filter(([x, y]) => [[1, 0], [-1, 0], [0, 1], [0, -1]].some(([dx, dy]) => { const c = g[y + dy]?.[x + dx]; return c && c !== line && !isTail(x + dx, y + dy); }));
    for (const [x, y] of against) g[y][x] = shade(g[y][x], 0.88);
  }
  if (o.small) {
    // The narrower body pinches in under the head: move the deepest point of that notch
    // out by one pixel on each side.
    let best = -1, by = -1;
    for (let y = 18; y <= 28; y++) {
      const x = g[y].findIndex((c) => c);
      if (x > best && x < 23) { best = x; by = y; }
    }
    if (by >= 0 && g[by][best] === line) {
      g[by][best] = g[by][best + 1]; g[by][best - 1] = line;
      g[by][47 - best] = g[by][46 - best]; g[by][48 - best] = line;
    }
  }
  if (o.detail) {
    // Shading, light from above: the body's sides and the band under the jaw fall into
    // shadow, the top of the head catches the light (not under the ears, which shade it).
    // Worked out on the left half and mirrored, so it is symmetrical; every colour shades
    // itself, markings included.
    const brx = bodyRx * S + (o.small ? 1 : 0), cy0 = bodyCy * S, ry0 = bodyRy * S; // a small cat's shading sits a pixel further out, under its eased neck
    const inB = (x, y) => ((x + 0.5 - 24) / brx) ** 2 + ((y + 0.5 - cy0) / ry0) ** 2 <= 1;
    const inHead = (x, y) => ((x + 0.5 - 24) / 10.5) ** 2 + ((y + 0.5 - 15) / 9) ** 2 <= 1;
    const isEar = (x, y) => !inHead(x, y) && g[y]?.[x] && g[y][x] !== line;
    const underEar = (x, y) => [1, 2, 3, 4, 5].some((k) => isEar(x, y - k) || isEar(47 - x, y - k));
    const fac = grid(); // 0.94 shadow, 0.95 under the jaw, "lit" highlight
    for (let y = 0; y < 44; y++)
      for (let x = 0; x <= 23; x++) {
        if (y >= 25 && inB(x, y)) {
          // The body as a rounded form lit from above and in front: flat tones, no dither.
          const nx = (x + 0.5 - 24) / brx, ny = (y + 0.5 - cy0) / ry0, nz = Math.sqrt(Math.max(0, 1 - nx * nx - ny * ny));
          const d = -0.5 * ny + 0.87 * nz;
          if (d < 0.2) fac[y][x] = 0.84;
          else if (d < 0.46) fac[y][x] = 0.92;
          else if (d > 0.97) fac[y][x] = 1.05;
        }
        if (y >= 22 && y < 26 && !inHead(x, y) && g[y][x] && g[y][x] !== line && (g[y][x - 1] === line || g[y][x - 2] === line)) fac[y][x] = 0.94; // the shoulders, up to the neck
        const jaw = o.jawUp || 0, inJaw = (x, y) => inHead(x, y + jaw); // jawUp: the jaw (and the band under it) n rows higher
        if (y >= 23 - jaw && y <= 28 && !inJaw(x, y) && (inJaw(x, y - 2) || inJaw(x, y - 3))) fac[y][x] = 0.95; // the band under the jaw, a row down
        if (y < 24 && inHead(x, y) && !underEar(x, y) && (!inHead(x, y - 2) || (!inHead(x, y - 3) && (x + y) % 2 === 0))) fac[y][x] = "lit";
      }
    for (let y = 0; y < N; y++) for (let x = 24; x < N; x++) fac[y][x] = fac[y][47 - x];
    for (let y = 0; y < N; y++)
      for (let x = 0; x < N; x++) {
        const c = g[y][x], k = fac[y][x];
        if (!k || !c || c === line || c === "#d98f9c") continue;
        if (x > 23 && y >= 24 && !inB(x, y)) continue; // the tail keeps its own colours
        g[y][x] = shade(c, k === "lit" ? (midTone(c) ? 1.04 : 1.08) : k); // gentler on mid greys
      }
    // The tail, shaded as a round tube along its own path (light from above, toward us).
    for (let y = 0; y < N; y++)
      for (let x = 33; x < N; x++) {
        const c = g[y][x];
        if (!c || c === line || inB(x, y) || y >= 41) continue;
        const t = tube(x, y), r = t.r + 0.5;
        if (t.d > r + 0.5) continue; // not the tail
        const best = [t.d, t.dx, t.dy];
        const nx = best[1] / r, ny = best[2] / r, nz = Math.sqrt(Math.max(0, 1 - nx * nx - ny * ny));
        const d = -0.6 * ny + 0.8 * nz - 0.15 * nx;
        const k = d < 0.15 ? 0.8 : d < 0.45 ? 0.9 : d > 0.95 ? 1.08 : 1;
        if (k !== 1) g[y][x] = shade(c, k);
      }
  }
  if (o.lick || o.pawUp) {
    // The hind leg behind the lifted paw: a thigh curving down to a foot with toes.
    // Just the hind foot on that side, set apart by one short mark; no toe lines.
    px(g, 20, 42, line); px(g, 20, 43, line);
  }
  // Shoulders (on unless shoulders: false): where each front leg rises into the body.
  const shoulder = (x0) => {
    // The leg's outer outline carries on up four rows, then a softer line leans in toward
    // the neck; its inner edge steps in one, beside the belly's top.
    const k = shade(fur, 0.55), left = x0 < 23, dir = left ? 1 : -1;
    const outer = left ? x0 : x0 + 6, inner = left ? x0 + 6 : x0;
    const put = (x, y, c) => { if (g[y]?.[x] && g[y][x] !== line) px(g, x, y, c); };
    for (const y of [29, 30, 31, 32]) put(outer, y, line);
    put(outer, 28, k);
    for (const y of o.shoulderLean === false ? [27] : [26, 27]) put(outer + dir, y, k); // shoulderLean: false leaves out its topmost pixel
    // The inner outline, two pixels up (three on a tall cat, whose stretched chest rows would
    // otherwise leave it starting far below the shoulder line).
    const innerTop = o.tall === 2 ? 30 : 31;
    for (let y = innerTop; y <= 32; y++) put(inner - dir, y, line);
    put(inner - dir, innerTop - 1, k);
    // The hind leg's thigh, set apart from the body by a curve rising to the front leg.
  };
  for (const x0 of o.lick === 1 || o.lick === 2 || o.lick === "mid" ? [25] : [16, 25]) {
    if (o.lick === "up" && x0 === 16) {
      // Starting the lick: the paw just lifted off the ground, the leg otherwise as it stands.
      const pc = g[42]?.[27] && g[42][27] !== line ? g[42][27] : fur, lift = 2; // just off the ground, so it still reads as the foot
      for (let y = 33; y <= 40 - lift; y++) { px(g, x0, y, line); px(g, x0 + 6, y, line); }
      for (let y = 41 - lift; y <= 43 - lift; y++) {
        px(g, x0 - 1, y, line); px(g, x0 + 7, y, line);
        for (let x = x0; x <= x0 + 6; x++) px(g, x, y, y === 41 - lift && (x === x0 || x === x0 + 6) ? line : pc);
      }
      for (let x = x0; x <= x0 + 6; x++) px(g, x, 44 - lift, line); // the pad's underside, off the ground
      px(g, x0 + 2, 43 - lift, line); px(g, x0 + 4, 43 - lift, line); // toes
      if (o.cuffs) for (const y of [39 - lift, 40 - lift]) for (let dx = 1; dx <= 2; dx++) px(g, x0 + dx, y, o.cuffs); // lifted with the paw
      continue;
    }
    if (o.pawUp && x0 === 16) {
      if (o.shoulders !== false) shoulder(x0);
      // Paw up: this front paw held up, curled against the chest, three feet on the ground.
      const pc = g[38][19] && g[38][19] !== line ? g[38][19] : fur;
      const ps = o.pawShift || 0; // a little shuffle: the paw settles a pixel lower
      for (let y = 33; y <= 35 + ps; y++) { px(g, x0, y, line); px(g, x0 + 6, y, line); }
      for (let y = 36 + ps; y <= 38 + ps; y++) {
        const sh = y >= 37 + ps ? 1 : 0; // tipped in toward the chest
        px(g, x0 + sh, y, line); px(g, x0 + 6 + sh, y, line);
        for (let x = x0 + 1 + sh; x <= x0 + 5 + sh; x++) px(g, x, y, pc);
      }
      for (let x = x0 + 2; x <= x0 + 6; x++) px(g, x, 39 + ps, line); // its underside, well off the ground
      px(g, x0 + 3, 38 + ps, line); px(g, x0 + 5, 38 + ps, line); // toes, hanging
      // Cuffs: the same size as the standing leg's (two by two, on the outside), so the pair match.
      if (o.cuffs) for (const y of [34 + ps, 35 + ps]) for (let x = x0 + 1; x <= x0 + 2; x++) px(g, x, y, o.cuffs);
      continue;
    }
    for (let y = 33; y <= 40; y++) { px(g, x0, y, line); px(g, x0 + 6, y, line); } // the arm
    if (o.shoulders !== false) shoulder(x0);
    for (let y = 41; y <= 43; y++) { px(g, x0 - 1, y, line); px(g, x0 + 7, y, line); } // the paw, a touch wider
    px(g, x0, 41, line); px(g, x0 + 6, 41, line);
    for (const y of [42, 43]) { px(g, x0, y, g[y][x0 + 1]); px(g, x0 + 6, y, g[y][x0 + 5]); }
    px(g, x0 + 2, 43, line); px(g, x0 + 4, 43, line); // toes
    px(g, x0 - 1, 43, line); px(g, x0 + 7, 43, line); // rounded corners, closed off (no see-through gaps)
    if (o.cuffs) for (const y of [39, 40]) for (let dx = 0; dx < 2; dx++) px(g, x0 < 23 ? x0 + 1 + dx : x0 + 4 + dx, y, o.cuffs); // part-way round, on the outside
  }

  // Inner ears.
  const pink = o.innerEar || "#d98f9c";
  for (const [x, y] of [[15, 8], [15, 9], [15, 10], [16, 9], [16, 10]]) { px(g, x, y, pink); px(g, 47 - x + (o.earFlick ? 1 : 0), y + (o.earFlick ? 1 : 0), pink); } // the flicked ear's inside moves with it
  // Eyes: 3 wide, 3 tall, a slit pupil, a glint.
  const eye = o.eyes || "#d9b23a";
  for (const ex of [18, 27]) {
    if (o.blink || o.yawn === 2 || o.lick === 1 || o.lick === 2) { for (let dx = 0; dx < 3; dx++) px(g, ex + dx, 14, o.lid || "#141416"); continue; }
    for (let dx = 0; dx < 3; dx++) for (let dy = 0; dy < 3; dy++) px(g, ex + dx, 13 + dy, dy === 2 && o.eyeLow ? o.eyeLow : eye); // eyeLow: the lower part a warmer tone
    if (o.lidLine && !o.halfBlink && o.yawn !== 1) for (let dx = 0; dx < 3; dx++) px(g, ex + dx, 12, o.lidLine); // lidLine: a dark upper lid, so the eye reads almond
    if (o.alert) { px(g, ex + 1, 14, "#111"); px(g, ex + 1, 15, "#111"); px(g, ex, 14, "#111"); } // round pupils
    else { px(g, ex + 1, 13, "#111"); px(g, ex + 1, 14, "#111"); px(g, ex + 1, 15, "#111"); }
    px(g, ex + (ex < 23 ? 2 : 0), 13, "#fff");
    if (o.halfBlink || o.yawn === 1) for (let dx = 0; dx < 3; dx++) px(g, ex + dx, 13, o.lid || "#141416"); // lids half down
  }
  if (o.eyeliner) {
    const rim = o.eyeliner;
    for (const ex of [18, 27]) {
      for (let dx = 0; dx < 3; dx++) { if (o.rimTop && !o.halfBlink && !o.blink && !o.yawn && !o.lick) px(g, ex + dx, 12, rim); px(g, ex + dx, 16, rim); }
      const inner = ex < 23 ? ex + 3 : ex - 1, outer = ex < 23 ? ex - 1 : ex + 3;
      for (const y of [13, 14, 15]) px(g, inner, y, rim);
      px(g, outer, 15, rim);
    }
  }
  // Nose: a small pink triangle; mouth: a short line splitting into a "w".
  const m = o.mouth || "#3a2a2a";
  const nose = o.nose || "#e48a95";
  for (const x of [22, 23, 24, 25]) px(g, x, 17, nose);
  px(g, 23, 18, nose); px(g, 24, 18, nose);
  px(g, 23, 19, m); px(g, 24, 19, m);
  px(g, 22, 20, m); px(g, 21, 20, m); px(g, 20, 19, m);
  px(g, 25, 20, m); px(g, 26, 20, m); px(g, 27, 19, m);
  if (o.yawn) {
    // The mouth opens: dark inside, a pink tongue at the bottom.
    const deep = o.yawn === 2 ? 22 : 21;
    for (let y = 19; y <= deep; y++) for (let x = 21; x <= 26; x++) if (Math.abs(x + 0.5 - 24) <= (y === 19 ? 2 : 3)) px(g, x, y, "#3a1f24");
    for (const x of [22, 23, 24, 25]) px(g, x, deep, "#e48a95");
  }
  // Whiskers: fine lines across the cheeks and out past the face.
  const wk = o.whiskers || "#c9c5bd";
  for (const [x, y0] of [[19, 18], [18, 18], [17, 17], [16, 17], [15, 17], [14, 16], [13, 16], [12, 16], [19, 19], [18, 19], [17, 19], [16, 20], [15, 20], [14, 20], [13, 21], [12, 21]]) {
    const y = o.whiskerTwitch && x <= 14 ? y0 - 1 : y0; // a twitch: the outer ends lift
    px(g, x, y, wk); px(g, 47 - x, y, wk);
  }
  if (o.collar) {
    // A collar round the neck, under the chin, with an optional bell. Seen only in infrared,
    // its colour is unknown, so it shows as a neutral grey (o.ir).
    const band = o.ir ? "#8d8b90" : o.collar, dark = shade(band, 0.7);
    for (let x = 0; x < N; x++) {
      const d = Math.abs(x + 0.5 - 24);
      if (d > 8) continue;
      const top = 23 + (d <= 2.5 ? 2 : d <= 5.5 ? 1 : 0); // dipping in two steps at the front, round the neck
      for (const y of [top, top + 1]) {
        const c = g[y][x];
        if (c && c !== line) g[y][x] = y === top + 1 ? dark : band;
      }
    }
    if (o.bell) {
      const gold = o.ir ? "#c4c2c6" : "#d9b23a", dim = o.ir ? "#9a989c" : "#a8842a";
      for (const [x, y, c] of [[23, 27, gold], [24, 27, gold], [23, 28, dim], [24, 28, dim]]) px(g, x, y, c);
      px(g, 22, 27, line); px(g, 25, 27, line); px(g, 22, 28, line); px(g, 25, 28, line); px(g, 23, 29, line); px(g, 24, 29, line);
    }
  }
  // Depth: what shows behind the front legs low down (between them, and where a lifted leg
  // was) darkens gently toward the ground, its top rounded (the edge pixels a step lighter).
  {
    // Light coats need a deeper step to show at all.
    const fade = { 39: 0.97, 40: 0.94, 41: 0.91, 42: 0.88, 43: 0.86 };
    const fadeLight = { 39: 0.94, 40: 0.89, 41: 0.84, 42: 0.8, 43: 0.77 };
    const behind = (x, y) => g[y]?.[x] && g[y][x] !== line && ((x >= 23 && x <= 24) || bare(x, y));
    const was = g.map((r) => r.slice());
    for (let y = 39; y <= 43; y++)
      for (let x = 17; x <= 30; x++) {
        if (!behind(x, y)) continue;
        const edge = y <= 41 && (!behind(x - 1, y) || !behind(x + 1, y));
        const t = lum(was[y][x]) > 170 ? fadeLight : fade;
        const f = edge ? t[y - 1] ?? 1 : t[y];
        if (f < 1) g[y][x] = shade(was[y][x], f);
      }
    // Where the front legs stand so close that no gap shows, the shadow falls on their inner
    // edges instead: the pixel just inside each leg's inner outline.
    for (let y = 39; y <= 43; y++) {
      if (behind(23, y) || behind(24, y)) continue;
      let l = 23, r = 24;
      while (l > 17 && was[y][l] !== line) l--;
      while (r < 30 && was[y][r] !== line) r++;
      while (l > 17 && was[y][l - 1] === line) l--;
      while (r < 30 && was[y][r + 1] === line) r++;
      for (const x of [l - 1, r + 1]) {
        const c = was[y][x];
        if (c && c !== line) g[y][x] = shade(c, 1 - (1 - (lum(c) > 170 ? fadeLight : fade)[y]) * 0.4); // lighter than the gap: it's the leg itself
      }
    }
  }
  if (o.lick && o.lick !== "up") {
    // A front paw raised to the mouth (over the whiskers), toes up, a lick of tongue on it.
    const pw = g[42]?.[27] && g[42][27] !== line ? g[42][27] : fur;
    const up = o.lick === "mid", dy = up ? o.lickY ?? 9 : 0; // on the way up ("mid") it is lower, lickY rows below the mouth
    // The foreleg below the raised paw: the same width as the standing leg (outlined columns
    // at x 17 and 23), running down from the paw into the chest.
    const legC = o.legColor || (o.tuxedo || o.classicTabby ? fur : g[30]?.[17] && g[30][17] !== line ? g[30][17] : fur);
    // Held low (the paw at or below the shoulder), the leg rises from the paw to the shoulder;
    // held higher, it runs down from the paw into the chest to a closed elbow.
    // On its way up the whole foreleg shows: from the shoulder (when the paw is below it)
    // down past the paw to the bent elbow, the paw held in front of it.
    const low = up && 20 + dy >= 27;
    const top = up ? Math.min(27, 24 + dy) : 24 + dy, bottom = up ? Math.max(37, 26 + dy) : 31;
    const join = shade(fur, 0.68); // where the leg meets the body: soft, not a hard outline
    // Held high, the leg below the paw slants out to where the leg really is (two pixels to the
    // side by its base), not straight down the middle of the chest.
    const out = (y) => (low ? 0 : -Math.round(((y - top) * 2) / Math.max(1, bottom - top)));
    for (let y = top; y <= bottom; y++) {
      const soft = up && low && y === top; // only the shoulder join is soft
      const elbow = up && y === bottom; // the bent elbow below the paw: rounded and closed
      const o2 = out(y);
      if (!elbow) { px(g, 17 + o2, y, soft ? join : line); px(g, 23 + o2, y, soft ? join : line); } // the elbow's corners stay body
      for (let x = 18; x <= 22; x++) px(g, x + o2, y, elbow && (x === 18 || x === 22) ? line : legC);
    }
    if (up) for (let x = 19; x <= 21; x++) px(g, x + out(bottom), bottom + 1, line);
    // Cuffs: the paw is turned over, so the back of the wrist shows: the cuff right across it,
    // except its inside edge (toward the chest), a row of white clear of the toe beans.
    if (o.cuffs) for (const y of o.short && dy === 0 ? [26, 27] : [25 + dy, 26 + dy]) for (let x = 18; x <= 21; x++) px(g, x + out(y), y, o.cuffs); // (a short cat loses row 24 later)
    // The slanted leg leaves a one-pixel sliver of the side between it and the body's edge: plain
    // fur there (a patch of colour would show as stray dark pixels between two lines).
    for (let y = top; y <= bottom; y++)
      for (let x = 12; x <= 17; x++)
        if (g[y][x] && g[y][x] !== line && g[y][x - 1] === line && g[y][x + 1] === line) px(g, x, y, fur);
    // The paw, toes up: the same size and shape as a standing paw, turned over.
    for (let x = 17; x <= 23; x++) px(g, x, 20 + dy, line); // its top edge
    for (let y = 21 + dy; y <= 23 + dy; y++) {
      px(g, 16, y, line); px(g, 24, y, line);
      for (let x = 17; x <= 23; x++) px(g, x, y, pw);
    }
    px(g, 17, 21 + dy, line); px(g, 23, 21 + dy, line); // rounded shoulders of the paw
    px(g, 19, 21 + dy, line); px(g, 21, 21 + dy, line); // toes
    const bean = o.beans || o.innerEar || (lum(pw) > 120 ? "#d98f9c" : shade(pw, 1.6)); // the face's own pink if it has one; pink on a pale paw
    for (const x of [18, 20, 22]) px(g, x, 22 + dy, bean); // toe beans
    for (const x of [19, 20, 21]) px(g, x, 23 + dy, bean); // the main pad
    if (!up) {
      px(g, 25, 21, "#e48a95");
      if (o.lick === 2) px(g, 25, 20, "#e48a95");
    }
  }
  let zLayer = null; // the wrapped tail on its own, for cats drawn in layers (zTail)
  if (!o.alert && o.tail >= 6 && o.tail <= 9) {
    const tg = grid(), base = o.tailColor || (o.calico ? "#323034" : fur);
    if (o.tailTaper) {
      // Its own taper (fat at the root): drawn the same shape as the first pass, so the two
      // passes' outlines land on each other instead of side by side.
      const [r0, r1] = o.tailTaper, segL = tailPath.slice(1).map((b, i) => Math.hypot(b[0] - tailPath[i][0], b[1] - tailPath[i][1]));
      const total = segL.reduce((t, l) => t + l, 0);
      let run = 0;
      tailPath.slice(1).forEach((b, i) => {
        const a = tailPath[i], steps = Math.ceil(segL[i] * 4);
        for (let k = 0; k <= steps; k++) {
          const t = k / steps, r = r0 + (r1 - r0) * ((run + segL[i] * t) / total);
          ellipse(tg, a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, r, r, base);
        }
        run += segL[i];
      });
    } else thick(tg, tailPath, o.tailW || (o.fluffy ? 1.6 : 1.1), base);
    // Along the tail (in pixels, and as a fraction): orange bands on a calico, rings on a banded one.
    const wp = tailPath.map(([x, y]) => [x * S, y * S]), wl = wp.slice(1).map((q, i) => Math.hypot(q[0] - wp[i][0], q[1] - wp[i][1])), wTotal = wl.reduce((t, l) => t + l, 0);
    const along = (x, y) => {
      let best = Infinity, at = 0, run = 0;
      wp.slice(1).forEach((q, i) => {
        const a = wp[i], l = wl[i] || 1, t = Math.max(0, Math.min(1, ((x + 0.5 - a[0]) * (q[0] - a[0]) + (y + 0.5 - a[1]) * (q[1] - a[1])) / (l * l)));
        const d = Math.hypot(a[0] + (q[0] - a[0]) * t - x - 0.5, a[1] + (q[1] - a[1]) * t - y - 0.5);
        if (d < best) { best = d; at = run + l * t; }
        run += l;
      });
      return at;
    };
    const band = o.ringColor || shade(fur, o.contrast ? 0.42 : 0.55);
    for (let y = 0; y < N; y++)
      for (let x = 0; x < N; x++) {
        if (!tg[y][x]) continue;
        const s2 = along(x, y);
        if (o.calico && Math.floor(s2 / 2) % 3 === 1) tg[y][x] = "#c9935c";
        else if (o.bandedTail && o.ringPeriod && (s2 > wTotal * 0.9 || (s2 > 9 && s2 % o.ringPeriod < 1))) tg[y][x] = band;
      }
    outline(tg, line);
    zLayer = { tg: tg.map((r) => r.slice()), along, wp };
    const tail = (x, y) => tg[y]?.[x] && tg[y][x] !== line;
    // The tail comes from behind the body: above the feet it shows only where the sprite is
    // empty, and the body's outline runs down its edge (as on the other side). From the feet down
    // it lies in front, over the feet and their outlines, so no lines cross it. (A lifted tail's
    // "feet" line is lifted with it: the body is stretched down to meet it afterwards.)
    const fy = 39 - wrapLift;
    const shownM = grid();
    for (let y = 0; y < N; y++)
      for (let x = 0; x < N; x++)
        if (tg[y][x] && (!g[y][x] || y >= fy || (x >= 34 && (g[y][x] === line || tailMask.has(y * N + x))))) { g[y][x] = tg[y][x]; if (tail(x, y)) shownM[y][x] = 1; } // (tailMask: the same tail's first pass)
    // Its top edge where it crosses in front of the leg keeps its outline (the leg is behind it).
    for (let y = 0; y < fy; y++)
      for (let x = 0; x <= Math.max(33, Math.floor(24 + bodyRx * S) - 1); x++) // (a wider body: as far as its edge)
        if (tg[y][x] === line && [[0, 1], [1, 0], [-1, 0]].some(([dx, dy]) => y + dy >= fy && shownM[y + dy]?.[x + dx])) g[y][x] = line;
    // The body's right edge (mirroring its left one) runs down beside the tail.
    let lx = 0;
    while (lx < 23 && g[fy - 2][lx] !== line) lx++;
    const ex = lx < 23 ? N - 1 - lx : 33;
    // Where the tail first shows in front of the body (behind it just above), its top edge is
    // the body's lower edge: an outline there too, as far as the body's edge. Past it the tail
    // is one piece, front and back joined: no line across it.
    for (let x = 0; x <= ex; x++)
      if (shownM[fy]?.[x] && !shownM[fy - 1]?.[x] && g[fy - 1][x] && g[fy - 1][x] !== line) g[fy - 1][x] = line;
    for (let x = 1; x < ex; x++) // no one-pixel gap left in that line (over the tail only)
      if (shownM[fy][x] && g[fy - 1][x] !== line && g[fy - 1][x - 1] === line && g[fy - 1][x + 1] === line && g[fy][x] !== line) { g[fy - 1][x] = line; shownM[fy - 1][x] = 0; }
    const opened = [];
    for (let x = ex + 1; x < N; x++)
      if (g[fy - 1][x] === line && tail(x, fy - 2) && tail(x, fy) && g[fy - 2][x] !== line && g[fy][x] !== line) { g[fy - 1][x] = tg[fy - 1][x] && tg[fy - 1][x] !== line ? tg[fy - 1][x] : g[fy][x]; shownM[fy - 1][x] = 1; opened.push(x); }
    for (let y = fy - 3; y < fy; y++) { px(g, ex, y, line); shownM[y][ex] = 0; }
    // ...and joins the edge above it without a gap.
    if (g[fy - 4]?.[ex] !== line && g[fy - 4]?.[ex + 1] !== line && g[fy - 5]?.[ex + 1] === line) { px(g, ex + 1, fy - 4, line); shownM[fy - 4][ex + 1] = 0; }
    // Where the back of the tail rises beside the body, its own edge still runs on down to the front.
    for (const x of opened) if (g[fy - 2][x] === line) { g[fy - 1][x] = line; shownM[fy - 1][x] = 0; }
    // Where the tail meets the body it is a shade darker (the root most of all), so the two read apart.
    const shown = (x, y) => shownM[y]?.[x] === 1;
    const [rx, ry] = [tailPath[0][0] * S, tailPath[0][1] * S];
    const against = [];
    for (let y = 0; y < N; y++)
      for (let x = 0; x < N; x++) {
        if (!shown(x, y)) continue;
        const near = [[1, 0], [-1, 0], [0, 1], [0, -1]].some(([dx, dy]) => { const c = g[y + dy]?.[x + dx]; return c && !shown(x + dx, y + dy) && (tg[y + dy]?.[x + dx] !== line || (x + dx === ex && y + dy >= fy - 3 && y + dy < fy)); });
        const dr = Math.hypot(x + 0.5 - rx, y + 0.5 - ry);
        if (near || dr < 2.6) against.push([x, y, near ? 0.88 : 0.9 + (dr / 2.6) * 0.07]); // fading out from the root
      }
    for (const [x, y, k] of against) g[y][x] = shade(tg[y][x], k);
  }
  if (o.tall) {
    // Taller: two body rows repeated, the whole cat lifted a row (ear tips stay in frame).
    const rows = g.slice(1, 45).map((r) => r.slice()), from = rows.map((r, i) => i + 1); // each row's source row
    // tallAt: where the repeated rows go (chest rows by default; leg rows give longer legs).
    const [t1, t2, t3] = o.tallAt || [27, 29, 30];
    rows.splice(t1, 0, rows[t1 - 1].slice()); from.splice(t1, 0, from[t1 - 1]); // chest rows, clear of the belly spots and the tail's bend
    rows.splice(t2, 0, rows[t2 - 1].slice()); from.splice(t2, 0, from[t2 - 1]);
    // tall: 2 adds two more body rows (copies of a plain row, so they're coloured like it).
    if (o.tall === 2) { rows.splice(t3, 0, rows[t3].slice(), rows[t3].slice()); from.splice(t3, 0, from[t3], from[t3]); }
    const old = g.map((r) => r.slice()), drop = o.tall === 2 ? 3 : 1; // the tail moves down with the body
    // The tail (with the outline round it) moves down whole, never stretched or cut: its
    // pixels come from drop rows up; anything else from the stretched rows.
    const nearTail = (x, y) => [[0, 0], [1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [-1, 1], [1, -1], [-1, -1]].some(([dx, dy]) => tailMask.has((y + dy) * N + x + dx));
    for (let y = 0; y < rows.length + 1 && y < N; y++) {
      g[y] = rows[y] || Array(N).fill(null);
      for (let x = 32; x < N; x++) {
        const src = y - drop;
        if (x >= 36) g[y][x] = old[src]?.[x] ?? null; // the tail isn't stretched, so its rings stay even
        else if (src >= 0 && tailMask.size && nearTail(x, src) && !(((x + 0.5 - 24) / (bodyRx * S)) ** 2 + ((src + 0.5 - bodyCy * S) / (bodyRy * S)) ** 2 <= 1)) g[y][x] = old[src][x];
        else if (from[y] !== undefined && tailMask.size && nearTail(x, from[y]) && !(((x + 0.5 - 24) / (bodyRx * S)) ** 2 + ((from[y] + 0.5 - bodyCy * S) / (bodyRy * S)) ** 2 <= 1)) g[y][x] = old[src]?.[x] ?? null; // a stretched copy of tail: replaced
      }
    }
  }
  if (o.slim) {
    // A slim cat's back feet tuck in a pixel at their outer edges.
    for (let y = 41; y <= 43; y++) {
      const x0 = g[y].findIndex((c) => c), x1 = N - 1 - [...g[y]].reverse().findIndex((c) => c);
      if (x0 >= 0 && x0 <= 12) { g[y][x0] = null; g[y][x0 + 1] = line; }
      if (x1 >= 35 && x1 < N - 1 && x1 <= 36) { g[y][x1] = null; g[y][x1 - 1] = line; }
    }
  }
  if (o.grow) {
    // A pixel taller: a plain body row repeated, everything above it a row higher.
    g.splice(30, 0, g[30].slice());
    g.shift();
  }
  if (o.short) {
    // Shorter: one of the two shaded neck rows taken out, everything above it a row lower.
    g.splice(24, 1);
    g.unshift(Array(N).fill(null));
  }
  // Hand-placed pixels, last of all: [x, y, colour], colour "line" for the outline or null to
  // clear. tailBase ones apply only while the tail is in one of its resting swings.
  const place = ([x, y, c]) => px(g, x, y, c === "line" ? line : c);
  (o.pixels || []).forEach(place);
  if (!o.lick && !o.pawUp) (o.leftLegPixels || []).forEach(place); // leftLegPixels: only while that front leg is down
  if (!o.alert && (o.tail || 0) <= 3) (o.tailBasePixels || []).forEach(place);
  // Pin-holes inside the outline (between the paws, at the tail's root) are filled.
  for (let pass = 0; pass < 2; pass++)
    for (let y = 1; y < N - 1; y++)
      for (let x = 1; x < N - 1; x++) {
        if (g[y][x]) continue;
        const l = g[y][x - 1], r = g[y][x + 1], u = g[y - 1][x], d = g[y + 1][x];
        if (l && r && u && d) g[y][x] = [u, l, r, d].find((c) => c !== line) || line;
        else if (l && r && d && (y >= 40) && g[y + 1][x] === line) g[y][x] = line; // a notch in the ground line
        else if (x >= 33 && y >= 30 && l === line && r === line && d) g[y][x] = line; // a notch where the tail meets the body
      }
  // A pixel left on its own (nothing above, below or beside it) is a stray: removed.
  const was = g.map((r) => r.slice());
  for (let y = 0; y < N; y++)
    for (let x = 0; x < N; x++)
      if (was[y][x] && ![[1, 0], [-1, 0], [0, 1], [0, -1]].some(([dx, dy]) => was[y + dy]?.[x + dx])) g[y][x] = null;
  if (ownTail && !o.noTail) {
    tailLayer(g, o, line);
    // Where the tail's lower outline meets the back paw's corner, the corner takes the tail's
    // shaded fur instead, so the two outlines don't run together into one block.
    if (!o.alert && (o.tail || 0) <= 3) {
      let lx = 0;
      while (lx < 23 && g[39][lx] !== line) lx++;
      const cx = (lx < 23 ? N - 1 - lx : 33) + 2;
      if (g[40][cx] === line && g[41][cx] === line && g[39][cx - 1] && g[39][cx - 1] !== line) g[40][cx] = g[39][cx - 1];
    }
  }
  // A normal-sized cat's wrapped tail, in layers: the body drawn whole (its right side the
  // mirror of its left), the part of the tail behind it showing only where the body isn't, and
  // the part lying on the ground in front of the paws drawn over everything.
  if (zLayer && !o.noTail && !o.fluffy && !o.tailWrap) {
    const body = catGrid({ ...o, tail: 0, noTail: true });
    // A tall cat's tail moved down with its body (drop rows): the layer moves with it.
    const dz = o.tall ? (o.tall === 2 ? 3 : 1) : 0;
    const zt = (x, y) => zLayer.tg[y - dz]?.[x] ?? null;
    let lx = 0;
    while (lx < 23 && body[37][lx] !== line) lx++;
    const ex = lx < 23 ? N - 1 - lx : 33; // the body's right edge
    // The front part: from the bend at the bottom, where the tail turns along the ground toward
    // the front paws. Everything before it (rising from behind the rump) stays behind the body.
    let run = 0, cut = Infinity;
    const wp = zLayer.wp;
    wp.forEach(([x, y], i) => {
      if (i) run += Math.hypot(x - wp[i - 1][0], y - wp[i - 1][1]);
      if (cut === Infinity && i && wp[i + 1] && y + dz >= 42 && wp[i + 1][0] < x) cut = run;
    });
    // A gap boxed in by the tail and the body (inside the bend, where the tail turns) is tail
    // fur, not a knot of outline: the tail is filled in there and outlined again.
    const tf = grid().map((r, y) => r.map((_, x) => { const c = zt(x, y); return c && c !== line ? c : null; }));
    const open = grid(), q = [];
    const free = (x, y) => x >= 0 && y >= 0 && x < N && y < N && !tf[y][x] && !body[y][x];
    for (let i = 0; i < N; i++) for (const [x, y] of [[i, 0], [i, N - 1], [0, i], [N - 1, i]]) if (free(x, y) && !open[y][x]) { open[y][x] = 1; q.push([x, y]); }
    while (q.length) {
      const [x, y] = q.pop();
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) if (free(x + dx, y + dy) && !open[y + dy][x + dx]) { open[y + dy][x + dx] = 1; q.push([x + dx, y + dy]); }
    }
    for (let pass = 0; pass < 4; pass++)
      for (let y = 0; y < N; y++)
        for (let x = 0; x < N; x++)
          if (free(x, y) && !open[y][x]) tf[y][x] = [[1, 0], [-1, 0], [0, 1], [0, -1]].map(([dx, dy]) => tf[y + dy]?.[x + dx]).find(Boolean) || null;
    outline(tf, line);
    const layer = grid(); // which piece shows at each pixel: 0 back tail, 1 body, 2 front tail
    for (let y = 0; y < N; y++)
      for (let x = 0; x < N; x++) {
        const t = tf[y][x];
        const front = t && zLayer.along(x, y - dz) >= cut;
        g[y][x] = t && (!body[y][x] || front) ? t : body[y][x];
        layer[y][x] = !g[y][x] ? -1 : t && front ? 2 : t && !body[y][x] ? 0 : 1;
      }
    // Where the front lies over the back, the front's own edge carries on as an outline
    // part-way across the back, from the inside of the bend (next to the body) outward, so the
    // two read as separate pieces without being cut apart.
    const fill = (x, y) => tf[y]?.[x] && tf[y][x] !== line;
    // (Which piece shows where is read from the drawing itself, not the path, so a tall cat's
    // lowered tail gets its seam where its front really starts.)
    const backPx = (x, y) => fill(x, y) && layer[y]?.[x] === 0;
    const seam = new Set();
    for (let y = 0; y < N; y++)
      for (let x = 0; x < N; x++)
        if (backPx(x, y) && [[1, 0], [-1, 0], [0, 1], [0, -1]].some(([dx, dy]) => fill(x + dx, y + dy) && layer[y + dy]?.[x + dx] === 2)) seam.add(y * N + x);
    const near = (k) => { const x = k % N, y = (k - x) / N; return [[1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [-1, 1], [1, -1], [-1, -1]].map(([dx, dy]) => [x + dx, y + dy]); };
    const bodyShown = (x, y) => body[y]?.[x] && g[y][x] === body[y][x];
    let ring = [...seam].filter((k) => near(k).some(([x, y]) => bodyShown(x, y)));
    const mark = new Set(ring);
    for (let step = 1; step < 3; step++) {
      ring = ring.flatMap((k) => near(k).map(([x, y]) => y * N + x)).filter((k) => seam.has(k) && !mark.has(k));
      ring.forEach((k) => mark.add(k));
    }
    // ...reaching right to the body: a back pixel left between it and the body joins it.
    for (const k of [...mark]) {
      const x = k % N, y = (k - x) / N;
      for (const [dx, dy] of [[1, 0], [-1, 0]]) if (backPx(x + dx, y) && [[1, 0], [-1, 0], [0, 1], [0, -1]].some(([ex, ey]) => bodyShown(x + dx + ex, y + ey))) mark.add(y * N + x + dx);
    }
    for (const k of mark) { g[Math.floor(k / N)][k % N] = line; layer[Math.floor(k / N)][k % N] = 0.5; }
    // Each piece has its own outline, so where two pieces meet their lines lie side by side.
    // Only one line is kept there: the upper piece's (or whichever is the cat's outer edge). The
    // other takes the colour of the fur it borders, as long as all that fur is its own piece's
    // (a line between two different pieces is the only thing keeping them apart, so it stays).
    const D4 = [[1, 0], [-1, 0], [0, 1], [0, -1]];
    const edge = (x, y) => D4.some(([dx, dy]) => !g[y + dy]?.[x + dx]);
    for (let pass = 0; pass < 4; pass++) {
      const was = g.map((r) => r.slice()), lw = layer.map((r) => r.slice());
      for (let y = 30; y < N; y++)
        for (let x = 1; x < N - 1; x++) {
          const lp = lw[y][x];
          if (was[y][x] !== line || lp < 0 || D4.some(([dx, dy]) => !was[y + dy]?.[x + dx])) continue;
          const fur = D4.filter(([dx, dy]) => was[y + dy][x + dx] !== line);
          if (!fur.length || fur.some(([dx, dy]) => lw[y + dy][x + dx] !== lp)) continue;
          if (fur.some(([dx, dy]) => fur.some(([ex, ey]) => ex === -dx && ey === -dy))) continue; // fur on both sides: it parts two bits (like two paws)
          const over = D4.some(([dx, dy]) => { const q = lw[y + dy]?.[x + dx]; return was[y + dy]?.[x + dx] === line && q !== lp && (q > lp || (lp === 2 && edge(x + dx, y + dy))); }); // (the body's own edge always stays)
          if (over) g[y][x] = was[y + fur[0][1]][x + fur[0][0]];
        }
    }
    // Inside the tail there is no black: a tail line with tail on every side (not on the cat's
    // outer edge, not the seam, and not touching the body, where the tail is always outlined)
    // is shading, a shade darker than the tail fur beside it.
    for (let y = 30; y < N; y++)
      for (let x = 1; x < N - 1; x++) {
        const lp = layer[y][x];
        if (g[y][x] !== line || (lp !== 0 && lp !== 2) || edge(x, y) || mark.has(y * N + x)) continue;
        if ([...D4, [1, 1], [-1, 1], [1, -1], [-1, -1]].some(([dx, dy]) => layer[y + dy]?.[x + dx] === 1)) continue;
        const tc = [...D4, [1, 1], [-1, 1], [1, -1], [-1, -1]].map(([dx, dy]) => tf[y + dy]?.[x + dx]).find((c) => c && c !== line);
        if (tc) g[y][x] = shade(tc, 0.86);
      }
    // A single pixel boxed in by outline is a stray: it takes the colour around it.
    const lines = (m, x, y) => [[1, 0], [-1, 0], [0, 1], [0, -1]].filter(([dx, dy]) => m[y + dy]?.[x + dx] === line).length;
    const was = g.map((r) => r.slice());
    for (let y = 30; y < N - 1; y++)
      for (let x = 1; x < N - 1; x++) {
        const c = was[y][x];
        if (!c || c === line || [[1, 0], [-1, 0], [0, 1], [0, -1]].some(([dx, dy]) => was[y + dy]?.[x + dx] === c)) continue; // (two or more together are a real bit, like the back paw)
        if (!(lines(was, x, y) === 4 || (c === body[y][x] && lines(was, x, y) === 3 && lines(body, x, y) < 3))) continue;
        const n = {};
        for (const [dx, dy] of [[1, 1], [-1, 1], [1, -1], [-1, -1], [2, 0], [-2, 0], [0, 2], [0, -2]]) { const v = was[y + dy]?.[x + dx]; if (v && v !== line && v !== c) n[v] = (n[v] || 0) + 1; }
        const best = Object.keys(n).sort((p, q) => n[q] - n[p])[0];
        if (best) g[y][x] = best;
      }
    // The tail is always outlined where it meets the body: tail fur touching body fur is outline.
    const added = [];
    for (let y = 30; y < N; y++)
      for (let x = 0; x < N; x++)
        if (g[y][x] && g[y][x] !== line && (layer[y][x] === 0 || layer[y][x] === 2) && D4.some(([dx, dy]) => layer[y + dy]?.[x + dx] === 1 && g[y + dy][x + dx] && g[y + dy][x + dx] !== line)) added.push([x, y]);
    for (const [x, y] of added) g[y][x] = line;
    // Where the back of the tail comes out from behind the body it is a shade darker, as when
    // the tail is up: the pixel touching the body a little, the next one out barely.
    const bodyAt = (x, y) => layer[y]?.[x] === 1;
    const dark = [];
    for (let y = 0; y < N; y++)
      for (let x = 0; x < N; x++) {
        if (layer[y][x] !== 0 || !g[y][x] || g[y][x] === line) continue;
        const k = D4.some(([dx, dy]) => bodyAt(x + dx, y + dy)) ? 0.88 : bodyAt(x - 2, y) ? 0.95 : 1;
        if (k < 1) dark.push([x, y, k]);
      }
    for (const [x, y, k] of dark) g[y][x] = shade(g[y][x], k);
  }
  // Outline clean-up low down: a background pixel boxed in by outline on three sides (a notch
  // in the left side's edge where the body meets the paw) is closed; on a tail lying down, an
  // outline pixel sticking out on its own from the tail's edge is removed.
  for (let y = 36; y <= 41; y++)
    for (let x = 1; x <= 16; x++)
      if (!g[y][x] && [[0, -1], [0, 1], [1, 0], [-1, 0]].filter(([dx, dy]) => g[y + dy][x + dx] === line).length >= 3) g[y][x] = line;
  if (!o.noTail && !o.alert && !o.tailWrap && o.tail >= 6 && o.tail <= 9)
    for (let y = 36; y < N - 1; y++)
      for (let x = 34; x < N - 1; x++)
        if (g[y][x] === line && [[0, -1], [0, 1], [1, 0], [-1, 0]].filter(([dx, dy]) => g[y + dy][x + dx]).length === 1) g[y][x] = null;
  // Hand-placed fixes for one pose, as data: { [tail]: { line: runs, under: runs } }, runs as in
  // marks. "line" pixels become outline; "under" pixels show what the cat has there without a tail;
// "fur" pixels become tail fur.
  const fix = !o.noTail && !o.alert && o.tailFix?.[o.tail || 0];
  if (fix) {
    const at = (runs, f) => {
      for (const row of String(runs || "").split(";").filter(Boolean)) {
        const [y, xs] = row.split(":");
        for (const run of xs.split(",")) {
          const [a, b] = run.split("-").map(Number);
          for (let x = a; x <= (Number.isNaN(b) || b === undefined ? a : b); x++) f(x, Number(y));
        }
      }
    };
    at(fix.line, (x, y) => (g[y][x] = line));
    // "fur" pixels take the tail's own colour from beside them.
    at(fix.fur, (x, y) => (g[y][x] = [g[y][x + 1], g[y][x - 1], g[y + 1]?.[x]].find((c) => c && c !== line) || g[y][x]));
    if (fix.under) {
      const bare = catGrid({ ...o, tail: 0, noTail: true });
      at(fix.under, (x, y) => (g[y][x] = bare[y][x]));
    }
  }
  // A tail lying wholly behind the body never draws on it: its outline over the body's fur is
  // the body's fur again (the body's own edge already separates them).
  if (!o.noTail && !o.fluffy && !o.tailWrap && !zLayer) {
    const bare = catGrid({ ...o, tail: 0, noTail: true });
    for (let y = 26; y < N; y++)
      for (let x = N / 2; x < N; x++)
        if (g[y][x] === line && bare[y][x] && bare[y][x] !== line) g[y][x] = bare[y][x];
  }
  // Where the body's right edge steps in (the ankle) and the tail fills the step, the edge runs
  // straight on down past the tail instead: the outline moves out to the tail and the old edge
  // pixel becomes the leg's fur, so it stays a single line.
  if (!o.noTail && !o.fluffy && !o.tailWrap) {
    const bare = catGrid({ ...o, tail: 0, noTail: true });
    const re = (y) => { for (let x = N - 2; x > N / 2; x--) if (bare[y][x] === line && !bare[y][x + 1]) return x; return -1; };
    for (let y = 31; y < N - 1; y++) {
      const top = re(y - 1), e = re(y);
      if (top < 0 || e < 0 || e >= top) continue;
      const c = g[y][top];
      const leg = [g[y][e - 1], g[y - 1][e]].find((v) => v && v !== line); // the leg's fur, beside or above
      if (!c || c === line || g[y][e] !== line || !leg) continue;
      g[y][top] = line;
      for (let x = e; x < top; x++) g[y][x] = leg;
      // ...and a tail line right beside the new edge would double it: that one is tail fur.
      const tf2 = [g[y][top + 2], g[y - 1][top + 1]].find((v) => v && v !== line);
      if (g[y][top + 1] === line && !bare[y][top + 1] && tf2) g[y][top + 1] = tf2;
      // The tail fur now touching the body there takes the shade the touching pixel had.
      const nx = g[y][top + 1];
      if (nx && nx !== line && !bare[y][top + 1]) g[y][top + 1] = shade(nx, zLayer ? 0.93 : 0.88);
      bare[y][top] = line; bare[y][top + 1] = null; // the next row steps from here
      for (let x = e; x < top; x++) bare[y][x] = leg;
    }
  }
  // A black pixel never stands alone: one with no black anywhere around it (corners included)
  // takes the colour most of its neighbours have.
  const pre = g.map((r) => r.slice());
  for (let y = 0; y < N; y++)
    for (let x = 0; x < N; x++) {
      if (pre[y][x] !== line) continue;
      const n = {};
      let alone = true;
      for (let dy = -1; dy <= 1; dy++)
        for (let dx = -1; dx <= 1; dx++) {
          if (!dx && !dy) continue;
          const c = pre[y + dy]?.[x + dx];
          if (c === line) alone = false;
          else n[c ?? ""] = (n[c ?? ""] || 0) + 1;
        }
      if (alone) g[y][x] = Object.keys(n).sort((a, b) => n[b] - n[a])[0] || null;
    }
  return g;
}

// --- the tail -------------------------------------------------------------------------------
// Drawn by itself on an empty grid, cleaned so its edges never jog by a single pixel, then set
// behind the finished cat (only where nothing else is). Centre lines are [x, height above the
// ground]; "R" is the root lying along the ground. Heights scale with the cat's height.
const TAIL_POSES = {
  // The root sits behind the haunch, above the back foot, so the tail comes out of the rump.
  rest: [[31, 4], [35.4, 3], [38.6, 4], [39.8, 7], [40.6, 11.4], [41, 14.5], [40.8, 16.7]],
  sway1: [[31, 4], [35.6, 3], [39, 4], [40.8, 7], [41.8, 11.4], [42.4, 14.5], [42.6, 16.7]],
  sway2: [[31, 4], [35.8, 3], [39.4, 4], [41.6, 7], [43, 11.4], [43.8, 14.5], [44, 16.7]],
  curlIn: [[31, 4], [35.4, 3], [38.6, 4], [39.8, 7], [41, 10.4], [41.4, 13], [41, 14.9], [40.1, 15.7], [39.3, 15.4]], // up, over the top, the tip hooked back in toward the body
  curlOut: [[31, 4], [35.4, 3], [38.6, 4], [39.8, 7], [40.6, 11.4], [41.2, 14.1], [42, 15.8], [43, 16.4]],
  alert: [[31, 4.5], [35.2, 3.5], [38.4, 4.5], [40, 7.9], [41, 13.2], [41.4, 16.7], [41.4, 18.9]],
};
function tailLayer(g, o, line) {
  const fur = o.fur;
  // The ground line (the lowest outlined row) and the cat's height, for scaling.
  let yG = N - 1;
  while (yG > 0 && !g[yG].some((c) => c)) yG--;
  let top = 0;
  while (top < N && !g[top].some((c) => c)) top++;
  const k = Math.max(0.9, Math.min(1.2, (yG - top) / 41));
  const pose = o.alert ? "alert" : ["rest", "sway1", "sway2", o.curlOut ? "curlOut" : "curlIn"][o.tail || 0];
  const w = o.tailW || 1.1;
  const [r0, r1] = o.fluffy ? [3.4, 1.6] : [w * 1.95, w * 1.35]; // radius in pixels, root to tip
  // A long-haired tail is fuller, so it sits a little further out and lower, clear of the ruff.
  const fl = (x, h) => (o.fluffy && h !== "R" ? [x + Math.min(2, h * 0.2), h * 0.9] : [x, h]);
  let pts = TAIL_POSES[pose].map(([x0, h0]) => { const [x, h] = fl(x0, h0); return [x, h === "R" ? yG - 0.5 - r0 : yG - 0.5 - r0 - (h * k)]; });
  const segL = pts.slice(1).map((q, i) => Math.hypot(q[0] - pts[i][0], q[1] - pts[i][1]));
  const total = segL.reduce((t, l) => t + l, 0);
  // Inside the frame, outline included.
  let run = 0;
  pts = pts.map(([x, y], i) => { if (i) run += segL[i - 1]; return [Math.min(x, N - 1.6 - (r0 + (r1 - r0) * (run / total))), y]; });
  const near = (x, y) => {
    let best = null, at = 0;
    pts.slice(1).forEach((q, i) => {
      const a = pts[i], l = segL[i] || 1;
      const t = Math.max(0, Math.min(1, ((x + 0.5 - a[0]) * (q[0] - a[0]) + (y + 0.5 - a[1]) * (q[1] - a[1])) / (l * l)));
      const dx = x + 0.5 - (a[0] + (q[0] - a[0]) * t), dy = y + 0.5 - (a[1] + (q[1] - a[1]) * t), d = Math.hypot(dx, dy);
      if (!best || d < best.d) best = { d, dx, dy, s: at + l * t };
      at += l;
    });
    best.f = best.s / total;
    best.r = r0 + (r1 - r0) * best.f;
    return best;
  };
  // The tail and its outline as one shape.
  const inS = Array.from({ length: N }, () => Array(N).fill(false));
  const fillAt = (x, y) => y < yG && near(x, y).d <= near(x, y).r;
  for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) if (fillAt(x, y)) inS[y][x] = true;
  const dil = inS.map((r) => r.slice());
  for (let y = 0; y < N; y++)
    for (let x = 0; x < N; x++)
      if (!inS[y][x] && y <= yG && [[1, 0], [-1, 0], [0, 1], [0, -1]].some(([dx, dy]) => inS[y + dy]?.[x + dx])) dil[y][x] = true;
  // No single-pixel jogs: wherever one row's (or one column's) edge sticks out or dips in by
  // itself, it's set level with its neighbours.
  for (let pass = 0; pass < 6; pass++) {
    let changed = false;
    const span = (get, len, i) => { let a = -1, b = -1, runs = 0, prev = false; for (let j = 0; j < len; j++) { const v = get(i, j); if (v && !prev) { runs++; if (a < 0) a = j; } if (v) b = j; prev = v; } return runs === 1 ? [a, b] : null; };
    const fixAxis = (get, set, len) => {
      const sp = Array.from({ length: N }, (_, i) => span(get, len, i));
      for (let i = 1; i < N - 1; i++) {
        const [p, c, n] = [sp[i - 1], sp[i], sp[i + 1]];
        if (!p || !c || !n) continue;
        for (const e of [0, 1]) {
          const a = p[e], b = c[e], d = n[e];
          if ((b > a && b > d) || (b < a && b < d)) {
            const t = b > a ? Math.max(a, d) : Math.min(a, d); // level with the nearer neighbour
            if (e === 1) for (let j = Math.min(b, t) + 1; j <= Math.max(b, t); j++) set(i, j, t > b);
            else for (let j = Math.min(b, t); j < Math.max(b, t); j++) set(i, j, t < b);
            c[e] = t; changed = true;
          }
        }
      }
    };
    fixAxis((y, x) => dil[y][x], (y, x, v) => { if (y <= yG) dil[y][x] = v; }, N); // rows
    fixAxis((x, y) => y <= yG && dil[y][x], (x, y, v) => { if (y <= yG) dil[y][x] = v; }, N); // columns
    if (!changed) break;
  }
  // Outline: the shape's own edge. The rest is fur, coloured and shaded as a round tube.
  const base = o.tailColor || (o.calico ? "#323034" : fur);
  const band = o.ringColor || (o.tabby ? o.tabbyDark || shade(fur, 0.45) : shade(fur, 0.55));
  const layer = grid(), plain = grid(), tubeK = grid(); // each pixel's colour before shading, and its tube shade
  for (let y = 0; y <= yG; y++)
    for (let x = 0; x < N; x++) {
      if (!dil[y][x]) continue;
      if (y === yG || [[1, 0], [-1, 0], [0, 1], [0, -1]].some(([dx, dy]) => !dil[y + dy]?.[x + dx])) { layer[y][x] = line; continue; }
      const t = near(x, y);
      let c = base;
      if (o.bandedTail && o.ringPeriod) c = t.f > 0.9 || (t.s > 9 && t.s % o.ringPeriod < 1) ? band : base;
      else if (o.tabby) c = t.f > 0.86 || t.s % 4 < 2 ? band : base;
      else if (o.calico && y > yG - 18 && Math.floor((y + Math.round(Math.sin(x * 1.3) * 1.5)) / 2) % 3 === 0) c = "#c9935c";
      const r = t.r + 0.5, nx = t.dx / r, ny = t.dy / r, nz = Math.sqrt(Math.max(0, 1 - nx * nx - ny * ny));
      const d = -0.6 * ny + 0.8 * nz - 0.15 * nx;
      // Near the root the tail lies almost flat, so its underside is a whole row: shaded gently there.
      const kk = d < 0.15 ? (t.f < 0.3 ? 0.9 : 0.8) : d < 0.45 ? (t.f < 0.3 ? 0.95 : 0.9) : d > 0.95 ? 1.08 : 1;
      layer[y][x] = kk === 1 ? c : shade(c, kk);
      plain[y][x] = c; tubeK[y][x] = kk;
    }
  // Behind the cat: only where the sprite is empty. Where it comes out from behind the body
  // it is a shade darker, so the two read apart.
  const cat = g.map((r) => r.map((c) => !!c));
  for (let y = 0; y <= yG; y++) for (let x = 0; x < N; x++) if (layer[y][x] && !g[y][x]) g[y][x] = layer[y][x];
  for (let y = 0; y <= yG; y++)
    for (let x = 0; x < N; x++)
      if (layer[y][x] && !cat[y][x] && layer[y][x] !== line) {
        // A soft step, not a block: the pixel touching the cat a little darker, the next one barely.
        const d1 = [[1, 0], [-1, 0], [0, 1], [0, -1]].some(([dx, dy]) => cat[y + dy]?.[x + dx]);
        // (The darker of the two shades, never one on top of the other, so the underside doesn't go muddy.)
        const k2 = d1 ? 0.88 : cat[y]?.[x - 2] ? 0.95 : 1;
        if (k2 < tubeK[y][x]) g[y][x] = shade(plain[y][x], k2);
      }
  // A one-pixel slot left between the tail and the cat is closed with outline.
  for (let y = 1; y < yG; y++)
    for (let x = 30; x < N - 1; x++)
      if (!g[y][x] && layer[y][x + 1] && g[y][x - 1]) g[y][x] = line;
}

// --- wildlife: side-on, facing left ---------------------------------------------------------
// One builder for four-legged animals; each kind sets its shape and colours. Frames: the
// tail pose (0/1) and a blink.
const LINE = "#141416";
function quad(o, f = {}) {
  const g = grid();
  const fur = o.fur, legC = o.legColor || fur, far = shade(legC, 0.7);
  const by = o.by ?? 19, bry = o.bry ?? 5.5, feet = o.feet ?? 28.5, w = o.legW ?? 0.9;
  let [ff, fn, bf, bn] = o.legs || [11, 14.5, 20, 23.5];
  // Walking: near and far legs swing opposite ways.
  if (f.walk) { const s2 = f.walk === 1 ? 0.8 : -0.8; ff += s2; bn += s2; fn -= s2; bf -= s2; }
  // The head can move (alert, grazing, sniffing): everything on it moves together.
  const hdx = (o.headDx || 0), hdy = (o.headDy || 0) + (f.alert ? -0.8 : 0);
  const mv = (pts) => pts.map(([x, y]) => [x + hdx, y + hdy]);
  // Front legs drop straight to a forward paw; back legs angle back to the hock first.
  const top = legC === fur ? by : by + bry * 0.55; // dark legs start lower, not inside the body
  const front = (x) => [[x, Math.max(top, by + 1)], [x + 0.2, feet - 0.7], [x - 0.5, feet]];
  const back = (x) => [[x - 0.4, top], [x + 0.9, top + (feet - top) * 0.55], [x + 0.3, feet - 0.7], [x - 0.4, feet]];
  const tails = o.tails || [];
  const tail = tails[f.alert ? tails.length - 1 : f.tail || 0] || tails[0];
  if (tail) thick(g, tail, o.tailW || 1, o.tailColor || fur);
  thick(g, front(ff), w * 0.9, far); // the far legs, behind the body and in shadow
  thick(g, back(bf), w * 0.9, far);
  if (o.hump) ellipse(g, ...o.hump, fur);
  ellipse(g, o.bx ?? 17, by, o.brx ?? 9, bry, fur);
  thick(g, front(fn), w, legC);
  thick(g, back(bn), w, legC);
  if (legC === fur) ellipse(g, bn + 0.4, by + 0.5, w * 2.2, bry * 0.75, fur); // haunch
  if (o.neck) thick(g, [o.neck[0], ...mv(o.neck.slice(1))], o.neckW ?? 1.4, fur);
  ellipse(g, (o.hx ?? 8) + hdx, (o.hy ?? 14) + hdy, o.hrx ?? 4.4, o.hry ?? 3.7, o.headColor || fur);
  if (o.snout) tri(g, ...mv(o.snout), o.snoutColor || o.headColor || fur);
  for (const e of o.ears || []) tri(g, ...mv(e), o.earColor || fur);
  if (o.earBlob) ellipse(g, o.earBlob[0] + hdx, o.earBlob[1] + hdy, o.earBlob[2], o.earBlob[3], o.earColor || fur);
  outline(g, LINE);
  if (o.mark) o.mark(g);
  // The belly follows the underside of the body, in the animal's own belly colour.
  const bx = o.bx ?? 17, brx = o.brx ?? 9, belly = o.belly || shade(fur, 0.84);
  const inBody = (x, y) => ((x + 0.5) / S - bx) ** 2 / brx ** 2 + ((y + 0.5) / S - by) ** 2 / bry ** 2 <= 1;
  recolor(g, (x, y, c) => c === fur && inBody(x, y) && y / S > by && (!inBody(x, y + 3) || (!inBody(x, y + 4) && (x + y) % 2 === 0)), belly);
  // Light from above: a lighter back and a darker lower flank; dithered only where they meet.
  const lit = o.lit || shade(fur, 1.12), dim = shade(fur, 0.86);
  recolor(g, (x, y, c) => {
    if (c !== fur || !inBody(x, y)) return false;
    const d = ((y + 0.5) / S - by) / bry;
    return d < -0.62 || (d < -0.5 && (x + y) % 2 === 0);
  }, lit);
  recolor(g, (x, y, c) => {
    if (c !== fur || !inBody(x, y)) return false;
    const d = ((y + 0.5) / S - by) / bry;
    return d > 0.42 || (d > 0.3 && (x + y) % 2 === 0);
  }, dim);
  if (o.dorsal) recolor(g, (x, y, c) => inBody(x, y) && !inBody(x, y - 2) && c !== LINE, o.dorsal); // a darker stripe down the back
  // Toes: the bottom pixel row of every foot a shade darker.
  if (!o.noToes) recolor(g, (x, y, c) => y > (by + bry * 0.6) * S && g[y + 1]?.[x] === LINE && !g[y + 2]?.[x], (x, y) => shade(g[y][x], 0.62));
  const [ex, ey] = o.eye, edy = Math.round(hdy * S), edx = Math.round(hdx * S);
  px(g, ex + edx, ey + edy, f.blink ? g[ey + edy - 1]?.[ex + edx] || LINE : "#111");
  if (o.nose) px(g, o.nose[0] + edx + (o.noseDx || 0), o.nose[1] + edy, o.noseColor || "#111");
  return g;
}
// A steady pseudo-random number per pixel, for speckled fur.
function hash(x, y) {
  let h = (x * 374761393 + y * 668265263) >>> 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177) >>> 0;
  return (h ^ (h >>> 16)) >>> 0;
}
// A soft-edged patch of colour (fine-grid centre and radii) over the given colours only.
function patch(g, cx, cy, rx, ry, c, over) {
  recolor(g, (x, y, cur) => {
    if (!over.includes(cur)) return false;
    const d = ((x + 0.5 - cx) / rx) ** 2 + ((y + 0.5 - cy) / ry) ** 2;
    return d <= 0.65 || (d <= 1 && (x + y) % 2 === 0);
  }, c);
}
function recolor(g, test, c) {
  for (let y = 0; y < N; y++)
    for (let x = 0; x < N; x++) if (g[y][x] && g[y][x] !== LINE && test(x, y, g[y][x])) g[y][x] = typeof c === "function" ? c(x, y) : c;
}
// Idle for wildlife: breathing, the tail (or a bird's head) moves, a blink, then the
// animal's own move (ACTS) now and then.
function idle(make, name) {
  return animated(idleWildFrames(make, name));
}
function idleWildFrames(make, name) {
  const rest = make({ tail: 0 });
  const acts = (ACTS[name] || []).map(([f, d]) => [make({ tail: 0, ...f }), d]);
  return [
    [rest, jitter(1.4)],
    [breathe(rest, 36), 0.7],
    [make({ tail: 1 }), jitter(0.9)],
    [rest, jitter(1.2)],
    [make({ tail: 0, blink: true }), 0.18],
    [breathe(rest, 36), 0.7],
    [rest, jitter(0.8)],
    ...acts,
    [rest, jitter(1.2)],
    [breathe(rest, 36), 0.7],
  ];
}
// Walking in: legs swinging (four-legged animals) or hopping (the rest).
function walker(make) {
  return animated(walkerFrames(make));
}
function walkerFrames(make) {
  const a = make({ walk: 1 }), b = make({ walk: 2 });
  if (JSON.stringify(a) === JSON.stringify(b)) return [[make({}), 0.25], [lift(make({}), 2), 0.18]];
  return [[a, 0.18], [make({}), 0.12], [b, 0.18], [make({}), 0.12]];
}
// On alert: head up, tail up, holding still apart from a blink.
function alertWild(make) {
  return animated(alertWildFrames(make));
}
function alertWildFrames(make) {
  const a = make({ alert: true });
  return [[a, jitter(2.4)], [make({ alert: true, blink: true }), 0.18], [a, 1.2]];
}
const ACTS = {
  Raccoon: [[{ act: 1 }, 0.22], [{ act: 2 }, 0.22], [{ act: 1 }, 0.22], [{ act: 2 }, 0.22], [{ act: 1 }, 0.22]], // rubbing its paws
  Opossum: [[{ act: 1 }, 0.2], [{ act: 2 }, 0.2], [{ act: 1 }, 0.2], [{ act: 2 }, 0.2]], // sniffing
  Skunk: [[{ act: 1 }, 1.4]], // tail raised
  Rat: [[{ act: 1 }, 0.12], [{}, 0.12], [{ act: 1 }, 0.12], [{}, 0.12], [{ act: 1 }, 0.12]], // nose twitching
  Fox: [[{ act: 1 }, 0.2], [{}, 0.25], [{ act: 1 }, 0.2]], // ear flick
  Coyote: [[{ act: 1 }, 0.2], [{}, 0.25], [{ act: 1 }, 0.2]],
  Bear: [[{ act: 1 }, 0.6], [{ act: 2 }, 0.6]], // head swing
  Deer: [[{ act: 1 }, 0.2], [{}, 0.5], [{ act: 1 }, 0.2], [{ tail: 1 }, 0.3], [{}, 0.3], [{ tail: 1 }, 0.3]], // an ear flick, then the tail
  Rabbit: [[{ act: 1 }, 0.12], [{}, 0.12], [{ act: 1 }, 0.12], [{}, 0.12]], // nose twitch
  Groundhog: [[{ act: 1 }, 0.4], [{ act: 2 }, 1], [{ act: 1 }, 0.4]], // up to look about
  Squirrel: [[{ act: 1 }, 0.25], [{ act: 2 }, 0.25], [{ act: 1 }, 0.25], [{ act: 2 }, 0.25], [{ tail: 2 }, 0.15], [{}, 0.15], [{ tail: 2 }, 0.15]], // nibbling, tail flick
  Chipmunk: [[{ tail: 1 }, 0.12], [{}, 0.12], [{ tail: 1 }, 0.12], [{}, 0.12]], // tail flick
  Stoat: [[{ act: 1 }, 1.2]], // up on its haunches to look
  Dog: [[{ tail: 1 }, 0.14], [{}, 0.14], [{ tail: 1 }, 0.14], [{}, 0.14], [{ tail: 1 }, 0.14], [{}, 0.14]], // wagging
  Chicken: [[{ act: 1 }, 0.25], [{}, 0.2], [{ act: 1 }, 0.25], [{}, 0.2]], // pecking
  Bird: [[{ act: 2 }, 0.2], [{}, 0.2], [{ act: 1 }, 0.15], [{}, 0.2], [{ act: 2 }, 0.2]], // hop and peck
};

const WILD = {
  Raccoon: (f) => quad({
    fur: "#8d8a86", belly: "#b4b0a8", by: 19.5, brx: 9.5, bry: 7, legs: f.act === 1 ? [12.3, 13.8, 20, 23] : f.act === 2 ? [10.9, 15.2, 20, 23] : [11.5, 14.5, 20, 23], legW: 1.2, feet: 28,
    hx: 8.5, hy: 15, hrx: 5, hry: 4.2, snout: [[4, 14.5], [1, 17.5], [5, 18]],
    ears: [[[4.5, 13], [5, 8.5], [8, 11]], [[9.5, 11], [12, 8.5], [12.5, 12.5]]],
    tails: [[[24.5, 18], [27.6, 19.6], [28.6, 23.6]], [[24.5, 18], [27.9, 19], [29, 22.8]]], tailW: 2.4,
    eye: [9, 21], nose: [2, 26],
    mark(g) {
      recolor(g, (x, y) => y >= 20 && y <= 22 && x >= 5 && x <= 17, "#2e2c2a"); // the mask
      recolor(g, (x, y) => y >= 24 && y <= 27 && x >= 3 && x <= 10, "#d7d2c8"); // muzzle
      recolor(g, (x, y, c) => x >= 36 && c === "#8d8a86" && !(((x + 0.5) / S - 17) ** 2 / 90 + ((y + 0.5) / S - 19.5) ** 2 / 49 <= 1), (x, y) => (Math.floor((x + y) / 2.5) % 2 === 1 || y >= 38 ? "#2e2c2a" : "#6b6864")); // ringed tail, dark tip
      px(g, 11, 21, "#e8e3d8");
      recolor(g, (x, y, c) => c === "#8d8a86" && y >= 38 && x <= 37, "#2e2c2a"); // dark paws
    },
  }, f),
  Opossum: (f) => quad({
    fur: "#a9a6a1", belly: "#d6d1c8", by: 20, brx: 9, bry: 5, legs: [11.5, 14.5, 20.5, 23.5], legW: 1, feet: 27.3, legColor: "#26242a",
    headColor: "#efebe4", hx: 7.5, hy: 17, hrx: 4.3, hry: 3.6,
    snout: [[4, 15.5], f.act === 1 ? [0, 18.5] : f.act === 2 ? [0.3, 19.9] : [0, 19.2], [4.5, 19.8]],
    ears: [[[8, 14], [8.6, 10.8], [11.2, 13.4]]], earColor: "#26242a",
    tails: [[[25.5, 21], [28.5, 24.4], [30.8, 25.2], [31.4, 23]], [[25.5, 21], [28.6, 24.1], [31, 24.4], [31.5, 22.1]]], tailW: 0.8, tailColor: "#e7b1b1",
    eye: [9, 24], nose: [1, f.act === 1 ? 27 : f.act === 2 ? 29 : 28], noseColor: "#e48a95",
    mark(g) {
      recolor(g, (x, y, c) => c === "#a9a6a1" && y <= 33 && hash(x, y) % 6 === 0, "#f2efe8"); // white guard hairs
      recolor(g, (x, y, c) => c === "#a9a6a1" && hash(x, y) % 6 === 1, "#8f8c87"); // dark underfur
      recolor(g, (x, y, c) => c === "#e7b1b1" && x <= 41, "#26242a"); // the tail's dark base
      recolor(g, (x, y, c) => c === "#26242a" && y >= 41 && x >= 12, "#e7b1b1"); // pink toes
      for (const [x, y] of [[8, 23], [10, 23], [8, 24], [10, 24]]) px(g, x, y, "#4a4648"); // dark around the eye
    },
  }, f),
  Fox: (f) => quad({
    fur: "#d07a32", belly: "#ece2d2", by: 18, brx: 9, bry: 4.1, hump: [11.5, 18.4, 3.6, 4.2], hx: 7.5, hy: 13.5, hrx: 4.2, hry: 3.4, legColor: "#2b2422", legW: 0.75,
    snout: [[4, 13], [0.5, 15.5], [4.5, 16.5]],
    ears: [[[5, 11.5], [5.5, 6.5], [8.5, 10.5]], f.act === 1 ? [[8.5, 10.5], [11.8, 7.4], [11, 11.5]] : [[8.5, 10.5], [10.5, 6.5], [11, 11.5]]],
    tails: [[[25, 18], [28.5, 20], [30.5, 23]], [[25, 18], [29, 18.5], [31, 20.5]]], tailW: 2,
    eye: [9, 19], nose: [1, 23],
    mark(g) {
      recolor(g, (x, y) => y >= 22 && y <= 25 && x <= 10, "#f4ece0"); // muzzle
      patch(g, 15, 27, 4, 5.5, "#f4ece0", ["#d07a32"]); // white throat and chest
      recolor(g, (x) => x >= 43, "#f4ece0"); // tail tip
    },
  }, f),
  Coyote: (f) => quad({
    fur: "#a08a6c", belly: "#d3c4a8", by: 18, brx: 9, bry: 4.3, hump: [11.5, 18.4, 3.8, 4.4], hx: 7.5, hy: 13.5, hrx: 4.3, hry: 3.5, legW: 0.85,
    snout: [[4, 13], [0.5, 15.5], [4.5, 16.5]],
    ears: [[[5, 11.5], [5.5, 6.5], [8.5, 10.5]], f.act === 1 ? [[8.5, 10.5], [11.8, 7.4], [11, 11.5]] : [[8.5, 10.5], [10.5, 6.5], [11, 11.5]]],
    tails: [[[25, 18], [28, 22], [29.5, 26]], [[25, 18], [28.5, 21.5], [30.5, 25]]], tailW: 1.5,
    eye: [9, 19], nose: [1, 23],
    mark(g) {
      patch(g, 15, 27, 4, 5, "#d8ccb6", ["#a08a6c"]); // pale throat
      recolor(g, (x, y, c) => c === "#a08a6c" && y <= 22 && x >= 14 && x <= 34 && hash(x, y) % 3 === 0, "#6f5f4b"); // grizzled saddle
      recolor(g, (x, y) => x >= 42 && y >= 36, "#3a2f26"); // dark tail tip
    },
  }, f),
  Skunk: (f) => quad({ ...SKUNK }, { ...f, tail: f.act ? 2 : f.tail }),
};
const SKUNK = {
    fur: "#1f1f22", belly: "#1f1f22", bx: 16, by: 21.5, brx: 8.2, bry: 5, legs: [10.5, 12.8, 19.5, 21.8], legW: 0.8, feet: 27.6,
    hx: 7.2, hy: 20, hrx: 3.3, hry: 2.8, snout: [[4.8, 18.8], [1.6, 21.6], [4.8, 22.4]],
    ears: [[[7.6, 17.6], [8.4, 16.2], [9.6, 17.6]]],
    tails: [[[23, 19], [27, 15.5], [27.5, 9.5], [24.5, 6.5]], [[23, 19], [27.4, 15.8], [28.2, 10], [25.6, 6.8]], [[23, 19], [26, 13.5], [26.2, 7.5], [24.8, 3.8]]], tailW: 3,
    eye: [9, 29], nose: [2, 32],
    mark(g) {
      const inB = (x, y) => ((x + 0.5) / S - 16) ** 2 / 67 + ((y + 0.5) / S - 21.5) ** 2 / 25 <= 1;
      recolor(g, (x, y, c) => c === "#1f1f22" && inB(x, y) && !inB(x, y - 3) && x >= 13, "#f2f0ea"); // stripes down the back
      const inHead = (x, y) => ((x + 0.5) / S - 7.2) ** 2 / 10.9 + ((y + 0.5) / S - 20) ** 2 / 7.84 <= 1;
      recolor(g, (x, y, c) => c === "#1f1f22" && x >= 8 && x <= 14 && inHead(x, y) && !inHead(x, y - 2), "#f2f0ea"); // the stripe over the crown, running on into the back
      const deep = (x, y) => [[2, 0], [-2, 0], [0, 2], [0, -2], [1, 1], [-1, -1], [1, -1], [-1, 1]].every(([dx, dy]) => g[y + dy]?.[x + dx] && g[y + dy][x + dx] !== LINE);
      const core = [];
      for (let y = 0; y < 27; y++) for (let x = 33; x < N; x++) if (g[y][x] === "#1f1f22" && !inB(x, y) && deep(x, y)) core.push([x, y]);
      for (const [x, y] of core) g[y][x] = "#f2f0ea"; // white through the plume, a black rim around it
    },
};
Object.assign(WILD, {
  Groundhog: (f) => GROUNDHOG(f),
  Squirrel: (f) => SQUIRREL(f),
});
function GROUNDHOG(f) {
  // Beaver-like: near-black brown frosted with grey-blue tips, a big round hunched body the
  // head runs smoothly into, and a long, broad dark tail. Only the feet are darker.
  const hy = f.act === 1 ? -0.6 : f.act === 2 ? -1.4 : 0, dy = Math.round(hy * S);
  const fur = "#3a2f29", frost = "#544e52", dark = "#1d1815", muzzle = "#5e544f";
  return quad({
    headDy: hy,
    fur, belly: "#43372f", bx: 15.2, brx: 9.4, by: 21.6, bry: 6.4, legs: [9.4, 12.4, 18.6, 21.6], legW: 1.05,
    neck: [[12, 20.6], [8.4, 20.2]], neckW: 3.2,
    hx: 6.4, hy: 20.4, hrx: 4.4, hry: 3.6, earBlob: [8.2, 17.2, 1, 0.9],
    snout: [[3, 19.6], [0.8, 21.8], [3.6, 22.8]],
    tails: [[[22.6, 23.2], [26.6, 24.2], [30, 25]], [[22.6, 23.2], [26.6, 23.4], [29.8, 22.4]]], tailW: 1.7, tailColor: "#241d19",
    eye: [7, 29], nose: [2, 32],
    mark(g) {
      recolor(g, (x, y) => y >= 30 + dy && y <= 33 + dy && x >= 2 && x <= 7, muzzle); // a greyer muzzle
      recolor(g, (x, y, c) => y >= 41 && x <= 36 && c !== muzzle, dark); // darker feet
      recolor(g, (x, y, c) => c === fur && hash(x, y) % 4 === 0 && y < 38, frost); // grizzled: pale tips through the coat
    },
  }, f);
}
function SQUIRREL(f) {
  return quad({
    headDy: f.act === 1 ? 0.4 : 0, noseDx: f.act === 1 ? 1 : 0,
    fur: "#8a5a36", by: 22, brx: 6, bry: 4.5, legs: [13, 15.5, 19.5, 21.5], legW: 0.6,
    hx: 10.5, hy: 18.5, hrx: 3, hry: 2.6, ears: [[[10.5, 16.5], [11, 14.5], [12.5, 16.5]]],
    snout: [[8, 18], [6.5, 19.5], [8.5, 20.5]],
    tails: [[[23, 22], [27, 18], [27.5, 12], [25, 9]], [[23, 22], [27.5, 18.5], [28.5, 12.5], [26.5, 9.5]], [[23, 22], [26.4, 17.5], [26, 11.5], [23.5, 9]]], tailW: 2,
    eye: [14, 27], nose: [10, 29],
    belly: "#d9a46a",
  }, f);
}
Object.assign(WILD, {
  Chipmunk: (f) => quad({
    fur: "#a2723f", belly: "#efe2c8", by: 23.5, brx: 5, bry: 3.6, legs: [14, 16, 19.5, 21], legW: 0.55,
    hx: 11.5, hy: 21, hrx: 2.7, hry: 2.3, ears: [[[11.5, 19.2], [12, 17.6], [13.2, 19.2]]],
    snout: [[9.2, 20.5], [8, 22], [9.5, 22.8]],
    tails: [[[21.5, 23], [24, 19], [24, 15]], [[21.5, 23], [24.5, 19.5], [25, 15.5]]], tailW: 0.9,
    eye: [16, 31], nose: [12, 33],
    mark(g) {
      recolor(g, (x, y) => (y === 31 || y === 33) && x >= 20 && x <= 31, "#2c2018"); // back stripes
      recolor(g, (x, y) => y === 32 && x >= 20 && x <= 31, "#efe2c8");
    },
  }, f),
  Dog: (f) => quad({
    fur: "#9a6a3e", belly: "#c39a6c", by: 18.5, bry: 4.2, brx: 8.6, hx: 8, hy: 12.5, hrx: 4.3, hry: 3.8, legW: 1, neck: [[11, 15], [9, 13]],
    snout: [[4.5, 12], [1, 14.5], [4.5, 16.2]],
    earBlob: [10.8, 13.4, 1.2, 2.5], earColor: "#5f3f22",
    tails: [[[25, 16], [28, 12.5], [29, 10]], [[25, 16], [28.8, 13.5], [30.5, 11.5]]], tailW: 0.9,
    eye: [10, 18], nose: [1, 21],
  }, f),
});

// The stoat: long and low, brown above and cream below, a black-tipped tail.
WILD.Stoat = (f) => quad({
  headDy: f.act ? -3 : 0,
  fur: "#8a5b34", belly: "#efe2c4", bx: 17, by: 22.5, brx: 8, bry: 2.8, legs: [11, 12.8, 21, 22.8], legW: 0.6, feet: 27,
  neck: [[10.5, 22], [8.5, 20]], neckW: 1.8, hx: 7.5, hy: 19.2, hrx: 2.8, hry: 2.2,
  snout: [[5.5, 18.5], [3.5, 20], [5.5, 21]],
  ears: [[[8, 17.4], [8.5, 16], [9.8, 17.2]]],
  tails: [[[24.5, 22], [28, 21], [30.5, 19]], [[24.5, 22], [28.2, 21.6], [31, 20.6]]], tailW: 0.8,
  eye: [10, 28], nose: [5, 30],
  mark(g) {
    patch(g, 10, f.act ? 27.5 : 32, 4, 3, "#efe2c4", ["#8a5b34"]); // cream throat and chin
    recolor(g, (x, y, c) => c === "#8a5b34" && x >= 43, "#1d1a18"); // black tail tip
  },
}, f);
WILD.Rat = (f) => quad({
  fur: "#6e6359", belly: "#a99f94", by: 23, brx: 6, bry: 3.8, legs: [12.5, 14.5, 19.5, 21.5], legW: 0.55,
  hx: 9.5, hy: 21.5, hrx: 3, hry: 2.4, snout: [[7.5, 20.5], [5, 22.5], [7.8, 23.4]],
  ears: [[[10, 19.8], [10.5, 17.6], [12.3, 19.6]]], earColor: "#d9a0a0",
  tails: [[[22, 23.5], [26, 25.5], [30, 24.5]], [[22, 23.5], [26, 25], [29.6, 23]]], tailW: 0.4, tailColor: "#d9a0a0",
  eye: [13, 31], nose: [8, 34], noseColor: "#e48a95", noseDx: f.act ? -1 : 0,
}, f);
// The bear, drawn whole (side-on, facing left): a shoulder hump, the head carried low in front
// of it with round ears and a long tan muzzle, heavy legs, a stub of a tail.
WILD.Bear = (f) => {
  const g = grid();
  const fur = "#2b2421", far = "#1a1513", lit = "#463c37", muzzle = "#9a7652";
  const hy = f.act ? 0.8 : 0, hx = f.act === 2 ? -0.4 : 0; // its own move: the head swings low
  const sw = f.walk ? (f.walk === 1 ? 0.8 : -0.8) : 0;
  thick(g, [[14 - sw, 21], [14.3 - sw, 27.6]], 1.5, far); // far front leg
  thick(g, [[22 + sw, 21], [22.4 + sw, 27.6]], 1.5, far); // far hind leg
  thick(g, [[30, 15.6], [31, 15.2 - (f.tail ? 0.6 : 0)]], 0.7, fur); // the tail
  ellipse(g, 19.5, 18.5, 10, 5.6, fur); // body
  ellipse(g, 13.5, 15.6, 5.6, 4.6, fur); // shoulder hump
  ellipse(g, 25.5, 17.6, 5, 5, fur); // rump
  thick(g, [[11 + sw, 20], [11.2 + sw, 27.6]], 1.9, fur); // near front leg
  thick(g, [[25 - sw, 20], [25.6 - sw, 24.5], [25.2 - sw, 27.6]], 2.1, fur); // near hind leg
  thick(g, [[10, 16.4 + hy / 2], [7.5 + hx, 17.4 + hy]], 2.4, fur); // neck
  ellipse(g, 6.4 + hx, 17.4 + hy, 3.4, 3, fur); // head
  ellipse(g, 6.2 + hx, 14.5 + hy, 1.1, 1.1, far); // far ear
  ellipse(g, 8.2 + hx, 14.6 + hy, 1.2, 1.2, fur); // near ear
  ellipse(g, 2.8 + hx, 18.6 + hy, 2.3, 1.4, muzzle); // the long muzzle
  outline(g, LINE);
  // Light along the top of the back, the far legs set back in shadow.
  const edge = (x, y) => !g[y]?.[x] || g[y][x] === LINE;
  recolor(g, (x, y, c) => c === fur && y < 30 && (edge(x, y - 1) || (edge(x, y - 2) && (x + y) % 2 === 0)), lit); // the top of the head, ears, hump and back
  const ey = Math.round((16.6 + hy) * S), ex = Math.round((5.6 + hx) * S);
  px(g, ex, ey, f.blink ? fur : "#6b4a30"); // a small brown eye
  px(g, Math.round((0.8 + hx) * S), Math.round((18.2 + hy) * S), "#111"); // the nose, at the muzzle's tip
  recolor(g, (x, y, c) => y >= 40 && c !== LINE && c !== muzzle, (x, y) => shade(g[y][x], 0.7)); // dark paws
  return g;
};
// The rabbit sits up; its ear twitches instead of a tail.
WILD.Rabbit = (f) => {
  const g = grid();
  const fur = "#9b8063", pale = "#e3d6c2", ear = "#8a6f54";
  ellipse(g, 18.5, 22, 6, 4.4, fur); // body
  ellipse(g, 21.5, 22.6, 4.2, 4, fur); // haunch
  thick(g, [[16, 26.6], [22.6, 26.6]], 0.8, fur); // the long back foot
  thick(g, [[12.8, 22], [12.6, 26.4]], 0.6, fur); // front leg
  thick(g, [[13.5, 19], [12, 17]], 1.6, fur); // neck
  ellipse(g, 11.2, 16.8, 3.1, 2.6, fur); // head
  tri(g, [9, 15.8], [7.4, 17.6], [9.4, 18.6], fur); // muzzle
  thick(g, [[12.6, 14.6], [14.6, 7.6]], 0.75, ear); // far ear
  thick(g, f.tail ? [[11.6, 14.6], [11.9, 7.4]] : [[11.6, 14.6], [12.6, 7.6]], 0.8, fur); // near ear
  ellipse(g, 25.2, 21, 1.4, 1.4, "#f2efe8"); // cotton tail
  outline(g, LINE);
  const inB = (x, y) => ((x + 0.5) / S - 18.5) ** 2 / 36 + ((y + 0.5) / S - 22) ** 2 / 19.36 <= 1;
  recolor(g, (x, y, c) => c === fur && inB(x, y) && (!inB(x, y - 2) || (!inB(x, y - 3) && (x + y) % 2 === 0)), shade(fur, 1.12)); // lit back
  recolor(g, (x, y, c) => c === fur && inB(x, y) && y / S > 22 && (!inB(x, y + 2) || (!inB(x, y + 3) && (x + y) % 2 === 0)), pale); // pale belly
  recolor(g, (x, y, c) => c === fur && x >= 11 && x <= 14 && y >= 25 && y <= 27, pale); // pale chin
  const [e0, e1] = f.tail ? [[17.4, 21.9], [17.85, 11.1]] : [[17.4, 21.9], [18.9, 11.4]];
  for (let y = 13; y <= 20; y++) px(g, Math.round(e0[0] + ((e0[1] - y) / (e0[1] - e1[1])) * (e1[0] - e0[0])), y, "#d9a0a0"); // inside the near ear
  px(g, 14, 24, f.blink ? fur : "#111"); px(g, 13, 24, f.blink ? fur : "#2b2018"); // eye
  px(g, 11, f.act ? 25 : 26, "#e48a95"); // nose
  return g;
};

// The deer stands taller: long legs, a neck and a white tail that flicks.
WILD.Deer = (f) => quad({
  dorsal: "#8a6242",
  fur: "#a77a52", belly: "#eadfcf", bx: 17.5, by: 14.5, brx: 9, bry: 4.6, legs: [10.5, 13.5, 21, 24], legW: 0.6, feet: 29,
  neck: [[10.5, 13], [8, 8]], neckW: 1.5, hx: 7, hy: 6.5, hrx: 3, hry: 2.2,
  snout: [[5, 5.4], [2.8, 7.4], [5, 8.2]],
  ears: [f.act === 1 ? [[8.5, 4.8], [12, 3.4], [10.5, 5.6]] : [[8.5, 4.8], [11, 2], [10.5, 5.6]]], // act 1: the ear flicks back
  tails: [[[26, 12.4], [26.6, 13]], [[26, 11.6], [26.6, 10.8]]], tailW: 1.2, tailColor: "#eadfcf",
  eye: [9, 9], nose: [4, 11],
  mark(g) {
    patch(g, 13, 17, 2.5, 4, "#eadfcf", ["#a77a52"]); // white throat
    recolor(g, (x, y, c) => y >= 42 && c !== LINE, "#2b2422"); // hooves
  },
}, f);

// Birds: a small songbird and a hen; the head bobs.
// A robin: grey-brown back, dark head, orange breast, white eye-ring; the head bobs.
WILD.Bird = (f) => {
  const g = grid();
  const back = "#5f554d", head = "#2c2724", breast = "#d1652c";
  const dy = f.act === 2 ? 2.4 : f.tail ? 0.6 : 0;
  ellipse(g, 17, 22, 5.2, 3.6, back);
  thick(g, [[21, 21.5], [25.5, 23.5]], 1, back);
  ellipse(g, 12.5, 18.8 + dy, 2.7, 2.4, head);
  tri(g, [10, 18.4 + dy], [7.6, 19.4 + dy], [10, 20.2 + dy], "#e2b04a");
  outline(g, LINE);
  patch(g, 21.5, 35, 6.5, 4, breast, [back]); // the orange breast
  recolor(g, (x, y, c) => c === back && x >= 31 && y >= 36, "#e9e4dc"); // white under the tail
  recolor(g, (x, y, c) => c === back && g[y - 2]?.[x] === LINE, shade(back, 1.18)); // lit back
  for (const lx of [16, 18.5]) {
    thick(g, [[lx, 25.5], [lx, 28.4]], 0.2, "#c9873e");
    const fx = Math.round(lx * S), fy = Math.round(28.8 * S);
    for (let x = fx - 2; x <= fx + 1; x++) px(g, x, fy, "#c9873e"); // toes
  }
  const ey = Math.round((18.8 + dy) * S) - 1;
  px(g, 18, ey, f.blink ? head : "#111");
  px(g, 18, ey - 1, "#f2efe8"); px(g, 18, ey + 1, "#f2efe8"); // white eye-ring
  return f.act === 1 ? lift(g, 3) : g;
};
WILD.Chicken = (f) => {
  const g = grid();
  const c = "#f1ece3";
  const dy = f.act ? 5 : f.tail ? 1 : 0, dx = f.act ? -2 : 0;
  ellipse(g, 17, 20, 7, 5.5, c);
  thick(g, [[23, 18], [26, 14]], 1.6, c);
  thick(g, [[12, 18], [11 + dx, 14 + dy]], 1.5, c);
  ellipse(g, 10.5 + dx, 12.5 + dy, 2.6, 2.4, c);
  tri(g, [8.2 + dx, 12 + dy], [6 + dx, 13 + dy], [8.2 + dx, 13.8 + dy], "#e2b04a");
  ellipse(g, 10.5 + dx, 9.8 + dy, 1.8, 1, "#d23b2f");
  ellipse(g, 8.6 + dx, 14.6 + dy, 0.7, 0.9, "#d23b2f");
  outline(g, LINE);
  // The wing: a folded oval tipped toward the tail, edged and feathered in grey.
  const w = grid();
  ellipse(w, 18, 19.8, 4.6, 3, 1);
  tri(w, [20, 17.6], [25.5, 21], [19.5, 22.6], 1);
  const edge = "#a9a091", fill = c;
  for (let y = 0; y < N; y++)
    for (let x = 0; x < N; x++) {
      if (!w[y][x] || !g[y][x] || g[y][x] === LINE) continue;
      const rim = [[1, 0], [-1, 0], [0, 1], [0, -1]].some(([dx, dy2]) => !w[y + dy2]?.[x + dx]);
      g[y][x] = rim || (x >= 30 && x % 3 === 0 && y >= 28) ? edge : fill;
    }
  const inB = (x, y) => ((x + 0.5) / S - 17) ** 2 / 49 + ((y + 0.5) / S - 20) ** 2 / 30.25 <= 1;
  recolor(g, (x, y, k) => k === c && inB(x, y) && y / S > 20 && (!inB(x, y + 3) || (!inB(x, y + 4) && (x + y) % 2 === 0)), "#ddd6ca"); // soft grey underside
  // Legs and feet: three toes forward, one back.
  for (const lx of [15, 19]) {
    thick(g, [[lx, 25.5], [lx, 28.8]], 0.3, "#e2b04a");
    const fx = Math.round(lx * S), fy = Math.round(29 * S);
    for (let x = fx - 3; x <= fx + 1; x++) px(g, x, fy, "#e2b04a");
    px(g, fx - 3, fy - 1, "#e2b04a");
  }
  px(g, 16 + Math.round(dx * S), Math.round((12.5 + dy) * S) - 1, f.blink ? c : "#111");
  return g;
};

// Whether a colour sits in the middle tones, where a lightening shows most.
function midTone(hex) {
  const n = parseInt(hex.slice(1), 16);
  const l = (0.299 * (n >> 16) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255)) / 255;
  return l > 0.2 && l < 0.8;
}
// A colour made lighter (k > 1) or darker (k < 1).
function shade(hex, k) {
  const n = parseInt(hex.slice(1), 16);
  const ch = (v) => Math.max(0, Math.min(255, Math.round(v * k))).toString(16).padStart(2, "0");
  return "#" + ch(n >> 16) + ch((n >> 8) & 255) + ch(n & 255);
}

// --- Pixel sprites ------------------------------------------------------------------------------
// Animated 48x48 sprites for animals: a cat drawn from its Appearance setting (coat, patterns,
// extras), or a wildlife kind by name. Shown on a canvas, frame by frame; motion follows the
// device's reduced-motion setting (the first frame holds still).
// Ready-made looks, picked by name on an animal's page (its Sprite setting).
const SPRITE_PRESETS = {
  calico_collar: { detail: true, small: true, fur: "#f1ece3", calico: true, collar: "#3f9a55", bell: true, ears: "#c9935c", rightEar: "#323034", eyes: "#b5b648", mouth: "#7a6a62" },
  grey_chest_spot: { detail: true, small: true, fur: "#7f7e7b", chestPatch: true, bandedTail: true, rings: 10, ringPeriod: 3, ringColor: "#6e6d6a" },
  longhair_tuxedo: { detail: true, fur: "#1d1d20", tuxedo: true, fluffy: true, whiteBackPaws: true, marks: [["#0e0e10", "39:29-30;40:29-30"], ["#f1ece3", "33:28-30;34:27-30;35:26-30;36:26-30;37:26-30;38:26-30;39:26-30;40:26-30;41:26-30;42:26-30;43:26-30"]], tailTaper: [3, 1.1], outline: "#000", mouth: "#888", tailFix: { 6: { line: "39:37;40:38" }, 7: { line: "39:37", under: "40:30" }, 8: { under: "40:29-30", fur: "41:29" }, 9: { line: "39:32-33,37;40:31", under: "38:30-33;39:30-31" } } },
  brown_tabby: { detail: true, small: true, fur: "#3b281e", innerEar: "#6a4f3d", tabby: true, classicTabby: true, tabbyBold: true, tabbyDark: "#1e1410", mouth: "#1a1210", pale: "#6a4f3d", spottedBelly: true, plainBelly: true, eyeliner: "#6a4f3d", rimTop: true },
  charcoal_white_chest: { detail: true, grow: true, tailW: 1.5, fur: "#5a5856", whiteBackPaws: true, whiteBackLegs: true, whiteChest: true },
  black: { detail: true, small: true, slim: true, fur: "#1d1d20", outline: "#000", mouth: "#888" },
  smoky_banded_tail: { detail: true, slim: true, shoulderLean: false, pixels: [[30, 26, "#69615a"], [30, 28, "#3a3532"], [31, 28, "#69615a"], [31, 30, "#3a3532"], [14, 4, "#a89b90"], [14, 5, "#a89b90"], [14, 6, "#a89b90"], [14, 7, "#a89b90"], [14, 8, "#a89b90"], [14, 9, "#a89b90"], [14, 10, "#a89b90"], [33, 4, "#a89b90"], [33, 5, "#a89b90"], [33, 6, "#a89b90"], [33, 7, "#a89b90"], [33, 8, "#a89b90"], [33, 9, "#a89b90"], [33, 10, "#a89b90"], [20, 11, "#a89b90"], [27, 11, "#a89b90"], [14, 13, "#a89b90"], [15, 13, "#a89b90"], [32, 13, "#a89b90"], [33, 13, "#a89b90"], [22, 22, "#a29a92"], [23, 22, "#b0a8a0"], [24, 22, "#b0a8a0"], [25, 22, "#a29a92"], [18, 22, "#534c46"], [19, 22, "#534c46"], [20, 22, "#534c46"], [27, 22, "#534c46"], [28, 22, "#534c46"], [29, 22, "#534c46"], [21, 22, "#7f7770"], [26, 22, "#7f7770"]], leftLegPixels: [[17, 26, "#69615a"], [17, 28, "#3a3532"], [16, 28, "#69615a"], [16, 30, "#3a3532"]], softSpots: true, bellyPale: "#bbb3aa", tearMarks: true, mouth: "#8d7f78", legBars: [31, 34, 37], tall: 2, bellyDrop: 3, shoulders: true, contrast: true, tailW: 1.5, fur: "#69615a", pale: "#e8e3dc", faintTabby: true, bandedTail: true, rings: 10, ringPeriod: 3, ringColor: "#49433c", spottedBelly: true, eyeliner: "#ddd7cf", paleMuzzle: true, nose: "#c49088", innerEar: "#c49088", eyes: "#8a9a4e", eyeLow: "#b0a548", lidLine: "#3d3833", marks: [["#5a534c", "13:23-24;14:23-24;15:23-24;16:23-24"], ["#45403b", "11:20,27"], ["#5a534c", "0:0-47;1:0-47;2:0-47;3:0-47;4:0-47;5:0-47;6:0-47;7:0-47;8:0-47;9:0-47;10:0-17,30-47;11:0-15,32-47;12:0-13,34-47;13:0-13,34-47;14:0-13,34-47"], ["#e8e3dc", "22:23-24"], ["#57504a", "24:13-17,30-34;25:18-21,26-29"], ["#857d76", "26:22-25"], ["#9a928a", "23:21,26"], ["#c9c2b9", "23:22-25"], ["#a29a92", "24:22,25"], ["#b8b0a8", "24:23-24"], ["#8c847d", "25:22,25;26:22,25;27:22,25"], ["#a8a098", "25:23-24;26:23-24;27:23-24"], ["#918982", "28:22,25"], ["#7f7770", "29:22,25"], ["#aea69e", "28:23-24;29:23-24"], ["#b6aea6", "30:23-24;31:23-24"], ["#45403b", "15:16,31;16:14-15,32-33"]] },
  bicolour_mask_paw_up: { detail: true, short: true, jawUp: 1, pixels: [[25, 10, "#1d1d20"], [26, 10, "#1d1d20"], [14, 16, "#1d1d20"], [24, 12, "#f1ece3"], [24, 13, "#f1ece3"], [30, 13, "#f1ece3"], [31, 13, "#f1ece3"], [30, 14, "#f1ece3"], [31, 14, "#f1ece3"], [22, 16, "#f1ece3"], [22, 17, "#f1ece3"], [27, 19, "#f1ece3"]], fur: "#f1ece3", tailW: 0.75, tailWrap: true, sides: "#1d1d20", tailColor: "#1d1d20", cuffs: "#1d1d20", pawUp: true, small: true, ears: "#1d1d20", nose: "#2a2324", eyes: "#b9a843", mouth: "#7a6a62", marks: [["#1d1d20", "0:0-47;1:0-47;2:0-47;3:0-47;4:0-47;5:0-47;6:0-47;7:0-23,27-47;8:0-23,28-47;9:0-23,29-47"], ["#1d1d20", "10:30-47;11:30-47;12:30-31,33-47;13:30-31,33-47"], ["#1d1d20", "10:17-20;11:16-21;12:15-22;13:14-22;14:14-22;15:15-22"], ["#1d1d20", "10:0-23;11:0-23;12:0-23"], ["#1d1d20", "15:23-26;16:22-28;17:22-28;18:21-28;19:21-27;20:21-26;21:22-25;22:23-24"], ["#1d1d20", "7:24-26;8:24-26;9:24,27;10:24,28-29;11:24,29;12:24;13:23;14:23-26;16:16-17,21"], ["#1d1d20", "14:18-19;15:18-20"], ["#1d1d20", "36:31-47;37:31-47;38:31-47;39:31-47;40:31-47"]] },
};
// An appearance may name a preset; its own settings then change that look.
function spriteLook(look) {
  const { preset, ...own } = look || {};
  return { ...(SPRITE_PRESETS[preset] || {}), ...own };
}
const SPRITE_FRAMES = new Map();
function spriteFrames(spec) {
  const key = JSON.stringify(spec);
  if (!SPRITE_FRAMES.has(key)) {
    let frames = [];
    if (spec.cat) frames = spec.mode === "alert" ? alertCatFrames(spriteLook(spec.cat)) : idleCatFrames(spriteLook(spec.cat));
    else if (spec.wild && WILD[spec.wild]) {
      const make = WILD[spec.wild];
      frames = idleWildFrames(make, spec.wild); // wildlife has no alert pose: it just idles
      // Newly seen: it walks in first (a few steps), then idles.
      if (spec.mode === "walk") frames = [...[0, 1, 2].flatMap(() => walkerFrames(make)), ...frames];
    }
    SPRITE_FRAMES.set(key, frames.map(([g, d]) => [spriteImage(g), d]));
    if (SPRITE_FRAMES.size > 200) SPRITE_FRAMES.delete(SPRITE_FRAMES.keys().next().value);
  }
  return SPRITE_FRAMES.get(key);
}
function spriteImage(g) {
  const img = new ImageData(N, N);
  for (let y = 0; y < N; y++)
    for (let x = 0; x < N; x++) {
      const c = g[y][x];
      if (!c) continue;
      const n = parseInt((c.length === 4 ? c.replace(/#(.)(.)(.)/, "#$1$1$2$2$3$3") : c).slice(1), 16);
      const i = (y * N + x) * 4;
      img.data[i] = n >> 16;
      img.data[i + 1] = (n >> 8) & 255;
      img.data[i + 2] = n & 255;
      img.data[i + 3] = 255;
    }
  return img;
}
// Plays a sprite on a canvas until stopped; a walk-in plays once, then the idle loops.
function playSprite(canvas, spec) {
  const frames = spriteFrames(spec);
  const ctx = canvas.getContext("2d");
  if (!frames.length || !ctx) return () => {};
  const loopFrom = spec.mode === "walk" ? frames.length - spriteFrames({ ...spec, mode: "idle" }).length : 0;
  // Held still (reduced motion), it shows its resting frame, never one caught mid-move.
  let i = reducedMotion() ? loopFrom : spec.mode === "walk" ? 0 : Math.floor(Math.random() * frames.length);
  let timer = null;
  const step = () => {
    ctx.clearRect(0, 0, N, N);
    ctx.putImageData(frames[i][0], 0, 0);
    if (reducedMotion()) return;
    const wait = frames[i][1] * 1000;
    i = i + 1 >= frames.length ? loopFrom : i + 1;
    timer = setTimeout(step, wait);
  };
  step();
  return () => clearTimeout(timer);
}
// Mounts every sprite canvas under root (data-sprite holds its spec); returns a stop function.
function mountSprites(root) {
  const stops = [];
  root.querySelectorAll("canvas[data-sprite]").forEach((cv) => {
    try {
      stops.push(playSprite(cv, JSON.parse(cv.dataset.sprite)));
    } catch (e) {}
  });
  return () => stops.forEach((s) => s());
}
// Whether a photo is a night-vision (infrared) picture: almost no colour in it. A collar's
// colour can't be told from those, so the sprite shows it grey.
const IR_PHOTOS = new Map();
function isInfrared(url) {
  if (!url) return Promise.resolve(false);
  if (!IR_PHOTOS.has(url))
    IR_PHOTOS.set(
      url,
      new Promise((resolve) => {
        const img = new Image();
        img.onload = () => {
          try {
            const c = document.createElement("canvas");
            c.width = c.height = 32;
            const x = c.getContext("2d");
            x.drawImage(img, 0, 0, 32, 32);
            const d = x.getImageData(0, 0, 32, 32).data;
            let sat = 0;
            for (let i = 0; i < d.length; i += 4) sat += Math.max(d[i], d[i + 1], d[i + 2]) - Math.min(d[i], d[i + 1], d[i + 2]);
            resolve(sat / (d.length / 4) < 12);
          } catch (e) {
            resolve(false);
          }
        };
        img.onerror = () => resolve(false);
        img.src = url;
      }),
    );
  return IR_PHOTOS.get(url);
}
const spriteKind = (name) => Object.keys(WILD).find((k) => k.toLowerCase() === String(name || "").toLowerCase());

// A card with one animated sprite: a cat by its appearance, or a wildlife kind.
//   type: custom:neighborhood-watch-sprite
//   preset: brown_tabby   and/or   appearance: { fur: "#7c7e84", tabby: true, ... }   (or)   kind: Raccoon
//   name: optional caption; size: pixels (default 96)
class NeighborhoodWatchSprite extends HTMLElement {
  setConfig(config) {
    if (!config || (!config.appearance && !config.kind && !config.preset)) throw new Error("Set a preset or an appearance (a cat), or a kind (wildlife).");
    this._config = config;
    this._draw();
  }
  set hass(h) {}
  connectedCallback() {
    if (this._config && !this._stop) this._draw();
  }
  disconnectedCallback() {
    this._stop?.();
    this._stop = null;
  }
  _draw() {
    this._stop?.();
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    const c = this._config;
    const spec = c.appearance || c.preset ? { cat: { ...(c.preset ? { preset: c.preset } : {}), ...(c.appearance || {}) } } : { wild: spriteKind(c.kind) || c.kind };
    const size = Number(c.size) || 96;
    this.shadowRoot.innerHTML = `<style>
        :host { display: block; }
        .wrap { display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100%; }
        canvas { width: ${size}px; height: ${size}px; image-rendering: pixelated; }
        .name { font-size: 0.85em; color: var(--secondary-text-color); }
      </style><div class="wrap"><canvas width="48" height="48" data-sprite="${esc(JSON.stringify(spec))}" role="img" aria-label="${esc(c.name || c.kind || "Animal")}"></canvas>${
        c.name ? `<div class="name">${esc(c.name)}</div>` : ""
      }</div>`;
    this._stop = mountSprites(this.shadowRoot);
  }
  getCardSize() {
    return 2;
  }
  getGridOptions() {
    return { columns: 3, rows: 2, min_columns: 2, min_rows: 1 };
  }
  static getStubConfig() {
    return { kind: "Raccoon" };
  }
}
customElements.define("neighborhood-watch-sprite", NeighborhoodWatchSprite);
window.customCards.push({
  type: "neighborhood-watch-sprite",
  name: "Neighborhood Watch sprite",
  description: "One animated pixel animal: a cat from its appearance, or a wildlife kind.",
  preview: true,
  documentationURL: "https://github.com/b2dmx/neighborhood-watch",
});
  return { mountSprites, isInfrared, spriteKind, hasCollar: (look) => !!spriteLook(look).collar };
})();

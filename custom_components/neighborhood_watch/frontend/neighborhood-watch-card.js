/*
 * Neighborhood Watch card — ships with the integration and finds its own devices:
 * animals (photo, where and when, on camera now, missing mode), wildlife, the night
 * summary, each camera's last detection with Right / Wrong buttons, and the switches.
 * Every section can be turned off, devices hidden, and the layout tuned in the editor.
 */
// The pixel-art animals live in a file of their own, fetched with this card's version so
// an update always brings both.
const { NWSprites } = await import(new URL("./neighborhood-watch-sprites.js" + new URL(import.meta.url).search, import.meta.url).href);
const DOMAIN = "neighborhood_watch";
// Signed photo links, shared by every card on the page: path -> {url, exp}, and the requests under way.
const SIGNED = new Map();
const SIGNING = new Map();
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
    this._broken = this._broken || new Map();  // path -> when it was given up on
    this._answered = this._answered || new Set(); // answered here: their buttons stay off
    this._hidden = this._hidden || new Map(); // answered in To review: id -> how it was listed then
    if (this._hass) this._render();
  }

  connectedCallback() {
    // A quiet system sends no updates, so refresh each minute to keep "x min ago" true.
    if (!this._tick) this._tick = setInterval(() => this._onTick(), 60000);
    if (!this._onKey) {
      this._onKey = (ev) => {
        if (ev.key === "Escape" && this._zoom) this._closeLightbox();
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
    // Put back on the page (edit mode, a view laid out again): the sprites were stopped.
    if (this.shadowRoot && this._hass && this._config) {
      this._parts = null;
      this._render(true);
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
    return { sections: SECTIONS.map((s) => s.value).filter((v) => v !== "scan") }; // shown ticked in the editor
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "title", selector: { text: {} } },
        { name: "sections", selector: { select: { multiple: true, options: SECTIONS } } },
        { name: "hide", selector: { device: { multiple: true, filter: { integration: DOMAIN } } } },
        { name: "others", selector: { device: { multiple: true, filter: { integration: DOMAIN } } } },
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
          others: "Show under Also seen",
          order: "Animal order",
          columns: "Animal columns",
          photo_height: "Photo height",
          review_limit: "Sightings to review at once",
          photo_hours: "Photo shows for (0 = always)",
        })[s.name],
      computeHelper: (s) =>
        ({
          sections: "Empty: all of them except Scan a folder.",
          hide: "Animals or cameras to leave out.",
          others: "These animals move from the main row to the Also seen row below it, with any unknown cats.",
          order: "Default: most recently seen first.",
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
    const kinds = d.hub?.keys.wildlife ? this._state(d.hub.keys.wildlife)?.attributes?.kinds || {} : {};
    const names =
      Array.isArray(choices) && choices.length
        ? choices
        : [...new Set([...d.animals.map((x) => x.name), ...Object.keys(kinds)])];
    // Most recently seen first; never-seen ones keep their order after them.
    const seen = {};
    for (const a of d.animals) seen[a.name] = new Date(this._state(a.keys.last_seen)?.state).getTime();
    for (const [name, k] of Object.entries(kinds)) seen[name] = new Date(k?.last_seen).getTime();
    const at = (n) => (Number.isNaN(seen[n]) || seen[n] == null ? -Infinity : seen[n]);
    return names.map((n, i) => [n, i]).sort((a, b) => at(b[0]) - at(a[0]) || a[1] - b[1]).map(([n]) => n);
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
    // A press in progress: a redraw now would swap the button under the finger, and the tap
    // could land on another sighting's. Typing, or a list that's open: a redraw would undo it.
    // (At most 30 s, see _render: a list closed without choosing stays focused.)
    if (this._pressing) return true;
    const el = this.shadowRoot?.activeElement;
    return !!(el && !el.disabled && el.matches("input, textarea, select"));
  }

  // What has the keyboard focus, so a redraw can give it back (the redraw replaces every element).
  _focused() {
    const el = this.shadowRoot?.activeElement;
    if (!el) return null;
    const data = [...el.attributes].filter((a) => a.name.startsWith("data-") && a.name !== "data-ago");
    let caret = null;
    try {
      if (el.matches("input, textarea") && el.selectionStart != null) caret = [el.selectionStart, el.selectionEnd];
    } catch (e) {}
    if (data.length)
      return { sel: el.tagName.toLowerCase() + data.map((a) => `[${a.name}="${CSS.escape(a.value)}"]`).join(""), caret };
    if (el.classList.contains("foldbtn")) return { title: el.textContent.split(" · ")[0].trim() };
    return null;
  }

  _refocus(was) {
    if (!was || this._zoom) return;
    const root = this.shadowRoot;
    const el = was.sel
      ? root.querySelector(was.sel)
      : [...root.querySelectorAll(".foldbtn")].find((b) => b.textContent.split(" · ")[0].trim() === was.title);
    if (!el || el.disabled) return;
    el.focus({ preventScroll: true });
    try {
      if (was.caret) el.setSelectionRange(was.caret[0], was.caret[1]);
    } catch (e) {}
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
    const focused = this._focused();
    const typing = root?.querySelector("[data-note-text]");
    if (typing && this._note) this._note.text = typing.value;
    const about = root?.querySelector("[data-note-about]");
    if (about && this._note) this._note.pick = about.value;
    const folderInput = root?.querySelector("input[data-folder]");
    if (folderInput) this._scanFolder = folderInput.value;
    const typedBox = root?.querySelector("input[data-type-sid]");
    if (typedBox) {
      this._typedText = typedBox.value;
      this._typedFocus = this._typedFocus || root.activeElement === typedBox;
    }
    const show =new Set(this._config.sections || []);
    const d = this._devices();
    const parts = [];
    if (show.has("review") && d.hub?.keys.to_review) parts.push(this._review(d));
    const unknownSeen = Object.keys(this._state(d.hub?.keys.wildlife)?.attributes?.unknown || {}).length;
    if (show.has("animals") && (d.animals.length || unknownSeen)) parts.push(this._animals(d.animals, d.hub));
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
    const cols = Math.max(1, Math.min(6, Math.round(Number(this._config.columns)) || 3));
    const ph = Math.max(60, Math.min(400, Math.round(Number(this._config.photo_height)) || 140));
    if (this._styleKey !== `${cols}|${ph}`) {
      this._styleKey = `${cols}|${ph}`;
      this._style = STYLE.replace("__COLS__", cols).replace("__SMALLCOLS__", Math.min(cols, 2)).replace("__PH__", ph);
    }
    // Hold the card's height while it redraws and its pictures load, so the page doesn't
    // jump (scroll position snaps back when the card briefly gets shorter).
    const held = this.offsetHeight;
    if (held && force) {
      this.style.minHeight = `${held}px`;
      clearTimeout(this._release);
      this._release = setTimeout(() => (this.style.minHeight = ""), 1500);
    }
    // The frame (style, title, an empty body, a place for the enlarged photo) is built once.
    // Each section then sits in a holder of its own and is replaced only when its HTML
    // changed: an update to one animal doesn't rebuild To review (photos don't blink, an
    // open list stays open, sprites keep playing).
    const sr = this.shadowRoot;
    const frame = `<style>${this._style}</style>
      <ha-card>
        <div class="whole${this._config.fold === false ? " nofold" : ""}">
          ${this._config.hide_title ? "" : `<h2 class="title">${esc(this._config.title || "Neighborhood Watch")}</h2>`}
          <div class="inner"></div>
        </div>
      </ha-card><div class="lbhost"></div>`;
    let titles = [];
    if (this._frame !== frame || !sr.querySelector(".inner")) {
      this._stopSprites?.();
      this._frame = frame;
      sr.innerHTML = frame;
      this._parts = null;
      this._lbHtml = "";
      titles = [...sr.querySelectorAll(".whole > h2")];
    }
    const inner = sr.querySelector(".inner");
    const list = parts.filter(Boolean);
    if (!list.length)
      list.push(
        this._sectionsSet && !(this._config.sections || []).length
          ? `<div class="empty">No sections are picked in this card's settings.</div>`
          : `<div class="empty">${esc(this._config.empty || "Nothing to show yet. On the Neighborhood Watch integration page, add cameras under Configure → Cameras and animals with Add animal.")}</div>`
      );
    // "x min ago" moves on its own (the minute tick updates it in place): not a change.
    const keys = list.map((html) => html.replace(/(<span data-ago="[^"]*">)[^<]*/g, "$1"));
    const changed = [];
    if (!this._parts || this._parts.length !== keys.length || inner.children.length !== keys.length) {
      this._stopSprites?.();
      inner.innerHTML = list.map(() => `<div class="part"></div>`).join("");
      [...inner.children].forEach((holder, i) => {
        holder.innerHTML = list[i];
        changed.push(holder);
      });
    } else {
      keys.forEach((key, i) => {
        if (key === this._parts[i]) return;
        const holder = inner.children[i];
        holder._stop?.();
        holder.innerHTML = list[i];
        changed.push(holder);
      });
    }
    this._parts = keys;
    for (const holder of changed) holder._stop = NWSprites.mountSprites(holder);
    this._stopSprites = () => inner.querySelectorAll(".part").forEach((holder) => holder._stop?.());
    // Everything below wires up only what was just (re)built.
    const all = (sel) => changed.flatMap((holder) => [...holder.querySelectorAll(sel)]);
    const one = (sel) => all(sel)[0];
    this._checkInfrared();
    // Every section folds: tap its title. "To review" starts folded; choices are remembered.
    const tappable = (el) => {
      el.tabIndex = 0;
      el.setAttribute("role", "button");
    };
    // fold: false (the sidebar page's review list): nothing folds, whatever was saved.
    if (this._config.fold !== false) [...all(".section > h2"), ...titles].forEach((h) => {
      const key = h.textContent.split(" · ")[0].trim();
      if (this._folded().has(key)) h.parentElement.classList.add("folded");
      // The title stays a heading (screen readers jump between headings); the button is inside it.
      const btn = document.createElement("span");
      btn.className = "foldbtn";
      btn.append(...h.childNodes);
      h.append(btn);
      tappable(btn);
      btn.setAttribute("aria-expanded", String(!this._folded().has(key)));
      h.addEventListener("click", () => {
        const folded = this._folded();
        folded.has(key) ? folded.delete(key) : folded.add(key);
        try {
          localStorage.setItem(FOLD_KEY, JSON.stringify([...folded]));
        } catch (e) {}
        btn.setAttribute("aria-expanded", String(!h.parentElement.classList.toggle("folded")));
      });
    });
    all("[data-more]").forEach((el) => {
      tappable(el);
      el.addEventListener("click", () => this._more(el.dataset.more));
    });
    all("[data-toggle]").forEach((el) =>
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        this._hass.callService("switch", "toggle", { entity_id: el.dataset.toggle });
      })
    );
    const undoBtn = one("[data-undo]");
    if (undoBtn)
      undoBtn.addEventListener("click", () => {
        undoBtn.disabled = true;
        // The undone sighting comes back to the list: stop hiding it (and turn its buttons back on).
        const show = (id) => {
          if (!id) return;
          this._hidden.delete(String(id));
          this._answered.delete(String(id));
        };
        show(this._state(this._devices().hub?.keys.to_review)?.attributes?.last_answer?.id);
        this._note = null;
        Promise.resolve(this._hass.callService(DOMAIN, "undo_last_answer", {}, undefined, true, true))
          .then((r) => show(r?.response?.undone))
          .catch((err) => alert(`Not undone: ${err.message || err}`))
          .finally(() => {
            this._parts = null; // everything again: the button was turned off by hand
            this._render(true);
          });
      });
    all("[data-answer]").forEach((el) =>
      el.addEventListener("click", () => {
        el.disabled = true;
        this._answer(el.dataset.sid, el.dataset.answer);
      })
    );
    all("select[data-sid]").forEach((el) =>
      el.addEventListener("change", () => {
        if (!el.value) return;
        if (el.value === TYPE_IT) {
          // Not on the list: type it instead.
          this._typing = el.dataset.sid;
          this._typedText = "";
          this._typedMsg = "";
          this._render(true);
          this.shadowRoot.querySelector("input[data-type-sid]")?.focus();
          return;
        }
        el.disabled = true;
        this._answer(el.dataset.sid, el.value);
      })
    );
    const typed = one("input[data-type-sid]");
    if (typed) {
      typed.value = this._typedText || "";
      if (this._typedFocus) {
        this._typedFocus = false;
        typed.focus();
      }
      typed.addEventListener("input", () => (this._typedText = typed.value));
      const send = () => {
        let v = typed.value.trim();
        if (!v) return typed.focus();
        const word = v.toLowerCase().replace(/[.!?]+$/, "");
        if (["none", "nothing"].includes(word)) v = "Nothing";
        else if (TYPED_RESERVED.includes(word)) {
          // These are answers of their own, not an animal's name.
          this._typedMsg = "Use the buttons for that.";
          this._typedText = typed.value;
          this._typedFocus = true;
          return this._render(true);
        }
        close();
        this._answer(typed.dataset.typeSid, v);
      };
      const close = () => {
        this._typing = null;
        this._typedText = "";
        this._typedMsg = "";
      };
      typed.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") send();
        else if (ev.key === "Escape") {
          ev.stopPropagation();
          close();
          this._render(true);
        }
      });
      one("[data-type-ok]")?.addEventListener("click", send);
      one("[data-type-cancel]")?.addEventListener("click", () => {
        close();
        this._render(true);
      });
    }
    const noteSave = one("[data-note-save]");
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
    const noteSkip = one("[data-note-skip]");
    if (noteSkip)
      noteSkip.addEventListener("click", () => {
        this._note = null;
        this._render(true);
      });
    this._loadFrames(all("img[data-frame]"));
    // Opened from an alert's "Other…" button (page#review): open To review and show it, once.
    if (location.hash === "#review" && !this._reviewShown) {
      const h = [...this.shadowRoot.querySelectorAll(".section > h2")].find((x) => x.textContent.startsWith("To review"));
      if (h) {
        this._reviewShown = true;
        h.parentElement.classList.remove("folded");
        h.querySelector(".foldbtn")?.setAttribute("aria-expanded", "true");
        setTimeout(() => h.scrollIntoView({ behavior: "smooth", block: "start" }), 300);
      }
    } else if (location.hash !== "#review") this._reviewShown = false;
    // Pictures that enlarge when tapped.
    all("[data-zoom]").forEach((el) => {
      tappable(el);
      el.addEventListener("click", () => {
        this._zoom = el.dataset.zoom;
        this._zoomAlt = el.alt;
        this._lb = null;
        this._render(true);
      });
    });
    // The enlarged photo, rebuilt only when it opens, closes or changes mode.
    const lightbox = this._lightbox();
    if (lightbox !== this._lbHtml) {
      this._lbHtml = lightbox;
      const host = sr.querySelector(".lbhost");
      host.innerHTML = lightbox;
      this._loadFrames([...host.querySelectorAll("img[data-frame]")]);
      this._wireLightbox();
    }
    this._refocus(focused);
    // Sections left as they were still show the right "x min ago".
    sr.querySelectorAll("[data-ago]").forEach((el) => (el.textContent = ago(el.dataset.ago)));
    const scanButton = one("[data-scan]");
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
    sid = String(sid);
    const d = this._devices();
    const items = this._state(d.hub?.keys.to_review)?.attributes?.items || [];
    const it = items.find((x) => String(x.id) === sid);
    // Remembered so its buttons stay off (Recent detections keeps showing the sighting).
    this._answered.add(sid);
    // Gone from To review at once, until it leaves the list; Home Assistant catches up in the background.
    if (it) this._hidden.set(sid, listedAs(it));
    // Answered while the list is that long: keep only the newest few hundred.
    for (const old of this._answered) if (this._answered.size > 200) this._answered.delete(old);
    this._note = this._noteFor(d, it, answer) || this._note;
    this._render(true);
    Promise.resolve(this._hass.callService(DOMAIN, "correct", { answer, sighting_id: sid })).catch((err) => {
      this._answered.delete(sid);
      this._hidden.delete(sid);
      this._parts = null;
      this._render(true);
      alert(`Answer not saved: ${err.message || err}`);
    });
  }

  // A mistake fixed: the note box that offers to tell the AI, where it will use it.
  _noteFor(d, it, answer) {
    if (["right", "unsure"].includes(answer) || !it) return null;
    // Notes are about an animal or a camera; wildlife kinds can't hold one.
    const known = new Set(d.animals.map((x) => x.name));
    const about = [];
    if (answer !== "wrong" && known.has(answer)) about.push(["animal", answer, `${answer}: how to recognise them`]);
    if (known.has(it.animal) && it.animal !== answer) about.push(["animal", it.animal, `${it.animal}: how to tell them apart`]);
    if (d.cameras.some((c) => c.name === it.where)) about.push(["camera", it.where, `${it.where} camera: what's in this view`]);
    return about.length ? { said: it.animal, was: answer === "wrong" ? "not that" : answer, about, text: "" } : null;
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

  _loadFrames(images) {
    images.forEach((img) => {
      // "crop": cut to the animal the local detector found; the full frame otherwise.
      const path = `/api/${DOMAIN}/frame/${img.dataset.frame}${img.dataset.crop ? "/crop" : ""}`;
      // A frame that won't load shows its fallback picture or is marked broken, and isn't
      // asked for again on every redraw: only after a couple of minutes, since most failures
      // pass (Home Assistant still starting, the photo folder slow to answer).
      const giveUp = () => {
        if (img.dataset.fallback && !img.dataset.fellBack) {
          img.dataset.fellBack = "1";
          img.src = img.dataset.fallback;
        } else img.classList.add("broken");
      };
      const since = this._broken.get(path);
      if (since && Date.now() - since < 120e3) return giveUp();
      this._broken.delete(path);
      // A failed load is tried twice more, a few seconds apart, each time signed afresh
      // (the link may have expired).
      // (Counted per photo on the card: a redraw replaces the element, and must not start
      // the count again, or a photo that's gone would be asked for on every redraw.)
      img.addEventListener("error", () => {
        if (img.dataset.fellBack) return img.classList.add("broken");
        this._tries = this._tries || new Map();
        const tries = this._tries.get(path) || 0;
        if (tries >= 2) {
          this._tries.delete(path);
          this._broken.set(path, Date.now());
          return giveUp();
        }
        this._tries.set(path, tries + 1);
        delete this._signed[path];
        SIGNED.delete(path);
        setTimeout(() => img.isConnected && this._sign(img, path, giveUp), tries ? 6000 : 2000);
      });
      img.addEventListener("load", () => this._tries?.delete(path));
      this._sign(img, path, giveUp);
    });
  }

  _sign(img, path, giveUp) {
    // Signed links last a day; sign again in the last hour so a thumbnail never goes stale.
    // Shared by every card on the page (SIGNED), and one request per photo however many
    // redraws ask for it meanwhile.
    const cached = this._signed[path] || SIGNED.get(path);
    if (cached && cached.exp - Date.now() > 3600e3) {
      this._signed[path] = cached;
      img.src = cached.url;
      return;
    }
    let asking = SIGNING.get(path);
    if (!asking) {
      asking = this._hass
        .callWS({ type: "auth/sign_path", path, expires: 86400 })
        .then((r) => {
          const now = Date.now();
          for (const [p, c] of SIGNED) if (c.exp <= now) SIGNED.delete(p);
          for (const [p, c] of Object.entries(this._signed)) if (c.exp <= now) delete this._signed[p];
          const link = { url: r.path, exp: now + 86400e3 };
          SIGNED.set(path, link);
          return link;
        })
        .finally(() => SIGNING.delete(path));
      SIGNING.set(path, asking);
    }
    asking
      .then((link) => {
        this._signed[path] = link;
        if (img.isConnected) img.src = link.url;
      })
      .catch(() => (giveUp ? giveUp() : img.classList.add("broken")));
  }

  _closeLightbox() {
    const from = this._zoom;
    this._zoom = this._lb = this._marked = null;
    this._marking = false;
    this._render(true);
    // Back to the picture it was opened from.
    if (from) this.shadowRoot.querySelector(`[data-zoom="${CSS.escape(from)}"]`)?.focus({ preventScroll: true });
  }

  _lightbox() {
    // The full frame, over everything. Tap the picture to zoom in where you tapped; scroll or
    // pinch to zoom, drag to move around.
    if (!this._zoom) return "";
    return `<div class="lightbox" role="dialog" aria-modal="true" aria-label="${esc(this._zoomAlt || "Sighting")}">
        <button class="lbclose" title="Close" aria-label="Close">✕</button>
        <button class="lbmark" aria-pressed="${this._marking ? "true" : "false"}" title="Drag a box around the animal: the detector learns from it">Mark the animal</button>
        <div class="lbbox" hidden></div>
        <img class="lbimg" data-frame="${esc(this._zoom)}" alt="${esc(this._zoomAlt || "Sighting")}" draggable="false">
        <div class="lbhint" role="status">${
          this._marking
            ? "Drag a box around the animal"
            : this._marked === this._zoom
              ? "Saved. The detector will learn from your box."
              : "Tap to zoom in · scroll, pinch or + and − to zoom · drag or arrows to move"
        }</div>
      </div>`;
  }

  _wireLightbox() {
    const box = this.shadowRoot.querySelector(".lightbox");
    if (!box) return;
    box.querySelector(".lbclose").focus();
    // Tapping around the picture (or ✕) closes it; the picture itself zooms and moves.
    box.addEventListener("click", (ev) => {
      if (ev.target.closest(".lbmark")) {
        // Mark the animal: the next drag on the picture draws a box instead of moving it.
        this._marking = !this._marking;
        this._marked = null;
        this._render(true);
        this.shadowRoot.querySelector(".lbmark")?.focus();
        return;
      }
      if (ev.target !== box && !ev.target.closest(".lbclose")) return;
      this._closeLightbox();
    });
    const outline = box.querySelector(".lbbox");
    let from = null; // where the box being drawn started
    const img = box.querySelector(".lbimg");
    // Kept on the card, so a redraw while it's open (a new sighting) keeps the zoom.
    const v = (this._lb = this._lb || { s: 1, x: 0, y: 0 });
    const apply = () => {
      if (v.s <= 1) Object.assign(v, { s: 1, x: 0, y: 0 });
      // Never further than the picture's own edge.
      const mx = (img.offsetWidth * (v.s - 1)) / 2;
      const my = (img.offsetHeight * (v.s - 1)) / 2;
      v.x = Math.max(-mx, Math.min(mx, v.x));
      v.y = Math.max(-my, Math.min(my, v.y));
      img.style.transform = `translate(${v.x}px, ${v.y}px) scale(${v.s})`;
      img.style.cursor = v.s > 1 ? "grab" : "zoom-in";
    };
    const zoomAt = (px, py, scale) => {
      const next = Math.max(1, Math.min(8, scale));
      const r = img.getBoundingClientRect();
      v.x += (px - (r.left + r.width / 2)) * (1 - next / v.s);
      v.y += (py - (r.top + r.height / 2)) * (1 - next / v.s);
      v.s = next;
      apply();
    };
    img.addEventListener("load", apply);
    apply();
    box.addEventListener(
      "wheel",
      (ev) => {
        ev.preventDefault();
        zoomAt(ev.clientX, ev.clientY, v.s * Math.exp(-ev.deltaY * 0.0015));
      },
      { passive: false }
    );
    const down = new Map();
    let moved = false;
    let pinch = 0;
    const drawBox = (ev) => {
      const l = Math.min(from.x, ev.clientX), t = Math.min(from.y, ev.clientY);
      Object.assign(outline.style, {
        left: `${l}px`, top: `${t}px`, width: `${Math.abs(ev.clientX - from.x)}px`, height: `${Math.abs(ev.clientY - from.y)}px`,
      });
      outline.hidden = false;
    };
    img.addEventListener("pointerdown", (ev) => {
      try {
        img.setPointerCapture(ev.pointerId);
      } catch {
        // already gone (a very quick tap): it still counts
      }
      if (this._marking) {
        from = { x: ev.clientX, y: ev.clientY, id: ev.pointerId };
        return;
      }
      down.set(ev.pointerId, { x: ev.clientX, y: ev.clientY });
      if (down.size === 1) moved = false;
      if (down.size === 2) {
        const [a, b] = [...down.values()];
        pinch = Math.hypot(a.x - b.x, a.y - b.y);
      }
    });
    img.addEventListener("pointermove", (ev) => {
      if (from && ev.pointerId === from.id) return drawBox(ev);
      const was = down.get(ev.pointerId);
      if (!was) return;
      const now = { x: ev.clientX, y: ev.clientY };
      down.set(ev.pointerId, now);
      if (down.size === 2) {
        const [a, b] = [...down.values()];
        const dist = Math.hypot(a.x - b.x, a.y - b.y);
        if (pinch) zoomAt((a.x + b.x) / 2, (a.y + b.y) / 2, (v.s * dist) / pinch);
        pinch = dist;
        moved = true;
        return;
      }
      if (Math.abs(now.x - was.x) + Math.abs(now.y - was.y) > 0) {
        if (Math.hypot(now.x - was.x, now.y - was.y) > 3) moved = true;
        v.x += now.x - was.x;
        v.y += now.y - was.y;
        apply();
      }
    });
    const up = (ev) => {
      if (from && ev.pointerId === from.id) {
        // The box, as fractions of the picture as it is shown (zoomed or not).
        const r = img.getBoundingClientRect();
        const f = (v, start, size) => Math.max(0, Math.min(1, (v - start) / size));
        const b = [
          f(Math.min(from.x, ev.clientX), r.left, r.width), f(Math.min(from.y, ev.clientY), r.top, r.height),
          f(Math.max(from.x, ev.clientX), r.left, r.width), f(Math.max(from.y, ev.clientY), r.top, r.height),
        ];
        from = null;
        outline.hidden = true;
        // A slip, not a box (or the picture isn't showing yet): nothing is saved.
        if (ev.type === "pointercancel" || !r.width || !r.height || !(b[2] - b[0] >= 0.01) || !(b[3] - b[1] >= 0.01)) return;
        const sid = this._zoom;
        this._marking = false;
        Promise.resolve(this._hass.callService(DOMAIN, "mark_animal", { sighting_id: sid, box: b.map((v) => Math.round(v * 10000) / 10000) }))
          .then(() => (this._marked = sid))
          .catch((err) => alert(`Box not saved: ${err.message || err}`))
          .finally(() => this._zoom === sid && this._render(true));
        return;
      }
      if (!down.delete(ev.pointerId)) return;
      pinch = 0;
      if (down.size || moved || ev.type === "pointercancel") return;
      // A tap: zoom in where it landed, or back out.
      if (v.s > 1) {
        v.s = 1;
        apply();
      } else zoomAt(ev.clientX, ev.clientY, 3);
    };
    img.addEventListener("pointerup", up);
    img.addEventListener("pointercancel", up);
    // Keyboard: + and - zoom, the arrows move, Tab stays on Close (nothing behind it is reachable).
    box.addEventListener("keydown", (ev) => {
      const r = img.getBoundingClientRect();
      const mid = [r.left + r.width / 2, r.top + r.height / 2];
      const move = { ArrowLeft: [60, 0], ArrowRight: [-60, 0], ArrowUp: [0, 60], ArrowDown: [0, -60] }[ev.key];
      if (ev.key === "Tab") {
        // Tab goes between the two buttons (nothing behind the picture is reachable).
        const buttons = [...box.querySelectorAll("button")];
        buttons[(buttons.indexOf(this.shadowRoot.activeElement) + 1) % buttons.length].focus();
      }
      else if (ev.key === "+" || ev.key === "=") zoomAt(mid[0], mid[1], v.s * 1.5);
      else if (ev.key === "-" || ev.key === "_") zoomAt(mid[0], mid[1], v.s / 1.5);
      else if (move && v.s > 1) {
        v.x += move[0];
        v.y += move[1];
        apply();
      } else return;
      ev.preventDefault();
    });
  }

  _review(d) {
    const s = this._state(d.hub.keys.to_review);
    const listed = s?.attributes?.items || [];
    // Answered here but not yet gone from the sensor: hidden already. Forget the ones that
    // have left the list, and show again any that is listed differently now (it's still waiting).
    const byId = new Map(listed.map((it) => [String(it.id), it]));
    for (const [id, was] of this._hidden) {
      const now = byId.get(id);
      if (!now || listedAs(now) !== was) this._hidden.delete(id);
    }
    const items = listed.filter((it) => !this._hidden.has(String(it.id)));
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
    const names = ["Nothing", ...this._names(d)];
    const options =
      names.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("") +
      `<option value="${TYPE_IT}">Something else (type it)…</option>`;
    const suggest = `<datalist id="nw-names">${names.map((n) => `<option value="${esc(n)}">`).join("")}</datalist>`;
    const rows = items.slice(0, limit).map((it) => {
      const sid = esc(it.id);
      const pick =
        this._typing === String(it.id)
          ? `<input class="typed" data-type-sid="${sid}" list="nw-names" placeholder="What was it?" aria-label="What was it?">` +
            `<button class="mini" data-type-ok title="Save" aria-label="Save">✓</button>` +
            `<button class="mini" data-type-cancel title="Cancel" aria-label="Cancel">✕</button>`
          : // "No answer" (no AI could look): nothing to agree with, only what it was.
            `${it.animal === "No answer" ? "" : `<button class="mini" data-answer="right" data-sid="${sid}" title="Right" aria-label="Right">✓</button>`}
            <button class="mini" data-answer="unsure" data-sid="${sid}" title="Not sure" aria-label="Not sure">?</button>
            <select data-sid="${sid}" aria-label="It was"><option value="">It was…</option>${options}</select>`;
      return `<div class="rev">
          <img class="thumb" data-frame="${sid}" data-crop="1" data-zoom="${sid}" alt="Sighting: ${esc(it.animal)} at ${esc(it.where)}" title="Tap to enlarge">
          <div class="revname"${it.disagree ? ` title="The AI said nothing is there, but the detector saw an animal. Which is it?"` : ""}><b>${esc(it.animal)}</b> <span class="muted">${it.disagree ? "· detector saw an animal" : esc(it.confidence)}</span></div>
          <div class="muted small">${esc(it.where)} · ${clock(it.time)} · <span data-ago="${esc(it.time)}">${ago(it.time)}</span></div>
          <div class="acts">${pick}</div>
          ${this._typing === String(it.id) && this._typedMsg ? `<div class="muted small" role="alert">${esc(this._typedMsg)}</div>` : ""}
        </div>`;
    });
    // The sensor lists at most 40; its state is the full count.
    const total = Number.isFinite(Number(s.state)) ? Number(s.state) : items.length;
    const left = total - (listed.length - items.length);
    const more = left > limit ? `<div class="muted">${left - limit} more after these.</div>` : "";
    return `<div class="section"><h2>To review · ${left}</h2>${undo}${this._noteBox()}<div class="revgrid">${rows.join("")}</div>${more}${suggest}</div>`;
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
      const photo = this._state(a.keys.last_photo);
      const url = photo?.attributes?.entity_picture;
      const look = seen?.attributes?.appearance;
      // One answer per photo: the link's access token changes every few minutes, the photo
      // (the image entity's state) only when a new one is taken.
      const was = this._ir[a.keys.last_photo];
      if (!look || !NWSprites.hasCollar(look) || !url || was?.photo === photo.state) continue;
      const mine = (this._ir[a.keys.last_photo] = { photo: photo.state, ir: false });
      NWSprites.isInfrared(url, `${a.keys.last_photo}|${photo.state}`).then((ir) => {
        mine.ir = ir;
        if (ir && this._ir[a.keys.last_photo] === mine) this._render(true);
      });
    }
  }

  _more(entityId) {
    this.dispatchEvent(new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true }));
  }

  _animals(animals, hub) {
    // The main row holds the animals you track closely; the rest (picked in the card's settings)
    // and unknown animals the AI couldn't name sit under "Also seen".
    const others = new Set(this._config.others || []);
    const main = animals.filter((a) => !others.has(a.id)).map((a) => this._tile(a));
    const unknown = Object.entries(this._state(hub?.keys.wildlife)?.attributes?.unknown || {}).map(([n, k]) => this._unknownTile(n, k, hub));
    const also = [...animals.filter((a) => others.has(a.id)).map((a) => this._tile(a)), ...unknown];
    return `<div class="section">${main.length ? `<div class="grid">${main.join("")}</div>` : ""}${
      also.length ? `<div class="subhead">Also seen</div><div class="grid">${also.join("")}</div>` : ""
    }</div>`;
  }

  // An unknown animal ("Unknown Cat"): drawn as the nearest ready-made look to the coat the
  // AI described at its latest sighting.
  _unknownTile(name, k, hub) {
    const live = k.last_seen && Date.now() - new Date(k.last_seen).getTime() < 10 * 60e3;
    // Always a sprite: its own kind's when there is one (an unknown fox), else the cat
    // look nearest to the coat the AI described.
    const kind = /cat/i.test(name) ? null : NWSprites.spriteKind(name.replace(/^unknown\s+/i, ""));
    const spec = kind ? { wild: kind } : { cat: NWSprites.nearestLook(k) };
    const pic = `<canvas class="sprite" width="48" height="48" data-sprite="${esc(JSON.stringify({ ...spec, ...(live ? { mode: "alert" } : {}) }))}" role="img" aria-label="${esc(name)}"></canvas>`;
    const badges = [];
    if (k.tonight) badges.push(`<span class="badge">${esc(k.tonight)} tonight</span>`);
    if (k.possible) badges.push(`<span class="badge dim">possible</span>`);
    const status = live
      ? `<span class="live">● On camera · ${esc(k.where || "")}</span>`
      : `${esc(k.where || "")} · <span data-ago="${esc(k.last_seen || "")}">${ago(k.last_seen)}</span>`;
    return `<div class="tile ${live ? "is-live" : ""}" title="${esc(k.description || "")}">
        <div class="pic" data-more="${esc(hub.keys.wildlife)}"><div class="nophoto">${pic}</div>
          <div class="over"><div class="name">${esc(name)}</div><div class="status">${status}</div></div>
          <div class="badges">${badges.join("")}</div>
        </div>
      </div>`;
  }

  _tile(a) {
    {
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
      // Its own look when one is set, else the ready-made look nearest to its description.
      const attrs = seen?.attributes || {};
      const look = attrs.appearance || NWSprites.nearestLook({ pattern: attrs.pattern, base: attrs.base, description: attrs.looks || "" });
      const ir = NWSprites.hasCollar(look) && this._ir?.[a.keys.last_photo]?.ir;
      // Away lately: asleep for part of the time, more the longer it has been gone.
      const awayDays = !live && absent?.state === "on" && seen ? (Date.now() - new Date(seen.state).getTime()) / 864e5 : NaN;
      const mode = live ? { mode: "alert" } : awayDays >= 0 ? { mode: "sleep", share: NWSprites.sleepShare(awayDays) } : {};
      const sprite = { cat: { ...look, ...(ir ? { ir: true } : {}) }, ...mode };
      // No real photo (never seen, or it's older than the card keeps): the animal's sprite.
      const img = !hasPhoto
        ? `<div class="nophoto"><canvas class="sprite" width="48" height="48" data-sprite="${esc(JSON.stringify(sprite))}" role="img" aria-label="${esc(a.name)}"></canvas></div>`
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
    }
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
const TYPE_IT = "__type__"; // the "It was…" choice that opens a box to type an answer
// How a sighting is listed in To review: a change means it's waiting again after an answer.
const listedAs = (it) => `${it.animal}|${it.confidence}`;
// Typed words that are the buttons' answers, not something it was ("none" and "nothing" mean Nothing).
const TYPED_RESERVED = ["right", "wrong", "unsure", "not sure", "skip", "maybe", "other"];

const STYLE = `
  :host { display: block; container-type: inline-size; }
  .section > h2, .whole > h2 { cursor: pointer; user-select: none; }
  .foldbtn:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; border-radius: 4px; }
  img.broken { opacity: .3; }
  .part { display: contents; }
  .lbmark { position: absolute; top: 12px; left: 14px; z-index: 1; min-height: 40px; padding: 0 16px; border-radius: 999px; border: none; font: inherit; font-weight: 500; cursor: pointer; background: var(--secondary-background-color); color: var(--primary-text-color); }
  .lbmark[aria-pressed="true"] { background: var(--primary-color); color: var(--text-primary-color, #fff); }
  .lbmark:focus-visible, .lbclose:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
  .lbbox { position: fixed; z-index: 1; border: 2px solid var(--primary-color); background: color-mix(in srgb, var(--primary-color) 18%, transparent); pointer-events: none; }
  .lbbox[hidden] { display: none; }
  .section > h2::after { content: "▾"; margin-left: 6px; display: inline-block; transition: transform .15s; }
  .nofold .section > h2 { cursor: auto; user-select: auto; }
  .nofold .section > h2::after { content: none; }
  .section.folded > h2::after { transform: rotate(-90deg); }
  .section.folded > :not(h2) { display: none; }
  ha-card { padding: 16px; }
  .subhead { font-size: .85em; font-weight: 600; margin: 14px 0 8px; color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: .04em; }
  h2 { font-size: 1em; font-weight: 600; margin: 0 0 8px; color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: .04em; }
  .section + .section, .section + .side, .side + .section { margin-top: 18px; }
  .part + .part > .section:first-child, .part + .part > .side:first-child { margin-top: 18px; }
  .side { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(300px, 100%), 1fr)); gap: 18px 28px; align-items: start; }
  .side > .section { margin-top: 0; min-width: 0; }
  .grid { display: grid; grid-template-columns: repeat(__COLS__, minmax(0, 1fr)); gap: 12px; }
  .tile { border-radius: 18px; overflow: hidden; background: var(--secondary-background-color); border: 2px solid transparent; display: flex; flex-direction: column; }
  .tile.is-live { border-color: var(--success-color, #43a047); }
  .tile.is-missing { border-color: var(--error-color, #db4437); }
  .pic { position: relative; height: __PH__px; cursor: pointer; }
  .pic img, .nophoto { display: block; width: 100%; height: 100%; object-fit: cover; }
  .nophoto { display: flex; align-items: center; justify-content: center; color: var(--disabled-text-color);
    background: linear-gradient(135deg, var(--secondary-background-color), var(--card-background-color)); }
  .over { position: absolute; left: 0; right: 0; bottom: 0; padding: 22px 12px 8px; color: var(--primary-text-color);
    background: linear-gradient(to top, color-mix(in srgb, var(--primary-background-color) 80%, transparent), transparent); }
  .nophoto + .over { color: var(--primary-text-color); background: none; }
  .nophoto canvas.sprite { width: 82%; height: 82%; object-fit: contain; image-rendering: pixelated; margin-bottom: 14%; }
  canvas.wsprite { width: 40px; height: 40px; image-rendering: pixelated; vertical-align: middle; margin-right: 6px; }
  .over .name { font-weight: 600; font-size: 1.1em; }
  .over .status { font-size: .85em; opacity: .9; color: inherit; }
  .over .live { color: var(--success-color); }
  .badges { position: absolute; top: 8px; left: 8px; right: 8px; display: flex; flex-wrap: wrap; gap: 4px; }
  .badge { font-size: .72em; padding: 2px 8px; border-radius: 999px; background: color-mix(in srgb, var(--primary-background-color) 75%, transparent); color: var(--primary-text-color); }
  .badge.dim { opacity: .8; }
  .badge.alarm { background: var(--error-color, #db4437); color: var(--text-primary-color, #fff); font-weight: 600; }
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
  .rev input.typed { flex: 1; min-width: 0; font: inherit; font-size: .85em; padding: 4px 10px; min-height: 32px; border-radius: 999px; border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); color-scheme: light dark; }
  .thumb { width: 100%; aspect-ratio: 4 / 3; object-fit: cover; border-radius: 10px; background: var(--card-background-color); cursor: zoom-in; display: block; }
  .lightbox { position: fixed; inset: 0; z-index: 10; background: color-mix(in srgb, var(--primary-background-color) 92%, transparent); display: flex; align-items: center; justify-content: center; overflow: hidden; }
  .lbimg { max-width: 96vw; max-height: 92vh; border-radius: 8px; cursor: zoom-in; touch-action: none; user-select: none; -webkit-user-select: none; transform-origin: center; }
  .lbhint { position: absolute; bottom: 12px; left: 50%; transform: translateX(-50%); padding: 4px 12px; border-radius: 999px; background: var(--secondary-background-color); color: var(--primary-text-color); font-size: .8em; pointer-events: none; white-space: nowrap; }
  .lbclose { position: absolute; top: 12px; right: 14px; font-size: 22px; width: 40px; height: 40px; border-radius: 50%; border: none; background: var(--secondary-background-color); color: var(--primary-text-color); cursor: pointer; }
  .thumb.broken { opacity: .3; }
  .acts { display: flex; gap: 6px; align-items: center; margin-top: 4px; flex-wrap: wrap; }
  input[data-folder] { flex: 1; min-width: 180px; font: inherit; padding: 6px 12px; border-radius: 999px; border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); }
  select { font: inherit; padding: 4px 8px; border-radius: 999px; border: 1px solid var(--divider-color); background: transparent; color: var(--primary-text-color); color-scheme: light dark; min-height: 32px; }
  select option { background-color: var(--primary-background-color, #111); color: var(--primary-text-color); }
  @container (max-width: 500px) { .grid { grid-template-columns: repeat(__SMALLCOLS__, minmax(0, 1fr)); } }
`;

if (!customElements.get("neighborhood-watch-card")) customElements.define("neighborhood-watch-card", NeighborhoodWatchCard);
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "neighborhood-watch-card")) window.customCards.push({
  type: "neighborhood-watch-card",
  name: "Neighborhood Watch",
  description: "Your animals, wildlife, last night and recent sightings, with Right / Not sure / Other buttons.",
  preview: true,
  documentationURL: "https://github.com/b2dmx/neighborhood-watch",
});

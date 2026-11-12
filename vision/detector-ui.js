/*
 * What the Detector tab (Home Assistant) and the vision service's own page both say about
 * the detector's training: one copy of the wording, so the two can't drift apart.
 * vision/detector-ui.js is the same file (a test checks it): change one, copy it to the other.
 */
export const DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
export const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : "");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
// "Every day", "Weekdays", or the days themselves.
export const dayList = (days) =>
  days.length === 7 ? "every day"
  : days.length === 5 && !days.includes("saturday") && !days.includes("sunday") ? "weekdays"
  : days.map((d) => cap(d).slice(0, 3)).join(", ");
export const hourName = (h) => new Date(2000, 0, 1, h).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
export const devName = (d) => ({ GPU: "Intel GPU", NPU: "NPU", CPU: "CPU", xpu: "GPU", cpu: "CPU" })[d] || d || "—";
// Training stages (vision/train.py) in plain words.
const STAGES = { starting: "Starting", labelling: "Marking animals in the photos", training: "Training" };
export const stageName = (stage) => STAGES[stage] || cap(stage || "");
// Before the first round, training waits for this many answered animal photos (the Photos for
// the first round setting; this is its default, for a service that doesn't send it). A
// stopped or failed round doesn't count as the first.
export const MIN_FIRST = 40;
export const firstRound = (rounds) => !(rounds || []).some((r) => r.confirmed != null && !r.error && !r.stopped);

// The line under Training: what it's doing now, or how the last round went and when the next is.
// outdated: a service too old to send its history, so whether a round has run isn't known.
export function stageText(t, rounds, outdated = false) {
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
  const first = firstRound(rounds) && !outdated;
  const need = first
    ? `there are ${s?.min_first ?? MIN_FIRST} answered animal photos`
    : `${s?.min_new} new photos are answered`;
  const so = t.answered_new != null && !first ? ` (${t.answered_new} so far)` : "";
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

// How far the running round is (0 to 1), or null when there's nothing to show a bar for.
export function progress(t) {
  const st = t.status || {};
  if (t.running && st.stage === "training" && st.epochs) return st.epoch / st.epochs;
  if (t.running && st.stage === "labelling" && st.total) return st.done / st.total;
  return null;
}

// Past rounds: the rule a new model is held to, the table's headings and its rows.
export const PROMOTION_RULE =
  "A new model replaces the current one when it names more animals right without missing more, or when it misses fewer animals and makes at least 10% fewer mistakes in all.";
export const HISTORY_HEAD =
  "<tr><th>Round</th><th>Date</th><th>Ran on</th><th>Time</th><th>Named right</th><th>Missed animals</th><th>False alarms</th><th>Result</th></tr>";
// quiet: the page's class for muted text (the empty row).
export function historyRows(rounds, quiet = "muted") {
  if (!rounds || !rounds.length) return `<tr><td colspan="8" class="${quiet}">No rounds yet.</td></tr>`;
  // up: more is better (named right); otherwise fewer is (misses, false alarms).
  const cell = (b, a, key, up = false) => {
    const x = b?.[key], y = a?.[key];
    if (y === undefined) return esc(x ?? "—");
    const better = up ? y > x : y < x, worse = up ? y < x : y > x;
    return `${esc(x)} → <span class="${better ? "better" : worse ? "worse" : ""}">${esc(y)}</span>`;
  };
  return rounds.slice().reverse().map((r) => {
    const result = r.error ? `<span class="worse">Failed</span>` : r.stopped ? "Stopped"
      : r.promoted ? (r.undone ? "Undone" : `<span class="better">New model</span>`) : "Kept the old one";
    const date = r.time ? new Date(r.time).toLocaleDateString([], { dateStyle: "medium" }) : "—";
    const mins = r.minutes != null ? `${Math.round(r.minutes)} min` : "—";
    return `<tr><td>${esc(r.round)}</td><td>${date}</td><td>${esc(devName(r.device))}</td><td>${mins}</td>` +
      `<td>${cell(r.before, r.after, "named_right", true)}</td><td>${cell(r.before, r.after, "misses")}</td><td>${cell(r.before, r.after, "false_alarms")}</td><td>${result}</td></tr>`;
  }).join("");
}

"""Pure decision logic: no Home Assistant imports, so it can be tested on its own.

Everything here is a straight port of the rules the automation version proved out:
answer tidying, when a second opinion runs, how two answers become one verdict, what
"counts", cooldowns, the night window, the prompt and the night summary.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
import re

HIGH = "high"
MEDIUM = "medium"
LOW = "low"
LEVELS = (HIGH, MEDIUM, LOW)

PATTERNS = ("blotchy", "uniform", "striped", "na")
BASES = ("white", "grey", "black", "na")

UNKNOWN_CAT = "Unknown Cat"
NOTHING = "Nothing"
UNSURE_NOTHING = "None|low|na|na|"

DEFAULT_WILDLIFE = (
    "Squirrel",
    "Chipmunk",
    "Opossum",
    "Raccoon",
    "Groundhog",
    "Fox",
    "Coyote",
    "Dog",
    "Chicken",
    "Bird",
    "Deer",
)

# Not visitors worth remembering: these never make a camera's "last real animal" (which
# picks that camera's cooldown) and never show as a camera's current animal.
NOT_VISITORS = frozenset(
    {
        "nothing",
        "none",
        "bug",
        "spider",
        "insect",
        "moth",
        "web",
        "spider web",
        "cobweb",
        "leaf",
        "leaves",
        "shadow",
        "light",
        "reflection",
        "headlights",
        "rain",
        "snow",
        "plant",
        "grass",
        "branch",
        "flag",
        "car",
        "vehicle",
        "truck",
        "trash can",
        "groundhog",
        "bird",
        "deer",
        "squirrel",
        "chipmunk",
        "chicken",
    }
)

# --- answers ----------------------------------------------------------------------


def is_unknown(name: str) -> bool:
    """'Unknown Cat', 'Unknown Dog': an animal of a known kind that matches none of yours."""
    n = name.strip().lower()
    return n.startswith("unknown ") and len(n) > len("unknown ")


def unknown_kind(name: str) -> str:
    return name.strip().split(" ", 1)[1].lower() if is_unknown(name) else ""


def tidy(raw: str | None) -> str:
    """Clean a model reply into `Name|confidence|pattern|base|description`.

    Strips markdown (``` ** ` ") and a leading label such as "Answer:". The first line
    holding a pipe wins. A reply with no pipe at all is an unsure "nothing". An empty
    reply stays empty (no answer).
    """
    text = str(raw or "")
    for junk in ("```", "**", "`", '"'):
        text = text.replace(junk, "")
    text = text.strip()
    if not text:
        return ""
    rows = [row.strip() for row in text.split("\n") if "|" in row]
    row = (rows[0] if rows else text).strip()
    parts = row.split("|")
    if len(parts) < 2:
        return UNSURE_NOTHING
    name = parts[0].split(":")[-1].strip()
    return "|".join([name, *parts[1:]]).strip()


@dataclass(frozen=True)
class Answer:
    name: str  # lower case
    confidence: str  # as given, lower case (may be junk)
    pattern: str
    base: str
    description: str

    @classmethod
    def parse(cls, tidied: str) -> Answer | None:
        if not tidied:
            return None
        parts = [p.strip() for p in tidied.split("|", 4)]
        parts += [""] * (5 - len(parts))
        return cls(
            name=parts[0].lower(),
            confidence=parts[1].lower() or LOW,
            pattern=parts[2].lower() or "na",
            base=parts[3].lower() or "na",
            description=parts[4],
        )


@dataclass(frozen=True)
class AnimalRule:
    """What the decision needs to know about one known animal."""

    name: str
    priority: bool = False  # never miss, never falsely confirm (Luna)
    always_strict: bool = False  # counts only when both models agree at high (Pepper)
    pattern: str = "na"  # expected coat pattern; checked for priority animals
    base: str = "na"  # expected dominant tone; checked for priority animals
    alert: bool = True  # alert when it is seen
    species: str = "cat"

    @property
    def key(self) -> str:
        return self.name.lower()


def needs_second_opinion(
    first: str, priority: Iterable[str], strict: Iterable[str]
) -> bool:
    """A second model looks when the first named a priority, strict or unknown cat,
    was unsure, said an unsure "nothing", or nobody answered."""
    answer = Answer.parse(first)
    if answer is None:
        return True
    special = {*priority, *strict}
    return (
        answer.name in special
        or is_unknown(answer.name)
        or answer.confidence not in (HIGH, MEDIUM)
        or (answer.name == "none" and answer.confidence != HIGH)
    )


@dataclass
class Verdict:
    animal: str
    confidence: str
    pattern: str
    description: str
    priority_candidate: str  # the priority animal either model named, or ""
    first_look_said: str  # tidied, lower case
    second_opinion_said: str  # tidied

    @property
    def is_nothing(self) -> bool:
        return self.animal == NOTHING


def _level(value: str) -> str:
    value = re.sub(r"[^a-z]", "", value.lower())
    return value if value in LEVELS else LOW


def _display(name: str, rules: Mapping[str, AnimalRule]) -> str:
    rule = rules.get(name.lower())
    if rule:
        return rule.name
    return name.strip().title()


def decide(
    first: str,
    second: str,
    rules: Mapping[str, AnimalRule],
    strict: Iterable[str] = (),
    unconfirmed: bool = False,
) -> Verdict:
    """Turn the tidied first look and second opinion into one verdict.

    `rules` maps lower-case names to known animals; `strict` holds the lower-case names
    that need both models at high (always-strict animals plus recently absent ones).
    `unconfirmed` = the first look came from a backup model and no second opinion could
    be had: its answer is capped at medium.
    """
    strict = {s.lower() for s in strict} | {k for k, r in rules.items() if r.always_strict}
    first_said = first.lower()
    used = second or first or UNSURE_NOTHING
    final = Answer.parse(used) or Answer.parse(UNSURE_NOTHING)
    look = Answer.parse(first_said)
    second_ran = bool(second)

    name = final.name
    rule = rules.get(name)
    confidence = _level(final.confidence)

    # Priority double-check: both models must name it AND see its coat.
    priority_level = None
    if rule and rule.priority:

        def coat_ok(answer: Answer | None) -> bool:
            if answer is None:
                return False
            pattern_ok = rule.pattern == "na" or answer.pattern == rule.pattern
            base_ok = rule.base == "na" or answer.base == rule.base
            return pattern_ok and base_ok

        first_level = _level(look.confidence) if look else LOW
        if not second_ran:
            priority_level = (
                MEDIUM
                if look and look.name == name and coat_ok(look) and first_level in (HIGH, MEDIUM)
                else LOW
            )
        elif not look or look.name != name or not coat_ok(look) or not coat_ok(final):
            priority_level = LOW
        elif first_level == HIGH and confidence == HIGH:
            priority_level = HIGH
        elif first_level in (HIGH, MEDIUM) and confidence in (HIGH, MEDIUM):
            priority_level = MEDIUM
        else:
            priority_level = LOW

    # Strict double-check: both models name it, both at high.
    strict_level = None
    if name in strict and not (rule and rule.priority):
        first_level = _level(look.confidence) if look else LOW
        strict_level = (
            HIGH
            if second_ran and look and look.name == name and first_level == HIGH and confidence == HIGH
            else LOW
        )

    first_name = look.name if look else ""
    disagree = first_name not in ("", "none") and not is_unknown(first_name) and first_name != name
    if disagree:
        confidence = LOW  # the two models disagree
    elif priority_level is not None:
        confidence = priority_level
    elif strict_level is not None:
        confidence = strict_level
    elif first_name == "none" and name not in ("none", "") and confidence == HIGH:
        confidence = MEDIUM  # the first look saw nothing
    if unconfirmed and not second_ran and confidence == HIGH:
        confidence = MEDIUM  # a backup model alone is never sure

    candidate = ""
    for option in (first_name, name):
        option_rule = rules.get(option)
        if option_rule and option_rule.priority:
            candidate = option_rule.name
            break

    display = _display(name, rules)
    pattern = final.pattern
    if disagree and candidate and candidate.lower() != name:
        # A split vote involving a priority animal stays that animal (at low), so it is
        # never hidden behind another name.
        display = candidate
        pattern = look.pattern if look and look.name == candidate.lower() else pattern
    lowered = display.lower()
    if not display or "person" in lowered or "human" in lowered or "none" in lowered:
        display = NOTHING

    description = ""
    if is_unknown(display) and final.description:
        description = final.description[:90]

    return Verdict(
        animal=display,
        confidence=confidence,
        pattern=pattern if pattern in PATTERNS else "na",
        description=description,
        priority_candidate=candidate,
        first_look_said=first_said,
        second_opinion_said=second,
    )


def names_animal(tidied: str) -> bool:
    """The answer names an animal worth double-checking (not nothing, a bug or harmless
    small wildlife)."""
    answer = Answer.parse(tidied)
    return bool(answer and answer.name and answer.name not in NOT_VISITORS)


# --- what counts ------------------------------------------------------------------


def counts(verdict: Verdict, rules: Mapping[str, AnimalRule]) -> bool:
    """Known animals count at medium or high (always-strict ones at high only);
    everything else always counts."""
    rule = rules.get(verdict.animal.lower())
    if rule is None:
        return True
    if rule.always_strict:
        return verdict.confidence == HIGH
    return verdict.confidence in (HIGH, MEDIUM)


def is_visitor(verdict: Verdict, rules: Mapping[str, AnimalRule], wildlife: Iterable[str]) -> bool:
    """A real animal worth showing as a camera's current animal."""
    if not counts(verdict, rules):
        return False
    name = verdict.animal.lower()
    return (
        name in rules
        or is_unknown(verdict.animal)
        or name in {w.lower() for w in wildlife}
    )


def is_noteworthy(verdict: Verdict, rules: Mapping[str, AnimalRule]) -> bool:
    """Becomes the camera's "last real animal" (which sets its cooldown).

    A low-confidence priority animal does not: it must not stretch the cooldown.
    """
    name = verdict.animal.lower()
    if name in NOT_VISITORS:
        return False
    rule = rules.get(name)
    return not (rule and rule.priority and verdict.confidence == LOW)


def is_cat_for_log(verdict: Verdict, rules: Mapping[str, AnimalRule]) -> bool:
    """Goes in the night summary: known animals that count, and every Unknown Cat."""
    return is_unknown(verdict.animal) or (
        verdict.animal.lower() in rules and counts(verdict, rules)
    )


def recently_absent(last_seen: datetime | None, now: datetime, days: float) -> bool:
    """Not seen (counted) for `days`. An animal never seen is absent until both models
    confirm it once (give a known last-seen when adding it to carry history over)."""
    if last_seen is None:
        return True
    return now - last_seen > timedelta(days=days)


# --- cooldowns and the night window -----------------------------------------------


@dataclass(frozen=True)
class Cooldown:
    minutes: float = 3
    after_priority_minutes: float = 0  # 0 = off
    missing_mode_minutes: float = 0  # 0 = off


def cooldown_window(rule: Cooldown, last_was_priority: bool, missing_mode: bool) -> timedelta:
    if missing_mode and rule.missing_mode_minutes:
        return timedelta(minutes=rule.missing_mode_minutes)
    if last_was_priority and rule.after_priority_minutes:
        return timedelta(minutes=rule.after_priority_minutes)
    return timedelta(minutes=rule.minutes)


def may_check(
    now: datetime,
    last_check: datetime | None,
    window: timedelta,
    reopeners: Iterable[datetime | None] = (),
) -> bool:
    """Outside the cooldown, or a reopening camera was checked since this one was."""
    if last_check is None or now - last_check >= window:
        return True
    return any(t is not None and t > last_check for t in reopeners)


def in_time_window(now: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= now < end
    return now >= start or now < end


def is_night(now: time, start: time, end: time, dark: bool, use_dark: bool = True) -> bool:
    """Whichever is wider: the clock window, or sun-down."""
    return in_time_window(now, start, end) or (use_dark and dark)


# --- prompt -----------------------------------------------------------------------

DEFAULT_INSTRUCTIONS = (
    "Identify what is in this image from the {location} camera. It may be a normal colour image or a "
    "night-vision/infrared one. "
    "Return EXACTLY five values separated by pipe characters: Name|confidence|pattern|base|description. "
    "Never put a pipe character inside a value. "
    "confidence is high, medium, or low - use low if the subject is small, blurry, partially visible, or you are unsure. "
    "pattern is the COAT PATTERN - tonal VARIATION across the body, not how bright it is: "
    "'blotchy' = distinct lighter and darker regions with visible boundaries (like a calico or a patched dog), "
    "'uniform' = one dominant tone (a few small markings still count as uniform), 'striped' = regular stripes, "
    "'na' = no animal or coat not visible. "
    "base is the DOMINANT tone covering MORE THAN HALF of the visible body: 'white', 'grey', 'black', or 'na'. "
    "Small white paws, a chest or a bib do NOT make the base white. "
    "description is EMPTY unless the Name starts with Unknown (see the UNKNOWN ANIMAL RULE). "
    "Judge only from what you can see. "
    "If you see a person, or the frame is empty or shows only plants, return None|high|na|na|. "
    "Do NOT force a name onto something that isn't clearly that animal. "
    "If it is an insect, spider, web, bird, shadow, leaf, vehicle or any object, NAME WHAT IT ACTUALLY IS "
    "(e.g. 'Spider', 'Bug', 'Bird', 'Leaf', 'Shadow') and use na for pattern and base. "
    "If the subject is ON or right next to the lens (fills most of the frame, no ground visible), it is almost "
    "certainly an insect - return Bug|high|na|na|. "
    "THE NAMED ANIMALS (tell them apart by their markings first; in infrared, colour is unreliable): {animals} "
    "OVEREXPOSURE RULE: an animal blown out into a featureless white blob by the infrared light cannot be judged "
    "(return 'uniform' and 'low'). Also return 'low' if the animal is motion-blurred or cut off by the frame edge. "
    "{extra_rules} "
    "{absent}"
    "UNKNOWN ANIMAL RULE: use {unknown_kinds} ONLY if you clearly see that kind of animal matching none of the "
    "named animals. Then fill the description (max 12 words, no pipe character): size and build, fur length, "
    "pattern and markings. Name a colour only if the image clearly shows true colour; otherwise describe the tone "
    "(light-toned, mid-toned, dark-toned). Example: Unknown Cat|medium|striped|grey|stocky light-toned tabby with white paws. "
    "Listed wildlife (name these by kind): {wildlife}. For anything else, name the actual animal or object you see."
)

REQUIRED_PLACEHOLDERS = ("{animals}",)


@dataclass(frozen=True)
class AnimalProfile:
    name: str
    description: str = ""
    tips: str = ""
    has_reference: bool = False


def _sentence(text: str) -> str:
    text = " ".join(text.split())
    if text and text[-1] not in ".!?":
        text += "."
    return text


def build_prompt(
    template: str,
    *,
    location: str,
    animals: Sequence[AnimalProfile],
    absent: Sequence[str] = (),
    visiting: Sequence[str] = (),
    wildlife: Sequence[str] = DEFAULT_WILDLIFE,
    extra_rules: str = "",
    references: Sequence[str] = (),
    unknown_kinds: Sequence[str] = ("cat", "dog"),
) -> str:
    """Fill the instructions template. Plain replace, so braces elsewhere are safe."""
    described = " ".join(
        _sentence(f"{a.name} - {a.description}".strip(" -")) + (f" {_sentence(a.tips)}" if a.tips else "")
        for a in animals
    )
    absent_line = ""
    if absent:
        prefer = (
            f"prefer a cat that has been visiting ({', '.join(visiting)})" if visiting else "prefer"
        )
        absent_line = (
            f"RECENTLY ABSENT CATS: {', '.join(absent)} - these have NOT been seen here lately. "
            "Only name one of them with very clear, unmistakable evidence; if unsure, "
            f"{prefer + ' or ' if visiting else ''}return low confidence. "
        )
    text = (
        template.replace("{location}", location)
        .replace("{animals}", described)
        .replace("{extra_rules}", _sentence(extra_rules) if extra_rules.strip() else "")
        .replace("{absent}", absent_line)
        .replace("{wildlife}", ", ".join(wildlife))
        .replace("{unknown_kinds}", " or ".join(f"'Unknown {k.title()}'" for k in unknown_kinds))
    )
    if references:
        text = (
            "The FIRST image is the camera frame to identify. The other images are reference "
            f"photos of known animals, in this order: {', '.join(references)}. Use them only to "
            "compare body shape, fur length and markings; never answer about a reference photo. "
            + text
        )
    return re.sub(r" {2,}", " ", text).strip()


# --- night summary ----------------------------------------------------------------


@dataclass
class Summary:
    text: str
    lines: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    places: dict[str, list[str]] = field(default_factory=dict)


def summarize(entries: Iterable[tuple[str, str]], order: Sequence[str], limit: int = 255) -> Summary:
    """`entries` = (animal, camera name) for each logged cat sighting, any order.

    Known animals in roster order, each with its count and the cameras (first seen first);
    Unknown Cat last as a count only.
    """
    counts: dict[str, int] = {}
    places: dict[str, list[str]] = {}
    for animal, where in entries:
        counts[animal] = counts.get(animal, 0) + 1
        seen = places.setdefault(animal, [])
        if where and where not in seen:
            seen.append(where)
    lines = []
    for name in order:
        if counts.get(name):
            n = counts[name]
            lines.append(f"{name} — {n} sighting{'s' if n > 1 else ''} ({', '.join(places[name])})")
    for name in sorted(k for k in counts if is_unknown(k)):
        n = counts[name]
        lines.append(f"{n} {name} sighting{'s' if n > 1 else ''}")
    text = "\n".join(lines) if lines else "No sightings last night."
    return Summary(text=text[:limit], lines=lines, counts=counts, places=places)


def clock(t: datetime) -> str:
    """9:05 PM — portable (no %-I)."""
    hour = t.hour % 12 or 12
    return f"{hour}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


# --- alerts -----------------------------------------------------------------------

NORMAL = "normal"
CRITICAL = "critical"

ALERT_NEVER = "never"
ALERT_HOUSE = "house"
ALERT_CAMERAS = "cameras"
ALERT_ANY = "any"
ALERT_SCOPES = (ALERT_NEVER, ALERT_HOUSE, ALERT_CAMERAS, ALERT_ANY)

AUDIENCE_ALL = "all"
AUDIENCE_MISSING = "missing"  # people who get missing-mode alerts
AUDIENCE_NOT_MISSING = "not_missing"  # everyone else (they already had the critical one)


@dataclass(frozen=True)
class SpeciesRule:
    """Wildlife told apart by kind only (raccoon, opossum): which alerts it gets."""

    name: str
    alert: str = ALERT_NEVER
    cameras: tuple[str, ...] = ()
    critical: bool = False  # its critical-alerts switch is on

    @property
    def key(self) -> str:
        return self.name.lower()

    def alerts_at(self, camera: CameraRule) -> bool:
        if self.alert == ALERT_ANY:
            return True
        if self.alert == ALERT_HOUSE:
            return camera.house
        if self.alert == ALERT_CAMERAS:
            return camera.slug in self.cameras
        return False


@dataclass(frozen=True)
class CameraRule:
    slug: str
    name: str
    house: bool = False  # close to the house: alert on anything that isn't harmless
    unsure_alerts: bool = False  # alert on motion the AI could not identify
    critical: bool = False  # its critical-alerts switch is on


@dataclass(frozen=True)
class Alert:
    kind: str  # missing | priority | animal | wildlife | motion
    title: str
    message: str
    tag: str
    urgency: str
    audience: str = AUDIENCE_ALL
    volume: float | None = None  # critical volume override (missing mode: full)


def plan_alerts(
    v: Verdict,
    camera: CameraRule,
    rules: Mapping[str, AnimalRule],
    species: Mapping[str, SpeciesRule],
    missing: Iterable[str],
    when: str,
    test: bool = False,
    unknown_alerts: bool = True,
) -> list[Alert]:
    """Who hears about a sighting, and how loudly. A straight port of the Alerts
    automation, generalised: priority animals, individuals, wildlife, unsure motion."""
    prefix = "TEST · " if test else ""
    where = camera.name
    conf = v.confidence
    possible = conf != HIGH
    camera_urgency = CRITICAL if camera.critical else NORMAL
    missing = {m.lower() for m in missing}
    alerts: list[Alert] = []

    def tentative(name: str) -> tuple[str, str]:
        return (
            f"{prefix}Possible {name} — {where}",
            f"AI thinks this might be {name} at {where}, {when}.\nConfidence: {conf}",
        )

    def detected(name: str, description: str = "") -> tuple[str, str]:
        looks = f"\nLooks like: {description}" if description else ""
        lead = f"Possible {name}" if possible else f"{name} detected"
        return (
            f"{prefix}{'Possible ' if possible else ''}{name} — {where}",
            f"{lead} at {where}, {when}.{looks}\nConfidence: {conf}",
        )

    # Missing mode: ANY possible sighting of the missing animal, at any confidence.
    candidate = v.priority_candidate
    missing_sent = bool(candidate and candidate.lower() in missing)
    if missing_sent:
        title, message = tentative(candidate)
        alerts.append(
            Alert("missing", title, message, f"priority_missing_{camera.slug}", CRITICAL, AUDIENCE_MISSING, 1.0)
        )

    key = v.animal.lower()
    rule = rules.get(key)
    if rule and rule.priority:
        if conf in (HIGH, MEDIUM) and rule.alert:
            title, message = tentative(rule.name)
            audience = AUDIENCE_NOT_MISSING if missing_sent else AUDIENCE_ALL
            alerts.append(Alert("priority", title, message, f"priority_{camera.slug}", NORMAL, audience))
        return alerts

    if rule or (is_unknown(v.animal) and unknown_alerts):
        if rule and not rule.alert:
            return alerts
        if camera.house:
            title, message = detected(v.animal, v.description)
            alerts.append(Alert("animal", title, message, f"animal_{camera.slug}", camera_urgency))
        else:
            kind = rule.species if rule else (unknown_kind(v.animal) or "animal")
            coat = f"A {v.pattern}-coated {kind}" if v.pattern != "na" else f"A {kind}"
            looks = f"\nLooks like: {v.description}" if v.description else ""
            alerts.append(
                Alert(
                    "animal",
                    f"{prefix}{kind.title()} Sighting — {where}",
                    f"{coat} was seen at {where}, {when}.\nBest guess: {v.animal} (confidence: {conf}){looks}",
                    f"animal_{camera.slug}",
                    NORMAL,
                )
            )
        return alerts

    kind = species.get(key)
    if kind:
        if kind.alerts_at(camera):
            urgency = CRITICAL if kind.critical or camera.critical else NORMAL
            title, message = detected(kind.name)
            alerts.append(Alert("wildlife", title, message, f"wildlife_{camera.slug}", urgency))
        return alerts

    if v.animal == NOTHING:
        if camera.unsure_alerts and conf != HIGH:
            alerts.append(
                Alert(
                    "motion",
                    f"{prefix}Motion — {where}",
                    f"Unidentified motion at {where}, {when}.\nConfidence: {conf}",
                    f"motion_{camera.slug}",
                    camera_urgency,
                )
            )
        return alerts

    if key in NOT_VISITORS or is_unknown(v.animal):
        return alerts
    # Something not set up as wildlife (a fox, a dog): close to the house it matters.
    if camera.house:
        title, message = detected(v.animal, v.description)
        alerts.append(Alert("animal", title, message, f"animal_{camera.slug}", camera_urgency))
    return alerts

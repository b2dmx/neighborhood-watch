"""The watch engine: cameras → snapshot → AI chain → verdict → tracking and the sighting event.

One check runs at a time (the AI quotas are per model and per day, and the lock keeps the
order of sightings). A camera already waiting its turn is not queued twice; its cooldown is
judged when its turn comes, as the automation version did.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import partial
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

from homeassistant.components import camera as camera_component
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.storage import Store
from homeassistant.helpers.sun import is_up
from homeassistant.util import dt as dt_util, slugify

from . import dataset, logic
from .const import (
    ACTION_PREFIX,
    AI_TIMEOUT,
    BUILTIN_KEYS,
    CONF_ALERT_AT,
    CONF_ALERTS,
    CONF_BUTTONS,
    CONF_CRITICAL_AT,
    CONF_CRITICAL_EXCEPT,
    CONF_CRITICAL_SOUND,
    CONF_CRITICAL_VOLUME,
    CONF_DATASET,
    CONF_DATASET_DIR,
    CONF_DATASET_GB,
    CONF_DETECTOR_MIN,
    CONF_DETECTOR_MODE,
    CONF_DETECTOR_URL,
    DETECTOR_SKIP,
    CONF_DETER_LIGHTS,
    CONF_DETER_MINUTES,
    CONF_DETER_SOUND,
    CONF_AI_NOTES,
    CONF_ALWAYS,
    CONF_INTERVAL,
    CONF_IN_SUMMARY,
    CONF_MIN_CONFIDENCE,
    CONF_MISSING_ALERTS,
    CONF_MUTED,
    CONF_NOTIFY,
    CONF_PERSON,
    CONF_PEST,
    CONF_QUIET_END,
    CONF_QUIET_MODE,
    CONF_QUIET_START,
    CONF_QUIET_ZONES,
    EVENT_ACTION,
    QUIET_NONE,
    QUIET_NORMAL,
    VISIT_SLACK_MINUTES,
    SUBENTRY_RECIPIENT,
    SUBENTRY_SPECIES,
    COMPARISON_MAX,
    CONF_ABSENT_DAYS,
    CONF_ADDED,
    CONF_ALWAYS_STRICT,
    CONF_BASE,
    CONF_CAMERA,
    CONF_COOLDOWN,
    CONF_DESCRIPTION,
    CONF_EXTRA_RULES,
    CONF_FIRST_LOOK,
    CONF_INSTRUCTIONS,
    CONF_MAIN_END,
    CONF_MAIN_START,
    CONF_KNOWN_LAST_SEEN,
    CONF_KNOWN_LOCATION,
    CONF_MISSING_COOLDOWN,
    CONF_MODE,
    CONF_NAME,
    CONF_NIGHT_END,
    CONF_NIGHT_START,
    CONF_ON_CAMERA_MINUTES,
    CONF_PAID,
    CONF_PATTERN,
    CONF_PRIORITY,
    CONF_PRIORITY_COOLDOWN,
    CONF_PUBLIC_URL,
    CONF_REFERENCE_PHOTOS,
    CONF_REOPENED_BY,
    CONF_SECOND_OPINION,
    CONF_SKIP_WHEN,
    CONF_SLUG,
    CONF_SPECIES,
    CONF_SUMMARY_TIME,
    CONF_TIPS,
    CONF_TRIGGERS,
    CONF_USE_DARK,
    CONF_WILDLIFE,
    DEFAULT_OPTIONS,
    DOMAIN,
    EVENT_SIGHTING,
    MEDIA_SUBDIR,
    MODE_LIVE,
    MODE_SHADOW_INDEPENDENT,
    MODE_SHADOW_REUSE,
    PHOTO_DIR,
    QUOTA_TZ,
    SIGHTING_DAYS,
    SIGHTING_MAX,
    SNAPSHOT_TIMEOUT,
    STORAGE_VERSION,
    SUBENTRY_ANIMAL,
    SUBENTRY_CAMERA,
    TASK_NAME,
    WWW_SUBDIR,
)

_LOGGER = logging.getLogger(__name__)

BUSY_RETRY_SECONDS = 3
PAIR_WINDOW = timedelta(minutes=3)


def _is_busy(err: BaseException) -> bool:
    text = str(err).lower()
    return "503" in text or "high demand" in text or "overloaded" in text  # a live check and ours count as the same sighting
TICK = timedelta(seconds=30)


@dataclass
class CameraConfig:
    subentry_id: str
    name: str
    slug: str
    camera: str
    triggers: list[str]
    skip_when: list[str]
    cooldown: logic.Cooldown
    reopened_by: list[str]
    critical_except: frozenset[str] = frozenset()  # lower-case names that never ring here
    ai_notes: str = ""
    interval: float = 0  # minutes; 0 = only when a sensor triggers
    deter_lights: list[str] = field(default_factory=list)
    deter_minutes: float = 2
    deter_sound: dict[str, Any] | None = None

    @property
    def switch_key(self) -> str:
        return f"camera:{self.slug}"


@dataclass
class SpeciesConfig:
    """Wildlife told apart by kind only: a raccoon is a raccoon."""

    subentry_id: str
    name: str
    slug: str
    description: str
    alert_at: frozenset[str] | str
    critical_at: frozenset[str] | str
    pest: bool
    in_summary: bool
    kind: str = "species"

    @property
    def key(self) -> str:
        return self.name.lower()

    @property
    def switch_key(self) -> str:
        return f"species:{self.slug}"


@dataclass
class RecipientConfig:
    subentry_id: str
    name: str
    person: str | None
    notify: str
    quiet_zones: list[str]
    missing_alerts: bool
    sound: str
    volume: float
    muted: frozenset[str] = frozenset()  # lower-case names
    quiet_mode: str = QUIET_NORMAL
    quiet_start: str | None = None
    quiet_end: str | None = None

    @property
    def switch_key(self) -> str:
        return f"person:{self.subentry_id}"


@dataclass
class AnimalConfig:
    subentry_id: str
    name: str
    slug: str
    species: str
    description: str
    tips: str
    pattern: str
    base: str
    priority: bool
    always_strict: bool
    added: datetime | None
    known_last_seen: datetime | None = None
    known_location: str | None = None
    alert_at: frozenset[str] | str = logic.EVERY
    critical_at: frozenset[str] | str = frozenset()
    kind: str = "animal"

    @property
    def key(self) -> str:
        return self.name.lower()

    @property
    def switch_key(self) -> str:
        return f"animal:{self.slug}"

    @property
    def rule(self) -> logic.AnimalRule:
        return logic.AnimalRule(
            self.name,
            priority=self.priority,
            always_strict=self.always_strict,
            pattern=self.pattern,
            base=self.base,
            species=self.species,
        )


@dataclass
class Check:
    """One finished identification, ready to be recorded."""

    camera: CameraConfig
    verdict: logic.Verdict
    models: str
    frame: bytes | None
    at: datetime
    source: str = "own"  # own | reuse
    test: bool = False
    detector: dict[str, Any] | None = None


@dataclass
class Frame:
    content: bytes
    at: datetime


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = dt_util.parse_datetime(str(value))
    if parsed and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.UTC)
    return parsed


def _local_dt(value: Any) -> datetime | None:
    """A date-time picker value (local, no zone) or an ISO string, as UTC."""
    if not value:
        return None
    parsed = dt_util.parse_datetime(str(value))
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_utc(parsed)


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(content)
    tmp.replace(path)


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _rotate_and_write(path: Path, content: bytes) -> None:
    """Keep the previous photo so a wrong identification can be undone."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.replace(path.with_name(path.stem + ".prev" + path.suffix))
    _write(path, content)


def _restore_previous(path: Path) -> bool:
    prev = path.with_name(path.stem + ".prev" + path.suffix)
    if prev.exists():
        prev.replace(path)
        return True
    path.unlink(missing_ok=True)
    return False


class WatchEngine:
    """Owns the config, the persistent state and the pipeline for one entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.signal = f"{DOMAIN}_{entry.entry_id}_updated"
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self.data: dict[str, Any] = {}
        self.options: dict[str, Any] = {**DEFAULT_OPTIONS, **entry.options}
        self.cameras: dict[str, CameraConfig] = {}
        self.animals: dict[str, AnimalConfig] = {}  # by slug
        self.species: dict[str, SpeciesConfig] = {}  # by slug
        self.recipients: list[RecipientConfig] = []
        self._deterring: dict[str, Callable[[], None]] = {}
        self.frames: dict[str, Frame] = {}
        self._queued: set[str] = set()
        self.hub_device_id: str | None = None
        self._lock = asyncio.Lock()
        self._unsubs: list[Callable[[], None]] = []
        self._pending_live: dict[str, dict[str, Any]] = {}
        self._trigger_map: dict[str, list[str]] = {}
        self.references: dict[str, datetime] = {}
        self._load_config()

        self._media_key, self._media_root = media_root(hass)
        self._photo_root = Path(hass.config.path(PHOTO_DIR))
        self._www_root = Path(hass.config.path("www", WWW_SUBDIR))

    # --- config ------------------------------------------------------------------

    def _load_config(self) -> None:
        for subentry in self.entry.subentries.values():
            d = subentry.data
            if subentry.subentry_type == SUBENTRY_CAMERA:
                slug = d.get(CONF_SLUG) or slugify(subentry.title)
                self.cameras[slug] = CameraConfig(
                    subentry_id=subentry.subentry_id,
                    name=d.get(CONF_NAME) or subentry.title,
                    slug=slug,
                    camera=d[CONF_CAMERA],
                    triggers=list(d.get(CONF_TRIGGERS) or []),
                    skip_when=list(d.get(CONF_SKIP_WHEN) or []),
                    cooldown=logic.Cooldown(
                        minutes=float(d.get(CONF_COOLDOWN, 3)),
                        after_priority_minutes=float(d.get(CONF_PRIORITY_COOLDOWN, 0)),
                        missing_mode_minutes=float(d.get(CONF_MISSING_COOLDOWN, 0)),
                    ),
                    reopened_by=list(d.get(CONF_REOPENED_BY) or []),
                    critical_except=frozenset(n.lower() for n in d.get(CONF_CRITICAL_EXCEPT) or []),
                    interval=float(d.get(CONF_INTERVAL) or 0),
                    ai_notes=(d.get(CONF_AI_NOTES) or "").strip(),
                    deter_lights=list(d.get(CONF_DETER_LIGHTS) or []),
                    deter_minutes=float(d.get(CONF_DETER_MINUTES, 2)),
                    deter_sound=d.get(CONF_DETER_SOUND) or None,
                )
            elif subentry.subentry_type == SUBENTRY_ANIMAL:
                name = d.get(CONF_NAME) or subentry.title
                slug = d.get(CONF_SLUG) or slugify(name)
                self.animals[slug] = AnimalConfig(
                    subentry_id=subentry.subentry_id,
                    name=name,
                    slug=slug,
                    species=d.get(CONF_SPECIES, "cat"),
                    description=d.get(CONF_DESCRIPTION, ""),
                    tips=d.get(CONF_TIPS, ""),
                    pattern=d.get(CONF_PATTERN, "na"),
                    base=d.get(CONF_BASE, "na"),
                    priority=bool(d.get(CONF_PRIORITY)),
                    always_strict=bool(d.get(CONF_ALWAYS_STRICT)),
                    added=_dt(d.get(CONF_ADDED)),
                    known_last_seen=_local_dt(d.get(CONF_KNOWN_LAST_SEEN)),
                    known_location=d.get(CONF_KNOWN_LOCATION),
                    alert_at=logic.cameras(d.get(CONF_ALERT_AT, [logic.EVERY])),
                    critical_at=logic.cameras(d.get(CONF_CRITICAL_AT, [])),
                )
            elif subentry.subentry_type == SUBENTRY_SPECIES:
                name = d.get(CONF_NAME) or subentry.title
                slug = d.get(CONF_SLUG) or slugify(name)
                self.species[slug] = SpeciesConfig(
                    subentry_id=subentry.subentry_id,
                    name=name,
                    slug=slug,
                    description=d.get(CONF_DESCRIPTION, ""),
                    alert_at=logic.cameras(d.get(CONF_ALERT_AT, [logic.EVERY])),
                    critical_at=logic.cameras(d.get(CONF_CRITICAL_AT, [])),
                    pest=bool(d.get(CONF_PEST)),
                    in_summary=d.get(CONF_IN_SUMMARY, True),
                )
            elif subentry.subentry_type == SUBENTRY_RECIPIENT:
                self.recipients.append(
                    RecipientConfig(
                        subentry_id=subentry.subentry_id,
                        name=d.get(CONF_NAME) or subentry.title,
                        person=d.get(CONF_PERSON) or None,
                        notify=d[CONF_NOTIFY],
                        quiet_zones=list(d.get(CONF_QUIET_ZONES) or []),
                        missing_alerts=bool(d.get(CONF_MISSING_ALERTS)),
                        sound=d.get(CONF_CRITICAL_SOUND) or "default",
                        volume=float(d.get(CONF_CRITICAL_VOLUME, 0.7)),
                        muted=frozenset(n.lower() for n in d.get(CONF_MUTED) or []),
                        quiet_mode=d.get(CONF_QUIET_MODE) or QUIET_NORMAL,
                        quiet_start=d.get(CONF_QUIET_START) or None,
                        quiet_end=d.get(CONF_QUIET_END) or None,
                    )
                )

    @property
    def mode(self) -> str:
        return self.options.get(CONF_MODE, MODE_LIVE)

    @property
    def rules(self) -> dict[str, logic.AnimalRule]:
        return {a.key: a.rule for a in self.animals.values()}

    def animal_by_name(self, name: str) -> AnimalConfig | None:
        key = name.strip().lower()
        return next((a for a in self.animals.values() if a.key == key or a.slug == key), None)

    def species_by_name(self, name: str) -> SpeciesConfig | None:
        key = name.strip().lower()
        return next((s for s in self.species.values() if s.key == key or s.slug == key), None)

    def subject(self, name: str) -> AnimalConfig | SpeciesConfig | None:
        """A tracked animal: an individual or a wildlife kind."""
        return self.animal_by_name(name) or self.species_by_name(name)

    @property
    def wildlife(self) -> list[str]:
        names = [s.name for s in self.species.values()]
        extra = self.options.get(CONF_WILDLIFE) or list(logic.DEFAULT_WILDLIFE)
        return names + [w for w in extra if w.lower() not in {n.lower() for n in names}]

    def critical_on(self, key: str) -> bool:
        """A Critical alerts switch (camera, animal, wildlife or catch-all). All start off."""
        return bool(self.data.setdefault("critical", {}).get(key))

    async def async_set_critical(self, key: str, on: bool) -> None:
        self.data.setdefault("critical", {})[key] = on
        self._save()
        self._notify()

    def person_critical_on(self, person: RecipientConfig) -> bool:
        """A person's "gets critical alerts" switch; starts on."""
        return bool(self.data.setdefault("critical", {}).get(person.switch_key, True))

    def alerts_on(self, key: str) -> bool:
        """An animal's Alerts switch: off pauses its alerts without losing its cameras."""
        return bool(self.data.setdefault("alerts", {}).get(key, True))

    async def async_set_alerts(self, key: str, on: bool) -> None:
        self.data.setdefault("alerts", {})[key] = on
        self._save()
        self._notify()

    def camera_by_ref(self, ref: str) -> CameraConfig | None:
        ref = ref.strip()
        for cam in self.cameras.values():
            if ref in (cam.slug, cam.camera, cam.name) or ref.lower() == cam.name.lower():
                return cam
        return None

    # --- paths ----------------------------------------------------------------------

    def frame_path(self, slug: str) -> Path:
        return self._media_root / "frames" / f"{slug}.jpg"

    def frame_media_id(self, slug: str) -> str:
        return f"media-source://media_source/{self._media_key}/{MEDIA_SUBDIR}/frames/{slug}.jpg"

    def reference_path(self, animal: AnimalConfig) -> Path:
        return self._media_root / "reference" / f"{animal.slug}.jpg"

    def reference_media_id(self, animal: AnimalConfig) -> str:
        return f"media-source://media_source/{self._media_key}/{MEDIA_SUBDIR}/reference/{animal.slug}.jpg"

    def photo_path(self, animal: AnimalConfig | SpeciesConfig) -> Path:
        folder = "species" if animal.kind == "species" else "photos"
        return self._photo_root / folder / f"{animal.slug}.jpg"

    @property
    def dataset_root(self) -> Path:
        custom = (self.options.get(CONF_DATASET_DIR) or "").strip()
        return Path(custom) if custom else self._media_root / "dataset"

    def www_path(self, slug: str) -> Path:
        return self._www_root / f"{slug}.jpg"

    def image_url(self, slug: str, at: datetime) -> str:
        base = (self.options.get(CONF_PUBLIC_URL) or "").rstrip("/")
        if not base:
            try:
                base = get_url(self.hass, allow_internal=False, prefer_external=True)
            except NoURLAvailableError:
                try:
                    base = get_url(self.hass)
                except NoURLAvailableError:
                    base = ""
        return f"{base}/local/{WWW_SUBDIR}/{slug}.jpg?t={int(at.timestamp())}"

    # --- state helpers ----------------------------------------------------------------

    def animal_state(self, animal: AnimalConfig | SpeciesConfig) -> dict[str, Any]:
        bucket = "species" if animal.kind == "species" else "animals"
        return self.data.setdefault(bucket, {}).setdefault(animal.slug, {})

    def camera_state(self, slug: str) -> dict[str, Any]:
        return self.data.setdefault("cameras", {}).setdefault(slug, {})

    def missing_mode(self, animal: AnimalConfig) -> bool:
        return bool(self.data.setdefault("missing", {}).get(animal.slug))

    def any_missing_mode(self) -> bool:
        return any(self.missing_mode(a) for a in self.animals.values())

    async def async_set_missing_mode(self, animal: AnimalConfig, on: bool) -> None:
        self.data.setdefault("missing", {})[animal.slug] = on
        self._save()
        self._notify()

    def last_seen(self, animal: AnimalConfig | SpeciesConfig) -> datetime | None:
        return _dt(self.animal_state(animal).get("last_seen"))

    def is_absent(self, animal: AnimalConfig, now: datetime | None = None) -> bool:
        if animal.kind == "species" or animal.priority:
            return False
        return logic.recently_absent(
            self.last_seen(animal),
            now or dt_util.utcnow(),
            float(self.options.get(CONF_ABSENT_DAYS, 14)),
        )

    def strict_keys(self, now: datetime | None = None) -> set[str]:
        return {
            a.key
            for a in self.animals.values()
            if not a.priority and (a.always_strict or self.is_absent(a, now))
        }

    def until_active(self, value: Any, now: datetime | None = None) -> bool:
        until = _dt(value)
        return bool(until and until > (now or dt_util.utcnow()))

    def sightings(self) -> list[dict[str, Any]]:
        return self.data.setdefault("sightings", [])

    def counted_sightings(
        self, animal: AnimalConfig | SpeciesConfig, since: datetime | None = None
    ) -> list[dict]:
        return [
            s
            for s in self.sightings()
            if s.get("animal") == animal.name
            and s.get("counted")
            and not s.get("wrong")
            and (since is None or (_dt(s.get("time")) or since) > since)
        ]

    def night_start(self) -> datetime | None:
        return _dt(self.data.get("last_summary_at"))

    # --- lifecycle ----------------------------------------------------------------------

    async def async_start(self) -> None:
        self.data = await self._store.async_load() or {}
        self.data.setdefault("animals", {})
        self.data.setdefault("cameras", {})
        self.data.setdefault("sightings", [])
        self.data.setdefault("missing", {})
        self.data.setdefault("comparisons", [])
        self.data.setdefault("ai", {"day": None, "models": {}})
        self.data.setdefault("species", {})
        self.data.setdefault("critical", {})
        self.data.setdefault("alerts", {})
        for row in self.sightings():
            row.setdefault("id", uuid4().hex[:10])
        await self.hass.async_add_executor_job(self._load_frames)
        # A last-seen entered in setup (e.g. carried over from another system) is the
        # starting point until a newer sighting replaces it.
        for animal in self.animals.values():
            known = animal.known_last_seen
            current = self.last_seen(animal)
            if known and (current is None or known > current):
                state = self.animal_state(animal)
                state["last_seen"] = _iso(known)
                state["location"] = animal.known_location or state.get("location")

        if not self.options.get(CONF_FIRST_LOOK):
            _LOGGER.warning("No AI model is set for the first look; checks will find nothing")

        trigger_map: dict[str, list[str]] = {}
        for cam in self.cameras.values():
            for entity_id in cam.triggers:
                trigger_map.setdefault(entity_id, []).append(cam.slug)
        self._trigger_map = trigger_map
        if trigger_map:
            self._unsubs.append(
                async_track_state_change_event(self.hass, list(trigger_map), self._on_trigger)
            )
        self._unsubs.append(self.hass.bus.async_listen(EVENT_SIGHTING, self._on_live_event))
        # Cameras without (or besides) a motion sensor: a check every N minutes.
        for cam in self.cameras.values():
            if cam.interval > 0:
                self._unsubs.append(
                    async_track_time_interval(
                        self.hass,
                        partial(self._on_interval, cam.slug),
                        timedelta(minutes=cam.interval),
                    )
                )
        self._unsubs.append(self.hass.bus.async_listen(EVENT_ACTION, self._on_phone_action))
        summary = dt_util.parse_time(self.options.get(CONF_SUMMARY_TIME, "08:05:00"))
        self._unsubs.append(
            async_track_time_change(
                self.hass,
                self._on_summary_time,
                hour=summary.hour,
                minute=summary.minute,
                second=summary.second,
            )
        )
        self._unsubs.append(async_track_time_interval(self.hass, self._on_tick, TICK))

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for cancel in self._deterring.values():
            cancel()
        self._deterring.clear()
        await self._store.async_save(self.data)

    def _load_frames(self) -> None:
        self._scan_references()
        for slug in self.cameras:
            path = self.frame_path(slug)
            content = _read(path)
            if content:
                at = datetime.fromtimestamp(path.stat().st_mtime, dt_util.UTC)
                self.frames[slug] = Frame(content, at)

    def _scan_references(self) -> None:
        self.references = {}
        for animal in self.animals.values():
            path = self.reference_path(animal)
            if path.exists():
                self.references[animal.slug] = datetime.fromtimestamp(path.stat().st_mtime, dt_util.UTC)

    def has_reference(self, animal: AnimalConfig) -> bool:
        return animal.slug in self.references

    def reference_at(self, animal: AnimalConfig) -> datetime | None:
        return self.references.get(animal.slug)

    def _save(self) -> None:
        self._store.async_delay_save(lambda: self.data, 5)

    @callback
    def _notify(self) -> None:
        async_dispatcher_send(self.hass, self.signal)

    # --- triggers -------------------------------------------------------------------------

    def is_night(self) -> bool:
        if self.options.get(CONF_ALWAYS):
            return True
        now = dt_util.now()
        start = dt_util.parse_time(self.options.get(CONF_NIGHT_START, "20:00:00"))
        end = dt_util.parse_time(self.options.get(CONF_NIGHT_END, "08:00:00"))
        dark = not is_up(self.hass)
        return logic.is_night(now.time(), start, end, dark, self.options.get(CONF_USE_DARK, True))

    @callback
    def _on_trigger(self, event: Event[EventStateChangedData]) -> None:
        old, new = event.data["old_state"], event.data["new_state"]
        if new is None or new.state != STATE_ON or old is None or old.state != "off":
            return
        for slug in self._trigger_map.get(event.data["entity_id"], []):
            self._queue_check(slug)

    @callback
    def _on_interval(self, slug: str, _now: datetime) -> None:
        self._queue_check(slug)

    @callback
    def _queue_check(self, slug: str) -> None:
        if self.mode == MODE_SHADOW_REUSE or not self.is_night():
            return
        if slug not in self._queued:
            self._queued.add(slug)
            self.entry.async_create_task(self.hass, self._run(slug), f"{DOMAIN} check {slug}")

    async def _run(self, slug: str) -> None:
        """Checks wait their turn on the lock (first come, first served)."""
        try:
            await self.async_check(slug)
        except Exception:  # noqa: BLE001 - one bad check must not stop the watch
            _LOGGER.exception("Check for %s failed", slug)
        finally:
            self._queued.discard(slug)

    # --- one check ----------------------------------------------------------------------------

    def _cooldown_open(self, cam: CameraConfig, now: datetime) -> bool:
        state = self.camera_state(cam.slug)
        last_noteworthy = (state.get("last_noteworthy") or "").lower()
        last_was_priority = any(
            a.priority and a.key == last_noteworthy for a in self.animals.values()
        )
        window = logic.cooldown_window(cam.cooldown, last_was_priority, self.any_missing_mode())
        reopeners = [_dt(self.camera_state(s).get("last_check")) for s in cam.reopened_by]
        return logic.may_check(now, _dt(state.get("last_check")), window, reopeners)

    def _skipped(self, cam: CameraConfig) -> str | None:
        for entity_id in cam.skip_when:
            state = self.hass.states.get(entity_id)
            if state and state.state == STATE_ON:
                return entity_id
        return None

    async def async_check(self, slug: str, force: bool = False) -> dict[str, Any] | None:
        """Check one camera. `force` ignores the cooldown and the skip sensors."""
        async with self._lock:
            cam = self.cameras.get(slug)
            if cam is None:
                return None
            now = dt_util.utcnow()
            if not force:
                if not self._cooldown_open(cam, now):
                    _LOGGER.debug("%s: in cooldown", cam.name)
                    return None
                if skipper := self._skipped(cam):
                    _LOGGER.debug("%s: skipped, %s is on", cam.name, skipper)
                    return None
            self.camera_state(slug)["last_check"] = _iso(now)

            # A failed snapshot STOPS the check: the AI must never judge an old photo.
            try:
                image = await camera_component.async_get_image(
                    self.hass, cam.camera, timeout=SNAPSHOT_TIMEOUT
                )
                await self.hass.async_add_executor_job(_write, self.frame_path(slug), image.content)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("%s: snapshot failed, check stopped: %s", cam.name, err)
                self.camera_state(slug)["last"] = {
                    "animal": None,
                    "error": f"Snapshot failed: {err}",
                    "time": _iso(now),
                }
                self._save()
                self._notify()
                return None
            self.frames[slug] = Frame(image.content, now)
            if self.mode == MODE_LIVE:
                try:
                    await self.hass.async_add_executor_job(_write, self.www_path(slug), image.content)
                except OSError as err:
                    _LOGGER.warning("%s: notification picture not saved: %s", cam.name, err)

            detector = await self._detect(image.content)
            skip = (
                detector is not None
                and self.options.get(CONF_DETECTOR_MODE) == DETECTOR_SKIP
                and detector["animal"] < float(self.options.get(CONF_DETECTOR_MIN, 0.2))
                and not self.any_missing_mode()  # in missing mode the AI always looks
            )
            if skip:
                verdict = logic.Verdict(logic.NOTHING, logic.HIGH, "na", "", "", "", "")
                models = "Local detector (no animal)"
            else:
                verdict, models = await self._identify(cam)
            check = Check(cam, verdict, models, image.content, now, detector=detector)
            await self._record(check)
            self._pair_with_live(cam.slug)
            return self._result(check)

    async def _detect(self, frame: bytes) -> dict[str, Any] | None:
        """Ask the local detector (MegaDetector) what's in the frame; None if it's not set up
        or doesn't answer. Never blocks a check."""
        url = (self.options.get(CONF_DETECTOR_URL) or "").strip().rstrip("/")
        if not url:
            return None
        try:
            async with asyncio.timeout(10):
                response = await async_get_clientsession(self.hass).post(
                    f"{url}/detect", data=frame, headers={"Content-Type": "image/jpeg"}
                )
                response.raise_for_status()
                body = await response.json()
        except Exception as err:  # noqa: BLE001
            _LOGGER.info("Local detector gave no answer: %s", err)
            self.data.setdefault("detector", {})["last_error"] = str(err)[:200]
            return None
        found = body.get("detections") or []

        def best(label: str) -> float:
            return max((d["confidence"] for d in found if d.get("label") == label), default=0.0)

        return {
            "animal": best("animal"),
            "person": best("person"),
            "vehicle": best("vehicle"),
            "boxes": [d for d in found if d.get("label") == "animal"][:3],
            "ms": body.get("ms"),
            "device": body.get("device"),
            "size": [body.get("width"), body.get("height")],
        }

    def _score_detector(self, v: logic.Verdict, det: dict[str, Any], now: datetime) -> None:
        """Shadow statistics: does the detector agree with the AI about whether an animal
        is there? A "miss" (AI found an animal, detector didn't) is what must be ~0 before
        empty frames may skip the AI."""
        threshold = float(self.options.get(CONF_DETECTOR_MIN, 0.2))
        ai_animal = v.animal != logic.NOTHING and v.animal.lower() not in logic.NOT_VISITORS
        det_animal = det["animal"] >= threshold
        kind = (
            "agree" if ai_animal == det_animal else "miss" if ai_animal else "extra"
        )
        rows = self.data.setdefault("detector", {}).setdefault("log", [])
        rows.append(
            {"time": _iso(now), "ai": f"{v.animal} / {v.confidence}", "detector": round(det["animal"], 3), "kind": kind}
        )
        del rows[:-500]

    def _result(self, check: Check) -> dict[str, Any]:
        v = check.verdict
        return {
            "animal": v.animal,
            "confidence": v.confidence,
            "pattern": v.pattern,
            "description": v.description,
            "priority_candidate": v.priority_candidate,
            "first_look_said": v.first_look_said,
            "second_opinion_said": v.second_opinion_said,
            "models_used": check.models,
            "counted": logic.counts(v, self.rules),
        }

    # --- the AI chain ---------------------------------------------------------------------

    def _prompt(self, cam: CameraConfig) -> tuple[str, list[dict[str, str]]]:
        now = dt_util.utcnow()
        attachments = [{"media_content_id": self.frame_media_id(cam.slug), "media_content_type": "image/jpeg"}]
        references: list[str] = []
        profiles = []
        for animal in self.animals.values():
            has_ref = self.options.get(CONF_REFERENCE_PHOTOS, True) and self.has_reference(animal)
            profiles.append(
                logic.AnimalProfile(animal.name, animal.description, animal.tips, bool(has_ref))
            )
            if has_ref:
                references.append(animal.name)
                attachments.append(
                    {"media_content_id": self.reference_media_id(animal), "media_content_type": "image/jpeg"}
                )
        absent = [a.name for a in self.animals.values() if self.is_absent(a, now)]
        visiting = [
            a.name for a in self.animals.values() if not a.priority and a.name not in absent and not a.always_strict
        ]
        prompt = logic.build_prompt(
            self.options.get(CONF_INSTRUCTIONS) or logic.DEFAULT_INSTRUCTIONS,
            location=cam.name,
            animals=profiles,
            absent=absent,
            visiting=visiting,
            wildlife=self.wildlife,
            unknown_kinds=sorted({a.species.lower() for a in self.animals.values()}) or ["cat", "dog"],
            extra_rules=" ".join(
                [self.options.get(CONF_EXTRA_RULES, "")]
                + ([f"ABOUT THIS CAMERA'S VIEW: {cam.ai_notes}"] if cam.ai_notes else [])
                + [f"{s.name}: {s.description}" for s in self.species.values() if s.description]
            ).strip(),
            references=references,
        )
        return prompt, attachments

    def _model_label(self, entity_id: str, index: int) -> str:
        state = self.hass.states.get(entity_id)
        label = state.name if state and state.name else entity_id
        if entity_id in self.options.get(CONF_PAID, []):
            return f"{label} (last resort)"
        return f"{label} (backup)" if index else label

    def _chain(self, key: str, free_only: bool = False) -> list[str]:
        chain = list(self.options.get(key) or [])
        if free_only or self.mode == MODE_SHADOW_INDEPENDENT:
            paid = set(self.options.get(CONF_PAID) or [])
            chain = [m for m in chain if m not in paid]
        return chain

    def main_model_resting(self) -> bool:
        """Outside the main model's hours its daily quota is saved for the busy hours."""
        start = dt_util.parse_time(self.options.get(CONF_MAIN_START) or "")
        end = dt_util.parse_time(self.options.get(CONF_MAIN_END) or "")
        if start is None or end is None or start == end:
            return False
        return not logic.in_time_window(dt_util.now().time(), start, end)

    async def _identify(self, cam: CameraConfig) -> tuple[logic.Verdict, str]:
        prompt, attachments = self._prompt(cam)
        priority = {a.key for a in self.animals.values() if a.priority or self.missing_mode(a)}
        strict = self.strict_keys()

        first, first_model, first_label, from_backup = "", None, "", False
        order = list(enumerate(self._chain(CONF_FIRST_LOOK)))
        if order and self.main_model_resting():
            # Resting: the main model goes after the free backups, still before paid ones.
            paid = set(self.options.get(CONF_PAID) or [])
            main = order.pop(0)
            at = next((i for i, (_, m) in enumerate(order) if m in paid), len(order))
            order.insert(at, main)
        for index, model in order:
            raw = await self._ask(model, prompt, attachments)
            if raw.strip():
                first, first_model, first_label = logic.tidy(raw), model, self._model_label(model, index)
                from_backup = index > 0
                break

        # A backup's answer that names an animal is always double-checked, by free models
        # only (paid models are kept for the cases the rules call for).
        special = logic.needs_second_opinion(first, priority, strict)
        backup_check = from_backup and logic.names_animal(first)
        second, second_label = "", ""
        if special or backup_check:
            for index, model in enumerate(self._chain(CONF_SECOND_OPINION, free_only=not special)):
                if model == first_model:
                    continue  # a model never second-guesses itself
                if index and first_model is None:
                    break  # nobody answered the first look: only the main second model tries
                raw = await self._ask(model, prompt, attachments)
                if raw.strip():
                    second, second_label = logic.tidy(raw), self._model_label(model, index)
                    break

        verdict = logic.decide(first, second, self.rules, strict, unconfirmed=backup_check)
        models = first_label or "none answered"
        if second_label:
            models += f" + {second_label}"
        self.data["ai"]["last_models"] = models
        return verdict, models

    def _ai_stats(self, model: str) -> dict[str, Any]:
        ai = self.data["ai"]
        today = dt_util.now(ZoneInfo(QUOTA_TZ)).date().isoformat()
        if ai.get("day") != today:
            ai["day"] = today
            ai["models"] = {}
        return ai["models"].setdefault(model, {"calls": 0, "answers": 0, "errors": 0})

    async def _ask(self, model: str, prompt: str, attachments: list[dict[str, str]]) -> str:
        answer = await self._ask_once(model, prompt, attachments)
        if answer is None:  # busy (503): one quick retry before moving on
            await asyncio.sleep(BUSY_RETRY_SECONDS)
            answer = await self._ask_once(model, prompt, attachments)
        return answer or ""

    async def _ask_once(
        self, model: str, prompt: str, attachments: list[dict[str, str]]
    ) -> str | None:
        """The model's text; "" for no answer; None when it was only busy."""
        stats = self._ai_stats(model)
        stats["calls"] += 1
        try:
            async with asyncio.timeout(AI_TIMEOUT):
                response = await self.hass.services.async_call(
                    "ai_task",
                    "generate_data",
                    {
                        "entity_id": model,
                        "task_name": TASK_NAME,
                        "instructions": prompt,
                        "attachments": attachments,
                    },
                    blocking=True,
                    return_response=True,
                )
        except Exception as err:  # noqa: BLE001 - every failure falls through to the next model
            stats["errors"] += 1
            stats["last_error"] = str(err)[:200] or type(err).__name__
            stats["last_error_at"] = _iso(dt_util.utcnow())
            _LOGGER.info("%s gave no answer: %s", model, stats["last_error"])
            return None if _is_busy(err) else ""
        data = response.get("data") if isinstance(response, dict) else None
        if not isinstance(data, str):
            stats["errors"] += 1
            stats["last_error"] = "No text in the reply"
            stats["last_error_at"] = _iso(dt_util.utcnow())
            return ""
        stats["answers"] += 1
        stats["last_answer_at"] = _iso(dt_util.utcnow())
        return data

    # --- recording -----------------------------------------------------------------------------

    async def _record(self, check: Check) -> None:
        cam, v, now = check.camera, check.verdict, check.at
        rules = self.rules
        counted = logic.counts(v, rules)
        on_minutes = float(self.options.get(CONF_ON_CAMERA_MINUTES, 10))
        until = now + timedelta(minutes=on_minutes)

        cam_state = self.camera_state(cam.slug)
        cam_state["last"] = {
            "animal": v.animal,
            "confidence": v.confidence,
            "pattern": v.pattern,
            "description": v.description,
            "counted": counted,
            "priority_candidate": v.priority_candidate,
            "first_look_said": v.first_look_said,
            "second_opinion_said": v.second_opinion_said,
            "models_used": check.models,
            "time": _iso(now),
            "source": check.source,
            "detector": check.detector,
        }
        if check.detector is not None and not check.models.startswith("Local detector"):
            self._score_detector(v, check.detector, now)
        if logic.is_noteworthy(v, rules):
            cam_state["last_noteworthy"] = v.animal
        if logic.is_visitor(v, rules, self.wildlife):
            cam_state["current"] = v.animal
            cam_state["current_until"] = _iso(until)

        sid = uuid4().hex[:10]
        day = dt_util.as_local(now).date().isoformat()
        cam_state["last"]["id"] = sid
        self.data["last_sighting"] = {"camera": cam.name, **cam_state["last"]}
        kind = self.species_by_name(v.animal)
        self.sightings().append(
            {
                "id": sid,
                "day": day,
                "time": _iso(now),
                "camera": cam.slug,
                "where": cam.name,
                "animal": v.animal,
                "confidence": v.confidence,
                "counted": counted,
                "log": logic.is_cat_for_log(v, rules) or bool(kind and kind.in_summary),
                "possible": bool(kind) and v.confidence == logic.LOW,
            }
        )
        self._prune(now)
        if check.frame and self.options.get(CONF_DATASET, True):
            meta = {
                "id": sid,
                "time": _iso(now),
                "camera": cam.slug,
                "detector": check.detector,
                "ai_animal": v.animal,
                "ai_confidence": v.confidence,
                "first_look_said": v.first_look_said,
                "second_opinion_said": v.second_opinion_said,
                "models_used": check.models,
                "label": None,
            }
            try:
                await self.hass.async_add_executor_job(
                    dataset.save_sample, self.dataset_root, day, sid, check.frame, meta
                )
            except Exception as err:  # noqa: BLE001 - training data is best-effort
                _LOGGER.debug("Training sample not saved: %s", err)

        if kind and counted:
            state = self.animal_state(kind)
            state["last_seen"] = _iso(now)
            state["location"] = cam.name
            state["on_camera_until"] = _iso(until)
            state["on_camera_where"] = cam.name
            if check.frame:
                try:
                    await self.hass.async_add_executor_job(
                        _rotate_and_write, self.photo_path(kind), check.frame
                    )
                    state["photo_at"] = _iso(now)
                except OSError as err:
                    _LOGGER.warning("Photo for %s not saved: %s", kind.name, err)

        animal = self.animal_by_name(v.animal)
        if animal and counted:
            state = self.animal_state(animal)
            state["last_seen"] = _iso(now)
            state["location"] = cam.name
            state["on_camera_until"] = _iso(until)
            state["on_camera_where"] = cam.name
            if check.frame:
                try:
                    await self.hass.async_add_executor_job(
                        _rotate_and_write, self.photo_path(animal), check.frame
                    )
                    state["photo_at"] = _iso(now)
                except OSError as err:
                    _LOGGER.warning("Photo for %s not saved: %s", animal.name, err)
            # The confirmed signal (a feeder's trigger) is HIGH only. Where comes first.
            if animal.priority and v.confidence == logic.HIGH:
                state["confirmed_where"] = cam.name
                state["last_confirmed"] = _iso(now)
                state["confirmed_until"] = _iso(until)

        self._save()
        self._notify()
        if self.mode == MODE_LIVE and check.source == "own":
            data = self.event_data(cam, v, check.models, now)
            data["sighting_id"] = sid
            self.hass.bus.async_fire(EVENT_SIGHTING, data)
            await self._alert(cam, v, now, sid)
            if kind and kind.pest and counted:
                await self._deter(cam)

    def event_data(
        self, cam: CameraConfig, v: logic.Verdict, models: str, now: datetime, test: bool = False
    ) -> dict[str, Any]:
        return {
            "location": cam.slug,
            "location_name": cam.name,
            "camera": cam.camera,
            "animal": v.animal,
            "confidence": v.confidence,
            "pattern": v.pattern,
            "description": v.description,
            "priority_candidate": "yes" if v.priority_candidate else "no",
            "priority_animal": v.priority_candidate,
            "first_look_said": v.first_look_said,
            "second_opinion_said": v.second_opinion_said,
            "models_used": models,
            "counted": "yes" if logic.counts(v, self.rules) else "no",
            "time": logic.clock(dt_util.as_local(now)),
            "image": self.image_url(cam.slug, now),
            "test": "yes" if test else "no",
            "source": DOMAIN,
        }

    def _prune(self, now: datetime) -> None:
        cutoff = now - timedelta(days=SIGHTING_DAYS)
        kept = [s for s in self.sightings() if (_dt(s.get("time")) or now) >= cutoff]
        self.data["sightings"] = kept[-SIGHTING_MAX:]

    # --- shadow mode --------------------------------------------------------------------------

    @callback
    def _on_live_event(self, event: Event) -> None:
        data = event.data
        if data.get("source") == DOMAIN or data.get("test") == "yes" or self.mode == MODE_LIVE:
            return
        if self.mode == MODE_SHADOW_REUSE:
            self.hass.async_create_task(self._reuse_live(dict(data)))
        else:
            slug = str(data.get("location", ""))
            self._pending_live[slug] = {**data, "_at": dt_util.utcnow()}
            self._pair_with_live(slug)

    async def _reuse_live(self, data: dict[str, Any]) -> None:
        """Re-run our rules on the live system's own AI answers (no extra AI calls)."""
        async with self._lock:
            now = dt_util.utcnow()
            slug = str(data.get("location", ""))
            cam = self.cameras.get(slug) or self.camera_by_ref(str(data.get("location_name", "")))
            first = logic.tidy(str(data.get("first_look_said") or ""))
            second = logic.tidy(str(data.get("second_opinion_said") or ""))
            verdict = logic.decide(first, second, self.rules, self.strict_keys(now))
            self._compare("reuse", slug, data, verdict, now)
            if cam is None:
                self._save()
                self._notify()
                return
            frame = await self.hass.async_add_executor_job(self._read_live_picture, data.get("image"))
            if frame:
                self.frames[cam.slug] = Frame(frame, now)
            self.camera_state(cam.slug)["last_check"] = _iso(now)
            await self._record(Check(cam, verdict, str(data.get("models_used") or ""), frame, now, "reuse"))

    def _read_live_picture(self, url: Any) -> bytes | None:
        """The live event's picture, when it is a /local/ file of this install."""
        path = urlparse(str(url or "")).path
        if not path.startswith("/local/"):
            return None
        www = Path(self.hass.config.path("www")).resolve()
        target = (www / path.removeprefix("/local/")).resolve()
        if www not in target.parents:
            return None
        return _read(target)

    def _pair_with_live(self, slug: str) -> None:
        """Independent shadow: compare our check with the live one at the same camera."""
        live = self._pending_live.get(slug)
        if live is None or self.mode != MODE_SHADOW_INDEPENDENT:
            return
        now = dt_util.utcnow()
        if now - live["_at"] > PAIR_WINDOW:
            self._pending_live.pop(slug, None)
            self._compare("independent", slug, live, None, now)
            return
        ours = self.camera_state(slug).get("last") or {}
        ours_at = _dt(ours.get("time"))
        if ours_at is None or abs(ours_at - live["_at"]) > PAIR_WINDOW or ours.get("animal") is None:
            return  # wait for our check (or it never comes; the next event reports it)
        self._pending_live.pop(slug, None)
        verdict = logic.Verdict(
            ours["animal"], ours["confidence"], ours.get("pattern", "na"), "", "", "", ""
        )
        self._compare("independent", slug, live, verdict, now)

    def _compare(
        self,
        kind: str,
        slug: str,
        live: dict[str, Any],
        ours: logic.Verdict | None,
        now: datetime,
    ) -> None:
        live_animal, live_conf = str(live.get("animal", "")), str(live.get("confidence", ""))
        record = {
            "time": _iso(now),
            "kind": kind,
            "camera": live.get("location_name") or slug,
            "live": f"{live_animal} / {live_conf}",
            "ours": f"{ours.animal} / {ours.confidence}" if ours else "no check of our own",
            "match": bool(ours and ours.animal == live_animal and ours.confidence == live_conf),
        }
        comparisons = self.data.setdefault("comparisons", [])
        comparisons.append(record)
        del comparisons[:-COMPARISON_MAX]
        if not record["match"]:
            _LOGGER.info("Shadow mismatch at %s: live %s, ours %s", record["camera"], record["live"], record["ours"])
        self._save()
        self._notify()

    # --- timers -------------------------------------------------------------------------------

    @callback
    def _on_tick(self, now: datetime) -> None:
        """Expire 'on camera now', current animals and the confirmed signal."""
        changed = False
        for state in self.data.get("cameras", {}).values():
            if state.get("current") and not self.until_active(state.get("current_until"), now):
                state["current"] = None
                changed = True
        for state in self.data.get("animals", {}).values():
            for key in ("on_camera_until", "confirmed_until"):
                if state.get(key) and not self.until_active(state.get(key), now):
                    state[key] = None
                    changed = True
        for slug, live in list(self._pending_live.items()):
            if now - live["_at"] > PAIR_WINDOW:
                self._pair_with_live(slug)
        if changed:
            self._save()
        # Absent status and "tonight" can change with the clock alone.
        if changed or now.second < TICK.seconds:
            self._notify()

    @callback
    def _on_summary_time(self, now: datetime) -> None:
        self.build_summary(now)

    def build_summary(self, now: datetime | None = None) -> logic.Summary:
        now = now or dt_util.utcnow()
        since = self.night_start()
        entries = [
            (s["animal"], s.get("where", ""), bool(s.get("possible")) and not s.get("user"))
            for s in self.sightings()
            if s.get("log") and not s.get("wrong") and (since is None or (_dt(s.get("time")) or now) > since)
        ]
        order = [a.name for a in self.animals.values()] + [
            s.name for s in self.species.values() if s.in_summary
        ]
        summary = logic.summarize(entries, order)
        if self.options.get(CONF_DATASET, True):
            limit = int(float(self.options.get(CONF_DATASET_GB, 5)) * 1024**3)
            self.hass.async_add_executor_job(dataset.prune, self.dataset_root, limit)
        self.data["summary"] = {
            "text": summary.text,
            "lines": summary.lines,
            "counts": summary.counts,
            "places": summary.places,
            "built_at": _iso(now),
            "night_started": _iso(since),
        }
        self.data["last_summary_at"] = _iso(now)
        self._save()
        self._notify()
        return summary

    # --- services ---------------------------------------------------------------------------------

    async def async_mark_wrong(self, animal: AnimalConfig, camera: CameraConfig | None) -> dict[str, Any]:
        """Undo the latest counted sighting of `animal` (optionally at one camera)."""
        candidates = [
            s
            for s in self.counted_sightings(animal)
            if camera is None or s.get("camera") == camera.slug
        ]
        if not candidates:
            return {"removed": None}
        row = candidates[-1]
        await self.async_correct(row["id"], None, "wrong")
        return {"removed": {k: row[k] for k in ("time", "where", "animal", "confidence")}}

    def review_queue(self, days: int = 7) -> list[dict[str, Any]]:
        """Sightings nobody has answered yet, newest first: anything with an animal in it
        (a confident "nothing" is left out) whose photo was kept."""
        cutoff = dt_util.utcnow() - timedelta(days=days)
        return [
            s
            for s in reversed(self.sightings())
            if not s.get("user")
            and s.get("day")
            and not (s.get("animal") == logic.NOTHING and s.get("confidence") == logic.HIGH)
            and (_dt(s.get("time")) or cutoff) >= cutoff
        ]

    def find_sighting(self, sid: str | None, camera: CameraConfig | None) -> dict[str, Any] | None:
        rows = self.sightings()
        if sid:
            return next((s for s in reversed(rows) if s.get("id") == sid), None)
        for s in reversed(rows):
            if camera is None or s.get("camera") == camera.slug:
                return s
        return None

    async def async_correct(
        self, sid: str | None, camera: CameraConfig | None, answer: str
    ) -> dict[str, Any]:
        """A person's verdict on a sighting: "right", "wrong", or what it really was.

        Tracking moves to the right animal, the photo follows, and the training sample is
        labelled. The AI itself does not learn from this; the labelled frames are for a
        custom model.
        """
        row = self.find_sighting(sid, camera)
        if row is None:
            return {"corrected": None}
        answer = answer.strip()
        before = row.get("animal")
        old = self.subject(before or "")
        if row.get("scan"):
            # Scanned footage can be old: a verdict labels it, never moves tracking.
            old = None
        if answer.lower() in ("unsure", "not sure", "skip", "maybe"):
            # Looked at, but no verdict: leaves the queue, never labels the training frame.
            row["user"] = "unsure"
            row["label"] = None
        elif answer.lower() == "right":
            row["label"] = before
            row["user"] = "right"
            if old and not row.get("counted"):
                row["counted"] = True
                row["log"] = old.kind == "animal" or old.in_summary
                await self._recompute(old, row)
        elif answer.lower() == "wrong":
            row["label"] = f"not:{before}"
            row["user"] = "wrong"
            row["wrong"] = True
            if old:
                await self._recompute(old, row, removed=True)
        else:
            new = self.subject(answer)
            if answer.lower() in ("nothing", "none"):
                name = logic.NOTHING
            elif logic.is_unknown(answer):
                name = answer.strip().title()
            elif new is None:
                # Any other animal (a groundhog, a heron): labelled for training, not tracked.
                listed = {w.lower(): w for w in self.wildlife}
                name = listed.get(answer.lower()) or answer.strip().title()
            else:
                name = new.name
            row.update(animal=name, label=name, user="corrected", counted=True, wrong=False)
            row["confidence"] = "confirmed"
            row["log"] = bool(
                (new and new.kind == "animal")
                or logic.is_unknown(name)
                or (new and new.kind == "species" and new.in_summary)
            )
            if old and old is not new:
                await self._recompute(old, row, removed=True)
            if new and not row.get("scan"):
                await self._recompute(new, row)
        if row.get("day"):
            await self.hass.async_add_executor_job(
                dataset.set_label, self.dataset_root, row["day"], row["id"], row["label"], row["user"]
            )
        self._save()
        self._notify()
        return {"corrected": {k: row.get(k) for k in ("id", "time", "where", "animal", "label")}, "was": before}

    async def _recompute(
        self, subject: AnimalConfig | SpeciesConfig, row: dict[str, Any], removed: bool = False
    ) -> None:
        """Rebuild last seen / location / photo after a sighting was removed or added."""
        state = self.animal_state(subject)
        rows = self.counted_sightings(subject)
        latest = rows[-1] if rows else None
        state["last_seen"] = latest["time"] if latest else None
        state["location"] = latest["where"] if latest else None
        known = getattr(subject, "known_last_seen", None)
        if known and (latest is None or known > (_dt(latest["time"]) or known)):
            state["last_seen"] = _iso(known)
            state["location"] = getattr(subject, "known_location", None)
        if subject.kind == "animal" and subject.priority:
            sure = [s for s in rows if s.get("confidence") in (logic.HIGH, "confirmed")]
            state["last_confirmed"] = sure[-1]["time"] if sure else state.get("last_confirmed") if not removed else None
            if sure:
                state["confirmed_where"] = sure[-1]["where"]
        photo = self.photo_path(subject)
        if removed:
            if state.get("photo_at") == row.get("time"):
                state["on_camera_until"] = None
                state["confirmed_until"] = None
                restored = await self.hass.async_add_executor_job(_restore_previous, photo)
                state["photo_at"] = (latest["time"] if latest else None) if restored else None
            cam_state = self.camera_state(row.get("camera", ""))
            if cam_state.get("current") == subject.name:
                cam_state["current"] = None
        elif latest is row and row.get("day"):
            frame = await self.hass.async_add_executor_job(
                dataset.read_frame, self.dataset_root, row["day"], row["id"]
            )
            if frame:
                await self.hass.async_add_executor_job(_rotate_and_write, photo, frame)
                state["photo_at"] = row["time"]

    @callback
    def _on_phone_action(self, event: Event) -> None:
        """Right / Wrong / It's <animal> buttons on an alert."""
        parts = str(event.data.get("action", "")).split("|")
        if len(parts) < 3 or parts[0] != ACTION_PREFIX:
            return
        verb, sid = parts[1], parts[2]
        if verb == "is" and len(parts) > 3:
            target = self.subject(parts[3])
            answer = target.name if target else parts[3]
        elif verb in ("right", "wrong"):
            answer = verb
        else:
            return
        self.hass.async_create_task(self.async_correct(sid, None, answer))

    # --- alerts and deterrents ------------------------------------------------------------------

    def camera_rule(self, cam: CameraConfig) -> logic.CameraRule:
        return logic.CameraRule(
            cam.slug, cam.name, self.critical_on(cam.switch_key), cam.critical_except
        )

    def alert_rules(self) -> dict[str, logic.AlertRule]:
        """Each named animal and wildlife kind, with its switches applied."""
        floor = self.options.get(CONF_MIN_CONFIDENCE, logic.MEDIUM)
        rules: dict[str, logic.AlertRule] = {}
        for s in self.species.values():
            rules[s.key] = logic.AlertRule(
                s.name,
                alert_at=s.alert_at,
                min_confidence=floor,
                critical_at=s.critical_at or s.alert_at,  # empty: wherever it alerts
                alerts_on=self.alerts_on(s.switch_key),
                critical_on=self.critical_on(s.switch_key),
                repeat_every=s.pest,  # a pest that was scared off and came back: tell again
            )
        for a in self.animals.values():
            rules[a.key] = logic.AlertRule(
                a.name,
                alert_at=a.alert_at,
                min_confidence=floor,
                critical_at=a.critical_at or a.alert_at,
                alerts_on=self.alerts_on(a.switch_key),
                critical_on=self.critical_on(a.switch_key),
                priority=a.priority,
            )
        return rules

    def builtin_rules(self) -> dict[str, logic.AlertRule]:
        floor = self.options.get(CONF_MIN_CONFIDENCE, logic.MEDIUM)
        rules = {}
        for name, key in BUILTIN_KEYS.items():
            alert_at = logic.cameras(self.options.get(f"{key}_{CONF_ALERT_AT}", []))
            rules[name] = logic.AlertRule(
                name,
                alert_at=alert_at,
                min_confidence=floor,
                critical_at=logic.cameras(self.options.get(f"{key}_{CONF_CRITICAL_AT}", [])) or alert_at,
                critical_on=self.critical_on(builtin_switch_key(key)),
            )
        return rules

    def _repeat(self, cam: CameraConfig, v: logic.Verdict, now: datetime) -> tuple[bool, str | None]:
        """Is this the same visit as the subject's last sighting at this camera?"""
        rule = logic.subject_of(v, self.alert_rules(), self.builtin_rules())
        if rule is None:
            return False, None
        visits = self.camera_state(cam.slug).setdefault("visits", {})
        gap = logic.cooldown_window(cam.cooldown, rule.priority, False).total_seconds() / 60
        same = logic.same_visit(_dt(visits.get(rule.key)), now, gap, VISIT_SLACK_MINUTES)
        return same, rule.key

    async def _alert(
        self, cam: CameraConfig, v: logic.Verdict, now: datetime, sid: str | None, test: bool = False
    ) -> list[logic.Alert]:
        if not self.options.get(CONF_ALERTS, True) or not self.recipients:
            return []
        missing = {a.key for a in self.animals.values() if self.missing_mode(a)}
        repeat, visit_key = (False, None) if test else self._repeat(cam, v, now)
        alerts = logic.plan_alerts(
            v,
            self.camera_rule(cam),
            self.alert_rules(),
            self.builtin_rules(),
            missing,
            logic.clock(dt_util.as_local(now)),
            test=test,
            repeat=repeat,
        )
        if visit_key and any(a.kind != "missing" for a in alerts):
            self.camera_state(cam.slug).setdefault("visits", {})[visit_key] = _iso(now)
            self._save()
        if not alerts:
            return alerts
        image = self.image_url(cam.slug, now)
        actions = []
        if sid and self.options.get(CONF_BUTTONS, True):
            actions = [
                {"action": f"{ACTION_PREFIX}|right|{sid}", "title": "✓ Right"},
                {"action": f"{ACTION_PREFIX}|wrong|{sid}", "title": "✗ Wrong"},
            ]
            for a in self.animals.values():
                if (a.priority or a.key in missing) and a.name != v.animal:
                    actions.append({"action": f"{ACTION_PREFIX}|is|{sid}|{a.slug}", "title": f"It's {a.name}"})
        for alert in alerts:
            for person in self.recipients:
                if alert.audience == logic.AUDIENCE_MISSING and not person.missing_alerts:
                    continue
                if alert.audience == logic.AUDIENCE_NOT_MISSING and person.missing_alerts:
                    continue
                if alert.kind != "missing" and alert.subject.lower() in person.muted:
                    continue
                await self._send(person, alert, image, actions)
        return alerts

    def _quiet(self, person: RecipientConfig) -> bool:
        """In one of the person's quiet zones (work) or inside their quiet hours."""
        if person.quiet_start and person.quiet_end:
            start = dt_util.parse_time(person.quiet_start)
            end = dt_util.parse_time(person.quiet_end)
            if start and end and start != end and logic.in_time_window(dt_util.now().time(), start, end):
                return True
        if not person.person or not person.quiet_zones:
            return False
        state = self.hass.states.get(person.person)
        if state is None:
            return False
        for zone_id in person.quiet_zones:
            zone = self.hass.states.get(zone_id)
            names = {zone_id.split(".", 1)[-1]}
            if zone is not None:
                names.add(zone.name)
            if state.state in names:
                return True
        return False

    async def _send(
        self, person: RecipientConfig, alert: logic.Alert, image: str, actions: list[dict[str, str]]
    ) -> None:
        quiet = self._quiet(person)
        if quiet and person.quiet_mode == QUIET_NONE and alert.kind != "missing":
            return  # missing-mode alerts always arrive, at most as normal ones
        # A repeat in the same visit replaces the earlier notification without a sound;
        # a person whose "gets critical alerts" switch is off gets a normal one.
        critical = (
            alert.urgency == logic.CRITICAL
            and not quiet
            and not alert.quiet
            and self.person_critical_on(person)
        )
        payload: dict[str, Any] = {"title": alert.title, "message": alert.message}
        if person.notify.startswith("mobile_app_"):
            # One payload for both Companion apps: iOS reads "push", Android reads
            # ttl / priority / channel; each ignores the other's keys.
            level = "critical" if critical else "passive" if alert.quiet else "active"
            push: dict[str, Any] = {"interruption-level": level}
            data: dict[str, Any] = {"image": image, "tag": alert.tag, "push": push}
            if alert.quiet:
                data["alert_once"] = True  # Android: update in place, silently
            if critical:
                push["sound"] = {
                    "name": person.sound,
                    "critical": 1,
                    "volume": alert.volume if alert.volume is not None else person.volume,
                }
                data.update({"ttl": 0, "priority": "high", "channel": "alarm_stream"})
            if actions:
                data["actions"] = actions
            payload["data"] = data
        else:
            payload["message"] = f"{alert.message}\n{image}"
        try:
            await self.hass.services.async_call("notify", person.notify, payload, blocking=True)
        except Exception as err:  # noqa: BLE001 - one phone failing must not stop the others
            _LOGGER.warning("Alert to %s failed: %s", person.name, err)

    async def _deter(self, cam: CameraConfig) -> None:
        """Lights on (and a sound) at this camera to scare a pest off."""
        if not cam.deter_lights and not cam.deter_sound:
            return
        if cancel := self._deterring.pop(cam.slug, None):
            cancel()  # already running: restart the timer, keep what we turned on
            turned_on = self.data.setdefault("deterring", {}).get(cam.slug, [])
        else:
            turned_on = [
                e for e in cam.deter_lights if (s := self.hass.states.get(e)) and s.state == "off"
            ]
            self.data.setdefault("deterring", {})[cam.slug] = turned_on
            if cam.deter_lights:
                await self.hass.services.async_call(
                    "homeassistant", "turn_on", {"entity_id": cam.deter_lights}, blocking=False
                )
        if cam.deter_sound and cam.deter_sound.get("entity_id"):
            await self.hass.services.async_call(
                "media_player",
                "play_media",
                {
                    "entity_id": cam.deter_sound["entity_id"],
                    "media_content_id": cam.deter_sound.get("media_content_id"),
                    "media_content_type": cam.deter_sound.get("media_content_type", "music"),
                },
                blocking=False,
            )

        async def lights_back(_now: datetime) -> None:
            self._deterring.pop(cam.slug, None)
            back = self.data.setdefault("deterring", {}).pop(cam.slug, [])
            if back:
                await self.hass.services.async_call(
                    "homeassistant", "turn_off", {"entity_id": back}, blocking=False
                )

        self._deterring[cam.slug] = async_call_later(
            self.hass, cam.deter_minutes * 60, lights_back
        )

    # --- bulk scanning ----------------------------------------------------------------------

    def _detector_url(self) -> str:
        return (self.options.get(CONF_DETECTOR_URL) or "").strip().rstrip("/")

    async def async_scan(self, folder: str, identify: bool = True, limit: int = 50) -> str:
        """Start a bulk scan of a folder on the detector's machine; returns the scan id.

        The detector finds the animal moments (visits); each visit's best frame is then named
        by the same AI chain as a camera check, joins the review queue and the training data,
        and the names go into the report written next to the files.
        """
        url = self._detector_url()
        if not url:
            raise ValueError("no_detector")
        session = async_get_clientsession(self.hass)
        async with asyncio.timeout(30):
            response = await session.post(f"{url}/scan", json={"path": folder})
            body = await response.json()
        if response.status != 200 or "id" not in body:
            raise ValueError(body.get("error") or f"detector answered {response.status}")
        sid = body["id"]
        self.data.setdefault("scans", {})[sid] = {
            "id": sid,
            "folder": folder,
            "state": "scanning",
            "started": _iso(dt_util.utcnow()),
        }
        self._prune_scans()
        self._save()
        self._notify()
        self.entry.async_create_background_task(
            self.hass, self._follow_scan(sid, identify, limit), f"{DOMAIN} scan {sid}"
        )
        return sid

    def _prune_scans(self) -> None:
        scans = self.data.setdefault("scans", {})
        for old in list(scans)[:-20]:
            scans.pop(old, None)

    def latest_scan(self) -> dict[str, Any] | None:
        scans = self.data.get("scans") or {}
        return scans[list(scans)[-1]] if scans else None

    async def _follow_scan(self, sid: str, identify: bool, limit: int) -> None:
        url = self._detector_url()
        session = async_get_clientsession(self.hass)
        record = self.data["scans"][sid]
        try:
            status: dict[str, Any] = {}
            for _ in range(6 * 60 * 12):  # up to 12 hours
                async with asyncio.timeout(30):
                    status = await (await session.get(f"{url}/scan/{sid}")).json()
                record.update(
                    files_total=status.get("files_total"), files_done=status.get("files_done")
                )
                self._notify()
                if status.get("state") in ("done", "error"):
                    break
                await asyncio.sleep(5)
            visits = status.get("visits") or []
            names: dict[str, dict[str, str]] = {}
            results = []
            for visit in visits:
                entry = {k: visit.get(k) for k in ("n", "file", "kind", "start", "end", "confidence")}
                if identify and len(names) < limit:
                    named = await self._name_visit(sid, visit, record["folder"])
                    if named:
                        names[str(visit["n"])] = {"animal": named["animal"], "confidence": named["confidence"]}
                        entry.update(named)
                results.append(entry)
            report = None
            if status.get("state") == "done":
                async with asyncio.timeout(120):
                    answer = await (await session.post(f"{url}/scan/{sid}/report", json={"names": names})).json()
                report = answer.get("report")
            tally: dict[str, int] = {}
            for entry in results:
                key = entry.get("animal") or "animal"
                tally[key] = tally.get(key, 0) + 1
            record.update(
                state=status.get("state", "error"),
                error=status.get("error") or None,
                finished=_iso(dt_util.utcnow()),
                visits=results,
                tally=tally,
                report=report,
            )
            await self._scan_done_alert(record)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Scan %s failed: %s", sid, err)
            record.update(state="error", error=str(err)[:200], finished=_iso(dt_util.utcnow()))
        self._save()
        self._notify()

    async def _name_visit(self, sid: str, visit: dict[str, Any], folder: str) -> dict[str, Any] | None:
        """Identify one visit's best frame like a camera check (without tracking)."""
        url = self._detector_url()
        async with asyncio.timeout(60):
            response = await async_get_clientsession(self.hass).get(f"{url}/scan/{sid}/frame/{visit['n']}")
            if response.status != 200:
                return None
            frame = await response.read()
        slug = f"scan_{sid}_{visit['n']}"
        place = f"{folder}/{visit['file']}"
        cam = CameraConfig("", f"scan of {place}", slug, "", [], [], logic.Cooldown(), [])
        async with self._lock:
            await self.hass.async_add_executor_job(_write, self.frame_path(slug), frame)
            try:
                verdict, models = await self._identify(cam)
            finally:
                await self.hass.async_add_executor_job(remove_file, self.frame_path(slug))
        now = dt_util.utcnow()
        row_id = uuid4().hex[:10]
        day = dt_util.as_local(now).date().isoformat()
        self.sightings().append(
            {
                "id": row_id,
                "day": day,
                "time": _iso(now),
                "camera": f"scan:{sid}",
                "where": place,
                "animal": verdict.animal,
                "confidence": verdict.confidence,
                "counted": False,
                "log": False,
                "scan": sid,
            }
        )
        if self.options.get(CONF_DATASET, True):
            meta = {
                "id": row_id,
                "time": _iso(now),
                "source": "scan",
                "file": place,
                "video_seconds": [visit.get("start"), visit.get("end")],
                "ai_animal": verdict.animal,
                "ai_confidence": verdict.confidence,
                "first_look_said": verdict.first_look_said,
                "second_opinion_said": verdict.second_opinion_said,
                "models_used": models,
                "label": None,
            }
            try:
                await self.hass.async_add_executor_job(
                    dataset.save_sample, self.dataset_root, day, row_id, frame, meta
                )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Scan sample not saved: %s", err)
        return {"animal": verdict.animal, "confidence": verdict.confidence, "sighting_id": row_id}

    async def _scan_done_alert(self, record: dict[str, Any]) -> None:
        if not self.options.get(CONF_ALERTS, True) or not self.recipients:
            return
        tally = record.get("tally") or {}
        found = ", ".join(f"{n} {k}" for k, n in sorted(tally.items(), key=lambda kv: -kv[1])) or "no animals"
        alert = logic.Alert(
            "scan",
            f"Scan finished — {record['folder']}",
            f"{record.get('files_total') or 0} files: {found}."
            + (f"\nReport: {record['report']}" if record.get("report") else ""),
            f"scan_{record['id']}",
            logic.NORMAL,
        )
        for person in self.recipients:
            await self._send(person, alert, "", [])

    async def async_set_reference(self, animal: AnimalConfig, camera: CameraConfig | None) -> str:
        """Use the animal's last photo (or a fresh snapshot) as its reference photo."""
        if camera is not None:
            image = await camera_component.async_get_image(self.hass, camera.camera, timeout=SNAPSHOT_TIMEOUT)
            content: bytes | None = image.content
        else:
            content = await self.hass.async_add_executor_job(_read, self.photo_path(animal))
        if not content:
            return "no_photo"
        await self.hass.async_add_executor_job(save_reference, content, self.reference_path(animal))
        self.references[animal.slug] = dt_util.utcnow()
        self._notify()
        return "ok"

    def add_note(self, note: str, camera: CameraConfig | None = None, animal: str | None = None) -> str:
        """Add a note for the AI where it belongs: a camera's view notes, an animal's
        identification tips or a wildlife kind's description. Returns where it went."""
        note = note.strip()
        if camera is not None:
            owner, key, where = camera.subentry_id, CONF_AI_NOTES, camera.name
        else:
            subject = self.subject(animal or "")
            if subject is None:
                raise ValueError(f"unknown animal {animal}")
            owner, where = subject.subentry_id, subject.name
            key = CONF_TIPS if subject.kind == "animal" else CONF_DESCRIPTION
        subentry = self.entry.subentries[owner]
        old = (subentry.data.get(key) or "").strip()
        self.hass.config_entries.async_update_subentry(
            self.entry, subentry, data={**subentry.data, key: f"{old} {note}".strip()}
        )
        return where

    def test_event(self, cam: CameraConfig, animal: str, confidence: str, candidate: str) -> dict[str, Any]:
        now = dt_util.utcnow()
        rule = self.rules.get(animal.lower())
        if candidate == "auto":
            candidate_name = rule.name if rule and rule.priority else ""
        elif candidate == "yes":
            candidate_name = next((a.name for a in self.animals.values() if a.priority), animal)
        else:
            candidate_name = ""
        verdict = logic.Verdict(
            rule.name if rule else animal,
            confidence,
            "na",
            "test description" if logic.is_unknown(animal) else "",
            candidate_name,
            "",
            "",
        )
        data = self.event_data(cam, verdict, "test", now, test=True)
        self.hass.bus.async_fire(EVENT_SIGHTING, data)
        self.hass.async_create_task(self._alert(cam, verdict, now, None, test=True))
        return data


def builtin_switch_key(key: str) -> str:
    return f"builtin:{key}"


def media_root(hass: HomeAssistant) -> tuple[str, Path]:
    """The media folder the AI reads attachments from, and our folder inside it."""
    media_dirs = hass.config.media_dirs or {"local": hass.config.path("media")}
    key = "local" if "local" in media_dirs else next(iter(media_dirs))
    return key, Path(media_dirs[key]) / MEDIA_SUBDIR


def reference_file(hass: HomeAssistant, animal_slug: str) -> Path:
    return media_root(hass)[1] / "reference" / f"{animal_slug}.jpg"


def save_reference(source: Path | bytes, target: Path, max_side: int = 1280) -> None:
    """Store a reference photo as a JPEG no larger than `max_side` (fewer AI tokens)."""
    from io import BytesIO  # noqa: PLC0415

    from PIL import Image  # noqa: PLC0415

    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(BytesIO(source) if isinstance(source, bytes) else source) as original:
        img = original.convert("RGB")
        img.thumbnail((max_side, max_side))
        buffer = BytesIO()
        img.save(buffer, "JPEG", quality=90)
    _write(target, buffer.getvalue())


def remove_file(path: Path) -> None:
    path.unlink(missing_ok=True)

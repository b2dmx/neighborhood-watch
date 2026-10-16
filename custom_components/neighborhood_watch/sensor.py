"""Sensors: the night summary, AI health, shadow comparison, cameras and animals."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import WatchConfigEntry
from .const import CONF_MODE, MODE_LIVE
from .engine import WatchEngine, _dt
from .entity import WatchEntity

STATE_LIMIT = 255


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WatchConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    watch = entry.runtime_data
    async_add_entities(
        [
            NightSummary(watch),
            LastSighting(watch),
            AiCalls(watch),
            ShadowMismatches(watch),
            ToReview(watch),
            DetectorAgreement(watch),
            LastScan(watch),
            Wildlife(watch),
        ]
    )
    for cam in watch.cameras.values():
        async_add_entities(
            [CameraAnimal(watch, "current_animal", camera=cam), LastDetection(watch, "last_detection", camera=cam)],
            config_subentry_id=cam.subentry_id,
        )
    for animal in watch.animals.values():
        entities: list[SensorEntity] = [
            LastSeen(watch, "last_seen", animal=animal),
            Location(watch, "location", animal=animal),
            SightingsTonight(watch, "sightings_tonight", animal=animal),
            SightingsWeek(watch, "sightings_week", animal=animal),
        ]
        if animal.priority:
            entities.append(LastConfirmed(watch, "last_confirmed", animal=animal))
        async_add_entities(entities, config_subentry_id=animal.subentry_id)
    for kind in watch.species.values():
        async_add_entities(
            [
                LastSeen(watch, "last_seen", animal=kind),
                Location(watch, "location", animal=kind),
                SightingsTonight(watch, "sightings_tonight", animal=kind),
                SightingsWeek(watch, "sightings_week", animal=kind),
            ],
            config_subentry_id=kind.subentry_id,
        )


# --- hub ----------------------------------------------------------------------------


class NightSummary(WatchEntity, SensorEntity):
    _attr_icon = "mdi:weather-night"

    def __init__(self, watch: WatchEngine) -> None:
        super().__init__(watch, "night_summary")

    @property
    def native_value(self) -> str | None:
        summary = self.watch.data.get("summary")
        return summary["text"][:STATE_LIMIT] if summary else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        summary = self.watch.data.get("summary") or {}
        return {k: summary.get(k) for k in ("lines", "counts", "places", "built_at", "night_started")}


class Wildlife(WatchEntity, SensorEntity):
    """Every wildlife kind seen, tracked here instead of a device per animal."""

    _attr_icon = "mdi:paw-outline"

    def __init__(self, watch: WatchEngine) -> None:
        super().__init__(watch, "wildlife")

    @property
    def native_value(self) -> str | None:
        kinds = self.watch.wildlife_seen()
        if not kinds:
            return None
        name, k = next(iter(kinds.items()))
        return f"{name} · {k.get('where')}"[:STATE_LIMIT]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"kinds": self.watch.wildlife_seen()}


class LastSighting(WatchEntity, SensorEntity):
    _attr_icon = "mdi:paw"

    def __init__(self, watch: WatchEngine) -> None:
        super().__init__(watch, "last_sighting")

    @property
    def native_value(self) -> str | None:
        last = self.watch.data.get("last_sighting")
        if not last:
            return None
        return f"{last.get('animal')} · {last.get('camera')}"[:STATE_LIMIT]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return dict(self.watch.data.get("last_sighting") or {})


class AiCalls(WatchEntity, SensorEntity):
    _attr_icon = "mdi:robot-outline"
    _attr_native_unit_of_measurement = "calls"

    def __init__(self, watch: WatchEngine) -> None:
        super().__init__(watch, "ai_calls")

    @property
    def native_value(self) -> int:
        models = self.watch.data.get("ai", {}).get("models", {})
        return sum(m.get("calls", 0) for m in models.values())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        ai = self.watch.data.get("ai", {})
        per_model = {}
        for entity_id, stats in ai.get("models", {}).items():
            state = self.hass.states.get(entity_id)
            per_model[state.name if state else entity_id] = stats
        return {"quota_day": ai.get("day"), "models": per_model, "last_models_used": ai.get("last_models")}


class ShadowMismatches(WatchEntity, SensorEntity):
    _attr_icon = "mdi:compare-horizontal"

    def __init__(self, watch: WatchEngine) -> None:
        super().__init__(watch, "shadow_mismatches")

    def _tonight(self) -> list[dict[str, Any]]:
        since = self.watch.night_start()
        rows = self.watch.data.get("comparisons", [])
        return [r for r in rows if since is None or (_dt(r.get("time")) or since) > since]

    @property
    def native_value(self) -> int:
        return sum(1 for r in self._tonight() if not r.get("match"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        tonight = self._tonight()
        return {
            "mode": self.watch.options.get(CONF_MODE, MODE_LIVE),
            "compared_tonight": len(tonight),
            "matched_tonight": sum(1 for r in tonight if r.get("match")),
            "recent": list(reversed(self.watch.data.get("comparisons", [])[-30:])),
        }


# --- cameras --------------------------------------------------------------------------


class CameraAnimal(WatchEntity, SensorEntity):
    """What this camera is seeing now (clears itself after the on-camera window)."""

    _attr_icon = "mdi:cctv"

    @property
    def native_value(self) -> str:
        return self.watch.camera_state(self.camera.slug).get("current") or "None"


class LastDetection(WatchEntity, SensorEntity):
    _attr_icon = "mdi:history"

    @property
    def native_value(self) -> str | None:
        last = self.watch.camera_state(self.camera.slug).get("last")
        if not last:
            return None
        if last.get("animal") is None:
            return "Snapshot failed"
        return f"{last['animal']} ({last.get('confidence')})"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.watch.camera_state(self.camera.slug)
        return {
            **(state.get("last") or {}),
            "last_check": state.get("last_check"),
            "camera_name": self.camera.name,  # the camera's device may be named differently
        }


# --- animals --------------------------------------------------------------------------


class LastSeen(WatchEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-outline"

    @property
    def native_value(self) -> datetime | None:
        return self.watch.last_seen(self.animal)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """How it looks, for the card's drawn icon when there's no photo yet."""
        a = self.animal
        if getattr(a, "kind", "") != "animal":
            return {}
        return {"pattern": a.pattern, "base": a.base, "looks": f"{a.description} {a.tips}"[:600]}


class Location(WatchEntity, SensorEntity):
    _attr_icon = "mdi:map-marker"

    @property
    def native_value(self) -> str:
        return self.watch.animal_state(self.animal).get("location") or "Not seen"


class SightingsTonight(WatchEntity, SensorEntity):
    _attr_icon = "mdi:counter"
    _attr_native_unit_of_measurement = "sightings"

    @property
    def native_value(self) -> int:
        return len(self.watch.counted_sightings(self.animal, self.watch.night_start()))


class SightingsWeek(WatchEntity, SensorEntity):
    _attr_icon = "mdi:calendar-week"
    _attr_native_unit_of_measurement = "sightings"

    @property
    def native_value(self) -> int:
        since = dt_util.utcnow() - timedelta(days=7)
        return len(self.watch.counted_sightings(self.animal, since))


class LastConfirmed(WatchEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:check-decagram"

    @property
    def native_value(self) -> datetime | None:
        return _dt(self.watch.animal_state(self.animal).get("last_confirmed"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"camera": self.watch.animal_state(self.animal).get("confirmed_where")}


class ToReview(WatchEntity, SensorEntity):
    """Sightings waiting for a person's answer (the card lists them with their photos)."""

    _attr_icon = "mdi:clipboard-check-outline"
    _attr_native_unit_of_measurement = "sightings"

    def __init__(self, watch: WatchEngine) -> None:
        super().__init__(watch, "to_review")

    @property
    def native_value(self) -> int:
        return len(self.watch.review_queue())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "items": [
                {k: s.get(k) for k in ("id", "time", "where", "animal", "confidence")}
                for s in self.watch.review_queue()[:40]
            ]
        }


class DetectorAgreement(WatchEntity, SensorEntity):
    """How often the local detector agrees with the AI on "is an animal there" (last 7 days)."""

    _attr_icon = "mdi:radar"
    _attr_native_unit_of_measurement = "%"

    def __init__(self, watch: WatchEngine) -> None:
        super().__init__(watch, "detector_agreement")

    def _rows(self) -> list[dict[str, Any]]:
        since = dt_util.utcnow() - timedelta(days=7)
        rows = self.watch.data.get("detector", {}).get("log", [])
        return [r for r in rows if (_dt(r.get("time")) or since) >= since]

    @property
    def native_value(self) -> float | None:
        rows = self._rows()
        if not rows:
            return None
        return round(100 * sum(r["kind"] == "agree" for r in rows) / len(rows), 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rows = self._rows()
        return {
            "compared": len(rows),
            "misses": sum(r["kind"] == "miss" for r in rows),
            "extras": sum(r["kind"] == "extra" for r in rows),
            "recent_misses": [r for r in reversed(rows) if r["kind"] == "miss"][:10],
            "last_error": self.watch.data.get("detector", {}).get("last_error"),
        }


class LastScan(WatchEntity, SensorEntity):
    """The latest bulk scan: its state while running, then how many visits it found."""

    _attr_icon = "mdi:folder-search-outline"

    def __init__(self, watch: WatchEngine) -> None:
        super().__init__(watch, "last_scan")

    @property
    def native_value(self) -> str | None:
        scan = self.watch.latest_scan()
        if not scan:
            return None
        if scan["state"] == "scanning":
            return f"Scanning {scan.get('files_done') or 0}/{scan.get('files_total') or '?'}"
        if scan["state"] == "error":
            return "Error"
        return f"{len(scan.get('visits') or [])} visits"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        scan = self.watch.latest_scan() or {}
        return {k: scan.get(k) for k in ("folder", "state", "started", "finished", "tally", "report", "error")} | {
            "visits": (scan.get("visits") or [])[:50]
        }

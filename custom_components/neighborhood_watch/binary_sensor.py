"""Binary sensors: on camera now, recently absent, and the priority animal's confirmed signal."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import WatchConfigEntry
from .engine import _dt
from .entity import WatchEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WatchConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    watch = entry.runtime_data
    for animal in watch.animals.values():
        entities: list[BinarySensorEntity] = [OnCamera(watch, "on_camera", animal=animal)]
        if animal.priority:
            entities += [
                Confirmed(watch, "confirmed_on_camera", animal=animal),
                ConfirmedToday(watch, "confirmed_today", animal=animal),
            ]
        else:
            entities.append(RecentlyAbsent(watch, "recently_absent", animal=animal))
        async_add_entities(entities, config_subentry_id=animal.subentry_id)
    async_add_entities(
        [WildlifeOnCamera(watch, "wildlife_on_camera"), WildlifeOnCamera(watch, "pest_on_camera")]
    )


class WildlifeOnCamera(WatchEntity, BinarySensorEntity):
    """Any wildlife (or a pest) seen for sure within the on-camera window; for automations
    such as lights."""

    _attr_icon = "mdi:paw"

    @property
    def _now(self) -> dict[str, Any] | None:
        return self.watch.wildlife_now(pests_only=self._attr_translation_key == "pest_on_camera")

    @property
    def is_on(self) -> bool:
        return self._now is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        now = self._now or {}
        return {"animal": now.get("animal"), "camera": now.get("camera")}


class OnCamera(WatchEntity, BinarySensorEntity):
    """On for the on-camera window after a sighting that counts (medium or high)."""

    _attr_icon = "mdi:cctv"

    @property
    def is_on(self) -> bool:
        return self.watch.until_active(self.watch.animal_state(self.animal).get("on_camera_until"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.watch.animal_state(self.animal)
        return {"camera": state.get("on_camera_where") if self.is_on else None}


class Confirmed(WatchEntity, BinarySensorEntity):
    """HIGH-confidence only: the signal a feeder can safely trigger on."""

    _attr_icon = "mdi:check-decagram"

    @property
    def is_on(self) -> bool:
        return self.watch.until_active(self.watch.animal_state(self.animal).get("confirmed_until"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"camera": self.watch.animal_state(self.animal).get("confirmed_where")}


class ConfirmedToday(WatchEntity, BinarySensorEntity):
    _attr_icon = "mdi:calendar-check"

    @property
    def is_on(self) -> bool:
        last = _dt(self.watch.animal_state(self.animal).get("last_confirmed"))
        return bool(last and dt_util.as_local(last).date() == dt_util.now().date())


class RecentlyAbsent(WatchEntity, BinarySensorEntity):
    """Not seen for the absent window; the AI must then be sure (both models at high)."""

    _attr_icon = "mdi:help-circle-outline"

    @property
    def is_on(self) -> bool:
        return self.watch.is_absent(self.animal)

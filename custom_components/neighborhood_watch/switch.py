"""Quick on/off switches, all starting off except Alerts.

Each animal and wildlife kind: Alerts, Critical alerts (rings through silent mode at its
critical cameras), and Missing mode for named animals. Each camera: Critical alerts
(anything there rings, except the animals its page lists). The hub: Critical alerts for
unknown animals, other wildlife and unsure motion."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WatchConfigEntry
from .const import BUILTIN_KEYS
from .engine import builtin_switch_key
from .entity import WatchEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WatchConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    watch = entry.runtime_data
    for subject in [*watch.animals.values(), *watch.species.values()]:
        entities: list[SwitchEntity] = [
            Alerts(watch, "alerts", animal=subject),
            CriticalAlerts(watch, "critical_alerts", animal=subject),
        ]
        if subject.kind == "animal":
            entities.append(MissingMode(watch, "missing_mode", animal=subject))
        async_add_entities(entities, config_subentry_id=subject.subentry_id)
    for cam in watch.cameras.values():
        async_add_entities(
            [CriticalAlerts(watch, "critical_alerts", camera=cam)],
            config_subentry_id=cam.subentry_id,
        )
    # The catch-alls (unknown animals, other wildlife, unsure motion) live on the hub.
    async_add_entities([CatchAllCritical(watch, f"{key}_critical") for key in BUILTIN_KEYS.values()])


class MissingMode(WatchEntity, SwitchEntity):
    _attr_icon = "mdi:alert-circle-outline"

    @property
    def is_on(self) -> bool:
        return self.watch.missing_mode(self.animal)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.watch.async_set_missing_mode(self.animal, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.watch.async_set_missing_mode(self.animal, False)


class Alerts(WatchEntity, SwitchEntity):
    """Pauses an animal's alerts without losing where they're set to go."""

    _attr_icon = "mdi:bell"

    @property
    def is_on(self) -> bool:
        return self.watch.alerts_on(self.animal.switch_key)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.watch.async_set_alerts(self.animal.switch_key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.watch.async_set_alerts(self.animal.switch_key, False)


class CriticalAlerts(WatchEntity, SwitchEntity):
    _attr_icon = "mdi:bell-alert"

    @property
    def _key(self) -> str:
        return (self.camera or self.animal).switch_key

    @property
    def is_on(self) -> bool:
        return self.watch.critical_on(self._key)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.watch.async_set_critical(self._key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.watch.async_set_critical(self._key, False)


class CatchAllCritical(CriticalAlerts):
    @property
    def _key(self) -> str:
        return builtin_switch_key(self._attr_translation_key.removesuffix("_critical"))

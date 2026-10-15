"""Switches: missing mode for each priority animal, and critical alerts per camera and per
wildlife kind (they break through silent mode, except where a person has a quiet zone)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WatchConfigEntry
from .entity import WatchEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WatchConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    watch = entry.runtime_data
    for animal in watch.animals.values():
        if animal.priority:
            async_add_entities(
                [MissingMode(watch, "missing_mode", animal=animal)],
                config_subentry_id=animal.subentry_id,
            )
    for cam in watch.cameras.values():
        async_add_entities(
            [CriticalAlerts(watch, "critical_alerts", camera=cam)],
            config_subentry_id=cam.subentry_id,
        )
    for kind in watch.species.values():
        if kind.critical_switch:
            async_add_entities(
                [CriticalAlerts(watch, "critical_alerts", animal=kind)],
                config_subentry_id=kind.subentry_id,
            )


class MissingMode(WatchEntity, SwitchEntity):
    _attr_icon = "mdi:alert-circle-outline"

    @property
    def is_on(self) -> bool:
        return self.watch.missing_mode(self.animal)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.watch.async_set_missing_mode(self.animal, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.watch.async_set_missing_mode(self.animal, False)


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

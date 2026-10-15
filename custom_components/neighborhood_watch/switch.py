"""Quick on/off switches, all starting off except Alerts.

Each animal and wildlife kind: Alerts, Critical alerts (rings through silent mode at its
critical cameras), and Missing mode for named animals. Each camera: Critical alerts
(anything there rings, except the animals its page lists). The hub: Critical alerts for
unknown animals, other wildlife and unsure motion, and one per person: gets critical alerts
(starts on)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WatchConfigEntry
from .const import BUILTIN_KEYS
from .engine import RecipientConfig, WatchEngine, builtin_switch_key
from .entity import WatchEntity, phone_device


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
    for person in watch.recipients:
        switch = PersonCritical(watch, person)
        if switch.device_entry is not None:  # on their phone: listed under their page
            async_add_entities([switch], config_subentry_id=person.subentry_id)
        else:  # on the hub, which belongs to no single page
            async_add_entities([switch])


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


class PersonCritical(WatchEntity, SwitchEntity):
    """Off: this person's critical alerts arrive as normal notifications."""

    _attr_icon = "mdi:bell-ring"

    def __init__(self, watch: WatchEngine, person: RecipientConfig) -> None:
        super().__init__(watch, "person_critical")
        self.person = person
        # Shown on the person's phone (no device of ours); the hub if no phone is found.
        if (phone := phone_device(watch.hass, person.notify, person.person)) is not None:
            self._attr_device_info = None
            self.device_entry = phone
        self._attr_translation_placeholders = {"person": person.name}
        self._attr_unique_id = f"{watch.entry.entry_id}_{person.subentry_id}_person_critical"

    @property
    def is_on(self) -> bool:
        return self.watch.person_critical_on(self.person)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.watch.async_set_critical(self.person.switch_key, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.watch.async_set_critical(self.person.switch_key, False)

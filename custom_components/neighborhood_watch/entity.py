"""Shared entity base: one hub device, a device per camera and a device per animal."""
from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, NAME
from .engine import AnimalConfig, CameraConfig, SpeciesConfig, WatchEngine


class WatchEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        watch: WatchEngine,
        key: str,
        camera: CameraConfig | None = None,
        animal: AnimalConfig | SpeciesConfig | None = None,
    ) -> None:
        self.watch = watch
        self.camera = camera
        self.animal = animal
        entry = watch.entry
        hub = (DOMAIN, entry.entry_id)
        self._attr_translation_key = key
        if camera is not None:
            owner = camera.subentry_id
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"camera_{owner}")},
                name=camera.name,
                model="Camera",
                manufacturer=NAME,
                via_device_id=watch.hub_device_id,
                entry_type=DeviceEntryType.SERVICE,
            )
        elif animal is not None:
            owner = animal.subentry_id
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"animal_{owner}")},
                name=animal.name,
                model="Wildlife" if animal.kind == "species" else (animal.species.title() or "Animal"),
                manufacturer=NAME,
                via_device_id=watch.hub_device_id,
                entry_type=DeviceEntryType.SERVICE,
            )
        else:
            owner = "hub"
            self._attr_device_info = DeviceInfo(
                identifiers={hub},
                name=NAME,
                manufacturer="Goobis",
                model=NAME,
                entry_type=DeviceEntryType.SERVICE,
            )
        self._attr_unique_id = f"{entry.entry_id}_{owner}_{key}"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self.watch.signal, self._handle_update)
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

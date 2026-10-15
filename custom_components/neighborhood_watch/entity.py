"""Shared entity base: one hub device and a device per animal. A camera's entities join
the camera's own device (from its integration), so cameras aren't listed twice."""
from __future__ import annotations

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
from homeassistant.util import slugify

from .const import DOMAIN, NAME
from .engine import AnimalConfig, CameraConfig, SpeciesConfig, WatchEngine


def camera_device(hass: HomeAssistant, camera_entity: str) -> dr.DeviceEntry | None:
    """The device the camera entity already belongs to, if any."""
    entry = er.async_get(hass).async_get(camera_entity)
    if entry is None or entry.device_id is None:
        return None
    return dr.async_get(hass).async_get(entry.device_id)


def phone_device(hass: HomeAssistant, notify_service: str, person: str | None) -> dr.DeviceEntry | None:
    """The phone behind a person's notify service (mobile_app_<device>), else a device
    tracker of theirs."""
    devices = dr.async_get(hass)
    if notify_service.startswith("mobile_app_"):
        wanted = notify_service.removeprefix("mobile_app_")
        for app in hass.config_entries.async_entries("mobile_app"):
            for device in dr.async_entries_for_config_entry(devices, app.entry_id):
                if slugify(device.name or "") == wanted:
                    return device
    state = hass.states.get(person) if person else None
    for tracker in (state.attributes.get("device_trackers") or []) if state else []:
        if (device := camera_device(hass, tracker)) is not None:
            return device
    return None


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
            existing = camera_device(watch.hass, camera.camera)
            if existing is not None:
                # Shown on the camera's own device (HA links it like a helper's entity).
                self.device_entry = existing
            else:  # a camera without a device of its own
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

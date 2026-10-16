"""Images: each animal's last photo and each camera's last checked frame — the exact frame
the AI judged, not a later snapshot."""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WatchConfigEntry
from .engine import AnimalConfig, CameraConfig, WatchEngine, _dt, _read
from .entity import WatchEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WatchConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    watch = entry.runtime_data
    for cam in watch.cameras.values():
        async_add_entities([CameraFrame(watch, cam)])
    for animal in watch.animals.values():
        async_add_entities(
            [AnimalPhoto(watch, animal), ReferencePhoto(watch, animal)],
            config_subentry_id=animal.subentry_id,
        )


class _Image(WatchEntity, ImageEntity):
    _attr_content_type = "image/jpeg"

    def __init__(self, watch: WatchEngine, key: str, **kwargs) -> None:
        WatchEntity.__init__(self, watch, key, **kwargs)
        ImageEntity.__init__(self, watch.hass)
        self._attr_image_last_updated = self._updated_at()

    def _updated_at(self) -> datetime | None:
        return None

    @callback
    def _handle_update(self) -> None:
        updated = self._updated_at()
        if updated != self._attr_image_last_updated:
            self._attr_image_last_updated = updated
        self.async_write_ha_state()


class CameraFrame(_Image):
    def __init__(self, watch: WatchEngine, cam: CameraConfig) -> None:
        super().__init__(watch, "last_frame", camera=cam)

    def _updated_at(self) -> datetime | None:
        frame = self.watch.frames.get(self.camera.slug)
        return frame.at if frame else None

    async def async_image(self) -> bytes | None:
        frame = self.watch.frames.get(self.camera.slug)
        return frame.content if frame else None


class AnimalPhoto(_Image):
    def __init__(self, watch: WatchEngine, animal: AnimalConfig) -> None:
        super().__init__(watch, "last_photo", animal=animal)

    def _updated_at(self) -> datetime | None:
        return _dt(self.watch.animal_state(self.animal).get("photo_at"))

    async def async_image(self) -> bytes | None:
        return await self.hass.async_add_executor_job(_read, self.watch.photo_path(self.animal))


class ReferencePhoto(_Image):
    """The photo sent to the AI beside each frame, when reference photos are on."""

    def __init__(self, watch: WatchEngine, animal: AnimalConfig) -> None:
        super().__init__(watch, "reference_photo", animal=animal)

    @property
    def available(self) -> bool:
        return self.watch.has_reference(self.animal)

    def _updated_at(self) -> datetime | None:
        return self.watch.reference_at(self.animal)

    async def async_image(self) -> bytes | None:
        return await self.hass.async_add_executor_job(_read, self.watch.reference_path(self.animal))

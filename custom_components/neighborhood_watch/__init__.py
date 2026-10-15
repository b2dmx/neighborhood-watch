"""Neighborhood Watch: cameras → AI identification → per-animal tracking and photos."""
from __future__ import annotations

from pathlib import Path
from typing import Any

try:  # Home Assistant 2026.10 replaced voluptuous with probatio (same API).
    import probatio as vol
except ImportError:  # pragma: no cover - older Home Assistant
    import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import DOMAIN, NAME
from .engine import AnimalConfig, CameraConfig, WatchEngine

PLATFORMS = [Platform.BINARY_SENSOR, Platform.IMAGE, Platform.SENSOR, Platform.SWITCH]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type WatchConfigEntry = ConfigEntry[WatchEngine]

LEVELS = vol.In(["high", "medium", "low"])


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    await _register_card(hass)
    if getattr(hass, "http", None) is not None:
        from .views import FrameView  # noqa: PLC0415

        hass.http.register_view(FrameView())

    def engine() -> WatchEngine:
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            if isinstance(getattr(entry, "runtime_data", None), WatchEngine):
                return entry.runtime_data
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")

    def camera(watch: WatchEngine, ref: Any, required: bool = True) -> CameraConfig | None:
        if not ref:
            if required:
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unknown_camera")
            return None
        cam = watch.camera_by_ref(str(ref))
        if cam is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_camera",
                translation_placeholders={"camera": str(ref)},
            )
        return cam

    def animal(watch: WatchEngine, ref: Any) -> AnimalConfig:
        found = watch.animal_by_name(str(ref or ""))
        if found is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_animal",
                translation_placeholders={"animal": str(ref)},
            )
        return found

    async def identify(call: ServiceCall) -> ServiceResponse:
        watch = engine()
        cam = camera(watch, call.data["camera"])
        result = await watch.async_check(cam.slug, force=True)
        return result or {"animal": None}

    async def test_sighting(call: ServiceCall) -> ServiceResponse:
        watch = engine()
        cam = camera(watch, call.data["camera"])
        return watch.test_event(
            cam,
            call.data["animal"],
            call.data["confidence"],
            call.data["priority_candidate"],
        )

    async def mark_wrong(call: ServiceCall) -> ServiceResponse:
        watch = engine()
        return await watch.async_mark_wrong(
            animal(watch, call.data["animal"]), camera(watch, call.data.get("camera"), False)
        )

    async def set_reference_photo(call: ServiceCall) -> None:
        watch = engine()
        result = await watch.async_set_reference(
            animal(watch, call.data["animal"]), camera(watch, call.data.get("camera"), False)
        )
        if result == "no_photo":
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_photo")

    async def correct(call: ServiceCall) -> ServiceResponse:
        watch = engine()
        result = await watch.async_correct(
            call.data.get("sighting_id"),
            camera(watch, call.data.get("camera"), False),
            call.data["answer"],
        )
        if result.get("error"):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_animal",
                translation_placeholders={"animal": call.data["answer"]},
            )
        return result

    async def scan(call: ServiceCall) -> ServiceResponse:
        watch = engine()
        try:
            sid = await watch.async_scan(
                call.data["folder"], call.data.get("identify", True), call.data.get("limit", 50)
            )
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="scan_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        return {"scan_id": sid}

    async def build_summary(call: ServiceCall) -> ServiceResponse:
        summary = engine().build_summary()
        return {"text": summary.text, "lines": summary.lines}

    hass.services.async_register(
        DOMAIN,
        "identify",
        identify,
        vol.Schema({vol.Required("camera"): cv.string}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "test_sighting",
        test_sighting,
        vol.Schema(
            {
                vol.Required("camera"): cv.string,
                vol.Required("animal"): cv.string,
                vol.Optional("confidence", default="medium"): LEVELS,
                vol.Optional("priority_candidate", default="auto"): vol.In(["auto", "yes", "no"]),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "mark_wrong",
        mark_wrong,
        vol.Schema({vol.Required("animal"): cv.string, vol.Optional("camera"): cv.string}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "set_reference_photo",
        set_reference_photo,
        vol.Schema({vol.Required("animal"): cv.string, vol.Optional("camera"): cv.string}),
    )
    hass.services.async_register(
        DOMAIN,
        "correct",
        correct,
        vol.Schema(
            {
                vol.Required("answer"): cv.string,
                vol.Optional("sighting_id"): cv.string,
                vol.Optional("camera"): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "scan",
        scan,
        vol.Schema(
            {
                vol.Required("folder"): cv.string,
                vol.Optional("identify", default=True): cv.boolean,
                vol.Optional("limit", default=50): vol.All(vol.Coerce(int), vol.Range(min=0, max=1000)),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "build_summary",
        build_summary,
        vol.Schema({}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: WatchConfigEntry) -> bool:
    watch = WatchEngine(hass, entry)
    # The hub device first: camera and animal devices hang off it.
    hub = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=NAME,
        manufacturer="Goobis",
        model=NAME,
        entry_type=dr.DeviceEntryType.SERVICE,
    )
    watch.hub_device_id = hub.id
    await watch.async_start()
    entry.runtime_data = watch
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WatchConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    watch = getattr(entry, "runtime_data", None)
    if unloaded and isinstance(watch, WatchEngine):
        await watch.async_stop()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: WatchConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


CARD_URL = f"/{DOMAIN}/neighborhood-watch-card.js"


async def _register_card(hass: HomeAssistant) -> None:
    """Serve the dashboard card and load it on every dashboard (no resource to add)."""
    if getattr(hass, "http", None) is None:
        return
    from homeassistant.components.http import StaticPathConfig  # noqa: PLC0415

    path = Path(__file__).parent / "frontend" / "neighborhood-watch-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(path), True)])
    if "frontend" in hass.config.components:
        from homeassistant.components.frontend import add_extra_js_url  # noqa: PLC0415

        integration = await async_get_integration(hass, DOMAIN)
        add_extra_js_url(hass, f"{CARD_URL}?v={integration.version}")


"""A camera's entities join the camera's own device; no duplicate camera devices."""
from __future__ import annotations

from pathlib import Path

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.neighborhood_watch.const import DOMAIN

from .test_ha_integration import FIRST, NIGHT, camera_sub


async def test_camera_entities_join_the_cameras_own_device(hass: HomeAssistant, tmp_path: Path, freezer):
    freezer.move_to(NIGHT)
    hass.config.config_dir = str(tmp_path)
    hass.config.media_dirs = {"local": str(tmp_path / "media")}
    assert await async_setup_component(hass, "homeassistant", {})
    hass.states.async_set(FIRST, "unknown")

    # The camera as its own integration made it: a device with the camera entity on it.
    vendor = MockConfigEntry(domain="vendor_cams")
    vendor.add_to_hass(hass)
    devices, entities = dr.async_get(hass), er.async_get(hass)
    cam_device = devices.async_get_or_create(
        config_entry_id=vendor.entry_id, identifiers={("vendor_cams", "cam-1")}, name="Backyard Cam"
    )
    entities.async_get_or_create("camera", "vendor_cams", "cam-1", suggested_object_id="backyard", device_id=cam_device.id)

    entry = MockConfigEntry(
        domain=DOMAIN,
        minor_version=2,
        options={"first_look": [FIRST], "use_dark": False},
        subentries_data=[
            camera_sub("Backyard", "backyard", "binary_sensor.backyard_motion"),
            camera_sub("Shed", "shed", "binary_sensor.shed_motion"),  # no device of its own
        ],
    )
    entry.add_to_hass(hass)
    backyard_id = next(s.subentry_id for s in entry.subentries.values() if s.title == "Backyard")
    # A leftover from before: our own device for that camera.
    old = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id=backyard_id,
        identifiers={(DOMAIN, f"camera_{backyard_id}")},
        name="Backyard",
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    ours = [e for e in er.async_entries_for_config_entry(entities, entry.entry_id) if e.config_subentry_id == backyard_id]
    assert ours and {e.device_id for e in ours} == {cam_device.id}
    assert devices.async_get(old.id) is None
    assert devices.async_get(cam_device.id) is not None
    shed = next(s.subentry_id for s in entry.subentries.values() if s.title == "Shed")
    assert devices.async_get_device_by_identifier((DOMAIN, f"camera_{shed}"), entry.entry_id) is not None


async def test_person_switch_sits_on_their_phone(hass: HomeAssistant, tmp_path: Path, freezer):
    freezer.move_to(NIGHT)
    hass.config.config_dir = str(tmp_path)
    hass.config.media_dirs = {"local": str(tmp_path / "media")}
    assert await async_setup_component(hass, "homeassistant", {})
    hass.states.async_set(FIRST, "unknown")
    app = MockConfigEntry(domain="mobile_app")
    app.add_to_hass(hass)
    devices, entities = dr.async_get(hass), er.async_get(hass)
    phone = devices.async_get_or_create(
        config_entry_id=app.entry_id, identifiers={("mobile_app", "phone-1")}, name="Ann's Phone"
    )
    from .test_ha_v03 import person_sub

    entry = MockConfigEntry(
        domain=DOMAIN,
        minor_version=2,
        options={"first_look": [FIRST], "use_dark": False},
        subentries_data=[
            person_sub("Ann", notify_service="mobile_app_ann_s_phone"),
            person_sub("Bo", notify_service="mobile_app_unknown"),
        ],
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    ann = entities.async_get("switch.ann_s_phone_ann_gets_critical_alerts") or next(
        e for e in er.async_entries_for_config_entry(entities, entry.entry_id) if e.translation_key == "person_critical" and e.device_id == phone.id
    )
    assert ann.device_id == phone.id
    assert hass.states.get(ann.entity_id).state == "on"
    bo = [e for e in er.async_entries_for_config_entry(entities, entry.entry_id) if e.translation_key == "person_critical" and e.device_id != phone.id]
    assert len(bo) == 1 and hass.states.get(bo[0].entity_id) is not None  # no phone found: on the hub

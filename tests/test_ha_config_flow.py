"""Onboarding, settings and the Add camera / Add animal flows inside a real Home Assistant."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.neighborhood_watch.const import DOMAIN
from custom_components.neighborhood_watch.logic import DEFAULT_INSTRUCTIONS


@pytest.fixture
async def ready(hass: HomeAssistant, tmp_path: Path):
    hass.config.config_dir = str(tmp_path)
    hass.config.media_dirs = {"local": str(tmp_path / "media")}
    assert await async_setup_component(hass, "homeassistant", {})
    hass.states.async_set("ai_task.gemini", "unknown")
    hass.states.async_set("ai_task.gemini_pro", "unknown")
    # A camera with its own device carrying motion, animal and person sensors.
    entry = MockConfigEntry(domain="demo_cameras")
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("demo_cameras", "cam1")}, name="Backyard Cam"
    )
    ent_reg = er.async_get(hass)
    for domain, object_id, name in (
        ("camera", "backyard_cam_high_resolution_channel", "High resolution channel"),
        ("binary_sensor", "backyard_cam_motion", "Motion"),
        ("binary_sensor", "backyard_cam_animal_detected", "Animal detected"),
        ("binary_sensor", "backyard_cam_person_detected", "Person detected"),
        ("binary_sensor", "backyard_cam_vehicle_detected", "Vehicle detected"),
        ("sensor", "backyard_cam_uptime", "Uptime"),
    ):
        ent_reg.async_get_or_create(
            domain,
            "demo_cameras",
            object_id,
            suggested_object_id=object_id,
            device_id=device.id,
            original_name=name,
            config_entry=entry,
        )
    with (
        patch("custom_components.neighborhood_watch.async_setup_entry", return_value=True),
        patch("custom_components.neighborhood_watch.async_unload_entry", return_value=True),
    ):
        yield


async def test_onboarding_creates_cameras_and_animals(hass: HomeAssistant, ready):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"first_look": []})
    assert result["errors"] == {"first_look": "first_look_required"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"first_look": ["ai_task.gemini"], "second_opinion": ["ai_task.gemini_pro"], "paid_models": []},
    )
    assert result["step_id"] == "schedule"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "night_start": "20:00:00",
            "night_end": "08:00:00",
            "use_dark": True,
            "summary_time": "08:05:00",
            "on_camera_minutes": 10,
        },
    )
    assert result["step_id"] == "cameras"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"cameras": ["camera.backyard_cam_high_resolution_channel"]}
    )
    assert result["step_id"] == "camera"
    # Pre-filled from the device.
    schema = {str(k): k.default() for k in result["data_schema"].schema if callable(getattr(k, "default", None))}
    assert schema["name"] == "Backyard Cam"
    assert sorted(schema["triggers"]) == [
        "binary_sensor.backyard_cam_animal_detected",
        "binary_sensor.backyard_cam_motion",
    ]
    assert sorted(schema["skip_when"]) == [
        "binary_sensor.backyard_cam_person_detected",
        "binary_sensor.backyard_cam_vehicle_detected",
    ]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Garage",
            "triggers": schema["triggers"],
            "skip_when": schema["skip_when"],
            "cooldown": 15,
            "priority_cooldown": 30,
            "missing_cooldown": 2,
        },
    )
    assert result["step_id"] == "wildlife"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"wildlife": ["Raccoon", "Skunk"]})
    assert result["step_id"] == "animal"  # no phones to choose from: the people step is skipped
    luna = {
        "name": "Luna",
        "species": "cat",
        "description": "A calico, mostly white with dark blotches.",
        "pattern": "blotchy",
        "base": "white",
        "priority": True,
        "always_strict": True,
        "add_another": True,
    }
    result = await hass.config_entries.flow.async_configure(result["flow_id"], luna)
    assert result["errors"] == {"always_strict": "priority_not_strict"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {**luna, "always_strict": False})
    assert result["step_id"] == "animal"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**luna, "always_strict": False, "priority": False}
    )
    assert result["errors"] == {"name": "name_taken"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Pepper", "species": "cat", "pattern": "uniform", "base": "black", "priority": False,
         "always_strict": True, "add_another": False},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.options["first_look"] == ["ai_task.gemini"]
    assert entry.options["instructions"] == DEFAULT_INSTRUCTIONS
    subs = sorted((s.subentry_type, s.title) for s in entry.subentries.values())
    assert subs == [("animal", "Luna"), ("animal", "Pepper"), ("camera", "Garage"), ("species", "Raccoon"), ("species", "Skunk")]
    raccoon = next(s for s in entry.subentries.values() if s.title == "Raccoon")
    assert raccoon.data["pest"] is True and raccoon.data["alert_scope"] == "any"
    garage = next(s for s in entry.subentries.values() if s.title == "Garage")
    assert garage.data["slug"] == "garage"
    assert garage.data["camera"] == "camera.backyard_cam_high_resolution_channel"
    assert garage.data["priority_cooldown"] == 30


async def test_onboarding_without_ai_task_aborts(hass: HomeAssistant, ready):
    hass.states.async_remove("ai_task.gemini")
    hass.states.async_remove("ai_task.gemini_pro")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["reason"] == "no_ai_task"


async def test_onboarding_can_skip_cameras_and_animals(hass: HomeAssistant, ready):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"first_look": ["ai_task.gemini"]})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"night_start": "20:00:00", "night_end": "08:00:00", "use_dark": True, "summary_time": "08:05:00",
         "on_camera_minutes": 10},
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"cameras": []})
    assert result["step_id"] == "wildlife"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"wildlife": []})
    assert result["step_id"] == "animal"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"species": "cat"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert not result["result"].subentries


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Neighborhood Watch",
        options={"first_look": ["ai_task.gemini"]},
        subentries_data=[
            {
                "data": {"camera": "camera.backyard_cam_high_resolution_channel", "name": "Garage", "slug": "garage",
                         "triggers": [], "skip_when": [], "cooldown": 15},
                "subentry_type": "camera",
                "title": "Garage",
                "unique_id": "garage",
            },
            {
                "data": {"name": "Luna", "slug": "luna", "priority": True, "pattern": "blotchy", "base": "white",
                         "added": "2026-10-01T00:00:00+00:00"},
                "subentry_type": "animal",
                "title": "Luna",
                "unique_id": "luna",
            },
        ],
    )
    entry.add_to_hass(hass)
    return entry


async def test_add_camera_prefills_and_rejects_duplicates(hass: HomeAssistant, ready):
    entry = _entry(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "camera"), context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"camera": "camera.backyard_cam_high_resolution_channel"}
    )
    assert result["step_id"] == "details"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Garage", "cooldown": 3, "priority_cooldown": 0, "missing_cooldown": 0}
    )
    assert result["errors"] == {"name": "name_taken"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Driveway", "cooldown": 2, "priority_cooldown": 0, "missing_cooldown": 0},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    driveway = next(s for s in entry.subentries.values() if s.title == "Driveway")
    assert driveway.data["slug"] == "driveway"


async def test_reconfigure_camera_keeps_its_slug(hass: HomeAssistant, ready):
    entry = _entry(hass)
    garage = next(s for s in entry.subentries.values() if s.title == "Garage")
    result = await entry.start_subentry_reconfigure_flow(hass, garage.subentry_id)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"camera": "camera.backyard_cam_high_resolution_channel", "name": "Garage Door", "cooldown": 10,
         "priority_cooldown": 30, "missing_cooldown": 2},
    )
    assert result["reason"] == "reconfigure_successful"
    assert garage.data["slug"] == "garage"
    assert garage.title == "Garage Door"


async def test_add_and_edit_animal(hass: HomeAssistant, ready):
    entry = _entry(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "animal"), context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Unknown Cat", "species": "cat", "pattern": "na", "base": "na",
                            "priority": False, "always_strict": False},
    )
    assert result["errors"] == {"name": "name_reserved"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Milo", "species": "cat", "description": "Black, long-haired.",
                            "pattern": "uniform", "base": "black", "priority": False, "always_strict": False},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    milo = next(s for s in entry.subentries.values() if s.title == "Milo")
    assert milo.data["description"] == "Black, long-haired."
    assert milo.data["added"]

    result = await entry.start_subentry_reconfigure_flow(hass, milo.subentry_id)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Milo", "species": "cat", "description": "Black with a white bib.",
                            "pattern": "uniform", "base": "black", "priority": False, "always_strict": False,
                            "remove_reference": False},
    )
    assert result["reason"] == "reconfigure_successful"
    assert milo.data["description"] == "Black with a white bib."
    assert milo.data["slug"] == "milo"


async def test_options_identification_requires_animals_placeholder(hass: HomeAssistant, ready):
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "identification"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"instructions": "no placeholder here", "reset_instructions": False, "wildlife": ["Fox"],
         "absent_days": 14, "reference_photos": True},
    )
    assert result["errors"] == {"instructions": "missing_animals"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"instructions": "x", "reset_instructions": True, "wildlife": ["Fox"], "absent_days": 10,
         "reference_photos": False},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["instructions"] == DEFAULT_INSTRUCTIONS
    assert entry.options["absent_days"] == 10
    assert entry.options["first_look"] == ["ai_task.gemini"]


async def test_options_mode(hass: HomeAssistant, ready):
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "mode"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"mode": "shadow_reuse"})
    assert entry.options["mode"] == "shadow_reuse"


async def test_add_wildlife(hass: HomeAssistant, ready):
    entry = _entry(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "species"), context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Luna", "alert_scope": "any", "critical_switch": False, "pest": False, "in_summary": True}
    )
    assert result["errors"] == {"name": "name_taken"}  # an animal already has that name
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Raccoon", "description": "ringed tail", "alert_scope": "cameras", "alert_cameras": ["garage"],
         "critical_switch": True, "pest": True, "in_summary": True},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    raccoon = next(s for s in entry.subentries.values() if s.title == "Raccoon")
    assert raccoon.data["alert_cameras"] == ["garage"] and raccoon.data["pest"] is True


async def test_add_person(hass: HomeAssistant, ready):
    entry = _entry(hass)
    hass.services.async_register("notify", "mobile_app_phone", lambda call: None)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "recipient"), context={"source": config_entries.SOURCE_USER}
    )
    selector = next(v for k, v in result["data_schema"].schema.items() if str(k) == "notify_service")
    assert "mobile_app_phone" in selector.config["options"]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Sam", "notify_service": "notify.mobile_app_phone", "missing_alerts": True,
         "critical_sound": "default", "critical_volume": 0.5},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    sam = next(s for s in entry.subentries.values() if s.title == "Sam")
    assert sam.data["notify_service"] == "mobile_app_phone"


async def test_options_alerts_and_training(hass: HomeAssistant, ready):
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "alerts"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"alerts": False, "unknown_alerts": True, "correction_buttons": True}
    )
    assert entry.options["alerts"] is False
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "training"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"dataset": True, "dataset_dir": "/media/nas/nw", "dataset_gb": 50}
    )
    assert entry.options["dataset_dir"] == "/media/nas/nw" and entry.options["alerts"] is False



async def test_onboarding_people_step_lists_phones(hass: HomeAssistant, ready):
    hass.services.async_register("notify", "mobile_app_sams_phone", lambda call: None)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"first_look": ["ai_task.gemini"]})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"night_start": "20:00:00", "night_end": "08:00:00", "use_dark": True, "watch_always": False,
         "summary_time": "08:05:00", "on_camera_minutes": 10},
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"cameras": []})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"wildlife": ["Raccoon"]})
    assert result["step_id"] == "people"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"phones": ["mobile_app_sams_phone"]})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"species": "cat"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    sam = next(s for s in result["result"].subentries.values() if s.subentry_type == "recipient")
    assert (sam.title, sam.data["notify_service"]) == ("Sams Phone", "mobile_app_sams_phone")

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
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"first_look": [], "first_model_hours": {}})
    assert result["errors"] == {"first_look": "first_look_required"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"first_look": ["ai_task.gemini"], "second_opinion": ["ai_task.gemini_pro"], "paid_models": [],
         "first_model_hours": {"main_start": "06:00:00", "main_end": "22:00:00"}},
    )
    assert result["step_id"] == "schedule"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "night_start": "20:00:00",
            "night_end": "08:00:00",
            "use_dark": True,
            "more": {"summary_time": "08:05:00", "on_camera_minutes": 10},
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
            "timing": {"cooldown": 15, "priority_cooldown": 30, "missing_cooldown": 2},
            "deterrent": {},
        },
    )
    assert result["step_id"] == "wildlife"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"pests": ["Raccoon", "skunk"]})
    assert result["step_id"] == "animal"  # no phones to choose from: the people step is skipped
    assert "No cameras were picked" not in result["description_placeholders"]["notes"]
    assert "No phones with the Home Assistant app" in result["description_placeholders"]["notes"]
    assert {str(k) for k in result["data_schema"].schema} == {
        "name", "species", "description", "reference_upload", "looks", "rules", "last_seen"
    }
    luna = {
        "name": "Luna",
        "species": "cat",
        "description": "A calico, mostly white with dark blotches.",
        "looks": {"pattern": "blotchy", "base": "white"},
        "rules": {"priority": True, "always_strict": True},
        "last_seen": {},
    }
    result = await hass.config_entries.flow.async_configure(result["flow_id"], luna)
    assert result["errors"] == {"always_strict": "priority_not_strict"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**luna, "rules": {"priority": True, "always_strict": False}}
    )
    assert result["step_id"] == "animal"
    assert result["description_placeholders"]["count"] == "1"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**luna, "rules": {"priority": False, "always_strict": False}}
    )
    assert result["errors"] == {"name": "name_taken"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Pepper", "species": "cat", "looks": {"pattern": "uniform", "base": "black"},
         "rules": {"priority": False, "always_strict": True}, "last_seen": {}},
    )
    assert result["step_id"] == "animal"  # an empty name finishes
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"species": "cat", "looks": {}, "rules": {}, "last_seen": {}}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.options["first_look"] == ["ai_task.gemini"]
    assert entry.options["summary_time"] == "08:05:00" and "more" not in entry.options  # stored flat
    assert entry.options["instructions"] == DEFAULT_INSTRUCTIONS
    subs = sorted((s.subentry_type, s.title) for s in entry.subentries.values())
    # Only animals are pages; cameras and people live in the options, pests are a list.
    assert subs == [("animal", "Luna"), ("animal", "Pepper")]
    assert entry.options["pests"] == ["Raccoon", "Skunk"]
    (garage,) = entry.options["cameras"].values()
    assert garage["slug"] == "garage" and garage["name"] == "Garage"
    assert garage["camera"] == "camera.backyard_cam_high_resolution_channel"
    assert garage["priority_cooldown"] == 30 and garage["missing_cooldown"] == 2
    assert "timing" not in garage and garage["deterrent_minutes"] == 2  # stored flat, as before
    luna_data = next(s.data for s in entry.subentries.values() if s.title == "Luna")
    assert luna_data["pattern"] == "blotchy" and luna_data["priority"] is True and "looks" not in luna_data


async def test_onboarding_without_ai_task_aborts(hass: HomeAssistant, ready):
    hass.states.async_remove("ai_task.gemini")
    hass.states.async_remove("ai_task.gemini_pro")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["reason"] == "no_ai_task"


async def test_onboarding_can_skip_cameras_and_animals(hass: HomeAssistant, ready):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"first_look": ["ai_task.gemini"], "first_model_hours": {}})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"night_start": "20:00:00", "night_end": "08:00:00", "use_dark": True,
         "more": {"summary_time": "08:05:00", "on_camera_minutes": 10}},
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"cameras": []})
    assert result["step_id"] == "wildlife"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"pests": []})
    assert result["step_id"] == "animal"
    assert "No cameras were picked" in result["description_placeholders"]["notes"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"species": "cat", "looks": {}, "rules": {}, "last_seen": {}}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert not result["result"].subentries


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Neighborhood Watch",
        minor_version=3,
        options={
            "first_look": ["ai_task.gemini"],
            "cameras": {
                "cam1": {"camera": "camera.backyard_cam_high_resolution_channel", "name": "Garage", "slug": "garage",
                         "triggers": [], "skip_when": [], "cooldown": 15},
            },
            "people": {},
        },
        subentries_data=[
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


ADVANCED = {"identification", "training", "detector", "notifications"}


async def _options(hass: HomeAssistant, entry, step: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    if step in ADVANCED:  # these sit in the Advanced submenu
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "advanced"})
        assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": step})


async def test_add_camera_prefills_and_rejects_duplicates(hass: HomeAssistant, ready):
    entry = _entry(hass)
    result = await _options(hass, entry, "cameras")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"camera": "add_new"})
    assert result["step_id"] == "camera_add"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"camera": "camera.backyard_cam_high_resolution_channel"}
    )
    assert result["step_id"] == "camera"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Garage", "timing": {"cooldown": 3, "priority_cooldown": 0, "missing_cooldown": 0},
         "critical_alerts": {}, "deterrent": {}},
    )
    assert result["errors"] == {"name": "name_taken"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Driveway", "timing": {"cooldown": 2, "priority_cooldown": 0, "missing_cooldown": 0},
         "critical_alerts": {}, "deterrent": {}},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    names = sorted(d["name"] for d in entry.options["cameras"].values())
    assert names == ["Driveway", "Garage"]
    assert entry.options["first_look"] == ["ai_task.gemini"]  # other settings kept


async def test_edit_camera_keeps_its_slug_and_remove(hass: HomeAssistant, ready):
    entry = _entry(hass)
    result = await _options(hass, entry, "cameras")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"camera": "cam1"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"camera": "camera.backyard_cam_high_resolution_channel", "name": "Garage Door",
         "timing": {"cooldown": 10, "priority_cooldown": 30, "missing_cooldown": 2},
         "critical_alerts": {"critical_except": ["Luna"]}, "deterrent": {}, "remove": False},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["cameras"]["cam1"]["slug"] == "garage"
    assert entry.options["cameras"]["cam1"]["name"] == "Garage Door"
    assert entry.options["cameras"]["cam1"]["cooldown"] == 10
    assert entry.options["cameras"]["cam1"]["critical_except"] == ["luna"]
    result = await _options(hass, entry, "cameras")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"camera": "cam1"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"camera": "camera.backyard_cam_high_resolution_channel", "name": "Garage Door",
         "timing": {}, "critical_alerts": {}, "deterrent": {}, "remove": True},
    )
    assert entry.options["cameras"] == {}


async def test_add_and_edit_animal(hass: HomeAssistant, ready):
    entry = _entry(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "animal"), context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Unknown Cat", "species": "cat", "looks": {"pattern": "na", "base": "na"},
                            "rules": {"priority": False, "always_strict": False}, "last_seen": {}},
    )
    assert result["errors"] == {"name": "name_reserved"}
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Milo", "species": "cat", "description": "Black, long-haired.",
                            "looks": {"pattern": "uniform", "base": "black"},
                            "rules": {"priority": False, "always_strict": False}, "last_seen": {}},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    milo = next(s for s in entry.subentries.values() if s.title == "Milo")
    assert milo.data["description"] == "Black, long-haired."
    assert milo.data["added"]

    result = await entry.start_subentry_reconfigure_flow(hass, milo.subentry_id)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Milo", "species": "cat", "description": "Black with a white bib.",
                            "looks": {"pattern": "uniform", "base": "black", "tips": "Bigger than the others."},
                            "rules": {"priority": False, "always_strict": False},
                            "last_seen": {"known_location": "Porch"}, "remove_reference": False},
    )
    assert result["reason"] == "reconfigure_successful"
    assert milo.data["description"] == "Black with a white bib."
    assert milo.data["slug"] == "milo"
    assert milo.data["tips"] == "Bigger than the others." and milo.data["known_location"] == "Porch"


async def test_options_identification_requires_animals_placeholder(hass: HomeAssistant, ready):
    entry = _entry(hass)
    result = await _options(hass, entry, "identification")
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


async def test_add_and_edit_person(hass: HomeAssistant, ready):
    entry = _entry(hass)
    hass.services.async_register("notify", "mobile_app_phone", lambda call: None)
    result = await _options(hass, entry, "people")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"person": "add_new"})
    selector = next(v for k, v in result["data_schema"].schema.items() if str(k) == "notify_service")
    assert "mobile_app_phone" in [o["value"] for o in selector.config["options"]]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Sam", "notify_service": "notify.mobile_app_phone",
         "critical_alerts": {"missing_alerts": True, "critical_sound": "default", "critical_volume": 0.5},
         "mutes": {"muted": ["Luna"]}, "quiet": {"quiet_start": "22:00:00", "quiet_end": "07:00:00"}},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    ((pid, sam),) = entry.options["people"].items()
    assert sam["notify_service"] == "mobile_app_phone"
    assert sam["muted"] == ["luna"] and sam["quiet_start"] == "22:00:00" and sam["critical_volume"] == 0.5
    assert "quiet" not in sam and "mutes" not in sam
    result = await _options(hass, entry, "people")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"person": pid})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"name": "Sam", "notify_service": "mobile_app_phone",
         "critical_alerts": {"missing_alerts": False, "critical_sound": "default", "critical_volume": 0.5},
         "mutes": {}, "quiet": {}, "remove": False},
    )
    assert entry.options["people"][pid]["missing_alerts"] is False


async def test_options_alerts_and_training(hass: HomeAssistant, ready):
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "alerts"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"alerts": False, "min_confidence": "high", "correction_buttons": True}
    )
    assert entry.options["alerts"] is False and entry.options["min_confidence"] == "high"
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "catch_alls"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"pest": {"pests": ["Raccoon"], "pest_alert_at": ["garage"], "pest_critical_at": []},
         "unknown": {"unknown_alert_at": ["*"], "unknown_critical_at": []},
         "other": {"other_alert_at": ["garage"], "other_critical_at": ["*"]},
         "motion": {"motion_alert_at": [], "motion_critical_at": []}},
    )
    assert not {"pest", "unknown", "other", "motion"} & set(entry.options)  # stored flat
    assert entry.options["other_alert_at"] == ["*"] and entry.options["other_critical_at"] == ["*"]
    assert entry.options["motion_alert_at"] == []
    assert entry.options["pests"] == ["Raccoon"] and entry.options["pest_alert_at"] == ["garage"]
    result = await _options(hass, entry, "training")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"dataset": True, "dataset_dir": "/media/nas/nw", "dataset_gb": 50}
    )
    assert entry.options["dataset_dir"] == "/media/nas/nw" and entry.options["alerts"] is False



async def test_onboarding_people_step_lists_phones(hass: HomeAssistant, ready):
    hass.services.async_register("notify", "mobile_app_sams_phone", lambda call: None)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    # API callers may leave a form's sections out: they get the sections' defaults.
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"first_look": ["ai_task.gemini"]})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"night_start": "20:00:00", "night_end": "08:00:00", "use_dark": True, "watch_always": False},
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"cameras": []})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"pests": ["Raccoon"]})
    assert result["step_id"] == "people"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"phones": ["mobile_app_sams_phone"]})
    assert "Configure → People" not in result["description_placeholders"]["notes"]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"species": "cat"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    options = result["result"].options
    assert "first_model_hours" not in options
    (sam,) = options["people"].values()
    assert (sam["name"], sam["notify_service"]) == ("Sams Phone", "mobile_app_sams_phone")


async def test_configure_menu_puts_the_advanced_pages_in_a_submenu(hass: HomeAssistant, ready):
    entry = _entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == [
        "cameras", "people", "alerts", "catch_alls", "schedule", "models", "advanced",
    ]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "advanced"})
    assert result["type"] is FlowResultType.MENU and result["step_id"] == "advanced"
    assert result["menu_options"] == ["identification", "training", "detector", "notifications"]


async def test_options_models_keeps_the_hours_flat(hass: HomeAssistant, ready):
    entry = _entry(hass)
    result = await _options(hass, entry, "models")
    assert "first_model_hours" in {str(k) for k in result["data_schema"].schema}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"first_look": ["ai_task.gemini"], "first_model_hours": {"main_start": "07:00:00", "main_end": "21:00:00"}},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["main_start"] == "07:00:00" and entry.options["main_end"] == "21:00:00"
    assert "first_model_hours" not in entry.options
    result = await _options(hass, entry, "models")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"first_look": ["ai_task.gemini"]})
    assert result["type"] is FlowResultType.CREATE_ENTRY  # the hours section may be left out
    assert "first_model_hours" not in entry.options


async def test_species_is_a_list_with_any_kind_allowed(hass: HomeAssistant, ready):
    entry = _entry(hass)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "animal"), context={"source": config_entries.SOURCE_USER}
    )
    key = next(k for k in result["data_schema"].schema if str(k) == "species")
    assert key.default() == "cat"
    selector = result["data_schema"].schema[key]
    assert selector.config["custom_value"] is True
    assert [o["value"] for o in selector.config["options"]][:2] == ["cat", "dog"]
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Ziggy", "species": " Fox "},  # sections left out: their defaults
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    ziggy = next(s for s in entry.subentries.values() if s.title == "Ziggy")
    assert ziggy.data["species"] == "fox"
    assert (ziggy.data["pattern"], ziggy.data["priority"]) == ("na", False) and "looks" not in ziggy.data
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "animal"), context={"source": config_entries.SOURCE_USER}
    )
    key = next(k for k in result["data_schema"].schema if str(k) == "species")
    assert "fox" in [o["value"] for o in result["data_schema"].schema[key].config["options"]]


async def test_camera_without_sensors_points_to_the_timer(hass: HomeAssistant, ready):
    hass.states.async_set("camera.rtsp_only", "idle")
    entry = _entry(hass)
    result = await _options(hass, entry, "cameras")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"camera": "add_new"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"camera": "camera.rtsp_only"})
    assert "Also check every" in result["description_placeholders"]["sensors"]
    result = await _options(hass, entry, "cameras")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"camera": "add_new"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"camera": "camera.backyard_cam_high_resolution_channel"}
    )
    assert result["description_placeholders"]["sensors"] == ""


async def test_onboarding_lists_phones_by_device_name(hass: HomeAssistant, ready):
    phone = MockConfigEntry(domain="mobile_app", title="Phone A", data={"device_name": "Phone A"})
    phone.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=phone.entry_id, identifiers={("mobile_app", "a")}, name="Phone A",
    )
    dr.async_get(hass).async_update_device(device.id, name_by_user="Kitchen iPad")
    hass.services.async_register("notify", "mobile_app_phone_a", lambda call: None)
    hass.services.async_register("notify", "mobile_app_other", lambda call: None)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"first_look": ["ai_task.gemini"], "first_model_hours": {}}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"night_start": "20:00:00", "night_end": "08:00:00", "use_dark": True,
         "more": {"summary_time": "08:05:00", "on_camera_minutes": 10}},
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"cameras": []})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"pests": []})
    assert result["step_id"] == "people"
    key = next(k for k in result["data_schema"].schema if str(k) == "phones")
    options = result["data_schema"].schema[key].config["options"]
    assert {o["value"]: o["label"] for o in options} == {
        "mobile_app_other": "mobile_app_other",
        "mobile_app_phone_a": "Kitchen iPad",
    }
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"phones": ["mobile_app_phone_a", "mobile_app_other"]}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"species": "cat"})
    names = sorted(p["name"] for p in result["result"].options["people"].values())
    assert names == ["Kitchen iPad", "Other"]  # the phone's name, else one made from the service


async def test_person_page_lists_phones_by_device_name(hass: HomeAssistant, ready):
    phone = MockConfigEntry(domain="mobile_app", title="Phone A", data={"device_name": "Phone A"})
    phone.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=phone.entry_id, identifiers={("mobile_app", "a")}, name="Phone A",
    )
    hass.services.async_register("notify", "mobile_app_phone_a", lambda call: None)
    entry = _entry(hass)
    result = await _options(hass, entry, "people")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"person": "add_new"})
    selector = next(v for k, v in result["data_schema"].schema.items() if str(k) == "notify_service")
    assert {"value": "mobile_app_phone_a", "label": "Phone A"} in selector.config["options"]


async def test_options_schedule_and_catch_alls_fold_into_sections(hass: HomeAssistant, ready):
    entry = _entry(hass)
    result = await _options(hass, entry, "schedule")
    assert {"more"} <= {str(k) for k in result["data_schema"].schema}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"night_start": "21:00:00", "night_end": "07:00:00", "use_dark": False,
         "more": {"summary_time": "09:00:00", "on_camera_minutes": 5}},
    )
    assert entry.options["summary_time"] == "09:00:00" and entry.options["on_camera_minutes"] == 5
    assert "more" not in entry.options
    result = await _options(hass, entry, "catch_alls")
    assert [str(k) for k in result["data_schema"].schema] == ["pest", "unknown", "other", "motion"]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})  # all left folded
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["unknown_alert_at"] == ["*"] and entry.options["motion_alert_at"] == []

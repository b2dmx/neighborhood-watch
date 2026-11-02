"""Settings checks that only look at what changed, the instructions migration, and the
engine's missing-mode watch and stored card look."""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256

from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.neighborhood_watch import async_migrate_entry, logic
from custom_components.neighborhood_watch.const import DOMAIN
from custom_components.neighborhood_watch.engine import WatchEngine

from .test_ha_config_flow import _entry, _options, _two_cameras, ready  # noqa: F401 - ready is a fixture

NOON = datetime(2026, 10, 14, 19, 0, tzinfo=dt_util.UTC)  # 12:00 in US/Pacific
CAMERA_FORM = {"timing": {}, "critical_alerts": {}, "deterrent": {}, "remove": False}


def _section_keys(result, name: str) -> list[str]:
    sec = next(v for k, v in result["data_schema"].schema.items() if str(k) == name)
    return [str(k) for k in sec.schema.schema]


async def _onboard_to(hass: HomeAssistant, step: str):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"first_look": ["ai_task.gemini"]})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"night_start": "20:00:00", "night_end": "08:00:00", "use_dark": True}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"pests": []})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"cameras": []})
    assert result["step_id"] == step
    return result


# --- B1: an existing priority animal can be edited without a second opinion ---------------


async def test_editing_a_priority_animal_without_second_opinion_saves(hass: HomeAssistant, ready):
    entry = _entry(hass)  # Luna is priority; no Second opinion model
    luna = next(iter(entry.subentries.values()))
    form = {"name": "Luna", "species": "cat", "description": "Calico.", "looks": {}, "last_seen": {}}
    result = await entry.start_subentry_reconfigure_flow(hass, luna.subentry_id)
    assert _section_keys(result, "rules")[:2] == ["alert_at", "critical_at"]  # where it alerts comes first
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**form, "rules": {"priority": True, "pattern": "blotchy", "base": "white"}}
    )
    assert result["reason"] == "reconfigure_successful"
    assert next(iter(entry.subentries.values())).data["description"] == "Calico."
    # Switching Always strict on is new, so it still needs one.
    result = await entry.start_subentry_reconfigure_flow(hass, luna.subentry_id)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**form, "rules": {"priority": False, "always_strict": True}}
    )
    assert result["errors"] == {"base": "needs_second_opinion"}


# --- B2: a stored copy of the built-in instructions follows updates -----------------------


def test_todays_instructions_are_listed_as_shipped():
    """Changing DEFAULT_INSTRUCTIONS means adding the old text's hash to SHIPPED_INSTRUCTIONS."""
    assert sha256(logic.DEFAULT_INSTRUCTIONS.strip().encode()).hexdigest() in logic.SHIPPED_INSTRUCTIONS
    assert logic.is_default_instructions(logic.DEFAULT_INSTRUCTIONS + "\n  ")
    assert not logic.is_default_instructions(logic.DEFAULT_INSTRUCTIONS + " Ignore birds.")
    assert not logic.is_default_instructions("")


async def test_migration_clears_a_stored_default_but_keeps_edits(hass: HomeAssistant):
    stored = MockConfigEntry(
        domain=DOMAIN, minor_version=3, options={"instructions": logic.DEFAULT_INSTRUCTIONS + "\n", "first_look": ["a"]}
    )
    edited = MockConfigEntry(
        domain=DOMAIN, minor_version=3, options={"instructions": logic.DEFAULT_INSTRUCTIONS + " Ignore birds."}
    )
    for entry in (stored, edited):
        entry.add_to_hass(hass)
        assert await async_migrate_entry(hass, entry)
        assert entry.minor_version == 4
    assert stored.options == {"instructions": "", "first_look": ["a"]}
    assert edited.options["instructions"].endswith("Ignore birds.")


# --- B3: the detector's address is only checked when it changes ---------------------------


async def test_detector_mode_saves_while_the_detector_is_down(hass: HomeAssistant, ready, aioclient_mock):
    entry = _entry(hass)
    hass.config_entries.async_update_entry(entry, options={**entry.options, "detector_url": "http://offline:8765"})
    aioclient_mock.get("http://offline:8765/health", exc=TimeoutError())
    result = await _options(hass, entry, "detector")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"detector_url": "http://offline:8765/", "detector_mode": "skip_empty", "detector_min": 0.3}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["detector_mode"] == "skip_empty"
    assert aioclient_mock.call_count == 0


# --- B4: camera name and entity checks ----------------------------------------------------


async def test_unchanged_camera_name_is_not_rechecked(hass: HomeAssistant, ready):
    entry = _two_cameras(hass)
    cams = entry.options["cameras"]
    # Renamed long ago to a name that now reads as the other camera's slug.
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "cameras": {**cams, "cam1": {**cams["cam1"], "name": "Driveway"}}}
    )
    result = await _options(hass, entry, "cameras")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"camera": "cam1"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**CAMERA_FORM, "camera": "camera.backyard_cam_high_resolution_channel", "name": "Driveway",
         "timing": {"cooldown": 7}},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["cameras"]["cam1"]["cooldown"] == 7


async def test_a_camera_cannot_be_moved_onto_one_already_set_up(hass: HomeAssistant, ready):
    entry = _two_cameras(hass)
    result = await _options(hass, entry, "cameras")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"camera": "cam1"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**CAMERA_FORM, "camera": "camera.driveway", "name": "Garage"}
    )
    assert result["errors"] == {"camera": "camera_taken"}


async def test_moving_a_camera_keeps_sensors_picked_by_hand(hass: HomeAssistant, ready):
    gate = MockConfigEntry(domain="demo_cameras")
    gate.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=gate.entry_id, identifiers={("demo_cameras", "gate")}, name="Gate Cam"
    )
    ent_reg = er.async_get(hass)
    for domain, object_id in (("camera", "gate_cam"), ("binary_sensor", "gate_cam_motion")):
        ent_reg.async_get_or_create(
            domain, "demo_cameras", object_id, suggested_object_id=object_id, device_id=device.id,
            original_name=object_id, config_entry=gate,
        )
    entry = _entry(hass)
    cams = entry.options["cameras"]
    picked = ["binary_sensor.backyard_cam_motion", "binary_sensor.doorbell_button"]  # suggested + by hand
    hass.config_entries.async_update_entry(
        entry,
        options={**entry.options, "cameras": {"cam1": {
            **cams["cam1"], "triggers": picked, "skip_when": ["binary_sensor.backyard_cam_person_detected"],
        }}},
    )
    result = await _options(hass, entry, "cameras")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"camera": "cam1"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**CAMERA_FORM, "camera": "camera.gate_cam", "name": "Garage", "triggers": picked,
         "skip_when": ["binary_sensor.backyard_cam_person_detected"]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    saved = entry.options["cameras"]["cam1"]
    assert saved["camera"] == "camera.gate_cam"
    assert saved["triggers"] == ["binary_sensor.doorbell_button", "binary_sensor.gate_cam_motion"]
    assert saved["skip_when"] == []


# --- B5: a person's saved phone and quiet hours are only checked when changed --------------


async def test_editing_a_person_rechecks_only_what_changed(hass: HomeAssistant, ready):
    entry = _entry(hass)
    sam = {"name": "Sam", "person": None, "notify_service": "mobile_app_old_phone", "quiet_zones": [], "muted": [],
           "quiet_start": "22:00:00", "quiet_end": None, "quiet_mode": "normal", "missing_alerts": True,
           "critical_sound": "default", "critical_volume": 0.7}
    hass.config_entries.async_update_entry(entry, options={**entry.options, "people": {"p1": sam}})
    form = {"name": "Sam", "notify_service": "mobile_app_old_phone", "critical_alerts": {},
            "mutes": {"muted": ["Luna"]}, "quiet": {"quiet_start": "22:00:00"}, "remove": False}
    result = await _options(hass, entry, "people")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"person": "p1"})
    result = await hass.config_entries.options.async_configure(result["flow_id"], form)
    assert result["type"] is FlowResultType.CREATE_ENTRY  # the phone is gone, but nothing about it changed
    assert entry.options["people"]["p1"]["muted"] == ["luna"]
    result = await _options(hass, entry, "people")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"person": "p1"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**form, "notify_service": "mobile_app_new_phone"}
    )
    assert result["errors"] == {"notify_service": "notify_unknown"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**form, "quiet": {"quiet_start": "23:00:00"}}
    )
    assert result["errors"] == {"base": "quiet_hours_both"}


# --- B6 / B7: the engine -------------------------------------------------------------------


def _engine(hass: HomeAssistant, **options) -> WatchEngine:
    entry = MockConfigEntry(
        domain=DOMAIN,
        minor_version=4,
        options={"first_look": ["ai_task.gemini"], **options},
        subentries_data=[
            {"data": {"name": "Milo", "slug": "milo", "appearance": "orange"}, "subentry_type": "animal",
             "title": "Milo", "unique_id": "milo"},
            {"data": {"name": "Ziggy", "slug": "ziggy", "appearance": {"fur": "#1d1d20"}}, "subentry_type": "animal",
             "title": "Ziggy", "unique_id": "ziggy"},
        ],
    )
    entry.add_to_hass(hass)
    return WatchEngine(hass, entry)


async def test_a_stored_text_appearance_is_no_card_look(hass: HomeAssistant):
    watch = _engine(hass)
    assert watch.animals["milo"].appearance is None
    assert watch.animals["ziggy"].appearance == {"fur": "#1d1d20"}


async def test_missing_mode_watches_all_day(hass: HomeAssistant, freezer):
    freezer.move_to(NOON)
    watch = _engine(hass, use_dark=False)
    assert not watch.is_night()
    watch.data["missing"] = {"milo": True}
    assert watch.is_night()


# --- B8 / B9: onboarding -------------------------------------------------------------------


async def test_onboarding_animal_starts_as_seen_now_and_still_finishes_empty(hass: HomeAssistant, ready, freezer):
    freezer.move_to(NOON)
    result = await _onboard_to(hass, "animal")
    last_seen = next(v for k, v in result["data_schema"].schema.items() if str(k) == "last_seen")
    (key,) = [k for k in last_seen.schema.schema if str(k) == "known_last_seen"]
    assert key.description["suggested_value"] == "2026-10-14 12:00:00"
    # The browser sends the filled-in time back with an empty name: that still finishes.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"species": "cat", "looks": {}, "rules": {}, "last_seen": {"known_last_seen": "2026-10-14 12:00:00"}}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # Add animal (after onboarding) starts empty.
    result = await hass.config_entries.subentries.async_init(
        (result["result"].entry_id, "animal"), context={"source": config_entries.SOURCE_USER}
    )
    last_seen = next(v for k, v in result["data_schema"].schema.items() if str(k) == "last_seen")
    (key,) = [k for k in last_seen.schema.schema if str(k) == "known_last_seen"]
    assert key.description["suggested_value"] is None


async def test_onboarding_names_people_after_their_person(hass: HomeAssistant, ready):
    phone = MockConfigEntry(domain="mobile_app", title="Phone A", data={"device_name": "Phone A"})
    phone.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=phone.entry_id, identifiers={("mobile_app", "a")}, name="Phone A"
    )
    er.async_get(hass).async_get_or_create(
        "device_tracker", "mobile_app", "a", suggested_object_id="phone_a", device_id=device.id, config_entry=phone
    )
    hass.states.async_set("person.sam", "home", {"friendly_name": "Sam", "device_trackers": ["device_tracker.phone_a"]})
    hass.states.async_set("person.kim", "home", {"friendly_name": "Kim", "device_trackers": []})
    hass.services.async_register("notify", "mobile_app_phone_a", lambda call: None)
    hass.services.async_register("notify", "mobile_app_other", lambda call: None)
    result = await _onboard_to(hass, "people")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"phones": ["mobile_app_phone_a", "mobile_app_other"]}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"species": "cat"})
    people = sorted((p["name"], p["person"]) for p in result["result"].options["people"].values())
    assert people == [("Other", None), ("Sam", "person.sam")]

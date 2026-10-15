"""v0.3: wildlife, alerts, corrections, training data and deterrents in a real Home Assistant."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
    async_mock_service,
)

from homeassistant.components.camera import Image
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.setup import async_setup_component

from custom_components.neighborhood_watch.const import DOMAIN, EVENT_SIGHTING

from .test_ha_integration import FIRST, NIGHT, SECOND, FakeAI, animal_sub, camera_sub, trigger


def species_sub(name: str, **extra: Any) -> ConfigSubentryData:
    return ConfigSubentryData(
        data={
            "name": name,
            "slug": name.lower(),
            "description": "",
            "alert_scope": "never",
            "alert_cameras": [],
            "critical_switch": False,
            "pest": False,
            "in_summary": True,
            **extra,
        },
        subentry_type="species",
        title=name,
        unique_id=f"species_{name.lower()}",
    )


def person_sub(name: str, **extra: Any) -> ConfigSubentryData:
    return ConfigSubentryData(
        data={
            "name": name,
            "person": f"person.{name.lower()}",
            "notify_service": f"mobile_app_{name.lower()}",
            "quiet_zones": [],
            "missing_alerts": False,
            "critical_sound": "chime.wav",
            "critical_volume": 0.7,
            **extra,
        },
        subentry_type="recipient",
        title=name,
    )


@pytest.fixture
async def watch(hass: HomeAssistant, tmp_path: Path, freezer):
    freezer.move_to(NIGHT)
    hass.config.config_dir = str(tmp_path)
    hass.config.media_dirs = {"local": str(tmp_path / "media")}
    hass.config.external_url = "https://example.ui.nabu.casa"
    assert await async_setup_component(hass, "homeassistant", {})
    for model in (FIRST, SECOND):
        hass.states.async_set(model, "unknown")
    for slug in ("garage", "yard"):
        hass.states.async_set(f"binary_sensor.{slug}_motion", "off")
        hass.states.async_set(f"binary_sensor.{slug}_person", "off")
    hass.states.async_set("light.garage_flood", "off")
    hass.states.async_set("light.porch", "on")
    hass.states.async_set("person.ann", "Work")
    hass.states.async_set("person.bo", "home")
    hass.states.async_set("zone.work", "0", {"friendly_name": "Work"})

    sent: list[dict[str, Any]] = []
    for name in ("ann", "bo"):

        async def phone(call: ServiceCall, who: str = name) -> None:
            sent.append({"to": who, **call.data})

        hass.services.async_register("notify", f"mobile_app_{name}", phone)
    lights_on = async_mock_service(hass, "light", "turn_on")
    lights_off = async_mock_service(hass, "light", "turn_off")

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Neighborhood Watch",
        options={
            "first_look": [FIRST],
            "second_opinion": [SECOND],
            "paid_models": [],
            "use_dark": False,
        },
        subentries_data=[
            camera_sub("Garage", "garage", "binary_sensor.garage_motion", house=True, unsure_alerts=True,
                       ai_notes="The bins are at the left edge.",
                       deterrent_lights=["light.garage_flood", "light.porch"], deterrent_minutes=2),
            camera_sub("Yard", "yard", "binary_sensor.yard_motion"),
            animal_sub("Luna", priority=True, pattern="blotchy", base="white", known_last_seen=None),
            animal_sub("Milo"),
            species_sub("Raccoon", alert_scope="any", critical_switch=True, pest=True),
            species_sub("Opossum", alert_scope="cameras", alert_cameras=["garage"]),
            person_sub("Ann", missing_alerts=True, quiet_zones=["zone.work"]),
            person_sub("Bo"),
        ],
    )
    entry.add_to_hass(hass)
    ai = FakeAI()

    async def fake_image(hass, entity_id, timeout=10, width=None, height=None):
        return Image("image/jpeg", _jpeg())

    with patch(
        "custom_components.neighborhood_watch.engine.camera_component.async_get_image",
        side_effect=fake_image,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        hass.services.async_register("ai_task", "generate_data", ai.handle, supports_response=SupportsResponse.ONLY)
        yield {
            "entry": entry,
            "ai": ai,
            "sent": sent,
            "on": lights_on,
            "off": lights_off,
            "events": async_capture_events(hass, EVENT_SIGHTING),
            "watch": entry.runtime_data,
        }


def _jpeg() -> bytes:
    from io import BytesIO

    from PIL import Image as PILImage

    buffer = BytesIO()
    PILImage.new("RGB", (64, 48), (90, 90, 90)).save(buffer, "JPEG")
    return buffer.getvalue()


def state(hass, entity_id):
    s = hass.states.get(entity_id)
    assert s is not None, f"{entity_id} missing"
    return s


async def test_wildlife_is_tracked_with_its_own_entities(hass, watch):
    watch["ai"].say(FIRST, "Raccoon|high|na|na|")
    await trigger(hass, "yard")
    assert state(hass, "sensor.raccoon_location").state == "Yard"
    assert state(hass, "binary_sensor.raccoon_on_camera").state == "on"
    assert state(hass, "sensor.raccoon_sightings_tonight").state == "1"
    assert state(hass, "image.raccoon_last_photo").state != "unknown"
    assert state(hass, "switch.raccoon_critical_alerts").state == "off"
    assert state(hass, "switch.garage_critical_alerts").state == "off"
    (event,) = watch["events"]
    assert event.data["sighting_id"]


async def test_older_setup_is_moved_to_alert_at(hass, watch):
    """0.6 settings (house cameras, alert scopes, critical switches) become the 0.7 rules."""
    entry = watch["entry"]
    assert entry.minor_version == 2
    data = {s.title: s.data for s in entry.subentries.values()}
    assert data["Garage"]["critical_except"] == ["luna"] and "house" not in data["Garage"]
    assert data["Yard"]["critical_except"] == ["luna"]
    assert (data["Raccoon"]["alert_at"], data["Raccoon"]["critical_at"]) == (["*"], [])
    assert (data["Opossum"]["alert_at"], data["Opossum"]["critical_at"]) == (["garage"], [])
    assert data["Milo"]["alert_at"] == ["*"] and data["Milo"]["critical_at"] == []
    assert "alert_scope" not in data["Raccoon"]
    opts = entry.options
    assert opts["unknown_alert_at"] == ["*"] and "unknown_alerts" not in opts
    assert opts["other_alert_at"] == ["garage"] and opts["motion_alert_at"] == ["garage"]


async def test_alerts_reach_everyone_with_buttons(hass, watch):
    watch["ai"].say(FIRST, "Raccoon|high|na|na|")
    await trigger(hass, "yard")
    assert sorted(m["to"] for m in watch["sent"]) == ["ann", "bo"]
    msg = watch["sent"][0]
    assert msg["title"] == "Raccoon — Yard"
    data = msg["data"]
    assert data["push"] == {"interruption-level": "active"}
    assert data["image"].startswith("https://example.ui.nabu.casa/local/neighborhood_watch/yard.jpg")
    titles = [a["title"] for a in data["actions"]]
    assert titles == ["✓ Right", "✗ Wrong", "It's Luna"]


async def test_critical_switch_but_quiet_zone_mutes_it(hass, watch):
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.raccoon_critical_alerts"}, blocking=True)
    watch["ai"].say(FIRST, "Raccoon|high|na|na|")
    await trigger(hass, "yard")
    by = {m["to"]: m["data"]["push"] for m in watch["sent"]}
    assert by["bo"]["interruption-level"] == "critical"
    assert by["bo"]["sound"] == {"name": "chime.wav", "critical": 1, "volume": 0.7}
    assert by["ann"]["interruption-level"] == "active"  # Ann is at work


async def test_missing_mode_goes_to_missing_people_only(hass, watch):
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.luna_missing_mode"}, blocking=True)
    hass.states.async_set("person.ann", "home")
    watch["ai"].say(FIRST, "Luna|low|blotchy|white|")
    watch["ai"].say(SECOND, "Luna|low|blotchy|white|")
    await trigger(hass, "yard")
    (msg,) = watch["sent"]
    assert msg["to"] == "ann"
    assert msg["title"] == "Possible Luna — Yard"
    assert msg["data"]["push"]["sound"]["volume"] == 1.0


async def test_opossum_only_where_chosen(hass, watch, freezer):
    watch["ai"].say(FIRST, "Opossum|high|na|na|", "Opossum|high|na|na|")
    await trigger(hass, "yard")
    assert watch["sent"] == []
    await trigger(hass, "garage")
    assert {m["title"] for m in watch["sent"]} == {"Opossum — Garage"}


async def test_unsure_motion_near_the_house(hass, watch):
    watch["ai"].say(FIRST, "None|low|na|na|")
    watch["ai"].say(SECOND, "None|low|na|na|")
    await trigger(hass, "garage")
    assert {m["title"] for m in watch["sent"]} == {"Motion — Garage"}


async def test_alerts_off(hass, watch):
    entry = watch["entry"]
    hass.config_entries.async_update_entry(entry, options={**entry.options, "alerts": False})
    await hass.async_block_till_done()
    watch["ai"].say(FIRST, "Raccoon|high|na|na|")
    await trigger(hass, "yard")
    assert watch["sent"] == []


async def test_pest_turns_on_lights_that_were_off_then_back_off(hass, watch, freezer):
    watch["ai"].say(FIRST, "Raccoon|high|na|na|")
    await trigger(hass, "garage")
    assert watch["on"][0].data["entity_id"] == ["light.garage_flood", "light.porch"]
    freezer.tick(timedelta(minutes=3))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    (off,) = watch["off"]
    assert off.data["entity_id"] == ["light.garage_flood"]  # the porch was already on


async def test_training_sample_saved_and_labelled_by_phone_button(hass, watch):
    watch["ai"].say(FIRST, "Milo|high|uniform|black|")
    await trigger(hass, "yard")
    row = watch["watch"].sightings()[-1]
    root = watch["watch"].dataset_root / row["day"]
    meta = json.loads((root / f"{row['id']}.json").read_text())
    assert meta["ai_animal"] == "Milo" and meta["label"] is None
    assert (root / f"{row['id']}.jpg").exists()

    hass.bus.async_fire("mobile_app_notification_action", {"action": f"NW|right|{row['id']}"})
    await hass.async_block_till_done()
    meta = json.loads((root / f"{row['id']}.json").read_text())
    assert (meta["label"], meta["labelled_by"]) == ("Milo", "right")


async def test_its_luna_button_moves_tracking(hass, watch):
    watch["ai"].say(FIRST, "Milo|high|uniform|black|")
    await trigger(hass, "yard")
    assert state(hass, "sensor.milo_location").state == "Yard"
    sid = watch["watch"].sightings()[-1]["id"]
    hass.bus.async_fire("mobile_app_notification_action", {"action": f"NW|is|{sid}|luna"})
    await hass.async_block_till_done()
    assert state(hass, "sensor.luna_location").state == "Yard"
    assert state(hass, "sensor.luna_last_confirmed").state != "unknown"
    assert state(hass, "sensor.milo_location").state == "Yard"  # seeded known last seen
    assert state(hass, "sensor.milo_sightings_tonight").state == "0"
    assert state(hass, "binary_sensor.luna_confirmed_on_camera").state == "off"  # never feeds


async def test_correct_service_wrong_and_nothing(hass, watch):
    watch["ai"].say(FIRST, "Raccoon|high|na|na|")
    await trigger(hass, "yard")
    result = await hass.services.async_call(
        DOMAIN, "correct", {"answer": "nothing", "camera": "Yard"}, blocking=True, return_response=True
    )
    assert result["was"] == "Raccoon"
    assert state(hass, "sensor.raccoon_location").state == "Not seen"
    assert state(hass, "binary_sensor.raccoon_on_camera").state == "off"


async def test_correct_rejects_unknown_names(hass, watch):
    watch["ai"].say(FIRST, "Raccoon|high|na|na|")
    await trigger(hass, "yard")
    from homeassistant.exceptions import ServiceValidationError

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "correct", {"answer": "Dragon"}, blocking=True, return_response=True)


async def test_night_summary_counts_wildlife(hass, watch):
    watch["ai"].say(FIRST, "Raccoon|high|na|na|")
    await trigger(hass, "yard")
    result = await hass.services.async_call(DOMAIN, "build_summary", {}, blocking=True, return_response=True)
    assert result["lines"] == ["Raccoon — 1 sighting (Yard)"]


async def test_wildlife_names_and_notes_reach_the_prompt(hass, watch):
    w = watch["watch"]
    prompt, _ = w._prompt(w.cameras["yard"])
    assert "Listed wildlife (name these by kind): Raccoon, Opossum, Squirrel" in prompt



async def test_android_critical_fields_and_plain_notify(hass, watch):
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.raccoon_critical_alerts"}, blocking=True)
    watch["ai"].say(FIRST, "Raccoon|high|na|na|")
    await trigger(hass, "yard")
    bo = next(m for m in watch["sent"] if m["to"] == "bo")
    assert (bo["data"]["ttl"], bo["data"]["priority"], bo["data"]["channel"]) == (0, "high", "alarm_stream")


async def test_timed_checks_for_cameras_without_sensors(hass, watch, freezer):
    entry = watch["entry"]
    yard = next(s for s in entry.subentries.values() if s.title == "Yard")
    hass.config_entries.async_update_subentry(entry, yard, data={**yard.data, "check_every": 5})
    await hass.async_block_till_done()
    watch["ai"].say(FIRST, "None|high|na|na|")
    freezer.tick(timedelta(minutes=5, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert watch["ai"].calls == [FIRST]


async def test_watch_all_day(hass, watch, freezer):
    from datetime import datetime

    from homeassistant.util import dt as dt_util

    entry = watch["entry"]
    hass.config_entries.async_update_entry(entry, options={**entry.options, "watch_always": True})
    await hass.async_block_till_done()
    freezer.move_to(datetime(2026, 10, 14, 19, 0, tzinfo=dt_util.UTC))  # noon Pacific
    watch["ai"].say(FIRST, "None|high|na|na|")
    await trigger(hass, "yard")
    assert watch["ai"].calls == [FIRST]


async def test_review_queue_and_unsure(hass, watch, freezer):
    watch["ai"].say(FIRST, "Milo|high|uniform|black|", "None|high|na|na|", "Raccoon|high|na|na|")
    await trigger(hass, "yard")
    freezer.tick(timedelta(minutes=4))
    await trigger(hass, "yard")  # a confident "nothing": not queued
    freezer.tick(timedelta(minutes=4))
    await trigger(hass, "yard")
    queue = state(hass, "sensor.neighborhood_watch_to_review")
    assert queue.state == "2"
    items = queue.attributes["items"]
    assert [i["animal"] for i in items] == ["Raccoon", "Milo"]
    await hass.services.async_call(DOMAIN, "correct", {"answer": "unsure", "sighting_id": items[0]["id"]}, blocking=True, return_response=True)
    await hass.services.async_call(DOMAIN, "correct", {"answer": "right", "sighting_id": items[1]["id"]}, blocking=True, return_response=True)
    assert state(hass, "sensor.neighborhood_watch_to_review").state == "0"
    assert state(hass, "sensor.raccoon_location").state == "Yard"  # unsure changes nothing


async def test_frame_view_serves_the_saved_photo(hass, watch, hass_client):
    assert await async_setup_component(hass, "http", {})
    from custom_components.neighborhood_watch.views import FrameView

    hass.http.register_view(FrameView())
    watch["ai"].say(FIRST, "Milo|high|uniform|black|")
    await trigger(hass, "yard")
    sid = watch["watch"].sightings()[-1]["id"]
    client = await hass_client()
    response = await client.get(f"/api/neighborhood_watch/frame/{sid}")
    assert response.status == 200
    assert response.content_type == "image/jpeg"
    assert (await client.get("/api/neighborhood_watch/frame/nope")).status == 404
    assert (await client.get("/api/neighborhood_watch/frame/..%2Fx")).status in (400, 404)


async def _with_detector(hass, watch, mode, aioclient_mock, animal=0.0):
    entry = watch["entry"]
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "detector_url": "http://vision:8765", "detector_mode": mode}
    )
    await hass.async_block_till_done()
    detections = [{"label": "animal", "confidence": animal, "box": [1, 2, 3, 4]}] if animal else []
    aioclient_mock.post("http://vision:8765/detect", json={"width": 64, "height": 48, "ms": 5, "device": "CPU", "detections": detections})


async def test_detector_shadow_records_and_scores(hass, watch, aioclient_mock):
    await _with_detector(hass, watch, "shadow", aioclient_mock, animal=0.05)
    watch["ai"].say(FIRST, "Raccoon|high|na|na|")
    await trigger(hass, "yard")
    assert watch["ai"].calls == [FIRST]  # shadow never skips
    agree = state(hass, "sensor.neighborhood_watch_detector_agreement")
    assert agree.state == "0.0"
    assert agree.attributes["misses"] == 1  # the AI saw a raccoon, the detector didn't
    assert state(hass, "sensor.yard_last_detection").attributes["detector"]["animal"] == 0.05


async def test_detector_skips_empty_frames(hass, watch, aioclient_mock):
    await _with_detector(hass, watch, "skip_empty", aioclient_mock)
    await trigger(hass, "yard")
    assert watch["ai"].calls == []
    (event,) = watch["events"]
    assert (event.data["animal"], event.data["models_used"]) == ("Nothing", "Local detector (no animal)")


async def test_detector_never_skips_in_missing_mode(hass, watch, aioclient_mock):
    await _with_detector(hass, watch, "skip_empty", aioclient_mock)
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.luna_missing_mode"}, blocking=True)
    watch["ai"].say(FIRST, "None|high|na|na|")
    await trigger(hass, "yard")
    assert watch["ai"].calls == [FIRST]


async def test_detector_down_falls_back_to_ai(hass, watch, aioclient_mock):
    entry = watch["entry"]
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "detector_url": "http://vision:8765", "detector_mode": "skip_empty"}
    )
    await hass.async_block_till_done()
    aioclient_mock.post("http://vision:8765/detect", status=500)
    watch["ai"].say(FIRST, "None|high|na|na|")
    await trigger(hass, "yard")
    assert watch["ai"].calls == [FIRST]


async def test_bulk_scan_names_visits_and_writes_report(hass, watch, aioclient_mock):
    entry = watch["entry"]
    hass.config_entries.async_update_entry(entry, options={**entry.options, "detector_url": "http://vision:8765"})
    await hass.async_block_till_done()
    aioclient_mock.post("http://vision:8765/scan", json={"id": "s1"})
    aioclient_mock.get(
        "http://vision:8765/scan/s1",
        json={
            "state": "done",
            "files_total": 3,
            "files_done": 3,
            "visits": [
                {"n": 1, "file": "a.jpg", "kind": "image", "start": 0, "end": 0, "confidence": 0.8},
                {"n": 2, "file": "clip.mp4", "kind": "video", "start": 4, "end": 9, "confidence": 0.6},
            ],
        },
    )
    aioclient_mock.get("http://vision:8765/scan/s1/frame/1", content=_jpeg())
    aioclient_mock.get("http://vision:8765/scan/s1/frame/2", content=_jpeg())
    aioclient_mock.post("http://vision:8765/scan/s1/report", json={"report": "trail/neighborhood-watch-report"})
    watch["ai"].say(FIRST, "Raccoon|high|na|na|", "Unknown Cat|medium|striped|grey|big tabby")
    watch["ai"].say(SECOND, "Unknown Cat|medium|striped|grey|big tabby")

    result = await hass.services.async_call(DOMAIN, "scan", {"folder": "trail"}, blocking=True, return_response=True)
    assert result == {"scan_id": "s1"}
    await hass.async_block_till_done(wait_background_tasks=True)

    last = state(hass, "sensor.neighborhood_watch_last_scan")
    assert last.state == "2 visits"
    assert last.attributes["tally"] == {"Raccoon": 1, "Unknown Cat": 1}
    assert last.attributes["report"] == "trail/neighborhood-watch-report"
    report_call = next(c for c in aioclient_mock.mock_calls if str(c[1]).endswith("/report"))
    assert report_call[2]["names"]["1"]["animal"] == "Raccoon"
    # Old footage never moves tracking, but the visits wait for review.
    assert state(hass, "sensor.raccoon_location").state == "Not seen"
    assert state(hass, "sensor.neighborhood_watch_to_review").state == "2"
    assert any(m["title"] == "Scan finished — trail" for m in watch["sent"])


async def test_scan_needs_a_detector(hass, watch):
    from homeassistant.exceptions import ServiceValidationError

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "scan", {"folder": "x"}, blocking=True, return_response=True)


async def test_low_wildlife_counts_as_possible(hass, watch, freezer):
    watch["ai"].say(FIRST, "Raccoon|low|na|na|", "Raccoon|high|na|na|")
    watch["ai"].say(SECOND, "Raccoon|low|na|na|")
    await trigger(hass, "yard")
    freezer.tick(timedelta(minutes=4))
    await trigger(hass, "yard")
    result = await hass.services.async_call(DOMAIN, "build_summary", {}, blocking=True, return_response=True)
    assert result["lines"] == ["Raccoon — 2 sightings (1 confirmed, 1 possible) (Yard)"]


async def test_correct_to_listed_wildlife_that_is_not_set_up(hass, watch):
    watch["ai"].say(FIRST, "Raccoon|high|na|na|")
    await trigger(hass, "yard")
    result = await hass.services.async_call(DOMAIN, "correct", {"answer": "squirrel"}, blocking=True, return_response=True)
    assert result["corrected"]["animal"] == "Squirrel" and result["was"] == "Raccoon"
    assert state(hass, "sensor.raccoon_location").state in ("unknown", "Not seen")


async def test_camera_notes_reach_only_that_camera(hass, watch):
    w = watch["watch"]
    garage, _ = w._prompt(w.cameras["garage"])
    yard, _ = w._prompt(w.cameras["yard"])
    assert "ABOUT THIS CAMERA'S VIEW: The bins are at the left edge." in garage
    assert "bins" not in yard

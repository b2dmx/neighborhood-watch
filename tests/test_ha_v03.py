"""v0.3: wildlife, alerts, corrections, training data and deterrents in a real Home Assistant."""
from __future__ import annotations

import asyncio
import time
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
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

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


def kinds(hass):
    """Wildlife tracked on the hub: {kind: {where, tonight, week, pest, ...}}."""
    return state(hass, "sensor.neighborhood_watch_wildlife").attributes["kinds"]


def camera_options(entry, name):
    return next(d for d in entry.options["cameras"].values() if d["name"] == name)


def state(hass, entity_id):
    s = hass.states.get(entity_id)
    assert s is not None, f"{entity_id} missing"
    return s


async def test_wildlife_is_tracked_on_the_hub(hass, watch):
    watch["ai"].say(FIRST, "Raccoon|high|na|na|")
    await trigger(hass, "yard")
    raccoon = kinds(hass)["Raccoon"]
    assert (raccoon["where"], raccoon["tonight"], raccoon["pest"]) == ("Yard", 1, True)
    pest = state(hass, "binary_sensor.neighborhood_watch_pest_on_camera")
    assert (pest.state, pest.attributes["animal"], pest.attributes["camera"]) == ("on", "Raccoon", "Yard")
    assert state(hass, "switch.neighborhood_watch_pests_critical_alerts").state == "off"
    assert hass.states.get("sensor.raccoon_location") is None  # no device per wild animal
    assert state(hass, "switch.garage_critical_alerts").state == "off"
    (event,) = watch["events"]
    assert event.data["sighting_id"]


async def test_older_setup_is_moved_to_alert_at(hass, watch):
    """0.6 settings become today's: house cameras and alert scopes → Alert at, cameras and
    people → Configure, wildlife pages → the pest list."""
    entry = watch["entry"]
    assert entry.minor_version == 3
    assert sorted(s.subentry_type for s in entry.subentries.values()) == ["animal", "animal"]
    opts = entry.options
    garage, yard = camera_options(entry, "Garage"), camera_options(entry, "Yard")
    assert garage["critical_except"] == ["luna"] and "house" not in garage
    assert yard["critical_except"] == ["luna"]
    assert sorted(p["name"] for p in opts["people"].values()) == ["Ann", "Bo"]
    assert opts["pests"] == ["Raccoon"]  # the opossum wasn't a pest: it's other wildlife now
    milo = next(s.data for s in entry.subentries.values() if s.title == "Milo")
    assert milo["alert_at"] == ["*"] and milo["critical_at"] == []
    assert opts["unknown_alert_at"] == ["*"] and "unknown_alerts" not in opts
    assert opts["other_alert_at"] == ["garage"] and opts["motion_alert_at"] == ["garage"]
    assert "mode" not in opts


async def test_alerts_reach_everyone_with_buttons(hass, watch):
    watch["ai"].say(FIRST, "Raccoon|high|na|na|")
    await trigger(hass, "yard")
    assert sorted(m["to"] for m in watch["sent"]) == ["ann", "bo"]
    msg = watch["sent"][0]
    assert msg["title"] == "Pest: Raccoon — Yard"
    assert "Lights" not in msg["message"]  # the yard has no deterrent
    data = msg["data"]
    assert data["push"] == {"interruption-level": "active"}
    assert data["image"].startswith("https://example.ui.nabu.casa/local/neighborhood_watch/")
    assert data["image"].split("?")[0].endswith(f"/{watch['watch'].data['www_secret']}/yard.jpg")
    titles = [a["title"] for a in data["actions"]]
    assert titles == ["✓ Right", "? Not sure", "… Other"]
    assert "url" not in data  # no review page set: tapping the alert opens the app as usual


async def test_other_takes_it_off_the_animal_and_keeps_it_for_review(hass, watch):
    entry = watch["entry"]
    hass.config_entries.async_update_entry(entry, options={**entry.options, "review_page": "/lovelace/pets#review"})
    await hass.async_block_till_done()
    before = state(hass, "sensor.milo_last_seen").state
    watch["ai"].say(FIRST, "Milo|high|uniform|black|")
    await trigger(hass, "yard")
    data = watch["sent"][-1]["data"]
    assert data["url"] == data["clickAction"] == "/lovelace/pets#review"
    prefix, _, sid = data["actions"][2]["action"].split("|")
    assert state(hass, "sensor.milo_last_seen").state != before
    hass.bus.async_fire("mobile_app_notification_action", {"action": f"{prefix}|other|{sid}"})
    await hass.async_block_till_done()
    assert state(hass, "sensor.milo_last_seen").state == before  # off Milo's tracking
    queue = state(hass, "sensor.neighborhood_watch_to_review").attributes["items"]
    assert sid in [i["id"] for i in queue]  # still waiting for what it was
    await hass.services.async_call(DOMAIN, "correct", {"sighting_id": sid, "answer": "Raccoon"}, blocking=True)
    await hass.async_block_till_done()
    queue = state(hass, "sensor.neighborhood_watch_to_review").attributes["items"]
    assert sid not in [i["id"] for i in queue]


async def test_an_older_alerts_typed_reply_still_corrects_the_sighting(hass, watch):
    watch["ai"].say(FIRST, "Raccoon|high|na|na|")
    await trigger(hass, "yard")
    # Alerts sent before "Other…" replaced it may still carry the typed reply: it keeps working.
    prefix, _, sid = watch["sent"][0]["data"]["actions"][0]["action"].split("|")
    action = f"{prefix}|was|{sid}"
    with patch.object(watch["watch"], "async_correct") as correct:
        hass.bus.async_fire("mobile_app_notification_action", {"action": action, "reply_text": " fox "})
        await hass.async_block_till_done()
        hass.bus.async_fire("mobile_app_notification_action", {"action": action, "reply_text": "milo"})
        await hass.async_block_till_done()
        hass.bus.async_fire("mobile_app_notification_action", {"action": action, "reply_text": "  "})
        await hass.async_block_till_done()
    # Matched to the wildlife kind and the animal's own spelling; an empty reply is ignored.
    assert [c.args for c in correct.call_args_list] == [(sid, None, "Fox"), (sid, None, "Milo")]


async def test_critical_switch_but_quiet_zone_mutes_it(hass, watch):
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.neighborhood_watch_pests_critical_alerts"}, blocking=True)
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
    msg = watch["sent"][0]
    assert msg["title"] == "Pest: Raccoon — Garage"
    assert msg["message"].endswith("· Lights on")
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
    assert "Raccoon" not in kinds(hass)
    assert state(hass, "binary_sensor.neighborhood_watch_pest_on_camera").state == "off"


async def test_correct_accepts_any_animal_name(hass, watch):
    watch["ai"].say(FIRST, "Raccoon|high|na|na|")
    await trigger(hass, "yard")
    result = await hass.services.async_call(DOMAIN, "correct", {"answer": "great blue heron"}, blocking=True, return_response=True)
    assert result["corrected"]["animal"] == "Great Blue Heron"


async def test_night_summary_counts_wildlife(hass, watch):
    watch["ai"].say(FIRST, "Raccoon|high|na|na|")
    await trigger(hass, "yard")
    result = await hass.services.async_call(DOMAIN, "build_summary", {}, blocking=True, return_response=True)
    assert result["lines"] == ["Raccoon — 1 sighting (Yard)"]


async def test_wildlife_names_and_notes_reach_the_prompt(hass, watch):
    w = watch["watch"]
    prompt, _ = w._prompt(w.cameras["yard"])
    assert "Listed wildlife (name these by kind): Raccoon, Squirrel, Chipmunk, Opossum" in prompt  # pests first



async def test_android_critical_fields_and_plain_notify(hass, watch):
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.neighborhood_watch_pests_critical_alerts"}, blocking=True)
    watch["ai"].say(FIRST, "Raccoon|high|na|na|")
    await trigger(hass, "yard")
    bo = next(m for m in watch["sent"] if m["to"] == "bo")
    assert (bo["data"]["ttl"], bo["data"]["priority"], bo["data"]["channel"]) == (0, "high", "alarm_stream")


async def test_a_plain_notify_service_gets_the_picture_link_in_the_text(hass, watch):
    from dataclasses import replace

    from custom_components.neighborhood_watch import logic

    w = watch["watch"]
    chat: list[dict[str, Any]] = []

    async def family_chat(call: ServiceCall) -> None:
        chat.append(dict(call.data))

    hass.services.async_register("notify", "family_chat", family_chat)
    alert = logic.Alert("animal", "Milo", "Milo is in the yard.", "milo", logic.NORMAL)
    image = w.image_url("yard", dt_util.utcnow())
    bo = next(p for p in w.recipients if p.name == "Bo")
    await w._send(replace(bo, notify="family_chat"), alert, image, [])
    assert chat == [{"title": "Milo", "message": f"Milo is in the yard.\n{image}"}]
    await w._send(bo, alert, image, [])
    phone = watch["sent"][-1]
    assert phone["message"] == "Milo is in the yard." and phone["data"]["image"] == image


async def test_timed_checks_for_cameras_without_sensors(hass, watch, freezer):
    entry = watch["entry"]
    cameras = {cid: ({**d, "check_every": 5} if d["name"] == "Yard" else d) for cid, d in entry.options["cameras"].items()}
    hass.config_entries.async_update_entry(entry, options={**entry.options, "cameras": cameras})
    await hass.async_block_till_done()
    watch["ai"].say(FIRST, "None|high|na|na|")
    freezer.tick(timedelta(minutes=5, seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert watch["ai"].calls == [FIRST]


async def test_watch_all_day(hass, watch, freezer):
    from datetime import datetime


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
    # "It was…" offers your animals and every wildlife kind, not only ones seen so far.
    choices = queue.attributes["choices"]
    assert {"Milo", "Raccoon", "Fox", "Deer"} <= set(choices)
    assert len(choices) == len(set(choices))
    await hass.services.async_call(DOMAIN, "correct", {"answer": "unsure", "sighting_id": items[0]["id"]}, blocking=True, return_response=True)
    await hass.services.async_call(DOMAIN, "correct", {"answer": "right", "sighting_id": items[1]["id"]}, blocking=True, return_response=True)
    assert state(hass, "sensor.neighborhood_watch_to_review").state == "0"
    assert kinds(hass)["Raccoon"]["where"] == "Yard"  # unsure changes nothing


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
    watch["ai"].say(SECOND, "Raccoon|high|na|na|")  # a pest is double-checked
    await trigger(hass, "yard")
    assert watch["ai"].calls == [FIRST, SECOND]  # shadow never skips
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
    watch["ai"].say(SECOND, "Raccoon|high|na|na|", "Unknown Cat|medium|striped|grey|big tabby")

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
    assert "Raccoon" not in kinds(hass)
    assert state(hass, "sensor.neighborhood_watch_to_review").state == "2"
    assert any(m["title"] == "Scan finished — trail" for m in watch["sent"])


async def test_scan_needs_a_detector(hass, watch):
    from homeassistant.exceptions import ServiceValidationError

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(DOMAIN, "scan", {"folder": "x"}, blocking=True, return_response=True)
    assert err.value.translation_key == "no_detector"


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
    assert "Raccoon" not in kinds(hass) and "Squirrel" in kinds(hass)


async def test_camera_notes_reach_only_that_camera(hass, watch):
    w = watch["watch"]
    garage, _ = w._prompt(w.cameras["garage"])
    yard, _ = w._prompt(w.cameras["yard"])
    assert "ABOUT THIS CAMERA'S VIEW: The bins are at the left edge." in garage
    assert "bins" not in yard


async def test_add_note_goes_where_the_ai_uses_it(hass, watch):
    entry = watch["entry"]
    before = entry.runtime_data
    result = await hass.services.async_call(
        DOMAIN, "add_note", {"camera": "Yard", "note": "The dark shape by the fence is a bush."},
        blocking=True, return_response=True,
    )
    assert result == {"added_to": "Yard"}
    await hass.async_block_till_done()  # let the update listener run
    await hass.services.async_call(
        DOMAIN, "add_note", {"animal": "milo", "note": "Looks very bright under strong infrared."},
        blocking=True, return_response=True,
    )
    await hass.async_block_till_done()
    data = {s.title: s.data for s in entry.subentries.values()}
    assert camera_options(entry, "Yard")["ai_notes"] == "The dark shape by the fence is a bush."
    assert data["Milo"]["tips"].endswith("Looks very bright under strong infrared.")
    w = entry.runtime_data
    assert w is before and w.own_updates == 0  # saved without a reload
    prompt, _ = w._prompt(w.cameras["yard"])
    assert "bush" in prompt and "very bright under strong infrared" in prompt
    # A later settings change still reloads.
    hass.config_entries.async_update_entry(entry, options={**entry.options, "alerts": False})
    await hass.async_block_till_done()
    assert entry.runtime_data is not before
    prompt, _ = entry.runtime_data._prompt(entry.runtime_data.cameras["yard"])
    assert "bush" in prompt

    from homeassistant.exceptions import ServiceValidationError

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "add_note", {"animal": "Dragon", "note": "x"}, blocking=True, return_response=True)


async def test_move_training_data_into_the_new_folder(hass, watch, tmp_path):
    w = watch["watch"]
    old = tmp_path / "old_dataset"
    (old / "2026-10-14").mkdir(parents=True)
    (old / "2026-10-14" / "abc.jpg").write_bytes(b"jpg")
    (old / "2026-10-14" / "abc.json").write_text("{}")
    result = await hass.services.async_call(
        DOMAIN, "move_training_data", {"from": [str(old)]}, blocking=True, return_response=True
    )
    assert result == {"moved": 1}
    assert (w.dataset_root / "2026-10-14" / "abc.jpg").read_bytes() == b"jpg"
    assert not (old / "2026-10-14").exists()


def test_crop_to_the_detected_animal(tmp_path):
    from io import BytesIO

    from PIL import Image as PILImage

    from custom_components.neighborhood_watch import dataset

    buffer = BytesIO()
    PILImage.new("RGB", (1280, 720), (90, 90, 90)).save(buffer, "JPEG")
    meta = {"detector": {"boxes": [{"confidence": 0.8, "box": [300, 200, 340, 240]}], "size": [640, 360]}}
    dataset.save_sample(tmp_path, "2026-10-16", "abc", buffer.getvalue(), meta)
    with PILImage.open(BytesIO(dataset.read_crop(tmp_path, "2026-10-16", "abc"))) as crop:
        assert crop.width < 1280 and abs(crop.width / crop.height - 4 / 3) < 0.02
    meta["detector"]["boxes"] = []
    dataset.save_sample(tmp_path, "2026-10-16", "def", buffer.getvalue(), meta)
    with PILImage.open(BytesIO(dataset.read_crop(tmp_path, "2026-10-16", "def"))) as full:
        assert full.width == 1280


def test_reference_photo_from_an_iphone_heic(tmp_path):
    from io import BytesIO

    from PIL import Image as PILImage
    from pillow_heif import register_heif_opener

    from custom_components.neighborhood_watch.engine import save_reference

    register_heif_opener()
    buffer = BytesIO()
    PILImage.new("RGB", (2400, 1800), (200, 180, 160)).save(buffer, "HEIF")
    save_reference(buffer.getvalue(), tmp_path / "luna.jpg")
    with PILImage.open(tmp_path / "luna.jpg") as saved:
        assert saved.format == "JPEG" and max(saved.size) == 1280


# --- audit fixes ------------------------------------------------------------------------


async def test_reload_turns_the_deterrent_lights_off(hass, watch):
    watch["ai"].say(FIRST, "Raccoon|high|na|na|")
    await trigger(hass, "garage")
    assert watch["on"] and not watch["off"]
    assert await hass.config_entries.async_reload(watch["entry"].entry_id)
    await hass.async_block_till_done()
    (off,) = watch["off"]
    assert off.data["entity_id"] == ["light.garage_flood"]
    assert "deterring" not in watch["entry"].runtime_data.data


async def test_lights_left_on_before_a_restart_go_off(hass, watch, hass_storage):
    entry = watch["entry"]
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]["deterring"] = {"garage": ["light.garage_flood"]}
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    (off,) = watch["off"]
    assert off.data["entity_id"] == ["light.garage_flood"]


async def test_alerts_go_out_after_the_check_lock_is_released(hass, watch):
    w = watch["watch"]
    held: list[bool] = []

    async def phone(call: ServiceCall) -> None:
        held.append(w._lock.locked())

    hass.services.async_register("notify", "mobile_app_bo", phone)
    watch["ai"].say(FIRST, "Raccoon|high|na|na|")
    await hass.services.async_call(DOMAIN, "identify", {"camera": "yard"}, blocking=True, return_response=True)
    assert held and not any(held)  # sent by the time identify returns, without the lock


async def test_a_slow_ai_does_not_hold_up_other_cameras(hass, watch):
    w = watch["watch"]
    release = asyncio.Event()
    asked: list[str] = []

    async def slow_ai(call: ServiceCall) -> dict[str, Any]:
        asked.append(call.data["entity_id"])
        await release.wait()
        return {"conversation_id": "x", "data": "Milo|high|na|na|"}

    hass.services.async_register("ai_task", "generate_data", slow_ai, supports_response=SupportsResponse.ONLY)

    async def until(condition) -> None:
        # Waits in real time (a thread's sleep), since the test loop's clock may not move.
        for _ in range(500):
            if condition():
                return
            await hass.async_add_executor_job(time.sleep, 0.01)
        raise AssertionError("timed out")

    hass.states.async_set("binary_sensor.garage_motion", "on")
    await until(lambda: asked)  # the garage's AI is thinking
    assert "yard" not in w.frames
    hass.states.async_set("binary_sensor.yard_motion", "on")
    await until(lambda: "yard" in w.frames)  # the yard's snapshot didn't wait for it
    await until(lambda: len(asked) == 2)  # and its AI is asked without waiting either
    release.set()
    await hass.async_block_till_done()
    assert len(asked) == 2
    assert {s["camera"] for s in w.sightings()} == {"garage", "yard"}


async def test_the_ai_chain_has_a_total_time_cap(hass, watch):
    from custom_components.neighborhood_watch import engine

    async def never(call: ServiceCall) -> dict[str, Any]:
        await asyncio.Event().wait()
        return {}

    hass.services.async_register("ai_task", "generate_data", never, supports_response=SupportsResponse.ONLY)
    with patch.object(engine, "IDENTIFY_CAP_SECONDS", 0):  # the test clock is frozen
        result = await hass.services.async_call(DOMAIN, "identify", {"camera": "yard"}, blocking=True, return_response=True)
    assert result["models_used"] == "none answered" and result["animal"] == "Nothing"
    assert watch["watch"].camera_state("yard")["last"]["models_used"] == "none answered"


async def test_an_older_sighting_recorded_late_does_not_move_the_animal_back(hass, watch):
    from custom_components.neighborhood_watch import engine, logic

    w = watch["watch"]
    milo = logic.Verdict("Milo", logic.HIGH, "na", "", "", "milo|high|na|na|", "")
    newer = dt_util.utcnow()
    older = newer - timedelta(minutes=1)
    # The garage's check took its snapshot later but recorded first.
    await w._record(engine.Check(w.cameras["garage"], milo, "test", None, newer))
    await w._record(engine.Check(w.cameras["yard"], milo, "test", None, older))
    state = w.animal_state(w.animal_by_name("Milo"))
    assert state["last_seen"] == newer.isoformat() and state["location"] == "Garage"
    assert state["on_camera_where"] == "Garage"
    assert w.data["last_sighting"]["camera"] == "Garage"
    times = [s["time"] for s in w.sightings()]
    assert times == sorted(times) and [s["where"] for s in w.sightings()][-2:] == ["Yard", "Garage"]


async def test_at_the_time_cap_what_the_ai_had_said_still_counts(hass, watch):
    from custom_components.neighborhood_watch import engine

    real_timeout = asyncio.timeout
    caps: list[asyncio.Timeout] = []

    def timeout(delay):
        if delay != 12345:
            return real_timeout(delay)
        caps.append(real_timeout(None))
        return caps[-1]

    async def answer(call: ServiceCall) -> dict[str, Any]:
        model = call.data["entity_id"]
        if model == "ai_task.down":
            raise HomeAssistantError("down")
        if model == FIRST:
            return {"conversation_id": "x", "data": "Milo|high|na|na|"}
        caps[-1].reschedule(asyncio.get_running_loop().time())  # the cap runs out now
        await asyncio.Event().wait()
        return {}

    hass.services.async_register("ai_task", "generate_data", answer, supports_response=SupportsResponse.ONLY)
    watch["watch"].options["first_look"] = ["ai_task.down", FIRST]  # Milo from a backup
    with patch.object(engine, "IDENTIFY_CAP_SECONDS", 12345), patch.object(engine.asyncio, "timeout", timeout):
        result = await hass.services.async_call(DOMAIN, "identify", {"camera": "yard"}, blocking=True, return_response=True)
    # The second opinion never came: the backup's Milo stands, capped at medium.
    assert result["animal"] == "Milo" and result["confidence"] == "medium"
    assert result["models_used"] == watch["watch"].data["ai"]["last_models"] != "none answered"
    assert "+" not in result["models_used"]


async def test_the_cap_during_a_priority_second_opinion_keeps_the_first_answer(hass, watch):
    from custom_components.neighborhood_watch import engine

    real_timeout = asyncio.timeout
    caps: list[asyncio.Timeout] = []

    def timeout(delay):
        if delay != 12345:
            return real_timeout(delay)
        caps.append(real_timeout(None))
        return caps[-1]

    async def answer(call: ServiceCall) -> dict[str, Any]:
        if call.data["entity_id"] == FIRST:
            return {"conversation_id": "x", "data": "Luna|high|blotchy|white|"}
        caps[-1].reschedule(asyncio.get_running_loop().time())  # the cap runs out now
        await asyncio.Event().wait()
        return {}

    hass.services.async_register("ai_task", "generate_data", answer, supports_response=SupportsResponse.ONLY)
    w = watch["watch"]
    with patch.object(engine, "IDENTIFY_CAP_SECONDS", 12345), patch.object(engine.asyncio, "timeout", timeout):
        result = await hass.services.async_call(DOMAIN, "identify", {"camera": "yard"}, blocking=True, return_response=True)
    # The priority double-check never finished: the main model's Luna stands, at medium.
    assert result["animal"] == "Luna" and result["confidence"] == "medium"
    assert "+" not in result["models_used"] and result["models_used"] != "none answered"
    second = w.data["ai"]["models"][SECOND]
    assert second["errors"] == 1 and second["last_error"] == "Cut off by the time limit"


async def test_add_note_rejects_a_blank_note_and_skips_a_repeat(hass, watch):
    import voluptuous as vol

    entry = watch["entry"]
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, "add_note", {"camera": "Yard", "note": "   "}, blocking=True, return_response=True
        )
    for note in ("The bins are by the gate.", "  the bins are by the gate. "):
        result = await hass.services.async_call(
            DOMAIN, "add_note", {"camera": "Yard", "note": note}, blocking=True, return_response=True
        )
        assert result == {"added_to": "Yard"}
        await hass.async_block_till_done()
    assert camera_options(entry, "Yard")["ai_notes"] == "The bins are by the gate."
    tip = "Has a short tail."
    for _ in range(2):
        result = await hass.services.async_call(
            DOMAIN, "add_note", {"animal": "Milo", "note": tip}, blocking=True, return_response=True
        )
        assert result == {"added_to": "Milo"}
        await hass.async_block_till_done()
    tips = next(s.data for s in entry.subentries.values() if s.title == "Milo")["tips"]
    assert tips.count(tip) == 1


async def test_sighting_windows_stop_at_their_cutoff(hass, watch):
    w = watch["entry"].runtime_data
    milo = w.animal_by_name("Milo")
    now = dt_util.utcnow()
    rows = w.sightings()
    rows.clear()
    for days, sid in ((9, "old"), (3, "mid"), (0, "new")):
        t = (now - timedelta(days=days, minutes=1)).isoformat()
        rows.append({"id": sid, "day": t[:10], "time": t, "animal": "Milo", "where": "Yard", "counted": True})
    since = dt_util.as_local(now - timedelta(days=7))  # a local time still compares right
    assert [s["id"] for s in w.counted_sightings(milo, since)] == ["mid", "new"]
    assert [s["id"] for s in w.counted_sightings(milo)] == ["old", "mid", "new"]
    assert [s["id"] for s in w.review_queue()] == ["new", "mid"]
    rows.insert(0, {"id": "timeless", "animal": "Milo"})  # a row without a time can't stall pruning
    w._prune(now)
    assert [s["id"] for s in rows] == ["old", "mid", "new"]


async def test_detector_error_clears_and_an_odd_reply_is_no_answer(hass, watch, aioclient_mock, freezer):
    await _with_detector(hass, watch, "shadow", aioclient_mock, animal=0.5)
    w = watch["entry"].runtime_data
    w.data.setdefault("detector", {})["last_error"] = "old"
    watch["ai"].say(FIRST, "Deer|high|na|na|", "Deer|high|na|na|")
    await trigger(hass, "yard")
    assert "last_error" not in w.data["detector"]
    aioclient_mock.clear_requests()
    aioclient_mock.post("http://vision:8765/detect", json=[1, 2])
    freezer.tick(timedelta(minutes=4))
    await trigger(hass, "yard")
    assert w.data["detector"]["last_error"]
    assert watch["ai"].calls == [FIRST, FIRST]  # the check still ran


async def test_scan_with_the_detector_down_is_a_friendly_error(hass, watch, aioclient_mock):
    import aiohttp

    from homeassistant.exceptions import ServiceValidationError

    entry = watch["entry"]
    hass.config_entries.async_update_entry(entry, options={**entry.options, "detector_url": "http://vision:8765"})
    await hass.async_block_till_done()
    aioclient_mock.post("http://vision:8765/scan", exc=aiohttp.ClientError("down"))
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(DOMAIN, "scan", {"folder": "x"}, blocking=True, return_response=True)
    # A failure on the detector's side, not a mistake in the call.
    assert not isinstance(err.value, ServiceValidationError)
    assert err.value.translation_key == "scan_failed"


async def test_state_of_removed_cameras_and_animals_is_forgotten(hass, watch):
    entry = watch["entry"]
    w = watch["watch"]
    w.data["cameras"]["gone"] = {"current": "Milo"}
    w.data["animals"]["gone"] = {"last_seen": None}
    w.data["critical"]["animal:gone"] = True
    w.data["critical"]["animal:luna"] = True
    w.data["missing"]["gone"] = True
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    data = entry.runtime_data.data
    assert "gone" not in data["cameras"] and "gone" not in data["animals"] and "gone" not in data["missing"]
    assert "animal:gone" not in data["critical"] and data["critical"]["animal:luna"] is True


async def test_diagnostics_hide_people(hass, watch):
    from custom_components.neighborhood_watch.diagnostics import async_get_config_entry_diagnostics

    result = await async_get_config_entry_diagnostics(hass, watch["entry"])
    text = json.dumps(result, default=str)
    assert "mobile_app_ann" not in text and "person.ann" not in text and "zone.work" not in text
    assert all(p["name"] == "**REDACTED**" for p in result["options"]["people"].values())


def test_crop_falls_back_to_the_whole_frame(tmp_path):
    from io import BytesIO

    from PIL import Image as PILImage

    from custom_components.neighborhood_watch import dataset

    buffer = BytesIO()
    PILImage.new("RGB", (320, 240), (90, 90, 90)).save(buffer, "JPEG")
    dataset.save_sample(tmp_path, "2026-10-16", "nobox", buffer.getvalue(), {"detector": {"boxes": [{"confidence": 0.9}]}})
    assert dataset.read_crop(tmp_path, "2026-10-16", "nobox") == dataset.read_frame(tmp_path, "2026-10-16", "nobox")
    (tmp_path / "2026-10-16" / "bad.jpg").write_bytes(b"not a picture")
    (tmp_path / "2026-10-16" / "bad.json").write_text(json.dumps({"detector": {"boxes": [{"box": [1, 2, 3, 4]}]}}))
    assert dataset.read_crop(tmp_path, "2026-10-16", "bad") == b"not a picture"


async def test_a_pest_from_the_first_look_gets_a_free_second_opinion(hass, watch):
    # A cat called a raccoon would send a wrong pest alert: wildlife that alerts is checked.
    watch["ai"].say(FIRST, "Raccoon|high|na|na|")
    watch["ai"].say(SECOND, "Milo|high|na|na|")
    await trigger(hass, "yard")
    assert watch["ai"].calls == [FIRST, SECOND]
    # A harmless kind (a deer) isn't.
    watch["ai"].calls.clear()
    watch["ai"].say(FIRST, "Deer|high|na|na|")
    await trigger(hass, "garage")
    assert watch["ai"].calls == [FIRST]


async def test_an_unchecked_priority_sighting_alerts_but_waits_for_review(hass, watch):
    # Luna named, but no second model could check it: still alert, don't count it yet.
    before = state(hass, "sensor.luna_last_seen").state
    watch["ai"].say(FIRST, "Luna|high|blotchy|white|")  # every second opinion fails
    await trigger(hass, "yard")
    assert any("Luna" in m["title"] for m in watch["sent"])  # the alert still goes out
    assert state(hass, "sensor.luna_last_seen").state == before  # not counted
    queue = state(hass, "sensor.neighborhood_watch_to_review").attributes["items"]
    (item,) = [i for i in queue if i["animal"] == "Luna"]
    # Confirmed with ✓ the next day, it counts.
    await hass.services.async_call(DOMAIN, "correct", {"sighting_id": item["id"], "answer": "right"}, blocking=True)
    await hass.async_block_till_done()
    assert state(hass, "sensor.luna_last_seen").state != before


async def test_undo_last_answer_puts_it_back(hass, watch):
    before = state(hass, "sensor.milo_last_seen").state
    watch["ai"].say(FIRST, "Raccoon|high|na|na|")
    watch["ai"].say(SECOND, "Raccoon|high|na|na|")
    await trigger(hass, "yard")
    queue = state(hass, "sensor.neighborhood_watch_to_review").attributes["items"]
    sid = queue[0]["id"]
    # Answered wrongly: it was "Milo".
    await hass.services.async_call(DOMAIN, "correct", {"sighting_id": sid, "answer": "Milo"}, blocking=True)
    await hass.async_block_till_done()
    assert state(hass, "sensor.milo_last_seen").state != before
    review = state(hass, "sensor.neighborhood_watch_to_review")
    assert sid not in [i["id"] for i in review.attributes["items"]]
    assert review.attributes["last_answer"]["id"] == sid
    result = await hass.services.async_call(DOMAIN, "undo_last_answer", {}, blocking=True, return_response=True)
    await hass.async_block_till_done()
    assert result["undone"] == sid
    assert state(hass, "sensor.milo_last_seen").state == before  # Milo's tracking is back
    review = state(hass, "sensor.neighborhood_watch_to_review")
    assert sid in [i["id"] for i in review.attributes["items"]]  # back in To review
    assert review.attributes["last_answer"] is None

"""End-to-end tests inside a real Home Assistant (pytest-homeassistant-custom-component).

Cameras and AI models are fakes: each fake model answers from a script, so every rule in the
handoff's acceptance list can be driven exactly.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)

from homeassistant.components.camera import Image
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.neighborhood_watch.const import DOMAIN, EVENT_SIGHTING

FIRST = "ai_task.first_look"
BACKUP = "ai_task.backup"
SECOND = "ai_task.second_opinion"
PAID = "ai_task.claude"
NIGHT = datetime(2026, 10, 14, 5, 0, tzinfo=dt_util.UTC)  # 22:00 in US/Pacific
FRAME = b"\xff\xd8\xff frame one"


class FakeAI:
    """Scripted answers per model. An Exception (or None) means no answer."""

    def __init__(self) -> None:
        self.script: dict[str, list[Any]] = defaultdict(list)
        self.calls: list[str] = []

    def say(self, model: str, *answers: Any) -> None:
        self.script[model].extend(answers)

    async def handle(self, call: ServiceCall) -> dict[str, Any]:
        model = call.data["entity_id"]
        self.calls.append(model)
        answer = self.script[model].pop(0) if self.script[model] else None
        if answer is None or isinstance(answer, Exception):
            raise HomeAssistantError(str(answer or "no answer"))
        return {"conversation_id": "x", "data": answer}


def camera_sub(name: str, slug: str, trigger: str, **extra: Any) -> ConfigSubentryData:
    return ConfigSubentryData(
        data={
            "camera": f"camera.{slug}",
            "name": name,
            "slug": slug,
            "triggers": [trigger],
            "skip_when": [f"binary_sensor.{slug}_person"],
            "cooldown": 3,
            "priority_cooldown": 0,
            "missing_cooldown": 0,
            "reopened_by": [],
            **extra,
        },
        subentry_type="camera",
        title=name,
        unique_id=slug,
    )


def animal_sub(name: str, **extra: Any) -> ConfigSubentryData:
    return ConfigSubentryData(
        data={
            "name": name,
            "slug": name.lower(),
            "species": "cat",
            "description": f"{name} description",
            "tips": "",
            "pattern": "na",
            "base": "na",
            "priority": False,
            "always_strict": False,
            "added": (NIGHT - timedelta(days=1)).isoformat(),
            "known_last_seen": "2026-10-12 21:00:00",
            "known_location": "Yard",
            **extra,
        },
        subentry_type="animal",
        title=name,
        unique_id=name.lower(),
    )


@pytest.fixture
async def setup(hass: HomeAssistant, tmp_path: Path, freezer):
    freezer.move_to(NIGHT)
    hass.config.config_dir = str(tmp_path)
    hass.config.media_dirs = {"local": str(tmp_path / "media")}
    hass.config.external_url = "https://example.ui.nabu.casa"
    for model in (FIRST, BACKUP, SECOND, PAID):
        hass.states.async_set(model, "unknown", {"friendly_name": model.split(".")[1].replace("_", " ").title()})
    for slug in ("garage", "driveway", "yard"):
        hass.states.async_set(f"binary_sensor.{slug}_motion", "off")
        hass.states.async_set(f"binary_sensor.{slug}_person", "off")

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Neighborhood Watch",
        options={
            "first_look": [FIRST, BACKUP, PAID],
            "second_opinion": [SECOND, BACKUP, PAID],
            "paid_models": [PAID],
            "night_start": "20:00:00",
            "night_end": "08:00:00",
            "use_dark": False,
            "summary_time": "08:05:00",
        },
        subentries_data=[
            camera_sub("Garage", "garage", "binary_sensor.garage_motion", cooldown=15,
                       priority_cooldown=30, missing_cooldown=2, reopened_by=["driveway"]),
            camera_sub("Driveway", "driveway", "binary_sensor.driveway_motion", cooldown=2),
            camera_sub("Yard", "yard", "binary_sensor.yard_motion"),
            animal_sub("Luna", priority=True, pattern="blotchy", base="white", known_last_seen=None, known_location=None),
            animal_sub("Milo"),
            animal_sub("Pepper", always_strict=True, known_last_seen=None, known_location=None),
            animal_sub("Ziggy", known_last_seen=None, known_location=None),
            animal_sub("Coco", added=(NIGHT - timedelta(days=30)).isoformat(),
                       known_last_seen="2026-10-11 21:30:00", known_location="Cat House"),
            animal_sub("Oscar", known_last_seen="2026-09-01 21:30:00"),
        ],
    )
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, "homeassistant", {})

    ai = FakeAI()
    frames = {"content": FRAME, "fail": False}

    async def fake_image(hass, entity_id, timeout=10, width=None, height=None):
        if frames["fail"]:
            raise HomeAssistantError("camera offline")
        return Image("image/jpeg", frames["content"])

    with patch(
        "custom_components.neighborhood_watch.engine.camera_component.async_get_image",
        side_effect=fake_image,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        hass.services.async_register(
            "ai_task", "generate_data", ai.handle, supports_response=SupportsResponse.ONLY
        )
        events = async_capture_events(hass, EVENT_SIGHTING)
        yield {"entry": entry, "ai": ai, "frames": frames, "events": events, "watch": entry.runtime_data}


async def trigger(hass: HomeAssistant, slug: str) -> None:
    hass.states.async_set(f"binary_sensor.{slug}_motion", "on")
    await hass.async_block_till_done()
    hass.states.async_set(f"binary_sensor.{slug}_motion", "off")
    await hass.async_block_till_done()


def state(hass: HomeAssistant, entity_id: str):
    s = hass.states.get(entity_id)
    assert s is not None, f"{entity_id} missing; have {sorted(hass.states.async_entity_ids())}"
    return s


# --- the happy path -----------------------------------------------------------------------


async def test_milo_sighting_is_tracked_and_announced(hass, setup):
    setup["ai"].say(FIRST, "Milo|high|uniform|black|")
    await trigger(hass, "yard")

    assert setup["ai"].calls == [FIRST]  # high + not special: no second opinion
    (event,) = setup["events"]
    data = event.data
    assert data["animal"] == "Milo"
    assert data["confidence"] == "high"
    assert data["location"] == "yard"
    assert data["location_name"] == "Yard"
    assert data["test"] == "no"
    assert data["image"].startswith("https://example.ui.nabu.casa/local/neighborhood_watch/yard.jpg?t=")
    assert data["models_used"] == "First Look"

    assert state(hass, "sensor.milo_location").state == "Yard"
    assert state(hass, "binary_sensor.milo_on_camera").state == "on"
    assert state(hass, "sensor.yard_animal").state == "Milo"
    assert state(hass, "sensor.milo_sightings_tonight").state == "1"
    assert state(hass, "sensor.milo_last_seen").state != "unknown"


async def test_dashboard_photo_is_the_exact_frame_the_ai_judged(hass, setup, hass_client):
    setup["ai"].say(FIRST, "Milo|high|uniform|black|")
    await trigger(hass, "yard")
    setup["frames"]["content"] = b"a later frame"  # a later snapshot must not replace it
    watch = setup["watch"]
    photo = await hass.async_add_executor_job(watch.photo_path(watch.animal_by_name("Milo")).read_bytes)
    assert photo == FRAME
    www = Path(hass.config.path("www", "neighborhood_watch", "yard.jpg")).read_bytes()
    assert www == FRAME


async def test_on_camera_clears_after_the_window(hass, setup, freezer):
    setup["ai"].say(FIRST, "Milo|high|uniform|black|")
    await trigger(hass, "yard")
    freezer.tick(timedelta(minutes=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert state(hass, "binary_sensor.milo_on_camera").state == "off"
    assert state(hass, "sensor.yard_animal").state == "None"
    assert state(hass, "sensor.milo_location").state == "Yard"


# --- acceptance tests from the handoff ------------------------------------------------------


async def test_failed_snapshot_produces_no_identification(hass, setup):
    setup["frames"]["fail"] = True
    await trigger(hass, "yard")
    assert setup["ai"].calls == []
    assert setup["events"] == []
    assert state(hass, "sensor.yard_last_detection").state == "Snapshot failed"


async def test_all_gemini_503_falls_through_to_claude(hass, setup):
    setup["ai"].say(FIRST, HomeAssistantError("429 quota"))
    setup["ai"].say(BACKUP, HomeAssistantError("429 quota"))
    setup["ai"].say(PAID, "Milo|high|uniform|black|")
    await trigger(hass, "yard")
    # Claude answered as a backup, so free models try to confirm it (both fail here).
    assert setup["ai"].calls == [FIRST, BACKUP, PAID, SECOND, BACKUP]
    (event,) = setup["events"]
    assert event.data["animal"] == "Milo"
    assert event.data["confidence"] == "medium"  # unconfirmed backup
    assert event.data["models_used"] == "Claude (last resort)"


async def test_all_models_fail_is_unsure_nothing(hass, setup):
    await trigger(hass, "garage")
    # First look: all three fail. Second opinion: only the main second model tries.
    assert setup["ai"].calls == [FIRST, BACKUP, PAID, SECOND]
    (event,) = setup["events"]
    assert (event.data["animal"], event.data["confidence"]) == ("Nothing", "low")
    assert event.data["models_used"] == "none answered"


async def test_markdown_answer_parses_as_luna(hass, setup):
    setup["ai"].say(FIRST, "**Luna**|medium|blotchy|white|")
    setup["ai"].say(SECOND, "```\nLuna|medium|blotchy|white|\n```")
    await trigger(hass, "yard")
    (event,) = setup["events"]
    assert (event.data["animal"], event.data["confidence"]) == ("Luna", "medium")
    assert event.data["priority_candidate"] == "yes"


async def test_medium_luna_never_confirms_high_does_with_camera_first(hass, setup):
    setup["ai"].say(FIRST, "Luna|medium|blotchy|white|")
    setup["ai"].say(SECOND, "Luna|high|blotchy|white|")
    await trigger(hass, "yard")
    assert state(hass, "binary_sensor.luna_confirmed_on_camera").state == "off"
    assert state(hass, "binary_sensor.luna_on_camera").state == "on"  # medium still counts

    setup["ai"].say(FIRST, "Luna|high|blotchy|white|")
    setup["ai"].say(SECOND, "Luna|high|blotchy|white|")
    await trigger(hass, "driveway")
    confirmed = state(hass, "binary_sensor.luna_confirmed_on_camera")
    assert confirmed.state == "on"
    assert confirmed.attributes["camera"] == "Driveway"
    assert state(hass, "sensor.luna_last_confirmed").attributes["camera"] == "Driveway"
    assert state(hass, "binary_sensor.luna_confirmed_today").state == "on"


async def test_low_luna_is_still_a_candidate(hass, setup):
    setup["ai"].say(FIRST, "Luna|low|blotchy|white|")
    setup["ai"].say(SECOND, "Oscar|high|uniform|grey|")
    await trigger(hass, "yard")
    (event,) = setup["events"]
    assert event.data["priority_candidate"] == "yes"
    assert event.data["confidence"] == "low"
    assert state(hass, "sensor.luna_location").state == "Not seen"


async def test_recently_absent_named_by_one_model_is_low_and_not_tracked(hass, setup):
    watch = setup["watch"]
    assert "ziggy" in watch.strict_keys()  # never seen
    assert state(hass, "binary_sensor.ziggy_recently_absent").state == "on"
    setup["ai"].say(FIRST, "Ziggy|high|uniform|grey|")
    setup["ai"].say(SECOND, "Milo|high|uniform|black|")
    await trigger(hass, "yard")
    (event,) = setup["events"]
    assert event.data["confidence"] == "low"
    assert state(hass, "sensor.ziggy_location").state == "Not seen"
    assert state(hass, "sensor.milo_location").state == "Yard"  # unchanged (the seeded value)


async def test_prompt_names_absent_cats(hass, setup):
    watch = setup["watch"]
    prompt, attachments = watch._prompt(watch.cameras["yard"])
    assert "RECENTLY ABSENT CATS: Pepper, Ziggy, Oscar -" in prompt
    assert "from the Yard camera" in prompt
    assert attachments[0]["media_content_id"].endswith("/neighborhood_watch/frames/yard.jpg")


async def test_test_sightings_never_change_tracking(hass, setup):
    await hass.services.async_call(
        DOMAIN,
        "test_sighting",
        {"camera": "Garage", "animal": "Luna", "confidence": "high"},
        blocking=True,
        return_response=True,
    )
    (event,) = setup["events"]
    assert event.data["test"] == "yes"
    assert event.data["priority_candidate"] == "yes"
    assert state(hass, "sensor.luna_location").state == "Not seen"
    assert state(hass, "binary_sensor.luna_confirmed_on_camera").state == "off"
    assert setup["watch"].sightings() == []


# --- cooldowns, skips and the night window ----------------------------------------------------


async def test_cooldown_blocks_a_second_check(hass, setup, freezer):
    setup["ai"].say(FIRST, "Milo|high|uniform|black|", "Milo|high|uniform|black|")
    await trigger(hass, "yard")
    freezer.tick(timedelta(minutes=2))
    await trigger(hass, "yard")
    assert len(setup["events"]) == 1
    freezer.tick(timedelta(minutes=2))
    await trigger(hass, "yard")
    assert len(setup["events"]) == 2


async def test_driveway_reopens_the_garage(hass, setup, freezer):
    say = setup["ai"].say
    say(FIRST, "Raccoon|high|na|na|", "Raccoon|high|na|na|", "Raccoon|high|na|na|")
    await trigger(hass, "garage")
    freezer.tick(timedelta(minutes=5))
    await trigger(hass, "garage")
    assert len(setup["events"]) == 1  # 15 min garage cooldown
    await trigger(hass, "driveway")
    freezer.tick(timedelta(minutes=1))
    await trigger(hass, "garage")
    assert len(setup["events"]) == 3


async def test_missing_mode_shortens_the_garage_cooldown(hass, setup, freezer):
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.luna_missing_mode"}, blocking=True)
    setup["ai"].say(FIRST, "Raccoon|high|na|na|", "Raccoon|high|na|na|")
    await trigger(hass, "garage")
    freezer.tick(timedelta(minutes=3))
    await trigger(hass, "garage")
    assert len(setup["events"]) == 2


async def test_person_skips_the_check(hass, setup):
    hass.states.async_set("binary_sensor.yard_person", "on")
    await trigger(hass, "yard")
    assert setup["ai"].calls == []


async def test_daytime_is_ignored(hass, setup, freezer):
    freezer.move_to(datetime(2026, 10, 13, 19, 0, tzinfo=dt_util.UTC))  # noon Pacific
    await trigger(hass, "yard")
    assert setup["ai"].calls == []


# --- services -----------------------------------------------------------------------------


async def test_mark_wrong_undoes_tracking(hass, setup, freezer):
    say = setup["ai"].say
    say(FIRST, "Pepper|high|uniform|black|")
    say(SECOND, "Pepper|high|uniform|black|")
    await trigger(hass, "yard")
    assert state(hass, "sensor.pepper_location").state == "Yard"
    result = await hass.services.async_call(
        DOMAIN, "mark_wrong", {"animal": "Pepper"}, blocking=True, return_response=True
    )
    assert result["removed"]["where"] == "Yard"
    assert state(hass, "sensor.pepper_location").state == "Not seen"
    assert state(hass, "binary_sensor.pepper_on_camera").state == "off"
    assert state(hass, "sensor.pepper_sightings_tonight").state == "0"


async def test_identify_service_returns_the_result(hass, setup):
    setup["ai"].say(FIRST, "Milo|high|uniform|black|")
    result = await hass.services.async_call(
        DOMAIN, "identify", {"camera": "yard"}, blocking=True, return_response=True
    )
    assert result["animal"] == "Milo"
    assert result["counted"] is True


async def test_night_summary(hass, setup, freezer):
    say = setup["ai"].say
    say(FIRST, "Milo|high|uniform|black|", "Milo|medium|uniform|black|")
    await trigger(hass, "yard")
    freezer.tick(timedelta(minutes=4))
    await trigger(hass, "driveway")
    result = await hass.services.async_call(DOMAIN, "build_summary", {}, blocking=True, return_response=True)
    assert result["lines"] == ["Milo — 2 sightings (Yard, Driveway)"]
    assert state(hass, "sensor.neighborhood_watch_night_summary").state == "Milo — 2 sightings (Yard, Driveway)"
    assert state(hass, "sensor.milo_sightings_tonight").state == "0"  # a new night started


async def test_set_reference_photo_from_last_photo(hass, setup):
    setup["ai"].say(FIRST, "Milo|high|uniform|black|")
    await trigger(hass, "yard")
    with patch("custom_components.neighborhood_watch.engine.save_reference") as save:
        await hass.services.async_call(DOMAIN, "set_reference_photo", {"animal": "Milo"}, blocking=True)
    assert save.call_args.args[0] == FRAME


# --- shadow mode ------------------------------------------------------------------------------


async def test_known_last_seen_seeds_tracking(hass, setup):
    assert state(hass, "sensor.coco_location").state == "Cat House"
    assert state(hass, "binary_sensor.coco_recently_absent").state == "off"
    assert state(hass, "sensor.coco_last_seen").state.startswith("2026-10-12T04:30:00")  # 21:30 Pacific
    assert state(hass, "binary_sensor.oscar_recently_absent").state == "on"



async def test_backup_answer_is_double_checked(hass, setup):
    """Last night's false raccoon: a backup alone said Raccoon/high."""
    setup["ai"].say(FIRST, HomeAssistantError("429 quota"))
    setup["ai"].say(BACKUP, "Raccoon|high|na|na|")
    setup["ai"].say(SECOND, "None|high|na|na|")
    await trigger(hass, "garage")
    assert setup["ai"].calls == [FIRST, BACKUP, SECOND]
    (event,) = setup["events"]
    assert (event.data["animal"], event.data["confidence"]) == ("Nothing", "low")


async def test_backup_saying_nothing_is_not_double_checked(hass, setup):
    setup["ai"].say(FIRST, HomeAssistantError("429 quota"))
    setup["ai"].say(BACKUP, "None|high|na|na|")
    await trigger(hass, "yard")
    assert setup["ai"].calls == [FIRST, BACKUP]


async def test_backup_double_check_never_uses_paid_models(hass, setup):
    setup["ai"].say(FIRST, HomeAssistantError("429 quota"))
    setup["ai"].say(BACKUP, "Raccoon|high|na|na|")
    await trigger(hass, "yard")
    assert PAID not in setup["ai"].calls
    (event,) = setup["events"]
    assert event.data["confidence"] == "medium"


async def test_split_vote_on_luna_alerts_as_luna(hass, setup):
    """A split vote: the backup said the priority cat, the paid model another cat."""
    setup["ai"].say(FIRST, HomeAssistantError("429 quota"))
    setup["ai"].say(BACKUP, "Luna|high|blotchy|white|")
    setup["ai"].say(SECOND, HomeAssistantError("429 quota"))
    setup["ai"].say(PAID, "Ziggy|medium|uniform|black|")
    await trigger(hass, "garage")
    (event,) = setup["events"]
    assert (event.data["animal"], event.data["confidence"]) == ("Luna", "low")
    assert event.data["priority_candidate"] == "yes"


async def test_main_model_rests_outside_its_hours(hass, setup):
    entry = setup["entry"]
    # 22:00 Pacific is outside 23:00-03:00.
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "main_start": "23:00:00", "main_end": "03:00:00"}
    )
    await hass.async_block_till_done()
    setup["ai"].say(BACKUP, "None|high|na|na|")
    await trigger(hass, "yard")
    assert setup["ai"].calls == [BACKUP]


async def test_resting_main_model_comes_before_paid(hass, setup):
    entry = setup["entry"]
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "main_start": "23:00:00", "main_end": "03:00:00"}
    )
    await hass.async_block_till_done()
    setup["ai"].say(BACKUP, ValueError("quota"))
    setup["ai"].say(FIRST, "None|high|na|na|")
    await trigger(hass, "yard")
    assert setup["ai"].calls == [BACKUP, FIRST]



async def test_busy_model_is_retried_once(hass, setup):
    setup["ai"].say(FIRST, HomeAssistantError("503 high demand"), "Milo|high|uniform|black|")
    with patch("custom_components.neighborhood_watch.engine.BUSY_RETRY_SECONDS", 0):
        await trigger(hass, "yard")
    assert setup["ai"].calls == [FIRST, FIRST]
    (event,) = setup["events"]
    assert event.data["animal"] == "Milo"

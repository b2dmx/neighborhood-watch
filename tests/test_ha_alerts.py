"""Alert rules in a real Home Assistant: Alert at / Critical at, camera exceptions, visits,
the Alerts switch, per-person mutes and quiet hours, and missing mode for any animal."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.camera import Image
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.setup import async_setup_component

from custom_components.neighborhood_watch.const import DOMAIN

from .test_ha_integration import FIRST, NIGHT, SECOND, FakeAI, animal_sub, camera_sub, trigger
from .test_ha_v03 import _jpeg, person_sub

EVERY = ["*"]


@pytest.fixture
async def watch(hass: HomeAssistant, tmp_path: Path, freezer):
    freezer.move_to(NIGHT)  # 22:00 local
    hass.config.config_dir = str(tmp_path)
    hass.config.media_dirs = {"local": str(tmp_path / "media")}
    hass.config.external_url = "https://example.ui.nabu.casa"
    assert await async_setup_component(hass, "homeassistant", {})
    for model in (FIRST, SECOND):
        hass.states.async_set(model, "unknown")
    for slug in ("garage", "yard"):
        hass.states.async_set(f"binary_sensor.{slug}_motion", "off")
        hass.states.async_set(f"binary_sensor.{slug}_person", "off")
    hass.states.async_set("person.ann", "Work")
    hass.states.async_set("zone.work", "0", {"friendly_name": "Work"})
    for name in ("bo", "cy", "dee"):
        hass.states.async_set(f"person.{name}", "home")

    sent: list[dict[str, Any]] = []
    for name in ("ann", "bo", "cy", "dee"):

        async def phone(call: ServiceCall, who: str = name) -> None:
            sent.append({"to": who, **call.data})

        hass.services.async_register("notify", f"mobile_app_{name}", phone)

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Neighborhood Watch",
        minor_version=2,
        options={
            "first_look": [FIRST],
            "second_opinion": [SECOND],
            "paid_models": [],
            "use_dark": False,
            "unknown_alert_at": EVERY,
            "other_alert_at": ["garage"],
            "motion_alert_at": [],
            "pests": ["Raccoon", "Opossum"],
            "pest_alert_at": EVERY,
            "pest_critical_at": ["garage"],
        },
        subentries_data=[
            camera_sub("Garage", "garage", "binary_sensor.garage_motion", critical_except=["luna"]),
            camera_sub("Yard", "yard", "binary_sensor.yard_motion"),
            animal_sub("Luna", priority=True, pattern="blotchy", base="white", known_last_seen=None,
                       alert_at=EVERY, critical_at=[]),
            animal_sub("Milo", alert_at=EVERY, critical_at=[]),
            person_sub("Ann", missing_alerts=True, quiet_zones=["zone.work"]),
            person_sub("Bo"),
            person_sub("Cy", missing_alerts=True, quiet_start="21:00:00", quiet_end="07:00:00", quiet_mode="none"),
            person_sub("Dee", muted=["raccoon"]),
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
        yield {"entry": entry, "ai": ai, "sent": sent}


def to(sent: list[dict[str, Any]], who: str) -> list[dict[str, Any]]:
    return [m for m in sent if m["to"] == who]


def level(msg: dict[str, Any]) -> str:
    return msg["data"]["push"]["interruption-level"]


async def switch(hass: HomeAssistant, entity_id: str, on: bool) -> None:
    await hass.services.async_call("switch", "turn_on" if on else "turn_off", {"entity_id": entity_id}, blocking=True)


async def later(freezer, minutes: float) -> None:
    freezer.tick(timedelta(minutes=minutes))


async def test_switches_exist_where_they_should(hass, watch):
    for entity_id in ("switch.luna_alerts", "switch.milo_alerts",
                      "switch.luna_missing_mode", "switch.milo_missing_mode"):
        assert hass.states.get(entity_id) is not None, entity_id
    for entity_id in ("switch.neighborhood_watch_pests_critical_alerts", "switch.milo_critical_alerts",
                      "switch.garage_critical_alerts", "switch.neighborhood_watch_unknown_animals_critical_alerts",
                      "switch.neighborhood_watch_unsure_motion_critical_alerts"):
        assert hass.states.get(entity_id).state == "off", entity_id  # every critical switch starts off
    assert hass.states.get("switch.raccoon_alerts") is None  # no switches per wild animal


async def test_critical_switch_rings_only_at_its_cameras(hass, watch, freezer):
    watch["ai"].say(FIRST, "Raccoon|high|na|na|")
    await trigger(hass, "garage")
    assert level(to(watch["sent"], "bo")[-1]) == "active"  # switch off: a normal alert
    await later(freezer, 4)
    await switch(hass, "switch.neighborhood_watch_pests_critical_alerts", True)
    watch["ai"].say(FIRST, "Raccoon|high|na|na|", "Raccoon|high|na|na|")
    await trigger(hass, "yard")
    assert level(to(watch["sent"], "bo")[-1]) == "active"  # not one of its critical cameras
    await later(freezer, 4)
    await trigger(hass, "garage")
    assert level(to(watch["sent"], "bo")[-1]) == "critical"
    assert level(to(watch["sent"], "ann")[-1]) == "active"  # at work: toned down


async def test_critical_camera_rings_for_anything_except_listed(hass, watch, freezer):
    await switch(hass, "switch.garage_critical_alerts", True)
    watch["ai"].say(FIRST, "Milo|high|na|na|", "Luna|high|blotchy|white|")
    watch["ai"].say(SECOND, "Luna|high|blotchy|white|")
    await trigger(hass, "garage")
    (milo,) = to(watch["sent"], "bo")
    assert (milo["title"], level(milo)) == ("Milo — Garage", "critical")
    await later(freezer, 4)
    await trigger(hass, "garage")
    luna = to(watch["sent"], "bo")[-1]
    assert (luna["title"], level(luna)) == ("Possible Luna — Garage", "active")


async def test_same_visit_updates_quietly_but_pests_alert_again(hass, watch, freezer):
    watch["ai"].say(FIRST, "Milo|high|na|na|", "Milo|high|na|na|", "Milo|high|na|na|")
    await trigger(hass, "yard")
    await later(freezer, 5)
    await trigger(hass, "yard")
    await later(freezer, 20)  # longer than the cooldown (3) + 10: a new visit
    await trigger(hass, "yard")
    first, again, new_visit = to(watch["sent"], "bo")
    assert level(first) == "active" and level(new_visit) == "active"
    assert level(again) == "passive" and again["data"]["alert_once"] is True
    assert first["data"]["tag"] == again["data"]["tag"] == "yard_milo"

    watch["sent"].clear()
    watch["ai"].say(FIRST, "Raccoon|high|na|na|", "Raccoon|high|na|na|")
    await later(freezer, 4)
    await trigger(hass, "yard")
    await later(freezer, 4)
    await trigger(hass, "yard")
    assert [level(m) for m in to(watch["sent"], "bo")] == ["active", "active"]


async def test_alerts_switch_off_but_critical_still_rings(hass, watch, freezer):
    await switch(hass, "switch.milo_alerts", False)
    watch["ai"].say(FIRST, "Milo|high|na|na|", "Milo|high|na|na|")
    await trigger(hass, "yard")
    assert watch["sent"] == []
    await switch(hass, "switch.garage_critical_alerts", True)
    await trigger(hass, "garage")
    assert level(to(watch["sent"], "bo")[-1]) == "critical"


async def test_muted_and_quiet_hours_per_person(hass, watch):
    watch["ai"].say(FIRST, "Raccoon|high|na|na|")
    await trigger(hass, "yard")
    assert sorted({m["to"] for m in watch["sent"]}) == ["ann", "bo"]  # Dee muted it; Cy is in quiet hours


async def test_missing_mode_for_any_animal_asks_twice_and_always_arrives(hass, watch):
    await switch(hass, "switch.milo_missing_mode", True)
    hass.states.async_set("person.ann", "home")
    watch["ai"].say(FIRST, "Milo|low|na|na|")
    watch["ai"].say(SECOND, "Milo|low|na|na|")
    await trigger(hass, "yard")
    assert SECOND in watch["ai"].calls  # a missing animal always gets a second look
    (ann,) = to(watch["sent"], "ann")
    assert (ann["title"], level(ann), ann["data"]["push"]["sound"]["volume"]) == ("Possible Milo — Yard", "critical", 1.0)
    (cy,) = to(watch["sent"], "cy")
    assert level(cy) == "active"  # quiet hours with "no alerts" still let missing mode through
    assert to(watch["sent"], "bo") == []  # a low guess at a cat that isn't missing for Bo


async def test_catch_alls(hass, watch, freezer):
    watch["ai"].say(FIRST, "None|low|na|na|", "Fox|high|na|na|", "Fox|high|na|na|", "Unknown Cat|medium|uniform|black|small black cat")
    watch["ai"].say(SECOND, "None|low|na|na|", "Unknown Cat|medium|uniform|black|small black cat")
    await trigger(hass, "garage")
    assert watch["sent"] == []  # unsure motion alerts nowhere here
    await trigger(hass, "yard")
    assert watch["sent"] == []  # other wildlife only at the garage
    await later(freezer, 4)
    await trigger(hass, "garage")
    assert {m["title"] for m in watch["sent"]} == {"Fox — Garage"}
    watch["sent"].clear()
    await later(freezer, 4)
    await trigger(hass, "yard")
    assert {m["title"] for m in watch["sent"]} == {"Possible Unknown Cat — Yard"}


async def test_critical_switch_without_cameras_rings_wherever_it_alerts(hass, watch):
    await switch(hass, "switch.milo_critical_alerts", True)
    watch["ai"].say(FIRST, "Milo|high|na|na|")
    await trigger(hass, "yard")
    assert level(to(watch["sent"], "bo")[-1]) == "critical"


async def test_catch_all_critical_switch(hass, watch):
    await switch(hass, "switch.neighborhood_watch_unknown_animals_critical_alerts", True)
    watch["ai"].say(FIRST, "Unknown Cat|medium|uniform|black|small black cat")
    watch["ai"].say(SECOND, "Unknown Cat|medium|uniform|black|small black cat")
    await trigger(hass, "yard")
    assert level(to(watch["sent"], "bo")[-1]) == "critical"


async def test_person_critical_switch_turns_their_critical_alerts_normal(hass, watch):
    await switch(hass, "switch.neighborhood_watch_bo_gets_critical_alerts", False)
    await switch(hass, "switch.garage_critical_alerts", True)
    watch["ai"].say(FIRST, "Milo|high|na|na|")
    await trigger(hass, "garage")
    assert level(to(watch["sent"], "bo")[-1]) == "active"
    assert level(to(watch["sent"], "dee")[-1]) == "critical"



async def test_pests_and_wildlife_without_pages(hass, watch, freezer):
    watch["ai"].say(FIRST, "Opossum|high|na|na|", "Opossum|high|na|na|", "Opossum|high|na|na|", "Groundhog|high|na|na|")
    await trigger(hass, "garage")
    await later(freezer, 4)
    await trigger(hass, "garage")
    assert [m["title"] for m in to(watch["sent"], "bo")] == ["Pest: Opossum — Garage", "Pest: Opossum — Garage"]
    assert [level(m) for m in to(watch["sent"], "bo")] == ["active", "active"]  # a pest alerts every time
    assert hass.states.get("binary_sensor.neighborhood_watch_pest_on_camera").state == "on"
    assert hass.states.get("binary_sensor.neighborhood_watch_pest_on_camera").attributes["camera"] == "Garage"
    await later(freezer, 4)
    await trigger(hass, "yard")
    await later(freezer, 4)
    await trigger(hass, "yard")
    wildlife = hass.states.get("sensor.neighborhood_watch_wildlife")
    kinds = wildlife.attributes["kinds"]
    assert wildlife.state == "Groundhog · Yard"
    assert kinds["Opossum"]["pest"] is True and kinds["Opossum"]["tonight"] == 3
    assert kinds["Groundhog"]["pest"] is False
    assert len(to(watch["sent"], "bo")) == 3  # the opossum at the yard too; a groundhog never alerts
    assert hass.states.get("sensor.opossum_last_seen") is None

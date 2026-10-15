"""Logic tests. Run: py -3 -m pytest tests (Home Assistant is not needed)."""
from __future__ import annotations

from datetime import datetime, time, timedelta
import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).parents[1] / "custom_components" / "neighborhood_watch" / "logic.py"
_spec = importlib.util.spec_from_file_location("nw_logic", _path)
logic = importlib.util.module_from_spec(_spec)
sys.modules["nw_logic"] = logic
_spec.loader.exec_module(logic)

RULES = {
    r.key: r
    for r in (
        logic.AnimalRule("Luna", priority=True, pattern="blotchy", base="white"),
        logic.AnimalRule("Ziggy"),
        logic.AnimalRule("Coco"),
        logic.AnimalRule("Oscar"),
        logic.AnimalRule("Pepper", always_strict=True),
        logic.AnimalRule("Milo"),
    )
}
PRIORITY = {"luna"}


def decide(first: str, second: str = "", strict=()):
    return logic.decide(logic.tidy(first), logic.tidy(second), RULES, strict)


# --- tidying ----------------------------------------------------------------------


def test_markdown_bold_parses_as_luna():
    v = decide("**Luna**|medium|blotchy|white|")
    assert v.animal == "Luna"
    assert v.first_look_said == "luna|medium|blotchy|white|"


def test_fenced_and_labelled_answers_parse():
    assert logic.tidy("```\nAnswer: Luna|high|blotchy|white|\n```") == "Luna|high|blotchy|white|"
    assert logic.tidy('"Milo|high|uniform|black|"') == "Milo|high|uniform|black|"


def test_reply_without_pipes_is_unsure_nothing():
    assert logic.tidy("I can't tell what this is.") == "None|low|na|na|"
    assert logic.tidy("") == ""
    assert logic.tidy(None) == ""


def test_first_line_with_pipe_wins():
    assert logic.tidy("Looking at the image:\nOscar|high|uniform|grey|\nextra") == "Oscar|high|uniform|grey|"


# --- second opinion ---------------------------------------------------------------


def test_second_opinion_triggers():
    tidy = logic.tidy
    assert logic.needs_second_opinion(tidy("Luna|high|blotchy|white|"), PRIORITY, {"pepper"})
    assert logic.needs_second_opinion(tidy("Pepper|high|uniform|black|"), PRIORITY, {"pepper"})
    assert logic.needs_second_opinion(tidy("Unknown Cat|high|striped|grey|tabby"), PRIORITY, set())
    assert logic.needs_second_opinion(tidy("Oscar|low|uniform|grey|"), PRIORITY, set())
    assert logic.needs_second_opinion(tidy("None|medium|na|na|"), PRIORITY, set())
    assert logic.needs_second_opinion("", PRIORITY, set())
    assert logic.needs_second_opinion(tidy("Ziggy|high|uniform|grey|"), PRIORITY, {"ziggy"})
    assert not logic.needs_second_opinion(tidy("Oscar|high|uniform|grey|"), PRIORITY, set())
    assert not logic.needs_second_opinion(tidy("None|high|na|na|"), PRIORITY, set())
    assert not logic.needs_second_opinion(tidy("Raccoon|medium|striped|grey|"), PRIORITY, set())


# --- priority animal (Luna) ------------------------------------------------------


def test_luna_high_needs_both_high_with_coat():
    v = decide("Luna|high|blotchy|white|", "Luna|high|blotchy|white|")
    assert (v.animal, v.confidence, v.priority_candidate) == ("Luna", "high", "Luna")


def test_luna_medium_when_either_is_medium():
    assert decide("Luna|medium|blotchy|white|", "Luna|high|blotchy|white|").confidence == "medium"
    assert decide("Luna|high|blotchy|white|", "Luna|medium|blotchy|white|").confidence == "medium"


def test_luna_low_when_coat_mismatch():
    assert decide("Luna|high|uniform|white|", "Luna|high|blotchy|white|").confidence == "low"
    assert decide("Luna|high|blotchy|white|", "Luna|high|blotchy|grey|").confidence == "low"


def test_luna_without_second_opinion_caps_at_medium():
    v = decide("Luna|high|blotchy|white|", "")
    assert v.confidence == "medium"
    assert decide("Luna|low|blotchy|white|", "").confidence == "low"


def test_luna_only_second_named_is_low_but_candidate():
    v = decide("Oscar|low|uniform|grey|", "Luna|high|blotchy|white|")
    assert v.animal == "Luna"
    assert v.confidence == "low"
    assert v.priority_candidate == "Luna"


def test_luna_named_first_only_is_candidate():
    v = decide("Luna|low|blotchy|white|", "Oscar|high|uniform|grey|")
    assert v.animal == "Luna"  # a split vote involving the priority animal stays her
    assert v.pattern == "blotchy"
    assert v.confidence == "low"  # disagreement
    assert v.priority_candidate == "Luna"


def test_nobody_answered_first_luna_from_second_is_low():
    v = decide("", "Luna|high|blotchy|white|")
    assert v.confidence == "low"


# --- strict animals ---------------------------------------------------------------


def test_pepper_counts_only_when_both_high():
    assert decide("Pepper|high|uniform|black|", "Pepper|high|uniform|black|").confidence == "high"
    assert decide("Pepper|high|uniform|black|", "Pepper|medium|uniform|black|").confidence == "low"
    assert decide("Pepper|high|uniform|black|", "").confidence == "low"


def test_recently_absent_named_by_one_model_is_low_and_not_tracked():
    v = decide("Ziggy|high|uniform|grey|", "Milo|high|uniform|black|", strict={"ziggy"})
    assert v.confidence == "low"
    v = decide("Milo|high|uniform|black|", "Ziggy|high|uniform|grey|", strict={"ziggy"})
    assert (v.animal, v.confidence) == ("Ziggy", "low")
    assert not logic.counts(v, RULES)


def test_recently_absent_confirmed_by_both():
    v = decide("Coco|high|striped|grey|", "Coco|high|striped|grey|", strict={"coco"})
    assert v.confidence == "high"
    assert logic.counts(v, RULES)


# --- general rules ----------------------------------------------------------------


def test_disagreement_is_low():
    assert decide("Oscar|high|uniform|grey|", "Raccoon|high|na|na|").confidence == "low"


def test_first_saw_nothing_caps_second_high_at_medium():
    v = decide("None|low|na|na|", "Raccoon|high|striped|grey|")
    assert (v.animal, v.confidence) == ("Raccoon", "medium")


def test_unknown_first_does_not_force_disagreement():
    v = decide("Unknown Cat|low|striped|grey|tabby", "Oscar|high|uniform|grey|")
    assert (v.animal, v.confidence) == ("Oscar", "high")


def test_people_and_none_become_nothing():
    assert decide("Person|high|na|na|").animal == "Nothing"
    assert decide("None|high|na|na|").animal == "Nothing"
    assert decide("Human|high|na|na|").animal == "Nothing"


def test_all_models_failed_is_unsure_nothing():
    v = decide("", "")
    assert (v.animal, v.confidence) == ("Nothing", "low")


def test_unknown_cat_description_kept_and_trimmed():
    v = decide("Unknown Cat|medium|striped|grey|" + "x" * 200, "Unknown Cat|medium|striped|grey|" + "y" * 200)
    assert v.description == "y" * 90
    assert decide("Oscar|high|uniform|grey|ignored").description == ""


def test_junk_confidence_is_low():
    assert decide("Oscar|probably|uniform|grey|", "Oscar|probably|uniform|grey|").confidence == "low"


# --- counting -----------------------------------------------------------------------


def test_counts():
    assert logic.counts(decide("Oscar|medium|uniform|grey|"), RULES)
    assert not logic.counts(decide("Oscar|low|uniform|grey|", "Oscar|low|uniform|grey|"), RULES)
    assert logic.counts(decide("Raccoon|low|na|na|", "Raccoon|low|na|na|"), RULES)
    unknown = decide("Unknown Cat|low|na|na|small", "Unknown Cat|low|na|na|small")
    assert logic.is_cat_for_log(unknown, RULES)
    assert not logic.is_cat_for_log(decide("Raccoon|high|na|na|"), RULES)


def test_visitors_and_noteworthy():
    wildlife = logic.DEFAULT_WILDLIFE
    assert logic.is_visitor(decide("Raccoon|high|na|na|"), RULES, wildlife)
    assert not logic.is_visitor(decide("Spider|high|na|na|"), RULES, wildlife)
    assert not logic.is_noteworthy(decide("Squirrel|high|na|na|"), RULES)
    assert logic.is_noteworthy(decide("Raccoon|high|na|na|"), RULES)
    low_luna = decide("Luna|low|blotchy|white|", "Luna|low|blotchy|white|")
    assert not logic.is_noteworthy(low_luna, RULES)


def test_recently_absent_window():
    now = datetime(2026, 10, 13, 22, 0)
    assert logic.recently_absent(now - timedelta(days=15), now, 14)
    assert not logic.recently_absent(now - timedelta(days=3), now, 14)
    assert logic.recently_absent(None, now, 14)


# --- cooldowns and night --------------------------------------------------------------

GARAGE = logic.Cooldown(minutes=15, after_priority_minutes=30, missing_mode_minutes=2)


def test_garage_cooldown_windows():
    assert logic.cooldown_window(GARAGE, False, False) == timedelta(minutes=15)
    assert logic.cooldown_window(GARAGE, True, False) == timedelta(minutes=30)
    assert logic.cooldown_window(GARAGE, True, True) == timedelta(minutes=2)
    assert logic.cooldown_window(logic.Cooldown(3), True, True) == timedelta(minutes=3)


def test_driveway_reopens_garage():
    now = datetime(2026, 10, 13, 22, 0)
    last = now - timedelta(minutes=5)
    window = timedelta(minutes=15)
    assert not logic.may_check(now, last, window)
    assert not logic.may_check(now, last, window, [now - timedelta(minutes=6)])
    assert logic.may_check(now, last, window, [now - timedelta(minutes=1)])
    assert logic.may_check(now, None, window)
    assert logic.may_check(now, now - timedelta(minutes=15), window)


def test_night_window():
    start, end = time(20), time(8)
    assert logic.is_night(time(21), start, end, dark=False)
    assert logic.is_night(time(7, 59), start, end, dark=False)
    assert not logic.is_night(time(12), start, end, dark=False)
    assert logic.is_night(time(19, 30), start, end, dark=True)  # early sunset
    assert not logic.is_night(time(19, 30), start, end, dark=True, use_dark=False)


# --- prompt -------------------------------------------------------------------------


def test_prompt_lists_animals_absent_and_references():
    animals = [
        logic.AnimalProfile("Luna", "a CALICO, female", "LUNA RULE: must be mostly white"),
        logic.AnimalProfile("Milo", "PREDOMINANTLY BLACK body.", has_reference=True),
    ]
    text = logic.build_prompt(
        logic.DEFAULT_INSTRUCTIONS,
        location="Garage",
        animals=animals,
        absent=["Ziggy", "Pepper"],
        visiting=["Milo"],
        extra_rules="DARK-CAT RULE: white markings decide",
        references=["Milo"],
    )
    assert "from the Garage camera" in text
    assert "Luna - a CALICO, female. LUNA RULE: must be mostly white." in text
    assert "Milo - PREDOMINANTLY BLACK body." in text
    assert "RECENTLY ABSENT CATS: Ziggy, Pepper" in text
    assert "prefer a cat that has been visiting (Milo) or return low confidence" in text
    assert "DARK-CAT RULE: white markings decide." in text
    assert text.startswith("The FIRST image is the camera frame")
    assert "{" not in text and "  " not in text
    assert "Listed wildlife (name these by kind): Squirrel, Chipmunk" in text


def test_prompt_without_absent_or_extras():
    text = logic.build_prompt(logic.DEFAULT_INSTRUCTIONS, location="Yard", animals=[])
    assert "RECENTLY ABSENT" not in text
    assert "{" not in text


# --- summary ------------------------------------------------------------------------


def test_summary_order_counts_and_places():
    s = logic.summarize(
        [("Milo", "Yard"), ("Luna", "Garage"), ("Milo", "Cat House"), ("Milo", "Yard"), ("Unknown Cat", "Yard")],
        ["Luna", "Ziggy", "Milo"],
    )
    assert s.lines == [
        "Luna — 1 sighting (Garage)",
        "Milo — 3 sightings (Yard, Cat House)",
        "1 Unknown Cat sighting",
    ]
    assert logic.summarize([], ["Luna"]).text == "No sightings last night."


def test_summary_text_is_capped_but_lines_are_not():
    many = [("Milo", f"Camera {i}") for i in range(60)]
    s = logic.summarize(many, ["Milo"])
    assert len(s.text) == 255
    assert len(s.places["Milo"]) == 60


def test_clock():
    assert logic.clock(datetime(2026, 1, 1, 21, 5)) == "9:05 PM"
    assert logic.clock(datetime(2026, 1, 1, 0, 30)) == "12:30 AM"
    assert logic.clock(datetime(2026, 1, 1, 12, 0)) == "12:00 PM"


def test_split_vote_with_priority_from_second_model_stays_priority():
    v = decide("Luna|high|blotchy|white|", "Ziggy|medium|uniform|black|", strict={"ziggy"})
    assert (v.animal, v.confidence, v.priority_candidate) == ("Luna", "low", "Luna")
    assert not logic.counts(v, RULES)


def test_split_vote_without_priority_keeps_second_name():
    v = decide("Milo|high|uniform|black|", "Ziggy|medium|uniform|grey|")
    assert (v.animal, v.confidence) == ("Ziggy", "low")


def test_unconfirmed_backup_is_capped_at_medium():
    first = logic.tidy("Raccoon|high|na|na|")
    v = logic.decide(first, "", RULES, unconfirmed=True)
    assert (v.animal, v.confidence) == ("Raccoon", "medium")
    v = logic.decide(first, logic.tidy("Raccoon|high|na|na|"), RULES, unconfirmed=True)
    assert v.confidence == "high"  # confirmed by a second model


def test_names_animal():
    assert logic.names_animal(logic.tidy("Raccoon|high|na|na|"))
    assert logic.names_animal(logic.tidy("Milo|high|uniform|black|"))
    assert not logic.names_animal(logic.tidy("None|high|na|na|"))
    assert not logic.names_animal(logic.tidy("Spider Web|high|na|na|"))
    assert not logic.names_animal(logic.tidy("Squirrel|high|na|na|"))
    assert not logic.names_animal("")


# --- alerts -------------------------------------------------------------------------

GARAGE_CAM = logic.CameraRule("garage", "Garage", house=True, unsure_alerts=True)
YARD_CAM = logic.CameraRule("yard", "Yard")
PORCH_CAM = logic.CameraRule("front_porch", "Front Porch")
SPECIES = {
    s.key: s
    for s in (
        logic.SpeciesRule("Raccoon", alert=logic.ALERT_ANY),
        logic.SpeciesRule("Opossum", alert=logic.ALERT_CAMERAS, cameras=("garage", "front_porch")),
        logic.SpeciesRule("Fox", alert=logic.ALERT_HOUSE),
        logic.SpeciesRule("Squirrel"),
    )
}


def verdict(animal, confidence="high", candidate="", pattern="na", description=""):
    return logic.Verdict(animal, confidence, pattern, description, candidate, "", "")


def plan(v, cam, missing=(), **kw):
    return logic.plan_alerts(v, cam, RULES, SPECIES, missing, "9:05 PM", **kw)


def test_priority_medium_alerts_everyone_tentatively():
    (a,) = plan(verdict("Luna", "medium", "Luna"), YARD_CAM)
    assert (a.kind, a.urgency, a.audience) == ("priority", "normal", "all")
    assert a.title == "Possible Luna — Yard"
    assert a.message == "AI thinks this might be Luna at Yard, 9:05 PM.\nConfidence: medium"


def test_priority_low_is_silent_unless_missing():
    assert plan(verdict("Luna", "low", "Luna"), GARAGE_CAM) == []
    (a,) = plan(verdict("Luna", "low", "Luna"), GARAGE_CAM, missing={"luna"})
    assert (a.kind, a.urgency, a.audience, a.volume) == ("missing", "critical", "missing", 1.0)


def test_missing_mode_high_luna_critical_plus_normal_for_the_rest():
    missing, normal = plan(verdict("Luna", "high", "Luna"), YARD_CAM, missing={"luna"})
    assert missing.audience == "missing" and missing.urgency == "critical"
    assert normal.audience == "not_missing" and normal.urgency == "normal"


def test_split_vote_candidate_on_another_cat_in_missing_mode():
    alerts = plan(verdict("Oscar", "low", "Luna"), GARAGE_CAM, missing={"luna"})
    assert [a.kind for a in alerts] == ["missing", "animal"]


def test_cat_on_house_camera_uses_camera_urgency():
    cam = logic.CameraRule("garage", "Garage", house=True, critical=True)
    (a,) = plan(verdict("Milo", "medium"), cam)
    assert a.title == "Possible Milo — Garage" and a.urgency == "critical"
    (a,) = plan(verdict("Milo"), GARAGE_CAM)
    assert a.message == "Milo detected at Garage, 9:05 PM.\nConfidence: high"


def test_cat_on_other_camera():
    (a,) = plan(verdict("Milo", "low", pattern="uniform"), YARD_CAM)
    assert a.title == "Cat Sighting — Yard"
    assert a.message == "A uniform-coated cat was seen at Yard, 9:05 PM.\nBest guess: Milo (confidence: low)"


def test_unknown_cat_alert_can_be_turned_off():
    v = verdict("Unknown Cat", "medium", description="ginger tabby")
    (a,) = plan(v, YARD_CAM)
    assert "Looks like: ginger tabby" in a.message
    assert plan(v, YARD_CAM, unknown_alerts=False) == []


def test_wildlife_scopes():
    assert plan(verdict("Raccoon"), YARD_CAM)[0].title == "Raccoon — Yard"
    assert plan(verdict("Opossum"), PORCH_CAM)[0].urgency == "normal"
    assert plan(verdict("Opossum"), YARD_CAM) == []
    assert plan(verdict("Fox"), YARD_CAM) == []
    assert plan(verdict("Fox", "medium"), GARAGE_CAM)[0].title == "Possible Fox — Garage"
    assert plan(verdict("Squirrel"), GARAGE_CAM) == []


def test_wildlife_critical_from_its_switch_or_the_camera():
    racc = {"raccoon": logic.SpeciesRule("Raccoon", alert=logic.ALERT_ANY, critical=True)}
    a = logic.plan_alerts(verdict("Raccoon"), YARD_CAM, RULES, racc, (), "now")[0]
    assert a.urgency == "critical"
    cam = logic.CameraRule("garage", "Garage", house=True, critical=True)
    assert plan(verdict("Opossum"), cam)[0].urgency == "critical"


def test_unsure_motion_only_where_asked():
    (a,) = plan(verdict("Nothing", "low"), GARAGE_CAM)
    assert a.title == "Motion — Garage"
    assert plan(verdict("Nothing", "high"), GARAGE_CAM) == []
    assert plan(verdict("Nothing", "low"), YARD_CAM) == []


def test_unlisted_animal_near_the_house_and_harmless_things():
    assert plan(verdict("Rabbit"), GARAGE_CAM)[0].title == "Rabbit — Garage"
    assert plan(verdict("Rabbit"), YARD_CAM) == []
    assert plan(verdict("Spider Web"), GARAGE_CAM) == []


def test_individual_alerts_can_be_off_and_test_prefix():
    quiet = {**RULES, "milo": logic.AnimalRule("Milo", alert=False)}
    assert logic.plan_alerts(verdict("Milo"), GARAGE_CAM, quiet, SPECIES, (), "now") == []
    (a,) = plan(verdict("Raccoon"), YARD_CAM, test=True)
    assert a.title.startswith("TEST · ")


def test_unknown_animals_of_any_kind():
    assert logic.is_unknown("Unknown Dog") and logic.is_unknown("unknown cat")
    assert not logic.is_unknown("Unknown") and not logic.is_unknown("Milo")
    v = decide("Unknown Dog|medium|uniform|black|big shaggy dog", "Unknown Dog|medium|uniform|black|big shaggy dog")
    assert (v.animal, v.description) == ("Unknown Dog", "big shaggy dog")
    assert logic.counts(v, RULES) and logic.is_cat_for_log(v, RULES)
    (a,) = plan(v, YARD_CAM)
    assert a.title == "Dog Sighting — Yard"
    s = logic.summarize([("Unknown Dog", "Yard"), ("Unknown Cat", "Yard"), ("Unknown Dog", "Yard")], [])
    assert s.lines == ["1 Unknown Cat sighting", "2 Unknown Dog sightings"]


def test_prompt_names_the_unknown_kinds():
    text = logic.build_prompt(logic.DEFAULT_INSTRUCTIONS, location="Bins", animals=[], unknown_kinds=["dog"])
    assert "use 'Unknown Dog' ONLY" in text
    assert "{" not in text

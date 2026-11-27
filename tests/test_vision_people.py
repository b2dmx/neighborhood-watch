"""People and vehicles in the vision service's training and detector, with made-up photos and
stand-ins for the models."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types

import numpy as np
from PIL import Image

VISION = Path(__file__).parent.parent / "vision"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"nw_{name}", VISION / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up
    spec.loader.exec_module(module)
    return module


train = _load("train")
if "openvino" not in sys.modules:  # the detector only needs it to run a real model
    sys.modules["openvino"] = types.SimpleNamespace(Core=object)
detector = _load("detector")


def _sample(tmp_path: Path, sid: str, animal: bool, **meta):
    jpg = tmp_path / f"{sid}.jpg"
    Image.new("RGB", (1000, 1000), "gray").save(jpg)
    return train.Sample(sid, jpg, meta, "2026-01-01T00:00:00", "2026-01-01", animal, "confirmed",
                        name="Pepper" if animal else "")


class _Tensor(list):
    def tolist(self):
        return list(self)


def _teacher(monkeypatch, found):
    """A stand-in for the COCO model: found = {photo id: [(box, coco class)]}."""

    class Yolo:
        def __init__(self, _weights):
            pass

        def predict(self, path, **_kw):
            rows = found.get(Path(path).stem, [])
            boxes = types.SimpleNamespace(xyxy=_Tensor([list(b) for b, _c in rows]),
                                          cls=_Tensor([c for _b, c in rows]))
            return [types.SimpleNamespace(boxes=boxes)]

    monkeypatch.setitem(sys.modules, "ultralytics", types.SimpleNamespace(YOLO=Yolo))


def test_people_and_vehicles_are_boxed_and_a_drawn_box_comes_first(tmp_path, monkeypatch):
    person = _sample(tmp_path, "a", False, empty_reason="Person", user_box=[0.1, 0.1, 0.3, 0.6])
    street = _sample(tmp_path, "b", False)
    cat = _sample(tmp_path, "c", True)
    cat.boxes, cat.main = [[100, 100, 200, 200, 0.9]], 0
    _teacher(monkeypatch, {
        "a": [([102, 102, 298, 590], 0), ([500, 500, 700, 600], 2)],  # the same person, and a car
        "b": [([10, 10, 300, 200], 7)],  # a truck is a vehicle
        "c": [([101, 101, 199, 199], 0), ([600, 100, 700, 400], 0)],  # "a person" over the cat
    })
    train.other_boxes([person, street, cat], tmp_path, 1280, "cpu", lambda **_k: None)
    assert [b[4] for b in person.others] == ["person", "vehicle"]
    assert [round(v) for v in person.others[0][:4]] == [100, 100, 300, 600]
    assert street.others == [[10, 10, 300, 200, "vehicle"]]
    assert cat.others == [[600, 100, 700, 400, "person"]]
    # Cached: the next round doesn't ask the teacher again.
    _teacher(monkeypatch, {})
    train.other_boxes([street], tmp_path, 1280, "cpu", lambda **_k: None)
    assert street.others == [[10, 10, 300, 200, "vehicle"]]


def test_a_kind_becomes_a_class_only_with_enough_photos(tmp_path):
    many = [_sample(tmp_path, f"p{i}", False) for i in range(train.MIN_PER_NAME)]
    for s in many:
        s.others = [[0, 0, 100, 100, "person"]]
    few = _sample(tmp_path, "v", False)
    few.others = [[0, 0, 100, 100, "vehicle"]]
    assert train.pick_others([*many, few]) == ["person"]


def test_labels_hold_people_only_when_they_are_a_class(tmp_path):
    cat = _sample(tmp_path, "c", True)
    cat.boxes, cat.main = [[100, 100, 200, 200, 0.9]], 0
    cat.others = [[500, 500, 700, 900, "person"], [0, 0, 100, 100, "vehicle"]]
    names = ["Pepper", "animal", "person"]
    lines = train.label_lines(cat, names, (0, 0, 1000, 1000))
    assert [line.split()[0] for line in lines] == ["0", "2"]
    assert lines[1] == "2 0.600000 0.700000 0.200000 0.400000"
    assert [line.split()[0] for line in train.label_lines(cat, names[:2], (0, 0, 1000, 1000))] == ["0"]


def test_an_answered_person_nobody_could_box_is_not_taught_as_nothing(tmp_path):
    s = _sample(tmp_path, "a", False, empty_reason="Person")
    assert train.unfound(s, ["animal", "person"])
    assert not train.unfound(s, ["animal"])
    s.others = [[0, 0, 10, 10, "person"]]
    assert not train.unfound(s, ["animal", "person"])


def test_the_dataset_labels_a_photo_with_no_animal_but_a_person(tmp_path):
    person = _sample(tmp_path, "a", False, empty_reason="Person")
    person.others = [[100, 100, 300, 600, "person"]]
    empty = _sample(tmp_path, "b", False)
    cat = _sample(tmp_path, "c", True)
    cat.boxes, cat.main = [[100, 100, 200, 200, 0.9]], 0
    _yaml, _n = train.build_yolo_dataset([person, empty, cat], tmp_path / "data", ["Pepper", "animal", "person"])
    labels = {p.stem: p.read_text() for p in (tmp_path / "data" / "labels").rglob("*.txt")}
    assert labels["a"].startswith("2 ")
    assert labels["b"] == ""


def test_people_found_are_counted_apart_from_animals(tmp_path):
    with_person = _sample(tmp_path, "a", False, empty_reason="Person")
    nothing = _sample(tmp_path, "b", False)
    found = [[{"label": "person", "confidence": 0.8}], [{"label": "person", "confidence": 0.5}]]
    score = train.others_score(found, [with_person, nothing], ["person"], 0.2)
    assert score == {"person": {"photos": 1, "found": 1, "extra": 1}}
    assert train.animals_only(found) == [[], []]


def _named_detector(labels, rows):
    """A Detector whose model "sees" rows: (box, one score per class)."""
    d = detector.Detector.__new__(detector.Detector)
    d.labels, d.named, d.min_conf = dict(enumerate(labels)), True, 0.1
    d.tiles = lambda w, h: []

    def candidates(img, dx=0, dy=0):
        every = np.array([r[1] for r in rows], dtype=float)
        cls, conf = d.classify(every)
        return np.array([r[0] for r in rows], dtype=float), conf, cls, every

    d.candidates = candidates
    return d


def test_a_person_is_not_an_animal_and_an_animal_keeps_its_name():
    d = _named_detector(["Pepper", "animal", "person", "vehicle"], [
        ([0, 0, 100, 300], [0.15, 0.1, 0.9, 0.0]),  # a person (a little animal-like)
        ([400, 400, 500, 500], [0.4, 0.3, 0.05, 0.0]),  # a cat
        ([600, 0, 900, 200], [0.0, 0.0, 0.0, 0.7]),  # a car
    ])
    found = d.detect(Image.new("RGB", (1000, 1000)))
    by = {f["label"]: f for f in found}
    assert sorted(f["label"] for f in found) == ["animal", "person", "vehicle"]
    assert by["person"]["confidence"] == 0.9 and "name" not in by["person"]
    assert by["animal"]["name"] == "Pepper" and by["animal"]["confidence"] == 0.7
    assert set(by["animal"]["names"]) == {"Pepper", "animal"}


def test_a_model_without_those_classes_works_as_before():
    d = _named_detector(["Pepper", "animal"], [([0, 0, 100, 100], [0.4, 0.3])])
    [found] = d.detect(Image.new("RGB", (1000, 1000)))
    assert (found["label"], found["name"], found["confidence"], found["name_confidence"]) == ("animal", "Pepper", 0.7, 0.4)


def test_the_clear_line_stops_above_any_photo_that_had_an_animal(tmp_path):
    def found(score, animal=0.0):
        rows = [{"label": "person", "confidence": score}]
        return rows + ([{"label": "animal", "confidence": animal}] if animal else [])

    empties = [_sample(tmp_path, f"e{i}", False) for i in range(train.SURE_MIN_COUNT)]
    scores = [found(0.8 + i / 1000) for i in range(len(empties))]
    line = train.clear_lines(scores, empties, ["person"], 0.2)["person"]
    assert 0.8 <= line <= 0.801
    # A cat the model took for a person, scored among them: too few photos are left above it.
    cat = _sample(tmp_path, "c", True)
    assert train.clear_lines([*scores, found(0.807)], [*empties, cat], ["person"], 0.2) == {}
    # Scored below them, or with the animal found too, it doesn't matter.
    assert train.clear_lines([*scores, found(0.6)], [*empties, cat], ["person"], 0.2)["person"] == line
    assert train.clear_lines([*scores, found(0.99, animal=0.5)], [*empties, cat], ["person"], 0.2)["person"] == line
    assert train.clear_lines(scores[:-1], empties[:-1], ["person"], 0.2) == {}


def test_a_nothing_the_model_decided_itself_is_not_proof_of_an_empty_photo(tmp_path):
    s = _sample(tmp_path, "a", False, ai_confidence="high")
    s.trust = "ai"
    assert s.empty_ok
    s.meta["named_by"] = "local"
    assert not s.empty_ok

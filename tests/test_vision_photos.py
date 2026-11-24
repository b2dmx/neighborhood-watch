"""The Photos tool of the vision service (vision/photos.py), tested without the service."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from PIL import Image
import pytest

from custom_components.neighborhood_watch.const import EMPTY_REASONS

_spec = importlib.util.spec_from_file_location("nw_photos", Path(__file__).parent.parent / "vision" / "photos.py")
photos = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(photos)


def _photo(root: Path, day: str, sid: str, **meta) -> None:
    (root / day).mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (800, 600), "gray").save(root / day / f"{sid}.jpg")
    (root / day / f"{sid}.json").write_text(json.dumps({"id": sid, **meta}))


def test_the_reasons_match_home_assistants():
    assert photos.EMPTY_REASONS == EMPTY_REASONS


def test_names_count_answered_photos_and_boxes(tmp_path):
    _photo(tmp_path, "d1", "a", label="Boshi", labelled_by="right", time="2026-11-01T00:00:00", user_box=[0.1, 0.1, 0.5, 0.5])
    _photo(tmp_path, "d1", "b", label="Boshi", labelled_by="corrected", time="2026-11-02T00:00:00")
    _photo(tmp_path, "d1", "c", label="Nothing", labelled_by="right", empty_reason="Person", time="2026-11-03T00:00:00")
    _photo(tmp_path, "d1", "d", ai_animal="Raccoon", time="2026-11-04T00:00:00")  # nobody answered
    lib = photos.Photos(tmp_path, tmp_path / "t")
    assert lib.names() == [{"name": "Boshi", "answered": 2, "boxed": 1}]
    assert [r["id"] for r in lib.page("boshi")["items"]] == ["b", "a"]  # newest first
    assert [r["id"] for r in lib.page(need_box=True)["items"]] == ["b"]
    assert [r["id"] for r in lib.page("(unanswered)")["items"]] == ["d"]


def test_only_plain_names_reach_the_disk(tmp_path):
    _photo(tmp_path, "d1", "a", label="Boshi", labelled_by="right")
    lib = photos.Photos(tmp_path, tmp_path / "t")
    assert lib.jpg("d1", "a") and lib.thumb("d1", "a").is_file()
    for bad in ("..", "a/b", "", "a\\b"):
        assert lib.jpg("d1", bad) is None and lib.jpg(bad, "a") is None


def test_the_inbox_keeps_the_newest_and_only_forgets_what_was_applied(tmp_path):
    inbox = photos.Inbox(tmp_path / "inbox.json")
    first = inbox.add("d1", "a", box=[0.6, 0.7, 0.2, 0.3])
    assert first["box"] == [0.2, 0.3, 0.6, 0.7]  # corners put in order
    named = inbox.add("d1", "a", answer="Boshi")
    assert named["box"] == [0.2, 0.3, 0.6, 0.7] and named["answer"] == "Boshi"  # a name adds to the box
    assert inbox.ack([("a", first["time"] - 1)]) == 0  # an older version was applied: the item stays
    assert len(inbox.pending()) == 1
    assert inbox.ack([("a", named["time"])]) == 1 and inbox.pending() == []
    for bad in (dict(box=[0.5, 0.5, 0.5, 0.5]), dict(), dict(box=[1, 2])):
        with pytest.raises(ValueError):
            inbox.add("d1", "a", **bad)
    with pytest.raises(ValueError):
        inbox.add("..", "a", answer="x")

"""The Photos tool of the vision page: browse the saved photos, draw boxes, give names.

The saved photos are mounted read-only here (on purpose), so nothing is written to them from
this side. A box or a name is kept in an inbox (a file in the service's own work folder);
Home Assistant fetches it on its next check, applies it exactly like an answer given in its
own card (the sighting, the animal's tracking and the photo's record all follow) and then
says so, which takes the item out of the inbox. This module has no dependencies beyond the
standard library and Pillow, so it can be tested on its own.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import threading
import time

from PIL import Image

# What a photo with no animal can show (the same list as the Home Assistant side, const.py
# EMPTY_REASONS: a test keeps the two equal).
EMPTY_REASONS = ("Person", "Car", "Leaves or wind", "Bug or spider", "Light or shadow")
NOT_ANIMAL = {"nothing", "none", "no answer", "unsure"} | {r.lower() for r in EMPTY_REASONS}
CONFIRMED = {"right", "corrected", "vision"}  # who labelled it: a person
_SAFE = re.compile(r"^[A-Za-z0-9._-]+$")
THUMB = 360
PAGE = 48


def safe(part: str) -> bool:
    """A folder or photo name from the request: plain characters only (no paths)."""
    return bool(part) and bool(_SAFE.match(part)) and part not in (".", "..")


def read_record(path: Path) -> dict | None:
    """A photo's record; one with leftovers after it (an interrupted rewrite) is read up to the
    end of its first object."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        meta = json.loads(text)
    except ValueError:
        try:
            meta, _ = json.JSONDecoder().raw_decode(text.lstrip())
        except ValueError:
            return None
    return meta if isinstance(meta, dict) else None


def _box(value) -> list[float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            return [round(float(v), 4) for v in value]
        except (TypeError, ValueError):
            return None
    return None


class Photos:
    """The saved photos as a list (read again at most every few seconds)."""

    def __init__(self, root: Path, thumbs: Path) -> None:
        self.root = root
        self.thumbs = thumbs
        self._rows: list[dict] = []
        self._at = 0.0
        self._lock = threading.Lock()

    def rows(self, ttl: float = 20.0) -> list[dict]:
        with self._lock:
            if time.monotonic() - self._at < ttl and self._rows:
                return self._rows
            out = []
            if self.root.is_dir():
                for js in self.root.glob("*/*.json"):
                    if not js.with_suffix(".jpg").exists():
                        continue
                    meta = read_record(js)
                    if meta is None:
                        continue
                    by = str(meta.get("labelled_by") or "").lower()
                    label = meta.get("label")
                    name = label if label is not None else meta.get("ai_animal")
                    answered = by in CONFIRMED and bool(label)
                    out.append(
                        {
                            "day": js.parent.name,
                            "id": js.stem,
                            "time": str(meta.get("time") or ""),
                            "camera": str(meta.get("camera") or ""),
                            "name": str(name or ""),
                            "ai": str(meta.get("ai_animal") or ""),
                            "answered": answered,
                            "animal": bool(name) and str(name).strip().lower() not in NOT_ANIMAL
                            and not str(name).startswith("not:"),
                            "box": _box(meta.get("user_box")),
                            "ai_box": _box(meta.get("ai_box")),
                            "reason": meta.get("empty_reason"),
                        }
                    )
            out.sort(key=lambda r: r["time"], reverse=True)
            self._rows, self._at = out, time.monotonic()
            return out

    def forget(self) -> None:
        self._at = 0.0

    def names(self) -> list[dict]:
        """Every name that has answered photos: how many, and how many of those have a box
        drawn by a person (what the detector trains on first)."""
        count: dict[str, list[int]] = {}
        for r in self.rows():
            if r["answered"] and r["animal"]:
                c = count.setdefault(r["name"], [0, 0])
                c[0] += 1
                c[1] += bool(r["box"])
        return sorted(
            ({"name": n, "answered": c[0], "boxed": c[1]} for n, c in count.items()),
            key=lambda x: (x["answered"] - x["boxed"] < 1, -x["answered"]),
        )

    def page(self, name: str = "", need_box: bool = False, offset: int = 0, limit: int = PAGE) -> dict:
        wanted = name.strip().lower()
        rows = [
            r for r in self.rows()
            if (not wanted or (wanted == "(unanswered)" and not r["answered"]) or r["name"].lower() == wanted)
            and (not need_box or (r["answered"] and r["animal"] and not r["box"]))
        ]
        return {"total": len(rows), "items": rows[offset: offset + limit]}

    def jpg(self, day: str, sid: str) -> Path | None:
        if not (safe(day) and safe(sid)):
            return None
        path = self.root / day / f"{sid}.jpg"
        return path if path.is_file() else None

    def thumb(self, day: str, sid: str) -> Path | None:
        """A small copy of a photo (made once, kept in the work folder)."""
        src = self.jpg(day, sid)
        if src is None:
            return None
        dest = self.thumbs / day / f"{sid}.jpg"
        if dest.is_file() and dest.stat().st_mtime >= src.stat().st_mtime:
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((THUMB, THUMB))
            tmp = dest.with_suffix(".tmp")
            im.save(tmp, "JPEG", quality=80)
        tmp.replace(dest)
        return dest


class Inbox:
    """What was boxed or named here and Home Assistant hasn't applied yet."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _store(self, data: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, day: str, sid: str, box=None, clear_box: bool = False, answer: str | None = None) -> dict:
        """A box and/or a name for a photo; a later one for the same photo adds to it."""
        if not (safe(day) and safe(sid)):
            raise ValueError("not a photo")
        item_box = None
        if box is not None:
            item_box = _box(box)
            if item_box is None:
                raise ValueError("a box is four numbers")
            x1, y1, x2, y2 = (min(1.0, max(0.0, v)) for v in item_box)
            item_box = [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]
            if item_box[2] - item_box[0] < 0.005 or item_box[3] - item_box[1] < 0.005:
                raise ValueError("that box has no area")
        answer = (answer or "").strip() or None
        if not (item_box or clear_box or answer):
            raise ValueError("nothing to save")
        with self._lock:
            data = self._load()
            item = data.get(sid) or {"id": sid, "day": day}
            item["day"] = day
            if item_box:
                item["box"], item["clear_box"] = item_box, False
            elif clear_box:
                item["box"], item["clear_box"] = None, True
            if answer:
                item["answer"] = answer
            item["time"] = time.time()
            data[sid] = item
            self._store(data)
            return item

    def pending(self) -> list[dict]:
        with self._lock:
            return sorted(self._load().values(), key=lambda i: i.get("time", 0))

    def ack(self, applied: list[tuple[str, float]]) -> int:
        """Home Assistant applied these (id, time of the version it applied). An item that was
        changed since stays, so the newer box or name is not lost."""
        with self._lock:
            data = self._load()
            gone = 0
            for sid, at in applied:
                item = data.get(sid)
                if item is not None and float(item.get("time", 0)) <= float(at):
                    del data[sid]
                    gone += 1
            if gone:
                self._store(data)
            return gone

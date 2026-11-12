"""Training data: every checked frame (downscaled unless full size is on), with what the AI
said and what a person confirmed or corrected. This is what a custom model is trained on later.

Layout: <root>/<YYYY-MM-DD>/<sighting id>.jpg + .json. Only files; nothing here is needed
for the watch to run, so every function is best-effort and runs in the executor.
"""
from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import shutil
from typing import Any

MAX_SIDE = 1280
FULL_SIDE = 3840  # "full size": the camera's own size, up to 4K


def save_sample(
    root: Path, day: str, sid: str, frame: bytes, meta: dict[str, Any], full: bool = False
) -> None:
    """full: keep the camera's size (the detector then trains on animals as large as it
    sees them live; about 4 times the space)."""
    from PIL import Image  # noqa: PLC0415

    folder = root / day
    folder.mkdir(parents=True, exist_ok=True)
    side = FULL_SIDE if full else MAX_SIDE
    with Image.open(BytesIO(frame)) as original:
        img = original.convert("RGB")
        img.thumbnail((side, side))
        buffer = BytesIO()
        img.save(buffer, "JPEG", quality=88)
    (folder / f"{sid}.jpg").write_bytes(buffer.getvalue())
    _write_record(folder / f"{sid}.json", meta)


def _write_record(path: Path, meta: dict[str, Any]) -> None:
    """Written beside it and moved into place, so a record is never left half rewritten
    (which left a few unreadable ones behind)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    tmp.replace(path)


def _read_record(path: Path) -> dict[str, Any] | None:
    """A photo's record; one with leftovers after it (an old interrupted rewrite) is read up
    to the end of its first object."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        meta = json.loads(text)
    except ValueError:
        try:
            meta, _end = json.JSONDecoder().raw_decode(text.lstrip())
        except ValueError:
            return None
    return meta if isinstance(meta, dict) else None


def set_label(root: Path, day: str, sid: str, label: str | None, how: str) -> bool:
    path = root / day / f"{sid}.json"
    meta = _read_record(path)
    if meta is None:
        return False
    meta["label"] = label
    meta["labelled_by"] = how
    try:
        _write_record(path, meta)
    except OSError:  # the folder is away (a NAS that's off): the answer itself still stands
        return False
    return True


def set_box(root: Path, day: str, sid: str, box: list[float] | None) -> bool:
    """Where a person marked the animal in the photo (0-1: x1, y1, x2, y2), or None to take
    the mark away. Training uses it before any other box."""
    path = root / day / f"{sid}.json"
    meta = _read_record(path)
    if meta is None:
        return False
    if box is None:
        meta.pop("user_box", None)
    else:
        meta["user_box"] = [round(float(v), 4) for v in box]
    try:
        _write_record(path, meta)
    except OSError:
        return False
    return True


def rename_labels(root: Path, names: dict[str, str]) -> int:
    """An animal was renamed: every saved photo answered or named with the old name takes
    the new one, so the detector keeps learning it as one animal."""
    changed = 0
    if not root.exists():
        return 0
    for path in root.rglob("*.json"):
        meta = _read_record(path)
        if meta is None:
            continue
        hit = False
        for key in ("label", "ai_animal"):
            if meta.get(key) in names:
                meta[key] = names[meta[key]]
                hit = True
        if hit:
            try:
                _write_record(path, meta)
                changed += 1
            except OSError:
                continue
    return changed


def read_frame(root: Path, day: str, sid: str) -> bytes | None:
    try:
        return (root / day / f"{sid}.jpg").read_bytes()
    except OSError:
        return None


def size(root: Path) -> int:
    if not root.exists():
        return 0
    return sum(f.stat().st_size for f in root.rglob("*") if f.is_file())


def prune(root: Path, max_bytes: int) -> int:
    """Over the limit: drop the oldest days' UNLABELLED samples first, then whole oldest
    days. Labelled samples are the valuable ones, so they go last."""
    if not root.exists():
        return 0
    removed = 0
    total = size(root)
    days = sorted(p for p in root.iterdir() if p.is_dir())
    for day in days:
        if total <= max_bytes:
            return removed
        for meta_path in sorted(day.glob("*.json")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
            if meta.get("label"):
                continue
            for f in (meta_path, meta_path.with_suffix(".jpg")):
                if f.exists():
                    total -= f.stat().st_size
                    f.unlink()
                    removed += 1
            if total <= max_bytes:
                return removed
    for day in days:
        if total <= max_bytes:
            break
        if day.exists():
            freed = size(day)
            shutil.rmtree(day, ignore_errors=True)
            total -= freed
            removed += 1
    return removed


def move_all(source: Path, target: Path) -> int:
    """Move every day folder's samples from `source` into `target` (merging; a sample
    already in the target is kept). Returns how many photos moved."""
    if not source.is_dir() or source.resolve() == target.resolve():
        return 0
    moved = 0
    for day in sorted(d for d in source.iterdir() if d.is_dir()):
        dest = target / day.name
        dest.mkdir(parents=True, exist_ok=True)
        for file in day.iterdir():
            if not (dest / file.name).exists():
                shutil.copy2(file, dest / file.name)
                if file.suffix == ".jpg":
                    moved += 1
            file.unlink()
        day.rmdir()
    return moved


def read_crop(root: Path, day: str, sid: str) -> bytes | None:
    """The frame cropped to the animal (padded), or the whole frame when nobody says where
    it is. The local detector's box is used when it agrees with where the AI saw the animal
    (or the AI didn't say); otherwise the AI's."""
    from PIL import Image  # noqa: PLC0415

    frame = read_frame(root, day, sid)
    if frame is None:
        return None
    try:
        meta = json.loads((root / day / f"{sid}.json").read_text(encoding="utf-8"))
        det = meta.get("detector") or {}
        boxes = det.get("boxes") or []
        # Where a person marked the animal wins over where the AI said it was.
        ai_box = meta.get("user_box") or meta.get("ai_box")
        if meta.get("user_box"):
            boxes = []
        if not boxes and not ai_box:
            return frame
        size = det.get("size") or [None, None]
        with Image.open(BytesIO(frame)) as img:
            # Detector boxes are in the camera's own resolution; the saved frame may be smaller.
            sx = img.width / (size[0] or img.width)
            sy = img.height / (size[1] or img.height)
            found = [
                (b["box"][0] * sx, b["box"][1] * sy, b["box"][2] * sx, b["box"][3] * sy, b.get("confidence", 0))
                for b in boxes
            ]
            if ai_box:
                ai = (ai_box[0] * img.width, ai_box[1] * img.height, ai_box[2] * img.width, ai_box[3] * img.height)
                overlapping = [b for b in found if _overlap(b, ai) > 0]
                found = overlapping or [(*ai, 0)]
            x1, y1, x2, y2, _ = max(found, key=lambda b: b[4])
            side = max(x2 - x1, y2 - y1) * 1.8
            side = max(side, min(img.width, img.height) * 0.25)
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            w, h = min(side * 4 / 3, img.width), min(side, img.height)
            left = min(max(cx - w / 2, 0), img.width - w)
            top = min(max(cy - h / 2, 0), img.height - h)
            crop = img.convert("RGB").crop((int(left), int(top), int(left + w), int(top + h)))
            buffer = BytesIO()
            crop.save(buffer, "JPEG", quality=88)
    except Exception:  # noqa: BLE001 - no metadata, an odd box or a bad image: the whole frame
        return frame
    return buffer.getvalue()


def _overlap(a, b) -> float:
    """The area two boxes (x1, y1, x2, y2, ...) share."""
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))

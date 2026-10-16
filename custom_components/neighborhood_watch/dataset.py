"""Training data: every checked frame, downscaled, with what the AI said and what a person
confirmed or corrected. This is what a custom model is trained on later.

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


def save_sample(root: Path, day: str, sid: str, frame: bytes, meta: dict[str, Any]) -> None:
    from PIL import Image  # noqa: PLC0415

    folder = root / day
    folder.mkdir(parents=True, exist_ok=True)
    with Image.open(BytesIO(frame)) as original:
        img = original.convert("RGB")
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        buffer = BytesIO()
        img.save(buffer, "JPEG", quality=88)
    (folder / f"{sid}.jpg").write_bytes(buffer.getvalue())
    (folder / f"{sid}.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")


def set_label(root: Path, day: str, sid: str, label: str | None, how: str) -> bool:
    path = root / day / f"{sid}.json"
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    meta["label"] = label
    meta["labelled_by"] = how
    path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return True


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
    """The frame cropped to the animal the local detector found (padded), or the whole
    frame when there's no box."""
    from PIL import Image  # noqa: PLC0415

    frame = read_frame(root, day, sid)
    if frame is None:
        return None
    try:
        meta = json.loads((root / day / f"{sid}.json").read_text(encoding="utf-8"))
        det = meta.get("detector") or {}
        boxes = det.get("boxes") or []
        size = det.get("size") or [None, None]
    except (OSError, ValueError):
        return frame
    if not boxes:
        return frame
    box = max(boxes, key=lambda b: b.get("confidence", 0))["box"]
    with Image.open(BytesIO(frame)) as img:
        # Boxes are in the camera's own resolution; the saved frame may be smaller.
        sx = img.width / (size[0] or img.width)
        sy = img.height / (size[1] or img.height)
        x1, y1, x2, y2 = box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy
        side = max(x2 - x1, y2 - y1) * 1.8
        side = max(side, min(img.width, img.height) * 0.25)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        w, h = min(side * 4 / 3, img.width), min(side, img.height)
        left = min(max(cx - w / 2, 0), img.width - w)
        top = min(max(cy - h / 2, 0), img.height - h)
        crop = img.convert("RGB").crop((int(left), int(top), int(left + w), int(top + h)))
        buffer = BytesIO()
        crop.save(buffer, "JPEG", quality=88)
    return buffer.getvalue()

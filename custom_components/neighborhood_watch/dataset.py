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

"""Bulk scanning: every photo, and a frame every few seconds of every video, through the
detector. Animal moments are grouped into visits (one per photo; for a video, consecutive
frames with an animal), each kept with its best frame.

Runs in a worker thread; the web app polls it. Results stay on disk under WORK/<scan id>.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
import csv
import html
import json
import os
from pathlib import Path
import time
import uuid

from PIL import Image

IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
VIDEOS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".mts", ".webm", ".3gp"}
REPORT_DIR = "neighborhood-watch-report"


@dataclass
class Visit:
    n: int
    file: str  # relative to the scanned folder
    kind: str  # image | video
    start: float  # seconds into the video (0 for photos)
    end: float
    confidence: float
    box: list[int]
    frames: int = 1
    animal: str | None = None  # filled in later by the integration's identification
    label_confidence: str | None = None


@dataclass
class Scan:
    path: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    state: str = "queued"  # queued | running | done | error
    files_total: int = 0
    files_done: int = 0
    current: str = ""
    error: str = ""
    started: float = 0.0
    finished: float = 0.0
    visits: list[Visit] = field(default_factory=list)

    def summary(self) -> dict:
        data = asdict(self)
        data["visits"] = [asdict(v) for v in self.visits]
        return data


def media_files(folder: Path) -> list[Path]:
    out = []
    top = folder.resolve()
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d != REPORT_DIR and not d.startswith("."))
        for name in sorted(files):
            path = Path(root) / name
            # Links pointing outside the scanned folder are skipped.
            if path.suffix.lower() in IMAGES | VIDEOS and top in path.resolve().parents:
                out.append(path)
    return out


def _best_animal(detections: list[dict]) -> dict | None:
    animals = [d for d in detections if d["label"] == "animal"]
    return max(animals, key=lambda d: d["confidence"]) if animals else None


def run_scan(
    scan: Scan,
    folder: Path,
    work: Path,
    detect: Callable[[Image.Image], list[dict]],
    threshold: float = 0.25,
    step: float = 1.0,
    gap: float = 3.0,
) -> None:
    """Fill `scan.visits`; frames go to work/<id>/<n>.jpg."""
    out = work / scan.id
    out.mkdir(parents=True, exist_ok=True)
    scan.state, scan.started = "running", time.time()
    try:
        files = media_files(folder)
        scan.files_total = len(files)
        for path in files:
            rel = str(path.relative_to(folder))
            scan.current = rel
            try:
                if path.suffix.lower() in IMAGES:
                    _scan_image(scan, path, rel, out, detect, threshold)
                else:
                    _scan_video(scan, path, rel, out, detect, threshold, step, gap)
            except Exception as err:  # noqa: BLE001 - one bad file never stops the scan
                scan.error = f"{rel}: {err}"
            scan.files_done += 1
        scan.state = "done"
    except Exception as err:  # noqa: BLE001
        scan.state, scan.error = "error", str(err)
    scan.current = ""
    scan.finished = time.time()
    (out / "scan.json").write_text(json.dumps(scan.summary(), indent=1), encoding="utf-8")


def _keep(scan: Scan, out: Path, img: Image.Image, visit: Visit) -> None:
    img.save(out / f"{visit.n}.jpg", "JPEG", quality=90)
    scan.visits.append(visit)


def _scan_image(scan, path, rel, out, detect, threshold, size: int = 640) -> None:
    # Detect on a small decode (JPEGs decode at a fraction of full size directly); only a
    # photo with an animal is decoded again in full to keep.
    with Image.open(path) as original:
        ow, oh = original.size
        original.draft("RGB", (size * ow // max(ow, oh), size * oh // max(ow, oh)))
        small = original.convert("RGB")
    best = _best_animal(detect(small))
    if best and best["confidence"] >= threshold:
        sx, sy = ow / small.width, oh / small.height
        x1, y1, x2, y2 = best["box"]
        box = [round(x1 * sx), round(y1 * sy), round(x2 * sx), round(y2 * sy)]
        with Image.open(path) as original:
            img = original.convert("RGB")
        _keep(scan, out, img, Visit(len(scan.visits) + 1, rel, "image", 0, 0, best["confidence"], box))


def _scan_video(scan, path, rel, out, detect, threshold, step, gap) -> None:
    import cv2  # noqa: PLC0415 - only needed for videos

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError("can't open video")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    every = max(1, round(fps * step))
    index = 0
    current: dict | None = None  # the visit being built: start, end, best conf/box/frame, frames

    def close() -> None:
        nonlocal current
        if current:
            visit = Visit(
                len(scan.visits) + 1, rel, "video", round(current["start"], 1), round(current["end"], 1),
                current["conf"], current["box"], current["frames"],
            )
            _keep(scan, out, current["img"], visit)
        current = None

    try:
        while True:
            if not cap.grab():
                break
            if index % every == 0:
                ok, frame = cap.retrieve()
                if ok:
                    t = index / fps
                    img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                    best = _best_animal(detect(img))
                    if best and best["confidence"] >= threshold:
                        if current and t - current["end"] > gap:
                            close()
                        if current is None:
                            current = {"start": t, "end": t, "conf": 0.0, "frames": 0}
                        current["end"] = t
                        current["frames"] += 1
                        if best["confidence"] > current["conf"]:
                            current.update(conf=best["confidence"], box=best["box"], img=img)
                    elif current and t - current["end"] > gap:
                        close()
            index += 1
        close()
    finally:
        cap.release()


def write_report(scan: Scan, folder: Path, work: Path) -> Path:
    """An HTML page and a CSV in <folder>/neighborhood-watch-report, with each visit's frame."""
    target = folder / REPORT_DIR
    target.mkdir(exist_ok=True)
    rows = []
    for v in scan.visits:
        src = work / scan.id / f"{v.n}.jpg"
        if src.exists():
            (target / f"{v.n}.jpg").write_bytes(src.read_bytes())
        when = f"{v.start:.0f}s-{v.end:.0f}s" if v.kind == "video" else ""
        rows.append((v.n, v.file, when, v.animal or "animal", v.label_confidence or "", f"{v.confidence:.2f}"))
    with (target / "report.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["visit", "file", "time in video", "animal", "confidence", "detector score"])
        writer.writerows(rows)
    cards = "\n".join(
        f'<figure><img src="{n}.jpg" loading="lazy"><figcaption><b>{html.escape(animal)}</b> '
        f'{html.escape(conf)}<br>{html.escape(file)} {html.escape(when)}</figcaption></figure>'
        for n, file, when, animal, conf, _ in rows
    )
    counts: dict[str, int] = {}
    for *_, animal, _c, _s in rows:
        counts[animal] = counts.get(animal, 0) + 1
    tally = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])) or "no animals"
    (target / "index.html").write_text(
        f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Neighborhood Watch scan</title>
<style>body{{font-family:system-ui;margin:16px;background:#111;color:#eee}}
main{{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}}
figure{{margin:0;background:#1d1d1d;border-radius:12px;overflow:hidden}}img{{width:100%;display:block}}
figcaption{{padding:8px 10px;font-size:14px}}</style>
<h1>Neighborhood Watch scan</h1><p>{html.escape(scan.path)} · {scan.files_total} files · {len(rows)} visits · {html.escape(tally)}</p>
<main>{cards}</main>""",
        encoding="utf-8",
    )
    return target

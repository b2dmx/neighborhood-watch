"""Local check of bulk scanning: builds a short video from sample frames, then scans a folder
with photos and that video through the real detector.

Run: model/.venv/Scripts/python vision/test_scan_local.py <model dir> <sample frames dir>
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import tempfile

model_dir, samples = sys.argv[1], Path(sys.argv[2])
os.environ["NW_MODEL"] = model_dir
os.environ["NW_DEVICE"] = "CPU"
sys.path.insert(0, str(Path(__file__).parent))

import cv2  # noqa: E402
from PIL import Image  # noqa: E402

import app  # noqa: E402
import scan  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    folder = Path(tmp) / "trailcam"
    (folder / "clips").mkdir(parents=True)
    for name in ("old_yard.jpg", "old_driveway.jpg", "new_yard.jpg"):
        shutil.copy(samples / name, folder / name)
    # A 12-second clip at 5 fps: empty yard, the cat for 4 s, empty again.
    empty = cv2.cvtColor(__import__("numpy").asarray(Image.open(samples / "new_yard.jpg").convert("RGB").resize((640, 360))), cv2.COLOR_RGB2BGR)
    cat = cv2.cvtColor(__import__("numpy").asarray(Image.open(samples / "old_yard.jpg").convert("RGB").resize((640, 360))), cv2.COLOR_RGB2BGR)
    writer = cv2.VideoWriter(str(folder / "clips" / "night.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), 5, (640, 360))
    for second in range(12):
        for _ in range(5):
            writer.write(cat if 4 <= second < 8 else empty)
    writer.release()

    job = scan.Scan(path="trailcam")
    scan.run_scan(job, folder, Path(tmp) / "work", app.detect)
    print("state", job.state, job.files_done, "/", job.files_total, job.error or "")
    for v in job.visits:
        print(f"  visit {v.n}: {v.file} {v.kind} {v.start}-{v.end}s conf {v.confidence:.2f} frames {v.frames}")
    job.visits[0].animal = "Luna"
    report = scan.write_report(job, folder, Path(tmp) / "work")
    print("report files:", sorted(p.name for p in report.iterdir()))
    print((report / "report.csv").read_text())

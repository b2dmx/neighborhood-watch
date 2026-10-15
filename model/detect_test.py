"""Quick check: run MegaDetector v6 on a folder of frames and print what it finds.

Run: model/.venv/Scripts/python model/detect_test.py model/data/sample
"""
from __future__ import annotations

from pathlib import Path
import sys
import time

from ultralytics import YOLO

HERE = Path(__file__).parent
CLASSES = {0: "animal", 1: "person", 2: "vehicle"}


def main(folder: str) -> None:
    model = YOLO(str(HERE / "weights" / "MDV6-yolov9-c.pt"))
    images = sorted(p for p in Path(folder).glob("*.jpg") if p.stat().st_size > 1000)
    model.predict(str(images[0]), verbose=False, device=0)  # warm-up
    for path in images:
        start = time.perf_counter()
        result = model.predict(str(path), conf=0.15, verbose=False, device=0)[0]
        ms = (time.perf_counter() - start) * 1000
        found = [
            f"{CLASSES.get(int(b.cls), b.cls)} {float(b.conf):.2f}"
            for b in result.boxes
        ]
        h, w = result.orig_shape
        print(f"{path.name:22} {w}x{h}  {ms:5.0f} ms  {', '.join(found) or 'nothing'}")


if __name__ == "__main__":
    main(sys.argv[1])

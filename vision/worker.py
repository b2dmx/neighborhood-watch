"""The detector in its own process, so the service holds no GPU, driver or model memory while
idle: app.py starts it on the first request and stops it after NW_IDLE_UNLOAD seconds without
one (OpenVINO and the GPU driver stay resident for as long as a process has used them).

Protocol on stdin/stdout: pickled (op, payload) requests, each answered by a pickled reply.
    ("info", None)                     -> {"name", "dir", "size", "labels", "device", "available"}
    ("detect", (width, height, rgb))   -> [{"label", "confidence", "box"}, ...]
A failed request answers {"error": "<message>"}. The worker exits when stdin closes.
"""
from __future__ import annotations

import os
from pathlib import Path
import pickle
import sys

from PIL import Image

import detector as det

MODEL_DIR = Path(os.environ.get("NW_MODEL", "/models/megadetector"))
WORK = Path(os.environ.get("NW_WORK", "/work"))


def choose_model(core) -> det.Detector:
    """The trained model named in WORK/models/current.txt if it loads, else MegaDetector."""
    wanted = det.production_dir(WORK, MODEL_DIR)
    if wanted != MODEL_DIR:
        try:
            model = det.env_detector(wanted, core)
            model.load()
            return model
        except Exception as err:  # noqa: BLE001 - a broken export must never take the service down
            print(f"Trained model {wanted} failed to load ({err}); using MegaDetector", file=sys.stderr, flush=True)
    model = det.env_detector(MODEL_DIR, core)
    model.load()
    return model


def main() -> None:
    # Replies go to the real stdout; anything printed by libraries goes to stderr (the log).
    reply_fd = os.dup(1)
    os.dup2(2, 1)
    out = os.fdopen(reply_fd, "wb")
    inp = sys.stdin.buffer
    core = det.make_core(WORK)
    model = choose_model(core)
    print(f"Detector process up: {model.name} ({model.size} px) on {model.device}", file=sys.stderr, flush=True)
    while True:
        try:
            op, payload = pickle.load(inp)
        except EOFError:
            return
        try:
            if op == "info":
                reply = {
                    "name": model.name,
                    "dir": str(model.dir),
                    "size": model.size,
                    "labels": sorted(set(model.labels.values())),
                    "device": model.device,
                    "available": core.available_devices,
                }
            elif op == "detect":
                w, h, rgb = payload
                reply = model.detect(Image.frombytes("RGB", (w, h), rgb))
            else:
                reply = {"error": f"unknown op {op!r}"}
        except Exception as err:  # noqa: BLE001 - report it; the next request may work
            reply = {"error": str(err) or type(err).__name__}
        pickle.dump(reply, out, protocol=pickle.HIGHEST_PROTOCOL)
        out.flush()


if __name__ == "__main__":
    main()

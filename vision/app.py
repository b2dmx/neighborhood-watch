"""Neighborhood Watch vision service: an animal detector on OpenVINO (MegaDetector v6, or
a model trained on the owner's labelled photos by train.py).

POST /detect  (body: a JPEG/PNG)  ->  {"width", "height", "ms", "device",
                                        "detections": [{"label", "confidence", "box": [x1, y1, x2, y2]}]}
GET  /health  ->  {"ok": true, "device": ..., "model": ..., "trained": ...}
POST /scan    {"path": "<folder under NW_SCAN_ROOT>"}  ->  {"id"}      (bulk scan, runs in the background)
GET  /scan/<id>                ->  progress and visits
GET  /scan/<id>/frame/<n>      ->  a visit's best frame (JPEG)
POST /scan/<id>/report {"names": {"<n>": {"animal", "confidence"}}}  ->  writes the report into the folder
POST /train   ->  starts a training round now (409 if one is running)
GET  /train   ->  training status and the last round's results

Boxes are in pixels of the image that was sent. Labels: animal, person, vehicle (a trained
model only has animal). Runs on an Intel GPU/NPU when the container can see /dev/dri,
otherwise on the CPU.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

from aiohttp import web
from PIL import Image, UnidentifiedImageError

import detector as det

MODEL_DIR = Path(os.environ.get("NW_MODEL", "/models/megadetector"))
DECODE_PIXELS = 3_100_000  # decode no bigger than about this: enough detail for the tiles
SCAN_ROOT = Path(os.environ.get("NW_SCAN_ROOT", "/scan"))
WORK = Path(os.environ.get("NW_WORK", "/work"))
DATASET = Path(os.environ.get("NW_DATASET", "/dataset"))
SCANS: dict = {}
KEEP_SCANS = 20  # finished scans (and their frames in WORK) kept; older ones are deleted

# Weekly training (train.py) on the labelled photos in NW_DATASET; NW_TRAIN=0 turns it off.
TRAIN = os.environ.get("NW_TRAIN", "1").strip().lower() not in ("0", "false", "no", "off")
_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_day = os.environ.get("NW_TRAIN_DAY", "sunday").strip().lower()
TRAIN_DAY = int(_day) % 7 if _day.isdigit() else next((i for i, d in enumerate(_DAYS) if d.startswith(_day[:3])), 6)
TRAIN_HOUR = int(os.environ.get("NW_TRAIN_HOUR", "13"))
TRAIN_DIR = WORK / "train"
CURRENT = WORK / "models" / "current.txt"

# Memory: the compiled model is ~250 MB, so it's unloaded after NW_IDLE_UNLOAD seconds
# without a request (0 = keep it) and compiled again on the next one (well under a second).
IDLE_UNLOAD = float(os.environ.get("NW_IDLE_UNLOAD", "600"))
core = det.make_core(WORK)


def _choose_model() -> det.Detector:
    """The trained model named in WORK/models/current.txt if it loads, else MegaDetector."""
    wanted = det.production_dir(WORK, MODEL_DIR)
    if wanted != MODEL_DIR:
        try:
            model = det.env_detector(wanted, core)
            with model.lock:
                model.load()
            return model
        except Exception as err:  # noqa: BLE001 - a broken export must never take the service down
            print(f"Trained model {wanted} failed to load ({err}); using MegaDetector", flush=True)
    model = det.env_detector(MODEL_DIR, core)
    with model.lock:
        model.load()  # fail fast at start if the model can't load
    return model


_det = _choose_model()
print(f"Serving {_det.name} ({_det.size} px, labels {_det.labels}) on {_det.device}", flush=True)


def _current_stamp() -> tuple:
    try:
        return (CURRENT.stat().st_mtime, CURRENT.read_text(encoding="utf-8").strip())
    except OSError:
        return ()


def _watch_model() -> None:
    """Switch to a newly promoted model (current.txt changed) without a restart."""
    global _det  # noqa: PLW0603
    seen = _current_stamp()
    while True:
        time.sleep(30)
        stamp = _current_stamp()
        if stamp == seen:
            continue
        seen = stamp
        try:
            new = _choose_model()
        except Exception as err:  # noqa: BLE001
            print(f"Model switch failed: {err}", flush=True)
            continue
        if new.dir != _det.dir:
            print(f"Now serving {new.name} ({new.size} px) on {new.device}", flush=True)
            _det = new


def _unload_when_idle() -> None:
    while IDLE_UNLOAD > 0:
        time.sleep(30)
        if _det.unload_if_idle(IDLE_UNLOAD):
            import gc  # noqa: PLC0415

            gc.collect()
            try:  # hand the freed memory back to the system (glibc)
                import ctypes  # noqa: PLC0415

                ctypes.CDLL("libc.so.6").malloc_trim(0)
            except OSError:
                pass


# ---- training -------------------------------------------------------------------------
_train_proc: subprocess.Popen | None = None
_train_lock = threading.Lock()


def training_running() -> bool:
    return _train_proc is not None and _train_proc.poll() is None


def start_training(*args: str) -> bool:
    """Run train.py in its own low-priority process (never two at once). False if busy."""
    global _train_proc  # noqa: PLW0603
    with _train_lock:
        if training_running():
            return False
        TRAIN_DIR.mkdir(parents=True, exist_ok=True)
        log = TRAIN_DIR / "train.log"
        if log.exists() and log.stat().st_size > 5_000_000:
            log.replace(TRAIN_DIR / "train.log.1")
        env = {**os.environ, "NW_DATASET": str(DATASET), "NW_WORK": str(WORK), "NW_MODEL": str(MODEL_DIR)}
        with log.open("a", encoding="utf-8") as fh:
            fh.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} train.py {' '.join(args)}\n")
            fh.flush()
            _train_proc = subprocess.Popen(  # noqa: S603
                [sys.executable, str(Path(__file__).with_name("train.py")), *args],
                cwd=WORK,
                env=env,
                stdout=fh,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                preexec_fn=(lambda: os.nice(10)) if hasattr(os, "nice") else None,  # noqa: PLW1509
            )
        print(f"Training started (pid {_train_proc.pid}); log in {log}", flush=True)
        return True


def _schedule_training() -> None:
    """Once on NW_TRAIN_DAY from NW_TRAIN_HOUR (local time; set TZ): train.py --if-needed,
    which only trains when enough new confirmed photos arrived since the last round."""
    done_on = None
    while TRAIN:
        time.sleep(60)
        now = datetime.now()
        if now.weekday() == TRAIN_DAY and now.hour >= TRAIN_HOUR and done_on != now.date() and DATASET.is_dir():
            if start_training("--if-needed"):
                done_on = now.date()


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _restart_on_new_code() -> None:
    """The code can live in a folder on the host (./app) so updates are a file copy: when a
    .py file changes, exit between requests and let Docker's restart policy start the new
    code. A bulk scan or a training round in progress is never cut short."""
    here = Path(__file__).parent

    def stamp() -> dict[str, float]:
        return {f.name: f.stat().st_mtime for f in here.glob("*.py")}

    started = stamp()
    while True:
        time.sleep(60)
        if (
            stamp() != started
            and not any(s.state in ("queued", "running") for s in SCANS.values())
            and not training_running()
        ):
            print("New code found; restarting", flush=True)
            os._exit(0)


threading.Thread(target=_unload_when_idle, daemon=True).start()
threading.Thread(target=_restart_on_new_code, daemon=True).start()
threading.Thread(target=_watch_model, daemon=True).start()
threading.Thread(target=_schedule_training, daemon=True).start()


def detect(img: Image.Image) -> list[dict]:
    """Detections from the model being served (used by /detect and bulk scans)."""
    return _det.detect(img)


def open_small(data: bytes) -> tuple[Image.Image, int, int]:
    """Decode an image no bigger than the model needs (JPEG decodes at 1/2, 1/4 or 1/8 scale
    directly), and return it with the original width and height."""
    img = Image.open(BytesIO(data))
    ow, oh = img.size
    k = min(1.0, (DECODE_PIXELS / (ow * oh)) ** 0.5)
    img.draft("RGB", (int(ow * k), int(oh * k)))
    return img.convert("RGB"), ow, oh


def scale_boxes(found: list[dict], img: Image.Image, ow: int, oh: int) -> list[dict]:
    """Boxes from a reduced decode, back in the original image's pixels."""
    sx, sy = ow / img.width, oh / img.height
    if sx != 1 or sy != 1:
        for d in found:
            x1, y1, x2, y2 = d["box"]
            d["box"] = [round(x1 * sx), round(y1 * sy), round(x2 * sx), round(y2 * sy)]
    return found


async def health(_request: web.Request) -> web.Response:
    model = _det
    return web.json_response(
        {
            "ok": True,
            "device": model.device,
            "model": model.name,
            "trained": model.dir != MODEL_DIR,
            "size": model.size,
            "labels": sorted(set(model.labels.values())),
            "loaded": model.loaded,
            "training": training_running(),
            "available": core.available_devices,  # what OpenVINO can use here (CPU, GPU, NPU)
            "dri": sorted(p.name for p in Path("/dev/dri").glob("*")) if Path("/dev/dri").exists() else [],
        }
    )


async def train_start(_request: web.Request) -> web.Response:
    if not DATASET.is_dir():
        return web.json_response({"error": f"no dataset folder at {DATASET}"}, status=400)
    if not await asyncio.get_running_loop().run_in_executor(None, start_training):
        return web.json_response({"error": "a training round is already running"}, status=409)
    return web.json_response({"started": True})


async def train_status(_request: web.Request) -> web.Response:
    history = _read_json(TRAIN_DIR / "history.json", [])
    return web.json_response(
        {
            "running": training_running(),
            "enabled": TRAIN,
            "schedule": f"{_DAYS[TRAIN_DAY]} from {TRAIN_HOUR}:00",
            "dataset": DATASET.is_dir(),
            "serving": _det.name,
            "status": _read_json(TRAIN_DIR / "status.json", {}),
            "rounds": len(history),
            "last": history[-1] if history else None,
        }
    )


def _detect_bytes(data: bytes) -> tuple[list[dict], int, int, int]:
    start = time.perf_counter()
    img, ow, oh = open_small(data)
    found = scale_boxes(detect(img), img, ow, oh)
    return found, ow, oh, round((time.perf_counter() - start) * 1000)


async def detect_endpoint(request: web.Request) -> web.Response:
    body = await request.read()
    try:
        # Decoding and inference run off the event loop so /health and scans stay responsive.
        detections, ow, oh, ms = await asyncio.get_running_loop().run_in_executor(None, _detect_bytes, body)
    except (UnidentifiedImageError, OSError) as err:
        return web.json_response({"error": f"not an image: {err}"}, status=400)
    except Exception as err:  # noqa: BLE001 - e.g. the GPU failed
        return web.json_response({"error": f"detection failed: {err}"}, status=500)
    return web.json_response(
        {
            "width": ow,
            "height": oh,
            "ms": ms,
            "device": _det.device,
            "detections": detections,
        }
    )


def _folder(rel: str) -> Path | None:
    root = SCAN_ROOT.resolve()
    target = (root / rel.strip().lstrip("/\\")).resolve()
    if target != root and root not in target.parents:
        return None
    return target if target.is_dir() else None


async def scan_start(request: web.Request) -> web.Response:
    import scan as scanning  # noqa: PLC0415

    # Everything is checked before the scan is registered, so a bad request can't leave a
    # "queued" scan behind (which would also block restarting on new code).
    try:
        body = await request.json()
        threshold, step = float(body.get("threshold", 0.25)), float(body.get("step", 1.0))
        rel = str(body.get("path", ""))
    except (ValueError, TypeError, AttributeError):
        return web.json_response({"error": "bad request"}, status=400)
    if any(s.state in ("queued", "running") for s in SCANS.values()):
        return web.json_response({"error": "a scan is already running"}, status=409)
    folder = _folder(rel)
    if folder is None:
        return web.json_response({"error": "no such folder under the scan root"}, status=404)
    _prune_scans()
    job = scanning.Scan(path=str(folder.relative_to(SCAN_ROOT.resolve())) or ".")
    SCANS[job.id] = job

    def run() -> None:
        try:
            scanning.run_scan(job, folder, WORK, detect, threshold, step)
        except Exception as err:  # noqa: BLE001
            print(f"Scan {job.id} failed: {err}", flush=True)
            if job.state in ("queued", "running"):
                job.state = "error"

    asyncio.get_running_loop().run_in_executor(None, run)
    return web.json_response({"id": job.id})


def _prune_scans() -> None:
    finished = [sid for sid, s in SCANS.items() if s.state not in ("queued", "running")]
    for sid in finished[: max(0, len(finished) - KEEP_SCANS + 1)]:
        SCANS.pop(sid, None)
        shutil.rmtree(WORK / sid, ignore_errors=True)


async def scan_status(request: web.Request) -> web.Response:
    job = SCANS.get(request.match_info["id"])
    if job is None:
        return web.json_response({"error": "unknown scan"}, status=404)
    return web.json_response(job.summary())


async def scan_frame(request: web.Request) -> web.Response:
    sid, n = request.match_info["id"], request.match_info["n"]
    if sid not in SCANS or not n.isdigit():
        return web.Response(status=404)
    path = WORK / sid / f"{n}.jpg"
    if not path.exists():
        return web.Response(status=404)
    return web.Response(body=path.read_bytes(), content_type="image/jpeg")


async def scan_report(request: web.Request) -> web.Response:
    import scan as scanning  # noqa: PLC0415

    job = SCANS.get(request.match_info["id"])
    if job is None or job.state != "done":
        return web.json_response({"error": "scan not finished"}, status=409)
    try:
        body = await request.json()
        names = body.get("names") or {}
        if not isinstance(names, dict):
            raise TypeError("names is not an object")
    except (ValueError, TypeError, AttributeError):
        return web.json_response({"error": "bad request"}, status=400)
    for visit in job.visits:
        named = names.get(str(visit.n))
        named = named if isinstance(named, dict) else {}
        visit.animal = named.get("animal") or visit.animal
        visit.label_confidence = named.get("confidence") or visit.label_confidence
    folder = _folder(job.path)
    if folder is None:
        return web.json_response({"error": "the scanned folder is gone"}, status=404)
    target = await asyncio.get_running_loop().run_in_executor(None, scanning.write_report, job, folder, WORK)
    return web.json_response({"report": str(target.relative_to(SCAN_ROOT.resolve()))})


def make_app() -> web.Application:
    app = web.Application(client_max_size=32 * 1024 * 1024)
    app.add_routes(
        [
            web.get("/health", health),
            web.post("/detect", detect_endpoint),
            web.post("/scan", scan_start),
            web.get("/scan/{id}", scan_status),
            web.get("/scan/{id}/frame/{n}", scan_frame),
            web.post("/scan/{id}/report", scan_report),
            web.post("/train", train_start),
            web.get("/train", train_status),
        ]
    )
    return app


if __name__ == "__main__":
    web.run_app(make_app(), port=int(os.environ.get("NW_PORT", "8765")))

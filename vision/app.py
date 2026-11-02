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
POST /train/stop     ->  stops the round that is running (it is recorded as stopped, never promoted)
POST /train/undo     ->  serves the model from before the last promoted round again
GET  /train/history  ->  every round's results (without the photo id lists)
GET  /settings       ->  the detector and training settings; POST /settings {...} changes some of them
GET  /               ->  a web page with all of the above

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
import pickle
import shutil
import signal
import subprocess
import sys
import threading
import time

from aiohttp import web
from PIL import Image, UnidentifiedImageError


MODEL_DIR = Path(os.environ.get("NW_MODEL", "/models/megadetector"))
DECODE_PIXELS = 3_100_000  # decode no bigger than about this: enough detail for the tiles
SCAN_ROOT = Path(os.environ.get("NW_SCAN_ROOT", "/scan"))
WORK = Path(os.environ.get("NW_WORK", "/work"))
DATASET = Path(os.environ.get("NW_DATASET", "/dataset"))
SCANS: dict = {}
KEEP_SCANS = 20  # finished scans (and their frames in WORK) kept; older ones are deleted

# Weekly training (train.py) on the labelled photos in NW_DATASET. The environment gives the
# defaults (NW_TRAIN=0 turns it off); changes made on the web page or through POST /settings
# are kept in WORK/settings.json and win over them.
_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_DEVICES = ["auto", "cpu"]  # auto: the Intel GPU when it can be used, otherwise the CPU
# Training length: epochs and early-stop patience (normal: the environment's, 20 and 8).
_LENGTHS = {"quick": (10, 4), "normal": None, "thorough": (40, 15)}
_SIZES = [640, 960, 1280]  # training picture size; the export (and so the detector) uses it too


def _day_name(value: str) -> str:
    v = str(value).strip().lower()
    if v.isdigit():
        return _DAYS[int(v) % 7]
    return next((d for d in _DAYS if v and d.startswith(v[:3])), "sunday")


DEFAULTS = {
    "train_enabled": os.environ.get("NW_TRAIN", "1").strip().lower() not in ("0", "false", "no", "off"),
    "train_day": _day_name(os.environ.get("NW_TRAIN_DAY", "sunday")),
    "train_hour": int(os.environ.get("NW_TRAIN_HOUR", "13")) % 24,
    "train_device": "cpu" if os.environ.get("NW_TRAIN_DEVICE", "").strip().lower() == "cpu" else "auto",
    "min_new": int(os.environ.get("NW_TRAIN_MIN_NEW", "100")),
    "min_first": int(os.environ.get("NW_TRAIN_MIN_FIRST", "40")),
    "train_length": "normal",
    "train_imgsz": int(os.environ.get("NW_TRAIN_IMGSZ", "960")),
    "detect_device": "cpu" if os.environ.get("NW_DEVICE", "").strip().upper() == "CPU" else "auto",
}
STOP_GRACE = 20  # seconds a stopped round gets to tidy up before it is killed
TRAIN_DIR = WORK / "train"
CURRENT = WORK / "models" / "current.txt"
SETTINGS_FILE = WORK / "settings.json"
UI_DIR = Path(__file__).parent

# Memory: OpenVINO, the GPU driver and the compiled model (~500 MB together) live in a
# separate detector process (worker.py), started on the first request and stopped after
# NW_IDLE_UNLOAD seconds without one (0 = keep it), so an idle service holds no GPU at all.
# Starting it again takes a second or two (compiled GPU kernels are cached in WORK).
IDLE_UNLOAD = float(os.environ.get("NW_IDLE_UNLOAD", "600"))


class DetectorProcess:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.info: dict = {}  # from the last process started: name, size, labels, device...
        self.last_used = 0.0

    @property
    def loaded(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _ask(self, op: str, payload=None):
        pickle.dump((op, payload), self.proc.stdin, protocol=pickle.HIGHEST_PROTOCOL)
        self.proc.stdin.flush()
        reply = pickle.load(self.proc.stdout)
        if isinstance(reply, dict) and "error" in reply:
            raise RuntimeError(reply["error"])
        return reply

    def _start(self) -> None:
        self.proc = subprocess.Popen(  # noqa: S603
            [sys.executable, str(Path(__file__).with_name("worker.py"))],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env={**os.environ, "NW_DEVICE": detect_device()},
        )
        try:
            self.info = self._ask("info")
        except Exception:
            self._stop()
            raise

    def _stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            proc.stdin.close()
            proc.wait(10)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
            proc.wait()

    def detect(self, img: Image.Image) -> list[dict]:
        payload = (img.width, img.height, img.convert("RGB").tobytes())
        with self.lock:
            self.last_used = time.monotonic()
            for attempt in (1, 2):  # a process that died (or was stopped) is started again once
                if not self.loaded:
                    self._stop()
                    self._start()
                try:
                    return self._ask("detect", payload)
                except (EOFError, OSError, pickle.UnpicklingError):
                    self._stop()
                    if attempt == 2:
                        raise
                finally:
                    self.last_used = time.monotonic()
        return []

    def refresh(self) -> None:
        """Stop the process and read the served model's details again (after a model switch
        or a change of Detects with: the next request starts it on the chosen device)."""
        with self.lock:
            self._stop()
            self._start()
            self._stop()

    def stop_if_idle(self, idle: float) -> bool:
        with self.lock:
            if self.loaded and time.monotonic() - self.last_used > idle:
                self._stop()
                return True
        return False


def detect_device() -> str:
    """NW_DEVICE for the detector process: the CPU when Detects with is CPU, otherwise the
    environment's choice (AUTO: the Intel GPU when there is one)."""
    if settings()["detect_device"] == "cpu":
        return "CPU"
    wanted = os.environ.get("NW_DEVICE", "AUTO").strip().upper()
    return "AUTO" if wanted in ("", "CPU") else wanted


def _current_stamp() -> tuple:
    try:
        return (CURRENT.stat().st_mtime, CURRENT.read_text(encoding="utf-8").strip())
    except OSError:
        return ()


_model_seen: tuple = ()  # current.txt as the detector last loaded it (undo updates it too)


def _watch_model() -> None:
    """Switch to a newly promoted model (current.txt changed) without a restart."""
    global _model_seen  # noqa: PLW0603
    _model_seen = _current_stamp()
    while True:
        time.sleep(30)
        stamp = _current_stamp()
        if stamp == _model_seen:
            continue
        _model_seen = stamp
        try:
            _det.refresh()
        except Exception as err:  # noqa: BLE001
            print(f"Model switch failed: {err}", flush=True)
            continue
        print(f"Now serving {_det.info['name']} ({_det.info['size']} px) on {_det.info['device']}", flush=True)


def _stop_when_idle() -> None:
    while IDLE_UNLOAD > 0:
        time.sleep(30)
        _det.stop_if_idle(IDLE_UNLOAD)


# ---- training -------------------------------------------------------------------------
_train_proc: subprocess.Popen | None = None
_train_lock = threading.Lock()
_train_stopping = False  # Stop training was pressed; cleared once the process is gone


def training_running() -> bool:
    return _train_proc is not None and _train_proc.poll() is None


def _train_env(conf: dict) -> dict:
    """train.py's environment from the settings."""
    env = {
        **os.environ,
        "NW_DATASET": str(DATASET),
        "NW_WORK": str(WORK),
        "NW_MODEL": str(MODEL_DIR),
        "NW_DEVICE": detect_device(),  # it scores the models the way the service runs them
        "NW_TRAIN_DEVICE": "cpu" if conf["train_device"] == "cpu" else "",
        "NW_TRAIN_MIN_NEW": str(conf["min_new"]),
        "NW_TRAIN_MIN_FIRST": str(conf["min_first"]),
        "NW_TRAIN_IMGSZ": str(conf["train_imgsz"]),
    }
    length = _LENGTHS.get(conf["train_length"])
    if length:
        env["NW_TRAIN_EPOCHS"], env["NW_TRAIN_PATIENCE"] = str(length[0]), str(length[1])
    if conf["train_imgsz"] > 960 and "NW_TRAIN_BATCH" not in os.environ:
        env["NW_TRAIN_BATCH"] = "4"  # bigger pictures: half the batch, so memory use stays about the same
    return env


def start_training(*args: str) -> bool:
    """Run train.py in its own low-priority process (never two at once). False if busy."""
    global _train_proc  # noqa: PLW0603
    with _train_lock:
        if training_running() or _train_stopping:
            return False
        TRAIN_DIR.mkdir(parents=True, exist_ok=True)
        log = TRAIN_DIR / "train.log"
        if log.exists() and log.stat().st_size > 5_000_000:
            log.replace(TRAIN_DIR / "train.log.1")
        env = _train_env(settings())
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
                # Its own process group, so stopping it also ends its data-loading processes.
                start_new_session=hasattr(os, "killpg"),
            )
        print(f"Training started (pid {_train_proc.pid}); log in {log}", flush=True)
        return True


def stop_training() -> bool:
    """Ask the running round to stop (train.py records it as stopped and never promotes);
    it is killed if it hasn't ended after STOP_GRACE seconds. False if none is running."""
    global _train_stopping  # noqa: PLW0603
    with _train_lock:
        proc = _train_proc
        if proc is None or proc.poll() is not None:
            return False
        if _train_stopping:
            return True
        _train_stopping = True
        try:
            proc.terminate()  # SIGTERM to train.py only: its data loaders end with it
        except OSError:
            pass
    threading.Thread(target=_finish_stop, args=(proc,), daemon=True).start()
    print(f"Stopping training (pid {proc.pid})", flush=True)
    return True


def _finish_stop(proc: subprocess.Popen) -> None:
    global _train_stopping  # noqa: PLW0603
    try:
        proc.wait(STOP_GRACE)
    except subprocess.TimeoutExpired:
        print("Training didn't stop in time; killing it", flush=True)
    if hasattr(os, "killpg"):  # whatever is left of its process group
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    elif proc.poll() is None:
        proc.kill()
    proc.wait()
    try:
        status = _read_json(TRAIN_DIR / "status.json", {})
        if isinstance(status, dict) and status.get("state") == "running":
            # Killed before train.py could record it: record the stopped round here.
            history = _read_json(TRAIN_DIR / "history.json", [])
            history = history if isinstance(history, list) else []
            started = status.get("started")
            minutes = None
            try:
                minutes = round((datetime.now() - datetime.fromisoformat(started)).total_seconds() / 60, 1)
            except (TypeError, ValueError):
                pass
            history.append({"round": len(history) + 1, "time": started, "promoted": False, "stopped": True, "minutes": minutes})
            _write_json(TRAIN_DIR / "history.json", history)
            _write_json(
                TRAIN_DIR / "status.json",
                {**status, "state": "stopped", "stage": "stopped", "finished": datetime.now().isoformat(timespec="seconds")},
            )
    finally:
        with _train_lock:
            _train_stopping = False
    print("Training stopped", flush=True)


def _served_name() -> str:
    """The folder name in current.txt ("" when MegaDetector is served)."""
    try:
        return Path(CURRENT.read_text(encoding="utf-8").strip()).name
    except OSError:
        return ""


def _has_model(folder: Path) -> bool:
    return folder.is_dir() and any(folder.glob("*.xml"))


def undo_target(history) -> dict | None:
    """What Undo the last round would do: {"round": the promoted round being served, "to": the
    model folder before it ("" for MegaDetector)}, or None when there is nothing to undo."""
    promoted = [
        h for h in (history if isinstance(history, list) else [])
        if isinstance(h, dict) and h.get("promoted") and not h.get("undone") and h.get("model_dir")
    ]
    if not promoted or Path(promoted[-1]["model_dir"]).name != _served_name():
        return None
    models = CURRENT.parent
    back = next(
        (Path(h["model_dir"]).name for h in reversed(promoted[:-1]) if _has_model(models / Path(h["model_dir"]).name)),
        "",
    )
    return {"round": promoted[-1].get("round"), "to": back}


def undo_round() -> dict:
    """Serve the model from before the last promoted round again, and mark that round undone
    in the history. ValueError when it can't (a round is running, or nothing to undo)."""
    global _model_seen  # noqa: PLW0603
    with _train_lock:
        if training_running() or _train_stopping:
            raise ValueError("a training round is running")
        history = _read_json(TRAIN_DIR / "history.json", [])
        target = undo_target(history)
        if target is None:
            raise ValueError("there is no promoted round to undo")
        tmp = CURRENT.with_suffix(".tmp")
        tmp.write_text(target["to"], encoding="utf-8")  # empty: MegaDetector
        tmp.replace(CURRENT)
        for h in history:
            if isinstance(h, dict) and h.get("round") == target["round"] and h.get("promoted"):
                h["undone"] = datetime.now().isoformat(timespec="seconds")
        _write_json(TRAIN_DIR / "history.json", history)
    print(f"Undid round {target['round']}; back to {target['to'] or 'MegaDetector'}", flush=True)
    try:
        _det.refresh()
        _model_seen = _current_stamp()
    except Exception as err:  # noqa: BLE001 - the model watcher tries again
        print(f"Model switch failed: {err}", flush=True)
    return target


def settings() -> dict:
    """The detector and training settings: the environment's defaults with the saved changes on top."""
    saved = _read_json(SETTINGS_FILE, {})
    saved = saved if isinstance(saved, dict) else {}
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def _clean_settings(changes: dict) -> dict:
    """Validated copies of the settings in changes; ValueError names a bad one."""
    out: dict = {}
    for key, value in changes.items():
        if key == "train_enabled":
            if not isinstance(value, bool):
                raise ValueError("train_enabled must be true or false")
            out[key] = value
        elif key == "train_day":
            if str(value).lower() not in _DAYS:
                raise ValueError(f"train_day must be one of {', '.join(_DAYS)}")
            out[key] = str(value).lower()
        elif key in ("train_hour", "min_new", "min_first"):
            low, high = {"train_hour": (0, 23), "min_new": (1, 10000), "min_first": (10, 1000)}[key]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{key} must be a whole number from {low} to {high}")
            out[key] = value
        elif key in ("train_device", "detect_device"):
            if value not in _DEVICES:
                raise ValueError(f"{key} must be one of {', '.join(_DEVICES)}")
            out[key] = value
        elif key == "train_length":
            if value not in _LENGTHS:
                raise ValueError(f"train_length must be one of {', '.join(_LENGTHS)}")
            out[key] = value
        elif key == "train_imgsz":
            if isinstance(value, bool) or value not in _SIZES:
                raise ValueError(f"train_imgsz must be one of {', '.join(map(str, _SIZES))}")
            out[key] = int(value)
        else:
            raise ValueError(f"unknown setting {key}")
    return out


_settings_lock = threading.Lock()


def save_settings(changes: dict) -> dict:
    clean = _clean_settings(changes)
    with _settings_lock:
        saved = _read_json(SETTINGS_FILE, {})
        saved = saved if isinstance(saved, dict) else {}
        device_before = {**DEFAULTS, **saved}.get("detect_device")
        saved = {**saved, **clean}
        _write_json(SETTINGS_FILE, saved)
    conf = settings()
    if conf["detect_device"] != device_before:
        # Reload the detector on the chosen device now, so /health shows what it really uses.
        try:
            _det.refresh()
            print(f"Detector now on {_det.info.get('device')}", flush=True)
        except Exception as err:  # noqa: BLE001 - the next request starts it again
            print(f"Detector reload failed: {err}", flush=True)
    return conf


def _schedule_training() -> None:
    """Once on the training day from the training hour (the host's local time): train.py
    --if-needed, which only trains when enough new answered photos arrived since the last
    round. The settings are read every minute, so changes apply without a restart."""
    done_on = None
    while True:
        time.sleep(60)
        conf, now = settings(), datetime.now()
        if (
            conf["train_enabled"]
            and _DAYS[now.weekday()] == conf["train_day"]
            and now.hour >= conf["train_hour"]
            and done_on != now.date()
            and DATASET.is_dir()
            and start_training("--if-needed")
        ):
            done_on = now.date()


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)


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


_det = DetectorProcess()
_det.refresh()  # fail fast at start if no model can load
print(f"Serving {_det.info['name']} ({_det.info['size']} px, labels {_det.info['labels']}) on {_det.info['device']}", flush=True)

threading.Thread(target=_stop_when_idle, daemon=True).start()
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
    info = _det.info
    return web.json_response(
        {
            "ok": True,
            "device": info.get("device"),  # what the detector really runs on (Detects with)
            "model": info.get("name"),
            "trained": info.get("dir") != str(MODEL_DIR),
            "size": info.get("size"),
            "labels": info.get("labels", []),
            "loaded": _det.loaded,  # the detector process is running (it stops when idle)
            "training": training_running(),
            "available": info.get("available", []),  # what OpenVINO can use here (CPU, GPU, NPU)
            "dri": sorted(p.name for p in Path("/dev/dri").glob("*")) if Path("/dev/dri").exists() else [],
        }
    )


async def train_start(_request: web.Request) -> web.Response:
    if not DATASET.is_dir():
        return web.json_response({"error": f"no dataset folder at {DATASET}"}, status=400)
    if not await asyncio.get_running_loop().run_in_executor(None, start_training):
        return web.json_response({"error": "a training round is already running"}, status=409)
    return web.json_response({"started": True})


async def train_stop(_request: web.Request) -> web.Response:
    if not await asyncio.get_running_loop().run_in_executor(None, stop_training):
        return web.json_response({"error": "no training round is running"}, status=409)
    return web.json_response({"stopping": True})


async def train_undo(_request: web.Request) -> web.Response:
    try:
        target = await asyncio.get_running_loop().run_in_executor(None, undo_round)
    except ValueError as err:
        return web.json_response({"error": str(err)}, status=409)
    return web.json_response({"undone": target["round"], "serving": _det.info.get("name")})


async def train_status(_request: web.Request) -> web.Response:
    history = _read_json(TRAIN_DIR / "history.json", [])
    conf = settings()
    running = training_running()
    return web.json_response(
        {
            "running": running,
            "stopping": running and _train_stopping,
            # The round Undo the last round would undo, and the model it goes back to.
            "undo": None if running or _train_stopping else undo_target(history),
            "enabled": conf["train_enabled"],
            "schedule": f"{conf['train_day']} from {conf['train_hour']}:00",
            "settings": conf,
            "dataset": DATASET.is_dir(),
            "serving": _det.info.get("name"),
            "status": _read_json(TRAIN_DIR / "status.json", {}),
            "rounds": len(history),
            "last": history[-1] if history else None,
        }
    )


def _compact(entry: dict) -> dict:
    """A history entry without the long photo id lists."""
    out = dict(entry)
    for key in ("before", "after"):
        if isinstance(out.get(key), dict):
            out[key] = {k: v for k, v in out[key].items() if not k.endswith("_ids")}
    return out


async def train_history(_request: web.Request) -> web.Response:
    history = _read_json(TRAIN_DIR / "history.json", [])
    return web.json_response([_compact(e) for e in history if isinstance(e, dict)])


async def settings_get(_request: web.Request) -> web.Response:
    return web.json_response(
        {"settings": settings(), "days": _DAYS, "devices": _DEVICES, "lengths": list(_LENGTHS), "sizes": _SIZES}
    )


async def settings_set(request: web.Request) -> web.Response:
    try:
        changes = await request.json()
        if not isinstance(changes, dict):
            raise ValueError("send an object of settings")
        conf = await asyncio.get_running_loop().run_in_executor(None, save_settings, changes)
    except ValueError as err:
        return web.json_response({"error": str(err)}, status=400)
    return web.json_response({"settings": conf})


async def ui_page(_request: web.Request) -> web.Response:
    return web.FileResponse(UI_DIR / "ui.html", headers={"Cache-Control": "no-cache"})


async def ui_font(_request: web.Request) -> web.Response:
    return web.FileResponse(UI_DIR / "GoogleSans.woff2", headers={"Cache-Control": "public, max-age=31536000"})


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
            "device": _det.info.get("device"),
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
            web.post("/train/stop", train_stop),
            web.post("/train/undo", train_undo),
            web.get("/train/history", train_history),
            web.get("/settings", settings_get),
            web.post("/settings", settings_set),
            web.get("/", ui_page),
            web.get("/GoogleSans.woff2", ui_font),
        ]
    )
    return app


if __name__ == "__main__":
    web.run_app(make_app(), port=int(os.environ.get("NW_PORT", "8765")))

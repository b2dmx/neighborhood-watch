"""Neighborhood Watch vision service: MegaDetector on OpenVINO.

POST /detect  (body: a JPEG/PNG)  ->  {"width", "height", "ms", "device",
                                        "detections": [{"label", "confidence", "box": [x1, y1, x2, y2]}]}
GET  /health  ->  {"ok": true, "device": ..., "model": ...}
POST /scan    {"path": "<folder under NW_SCAN_ROOT>"}  ->  {"id"}      (bulk scan, runs in the background)
GET  /scan/<id>                ->  progress and visits
GET  /scan/<id>/frame/<n>      ->  a visit's best frame (JPEG)
POST /scan/<id>/report {"names": {"<n>": {"animal", "confidence"}}}  ->  writes the report into the folder

Boxes are in pixels of the image that was sent. Labels: animal, person, vehicle.
Runs on an Intel GPU/NPU when the container can see /dev/dri, otherwise on the CPU.
"""
from __future__ import annotations

import asyncio
from io import BytesIO
import os
from pathlib import Path
import shutil
import threading
import time

from aiohttp import web
import numpy as np
import openvino as ov
from PIL import Image, UnidentifiedImageError

MODEL_DIR = Path(os.environ.get("NW_MODEL", "/models/megadetector"))
DEVICE = os.environ.get("NW_DEVICE", "AUTO")
SIZE = int(os.environ.get("NW_SIZE", "640"))  # MegaDetector v6 was trained at 640
MIN_CONF = float(os.environ.get("NW_MIN_CONF", "0.1"))
LABELS = {0: "animal", 1: "person", 2: "vehicle"}
SCAN_ROOT = Path(os.environ.get("NW_SCAN_ROOT", "/scan"))
WORK = Path(os.environ.get("NW_WORK", "/work"))
_infer_lock = threading.Lock()
SCANS: dict = {}
KEEP_SCANS = 20  # finished scans (and their frames in WORK) kept; older ones are deleted

# Memory: the compiled model is ~250 MB, so it's unloaded after NW_IDLE_UNLOAD seconds
# without a request (0 = keep it) and compiled again on the next one (well under a second).
IDLE_UNLOAD = float(os.environ.get("NW_IDLE_UNLOAD", "600"))
COMPILE_CONFIG = {"PERFORMANCE_HINT": "LATENCY", "NUM_STREAMS": "1"}  # one request at a time
core = ov.Core()
# GPU kernels take seconds to compile; caching them makes reloading after an idle unload fast.
core.set_property({"CACHE_DIR": str(WORK / "openvino-cache")})
_xml = next(MODEL_DIR.glob("*.xml"))
_wanted = DEVICE if DEVICE != "AUTO" else ("GPU" if "GPU" in core.available_devices else "CPU")
_device = _wanted
_model = None  # (compiled model, its output)
_last_used = 0.0


def _load():
    """The compiled model, compiling it first if it was unloaded. Call under _infer_lock."""
    global _model, _device, _last_used, _wanted
    _last_used = time.monotonic()
    if _model is None:
        try:
            compiled = core.compile_model(core.read_model(_xml), _wanted, COMPILE_CONFIG)
            _device = _wanted
        except Exception:  # noqa: BLE001 - e.g. the container can't reach the GPU: run on the CPU
            compiled = core.compile_model(core.read_model(_xml), "CPU", COMPILE_CONFIG)
            _device = _wanted = "CPU"  # don't retry the GPU on every reload
        _model = (compiled, compiled.output(0))
    return _model


def _unload_when_idle() -> None:
    global _model
    while IDLE_UNLOAD > 0:
        time.sleep(30)
        with _infer_lock:
            if _model is not None and time.monotonic() - _last_used > IDLE_UNLOAD:
                _model = None
                import gc  # noqa: PLC0415

                gc.collect()
                try:  # hand the freed memory back to the system (glibc)
                    import ctypes  # noqa: PLC0415

                    ctypes.CDLL("libc.so.6").malloc_trim(0)
                except OSError:
                    pass


def _restart_on_new_code() -> None:
    """The code can live in a folder on the host (./app) so updates are a file copy: when a
    .py file changes, exit between requests and let Docker's restart policy start the new
    code. A bulk scan in progress is never cut short."""
    here = Path(__file__).parent

    def stamp() -> dict[str, float]:
        return {f.name: f.stat().st_mtime for f in here.glob("*.py")}

    started = stamp()
    while True:
        time.sleep(60)
        if stamp() != started and not any(s.state in ("queued", "running") for s in SCANS.values()):
            print("New code found; restarting", flush=True)
            os._exit(0)


with _infer_lock:
    _load()  # fail fast at start if the model can't load
threading.Thread(target=_unload_when_idle, daemon=True).start()
threading.Thread(target=_restart_on_new_code, daemon=True).start()



def letterbox(img: Image.Image) -> tuple[np.ndarray, float, int, int]:
    """Scale to fit SIZE x SIZE keeping the aspect, pad with grey (as YOLO was trained)."""
    w, h = img.size
    scale = min(SIZE / w, SIZE / h)
    nw, nh = round(w * scale), round(h * scale)
    canvas = Image.new("RGB", (SIZE, SIZE), (114, 114, 114))
    left, top = (SIZE - nw) // 2, (SIZE - nh) // 2
    canvas.paste(img.resize((nw, nh), Image.BILINEAR, reducing_gap=2.0), (left, top))
    arr = np.asarray(canvas, dtype=np.float32)
    arr *= 1 / 255.0
    return np.ascontiguousarray(arr.transpose(2, 0, 1))[None], scale, left, top


def open_small(data: bytes) -> tuple[Image.Image, int, int]:
    """Decode an image no bigger than the model needs (JPEG decodes at 1/2, 1/4 or 1/8 scale
    directly), and return it with the original width and height."""
    img = Image.open(BytesIO(data))
    ow, oh = img.size
    img.draft("RGB", (SIZE * ow // max(ow, oh), SIZE * oh // max(ow, oh)))
    return img.convert("RGB"), ow, oh


def scale_boxes(found: list[dict], img: Image.Image, ow: int, oh: int) -> list[dict]:
    """Boxes from a reduced decode, back in the original image's pixels."""
    sx, sy = ow / img.width, oh / img.height
    if sx != 1 or sy != 1:
        for d in found:
            x1, y1, x2, y2 = d["box"]
            d["box"] = [round(x1 * sx), round(y1 * sy), round(x2 * sx), round(y2 * sy)]
    return found


def nms(boxes: np.ndarray, scores: np.ndarray, iou: float = 0.45) -> list[int]:
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
        yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
        yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        area_o = (boxes[order[1:], 2] - boxes[order[1:], 0]) * (boxes[order[1:], 3] - boxes[order[1:], 1])
        order = order[1:][inter / (area_i + area_o - inter + 1e-9) < iou]
    return keep


def detect(img: Image.Image) -> list[dict]:
    tensor, scale, left, top = letterbox(img)
    with _infer_lock:
        compiled, output = _load()
        raw = compiled(tensor)[output][0]  # (4 + classes, anchors)
    raw = raw.T
    scores_all = raw[:, 4:]
    cls = scores_all.argmax(1)
    conf = scores_all.max(1)
    mask = conf >= MIN_CONF
    raw, cls, conf = raw[mask], cls[mask], conf[mask]
    if not len(conf):
        return []
    cx, cy, bw, bh = raw[:, 0], raw[:, 1], raw[:, 2], raw[:, 3]
    boxes = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], 1)
    boxes[:, [0, 2]] = (boxes[:, [0, 2]] - left) / scale
    boxes[:, [1, 3]] = (boxes[:, [1, 3]] - top) / scale
    w, h = img.size
    boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, w)
    boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, h)
    out = []
    for c in np.unique(cls):
        idx = np.where(cls == c)[0]
        for k in nms(boxes[idx], conf[idx]):
            i = idx[k]
            out.append(
                {
                    "label": LABELS.get(int(c), str(int(c))),
                    "confidence": round(float(conf[i]), 3),
                    "box": [round(float(v)) for v in boxes[i]],
                }
            )
    return sorted(out, key=lambda d: -d["confidence"])


async def health(_request: web.Request) -> web.Response:
    return web.json_response(
        {
            "ok": True,
            "device": _device,
            "model": _xml.stem,
            "size": SIZE,
            "loaded": _model is not None,
            "available": core.available_devices,  # what OpenVINO can use here (CPU, GPU, NPU)
            "dri": sorted(p.name for p in Path("/dev/dri").glob("*")) if Path("/dev/dri").exists() else [],
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
            "device": _device,
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
    names = (await request.json()).get("names", {})
    for visit in job.visits:
        named = names.get(str(visit.n)) or {}
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
        ]
    )
    return app


if __name__ == "__main__":
    web.run_app(make_app(), port=int(os.environ.get("NW_PORT", "8765")))

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
import threading
import time

from aiohttp import web
import numpy as np
import openvino as ov
from PIL import Image

MODEL_DIR = Path(os.environ.get("NW_MODEL", "/models/megadetector"))
DEVICE = os.environ.get("NW_DEVICE", "AUTO")
SIZE = int(os.environ.get("NW_SIZE", "640"))  # MegaDetector v6 was trained at 640
MIN_CONF = float(os.environ.get("NW_MIN_CONF", "0.1"))
LABELS = {0: "animal", 1: "person", 2: "vehicle"}
SCAN_ROOT = Path(os.environ.get("NW_SCAN_ROOT", "/scan"))
WORK = Path(os.environ.get("NW_WORK", "/work"))
_infer_lock = threading.Lock()
SCANS: dict = {}

core = ov.Core()
_xml = next(MODEL_DIR.glob("*.xml"))
_wanted = DEVICE if DEVICE != "AUTO" else ("GPU" if "GPU" in core.available_devices else "CPU")
try:
    compiled = core.compile_model(core.read_model(_xml), _wanted)
    _device = _wanted
except Exception:  # noqa: BLE001 - e.g. the container can't reach the GPU: run on the CPU
    compiled = core.compile_model(core.read_model(_xml), "CPU")
    _device = "CPU"
output = compiled.output(0)



def letterbox(img: Image.Image) -> tuple[np.ndarray, float, int, int]:
    """Scale to fit SIZE x SIZE keeping the aspect, pad with grey (as YOLO was trained)."""
    w, h = img.size
    scale = min(SIZE / w, SIZE / h)
    nw, nh = round(w * scale), round(h * scale)
    canvas = Image.new("RGB", (SIZE, SIZE), (114, 114, 114))
    left, top = (SIZE - nw) // 2, (SIZE - nh) // 2
    canvas.paste(img.resize((nw, nh), Image.BILINEAR), (left, top))
    arr = np.asarray(canvas, dtype=np.float32) / 255.0
    return arr.transpose(2, 0, 1)[None], scale, left, top


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
    return web.json_response({"ok": True, "device": _device, "model": _xml.stem, "size": SIZE})


async def detect_endpoint(request: web.Request) -> web.Response:
    body = await request.read()
    try:
        img = Image.open(BytesIO(body)).convert("RGB")
    except Exception as err:  # noqa: BLE001
        return web.json_response({"error": f"not an image: {err}"}, status=400)
    start = time.perf_counter()
    detections = detect(img)
    return web.json_response(
        {
            "width": img.width,
            "height": img.height,
            "ms": round((time.perf_counter() - start) * 1000),
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

    body = await request.json()
    folder = _folder(str(body.get("path", "")))
    if folder is None:
        return web.json_response({"error": "no such folder under the scan root"}, status=404)
    job = scanning.Scan(path=str(folder.relative_to(SCAN_ROOT.resolve())) or ".")
    SCANS[job.id] = job
    loop = asyncio.get_running_loop()
    loop.run_in_executor(
        None,
        scanning.run_scan,
        job,
        folder,
        WORK,
        detect,
        float(body.get("threshold", 0.25)),
        float(body.get("step", 1.0)),
    )
    return web.json_response({"id": job.id})


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

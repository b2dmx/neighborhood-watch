"""A YOLO-family detector exported to OpenVINO (MegaDetector v6, or a model trained by
train.py), with letterboxing, overlapping tiles for wide frames, and NMS.

Input size and class names come from the export itself (metadata.yaml, else the model's
input shape and output width), never from constants. Used by the web app and by the trainer
(which scores the served model and a candidate the same way).
"""
from __future__ import annotations

import math
import os
from pathlib import Path
import re
import threading
import time

import numpy as np
import openvino as ov
from PIL import Image

MEGADETECTOR_LABELS = {0: "animal", 1: "person", 2: "vehicle"}
COMPILE_CONFIG = {"PERFORMANCE_HINT": "LATENCY", "NUM_STREAMS": "1"}  # one request at a time
_gpu_failed = False  # once the GPU fails to compile a model, don't retry it on every reload


def make_core(work: Path) -> ov.Core:
    core = ov.Core()
    # GPU kernels take seconds to compile; caching them makes reloading after an idle unload fast.
    core.set_property({"CACHE_DIR": str(work / "openvino-cache")})
    return core


def read_metadata(model_dir: Path) -> dict:
    """The export's metadata.yaml (imgsz, names), or {} when there is none."""
    path = model_dir / "metadata.yaml"
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # noqa: PLC0415 - comes with ultralytics

        return yaml.safe_load(text) or {}
    except ImportError:  # a minimal reader for the two keys used here
        meta: dict = {}
        if m := re.search(r"^imgsz:\s*\n((?:\s*-\s*\d+\s*\n)+)", text, re.M):
            meta["imgsz"] = [int(v) for v in re.findall(r"\d+", m.group(1))]
        if m := re.search(r"^names:\s*\n((?:[ \t]+\d+:.*\n?)+)", text, re.M):
            meta["names"] = {int(k): v.strip().strip("'\"") for k, v in re.findall(r"(\d+):\s*(.+)", m.group(1))}
        return meta


def production_dir(work: Path, fallback: Path) -> Path:
    """The model the service should serve: the one named in WORK/models/current.txt (written
    by the trainer when it promotes a model), else `fallback` (MegaDetector)."""
    current = work / "models" / "current.txt"
    try:
        name = current.read_text(encoding="utf-8").strip()
    except OSError:
        return fallback
    if not name:
        return fallback
    path = Path(name) if Path(name).is_absolute() else current.parent / name
    return path if path.is_dir() and any(path.glob("*.xml")) else fallback


class Detector:
    def __init__(
        self,
        model_dir: Path,
        core: ov.Core,
        device: str = "AUTO",
        max_tiles: int = 8,
        min_conf: float = 0.1,
        fallback_size: int = 640,
    ) -> None:
        self.dir = Path(model_dir)
        self.xml = next(self.dir.glob("*.xml"))
        self.name = self.dir.name if self.dir.name not in ("", ".") else self.xml.stem
        self.core = core
        self.max_tiles = max_tiles
        self.min_conf = min_conf
        meta = read_metadata(self.dir)
        model = core.read_model(self.xml)
        shape = model.input(0).partial_shape
        out = model.output(0).partial_shape
        imgsz = meta.get("imgsz")
        if isinstance(imgsz, (list, tuple)) and imgsz:
            self.size = int(imgsz[0])
        elif isinstance(imgsz, int):
            self.size = imgsz
        elif shape.rank.get_length() == 4 and shape[2].is_static:
            self.size = shape[2].get_length()
        else:
            self.size = fallback_size
        names = meta.get("names")
        if isinstance(names, dict) and names:
            self.labels = {int(k): str(v) for k, v in names.items()}
        elif isinstance(names, list) and names:
            self.labels = dict(enumerate(map(str, names)))
        else:  # no metadata: 3 outputs are MegaDetector's, 1 is a trained animal model
            nc = out[1].get_length() - 4 if out.rank.get_length() == 3 and out[1].is_static else 3
            self.labels = {i: MEGADETECTOR_LABELS.get(i, str(i)) for i in range(nc)}
        if len(self.labels) == 1:  # a one-class model is the trained animal detector, whatever it calls it
            self.labels = {0: "animal"}
        global _gpu_failed  # noqa: PLW0603
        self.wanted = device if device != "AUTO" else ("GPU" if "GPU" in core.available_devices else "CPU")
        if self.wanted == "GPU" and _gpu_failed:
            self.wanted = "CPU"
        self.device = self.wanted
        self.lock = threading.Lock()
        self._model = None  # (compiled model, its output)
        self.last_used = 0.0
        del model

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self):
        """The compiled model, compiling it first if it was unloaded. Call under self.lock."""
        global _gpu_failed  # noqa: PLW0603
        self.last_used = time.monotonic()
        if self._model is None:
            try:
                compiled = self.core.compile_model(self.core.read_model(self.xml), self.wanted, COMPILE_CONFIG)
                self.device = self.wanted
            except Exception:  # noqa: BLE001 - e.g. the container can't reach the GPU: run on the CPU
                if self.wanted == "CPU":
                    raise
                compiled = self.core.compile_model(self.core.read_model(self.xml), "CPU", COMPILE_CONFIG)
                self.device = self.wanted = "CPU"
                _gpu_failed = True
            self._model = (compiled, compiled.output(0))
        return self._model

    def unload_if_idle(self, idle: float) -> bool:
        with self.lock:
            if self._model is not None and time.monotonic() - self.last_used > idle:
                self._model = None
                return True
        return False

    def letterbox(self, img: Image.Image) -> tuple[np.ndarray, float, int, int]:
        """Scale to fit size x size keeping the aspect, pad with grey (as YOLO was trained)."""
        size = self.size
        w, h = img.size
        scale = min(size / w, size / h)
        nw, nh = round(w * scale), round(h * scale)
        canvas = Image.new("RGB", (size, size), (114, 114, 114))
        left, top = (size - nw) // 2, (size - nh) // 2
        canvas.paste(img.resize((nw, nh), Image.BILINEAR, reducing_gap=2.0), (left, top))
        arr = np.asarray(canvas, dtype=np.float32)
        arr *= 1 / 255.0
        return np.ascontiguousarray(arr.transpose(2, 0, 1))[None], scale, left, top

    def tiles(self, w: int, h: int) -> list[tuple[int, int, int, int]]:
        """Overlapping tiles about the model's input size (fewer, larger ones on big frames)."""
        target = float(self.size)
        while True:
            cols, rows = math.ceil(w / target), math.ceil(h / target)
            if cols * rows <= self.max_tiles:
                break
            target *= 1.15
        if cols * rows <= 1:
            return []
        tw, th = w / cols, h / rows
        pad_x, pad_y = tw * 0.1, th * 0.1  # 20% overlap so an animal on a seam is whole in one
        return [
            (
                max(0, int(c * tw - pad_x)),
                max(0, int(r * th - pad_y)),
                min(w, int((c + 1) * tw + pad_x)),
                min(h, int((r + 1) * th + pad_y)),
            )
            for r in range(rows)
            for c in range(cols)
        ]

    def candidates(self, img: Image.Image, dx: int = 0, dy: int = 0) -> tuple[np.ndarray, ...]:
        """Boxes (in the full image, offset by dx/dy), confidences and classes above min_conf.
        The output is YOLO's [1, 4 + classes, anchors]: cx, cy, w, h in input pixels, then
        one (already sigmoid) score per class."""
        tensor, scale, left, top = self.letterbox(img)
        with self.lock:
            compiled, output = self.load()
            raw = compiled(tensor)[output][0]
        raw = raw.T
        scores_all = raw[:, 4:]
        cls = scores_all.argmax(1)
        conf = scores_all.max(1)
        mask = conf >= self.min_conf
        raw, cls, conf = raw[mask], cls[mask], conf[mask]
        cx, cy, bw, bh = raw[:, 0], raw[:, 1], raw[:, 2], raw[:, 3]
        boxes = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], 1)
        boxes[:, [0, 2]] = (boxes[:, [0, 2]] - left) / scale + dx
        boxes[:, [1, 3]] = (boxes[:, [1, 3]] - top) / scale + dy
        return boxes, conf, cls

    def detect(self, img: Image.Image) -> list[dict]:
        found = [self.candidates(img)]
        for x0, y0, x1, y1 in self.tiles(*img.size):
            found.append(self.candidates(img.crop((x0, y0, x1, y1)), x0, y0))
        boxes = np.concatenate([f[0] for f in found])
        conf = np.concatenate([f[1] for f in found])
        cls = np.concatenate([f[2] for f in found])
        if not len(conf):
            return []
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
                        "label": self.labels.get(int(c), str(int(c))),
                        "confidence": round(float(conf[i]), 3),
                        "box": [round(float(v)) for v in boxes[i]],
                    }
                )
        return sorted(out, key=lambda d: -d["confidence"])


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


def env_detector(model_dir: Path, core: ov.Core) -> Detector:
    """A Detector configured from the service's environment variables."""
    return Detector(
        model_dir,
        core,
        device=os.environ.get("NW_DEVICE", "AUTO"),
        max_tiles=int(os.environ.get("NW_TILES", "8")),
        min_conf=float(os.environ.get("NW_MIN_CONF", "0.1")),
        fallback_size=int(os.environ.get("NW_SIZE", "640")),
    )

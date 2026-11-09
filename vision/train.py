"""Teach the detector the owner's cameras: one training round on the labelled photos that
the Home Assistant integration saves (NW_DATASET/<date>/<id>.jpg + <id>.json).

1. Auto-label: every animal photo gets boxes from two teachers, a COCO YOLO11m at 1280 px
   (its animal classes as one class) and the boxes the detector stored with the photo. A
   confirmed animal photo neither could box uses the box the AI gave, if any.
   Photos marked empty become background images (no boxes).
   Each animal photo's main box is named after what it was (your answer, or the AI's when it
   was sure and nobody has answered): the animals with at least MIN_PER_NAME answered photos
   are classes of their own, everything else is "animal". So one model finds and names.
2. Split by time: the confirmed photos from the most recent ~20% of dates are the
   evaluation set and are never trained on. So are confirmed animal photos that no teacher
   could box (they can't be trained on, but they're the misses that matter most).
3. Train a one-class YOLO ("animal"), from yolo11s (COCO) the first time, then from the
   trained model being served (so after an undo, from the model it went back to).
4. Score the model being served and the new one on the evaluation set, the way the service
   runs them (OpenVINO, tiles) at the integration's threshold. The new model is promoted
   if it names more of the held-back animals right without missing more of them, or misses
   fewer and makes at least 10% fewer mistakes in all (misses plus false alarms) while naming
   no fewer right. For each name it also finds the confidence from which that name was right
   at least SURE_PRECISION of the time (id.json beside the export): from there the
   integration can trust it without asking the AI.
5. A promoted model is exported to OpenVINO in WORK/models/trained-<n>/ and named in
   WORK/models/current.txt; the service switches to it within a minute.

Every round is recorded in WORK/train/history.json; WORK/train/status.json shows progress.
SIGTERM (Stop training) ends a round early: it is recorded as stopped and never promoted.

    python train.py [--if-needed] [--epochs 40] [--imgsz 960] [--baseline service|stored]
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import random
import re
import shutil
import signal
import sys
import time
import traceback

NOT_ANIMAL = {"nothing", "none", "no answer", "", "person", "bug", "spider", "leaf", "shadow", "vehicle", "car"}
CONFIRMED = {"right", "corrected"}
GENERIC = "animal"  # the class for animals without enough answered photos to have their own
MIN_PER_NAME = int(os.environ.get("NW_TRAIN_MIN_PER_NAME", "12"))
SURE_PRECISION = float(os.environ.get("NW_TRAIN_SURE_PRECISION", "0.95"))
SURE_MIN_COUNT = 5  # a name is trusted only after this many right answers above the line
COCO_ANIMALS = list(range(14, 24))  # bird cat dog horse sheep cow elephant bear zebra giraffe
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


class Stopped(BaseException):
    """Stop training was pressed (SIGTERM). A BaseException, so nothing on the way catches it
    (the GPU-failed retry on the CPU included)."""


_stop = {"asked": False, "hold": False}  # hold: past the point of no return (promoting)


def _on_term(_signum, _frame) -> None:
    _stop["asked"] = True
    if not _stop["hold"]:
        raise Stopped


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", type=Path, default=Path(env("NW_DATASET", "/dataset")))
    p.add_argument("--work", type=Path, default=Path(env("NW_WORK", "/work")))
    p.add_argument("--megadetector", type=Path, default=Path(env("NW_MODEL", "/models/megadetector")))
    p.add_argument("--epochs", type=int, default=int(env("NW_TRAIN_EPOCHS", "20")))
    p.add_argument("--patience", type=int, default=int(env("NW_TRAIN_PATIENCE", "8")))
    p.add_argument("--imgsz", type=int, default=int(env("NW_TRAIN_IMGSZ", "960")))
    p.add_argument("--batch", type=int, default=int(env("NW_TRAIN_BATCH", "8")))
    p.add_argument("--teacher-imgsz", type=int, default=1280)
    p.add_argument("--threshold", type=float, default=float(env("NW_TRAIN_THRESHOLD", "0.2")),
                   help="the integration's detector threshold, used to count misses and false alarms")
    p.add_argument("--min-new", type=int, default=int(env("NW_TRAIN_MIN_NEW", "100")))
    p.add_argument("--min-first", type=int, default=int(env("NW_TRAIN_MIN_FIRST", "40")))
    p.add_argument("--if-needed", action="store_true",
                   help="only train with enough new answered photos since the last round")
    p.add_argument("--baseline", choices=["service", "stored"], default="service",
                   help="score the current model by running it (service) or from the scores stored with each photo")
    p.add_argument("--device", default=env("NW_TRAIN_DEVICE", ""), help="torch device (default: xpu if available, else cpu)")
    p.add_argument("--no-promote", action="store_true", help="evaluate but never switch the service's model")
    return p.parse_args(argv)


# ---- dataset ----------------------------------------------------------------------------
@dataclass
class Sample:
    id: str
    jpg: Path
    meta: dict
    time: str
    date: str
    animal: bool
    trust: str  # confirmed | ai | skip
    boxes: list = field(default_factory=list)  # [x1, y1, x2, y2, conf] in jpg pixels
    name: str = ""  # what the animal is (your answer, else the AI's), title case
    main: int = -1  # which box is that animal (the others are just "animal")

    @property
    def empty_ok(self) -> bool:
        """Safe to use as a background (no animal) image."""
        if self.animal:
            return False
        return self.trust == "confirmed" or (
            self.trust == "ai" and str(self.meta.get("ai_confidence") or "").lower() == "high"
        )


def load_samples(dataset: Path) -> list[Sample]:
    out = []
    for js in sorted(dataset.rglob("*.json")):
        jpg = js.with_suffix(".jpg")
        if not jpg.exists():
            continue
        try:
            meta = json.loads(js.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict):
            continue
        by = (meta.get("labelled_by") or "").lower()
        trust = "confirmed" if by in CONFIRMED else "skip" if by == "unsure" else "ai"
        name = meta.get("label") if meta.get("label") is not None else meta.get("ai_animal")
        if str(name or "").strip().lower() == "no answer":
            trust = "skip"  # no AI looked and nobody said: unknown either way
        animal = str(name or "").strip().lower() not in NOT_ANIMAL
        stamp = str(meta.get("time") or "")
        date = js.parent.name if DATE.match(js.parent.name) else stamp[:10]
        named = str(name or "").strip()
        out.append(Sample(str(meta.get("id") or js.stem), jpg, meta, stamp, date, animal, trust,
                          name=named[:1].upper() + named[1:] if animal else ""))
    return [s for s in out if s.trust != "skip"]


def stored_boxes(s: Sample, w: int, h: int) -> list[list[float]]:
    """The animal boxes the detector stored, scaled from the original frame to the jpg."""
    d = s.meta.get("detector") or {}
    size = d.get("size") or [w, h]
    sx, sy = w / max(1, size[0]), h / max(1, size[1])
    return [
        [b["box"][0] * sx, b["box"][1] * sy, b["box"][2] * sx, b["box"][3] * sy, float(b["confidence"])]
        for b in d.get("boxes") or []
        if b.get("label") == "animal" and float(b.get("confidence", 0)) >= 0.1 and len(b.get("box") or []) == 4
    ]


def merge(boxes: list[list[float]], iou: float = 0.5) -> list[list[float]]:
    boxes = sorted(boxes, key=lambda b: -b[4])
    keep: list[list[float]] = []
    for b in boxes:
        if all(_iou(b, k) < iou for k in keep):
            keep.append(b)
    return keep


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def teacher_boxes(samples: list[Sample], work: Path, imgsz: int, device: str, status) -> None:
    """Fill s.boxes for animal photos: COCO YOLO11m (cached per id) + the stored boxes."""
    from PIL import Image  # noqa: PLC0415

    cache_path = work / "train" / "labels-cache.json"
    cache = read_json(cache_path, {})
    key = f"yolo11m@{imgsz}"
    todo = [s for s in samples if s.animal and (cache.get(s.id) or {}).get("teacher") != key]
    if todo:
        from ultralytics import YOLO  # noqa: PLC0415

        teacher = YOLO(weights(work, "yolo11m.pt"))
        for i, s in enumerate(todo):
            if i % 20 == 0:
                status(stage="labelling", done=i, total=len(todo))
            r = teacher.predict(str(s.jpg), imgsz=imgsz, conf=0.1, classes=COCO_ANIMALS, device=device, verbose=False)[0]
            cache[s.id] = {
                "teacher": key,
                "boxes": [[round(v, 1) for v in b] + [round(float(c), 3)]
                          for b, c in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist())],
            }
        write_json(cache_path, cache)
    for s in samples:
        if s.animal:
            with Image.open(s.jpg) as im:
                w, h = im.size
            s.boxes = merge((cache.get(s.id) or {}).get("boxes", []) + stored_boxes(s, w, h))
            if not s.boxes and s.trust == "confirmed":
                s.boxes = ai_box(s, w, h)
            s.main = main_box(s, w, h)


def main_box(s: Sample, w: int, h: int) -> int:
    """The box of the animal the photo is labelled as: the one overlapping where the AI said
    it was most, else the most confident."""
    if not s.boxes:
        return -1
    said = ai_box(s, w, h)
    if said:
        overlaps = [_iou(b, said[0]) for b in s.boxes]
        if max(overlaps) > 0:
            return overlaps.index(max(overlaps))
    return max(range(len(s.boxes)), key=lambda i: s.boxes[i][4])


def pick_names(samples: list[Sample]) -> list[str]:
    """The classes: animals with at least MIN_PER_NAME answered, boxed photos (most first),
    then "animal" for everything else."""
    counts: dict[str, int] = {}
    for s in samples:
        if s.animal and s.trust == "confirmed" and s.boxes and s.name.lower() != GENERIC:
            counts[s.name] = counts.get(s.name, 0) + 1
    return [n for n, c in sorted(counts.items(), key=lambda kv: -kv[1]) if c >= MIN_PER_NAME] + [GENERIC]


def class_of(s: Sample, names: list[str]) -> int | None:
    """The class of the photo's main box; None when it shouldn't be trained on (the AI named
    one of the classes without being sure, and nobody has answered)."""
    if s.name in names:
        sure = s.trust == "confirmed" or str(s.meta.get("ai_confidence") or "").lower() == "high"
        return names.index(s.name) if sure else None
    return names.index(GENERIC)


def ai_box(s: Sample, w: int, h: int) -> list[list[float]]:
    """Where the AI said the animal was (0-1 of the frame), for a confirmed animal photo that
    neither teacher could box: rougher than a teacher's box, but these are the very photos
    the detector misses."""
    b = s.meta.get("ai_box")
    if not (isinstance(b, list) and len(b) == 4):
        return []
    x1, y1, x2, y2 = (float(v) for v in b)
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        return []
    return [[x1 * w, y1 * h, x2 * w, y2 * h, 0.5]]


def split(samples: list[Sample]) -> tuple[list[Sample], list[Sample]]:
    """(train, eval). Eval: confirmed photos from the newest ~20% of dates, plus high-confidence
    'nothing' photos from those dates as the empty side (confirmed empty photos are rare, and
    false alarms can't be counted without them), plus confirmed animal photos no teacher
    could box."""
    dates = sorted({s.date for s in samples})
    k = max(1, round(len(dates) * 0.2)) if len(dates) >= 2 else 0
    recent = set(dates[-k:]) if k else set()
    if not recent:  # a single day: the newest 20% of photos instead
        ordered = sorted(samples, key=lambda s: s.time)
        newest = {s.id for s in ordered[int(len(ordered) * 0.8):]}
    evals, train = [], []
    for s in samples:
        is_recent = s.date in recent if recent else s.id in newest
        if is_recent and (s.trust == "confirmed" or s.empty_ok):
            evals.append(s)
        elif s.animal and s.trust == "confirmed" and not s.boxes:
            evals.append(s)
        elif (s.animal and s.boxes) or s.empty_ok:
            train.append(s)
    return train, evals


def build_yolo_dataset(train: list[Sample], root: Path, names: list[str], seed: int = 0) -> tuple[Path, int]:
    """images/{train,val} + labels/{train,val} + data.yaml. val (20% of the animal and of the
    empty photos) only picks the best epoch and stops early; it is not the evaluation set."""
    from PIL import Image  # noqa: PLC0415

    shutil.rmtree(root, ignore_errors=True)
    parts: dict[str, list[Sample]] = {"val": [], "train": []}
    for animal in (True, False):
        group = sorted((s for s in train if s.animal == animal), key=lambda s: s.id)
        random.Random(seed).shuffle(group)
        n_val = max(1, round(len(group) * 0.2)) if len(group) > 1 else 0
        parts["val"] += group[:n_val]
        parts["train"] += group[n_val:]
    for part, group in parts.items():
        (root / "images" / part).mkdir(parents=True)
        (root / "labels" / part).mkdir(parents=True)
        for s in group:
            dest = root / "images" / part / f"{s.id}.jpg"
            if os.name == "posix":
                dest.symlink_to(s.jpg.resolve())
            else:
                shutil.copyfile(s.jpg, dest)
            lines = []
            if s.animal:
                with Image.open(s.jpg) as im:
                    w, h = im.size
                main = class_of(s, names)
                generic = names.index(GENERIC)
                for i, (x1, y1, x2, y2, _c) in enumerate(s.boxes):
                    x1, x2 = max(0.0, x1), min(float(w), x2)
                    y1, y2 = max(0.0, y1), min(float(h), y2)
                    k = main if i == s.main else generic
                    if x2 - x1 >= 2 and y2 - y1 >= 2:
                        lines.append(f"{k} {(x1 + x2) / 2 / w:.6f} {(y1 + y2) / 2 / h:.6f} {(x2 - x1) / w:.6f} {(y2 - y1) / h:.6f}")
            (root / "labels" / part / f"{s.id}.txt").write_text("\n".join(lines), encoding="utf-8")
    yaml_path = root / "data.yaml"
    listed = "".join(f"  {i}: {json.dumps(n)}\n" for i, n in enumerate(names))
    yaml_path.write_text(
        f"path: {root.resolve().as_posix()}\ntrain: images/train\nval: images/val\nnames:\n{listed}",
        encoding="utf-8",
    )
    return yaml_path, sum(s.animal for s in parts["val"])


# ---- evaluation -------------------------------------------------------------------------
def run_model(model_dir: Path, evals: list[Sample], work: Path) -> list[list[dict]]:
    """Every animal detection per photo, run exactly as the service runs the model."""
    from PIL import Image  # noqa: PLC0415

    import detector as det  # noqa: PLC0415

    model = det.env_detector(model_dir, det.make_core(work))
    model.min_conf = min(model.min_conf, 0.05)
    out = []
    for s in evals:
        with Image.open(s.jpg) as im:
            out.append([d for d in model.detect(im.convert("RGB")) if d["label"] == "animal"])
    return out


def best_scores(found: list[list[dict]]) -> list[float]:
    return [max((d["confidence"] for d in f), default=0.0) for f in found]


def naming(found: list[list[dict]], evals: list[Sample], names: list[str], threshold: float) -> dict:
    """How well the model names the held-back photos. A photo's answer is its most confident
    box's name (at or above the detector threshold). Per name: right/total, and `sure`, the
    lowest confidence from which that name was right at least SURE_PRECISION of the time
    (photos of other animals and empty photos count against it)."""
    guesses: dict[str, list[tuple[float, bool]]] = {}
    right = total = 0
    per: dict[str, dict] = {n: {"photos": 0, "right": 0} for n in names if n != GENERIC}
    for f, s in zip(found, evals):
        top = max(f, key=lambda d: d["confidence"], default=None)
        said = top.get("name") if top and top["confidence"] >= threshold else None
        truth = s.name if s.animal and s.trust == "confirmed" and s.name in per else None
        if truth:
            total += 1
            per[truth]["photos"] += 1
            if said == truth:
                right += 1
                per[truth]["right"] += 1
        if said in per and (truth or not s.animal):
            guesses.setdefault(said, []).append((top["confidence"], said == truth))
    sure: dict[str, float] = {}
    for name, rows in guesses.items():
        rows.sort(key=lambda r: -r[0])
        hits = 0
        for i, (conf, ok) in enumerate(rows, 1):
            hits += ok
            if hits / i >= SURE_PRECISION and hits >= SURE_MIN_COUNT:
                sure[name] = conf  # from here up, right often enough
    for name, line in sure.items():
        rows = guesses[name]
        per[name]["sure_from"] = round(line, 3)
        per[name]["sure_right"] = sum(ok for c, ok in rows if c >= line)
    return {"named": total, "named_right": right, "names": per, "sure": {k: round(v, 3) for k, v in sure.items()}}


def stored_scores(evals: list[Sample]) -> list[float]:
    return [float((s.meta.get("detector") or {}).get("animal") or 0.0) for s in evals]


def metrics(scores: list[float], evals: list[Sample], threshold: float) -> dict:
    animals = [sc for sc, s in zip(scores, evals) if s.animal]
    empty = [sc for sc, s in zip(scores, evals) if not s.animal]
    misses = sum(sc < threshold for sc in animals)
    false_alarms = sum(sc >= threshold for sc in empty)
    return {
        "animals": len(animals),
        "empty": len(empty),
        "misses": misses,
        "false_alarms": false_alarms,
        "recall": round(1 - misses / len(animals), 3) if animals else None,
        "agreement": round(1 - (misses + false_alarms) / len(evals), 3) if evals else None,
        "missed_ids": [s.id for sc, s in zip(scores, evals) if s.animal and sc < threshold][:50],
        "false_alarm_ids": [s.id for sc, s in zip(scores, evals) if not s.animal and sc >= threshold][:50],
    }


def better(before: dict, after: dict) -> bool:
    """Names more held-back animals right without missing more of them; or misses fewer and
    makes at least 10% fewer mistakes in all (misses plus false alarms), naming no fewer
    right. (A false alarm only costs an AI call; a miss loses a sighting.)"""
    if not after["animals"]:
        return False
    mistakes = lambda m: m["misses"] + m["false_alarms"]  # noqa: E731
    named, named_before = after.get("named_right", 0), before.get("named_right", 0)
    if named > named_before and after["misses"] <= before["misses"]:
        return True
    return (
        named >= named_before
        and after["misses"] < before["misses"]
        and mistakes(after) <= 0.9 * mistakes(before)
    )


# ---- helpers ----------------------------------------------------------------------------
def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)


def weights(work: Path, name: str) -> str:
    """A COCO checkpoint, downloaded once into WORK/train/weights."""
    folder = work / "train" / "weights"
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder / name)


def loader_workers() -> int:
    """2 data-loading processes, or none when Docker's default 64 MB /dev/shm would crash them."""
    try:
        if os.name == "posix" and shutil.disk_usage("/dev/shm").total < 512 * 1024 * 1024:
            return 0
    except OSError:
        return 0
    return 2


def pick_device(wanted: str) -> str:
    if wanted:
        return wanted
    import torch  # noqa: PLC0415

    if not (hasattr(torch, "xpu") and torch.xpu.is_available()):
        return "cpu"
    try:  # the GPU is visible, but check it can really compute before relying on it
        x = torch.ones(256, 256, device="xpu")
        float((x @ x).sum().cpu())
    except Exception as err:  # noqa: BLE001 - e.g. a driver too old for this torch
        print(f"Intel GPU found but unusable ({err}); training on the CPU", flush=True)
        return "cpu"
    return "xpu"


def export_openvino(best_pt: Path, imgsz: int, target: Path) -> Path:
    from ultralytics import YOLO  # noqa: PLC0415

    try:
        out = YOLO(str(best_pt)).export(format="openvino", imgsz=imgsz, half=True, dynamic=False, verbose=False)
    except Exception as err:  # noqa: BLE001 - FP16 compression unsupported: plain FP32
        print(f"FP16 export failed ({err}); exporting FP32", flush=True)
        out = YOLO(str(best_pt)).export(format="openvino", imgsz=imgsz, half=False, dynamic=False, verbose=False)
    shutil.rmtree(target, ignore_errors=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(out), str(target))
    shutil.copyfile(best_pt, target / "best.pt")  # the next round starts from it
    return target


# ---- the round --------------------------------------------------------------------------
def run(args: argparse.Namespace) -> int:
    work = args.work
    tdir = work / "train"
    tdir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("YOLO_CONFIG_DIR", str(tdir / "ultralytics"))
    sys.path.insert(0, str(Path(__file__).parent))
    if hasattr(os, "nice"):  # low priority, so detection stays responsive (already 10 under the app)
        try:
            os.nice(max(0, 10 - os.nice(0)))
        except OSError:
            pass
    status_path = tdir / "status.json"
    history_path = tdir / "history.json"
    started = time.time()
    state = {"state": "running", "pid": os.getpid(), "started": datetime.now().isoformat(timespec="seconds")}
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_term)

    def status(**kw) -> None:
        if "done" not in kw:  # progress counts belong to the stage that set them
            state.pop("done", None)
            state.pop("total", None)
        state.update(kw)
        write_json(status_path, state)

    try:
        status(stage="reading the dataset")
        history = read_json(history_path, [])
        samples = load_samples(args.dataset)
    except Stopped:
        status(state="stopped", stage="stopped", finished=datetime.now().isoformat(timespec="seconds"))
        return 0
    confirmed = [s for s in samples if s.trust == "confirmed"]
    confirmed_animals = sum(s.animal for s in confirmed)
    if args.if_needed:
        # A stopped or failed round doesn't count: new photos are counted from the last real one.
        last = next(
            (h for h in reversed(history) if "confirmed" in h and not h.get("error") and not h.get("stopped")), None
        )
        if last is None and confirmed_animals < args.min_first:
            status(state="skipped", stage=f"only {confirmed_animals} answered animal photos (need {args.min_first})")
            return 0
        if last is not None and len(confirmed) - last["confirmed"] < args.min_new:
            status(state="skipped", stage=f"only {len(confirmed) - last['confirmed']} new answered photos (need {args.min_new})")
            return 0

    n = len(history) + 1
    entry: dict = {"round": n, "time": state["started"], "confirmed": len(confirmed), "promoted": False}
    try:
        device = pick_device(args.device)
        entry["device"] = device
        from ultralytics import settings  # noqa: PLC0415

        settings.update({"sync": False, "weights_dir": str(tdir / "weights"), "runs_dir": str(tdir / "runs")})

        teacher_boxes(samples, work, args.teacher_imgsz, device, status)
        names = pick_names(samples)
        entry["names"] = names
        train, evals = split(samples)
        train = [s for s in train if not s.animal or class_of(s, names) is not None]
        boxed = [s for s in train if s.animal]
        unboxed = [s for s in evals if s.animal and s.trust == "confirmed" and not s.boxes]
        entry["samples"] = {
            "total": len(samples),
            "train_animals": len(boxed),
            "train_empty": len(train) - len(boxed),
            "train_boxes": sum(len(s.boxes) for s in boxed),
            "unboxed_confirmed_animals": len(unboxed),
            "eval_animals": sum(s.animal for s in evals),
            "eval_empty": sum(not s.animal for s in evals),
            "per_name": {n: sum(1 for s in boxed if class_of(s, names) == i) for i, n in enumerate(names)},
        }
        print("Samples:", entry["samples"], flush=True)
        if len(boxed) < 10 or not any(s.animal for s in evals):
            raise RuntimeError("not enough labelled photos to train and evaluate")

        # Before: the model being served now.
        status(stage="scoring the current model")
        import detector as det  # noqa: PLC0415

        current_dir = det.production_dir(work, args.megadetector)
        if args.baseline == "stored":
            before_scores, before_name, before_found = stored_scores(evals), "stored detector scores", None
        else:
            before_found = run_model(current_dir, evals, work)
            before_scores, before_name = best_scores(before_found), current_dir.name
        entry["before"] = {"model": before_name, **metrics(before_scores, evals, args.threshold)}
        if before_found is not None:
            entry["before"].update(naming(before_found, evals, names, args.threshold))
        print("Before:", entry["before"], flush=True)

        # Train.
        from ultralytics import YOLO  # noqa: PLC0415

        # From the trained model being served (not simply the last promoted one: that may have
        # been undone), else from COCO.
        previous = current_dir / "best.pt" if (current_dir / "best.pt").exists() else None
        start = str(previous) if previous else weights(work, "yolo11s.pt")
        entry["start_from"] = Path(start).parent.name if previous else "yolo11s.pt"
        data_yaml, val_animals = build_yolo_dataset(train, tdir / "data", names)
        # With only a few animals to validate on, the "best" epoch is noise (often an early,
        # barely trained one): then train every epoch and keep the last.
        small = val_animals < 30
        entry["checkpoint"] = "last" if small else "best"
        t0 = time.time()

        def fit(on: str) -> YOLO:
            model = YOLO(start)
            model.add_callback(
                "on_fit_epoch_end",
                lambda trainer: status(stage="training", epoch=min(trainer.epoch + 1, trainer.epochs), epochs=trainer.epochs),
            )
            status(stage="training", epoch=0, epochs=args.epochs)
            model.train(
                data=str(data_yaml), epochs=args.epochs, patience=0 if small else args.patience, imgsz=args.imgsz,
                batch=args.batch, device=on, workers=loader_workers(), seed=0, deterministic=True,
                project=str(tdir / "runs"), name=f"round-{n}", exist_ok=True, plots=False, verbose=False,
                cache=False, amp=on not in ("cpu", "xpu"),
                # A small dataset with few boxes per batch: Ultralytics' automatic choice (AdamW plus a
                # 0.1 bias learning rate during warm-up, meant for SGD) blows the class scores up.
                optimizer="AdamW", lr0=0.001, warmup_bias_lr=0.0,
            )
            return model

        try:
            model = fit(device)
        except Exception as err:  # a GPU failure mid-round shouldn't waste the week
            if device == "cpu":
                raise
            print(f"Training on {device} failed ({err}); starting again on the CPU", flush=True)
            entry["gpu_error"] = str(err)[:300]
            device = entry["device"] = "cpu"
            model = fit(device)
        entry["train_minutes"] = round((time.time() - t0) / 60, 1)
        best = Path(model.trainer.last if small else model.trainer.best)
        if not best.exists():
            best = Path(model.trainer.last)

        # After: the exported model, run the way the service will run it.
        status(stage="exporting")
        candidate = export_openvino(best, args.imgsz, work / "models" / f"candidate-{n}")
        status(stage="scoring the new model")
        after_found = run_model(candidate, evals, work)
        entry["after"] = {
            "model": f"trained-{n}",
            **metrics(best_scores(after_found), evals, args.threshold),
            **naming(after_found, evals, names, args.threshold),
        }
        write_json(candidate / "id.json", {"names": names[:-1], "sure": entry["after"]["sure"],
                                           "per_name": entry["after"]["names"], "round": n})
        print("After:", entry["after"], flush=True)

        _stop["hold"] = True  # from here a stop waits: the round finishes and is recorded as it is
        if _stop["asked"]:
            raise Stopped
        if better(entry["before"], entry["after"]) and not args.no_promote:
            final = work / "models" / f"trained-{n}"
            shutil.rmtree(final, ignore_errors=True)
            candidate.rename(final)
            write_text_atomic(work / "models" / "current.txt", final.name)
            entry.update(promoted=True, model_dir=str(final))
            prune_models(work / "models", keep=final.name)
        else:
            entry["kept_candidate"] = str(candidate) if args.no_promote else None
            if not args.no_promote:
                shutil.rmtree(candidate, ignore_errors=True)
        shutil.rmtree(tdir / "data", ignore_errors=True)
        shutil.rmtree(best.parent, ignore_errors=True)  # checkpoints; best.pt lives on in the model folder
        entry["minutes"] = round((time.time() - started) / 60, 1)
        history.append(entry)
        write_json(history_path, history)
        status(state="done", stage="promoted" if entry["promoted"] else "kept the current model", finished=datetime.now().isoformat(timespec="seconds"))
        print("Round", n, "promoted" if entry["promoted"] else "not promoted", flush=True)
        return 0
    except Stopped:
        _stop["hold"] = True  # a second stop request can't cut the tidying short
        print("Round", n, "stopped", flush=True)
        for leftover in (work / "models" / f"candidate-{n}", tdir / "data", tdir / "runs" / f"round-{n}"):
            shutil.rmtree(leftover, ignore_errors=True)
        entry.update(stopped=True, promoted=False, minutes=round((time.time() - started) / 60, 1))
        history.append(entry)
        write_json(history_path, history)
        status(state="stopped", stage="stopped", finished=datetime.now().isoformat(timespec="seconds"))
        return 0
    except Exception as err:  # noqa: BLE001
        traceback.print_exc()
        entry["error"] = str(err)[:500]
        entry["minutes"] = round((time.time() - started) / 60, 1)
        history.append(entry)
        write_json(history_path, history)
        status(state="error", stage=str(err)[:300], finished=datetime.now().isoformat(timespec="seconds"))
        return 1


def write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def prune_models(models: Path, keep: str, count: int = 3) -> None:
    """Keep the newest few trained models (to roll back by editing current.txt)."""
    trained = sorted(
        (p for p in models.glob("trained-*") if p.is_dir() and p.name.split("-")[-1].isdigit()),
        key=lambda p: int(p.name.split("-")[-1]),
    )
    for p in trained[:-count]:
        if p.name != keep:
            shutil.rmtree(p, ignore_errors=True)
    for p in models.glob("candidate-*"):
        shutil.rmtree(p, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(run(parse_args()))

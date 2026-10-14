"""
Camera Detection page — dock detection on camera video, live cameras,
network streams, single images and image folders.

All decoding + inference runs in a background QThread so the ground station
UI never blocks. Video playback is paced to the source frame rate, with an
optional real-time mode that drops frames when inference can't keep up.
"""
import csv
import os
import shutil
import threading
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QSettings, QUrl, QDateTime, QRectF, QTimer
from PySide6.QtGui import QImage, QPainter, QColor, QFont, QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton,
    QComboBox, QSlider, QLineEdit, QFileDialog, QCheckBox, QSpinBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QSizePolicy, QScrollArea, QSplitter, QAbstractItemView,
)

# Quiet third-party console output (library banners / codec probing noise).
os.environ.setdefault("YOLO_VERBOSE", "False")
os.environ.setdefault("YOLO_OFFLINE", "1")
os.environ.setdefault("OPENCV_LOG_LEVEL", "OFF")
os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")

MODEL_DEFAULT = Path(__file__).resolve().parent.parent / "models" / "camera_detection.pt"
RECORD_DIR_DEFAULT = Path.home() / "MarineGS_Recordings" / "camera_detection"
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp")
VIDEO_FILTER = "Video files (*.mp4 *.avi *.mkv *.mov *.m4v *.wmv *.mpg *.mpeg *.ts *.h264 *.webm);;All files (*)"
IMAGE_FILTER = "Images (*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp);;All files (*)"

SRC_VIDEO, SRC_CAMERA, SRC_STREAM, SRC_IMAGE, SRC_FOLDER = (
    "Video file", "USB / built-in camera", "Network stream (RTSP / HTTP / UDP)", "Image", "Image folder")

LOCK_FRAMES = 5          # consecutive detections needed for "DOCK LOCKED"
COAST_FRAMES = 6         # frames a lost target is held before the lock drops
TRACK_FACTOR = 0.4       # while tracking, accept a box at TRACK_FACTOR × confidence if it overlaps the last one
WORK_MAX = 1280          # frames larger than this (e.g. 1080p / 4K) are downscaled once for all processing
RAW_FLOOR = 0.03         # model is always queried at this floor so the raw score can be shown
LOG_TABLE_ROWS = 300     # rows kept visible in the table (CSV keeps everything)
SETTINGS_ORG, SETTINGS_APP = "NIOT", "MarineGroundStation"

_MODEL_CACHE = {}


# ============================================================ frame sources
class _VideoSource:
    """Video file / camera / stream via OpenCV, with its own frame counter
    (CAP_PROP_POS_FRAMES is unreliable on many codecs)."""

    def __init__(self, cv2, kind, target):
        self.cv2 = cv2
        self.kind = kind
        self.live = kind in (SRC_CAMERA, SRC_STREAM)
        if kind == SRC_CAMERA:
            idx = int(target)
            cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW) if os.name == "nt" else cv2.VideoCapture(idx)
            if not cap.isOpened() and os.name == "nt":
                cap = cv2.VideoCapture(idx, cv2.CAP_MSMF)
        elif kind == SRC_STREAM:
            cap = cv2.VideoCapture(str(target), cv2.CAP_FFMPEG)
            if not cap.isOpened():
                cap = cv2.VideoCapture(str(target))
        else:
            cap = cv2.VideoCapture(str(target), cv2.CAP_FFMPEG)
            if not cap.isOpened():
                cap = cv2.VideoCapture(str(target))
        self.cap = cap
        if not cap.isOpened():
            raise RuntimeError(f"Could not open {kind.lower()}: {target}")
        if hasattr(cv2, "CAP_PROP_ORIENTATION_AUTO"):
            cap.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)       # respect phone-video rotation
        if self.live:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)              # minimise latency
        fps = cap.get(cv2.CAP_PROP_FPS)
        self.fps = fps if fps and 1.0 < fps < 241.0 else (30.0 if not self.live else 25.0)
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        self.total = n if (n > 0 and not self.live) else 0
        self.pos = 0
        self.name = f"Camera {target}" if kind == SRC_CAMERA else Path(str(target)).name or str(target)
        # Live sources: a reader thread always keeps only the newest frame, so the
        # displayed image never lags behind real time when inference is slower.
        self._latest, self._seq, self._seen = None, 0, 0
        self._alive = True
        self._cond = threading.Condition()
        # Video files: a decoder thread reads ahead into a small queue so decoding runs
        # in parallel with inference (big FPS gain on GPU, where decode is the bottleneck).
        self._q = deque()
        self._eof = False
        self._seek_req = None
        self._next = 0
        self._thr = threading.Thread(target=self._reader if self.live else self._prefetcher, daemon=True)
        self._thr.start()

    def _prefetcher(self):
        cap = self.cap
        while True:
            with self._cond:
                if not self._alive:
                    break
                if self._seek_req is not None:
                    cap.set(self.cv2.CAP_PROP_POS_FRAMES, self._seek_req)
                    self._next, self._seek_req = self._seek_req, None
                    self._q.clear()
                    self._eof = False
                if self._eof or len(self._q) >= 6:
                    self._cond.wait(0.05)
                    continue
                idx = self._next
            ok, frame = cap.read()                       # decode outside the lock
            with self._cond:
                if self._seek_req is not None:           # a seek arrived meanwhile: discard
                    continue
                if ok:
                    self._q.append((idx, frame))
                    self._next = idx + 1
                else:
                    self._eof = True
                self._cond.notify_all()

    def _pop(self):
        with self._cond:
            while not self._q and not self._eof and self._alive:
                self._cond.wait(0.5)
            if not self._q:
                return False, None
            idx, frame = self._q.popleft()
            self.pos = idx + 1
            self._cond.notify_all()
            return True, frame

    def _reader(self):
        fails = 0
        while self._alive:
            ok, frame = self.cap.read()
            if not ok:
                fails += 1
                if fails > 100:
                    break
                time.sleep(0.01)
                continue
            fails = 0
            with self._cond:
                self._latest, self._seq = frame, self._seq + 1
                self._cond.notify_all()
        with self._cond:
            self._alive = False
            self._cond.notify_all()

    def read(self):
        if self.live:
            with self._cond:
                if self._seq == self._seen:
                    self._cond.wait(timeout=2.0)
                if self._seq == self._seen:
                    return False, None
                self._seen = self._seq
                self.pos += 1
                return True, self._latest
        return self._pop()

    def grab(self):
        if self.live:
            return False
        ok, _ = self._pop()
        return ok

    def seek(self, idx):
        if self.live or self.total <= 0:
            return
        idx = max(0, min(int(idx), self.total - 1))
        with self._cond:
            self._seek_req = idx
            self._q.clear()
            self._eof = False
            self.pos = idx
            self._cond.notify_all()

    def release(self):
        with self._cond:
            self._alive = False
            self._cond.notify_all()
        self._thr.join(timeout=2.0)
        try:
            self.cap.release()
        except Exception:
            pass


class _ImageSource:
    def __init__(self, cv2, files, fps=2.0):
        self.cv2 = cv2
        self.files = files
        self.fps = fps
        self.total = len(files)
        self.pos = 0
        self.live = False
        self.name = Path(files[0]).parent.name if len(files) > 1 else Path(files[0]).name

    def current_name(self):
        return Path(self.files[max(0, self.pos - 1)]).name

    def read(self):
        while self.pos < self.total:
            f = self.files[self.pos]
            self.pos += 1
            img = self.cv2.imdecode(_read_bytes(f), self.cv2.IMREAD_COLOR)   # unicode-safe on Windows
            if img is not None:
                return True, img
        return False, None

    def grab(self):
        if self.pos < self.total:
            self.pos += 1
            return True
        return False

    def seek(self, idx):
        self.pos = max(0, min(int(idx), self.total - 1))

    def release(self):
        pass


_CLAHE = {}


def enhance_frame(cv2, img, clip=2.5):
    """Gray-world white balance + CLAHE on luminance.
    Removes the blue/green underwater cast and lifts dark / hazy frames so they look
    like the (dry, well-lit) training images. On already-clean frames it is nearly neutral."""
    import numpy as np
    small = cv2.resize(img, (64, 36), interpolation=cv2.INTER_AREA)
    means = small.reshape(-1, 3).mean(0) + 1e-6
    gain = np.clip(means.mean() / means, 0.5, 3.0)       # cap gains so noise isn't blown up
    ramp = np.arange(256, dtype=np.float32)
    lut = np.stack([np.clip(ramp * g, 0, 255) for g in gain], axis=1).astype(np.uint8).reshape(256, 1, 3)
    balanced = cv2.LUT(img, lut)
    lab = cv2.cvtColor(balanced, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = _CLAHE.get(clip)
    if clahe is None:
        clahe = _CLAHE[clip] = cv2.createCLAHE(clipLimit=clip, tileGridSize=(8, 8))
    return cv2.cvtColor(cv2.merge([clahe.apply(l), a, b]), cv2.COLOR_LAB2BGR)


ENV_AUTO, ENV_AIR, ENV_WATER, ENV_OFF = "Auto (air / underwater)", "Air", "Underwater", "No correction"


def scene_stats(cv2, img):
    """Return (red ratio, brightness). Underwater footage loses red light first, so
    red / max(green, blue) drops well below ~0.7; in-air scenes sit around 0.85–1.15."""
    small = cv2.resize(img, (64, 36), interpolation=cv2.INTER_AREA).reshape(-1, 3)
    b, g, r = small.mean(0)
    return float(r / max(g, b, 1.0)), float(small.mean())


def camera_geometry(cx, cy, bw, bh, w, h, hfov_deg, yaw_off=0.0, pitch_off=0.0, dock_size_m=1.0):
    """Pinhole model for the forward-facing nose camera.
    Returns (bearing_deg, elevation_deg, range_m). Bearing + = target to starboard (right),
    elevation + = target above the camera axis. Range from the apparent size of the dock."""
    import math
    f = (w / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)          # focal length in pixels
    bearing = math.degrees(math.atan((cx - w / 2.0) / f)) + yaw_off
    elevation = -math.degrees(math.atan((cy - h / 2.0) / f)) + pitch_off
    size_px = max(bw, bh, 1.0)
    rng = dock_size_m * f / size_px
    return bearing, elevation, rng


def _iou(a, b):
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    u = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / u if u > 0 else 0.0


def _read_bytes(path):
    import numpy as np
    return np.fromfile(str(path), dtype=np.uint8)


# ============================================================ worker thread
class DetectionWorker(QThread):
    frame_ready = Signal(QImage, dict)
    status = Signal(str, str)           # text, level: info|ok|warn|error
    opened = Signal(dict)               # source info
    ended = Signal(str)                 # reason
    recording = Signal(str)             # path of finished recording ("" if none)

    def __init__(self, parent=None):
        super().__init__(parent)
        # live-tunable parameters (read every frame)
        self.conf = 0.25
        self.iou = 0.45
        self.imgsz = 0                  # 0 = use the size the model was trained at
        self.speed = 1.0                # 0 -> as fast as possible
        self.realtime = True            # drop frames to stay real-time on video files
        self.show_guide = True
        self.environment = ENV_AUTO     # Auto / Air / Underwater / No correction
        self.show_enhanced = True       # display the corrected frame when correction is used
        self.distant = True             # 2nd pass at higher resolution when nothing is found
        self.track = True               # hysteresis + coasting through brief misses
        self.capture = False            # save frames + pre-labels for retraining
        self.capture_every = 10
        self.capture_dir = None
        self.loop = False
        self.record = False
        self.record_dir = RECORD_DIR_DEFAULT
        # source
        self.kind = SRC_VIDEO
        self.target = ""
        self.model_path = str(MODEL_DEFAULT)
        self.force_cpu = False
        self.rotate180 = False          # camera mounted upside-down
        self.start_frame = 0
        # control flags
        self._stop = False
        self._paused = False
        self._step = False
        self._seek = None
        self._reprocess = False

    # ---- control API (GUI thread)
    def stop(self):
        self._stop = True

    def set_paused(self, p):
        self._paused = bool(p)

    def is_paused(self):
        return self._paused

    def step(self):
        self._step = True

    def seek(self, idx):
        self._seek = int(idx)

    def reprocess(self):
        """Re-run detection on the frame on screen (e.g. after a threshold change while paused)."""
        self._reprocess = True

    # ---- helpers
    def _load_model(self):
        import numpy as np
        import torch
        from ultralytics import YOLO as _Detector   # third-party detector backend
        path = str(self.model_path)
        if not Path(path).is_file():
            raise RuntimeError(f"Model file not found:\n{path}")
        cuda = (not self.force_cpu) and torch.cuda.is_available()
        if cuda:
            torch.backends.cudnn.benchmark = True        # fastest kernels for fixed input sizes
        key = (path, os.path.getmtime(path), cuda)
        if key not in _MODEL_CACHE:
            self.status.emit("Loading detection model" + (" on GPU…" if cuda else "…"), "info")
            model = _Detector(path, task="detect")
            trained = 640
            try:
                trained = int((getattr(model, "ckpt", None) or {}).get("train_args", {}).get("imgsz") or
                              model.overrides.get("imgsz") or 640)
            except Exception:
                pass
            dev = 0 if cuda else "cpu"
            sizes = {trained, min(1280, int(round(trained * 2 / 32)) * 32)}
            if self.imgsz:
                sizes.add(int(self.imgsz))
            for sz in sorted(sizes):                      # warm-up every size we will use
                for _ in range(2 if cuda else 1):
                    model.predict(np.zeros((sz, sz, 3), np.uint8), imgsz=sz, device=dev, half=cuda, verbose=False)
            _MODEL_CACHE.clear()
            _MODEL_CACHE[key] = (model, trained)
        model, trained = _MODEL_CACHE[key]
        if cuda:
            dev_name = f"{torch.cuda.get_device_name(0)} · GPU FP16"
        else:
            dev_name = "CPU"
        hint = ""
        if not cuda and not self.force_cpu:
            if torch.version.cuda is None and shutil.which("nvidia-smi"):
                hint = ("NVIDIA GPU found, but the installed PyTorch is CPU-only. "
                        "Run install_gpu_support.bat once, then restart.")
            elif torch.version.cuda is not None and shutil.which("nvidia-smi"):
                hint = "PyTorch has CUDA support but cannot use the GPU — update the NVIDIA driver."
        return model, (0 if cuda else "cpu"), cuda, dev_name, trained, hint

    def _open_source(self, cv2):
        if self.kind in (SRC_IMAGE, SRC_FOLDER):
            p = Path(self.target)
            if self.kind == SRC_FOLDER:
                files = sorted(str(f) for f in p.iterdir() if f.suffix.lower() in IMAGE_EXTS) if p.is_dir() else []
                if not files:
                    raise RuntimeError(f"No images found in folder:\n{p}")
            else:
                if not p.is_file():
                    raise RuntimeError(f"Image not found:\n{p}")
                files = [str(p)]
            return _ImageSource(cv2, files)
        if self.kind == SRC_VIDEO and not Path(self.target).is_file():
            raise RuntimeError(f"Video file not found:\n{self.target}")
        return _VideoSource(cv2, self.kind, self.target)

    def _annotate(self, cv2, frame, dets, best, coasting=None):
        h, w = frame.shape[:2]
        out = frame.copy()
        t = max(1, int(round(min(w, h) / 320)))
        if self.show_guide:
            c = (150, 150, 150)
            cv2.line(out, (w // 2 - 14 * t, h // 2), (w // 2 + 14 * t, h // 2), c, t, cv2.LINE_AA)
            cv2.line(out, (w // 2, h // 2 - 14 * t), (w // 2, h // 2 + 14 * t), c, t, cv2.LINE_AA)
        for d in dets:
            x1, y1, x2, y2 = (int(v) for v in d["xyxy"])
            primary = d is best
            col = (64, 220, 0) if primary else (255, 200, 0)      # BGR: green primary, cyan others
            cv2.rectangle(out, (x1, y1), (x2, y2), col, 2 * t if primary else t, cv2.LINE_AA)
            # corner brackets on primary target
            if primary:
                L = max(8, int(0.12 * min(x2 - x1, y2 - y1)))
                for (px, py, dx, dy) in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)):
                    cv2.line(out, (px, py), (px + dx * L, py), col, 3 * t, cv2.LINE_AA)
                    cv2.line(out, (px, py), (px, py + dy * L), col, 3 * t, cv2.LINE_AA)
            label = f"{d['name'].upper()} {d['conf']:.2f}" + (" TRK" if d.get("_trk") else "")
            fs = 0.45 * t
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, fs, t)
            ly = y1 - 4 if y1 - th - 8 > 0 else y1 + th + 6
            cv2.rectangle(out, (x1, ly - th - 4), (x1 + tw + 6, ly + 3), col, -1)
            cv2.putText(out, label, (x1 + 3, ly), cv2.FONT_HERSHEY_SIMPLEX, fs, (10, 10, 10), t, cv2.LINE_AA)
        if coasting is not None:
            x1, y1, x2, y2 = (int(v) for v in coasting["xyxy"])
            col = (0, 200, 255)
            for i in range(x1, x2, 14 * t):
                cv2.line(out, (i, y1), (min(i + 7 * t, x2), y1), col, t, cv2.LINE_AA)
                cv2.line(out, (i, y2), (min(i + 7 * t, x2), y2), col, t, cv2.LINE_AA)
            for j in range(y1, y2, 14 * t):
                cv2.line(out, (x1, j), (x1, min(j + 7 * t, y2)), col, t, cv2.LINE_AA)
                cv2.line(out, (x2, j), (x2, min(j + 7 * t, y2)), col, t, cv2.LINE_AA)
            cv2.putText(out, "HOLD", (x1 + 4, y1 + int(16 * t)), cv2.FONT_HERSHEY_SIMPLEX, 0.45 * t, col, t, cv2.LINE_AA)
        if best is not None and self.show_guide:
            cx, cy = int(best["cx"]), int(best["cy"])
            cv2.line(out, (w // 2, h // 2), (cx, cy), (0, 165, 255), t, cv2.LINE_AA)
            cv2.circle(out, (cx, cy), 4 * t, (0, 165, 255), -1, cv2.LINE_AA)
        return out

    # ---- main loop
    def run(self):
        try:
            import cv2
        except Exception:
            self.status.emit("OpenCV is not installed — run: pip install opencv-python", "error")
            self.ended.emit("error")
            return
        try:
            model, device, half, dev_name, trained_size, gpu_hint = self._load_model()
        except ImportError:
            self.status.emit("Detection libraries missing — run: pip install -r requirements_camera_detection.txt", "error")
            self.ended.emit("error")
            return
        except Exception as e:
            self.status.emit(f"Model load failed: {e}", "error")
            self.ended.emit("error")
            return

        try:
            src = self._open_source(cv2)
        except Exception as e:
            self.status.emit(str(e), "error")
            self.ended.emit("error")
            return

        is_images = isinstance(src, _ImageSource)
        self.opened.emit({"name": src.name, "fps": src.fps, "total": src.total,
                          "live": src.live, "images": is_images, "device": dev_name,
                          "trained_size": trained_size, "gpu": bool(half), "gpu_hint": gpu_hint})
        if gpu_hint:
            self.status.emit(gpu_hint, "warn")
        else:
            self.status.emit("Running on GPU" if half else "Running on CPU", "ok")
        if self.start_frame:
            src.seek(self.start_frame)

        writer, writer_path, writer_buf, writer_t0 = None, None, [], None
        last_frame = None
        streak = 0
        last_box, coast = None, 0
        red_ema, env_name = None, "—"
        t_cap, src_res = time.time(), (0, 0)
        cap_count, cap_seen, cap_root = 0, 0, None
        proc_times = deque(maxlen=30)
        t0, f0 = time.perf_counter(), src.pos
        reason = "stopped"
        miss = 0

        try:
            while not self._stop:
                # ---------- seek / pause / step
                if self._seek is not None:
                    src.seek(self._seek)
                    self._seek = None
                    streak = 0
                    last_box, coast = None, 0
                    t0, f0 = time.perf_counter(), src.pos
                    if self._paused:
                        self._step = True            # show the frame we landed on
                reprocess = False
                if self._paused and not self._step:
                    if self._reprocess and last_frame is not None:
                        self._reprocess = False
                        reprocess = True
                    else:
                        time.sleep(0.02)
                        t0, f0 = time.perf_counter(), src.pos
                        continue
                stepping = self._step
                self._step = False

                speed = self.speed
                realtime_drop = (self.realtime and not self.record and not src.live
                                 and not is_images and speed > 0 and not stepping)

                # ---------- read frame
                if reprocess:
                    frame = last_frame
                else:
                    if realtime_drop:
                        due = f0 + (time.perf_counter() - t0) * src.fps * speed
                        lag = int(due - src.pos)
                        for _ in range(min(lag, 60)):
                            if not src.grab():
                                break
                    ok, frame = src.read()
                    if not ok:
                        if src.live:
                            miss += 1
                            if miss > 5:
                                reason = "lost"
                                break
                            time.sleep(0.02)
                            continue
                        if self.loop and src.total > 0 and not is_images:
                            src.seek(0)
                            t0, f0 = time.perf_counter(), 0
                            continue
                        reason = "end"
                        break
                    miss = 0
                    t_cap = time.time()
                    if self.rotate180:
                        frame = cv2.rotate(frame, cv2.ROTATE_180)
                    src_res = (frame.shape[1], frame.shape[0])
                    if max(src_res) > WORK_MAX:
                        k = WORK_MAX / max(src_res)
                        frame = cv2.resize(frame, (int(src_res[0] * k), int(src_res[1] * k)),
                                           interpolation=cv2.INTER_AREA)
                    last_frame = frame

                # ---------- inference
                ti = time.perf_counter()
                h, w = frame.shape[:2]
                base = int(self.imgsz) or trained_size
                thr = self.conf
                red, bright = scene_stats(cv2, frame)
                red_ema = red if red_ema is None else 0.8 * red_ema + 0.2 * red   # smoothed, no flicker
                env = self.environment
                underwater = red_ema < 0.70
                dark = bright < 60
                if env == ENV_WATER:
                    use_corr, env_name = True, "UNDERWATER"
                elif env == ENV_AIR:
                    use_corr, env_name = dark, ("AIR · LOW LIGHT" if dark else "AIR")
                elif env == ENV_OFF:
                    use_corr, env_name = False, "NO CORRECTION"
                else:
                    use_corr = underwater or dark
                    env_name = ("UNDERWATER" if underwater else "AIR") + (" · LOW LIGHT" if dark else "")
                _corr = []

                def corrected():
                    if not _corr:
                        _corr.append(enhance_frame(cv2, frame))
                    return _corr[0]

                proc = corrected() if use_corr else frame

                def detect(img, size):
                    r = model.predict(img, conf=min(RAW_FLOOR, thr), iou=self.iou, imgsz=size,
                                      device=device, half=half, verbose=False)[0]
                    out = []
                    if r.boxes is not None and len(r.boxes):
                        xyxy = r.boxes.xyxy.cpu().numpy()
                        confs = r.boxes.conf.cpu().numpy()
                        clss = r.boxes.cls.cpu().numpy().astype(int)
                        for (x1, y1, x2, y2), c, k in zip(xyxy, confs, clss):
                            cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                            out.append({
                                "xyxy": (float(x1), float(y1), float(x2), float(y2)),
                                "conf": float(c), "name": str(r.names.get(int(k), k)),
                                "cx": float(cx), "cy": float(cy),
                                "offx": float((cx - w / 2) / (w / 2)), "offy": float((cy - h / 2) / (h / 2)),
                                "area": float((x2 - x1) * (y2 - y1) / (w * h) * 100.0),
                            })
                    return out

                raw = detect(proc, base)
                pass_name = f"{base}" + (" corr" if use_corr else "")
                top = max((d["conf"] for d in raw), default=0.0)
                if top < thr and env != ENV_OFF:
                    # Nothing found: try the other processing (raw <-> corrected) so a wrong
                    # air/underwater guess can never cost a detection.
                    alt = frame if use_corr else corrected()
                    raw2 = detect(alt, base)
                    top2 = max((d["conf"] for d in raw2), default=0.0)
                    if top2 > top:
                        raw, top, proc = raw2, top2, alt
                        use_corr = not use_corr
                        pass_name = f"{base}" + (" corr" if use_corr else "")
                if self.distant and top < thr:
                    # Distant / small dock: look again at higher resolution
                    big = min(1280, int(round(base * 2 / 32)) * 32)
                    raw2 = detect(proc, big)
                    top2 = max((d["conf"] for d in raw2), default=0.0)
                    if top2 > top:
                        raw, top = raw2, top2
                        pass_name = f"{big}" + (" corr" if use_corr else "")
                dets = [d for d in raw if d["conf"] >= thr]
                tracked = False
                if not dets and self.track and last_box is not None:
                    cand = [d for d in raw if d["conf"] >= thr * TRACK_FACTOR and _iou(d["xyxy"], last_box["xyxy"]) > 0.1]
                    if cand:
                        dets = [dict(max(cand, key=lambda d: d["conf"]), _trk=True)]
                        tracked = True
                        pass_name += " +track"
                infer_ms = (time.perf_counter() - ti) * 1000.0
                best = max(dets, key=lambda d: d["conf"]) if dets else None
                coasting = None
                if not reprocess:
                    if best is not None:
                        streak += 1
                        last_box, coast = best, 0
                    elif self.track and last_box is not None and coast < COAST_FRAMES:
                        coast += 1
                        coasting = last_box            # hold last position briefly, lock kept
                    else:
                        streak, last_box, coast = 0, None, 0

                shown = proc if (use_corr and self.show_enhanced) else frame
                annotated = self._annotate(cv2, shown, dets, best, coasting)

                # ---------- training-frame capture (original frame + pre-labels)
                if self.capture and not reprocess and self.capture_dir:
                    cap_seen += 1
                    if cap_seen % max(1, int(self.capture_every)) == 1 or self.capture_every <= 1:
                        if cap_root is None or Path(cap_root).parent != Path(self.capture_dir):
                            cap_root = Path(self.capture_dir) / f"training_capture_{time.strftime('%Y%m%d_%H%M%S')}"
                            (cap_root / "images").mkdir(parents=True, exist_ok=True)
                            (cap_root / "labels").mkdir(parents=True, exist_ok=True)
                            names = sorted({d["name"] for d in raw}) or ["dock"]
                            (cap_root / "classes.txt").write_text("\n".join(names) + "\n")
                            (cap_root / "data.yaml").write_text(
                                f"train: images\nval: images\nnc: {len(names)}\nnames: {names}\n")
                            self.status.emit(f"Capturing frames → {cap_root.name}", "warn")
                        stem = f"{Path(str(src.name)).stem.replace(' ', '_')}_f{max(0, src.pos - 1):06d}"
                        ok_w, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
                        if ok_w:
                            buf.tofile(str(cap_root / "images" / f"{stem}.jpg"))
                            with open(cap_root / "labels" / f"{stem}.txt", "w") as lf:
                                for d in dets:
                                    x1, y1, x2, y2 = d["xyxy"]
                                    lf.write(f"0 {((x1 + x2) / 2) / w:.6f} {((y1 + y2) / 2) / h:.6f} "
                                             f"{(x2 - x1) / w:.6f} {(y2 - y1) / h:.6f}\n")
                            cap_count += 1
                elif not self.capture:
                    cap_root, cap_seen = None, 0

                # ---------- recording (every processed frame, correct timing)
                if self.record and not reprocess:
                    if writer is None:
                        if src.live:
                            # measure real frame rate first so the file plays back at true speed
                            writer_buf.append(annotated)
                            writer_t0 = writer_t0 or time.perf_counter()
                            if time.perf_counter() - writer_t0 >= 1.5 and len(writer_buf) >= 3:
                                fps_w = max(1.0, min(src.fps, len(writer_buf) / (time.perf_counter() - writer_t0)))
                                writer, writer_path = self._open_writer(cv2, src, w, h, fps_w)
                                for b in writer_buf:
                                    writer.write(b)
                                writer_buf = []
                        else:
                            writer, writer_path = self._open_writer(cv2, src, w, h, src.fps)
                    if writer is not None:
                        writer.write(annotated)
                elif not self.record and writer is not None:
                    writer.release()
                    self.recording.emit(str(writer_path))
                    writer, writer_path, writer_buf, writer_t0 = None, None, [], None

                # ---------- publish
                now = time.perf_counter()
                proc_times.append(now)
                proc_fps = ((len(proc_times) - 1) / (proc_times[-1] - proc_times[0])
                            if len(proc_times) > 1 and proc_times[-1] > proc_times[0] else 0.0)
                rgb = cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB)
                qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
                idx = max(0, src.pos - 1)
                info = {
                    "frame": idx, "total": src.total, "fps": src.fps, "t": idx / src.fps if src.fps else 0.0,
                    "w": w, "h": h, "dets": dets, "best": best, "streak": streak,
                    "infer_ms": infer_ms, "proc_fps": proc_fps, "live": src.live,
                    "reprocess": reprocess, "recording": writer is not None or bool(writer_buf),
                    "image_name": src.current_name() if is_images else "",
                    "raw_top": top, "pass": pass_name, "tracked": tracked, "env": env_name,
                    "t_capture": t_cap, "src_w": src_res[0], "src_h": src_res[1],
                    "coasting": coasting is not None, "capture_count": cap_count,
                    "capture_dir": str(cap_root) if cap_root else "",
                }
                self.frame_ready.emit(qimg, info)

                if is_images and src.total == 1 and not reprocess:
                    self._paused = True
                    self.status.emit("Image processed — adjust thresholds to re-run", "ok")
                    continue
                if stepping or reprocess:
                    continue

                # ---------- pacing
                if src.live:
                    continue
                rate = src.fps * speed if speed > 0 else 0
                if is_images:
                    rate = 2.0 * speed if speed > 0 else 0
                if rate > 0:
                    due_t = t0 + (src.pos - f0) / rate
                    delay = due_t - time.perf_counter()
                    if delay > 0:
                        time.sleep(min(delay, 1.0))
                    elif delay < -1.0 and not realtime_drop:
                        t0, f0 = time.perf_counter(), src.pos   # don't try to "catch up" in every-frame mode
        except Exception as e:
            self.status.emit(f"Processing error: {e}", "error")
            reason = "error"
        finally:
            src.release()
            if writer is not None:
                writer.release()
                self.recording.emit(str(writer_path))
        self.ended.emit(reason)

    def _open_writer(self, cv2, src, w, h, fps):
        out_dir = Path(self.record_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(str(src.name)).stem.replace(" ", "_") or "camera"
        ts = time.strftime("%Y%m%d_%H%M%S")
        for ext, codec in ((".mp4", "mp4v"), (".avi", "XVID"), (".avi", "MJPG")):
            path = out_dir / f"{stem}_detection_{ts}{ext}"
            wr = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*codec), float(fps), (w, h))
            if wr.isOpened():
                self.status.emit(f"Recording → {path.name}", "warn")
                return wr, path
            wr.release()
        raise RuntimeError("Could not open a video writer (no mp4v/XVID/MJPG codec available)")


# ============================================================ video view
class DetectionVideoView(QWidget):
    file_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAcceptDrops(True)
        self.image = None
        self.bg = QColor(8, 12, 20)
        self.fg = QColor(120, 140, 165)
        self.hud = ""
        self.hud2 = ""
        self.rec = False

    def set_colors(self, bg, fg):
        self.bg, self.fg = QColor(bg), QColor(fg)
        self.update()

    def set_image(self, img):
        self.image = img
        self.update()

    def clear(self):
        self.image = None
        self.hud = ""
        self.rec = False
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), self.bg)
        if self.image is None or self.image.isNull():
            p.setPen(self.fg)
            f = QFont("Google Sans", 10)
            f.setBold(True)
            p.setFont(f)
            p.drawText(self.rect(), Qt.AlignCenter,
                       "NO SOURCE\n\nOpen a video, camera, stream or image\n— or drop a file here —")
            return
        iw, ih = self.image.width(), self.image.height()
        s = min(self.width() / iw, self.height() / ih)
        dw, dh = iw * s, ih * s
        r = QRectF((self.width() - dw) / 2, (self.height() - dh) / 2, dw, dh)
        p.drawImage(r, self.image)
        if self.hud:
            f = QFont("Consolas", 9)
            f.setBold(True)
            p.setFont(f)
            tw = p.fontMetrics().horizontalAdvance(self.hud) + 14
            box = QRectF(r.left() + 8, r.top() + 8, tw, 22)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 150))
            p.drawRoundedRect(box, 4, 4)
            p.setPen(QColor(230, 240, 255))
            p.drawText(box, Qt.AlignCenter, self.hud)
        if self.hud2:
            f = QFont("Consolas", 10)
            f.setBold(True)
            p.setFont(f)
            tw = p.fontMetrics().horizontalAdvance(self.hud2) + 16
            box = QRectF(r.left() + (r.width() - tw) / 2, r.bottom() - 32, tw, 24)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 160))
            p.drawRoundedRect(box, 4, 4)
            p.setPen(QColor(255, 196, 0))
            p.drawText(box, Qt.AlignCenter, self.hud2)
        if self.rec:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 59, 48))
            p.drawEllipse(QRectF(r.right() - 64, r.top() + 14, 10, 10))
            p.setPen(QColor(255, 255, 255))
            f = QFont("Google Sans", 9)
            f.setBold(True)
            p.setFont(f)
            p.drawText(QRectF(r.right() - 50, r.top() + 8, 44, 22), Qt.AlignVCenter | Qt.AlignLeft, "REC")

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        urls = e.mimeData().urls()
        if urls:
            self.file_dropped.emit(urls[0].toLocalFile())


# ============================================================ theme
def apply_camera_detection_theme(parent, theme):
    if theme in ("cockpit", "dark"):
        c = dict(bg="#121214", card="#1E1E22", border="#323238", text="#FFFFFF", sub="#A0A0A0",
                 btn="#26262B", btn_b="#3A3A42", btn_t="#E0E0E0", btn_h="#303037", inp="#1A1A1E",
                 tbl="#1A1A1E", alt="#222228", hdr="#26262D", grid="#2C2C32", view="#08080A", accent="#FF9100")
    elif theme == "ocean":
        c = dict(bg="#050B14", card="#0F223C", border="#1C3B65", text="#E2F1FF", sub="#8EB7E6",
                 btn="#0F223C", btn_b="#1C3B65", btn_t="#E2F1FF", btn_h="#152F53", inp="#0A1625",
                 tbl="#0A1625", alt="#0F223C", hdr="#152F53", grid="#1C3B65", view="#02060C", accent="#00E5FF")
    else:
        c = dict(bg="#F4F7FB", card="#FFFFFF", border="#CBD5E1", text="#0A2540", sub="#5A738E",
                 btn="#FFFFFF", btn_b="#CBD5E1", btn_t="#334155", btn_h="#F8FAFC", inp="#FFFFFF",
                 tbl="#FFFFFF", alt="#F8FAFC", hdr="#F1F5F9", grid="#E2E8F0", view="#0B1220", accent="#0284C7")
    parent._camdet_colors = c
    root = getattr(parent, "camdet_container", None)
    if root is None:
        return
    root.setStyleSheet(f"""
        QWidget#CamDetContainer {{ background-color: {c['bg']}; }}
        QFrame#CamDetCard {{ background-color: {c['card']}; border: 1px solid {c['border']}; border-radius: 8px; }}
        QLabel {{ color: {c['text']}; background: transparent; border: none; font-family: 'Google Sans', sans-serif; }}
        QLabel#CamDetTitle {{ color: {c['sub']}; font-size: 10px; font-weight: bold; letter-spacing: 1px; }}
        QLabel#CamDetPageTitle {{ color: {c['text']}; font-size: 15px; font-weight: bold; letter-spacing: 1.5px; }}
        QLabel#CamDetSub {{ color: {c['sub']}; font-size: 10px; }}
        QLabel#CamDetKey {{ color: {c['sub']}; font-size: 9px; font-weight: bold; letter-spacing: 0.8px; }}
        QLabel#CamDetVal {{ color: {c['text']}; font-size: 15px; font-weight: bold; font-family: 'Consolas', monospace; }}
        QLabel#CamDetTime {{ color: {c['sub']}; font-size: 11px; font-family: 'Consolas', monospace; }}
        QPushButton {{ background-color: {c['btn']}; color: {c['btn_t']}; border: 1px solid {c['btn_b']};
                       border-radius: 5px; padding: 6px 10px; font-size: 10px; font-weight: bold; }}
        QPushButton:hover {{ background-color: {c['btn_h']}; border-color: {c['accent']}; }}
        QPushButton:disabled {{ color: {c['sub']}; }}
        QPushButton#CamDetPrimary {{ background-color: {c['accent']}; color: #0A0A0A; border: none; }}
        QPushButton#CamDetPrimary:hover {{ background-color: {c['accent']}; }}
        QPushButton:checked {{ background-color: #B3261E; color: #FFFFFF; border-color: #B3261E; }}
        QComboBox, QLineEdit, QSpinBox {{ background-color: {c['inp']}; color: {c['text']}; border: 1px solid {c['btn_b']};
                       border-radius: 4px; padding: 4px 6px; font-size: 10px; }}
        QComboBox QAbstractItemView {{ background-color: {c['card']}; color: {c['text']};
                       selection-background-color: {c['btn_h']}; }}
        QCheckBox {{ color: {c['text']}; font-size: 10px; background: transparent; }}
        QCheckBox::indicator {{ width: 13px; height: 13px; border: 1px solid {c['btn_b']};
                       border-radius: 3px; background: {c['inp']}; }}
        QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}
        QSlider::groove:horizontal {{ height: 4px; background: {c['grid']}; border-radius: 2px; }}
        QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 2px; }}
        QSlider::handle:horizontal {{ background: {c['text']}; width: 12px; margin: -5px 0; border-radius: 6px; }}
        QTableWidget {{ background-color: {c['tbl']}; alternate-background-color: {c['alt']}; color: {c['text']};
                        border: 1px solid {c['border']}; border-radius: 6px; gridline-color: {c['grid']};
                        font-family: 'Consolas', monospace; font-size: 10px; }}
        QHeaderView::section {{ background-color: {c['hdr']}; color: {c['sub']}; padding: 4px 6px;
                        font-size: 9.5px; font-weight: bold; border: 1px solid {c['border']}; }}
        QScrollArea {{ background: transparent; border: none; }}
        QSplitter::handle {{ background: {c['bg']}; }}
    """)
    view = getattr(parent, "camdet_view", None)
    if view is not None:
        view.set_colors(c["view"], c["sub"])
    if hasattr(parent, "_camdet_refresh_status_pill"):
        parent._camdet_refresh_status_pill()


# ============================================================ page
def create_camera_detection_page(parent):
    parent.apply_camera_detection_theme = lambda theme: apply_camera_detection_theme(parent, theme)
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
    state = {
        "worker": None, "source_info": None, "log": [], "slider_drag": False,
        "status": ("IDLE", "info"), "pending": None, "ema": None, "retired": set(),
        "raw_hist": deque(maxlen=45), "seq": 0,
    }
    parent.camera_detection_state = {"detected": False, "locked": False, "running": False}   # for other modules

    page = QWidget()
    page.setObjectName("CameraDetectionPage")
    outer = QVBoxLayout(page)
    outer.setContentsMargins(0, 0, 0, 0)

    container = QWidget()
    container.setObjectName("CamDetContainer")
    parent.camdet_container = container
    outer.addWidget(container)
    root = QVBoxLayout(container)
    root.setContentsMargins(12, 10, 12, 10)
    root.setSpacing(10)

    def card(title=None):
        f = QFrame()
        f.setObjectName("CamDetCard")
        lay = QVBoxLayout(f)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(7)
        if title:
            t = QLabel(title)
            t.setObjectName("CamDetTitle")
            lay.addWidget(t)
        return f, lay

    def lbl(text, name="CamDetSub"):
        l = QLabel(text)
        l.setObjectName(name)
        return l

    # ---------------- header
    hdr = QHBoxLayout()
    title = lbl("CAMERA DETECTION", "CamDetPageTitle")
    hdr.addWidget(title)
    hdr.addSpacing(12)
    pill = QLabel("IDLE")
    pill.setAlignment(Qt.AlignCenter)
    pill.setFixedHeight(22)
    pill.setMinimumWidth(90)
    hdr.addWidget(pill)
    lbl_device = lbl("")
    hdr.addSpacing(8)
    hdr.addWidget(lbl_device)
    hdr.addStretch()
    lbl_source_name = lbl("")
    hdr.addWidget(lbl_source_name)
    root.addLayout(hdr)

    splitter = QSplitter(Qt.Horizontal)
    splitter.setChildrenCollapsible(False)
    root.addWidget(splitter, 1)

    # ---------------- left: video + transport + log
    left = QWidget()
    left_l = QVBoxLayout(left)
    left_l.setContentsMargins(0, 0, 0, 0)
    left_l.setSpacing(8)

    vcard, vlay = card()
    vlay.setContentsMargins(6, 6, 6, 6)
    view = DetectionVideoView()
    parent.camdet_view = view
    vlay.addWidget(view, 1)

    trans = QHBoxLayout()
    trans.setSpacing(6)
    btn_play = QPushButton("▶  START")
    btn_play.setObjectName("CamDetPrimary")
    btn_play.setMinimumWidth(96)
    btn_stop = QPushButton("■  STOP")
    btn_step = QPushButton("STEP ▸|")
    btn_step.setToolTip("Advance one frame (while paused)")
    seek = QSlider(Qt.Horizontal)
    seek.setRange(0, 0)
    seek.setEnabled(False)
    lbl_time = lbl("--:--.- / --:--.-", "CamDetTime")
    lbl_time.setMinimumWidth(130)
    lbl_time.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    cmb_speed = QComboBox()
    cmb_speed.addItems(["0.25×", "0.5×", "1×", "2×", "4×", "Max"])
    cmb_speed.setCurrentText("1×")
    cmb_speed.setToolTip("Playback speed (Max = process as fast as possible)")
    for w_ in (btn_play, btn_stop, btn_step):
        trans.addWidget(w_)
    trans.addWidget(seek, 1)
    trans.addWidget(lbl_time)
    trans.addWidget(cmb_speed)
    vlay.addLayout(trans)
    left_l.addWidget(vcard, 1)

    lcard, llay = card("DETECTION LOG")
    log_hdr = QHBoxLayout()
    lbl_log_count = lbl("0 detections")
    log_hdr.addWidget(lbl_log_count)
    log_hdr.addStretch()
    btn_export = QPushButton("EXPORT CSV")
    btn_clear = QPushButton("CLEAR")
    log_hdr.addWidget(btn_export)
    log_hdr.addWidget(btn_clear)
    llay.addLayout(log_hdr)
    cols = ["TIME", "FRAME", "SRC t (s)", "CONF", "CX px", "CY px", "OFF X", "OFF Y", "AREA %"]
    table = QTableWidget(0, len(cols))
    table.setHorizontalHeaderLabels(cols)
    table.verticalHeader().setVisible(False)
    table.setAlternatingRowColors(True)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    table.setMinimumHeight(150)
    table.setMaximumHeight(210)
    llay.addWidget(table)
    left_l.addWidget(lcard)
    splitter.addWidget(left)

    # ---------------- right: controls
    right_scroll = QScrollArea()
    right_scroll.setWidgetResizable(True)
    right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    right = QWidget()
    right.setObjectName("CamDetContainer")
    right_l = QVBoxLayout(right)
    right_l.setContentsMargins(0, 0, 4, 0)
    right_l.setSpacing(10)
    right_scroll.setWidget(right)
    right_scroll.setMinimumWidth(300)
    right_scroll.setMaximumWidth(380)
    splitter.addWidget(right_scroll)
    splitter.setStretchFactor(0, 1)
    splitter.setStretchFactor(1, 0)

    # SOURCE
    scard, slay = card("SOURCE")
    cmb_src = QComboBox()
    cmb_src.addItems([SRC_VIDEO, SRC_CAMERA, SRC_STREAM, SRC_IMAGE, SRC_FOLDER])
    slay.addWidget(cmb_src)
    path_row = QHBoxLayout()
    ed_path = QLineEdit()
    ed_path.setPlaceholderText("Choose a file…")
    btn_browse = QPushButton("BROWSE")
    path_row.addWidget(ed_path, 1)
    path_row.addWidget(btn_browse)
    path_w = QWidget()
    path_w.setLayout(path_row)
    path_row.setContentsMargins(0, 0, 0, 0)
    slay.addWidget(path_w)
    cam_row = QHBoxLayout()
    cam_row.setContentsMargins(0, 0, 0, 0)
    cam_row.addWidget(lbl("Camera index"))
    sp_cam = QSpinBox()
    sp_cam.setRange(0, 15)
    cam_row.addWidget(sp_cam)
    cam_row.addStretch()
    cam_w = QWidget()
    cam_w.setLayout(cam_row)
    slay.addWidget(cam_w)
    chk_loop = QCheckBox("Loop video")
    slay.addWidget(chk_loop)
    right_l.addWidget(scard)

    # DETECTION
    dcard, dlay = card("DETECTION")
    mrow = QHBoxLayout()
    mrow.setContentsMargins(0, 0, 0, 0)
    ed_model = QLineEdit()
    ed_model.setToolTip("Detection model weights (.pt)")
    btn_model = QPushButton("…")
    btn_model.setFixedWidth(34)
    mrow.addWidget(ed_model, 1)
    mrow.addWidget(btn_model)
    dlay.addWidget(lbl("Model file"))
    dlay.addLayout(mrow)
    gr = QHBoxLayout()
    gr.setContentsMargins(0, 0, 0, 0)
    gr.addWidget(lbl("Compute"))
    cmb_compute = QComboBox()
    cmb_compute.addItems(["Auto (GPU if available)", "CPU only"])
    cmb_compute.setToolTip("GPU uses CUDA with half precision (FP16). Takes effect on the next START.")
    gr.addWidget(cmb_compute, 1)
    dlay.addLayout(gr)

    def slider_row(name, lo, hi, val):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        k = lbl(name)
        k.setMinimumWidth(78)
        s = QSlider(Qt.Horizontal)
        s.setRange(lo, hi)
        s.setValue(val)
        v = lbl(f"{val / 100:.2f}")
        v.setMinimumWidth(34)
        row.addWidget(k)
        row.addWidget(s, 1)
        row.addWidget(v)
        dlay.addLayout(row)
        return s, v

    sl_conf, lv_conf = slider_row("Confidence", 1, 95, 25)
    sl_iou, lv_iou = slider_row("Overlap (NMS)", 10, 90, 45)
    irow = QHBoxLayout()
    irow.setContentsMargins(0, 0, 0, 0)
    irow.addWidget(lbl("Input size"))
    cmb_size = QComboBox()
    cmb_size.addItems(["Auto (model)", "320", "416", "480", "640", "800", "960"])
    cmb_size.setToolTip("Auto uses the resolution the model was trained at (recommended).")
    irow.addWidget(cmb_size, 1)
    dlay.addLayout(irow)
    prow = QHBoxLayout()
    prow.setContentsMargins(0, 0, 0, 0)
    prow.addWidget(lbl("Playback"))
    cmb_mode = QComboBox()
    cmb_mode.addItems(["Real-time (skip frames if slow)", "Every frame"])
    cmb_mode.setToolTip("Real-time keeps video at true speed by skipping frames when inference is slower "
                        "than the video.\nEvery frame processes all frames (slower than real-time on CPU).\n"
                        "Recording always processes every frame.")
    prow.addWidget(cmb_mode, 1)
    dlay.addLayout(prow)
    chk_guide = QCheckBox("Show centre guide & offset line".replace("&", "&&"))
    chk_guide.setChecked(True)
    dlay.addWidget(chk_guide)
    erow = QHBoxLayout()
    erow.setContentsMargins(0, 0, 0, 0)
    erow.addWidget(lbl("Environment"))
    cmb_env = QComboBox()
    cmb_env.addItems([ENV_AUTO, ENV_AIR, ENV_WATER, ENV_OFF])
    cmb_env.setToolTip("Auto: detects air vs underwater (blue/green cast) and low light every frame,\n"
                       "applies colour & contrast correction only when needed, and if nothing is found\n"
                       "also tries the other way. Works in air and underwater without switching.\n"
                       "Air / Underwater: force the mode.  No correction: raw frames only.")
    erow.addWidget(cmb_env, 1)
    dlay.addLayout(erow)
    chk_show_enh = QCheckBox("Show corrected image")
    chk_show_enh.setToolTip("Display what the detector actually sees")
    chk_distant = QCheckBox("Distant target search")
    chk_distant.setToolTip("If nothing is found, the frame is checked again at double resolution.\n"
                           "Finds small / far-away docks; costs extra time only on frames with no detection.")
    chk_track = QCheckBox("Target tracking (hold lock)")
    chk_track.setToolTip("Once locked, weaker detections overlapping the last position are accepted and\n"
                         f"the target is held for up to {COAST_FRAMES} frames if it drops out.")
    for cb in (chk_show_enh, chk_distant, chk_track):
        cb.setChecked(True)
        dlay.addWidget(cb)
    right_l.addWidget(dcard)

    # TARGET
    tcard, tlay = card("TARGET")
    lbl_lock = QLabel("SEARCHING")
    lbl_lock.setAlignment(Qt.AlignCenter)
    lbl_lock.setFixedHeight(30)
    tlay.addWidget(lbl_lock)
    grid = QGridLayout()
    grid.setHorizontalSpacing(10)
    grid.setVerticalSpacing(2)
    readouts = {}
    for i, (key, name) in enumerate([("conf", "CONFIDENCE"), ("count", "DETECTIONS"),
                                     ("brg", "BEARING"), ("elv", "ELEVATION"),
                                     ("rng", "RANGE (EST.)"), ("area", "BOX AREA"),
                                     ("offx", "OFFSET X"), ("offy", "OFFSET Y"),
                                     ("streak", "LOCK FRAMES"), ("compute", "COMPUTE"),
                                     ("raw", "RAW TOP SCORE"), ("pass", "PASS"),
                                     ("env", "ENVIRONMENT"), ("res", "FRAME"),
                                     ("infer", "INFERENCE"), ("fps", "PROCESS FPS")]):
        r_, c_ = divmod(i, 2)
        grid.addWidget(lbl(name, "CamDetKey"), r_ * 2, c_)
        v = lbl("—", "CamDetVal")
        grid.addWidget(v, r_ * 2 + 1, c_)
        readouts[key] = v
    tlay.addLayout(grid)
    for k in ("env", "pass", "res", "compute"):
        readouts[k].setStyleSheet("font-size: 11px;")
    note = lbl("Nose camera, facing forward. Bearing + = dock to starboard (right), elevation + = above.\n"
               "Range is estimated from the dock's apparent size (set dock size in CAMERA MOUNT).")
    note.setWordWrap(True)
    tlay.addWidget(note)
    lbl_hint = QLabel("")
    lbl_hint.setWordWrap(True)
    lbl_hint.setVisible(False)
    lbl_hint.setStyleSheet("background:#3D2A00; color:#FFB020; border:1px solid #FFB020; border-radius:6px;"
                           " padding:6px; font-size:10px;")
    tlay.addWidget(lbl_hint)
    right_l.addWidget(tcard)

    # CAMERA MOUNT
    mcard, mlay = card("CAMERA MOUNT")
    mpos = lbl("Position: NOSE · facing forward along the vehicle axis")
    mpos.setWordWrap(True)
    mlay.addWidget(mpos)
    mgrid = QGridLayout()
    mgrid.setHorizontalSpacing(8)
    mgrid.setVerticalSpacing(4)

    def dspin(lo, hi, val, step, suffix, dec=1):
        from PySide6.QtWidgets import QDoubleSpinBox
        sp = QDoubleSpinBox()
        sp.setRange(lo, hi)
        sp.setDecimals(dec)
        sp.setSingleStep(step)
        sp.setValue(val)
        sp.setSuffix(suffix)
        return sp

    sp_hfov = dspin(20, 170, 80, 1, " °")
    sp_hfov.setToolTip("Horizontal field of view of the nose camera (from its datasheet).")
    sp_yaw = dspin(-45, 45, 0, 0.5, " °")
    sp_yaw.setToolTip("Camera yaw misalignment: + if the camera points to starboard.")
    sp_pitch = dspin(-60, 60, 0, 0.5, " °")
    sp_pitch.setToolTip("Camera tilt: + if the camera points up.")
    sp_dock = dspin(0.1, 10, 1.0, 0.05, " m", 2)
    sp_dock.setToolTip("Real outer width/height of the dock (largest side). Used for range estimation —\n"
                       "measure it and set it here.")
    for i_, (t_, w_) in enumerate((("Horizontal FOV", sp_hfov), ("Yaw offset", sp_yaw),
                                   ("Pitch offset", sp_pitch), ("Dock size", sp_dock))):
        mgrid.addWidget(lbl(t_), i_, 0)
        mgrid.addWidget(w_, i_, 1)
    mlay.addLayout(mgrid)
    chk_rot = QCheckBox("Camera mounted upside-down (rotate 180°)")
    mlay.addWidget(chk_rot)
    right_l.addWidget(mcard)

    # TRAINING DATA
    ccard, clay = card("TRAINING DATA")
    btn_capture = QPushButton("●  CAPTURE FRAMES FOR RETRAINING")
    btn_capture.setCheckable(True)
    btn_capture.setToolTip("Saves original frames + pre-filled labels (from current detections) into\n"
                           "a Roboflow-ready folder. Review/correct the boxes, then retrain.")
    clay.addWidget(btn_capture)
    crow = QHBoxLayout()
    crow.setContentsMargins(0, 0, 0, 0)
    crow.addWidget(lbl("Save every"))
    sp_every = QSpinBox()
    sp_every.setRange(1, 300)
    sp_every.setValue(10)
    sp_every.setSuffix(" frames")
    crow.addWidget(sp_every, 1)
    clay.addLayout(crow)
    lbl_capture = lbl("Capture frames where detection fails or is weak, label them, and retrain —\n"
                      "this is the most effective way to improve detection on your footage.")
    lbl_capture.setWordWrap(True)
    clay.addWidget(lbl_capture)
    right_l.addWidget(ccard)

    # RECORD
    rcard, rlay = card("RECORD & CAPTURE")
    btn_rec = QPushButton("●  RECORD ANNOTATED VIDEO")
    btn_rec.setCheckable(True)
    rlay.addWidget(btn_rec)
    rrow = QHBoxLayout()
    rrow.setContentsMargins(0, 0, 0, 0)
    btn_snap = QPushButton("SNAPSHOT")
    btn_open_dir = QPushButton("OPEN FOLDER")
    rrow.addWidget(btn_snap)
    rrow.addWidget(btn_open_dir)
    rlay.addLayout(rrow)
    lbl_rec_dir = lbl("")
    lbl_rec_dir.setWordWrap(True)
    btn_rec_dir = QPushButton("CHANGE FOLDER")
    rlay.addWidget(lbl_rec_dir)
    rlay.addWidget(btn_rec_dir)
    right_l.addWidget(rcard)
    right_l.addStretch()

    # ---------------- persisted settings
    def sget(k, default, typ=str):
        v = settings.value(f"camera_detection/{k}", default)
        try:
            return typ(v)
        except Exception:
            return default

    cmb_src.setCurrentText(sget("source_kind", SRC_VIDEO))
    ed_path.setText(sget("source_path", ""))
    sp_cam.setValue(sget("camera_index", 0, int))
    stored_model = sget("model_path", str(MODEL_DEFAULT))
    ed_model.setText(stored_model if Path(stored_model).is_file() else str(MODEL_DEFAULT))
    sl_conf.setValue(sget("conf", 25, int))
    sl_iou.setValue(sget("iou", 45, int))
    cmb_size.setCurrentText(sget("imgsz_v2", "Auto (model)"))
    cmb_env.setCurrentText(sget("environment", ENV_AUTO))
    cmb_compute.setCurrentIndex(sget("compute", 0, int))
    sp_hfov.setValue(sget("hfov", 80.0, float))
    sp_yaw.setValue(sget("yaw_off", 0.0, float))
    sp_pitch.setValue(sget("pitch_off", 0.0, float))
    sp_dock.setValue(sget("dock_size", 1.0, float))
    chk_rot.setChecked(sget("rotate180", 0, int) == 1)
    chk_show_enh.setChecked(sget("show_enhanced", 1, int) == 1)
    chk_distant.setChecked(sget("distant", 1, int) == 1)
    chk_track.setChecked(sget("track", 1, int) == 1)
    sp_every.setValue(sget("capture_every", 10, int))
    cmb_mode.setCurrentIndex(sget("mode", 0, int))
    rec_dir = {"path": Path(sget("record_dir", str(RECORD_DIR_DEFAULT)))}
    lbl_rec_dir.setText(f"Saving to: {rec_dir['path']}")

    def save_settings():
        settings.setValue("camera_detection/source_kind", cmb_src.currentText())
        settings.setValue("camera_detection/source_path", ed_path.text())
        settings.setValue("camera_detection/camera_index", sp_cam.value())
        settings.setValue("camera_detection/model_path", ed_model.text())
        settings.setValue("camera_detection/conf", sl_conf.value())
        settings.setValue("camera_detection/iou", sl_iou.value())
        settings.setValue("camera_detection/imgsz_v2", cmb_size.currentText())
        settings.setValue("camera_detection/environment", cmb_env.currentText())
        settings.setValue("camera_detection/compute", cmb_compute.currentIndex())
        settings.setValue("camera_detection/hfov", sp_hfov.value())
        settings.setValue("camera_detection/yaw_off", sp_yaw.value())
        settings.setValue("camera_detection/pitch_off", sp_pitch.value())
        settings.setValue("camera_detection/dock_size", sp_dock.value())
        settings.setValue("camera_detection/rotate180", int(chk_rot.isChecked()))
        settings.setValue("camera_detection/show_enhanced", int(chk_show_enh.isChecked()))
        settings.setValue("camera_detection/distant", int(chk_distant.isChecked()))
        settings.setValue("camera_detection/track", int(chk_track.isChecked()))
        settings.setValue("camera_detection/capture_every", sp_every.value())
        settings.setValue("camera_detection/mode", cmb_mode.currentIndex())
        settings.setValue("camera_detection/record_dir", str(rec_dir["path"]))

    # ---------------- helpers
    def fmt_t(sec):
        if sec is None or sec < 0:
            return "--:--.-"
        m, s = divmod(sec, 60)
        return f"{int(m):02d}:{s:04.1f}"

    def speed_value():
        t = cmb_speed.currentText()
        return 0.0 if t == "Max" else float(t.replace("×", ""))

    def set_status(text, level="info"):
        state["status"] = (text, level)
        parent._camdet_refresh_status_pill()

    def refresh_status_pill():
        text, level = state["status"]
        colors = {"info": ("#26262D", "#A0A0A0"), "ok": ("#0E3B1E", "#34D399"),
                  "warn": ("#3D2A00", "#FFB020"), "error": ("#3B0E0E", "#FF6B6B")}
        bg, fg = colors.get(level, colors["info"])
        pill.setText(f"  {text.upper() if len(text) < 26 else text}  ")
        pill.setToolTip(text)
        pill.setStyleSheet(f"background:{bg}; color:{fg}; border:1px solid {fg}; border-radius:11px;"
                           f" font-size:9.5px; font-weight:bold; padding:0 8px;")

    parent._camdet_refresh_status_pill = refresh_status_pill

    def set_lock_label(mode):
        styles = {
            "locked": ("DOCK LOCKED", "#0E3B1E", "#34D399"),
            "acq": ("ACQUIRING…", "#3D2A00", "#FFB020"),
            "search": ("SEARCHING", "#26262D", "#A0A0A0"),
            "hold": ("HOLDING (TARGET LOST)", "#3D2A00", "#FFB020"),
        }
        t, bg, fg = styles[mode]
        lbl_lock.setText(t)
        lbl_lock.setStyleSheet(f"background:{bg}; color:{fg}; border:1px solid {fg}; border-radius:6px;"
                               f" font-size:12px; font-weight:bold; letter-spacing:1.5px;")

    def update_source_widgets():
        k = cmb_src.currentText()
        path_w.setVisible(k in (SRC_VIDEO, SRC_STREAM, SRC_IMAGE, SRC_FOLDER))
        cam_w.setVisible(k == SRC_CAMERA)
        chk_loop.setVisible(k == SRC_VIDEO)
        btn_browse.setVisible(k != SRC_STREAM)
        ed_path.setPlaceholderText({
            SRC_VIDEO: "Video file path…", SRC_STREAM: "rtsp://192.168.2.2:8554/cam  or  udp://@:5600",
            SRC_IMAGE: "Image file path…", SRC_FOLDER: "Folder containing images…",
        }.get(k, ""))

    def is_running():
        w = state["worker"]
        return w is not None and w.isRunning()

    def set_running_ui(running, paused=False):
        btn_play.setText("❚❚  PAUSE" if running and not paused else ("▶  RESUME" if running else "▶  START"))
        btn_step.setEnabled(running and paused)
        cmb_src.setEnabled(not running)
        path_w.setEnabled(not running)
        cam_w.setEnabled(not running)
        btn_model.setEnabled(not running)
        ed_model.setEnabled(not running)

    def thr_now():
        return sl_conf.value() / 100.0

    def reset_readouts():
        state["raw_hist"].clear()
        lbl_hint.setVisible(False)
        for v in readouts.values():
            v.setText("—")
        set_lock_label("search")
        state["ema"] = None

    # ---------------- worker wiring
    def start_worker(start_frame=0):
        kind = cmb_src.currentText()
        target = str(sp_cam.value()) if kind == SRC_CAMERA else ed_path.text().strip().strip('"')
        if kind != SRC_CAMERA and not target:
            set_status("Choose a source first", "warn")
            return
        save_settings()
        w = DetectionWorker()
        w.kind, w.target = kind, target
        w.model_path = ed_model.text().strip().strip('"') or str(MODEL_DEFAULT)
        w.force_cpu = cmb_compute.currentIndex() == 1
        w.rotate180 = chk_rot.isChecked()
        w.start_frame = start_frame
        apply_params(w)
        w.frame_ready.connect(lambda img, info, w=w: on_frame(img, info) if state["worker"] is w else None)
        w.status.connect(lambda t, l, w=w: set_status(t, l) if state["worker"] is w else None)
        w.opened.connect(lambda info, w=w: on_opened(info) if state["worker"] is w else None)
        w.ended.connect(lambda r, w=w: on_ended(r) if state["worker"] is w else None)
        w.recording.connect(on_recording_saved)
        w.finished.connect(lambda w=w: state["retired"].discard(w))
        state["retired"].add(w)          # hold a reference until the thread really exits
        state["worker"] = w
        reset_readouts()
        set_status("Starting…", "info")
        set_running_ui(True)
        w.start()

    def apply_params(w=None):
        w = w or state["worker"]
        if w is None:
            return
        w.conf = sl_conf.value() / 100.0
        w.iou = sl_iou.value() / 100.0
        w.imgsz = 0 if cmb_size.currentIndex() == 0 else int(cmb_size.currentText())
        w.speed = speed_value()
        w.realtime = cmb_mode.currentIndex() == 0
        w.show_guide = chk_guide.isChecked()
        w.loop = chk_loop.isChecked()
        w.record = btn_rec.isChecked()
        w.record_dir = rec_dir["path"]
        w.environment = cmb_env.currentText()
        w.show_enhanced = chk_show_enh.isChecked()
        w.distant = chk_distant.isChecked()
        w.track = chk_track.isChecked()
        w.capture = btn_capture.isChecked()
        w.capture_every = sp_every.value()
        w.capture_dir = rec_dir["path"]

    def stop_worker(wait=True):
        w = state["worker"]
        if w is not None:
            w.stop()
            if wait:
                w.wait(4000)
        state["worker"] = None
        state["pending"] = None
        set_running_ui(False)
        set_lock_label("search")
        view.hud2 = ""
        view.update()
        parent.camera_detection_state = {"detected": False, "locked": False, "running": False}

    parent.shutdown_camera_detection = lambda: stop_worker(True)

    def api_start():
        if not is_running():
            start_worker()

    def api_source_label():
        k = cmb_src.currentText()
        if k == SRC_CAMERA:
            return f"Camera {sp_cam.value()}"
        t = ed_path.text().strip()
        return f"{k}: {Path(t).name or t}" if t else k

    parent.camera_detection_start = api_start
    parent.camera_detection_stop = lambda: (stop_worker(), set_status("Stopped", "info"))
    parent.camera_detection_is_running = lambda: is_running()
    parent.camera_detection_source_label = api_source_label

    def on_opened(info):
        state["source_info"] = info
        lbl_device.setText(f"Compute: {info['device']}   ·   model trained at {info.get('trained_size', '?')} px")
        lbl_device.setStyleSheet("color:#FFB020;" if info.get("gpu_hint") else "")
        lbl_device.setToolTip(info.get("gpu_hint", ""))
        lbl_source_name.setText(info["name"])
        total = info["total"]
        seek.setEnabled(total > 1)
        seek.setRange(0, max(0, total - 1))
        if info["live"]:
            lbl_time.setText("LIVE")
        if info["images"] and total == 1:
            set_running_ui(True, paused=True)
            btn_play.setText("↻  RELOAD")
            btn_step.setEnabled(False)

    def on_frame(qimg, info):
        # Coalesce: if frames arrive faster than the UI paints, only the newest is shown.
        state["pending"] = (qimg, info)
        if not ui_timer.isActive():
            ui_timer.start(0)

    def flush_frame():
        item = state["pending"]
        state["pending"] = None
        if item is None:
            return
        qimg, info = item
        view.set_image(qimg)
        state["latest_info"] = info
        best = info["best"]
        view.hud = (f"{info['proc_fps']:4.1f} FPS · {info['infer_ms']:.0f} ms"
                    + (f" · {info['image_name']}" if info["image_name"] else ""))
        view.rec = info["recording"]
        # transport
        if not info["live"]:
            total = info["total"]
            if total > 0 and not state["slider_drag"]:
                seek.blockSignals(True)
                seek.setValue(info["frame"])
                seek.blockSignals(False)
            if state["source_info"] and state["source_info"].get("images"):
                lbl_time.setText(f"{info['frame'] + 1} / {total}")
            else:
                lbl_time.setText(f"{fmt_t(info['t'])} / {fmt_t(total / info['fps'] if total else None)}")
        # target readouts (EMA-smoothed offsets)
        readouts["count"].setText(str(len(info["dets"])))
        readouts["infer"].setText(f"{info['infer_ms']:.0f} ms")
        readouts["fps"].setText(f"{info['proc_fps']:.1f}")
        readouts["streak"].setText(str(info["streak"]))
        readouts["raw"].setText(f"{info['raw_top']:.2f}")
        readouts["pass"].setText(info["pass"])
        readouts["env"].setText(info["env"])
        readouts["res"].setText(f"{info['src_w']}×{info['src_h']}" if info.get("src_w") else f"{info['w']}×{info['h']}")
        if not info["reprocess"]:
            state["raw_hist"].append(info["raw_top"])
        hist = state["raw_hist"]
        if len(hist) >= 30 and max(hist) < 0.10:
            lbl_hint.setText("The model barely recognises anything here (raw score < 0.10 for the last "
                             f"{len(hist)} frames).\n• Keep Environment on Auto and Distant target search on.\n"
                             "• If it still fails, this scene looks too different from the training images — "
                             "capture frames below and retrain (see README).")
            lbl_hint.setVisible(True)
        elif len(hist) >= 30 and max(hist) < thr_now() and not best:
            lbl_hint.setText(f"Weak detections (best raw score {max(hist):.2f}) are below the confidence "
                             f"threshold ({thr_now():.2f}). Lowering confidence may help; retraining on this "
                             "footage will help more.")
            lbl_hint.setVisible(True)
        else:
            lbl_hint.setVisible(False)
        if info["capture_count"]:
            lbl_capture.setText(f"{info['capture_count']} frames saved to:\n{info['capture_dir']}")
        readouts["compute"].setText("GPU" if (state.get("source_info") or {}).get("gpu") else "CPU")
        geo = None
        if best:
            x1, y1, x2, y2 = best["xyxy"]
            g = camera_geometry(best["cx"], best["cy"], x2 - x1, y2 - y1, info["w"], info["h"],
                                sp_hfov.value(), sp_yaw.value(), sp_pitch.value(), sp_dock.value())
            a = 0.4
            e = state["ema"]
            e = (best["offx"], best["offy"], *g) if e is None else tuple(
                a * n + (1 - a) * o for n, o in zip((best["offx"], best["offy"], *g), e))
            state["ema"] = e
            geo = e[2:]
            edge = (x1 <= 2 or y1 <= 2 or x2 >= info["w"] - 2 or y2 >= info["h"] - 2)
            readouts["brg"].setText(f"{geo[0]:+.1f}°")
            readouts["elv"].setText(f"{geo[1]:+.1f}°")
            readouts["rng"].setText(("≤ " if edge else "≈ ") + f"{geo[2]:.2f} m")
            view.hud2 = (f"BRG {geo[0]:+.1f}°   ELV {geo[1]:+.1f}°   RNG {'≤' if edge else '≈'} {geo[2]:.1f} m   "
                         + ("◀ LEFT" if geo[0] < -3 else "RIGHT ▶" if geo[0] > 3 else "▲ AHEAD"))
            readouts["conf"].setText(f"{best['conf']:.2f}")
            readouts["offx"].setText(f"{e[0]:+.2f}")
            readouts["offy"].setText(f"{e[1]:+.2f}")
            readouts["area"].setText(f"{best['area']:.1f} %")
            set_lock_label("locked" if info["streak"] >= LOCK_FRAMES else "acq")
        elif info["coasting"] and info["streak"] >= LOCK_FRAMES:
            set_lock_label("hold")
        else:
            state["ema"] = None
            view.hud2 = ""
            for k in ("conf", "offx", "offy", "area", "brg", "elv", "rng"):
                readouts[k].setText("—")
            set_lock_label("search")
        bx = best["xyxy"] if best else None
        parent.camera_detection_state = {
            "running": True, "seq": state["seq"],
            "detected": best is not None, "locked": info["streak"] >= LOCK_FRAMES,
            "holding": bool(info["coasting"]),
            "offset_x": state["ema"][0] if state["ema"] else None,      # smoothed, -1..1
            "offset_y": state["ema"][1] if state["ema"] else None,
            "bearing_deg": geo[0] if geo else None,       # + = starboard, nose camera, smoothed
            "elevation_deg": geo[1] if geo else None,     # + = above axis
            "range_m": geo[2] if geo else None,           # from apparent dock size
            "range_reliable": bool(best) and not (best["xyxy"][0] <= 2 or best["xyxy"][1] <= 2 or
                                                   best["xyxy"][2] >= info["w"] - 2 or best["xyxy"][3] >= info["h"] - 2),
            "hfov_deg": sp_hfov.value(), "mount": "nose_forward",
            "offset_x_raw": best["offx"] if best else None,             # this frame, -1..1
            "offset_y_raw": best["offy"] if best else None,
            "box_xyxy": bx, "box_w": (bx[2] - bx[0]) if bx else None, "box_h": (bx[3] - bx[1]) if bx else None,
            "box_touches_edge": bool(bx and (bx[0] <= 2 or bx[1] <= 2 or bx[2] >= info["w"] - 2 or bx[3] >= info["h"] - 2)),
            "frame_w": info["w"], "frame_h": info["h"],
            "confidence": best["conf"] if best else None,
            "area_pct": best["area"] if best else None, "env": info["env"],
            "t_capture": info["t_capture"], "timestamp": time.time(), "live": info["live"],
        }
        state["seq"] += 1
        # log
        if best and not info["reprocess"]:
            now = QDateTime.currentDateTime().toString("HH:mm:ss.zzz")
            row = [now, info["frame"], round(info["t"], 2), round(best["conf"], 3),
                   round(best["cx"], 1), round(best["cy"], 1), round(best["offx"], 3),
                   round(best["offy"], 3), round(best["area"], 2)]
            state["log"].append(row)
            if table.rowCount() >= LOG_TABLE_ROWS:
                table.removeRow(table.rowCount() - 1)
            table.insertRow(0)
            for ci, v in enumerate(row):
                it = QTableWidgetItem(str(v))
                it.setTextAlignment(Qt.AlignCenter)
                table.setItem(0, ci, it)
            lbl_log_count.setText(f"{len(state['log'])} detections")

    parent.camdet_frame_listeners = []

    def flush_and_notify():
        had = state["pending"] is not None
        flush_frame()
        if had and view.image is not None:
            for cb in list(parent.camdet_frame_listeners):
                try:
                    cb(view.image, state.get("latest_info") or {})
                except Exception as e:                       # a listener must never break detection
                    print(f"[Camera Detection] listener error: {e}")

    ui_timer = QTimer(page)
    ui_timer.setSingleShot(True)
    ui_timer.timeout.connect(flush_and_notify)

    def on_ended(reason):
        flush_frame()
        state["worker"] = None
        set_lock_label("search")
        parent.camera_detection_state = {"detected": False, "locked": False, "running": False}
        set_running_ui(False)
        msg = {"end": ("End of video", "info"), "stopped": ("Stopped", "info"),
               "lost": ("Stream lost", "error"), "error": (state["status"][0], "error")}
        set_status(*msg.get(reason, ("Stopped", "info")))
        if reason == "end" and state["source_info"] and state["source_info"].get("images"):
            set_status("Folder complete", "ok")

    def on_recording_saved(path):
        if path:
            set_status(f"Saved {Path(path).name}", "ok")

    # ---------------- button handlers
    def on_play():
        w = state["worker"]
        if w is not None and w.isRunning():
            info = state["source_info"] or {}
            if info.get("images") and info.get("total") == 1:
                # single image: START re-runs from scratch
                stop_worker()
                start_worker()
                return
            p = not w.is_paused()
            w.set_paused(p)
            set_running_ui(True, paused=p)
            set_status("Paused" if p else "Running", "warn" if p else "ok")
        else:
            start_worker()

    def on_stop():
        stop_worker()
        set_status("Stopped", "info")

    def on_step():
        w = state["worker"]
        if w is not None:
            w.step()

    def on_seek_released():
        state["slider_drag"] = False
        w = state["worker"]
        if w is not None and w.isRunning():
            w.seek(seek.value())
        elif state["source_info"] and not state["source_info"].get("live"):
            start_worker(start_frame=seek.value())

    def on_seek_moved(v):
        info = state["source_info"]
        if info and info.get("fps") and not info.get("images"):
            lbl_time.setText(f"{fmt_t(v / info['fps'])} / {fmt_t(info['total'] / info['fps'])}")

    def on_browse():
        k = cmb_src.currentText()
        start = ed_path.text() or str(Path.home())
        if k == SRC_VIDEO:
            p, _ = QFileDialog.getOpenFileName(page, "Open video", start, VIDEO_FILTER)
        elif k == SRC_IMAGE:
            p, _ = QFileDialog.getOpenFileName(page, "Open image", start, IMAGE_FILTER)
        else:
            p = QFileDialog.getExistingDirectory(page, "Choose image folder", start)
        if p:
            ed_path.setText(p)
            save_settings()

    def on_model_browse():
        p, _ = QFileDialog.getOpenFileName(page, "Choose detection model", ed_model.text(),
                                           "Model weights (*.pt *.onnx);;All files (*)")
        if p:
            ed_model.setText(p)
            save_settings()

    def on_dropped(path):
        if not path:
            return
        p = Path(path)
        if p.is_dir():
            cmb_src.setCurrentText(SRC_FOLDER)
        elif p.suffix.lower() in IMAGE_EXTS:
            cmb_src.setCurrentText(SRC_IMAGE)
        else:
            cmb_src.setCurrentText(SRC_VIDEO)
        ed_path.setText(str(p))
        stop_worker()
        start_worker()

    def on_param_changed(*_):
        lv_conf.setText(f"{sl_conf.value() / 100:.2f}")
        lv_iou.setText(f"{sl_iou.value() / 100:.2f}")
        chk_show_enh.setEnabled(cmb_env.currentText() != ENV_OFF)
        apply_params()
        w = state["worker"]
        if w is not None and w.isRunning() and w.is_paused():
            w.reprocess()

    def on_record_toggled(checked):
        btn_rec.setText("■  STOP RECORDING" if checked else "●  RECORD ANNOTATED VIDEO")
        apply_params()

    def on_capture_toggled(checked):
        btn_capture.setText("■  STOP CAPTURING" if checked else "●  CAPTURE FRAMES FOR RETRAINING")
        apply_params()

    def on_snapshot():
        if view.image is None:
            set_status("Nothing to capture", "warn")
            return
        rec_dir["path"].mkdir(parents=True, exist_ok=True)
        p = rec_dir["path"] / f"snapshot_{time.strftime('%Y%m%d_%H%M%S')}.png"
        view.image.save(str(p))
        set_status(f"Saved {p.name}", "ok")

    def on_open_dir():
        rec_dir["path"].mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(rec_dir["path"])))

    def on_change_dir():
        p = QFileDialog.getExistingDirectory(page, "Recording folder", str(rec_dir["path"]))
        if p:
            rec_dir["path"] = Path(p)
            lbl_rec_dir.setText(f"Saving to: {p}")
            apply_params()
            save_settings()

    def on_export():
        if not state["log"]:
            set_status("Log is empty", "warn")
            return
        default = str(rec_dir["path"] / f"detections_{time.strftime('%Y%m%d_%H%M%S')}.csv")
        p, _ = QFileDialog.getSaveFileName(page, "Export detections", default, "CSV (*.csv)")
        if p:
            with open(p, "w", newline="") as f:
                wr = csv.writer(f)
                wr.writerow(["time", "frame", "source_t_s", "confidence", "cx_px", "cy_px",
                             "offset_x", "offset_y", "area_pct"])
                wr.writerows(state["log"])
            set_status(f"Exported {len(state['log'])} rows", "ok")

    def on_clear():
        state["log"].clear()
        table.setRowCount(0)
        lbl_log_count.setText("0 detections")

    btn_play.clicked.connect(on_play)
    btn_stop.clicked.connect(on_stop)
    btn_step.clicked.connect(on_step)
    seek.sliderPressed.connect(lambda: state.__setitem__("slider_drag", True))
    seek.sliderReleased.connect(on_seek_released)
    seek.sliderMoved.connect(on_seek_moved)
    btn_browse.clicked.connect(on_browse)
    btn_model.clicked.connect(on_model_browse)
    view.file_dropped.connect(on_dropped)
    cmb_src.currentTextChanged.connect(lambda *_: (update_source_widgets(), save_settings()))
    for s in (sl_conf, sl_iou):
        s.valueChanged.connect(on_param_changed)
        s.sliderReleased.connect(save_settings)
    for cb in (cmb_size, cmb_mode, cmb_speed):
        cb.currentIndexChanged.connect(on_param_changed)
    cmb_size.currentIndexChanged.connect(lambda *_: save_settings())
    cmb_mode.currentIndexChanged.connect(lambda *_: save_settings())
    chk_guide.toggled.connect(on_param_changed)
    chk_loop.toggled.connect(on_param_changed)
    btn_rec.toggled.connect(on_record_toggled)
    btn_capture.toggled.connect(on_capture_toggled)
    sp_every.valueChanged.connect(on_param_changed)
    for cb in (chk_show_enh, chk_distant, chk_track):
        cb.toggled.connect(on_param_changed)
        cb.toggled.connect(lambda *_: save_settings())
    cmb_env.currentIndexChanged.connect(on_param_changed)
    cmb_compute.currentIndexChanged.connect(lambda *_: save_settings())
    for sp_ in (sp_hfov, sp_yaw, sp_pitch, sp_dock):
        sp_.valueChanged.connect(lambda *_: save_settings())
    chk_rot.toggled.connect(on_param_changed)
    chk_rot.toggled.connect(lambda *_: save_settings())
    cmb_env.currentIndexChanged.connect(lambda *_: save_settings())
    btn_snap.clicked.connect(on_snapshot)
    btn_open_dir.clicked.connect(on_open_dir)
    btn_rec_dir.clicked.connect(on_change_dir)
    btn_export.clicked.connect(on_export)
    btn_clear.clicked.connect(on_clear)

    update_source_widgets()
    set_running_ui(False)
    set_lock_label("search")
    if not Path(ed_model.text()).is_file():
        set_status("Model file missing", "warn")
    apply_camera_detection_theme(parent, getattr(parent, "current_theme", "cockpit"))
    return page

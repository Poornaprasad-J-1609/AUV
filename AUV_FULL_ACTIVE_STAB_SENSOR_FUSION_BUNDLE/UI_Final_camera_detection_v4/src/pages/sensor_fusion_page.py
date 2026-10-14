"""
Sensor Fusion page — combines the four docking sensors into one estimate of where the
dock is relative to the vehicle:

    1) GPS               vehicle fix vs. docking-station coordinates (surface only)
    2) Magnetometer      RM3100 field anomaly from the dock's EMF coil → range (+ optional bearing)
    3) Sonar             Ping360 target range / bearing
    4) Camera Detection  nose camera, facing forward → bearing (+ range from dock size)

Modes
  • Staged handover (default, follows the Trinetra plan): the highest-priority sensor that is
    enabled, fresh and inside its active range is used — GPS far out, sonar inside 5 m,
    magnetometer inside 3 m — with the camera refining bearing whenever it sees the dock.
  • Weighted fusion: a 2-D Kalman filter blends every valid measurement by its uncertainty,
    with outlier gating.

Each sensor can be switched in/out independently (switched-out sensors are still shown,
so they can be monitored and tested on their own before being trusted).
"""
import csv
import math
import random
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QSettings, QPointF, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QPolygonF, QPainterPath
from PySide6.QtWidgets import (
    QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QComboBox,
    QCheckBox, QDoubleSpinBox, QSpinBox, QListWidget, QListWidgetItem, QAbstractItemView,
    QScrollArea, QSizePolicy,
)

SETTINGS_ORG, SETTINGS_APP = "NIOT", "MarineGroundStation"
GPS, MAG, SONAR, CAM = "gps", "mag", "sonar", "cam"
SENSORS = (GPS, MAG, SONAR, CAM)
NAMES = {GPS: "GPS", MAG: "MAGNETOMETER", SONAR: "SONAR", CAM: "CAMERA DETECTION"}
LABELS = {GPS: "GPS", MAG: "Magnetometer", SONAR: "Sonar", CAM: "Camera detection"}
SHORT = {GPS: "G", MAG: "M", SONAR: "S", CAM: "C"}
COLORS = {GPS: "#3B82F6", MAG: "#A855F7", SONAR: "#F59E0B", CAM: "#22C55E"}
MODE_STAGED, MODE_KALMAN = "Staged handover (Trinetra plan)", "Weighted fusion (Kalman filter)"

DEFAULTS = {
    GPS:   dict(enabled=True,  rmin=0.0, rmax=5000.0, timeout=3.0, sr=3.0,  sb=0.0),
    MAG:   dict(enabled=False, rmin=0.2, rmax=3.0,    timeout=1.0, sr=20.0, sb=25.0),
    SONAR: dict(enabled=True,  rmin=0.3, rmax=5.0,    timeout=3.0, sr=0.15, sb=2.0),
    CAM:   dict(enabled=True,  rmin=0.2, rmax=8.0,    timeout=0.5, sr=15.0, sb=1.5),
}


def wrap180(a):
    return (a + 180.0) % 360.0 - 180.0


def gps_range_bearing(lat1, lon1, lat2, lon2):
    """Distance (m) and initial true bearing (deg) from point 1 to point 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    d = 6371000.0 * 2 * math.asin(min(1.0, math.sqrt(a)))
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return d, math.degrees(math.atan2(y, x)) % 360.0


# ======================================================================= fusion engine
class Kalman2D:
    """Dock position relative to the vehicle in a north/east frame (m)."""

    def __init__(self):
        import numpy as np
        self.np = np
        self.reset()

    def reset(self):
        self.x = None
        self.P = None
        self.t = None
        self.rejects = {s: 0 for s in SENSORS}
        self.last_update = None

    def predict(self, t, q):
        if self.x is None:
            return
        dt = max(0.0, t - self.t)
        self.t = t
        self.P = self.P + self.np.eye(2) * (q * q * dt)

    def init(self, z, R, t):
        self.x = z.astype(float).copy()
        self.P = R.astype(float).copy() + self.np.eye(2) * 1e-4
        self.t = t
        self.last_update = t

    def update(self, z, h, H, R, gate, src, wrap=False):
        np = self.np
        y = np.atleast_1d(z - h).astype(float)
        if wrap:
            y = (y + np.pi) % (2 * np.pi) - np.pi
        S = H @ self.P @ H.T + R
        try:
            Si = np.linalg.inv(S)
        except np.linalg.LinAlgError:
            return False
        d = float(np.sqrt(max(0.0, y @ Si @ y)))
        if d > gate * (1.0 if len(y) == 1 else 1.18):     # ~same tail probability for 1-D and 2-D
            self.rejects[src] += 1
            return False
        K = self.P @ H.T @ Si
        self.x = self.x + K @ y
        self.P = (np.eye(2) - K @ H) @ self.P
        self.rejects[src] = 0
        self.last_update = self.t
        return True


# ======================================================================= plot widget
class FusionPlot(QWidget):
    """Vehicle-centred top view, bow up. Rings auto-scale to the target distance."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(360, 360)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.meas = {}        # sensor -> (r, b, sr_m, sb_deg) ; r or b may be None
        self.fused = None     # (r, b, sigma_m)
        self.rings = {}       # label -> radius (handover distances)
        self.hfov = 80.0
        self.sim = False
        self.colors = dict(bg="#0E1117", grid="#2A3242", text="#9AA7BA", vehicle="#E2E8F0", fused="#00E5FF")
        self._scale = 10.0

    def set_colors(self, **kw):
        self.colors.update(kw)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = self.colors
        p.fillRect(self.rect(), QColor(c["bg"]))
        w, h = self.width(), self.height()
        cx, cy = w / 2, h * 0.62
        R = min(w / 2, h * 0.58) - 18

        # auto scale (smoothed) so the farthest thing of interest sits at ~70 % radius
        far = [m[0] for m in self.meas.values() if m[0]]
        if self.fused:
            far.append(self.fused[0])
        target = max([3.0] + [f * 1.45 for f in far])
        nice = [3, 5, 8, 10, 15, 20, 30, 50, 75, 100, 150, 200, 300, 500, 1000, 2000, 5000]
        target = next((n for n in nice if n >= target), nice[-1])
        self._scale += (target - self._scale) * 0.15
        S = R / self._scale                                  # px per metre

        def pt(r, b):
            a = math.radians(b)
            return QPointF(cx + r * S * math.sin(a), cy - r * S * math.cos(a))

        # range rings
        f = QFont("Consolas", 8)
        p.setFont(f)
        step = next((s for s in (0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 500, 1000) if self._scale / s <= 5), 1000)
        r_ = step
        while r_ <= self._scale + 1e-6:
            p.setPen(QPen(QColor(c["grid"]), 1))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), r_ * S, r_ * S)
            p.setPen(QColor(c["text"]))
            p.drawText(QPointF(cx + 4, cy - r_ * S - 3), f"{r_:g} m")
            r_ += step
        p.setPen(QPen(QColor(c["grid"]), 1, Qt.DashLine))
        p.drawLine(QPointF(cx, cy - R), QPointF(cx, cy + R * 0.6))
        p.drawLine(QPointF(cx - R, cy), QPointF(cx + R, cy))

        # handover rings
        for label, rr in self.rings.items():
            if rr and rr <= self._scale * 1.05 and rr * S > 28:
                p.setPen(QPen(QColor(c["text"]), 1, Qt.DotLine))
                p.drawEllipse(QPointF(cx, cy), rr * S, rr * S)
                p.drawText(QPointF(cx - rr * S * 0.71 - 4, cy - rr * S * 0.71 - 4), label)

        # camera field of view wedge (nose, forward)
        fov = QPainterPath()
        fov.moveTo(cx, cy)
        fov.arcTo(QRectF(cx - R, cy - R, 2 * R, 2 * R), 90 - self.hfov / 2, self.hfov)
        fov.closeSubpath()
        col = QColor(COLORS[CAM])
        col.setAlpha(22)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawPath(fov)

        # sensor measurements
        for s, (r, b, sr, sb) in self.meas.items():
            col = QColor(COLORS[s])
            if r is not None and b is not None:
                # uncertainty patch: radial ±sr, angular ±sb
                path = QPainterPath()
                r0, r1 = max(0.0, r - sr), r + sr
                b0, b1 = b - max(sb, 0.5), b + max(sb, 0.5)
                pts = [pt(r1, b0 + (b1 - b0) * k / 10) for k in range(11)] + \
                      [pt(r0, b1 - (b1 - b0) * k / 10) for k in range(11)]
                path.addPolygon(QPolygonF(pts))
                path.closeSubpath()
                fill = QColor(col)
                fill.setAlpha(60)
                p.setPen(QPen(col, 1))
                p.setBrush(fill)
                p.drawPath(path)
                p.setBrush(col)
                p.setPen(QPen(QColor("#000000"), 1))
                p.drawEllipse(pt(r, b), 5, 5)
            elif r is not None:                               # range only → circle
                p.setPen(QPen(col, 2, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QPointF(cx, cy), r * S, r * S)
            elif b is not None:                               # bearing only → ray
                p.setPen(QPen(col, 2, Qt.DashLine))
                p.drawLine(QPointF(cx, cy), pt(self._scale, b))

        # fused estimate
        if self.fused:
            r, b, sig = self.fused
            q = pt(r, b)
            p.setPen(QPen(QColor(c["fused"]), 2))
            p.drawLine(QPointF(cx, cy), q)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(q, max(6.0, sig * S), max(6.0, sig * S))
            d = QPolygonF([q + QPointF(0, -9), q + QPointF(9, 0), q + QPointF(0, 9), q + QPointF(-9, 0)])
            p.setBrush(QColor(c["fused"]))
            p.setPen(QPen(QColor("#000000"), 1))
            p.drawPolygon(d)

        # vehicle (bow up)
        v = QPolygonF([QPointF(cx, cy - 16), QPointF(cx + 7, cy + 10), QPointF(cx, cy + 5), QPointF(cx - 7, cy + 10)])
        p.setBrush(QColor(c["vehicle"]))
        p.setPen(QPen(QColor("#000000"), 1))
        p.drawPolygon(v)

        # legend
        f2 = QFont("Google Sans", 8)
        f2.setBold(True)
        p.setFont(f2)
        y = 14
        for s in SENSORS:
            p.setBrush(QColor(COLORS[s]))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(14, y), 4, 4)
            p.setPen(QColor(c["text"]))
            p.drawText(QPointF(24, y + 4), LABELS[s])
            y += 16
        p.setBrush(QColor(c["fused"]))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(14, y), 4, 4)
        p.setPen(QColor(c["text"]))
        p.drawText(QPointF(24, y + 4), "Fused")
        p.drawText(QRectF(0, h - 18, w - 8, 16), Qt.AlignRight, "Top view · bow up · nose camera FOV shaded")
        if self.sim:
            f3 = QFont("Google Sans", 10)
            f3.setBold(True)
            p.setFont(f3)
            p.setPen(QColor("#FFB020"))
            p.drawText(QRectF(0, 6, w - 10, 20), Qt.AlignRight, "SIMULATED SENSORS")


# ======================================================================= theme
def apply_sensor_fusion_theme(parent, theme):
    if theme in ("cockpit", "dark"):
        c = dict(bg="#121214", card="#1E1E22", border="#323238", text="#FFFFFF", sub="#A0A0A0", btn="#26262B",
                 btn_b="#3A3A42", btn_h="#303037", inp="#1A1A1E", grid="#2C2C32", accent="#FF9100", plot="#0E0E11")
    elif theme == "ocean":
        c = dict(bg="#050B14", card="#0F223C", border="#1C3B65", text="#E2F1FF", sub="#8EB7E6", btn="#0F223C",
                 btn_b="#1C3B65", btn_h="#152F53", inp="#0A1625", grid="#1C3B65", accent="#00E5FF", plot="#030810")
    else:
        c = dict(bg="#F4F7FB", card="#FFFFFF", border="#CBD5E1", text="#0A2540", sub="#5A738E", btn="#FFFFFF",
                 btn_b="#CBD5E1", btn_h="#F8FAFC", inp="#FFFFFF", grid="#E2E8F0", accent="#0284C7", plot="#0B1220")
    root = getattr(parent, "fusion_container", None)
    if root is None:
        return
    root.setStyleSheet(f"""
        QWidget#FusContainer {{ background-color: {c['bg']}; }}
        QFrame#FusCard {{ background-color: {c['card']}; border: 1px solid {c['border']}; border-radius: 8px; }}
        QLabel {{ color: {c['text']}; background: transparent; border: none; font-family: 'Google Sans', sans-serif; }}
        QLabel#FusTitle {{ color: {c['sub']}; font-size: 10px; font-weight: bold; letter-spacing: 1px; }}
        QLabel#FusPageTitle {{ color: {c['text']}; font-size: 15px; font-weight: bold; letter-spacing: 1.5px; }}
        QLabel#FusSub {{ color: {c['sub']}; font-size: 10px; }}
        QLabel#FusKey {{ color: {c['sub']}; font-size: 9px; font-weight: bold; letter-spacing: 0.8px; }}
        QLabel#FusVal {{ color: {c['text']}; font-size: 14px; font-weight: bold; font-family: 'Consolas', monospace; }}
        QLabel#FusBig {{ color: {c['text']}; font-size: 24px; font-weight: bold; font-family: 'Consolas', monospace; }}
        QPushButton {{ background-color: {c['btn']}; color: {c['text']}; border: 1px solid {c['btn_b']};
                       border-radius: 5px; padding: 5px 9px; font-size: 10px; font-weight: bold; }}
        QPushButton:hover {{ background-color: {c['btn_h']}; border-color: {c['accent']}; }}
        QPushButton:checked {{ background-color: #B3261E; color: #FFFFFF; border-color: #B3261E; }}
        QComboBox, QSpinBox, QDoubleSpinBox {{ background-color: {c['inp']}; color: {c['text']};
                       border: 1px solid {c['btn_b']}; border-radius: 4px; padding: 3px 5px; font-size: 10px; }}
        QComboBox QAbstractItemView {{ background-color: {c['card']}; color: {c['text']}; selection-background-color: {c['btn_h']}; }}
        QCheckBox {{ color: {c['text']}; font-size: 10px; background: transparent; }}
        QCheckBox::indicator {{ width: 13px; height: 13px; border: 1px solid {c['btn_b']}; border-radius: 3px; background: {c['inp']}; }}
        QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']}; }}
        QListWidget {{ background-color: {c['inp']}; color: {c['text']}; border: 1px solid {c['btn_b']};
                       border-radius: 4px; font-size: 11px; font-weight: bold; }}
        QListWidget::item {{ padding: 5px; border-bottom: 1px solid {c['grid']}; }}
        QListWidget::item:selected {{ background-color: {c['btn_h']}; color: {c['text']}; }}
        QScrollArea {{ background: transparent; border: none; }}
    """)
    plot = getattr(parent, "fusion_plot", None)
    if plot is not None:
        plot.set_colors(bg=c["plot"], grid="#2A3242" if theme != "light" else "#334155",
                        text="#9AA7BA", vehicle="#E2E8F0")


# ======================================================================= page
def create_sensor_fusion_page(parent):
    parent.apply_sensor_fusion_theme = lambda theme: apply_sensor_fusion_theme(parent, theme)
    settings = QSettings(SETTINGS_ORG, SETTINGS_APP)

    def sget(k, default, typ=float):
        v = settings.value(f"sensor_fusion/{k}", default)
        try:
            if typ is bool:
                return str(v).lower() in ("1", "true", "yes")
            return typ(v)
        except Exception:
            return default

    def sset(k, v):
        settings.setValue(f"sensor_fusion/{k}", v)

    st = {
        "heading": None, "heading_t": 0.0,
        "gps": None, "mag_raw": None, "sonar": None, "depth": None,
        "meas": {s: None for s in SENSORS},           # latest processed measurement per sensor
        "used_seq": {s: -1 for s in SENSORS},
        "seq": {s: 0 for s in SENSORS},
        "cam_seq": None,
        "kf": Kalman2D(),
        "staged_ema": None,
        "output": None,
        "last_send": 0.0, "last_sentence": "", "last_status_request": 0.0,
        "log_file": None, "log_writer": None, "last_log": 0.0,
        "sim": None,
    }
    parent.sensor_fusion_state = {"valid": False}

    page = QWidget()
    page.setObjectName("SensorFusionPage")
    outer = QVBoxLayout(page)
    outer.setContentsMargins(0, 0, 0, 0)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    outer.addWidget(scroll)
    container = QWidget()
    container.setObjectName("FusContainer")
    parent.fusion_container = container
    scroll.setWidget(container)
    root = QVBoxLayout(container)
    root.setContentsMargins(12, 10, 12, 10)
    root.setSpacing(10)

    def card(title=None):
        f = QFrame()
        f.setObjectName("FusCard")
        lay = QVBoxLayout(f)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        if title:
            t = QLabel(title)
            t.setObjectName("FusTitle")
            lay.addWidget(t)
        return f, lay

    def lbl(text, name="FusSub"):
        l_ = QLabel(text)
        l_.setObjectName(name)
        return l_

    def dspin(lo, hi, val, step=0.1, dec=2, suffix=""):
        sp = QDoubleSpinBox()
        sp.setRange(lo, hi)
        sp.setDecimals(dec)
        sp.setSingleStep(step)
        sp.setValue(val)
        sp.setSuffix(suffix)
        sp.setKeyboardTracking(False)
        return sp

    def pill(text, fg, bg):
        return (f"background:{bg}; color:{fg}; border:1px solid {fg}; border-radius:10px;"
                f" font-size:9px; font-weight:bold; padding:2px 8px;")

    # -------------------------------------------------------------- header
    hdr = QHBoxLayout()
    hdr.addWidget(lbl("SENSOR FUSION", "FusPageTitle"))
    hdr.addSpacing(12)
    lbl_mode_pill = QLabel("")
    hdr.addWidget(lbl_mode_pill)
    hdr.addSpacing(6)
    lbl_out_pill = QLabel("")
    hdr.addWidget(lbl_out_pill)
    hdr.addStretch()
    lbl_heading = lbl("Heading: —")
    hdr.addWidget(lbl_heading)
    root.addLayout(hdr)

    # -------------------------------------------------------------- sensor cards
    sensors_row = QHBoxLayout()
    sensors_row.setSpacing(10)
    ui = {}

    for idx, s in enumerate(SENSORS, 1):
        d = DEFAULTS[s]
        f, lay = card()
        top = QHBoxLayout()
        dot = QLabel("●")
        dot.setStyleSheet(f"color:{COLORS[s]}; font-size:14px;")
        top.addWidget(dot)
        t = lbl(f"{idx}) {NAMES[s]}", "FusTitle")
        top.addWidget(t)
        top.addStretch()
        status = QLabel("NO DATA")
        top.addWidget(status)
        lay.addLayout(top)

        chk = QCheckBox("Use in fusion")
        chk.setChecked(sget(f"{s}/enabled", d["enabled"], bool))
        chk.setToolTip("Unticked = monitor only: shown on this page and the plot, but not used for the estimate.")
        lay.addWidget(chk)

        vals = QGridLayout()
        vals.setHorizontalSpacing(8)
        vals.setVerticalSpacing(0)
        v_r, v_b, v_x = lbl("—", "FusVal"), lbl("—", "FusVal"), lbl("—", "FusVal")
        for col_, (k_, v_) in enumerate((("RANGE", v_r), ("BEARING", v_b), ("AGE", v_x))):
            vals.addWidget(lbl(k_, "FusKey"), 0, col_)
            vals.addWidget(v_, 1, col_)
        lay.addLayout(vals)
        info = lbl("")
        info.setWordWrap(True)
        lay.addWidget(info)

        g = QGridLayout()
        g.setHorizontalSpacing(6)
        g.setVerticalSpacing(3)
        sp_min = dspin(0, 10000, sget(f"{s}/rmin", d["rmin"]), 0.1, 1, " m")
        sp_max = dspin(0.1, 10000, sget(f"{s}/rmax", d["rmax"]), 0.5, 1, " m")
        sp_to = dspin(0.1, 30, sget(f"{s}/timeout", d["timeout"]), 0.1, 1, " s")
        g.addWidget(lbl("Active range"), 0, 0)
        rr = QHBoxLayout()
        rr.addWidget(sp_min)
        rr.addWidget(lbl("to"))
        rr.addWidget(sp_max)
        g.addLayout(rr, 0, 1)
        g.addWidget(lbl("Stale after"), 1, 0)
        g.addWidget(sp_to, 1, 1)
        if s == GPS:
            sp_sr = dspin(0.2, 50, sget(f"{s}/sr", d["sr"]), 0.5, 1, " m")
            sp_sr.setToolTip("1σ horizontal accuracy of the GPS fix + docking-station coordinates.")
            g.addWidget(lbl("Position accuracy"), 2, 0)
            g.addWidget(sp_sr, 2, 1)
            sp_sb = None
        else:
            pct = s in (MAG, CAM)
            sp_sr = dspin(0.01, 100, sget(f"{s}/sr", d["sr"]), 1 if pct else 0.05, 0 if pct else 2,
                          " %" if pct else " m")
            sp_sr.setToolTip("1σ range uncertainty" + (" as % of range." if pct else "."))
            sp_sb = dspin(0.1, 90, sget(f"{s}/sb", d["sb"]), 0.5, 1, " °")
            sp_sb.setToolTip("1σ bearing uncertainty.")
            g.addWidget(lbl("Range σ"), 2, 0)
            g.addWidget(sp_sr, 2, 1)
            g.addWidget(lbl("Bearing σ"), 3, 0)
            g.addWidget(sp_sb, 3, 1)
        lay.addLayout(g)
        ui[s] = dict(card=f, status=status, chk=chk, r=v_r, b=v_b, x=v_x, info=info,
                     rmin=sp_min, rmax=sp_max, timeout=sp_to, sr=sp_sr, sb=sp_sb, extra=lay)
        sensors_row.addWidget(f, 1)
    root.addLayout(sensors_row)

    # ---- sensor-specific controls
    # GPS: docking-station position, satellites
    gl = QGridLayout()
    gl.setHorizontalSpacing(6)
    sp_lat = dspin(-90, 90, sget("gps/dock_lat", float(getattr(parent, "docking_station_latitude", 0.0) or 0.0)), 0.0000001, 7)
    sp_lon = dspin(-180, 180, sget("gps/dock_lon", float(getattr(parent, "docking_station_longitude", 0.0) or 0.0)), 0.0000001, 7)
    sp_sats = QSpinBox()
    sp_sats.setRange(0, 32)
    sp_sats.setValue(sget("gps/min_sats", 6, int))
    sp_depth = dspin(0, 50, sget("gps/max_depth", 0.5), 0.1, 1, " m")
    sp_depth.setToolTip("GPS is ignored when telemetry reports a depth greater than this (no fix underwater).")
    for i_, (k_, w_) in enumerate((("Dock latitude", sp_lat), ("Dock longitude", sp_lon),
                                   ("Min satellites", sp_sats), ("Max depth", sp_depth))):
        gl.addWidget(lbl(k_), i_, 0)
        gl.addWidget(w_, i_, 1)
    ui[GPS]["extra"].addLayout(gl)
    btn_dock = QPushButton("USE DOCKING STATION FROM PLAN")
    btn_dock.setToolTip("Copy the docking-station coordinates set on the Earth / Docking pages.")
    ui[GPS]["extra"].addWidget(btn_dock)

    # MAG: baseline + calibration
    ml = QGridLayout()
    ml.setHorizontalSpacing(6)
    btn_base = QPushButton("CAPTURE BASELINE")
    btn_base.setToolTip("Record the background field with the vehicle away from the dock coil (or coil off).")
    sp_cal = dspin(0.2, 10, sget("mag/cal_range", 1.0), 0.1, 2, " m")
    btn_cal = QPushButton("CALIBRATE AT")
    btn_cal.setToolTip("Place the vehicle at the given distance from the coil, then press.\n"
                       "Range is then estimated from the field anomaly (∝ 1/r³ for a coil).")
    chk_mag_brg = QCheckBox("Use bearing from field direction")
    chk_mag_brg.setChecked(sget("mag/use_bearing", False, bool))
    chk_mag_brg.setToolTip("Coil fields curve, so this bearing is only approximate — leave off until tested.")
    sp_mag_off = dspin(-180, 180, sget("mag/bearing_offset", 0.0), 1, 0, " °")
    ml.addWidget(btn_base, 0, 0, 1, 2)
    ml.addWidget(btn_cal, 1, 0)
    ml.addWidget(sp_cal, 1, 1)
    ml.addWidget(chk_mag_brg, 2, 0, 1, 2)
    ml.addWidget(lbl("Bearing offset"), 3, 0)
    ml.addWidget(sp_mag_off, 3, 1)
    ui[MAG]["extra"].addLayout(ml)
    mag_cal = {
        "base": [sget("mag/base_x", 0.0), sget("mag/base_y", 0.0), sget("mag/base_z", 0.0)],
        "has_base": sget("mag/has_base", False, bool),
        "a_cal": sget("mag/a_cal", 0.0), "r_cal": sget("mag/r_cal", 0.0),
    }

    # SONAR: mount offset, confidence
    sl = QGridLayout()
    sl.setHorizontalSpacing(6)
    sp_son_off = dspin(-180, 180, sget("sonar/bearing_offset", 0.0), 1, 1, " °")
    sp_son_off.setToolTip("Added to the sonar bearing if the Ping360 0° is not aligned with the bow.")
    sp_son_conf = QSpinBox()
    sp_son_conf.setRange(0, 100)
    sp_son_conf.setSuffix(" %")
    sp_son_conf.setValue(sget("sonar/min_conf", 0, int))
    sl.addWidget(lbl("Mount offset"), 0, 0)
    sl.addWidget(sp_son_off, 0, 1)
    sl.addWidget(lbl("Min confidence"), 1, 0)
    sl.addWidget(sp_son_conf, 1, 1)
    ui[SONAR]["extra"].addLayout(sl)

    # CAMERA: lock, confidence, range use
    cl = QGridLayout()
    cl.setHorizontalSpacing(6)
    chk_cam_lock = QCheckBox("Only when DOCK LOCKED")
    chk_cam_lock.setChecked(sget("cam/require_lock", True, bool))
    chk_cam_rng = QCheckBox("Use camera range (from dock size)")
    chk_cam_rng.setChecked(sget("cam/use_range", True, bool))
    sp_cam_conf = dspin(0.05, 0.99, sget("cam/min_conf", 0.40), 0.05, 2)
    cl.addWidget(chk_cam_lock, 0, 0, 1, 2)
    cl.addWidget(chk_cam_rng, 1, 0, 1, 2)
    cl.addWidget(lbl("Min confidence"), 2, 0)
    cl.addWidget(sp_cam_conf, 2, 1)
    ui[CAM]["extra"].addLayout(cl)
    cam_btns = QHBoxLayout()
    btn_cam_start = QPushButton("START CAMERA")
    btn_cam_open = QPushButton("OPEN PAGE")
    cam_btns.addWidget(btn_cam_start)
    cam_btns.addWidget(btn_cam_open)
    ui[CAM]["extra"].addLayout(cam_btns)
    for s in SENSORS:
        ui[s]["extra"].addStretch()

    # -------------------------------------------------------------- bottom row
    bottom = QHBoxLayout()
    bottom.setSpacing(10)
    pcard, play = card("DOCK POSITION RELATIVE TO VEHICLE")
    plot = FusionPlot()
    parent.fusion_plot = plot
    play.addWidget(plot, 1)
    bottom.addWidget(pcard, 3)

    # fusion control
    fcard, flay = card("FUSION CONTROL")
    cmb_mode = QComboBox()
    cmb_mode.addItems([MODE_STAGED, MODE_KALMAN])
    cmb_mode.setCurrentText(sget("mode", MODE_STAGED, str))
    flay.addWidget(cmb_mode)
    lbl_prio = lbl("Priority (drag to reorder) — first valid sensor wins:")
    lbl_prio.setWordWrap(True)
    flay.addWidget(lbl_prio)
    lst = QListWidget()
    lst.setDragDropMode(QAbstractItemView.InternalMove)
    lst.setFixedHeight(122)
    order = [x for x in str(sget("priority", "mag,sonar,cam,gps", str)).split(",") if x in SENSORS]
    for s in SENSORS:
        if s not in order:
            order.append(s)
    for s in order:
        it = QListWidgetItem(f"●  {LABELS[s]}")
        it.setData(Qt.UserRole, s)
        it.setForeground(QColor(COLORS[s]))
        lst.addItem(it)
    flay.addWidget(lst)
    chk_cam_brg = QCheckBox("Camera refines bearing when it sees the dock")
    chk_cam_brg.setChecked(sget("cam_refines", True, bool))
    flay.addWidget(chk_cam_brg)
    fg = QGridLayout()
    fg.setHorizontalSpacing(6)
    fg.setVerticalSpacing(3)
    sp_q = dspin(0.01, 5, sget("process_noise", 0.4), 0.05, 2, " m/s")
    sp_q.setToolTip("How fast the dock can appear to move relative to the vehicle (vehicle speed + turns).")
    sp_gate = dspin(1, 20, sget("gate", 4.0), 0.5, 1, " σ")
    sp_gate.setToolTip("Measurements further than this from the estimate are rejected as outliers.")
    sp_db = dspin(0, 45, sget("deadband", 5.0), 0.5, 1, " °")
    sp_db.setToolTip("Steering dead-band: within ± this bearing the hint is AHEAD.")
    for i_, (k_, w_) in enumerate((("Process noise", sp_q), ("Outlier gate", sp_gate), ("Steer dead-band", sp_db))):
        fg.addWidget(lbl(k_), i_, 0)
        fg.addWidget(w_, i_, 1)
    flay.addLayout(fg)
    btn_reset = QPushButton("RESET ESTIMATE")
    flay.addWidget(btn_reset)
    chk_sim = QCheckBox("Simulated sensors (dry test)")
    chk_sim.setToolTip("Generates a simulated approach with all four sensors so the fusion settings can be\n"
                       "tried without the vehicle. Real telemetry is ignored while this is on.")
    flay.addWidget(chk_sim)
    flay.addStretch()
    bottom.addWidget(fcard, 2)

    # output
    ocard, olay = card("FUSED OUTPUT")
    lbl_steer = QLabel("NO FIX")
    lbl_steer.setAlignment(Qt.AlignCenter)
    lbl_steer.setFixedHeight(40)
    olay.addWidget(lbl_steer)
    og = QGridLayout()
    og.setHorizontalSpacing(10)
    og.setVerticalSpacing(1)
    outv = {}
    for i_, (k_, n_) in enumerate((("range", "RANGE"), ("brg", "BEARING (REL)"), ("abs", "BEARING (TRUE)"),
                                   ("sig", "UNCERTAINTY 1σ"), ("src", "SOURCES"), ("stage", "STAGE"))):
        r_, c_ = divmod(i_, 2)
        og.addWidget(lbl(n_, "FusKey"), r_ * 2, c_)
        v_ = lbl("—", "FusBig" if k_ in ("range", "brg") else "FusVal")
        og.addWidget(v_, r_ * 2 + 1, c_)
        outv[k_] = v_
    outv["stage"].setStyleSheet("font-size: 11px;")
    olay.addLayout(og)
    chk_send = QCheckBox("Send fused target to vehicle")
    chk_send.setChecked(sget("send_enabled", True, bool))
    chk_send.setToolTip("Sends $FUSE,<valid>,<range m>,<rel bearing °>,<1σ m>,<sources>,<stage>\n"
                        "to the backend in this bundle. The backend validates, stores and ACKs every target.")
    olay.addWidget(chk_send)
    lbl_backend_link = QLabel("BACKEND: DISCONNECTED")
    lbl_backend_link.setAlignment(Qt.AlignCenter)
    lbl_backend_link.setMinimumHeight(28)
    olay.addWidget(lbl_backend_link)
    srow = QHBoxLayout()
    srow.addWidget(lbl("Rate"))
    sp_rate = dspin(0.5, 20, sget("send_rate", 5.0), 0.5, 1, " Hz")
    srow.addWidget(sp_rate)
    olay.addLayout(srow)
    lbl_sentence = lbl("")
    lbl_sentence.setWordWrap(True)
    lbl_sentence.setStyleSheet("font-family: Consolas, monospace; font-size: 10px;")
    olay.addWidget(lbl_sentence)
    btn_log = QPushButton("●  LOG TO CSV")
    btn_log.setCheckable(True)
    olay.addWidget(btn_log)
    olay.addStretch()
    bottom.addWidget(ocard, 2)
    root.addLayout(bottom, 1)

    # -------------------------------------------------------------- persistence
    def save():
        for s in SENSORS:
            u = ui[s]
            sset(f"{s}/enabled", int(u["chk"].isChecked()))
            sset(f"{s}/rmin", u["rmin"].value())
            sset(f"{s}/rmax", u["rmax"].value())
            sset(f"{s}/timeout", u["timeout"].value())
            sset(f"{s}/sr", u["sr"].value())
            if u["sb"] is not None:
                sset(f"{s}/sb", u["sb"].value())
        sset("gps/dock_lat", sp_lat.value())
        sset("gps/dock_lon", sp_lon.value())
        sset("gps/min_sats", sp_sats.value())
        sset("gps/max_depth", sp_depth.value())
        sset("mag/use_bearing", int(chk_mag_brg.isChecked()))
        sset("mag/bearing_offset", sp_mag_off.value())
        sset("mag/cal_range", sp_cal.value())
        sset("sonar/bearing_offset", sp_son_off.value())
        sset("sonar/min_conf", sp_son_conf.value())
        sset("cam/require_lock", int(chk_cam_lock.isChecked()))
        sset("cam/use_range", int(chk_cam_rng.isChecked()))
        sset("cam/min_conf", sp_cam_conf.value())
        sset("mode", cmb_mode.currentText())
        sset("priority", ",".join(lst.item(i).data(Qt.UserRole) for i in range(lst.count())))
        sset("cam_refines", int(chk_cam_brg.isChecked()))
        sset("process_noise", sp_q.value())
        sset("gate", sp_gate.value())
        sset("deadband", sp_db.value())
        sset("send_rate", sp_rate.value())
        sset("send_enabled", int(chk_send.isChecked()))

    def save_mag_cal():
        b = mag_cal["base"]
        sset("mag/base_x", b[0])
        sset("mag/base_y", b[1])
        sset("mag/base_z", b[2])
        sset("mag/has_base", int(mag_cal["has_base"]))
        sset("mag/a_cal", mag_cal["a_cal"])
        sset("mag/r_cal", mag_cal["r_cal"])

    # -------------------------------------------------------------- measurement layer
    def push(s, r, b, sr, sb, extra=""):
        """Store a processed measurement: range r (m or None), relative bearing b (deg or None)."""
        st["seq"][s] += 1
        st["meas"][s] = dict(t=time.monotonic(), r=r, b=b, sr=sr, sb=sb, extra=extra, seq=st["seq"][s])

    def heading():
        return st["heading"] if st["heading"] is not None else 0.0

    def ingest(data):
        """Called from the main window for every telemetry packet."""
        if chk_sim.isChecked():
            return
        now = time.monotonic()
        try:
            st["heading"] = float(data.get("yaw", data.get("heading", 0.0)) or 0.0) % 360.0
            st["heading_t"] = now
        except (TypeError, ValueError):
            pass
        depth = data.get("depth", data.get("depth_m"))
        try:
            st["depth"] = float(depth) if depth is not None else None
        except (TypeError, ValueError):
            st["depth"] = None

        # 1) GPS
        try:
            lat, lon = float(data.get("latitude", 0.0) or 0.0), float(data.get("longitude", 0.0) or 0.0)
            sats = int(float(data.get("satellites", 0) or 0))
        except (TypeError, ValueError):
            lat = lon = 0.0
            sats = 0
        if lat or lon:
            st["gps"] = (lat, lon, sats)
            dlat, dlon = sp_lat.value(), sp_lon.value()
            if not (dlat == 0.0 and dlon == 0.0):
                too_deep = st["depth"] is not None and st["depth"] > sp_depth.value()
                if sats >= sp_sats.value() and not too_deep:
                    d, brg_true = gps_range_bearing(lat, lon, dlat, dlon)
                    push(GPS, d, wrap180(brg_true - heading()), ui[GPS]["sr"].value(), None,
                         extra=f"{sats} sats · true brg {brg_true:.0f}°")
                else:
                    ui[GPS]["info"].setText(f"{sats} sats" + (" · too deep for GPS" if too_deep else
                                                               f" · need ≥ {sp_sats.value()}"))

        # 2) Magnetometer
        try:
            mx, my, mz = (float(data.get(k, 0.0) or 0.0) for k in ("mx", "my", "mz"))
        except (TypeError, ValueError):
            mx = my = mz = 0.0
        if mx or my or mz:
            st["mag_raw"] = (mx, my, mz)
            if mag_cal["has_base"]:
                bx, by, bz = mag_cal["base"]
                ax, ay, az = mx - bx, my - by, mz - bz
                a = math.sqrt(ax * ax + ay * ay + az * az)
                r = None
                if mag_cal["a_cal"] > 0 and a > 1e-6:
                    r = mag_cal["r_cal"] * (mag_cal["a_cal"] / a) ** (1.0 / 3.0)
                b = wrap180(math.degrees(math.atan2(ay, ax)) + sp_mag_off.value()) if chk_mag_brg.isChecked() else None
                if r is not None or b is not None:
                    push(MAG, r, b, None, ui[MAG]["sb"].value(), extra=f"anomaly {a:.1f} µT")
                else:
                    ui[MAG]["info"].setText(f"anomaly {a:.1f} µT · not calibrated")

        # 3) Sonar
        sr_, sb_ = data.get("sonar_range"), data.get("sonar_bearing")
        try:
            if sr_ is not None and sb_ is not None:
                conf = float(data.get("sonar_confidence", data.get("confidence", 100)) or 0)
                r = float(sr_)
                if r > 0 and conf >= sp_son_conf.value():
                    push(SONAR, r, wrap180(float(sb_) + sp_son_off.value()), ui[SONAR]["sr"].value(),
                         ui[SONAR]["sb"].value(), extra=f"conf {conf:.0f} %")
        except (TypeError, ValueError):
            pass

    parent.sensor_fusion_ingest = ingest

    def poll_camera():
        cs = getattr(parent, "camera_detection_state", None) or {}
        if chk_sim.isChecked() or not cs.get("running"):
            return
        seq = cs.get("seq")
        if seq is None or seq == st["cam_seq"]:
            return
        st["cam_seq"] = seq
        if not cs.get("detected") or cs.get("bearing_deg") is None:
            return
        if chk_cam_lock.isChecked() and not cs.get("locked"):
            return
        if (cs.get("confidence") or 0) < sp_cam_conf.value():
            return
        r = None
        if chk_cam_rng.isChecked() and cs.get("range_reliable") and cs.get("range_m"):
            r = float(cs["range_m"]) * math.cos(math.radians(cs.get("elevation_deg") or 0.0))
        push(CAM, r, float(cs["bearing_deg"]), None, ui[CAM]["sb"].value(),
             extra=f"conf {cs.get('confidence', 0):.2f} · elev {cs.get('elevation_deg', 0):+.1f}°"
                   + ("" if r is not None else " · bearing only"))

    # -------------------------------------------------------------- simulation
    def sim_step(now):
        sm = st["sim"]
        if sm is None:
            sm = st["sim"] = dict(t=now, n=-24.0, e=6.0, hdg=0.0, k=0, last={s: 0.0 for s in SENSORS})
        dt = min(0.2, now - sm["t"])
        sm["t"] = now
        sm["k"] += 1
        # vehicle steers at the dock (at 0,0) with a weave, 0.45 m/s
        dn, de = -sm["n"], -sm["e"]
        dist = math.hypot(dn, de)
        if dist < 0.6:
            st["sim"] = None
            st["kf"].reset()
            return
        want = math.degrees(math.atan2(de, dn))
        sm["hdg"] = (want + 18.0 * math.sin(sm["k"] * 0.02)) % 360.0
        sm["n"] += 0.45 * dt * math.cos(math.radians(sm["hdg"]))
        sm["e"] += 0.45 * dt * math.sin(math.radians(sm["hdg"]))
        st["heading"], st["heading_t"] = sm["hdg"], now
        rel = wrap180(want - sm["hdg"])

        def due(s, hz):
            if now - sm["last"][s] >= 1.0 / hz:
                sm["last"][s] = now
                return True
            return False

        g = random.gauss
        if dist > 4.0 and due(GPS, 1.0):                          # surface while far
            en = (g(0, 2.0), g(0, 2.0))
            r = math.hypot(dn + en[0], de + en[1])
            b = wrap180(math.degrees(math.atan2(de + en[1], dn + en[0])) - sm["hdg"])
            push(GPS, r, b, ui[GPS]["sr"].value(), None, extra="simulated · 9 sats")
        if dist < 20.0 and due(SONAR, 2.0):
            push(SONAR, max(0.1, dist + g(0, 0.08)), wrap180(rel + g(0, 1.5)), ui[SONAR]["sr"].value(),
                 ui[SONAR]["sb"].value(), extra="simulated")
        if dist < 3.5 and due(MAG, 10.0):
            push(MAG, max(0.05, dist * (1 + g(0, 0.12))), None, None, ui[MAG]["sb"].value(),
                 extra="simulated · range only")
        hf = 40.0
        if dist < 9.0 and abs(rel) < hf and due(CAM, 15.0) and random.random() > 0.1:
            push(CAM, max(0.1, dist * (1 + g(0, 0.08))), wrap180(rel + g(0, 0.8)), None, ui[CAM]["sb"].value(),
                 extra="simulated")

    # -------------------------------------------------------------- fusion step
    def valid_meas(now):
        out = {}
        for s in SENSORS:
            m = st["meas"][s]
            if not m:
                continue
            fresh = now - m["t"] <= ui[s]["timeout"].value()
            in_range = m["r"] is None or ui[s]["rmin"].value() <= m["r"] <= ui[s]["rmax"].value()
            out[s] = dict(m, fresh=fresh, in_range=in_range, use=ui[s]["chk"].isChecked() and fresh and in_range)
        return out

    def sigma_r(s, m):
        if m["r"] is None:
            return None
        v = ui[s]["sr"].value()
        return v / 100.0 * m["r"] + 0.05 if s in (MAG, CAM) else v

    def fuse_kalman(now, vm):
        import numpy as np
        kf = st["kf"]
        kf.predict(now, sp_q.value())
        used = []
        hdg = heading()
        for s in SENSORS:
            m = vm.get(s)
            if not m or not m["use"] or m["seq"] == st["used_seq"][s]:
                continue
            st["used_seq"][s] = m["seq"]
            r, b = m["r"], m["b"]
            sr = sigma_r(s, m)
            sb = math.radians(m["sb"]) if m["sb"] else None
            if r is not None and b is not None:
                beta = math.radians(hdg + b)
                z = np.array([r * math.cos(beta), r * math.sin(beta)])
                if s == GPS:
                    R = np.eye(2) * sr * sr
                else:
                    J = np.array([[math.cos(beta), -r * math.sin(beta)], [math.sin(beta), r * math.cos(beta)]])
                    R = J @ np.diag([sr * sr, sb * sb]) @ J.T + np.eye(2) * 1e-4
                if kf.x is None or kf.rejects[s] >= 8:
                    kf.init(z, R, now)
                    used.append(s)
                elif kf.update(z, kf.x, np.eye(2), R, sp_gate.value(), s):
                    used.append(s)
            elif kf.x is not None and r is not None:                       # range only
                rn = float(np.linalg.norm(kf.x))
                if rn > 1e-3:
                    H = (kf.x / rn).reshape(1, 2)
                    if kf.update(np.array([r]), np.array([rn]), H, np.array([[sr * sr]]), sp_gate.value(), s):
                        used.append(s)
            elif kf.x is not None and b is not None:                       # bearing only
                n_, e_ = kf.x
                r2 = max(1e-3, n_ * n_ + e_ * e_)
                H = np.array([[-e_ / r2, n_ / r2]])
                beta = math.radians(hdg + b)
                if kf.update(np.array([beta]), np.array([math.atan2(e_, n_)]), H, np.array([[sb * sb]]),
                             sp_gate.value(), s, wrap=True):
                    used.append(s)
        if kf.x is None:
            return bearing_only(vm)
        if now - (kf.last_update or now) > 15.0:
            kf.reset()
            return bearing_only(vm)
        n_, e_ = kf.x
        r = math.hypot(n_, e_)
        absb = math.degrees(math.atan2(e_, n_)) % 360.0
        sig = math.sqrt(max(0.0, float(np.trace(kf.P)) / 2.0))
        recent = [s for s in SENSORS if vm.get(s) and vm[s]["use"]]
        return dict(r=r, b=wrap180(absb - hdg), abs=absb, sig=sig, src=recent or used,
                    coasting=now - (kf.last_update or now) > 2.0)

    def bearing_only(vm):
        """No range available: still give a heading cue from the best bearing source."""
        prio = [lst.item(i).data(Qt.UserRole) for i in range(lst.count())]
        if chk_cam_brg.isChecked() and vm.get(CAM) and vm[CAM]["use"] and vm[CAM]["b"] is not None:
            prio = [CAM] + [p_ for p_ in prio if p_ != CAM]
        src = next((s for s in prio if vm.get(s) and vm[s]["use"] and vm[s]["b"] is not None), None)
        if src is None:
            return None
        b = vm[src]["b"]
        return dict(r=None, b=b, abs=(heading() + b) % 360.0, sig=None, src=[src], coasting=False)

    def fuse_staged(now, vm):
        prio = [lst.item(i).data(Qt.UserRole) for i in range(lst.count())]
        chosen = next((s for s in prio if vm.get(s) and vm[s]["use"] and vm[s]["r"] is not None), None)
        if chosen is None:
            st["staged_ema"] = None
            return bearing_only(vm)
        m = vm[chosen]
        r, b, srcs = m["r"], m["b"], [chosen]
        cam = vm.get(CAM)
        if chk_cam_brg.isChecked() and cam and cam["use"] and chosen != CAM:
            b = cam["b"]
            srcs.append(CAM)
        if b is None:                                        # e.g. magnetometer without bearing
            donor = next((s for s in prio if s != chosen and vm.get(s) and vm[s]["use"] and vm[s]["b"] is not None), None)
            if donor:
                b = vm[donor]["b"]
                srcs.append(donor)
        if b is None:
            return dict(r=r, b=None, abs=None, sig=sigma_r(chosen, m), src=srcs, coasting=False)
        e = st["staged_ema"]
        a = 0.5
        if e is None or e[2] != chosen:
            e = (r, b, chosen)
        else:
            e = (a * r + (1 - a) * e[0], e[1] + a * wrap180(b - e[1]), chosen)
        st["staged_ema"] = e
        return dict(r=e[0], b=wrap180(e[1]), abs=(heading() + e[1]) % 360.0, sig=sigma_r(chosen, m),
                    src=srcs, coasting=False)

    def stage_name(r):
        if r is None:
            return "BEARING ONLY"
        if r <= ui[MAG]["rmax"].value():
            return "TERMINAL · MAGNETOMETER"
        if r <= ui[SONAR]["rmax"].value():
            return "APPROACH · SONAR"
        return "TRANSIT · GPS"

    # -------------------------------------------------------------- UI refresh
    def fmt_age(dt):
        return f"{dt:.1f} s" if dt < 60 else f"{dt / 60:.0f} min"

    def set_status(s, text, fg, bg):
        ui[s]["status"].setText(text)
        ui[s]["status"].setStyleSheet(pill(text, fg, bg))

    def tick():
        now = time.monotonic()
        if chk_sim.isChecked():
            sim_step(now)
        else:
            poll_camera()
        vm = valid_meas(now)

        # sensor cards
        plot_meas = {}
        for s in SENSORS:
            u = ui[s]
            m = vm.get(s)
            if not m:
                set_status(s, "NO DATA", "#A0A0A0", "#26262D")
                u["r"].setText("—")
                u["b"].setText("—")
                u["x"].setText("—")
                continue
            u["r"].setText(f"{m['r']:.2f} m" if m["r"] is not None else "—")
            u["b"].setText(f"{m['b']:+.1f}°" if m["b"] is not None else "—")
            u["x"].setText(fmt_age(now - m["t"]))
            if m["extra"]:
                u["info"].setText(m["extra"])
            if not m["fresh"]:
                set_status(s, "STALE", "#FFB020", "#3D2A00")
            elif not m["in_range"]:
                set_status(s, "OUT OF RANGE", "#8EB7E6", "#0F223C")
            elif not u["chk"].isChecked():
                set_status(s, "MONITOR ONLY", "#A0A0A0", "#26262D")
            else:
                set_status(s, "ACTIVE", "#34D399", "#0E3B1E")
            if m["fresh"]:
                plot_meas[s] = (m["r"], m["b"], sigma_r(s, m) or 0.0, m["sb"] or 0.0)

        out = fuse_kalman(now, vm) if cmb_mode.currentText() == MODE_KALMAN else fuse_staged(now, vm)
        st["output"] = out
        hd = st["heading"]
        lbl_heading.setText(f"Heading: {hd:.1f}°" if hd is not None else "Heading: — (no AHRS)")

        if out and out.get("b") is not None:
            db = sp_db.value()
            steer = "◀  LEFT" if out["b"] < -db else ("RIGHT  ▶" if out["b"] > db else "▲  AHEAD")
            col = "#34D399" if not out.get("coasting") else "#FFB020"
            lbl_steer.setText(steer + ("   (COASTING)" if out.get("coasting") else ""))
            lbl_steer.setStyleSheet(f"background:#0B1F17; color:{col}; border:1px solid {col}; border-radius:6px;"
                                    " font-size:16px; font-weight:bold; letter-spacing:2px;")
            outv["range"].setText(f"{out['r']:.2f} m" if out["r"] is not None else "—")
            outv["brg"].setText(f"{out['b']:+.1f}°")
            outv["abs"].setText(f"{out['abs']:.1f}°" if out.get("abs") is not None else "—")
            outv["sig"].setText(f"± {out['sig']:.2f} m" if out.get("sig") is not None else "—")
            outv["src"].setText(" + ".join(SHORT[s] for s in out["src"]))
            outv["stage"].setText(stage_name(out["r"]))
            if out["r"] is not None:
                lbl_out_pill.setText("FIX")
                lbl_out_pill.setStyleSheet(pill("FIX", "#34D399", "#0E3B1E"))
            else:
                lbl_out_pill.setText("BEARING ONLY")
                lbl_out_pill.setStyleSheet(pill("", "#FFB020", "#3D2A00"))
            plot.fused = (out["r"], out["b"], out.get("sig") or 0.0) if out["r"] is not None else None
        else:
            lbl_steer.setText("NO FIX" if not out else "RANGE ONLY — NO BEARING")
            lbl_steer.setStyleSheet("background:#26262D; color:#A0A0A0; border:1px solid #A0A0A0; border-radius:6px;"
                                    " font-size:14px; font-weight:bold; letter-spacing:2px;")
            for k_ in outv:
                outv[k_].setText("—")
            if out and out.get("r") is not None:
                outv["range"].setText(f"{out['r']:.2f} m")
            lbl_out_pill.setText("NO FIX")
            lbl_out_pill.setStyleSheet(pill("NO FIX", "#A0A0A0", "#26262D"))
            plot.fused = None
        lbl_mode_pill.setText("STAGED" if cmb_mode.currentText() == MODE_STAGED else "KALMAN")
        lbl_mode_pill.setStyleSheet(pill("", "#00E5FF", "#082A33"))

        plot.meas = plot_meas
        plot.rings = {"sonar": ui[SONAR]["rmax"].value(), "mag": ui[MAG]["rmax"].value()}
        cs = getattr(parent, "camera_detection_state", None) or {}
        plot.hfov = float(cs.get("hfov_deg") or 80.0)
        plot.sim = chk_sim.isChecked()
        plot.update()

        valid = bool(out and out.get("b") is not None)
        parent.sensor_fusion_state = dict(
            valid=valid, range_m=out["r"] if out else None, bearing_rel_deg=out["b"] if out else None,
            bearing_true_deg=out.get("abs") if out else None, sigma_m=out.get("sig") if out else None,
            sources=list(out["src"]) if out else [], stage=stage_name(out["r"]) if out else None,
            mode="staged" if cmb_mode.currentText() == MODE_STAGED else "kalman", timestamp=time.time(),
            simulated=chk_sim.isChecked(),
            backend_connected=bool((getattr(parent, "fusion_backend_state", None) or {}).get("connected")),
            backend_valid=bool((getattr(parent, "fusion_backend_state", None) or {}).get("valid")))

        # Backend acknowledgement / round-trip status.
        th = getattr(parent, "telemetry_thread", None)
        backend_state = getattr(parent, "fusion_backend_state", None) or {}
        ack_t = float(backend_state.get("timestamp") or 0.0)
        ack_age = now - ack_t if ack_t > 0.0 else float("inf")
        thread_live = bool(th is not None and th.isRunning())
        backend_error = str(backend_state.get("error") or "")
        if not thread_live:
            lbl_backend_link.setText("BACKEND: DISCONNECTED")
            lbl_backend_link.setStyleSheet(pill("", "#A0A0A0", "#26262D"))
        elif backend_error and ack_age <= 3.0:
            lbl_backend_link.setText("BACKEND: ERROR · " + backend_error[:48])
            lbl_backend_link.setStyleSheet(pill("", "#FF6B6B", "#3A1414"))
        elif backend_state.get("connected") and ack_age <= max(2.0, 4.0 / max(0.5, sp_rate.value())):
            state_txt = "FIX" if backend_state.get("valid") else "NO FIX"
            lbl_backend_link.setText(f"BACKEND: CONNECTED · {state_txt}")
            lbl_backend_link.setStyleSheet(pill("", "#34D399", "#0E3B1E"))
        else:
            lbl_backend_link.setText("BACKEND: WAITING FOR ACK")
            lbl_backend_link.setStyleSheet(pill("", "#FFB020", "#3D2A00"))

        # send to vehicle
        if chk_send.isChecked() and now - st["last_send"] >= 1.0 / sp_rate.value():
            st["last_send"] = now
            if valid:
                rng_txt = f"{out['r']:.2f}" if out["r"] is not None else ""
                sent = (f"$FUSE,1,{rng_txt},{out['b']:.1f},{(out.get('sig') or 0):.2f},"
                        f"{''.join(SHORT[s] for s in out['src'])},{stage_name(out['r']).split(' ')[0]}")
            else:
                sent = "$FUSE,0,,,,,"
            ok = False
            allow_simulator_protocol = bool(th is not None and getattr(th, "port", None) == "SIMULATOR")
            if th is not None and th.isRunning() and (not chk_sim.isChecked() or allow_simulator_protocol):
                try:
                    ok = bool(th.write_data(sent))
                except Exception:
                    ok = False
            lbl_sentence.setText(("Queued to backend: " if ok else "Not connected — would send: ") + sent)

        # CSV log (10 Hz)
        if st["log_writer"] and now - st["last_log"] >= 0.1:
            st["last_log"] = now
            row = [time.strftime("%H:%M:%S"), f"{now:.2f}", f"{hd if hd is not None else ''}"]
            for s in SENSORS:
                m = vm.get(s)
                row += [f"{m['r']:.3f}" if m and m["r"] is not None and m["fresh"] else "",
                        f"{m['b']:.2f}" if m and m["b"] is not None and m["fresh"] else "",
                        int(bool(m and m["use"]))]
            row += [f"{out['r']:.3f}" if out and out.get("r") is not None else "", f"{out['b']:.2f}" if out and out.get("b") is not None else "",
                    f"{out.get('sig') or 0:.3f}" if out else "", "".join(SHORT[s] for s in out["src"]) if out else ""]
            st["log_writer"].writerow(row)

    timer = QTimer(page)
    timer.timeout.connect(tick)
    timer.start(50)                      # 20 Hz

    # -------------------------------------------------------------- handlers
    def on_dock_from_plan():
        lat = float(getattr(parent, "docking_station_latitude", 0.0) or 0.0)
        lon = float(getattr(parent, "docking_station_longitude", 0.0) or 0.0)
        if lat == 0.0 and lon == 0.0:
            ui[GPS]["info"].setText("No docking station set on the Earth / Docking page yet.")
            return
        sp_lat.setValue(lat)
        sp_lon.setValue(lon)
        save()

    def on_baseline():
        if not st["mag_raw"]:
            ui[MAG]["info"].setText("No magnetometer data yet.")
            return
        mag_cal["base"] = list(st["mag_raw"])
        mag_cal["has_base"] = True
        save_mag_cal()
        ui[MAG]["info"].setText("Baseline captured: " + ", ".join(f"{v:.1f}" for v in mag_cal["base"]) + " µT")

    def on_calibrate():
        if not st["mag_raw"] or not mag_cal["has_base"]:
            ui[MAG]["info"].setText("Capture a baseline first (away from the coil), then calibrate.")
            return
        bx, by, bz = mag_cal["base"]
        mx, my, mz = st["mag_raw"]
        a = math.sqrt((mx - bx) ** 2 + (my - by) ** 2 + (mz - bz) ** 2)
        if a < 0.5:
            ui[MAG]["info"].setText(f"Anomaly only {a:.2f} µT — move closer to the coil or check it is on.")
            return
        mag_cal["a_cal"], mag_cal["r_cal"] = a, sp_cal.value()
        save_mag_cal()
        ui[MAG]["info"].setText(f"Calibrated: {a:.1f} µT at {sp_cal.value():.2f} m")

    def on_reset():
        st["kf"].reset()
        st["staged_ema"] = None
        st["used_seq"] = {s: -1 for s in SENSORS}

    def on_sim(on):
        st["sim"] = None
        st["meas"] = {s: None for s in SENSORS}
        on_reset()

    def on_log(on):
        if on:
            d = Path.home() / "MarineGS_Recordings" / "sensor_fusion"
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"fusion_{time.strftime('%Y%m%d_%H%M%S')}.csv"
            f = open(path, "w", newline="")
            w = csv.writer(f)
            hdr_ = ["time", "t_mono", "heading"]
            for s in SENSORS:
                hdr_ += [f"{s}_range", f"{s}_bearing", f"{s}_used"]
            hdr_ += ["fused_range", "fused_bearing", "fused_sigma", "fused_sources"]
            w.writerow(hdr_)
            st["log_file"], st["log_writer"] = f, w
            btn_log.setText("■  STOP LOG")
            btn_log.setToolTip(str(path))
            lbl_sentence.setText(f"Logging to {path}")
        else:
            if st["log_file"]:
                st["log_file"].close()
            st["log_file"] = st["log_writer"] = None
            btn_log.setText("●  LOG TO CSV")

    def on_send_toggle(on):
        save()
        th = getattr(parent, "telemetry_thread", None)
        if th is None or not th.isRunning():
            return
        try:
            if on:
                th.write_data("$FUSE,STATUS")
                st["last_send"] = 0.0
            else:
                # Explicitly invalidate the backend copy when the operator stops
                # transmitting; the backend also has a freshness timeout.
                th.write_data("$FUSE,0,,,,,")
        except Exception:
            pass

    def open_camera_page():
        if hasattr(parent, "stacked_widget"):
            parent.stacked_widget.setCurrentIndex(8)
            if hasattr(parent, "sidebar"):
                parent.sidebar.select_page(8)

    def start_camera():
        if hasattr(parent, "camera_detection_start"):
            parent.camera_detection_start()

    btn_dock.clicked.connect(on_dock_from_plan)
    btn_base.clicked.connect(on_baseline)
    btn_cal.clicked.connect(on_calibrate)
    btn_reset.clicked.connect(on_reset)
    chk_sim.toggled.connect(on_sim)
    chk_send.toggled.connect(on_send_toggle)
    btn_log.toggled.connect(on_log)
    btn_cam_open.clicked.connect(open_camera_page)
    btn_cam_start.clicked.connect(start_camera)
    cmb_mode.currentIndexChanged.connect(lambda *_: (on_reset(), save()))
    lst.model().rowsMoved.connect(lambda *_: save())
    for w_ in [sp_lat, sp_lon, sp_depth, sp_mag_off, sp_cal, sp_son_off, sp_cam_conf, sp_q, sp_gate, sp_db, sp_rate]:
        w_.valueChanged.connect(lambda *_: save())
    for w_ in [sp_sats, sp_son_conf]:
        w_.valueChanged.connect(lambda *_: save())
    for w_ in [chk_mag_brg, chk_cam_lock, chk_cam_rng, chk_cam_brg]:
        w_.toggled.connect(lambda *_: save())
    for s in SENSORS:
        u = ui[s]
        u["chk"].toggled.connect(lambda *_: save())
        for k_ in ("rmin", "rmax", "timeout", "sr", "sb"):
            if u[k_] is not None:
                u[k_].valueChanged.connect(lambda *_: save())

    def shutdown():
        timer.stop()
        if chk_send.isChecked():
            th = getattr(parent, "telemetry_thread", None)
            if th is not None and th.isRunning():
                try:
                    th.write_data("$FUSE,0,,,,,")
                except Exception:
                    pass
        if st["log_file"]:
            st["log_file"].close()

    parent.shutdown_sensor_fusion = shutdown
    parent._sensor_fusion_internal = st          # for diagnostics / tests
    if mag_cal["has_base"]:
        ui[MAG]["info"].setText("Baseline set" + (f" · calibrated at {mag_cal['r_cal']:.2f} m" if mag_cal["a_cal"] else
                                                   " · not calibrated"))
    else:
        ui[MAG]["info"].setText("Not calibrated — capture baseline, then calibrate at a known range.")
    apply_sensor_fusion_theme(parent, getattr(parent, "current_theme", "cockpit"))
    return page

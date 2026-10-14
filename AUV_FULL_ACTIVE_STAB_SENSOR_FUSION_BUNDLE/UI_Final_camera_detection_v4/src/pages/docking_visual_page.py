import os
import math
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QSizePolicy, QStackedWidget
)
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF
from PySide6.QtGui import (
    QPainter, QColor, QPen, QBrush, QFont, QLinearGradient,
    QPainterPath, QPixmap, QPolygonF, QRadialGradient
)


class DockingVisualScene(QWidget):
    """
    Tactical Docking Visualizer with switchable Top View (Plan)
    and Surface-Level Side View (Elevation) using transparent AUV and Docking Station graphics.
    """
    def __init__(self, parent=None, theme="cockpit"):
        super().__init__(parent if isinstance(parent, QWidget) else None)
        self.main_app = parent
        self.theme = theme
        self.view_mode = "top"  # "top" or "side"
        
        # Coordinates & Telemetry state
        self.station_lat = getattr(parent, 'docking_station_latitude', None)
        self.station_lon = getattr(parent, 'docking_station_longitude', None)
        self.station_heading = getattr(parent, 'docking_station_heading', None)
        self.auv_lat = None
        self.auv_lon = None
        self.heading = None
        self.depth = None
        self.pitch = None
        self.distance = None
        self.lat_offset = None
        self.bearing = None
        
        # Zoom and field-of-view level (1.0 = 50m full sonar range)
        self.zoom_level = 1.0
        self._pan_offset = QPointF(0.0, 0.0)
        self._pan_drag_origin = None
        
        # Animation phase for subtle water ripple motion
        self._phase = 0.0  # seconds; EM cycles at 2 Hz (0.25 s on, 0.25 s off)
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(33)  # ~30 FPS
        self._anim_timer.timeout.connect(self._on_animation_tick)
        self._anim_timer.start()
        
        # Load visual image assets (with fallback paths)
        self._load_assets()
        
        # Build UI Controls (Top-Right View Switcher)
        self._init_controls()

    def _load_assets(self):
        """Loads docking station and AUV transparent images."""
        # Project root is 3 levels up from this file (src/pages/docking_visual_page.py)
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        assets_dir = os.path.join(project_root, "assets", "docking")
        downloads_dir = os.path.expanduser(r"~\Downloads")
        
        # 1. Docking station top view (used for both top and side view)
        dock_candidates = [
            os.path.join(project_root, "src", "docking_station_top_view.png"),
            os.path.join(assets_dir, "docking_station_top_view.png"),
            os.path.join(assets_dir, "docking_station_top_view.jpeg"),
            os.path.join(downloads_dir, "docking_station_top_view.jpeg")
        ]
        self.dock_pixmap = QPixmap()
        for p in dock_candidates:
            if os.path.exists(p):
                self.dock_pixmap = QPixmap(p)
                if not self.dock_pixmap.isNull():
                    break
                    
        # 2. AUV Top View
        auv_top_candidates = [
            os.path.join(project_root, "src", "auv_top_view.png"),
            os.path.join(assets_dir, "auv_top_view.png"),
            os.path.join(assets_dir, "auv_top_view.jpeg"),
            os.path.join(downloads_dir, "auv_top_view.jpeg")
        ]
        self.auv_top_pixmap = QPixmap()
        for p in auv_top_candidates:
            if os.path.exists(p):
                self.auv_top_pixmap = QPixmap(p)
                if not self.auv_top_pixmap.isNull():
                    break

        # 3. AUV Side View
        auv_side_candidates = [
            os.path.join(assets_dir, "auv_side_view.png"),
            os.path.join(assets_dir, "auv_side_view.jpeg"),
            os.path.join(downloads_dir, "auv side view.jpeg")
        ]
        self.auv_side_pixmap = QPixmap()
        for p in auv_side_candidates:
            if os.path.exists(p):
                self.auv_side_pixmap = QPixmap(p)
                if not self.auv_side_pixmap.isNull():
                    break

        # 4. Photorealistic Sea Backgrounds
        sea_side_candidates = [
            os.path.join(assets_dir, "sea_surface_side.jpg"),
            os.path.join(assets_dir, "sea_surface_side.png"),
            r"C:\Users\jerfi\.gemini\antigravity\brain\73eb6b2e-4370-4480-9406-9512653a85ba\realistic_sea_side_view_1789949887689.jpg"
        ]
        self.sea_side_pixmap = QPixmap()
        for p in sea_side_candidates:
            if os.path.exists(p):
                self.sea_side_pixmap = QPixmap(p)
                if not self.sea_side_pixmap.isNull():
                    break

        sea_top_candidates = [
            os.path.join(assets_dir, "sea_surface_top.jpg"),
            os.path.join(assets_dir, "sea_surface_top.png"),
            r"C:\Users\jerfi\.gemini\antigravity\brain\73eb6b2e-4370-4480-9406-9512653a85ba\realistic_sea_top_view_1789949906075.jpg"
        ]
        self.sea_top_pixmap = QPixmap()
        for p in sea_top_candidates:
            if os.path.exists(p):
                self.sea_top_pixmap = QPixmap(p)
                if not self.sea_top_pixmap.isNull():
                    break

    def _init_controls(self):
        """Creates top-right switch buttons for TOP/SIDE VIEW and Range Presets (50m, 10m, 3m)."""
        self.top_button = QPushButton("TOP VIEW", self)
        self.side_button = QPushButton("SIDE VIEW", self)
        
        self.btn_range_50m = QPushButton("50m SONAR", self)
        self.btn_range_10m = QPushButton("10m CAM", self)
        self.btn_range_3m = QPushButton("3m DOCK", self)
        
        for btn in (self.top_button, self.side_button, self.btn_range_50m, self.btn_range_10m, self.btn_range_3m):
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(32)
            
        self.top_button.clicked.connect(lambda: self.set_view_mode("top"))
        self.side_button.clicked.connect(lambda: self.set_view_mode("side"))
        
        self.btn_range_50m.clicked.connect(lambda: self.set_zoom(1.0))
        self.btn_range_10m.clicked.connect(lambda: self.set_zoom(3.5))
        self.btn_range_3m.clicked.connect(lambda: self.set_zoom(8.0))
        
        self.set_view_mode("top")
        self.set_zoom(1.0)
        self._apply_control_styles()

    def set_view_mode(self, mode: str):
        self.view_mode = mode
        self.top_button.setChecked(mode == "top")
        self.side_button.setChecked(mode == "side")
        self.update()

    def set_zoom(self, zoom: float):
        self.zoom_level = zoom
        self.btn_range_50m.setChecked(abs(zoom - 1.0) < 0.1)
        self.btn_range_10m.setChecked(abs(zoom - 3.5) < 0.1)
        self.btn_range_3m.setChecked(abs(zoom - 8.0) < 0.1)
        self.update()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        factor = 1.15 if delta > 0 else (1.0 / 1.15)
        self.zoom_level = max(0.5, min(12.0, self.zoom_level * factor))
        self.btn_range_50m.setChecked(abs(self.zoom_level - 1.0) < 0.1)
        self.btn_range_10m.setChecked(abs(self.zoom_level - 3.5) < 0.1)
        self.btn_range_3m.setChecked(abs(self.zoom_level - 8.0) < 0.1)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._pan_drag_origin = event.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pan_drag_origin is not None and event.buttons() & Qt.LeftButton:
            current = event.position().toPoint()
            delta = current - self._pan_drag_origin
            self._pan_offset = QPointF(
                max(-self.width() * 0.5, min(self.width() * 0.5, self._pan_offset.x() + delta.x())),
                max(-self.height() * 0.5, min(self.height() * 0.5, self._pan_offset.y() + delta.y()))
            )
            self._pan_drag_origin = current
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._pan_drag_origin is not None:
            self._pan_drag_origin = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def set_theme(self, theme: str):
        self.theme = theme
        self._apply_control_styles()
        self.update()

    def _apply_control_styles(self):
        if self.theme == "cockpit":
            btn_style = """
                QPushButton {
                    background-color: #0F172A;
                    color: #94A3B8;
                    border: 1px solid #334155;
                    border-radius: 6px;
                    padding: 0 12px;
                    font-family: 'Google Sans', 'Segoe UI', sans-serif;
                    font-size: 11px;
                    font-weight: bold;
                    letter-spacing: 0.5px;
                }
                QPushButton:hover {
                    background-color: #1E293B;
                    color: #00E5FF;
                    border-color: #0284C7;
                }
                QPushButton:checked {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #0284C7, stop:1 #0369A1);
                    color: #FFFFFF;
                    border: 1px solid #00E5FF;
                }
            """
        else: # light theme
            btn_style = """
                QPushButton {
                    background-color: #FFFFFF;
                    color: #475569;
                    border: 1px solid #CBD5E1;
                    border-radius: 6px;
                    padding: 0 12px;
                    font-family: 'Google Sans', 'Segoe UI', sans-serif;
                    font-size: 11px;
                    font-weight: bold;
                    letter-spacing: 0.5px;
                }
                QPushButton:hover {
                    background-color: #F8FAFC;
                    color: #0284C7;
                    border-color: #94A3B8;
                }
                QPushButton:checked {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #0284C7, stop:1 #0369A1);
                    color: #FFFFFF;
                    border: 1px solid #0284C7;
                }
            """
        for btn in (self.top_button, self.side_button, self.btn_range_50m, self.btn_range_10m, self.btn_range_3m):
            btn.setStyleSheet(btn_style)

    def resizeEvent(self, event):
        margin = 18
        gap = 8
        btn_w = 92
        btn_h = 32
        
        right_x = self.width() - margin
        self.side_button.setGeometry(right_x - btn_w, margin, btn_w, btn_h)
        self.top_button.setGeometry(right_x - btn_w * 2 - gap, margin, btn_w, btn_h)
        
        r_start = right_x - btn_w * 2 - gap - (gap * 2)
        self.btn_range_50m.setGeometry(r_start - btn_w, margin, btn_w, btn_h)
        self.btn_range_10m.setGeometry(r_start - btn_w * 2 - gap, margin, btn_w, btn_h)
        self.btn_range_3m.setGeometry(r_start - btn_w * 3 - gap * 2, margin, btn_w, btn_h)
        super().resizeEvent(event)

    def _on_animation_tick(self):
        self._phase = (self._phase + self._anim_timer.interval() / 1000.0) % 1000.0
        self.update()

    def clear_vehicle_position(self):
        """Hide vehicle GPS-derived data until a valid connected fix arrives."""
        self.auv_lat = None
        self.auv_lon = None
        self.heading = None
        self.depth = None
        self.pitch = None
        self.distance = None
        self.lat_offset = None
        self.bearing = None
        self.update()

    def update_positions(self, station_lat, station_lon, auv_lat, auv_lon, heading=None, depth=None, pitch=None, station_heading=None):
        """Updates real-time GPS coordinates and telemetry."""
        self.station_lat = float(station_lat) if station_lat is not None else None
        self.station_lon = float(station_lon) if station_lon is not None else None
        self.station_heading = float(station_heading) if station_heading is not None else self.station_heading
        self.auv_lat = float(auv_lat) if auv_lat is not None else None
        self.auv_lon = float(auv_lon) if auv_lon is not None else None
        self.heading = float(heading) if heading is not None else None
        self.depth = float(depth) if depth is not None else None
        self.pitch = float(pitch) if pitch is not None else None
        
        # Calculate real-world displacement in meters
        coords = (self.station_lat, self.station_lon, self.auv_lat, self.auv_lon)
        if all(v is not None and math.isfinite(v) for v in coords) and not (self.auv_lat == 0 and self.auv_lon == 0):
            d_north = (self.auv_lat - self.station_lat) * 111319.9
            d_east = (self.auv_lon - self.station_lon) * 111319.9 * math.cos(math.radians(self.station_lat))
            self.distance = math.hypot(d_north, d_east)
            self.lat_offset = d_north
            self.bearing = (math.degrees(math.atan2(d_east, d_north))) % 360.0
        else:
            self.distance = None
            self.lat_offset = None
            self.bearing = None
            
        self.update()

    @staticmethod
    def _fit_rect(source_pixmap, target_rect):
        """Fits pixmap into target bounding box while maintaining aspect ratio."""
        if source_pixmap.isNull() or target_rect.width() <= 0 or target_rect.height() <= 0:
            return QRectF()
        scale = min(target_rect.width() / source_pixmap.width(), target_rect.height() / source_pixmap.height())
        w = source_pixmap.width() * scale
        h = source_pixmap.height() * scale
        return QRectF(
            target_rect.x() + (target_rect.width() - w) / 2.0,
            target_rect.y() + (target_rect.height() - h) / 2.0,
            w, h
        )

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        base_color = QColor("#080D18") if self.theme == "cockpit" else QColor("#EAF4F8")
        painter.fillRect(self.rect(), base_color)
        painter.translate(self._pan_offset)
        
        w = self.width()
        h = self.height()
        
        if self.view_mode == "top":
            self._paint_top_view(painter, w, h)
        else:
            self._paint_side_view(painter, w, h)

    def _draw_range_badge(self, painter: QPainter, cx: float, cy: float, text: str, col: QColor):
        """Draws a tactical range pill badge centered at (cx, cy)."""
        painter.save()
        painter.setFont(QFont("Google Sans", 7, QFont.Bold))
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(text) + 14
        th = 18
        rect = QRectF(cx - tw / 2.0, cy - th / 2.0, tw, th)

        bg_col = QColor(8, 16, 28, 225) if self.theme == "cockpit" else QColor(255, 255, 255, 230)
        painter.setBrush(QBrush(bg_col))
        painter.setPen(QPen(QColor(col.red(), col.green(), col.blue(), 160), 1))
        painter.drawRoundedRect(rect, 4, 4)

        painter.setPen(col)
        painter.drawText(rect, Qt.AlignCenter, text)
        painter.restore()

    def _paint_range_circles(self, painter: QPainter, origin_x: float, origin_y: float, px_per_m: float, w: int, h: int, is_side_view: bool = False):
        """
        Renders concentric 50m Sonar, 10m Camera, and 3m Magnetic Field surrounding circles
        with slow wave emission and clear range badges. No heart shape.
        """
        r_3m = 3.0 * px_per_m
        r_10m = 10.0 * px_per_m
        r_50m = 50.0 * px_per_m

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        # 1. 50m Sonar Range Arc / Circle
        sonar_col = QColor(16, 185, 129)
        sonar_pen = QPen(QColor(sonar_col.red(), sonar_col.green(), sonar_col.blue(), 130), 1.4, Qt.DashLine)
        painter.setBrush(Qt.NoBrush)

        if not is_side_view:
            for m in (20.0, 30.0, 40.0):
                rm = m * px_per_m
                painter.setPen(QPen(QColor(sonar_col.red(), sonar_col.green(), sonar_col.blue(), 25), 1.0, Qt.DotLine))
                painter.drawEllipse(QPointF(origin_x, origin_y), rm, rm)

            painter.setPen(sonar_pen)
            painter.drawEllipse(QPointF(origin_x, origin_y), r_50m, r_50m)

            target_y = max(70.0, origin_y - r_50m * 0.30)
            delta_y = origin_y - target_y
            sin_val = min(0.95, max(0.05, delta_y / r_50m))
            ang50 = math.asin(sin_val)
            tag50_x = min(w - 110.0, origin_x + r_50m * math.cos(ang50))
            tag50_y = origin_y - r_50m * math.sin(ang50)
            self._draw_range_badge(painter, tag50_x, tag50_y, "SONAR RANGE • 50m", sonar_col)
        else:
            bound_rect50 = QRectF(origin_x - r_50m, origin_y - r_50m, r_50m * 2.0, r_50m * 2.0)
            painter.setPen(sonar_pen)
            painter.drawArc(bound_rect50, int(-85 * 16), int(170 * 16))

            target_y = max(70.0, origin_y - r_50m * 0.28)
            delta_y = origin_y - target_y
            sin_val = min(0.95, max(0.05, delta_y / r_50m))
            ang50 = math.asin(sin_val)
            tag50_x = min(w - 110.0, origin_x + r_50m * math.cos(ang50))
            tag50_y = origin_y - r_50m * math.sin(ang50)
            self._draw_range_badge(painter, tag50_x, tag50_y, "SONAR RANGE • 50m", sonar_col)

        # 2. 10m Camera Range Circle (360 degrees)
        cam_col = QColor(56, 189, 248)
        cam_pen = QPen(QColor(cam_col.red(), cam_col.green(), cam_col.blue(), 170), 1.5, Qt.DashLine)
        painter.setPen(cam_pen)
        painter.setBrush(Qt.NoBrush)

        painter.drawEllipse(QPointF(origin_x, origin_y), r_10m, r_10m)
        for deg in (0, 90, 180, 270):
            rad = math.radians(deg)
            cx1 = origin_x + (r_10m - 5) * math.cos(rad)
            cy1 = origin_y + (r_10m - 5) * math.sin(rad)
            cx2 = origin_x + (r_10m + 5) * math.cos(rad)
            cy2 = origin_y + (r_10m + 5) * math.sin(rad)
            painter.drawLine(QPointF(cx1, cy1), QPointF(cx2, cy2))

        ang10 = math.radians(48.0 if not is_side_view else 42.0)
        tag10_x = origin_x + r_10m * math.cos(ang10)
        tag10_y = origin_y - r_10m * math.sin(ang10)
        self._draw_range_badge(painter, tag10_x, tag10_y, "CAMERA RANGE • 10m", cam_col)

        # 3. 3m Magnetic Field Surrounding Circle & Slow Wave Emission (No heart shape)
        em_col = QColor(0, 229, 255) if self.theme == "cockpit" else QColor(2, 132, 199)
        em_glow = QColor(56, 189, 248) if self.theme == "cockpit" else QColor(14, 165, 233)
        em_active = (self._phase % 0.5) < 0.25  # 2 Hz pulse: 250 ms on, 250 ms off
        in_em_range = self.distance is not None and self.distance <= 3.0
        if in_em_range:
            em_col = QColor(0, 255, 210) if self.theme == "cockpit" else QColor(5, 150, 105)
            em_glow = QColor(52, 211, 153) if self.theme == "cockpit" else QColor(16, 185, 129)

        if em_active:
            pulse_progress = (self._phase % 0.5) / 0.25
            num_waves = 3
            for i in range(num_waves):
                progress = (pulse_progress + i / float(num_waves)) % 1.0
                r = max(2.0, progress * r_3m)
                attenuation = math.pow(1.0 - progress, 1.25)
                alpha = int(attenuation * (190 if self.theme == "cockpit" else 150))
                if alpha <= 3:
                    continue

                painter.setPen(QPen(QColor(em_glow.red(), em_glow.green(), em_glow.blue(), int(alpha * 0.35)), 3.2))
                painter.drawEllipse(QPointF(origin_x, origin_y), r, r)
                painter.setPen(QPen(QColor(em_col.red(), em_col.green(), em_col.blue(), alpha), 1.5))
                painter.drawEllipse(QPointF(origin_x, origin_y), r, r)

        # 3.0m Physical Field Boundary Circle (Surrounding docking station 360 deg)
        em_bound_pen = QPen(QColor(em_col.red(), em_col.green(), em_col.blue(), 210), 1.6, Qt.DashLine)
        painter.setPen(em_bound_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(origin_x, origin_y), r_3m, r_3m)

        ang3 = math.radians(70.0 if not is_side_view else 55.0)
        tag3_x = origin_x + r_3m * math.cos(ang3)
        tag3_y = origin_y - r_3m * math.sin(ang3) - 10
        tag_text = "EM ACTIVE • 3m" if in_em_range and em_active else "EM FIELD • 3m"
        self._draw_range_badge(painter, tag3_x, tag3_y, tag_text, em_col)

        # Central station emitter beacon glow
        emitter_radius = max(6.0, min(16.0, r_3m * 0.18))
        if em_active:
            glow_radial = QRadialGradient(origin_x, origin_y, emitter_radius)
            glow_radial.setColorAt(0.0, QColor(em_col.red(), em_col.green(), em_col.blue(), 225))
            glow_radial.setColorAt(1.0, QColor(em_col.red(), em_col.green(), em_col.blue(), 0))
            painter.setBrush(QBrush(glow_radial))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(origin_x, origin_y), emitter_radius, emitter_radius)

        painter.restore()

    def _paint_top_view(self, painter: QPainter, w: int, h: int):
        """Top-Down Tactical Plan View."""
        # 1. Background Realistic Sea Texture & Coordinate Grid
        if not self.sea_top_pixmap.isNull():
            background_rect = QRectF(-w * 0.5, -h * 0.5, w * 2.0, h * 2.0)
            painter.drawPixmap(background_rect, self.sea_top_pixmap, QRectF(self.sea_top_pixmap.rect()))
            if self.theme == "cockpit":
                painter.fillRect(background_rect, QColor(6, 14, 26, 150))
                grid_pen = QPen(QColor(0, 229, 255, 30), 1, Qt.SolidLine)
                axis_pen = QPen(QColor(0, 229, 255, 75), 1.2, Qt.DashLine)
            else:
                painter.fillRect(background_rect, QColor(240, 248, 255, 95))
                grid_pen = QPen(QColor(2, 132, 199, 35), 1, Qt.SolidLine)
                axis_pen = QPen(QColor(2, 132, 199, 85), 1.2, Qt.DashLine)
        else:
            if self.theme == "cockpit":
                painter.fillRect(self.rect(), QColor("#080D18"))
                grid_pen = QPen(QColor(24, 40, 68), 1, Qt.SolidLine)
                axis_pen = QPen(QColor(0, 229, 255, 60), 1.2, Qt.DashLine)
            else:
                painter.fillRect(self.rect(), QColor("#EAF4F8"))
                grid_pen = QPen(QColor("#D5E4EA"), 1, Qt.SolidLine)
                axis_pen = QPen(QColor(2, 132, 199, 60), 1.2, Qt.DashLine)
            
        painter.setPen(grid_pen)
        step = 48
        for x in range(-w, 2 * w, step):
            painter.drawLine(x, -h, x, 2 * h)
        for y in range(-h, 2 * h, step):
            painter.drawLine(-w, y, 2 * w, y)
            
        # Center approach datum axis
        center_y = h * 0.50
        origin_x = w * 0.18
        origin_y = center_y
        painter.setPen(axis_pen)
        painter.drawLine(-w, center_y, 2 * w, center_y)

        # 50m scale computation
        view_w = (w * 0.94) - origin_x
        base_px_per_m = view_w / 50.0
        px_per_m = base_px_per_m * self.zoom_level

        # Draw 50m, 10m, and 3m range circles with slow wave emission
        self._paint_range_circles(painter, origin_x, origin_y, px_per_m, w, h, is_side_view=False)

        # 1.9m physical scaling for Docking Station and AUV (1900 mm)
        w_phys = 1.9 * px_per_m
        w_draw = max(54.0, w_phys)
        h_dock = w_draw / 1.494
        if not self.dock_pixmap.isNull():
            painter.save()
            painter.translate(origin_x, origin_y)
            if self.station_heading is not None:
                painter.rotate(self.station_heading + 270.0)
            painter.drawPixmap(QRectF(-w_draw / 2.0, -h_dock / 2.0, w_draw, h_dock),
                               self.dock_pixmap, QRectF(self.dock_pixmap.rect()))
            painter.restore()

        # AUV Top View
        w_auv = w_draw
        h_auv = w_draw / 2.50
        # Project the real GPS offset into a local east/north screen plane.
        guide_color = QColor(0, 229, 255) if self.theme == "cockpit" else QColor("#0284C7")
        if self.distance is not None and self.lat_offset is not None and self.bearing is not None:
            d_east = math.sin(math.radians(self.bearing)) * self.distance
            d_north = math.cos(math.radians(self.bearing)) * self.distance
            auv_center_x = origin_x + d_east * px_per_m
            auv_center_y = origin_y - d_north * px_per_m
            painter.setPen(QPen(guide_color, 1.6, Qt.DashLine))
            painter.drawLine(QPointF(origin_x, origin_y), QPointF(auv_center_x, auv_center_y))

            if not self.auv_top_pixmap.isNull():
                painter.save()
                painter.translate(auv_center_x, auv_center_y)
                if self.heading is not None:
                    painter.rotate(self.heading - 270.0)
                painter.drawPixmap(QRectF(-w_auv / 2.0, -h_auv / 2.0, w_auv, h_auv),
                                   self.auv_top_pixmap, QRectF(self.auv_top_pixmap.rect()))
                painter.restore()

            mid_x = (origin_x + auv_center_x) / 2.0
            mid_y = (origin_y + auv_center_y) / 2.0
            tag_text = f"DISTANCE: {self.distance:.2f} m"
            painter.setFont(QFont("Google Sans", 9, QFont.Bold))
            tag_w, tag_h = 132, 22
            tag_rect = QRectF(mid_x - tag_w / 2, mid_y - tag_h / 2 - 16, tag_w, tag_h)
            painter.setPen(QPen(guide_color, 1))
            tag_bg = QColor(10, 18, 32, 220) if self.theme == "cockpit" else QColor(255, 255, 255, 230)
            painter.setBrush(QBrush(tag_bg))
            painter.drawRoundedRect(tag_rect, 4, 4)
            painter.setPen(guide_color)
            painter.drawText(tag_rect, Qt.AlignCenter, tag_text)

    def _paint_side_view(self, painter: QPainter, w: int, h: int):
        """Surface-Level Elevation Side View ('surface level not in water')."""
        water_y = h * 0.577
        origin_x = w * 0.18
        origin_y = water_y
        
        # 1. Photorealistic Sea Background (Sky, horizon, and underwater god rays)
        if not self.sea_side_pixmap.isNull():
            background_rect = QRectF(-w * 0.5, -h * 0.5, w * 2.0, h * 2.0)
            painter.drawPixmap(background_rect, self.sea_side_pixmap, QRectF(self.sea_side_pixmap.rect()))
            if self.theme == "cockpit":
                tactical_tint = QLinearGradient(0, 0, 0, h)
                tactical_tint.setColorAt(0.0, QColor(8, 16, 30, 80))
                tactical_tint.setColorAt(0.55, QColor(4, 10, 22, 30))
                tactical_tint.setColorAt(1.0, QColor(2, 8, 18, 110))
                painter.fillRect(background_rect, tactical_tint)
            else:
                light_tint = QLinearGradient(0, 0, 0, h)
                light_tint.setColorAt(0.0, QColor(255, 255, 255, 20))
                light_tint.setColorAt(1.0, QColor(2, 40, 70, 20))
                painter.fillRect(background_rect, light_tint)
        else:
            sky = QLinearGradient(0, 0, 0, water_y)
            if self.theme == "cockpit":
                sky.setColorAt(0.0, QColor("#0A1424"))
                sky.setColorAt(1.0, QColor("#132844"))
            else:
                sky.setColorAt(0.0, QColor("#DDF2FA"))
                sky.setColorAt(1.0, QColor("#F8FCFD"))
            painter.fillRect(QRectF(0, 0, w, water_y), sky)
            
            water_body = QLinearGradient(0, water_y, 0, h)
            if self.theme == "cockpit":
                water_body.setColorAt(0.0, QColor("#073352"))
                water_body.setColorAt(1.0, QColor("#021627"))
            else:
                water_body.setColorAt(0.0, QColor("#4BA4C5"))
                water_body.setColorAt(1.0, QColor("#227694"))
            painter.fillRect(QRectF(0, water_y, w, h - water_y), water_body)
        
        # 2. Gentle Dynamic Surface Waves along the Waterline
        for band in range(3):
            y_off = water_y + band * 6
            amp = 1.6 + (band % 2) * 0.8
            wl = 110 + band * 35
            path = QPainterPath()
            path.moveTo(0, y_off)
            for x in range(0, w + 20, 20):
                y = y_off + math.sin((x / wl) + self._phase + band * 0.8) * amp
                path.lineTo(x, y)
            ripple_alpha = 95 if band == 0 else max(15, 55 - band * 15)
            ripple_color = QColor(0, 229, 255, ripple_alpha) if self.theme == "cockpit" else QColor(255, 255, 255, ripple_alpha)
            painter.setPen(QPen(ripple_color, 1.8 if band == 0 else 1.0))
            painter.drawPath(path)
            
        # 50m scale computation
        view_w = (w * 0.94) - origin_x
        base_px_per_m = view_w / 50.0
        px_per_m = base_px_per_m * self.zoom_level

        # Draw 50m, 10m, and 3m range circles with slow wave emission
        self._paint_range_circles(painter, origin_x, origin_y, px_per_m, w, h, is_side_view=True)

        # 1.9m physical scaling for Docking Station and AUV (1900 mm)
        w_phys = 1.9 * px_per_m
        w_draw = max(54.0, w_phys)
        h_dock = w_draw / 1.494
        if not self.dock_pixmap.isNull():
            painter.save()
            painter.translate(origin_x, origin_y)
            painter.rotate(360.0)
            painter.drawPixmap(QRectF(-w_draw / 2.0, -h_dock / 2.0, w_draw, h_dock),
                               self.dock_pixmap, QRectF(self.dock_pixmap.rect()))
            painter.restore()

        # AUV Side View
        w_auv = w_draw
        h_auv = w_draw / 4.22
        # Surface range guideline connecting station to AUV along waterline
        line_color = QColor(0, 229, 255) if self.theme == "cockpit" else QColor("#0284C7")
        if self.distance is not None and self.depth is not None:
            auv_center_x = origin_x + self.distance * px_per_m
            auv_center_y = origin_y + self.depth * px_per_m
            painter.setPen(QPen(line_color, 1.6, Qt.DashLine))
            painter.drawLine(QPointF(origin_x, origin_y), QPointF(auv_center_x, auv_center_y))

            if not self.auv_side_pixmap.isNull():
                painter.save()
                painter.translate(auv_center_x, auv_center_y)
                pitch_angle = max(-15.0, min(15.0, self.pitch or 0.0))
                painter.rotate(pitch_angle)
                painter.drawPixmap(QRectF(-w_auv / 2.0, -h_auv / 2.0, w_auv, h_auv),
                                   self.auv_side_pixmap, QRectF(self.auv_side_pixmap.rect()))
                painter.restore()

            mid_x = (origin_x + auv_center_x) / 2.0
            mid_y = (origin_y + auv_center_y) / 2.0
            tag_text = f"DISTANCE: {self.distance:.2f} m"
            painter.setFont(QFont("Google Sans", 9, QFont.Bold))
            tag_w, tag_h = 132, 24
            tag_rect = QRectF(mid_x - tag_w / 2, mid_y - tag_h - 16, tag_w, tag_h)
            painter.setPen(QPen(line_color, 1))
            tag_bg = QColor(10, 18, 32, 220) if self.theme == "cockpit" else QColor(255, 255, 255, 230)
            painter.setBrush(QBrush(tag_bg))
            painter.drawRoundedRect(tag_rect, 4, 4)
            painter.setPen(line_color)
            painter.drawText(tag_rect, Qt.AlignCenter, tag_text)

    def _paint_hud_overlay(self, painter: QPainter, w: int, h: int):
        """Tactical HUD Card overlay in top-left."""
        hud_w = 340
        hud_h = 104
        hud_rect = QRectF(18, 18, hud_w, hud_h)
        
        card_bg = QColor(10, 18, 30, 230) if self.theme == "cockpit" else QColor(255, 255, 255, 240)
        border_col = QColor(30, 58, 90) if self.theme == "cockpit" else QColor(203, 213, 225)
        text_pri = QColor(255, 255, 255) if self.theme == "cockpit" else QColor(10, 37, 64)
        text_sec = QColor(148, 163, 184) if self.theme == "cockpit" else QColor(100, 116, 139)
        accent_cyan = QColor(0, 229, 255) if self.theme == "cockpit" else QColor(2, 132, 199)
        
        painter.setPen(QPen(border_col, 1))
        painter.setBrush(QBrush(card_bg))
        painter.drawRoundedRect(hud_rect, 8, 8)
        
        # Left accent pill
        painter.setBrush(QBrush(accent_cyan))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(QRectF(18, 26, 3, 16), 1.5, 1.5)
        
        # HUD Header
        painter.setPen(text_pri)
        painter.setFont(QFont("Google Sans", 10, QFont.Bold))
        view_title = "DOCKING • TOP VIEW (PLAN)" if self.view_mode == "top" else "DOCKING • SURFACE PROFILE"
        painter.drawText(28, 39, view_title)
        
        # Sub-header telemetry lines
        painter.setFont(QFont("Consolas", 9))
        painter.setPen(text_sec)
        if self.station_lat is not None and self.station_lon is not None:
            station_text = f"STATION: {self.station_lat:.7f}°, {self.station_lon:.7f}°"
        else:
            station_text = "STATION: NOT SET"
        if self.auv_lat is not None and self.auv_lon is not None:
            auv_text = f"AUV GPS: {self.auv_lat:.7f}°, {self.auv_lon:.7f}°"
        else:
            auv_text = "AUV GPS: WAITING FOR FIX"
        painter.drawText(28, 58, station_text)
        painter.drawText(28, 74, auv_text)
        
        # Distance & alignment summary
        painter.setFont(QFont("Google Sans", 9, QFont.Bold))
        painter.setPen(accent_cyan)
        painter.drawText(28, 97, f"RANGE: {self.distance:.2f} m" if self.distance is not None else "RANGE: --")
        painter.setPen(text_pri)
        painter.drawText(150, 97, f"ALIGN: {self.lat_offset:+.2f} m" if self.lat_offset is not None else "ALIGN: --")

        if self.distance is None:
            status_text = "WAITING FOR GPS"
            status_col = text_sec
        elif self.distance < 0.5:
            status_text = "LOCKED"
            status_col = QColor("#10B981")
        elif self.distance <= 3.0:
            status_text = "EM GUIDED"
            status_col = QColor("#00E5FF") if self.theme == "cockpit" else QColor("#059669")
        elif self.distance < 15.0:
            status_text = "APPROACH"
            status_col = accent_cyan
        else:
            status_text = "TRACKING"
            status_col = text_sec
        painter.setPen(status_col)
        painter.drawText(265, 97, status_text)


def create_docking_visual_page(parent):
    """Factory function for instantiating the Docking Visual Tracking page."""
    page = QWidget()
    page.setObjectName("DockingVisualPage")
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    
    current_theme = getattr(parent, 'current_theme', 'cockpit')
    visual_scene = DockingVisualScene(parent, theme=current_theme)
    parent.docking_visual_widget = visual_scene
    
    layout.addWidget(visual_scene)
    return page

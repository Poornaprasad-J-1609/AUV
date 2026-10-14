import os
from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWebEngineWidgets import QWebEngineView


class Docking3DWidget(QWidget):
    """
    High-performance realistic 3D tactical docking environment using Three.js and WebGL.
    Provides orbit controls, realistic ocean surface, procedural 3D models,
    and 3D tactical range zones (3m EM sphere, 10m camera sphere, 50m sonar dome).
    """
    def __init__(self, parent=None, theme="cockpit"):
        super().__init__(parent)
        self.theme = theme
        self._is_loaded = False
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        self.web_view = QWebEngineView(self)
        layout.addWidget(self.web_view)
        
        # Load local HTML file
        curr_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        html_path = os.path.join(curr_dir, "web", "docking_3d.html")
        file_url = QUrl.fromLocalFile(html_path)
        
        self.web_view.loadFinished.connect(self._on_load_finished)
        self.web_view.setUrl(file_url)

    def _on_load_finished(self, ok):
        self._is_loaded = ok
        if ok and hasattr(self, '_pending_telemetry'):
            self.update_positions(*self._pending_telemetry)

    def update_positions(self, station_lat, station_lon, auv_lat, auv_lon, heading=270.0, depth=0.0, pitch=0.0, roll=0.0):
        """Streams real-time coordinates to 3D WebGL scene."""
        if not self._is_loaded:
            self._pending_telemetry = (station_lat, station_lon, auv_lat, auv_lon, heading, depth, pitch, roll)
            return

        s_lat = float(station_lat or 13.0827000)
        s_lon = float(station_lon or 80.2707000)
        a_lat = float(auv_lat or 13.0827000)
        a_lon = float(auv_lon or 80.270815)
        h = float(heading if heading is not None else 270.0)
        d = float(depth if depth is not None else 0.0)
        p = float(pitch if pitch is not None else 0.0)
        r = float(roll if roll is not None else 0.0)

        js = f"if (window.updateTelemetry) window.updateTelemetry({a_lat:.7f}, {a_lon:.7f}, {d:.2f}, {h:.1f}, {p:.1f}, {r:.1f}, undefined, {s_lat:.7f}, {s_lon:.7f});"
        self.web_view.page().runJavaScript(js)

    def set_camera_preset(self, preset: str):
        """Switches camera view between '50m', '10m', '3m', 'chase', 'reset'."""
        if self._is_loaded:
            self.web_view.page().runJavaScript(f"if (window.setCameraPreset) window.setCameraPreset('{preset}');")

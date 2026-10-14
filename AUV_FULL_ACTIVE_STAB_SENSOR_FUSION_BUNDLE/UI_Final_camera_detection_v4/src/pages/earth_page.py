import os
from PySide6.QtWidgets import QWidget, QGridLayout, QVBoxLayout, QFrame, QPushButton
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage
from src.widgets import MarineHorizon, MarineCompass

class ConsoleWebEnginePage(QWebEnginePage):
    """
    Subclassed WebEnginePage to route JavaScript warnings/errors to Python stdout.
    """
    def __init__(self, parent=None, callback=None):
        super().__init__(parent)
        self.callback = callback
        
    def javaScriptConsoleMessage(self, level, message, line, source):
        if self.callback:
            self.callback(message)
        print(f"[JS Console] {message} (Line: {line})")

def create_earth_page(parent):
    page = QWidget()
    
    # Grid layout allowing multiple widgets to occupy the same cell (0, 0)
    grid_layout = QGridLayout(page)
    grid_layout.setContentsMargins(0, 0, 0, 0)
    
    # 1. Fullscreen Web View Map
    parent.web_view = QWebEngineView()
    parent.web_page = ConsoleWebEnginePage(parent.web_view)
    parent.web_view.setPage(parent.web_page)
    
    # Load the bundled Leaflet page from its real local path.  This gives
    # WebEngine a stable origin/cache and lets the parent enable remote tiles.
    html_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "map.html")
    try:
        parent._configure_map_webview(parent.web_view)
        parent.web_view.loadFinished.connect(lambda ok: parent._on_map_loaded("earth", ok))
        parent.web_view.load(QUrl.fromLocalFile(os.path.abspath(html_path)))
    except Exception as e:
        print(f"Error loading map.html: {e}")
        
    grid_layout.addWidget(parent.web_view, 0, 0)
    
    # 2. Overlay Panel for circular instruments (Horizon + Compass)
    overlay_panel = QFrame()
    overlay_panel.setStyleSheet("""
        QFrame {
            background: transparent;
            border: none;
            padding: 0px;
            margin: 20px; /* Offset from top-right window borders */
        }
    """)
    
    overlay_layout = QVBoxLayout(overlay_panel)
    overlay_layout.setContentsMargins(0, 0, 0, 0)
    overlay_layout.setSpacing(15)
    
    # Horizon Indicator (Enlarged to 190x190 for ~5cm diameter)
    parent.map_horizon = MarineHorizon(theme="cockpit")
    parent.map_horizon.setFixedSize(190, 190)
    
    # Compass Indicator (Enlarged to 190x190 for ~5cm diameter)
    parent.map_compass = MarineCompass(theme="cockpit")
    parent.map_compass.setFixedSize(190, 190)
    
    overlay_layout.addWidget(parent.map_horizon)
    overlay_layout.addWidget(parent.map_compass)

    parent.btn_clear_map_trail = QPushButton("CLEAR TRAIL")
    parent.btn_clear_map_trail.setCursor(Qt.PointingHandCursor)
    parent.btn_clear_map_trail.setStyleSheet("""
        QPushButton {
            background-color: rgba(18, 18, 18, 220);
            color: #FFFFFF;
            border: 1px solid #FF9100;
            border-radius: 4px;
            padding: 9px 12px;
            font-weight: bold;
        }
        QPushButton:hover { background-color: #FF9100; color: #111111; }
    """)
    parent.btn_clear_map_trail.clicked.connect(
        lambda: parent.web_view.page().runJavaScript("clearTrack();")
    )
    overlay_layout.addWidget(parent.btn_clear_map_trail)
    
    # Align overlay panel to the Top Right corner of the map grid cell
    grid_layout.addWidget(overlay_panel, 0, 0, Qt.AlignTop | Qt.AlignRight)
    
    return page

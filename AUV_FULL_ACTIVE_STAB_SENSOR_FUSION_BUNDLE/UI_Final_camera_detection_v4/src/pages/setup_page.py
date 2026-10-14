import os
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QFrame, QLabel, 
                               QPushButton, QStackedWidget, QScrollArea, QComboBox, 
                               QCheckBox, QGridLayout, QLineEdit, QProgressBar, QSizePolicy)
from PySide6.QtCore import Qt, QTimer, QRect, QPoint, QSize, Signal
from PySide6.QtGui import QPainter, QColor, QLinearGradient

class ToggleSwitch(QWidget):
    toggled = Signal(bool)
    clicked = Signal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(54, 30)
        self._checked = False
        self.setCursor(Qt.PointingHandCursor)
        
    def isChecked(self):
        return self._checked
        
    def setChecked(self, checked):
        if self._checked != checked:
            self._checked = checked
            self.toggled.emit(checked)
            self.update()
            
    def toggle(self):
        self.setChecked(not self._checked)
        self.clicked.emit()
        
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle()
            
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Background capsule
        rect = QRect(0, 0, self.width(), self.height())
        r = self.height() / 2.0
        
        if self._checked:
            # Gradient from green to cyan
            grad = QLinearGradient(0, 0, self.width(), 0)
            grad.setColorAt(0.0, QColor("#2ECC71"))
            grad.setColorAt(1.0, QColor("#00E5FF"))
            painter.setBrush(grad)
            painter.setPen(Qt.NoPen)
        else:
            # Dark gray capsule for unchecked
            painter.setBrush(QColor("#29313B"))
            painter.setPen(Qt.NoPen)
            
        painter.drawRoundedRect(rect, r, r)
        
        # Thumb
        thumb_diameter = self.height() - 8
        if self._checked:
            thumb_x = self.width() - thumb_diameter - 4
        else:
            thumb_x = 4
        
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(thumb_x, 4, thumb_diameter, thumb_diameter)
        
    def setText(self, text):
        pass
        
    def setStyleSheet(self, style):
        pass
        
    def setCheckable(self, checkable):
        pass

def _update_menu_buttons(parent, active_btn):
    buttons = [parent.btn_menu_joystick, parent.btn_menu_light, parent.btn_menu_autopilot]
    if hasattr(parent, 'btn_menu_cam_rec'):
        buttons.append(parent.btn_menu_cam_rec)
    if hasattr(parent, 'btn_menu_sonar'):
        buttons.append(parent.btn_menu_sonar)
    if hasattr(parent, 'btn_menu_servo_output'):
        buttons.append(parent.btn_menu_servo_output)
    for btn in buttons:
        btn.setProperty("active", "true" if btn is active_btn else "false")
        btn.style().unpolish(btn)
        btn.style().polish(btn)

def select_joystick_setup(parent):
    _update_menu_buttons(parent, parent.btn_menu_joystick)
    parent.setup_stacked.setCurrentIndex(0)
    parent.setup_breadcrumb.setText("HARDWARE CONFIGURATION > JOYSTICK")

def select_light_setup(parent):
    _update_menu_buttons(parent, parent.btn_menu_light)
    parent.setup_stacked.setCurrentIndex(1)
    parent.setup_breadcrumb.setText("HARDWARE CONFIGURATION > EQUIPMENT CONTROL")

def set_thruster_limits(parent):
    try:
        min_val = int(parent.input_thruster_min.text().strip())
        max_val = int(parent.input_thruster_max.text().strip())
        
        # Enforce hardware safety boundaries
        if min_val < 1000 or min_val > 1500 or max_val < 1500 or max_val > 2000:
            parent.lbl_thruster_feedback.setStyleSheet("color: #FF1744; font-family: 'Google Sans', sans-serif; font-size: 11px; font-weight: bold; border: none; background: transparent;")
            parent.lbl_thruster_feedback.setText("Limits must satisfy 1000 <= min <= 1500 <= max <= 2000")
            return
            
        parent.thruster_min_limit = min_val
        parent.thruster_max_limit = max_val
        
        # Save limits to configuration
        parent.save_joystick_config()
        
        # Transmit speed limits to backend
        if parent.telemetry_thread and parent.telemetry_thread.isRunning():
            payload = f"$LIMIT,{min_val},{max_val}"
            parent.telemetry_thread.write_data(payload)
            print(f"[Mission Control] Sent speed limits to backend: {payload}")
            
        parent.lbl_thruster_feedback.setStyleSheet("color: #8BC34A; font-family: 'Google Sans', sans-serif; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        parent.lbl_thruster_feedback.setText("Limits applied and saved successfully!")
        QTimer.singleShot(3000, lambda: parent.lbl_thruster_feedback.setText(""))
    except ValueError:
        parent.lbl_thruster_feedback.setStyleSheet("color: #FF1744; font-family: 'Google Sans', sans-serif; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        parent.lbl_thruster_feedback.setText("Invalid integers entered.")

def flash_warning_banner(parent):
    parent.warning_flash_state = not parent.warning_flash_state
    has_critical = (getattr(parent, 'batt_state', 'normal') == 'critical' or getattr(parent, 'temp_state', 'normal') == 'critical')
    
    # Color schemes based on alert level
    if has_critical:
        # Crimson alert style
        bg_active = "#5C0B14"  # Dark Crimson
        bg_inactive = "#2D080D"
        border_color = "#FF4757" # Crimson
    else:
        # Amber warning style
        bg_active = "#4F2F00"  # Dark Amber/Orange
        bg_inactive = "#2A1800"
        border_color = "#FF9F43" # Amber
        
    if parent.warning_flash_state:
        parent.warning_banner_frame.setStyleSheet(f"""
            QFrame#WarningBannerFrame {{
                background-color: {bg_active};
                border: 2px solid {border_color};
                border-radius: 6px;
                padding: 10px 15px;
            }}
            QLabel#WarningBannerLabel {{
                color: #FFFFFF;
                font-family: 'Google Sans', sans-serif;
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 0.5px;
            }}
        """)
    else:
        parent.warning_banner_frame.setStyleSheet(f"""
            QFrame#WarningBannerFrame {{
                background-color: {bg_inactive};
                border: 2px solid {border_color};
                border-radius: 6px;
                padding: 10px 15px;
            }}
            QLabel#WarningBannerLabel {{
                color: #FFFFFF;
                font-family: 'Google Sans', sans-serif;
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 0.5px;
            }}
        """)


def get_autopilot_config_path():
    """Return a stable config path inside the UI project, independent of CWD."""
    ui_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(ui_root, "autopilot_config.json")

DEFAULT_FRONTEND_AUTOPILOT_SETTINGS = {
    "AHRS_OFFSET": 90.0,
    "STEERING_DEADBAND": 1.0,
    "LOS_LOOKAHEAD": 2.0,
    "WAYPOINT_TOLERANCE": 1.0,
    "DOCK_LINE_LENGTH": 10.0,
    "DOCK_LOOKAHEAD": 1.5,
    "DOCK_SPEED_SCALE": 0.50,
    "DOCK_SLOWDOWN_DISTANCE": 3.0,
    "DOCK_MIN_SPEED_SCALE": 0.20,
    "DOCK_MIN_SURGE_PWM": 30
}

def select_autopilot_setup(parent):
    _update_menu_buttons(parent, parent.btn_menu_autopilot)
    parent.setup_stacked.setCurrentIndex(2)
    parent.setup_breadcrumb.setText("HARDWARE CONFIGURATION > PARAMETERS")

def select_cam_rec_setup(parent):
    _update_menu_buttons(parent, getattr(parent, 'btn_menu_cam_rec', None))
    parent.setup_stacked.setCurrentIndex(3)
    parent.setup_breadcrumb.setText("HARDWARE CONFIGURATION > CAMERA REC")

def select_sonar_setup(parent):
    _update_menu_buttons(parent, getattr(parent, 'btn_menu_sonar', None))
    parent.setup_stacked.setCurrentIndex(4)
    parent.setup_breadcrumb.setText("HARDWARE CONFIGURATION > PING360 SONAR")

def select_servo_output_setup(parent):
    _update_menu_buttons(parent, parent.btn_menu_servo_output)
    parent.setup_stacked.setCurrentIndex(5)
    parent.setup_breadcrumb.setText("HARDWARE CONFIGURATION > SERVO OUTPUT")

def save_and_transmit_autopilot_config(parent):
    import json
    config = {}
    try:
        for key, default_val in DEFAULT_FRONTEND_AUTOPILOT_SETTINGS.items():
            field = getattr(parent, f"ap_input_{key}", None)
            if field is None:
                continue
            if isinstance(default_val, bool):
                config[key] = field.isChecked()
            elif isinstance(default_val, int):
                config[key] = int(field.text().strip())
            elif isinstance(default_val, float):
                config[key] = float(field.text().strip())
            else:
                config[key] = field.text().strip()
    except ValueError:
        parent.lbl_autopilot_feedback.setStyleSheet("color: #FF1744; font-family: 'Google Sans', sans-serif; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        parent.lbl_autopilot_feedback.setText("Error: All fields must contain valid numeric inputs!")
        return

    try:
        with open(get_autopilot_config_path(), "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        print(f"Error saving local autopilot config: {e}")

    # Keep UI-side waypoint completion logic identical to the value sent to backend.
    parent.wp_reach_threshold = float(config.get("WAYPOINT_TOLERANCE", parent.wp_reach_threshold))
    if hasattr(parent, "spin_wp_threshold"):
        parent.spin_wp_threshold.blockSignals(True)
        parent.spin_wp_threshold.setValue(parent.wp_reach_threshold)
        parent.spin_wp_threshold.blockSignals(False)

    # Redraw the projected dock axis with the configured physical length.
    dock_len = float(config.get("DOCK_LINE_LENGTH", 10.0))
    for view_name in ("plan_web_view", "web_view"):
        view = getattr(parent, view_name, None)
        if view is not None and hasattr(view, "page"):
            view.page().runJavaScript(
                f"if (typeof setDockingStation === 'function') setDockingStation({parent.docking_station_latitude:.7f}, {parent.docking_station_longitude:.7f}, {parent.docking_station_heading}, {dock_len});"
            )

    if parent.telemetry_thread and parent.telemetry_thread.isRunning():
        parts = ["$CONFIG"]
        for k, v in config.items():
            parts.append(f"{k}={v}")
        payload = ",".join(parts)
        parent.telemetry_thread.write_data(payload)
        
        parent.lbl_autopilot_feedback.setStyleSheet("color: #8BC34A; font-family: 'Google Sans', sans-serif; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        parent.lbl_autopilot_feedback.setText("Configuration saved and transmitted successfully!")
        QTimer.singleShot(4000, lambda: parent.lbl_autopilot_feedback.setText(""))
    else:
        parent.lbl_autopilot_feedback.setStyleSheet("color: #FF9F43; font-family: 'Google Sans', sans-serif; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        parent.lbl_autopilot_feedback.setText("Saved locally! (Telemetry offline - not transmitted)")

def load_autopilot_config(parent):
    import json
    config = dict(DEFAULT_FRONTEND_AUTOPILOT_SETTINGS)
    try:
        cfg_path = get_autopilot_config_path()
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in data.items():
                    if k in config:
                        config[k] = v
    except Exception as e:
        print(f"Error loading local autopilot config: {e}")
        
    parent.wp_reach_threshold = float(config.get("WAYPOINT_TOLERANCE", getattr(parent, "wp_reach_threshold", 1.0)))

    for key, val in config.items():
        field = getattr(parent, f"ap_input_{key}", None)
        if field is None:
            continue
        if isinstance(val, bool):
            field.setChecked(val)
        else:
            field.setText(str(val))

def toggle_section(container_widget, btn):
    if container_widget.isVisible():
        container_widget.setVisible(False)
        btn.setText("SHOW ▼")
    else:
        container_widget.setVisible(True)
        btn.setText("HIDE ▲")

def create_card_section(number, title, desc, accent_color):
    card = QFrame()
    card.setObjectName(f"Card_{number}")
    card.setStyleSheet(f"""
        QFrame#Card_{number} {{
            background-color: #151A21;
            border: 1px solid #29313B;
            border-radius: 8px;
        }}
    """)
    
    card_layout = QVBoxLayout(card)
    card_layout.setContentsMargins(20, 20, 20, 20)
    card_layout.setSpacing(16)
    
    # Header row
    header_row = QHBoxLayout()
    header_row.setSpacing(12)
    
    # Badge
    badge = QLabel(str(number))
    badge.setAlignment(Qt.AlignCenter)
    badge.setFixedSize(22, 22)
    badge.setStyleSheet(f"""
        QLabel {{
            background-color: {accent_color};
            color: #FFFFFF;
            font-family: 'Google Sans', sans-serif;
            font-size: 11px;
            font-weight: bold;
            border-radius: 11px;
            border: none;
        }}
    """)
    header_row.addWidget(badge)
    
    # Text block
    text_block = QVBoxLayout()
    text_block.setSpacing(2)
    
    title_lbl = QLabel(title)
    title_lbl.setStyleSheet("""
        QLabel {
            color: #E2E8F0;
            font-family: 'Google Sans', sans-serif;
            font-size: 13px;
            font-weight: bold;
            border: none;
            background: transparent;
        }
    """)
    text_block.addWidget(title_lbl)
    
    desc_lbl = QLabel(desc)
    desc_lbl.setStyleSheet("""
        QLabel {
            color: #94A3B8;
            font-family: 'Google Sans', sans-serif;
            font-size: 11px;
            border: none;
            background: transparent;
        }
    """)
    text_block.addWidget(desc_lbl)
    header_row.addLayout(text_block)
    header_row.addStretch()
    
    # Collapse/Expand Toggle Button
    toggle_btn = QPushButton("HIDE ▲")
    toggle_btn.setStyleSheet("""
        QPushButton {
            background-color: #20262E;
            border: 1px solid #29313B;
            border-radius: 4px;
            color: #94A3B8;
            font-family: 'Google Sans', sans-serif;
            font-size: 10px;
            font-weight: bold;
            padding: 4px 10px;
        }
        QPushButton:hover {
            background-color: #2D3540;
            color: #E2E8F0;
        }
    """)
    header_row.addWidget(toggle_btn)
    card_layout.addLayout(header_row)
    
    # Grid Content Container
    content_widget = QWidget()
    content_widget.setStyleSheet("background: transparent; border: none;")
    grid_layout = QGridLayout(content_widget)
    grid_layout.setContentsMargins(0, 10, 0, 0)
    grid_layout.setHorizontalSpacing(32)
    grid_layout.setVerticalSpacing(14)
    
    # Column stretches for proper alignment
    grid_layout.setColumnStretch(0, 3) # Left labels
    grid_layout.setColumnStretch(1, 1) # Left inputs
    grid_layout.setColumnStretch(2, 0) # Left units
    grid_layout.setColumnStretch(3, 3) # Right labels
    grid_layout.setColumnStretch(4, 1) # Right inputs
    grid_layout.setColumnStretch(5, 0) # Right units
    
    card_layout.addWidget(content_widget)
    
    # Connect toggle
    toggle_btn.clicked.connect(lambda: toggle_section(content_widget, toggle_btn))
    
    return card, grid_layout

def add_parameter_to_grid(grid, key, label_txt, val_type, unit_txt, row, col_offset, parent):
    lbl = QLabel(label_txt)
    lbl.setStyleSheet("""
        QLabel {
            color: #94A3B8;
            font-family: 'Google Sans', sans-serif;
            font-size: 11px;
            font-weight: 500;
            border: none;
            background: transparent;
        }
    """)
    grid.addWidget(lbl, row, col_offset + 0, Qt.AlignLeft | Qt.AlignVCenter)
    
    if val_type == "bool":
        field = ToggleSwitch()
        grid.addWidget(field, row, col_offset + 1, Qt.AlignLeft | Qt.AlignVCenter)
    else:
        field = QLineEdit()
        field.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        field.setFixedSize(90, 36)
        field.setStyleSheet("""
            QLineEdit {
                background-color: #0F1318;
                border: 1.5px solid #29313B;
                border-radius: 6px;
                color: #E2E8F0;
                padding-right: 8px;
                padding-left: 8px;
                font-family: 'Google Sans', sans-serif;
                font-size: 11px;
                font-weight: bold;
            }
            QLineEdit:focus {
                border: 1.5px solid #00E5FF;
                background-color: #151A21;
            }
        """)
        grid.addWidget(field, row, col_offset + 1, Qt.AlignLeft | Qt.AlignVCenter)
        
    setattr(parent, f"ap_input_{key}", field)
    
    if unit_txt:
        unit_lbl = QLabel(unit_txt)
        unit_lbl.setFixedWidth(40)
        unit_lbl.setStyleSheet("""
            QLabel {
                color: #5C6F84;
                font-family: 'Google Sans', sans-serif;
                font-size: 10px;
                font-weight: bold;
                border: none;
                background: transparent;
            }
        """)
        grid.addWidget(unit_lbl, row, col_offset + 2, Qt.AlignLeft | Qt.AlignVCenter)

def create_autopilot_page(parent):
    page = QWidget()
    page.setStyleSheet("background-color: #0B0F14;")
    layout = QVBoxLayout(page)
    layout.setContentsMargins(24, 24, 24, 24)
    layout.setSpacing(16)
    
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet("""
        QScrollArea {
            border: none;
            background-color: transparent;
        }
        QScrollBar:vertical {
            background-color: #0B0F14;
            width: 8px;
            margin: 0px;
            border-radius: 4px;
        }
        QScrollBar::handle:vertical {
            background-color: #29313B;
            min-height: 20px;
            border-radius: 4px;
        }
        QScrollBar::handle:vertical:hover {
            background-color: #00E5FF;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
        }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: none;
        }
    """)
    
    scroll_content = QWidget()
    scroll_content.setStyleSheet("background-color: transparent;")
    scroll_layout = QVBoxLayout(scroll_content)
    scroll_layout.setContentsMargins(0, 0, 0, 0)
    scroll_layout.setSpacing(20)
    
    # Keep only fields consumed by the backend's $CONFIG handler.
    c1, g1 = create_card_section(
        1, "Navigation & Heading",
        "Configure heading calibration and waypoint guidance precision.",
        "#00E5FF"
    )
    add_parameter_to_grid(g1, "AHRS_OFFSET", "AHRS Heading Offset", "float", "deg", 0, 0, parent)
    add_parameter_to_grid(g1, "STEERING_DEADBAND", "Steering Deadband", "float", "deg", 0, 3, parent)
    add_parameter_to_grid(g1, "LOS_LOOKAHEAD", "LOS Lookahead Distance", "float", "m", 1, 0, parent)
    add_parameter_to_grid(g1, "WAYPOINT_TOLERANCE", "Waypoint Tolerance", "float", "m", 1, 3, parent)
    scroll_layout.addWidget(c1)

    c2, g2 = create_card_section(
        2, "Docking Guidance",
        "Set the approach geometry and speed profile used by autonomous docking.",
        "#FF9F43"
    )
    add_parameter_to_grid(g2, "DOCK_LINE_LENGTH", "Dock Projection Length", "float", "m", 0, 0, parent)
    add_parameter_to_grid(g2, "DOCK_LOOKAHEAD", "Dock Line Lookahead", "float", "m", 0, 3, parent)
    add_parameter_to_grid(g2, "DOCK_SPEED_SCALE", "Dock Approach Speed Scale", "float", "x", 1, 0, parent)
    add_parameter_to_grid(g2, "DOCK_SLOWDOWN_DISTANCE", "Dock Slowdown Distance", "float", "m", 1, 3, parent)
    add_parameter_to_grid(g2, "DOCK_MIN_SPEED_SCALE", "Dock Minimum Speed Scale", "float", "x", 2, 0, parent)
    add_parameter_to_grid(g2, "DOCK_MIN_SURGE_PWM", "Dock Minimum Surge Offset", "int", "PWM", 2, 3, parent)
    scroll_layout.addWidget(c2)
    
    # Save & Transmit Button Panel
    btn_panel = QFrame()
    btn_panel.setObjectName("AutopilotBottomBar")
    btn_panel.setStyleSheet("""
        QFrame#AutopilotBottomBar {
            background-color: #151A21;
            border: 1px solid #29313B;
            border-radius: 8px;
            padding: 12px 20px;
        }
    """)
    btn_lay = QHBoxLayout(btn_panel)
    btn_lay.setContentsMargins(0, 0, 0, 0)
    
    parent.lbl_autopilot_feedback = QLabel("")
    parent.lbl_autopilot_feedback.setStyleSheet("color: #8BC34A; font-family: 'Google Sans', sans-serif; font-size: 11px; font-weight: bold; border: none; background: transparent;")
    btn_lay.addWidget(parent.lbl_autopilot_feedback)
    
    btn_save = QPushButton("SAVE & TRANSMIT CONFIG")
    btn_save.setStyleSheet("""
        QPushButton {
            background-color: #7CB342;
            border: none;
            border-radius: 4px;
            color: #FFFFFF;
            font-family: 'Google Sans', sans-serif;
            font-size: 11px;
            font-weight: bold;
            padding: 10px 24px;
        }
        QPushButton:hover {
            background-color: #9CCC65;
        }
        QPushButton:pressed {
            background-color: #689F38;
        }
    """)
    btn_save.clicked.connect(lambda: save_and_transmit_autopilot_config(parent))
    btn_lay.addWidget(btn_save, 0, Qt.AlignRight)
    
    scroll_layout.addWidget(btn_panel)
    scroll.setWidget(scroll_content)
    layout.addWidget(scroll)
    
    load_autopilot_config(parent)
    return page

def create_camera_rec_page(parent):
    """
    Creates the dedicated Camera Stream & Recording page within Hardware Setup.
    Provides RTSP configuration, live Start/Stop recording controls,
    and recording status / file name monitoring matching camera_rec.py on Jetson.
    """
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(15)

    # 1. Main Card Container
    card = QWidget()
    card.setStyleSheet("""
        QWidget {
            background-color: #1A1A1A;
            border: 1px solid #333333;
            border-radius: 6px;
        }
    """)
    card_layout = QVBoxLayout(card)
    card_layout.setContentsMargins(20, 20, 20, 20)
    card_layout.setSpacing(16)

    # Header
    lbl_title = QLabel("MISSION VIDEO RECORDER")
    lbl_title.setStyleSheet("color: #00E5FF; font-weight: bold; font-family: 'Google Sans', sans-serif; font-size: 13px; border: none; background: transparent; letter-spacing: 0.5px;")
    card_layout.addWidget(lbl_title)

    lbl_desc = QLabel("Captures direct H.264 video streams from the on-vehicle IP camera via Jetson ffmpeg without re-encoding.")
    lbl_desc.setStyleSheet("color: #94A3B8; font-family: 'Google Sans', sans-serif; font-size: 11px; border: none; background: transparent;")
    card_layout.addWidget(lbl_desc)

    # Divider
    div = QFrame()
    div.setFrameShape(QFrame.HLine)
    div.setFrameShadow(QFrame.Sunken)
    div.setStyleSheet("border: none; background-color: #29313B; max-height: 1px;")
    card_layout.addWidget(div)

    # RTSP URL Input Field
    url_layout = QVBoxLayout()
    url_layout.setSpacing(6)
    lbl_url = QLabel("RTSP CAMERA STREAM URL:")
    lbl_url.setStyleSheet("color: #E2E8F0; font-family: 'Google Sans', sans-serif; font-size: 10px; font-weight: bold; border: none; background: transparent;")
    url_layout.addWidget(lbl_url)

    parent.input_cam_rtsp_url = QLineEdit()
    parent.input_cam_rtsp_url.setText("rtsp://192.168.1.168:554/stream_1")
    parent.input_cam_rtsp_url.setStyleSheet("""
        QLineEdit {
            background-color: #2D2D2D;
            border: 1px solid #404040;
            border-radius: 4px;
            color: #00E5FF;
            font-family: 'Consolas', monospace;
            font-size: 11px;
            padding: 8px 12px;
        }
        QLineEdit:focus {
            border-color: #00E5FF;
        }
    """)
    url_layout.addWidget(parent.input_cam_rtsp_url)
    card_layout.addLayout(url_layout)

    # Status Display Row
    status_frame = QFrame()
    status_frame.setStyleSheet("background-color: #121212; border: 1px solid #282828; border-radius: 4px; padding: 10px;")
    status_layout = QHBoxLayout(status_frame)
    status_layout.setContentsMargins(10, 8, 10, 8)

    parent.lbl_cam_rec_badge = QLabel("● STANDBY")
    parent.lbl_cam_rec_badge.setStyleSheet("color: #94A3B8; font-family: 'Google Sans', sans-serif; font-size: 11px; font-weight: bold; border: none; background: transparent;")
    status_layout.addWidget(parent.lbl_cam_rec_badge)

    status_layout.addStretch()

    parent.lbl_cam_rec_file = QLabel("File: recording1.mp4")
    parent.lbl_cam_rec_file.setStyleSheet("color: #64748B; font-family: 'Consolas', monospace; font-size: 10px; border: none; background: transparent;")
    status_layout.addWidget(parent.lbl_cam_rec_file)

    parent.lbl_cam_rec_timer = QLabel("00:00:00")
    parent.lbl_cam_rec_timer.setStyleSheet("color: #CBD5E1; font-family: 'Consolas', monospace; font-size: 12px; font-weight: bold; border: none; background: transparent; padding-left: 10px;")
    status_layout.addWidget(parent.lbl_cam_rec_timer)

    card_layout.addWidget(status_frame)

    # Recording Control Buttons (Start & Stop)
    btn_row = QHBoxLayout()
    btn_row.setSpacing(12)

    parent.btn_cam_rec_start = QPushButton("▶ START RECORDING")
    parent.btn_cam_rec_start.setStyleSheet("""
        QPushButton {
            background-color: #059669;
            border: 1px solid #10B981;
            border-radius: 4px;
            color: #FFFFFF;
            font-family: 'Google Sans', sans-serif;
            font-size: 11px;
            font-weight: bold;
            padding: 10px 20px;
        }
        QPushButton:hover {
            background-color: #10B981;
        }
        QPushButton:disabled {
            background-color: #1E293B;
            border-color: #334155;
            color: #64748B;
        }
    """)
    parent.btn_cam_rec_start.clicked.connect(parent.start_camera_recording)
    btn_row.addWidget(parent.btn_cam_rec_start)

    parent.btn_cam_rec_stop = QPushButton("⏹ STOP RECORDING")
    parent.btn_cam_rec_stop.setEnabled(False)
    parent.btn_cam_rec_stop.setStyleSheet("""
        QPushButton {
            background-color: #DC2626;
            border: 1px solid #EF4444;
            border-radius: 4px;
            color: #FFFFFF;
            font-family: 'Google Sans', sans-serif;
            font-size: 11px;
            font-weight: bold;
            padding: 10px 20px;
        }
        QPushButton:hover {
            background-color: #EF4444;
        }
        QPushButton:disabled {
            background-color: #1E293B;
            border-color: #334155;
            color: #64748B;
        }
    """)
    parent.btn_cam_rec_stop.clicked.connect(parent.stop_camera_recording)
    btn_row.addWidget(parent.btn_cam_rec_stop)

    btn_row.addStretch()
    card_layout.addLayout(btn_row)

    # Info Note
    lbl_note = QLabel("ℹ Note: Video is recorded directly on the Jetson tegra board into the recordings/ folder as MP4. Stopping recording gracefully finalizes container headers.")
    lbl_note.setWordWrap(True)
    lbl_note.setStyleSheet("color: #64748B; font-family: 'Google Sans', sans-serif; font-size: 10px; border: none; background: transparent;")
    card_layout.addWidget(lbl_note)

    layout.addWidget(card)
    layout.addStretch()
    return page

def create_sonar_page(parent):
    """
    Creates the dedicated Ping360 Sonar radar display and control page within Hardware Setup.
    Streamlined to focus exclusively on:
      - Acoustic Sector Radar display (360° PPI scope)
      - Activate and Deactivate Sonar hardware controls
      - Sector Range selection (2m, 5m, 10m, 15m, 20m)
    """
    from src.widgets import Ping360SonarWidget

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet("""
        QScrollArea {
            border: none;
            background-color: transparent;
        }
        QScrollBar:vertical {
            border: none;
            background: #0A1625;
            width: 8px;
            margin: 0px;
        }
        QScrollBar::handle:vertical {
            background: #1C3B65;
            min-height: 20px;
            border-radius: 4px;
        }
        QScrollBar::handle:vertical:hover {
            background: #00E5FF;
        }
    """)

    container = QWidget()
    page_layout = QVBoxLayout(container)
    page_layout.setContentsMargins(18, 18, 18, 18)
    page_layout.setSpacing(14)

    # Clean Top Header
    header_layout = QHBoxLayout()
    lbl_title = QLabel("PING360 ACOUSTIC SCANNING SONAR")
    lbl_title.setStyleSheet("color: #00E5FF; font-weight: bold; font-family: 'Google Sans', sans-serif; font-size: 14px; letter-spacing: 0.8px; border: none; background: transparent;")
    header_layout.addWidget(lbl_title)
    header_layout.addStretch()
    page_layout.addLayout(header_layout)

    # Main Body: Left PPI Radar Scope + Right Sonar Controls
    body_layout = QHBoxLayout()
    body_layout.setSpacing(16)

    # -------------------------------------------------------------------------
    # LEFT PANEL: ACOUSTIC PPI RADAR SCOPE
    # -------------------------------------------------------------------------
    radar_card = QFrame()
    radar_card.setStyleSheet("""
        QFrame {
            background-color: #0B132B;
            border: 1px solid #1E293B;
            border-radius: 10px;
        }
    """)
    radar_layout = QVBoxLayout(radar_card)
    radar_layout.setContentsMargins(16, 16, 16, 16)
    radar_layout.setSpacing(10)

    scope_header = QHBoxLayout()
    lbl_radar_title = QLabel("ACOUSTIC SECTOR RADAR (360° PPI)")
    lbl_radar_title.setStyleSheet("color: #E2E8F0; font-family: 'Google Sans', sans-serif; font-size: 11px; font-weight: bold; letter-spacing: 0.5px; border: none; background: transparent;")
    scope_header.addWidget(lbl_radar_title)
    scope_header.addStretch()
    radar_layout.addLayout(scope_header)

    # Live Sonar Visualizer Widget
    parent.hw_sonar_widget = Ping360SonarWidget(parent)
    parent.hw_sonar_widget.setMinimumSize(420, 420)
    parent.hw_sonar_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    parent.hw_sonar_widget.setToolTip("Plan Position Indicator (PPI) acoustic radar scope showing raw echo intensities and detected targets.")
    radar_layout.addWidget(parent.hw_sonar_widget, 1)

    body_layout.addWidget(radar_card, 7)

    # -------------------------------------------------------------------------
    # RIGHT PANEL: SONAR CONTROLS (Power & Sector Range)
    # -------------------------------------------------------------------------
    ctrl_card = QFrame()
    ctrl_card.setFixedWidth(320)
    ctrl_card.setStyleSheet("""
        QFrame {
            background-color: #111827;
            border: 1px solid #1F2937;
            border-radius: 10px;
            padding: 16px;
        }
    """)
    ctrl_layout = QVBoxLayout(ctrl_card)
    ctrl_layout.setContentsMargins(16, 16, 16, 16)
    ctrl_layout.setSpacing(14)

    lbl_ctrl_title = QLabel("SONAR CONTROLS")
    lbl_ctrl_title.setStyleSheet("color: #E2E8F0; font-family: 'Google Sans', sans-serif; font-size: 12px; font-weight: bold; letter-spacing: 0.6px; border: none; background: transparent;")
    ctrl_layout.addWidget(lbl_ctrl_title)

    sep1 = QFrame()
    sep1.setFixedHeight(1)
    sep1.setStyleSheet("background-color: #1F2937; border: none;")
    ctrl_layout.addWidget(sep1)

    # Transducer Power Section
    lbl_pwr = QLabel("TRANSDUCER OPERATION")
    lbl_pwr.setStyleSheet("color: #94A3B8; font-family: 'Google Sans', sans-serif; font-size: 10px; font-weight: bold; letter-spacing: 0.5px; border: none; background: transparent;")
    ctrl_layout.addWidget(lbl_pwr)

    parent.btn_hw_sonar_start = QPushButton("ACTIVATE SONAR")
    parent.btn_hw_sonar_start.setCursor(Qt.PointingHandCursor)
    parent.btn_hw_sonar_start.setToolTip("Initialize mechanical sweep motor and acoustic transmit pulse ($SONAR,START)")
    parent.btn_hw_sonar_start.setStyleSheet("""
        QPushButton {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #10B981, stop:1 #059669);
            border: 1px solid #34D399;
            border-radius: 6px;
            color: #FFFFFF;
            font-family: 'Google Sans', sans-serif;
            font-size: 12px;
            font-weight: bold;
            padding: 12px 20px;
            letter-spacing: 0.5px;
        }
        QPushButton:hover {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #34D399, stop:1 #10B981);
            border: 1px solid #6EE7B7;
        }
        QPushButton:disabled {
            background: #1E293B;
            border-color: #334155;
            color: #64748B;
        }
    """)
    parent.btn_hw_sonar_start.clicked.connect(parent.activate_sonar)
    ctrl_layout.addWidget(parent.btn_hw_sonar_start)

    parent.btn_hw_sonar_stop = QPushButton("DEACTIVATE SONAR")
    parent.btn_hw_sonar_stop.setCursor(Qt.PointingHandCursor)
    parent.btn_hw_sonar_stop.setToolTip("Stop mechanical sector scanning and pause acoustic transmission ($SONAR,STOP)")
    parent.btn_hw_sonar_stop.setStyleSheet("""
        QPushButton {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #EF4444, stop:1 #DC2626);
            border: 1px solid #F87171;
            border-radius: 6px;
            color: #FFFFFF;
            font-family: 'Google Sans', sans-serif;
            font-size: 12px;
            font-weight: bold;
            padding: 12px 20px;
            letter-spacing: 0.5px;
        }
        QPushButton:hover {
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #F87171, stop:1 #EF4444);
            border: 1px solid #FCA5A5;
        }
        QPushButton:disabled {
            background: #1E293B;
            border-color: #334155;
            color: #64748B;
        }
    """)
    parent.btn_hw_sonar_stop.clicked.connect(parent.deactivate_sonar)
    ctrl_layout.addWidget(parent.btn_hw_sonar_stop)

    parent.btn_hw_sonar_start.setEnabled(not getattr(parent, 'sonar_active', False))
    parent.btn_hw_sonar_stop.setEnabled(getattr(parent, 'sonar_active', False))

    sep2 = QFrame()
    sep2.setFixedHeight(1)
    sep2.setStyleSheet("background-color: #1F2937; border: none;")
    ctrl_layout.addWidget(sep2)

    # Sector Range Controls Section
    lbl_rng = QLabel("SECTOR RANGE")
    lbl_rng.setStyleSheet("color: #94A3B8; font-family: 'Google Sans', sans-serif; font-size: 10px; font-weight: bold; letter-spacing: 0.5px; border: none; background: transparent;")
    ctrl_layout.addWidget(lbl_rng)

    parent.sonar_range_btns = []
    def create_range_setter(r_val):
        def _set():
            if hasattr(parent, 'set_sonar_range'):
                parent.set_sonar_range(r_val)
            else:
                if hasattr(parent, 'hw_sonar_widget') and parent.hw_sonar_widget:
                    parent.hw_sonar_widget.set_range(r_val)
                if hasattr(parent, 'docking_sonar') and parent.docking_sonar:
                    parent.docking_sonar.set_range(r_val)
                for b, v in parent.sonar_range_btns:
                    if v == r_val:
                        b.setStyleSheet("background-color: #0284C7; border: 1px solid #00E5FF; color: #FFFFFF; font-weight: bold; border-radius: 5px; padding: 10px; font-size: 11px; font-family: 'Consolas', monospace;")
                    else:
                        b.setStyleSheet("background-color: #1E293B; border: 1px solid #334155; color: #94A3B8; border-radius: 5px; padding: 10px; font-size: 11px; font-family: 'Consolas', monospace;")
        return _set

    rng_grid = QGridLayout()
    rng_grid.setSpacing(8)
    ranges = [2, 5, 10, 15, 20]
    for i, rng in enumerate(ranges):
        btn_r = QPushButton(f"{rng} m")
        btn_r.setCursor(Qt.PointingHandCursor)
        btn_r.setToolTip(f"Set radar display max sector range to {rng} meters")
        if rng == 10:
            btn_r.setStyleSheet("background-color: #0284C7; border: 1px solid #00E5FF; color: #FFFFFF; font-weight: bold; border-radius: 5px; padding: 10px; font-size: 11px; font-family: 'Consolas', monospace;")
        else:
            btn_r.setStyleSheet("background-color: #1E293B; border: 1px solid #334155; color: #94A3B8; border-radius: 5px; padding: 10px; font-size: 11px; font-family: 'Consolas', monospace;")
        btn_r.clicked.connect(create_range_setter(rng))
        parent.sonar_range_btns.append((btn_r, rng))
        row = i // 2
        col = i % 2
        if i == len(ranges) - 1 and len(ranges) % 2 == 1:
            rng_grid.addWidget(btn_r, row, col, 1, 2)
        else:
            rng_grid.addWidget(btn_r, row, col)

    ctrl_layout.addLayout(rng_grid)
    ctrl_layout.addStretch()

    body_layout.addWidget(ctrl_card, 3)
    page_layout.addLayout(body_layout)
    page_layout.addStretch()

    # Clear removed telemetry widget handles on parent to ensure safe operations
    parent.lbl_sonar_hw_badge = None
    parent.lbl_sonar_hw_dist = None
    parent.lbl_sonar_hw_bearing = None
    parent.lbl_sonar_hw_angle = None
    parent.lbl_sonar_hw_conf = None
    parent.hw_sonar_chart = None

    scroll.setWidget(container)
    return scroll

def create_setup_page(parent):
    from PySide6.QtWidgets import QProgressBar, QStackedWidget, QLineEdit, QSplitter
    
    # Bind helper callbacks to parent dynamically so other components can access them
    parent.select_joystick_setup = lambda: select_joystick_setup(parent)
    parent.select_light_setup = lambda: select_light_setup(parent)
    parent.set_thruster_limits = lambda: set_thruster_limits(parent)
    parent.flash_warning_banner = lambda: flash_warning_banner(parent)
    
    # Resolve absolute paths for setup page assets
    src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    down_arrow_path = os.path.join(src_dir, "down_arrow.png").replace("\\", "/")
    check_path = os.path.join(src_dir, "check.png").replace("\\", "/")
    
    page = QWidget()
    layout = QHBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    
    # Horizontal Splitter for Setup Menu and Content Panel
    parent.setup_splitter = QSplitter(Qt.Horizontal)
    parent.setup_splitter.setStyleSheet("""
        QSplitter::handle {
            background-color: #2D2D2D;
            width: 4px;
        }
        QSplitter::handle:hover {
            background-color: #555555;
        }
    """)
    
    # 1. Left Tile Bar Menu
    left_menu = QFrame()
    left_menu.setObjectName("SetupLeftMenu")
    left_menu.setMinimumWidth(180)
    left_menu.setMaximumWidth(300)
    left_menu.setStyleSheet("""
        QFrame#SetupLeftMenu {
            background-color: #141414;
            border-right: 1px solid #2d2d2d;
            border-left: none;
            border-top: none;
            border-bottom: none;
            border-radius: 0px;
            padding: 15px 5px;
        }
        QLabel {
            color: #888888;
            font-family: 'Google Sans', sans-serif;
            font-size: 10px;
            font-weight: bold;
            letter-spacing: 0.5px;
            text-transform: uppercase;
            margin-left: 15px;
            margin-bottom: 8px;
            background: transparent;
            border: none;
        }
        QPushButton {
            background-color: transparent;
            border: none;
            border-left: 3px solid transparent;
            border-radius: 4px;
            color: #CCCCCC;
            padding: 10px 15px;
            font-family: 'Google Sans', sans-serif;
            font-weight: bold;
            font-size: 11px;
            text-align: left;
            margin: 2px 10px;
        }
        QPushButton:hover {
            background-color: #202020;
            color: #FFFFFF;
        }
        QPushButton[active="true"] {
            background-color: #2b2b2b;
            color: #FFFFFF;
            border-left: 3px solid #0078D4;
            border-top-left-radius: 0px;
            border-bottom-left-radius: 0px;
            padding-left: 12px;
        }
    """)
    
    menu_layout = QVBoxLayout(left_menu)
    menu_layout.setContentsMargins(0, 0, 0, 0)
    menu_layout.setSpacing(5)
    
    menu_title = QLabel("HARDWARE MENU")
    menu_layout.addWidget(menu_title)
    
    # Joystick menu option button
    parent.btn_menu_joystick = QPushButton("JOYSTICK")
    parent.btn_menu_joystick.setProperty("active", "true")
    menu_layout.addWidget(parent.btn_menu_joystick)
    
    # Light menu option button
    parent.btn_menu_light = QPushButton("EQUIPMENT CONTROL")
    parent.btn_menu_light.setProperty("active", "false")
    menu_layout.addWidget(parent.btn_menu_light)
    
    # Autopilot menu option button
    parent.btn_menu_autopilot = QPushButton("PARAMETERS")
    parent.btn_menu_autopilot.setProperty("active", "false")
    menu_layout.addWidget(parent.btn_menu_autopilot)
    
    # Camera Recording menu option button
    parent.btn_menu_cam_rec = QPushButton("CAMERA REC")
    parent.btn_menu_cam_rec.setProperty("active", "false")
    menu_layout.addWidget(parent.btn_menu_cam_rec)

    # Ping360 Sonar menu option button
    parent.btn_menu_sonar = QPushButton("PING360 SONAR")
    parent.btn_menu_sonar.setProperty("active", "false")
    menu_layout.addWidget(parent.btn_menu_sonar)

    parent.btn_menu_servo_output = QPushButton("SERVO OUTPUT")
    parent.btn_menu_servo_output.setProperty("active", "false")
    menu_layout.addWidget(parent.btn_menu_servo_output)
    
    menu_layout.addStretch()
    parent.setup_splitter.addWidget(left_menu)
    
    # 2. Right Content Container (With Breadcrumb at Top + Stacked Widget)
    right_container = QWidget()
    right_container.setObjectName("SetupRightContainer")
    right_container.setStyleSheet("""
        QWidget#SetupRightContainer {
            background-color: #1e1e1f;
        }
    """)
    right_container_layout = QVBoxLayout(right_container)
    right_container_layout.setContentsMargins(0, 0, 0, 0)
    right_container_layout.setSpacing(0)
    
    # Breadcrumb header
    parent.setup_breadcrumb = QLabel("HARDWARE CONFIGURATION > JOYSTICK")
    parent.setup_breadcrumb.setStyleSheet("""
        color: #FFFFFF;
        background-color: #161616;
        border-bottom: 1px solid #2d2d2d;
        font-family: 'Google Sans', sans-serif;
        font-size: 11px;
        font-weight: bold;
        padding: 15px 30px;
        letter-spacing: 0.5px;
    """)
    right_container_layout.addWidget(parent.setup_breadcrumb)
    
    # Stacked widget to display selected option
    parent.setup_stacked = QStackedWidget()
    right_container_layout.addWidget(parent.setup_stacked, 1)
    
    parent.setup_splitter.addWidget(right_container)
    parent.setup_splitter.setSizes([180, 800])
    layout.addWidget(parent.setup_splitter, 1)
        
    # Create Joystick Page
    joystick_page = QWidget()
    joystick_layout = QVBoxLayout(joystick_page)
    joystick_layout.setContentsMargins(15, 15, 15, 15)
    joystick_layout.setSpacing(10)
    
    # Scroll Area Setup
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setStyleSheet("QScrollArea { border: none; background-color: transparent; }")
    
    scroll_content = QWidget()
    scroll_content.setStyleSheet("background-color: transparent;")
    scroll_layout = QVBoxLayout(scroll_content)
    scroll_layout.setContentsMargins(5, 5, 5, 5)
    scroll_layout.setSpacing(15)
    
    # Top Row: Joystick Label | ComboBox | Enable Btn | Save Btn | Loaded Config Label
    top_row = QHBoxLayout()
    top_row.setSpacing(10)
    
    lbl_joy_title = QLabel("Joystick")
    lbl_joy_title.setStyleSheet("color: #FFFFFF; font-weight: bold; font-family: 'Google Sans', sans-serif; font-size: 13px;")
    
    parent.combo_joystick = QComboBox()
    parent.combo_joystick.setMinimumWidth(180)
    parent.combo_joystick.setStyleSheet(f"""
        QComboBox {{
            background-color: #2d2d2d;
            border: 1px solid #404040;
            border-bottom: 1px solid #5a5a5a;
            border-radius: 4px;
            color: #FFFFFF;
            padding: 4px 25px 4px 8px;
            font-family: 'Google Sans', sans-serif;
            font-size: 11px;
        }}
        QComboBox:hover {{
            background-color: #323232;
            border-color: #555555;
        }}
        QComboBox::drop-down {{
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 20px;
            border: none;
        }}
        QComboBox::down-arrow {{
            image: url({down_arrow_path});
            width: 8px;
            height: 8px;
        }}
        QComboBox QAbstractItemView {{
            background-color: #2d2d2d;
            border: 1px solid #404040;
            color: #FFFFFF;
            selection-background-color: #353535;
            selection-color: #FFFFFF;
            outline: 0px;
        }}
    """)
    
    parent.btn_joystick_enable = QCheckBox("Enable")
    parent.btn_joystick_enable.setStyleSheet(f"""
        QCheckBox {{
            color: #FFFFFF;
            font-family: 'Google Sans', sans-serif;
            font-size: 11px;
            font-weight: bold;
        }}
        QCheckBox::indicator {{
            width: 14px;
            height: 14px;
            border: 1px solid #404040;
            background-color: #2d2d2d;
            border-radius: 3px;
        }}
        QCheckBox::indicator:unchecked:hover {{
            background-color: #323232;
            border-color: #555555;
        }}
        QCheckBox::indicator:checked {{
            background-color: #0078D4;
            border-color: #0078D4;
            image: url({check_path});
        }}
    """)
    parent.btn_joystick_enable.clicked.connect(parent.toggle_joystick_state)
    
    parent.btn_joystick_save = QPushButton("Save")
    parent.btn_joystick_save.setStyleSheet("""
        QPushButton {
            background-color: #8BC34A;
            border: none;
            border-radius: 2px;
            color: #000000;
            font-weight: bold;
            font-size: 11px;
            padding: 5px 15px;
        }
        QPushButton:hover {
            background-color: #9CCC65;
        }
    """)
    parent.btn_joystick_save.clicked.connect(parent.save_joystick_config)
    
    parent.lbl_config_status = QLabel("Loaded Config for ArduSub")
    parent.lbl_config_status.setStyleSheet("color: #FFFFFF; font-family: 'Google Sans', sans-serif; font-size: 11px; font-weight: bold; margin-left: 10px;")
    
    top_row.addWidget(lbl_joy_title)
    top_row.addWidget(parent.combo_joystick)
    top_row.addWidget(parent.btn_joystick_enable)
    top_row.addWidget(parent.btn_joystick_save)
    top_row.addWidget(parent.lbl_config_status)
    top_row.addStretch()
    
    scroll_layout.addLayout(top_row)
    
    # Grid Configuration Panel
    grid_widget = QWidget()
    grid_widget.setStyleSheet("background-color: #1A1A1A; border: 1px solid #333333; border-radius: 4px; padding: 10px;")
    grid_layout = QGridLayout(grid_widget)
    grid_layout.setSpacing(8)
    grid_layout.setContentsMargins(10, 10, 10, 10)
    
    # Grid Column Headers
    grid_layout.addWidget(QLabel(""), 0, 0) # Top-left empty label
    
    lbl_col_axis = QLabel("Controller Axis")
    lbl_col_axis.setStyleSheet("color: #AAAAAA; font-weight: bold; font-size: 11px;")
    grid_layout.addWidget(lbl_col_axis, 0, 1, 1, 2)
    
    lbl_col_out = QLabel("Output")
    lbl_col_out.setStyleSheet("color: #AAAAAA; font-weight: bold; font-size: 11px;")
    grid_layout.addWidget(lbl_col_out, 0, 3)
    
    lbl_col_rev = QLabel("Reverse")
    lbl_col_rev.setStyleSheet("color: #AAAAAA; font-weight: bold; font-size: 11px;")
    grid_layout.addWidget(lbl_col_rev, 0, 4, Qt.AlignCenter)
    
    # Common Styles for Elements
    combo_style = f"""
        QComboBox {{
            background-color: #2d2d2d;
            border: 1px solid #404040;
            border-bottom: 1px solid #5a5a5a;
            border-radius: 4px;
            color: #FFFFFF;
            padding: 4px 25px 4px 8px;
            font-family: 'Google Sans', sans-serif;
            font-size: 11px;
            min-width: 95px;
        }}
        QComboBox:hover {{
            background-color: #323232;
            border-color: #555555;
        }}
        QComboBox::drop-down {{
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 20px;
            border: none;
        }}
        QComboBox::down-arrow {{
            image: url({down_arrow_path});
            width: 8px;
            height: 8px;
        }}
        QComboBox QAbstractItemView {{
            background-color: #2d2d2d;
            border: 1px solid #404040;
            color: #FFFFFF;
            selection-background-color: #353535;
            selection-color: #FFFFFF;
            outline: 0px;
        }}
    """
    
    btn_detect_style = """
        QPushButton {
            background-color: #8BC34A;
            border: none;
            border-radius: 2px;
            color: #000000;
            font-weight: bold;
            font-size: 10px;
            padding: 4px 8px;
            min-width: 75px;
        }
        QPushButton:hover {
            background-color: #9CCC65;
        }
        QPushButton:disabled {
            background-color: #555555;
            color: #888888;
        }
    """
    
    output_style = """
        QLabel {
            background-color: #FFFFFF;
            border: 1px solid #CCCCCC;
            color: #000000;
            font-family: Courier, monospace;
            font-size: 11px;
            font-weight: bold;
            padding: 4px;
            border-radius: 2px;
            min-width: 60px;
            max-width: 80px;
        }
    """
    
    chk_style = f"""
        QCheckBox::indicator {{
            width: 14px;
            height: 14px;
            border: 1px solid #404040;
            background-color: #2d2d2d;
            border-radius: 3px;
        }}
        QCheckBox::indicator:unchecked:hover {{
            background-color: #323232;
            border-color: #555555;
        }}
        QCheckBox::indicator:checked {{
            background-color: #0078D4;
            border-color: #0078D4;
            image: url({check_path});
        }}
    """
    
    # Populate RC 1 to RC 3 Axes
    parent.rc_axis_combos = {}
    parent.rc_auto_detect_btns = {}
    parent.rc_output_fields = {}
    parent.rc_reverse_chks = {}
    
    for i in range(1, 4):
        row_idx = i
        rc_key = f"rc{i}"
        
        lbl_rc = QLabel(f"RC {i}")
        lbl_rc.setStyleSheet("color: #FFFFFF; font-weight: bold; font-size: 11px;")
        grid_layout.addWidget(lbl_rc, row_idx, 0)
        
        combo = QComboBox()
        combo.setStyleSheet(combo_style)
        combo.addItem("None", None)
        for axis_id in range(8):
            combo.addItem(f"Axis {axis_id}", axis_id)
        grid_layout.addWidget(combo, row_idx, 1)
        parent.rc_axis_combos[rc_key] = combo
        
        btn_detect = QPushButton("Auto Detect")
        btn_detect.setStyleSheet(btn_detect_style)
        btn_detect.clicked.connect(lambda checked=False, r=rc_key: parent.start_auto_detect_axis(r))
        grid_layout.addWidget(btn_detect, row_idx, 2)
        parent.rc_auto_detect_btns[rc_key] = btn_detect
        
        lbl_output = QLabel("1500")
        lbl_output.setAlignment(Qt.AlignCenter)
        lbl_output.setStyleSheet(output_style)
        grid_layout.addWidget(lbl_output, row_idx, 3)
        parent.rc_output_fields[rc_key] = lbl_output
        
        chk_rev = QCheckBox()
        chk_rev.setStyleSheet(chk_style)
        grid_layout.addWidget(chk_rev, row_idx, 4, Qt.AlignCenter)
        parent.rc_reverse_chks[rc_key] = chk_rev
        
    # Populate vehicle action and navigation mode button mappings.
    parent.btn_mapping_combos = {}
    parent.btn_auto_detect_btns = {}
    parent.btn_output_fields = {}
    
    btn_labels = {
        "arm": "ARM VEHICLE",
        "disarm": "DISARM",
        "start": "START ROUTE",
        "stop": "STOP ROUTE",
        "rth": "RETURN TO HOME",
        "light": "TOGGLE LIGHT",
        "camera": "TOGGLE CAMERA",
        "manual_mode": "MANUAL MODE",
        "auto_mode": "AUTOMATIC MODE",
        "dock": "TOGGLE DOCKING"
    }
    
    button_names = list(btn_labels.keys())
    for idx, btn_key in enumerate(button_names):
        row_idx = 4 + idx
        
        lbl_btn = QLabel(btn_labels[btn_key])
        lbl_btn.setStyleSheet("color: #FFFFFF; font-weight: bold; font-size: 11px;")
        grid_layout.addWidget(lbl_btn, row_idx, 0)
        
        combo = QComboBox()
        combo.setStyleSheet(combo_style)
        combo.addItem("None", None)
        for btn_id in range(16):
            combo.addItem(f"Button {btn_id}", btn_id)
        grid_layout.addWidget(combo, row_idx, 1)
        parent.btn_mapping_combos[btn_key] = combo
        
        btn_detect = QPushButton("Auto Detect")
        btn_detect.setStyleSheet(btn_detect_style)
        btn_detect.clicked.connect(lambda checked=False, b=btn_key: parent.start_auto_detect_button(b))
        grid_layout.addWidget(btn_detect, row_idx, 2)
        parent.btn_auto_detect_btns[btn_key] = btn_detect
        
        lbl_output = QLabel("0")
        lbl_output.setAlignment(Qt.AlignCenter)
        lbl_output.setStyleSheet(output_style)
        grid_layout.addWidget(lbl_output, row_idx, 3)
        parent.btn_output_fields[btn_key] = lbl_output
        
    scroll_layout.addWidget(grid_widget)
    
    # Add Thruster limits panel directly inside Joystick Scroll Area
    thruster_widget = QWidget()
    thruster_widget.setStyleSheet("background-color: #1A1A1A; border: 1px solid #333333; border-radius: 4px; padding: 15px;")
    thruster_grid = QGridLayout(thruster_widget)
    thruster_grid.setSpacing(12)
    thruster_grid.setContentsMargins(10, 10, 10, 10)
    
    lbl_thruster_title = QLabel("THRUSTER SPEEDS & PWM LIMITS CONFIGURATION")
    lbl_thruster_title.setStyleSheet("color: #FFFFFF; font-weight: bold; font-family: 'Google Sans', sans-serif; font-size: 11px; border: none; background: transparent;")
    thruster_grid.addWidget(lbl_thruster_title, 0, 0, 1, 2)
    
    lbl_thruster_desc = QLabel("Configure the maximum and minimum speed limits (PWM pulse width in microseconds) for manual control mode. This scales joystick axis outputs to limit maximum speed and reverse thrust.")
    lbl_thruster_desc.setStyleSheet("color: #888888; font-size: 10px; border: none; background: transparent;")
    lbl_thruster_desc.setWordWrap(True)
    thruster_grid.addWidget(lbl_thruster_desc, 1, 0, 1, 2)
    
    lbl_min = QLabel("Minimum PWM Speed Limit (us):")
    lbl_min.setStyleSheet("color: #FFFFFF; font-size: 11px; font-weight: bold; border: none; background: transparent;")
    parent.input_thruster_min = QLineEdit(str(parent.thruster_min_limit))
    parent.input_thruster_min.setStyleSheet("QLineEdit { background-color: #121212; border: 1px solid #333333; border-radius: 2px; color: #FFFFFF; padding: 5px; font-family: 'Google Sans', sans-serif; font-size: 11px; }")
    
    thruster_grid.addWidget(lbl_min, 2, 0)
    thruster_grid.addWidget(parent.input_thruster_min, 2, 1)
    
    lbl_max = QLabel("Maximum PWM Speed Limit (us):")
    lbl_max.setStyleSheet("color: #FFFFFF; font-size: 11px; font-weight: bold; border: none; background: transparent;")
    parent.input_thruster_max = QLineEdit(str(parent.thruster_max_limit))
    parent.input_thruster_max.setStyleSheet("QLineEdit { background-color: #121212; border: 1px solid #333333; border-radius: 2px; color: #FFFFFF; padding: 5px; font-family: 'Google Sans', sans-serif; font-size: 11px; }")
    
    thruster_grid.addWidget(lbl_max, 3, 0)
    thruster_grid.addWidget(parent.input_thruster_max, 3, 1)
    
    parent.btn_thruster_set = QPushButton("Set Limits")
    parent.btn_thruster_set.setStyleSheet("""
        QPushButton {
            background-color: #8BC34A;
            border: none;
            border-radius: 2px;
            color: #000000;
            font-weight: bold;
            font-size: 11px;
            padding: 6px 15px;
        }
        QPushButton:hover {
            background-color: #9CCC65;
        }
    """)
    parent.btn_thruster_set.clicked.connect(parent.set_thruster_limits)
    thruster_grid.addWidget(parent.btn_thruster_set, 4, 1, Qt.AlignRight)
    
    parent.lbl_thruster_feedback = QLabel("")
    parent.lbl_thruster_feedback.setStyleSheet("color: #8BC34A; font-family: 'Google Sans', sans-serif; font-size: 11px; font-weight: bold; border: none; background: transparent;")
    thruster_grid.addWidget(parent.lbl_thruster_feedback, 4, 0)
    
    scroll_layout.addWidget(thruster_widget)
    
    scroll_layout.addStretch()
    
    scroll.setWidget(scroll_content)
    joystick_layout.addWidget(scroll)
    
    # Create Light Page
    light_page = QWidget()
    light_page_layout = QVBoxLayout(light_page)
    light_page_layout.setContentsMargins(24, 24, 24, 24)
    light_page_layout.setSpacing(16)

    equipment_header = QVBoxLayout()
    equipment_header.setSpacing(4)
    lbl_payload_title = QLabel("EQUIPMENT CONTROL")
    lbl_payload_title.setStyleSheet("color: #E2E8F0; font-family: 'Google Sans', sans-serif; font-size: 16px; font-weight: bold; letter-spacing: 0.5px; border: none; background: transparent;")
    equipment_header.addWidget(lbl_payload_title)
    lbl_payload_desc = QLabel("Switch onboard equipment on or off.")
    lbl_payload_desc.setStyleSheet("color: #94A3B8; font-family: 'Google Sans', sans-serif; font-size: 11px; border: none; background: transparent;")
    equipment_header.addWidget(lbl_payload_desc)
    light_page_layout.addLayout(equipment_header)

    payload_widget = QFrame()
    payload_widget.setObjectName("EquipmentControlPanel")
    payload_widget.setStyleSheet("QFrame#EquipmentControlPanel { background-color: #151A21; border: 1px solid #29313B; border-radius: 10px; }")
    payload_layout = QHBoxLayout(payload_widget)
    payload_layout.setContentsMargins(14, 14, 14, 14)
    payload_layout.setSpacing(12)

    def add_equipment_card(title, description, accent, toggle):
        card = QFrame()
        card.setObjectName("EquipmentControlCard")
        card.setStyleSheet("QFrame#EquipmentControlCard { background-color: #10151B; border: 1px solid #29313B; border-radius: 8px; }")
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(12)
        info = QVBoxLayout()
        info.setSpacing(4)
        name = QLabel(title)
        name.setStyleSheet(f"color: {accent}; font-family: 'Google Sans', sans-serif; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        detail = QLabel(description)
        detail.setWordWrap(True)
        detail.setStyleSheet("color: #94A3B8; font-family: 'Google Sans', sans-serif; font-size: 10px; border: none; background: transparent;")
        info.addWidget(name)
        info.addWidget(detail)
        card_layout.addLayout(info, 1)
        card_layout.addWidget(toggle, 0, Qt.AlignVCenter)
        payload_layout.addWidget(card, 1)

    parent.btn_light_toggle = ToggleSwitch()
    parent.btn_light_toggle.clicked.connect(parent.toggle_light)
    add_equipment_card("LIGHT BEACON", "Vehicle visibility light", "#FBBF24", parent.btn_light_toggle)

    parent.btn_camera_toggle = ToggleSwitch()
    parent.btn_camera_toggle.clicked.connect(parent.toggle_camera)
    add_equipment_card("MISSION CAMERA", "Onboard video capture", "#38BDF8", parent.btn_camera_toggle)

    parent.btn_sonar_toggle = ToggleSwitch()
    parent.btn_sonar_toggle.clicked.connect(parent.toggle_sonar)
    add_equipment_card("PING360 SONAR", "Acoustic scanning sensor", "#A78BFA", parent.btn_sonar_toggle)
    
    light_page_layout.addWidget(payload_widget)
    light_page_layout.addStretch()

    # Instantiate original members as hidden so other logic works
    parent.bar_throttle = QProgressBar()
    parent.bar_steering = QProgressBar()
    parent.bar_pitch = QProgressBar()
    
    parent.load_joystick_config()
    
    # Bind Autopilot, Camera Rec, & Sonar setup helpers to parent dynamically
    parent.select_autopilot_setup = lambda: select_autopilot_setup(parent)
    parent.select_cam_rec_setup = lambda: select_cam_rec_setup(parent)
    parent.select_sonar_setup = lambda: select_sonar_setup(parent)
    
    # Create Autopilot, Camera Rec, & Sonar Pages
    autopilot_page = create_autopilot_page(parent)
    camera_rec_page = create_camera_rec_page(parent)
    sonar_page = create_sonar_page(parent)

    # The dashboard keeps only the PWM telemetry cards. Reparent its existing
    # servo controls into their dedicated Hardware Setup page.
    servo_output_page = QWidget()
    servo_output_page.setObjectName("ServoOutputPage")
    servo_output_page.setStyleSheet("QWidget#ServoOutputPage { background-color: #0B0F14; }")
    servo_output_layout = QVBoxLayout(servo_output_page)
    servo_output_layout.setContentsMargins(28, 28, 28, 24)
    servo_output_layout.setSpacing(10)

    servo_output_heading = QWidget()
    heading_layout = QVBoxLayout(servo_output_heading)
    heading_layout.setContentsMargins(0, 0, 0, 8)
    heading_layout.setSpacing(5)
    servo_output_title = QLabel("SERVO OUTPUT CONTROLS")
    servo_output_title.setStyleSheet("color: #00E5FF; font-family: 'Google Sans', sans-serif; font-weight: bold; font-size: 13px; letter-spacing: 0.6px; background: transparent;")
    heading_layout.addWidget(servo_output_title)
    servo_output_hint = QLabel("Manual PWM control  •  Neutral position: 1500 µs")
    servo_output_hint.setStyleSheet("color: #94A3B8; font-family: 'Google Sans', sans-serif; font-size: 10px; background: transparent;")
    heading_layout.addWidget(servo_output_hint)
    servo_output_layout.addWidget(servo_output_heading, 0, Qt.AlignHCenter)

    parent.tile_actuators.content_layout.removeWidget(parent.servo_controls_widget)
    parent.servo_controls_widget.setParent(servo_output_page)
    parent.servo_controls_widget.setMaximumWidth(1120)
    parent.servo_controls_widget.setMinimumWidth(560)
    parent.servo_controls_widget.setMinimumHeight(250)
    parent.servo_controls_widget.layout().setContentsMargins(22, 20, 22, 20)
    parent.servo_controls_widget.layout().setSpacing(16)
    servo_output_layout.addWidget(parent.servo_controls_widget, 0, Qt.AlignHCenter)
    servo_output_layout.addStretch()
    
    # Add pages to stacked widget
    parent.setup_stacked.addWidget(joystick_page)      # 0: Joystick
    parent.setup_stacked.addWidget(light_page)         # 1: Equipment Control
    parent.setup_stacked.addWidget(autopilot_page)     # 2: Parameters
    parent.setup_stacked.addWidget(camera_rec_page)    # 3: Camera Rec
    parent.setup_stacked.addWidget(sonar_page)         # 4: Ping360 Sonar
    parent.setup_stacked.addWidget(servo_output_page)   # 5: Servo Output
    parent.setup_stacked.setCurrentIndex(0)
    
    # Connect menu click slots
    parent.btn_menu_joystick.clicked.connect(parent.select_joystick_setup)
    parent.btn_menu_light.clicked.connect(parent.select_light_setup)
    parent.btn_menu_autopilot.clicked.connect(parent.select_autopilot_setup)
    parent.btn_menu_cam_rec.clicked.connect(parent.select_cam_rec_setup)
    parent.btn_menu_sonar.clicked.connect(parent.select_sonar_setup)
    parent.btn_menu_servo_output.clicked.connect(lambda: select_servo_output_setup(parent))
    
    return page

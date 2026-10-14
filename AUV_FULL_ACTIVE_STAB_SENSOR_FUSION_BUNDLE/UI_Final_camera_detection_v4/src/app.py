import os
import math
import time as _time
from PySide6.QtWidgets import QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFrame, QLabel, QStackedWidget, QTableWidget, QTableWidgetItem, QHeaderView, QCheckBox, QSlider, QPushButton, QSplitter, QComboBox, QScrollArea, QTextEdit, QSizePolicy, QDialog
from PySide6.QtCore import Qt, Slot, QTimer, QUrl, QDateTime, QSettings
from PySide6.QtGui import QColor, QFont, QPen, QBrush
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from src.widgets import TopBar, TelemetryCard, MarineCompass, MarineHorizon, Sidebar, MagnetometerVectorWidget, RealTimeChart, Marine3DHorizon, Ping360SonarWidget, VisionTargetWidget, ROVPositionWidget, AcousticFFTWidget, RadialGaugeWidget, VerticalDepthGauge, BatteryGauge, ActuatorsGauge, ExcavatableTile, TileExcavationWindow
from src.connection import MarineTelemetryThread
from src.styles import OCEAN_STYLESHEET



class MultiWidgetProxy:
    """
    Wraps multiple UI widgets or Card/Chart shims, forwarding method calls to all instances.
    Enables multi-monitor projections to mirror updates without breaking the main window.
    """
    def __init__(self, widgets=None):
        self._widgets = widgets if widgets is not None else []
        
    def add_widget(self, w):
        if w not in self._widgets:
            self._widgets.append(w)
            
    def remove_widget(self, w):
        if w in self._widgets:
            self._widgets.remove(w)
            
    def __getattr__(self, name):
        def method_wrapper(*args, **kwargs):
            results = []
            for w in list(self._widgets):
                try:
                    # Verify C++ object validity
                    w.parent()
                except RuntimeError:
                    if w in self._widgets:
                        self._widgets.remove(w)
                    continue
                    
                try:
                    attr = getattr(w, name)
                    if callable(attr):
                        results.append(attr(*args, **kwargs))
                    else:
                        results.append(attr)
                except Exception:
                    pass
            
            if not results:
                return None
                
            # If values returned are QObjects or dict objects, wrap in a sub-proxy for chained calls
            from PySide6.QtCore import QObject
            if all(isinstance(r, QObject) or hasattr(r, '__dict__') for r in results):
                return MultiWidgetProxy(results)
            return results[0]
            
        return method_wrapper


class MultiMonitorParentProxy:
    """
    Temporary proxy parent passed to page creators during multi-monitor setup.
    Captures new widgets without immediately contaminating the main window attributes,
    preventing Layout TypeError crashes during instantiation.
    """
    def __init__(self, real_parent):
        self._real_parent = real_parent
        self._new_widgets = {}
        
    def __getattr__(self, name):
        if name in self._new_widgets:
            return self._new_widgets[name]
        return getattr(self._real_parent, name)
        
    def __setattr__(self, name, value):
        if name in ('_real_parent', '_new_widgets'):
            super().__setattr__(name, value)
        else:
            self._new_widgets[name] = value


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


class MarineGroundStation(QMainWindow):
    """
    Main Application Window of the Marine Control Groundstation.
    """
    def __setattr__(self, name, value):
        from PySide6.QtWidgets import QWidget
        
        # We proxy QWidgets and custom shims (CardShim, ChartShim) to support multi-monitor updates
        is_proxyable = (
            isinstance(value, QWidget) or 
            type(value).__name__ in ('CardShim', 'ChartShim')
        ) and not isinstance(value, (QMainWindow, QStackedWidget))
        
        if is_proxyable:
            existing = self.__dict__.get(name)
            if existing is not None and (isinstance(existing, QWidget) or type(existing).__name__ in ('CardShim', 'ChartShim') or isinstance(existing, MultiWidgetProxy)):
                if not isinstance(existing, MultiWidgetProxy):
                    proxy = MultiWidgetProxy()
                    proxy.add_widget(existing)
                    self.__dict__[name] = proxy
                else:
                    proxy = existing
                
                proxy.add_widget(value)
                return
                
        super().__setattr__(name, value)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Marine Control Groundstation - ASV")
        self.resize(1100, 650)
        self.setMinimumSize(850, 500)
        
        # Apply the global stylesheet with dynamically resolved absolute paths
        import os
        src_dir = os.path.dirname(os.path.abspath(__file__))
        down_arrow_path = os.path.join(src_dir, "down_arrow.png").replace("\\", "/")
        check_path = os.path.join(src_dir, "check.png").replace("\\", "/")
        check_dark_path = os.path.join(src_dir, "check_dark.png").replace("\\", "/")
        
        app_stylesheet = OCEAN_STYLESHEET.replace("src/down_arrow.png", down_arrow_path)
        app_stylesheet = app_stylesheet.replace("src/check.png", check_path)
        app_stylesheet = app_stylesheet.replace("src/check_dark.png", check_dark_path)
        self.setStyleSheet(app_stylesheet)
        
        self.telemetry_thread = None
        self.current_theme = "cockpit"
        self.wp_reach_threshold = 1.0
        self.thruster_min_limit = 1100
        self.thruster_max_limit = 1900
        self.planned_waypoints = []
        self.last_lat = None
        self.last_lon = None
        self.last_time = None
        self.current_speed = 0.0
        self.vessel_icon_type = "dot"
        self.logging_enabled = True
        self.wp_upload_acknowledged = False
        # Connected telemetry demonstration disabled - use genuine hardware readings
        self.showcase_dummy_telemetry = False
        self.showcase_latitude = 0.0
        self.showcase_longitude = 0.0
        # Keep the last configured docking point across application restarts.
        self.docking_settings = QSettings("NIOT", "MarineGroundStation")
        self.docking_station_latitude = self.docking_settings.value(
            "docking/latitude", 13.0827000, type=float)
        self.docking_station_longitude = self.docking_settings.value(
            "docking/longitude", 80.2707000, type=float)
        self.docking_station_heading = self.docking_settings.value(
            "docking/heading", 0.0, type=float)
        self.docking_route_set = False  # backend route is not assumed after either side restarts
        self.sim_gps_loss = False
        self.sim_sonar_dropout = False
        self._docking_sonar_angle = 0.0
        self.sim_low_battery = False
        default_log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
        if not os.path.exists(default_log_dir):
            try:
                os.makedirs(default_log_dir)
            except:
                pass
        self.log_folder_path = default_log_dir
        self.log_file_path = None
        
        # Mission state variables
        self.mission_active = False
        self.current_wp_idx = 0
        self.is_armed = False
        self.light_state = 0
        self.camera_state = 0
        self.is_docking = False
        self.active_stabilization = False
        self.active_stabilization_mode = "OFF"
        self.fusion_backend_state = {
            "connected": False, "valid": False, "range_m": None,
            "bearing_rel_deg": None, "sigma_m": None, "sources": "",
            "stage": "NO_FIX", "age_s": None, "timestamp": 0.0, "error": ""
        }
        self.stabilization_status_timer = QTimer(self)
        self.stabilization_status_timer.setInterval(2000)
        self.stabilization_status_timer.timeout.connect(self.request_active_stabilization_status)
        self._cam_rec_active = False
        self._cam_rec_warming_up = False
        self._cam_rec_warmup_remaining = 15
        self._cam_rec_warmup_timer = None
        self._cam_rec_timer = None
        self.sonar_active = False
        self._user_toggled_sonar = False
        self._last_hw_sonar_time = 0.0
        self._sonar_anim_timer = None
        
        # Initialize pygame for joystick support
        try:
            import pygame
            pygame.init()
            pygame.joystick.init()
        except Exception as e:
            print(f"Error initializing pygame: {e}")
            
        self.joystick = None
        self.joystick_timer = QTimer(self)
        self.joystick_timer.setInterval(20) # 50Hz, 20ms update interval
        self.joystick_timer.timeout.connect(self.poll_joystick_input)
        
        self.joystick_refresh_timer = QTimer(self)
        self.joystick_refresh_timer.setInterval(2000) # 2 seconds
        self.joystick_refresh_timer.timeout.connect(self.refresh_joystick_devices)
        
        # Joystick auto-detect variables
        self.detecting_axis_key = None
        self.detecting_button_key = None
        self.detecting_initial_axes = {}
        self.joystick_auto_enable = self.docking_settings.value(
            "joystick/auto_enable", False, type=bool)
        self.joystick_auto_reconnect = self.joystick_auto_enable
        
        # Watchdog to monitor telemetry data activity
        self.watchdog_timer = QTimer(self)
        self.watchdog_timer.setInterval(2000) # 2 seconds watchdog timeout
        self.watchdog_timer.timeout.connect(self.on_data_timeout)
        
        # Command stream timer (20 Hz periodic transmission for ultra-smooth control)
        self.command_stream_timer = QTimer(self)
        self.command_stream_timer.setInterval(50) # 50ms = 20 Hz
        self.command_stream_timer.timeout.connect(self.send_immediate_cmd)

        # Map rendering is intentionally decoupled from telemetry.  The latest GPS
        # fix is coalesced here and rendered at 5 Hz, so QWebEngine can never build
        # an unbounded queue of runJavaScript() calls during high-rate telemetry.
        self._latest_map_fix = None
        self._earth_map_ready = False
        self._plan_map_ready = False
        self.map_render_timer = QTimer(self)
        self.map_render_timer.setInterval(200)
        self.map_render_timer.timeout.connect(self._flush_map_render)
        self.map_render_timer.start()
        
        self.init_ui()
        self.clear_telemetry_data()
        self.joystick_refresh_timer.start()

    def init_ui(self):
        # Main central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # Main layout
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # 1. Top Bar
        self.top_bar = TopBar()
        self.top_bar.connect_requested.connect(self.connect_serial)
        self.top_bar.disconnect_requested.connect(self.disconnect_serial)
        self.top_bar.alert_badge.clicked.connect(self.on_alert_badge_clicked)
        main_layout.addWidget(self.top_bar)
        
        # Flashing Warning Banner Frame (placed globally below the top bar, visible on all pages!)
        self.warning_banner_frame = QFrame()
        self.warning_banner_frame.setObjectName("WarningBannerFrame")
        self.warning_banner_frame.setStyleSheet("""
            QFrame#WarningBannerFrame {
                background-color: #2D080D;
                border: 2px solid #D32F2F;
                border-radius: 6px;
                padding: 10px 15px;
            }
            QLabel#WarningBannerLabel {
                color: #FF5252;
                font-family: 'Google Sans', sans-serif;
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 0.5px;
            }
        """)
        wb_layout = QHBoxLayout(self.warning_banner_frame)
        wb_layout.setContentsMargins(15, 10, 15, 10)
        wb_layout.setSpacing(10)
        self.lbl_warning_banner = QLabel("SYSTEM WARNING: NO FAULTS SIMULATED")
        self.lbl_warning_banner.setObjectName("WarningBannerLabel")
        wb_layout.addWidget(self.lbl_warning_banner)
        wb_layout.addStretch()
        main_layout.addWidget(self.warning_banner_frame)
        self.warning_banner_frame.setVisible(False) # Hidden by default
        
        # Warning flash timer
        self.warning_flash_timer = QTimer(self)
        self.warning_flash_timer.setInterval(500)
        self.warning_flash_timer.timeout.connect(self.flash_warning_banner)
        self.warning_flash_state = False
        
        # 2. Main Body Horizontal Frame (contains sidebar + stacked pages)
        body_frame = QFrame()
        body_frame.setObjectName("BodyFrame")
        body_layout = QHBoxLayout(body_frame)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        
        # 3. Sidebar Widget
        self.sidebar = Sidebar()
        body_layout.addWidget(self.sidebar)
        
        # 4. Stacked Widget containing all view pages
        self.stacked_widget = QStackedWidget()
        body_layout.addWidget(self.stacked_widget)
        
        # Connection: Click sidebar buttons to switch pages
        self.sidebar.page_changed.connect(self.stacked_widget.setCurrentIndex)
        
        # -------------------------------------------------------------
        # PAGE 0: DASHBOARD (Comprehensive Cockpit: AHRS, Chamber, GPS, BMS, Actuators)
        # -------------------------------------------------------------
        page_dashboard = QWidget()
        dashboard_scroll = QScrollArea()
        dashboard_scroll.setWidgetResizable(True)
        dashboard_scroll.setFrameShape(QFrame.NoFrame)
        dashboard_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        dashboard_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        dashboard_scroll.setStyleSheet("QScrollArea { background-color: transparent; border: none; }")
        
        dashboard_container = QWidget()
        dashboard_main_layout = QVBoxLayout(dashboard_container)
        dashboard_main_layout.setContentsMargins(12, 10, 12, 10)
        dashboard_main_layout.setSpacing(10)
        
        dashboard_layout = QHBoxLayout()
        dashboard_layout.setSpacing(12)
        dashboard_main_layout.addLayout(dashboard_layout)
        
        self.dashboard_container = dashboard_container
        self.dashboard_container.setObjectName("DashboardContainer")
        self.dashboard_container.setStyleSheet("background-color: #F4F7FB;")
        
        # 1. Card Instantiation with clean light theme matching the reference layout
        # Subsystem accent colours keep the dashboard readable at a glance
        AC_AHRS = "#0D6EFD"    # Blue   - attitude & heading
        AC_ENV = "#EF4444"     # Red    - chamber environment
        AC_GPS = "#0EA5E9"     # Sky    - satellite positioning
        AC_PWR = "#F59E0B"     # Amber  - battery / power
        AC_ACT = "#8B5CF6"     # Violet - actuators & thrusters
        self.dash_accents = {
            "ahrs": AC_AHRS, "env": AC_ENV, "gps": AC_GPS,
            "power": AC_PWR, "actuators": AC_ACT, "thrusters": AC_ACT,
        }

        self.cards = {
            "roll": TelemetryCard("Roll", "0.0°", "deg", theme="light", accent=AC_AHRS),
            "pitch": TelemetryCard("Pitch", "0.0°", "deg", theme="light", accent=AC_AHRS),
            "yaw": TelemetryCard("Yaw / Heading", "0.0°", "deg", theme="light", accent=AC_AHRS),
            "latitude": TelemetryCard("Latitude", "-- °", "N/S", theme="light", accent=AC_GPS),
            "longitude": TelemetryCard("Longitude", "-- °", "E/W", theme="light", accent=AC_GPS),
            "satellites": TelemetryCard("Satellites", "0", "SATS", theme="light", accent=AC_GPS),
            "chamber_temp": TelemetryCard("Chamber Temp", "0.0", "°C", theme="light", accent=AC_ENV),
            "chamber_hum": TelemetryCard("Chamber Hum", "0.0", "%", theme="light", accent=AC_ENV),
            "volts": TelemetryCard("Voltage", "0.0", "V", theme="light", accent=AC_PWR),
            "amps": TelemetryCard("Current", "0.0", "A", theme="light", accent=AC_PWR),
            "watts": TelemetryCard("Power", "0.0", "W", theme="light", accent=AC_PWR),
            "batt_temp": TelemetryCard("Batt Temp", "0.0", "°C", theme="light", accent=AC_PWR),
            "soc": TelemetryCard("SOC", "0", "%", theme="light", accent=AC_PWR),
            "soh": TelemetryCard("SOH", "0", "%", theme="light", accent=AC_PWR),
            "p1": TelemetryCard("T1 PWM", "1500", "µs", theme="light", accent=AC_ACT),
            "p2": TelemetryCard("T2 PWM", "1500", "µs", theme="light", accent=AC_ACT),
            "p3": TelemetryCard("T3 PWM", "1500", "µs", theme="light", accent=AC_ACT),
            "failsafe": TelemetryCard("Failsafe", "0.0", "FS", theme="light", accent=AC_ACT)
        }
        
        # Three balanced columns layout (1:1:1 distribution)
        col_left = QVBoxLayout()
        col_left.setSpacing(12)
        
        col_center = QVBoxLayout()
        col_center.setSpacing(12)
        
        col_right = QVBoxLayout()
        col_right.setSpacing(12)
        
        # ==========================================
        # TILE 1: AHRS (Attitude and Heading)
        # ==========================================
        self.tile_ahrs = ExcavatableTile("ahrs", "AHRS - ATTITUDE & HEADING",
                                         accent=AC_AHRS)
        ahrs_layout = self.tile_ahrs.content_layout

        # Numeric Row
        ahrs_cards = QHBoxLayout()
        ahrs_cards.setSpacing(6)
        ahrs_cards.addWidget(self.cards["roll"])
        ahrs_cards.addWidget(self.cards["pitch"])
        ahrs_cards.addWidget(self.cards["yaw"])
        ahrs_layout.addLayout(ahrs_cards)

        # ---- Instrument deck: recessed panel holding the three dials ----
        self.ahrs_deck = QFrame()
        self.ahrs_deck.setObjectName("AhrsDeck")
        self.ahrs_deck.setStyleSheet("""
            QFrame#AhrsDeck {
                background-color: #0E1622;
                border: 1px solid #1E2A3A;
                border-radius: 9px;
            }
        """)
        deck_layout = QVBoxLayout(self.ahrs_deck)
        deck_layout.setContentsMargins(8, 7, 8, 7)
        deck_layout.setSpacing(5)

        # Compact instrument grid: two dials across, with heading centered below.
        dials_layout = QGridLayout()
        dials_layout.setHorizontalSpacing(16)
        dials_layout.setVerticalSpacing(8)
        dials_layout.setColumnStretch(0, 1)
        dials_layout.setColumnStretch(1, 1)
        dials_layout.setAlignment(Qt.AlignCenter)

        DIAL = 110
        cap_style = ("color: #94A9C4; font-size: 8px; font-weight: bold;"
                     " letter-spacing: 1.2px; border: none; background: transparent;")

        def _build_dial(caption, widget):
            box = QVBoxLayout()
            box.setSpacing(3)
            box.setAlignment(Qt.AlignCenter)
            cap = QLabel(caption)
            cap.setAlignment(Qt.AlignCenter)
            cap.setStyleSheet(cap_style)
            widget.setFixedSize(DIAL, DIAL)
            box.addWidget(cap)
            box.addWidget(widget, 0, Qt.AlignCenter)
            return box, cap

        # Dial 1: Attitude (roll + pitch artificial horizon)
        self.horizon_widget = MarineHorizon(theme="cockpit", mode="horizon")
        d1_box, lbl_d1 = _build_dial("ATTITUDE", self.horizon_widget)
        self.lbl_dash_d1 = lbl_d1

        # Dial 2: Pitch inclinometer
        self.horizon_pitch_widget = MarineHorizon(theme="cockpit", mode="pitch")
        self.horizon_3d_widget = self.horizon_pitch_widget
        d2_box, lbl_d2 = _build_dial("PITCH", self.horizon_pitch_widget)
        self.lbl_dash_d2 = lbl_d2

        # Dial 3: Gyro compass
        self.compass_widget = MarineCompass(theme="cockpit")
        d3_box, lbl_d3 = _build_dial("HEADING", self.compass_widget)
        self.lbl_dash_d3 = lbl_d3

        dials_layout.addLayout(d1_box, 0, 0, Qt.AlignCenter)
        dials_layout.addLayout(d2_box, 0, 1, Qt.AlignCenter)
        dials_layout.addLayout(d3_box, 1, 0, 1, 2, Qt.AlignCenter)
        deck_layout.addLayout(dials_layout)
        ahrs_layout.addWidget(self.ahrs_deck)

        # Scrolling Chart
        self.attitude_chart = RealTimeChart("VEHICLE ATTITUDE DYNAMICS", -20.0, 20.0, "deg", theme="light")
        self.attitude_chart.add_series("Roll", "#0D6EFD")    # Blue
        self.attitude_chart.add_series("Pitch", "#F59E0B")   # Amber
        # Qt Charts reserves vertical space for the title and legend. A taller
        # plot area keeps the actual attitude traces visible beneath them.
        self.attitude_chart.setMinimumHeight(230)
        # Draw a visible neutral baseline before the first telemetry packet.
        self.attitude_chart.append_data([0.0, 0.0])
        self.attitude_chart.append_data([0.0, 0.0])
        ahrs_layout.addWidget(self.attitude_chart)

        col_left.addWidget(self.tile_ahrs, 5)
        
        # ==========================================
        # TILE 2: CHAMBER ENVIRONMENT (DHT22)
        # ==========================================
        self.tile_env = ExcavatableTile("env", "CHAMBER ENVIRONMENT (DHT22)", accent=AC_ENV)
        env_layout = self.tile_env.content_layout
        
        env_cards_1 = QHBoxLayout()
        env_cards_1.setSpacing(6)
        env_cards_1.addWidget(self.cards["chamber_temp"])
        env_cards_1.addWidget(self.cards["chamber_hum"])
        env_layout.addLayout(env_cards_1)
        
        self.env_chart = RealTimeChart("CHAMBER TEMP (°C) & HUMIDITY (%)", 0.0, 40.0, "C/%", theme="light")
        self.env_chart.add_series("Temp", "#EF4444")      # Red
        self.env_chart.add_series("Humidity", "#10B981")  # Green
        self.env_chart.setMinimumHeight(95)
        env_layout.addWidget(self.env_chart)
        
        
        # ==========================================
        # TILE 3: GPS (Satellite Positioning)
        # ==========================================
        self.tile_gps = ExcavatableTile("gps", "GPS - POSITIONING & SATELLITES", accent=AC_GPS)
        gps_layout = self.tile_gps.content_layout
        
        gps_cards = QHBoxLayout()
        gps_cards.setSpacing(6)
        gps_cards.addWidget(self.cards["latitude"])
        gps_cards.addWidget(self.cards["longitude"])
        gps_cards.addWidget(self.cards["satellites"])
        gps_layout.addLayout(gps_cards)

        self.gps_chart = RealTimeChart("SATELLITE RECEPTION STRENGTH", 0.0, 50.0, "sats", theme="light")
        self.gps_chart.add_series("Satellites", AC_GPS)
        # The GPS tile is weighted lighter than its neighbours, so the chart
        # simply fills whatever the tile gets instead of forcing it taller.
        self.gps_chart.setMinimumHeight(88)
        gps_layout.addWidget(self.gps_chart)
        
        col_center.addWidget(self.tile_gps, 3)
        
        # ==========================================
        # TILE 4: POWER SYSTEM (CAN BMS)
        # ==========================================
        self.tile_power = ExcavatableTile("power", "CAN BMS - 48V/52V BATTERY MANAGEMENT", accent=AC_PWR)
        power_layout = self.tile_power.content_layout
        
        power_cards_1 = QHBoxLayout()
        power_cards_1.setSpacing(6)
        power_cards_1.addWidget(self.cards["volts"])
        power_cards_1.addWidget(self.cards["amps"])
        power_cards_1.addWidget(self.cards["watts"])
        power_layout.addLayout(power_cards_1)
        
        power_cards_2 = QHBoxLayout()
        power_cards_2.setSpacing(6)
        power_cards_2.addWidget(self.cards["batt_temp"])
        power_cards_2.addWidget(self.cards["soc"])
        power_cards_2.addWidget(self.cards["soh"])
        power_layout.addLayout(power_cards_2)
        
        power_visuals = QHBoxLayout()
        power_visuals.setSpacing(8)
        
        self.battery_gauge = BatteryGauge(theme="light")
        self.battery_gauge.setFixedWidth(56)
        self.battery_gauge.setMinimumHeight(95)
        self.power_chart = RealTimeChart("POWER DYNAMICS", 0.0, 60.0, "V/A", theme="light")
        self.power_chart.add_series("Voltage", "#0284C7")  # Cyan / Blue
        self.power_chart.add_series("Current", "#D946EF")  # Purple
        self.power_chart.setMinimumHeight(95)
        
        power_visuals.addWidget(self.battery_gauge)
        power_visuals.addWidget(self.power_chart, 1)
        power_layout.addLayout(power_visuals)
        
        col_center.addWidget(self.tile_power, 5)
        
        # ==========================================
        # TILE 5: ACTUATOR OUTPUTS & VEHICLE PAYLOAD CONTROLS
        # ==========================================
        self.tile_actuators = ExcavatableTile("actuators", "ACTUATOR OUTPUTS", accent=AC_ACT)
        actuators_layout = self.tile_actuators.content_layout
        
        actuators_cards = QHBoxLayout()
        actuators_cards.setSpacing(6)
        actuators_cards.addWidget(self.cards["p1"])
        actuators_cards.addWidget(self.cards["p2"])
        actuators_cards.addWidget(self.cards["p3"])
        actuators_cards.addWidget(self.cards["failsafe"])
        actuators_layout.addLayout(actuators_cards)
        
        # Interactive Motor Throttle Sliders
        slider_groove_style = """
            QSlider::groove:horizontal {
                border: none;
                background: #29313B;
                height: 10px;
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #00E5FF;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #FFFFFF;
                border: 2px solid #00E5FF;
                width: 19px;
                margin-top: -4px;
                margin-bottom: -4px;
                border-radius: 7px;
            }
            QSlider::handle:horizontal:hover {
                background: #00E5FF;
                border-color: #00B8D4;
            }
        """
        btn_reset_sm_style = """
            QPushButton {
                background-color: #1E293B;
                color: #00E5FF;
                border: 1px solid #334155;
                border-radius: 5px;
                padding: 5px 9px;
                font-family: 'Consolas', monospace;
                font-size: 11px;
                font-weight: bold;
                min-width: 44px;
            }
            QPushButton:hover {
                background-color: #0284C7;
                color: #FFFFFF;
                border-color: #00E5FF;
            }
        """
        
        sliders_box = QVBoxLayout()
        sliders_box.setSpacing(18)
        
        # Thruster 1 (Pitch / Surge)
        t1_row = QHBoxLayout()
        lbl_t1 = QLabel("T1 (Surge):")
        lbl_t1.setStyleSheet("color: #38BDF8; font-weight: bold; font-size: 12px; min-width: 82px;")
        self.lbl_dash_t1_val = QLabel("1500 µs")
        self.lbl_dash_t1_val.setStyleSheet("color: #E2E8F0; font-family: 'Consolas', monospace; font-size: 11px; font-weight: bold; min-width: 64px;")
        self.lbl_dash_t1_badge = QLabel("NEUTRAL")
        self.lbl_dash_t1_badge.setStyleSheet("background-color: #1E293B; border: 1px solid #334155; border-radius: 8px; color: #94A3B8; font-size: 9px; font-weight: bold; letter-spacing: 0.5px; padding: 5px 10px;")
        self.slider_dash_t1 = QSlider(Qt.Horizontal)
        self.slider_dash_t1.setRange(1100, 1900)
        self.slider_dash_t1.setValue(1500)
        self.slider_dash_t1.setStyleSheet(slider_groove_style)
        self.slider_dash_t1.valueChanged.connect(lambda val: self.on_dash_slider_changed(1, val))
        btn_rst_t1 = QPushButton("1500")
        btn_rst_t1.setStyleSheet(btn_reset_sm_style)
        btn_rst_t1.clicked.connect(lambda: self.reset_dash_thruster(1))
        self.dash_btn_rst_t1 = btn_rst_t1
        t1_row.addWidget(lbl_t1)
        t1_row.addWidget(self.slider_dash_t1, 1)
        t1_row.addWidget(self.lbl_dash_t1_val)
        t1_row.addWidget(self.lbl_dash_t1_badge)
        t1_row.addWidget(btn_rst_t1)
        sliders_box.addLayout(t1_row)
        
        # Thruster 2 (Sway)
        t2_row = QHBoxLayout()
        lbl_t2 = QLabel("T2 (Sway):")
        lbl_t2.setStyleSheet("color: #38BDF8; font-weight: bold; font-size: 12px; min-width: 82px;")
        self.lbl_dash_t2_val = QLabel("1500 µs")
        self.lbl_dash_t2_val.setStyleSheet("color: #E2E8F0; font-family: 'Consolas', monospace; font-size: 11px; font-weight: bold; min-width: 64px;")
        self.lbl_dash_t2_badge = QLabel("NEUTRAL")
        self.lbl_dash_t2_badge.setStyleSheet("background-color: #1E293B; border: 1px solid #334155; border-radius: 8px; color: #94A3B8; font-size: 9px; font-weight: bold; letter-spacing: 0.5px; padding: 5px 10px;")
        self.slider_dash_t2 = QSlider(Qt.Horizontal)
        self.slider_dash_t2.setRange(1100, 1900)
        self.slider_dash_t2.setValue(1500)
        self.slider_dash_t2.setStyleSheet(slider_groove_style)
        self.slider_dash_t2.valueChanged.connect(lambda val: self.on_dash_slider_changed(2, val))
        btn_rst_t2 = QPushButton("1500")
        btn_rst_t2.setStyleSheet(btn_reset_sm_style)
        btn_rst_t2.clicked.connect(lambda: self.reset_dash_thruster(2))
        self.dash_btn_rst_t2 = btn_rst_t2
        t2_row.addWidget(lbl_t2)
        t2_row.addWidget(self.slider_dash_t2, 1)
        t2_row.addWidget(self.lbl_dash_t2_val)
        t2_row.addWidget(self.lbl_dash_t2_badge)
        t2_row.addWidget(btn_rst_t2)
        sliders_box.addLayout(t2_row)
        
        # Thruster 3 (Heave)
        t3_row = QHBoxLayout()
        lbl_t3 = QLabel("T3 (Heave):")
        lbl_t3.setStyleSheet("color: #38BDF8; font-weight: bold; font-size: 12px; min-width: 82px;")
        self.lbl_dash_t3_val = QLabel("1500 µs")
        self.lbl_dash_t3_val.setStyleSheet("color: #E2E8F0; font-family: 'Consolas', monospace; font-size: 11px; font-weight: bold; min-width: 64px;")
        self.lbl_dash_t3_badge = QLabel("NEUTRAL")
        self.lbl_dash_t3_badge.setStyleSheet("background-color: #1E293B; border: 1px solid #334155; border-radius: 8px; color: #94A3B8; font-size: 9px; font-weight: bold; letter-spacing: 0.5px; padding: 5px 10px;")
        self.slider_dash_t3 = QSlider(Qt.Horizontal)
        self.slider_dash_t3.setRange(1100, 1900)
        self.slider_dash_t3.setValue(1500)
        self.slider_dash_t3.setStyleSheet(slider_groove_style)
        self.slider_dash_t3.valueChanged.connect(lambda val: self.on_dash_slider_changed(3, val))
        btn_rst_t3 = QPushButton("1500")
        btn_rst_t3.setStyleSheet(btn_reset_sm_style)
        btn_rst_t3.clicked.connect(lambda: self.reset_dash_thruster(3))
        self.dash_btn_rst_t3 = btn_rst_t3
        t3_row.addWidget(lbl_t3)
        t3_row.addWidget(self.slider_dash_t3, 1)
        t3_row.addWidget(self.lbl_dash_t3_val)
        t3_row.addWidget(self.lbl_dash_t3_badge)
        t3_row.addWidget(btn_rst_t3)
        sliders_box.addLayout(t3_row)
        
        # Keep actuator controls together so Hardware Setup can host them while
        # the dashboard remains a telemetry-only view.
        self.servo_controls_widget = QWidget()
        self.servo_controls_widget.setObjectName("ServoControlsPanel")
        self.servo_controls_widget.setStyleSheet("QWidget#ServoControlsPanel { background-color: #151A21; border: 1px solid #29313B; border-radius: 10px; }")
        servo_controls_layout = QVBoxLayout(self.servo_controls_widget)
        servo_controls_layout.setContentsMargins(0, 4, 0, 0)
        servo_controls_layout.setSpacing(8)
        servo_controls_layout.addLayout(sliders_box)
        
        # Thruster Reset and Emergency Stop Controls
        controls_grid = QHBoxLayout()
        controls_grid.setSpacing(6)
        
        btn_ctrl_style = """
            QPushButton {
                background-color: #1E293B;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 7px 10px;
                color: #E2E8F0;
                font-family: 'Google Sans', sans-serif;
                font-weight: bold;
                font-size: 11px;
                letter-spacing: 0.7px;
            }
            QPushButton:hover {
                background-color: #29313B;
                border-color: #00E5FF;
                color: #00E5FF;
            }
            QPushButton:pressed {
                background-color: #334155;
            }
        """
        
        self.dash_btn_rst_all = QPushButton("ZERO ALL")
        self.dash_btn_rst_all.setStyleSheet(btn_ctrl_style)
        self.dash_btn_rst_all.setMinimumHeight(50)
        self.dash_btn_rst_all.clicked.connect(self.reset_all_dash_thrusters)
        
        self.dash_btn_dock = QPushButton("DOCK")
        self.dash_btn_dock.setStyleSheet("""
            QPushButton {
                background-color: #0284C7;
                border: 1px solid #0369A1;
                border-radius: 6px;
                padding: 7px 10px;
                color: #FFFFFF;
                font-family: 'Google Sans', sans-serif;
                font-weight: bold;
                font-size: 11px;
                letter-spacing: 0.7px;
            }
            QPushButton:hover {
                background-color: #0EA5E9;
                border-color: #38BDF8;
            }
            QPushButton:pressed {
                background-color: #0369A1;
            }
        """)
        self.dash_btn_dock.clicked.connect(self.toggle_manual_docking)
        self.dash_btn_dock.setMinimumHeight(50)

        self.dash_btn_estop = QPushButton("EMERGENCY STOP")
        self.dash_btn_estop.setStyleSheet("""
            QPushButton {
                background-color: #DC2626;
                border: 1px solid #B91C1C;
                border-radius: 6px;
                padding: 7px 10px;
                color: #FFFFFF;
                font-family: 'Google Sans', sans-serif;
                font-weight: bold;
                font-size: 11px;
                letter-spacing: 1px;
            }
            QPushButton:hover {
                background-color: #EF4444;
                border-color: #DC2626;
            }
            QPushButton:pressed {
                background-color: #991B1B;
            }
        """)
        self.dash_btn_estop.clicked.connect(self.master_emergency_stop)
        self.dash_btn_estop.setMinimumHeight(50)
        
        controls_grid.addWidget(self.dash_btn_rst_all)
        controls_grid.addWidget(self.dash_btn_dock)
        controls_grid.addWidget(self.dash_btn_estop)
        servo_controls_layout.addLayout(controls_grid)
        actuators_layout.addWidget(self.servo_controls_widget)
        
        self.tile_actuators.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        col_right.addWidget(self.tile_actuators, 0)
        
        # ==========================================
        # TILE 6: THRUSTER PWM CHANNELS (Dedicated 6th Tile)
        # ==========================================
        self.tile_thrusters = ExcavatableTile("thrusters", "THRUSTER PWM CHANNELS", accent=AC_ACT)
        thrusters_layout = self.tile_thrusters.content_layout
        
        thrusters_visuals = QHBoxLayout()
        thrusters_visuals.setSpacing(8)
        
        self.actuators_gauge = ActuatorsGauge(theme="light")
        self.actuators_gauge.setMinimumSize(130, 220)
        self.actuators_gauge.setMaximumSize(170, 300)
        self.actuators_chart = RealTimeChart("THRUSTER PWM CHANNELS", 1000.0, 2000.0, "us", theme="light")
        self.actuators_chart.add_series("T1", "#F59E0B") # Yellow / Amber
        self.actuators_chart.add_series("T2", "#10B981") # Green
        self.actuators_chart.add_series("T3", "#8B5CF6") # Violet / Purple
        self.actuators_chart.setMinimumHeight(140)
        
        thrusters_visuals.addWidget(self.actuators_gauge, 0, Qt.AlignVCenter)
        thrusters_visuals.addWidget(self.actuators_chart, 1)
        thrusters_layout.addLayout(thrusters_visuals)
        
        col_right.addWidget(self.tile_thrusters, 1)

        # Keep chamber readings directly beneath the thruster PWM visualizer.
        self.env_chart.setMinimumHeight(140)
        col_right.addWidget(self.tile_env, 2)
        
        # Registry of all 6 excavatable dashboard tiles
        self.dash_tiles = {
            "ahrs": self.tile_ahrs,
            "env": self.tile_env,
            "gps": self.tile_gps,
            "power": self.tile_power,
            "actuators": self.tile_actuators,
            "thrusters": self.tile_thrusters
        }
        
        # Assemble dashboard columns (1:1:1 balanced distribution)
        dashboard_layout.addLayout(col_left, 1)
        dashboard_layout.addLayout(col_center, 1)
        dashboard_layout.addLayout(col_right, 1)
        
        dashboard_scroll.setWidget(dashboard_container)
        page_dash_layout = QVBoxLayout(page_dashboard)
        page_dash_layout.setContentsMargins(0, 0, 0, 0)
        page_dash_layout.addWidget(dashboard_scroll)
        
        # Register Stacked Pages
        self.stacked_widget.addWidget(page_dashboard)                      # 0: Dashboard
        self.stacked_widget.addWidget(self.create_earth_page())            # 1: Earth Navigation Map
        self.stacked_widget.addWidget(self.create_plan_page())            # 2: Plan
        self.stacked_widget.addWidget(self.create_setup_page())           # 3: Setup
        self.stacked_widget.addWidget(self.create_docking_page())         # 4: Docking Data
        self.stacked_widget.addWidget(self.create_settings_page())        # 5: Settings
        self.stacked_widget.addWidget(self.create_about_page())           # 6: About
        self.stacked_widget.addWidget(self.create_docking_visual_page())  # 7: Docking Visual View
        self.stacked_widget.addWidget(self.create_camera_detection_page())  # 8: Camera Detection
        self.stacked_widget.addWidget(self.create_sensor_fusion_page())     # 9: Sensor Fusion
        
        
        # Default select Earth Navigation Map (index 1: Earth Map) to match active sidebar button
        self.stacked_widget.setCurrentIndex(1)
        if hasattr(self, 'sidebar') and self.sidebar:
            self.sidebar.select_page(1)
        
        # Apply initial theme across views on startup
        if hasattr(self, 'apply_theme'):
            self.apply_theme(self.current_theme)
        
        main_layout.addWidget(body_frame, 1) # Expand body_frame to occupy space
        
        # Populate Serial Ports on load
        self.refresh_ports()
        
        # Create a timer to periodically update port listings when not connected
        self.port_refresh_timer = QTimer(self)
        self.port_refresh_timer.timeout.connect(self.refresh_ports)
        self.port_refresh_timer.start(3000) # Every 3 seconds

    def _configure_map_webview(self, view):
        """Apply conservative WebEngine settings required by the local Leaflet maps."""
        try:
            settings = view.settings()
            settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        except Exception as exc:
            print(f"[MAP] WebEngine settings warning: {exc}")

    def _dock_projection_length(self):
        """Read the UI's persisted dock projection length from one stable path."""
        try:
            from src.pages.setup_page import get_autopilot_config_path
            import json
            cfg_path = get_autopilot_config_path()
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as fh:
                    return max(3.0, min(50.0, float(json.load(fh).get("DOCK_LINE_LENGTH", 10.0))))
        except Exception as exc:
            print(f"[MAP] Dock-line config warning: {exc}")
        return 10.0

    def _on_map_loaded(self, which, ok):
        """Verify Leaflet itself initialized before enabling telemetry rendering."""
        if which == "earth":
            self._earth_map_ready = False
            view = getattr(self, "web_view", None)
        else:
            self._plan_map_ready = False
            view = getattr(self, "plan_web_view", None)
        if not ok or view is None:
            print(f"[MAP] {which} map failed to load local HTML")
            return

        def finish_ready(js_ready):
            ready = bool(js_ready)
            if which == "earth":
                self._earth_map_ready = ready
            else:
                self._plan_map_ready = ready
            if not ready:
                view.page().runJavaScript(
                    "String(window.__leafletMapError || 'Leaflet initialization failed')",
                    lambda err: print(f"[MAP] {which} initialization error: {err}")
                )
                return
            dock_len = self._dock_projection_length()
            basemap = str(self.docking_settings.value("map/basemap", "satellite")).lower()
            if basemap not in ("satellite", "street"):
                basemap = "satellite"
            js = (
                f"if (typeof setVesselIcon === 'function') setVesselIcon('{self.vessel_icon_type}');"
                f"if (typeof setBaseMap === 'function') setBaseMap('{basemap}');"
                f"if (typeof setDockingStation === 'function') setDockingStation({self.docking_station_latitude:.7f},"
                f"{self.docking_station_longitude:.7f},{self.docking_station_heading:.3f},{dock_len:.3f});"
                "if (typeof refreshMapSize === 'function') refreshMapSize();"
            )
            view.page().runJavaScript(js)
            # Push the latest GPS immediately after a page reload instead of
            # waiting for the next 200 ms render tick.
            self._flush_map_render()

        view.page().runJavaScript("Boolean(window.__leafletMapReady)", finish_ready)

    def _flush_map_render(self):
        """Render only the most recent GPS fix to the currently visible map."""
        fix = self._latest_map_fix
        if fix is None or not hasattr(self, "stacked_widget"):
            return
        lat, lon, yaw = fix
        page_index = self.stacked_widget.currentIndex()
        if page_index == 1:
            view = getattr(self, "web_view", None)
            ready = getattr(self, "_earth_map_ready", False)
        elif page_index == 2:
            view = getattr(self, "plan_web_view", None)
            ready = getattr(self, "_plan_map_ready", False)
        else:
            return
        if view is None or not ready:
            return
        view.page().runJavaScript(
            f"if (typeof updatePosition === 'function') updatePosition({lat:.7f},{lon:.7f},{yaw:.3f});"
        )

    def _legacy_create_earth_page_unused(self):
        page = QWidget()
        
        # Grid layout allowing multiple widgets to occupy the same cell (0, 0)
        grid_layout = QGridLayout(page)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        
        # 1. Fullscreen Web View Map
        self.web_view = QWebEngineView()
        self.web_page = ConsoleWebEnginePage(self.web_view)
        self.web_view.setPage(self.web_page)
        
        # Load the bundled map as a real local file.  This avoids the fragile
        # data-URL path used by setHtml() and gives Qt WebEngine a stable origin
        # for cache/network access to remote basemap tiles.
        html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "map.html")
        try:
            self._configure_map_webview(self.web_view)
            self.web_view.loadFinished.connect(lambda ok: self._on_map_loaded("earth", ok))
            self.web_view.load(QUrl.fromLocalFile(os.path.abspath(html_path)))
        except Exception as e:
            print(f"Error loading map.html: {e}")
            
        grid_layout.addWidget(self.web_view, 0, 0)
        
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
        self.map_horizon = MarineHorizon(theme="cockpit")
        self.map_horizon.setFixedSize(190, 190)
        
        # Compass Indicator (Enlarged to 190x190 for ~5cm diameter)
        self.map_compass = MarineCompass(theme="cockpit")
        self.map_compass.setFixedSize(190, 190)
        
        overlay_layout.addWidget(self.map_horizon)
        overlay_layout.addWidget(self.map_compass)
        
        # Align overlay panel to the Top Right corner of the map grid cell
        grid_layout.addWidget(overlay_panel, 0, 0, Qt.AlignTop | Qt.AlignRight)
        
        return page

    def _legacy_create_plan_page_unused(self):
        page = QWidget()
        main_layout = QHBoxLayout(page)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Left container split area
        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        
        # Create Vertical Splitter for Map and Bottom Drawer
        self.plan_splitter = QSplitter(Qt.Vertical)
        self.plan_splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #333333;
                height: 4px;
            }
            QSplitter::handle:hover {
                background-color: #FF9100;
            }
        """)
        
        # Web view map
        self.plan_web_view = QWebEngineView()
        self.plan_web_view.setPage(ConsoleWebEnginePage(self.plan_web_view, callback=self.handle_plan_console))
        
        # Load from disk for reliable remote tile access and WebEngine caching.
        html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plan_map.html")
        try:
            self._configure_map_webview(self.plan_web_view)
            self.plan_web_view.loadFinished.connect(lambda ok: self._on_map_loaded("plan", ok))
            self.plan_web_view.load(QUrl.fromLocalFile(os.path.abspath(html_path)))
        except Exception as e:
            print(f"Error loading plan_map.html: {e}")
            
        self.plan_splitter.addWidget(self.plan_web_view)
        
        # 2. Control Panel (Docked on Right Side - fixed width)
        control_panel = QFrame()
        control_panel.setObjectName("PlanControlPanel")
        control_panel.setFixedWidth(280)
        control_panel.setStyleSheet("""
            QFrame#PlanControlPanel {
                background-color: #121212;
                border-left: 1px solid #333333;
                border-right: none;
                border-top: none;
                border-bottom: none;
                border-radius: 0px;
                padding: 10px;
            }
            QLabel {
                color: #A0A0A0;
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 1px;
                text-transform: uppercase;
                border: none;
                background: transparent;
            }
            QPushButton {
                background-color: #1A1A1A;
                border: 1px solid #333333;
                border-radius: 4px;
                color: #A0A0A0;
                padding: 8px 12px;
                font-weight: bold;
                font-size: 10px;
            }
            QPushButton:hover {
                background-color: #242424;
                border-color: #FF9100;
                color: #FFFFFF;
            }
            QPushButton[checked="true"] {
                background-color: #FF9100;
                border-color: #FF9100;
                color: #FFFFFF;
            }
        """)
        
        panel_layout = QVBoxLayout(control_panel)
        panel_layout.setContentsMargins(15, 15, 15, 15)
        panel_layout.setSpacing(15)
        
        # Mode selector header label
        mode_label = QLabel("VEHICLE NAVIGATION MODE")
        panel_layout.addWidget(mode_label)
        
        # Horizontal layout for Manual / Automatic buttons
        mode_buttons_layout = QHBoxLayout()
        mode_buttons_layout.setSpacing(6)
        
        self.btn_manual = QPushButton("MANUAL")
        self.btn_manual.setCheckable(True)
        self.btn_manual.setChecked(True)
        self.btn_manual.setProperty("checked", "true")
        
        self.btn_auto = QPushButton("AUTOMATIC")
        self.btn_auto.setCheckable(True)
        
        # Button groups/exclusive toggle connections
        self.btn_manual.clicked.connect(lambda: self.set_navigation_mode("manual"))
        self.btn_auto.clicked.connect(lambda: self.set_navigation_mode("automatic"))
        
        mode_buttons_layout.addWidget(self.btn_manual)
        mode_buttons_layout.addWidget(self.btn_auto)
        panel_layout.addLayout(mode_buttons_layout)

        # Active Stabilization is global: in AUTO it strengthens straight-line
        # waypoint tracking; with the route stopped it becomes GPS station hold.
        stab_hdr = QLabel("ACTIVE STABILIZATION")
        stab_hdr.setStyleSheet("color: #AAAAAA; font-weight: bold; font-size: 10px; margin-top: 4px;")
        panel_layout.addWidget(stab_hdr)

        self.btn_active_stabilization = QPushButton("ACTIVE STABILIZATION: OFF")
        self.btn_active_stabilization.setCheckable(True)
        self.btn_active_stabilization.setMinimumHeight(42)
        self.btn_active_stabilization.clicked.connect(self.toggle_active_stabilization)
        panel_layout.addWidget(self.btn_active_stabilization)

        self.lbl_active_stabilization_status = QLabel("OFF • current rejection disabled")
        self.lbl_active_stabilization_status.setWordWrap(True)
        self.lbl_active_stabilization_status.setStyleSheet(
            "color: #78909C; font-size: 9px; font-weight: bold; margin-bottom: 4px;"
        )
        panel_layout.addWidget(self.lbl_active_stabilization_status)
        self.update_active_stabilization_ui_state(False, "OFF")
        
        # Automatic Mode Actions Widget (visible only in Auto mode)
        self.auto_actions_widget = QWidget()
        self.auto_actions_widget.setStyleSheet("border: none; background: transparent; padding: 0px;")
        actions_layout = QVBoxLayout(self.auto_actions_widget)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(12)
        
        blue_btn_style = """
            QPushButton {
                background-color: #0F223C;
                border: 1px solid #1C3B65;
                border-radius: 4px;
                padding: 8px 12px;
                color: #E2F1FF;
                font-family: 'Google Sans', sans-serif;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #1C3B65;
                border-color: #2D5A8F;
                color: #FFFFFF;
            }
            QPushButton:pressed {
                background-color: #0A1625;
            }
        """
        
        wp_hdr = QLabel("WAYPOINT ROUTING", self.auto_actions_widget)
        wp_hdr.setStyleSheet("color: #AAAAAA; font-weight: bold; font-size: 10px; margin-bottom: 2px;")
        actions_layout.addWidget(wp_hdr)
        
        self.btn_load_wp = QPushButton("LOAD MISSION", self.auto_actions_widget)
        self.btn_load_wp.setStyleSheet(blue_btn_style)
        self.btn_load_wp.clicked.connect(self.load_mission_file)
        actions_layout.addWidget(self.btn_load_wp)
        
        self.btn_save_wp = QPushButton("SAVE MISSION", self.auto_actions_widget)
        self.btn_save_wp.setStyleSheet(blue_btn_style)
        self.btn_save_wp.clicked.connect(self.save_mission_file)
        actions_layout.addWidget(self.btn_save_wp)
        
        self.btn_send_wp = QPushButton("SEND WAYPOINTS", self.auto_actions_widget)
        self.btn_send_wp.setStyleSheet(blue_btn_style)
        self.btn_send_wp.clicked.connect(self.upload_planned_route)
        actions_layout.addWidget(self.btn_send_wp)
        
        self.btn_clear_wp = QPushButton("CLEAR WAYPOINTS", self.auto_actions_widget)
        self.btn_clear_wp.setStyleSheet(blue_btn_style)
        self.btn_clear_wp.clicked.connect(self.clear_planned_route)
        actions_layout.addWidget(self.btn_clear_wp)
        
        ctrl_hdr = QLabel("MISSION CONTROL", self.auto_actions_widget)
        ctrl_hdr.setStyleSheet("color: #AAAAAA; font-weight: bold; font-size: 10px; margin-bottom: 2px;")
        actions_layout.addWidget(ctrl_hdr)
        
        self.btn_arm = QPushButton("ARM VEHICLE", self.auto_actions_widget)
        self.btn_arm.setStyleSheet(blue_btn_style)
        self.btn_arm.setEnabled(False) # Disabled until waypoints uploaded & ACK received
        self.btn_arm.clicked.connect(self.arm_vehicle)
        actions_layout.addWidget(self.btn_arm)
        
        self.btn_disarm = QPushButton("DISARM", self.auto_actions_widget)
        self.btn_disarm.setStyleSheet(blue_btn_style)
        self.btn_disarm.clicked.connect(self.disarm_vehicle)
        actions_layout.addWidget(self.btn_disarm)
        
        self.btn_start = QPushButton("START ROUTE", self.auto_actions_widget)
        self.btn_start.setStyleSheet(blue_btn_style)
        self.btn_start.clicked.connect(self.start_mission)
        actions_layout.addWidget(self.btn_start)
        
        self.btn_stop = QPushButton("STOP ROUTE", self.auto_actions_widget)
        self.btn_stop.setStyleSheet(blue_btn_style)
        self.btn_stop.clicked.connect(self.stop_mission)
        actions_layout.addWidget(self.btn_stop)
 
        self.btn_rth = QPushButton("RETURN TO HOME", self.auto_actions_widget)
        self.btn_rth.setStyleSheet(blue_btn_style)
        self.btn_rth.clicked.connect(self.return_to_home)
        actions_layout.addWidget(self.btn_rth)
        

        
        self.blue_btn_style = blue_btn_style
 
        panel_layout.addWidget(self.auto_actions_widget)
        self.auto_actions_widget.setVisible(False) # Default to manual mode
        
        # Manual Mode Actions Widget (visible only in Manual mode)
        self.manual_actions_widget = QWidget()
        self.manual_actions_widget.setStyleSheet("border: none; background: transparent; padding: 0px;")
        manual_layout = QVBoxLayout(self.manual_actions_widget)
        manual_layout.setContentsMargins(0, 10, 0, 0)
        manual_layout.setSpacing(8)
        
        joy_status_title = QLabel("JOYSTICK STATUS")
        joy_status_title.setStyleSheet("color: #AAAAAA; font-weight: bold; font-size: 10px; margin-bottom: 2px;")
        manual_layout.addWidget(joy_status_title)
        
        self.lbl_plan_joy_status = QLabel("DISCONNECTED")
        self.lbl_plan_joy_status.setStyleSheet("color: #EF5350; font-weight: bold; font-size: 11px; margin-bottom: 10px;")
        manual_layout.addWidget(self.lbl_plan_joy_status)
        
        # Add ARM/DISARM control buttons for Manual Mode
        ctrl_hdr_manual = QLabel("MISSION CONTROL", self.manual_actions_widget)
        ctrl_hdr_manual.setStyleSheet("color: #AAAAAA; font-weight: bold; font-size: 10px; margin-bottom: 2px;")
        manual_layout.addWidget(ctrl_hdr_manual)
        
        self.btn_manual_arm = QPushButton("ARM VEHICLE", self.manual_actions_widget)
        self.btn_manual_arm.setStyleSheet(blue_btn_style)
        self.btn_manual_arm.clicked.connect(self.arm_vehicle)
        manual_layout.addWidget(self.btn_manual_arm)
        
        self.btn_manual_disarm = QPushButton("DISARM", self.manual_actions_widget)
        self.btn_manual_disarm.setStyleSheet(blue_btn_style)
        self.btn_manual_disarm.clicked.connect(self.disarm_vehicle)
        manual_layout.addWidget(self.btn_manual_disarm)

        self.btn_manual_dock = QPushButton("DOCK", self.manual_actions_widget)
        self.btn_manual_dock.setStyleSheet("""
            QPushButton {
                background-color: #0284C7;
                border: 1px solid #0369A1;
                border-radius: 4px;
                padding: 8px 12px;
                color: #FFFFFF;
                font-family: 'Google Sans', sans-serif;
                font-weight: bold;
                font-size: 11px;
                margin-top: 4px;
            }
            QPushButton:hover {
                background-color: #0EA5E9;
                border-color: #38BDF8;
            }
            QPushButton:pressed {
                background-color: #0369A1;
            }
        """)
        self.btn_manual_dock.clicked.connect(self.toggle_manual_docking)
        manual_layout.addWidget(self.btn_manual_dock)
        
        panel_layout.addWidget(self.manual_actions_widget)
        panel_layout.addStretch()
        
        # Widget 2: Bottom Panel (resizes/drags vertically in splitter)
        self.bottom_panel = QFrame()
        self.bottom_panel.setObjectName("BottomPanel")
        self.bottom_panel.setMinimumHeight(180)
        self.bottom_panel.setMaximumHeight(400)
        self.bottom_panel.setStyleSheet("""
            QFrame#BottomPanel {
                background-color: #121212;
                border-top: 1px solid #333333;
                border-bottom: none;
                border-left: none;
                border-right: none;
                border-radius: 0px;
                padding: 10px;
            }
            QTableWidget {
                background-color: transparent;
                border: none;
                color: #FFFFFF;
                font-size: 10px;
            }
            QHeaderView::section {
                background-color: transparent;
                color: #A0A0A0;
                padding: 6px;
                border: none;
                font-weight: bold;
                font-size: 9px;
            }
        """)
        
        bottom_layout = QHBoxLayout(self.bottom_panel)
        bottom_layout.setContentsMargins(15, 10, 15, 10)
        bottom_layout.setSpacing(0)
        
        # Table of waypoints (9 columns matching the image)
        self.wp_table = QTableWidget(0, 9)
        self.wp_table.setHorizontalHeaderLabels(["WP", "LATITUDE", "LONGITUDE", "DIST (m)", "STATUS", "DEL", "UP", "DN", "OFFSET"])
        self.wp_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.wp_table.setShowGrid(False)
        
        bottom_layout.addWidget(self.wp_table, 1)
        
        # Add bottom panel to splitter
        self.plan_splitter.addWidget(self.bottom_panel)
        
        # Set splitter sizes
        self.plan_splitter.setSizes([500, 200])
        
        # Assemble main layouts
        left_layout.addWidget(self.plan_splitter)
        main_layout.addWidget(left_container, 1)
        main_layout.addWidget(control_panel)
        
        # Default hidden on startup
        self.bottom_panel.setVisible(False)
        
        # Explicitly initialize to manual mode state on startup once all widgets exist
        self.set_navigation_mode("manual")
        
        return page

    def _legacy_create_setup_page_unused(self):
        from PySide6.QtWidgets import QProgressBar, QStackedWidget, QLineEdit, QSplitter
        import os
        
        # Resolve absolute paths for setup page assets
        src_dir = os.path.dirname(os.path.abspath(__file__))
        down_arrow_path = os.path.join(src_dir, "down_arrow.png").replace("\\", "/")
        check_path = os.path.join(src_dir, "check.png").replace("\\", "/")
        
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Horizontal Splitter for Setup Menu and Content Panel
        self.setup_splitter = QSplitter(Qt.Horizontal)
        self.setup_splitter.setStyleSheet("""
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
        self.btn_menu_joystick = QPushButton("JOYSTICK")
        self.btn_menu_joystick.setProperty("active", "true")
        menu_layout.addWidget(self.btn_menu_joystick)
        
        # Light menu option button
        self.btn_menu_light = QPushButton("LIGHT")
        self.btn_menu_light.setProperty("active", "false")
        menu_layout.addWidget(self.btn_menu_light)
        
        menu_layout.addStretch()
        self.setup_splitter.addWidget(left_menu)
        
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
        self.setup_breadcrumb = QLabel("HARDWARE CONFIGURATION > JOYSTICK")
        self.setup_breadcrumb.setStyleSheet("""
            color: #FFFFFF;
            background-color: #161616;
            border-bottom: 1px solid #2d2d2d;
            font-family: 'Google Sans', sans-serif;
            font-size: 11px;
            font-weight: bold;
            padding: 15px 30px;
            letter-spacing: 0.5px;
        """)
        right_container_layout.addWidget(self.setup_breadcrumb)
        
        # Stacked widget to display selected option
        self.setup_stacked = QStackedWidget()
        right_container_layout.addWidget(self.setup_stacked, 1)
        
        self.setup_splitter.addWidget(right_container)
        self.setup_splitter.setSizes([180, 800])
        layout.addWidget(self.setup_splitter, 1)
            
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
        
        self.combo_joystick = QComboBox()
        self.combo_joystick.setMinimumWidth(180)
        self.combo_joystick.setStyleSheet(f"""
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
        
        self.btn_joystick_enable = QCheckBox("Enable")
        self.btn_joystick_enable.setStyleSheet(f"""
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
        self.btn_joystick_enable.clicked.connect(self.toggle_joystick_state)
        
        self.btn_joystick_save = QPushButton("Save")
        self.btn_joystick_save.setStyleSheet("""
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
        self.btn_joystick_save.clicked.connect(self.save_joystick_config)
        
        self.lbl_config_status = QLabel("Loaded Config for ArduSub")
        self.lbl_config_status.setStyleSheet("color: #FFFFFF; font-family: 'Google Sans', sans-serif; font-size: 11px; font-weight: bold; margin-left: 10px;")
        
        top_row.addWidget(lbl_joy_title)
        top_row.addWidget(self.combo_joystick)
        top_row.addWidget(self.btn_joystick_enable)
        top_row.addWidget(self.btn_joystick_save)
        top_row.addWidget(self.lbl_config_status)
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
        self.rc_axis_combos = {}
        self.rc_auto_detect_btns = {}
        self.rc_output_fields = {}
        self.rc_reverse_chks = {}
        
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
            self.rc_axis_combos[rc_key] = combo
            
            btn_detect = QPushButton("Auto Detect")
            btn_detect.setStyleSheet(btn_detect_style)
            btn_detect.clicked.connect(lambda checked=False, r=rc_key: self.start_auto_detect_axis(r))
            grid_layout.addWidget(btn_detect, row_idx, 2)
            self.rc_auto_detect_btns[rc_key] = btn_detect
            
            lbl_output = QLabel("1500")
            lbl_output.setAlignment(Qt.AlignCenter)
            lbl_output.setStyleSheet(output_style)
            grid_layout.addWidget(lbl_output, row_idx, 3)
            self.rc_output_fields[rc_key] = lbl_output
            
            chk_rev = QCheckBox()
            chk_rev.setStyleSheet(chk_style)
            grid_layout.addWidget(chk_rev, row_idx, 4, Qt.AlignCenter)
            self.rc_reverse_chks[rc_key] = chk_rev
            
        # Populate Buttons Mappings (ARM, DISARM, START, STOP, RTH)
        self.btn_mapping_combos = {}
        self.btn_auto_detect_btns = {}
        self.btn_output_fields = {}
        
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
            self.btn_mapping_combos[btn_key] = combo
            
            btn_detect = QPushButton("Auto Detect")
            btn_detect.setStyleSheet(btn_detect_style)
            btn_detect.clicked.connect(lambda checked=False, b=btn_key: self.start_auto_detect_button(b))
            grid_layout.addWidget(btn_detect, row_idx, 2)
            self.btn_auto_detect_btns[btn_key] = btn_detect
            
            lbl_output = QLabel("0")
            lbl_output.setAlignment(Qt.AlignCenter)
            lbl_output.setStyleSheet(output_style)
            grid_layout.addWidget(lbl_output, row_idx, 3)
            self.btn_output_fields[btn_key] = lbl_output
            
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
        self.input_thruster_min = QLineEdit(str(self.thruster_min_limit))
        self.input_thruster_min.setStyleSheet("QLineEdit { background-color: #121212; border: 1px solid #333333; border-radius: 2px; color: #FFFFFF; padding: 5px; font-family: 'Google Sans', sans-serif; font-size: 11px; }")
        
        thruster_grid.addWidget(lbl_min, 2, 0)
        thruster_grid.addWidget(self.input_thruster_min, 2, 1)
        
        lbl_max = QLabel("Maximum PWM Speed Limit (us):")
        lbl_max.setStyleSheet("color: #FFFFFF; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        self.input_thruster_max = QLineEdit(str(self.thruster_max_limit))
        self.input_thruster_max.setStyleSheet("QLineEdit { background-color: #121212; border: 1px solid #333333; border-radius: 2px; color: #FFFFFF; padding: 5px; font-family: 'Google Sans', sans-serif; font-size: 11px; }")
        
        thruster_grid.addWidget(lbl_max, 3, 0)
        thruster_grid.addWidget(self.input_thruster_max, 3, 1)
        
        self.btn_thruster_set = QPushButton("Set Limits")
        self.btn_thruster_set.setStyleSheet("""
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
        self.btn_thruster_set.clicked.connect(self.set_thruster_limits)
        thruster_grid.addWidget(self.btn_thruster_set, 4, 1, Qt.AlignRight)
        
        self.lbl_thruster_feedback = QLabel("")
        self.lbl_thruster_feedback.setStyleSheet("color: #8BC34A; font-family: 'Google Sans', sans-serif; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        thruster_grid.addWidget(self.lbl_thruster_feedback, 4, 0)
        
        scroll_layout.addWidget(thruster_widget)
        
        scroll_layout.addStretch()
        
        scroll.setWidget(scroll_content)
        joystick_layout.addWidget(scroll)
        
        # Create Light Page
        light_page = QWidget()
        light_page_layout = QVBoxLayout(light_page)
        light_page_layout.setContentsMargins(15, 15, 15, 15)
        light_page_layout.setSpacing(15)
        
        # Equipment Controls Panel (LIGHT & CAMERA toggles)
        payload_widget = QWidget()
        payload_widget.setStyleSheet("background-color: #1A1A1A; border: 1px solid #333333; border-radius: 4px; padding: 15px;")
        payload_layout = QVBoxLayout(payload_widget)
        payload_layout.setSpacing(10)
        
        lbl_payload_title = QLabel("Equipment Control")
        lbl_payload_title.setStyleSheet("color: #00E5FF; font-weight: bold; font-family: 'Google Sans', sans-serif; font-size: 11px; border: none; background: transparent; text-transform: uppercase;")
        payload_layout.addWidget(lbl_payload_title)
        
        buttons_row = QHBoxLayout()
        buttons_row.setSpacing(15)
        
        self.btn_light_toggle = QPushButton("LIGHT: OFF")
        self.btn_light_toggle.setStyleSheet(self.blue_btn_style)
        self.btn_light_toggle.setCheckable(True)
        self.btn_light_toggle.clicked.connect(self.toggle_light)
        buttons_row.addWidget(self.btn_light_toggle)
        
        self.btn_camera_toggle = QPushButton("CAMERA: OFF")
        self.btn_camera_toggle.setStyleSheet(self.blue_btn_style)
        self.btn_camera_toggle.setCheckable(True)
        self.btn_camera_toggle.clicked.connect(self.toggle_camera)
        buttons_row.addWidget(self.btn_camera_toggle)
        buttons_row.addStretch()
        
        payload_layout.addLayout(buttons_row)
        light_page_layout.addWidget(payload_widget)
        light_page_layout.addStretch()

        # Instantiate original members as hidden so other logic works
        self.bar_throttle = QProgressBar()
        self.bar_steering = QProgressBar()
        self.bar_pitch = QProgressBar()
        
        self.load_joystick_config()
        
        # Add pages to stacked widget
        self.setup_stacked.addWidget(joystick_page)
        self.setup_stacked.addWidget(light_page)
        self.setup_stacked.setCurrentIndex(0)
        
        # Connect menu click slots
        self.btn_menu_joystick.clicked.connect(self.select_joystick_setup)
        self.btn_menu_light.clicked.connect(self.select_light_setup)
        
        return page
        
    def select_joystick_setup(self):
        self.btn_menu_joystick.setProperty("active", "true")
        self.btn_menu_joystick.style().unpolish(self.btn_menu_joystick)
        self.btn_menu_joystick.style().polish(self.btn_menu_joystick)
        
        self.btn_menu_light.setProperty("active", "false")
        self.btn_menu_light.style().unpolish(self.btn_menu_light)
        self.btn_menu_light.style().polish(self.btn_menu_light)
        
        self.setup_stacked.setCurrentIndex(0)
        self.setup_breadcrumb.setText("HARDWARE CONFIGURATION > JOYSTICK")

    def select_light_setup(self):
        self.btn_menu_joystick.setProperty("active", "false")
        self.btn_menu_joystick.style().unpolish(self.btn_menu_joystick)
        self.btn_menu_joystick.style().polish(self.btn_menu_joystick)
        
        self.btn_menu_light.setProperty("active", "true")
        self.btn_menu_light.style().unpolish(self.btn_menu_light)
        self.btn_menu_light.style().polish(self.btn_menu_light)
        
        self.setup_stacked.setCurrentIndex(1)
        self.setup_breadcrumb.setText("HARDWARE CONFIGURATION > LIGHT")

    def set_thruster_limits(self):
        try:
            min_val = int(self.input_thruster_min.text().strip())
            max_val = int(self.input_thruster_max.text().strip())
            
            # Enforce hardware safety boundaries
            if min_val < 1000 or min_val > 1500 or max_val < 1500 or max_val > 2000:
                self.lbl_thruster_feedback.setStyleSheet("color: #FF1744; font-family: 'Google Sans', sans-serif; font-size: 11px; font-weight: bold; border: none; background: transparent;")
                self.lbl_thruster_feedback.setText("Limits must satisfy 1000 <= min <= 1500 <= max <= 2000")
                return
                
            self.thruster_min_limit = min_val
            self.thruster_max_limit = max_val
            
            # Save limits to configuration
            self.save_joystick_config()
            
            # Transmit speed limits to backend
            if self.telemetry_thread and self.telemetry_thread.isRunning():
                payload = f"$LIMIT,{min_val},{max_val}"
                self.telemetry_thread.write_data(payload)
                print(f"[Mission Control] Sent speed limits to backend: {payload}")
                
            self.lbl_thruster_feedback.setStyleSheet("color: #8BC34A; font-family: 'Google Sans', sans-serif; font-size: 11px; font-weight: bold; border: none; background: transparent;")
            self.lbl_thruster_feedback.setText("Limits applied and saved successfully!")
            QTimer.singleShot(3000, lambda: self.lbl_thruster_feedback.setText(""))
        except ValueError:
            self.lbl_thruster_feedback.setStyleSheet("color: #FF1744; font-family: 'Google Sans', sans-serif; font-size: 11px; font-weight: bold; border: none; background: transparent;")
            self.lbl_thruster_feedback.setText("Invalid integers entered.")



    def flash_warning_banner(self):
        self.warning_flash_state = not self.warning_flash_state
        has_critical = (getattr(self, 'batt_state', 'normal') == 'critical' or getattr(self, 'temp_state', 'normal') == 'critical')
        
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
            
        if self.warning_flash_state:
            self.warning_banner_frame.setStyleSheet(f"""
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
            self.warning_banner_frame.setStyleSheet(f"""
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

    def create_earth_page(self):
        from src.pages.earth_page import create_earth_page
        return create_earth_page(self)

    def create_plan_page(self):
        from src.pages.plan_page import create_plan_page
        return create_plan_page(self)

    def create_setup_page(self):
        from src.pages.setup_page import create_setup_page
        return create_setup_page(self)

    def create_docking_page(self):
        from src.pages.docking_page import create_docking_page
        return create_docking_page(self)

    def create_docking_visual_page(self):
        from src.pages.docking_visual_page import create_docking_visual_page
        return create_docking_visual_page(self)

    def create_settings_page(self):
        from src.pages.settings_page import create_settings_page
        return create_settings_page(self)

    def create_camera_detection_page(self):
        from src.pages.camera_detection_page import create_camera_detection_page
        return create_camera_detection_page(self)

    def create_sensor_fusion_page(self):
        from src.pages.sensor_fusion_page import create_sensor_fusion_page
        return create_sensor_fusion_page(self)

    def apply_dashboard_theme(self, theme):
        if theme in ("cockpit", "dark"):
            dash_bg = "#121214"
            dial_label_color = "#A0A0A0"
            val_color = "#FFFFFF"
            badge_bg = "#26262D"
            badge_fg = "#A0A0A0"
            badge_border = "#3A3A42"
            slider_subpage = "#FF9100"
            slider_groove = "#2A2A30"
            btn_bg = "#26262B"
            btn_border = "#3A3A42"
            btn_text = "#E0E0E0"
            btn_hover = "#303037"
        elif theme == "ocean":
            dash_bg = "#050B14"
            dial_label_color = "#8EB7E6"
            val_color = "#E2F1FF"
            badge_bg = "#0F223C"
            badge_fg = "#8EB7E6"
            badge_border = "#1C3B65"
            slider_subpage = "#00E5FF"
            slider_groove = "#1C3B65"
            btn_bg = "#0F223C"
            btn_border = "#1C3B65"
            btn_text = "#E2F1FF"
            btn_hover = "#152F53"
        else: # light
            dash_bg = "#F4F7FB"
            dial_label_color = "#5A738E"
            val_color = "#0A2540"
            badge_bg = "#F1F5F9"
            badge_fg = "#64748B"
            badge_border = "#CBD5E1"
            slider_subpage = "#0284C7"
            slider_groove = "#F1F5F9"
            btn_bg = "#FFFFFF"
            btn_border = "#CBD5E1"
            btn_text = "#334155"
            btn_hover = "#F8FAFC"

        # Keep all three servo channels on the same blue accent in every
        # dashboard theme instead of assigning a different color per axis.
        slider_subpage = "#0284C7"

        # 1. Update Dashboard background
        if hasattr(self, 'dashboard_container') and self.dashboard_container:
            self.dashboard_container.setStyleSheet(f"background-color: {dash_bg};")

        # 2. AHRS dial captions sit on the dark instrument deck, so they keep
        #    their own light-on-dark styling regardless of the active theme.
        for lbl_attr in ('lbl_dash_d1', 'lbl_dash_d2', 'lbl_dash_d3'):
            lbl = getattr(self, lbl_attr, None)
            if lbl:
                lbl.setStyleSheet("color: #94A9C4; font-size: 8px; font-weight: bold;"
                                  " letter-spacing: 1.2px; border: none; background: transparent;")
        if hasattr(self, 'ahrs_deck') and self.ahrs_deck:
            deck_bg = "#0E1622" if theme == "light" else ("#050B14" if theme == "ocean" else "#121218")
            deck_border = "#1E2A3A" if theme != "cockpit" else "#2E2E36"
            self.ahrs_deck.setStyleSheet(f"""
                QFrame#AhrsDeck {{
                    background-color: {deck_bg};
                    border: 1px solid {deck_border};
                    border-radius: 9px;
                }}
            """)

        # 3. Update sliders groove styling
        slider_style = f"""
            QSlider::groove:horizontal {{
                border: 1px solid {badge_border};
                background: {slider_groove};
                height: 10px;
                border-radius: 3px;
            }}
            QSlider::sub-page:horizontal {{
                background: {slider_subpage};
                border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                background: #FFFFFF;
                border: 2px solid {slider_subpage};
                width: 19px;
                margin-top: -4px;
                margin-bottom: -4px;
                border-radius: 7px;
            }}
            QSlider::handle:horizontal:hover {{
                background: {slider_subpage};
                border-color: {slider_subpage};
            }}
        """
        for slider_attr in ('slider_dash_t1', 'slider_dash_t2', 'slider_dash_t3'):
            slider = getattr(self, slider_attr, None)
            if slider:
                slider.setStyleSheet(slider_style)

        # 4. Update thruster value labels and badges
        for val_attr in ('lbl_dash_t1_val', 'lbl_dash_t2_val', 'lbl_dash_t3_val'):
            val_lbl = getattr(self, val_attr, None)
            if val_lbl:
                val_lbl.setStyleSheet(f"color: {val_color}; font-family: 'Consolas', monospace; font-size: 11px; font-weight: bold; min-width: 64px; background: transparent; border: none;")

        for badge_attr in ('lbl_dash_t1_badge', 'lbl_dash_t2_badge', 'lbl_dash_t3_badge'):
            badge = getattr(self, badge_attr, None)
            if badge:
                badge.setStyleSheet(f"background-color: {badge_bg}; border: 1px solid {badge_border}; border-radius: 5px; color: {badge_fg}; font-size: 9px; font-weight: bold; padding: 5px 10px;")

        # 5. Update reset all button
        if hasattr(self, 'dash_btn_rst_all') and self.dash_btn_rst_all:
            self.dash_btn_rst_all.setStyleSheet(f"""
                QPushButton {{
                    background-color: {btn_bg};
                    border: 1px solid {btn_border};
                    border-radius: 4px;
                    padding: 9px 12px;
                    color: {btn_text};
                    font-family: 'Google Sans', sans-serif;
                    font-weight: bold;
                    font-size: 11px;
                }}
                QPushButton:hover {{
                    background-color: {btn_hover};
                    border-color: {dial_label_color};
                }}
            """)

    def log_mission(self, message):
        import time
        timestamp = time.strftime("[%H:%M:%S]")
        if hasattr(self, 'txt_mission_logs'):
            self.txt_mission_logs.append(f"{timestamp} {message}")
        if hasattr(self, 'txt_nav_mission_logs'):
            self.txt_nav_mission_logs.append(f"{timestamp} {message}")
            
        # Dynamically append log to telemetry log tables
        lvl = "INFO"
        src = "SYSTEM"
        msg = message
        
        upper_msg = message.upper()
        if "WARN" in upper_msg:
            lvl = "WARN"
        elif "ALERT" in upper_msg or "ERROR" in upper_msg or "FAIL" in upper_msg:
            lvl = "ERROR"
        elif "OK" in upper_msg or "SUCCESS" in upper_msg or "CONFIRMED" in upper_msg:
            lvl = "OK"
        
        if "GPS" in upper_msg:
            src = "GPS"
        elif "AHRS" in upper_msg:
            src = "AHRS"
        elif "VESSEL" in upper_msg or "ARM" in upper_msg or "AUTOPILOT" in upper_msg or "ROUTE" in upper_msg:
            src = "MISSION"
        elif "LINK" in upper_msg or "CONNECTION" in upper_msg or "FAILSAFE" in upper_msg:
            src = "COMM"
        elif "DOCK" in upper_msg:
            src = "DOCK"
        elif "SONAR" in upper_msg or "PING360" in upper_msg:
            src = "PING360"
        elif "MAG" in upper_msg or "RM3100" in upper_msg:
            src = "RM3100"
            
        t_str = time.strftime("%H:%M:%S")

        if hasattr(self, 'tbl_nav_logs') and self.tbl_nav_logs:
            self.tbl_nav_logs.insertRow(0)
            t_item = QTableWidgetItem(t_str)
            l_item = QTableWidgetItem(lvl)
            s_item = QTableWidgetItem(src)
            m_item = QTableWidgetItem(msg)
            
            if lvl == "WARN":
                l_item.setForeground(QColor(255, 179, 0))
            elif lvl == "ERROR":
                l_item.setForeground(QColor(244, 67, 54))
            else:
                l_item.setForeground(QColor(0, 230, 118))
                
            self.tbl_nav_logs.setItem(0, 0, t_item)
            self.tbl_nav_logs.setItem(0, 1, l_item)
            self.tbl_nav_logs.setItem(0, 2, s_item)
            self.tbl_nav_logs.setItem(0, 3, m_item)
            
            if self.tbl_nav_logs.rowCount() > 100:
                self.tbl_nav_logs.setRowCount(100)
                
        if hasattr(self, 'append_docking_log'):
            self.append_docking_log(t_str, lvl, src, msg)
                
        print(f"[Mission Log] {message}")
        self.show_map_overlay_message(message)

    def show_map_overlay_message(self, message):
        if not hasattr(self, 'map_overlay_lbl') or self.map_overlay_lbl is None:
            self.map_overlay_lbl = QLabel(self.plan_web_view)
            self.map_overlay_lbl.setAlignment(Qt.AlignCenter)
            self.map_overlay_lbl.setStyleSheet("""
                QLabel {
                    background-color: rgba(10, 22, 37, 0.92);
                    border: 1.5px solid #00E5FF;
                    border-radius: 6px;
                    color: #FFFFFF;
                    font-family: 'Google Sans', sans-serif;
                    font-weight: bold;
                    font-size: 11px;
                    padding: 8px 18px;
                }
            """)
            from PySide6.QtWidgets import QGraphicsDropShadowEffect
            shadow = QGraphicsDropShadowEffect(self.map_overlay_lbl)
            shadow.setBlurRadius(10)
            shadow.setColor(QColor(0, 0, 0, 180))
            shadow.setOffset(0, 3)
            self.map_overlay_lbl.setGraphicsEffect(shadow)
            
            self.map_overlay_timer = QTimer(self)
            self.map_overlay_timer.setSingleShot(True)
            self.map_overlay_timer.timeout.connect(self.map_overlay_lbl.hide)
            
        self.map_overlay_lbl.setText(message)
        self.map_overlay_lbl.adjustSize()
        
        parent_w = self.plan_web_view.width()
        lbl_w = self.map_overlay_lbl.width()
        x = (parent_w - lbl_w) // 2
        y = 20
        self.map_overlay_lbl.move(x, y)
        
        self.map_overlay_lbl.show()
        self.map_overlay_lbl.raise_()
        
        self.map_overlay_timer.start(3500)

    def return_to_home(self):
        self.log_mission("Return to Home (RTH) command sent. Aborting route.")
        if self.telemetry_thread and self.telemetry_thread.isRunning():
            self.telemetry_thread.write_data("$RTH")

    def handle_wp_ack(self):
        self.wp_upload_acknowledged = True
        self.btn_arm.setEnabled(True)
        self.log_mission("Vessel acknowledged waypoint route upload. Arming enabled.")

    def create_about_page(self):
        page = QWidget()
        page.setStyleSheet("""
            QWidget {
                background-color: #121212;
                color: #FFFFFF;
                font-family: 'Google Sans', sans-serif;
            }
            QLabel {
                color: #FFFFFF;
            }
        """)
        
        layout = QVBoxLayout(page)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(25)
        layout.setAlignment(Qt.AlignTop)
        
        # Header Label
        header_lbl = QLabel("ⓘ About ASV Control Groundstation")
        header_lbl.setStyleSheet("color: #00E5FF; font-size: 18px; font-weight: bold; font-family: 'Google Sans';")
        layout.addWidget(header_lbl)
        
        # Divider Line
        divider = QFrame()
        divider.setStyleSheet("background-color: #333333; min-height: 1px; max-height: 1px; border: none;")
        layout.addWidget(divider)
        
        logo = QLabel("XERA ROBOTICS")
        logo.setStyleSheet("color: #FF9100; font-size: 15px; font-weight: bold; letter-spacing: 0.5px;")
        logo.setAlignment(Qt.AlignCenter)
        
        dept = QLabel("Marine Autonomous Surface Vessels Control Groundstation")
        dept.setStyleSheet("color: #CCCCCC; font-size: 12px; font-weight: bold;")
        dept.setAlignment(Qt.AlignCenter)
        
        ver = QLabel("Product Version: 2.0 (Enterprise Release)")
        ver.setStyleSheet("color: #888888; font-size: 11px;")
        ver.setAlignment(Qt.AlignCenter)
        
        desc = QLabel(
            "This enterprise ground control application is designed to communicate with Marine Autonomous Surface Vehicles (ASVs) "
            "and subsea telemetry nodes. It manages path configurations, secure serial datalinks, acoustic modem communications, "
            "and telemetry updates. Unauthorized copying or redistribution is strictly prohibited."
        )
        desc.setStyleSheet("color: #AAAAAA; font-size: 12px; line-height: 1.6; margin-top: 10px;")
        desc.setAlignment(Qt.AlignCenter)
        desc.setWordWrap(True)
        
        # Add system information table for classic enterprise look (NO FRAME OUTLINE, JUST FLAT CLEAN GRID)
        sys_info_box = QWidget()
        sys_layout = QVBoxLayout(sys_info_box)
        sys_layout.setContentsMargins(0, 20, 0, 0)
        sys_layout.setSpacing(8)
        
        sys_title = QLabel("System Configuration Information:")
        sys_title.setStyleSheet("font-weight: bold; color: #00E5FF; font-size: 11px;")
        sys_layout.addWidget(sys_title)
        
        import platform
        infos = [
            ("Operating System:", platform.system() + " " + platform.release()),
            ("Python Runtime:", platform.python_version()),
            ("GUI Framework:", "PySide6 (Qt 6.x.x)"),
            ("Active Interfaces:", "COM Serial, HMAC Telemetry Link"),
            ("Copyright:", "© Xera Robotics. All rights reserved.")
        ]
        
        for key, val in infos:
            row = QHBoxLayout()
            row.setSpacing(10)
            kl = QLabel(key)
            kl.setStyleSheet("font-weight: bold; color: #888888; font-size: 10px; min-width: 130px;")
            vl = QLabel(val)
            vl.setStyleSheet("color: #FFFFFF; font-size: 10px;")
            row.addWidget(kl)
            row.addWidget(vl)
            row.addStretch()
            sys_layout.addLayout(row)
            
        layout.addWidget(logo)
        layout.addWidget(dept)
        layout.addWidget(ver)
        layout.addWidget(desc)
        layout.addWidget(sys_info_box)
        layout.addStretch()
        return page


    def refresh_ports(self):
        if self.top_bar.status_state == "disconnected":
            active_ports = MarineTelemetryThread.get_available_ports()
            # Save the current index so we don't disrupt selection
            curr_text = self.top_bar.port_combo.currentText()
            self.top_bar.populate_ports(active_ports)
            if curr_text in active_ports:
                self.top_bar.port_combo.setCurrentText(curr_text)



    @Slot(str, str)
    def connect_serial(self, port, baud):
        if hasattr(self, 'docking_visual_widget') and self.docking_visual_widget:
            self.docking_visual_widget.clear_vehicle_position()
        # Stop previous thread if it exists
        if self.telemetry_thread and self.telemetry_thread.isRunning():
            self.telemetry_thread.stop()
            
        self.telemetry_thread = MarineTelemetryThread(port, baud)
        self.telemetry_thread.light_state = self.light_state
        self.telemetry_thread.camera_state = self.camera_state
        self.telemetry_thread.data_received.connect(self.update_telemetry)
        self.telemetry_thread.connection_status.connect(self.on_connection_status_changed)
        self.telemetry_thread.wp_ack_received.connect(self.handle_wp_ack)
        self.telemetry_thread.stabilization_status_received.connect(self.handle_stabilization_status)
        self.telemetry_thread.fusion_status_received.connect(self.handle_fusion_status)
        self.telemetry_thread.log_received.connect(self.log_mission)
        self.telemetry_thread.raw_line_received.connect(self.log_diagnostics_raw)
        
        # Initially transition UI status to connecting (amber) waiting for valid format data
        self.top_bar.set_connection_status("connecting", "Waiting Data")
        for card in self.cards.values():
            card.set_status("connecting")
            
        self.port_refresh_timer.stop()
        self.watchdog_timer.start()
        self.command_stream_timer.start()
        
        self.telemetry_thread.start()

    @Slot()
    def disconnect_serial(self):
        self.watchdog_timer.stop()
        self.command_stream_timer.stop()
        if self.telemetry_thread:
            self.telemetry_thread.stop()
            self.telemetry_thread = None
            
        self.top_bar.set_connection_status("disconnected", "Standby")
        self.log_file_path = None
        for card in self.cards.values():
            card.set_status("disconnected")
            card.reset_style()
            
        self.warning_banner_frame.setVisible(False)
        self.warning_flash_timer.stop()
        self.port_refresh_timer.start(3000)
        self.clear_telemetry_data()

    def trigger_emergency_stop(self):
        print("[Emergency Stop] Triggered! Disarming and stopping vessel.")
        if self.telemetry_thread and self.telemetry_thread.isRunning():
            self.telemetry_thread.write_data("$STABILIZE,OFF")
        self.update_active_stabilization_ui_state(False, "OFF")
        self.disarm_vehicle()
        self.set_navigation_mode("manual")
        self.stop_mission()
        
        # Override warning banner to show emergency shutdown
        self.lbl_warning_banner.setText("EMERGENCY STOP ACTIVE: VEHICLE DISARMED & SHUTDOWN!")
        self.warning_banner_frame.setVisible(True)
        if not self.warning_flash_timer.isActive():
            self.warning_flash_timer.start()
            
        # Send explicit disarm and zero-thrust commands multiple times for redundancy/packet loss recovery
        if self.telemetry_thread and self.telemetry_thread.isRunning():
            for delay in [0, 50, 100, 150, 200]:
                QTimer.singleShot(delay, lambda: self.telemetry_thread.write_data("DISARM"))

    def clear_telemetry_data(self):
        self._minimize_docking_stage_popup()
        if hasattr(self, 'docking_visual_widget') and self.docking_visual_widget:
            self.docking_visual_widget.clear_vehicle_position()
        self.current_latitude = None
        self.current_longitude = None
        self.current_heading = None
        self.current_depth = None
        self.current_pitch = None
        # Reset GPS data
        if hasattr(self, 'lbl_nav_lat') and self.lbl_nav_lat:
            self.lbl_nav_lat.setText("--")
            self.lbl_nav_lat.setStyleSheet("color: #FF1744; font-family: 'Consolas', monospace; font-size: 10px; font-weight: bold;")
        if hasattr(self, 'lbl_nav_lon') and self.lbl_nav_lon:
            self.lbl_nav_lon.setText("--")
            self.lbl_nav_lon.setStyleSheet("color: #FF1744; font-family: 'Consolas', monospace; font-size: 10px; font-weight: bold;")
        if hasattr(self, 'lbl_nav_sats') and self.lbl_nav_sats:
            self.lbl_nav_sats.setText("0")
            self.lbl_nav_sats.setStyleSheet("color: #FF1744; font-family: 'Consolas', monospace; font-size: 10px; font-weight: bold;")
            
        if hasattr(self, 'lbl_nav_status') and self.lbl_nav_status:
            self.lbl_nav_status.setText("DISCONNECTED")
            self.lbl_nav_status.setStyleSheet("color: #FF1744; font-weight: bold; font-size: 10px;")
        if hasattr(self, 'lbl_nav_quality') and self.lbl_nav_quality:
            self.lbl_nav_quality.setText("0%")
            self.lbl_nav_quality.setStyleSheet("color: #FF1744; font-family: 'Consolas', monospace; font-size: 10px; font-weight: bold;")
        if hasattr(self, 'lbl_nav_time') and self.lbl_nav_time:
            self.lbl_nav_time.setText("--")
            self.lbl_nav_time.setStyleSheet("color: #FF1744; font-family: 'Consolas', monospace; font-size: 10px; font-weight: bold;")

        # Reset Top Bar indicators
        self.top_bar.set_gps_count("--")
        self.top_bar.set_gps_fix(False)
        self.top_bar.set_battery_percentage("--")
        self.top_bar.set_chamber_temp("--")

        # Reset BMS readouts
        if hasattr(self, 'lbl_nav_voltage') and self.lbl_nav_voltage:
            self.lbl_nav_voltage.setText("0.00 V")
        if hasattr(self, 'lbl_nav_current') and self.lbl_nav_current:
            self.lbl_nav_current.setText("0.00 A")
        if hasattr(self, 'lbl_nav_soc') and self.lbl_nav_soc:
            self.lbl_nav_soc.setText("0 %")
        if hasattr(self, 'lbl_nav_rem_ah') and self.lbl_nav_rem_ah:
            self.lbl_nav_rem_ah.setText("0.00 Ah")
        if hasattr(self, 'lbl_nav_soh') and self.lbl_nav_soh:
            self.lbl_nav_soh.setText("0 %")
        if hasattr(self, 'lbl_nav_batt_temp') and self.lbl_nav_batt_temp:
            self.lbl_nav_batt_temp.setText("0.0 °C")

        # Reset Cards
        for key in self.cards.keys():
            if key in ["latitude", "longitude"]:
                self.cards[key].set_value("--")
            elif key in ["satellites", "confidence", "soc", "soh"]:
                self.cards[key].set_value("0")
            elif key in ["p1", "p2", "p3"]:
                self.cards[key].set_value("1500")
            else:
                self.cards[key].set_value("0.0")

        # Reset instrument widgets
        self.compass_widget.set_yaw(0.0)
        self.horizon_widget.set_attitude(0.0, 0.0)
        self.horizon_3d_widget.set_attitude(0.0, 0.0)
        if hasattr(self, 'mag_vector_widget') and self.mag_vector_widget:
            self.mag_vector_widget.set_mag_values(0.0, 0.0, 0.0)
        if hasattr(self, 'battery_gauge') and self.battery_gauge:
            self.battery_gauge.set_soc(0)
        if hasattr(self, 'actuators_gauge') and self.actuators_gauge:
            self.actuators_gauge.set_values(1500, 1500, 1500)

        # Reset dynamic scrolling plots
        self.attitude_chart.append_data([0.0, 0.0])
        if hasattr(self, 'gps_chart') and self.gps_chart:
            self.gps_chart.append_data([0.0])
        if hasattr(self, 'env_chart') and self.env_chart:
            self.env_chart.append_data([0.0, 0.0])
        if hasattr(self, 'power_chart') and self.power_chart:
            self.power_chart.append_data([0.0, 0.0])
        if hasattr(self, 'actuators_chart') and self.actuators_chart:
            self.actuators_chart.append_data([1500, 1500, 1500])
        if hasattr(self, 'mag_chart') and self.mag_chart:
            self.mag_chart.append_data([0.0, 0.0, 0.0])

        # Reset Docking Cards & Widgets
        if hasattr(self, 'docking_cards') and self.docking_cards:
            self.docking_cards["sonar_range"].set_value("--")
            self.docking_cards["sonar_range"].set_status("normal")
            self.docking_cards["sonar_bearing"].set_value("--")
            self.docking_cards["sonar_scan"].set_value("0.0")
            self.docking_cards["sonar_conf"].set_value("0")
            self.docking_cards["mx"].set_value("0.0")
            self.docking_cards["my"].set_value("0.0")
            self.docking_cards["mz"].set_value("0.0")
            self.docking_cards["magb"].set_value("0.00")
            if "mag_heading" in self.docking_cards:
                self.docking_cards["mag_heading"].set_value("0.00")
            if "target_dist" in self.docking_cards:
                self.docking_cards["target_dist"].set_value("0.0")
                self.docking_cards["align_angle"].set_value("+0.0")
                self.docking_cards["lat_offset"].set_value("0.00")
                self.docking_cards["guidance_status"].set_value("STANDBY")
            
        if hasattr(self, 'docking_sonar') and self.docking_sonar:
            self.docking_sonar.update_scan_line(0.0, 10.0, [0]*60)
        if hasattr(self, 'docking_mag_vector') and self.docking_mag_vector:
            self.docking_mag_vector.set_mag_values(0.0, 0.0, 0.0)

        # Reset Docking Status checklist rows
        if hasattr(self, 'lbl_dock_status_sonar_dot') and self.lbl_dock_status_sonar_dot:
            self.lbl_dock_status_sonar_dot.setText("●")
            self.lbl_dock_status_sonar_dot.setStyleSheet("color: #FF1744; font-size: 11px; font-weight: bold;")
        if hasattr(self, 'lbl_dock_status_sonar_val') and self.lbl_dock_status_sonar_val:
            self.lbl_dock_status_sonar_val.setText("INACTIVE")
            self.lbl_dock_status_sonar_val.setStyleSheet("color: #888888; font-size: 9.5px; font-weight: bold;")
        if hasattr(self, 'lbl_dock_status_mag_dot') and self.lbl_dock_status_mag_dot:
            self.lbl_dock_status_mag_dot.setText("●")
            self.lbl_dock_status_mag_dot.setStyleSheet("color: #FF1744; font-size: 11px; font-weight: bold;")
        if hasattr(self, 'lbl_dock_status_mag_val') and self.lbl_dock_status_mag_val:
            self.lbl_dock_status_mag_val.setText("INACTIVE")
            self.lbl_dock_status_mag_val.setStyleSheet("color: #888888; font-size: 9.5px; font-weight: bold;")
        if hasattr(self, 'lbl_dock_status_target_dot') and self.lbl_dock_status_target_dot:
            self.lbl_dock_status_target_dot.setText("●")
            self.lbl_dock_status_target_dot.setStyleSheet("color: #FF1744; font-size: 11px; font-weight: bold;")
        if hasattr(self, 'lbl_dock_status_target_val') and self.lbl_dock_status_target_val:
            self.lbl_dock_status_target_val.setText("SEARCHING")
            self.lbl_dock_status_target_val.setStyleSheet("color: #FF9100; font-size: 9.5px; font-weight: bold;")
        if hasattr(self, 'lbl_dock_status_align_dot') and self.lbl_dock_status_align_dot:
            self.lbl_dock_status_align_dot.setText("●")
            self.lbl_dock_status_align_dot.setStyleSheet("color: #FF1744; font-size: 11px; font-weight: bold;")
        if hasattr(self, 'lbl_dock_status_align_val') and self.lbl_dock_status_align_val:
            self.lbl_dock_status_align_val.setText("INACTIVE")
            self.lbl_dock_status_align_val.setStyleSheet("color: #888888; font-size: 9.5px; font-weight: bold;")
        if hasattr(self, 'lbl_dock_status_ready_dot') and self.lbl_dock_status_ready_dot:
            self.lbl_dock_status_ready_dot.setText("●")
            self.lbl_dock_status_ready_dot.setStyleSheet("color: #FF1744; font-size: 11px; font-weight: bold;")
        if hasattr(self, 'lbl_dock_status_ready_val') and self.lbl_dock_status_ready_val:
            self.lbl_dock_status_ready_val.setText("STANDBY")
            self.lbl_dock_status_ready_val.setStyleSheet("color: #888888; font-size: 9.5px; font-weight: bold;")

        # Reset Communication Page Widgets
        if hasattr(self, 'comm_modem_widget') and self.comm_modem_widget:
            self.comm_modem_widget.update_modem("00:00:00", 0.0, 0.0, 0, "INACTIVE")
        if hasattr(self, 'comm_rov_positioning') and self.comm_rov_positioning:
            self.comm_rov_positioning.update_position(0.0, 0.0, 0.0, 0.0, 0.0)
        if hasattr(self, 'comm_hydrophone') and self.comm_hydrophone:
            self.comm_hydrophone.update_audio(0.0, -100.0, 0.0, "STANDBY")

        # Reset Communication Page Boxes & Labels
        if hasattr(self, 'lbl_comm_modem_rate') and self.lbl_comm_modem_rate:
            self.lbl_comm_modem_rate.setText("0.0 kbps")
        if hasattr(self, 'lbl_comm_modem_lat') and self.lbl_comm_modem_lat:
            self.lbl_comm_modem_lat.setText("0 ms")
        if hasattr(self, 'lbl_comm_modem_qual') and self.lbl_comm_modem_qual:
            self.lbl_comm_modem_qual.setText("INACTIVE")
            self.lbl_comm_modem_qual.setStyleSheet("color: #888888; font-family: 'Consolas', monospace; font-size: 9.5px; font-weight: bold;")
        if hasattr(self, 'lbl_comm_stat_tx') and self.lbl_comm_stat_tx:
            self.lbl_comm_stat_tx.setText("0")
        if hasattr(self, 'lbl_comm_stat_rx') and self.lbl_comm_stat_rx:
            self.lbl_comm_stat_rx.setText("0")
        if hasattr(self, 'lbl_comm_stat_loss') and self.lbl_comm_stat_loss:
            self.lbl_comm_stat_loss.setText("0.00 %")
            self.lbl_comm_stat_loss.setStyleSheet("color: #888888; font-family: 'Consolas', monospace; font-size: 9.5px; font-weight: bold;")
        if hasattr(self, 'lbl_comm_stat_err') and self.lbl_comm_stat_err:
            self.lbl_comm_stat_err.setText("0")
        if hasattr(self, 'lbl_comm_rov_range') and self.lbl_comm_rov_range:
            self.lbl_comm_rov_range.setText("0.00 m")
        if hasattr(self, 'lbl_comm_rov_bearing') and self.lbl_comm_rov_bearing:
            self.lbl_comm_rov_bearing.setText("0.0°")
        if hasattr(self, 'lbl_comm_stat_pq') and self.lbl_comm_stat_pq:
            self.lbl_comm_stat_pq.setText("INACTIVE")
            self.lbl_comm_stat_pq.setStyleSheet("color: #888888; font-family: 'Consolas', monospace; font-size: 9.5px; font-weight: bold;")
        if hasattr(self, 'lbl_comm_stat_hdop') and self.lbl_comm_stat_hdop:
            self.lbl_comm_stat_hdop.setText("0.00")
        if hasattr(self, 'lbl_comm_hist_max') and self.lbl_comm_hist_max:
            self.lbl_comm_hist_max.setText("0.00 m")
        if hasattr(self, 'lbl_comm_hist_avg') and self.lbl_comm_hist_avg:
            self.lbl_comm_hist_avg.setText("0.00 m")
        if hasattr(self, 'lbl_comm_hist_min') and self.lbl_comm_hist_min:
            self.lbl_comm_hist_min.setText("0.00 m")
        if hasattr(self, 'lbl_comm_hydro_pf') and self.lbl_comm_hydro_pf:
            self.lbl_comm_hydro_pf.setText("0.0 kHz")
        if hasattr(self, 'lbl_comm_hydro_sl') and self.lbl_comm_hydro_sl:
            self.lbl_comm_hydro_sl.setText("-100 dB")
            self.lbl_comm_hydro_sl.setStyleSheet("color: #888888; font-family: 'Consolas', monospace; font-size: 9.5px; font-weight: bold;")
        if hasattr(self, 'lbl_comm_hydro_doa') and self.lbl_comm_hydro_doa:
            self.lbl_comm_hydro_doa.setText("0.0°")
        if hasattr(self, 'lbl_comm_hydro_time') and self.lbl_comm_hydro_time:
            self.lbl_comm_hydro_time.setText("--")
        if hasattr(self, 'lbl_comm_hydro_bw') and self.lbl_comm_hydro_bw:
            self.lbl_comm_hydro_bw.setText("0.0 kHz")
        if hasattr(self, 'lbl_comm_hydro_stat') and self.lbl_comm_hydro_stat:
            self.lbl_comm_hydro_stat.setText("STANDBY")
            self.lbl_comm_hydro_stat.setStyleSheet("color: #888888; font-family: 'Consolas', monospace; font-size: 9.5px; font-weight: bold;")

        # Reset Depth Page Widgets
        if hasattr(self, 'depth_dvl_widget') and self.depth_dvl_widget:
            self.depth_dvl_widget.update_dvl(0.0, False, 0, 0.0, 0.0, 0.0)
        if hasattr(self, 'depth_pressure') and self.depth_pressure:
            self.depth_pressure.update_pressure(0.0, 0.0)
        if hasattr(self, 'water_column') and self.water_column:
            self.water_column.update_depth(0.0, 0.0)
        if hasattr(self, 'depth_quality_widget') and self.depth_quality_widget:
            self.depth_quality_widget.update_quality(0.0, 0.0, 0.0)
        if hasattr(self, 'altimeter_widget') and self.altimeter_widget:
            self.altimeter_widget.update_altimeter(0.0, 0.0)

        # Reset Depth Page Box & Checklist Labels
        if hasattr(self, 'lbl_depth_altimeter_val') and self.lbl_depth_altimeter_val:
            self.lbl_depth_altimeter_val.setText("0.00 m")
        if hasattr(self, 'lbl_depth_altimeter_conf') and self.lbl_depth_altimeter_conf:
            self.lbl_depth_altimeter_conf.setText("0 %")
            self.lbl_depth_altimeter_conf.setStyleSheet("color: #888888; font-family: 'Consolas', monospace; font-size: 12px; font-weight: bold;")
        if hasattr(self, 'lbl_depth_dvl_depth') and self.lbl_depth_dvl_depth:
            self.lbl_depth_dvl_depth.setText("0.00 m")
        if hasattr(self, 'lbl_depth_dvl_lock') and self.lbl_depth_dvl_lock:
            self.lbl_depth_dvl_lock.setText("LOST")
            self.lbl_depth_dvl_lock.setStyleSheet("color: #FF1744; font-family: 'Consolas', monospace; font-size: 12px; font-weight: bold;")
        if hasattr(self, 'lbl_depth_dvl_qual') and self.lbl_depth_dvl_qual:
            self.lbl_depth_dvl_qual.setText("0 %")
            self.lbl_depth_dvl_qual.setStyleSheet("color: #888888; font-family: 'Consolas', monospace; font-size: 12px; font-weight: bold;")
        if hasattr(self, 'lbl_depth_status_alt_dot') and self.lbl_depth_status_alt_dot:
            self.lbl_depth_status_alt_dot.setText("●")
            self.lbl_depth_status_alt_dot.setStyleSheet("color: #FF1744; font-size: 11px; font-weight: bold;")
        if hasattr(self, 'lbl_depth_status_alt_val') and self.lbl_depth_status_alt_val:
            self.lbl_depth_status_alt_val.setText("INACTIVE")
            self.lbl_depth_status_alt_val.setStyleSheet("color: #888888; font-size: 9.5px; font-weight: bold;")
        if hasattr(self, 'lbl_depth_status_press_dot') and self.lbl_depth_status_press_dot:
            self.lbl_depth_status_press_dot.setText("●")
            self.lbl_depth_status_press_dot.setStyleSheet("color: #FF1744; font-size: 11px; font-weight: bold;")
        if hasattr(self, 'lbl_depth_status_press_val') and self.lbl_depth_status_press_val:
            self.lbl_depth_status_press_val.setText("INACTIVE")
            self.lbl_depth_status_press_val.setStyleSheet("color: #888888; font-size: 9.5px; font-weight: bold;")
        if hasattr(self, 'lbl_depth_status_dvl_dot') and self.lbl_depth_status_dvl_dot:
            self.lbl_depth_status_dvl_dot.setText("●")
            self.lbl_depth_status_dvl_dot.setStyleSheet("color: #FF1744; font-size: 11px; font-weight: bold;")
        if hasattr(self, 'lbl_depth_status_dvl_val') and self.lbl_depth_status_dvl_val:
            self.lbl_depth_status_dvl_val.setText("INACTIVE")
            self.lbl_depth_status_dvl_val.setStyleSheet("color: #888888; font-size: 9.5px; font-weight: bold;")
        if hasattr(self, 'lbl_depth_status_lock_dot') and self.lbl_depth_status_lock_dot:
            self.lbl_depth_status_lock_dot.setText("●")
            self.lbl_depth_status_lock_dot.setStyleSheet("color: #FF1744; font-size: 11px; font-weight: bold;")
        if hasattr(self, 'lbl_depth_status_lock_val') and self.lbl_depth_status_lock_val:
            self.lbl_depth_status_lock_val.setText("LOST")
            self.lbl_depth_status_lock_val.setStyleSheet("color: #FF1744; font-size: 9.5px; font-weight: bold;")
        if hasattr(self, 'lbl_depth_status_quality') and self.lbl_depth_status_quality:
            self.lbl_depth_status_quality.setText("INACTIVE")
            self.lbl_depth_status_quality.setStyleSheet("color: #888888; font-family: 'Consolas', monospace; font-size: 9.5px; font-weight: bold;")
        if hasattr(self, 'lbl_depth_status_acc') and self.lbl_depth_status_acc:
            self.lbl_depth_status_acc.setText("0.00 m")
            self.lbl_depth_status_acc.setStyleSheet("color: #888888; font-family: 'Consolas', monospace; font-size: 9.5px; font-weight: bold;")
        if hasattr(self, 'lbl_depth_status_conf') and self.lbl_depth_status_conf:
            self.lbl_depth_status_conf.setText("0 %")
            self.lbl_depth_status_conf.setStyleSheet("color: #888888; font-family: 'Consolas', monospace; font-size: 9.5px; font-weight: bold;")

        # Reset Raw Sensor Sidebar Labels (Depth Page)
        if hasattr(self, 'lbl_raw_depth_alt_val') and self.lbl_raw_depth_alt_val:
            self.lbl_raw_depth_alt_val.setText("0.00")
        if hasattr(self, 'lbl_raw_depth_alt_conf') and self.lbl_raw_depth_alt_conf:
            self.lbl_raw_depth_alt_conf.setText("0")
        if hasattr(self, 'lbl_raw_depth_press_bar') and self.lbl_raw_depth_press_bar:
            self.lbl_raw_depth_press_bar.setText("0.000")
        if hasattr(self, 'lbl_raw_depth_press_kpa') and self.lbl_raw_depth_press_kpa:
            self.lbl_raw_depth_press_kpa.setText("0.0")
        if hasattr(self, 'lbl_raw_depth_press_depth') and self.lbl_raw_depth_press_depth:
            self.lbl_raw_depth_press_depth.setText("0.00")
        if hasattr(self, 'lbl_raw_depth_press_temp') and self.lbl_raw_depth_press_temp:
            self.lbl_raw_depth_press_temp.setText("0.0")
        if hasattr(self, 'lbl_raw_depth_chamber_temp') and self.lbl_raw_depth_chamber_temp:
            self.lbl_raw_depth_chamber_temp.setText("0.0")
        if hasattr(self, 'lbl_raw_depth_dvl_val') and self.lbl_raw_depth_dvl_val:
            self.lbl_raw_depth_dvl_val.setText("0.00")
        if hasattr(self, 'lbl_raw_depth_dvl_vx') and self.lbl_raw_depth_dvl_vx:
            self.lbl_raw_depth_dvl_vx.setText("0.00")
        if hasattr(self, 'lbl_raw_depth_dvl_vy') and self.lbl_raw_depth_dvl_vy:
            self.lbl_raw_depth_dvl_vy.setText("0.00")
        if hasattr(self, 'lbl_raw_depth_dvl_vz') and self.lbl_raw_depth_dvl_vz:
            self.lbl_raw_depth_dvl_vz.setText("0.00")
        if hasattr(self, 'lbl_raw_depth_dvl_lock') and self.lbl_raw_depth_dvl_lock:
            self.lbl_raw_depth_dvl_lock.setText("LOST")
            self.lbl_raw_depth_dvl_lock.setStyleSheet("color: #FF1744; font-size: 9px; font-weight: bold;")

        # Clear logs and detections tables
        if hasattr(self, 'tbl_packet_monitor') and self.tbl_packet_monitor:
            self.tbl_packet_monitor.setRowCount(0)
        if hasattr(self, 'tbl_hydro_detections') and self.tbl_hydro_detections:
            self.tbl_hydro_detections.setRowCount(0)
        if hasattr(self, 'tbl_depth_logs') and self.tbl_depth_logs:
            self.tbl_depth_logs.setRowCount(0)
        if hasattr(self, 'tbl_docking_logs') and self.tbl_docking_logs:
            self.tbl_docking_logs.setRowCount(0)



    @Slot(dict)
    def update_telemetry(self, data):
        # Ensure all key telemetry variables have safe defaults to prevent KeyErrors
        default_keys = {
            "roll": 0.0, "pitch": 0.0, "yaw": 0.0,
            "latitude": 0.0, "longitude": 0.0, "satellites": 0,
            "chamber_temp": 0.0, "chamber_hum": 0.0,
            "bms_volt": 0.0, "bms_curr": 0.0, "bms_soc": 0, "bms_soh": 100,
            "bms_temp": 0.0,
            "pwm_rc1": 1500, "pwm_rc2": 1500, "pwm_rc3": 1500,
            "distance": 0.0, "confidence": 100.0
        }
        # Standardize 25-element telemetry field names and map to internal UI variables
        if 'satellites' not in data:
            data['satellites'] = data.get('no_of_satellites', data.get('sat', 0))
        if 'no_of_satellites' not in data:
            data['no_of_satellites'] = data.get('satellites', data.get('sat', 0))

        if 'chamber_temp' not in data:
            data['chamber_temp'] = data.get('temperature', data.get('temp', 0.0))
        if 'temperature' not in data:
            data['temperature'] = data.get('chamber_temp', data.get('temp', 0.0))

        if 'chamber_hum' not in data:
            data['chamber_hum'] = data.get('humidity', data.get('hum', 0.0))
        if 'humidity' not in data:
            data['humidity'] = data.get('chamber_hum', data.get('hum', 0.0))

        if 'bms_volt' not in data:
            data['bms_volt'] = data.get('battery_volt', data.get('volts', data.get('battery_voltage', 0.0)))
        if 'battery_volt' not in data:
            data['battery_volt'] = data.get('bms_volt', data.get('volts', 0.0))

        if 'bms_curr' not in data:
            data['bms_curr'] = data.get('battery_current', data.get('amps', 0.0))
        if 'battery_current' not in data:
            data['battery_current'] = data.get('bms_curr', data.get('amps', 0.0))

        if 'bms_power' not in data:
            volt_for_pwr = float(data.get('bms_volt', data.get('battery_volt', 0.0)) or 0.0)
            curr_for_pwr = float(data.get('bms_curr', data.get('battery_current', 0.0)) or 0.0)
            calc_pwr = round(volt_for_pwr * curr_for_pwr, 2)
            data['bms_power'] = data.get('battery_power', data.get('watts', calc_pwr))
        if 'battery_power' not in data:
            data['battery_power'] = data.get('bms_power', 0.0)

        if 'bms_temp' not in data:
            data['bms_temp'] = data.get('battery_temp', 0.0)
        if 'battery_temp' not in data:
            data['battery_temp'] = data.get('bms_temp', 0.0)

        if 'bms_soc' not in data:
            data['bms_soc'] = data.get('battery_soc', data.get('soc', 0))
        if 'battery_soc' not in data:
            data['battery_soc'] = data.get('bms_soc', 0)

        if 'bms_soh' not in data:
            data['bms_soh'] = data.get('battery_soh', data.get('soh', 100))
        if 'battery_soh' not in data:
            data['battery_soh'] = data.get('bms_soh', 100)

        if 'pwm_rc1' not in data:
            data['pwm_rc1'] = data.get('pwm1', data.get('p1', data.get('t1', 1500)))
        if 'pwm1' not in data:
            data['pwm1'] = data.get('pwm_rc1', data.get('p1', 1500))

        if 'pwm_rc2' not in data:
            data['pwm_rc2'] = data.get('pwm2', data.get('p2', data.get('t2', 1500)))
        if 'pwm2' not in data:
            data['pwm2'] = data.get('pwm_rc2', data.get('p2', 1500))

        if 'pwm_rc3' not in data:
            data['pwm_rc3'] = data.get('pwm3', data.get('p3', data.get('t3', 1500)))
        if 'pwm3' not in data:
            data['pwm3'] = data.get('pwm_rc3', data.get('p3', 1500))

        if 'light' not in data:
            data['light'] = data.get('light_status', data.get('lit', 0))
        if 'light_status' not in data:
            data['light_status'] = data.get('light', 0)

        if 'camera' not in data:
            data['camera'] = data.get('camera_status', data.get('cam', 0))
        if 'camera_status' not in data:
            data['camera_status'] = data.get('camera', 0)

        if 'latitude' not in data:
            data['latitude'] = data.get('lat', 0.0)
        if 'longitude' not in data:
            data['longitude'] = data.get('lon', 0.0)

        if 'yaw' not in data:
            data['yaw'] = data.get('heading', data.get('course', 0.0))
        if 'heading' not in data:
            data['heading'] = data.get('yaw', data.get('course', 0.0))

        if 'distance' not in data:
            data['distance'] = data.get('sonar_range', 0.0)
        if 'sonar_range' not in data:
            data['sonar_range'] = data.get('distance', None)

        if 'mx' not in data:
            data['mx'] = data.get('magx', data.get('magX', data.get('mag_x', 0.0)))
        if 'my' not in data:
            data['my'] = data.get('magy', data.get('magY', data.get('mag_y', 0.0)))
        if 'mz' not in data:
            data['mz'] = data.get('magz', data.get('magZ', data.get('mag_z', 0.0)))
        if 'magx' not in data:
            data['magx'] = data.get('mx', 0.0)
        if 'magy' not in data:
            data['magy'] = data.get('my', 0.0)
        if 'magz' not in data:
            data['magz'] = data.get('mz', 0.0)

        if 'magB' not in data:
            data['magB'] = data.get('mag_total', data.get('magb', 0.0))
        if 'mag_total' not in data:
            data['mag_total'] = data.get('magB', 0.0)

        if 'gps' not in data:
            data['gps'] = data.get('gps_status', 'FIX' if (float(data.get('latitude', 0.0) or 0.0) != 0.0 or float(data.get('longitude', 0.0) or 0.0) != 0.0) else 'NO_FIX')

        for k, v in default_keys.items():
            if k not in data or data[k] is None:
                data[k] = v

        # Active-stabilization telemetry is present in JSON mode. In CSV mode
        # the same UI state is synchronized through ACK,STABILIZE replies.
        if 'active_stabilization' in data:
            try:
                stab_enabled = bool(int(float(data.get('active_stabilization', 0) or 0)))
            except (TypeError, ValueError):
                stab_enabled = False
            stab_mode = str(data.get('stabilization_mode', 'ON' if stab_enabled else 'OFF'))
            if (stab_enabled != getattr(self, 'active_stabilization', False)
                    or stab_mode != getattr(self, 'active_stabilization_mode', 'OFF')):
                self.update_active_stabilization_ui_state(stab_enabled, stab_mode)

        # 1. GPS Satellites Filtering (bounds check [0, 32])
        try:
            sats = int(float(data.get('satellites', 0) or 0))
            data['satellites'] = max(0, min(32, sats))
        except (ValueError, TypeError):
            data['satellites'] = 0

        # Smooth AHRS angles (circular yaw math avoids a false 359° -> 0° jump).
        def finite_reading(key):
            try:
                value = float(data.get(key, 0.0) or 0.0)
                return value if math.isfinite(value) else 0.0
            except (TypeError, ValueError):
                return 0.0

        for key in ('roll', 'pitch'):
            raw_angle = finite_reading(key)
            filtered_key = f'_filtered_{key}'
            previous = getattr(self, filtered_key, None)
            filtered = raw_angle if previous is None else 0.25 * raw_angle + 0.75 * previous
            setattr(self, filtered_key, filtered)
            data[key] = filtered

        raw_yaw = finite_reading('yaw') % 360.0
        previous_yaw = getattr(self, '_filtered_yaw', None)
        if previous_yaw is None:
            filtered_yaw = raw_yaw
        else:
            yaw_delta = (raw_yaw - previous_yaw + 180.0) % 360.0 - 180.0
            filtered_yaw = (previous_yaw + 0.25 * yaw_delta) % 360.0
        self._filtered_yaw = filtered_yaw
        data['yaw'] = filtered_yaw
        data['heading'] = filtered_yaw

        # Reject invalid fixes and implausible position jumps before smoothing GPS.
        raw_lat = finite_reading('latitude')
        raw_lon = finite_reading('longitude')
        now = _time.monotonic()
        gps_valid = data.get('gps_valid', data.get('gps', 1))
        if isinstance(gps_valid, str):
            gps_status = gps_valid.strip().upper().replace(" ", "_")
            if gps_status in ("FIX", "GPS_FIX", "VALID", "TRUE", "YES"):
                gps_valid = True
            elif gps_status in ("NO_FIX", "GPS_NO_FIX", "INVALID", "FALSE", "NO", ""):
                gps_valid = False
            else:
                try:
                    gps_valid = bool(int(float(gps_status)))
                except (TypeError, ValueError):
                    gps_valid = False
        else:
            try:
                gps_valid = bool(int(gps_valid))
            except (TypeError, ValueError):
                gps_valid = False
        valid_fix = (gps_valid and -90.0 <= raw_lat <= 90.0
                     and -180.0 <= raw_lon <= 180.0
                     and not (raw_lat == 0.0 and raw_lon == 0.0))
        previous_lat = getattr(self, '_filtered_lat', None)
        previous_lon = getattr(self, '_filtered_lon', None)
        accept_fix = valid_fix
        if valid_fix and previous_lat is not None and previous_lon is not None:
            lat1, lat2 = math.radians(previous_lat), math.radians(raw_lat)
            dlat = lat2 - lat1
            dlon = math.radians(raw_lon - previous_lon)
            hav = math.sin(dlat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
            jump_m = 6371000.0 * 2.0 * math.asin(min(1.0, math.sqrt(hav)))
            elapsed = max(0.0, now - getattr(self, '_last_gps_accept_time', now))
            # Allow normal vessel motion plus GPS noise; reject isolated large jumps.
            max_jump_m = min(150.0, 12.0 + 6.0 * elapsed)
            accept_fix = jump_m <= max_jump_m

        if accept_fix:
            if previous_lat is None or previous_lon is None:
                self._filtered_lat, self._filtered_lon = raw_lat, raw_lon
            else:
                alpha = 0.30
                self._filtered_lat = alpha * raw_lat + (1.0 - alpha) * previous_lat
                self._filtered_lon = alpha * raw_lon + (1.0 - alpha) * previous_lon
            self._last_gps_accept_time = now
            data['latitude'], data['longitude'] = self._filtered_lat, self._filtered_lon
        elif previous_lat is not None and previous_lon is not None:
            data['latitude'], data['longitude'] = previous_lat, previous_lon
        else:
            data['latitude'], data['longitude'] = 0.0, 0.0

        # If JSON telemetry is enabled, mirror the backend's last accepted fused
        # target as an additional confirmation channel.  CSV installations still
        # receive the same state through ACK,FUSE replies.
        if 'fusion_valid' in data:
            self.fusion_backend_state = {
                "connected": True,
                "valid": bool(int(float(data.get('fusion_valid', 0) or 0))),
                "range_m": data.get('fusion_range_m'),
                "bearing_rel_deg": data.get('fusion_bearing_rel_deg'),
                "sigma_m": data.get('fusion_sigma_m'),
                "sources": str(data.get('fusion_sources', '') or ''),
                "stage": str(data.get('fusion_stage', 'NO_FIX') or 'NO_FIX'),
                "age_s": data.get('fusion_age_s'),
                "timestamp": _time.monotonic(),
                "error": "",
            }

        # Feed the Sensor Fusion page (it must never break telemetry handling)
        if hasattr(self, 'sensor_fusion_ingest'):
            try:
                self.sensor_fusion_ingest(data)
            except Exception as fusion_err:
                print(f"[Sensor Fusion] ingest error: {fusion_err}")

        # Capture values for top bar update
        original_gps = data['satellites']
        original_soc = data.get('bms_soc', 0)
        self.last_sats = original_gps
        
        # NOTE: The zeroing-out block below is commented out to prevent:
        # - False critical battery failsafes when navigating away from the dashboard
        # - Telemetry data log corruption with zeroes
        # - GPS map jumping to 0.0, 0.0 (Null Island)
        # Showcase data receiving only in the 5 dashboard viewing pages (visualization). Make others to 0.
        current_page = self.stacked_widget.currentIndex()
        # if current_page not in [0, 6, 7, 8, 9]:
        #     data = data.copy()
        #     for k in data.keys():
        #         if k == 'mode':
        #             continue
        #         if isinstance(data[k], float):
        #             data[k] = 0.0
        #         elif isinstance(data[k], int):
        #             data[k] = 0
        
        # We received a valid data packet! Reset watchdog timer
        self.watchdog_timer.start()
        
        # Override data fields based on simulated alarm flags
        active_warnings = []
        
        if getattr(self, 'sim_gps_loss', False):
            data['satellites'] = 0
            data['latitude'] = 0.0
            data['longitude'] = 0.0
            active_warnings.append("CRITICAL: GPS SIGNAL LOSS (0 SATS)")
            # Flashing card styles for GPS loss
            if current_page == 0:
                self.cards["latitude"].set_alert(True, is_critical=True)
                self.cards["longitude"].set_alert(True, is_critical=True)
                self.cards["satellites"].set_alert(True, is_critical=True)
        else:
            if current_page == 0:
                self.cards["latitude"].reset_style()
                self.cards["longitude"].reset_style()
                self.cards["satellites"].reset_style()
            
        if getattr(self, 'sim_low_battery', False):
            active_warnings.append("CRITICAL: LOW BATTERY FAULT (<11.2V)")
            if current_page == 0:
                self.cards["soc"].set_alert(True, is_critical=True)
                self.cards["volts"].set_alert(True, is_critical=True)
        else:
            if current_page == 0:
                self.cards["roll"].reset_style()
                self.cards["pitch"].reset_style()
            
        # --- 1. Battery SOC Safety State Machine with Hysteresis ---
        soc_val = data.get('bms_soc', 100)
        if not hasattr(self, 'batt_state'):
            self.batt_state = "normal"
            
        if soc_val > 0:
            if self.batt_state == "normal":
                if soc_val <= 10:
                    self.batt_state = "critical"
                elif soc_val < 20:
                    self.batt_state = "warning"
            elif self.batt_state == "warning":
                if soc_val <= 10:
                    self.batt_state = "critical"
                elif soc_val >= 22:
                    self.batt_state = "normal"
            elif self.batt_state == "critical":
                if soc_val >= 12:
                    if soc_val >= 22:
                        self.batt_state = "normal"
                    else:
                        self.batt_state = "warning"
        else:
            self.batt_state = "normal"
                    
        # Apply battery card styles and warnings
        if self.batt_state == "warning":
            active_warnings.append("LOW BATTERY WARNING: Battery SOC is below 20%!")
            if current_page == 0:
                self.cards["soc"].set_alert(True, is_critical=False)
                self.cards["volts"].set_alert(True, is_critical=False)
        elif self.batt_state == "critical":
            active_warnings.append("CRITICAL BATTERY FAILSAFE (SOC <= 10%)! All thrusters, camera & light disabled.")
            if current_page == 0:
                self.cards["soc"].set_alert(True, is_critical=True)
                self.cards["volts"].set_alert(True, is_critical=True)
        else:
            if current_page == 0:
                self.cards["soc"].reset_style()
                self.cards["volts"].reset_style()

        # --- 2. Chamber Temperature Safety State Machine with Hysteresis ---
        temp_val = data.get('chamber_temp', 0.0)
        if not hasattr(self, 'temp_state'):
            self.temp_state = "normal"
            
        if self.temp_state == "normal":
            if temp_val > 55.0:
                self.temp_state = "critical"
            elif temp_val > 48.0:
                self.temp_state = "warning"
        elif self.temp_state == "warning":
            if temp_val > 55.0:
                self.temp_state = "critical"
            elif temp_val <= 46.5:
                self.temp_state = "normal"
        elif self.temp_state == "critical":
            if temp_val <= 53.0:
                if temp_val <= 46.5:
                    self.temp_state = "normal"
                else:
                    self.temp_state = "warning"
                    
        # Apply temperature card styles and warnings
        if self.temp_state == "warning":
            active_warnings.append("HIGH CHAMBER TEMP WARNING: Temperature is > 48°C!")
            if current_page == 0:
                self.cards["chamber_temp"].set_alert(True, is_critical=False)
        elif self.temp_state == "critical":
            active_warnings.append("CRITICAL OVERHEATING FAILSAFE (Chamber Temp > 55°C)! Emergency shutdown activated.")
            if current_page == 0:
                self.cards["chamber_temp"].set_alert(True, is_critical=True)
        else:
            if current_page == 0:
                self.cards["chamber_temp"].reset_style()

        # --- 3. Evaluate Failsafe Actions & Enforce Limits ---
        is_now_failsafe = (self.batt_state == "critical" or self.temp_state == "critical")
        if is_now_failsafe:
            if not getattr(self, 'failsafe_active', False):
                self.failsafe_active = True
                print("[SAFETY] Critical failsafe active! Zeroing thrusters and turning off relays.")
            
            # Force relays OFF
            if self.light_state == 1:
                self.update_light_ui_state(False)
            if self.camera_state == 1:
                self.update_camera_ui_state(False)
            if self.telemetry_thread and self.telemetry_thread.isRunning():
                self.telemetry_thread.light_state = 0
                self.telemetry_thread.camera_state = 0
                
            # Zero output fields to neutral
            for rc_key in ["rc1", "rc2", "rc3"]:
                if rc_key in self.rc_output_fields:
                    self.rc_output_fields[rc_key].setText("1500")
            # Immediately transmit neutral commands to node
            self.send_immediate_cmd()
        else:
            if getattr(self, 'failsafe_active', False):
                self.failsafe_active = False
                print("[SAFETY] Failsafe released. Resuming normal operations.")
                
        # Enable or disable UI toggles based on failsafe state
        if hasattr(self, 'chk_light') and self.chk_light:
            self.chk_light.setEnabled(not self.failsafe_active)
        if hasattr(self, 'chk_camera') and self.chk_camera:
            self.chk_camera.setEnabled(not self.failsafe_active)

        # --- 4. Asynchronous Sound Triggers ---
        import time
        now_time = time.time()
        
        def play_warning_sound(freq, is_critical):
            try:
                import winsound
                if is_critical:
                    winsound.Beep(freq, 120)
                    import time
                    time.sleep(0.08)
                    winsound.Beep(freq, 120)
                else:
                    winsound.Beep(freq, 150)
            except:
                pass

        # Temperature Sound Play
        if self.temp_state != "normal":
            is_crit = (self.temp_state == "critical")
            freq = 1800 if is_crit else 1200
            cooldown = 3.0 if is_crit else 5.0
            if not hasattr(self, '_last_temp_sound_time'):
                self._last_temp_sound_time = 0.0
            if now_time - self._last_temp_sound_time >= cooldown:
                self._last_temp_sound_time = now_time
                import threading
                threading.Thread(target=play_warning_sound, args=(freq, is_crit), daemon=True).start()

        # Battery Sound Play (staggered slightly if temp warning is also active to avoid conflicts)
        if self.batt_state != "normal":
            is_crit = (self.batt_state == "critical")
            freq = 1600 if is_crit else 1000
            cooldown = 3.0 if is_crit else 5.0
            if not hasattr(self, '_last_batt_sound_time'):
                self._last_batt_sound_time = 0.0
            delay = 0.5 if (self.temp_state != "normal") else 0.0
            if now_time - self._last_batt_sound_time >= cooldown:
                self._last_batt_sound_time = now_time
                def delayed_play():
                    if delay > 0:
                        import time
                        time.sleep(delay)
                    play_warning_sound(freq, is_crit)
                import threading
                threading.Thread(target=delayed_play, daemon=True).start()

        # Update Warning Banner
        if active_warnings:
            warn_text = "  |  ".join(active_warnings)
            self.lbl_warning_banner.setText(f"SYSTEM WARNINGS: {warn_text}")
            self.warning_banner_frame.setVisible(True)
            if not self.warning_flash_timer.isActive():
                self.warning_flash_timer.start()
        else:
            self.warning_banner_frame.setVisible(False)
            self.warning_flash_timer.stop()

        # Update Top Bar Alert Badge (Option 2: Pulsing Pill)
        if active_warnings:
            has_crit = (getattr(self, 'batt_state', 'normal') == 'critical' or getattr(self, 'temp_state', 'normal') == 'critical')
            alert_count = len(active_warnings)
            badge_text = f"{alert_count} FAILSAFE" if has_crit else f"{alert_count} ALERTS"
            self.top_bar.alert_badge.setText(badge_text)
            self.top_bar.alert_badge.setVisible(True)
            
            border_color = "#FF4757" if has_crit else "#FF9F43"
            if self.warning_flash_state:
                bg_col = "#5C0B14" if has_crit else "#4F2F00"
            else:
                bg_col = "#2D080D" if has_crit else "#2A1800"
                
            self.top_bar.alert_badge.setStyleSheet(f"""
                QPushButton#TopBarAlertBadge {{
                    background-color: {bg_col};
                    border: 1px solid {border_color};
                    border-radius: 10px;
                    color: {border_color};
                    font-family: 'Google Sans', sans-serif;
                    font-size: 10px;
                    font-weight: bold;
                    padding: 2px 10px;
                }}
            """)
        else:
            self.top_bar.alert_badge.setVisible(False)

        # Trigger Slide-in Toast Notifications (Option 1)
        if not hasattr(self, '_prev_active_warnings'):
            self._prev_active_warnings = set()
            
        current_warnings_set = set(active_warnings)
        
        # New warnings trigger toasts
        new_warnings = current_warnings_set - self._prev_active_warnings
        for warn in new_warnings:
            is_crit = ("CRITICAL" in warn or "FAILSAFE" in warn)
            self.show_toast_alert(warn, is_crit)
            
        # Old resolved warnings close their toasts
        resolved_warnings = self._prev_active_warnings - current_warnings_set
        for warn in resolved_warnings:
            if hasattr(self, '_active_toasts') and warn in self._active_toasts:
                self._active_toasts[warn].close_toast()
                
        self._prev_active_warnings = current_warnings_set
        
        # Log telemetry data if enabled
        if hasattr(self, 'logging_enabled') and self.logging_enabled:
            try:
                if not self.log_file_path:
                    import time
                    timestamp_fn = time.strftime("asv_telemetry_%Y%m%d_%H%M%S.csv")
                    self.log_file_path = os.path.join(self.log_folder_path, timestamp_fn)
                    
                from PySide6.QtCore import QDateTime
                t_str = QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss.zzz")
                ct_val = data.get('chamber_temp', 0.0)
                bms_volt = data.get('bms_volt', 0.0)
                bms_curr = data.get('bms_curr', 0.0)
                bms_soc = data.get('bms_soc', 0)
                
                # Fetch PWM pulses (either from telemetry data packet or live Ground Station joystick outputs)
                pwm_rc1 = data.get('pwm_rc1', data.get('t1'))
                pwm_rc2 = data.get('pwm_rc2', data.get('t2'))
                pwm_rc3 = data.get('pwm_rc3', data.get('t3'))
                
                if pwm_rc1 is None or pwm_rc2 is None or pwm_rc3 is None:
                    try:
                        pwm_rc1 = int(self.rc_output_fields["rc1"].text())
                        pwm_rc2 = int(self.rc_output_fields["rc2"].text())
                        pwm_rc3 = int(self.rc_output_fields["rc3"].text())
                    except:
                        pwm_rc1 = pwm_rc1 if pwm_rc1 is not None else 1500
                        pwm_rc2 = pwm_rc2 if pwm_rc2 is not None else 1500
                        pwm_rc3 = pwm_rc3 if pwm_rc3 is not None else 1500

                # Magnetometer, sonar, and failsafe fields
                mag_x = float(data.get('mag_x') if data.get('mag_x') is not None else data.get('mx', 0.0) or 0.0)
                mag_y = float(data.get('mag_y') if data.get('mag_y') is not None else data.get('my', 0.0) or 0.0)
                mag_z = float(data.get('mag_z') if data.get('mag_z') is not None else data.get('mz', 0.0) or 0.0)
                mag_b = float(data.get('mag_b') if data.get('mag_b') is not None else data.get('magB', 0.0) or 0.0)
                sr_val = data.get('sonar_range') if data.get('sonar_range') is not None else data.get('distance')
                sonar_rng = float(sr_val) if sr_val is not None else 0.0
                sb_val = data.get('sonar_bearing')
                sonar_brg = float(sb_val) if sb_val is not None else 0.0
                bms_soh = int(data.get('bms_soh', 100) or 100)
                failsafe_val = 1 if data.get('failsafe', False) else 0
                cam_val = 1 if (data.get('camera_status', data.get('camera_state', data.get('camera', 0))) or getattr(self, 'camera_state', 0)) else 0
                light_val = 1 if (data.get('light_status', data.get('light_state', data.get('light', 0))) or getattr(self, 'light_state', 0)) else 0

                dist_val = float(data.get('distance') or 0.0)
                ms5837_press = data.get('ms5837_press')
                ms5837_temp = data.get('ms5837_temp')
                ms5837_depth = data.get('ms5837_depth')
                if ms5837_press is None:
                    ms5837_press = 1.013 + (15.0 - dist_val) * 0.1
                if ms5837_temp is None:
                    ms5837_temp = 22.6 + math.sin(self._docking_sonar_angle * 0.05) * 0.2
                if ms5837_depth is None:
                    ms5837_depth = max(0.0, (15.0 - dist_val))

                ss_val = data.get('sonar_scan_angle')
                sonar_scan = float(ss_val) if ss_val is not None else 0.0
                dock_val = 1 if (data.get('docking', 0) or getattr(self, 'is_docking', False)) else 0
                dock_cmd_str = data.get('dock_cmd', 'NULL')
                rec_val = 1 if (data.get('rec', 0) or getattr(self, '_cam_rec_active', False)) else 0

                alert_str = ";".join(active_warnings) if active_warnings else "NONE"
                raw_packet_csv = str(data.get('raw_packet', '')).replace('"', '""')
                log_line = (f"{t_str},{data.get('roll', 0.0):.2f},{data.get('pitch', 0.0):.2f},{data.get('yaw', 0.0):.2f},"
                            f"{data.get('latitude', data.get('lat', 0.0)):.7f},{data.get('longitude', data.get('lon', 0.0)):.7f},{data.get('satellites', 0)},"
                            f"{ct_val:.2f},{data.get('chamber_hum', 0.0):.2f},"
                            f"{bms_volt:.2f},{bms_curr:.3f},{bms_soc},{bms_soh},"
                            f"{data.get('bms_temp', 0.0):.2f},"
                            f"{pwm_rc1},{pwm_rc2},{pwm_rc3},"
                            f"{light_val},{cam_val},{failsafe_val},"
                            f"{mag_x:.2f},{mag_y:.2f},{mag_z:.2f},{mag_b:.2f},"
                            f"{sonar_rng:.2f},{sonar_brg:.1f},{sonar_scan:.1f},"
                            f"{dock_val},{dock_cmd_str},{rec_val},"
                            f"\"{alert_str}\",\"{raw_packet_csv}\"\n")
                write_header = not os.path.exists(self.log_file_path)
                with open(self.log_file_path, "a", encoding="utf-8") as f:
                    if write_header:
                        f.write("TIMESTAMP,ROLL,PITCH,YAW,LATITUDE,LONGITUDE,SATELLITES,CHAMBER_TEMP,CHAMBER_HUMIDITY,BMS_VOLT,BMS_CURR,BMS_SOC,BMS_SOH,BATT_TEMP,PWM_RC1,PWM_RC2,PWM_RC3,LIGHT_ON,CAMERA_ON,FAILSAFE,MAG_X,MAG_Y,MAG_Z,MAG_B,SONAR_RANGE,SONAR_BEARING,SONAR_SCAN_ANGLE,DOCKING,DOCK_CMD,REC,ALERT_STATUS,RAW_PACKET\n")
                    f.write(log_line)
                    f.flush()
            except Exception as e:
                print(f"Error writing telemetry to log file: {e}")
        
        # Rate-limit all GUI rendering updates to 10Hz (once every 100ms)
        import time
        now_time = time.time()
        if not hasattr(self, '_last_gui_update_time'):
            self._last_gui_update_time = 0.0
        if now_time - self._last_gui_update_time < 0.1:
            # Return early (watchdog and telemetry logger still run at full rate above)
            return
        self._last_gui_update_time = now_time

        # If UI is not in connected state, transition it now (green glow)
        if self.top_bar.status_state != "connected":
            port_label = self.telemetry_thread.port.upper() if (self.telemetry_thread and hasattr(self.telemetry_thread, 'port') and self.telemetry_thread.port) else "ONLINE"
            self.top_bar.set_connection_status("connected", f"{port_label} Active")
            if current_page == 0:
                for card in self.cards.values():
                    card.set_status("connected")
        
        # Always update top bar statistics at 10Hz
        self.top_bar.set_gps_count(original_gps)
        self.top_bar.set_gps_fix(valid_fix)
        self.top_bar.set_battery_percentage(original_soc)
        self.top_bar.set_chamber_temp(data.get('chamber_temp', 0.0))
        
        # Extract tri-axial magnetometer & Ping360 sonar parameters
        mx_val = float(data.get('magx', data.get('magX', data.get('mx', data.get('mag_x', 0.0)))) or 0.0)
        my_val = float(data.get('magy', data.get('magY', data.get('my', data.get('mag_y', 0.0)))) or 0.0)
        mz_val = float(data.get('magz', data.get('magZ', data.get('mz', data.get('mag_z', 0.0)))) or 0.0)
        magb_val = float(data.get('magB', data.get('mag_total', math.sqrt(mx_val**2 + my_val**2 + mz_val**2))) or 0.0)
        
        sonar_r = data.get('sonar_range', data.get('dist'))
        sonar_b = data.get('sonar_bearing', data.get('bearing'))
        sonar_s = data.get('sonar_scan_angle', data.get('scan_angle'))

        # Only update Dashboard-specific widgets if Dashboard is the active tab
        if current_page == 0:
            # Update AHRS cards
            self.cards["roll"].set_value(f"{data['roll']:.2f}")
            self.cards["pitch"].set_value(f"{data['pitch']:.2f}")
            self.cards["yaw"].set_value(f"{data['yaw']:.2f}")
            
            # Update GPS cards
            self.cards["latitude"].set_value(f"{abs(data['latitude']):.7f}")
            self.cards["latitude"].unit_label.setText("N" if data['latitude'] >= 0 else "S")
            
            self.cards["longitude"].set_value(f"{abs(data['longitude']):.7f}")
            self.cards["longitude"].unit_label.setText("E" if data['longitude'] >= 0 else "W")
            
            self.cards["satellites"].set_value(data['satellites'])
            
            # Update Chamber Environment cards
            self.cards["chamber_temp"].set_value(f"{data.get('chamber_temp', 0.0):.2f}")
            self.cards["chamber_hum"].set_value(f"{data.get('chamber_hum', 0.0):.2f}")
            
            # Update BMS cards
            bms_volt = data.get('bms_volt', 0.0)
            bms_curr = data.get('bms_curr', 0.0)
            bms_watts = data.get('bms_power', round(bms_volt * bms_curr, 2))
            bms_soc = data.get('bms_soc', 0)
            bms_soh = data.get('bms_soh', 100)
            bms_temp = data.get('bms_temp', 25.0)
            self.cards["volts"].set_value(f"{bms_volt:.2f}")
            self.cards["amps"].set_value(f"{bms_curr:.3f}")
            self.cards["watts"].set_value(f"{bms_watts:.1f}")
            self.cards["batt_temp"].set_value(f"{bms_temp:.1f}")
            self.cards["soc"].set_value(bms_soc)
            self.cards["soh"].set_value(bms_soh)
            
            # Update Actuators & Relays cards
            pwm_rc1_val = data.get('pwm_rc1', 1500)
            pwm_rc2_val = data.get('pwm_rc2', 1500)
            pwm_rc3_val = data.get('pwm_rc3', 1500)
            self.cards["p1"].set_value(pwm_rc1_val)
            self.cards["p2"].set_value(pwm_rc2_val)
            self.cards["p3"].set_value(pwm_rc3_val)
            
            fs_val = data.get('failsafe', 0)
            self.cards["failsafe"].set_value("FAILSAFE" if fs_val else "NORMAL")
            self.cards["failsafe"].set_status("critical" if fs_val else "normal")
            
            # Update Light & Camera button toggle states from telemetry feedback
            if ('light' in data or 'light_status' in data) and hasattr(self, 'dash_btn_light'):
                lit = bool(data.get('light_status', data.get('light', 0)))
                if self.dash_btn_light.isChecked() != lit:
                    self.update_light_ui_state(lit)
            if ('camera' in data or 'camera_status' in data) and hasattr(self, 'dash_btn_camera'):
                cam = bool(data.get('camera_status', data.get('camera', 0)))
                if self.dash_btn_camera.isChecked() != cam:
                    self.update_camera_ui_state(cam)
                    
            if 'is_armed' in data:
                self.is_armed = bool(data['is_armed'])

            # Update GPS Docking state from telemetry feedback
            if 'docking' in data and hasattr(self, 'btn_manual_dock'):
                dock_val = bool(data['docking'])
                if getattr(self, 'is_docking', False) != dock_val:
                    self.update_docking_ui_state(dock_val)
                    
            # Update Camera Recording state from telemetry feedback
            if 'rec' in data:
                backend_rec = bool(data['rec'])
                if not hasattr(self, '_cam_rec_active'):
                    self._cam_rec_active = False
                if self._cam_rec_active != backend_rec:
                    self._cam_rec_active = backend_rec
                    if backend_rec:
                        self._cam_rec_warming_up = False
                        if hasattr(self, '_cam_rec_warmup_timer') and self._cam_rec_warmup_timer:
                            self._cam_rec_warmup_timer.stop()
                        import time
                        self._cam_rec_start_time = time.time()
                        if not hasattr(self, '_cam_rec_timer') or self._cam_rec_timer is None:
                            self._cam_rec_timer = QTimer(self)
                            self._cam_rec_timer.setInterval(1000)
                            self._cam_rec_timer.timeout.connect(self._update_cam_rec_duration)
                        self._cam_rec_timer.start()
                    else:
                        if hasattr(self, '_cam_rec_timer') and self._cam_rec_timer:
                            self._cam_rec_timer.stop()
                    self.update_cam_rec_ui_state(backend_rec, data.get('rec_file', ''))
                elif 'rec_file' in data and data['rec_file']:
                    if hasattr(self, 'lbl_cam_rec_file'):
                        self.lbl_cam_rec_file.setText(f"File: {os.path.basename(data['rec_file'])}")
            
            # Update instrument widgets
            self.compass_widget.set_yaw(data['yaw'])
            self.horizon_widget.set_attitude(data['roll'], data['pitch'])
            self.horizon_3d_widget.set_attitude(data['roll'], data['pitch'])
            
            # Update custom visual gauges
            self.battery_gauge.set_soc(bms_soc)
            self.actuators_gauge.set_values(pwm_rc1_val, pwm_rc2_val, pwm_rc3_val)
            
            # Update dynamic scrolling plots
            self.attitude_chart.append_data([data['roll'], data['pitch']])
            self.gps_chart.append_data([data['satellites']])
            self.env_chart.append_data([data.get('chamber_temp', 0.0), data.get('chamber_hum', 0.0)])
            self.power_chart.append_data([bms_volt, bms_curr])
            self.actuators_chart.append_data([pwm_rc1_val, pwm_rc2_val, pwm_rc3_val])
            
        # Update Docking Page Widgets (Page Index 4)
        if hasattr(self, 'docking_cards') and self.docking_cards:
            # Sonar cards
            if sonar_r is not None:
                self.docking_cards["sonar_range"].set_value(f"{sonar_r:.2f}")
                self.docking_cards["sonar_range"].set_status(
                    "critical" if sonar_r <= 2.0 else "warning" if sonar_r <= 5.0 else "normal"
                )
            else:
                self.docking_cards["sonar_range"].set_value("--")
                self.docking_cards["sonar_range"].set_status("normal")
                
            if sonar_b is not None:
                self.docking_cards["sonar_bearing"].set_value(f"{sonar_b:.1f}")
            else:
                self.docking_cards["sonar_bearing"].set_value("--")
                
            if sonar_s is not None:
                self.docking_cards["sonar_scan"].set_value(f"{sonar_s:.1f}")
            else:
                self.docking_cards["sonar_scan"].set_value("--")
                
            self.docking_cards["sonar_conf"].set_value(f"{int(data.get('confidence', 100))}")

        # Update Hardware Sonar Page widgets
        if hasattr(self, 'lbl_sonar_hw_angle') and self.lbl_sonar_hw_angle:
            self.lbl_sonar_hw_angle.setText(f"{sonar_s:.1f}°" if sonar_s is not None else "0.0°")
        if hasattr(self, 'lbl_sonar_hw_dist') and self.lbl_sonar_hw_dist:
            self.lbl_sonar_hw_dist.setText(f"{sonar_r:.2f} m" if sonar_r is not None else "Open Water")
        if hasattr(self, 'lbl_sonar_hw_bearing') and self.lbl_sonar_hw_bearing:
            self.lbl_sonar_hw_bearing.setText(f"{sonar_b:.1f}°" if sonar_b is not None else "—°")
        if hasattr(self, 'lbl_sonar_hw_conf') and self.lbl_sonar_hw_conf:
            self.lbl_sonar_hw_conf.setText(f"{int(data.get('confidence', 100))}%")
        telemetry_sonar_active = bool(data.get('sonar_active', False)) or (sonar_s is not None and sonar_s != 0.0) or (sonar_r is not None)
        if telemetry_sonar_active and not getattr(self, 'sonar_active', False):
            self.sonar_active = True
            self.update_sonar_ui_state(True)
        elif not telemetry_sonar_active and not getattr(self, '_user_toggled_sonar', False):
            if getattr(self, 'sonar_active', False):
                self.update_sonar_ui_state(False)
        is_sonar_active = getattr(self, 'sonar_active', False) or telemetry_sonar_active

        # Extract magnetic docking guidance & stage variables from telemetry
        dock_range = data.get('dock_range')
        dock_err = data.get('dock_err')
        dock_stage = str(data.get('dock_stage', 'STANDBY'))
        dock_guideline = str(data.get('dock_guideline', ''))
        dock_cal_prog = int(data.get('dock_cal_progress', 0) or 0)
        dock_base_ready = bool(data.get('dock_base_ready', False))
        dock_cmd = str(data.get('dock_cmd', 'NULL'))
        dock_axis = str(data.get('dock_axis', 'stall'))
        dock_mag = data.get('dock_mag')
        is_docking_active = bool(data.get('docking', 0)) or (dock_stage not in ("STANDBY", ""))

        if hasattr(self, 'docking_cards') and self.docking_cards:
            # Mag cards
            mx_fmt = f"{mx_val:.3f}" if abs(mx_val) < 1.0 else f"{mx_val:.1f}"
            my_fmt = f"{my_val:.3f}" if abs(my_val) < 1.0 else f"{my_val:.1f}"
            mz_fmt = f"{mz_val:.3f}" if abs(mz_val) < 1.0 else f"{mz_val:.1f}"
            magb_fmt = f"{magb_val:.3f}" if abs(magb_val) < 1.0 else f"{magb_val:.2f}"
            self.docking_cards["mx"].set_value(mx_fmt)
            self.docking_cards["my"].set_value(my_fmt)
            self.docking_cards["mz"].set_value(mz_fmt)
            self.docking_cards["magb"].set_value(magb_fmt)
            if "mag_heading" in self.docking_cards:
                magnetic_heading = math.degrees(math.atan2(my_val, mx_val))
                self.docking_cards["mag_heading"].set_value(f"{magnetic_heading:.2f}")
            
            # Approach cards: use magnetic docking range & alignment when active, else fallback to sonar

            if dock_range is not None:
                t_dist = float(dock_range)
            else:
                t_dist = sonar_r if sonar_r is not None else 0.0

            if dock_err is not None:
                a_angle = float(dock_err)
            else:
                a_angle = ((sonar_b - data['yaw'] + 180) % 360 - 180) if sonar_b is not None else 0.0

            lat_off = t_dist * math.sin(math.radians(a_angle))
            
            if "target_dist" in self.docking_cards:
                self.docking_cards["target_dist"].set_value(f"{t_dist:.2f}")
                self.docking_cards["align_angle"].set_value(f"{a_angle:+.1f}")
                self.docking_cards["lat_offset"].set_value(f"{lat_off:+.2f}")
            
            if dock_stage in ("APPROACH", "FINAL_APPROACH"):
                guidance_txt = "APPROACH" if dock_stage == "APPROACH" else "FINAL DOCK"
            elif dock_stage == "DOCKED":
                guidance_txt = "DOCKED"
            elif dock_stage == "HOMING_LOCKED":
                guidance_txt = "LOCKED"
            elif dock_stage == "BASELINE_READY_COIL_ON":
                guidance_txt = "COIL ON"
            elif dock_stage == "CALIBRATING_COIL_OFF":
                guidance_txt = "CALIBRATING"
            elif sonar_r is not None:
                if sonar_r <= 2.0 and abs(a_angle) <= 10.0:
                    guidance_txt = "LOCKED"
                elif sonar_r <= 5.0:
                    guidance_txt = "APPROACH"
                else:
                    guidance_txt = "TRACKING"
            else:
                guidance_txt = "STANDBY"
            if "guidance_status" in self.docking_cards:
                self.docking_cards["guidance_status"].set_value(guidance_txt)

        # Update GPS waypoint docking workflow and operator guidance.
        if hasattr(self, 'lbl_dock_guideline_stage') and self.lbl_dock_guideline_stage:
            if dock_stage == "APPROACH":
                self.lbl_dock_guideline_stage.setText(" APPROACH WAYPOINT ")
                self.lbl_dock_guideline_stage.setStyleSheet("background-color: #DBEAFE; color: #1D4ED8; font-family: 'Consolas', monospace; font-size: 9px; font-weight: bold; border-radius: 4px; padding: 2px 6px;")
            elif dock_stage == "FINAL_APPROACH":
                self.lbl_dock_guideline_stage.setText(" FINAL DOCK WAYPOINT ")
                self.lbl_dock_guideline_stage.setStyleSheet("background-color: #FEF3C7; color: #B45309; font-family: 'Consolas', monospace; font-size: 9px; font-weight: bold; border-radius: 4px; padding: 2px 6px;")
            elif dock_stage == "CALIBRATING_COIL_OFF":
                self.lbl_dock_guideline_stage.setText(" 1. COIL OFF (CALIBRATING) ")
                self.lbl_dock_guideline_stage.setStyleSheet("background-color: #FEF3C7; color: #B45309; font-family: 'Consolas', monospace; font-size: 9px; font-weight: bold; border-radius: 4px; padding: 2px 6px;")
            elif dock_stage == "BASELINE_READY_COIL_ON":
                self.lbl_dock_guideline_stage.setText(" 2. ENERGIZE COIL (ON) ")
                self.lbl_dock_guideline_stage.setStyleSheet("background-color: #DBEAFE; color: #1D4ED8; font-family: 'Consolas', monospace; font-size: 9px; font-weight: bold; border-radius: 4px; padding: 2px 6px;")
            elif dock_stage == "HOMING_LOCKED":
                self.lbl_dock_guideline_stage.setText(" 3. HOMING LOCKED ")
                self.lbl_dock_guideline_stage.setStyleSheet("background-color: #DCFCE7; color: #15803D; font-family: 'Consolas', monospace; font-size: 9px; font-weight: bold; border-radius: 4px; padding: 2px 6px;")
            elif dock_stage == "DOCKED":
                self.lbl_dock_guideline_stage.setText(" DOCKED ")
                self.lbl_dock_guideline_stage.setStyleSheet("background-color: #D1FAE5; color: #047857; font-family: 'Consolas', monospace; font-size: 9px; font-weight: bold; border-radius: 4px; padding: 2px 6px;")
            else:
                self.lbl_dock_guideline_stage.setText(" STANDBY ")
                self.lbl_dock_guideline_stage.setStyleSheet("background-color: #E2E8F0; color: #475569; font-family: 'Consolas', monospace; font-size: 9px; font-weight: bold; border-radius: 4px; padding: 2px 6px;")

        if hasattr(self, 'lbl_dock_guideline_banner') and self.lbl_dock_guideline_banner:
            if dock_guideline:
                self.lbl_dock_guideline_banner.setText(dock_guideline)
            elif dock_stage == "CALIBRATING_COIL_OFF":
                self.lbl_dock_guideline_banner.setText(f"PHASE 1: KEEP BEACON COIL OFF! Capturing Earth baseline ({dock_cal_prog}%)...")
            elif dock_stage == "BASELINE_READY_COIL_ON":
                self.lbl_dock_guideline_banner.setText("PHASE 2: EARTH BASELINE CAPTURED! TURN ON / ENERGIZE BEACON COIL NOW.")
            elif dock_stage == "HOMING_LOCKED":
                self.lbl_dock_guideline_banner.setText(f"PHASE 3: BEACON LOCKED ({dock_cmd}) -> Approaching docking cone...")
            elif dock_stage == "DOCKED":
                self.lbl_dock_guideline_banner.setText("PHASE 3 COMPLETE: VEHICLE DOCKED (<0.15m). Station holding active.")
            else:
                self.lbl_dock_guideline_banner.setText("DE-ENERGIZE BEACON COIL (OFF) BEFORE ENGAGING")

        if hasattr(self, 'lbl_dock_guideline_detail') and self.lbl_dock_guideline_detail:
            if dock_stage == "APPROACH":
                self.lbl_dock_guideline_detail.setText("GPS docking is navigating to the configured approach waypoint.")
            elif dock_stage == "FINAL_APPROACH":
                self.lbl_dock_guideline_detail.setText("Approach waypoint reached. GPS docking is navigating straight to the configured dock waypoint.")
            elif dock_stage == "CALIBRATING_COIL_OFF":
                self.lbl_dock_guideline_detail.setText("Step 1: Keep beacon coil DE-ENERGIZED (OFF) so vehicle captures 100 clean Earth field baseline samples. If coil is turned on early, baseline will be poisoned.")
            elif dock_stage == "BASELINE_READY_COIL_ON":
                self.lbl_dock_guideline_detail.setText("Step 2: Clean baseline is saved! Energize beacon coil power switch now. System will automatically lock once horizontal field reaches 5.0 uT.")
            elif dock_stage == "HOMING_LOCKED":
                self.lbl_dock_guideline_detail.setText("Step 3: Autonomous homing active. Driving T2 Yaw alignment & T1 Surge run-in into the receiver cone. Thruster commands allocated automatically.")
            elif dock_stage == "DOCKED":
                self.lbl_dock_guideline_detail.setText("Vehicle arrived inside docking station (<0.15m). Thrusters neutral/station hold. Safe to disengage or initiate latch.")
            else:
                self.lbl_dock_guideline_detail.setText("Phase 1: Keep beacon coil OFF so the system can record 100 clean Earth field samples. Energizing the coil too early poisons the baseline and inverts steering.")

        # Update 3-step chips highlight
        def set_chip_style(chip, state):
            if not chip:
                return
            if state == "active":
                chip.setStyleSheet("background-color: #EFF6FF; color: #1D4ED8; border: 1.5px solid #3B82F6; border-radius: 4px; padding: 4px 4px; font-family: 'Google Sans', sans-serif; font-size: 8.5px; font-weight: bold;")
            elif state == "completed":
                chip.setStyleSheet("background-color: #F0FDF4; color: #15803D; border: 1px solid #86EFAC; border-radius: 4px; padding: 4px 4px; font-family: 'Google Sans', sans-serif; font-size: 8.5px; font-weight: bold;")
            else:
                chip.setStyleSheet("background-color: #FFFFFF; color: #94A3B8; border: 1px solid #CBD5E1; border-radius: 4px; padding: 4px 4px; font-family: 'Google Sans', sans-serif; font-size: 8.5px; font-weight: bold;")

        if hasattr(self, 'chip_dock_step1') and self.chip_dock_step1:
            if dock_stage == "CALIBRATING_COIL_OFF":
                set_chip_style(self.chip_dock_step1, "active")
            elif dock_base_ready or dock_stage in ("BASELINE_READY_COIL_ON", "HOMING_LOCKED", "DOCKED"):
                set_chip_style(self.chip_dock_step1, "completed")
            else:
                set_chip_style(self.chip_dock_step1, "inactive")

        if hasattr(self, 'chip_dock_step2') and self.chip_dock_step2:
            if dock_stage == "BASELINE_READY_COIL_ON":
                set_chip_style(self.chip_dock_step2, "active")
            elif dock_stage in ("HOMING_LOCKED", "DOCKED"):
                set_chip_style(self.chip_dock_step2, "completed")
            else:
                set_chip_style(self.chip_dock_step2, "inactive")

        if hasattr(self, 'chip_dock_step3') and self.chip_dock_step3:
            if dock_stage == "HOMING_LOCKED":
                set_chip_style(self.chip_dock_step3, "active")
            elif dock_stage == "DOCKED":
                set_chip_style(self.chip_dock_step3, "completed")
            else:
                set_chip_style(self.chip_dock_step3, "inactive")

        # Update Calibration Progress Bar
        if hasattr(self, 'bar_dock_cal') and self.bar_dock_cal:
            self.bar_dock_cal.setValue(dock_cal_prog)
            if dock_base_ready or dock_stage in ("BASELINE_READY_COIL_ON", "HOMING_LOCKED", "DOCKED"):
                self.bar_dock_cal.setFormat("100% (READY)")
                self.bar_dock_cal.setStyleSheet("""
                    QProgressBar {
                        background-color: #DCFCE7;
                        border: 1px solid #BBF7D0;
                        border-radius: 3px;
                        text-align: center;
                        font-size: 8.5px;
                        font-weight: bold;
                        color: #166534;
                    }
                    QProgressBar::chunk {
                        background-color: #22C55E;
                        border-radius: 2px;
                    }
                """)
            else:
                self.bar_dock_cal.setFormat("%p% captured")
                self.bar_dock_cal.setStyleSheet("""
                    QProgressBar {
                        background-color: #E2E8F0;
                        border: 1px solid #CBD5E1;
                        border-radius: 3px;
                        text-align: center;
                        font-size: 8.5px;
                        font-weight: bold;
                        color: #1E293B;
                    }
                    QProgressBar::chunk {
                        background-color: #0284C7;
                        border-radius: 2px;
                    }
                """)

        # Sync Engage button state with is_docking_active if changed externally
        if hasattr(self, 'btn_docking_engage') and hasattr(self, 'is_docking'):
            if is_docking_active != self.is_docking:
                self.update_docking_ui_state(is_docking_active)
            
        # Update Docking & Hardware Sonar Visualizers
        if is_sonar_active:
            import time
            if sonar_s is not None and (sonar_s != 0.0 or sonar_r is not None):
                self._last_hw_sonar_time = time.monotonic()
                scan_angle = sonar_s
            else:
                self._docking_sonar_angle = (getattr(self, '_docking_sonar_angle', 0.0) + 4.0) % 360.0
                scan_angle = self._docking_sonar_angle

            bins = [15 + int(6 * math.sin(i * 0.3)) for i in range(60)]
            if sonar_r is not None and sonar_r > 0:
                target_bin = min(59, max(0, int((sonar_r / 10.0) * 60)))
                for offset in (-2, -1, 0, 1, 2):
                    idx = target_bin + offset
                    if 0 <= idx < 60:
                        bins[idx] = max(bins[idx], int(255 - abs(offset) * 45))

            if hasattr(self, 'docking_sonar') and self.docking_sonar:
                self.docking_sonar.set_active(True)
                self.docking_sonar.set_target(sonar_r, sonar_b, int(data.get('confidence', 100)))
                self.docking_sonar.update_scan_line(scan_angle, getattr(self.docking_sonar, 'max_range', 10.0), bins)

            if hasattr(self, 'hw_sonar_widget') and self.hw_sonar_widget:
                self.hw_sonar_widget.set_active(True)
                self.hw_sonar_widget.set_target(sonar_r, sonar_b, int(data.get('confidence', 100)))
                self.hw_sonar_widget.update_scan_line(scan_angle, getattr(self.hw_sonar_widget, 'max_range', 10.0), bins)
        else:
            if hasattr(self, 'docking_sonar') and self.docking_sonar:
                self.docking_sonar.set_active(False)
            if hasattr(self, 'hw_sonar_widget') and self.hw_sonar_widget:
                self.hw_sonar_widget.set_active(False)

        if hasattr(self, 'hw_sonar_chart') and self.hw_sonar_chart:
            self.hw_sonar_chart.append_data([sonar_r if sonar_r is not None else 0.0, (sonar_s if sonar_s is not None else 0.0) / 25.0])
            
        if hasattr(self, 'lbl_sonar_profile_dist') and self.lbl_sonar_profile_dist:
            self.lbl_sonar_profile_dist.setText(f"Distance: {sonar_r:.2f} m" if sonar_r is not None else "Distance: — m")
        if hasattr(self, 'lbl_sonar_profile_angle') and self.lbl_sonar_profile_angle:
            self.lbl_sonar_profile_angle.setText(f"Scan Angle: {sonar_s:.1f}° / 25°" if sonar_s is not None else "Scan Angle: — / 25°")
            
        if hasattr(self, 'docking_mag_vector') and self.docking_mag_vector:
            self.docking_mag_vector.set_mag_values(mx_val, my_val, mz_val)
            
        if hasattr(self, 'docking_sonar_chart') and self.docking_sonar_chart:
            if sonar_r is not None:
                self.docking_sonar_chart.append_data([float(sonar_r)])
            
        if hasattr(self, 'docking_mag_chart') and self.docking_mag_chart:
            self.docking_mag_chart.append_data([mx_val, my_val, mz_val])

        # Update Docking Visual Scene
        if hasattr(self, 'docking_visual_widget') and self.docking_visual_widget:
            auv_lat = data.get('latitude', data.get('lat'))
            auv_lon = data.get('longitude', data.get('lon'))
            try:
                auv_lat = float(auv_lat) if auv_lat is not None else None
                auv_lon = float(auv_lon) if auv_lon is not None else None
                valid_auv_gps = (
                    auv_lat is not None and auv_lon is not None
                    and math.isfinite(auv_lat) and math.isfinite(auv_lon)
                    and -90.0 <= auv_lat <= 90.0 and -180.0 <= auv_lon <= 180.0
                    and not (auv_lat == 0.0 and auv_lon == 0.0)
                )
            except (TypeError, ValueError):
                valid_auv_gps = False
            self.current_latitude = auv_lat if valid_auv_gps else None
            self.current_longitude = auv_lon if valid_auv_gps else None
            try:
                self.current_heading = float(data.get('heading', data.get('yaw', data.get('course'))))
            except (TypeError, ValueError):
                self.current_heading = None
            try:
                self.current_depth = float(data.get('depth')) if data.get('depth') is not None else None
            except (TypeError, ValueError):
                self.current_depth = None
            try:
                self.current_pitch = float(data.get('pitch')) if data.get('pitch') is not None else None
            except (TypeError, ValueError):
                self.current_pitch = None
            self.docking_visual_widget.update_positions(
                station_lat=self.docking_station_latitude,
                station_lon=self.docking_station_longitude,
                auv_lat=auv_lat if valid_auv_gps else None,
                auv_lon=auv_lon if valid_auv_gps else None,
                heading=self.current_heading,
                depth=self.current_depth,
                pitch=self.current_pitch,
                station_heading=self.docking_station_heading
            )

        gps_distance = getattr(getattr(self, 'docking_visual_widget', None), 'distance', None)
        self._update_docking_stage_popup(
            data=data,
            docking_active=bool(getattr(self, 'is_docking', False) or is_docking_active),
            gps_distance=gps_distance,
            dock_range=dock_range,
            sonar_range=sonar_r,
            sonar_bearing=sonar_b,
            scan_angle=sonar_s,
            mx=mx_val,
            my=my_val,
            mz=mz_val
        )

        # Update Docking Checklist Rows with real telemetry
        if hasattr(self, 'lbl_dock_status_baseline_dot') and self.lbl_dock_status_baseline_dot:
            if dock_base_ready:
                self.lbl_dock_status_baseline_dot.setText("●")
                self.lbl_dock_status_baseline_dot.setStyleSheet("color: #00E676; font-size: 11px; font-weight: bold;")
                if hasattr(self, 'lbl_dock_status_baseline_val'):
                    self.lbl_dock_status_baseline_val.setText("READY")
                    self.lbl_dock_status_baseline_val.setStyleSheet("color: #00E676; font-size: 9.5px; font-weight: bold;")
            elif dock_stage == "CALIBRATING_COIL_OFF":
                self.lbl_dock_status_baseline_dot.setText("○")
                self.lbl_dock_status_baseline_dot.setStyleSheet("color: #F59E0B; font-size: 11px; font-weight: bold;")
                if hasattr(self, 'lbl_dock_status_baseline_val'):
                    self.lbl_dock_status_baseline_val.setText(f"{dock_cal_prog}%")
                    self.lbl_dock_status_baseline_val.setStyleSheet("color: #F59E0B; font-size: 9.5px; font-weight: bold;")
            else:
                self.lbl_dock_status_baseline_dot.setText("●")
                self.lbl_dock_status_baseline_dot.setStyleSheet("color: #94A3B8; font-size: 11px; font-weight: bold;")
                if hasattr(self, 'lbl_dock_status_baseline_val'):
                    self.lbl_dock_status_baseline_val.setText("STANDBY")
                    self.lbl_dock_status_baseline_val.setStyleSheet("color: #94A3B8; font-size: 9.5px; font-weight: bold;")

        if hasattr(self, 'lbl_dock_status_mag_dot') and self.lbl_dock_status_mag_dot:
            is_locked = dock_stage in ("HOMING_LOCKED", "DOCKED") or (dock_mag is not None and dock_mag >= 5.0)
            if is_locked:
                self.lbl_dock_status_mag_dot.setText("●")
                self.lbl_dock_status_mag_dot.setStyleSheet("color: #00E676; font-size: 11px; font-weight: bold;")
                if hasattr(self, 'lbl_dock_status_mag_val'):
                    mag_str = f"LOCKED ({dock_mag:.1f}µT)" if dock_mag is not None else "LOCKED"
                    self.lbl_dock_status_mag_val.setText(mag_str)
                    self.lbl_dock_status_mag_val.setStyleSheet("color: #00E676; font-size: 9.5px; font-weight: bold;")
            elif dock_stage == "BASELINE_READY_COIL_ON":
                self.lbl_dock_status_mag_dot.setText("○")
                self.lbl_dock_status_mag_dot.setStyleSheet("color: #3B82F6; font-size: 11px; font-weight: bold;")
                if hasattr(self, 'lbl_dock_status_mag_val'):
                    self.lbl_dock_status_mag_val.setText("WAITING COIL")
                    self.lbl_dock_status_mag_val.setStyleSheet("color: #3B82F6; font-size: 9.5px; font-weight: bold;")
            else:
                has_mag = (mx_val != 0.0 or my_val != 0.0 or mz_val != 0.0)
                self.lbl_dock_status_mag_dot.setText("●")
                self.lbl_dock_status_mag_dot.setStyleSheet("color: #94A3B8; font-size: 11px; font-weight: bold;")
                if hasattr(self, 'lbl_dock_status_mag_val'):
                    self.lbl_dock_status_mag_val.setText("ARMED" if has_mag else "INACTIVE")
                    self.lbl_dock_status_mag_val.setStyleSheet("color: #94A3B8; font-size: 9.5px; font-weight: bold;")

        if hasattr(self, 'lbl_dock_status_sonar_dot') and self.lbl_dock_status_sonar_dot:
            has_sonar = is_sonar_active
            self.lbl_dock_status_sonar_dot.setText("●") if has_sonar else self.lbl_dock_status_sonar_dot.setText("○")
            self.lbl_dock_status_sonar_dot.setStyleSheet("color: #00E676; font-size: 11px; font-weight: bold;" if has_sonar else "color: #94A3B8; font-size: 11px; font-weight: bold;")
            if hasattr(self, 'lbl_dock_status_sonar_val'):
                self.lbl_dock_status_sonar_val.setText("ACTIVE" if has_sonar else "STANDBY")
                self.lbl_dock_status_sonar_val.setStyleSheet("color: #00E676; font-size: 9.5px; font-weight: bold;" if has_sonar else "color: #94A3B8; font-size: 9.5px; font-weight: bold;")

        if hasattr(self, 'lbl_dock_status_align_dot') and self.lbl_dock_status_align_dot:
            axis_txt = dock_axis.upper() if dock_axis else "STALL"
            if dock_stage in ("HOMING_LOCKED", "DOCKED"):
                self.lbl_dock_status_align_dot.setText("●")
                self.lbl_dock_status_align_dot.setStyleSheet("color: #00E676; font-size: 11px; font-weight: bold;")
                if hasattr(self, 'lbl_dock_status_align_val'):
                    self.lbl_dock_status_align_val.setText(axis_txt)
                    self.lbl_dock_status_align_val.setStyleSheet("color: #00E676; font-size: 9.5px; font-weight: bold;")
            else:
                self.lbl_dock_status_align_dot.setText("●")
                self.lbl_dock_status_align_dot.setStyleSheet("color: #94A3B8; font-size: 11px; font-weight: bold;")
                if hasattr(self, 'lbl_dock_status_align_val'):
                    self.lbl_dock_status_align_val.setText(axis_txt)
                    self.lbl_dock_status_align_val.setStyleSheet("color: #94A3B8; font-size: 9.5px; font-weight: bold;")

        if hasattr(self, 'lbl_dock_status_ready_dot') and self.lbl_dock_status_ready_dot:
            if dock_stage == "DOCKED":
                self.lbl_dock_status_ready_dot.setText("●")
                self.lbl_dock_status_ready_dot.setStyleSheet("color: #00E676; font-size: 11px; font-weight: bold;")
                if hasattr(self, 'lbl_dock_status_ready_val'):
                    self.lbl_dock_status_ready_val.setText("DOCKED (<0.15m)")
                    self.lbl_dock_status_ready_val.setStyleSheet("color: #00E676; font-size: 9.5px; font-weight: bold;")
            elif dock_range is not None:
                self.lbl_dock_status_ready_dot.setText("○")
                self.lbl_dock_status_ready_dot.setStyleSheet("color: #0284C7; font-size: 11px; font-weight: bold;")
                if hasattr(self, 'lbl_dock_status_ready_val'):
                    self.lbl_dock_status_ready_val.setText(f"{float(dock_range):.2f} m")
                    self.lbl_dock_status_ready_val.setStyleSheet("color: #0284C7; font-size: 9.5px; font-weight: bold;")
            else:
                self.lbl_dock_status_ready_dot.setText("●")
                self.lbl_dock_status_ready_dot.setStyleSheet("color: #94A3B8; font-size: 11px; font-weight: bold;")
                if hasattr(self, 'lbl_dock_status_ready_val'):
                    self.lbl_dock_status_ready_val.setText("SEARCHING")
                    self.lbl_dock_status_ready_val.setStyleSheet("color: #94A3B8; font-size: 9.5px; font-weight: bold;")

        # Log Stage Transitions
        if dock_stage != getattr(self, '_last_dock_stage_logged', None):
            self._last_dock_stage_logged = dock_stage
            if hasattr(self, 'append_docking_log'):
                import time as _t
                t_now = _t.strftime("%H:%M:%S")
                if dock_stage == "CALIBRATING_COIL_OFF":
                    self.append_docking_log(t_now, "INFO", "DOCK", "Phase 1: Capturing Earth field baseline. Coil must be OFF.")
                elif dock_stage == "BASELINE_READY_COIL_ON":
                    self.append_docking_log(t_now, "OK", "DOCK", "Phase 2: Earth baseline captured! Energize beacon coil now.")
                elif dock_stage == "HOMING_LOCKED":
                    self.append_docking_log(t_now, "OK", "DOCK", "Phase 3: Magnetic beacon locked! Autonomous homing in progress.")
                elif dock_stage == "DOCKED":
                    self.append_docking_log(t_now, "SUCCESS", "DOCK", "Docking complete! Vehicle inside receiver station.")
                elif dock_stage == "STANDBY":
                    self.append_docking_log(t_now, "INFO", "DOCK", "Magnetic docking guidance standby.")
                


        # Store only the newest valid GPS fix.  A dedicated 5 Hz timer renders it
        # to WebEngine, which coalesces telemetry bursts and prevents JS backlog.
        try:
            _lat = float(data.get('latitude', 0.0))
            _lon = float(data.get('longitude', 0.0))
            _yaw = float(data.get('yaw', 0.0))
            if (math.isfinite(_lat) and math.isfinite(_lon) and math.isfinite(_yaw)
                    and -90.0 <= _lat <= 90.0 and -180.0 <= _lon <= 180.0
                    and (abs(_lat) > 0.0001 or abs(_lon) > 0.0001)):
                self._latest_map_fix = (_lat, _lon, _yaw % 360.0)
        except (TypeError, ValueError):
            pass
            
        # Update map overlay instruments if they exist
        if current_page in [1, 2]:
            if hasattr(self, 'map_compass'):
                self.map_compass.set_yaw(data['yaw'])
            if hasattr(self, 'map_horizon'):
                self.map_horizon.set_attitude(data['roll'], data['pitch'])

        # Check waypoint proximity if mission is active
        if self.mission_active and self.planned_waypoints:
            # Update vehicle speed calculation
            import time
            now_time = time.time()
            if getattr(self, 'last_lat', None) is not None and getattr(self, 'last_lon', None) is not None and getattr(self, 'last_time', None) is not None:
                dt = now_time - self.last_time
                if dt > 0.1:
                    ds = self.haversine_distance(self.last_lat, self.last_lon, data['latitude'], data['longitude'])
                    calc_speed = ds / dt
                    if calc_speed < 10.0: # filter anomalies
                        self.current_speed = 0.8 * self.current_speed + 0.2 * calc_speed
            self.last_lat = data['latitude']
            self.last_lon = data['longitude']
            self.last_time = now_time

            if self.current_wp_idx < len(self.planned_waypoints):
                target_lat, target_lon = self.planned_waypoints[self.current_wp_idx]
                dist = self.haversine_distance(data['latitude'], data['longitude'], target_lat, target_lon)
                
                # Update Mission Profiler Readouts
                wp_label = "HOME" if self.current_wp_idx == 0 else f"WP {self.current_wp_idx}"
                if hasattr(self, 'lbl_profiler_target'):
                    self.lbl_profiler_target.setText(wp_label)
                if hasattr(self, 'lbl_profiler_dist'):
                    self.lbl_profiler_dist.setText(f"{dist:.1f} m")
                
                # Calculate ETA (based on self.current_speed)
                if hasattr(self, 'lbl_profiler_eta'):
                    if self.current_speed > 0.1:
                        eta_sec = dist / self.current_speed
                        if eta_sec < 60.0:
                            self.lbl_profiler_eta.setText(f"{int(eta_sec)} s")
                        else:
                            self.lbl_profiler_eta.setText(f"{int(eta_sec // 60)}m {int(eta_sec % 60)}s")
                    else:
                        self.lbl_profiler_eta.setText("-- (STATIONARY)")
                        
                # Calculate Remaining Route Distance
                if hasattr(self, 'lbl_profiler_route'):
                    route_rem = self.calculate_remaining_route_distance(data['latitude'], data['longitude'])
                    self.lbl_profiler_route.setText(f"{route_rem:.1f} m")

                # --- AUTONOMOUS NAVIGATION ---
        # Waypoint control is handled exclusively by sea_backend.py.
        # The UI only displays position/progress here. Sending $MANUAL from
        # the telemetry callback would be interpreted by the backend as a
        # manual override and would stop autonomous navigation.
                # Proximity check based on user-configured threshold
                if dist < self.wp_reach_threshold:
                    wp_label = "HOME" if self.current_wp_idx == 0 else f"WP {self.current_wp_idx}"
                    print(f"[Mission Control] Reached waypoint: {wp_label}")
                    
                    # Update table item status
                    if self.current_wp_idx < self.wp_table.rowCount():
                        status_item = self.wp_table.item(self.current_wp_idx, 4)
                        if status_item:
                            status_item.setText("REACHED")
                            status_item.setForeground(QColor("#00E676")) # bright neon green
                            
                    # Update leaflet map marker style via runJavaScript
                    if hasattr(self, 'plan_web_view'):
                        self.plan_web_view.page().runJavaScript(f"reachWaypoint({self.current_wp_idx});")
                        
                    self.current_wp_idx += 1
                    
                    if self.current_wp_idx >= len(self.planned_waypoints):
                        print("[Mission Control] Autonomous mission completed successfully.")
                        self.stop_mission()
        else:
            # Reset speed state
            self.last_lat = None
            self.last_lon = None
            self.last_time = None
            self.current_speed = 0.0
            
            # Reset profiler labels
            if hasattr(self, 'lbl_profiler_target'):
                self.lbl_profiler_target.setText("--")
            if hasattr(self, 'lbl_profiler_dist'):
                self.lbl_profiler_dist.setText("--")
            if hasattr(self, 'lbl_profiler_eta'):
                self.lbl_profiler_eta.setText("--")
            if hasattr(self, 'lbl_profiler_route'):
                self.lbl_profiler_route.setText("--")

    def calculate_remaining_route_distance(self, current_lat, current_lon):
        if not self.planned_waypoints or self.current_wp_idx >= len(self.planned_waypoints):
            return 0.0
            
        # Distance from vehicle to active waypoint
        active_wp = self.planned_waypoints[self.current_wp_idx]
        total_dist = self.haversine_distance(current_lat, current_lon, active_wp[0], active_wp[1])
        
        # Sum of segments from active waypoint to subsequent ones
        for idx in range(self.current_wp_idx, len(self.planned_waypoints) - 1):
            wp1 = self.planned_waypoints[idx]
            wp2 = self.planned_waypoints[idx + 1]
            total_dist += self.haversine_distance(wp1[0], wp1[1], wp2[0], wp2[1])
            
        return total_dist

    @Slot(bool, str)
    def on_connection_status_changed(self, is_connected, message):
        # Thread reported connection closed/error
        if not is_connected:
            self.watchdog_timer.stop()
            self.log_file_path = None
            self.fusion_backend_state = {
                "connected": False, "valid": False, "range_m": None,
                "bearing_rel_deg": None, "sigma_m": None, "sources": "",
                "stage": "NO_FIX", "age_s": None, "timestamp": 0.0, "error": ""
            }
            self.top_bar.set_connection_status("disconnected", message)
            for card in self.cards.values():
                card.set_status("disconnected")
            self.port_refresh_timer.start(3000)
            self.clear_telemetry_data()
        else:
            # Port opened, but wait for first valid format message before showing Connected
            if self.top_bar.status_state != "connected":
                self.top_bar.set_connection_status("connecting", "Waiting Data")
                for card in self.cards.values():
                    card.set_status("connecting")
            
            # Synchronize the persisted navigation/docking precision values on every
            # connection so a restarted backend cannot fall back to stale defaults.
            # AHRS_OFFSET is intentionally NOT auto-sent here: the backend keeps its
            # own persistent heading calibration and reconnecting the UI must never
            # overwrite a deliberate $CALHEADING calibration.
            if self.telemetry_thread and self.telemetry_thread.isRunning():
                try:
                    import json as _json
                    from src.pages.setup_page import get_autopilot_config_path, DEFAULT_FRONTEND_AUTOPILOT_SETTINGS
                    cfg = dict(DEFAULT_FRONTEND_AUTOPILOT_SETTINGS)
                    cfg_path = get_autopilot_config_path()
                    if os.path.exists(cfg_path):
                        with open(cfg_path, "r", encoding="utf-8") as _fh:
                            saved_cfg = _json.load(_fh)
                        for _k, _v in saved_cfg.items():
                            if _k in cfg:
                                cfg[_k] = _v
                    sync_keys = (
                        "STEERING_DEADBAND", "LOS_LOOKAHEAD", "WAYPOINT_TOLERANCE",
                        "DOCK_LINE_LENGTH", "DOCK_LOOKAHEAD", "DOCK_SPEED_SCALE",
                        "DOCK_SLOWDOWN_DISTANCE", "DOCK_MIN_SPEED_SCALE", "DOCK_MIN_SURGE_PWM"
                    )
                    payload = "$CONFIG," + ",".join(f"{k}={cfg[k]}" for k in sync_keys)
                    self.telemetry_thread.write_data(payload)
                    self.wp_reach_threshold = float(cfg["WAYPOINT_TOLERANCE"])
                    print(f"[Mission Control] Synced navigation precision on connect: {payload}")
                except Exception as exc:
                    print(f"[Mission Control] Could not sync navigation precision: {exc}")

                # Ask the vehicle for the authoritative stabilization state. This
                # also works when the backend telemetry format is CSV rather than JSON.
                QTimer.singleShot(250, self.request_active_stabilization_status)
                # Verify the UI Sensor Fusion protocol on every connection.
                QTimer.singleShot(350, lambda: self.telemetry_thread.write_data("$FUSE,STATUS")
                                  if self.telemetry_thread and self.telemetry_thread.isRunning() else None)

    @Slot()
    def on_data_timeout(self):
        # Watchdog timeout: no valid telemetry parsed in 2 seconds
        # Drop UI state back to amber "NO DATA"
        self.top_bar.set_connection_status("connecting", "NO DATA")
        for card in self.cards.values():
            card.set_status("connecting")
        self.clear_telemetry_data()

    def update_active_stabilization_ui_state(self, enabled, mode="OFF"):
        """Render the backend-confirmed Active Stabilization state."""
        enabled = bool(enabled)
        mode = str(mode or ("ON" if enabled else "OFF")).upper()
        self.active_stabilization = enabled
        self.active_stabilization_mode = mode
        if hasattr(self, 'stabilization_status_timer'):
            if enabled and not self.stabilization_status_timer.isActive():
                self.stabilization_status_timer.start()
            elif not enabled and self.stabilization_status_timer.isActive():
                self.stabilization_status_timer.stop()

        btn = getattr(self, 'btn_active_stabilization', None)
        lbl = getattr(self, 'lbl_active_stabilization_status', None)
        if btn is not None:
            btn.setChecked(enabled)
            btn.setText(f"ACTIVE STABILIZATION: {'ON' if enabled else 'OFF'}")
            if enabled:
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #0B6E4F; border: 1px solid #16A36D;
                        border-radius: 4px; padding: 8px 12px; color: #FFFFFF;
                        font-family: 'Google Sans', sans-serif; font-weight: bold; font-size: 11px;
                    }
                    QPushButton:hover { background-color: #10875F; border-color: #34D399; }
                    QPushButton:pressed { background-color: #07543D; }
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton {
                        background-color: #0F223C; border: 1px solid #1C3B65;
                        border-radius: 4px; padding: 8px 12px; color: #E2F1FF;
                        font-family: 'Google Sans', sans-serif; font-weight: bold; font-size: 11px;
                    }
                    QPushButton:hover { background-color: #1C3B65; border-color: #2D5A8F; color: #FFFFFF; }
                    QPushButton:pressed { background-color: #0A1625; }
                """)

        if lbl is not None:
            descriptions = {
                "OFF": "OFF • current rejection disabled",
                "PATH": "PATH • fixed-leg tracking + cross-current rejection",
                "HOLD": "HOLD • GPS station keeping active",
                "HOLD_RECOVER": "HOLD • correcting position drift",
                "HOLD_SENSOR_WAIT": "ON • waiting for fresh GPS/AHRS",
                "HOLD_TEENSY_WAIT": "ON • thruster link unavailable; neutral requested",
                "DISARMED": "ON • armed thrust disabled; will engage after ARM",
                "MANUAL_OVERRIDE": "OFF • cancelled by manual control",
                "PENDING": "Command sent • waiting for vehicle acknowledgement",
            }
            lbl.setText(descriptions.get(mode, f"{'ON' if enabled else 'OFF'} • {mode}"))
            lbl.setStyleSheet(
                ("color: #5BE7A9; font-size: 9px; font-weight: bold; margin-bottom: 4px;"
                 if enabled else
                 "color: #78909C; font-size: 9px; font-weight: bold; margin-bottom: 4px;")
            )

    def request_active_stabilization_status(self):
        if self.telemetry_thread and self.telemetry_thread.isRunning():
            self.telemetry_thread.write_data("$STABILIZE,STATUS")

    @Slot(bool, str)
    def handle_fusion_status(self, state):
        """Store the backend acknowledgement for the Sensor Fusion page."""
        if not isinstance(state, dict):
            return
        merged = dict(getattr(self, 'fusion_backend_state', {}))
        merged.update(state)
        merged['connected'] = True
        merged['timestamp'] = _time.monotonic()
        self.fusion_backend_state = merged

    def handle_stabilization_status(self, enabled, mode):
        previous = getattr(self, 'active_stabilization', False)
        previous_mode = getattr(self, 'active_stabilization_mode', 'OFF')
        self.update_active_stabilization_ui_state(enabled, mode)
        if bool(enabled) != bool(previous) or str(mode).upper() != str(previous_mode).upper():
            self.log_mission(
                f"Active Stabilization {'ON' if enabled else 'OFF'} | Mode: {str(mode).upper()}"
            )

    def toggle_active_stabilization(self, checked=False):
        """Operator ON/OFF switch for current rejection and GPS station hold."""
        desired = bool(checked)
        previous = getattr(self, 'active_stabilization', False)
        if not (self.telemetry_thread and self.telemetry_thread.isRunning()):
            self.update_active_stabilization_ui_state(previous, self.active_stabilization_mode)
            self.show_toast_alert("Connect to the vehicle before changing stabilization", is_critical=True)
            return

        command = "$STABILIZE,ON" if desired else "$STABILIZE,OFF"
        sent = self.telemetry_thread.write_data(command)
        if not sent and getattr(self.telemetry_thread, 'port', None) != "SIMULATOR":
            self.update_active_stabilization_ui_state(previous, self.active_stabilization_mode)
            self.show_toast_alert("Could not send Active Stabilization command", is_critical=True)
            return

        # Optimistic visual feedback; backend ACK/telemetry replaces PENDING with
        # PATH/HOLD/DISARMED/etc. as soon as the command is applied.
        self.update_active_stabilization_ui_state(desired, "PENDING" if desired else "OFF")
        self.log_mission(
            f"Active Stabilization {'enable' if desired else 'disable'} command sent."
        )

    def set_navigation_mode(self, mode):
        if mode == "manual":
            self.btn_manual.setChecked(True)
            self.btn_manual.setProperty("checked", "true")
            self.btn_auto.setChecked(False)
            self.btn_auto.setProperty("checked", "false")
            self.auto_actions_widget.setVisible(False)
            self.manual_actions_widget.setVisible(True)
            self.bottom_panel.setVisible(False)
            if hasattr(self, 'eta_panel') and self.eta_panel:
                self.eta_panel.setVisible(False)
            if hasattr(self, 'txt_mission_logs') and self.txt_mission_logs:
                self.txt_mission_logs.setVisible(False)
            if hasattr(self, 'plan_web_view') and self.plan_web_view:
                self.plan_web_view.page().runJavaScript("if (typeof setPlanningMode === 'function') { setPlanningMode(false); }")
            print("[Navigation Mode] Switched to MANUAL. Waypoints disabled.")
            self.log_mission("Navigation Mode switched to MANUAL.")
        else:
            self.btn_manual.setChecked(False)
            self.btn_manual.setProperty("checked", "false")
            self.btn_auto.setChecked(True)
            self.btn_auto.setProperty("checked", "true")
            self.auto_actions_widget.setVisible(True)
            self.manual_actions_widget.setVisible(False)
            self.bottom_panel.setVisible(True)
            if hasattr(self, 'eta_panel') and self.eta_panel:
                self.eta_panel.setVisible(True)
            if hasattr(self, 'txt_mission_logs') and self.txt_mission_logs:
                self.txt_mission_logs.setVisible(True)
            if hasattr(self, 'plan_splitter'):
                height = self.height()
                self.plan_splitter.setSizes([height - 200, 180])
            if hasattr(self, 'plan_web_view') and self.plan_web_view:
                self.plan_web_view.page().runJavaScript("if (typeof setPlanningMode === 'function') { setPlanningMode(true); }")
            print("[Navigation Mode] Switched to AUTOMATIC. Click map to add waypoints.")
            self.log_mission("Navigation Mode switched to AUTOMATIC.")
            
            # Disable ARM button unless waypoints have been uploaded & acknowledged
            self.btn_arm.setEnabled(self.wp_upload_acknowledged)
            
        # Re-apply styles
        self.btn_manual.style().unpolish(self.btn_manual)
        self.btn_manual.style().polish(self.btn_manual)
        self.btn_auto.style().unpolish(self.btn_auto)
        self.btn_auto.style().polish(self.btn_auto)

    def clear_planned_route(self):
        if hasattr(self, 'plan_web_view'):
            self.plan_web_view.page().runJavaScript("clearWaypoints();")
        print("[Waypoint Planner] Cleared waypoints from map.")

    def load_mission_file(self):
        from PySide6.QtWidgets import QFileDialog
        import json
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Mission File",
            "",
            "Mission Files (*.json);;All Files (*)"
        )
        if file_path:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    waypoints = json.load(f)
                if not isinstance(waypoints, list):
                    print("[Mission Load Error] Invalid mission format: expected list of coordinates.")
                    return
                
                # Clear existing route first
                if hasattr(self, 'plan_web_view'):
                    self.plan_web_view.page().runJavaScript("clearWaypoints();")
                
                self.planned_waypoints = []
                self.refresh_waypoints_table()
                
                # Plot each loaded waypoint on the Leaflet map
                if hasattr(self, 'plan_web_view'):
                    for pt in waypoints:
                        if isinstance(pt, list) and len(pt) >= 2:
                            lat, lon = round(float(pt[0]), 7), round(float(pt[1]), 7)
                            self.plan_web_view.page().runJavaScript(f"addWaypoint({lat:.7f}, {lon:.7f});")
                            
                print(f"[Mission Load] Mission loaded from: {file_path}")
            except Exception as e:
                print(f"[Mission Load Error] {e}")

    def save_mission_file(self):
        if not self.planned_waypoints:
            print("[Mission Save] No waypoints to save.")
            return
            
        from PySide6.QtWidgets import QFileDialog
        import json
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Mission File",
            "",
            "Mission Files (*.json);;All Files (*)"
        )
        if file_path:
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump([[round(float(lat), 7), round(float(lon), 7)] for lat, lon in self.planned_waypoints], f, indent=4)
                print(f"[Mission Save] Mission successfully saved to: {file_path}")
            except Exception as e:
                print(f"[Mission Save Error] {e}")

    def upload_planned_route(self):
        import time
        if not self.planned_waypoints:
            self.log_mission("Error: No waypoints to upload!")
            return
            
        self.wp_upload_acknowledged = False
        self.btn_arm.setEnabled(False)
        self.log_mission(f"Uploading {len(self.planned_waypoints)} waypoints to vessel...")
        
        # Send each waypoint via serial port
        for idx, (lat, lon) in enumerate(self.planned_waypoints):
            payload = f"$WP,{idx},{lat:.7f},{lon:.7f}"
            if self.telemetry_thread and self.telemetry_thread.isRunning():
                self.telemetry_thread.write_data(payload)
                time.sleep(0.05) # short buffer delay
            else:
                print(f"[Offline TX] {payload}")
                
        # Send end of route marker
        end_payload = f"$WP,END"
        if self.telemetry_thread and self.telemetry_thread.isRunning():
            self.telemetry_thread.write_data(end_payload)
        else:
            print(f"[Offline TX] {end_payload}")
            
        self.log_mission("Waypoint upload finished. Awaiting verification ACK from vessel...")
        print("[Waypoint Planner] Upload complete.")

    def handle_plan_console(self, message):
        if "[Waypoint] Added:" in message:
            try:
                coord_str = message.split("[Waypoint] Added:")[1].strip()
                lat_str, lon_str = coord_str.split(",")
                lat = round(float(lat_str), 7)
                lon = round(float(lon_str), 7)
                self.planned_waypoints.append((lat, lon))
                self.refresh_waypoints_table()
            except Exception as e:
                print(f"Error parsing waypoint: {e}")
        elif "[Waypoint] Cleared" in message:
            self.planned_waypoints = []
            self.refresh_waypoints_table()
        elif "[Waypoint] Moved:" in message:
            try:
                data_str = message.split("[Waypoint] Moved:")[1].strip()
                idx_str, lat_str, lon_str = data_str.split(",")
                idx = int(idx_str)
                lat = round(float(lat_str), 7)
                lon = round(float(lon_str), 7)
                if 0 <= idx < len(self.planned_waypoints):
                    self.planned_waypoints[idx] = (lat, lon)
                    self.refresh_waypoints_table()
            except Exception as e:
                print(f"Error handling moved waypoint: {e}")
        elif "[Waypoint] Inserted:" in message:
            try:
                data_str = message.split("[Waypoint] Inserted:")[1].strip()
                idx_str, lat_str, lon_str = data_str.split(",")
                idx = int(idx_str)
                lat = round(float(lat_str), 7)
                lon = round(float(lon_str), 7)
                self.planned_waypoints.insert(idx, (lat, lon))
                self.refresh_waypoints_table()
            except Exception as e:
                print(f"Error handling inserted waypoint: {e}")

    def refresh_waypoints_table(self):
        self.wp_table.setRowCount(0)
        n = len(self.planned_waypoints)
        for i in range(n):
            lat, lon = self.planned_waypoints[i]
            
            # Distance (m): segment from previous to current
            if i == 0:
                dist_str = "--"
            else:
                prev_lat, prev_lon = self.planned_waypoints[i - 1]
                dist = self.haversine_distance(prev_lat, prev_lon, lat, lon)
                dist_str = f"{dist:.1f}"
                
            # Offset (Bearing):
            if i == 0:
                if n > 1:
                    next_lat, next_lon = self.planned_waypoints[1]
                    bearing = self.calculate_bearing(lat, lon, next_lat, next_lon)
                    offset_str = f"{bearing:.1f} deg"
                else:
                    offset_str = "--"
            else:
                prev_lat, prev_lon = self.planned_waypoints[i - 1]
                bearing = self.calculate_bearing(prev_lat, prev_lon, lat, lon)
                offset_str = f"{bearing:.1f} deg"
                
            self.wp_table.insertRow(i)
            
            # 1. WP
            wp_name = "HOME" if i == 0 else f"WP {i}"
            item_name = QTableWidgetItem(wp_name)
            item_name.setFont(QFont("Google Sans", 9, QFont.Bold))
            self.wp_table.setItem(i, 0, item_name)
            
            # 2. LATITUDE
            self.wp_table.setItem(i, 1, QTableWidgetItem(f"{lat:.7f}"))
            
            # 3. LONGITUDE
            self.wp_table.setItem(i, 2, QTableWidgetItem(f"{lon:.7f}"))
            
            # 4. DIST (m)
            self.wp_table.setItem(i, 3, QTableWidgetItem(dist_str))
            
            # 5. STATUS
            item_status = QTableWidgetItem("HOME" if i == 0 else "WAIT")
            if i == 0:
                item_status.setForeground(QColor("#00E5FF")) # Cyan
                item_status.setFont(QFont("Google Sans", 9, QFont.Bold))
            else:
                item_status.setForeground(QColor("#A0A0A0"))
            self.wp_table.setItem(i, 4, item_status)
            
            # 6. DEL (✕)
            del_btn = QPushButton("✕")
            if i == 0:
                del_btn.setEnabled(False)
                del_btn.setStyleSheet("""
                    QPushButton {
                        background-color: transparent;
                        border: none;
                        color: #444444;
                        font-weight: bold;
                        font-size: 12px;
                    }
                """)
            else:
                del_btn.setStyleSheet("""
                    QPushButton {
                        background-color: transparent;
                        border: none;
                        color: #EF5350;
                        font-weight: bold;
                        font-size: 12px;
                    }
                    QPushButton:hover {
                        color: #FF1744;
                    }
                """)
                del_btn.clicked.connect(lambda checked=False, idx=i: self.delete_waypoint_at(idx))
            self.wp_table.setCellWidget(i, 5, del_btn)
            
            # 7. UP (▲)
            btn_up = QPushButton("▲")
            btn_up.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    border: none;
                    color: #A0A0A0;
                    font-weight: bold;
                    font-size: 11px;
                }
                QPushButton:hover {
                    color: #FF9100;
                }
                QPushButton:disabled {
                    color: #444444;
                }
            """)
            btn_up.setEnabled(i > 1) # HOME and WP 1 cannot move UP
            btn_up.clicked.connect(lambda checked=False, idx=i: self.move_waypoint_up(idx))
            self.wp_table.setCellWidget(i, 6, btn_up)
            
            # 8. DN (▼)
            btn_down = QPushButton("▼")
            btn_down.setStyleSheet("""
                QPushButton {
                    background-color: transparent;
                    border: none;
                    color: #A0A0A0;
                    font-weight: bold;
                    font-size: 11px;
                }
                QPushButton:hover {
                    color: #FF9100;
                }
                QPushButton:disabled {
                    color: #444444;
                }
            """)
            btn_down.setEnabled(i > 0 and i < n - 1) # HOME cannot move DN, last WP cannot move DN
            btn_down.clicked.connect(lambda checked=False, idx=i: self.move_waypoint_down(idx))
            self.wp_table.setCellWidget(i, 7, btn_down)
            
            # 9. OFFSET
            self.wp_table.setItem(i, 8, QTableWidgetItem(offset_str))
            
        for r in range(self.wp_table.rowCount()):
            for c in [0, 1, 2, 3, 4, 8]:
                item = self.wp_table.item(r, c)
                if item:
                    item.setTextAlignment(Qt.AlignCenter)
                    item.setFlags(item.flags() ^ Qt.ItemIsEditable)
                    
        self.update_eta_calculations()

    def update_eta_calculations(self):
        if not hasattr(self, 'lbl_eta_dist') or self.lbl_eta_dist is None:
            return
        total_dist = 0.0
        n = len(self.planned_waypoints)
        for i in range(1, n):
            lat1, lon1 = self.planned_waypoints[i - 1]
            lat2, lon2 = self.planned_waypoints[i]
            total_dist += self.haversine_distance(lat1, lon1, lat2, lon2)
            
        self.lbl_eta_dist.setText(f"Total Distance: {total_dist:.1f} m")
        
        # Get speed value
        speed_str = self.combo_cruise_speed.currentText().split(" ")[0]
        try:
            speed = float(speed_str)
        except:
            speed = 2.0
            
        if speed > 0:
            duration_s = total_dist / speed
            if duration_s >= 60:
                mins = int(duration_s // 60)
                secs = int(duration_s % 60)
                self.lbl_eta_time.setText(f"Est. Duration: {mins}m {secs}s")
            else:
                self.lbl_eta_time.setText(f"Est. Duration: {duration_s:.1f}s")
        else:
            self.lbl_eta_time.setText("Est. Duration: --")

    def haversine_distance(self, lat1, lon1, lat2, lon2):
        R = 6371000.0
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)
        a = math.sin(delta_phi/2.0)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(delta_lambda/2.0)**2
        c = 2.0*math.atan2(math.sqrt(a), math.sqrt(1.0-a))
        return R * c

    def calculate_bearing(self, lat1, lon1, lat2, lon2):
        lat1_rad = math.radians(lat1)
        lat2_rad = math.radians(lat2)
        dlon_rad = math.radians(lon2 - lon1)
        
        y = math.sin(dlon_rad) * math.cos(lat2_rad)
        x = math.cos(lat1_rad) * math.sin(lat2_rad) - math.sin(lat1_rad) * math.cos(lat2_rad) * math.cos(dlon_rad)
        
        bearing = math.atan2(y, x)
        bearing_deg = math.degrees(bearing)
        return (bearing_deg + 360.0) % 360.0

    def delete_waypoint_at(self, idx):
        if idx < 0 or idx >= len(self.planned_waypoints):
            return
        self.planned_waypoints.pop(idx)
        if hasattr(self, 'plan_web_view'):
            self.plan_web_view.page().runJavaScript(f"deleteWaypoint({idx});")
        print(f"[Waypoint Planner] Deleted waypoint at index: {idx}")
        self.refresh_waypoints_table()

    def move_waypoint_up(self, idx):
        if idx <= 0 or idx >= len(self.planned_waypoints):
            return
        self.planned_waypoints[idx], self.planned_waypoints[idx - 1] = \
            self.planned_waypoints[idx - 1], self.planned_waypoints[idx]
        if hasattr(self, 'plan_web_view'):
            self.plan_web_view.page().runJavaScript(f"swapWaypoints({idx}, {idx - 1});")
        print(f"[Waypoint Planner] Swapped waypoint {idx} UP to {idx - 1}")
        self.refresh_waypoints_table()

    def move_waypoint_down(self, idx):
        if idx < 0 or idx >= len(self.planned_waypoints) - 1:
            return
        self.planned_waypoints[idx], self.planned_waypoints[idx + 1] = \
            self.planned_waypoints[idx + 1], self.planned_waypoints[idx]
        if hasattr(self, 'plan_web_view'):
            self.plan_web_view.page().runJavaScript(f"swapWaypoints({idx}, {idx + 1});")
        print(f"[Waypoint Planner] Swapped waypoint {idx} DOWN to {idx + 1}")
        self.refresh_waypoints_table()

    def arm_vehicle(self):
        active_style = """
            QPushButton {
                background-color: #1C3B65;
                border: 1px solid #2D5A8F;
                border-radius: 4px;
                padding: 8px 12px;
                color: #FFFFFF;
                font-family: 'Google Sans', sans-serif;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #2D5A8F;
                border-color: #3B72B0;
                color: #FFFFFF;
            }
        """
        inactive_style = """
            QPushButton {
                background-color: #0F223C;
                border: 1px solid #1C3B65;
                border-radius: 4px;
                padding: 8px 12px;
                color: #E2F1FF;
                font-family: 'Google Sans', sans-serif;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #1C3B65;
                border-color: #2D5A8F;
                color: #FFFFFF;
            }
        """
        self.btn_arm.setStyleSheet(active_style)
        self.btn_disarm.setStyleSheet(inactive_style)
        if hasattr(self, 'btn_manual_arm'):
            self.btn_manual_arm.setStyleSheet(active_style)
        if hasattr(self, 'btn_manual_disarm'):
            self.btn_manual_disarm.setStyleSheet(inactive_style)
        self.is_armed = True
        
        # Send $ARM command - retry 5 times at 150ms intervals for UDP reliability
        if self.telemetry_thread and self.telemetry_thread.isRunning():
            print("[Mission Control] ARM button clicked - sending $ARM to Pi (x5 retries)...")
            def send_arm():
                if self.telemetry_thread and self.telemetry_thread.isRunning():
                    self.telemetry_thread.write_data("$ARM")
            send_arm()
            for delay_ms in [150, 300, 450, 600]:
                QTimer.singleShot(delay_ms, send_arm)
        else:
            print("[Mission Control WARNING] ARM clicked but telemetry thread is NOT running! Check connection.")
 
    def disarm_vehicle(self):
        active_style = """
            QPushButton {
                background-color: #1C3B65;
                border: 1px solid #2D5A8F;
                border-radius: 4px;
                padding: 8px 12px;
                color: #FFFFFF;
                font-family: 'Google Sans', sans-serif;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #2D5A8F;
                border-color: #3B72B0;
                color: #FFFFFF;
            }
        """
        inactive_style = """
            QPushButton {
                background-color: #0F223C;
                border: 1px solid #1C3B65;
                border-radius: 4px;
                padding: 8px 12px;
                color: #E2F1FF;
                font-family: 'Google Sans', sans-serif;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #1C3B65;
                border-color: #2D5A8F;
                color: #FFFFFF;
            }
        """
        self.btn_arm.setStyleSheet(inactive_style)
        self.btn_disarm.setStyleSheet(active_style)
        if hasattr(self, 'btn_manual_arm'):
            self.btn_manual_arm.setStyleSheet(inactive_style)
        if hasattr(self, 'btn_manual_disarm'):
            self.btn_manual_disarm.setStyleSheet(active_style)
        self.is_armed = False
        if getattr(self, 'is_docking', False):
            self.update_docking_ui_state(False)
        
        # Send $DISARM command - retry 5 times at 150ms intervals for UDP reliability
        if self.telemetry_thread and self.telemetry_thread.isRunning():
            print("[Mission Control] DISARM button clicked - sending $DISARM to Pi (x5 retries)...")
            def send_disarm():
                if self.telemetry_thread and self.telemetry_thread.isRunning():
                    self.telemetry_thread.write_data("$DISARM")
            send_disarm()
            for delay_ms in [150, 300, 450, 600]:
                QTimer.singleShot(delay_ms, send_disarm)
        else:
            print("[Mission Control WARNING] DISARM clicked but telemetry thread is NOT running! Check connection.")

    def set_docking_route(self):
        """Send docking station coordinates and approach heading to the vehicle."""
        dock_lat = self.spin_docking_latitude.value()
        dock_lon = self.spin_docking_longitude.value()
        dock_heading = self.spin_docking_heading.value()
        if dock_lat == 0.0 and dock_lon == 0.0:
            self.show_toast_alert("Enter valid docking station coordinates", is_critical=True)
            return

        command = f"$DOCK,SET,{dock_lat:.7f},{dock_lon:.7f},{dock_heading:.1f}"
        if not (self.telemetry_thread and self.telemetry_thread.isRunning()):
            self.show_toast_alert("Connect to the vehicle before setting the GPS docking route", is_critical=True)
            return
        sent = self.telemetry_thread.write_data(command)
        if not sent and getattr(self.telemetry_thread, 'port', None) != "SIMULATOR":
            self.show_toast_alert("Could not send the GPS docking route", is_critical=True)
            return

        self.docking_station_latitude = dock_lat
        self.docking_station_longitude = dock_lon
        self.docking_station_heading = dock_heading
        self.docking_route_set = True
        self.save_docking_station_settings()
        self.show_toast_alert("GPS docking route sent to vehicle", is_critical=False)
        self.log_mission(
            f"Docking station set: {dock_lat:.7f}, {dock_lon:.7f}; "
            f"heading {dock_heading:.1f}°."
        )

    def save_docking_station_settings(self):
        """Persist the selected docking point for the next application launch."""
        self.docking_settings.setValue("docking/latitude", self.docking_station_latitude)
        self.docking_settings.setValue("docking/longitude", self.docking_station_longitude)
        self.docking_settings.setValue("docking/heading", self.docking_station_heading)
        self.docking_settings.sync()
        # Refresh dock marker + 10 m projected approach line immediately on both maps.
        line_len = self._dock_projection_length()
        for _view_name in ("plan_web_view", "web_view"):
            _view = getattr(self, _view_name, None)
            if _view is not None and hasattr(_view, "page"):
                _view.page().runJavaScript(
                    f"if (typeof setDockingStation === 'function') setDockingStation({self.docking_station_latitude:.7f}, {self.docking_station_longitude:.7f}, {self.docking_station_heading}, {line_len});"
                )

    def _create_docking_stage_dialog(self, stage):
        dialog = QDialog(self, Qt.Window)
        dialog.setAttribute(Qt.WA_DeleteOnClose, False)
        dialog.setModal(False)
        dialog.setStyleSheet("QDialog { background: #081426; color: #E2F1FF; } QLabel { color: #E2F1FF; }")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(12)

        title = QLabel()
        title.setStyleSheet("font-size: 18px; font-weight: 700; color: #00E5FF;")
        layout.addWidget(title)
        distance_label = QLabel("Vehicle to station: --")
        distance_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #FFFFFF;")
        layout.addWidget(distance_label)

        widgets = {"dialog": dialog, "distance": distance_label}
        if stage == "sonar":
            dialog.setWindowTitle("Sonar Based Docking")
            title.setText("SONAR BASED DOCKING")
            note = QLabel("Live sonar return and bearing from vehicle telemetry")
            note.setStyleSheet("color: #8EB7E6;")
            layout.addWidget(note)
            sonar = Ping360SonarWidget(dialog, theme="cockpit")
            sonar.setMinimumSize(420, 420)
            layout.addWidget(sonar, 1)
            sensor_status = QLabel("Waiting for sonar telemetry")
            sensor_status.setStyleSheet("color: #8EB7E6;")
            layout.addWidget(sensor_status)
            widgets.update({"title": title, "sonar": sonar, "sensor_status": sensor_status})
            dialog.resize(560, 610)
        elif stage == "camera":
            dialog.setWindowTitle("Camera Docking Status")
            title.setText("CAMERA BASED DOCKING NOT IN PROGRESS")
            title.setStyleSheet("font-size: 17px; font-weight: 700; color: #FBBF24;")
            note = QLabel(
                "Homing coil not detected. GPS based movement and sonar guidance continue."
            )
            note.setWordWrap(True)
            note.setStyleSheet("font-size: 15px; color: #E2F1FF; padding: 14px 0;")
            layout.addWidget(note, 1)
            widgets.update({"title": title, "note": note})
            dialog.resize(560, 250)
        else:
            dialog.setWindowTitle("Magnetic Based Docking")
            title.setText("MAGNETIC BASED DOCKING")
            note = QLabel("Live magnetic field telemetry")
            note.setStyleSheet("color: #C4B5FD;")
            layout.addWidget(note)
            sensor_status = QLabel("Waiting for magnetic telemetry")
            sensor_status.setStyleSheet("color: #A0A0A0;")
            layout.addWidget(sensor_status)
            magnetometer = MagnetometerVectorWidget(theme="cockpit", parent=dialog)
            magnetometer.setMinimumSize(360, 360)
            magnetometer.setVisible(False)
            layout.addWidget(magnetometer, 1)
            widgets.update({"title": title, "magnetometer": magnetometer, "sensor_status": sensor_status})
            dialog.resize(520, 570)

        return widgets

    def _minimize_docking_stage_popup(self):
        active = getattr(self, "_active_docking_stage_popup", None)
        if active is not None:
            active["dialog"].showMinimized()
        self._active_docking_stage_popup = None
        self._active_docking_stage = None

    def _update_docking_stage_popup(self, data, docking_active, gps_distance, dock_range, sonar_range, sonar_bearing, scan_angle, mx, my, mz):
        """Show the phase window selected by measured distance during docking."""
        if not docking_active:
            self._minimize_docking_stage_popup()
            return

        measured_distance = gps_distance
        if measured_distance is None:
            measured_distance = dock_range if dock_range is not None else sonar_range
        try:
            measured_distance = float(measured_distance) if measured_distance is not None else None
            if measured_distance is not None and (not math.isfinite(measured_distance) or measured_distance < 0):
                measured_distance = None
        except (TypeError, ValueError):
            measured_distance = None
        if measured_distance is None:
            return

        if measured_distance > 50.0:
            self._minimize_docking_stage_popup()
            return
        if measured_distance > 10.0:
            stage = "sonar"
        elif measured_distance > 3.0:
            stage = "camera"
        else:
            stage = "magnetic"

        dialogs = getattr(self, "_docking_stage_popups", None)
        if dialogs is None:
            dialogs = self._docking_stage_popups = {}
        active = getattr(self, "_active_docking_stage_popup", None)
        if stage != getattr(self, "_active_docking_stage", None):
            if active is not None:
                active["dialog"].showMinimized()
            popup = dialogs.get(stage)
            if popup is None:
                popup = dialogs[stage] = self._create_docking_stage_dialog(stage)
            popup["dialog"].showNormal()
            popup["dialog"].raise_()
            popup["dialog"].activateWindow()
            self._active_docking_stage_popup = popup
            self._active_docking_stage = stage
            active = popup

        active["distance"].setText(f"Vehicle to station: {measured_distance:.2f} m")
        if stage == "sonar":
            sonar = active["sonar"]
            confidence = int(data.get("confidence", 0) or 0)
            sonar.set_target(sonar_range, sonar_bearing, confidence)
            raw_bins = data.get("sonar_bins")
            if scan_angle is not None and isinstance(raw_bins, (list, tuple)):
                sonar.update_scan_line(float(scan_angle), sonar.max_range, raw_bins)
            sonar.set_active(sonar_range is not None)
            active["sensor_status"].setText(
                f"Live sonar return: {float(sonar_range):.2f} m" if sonar_range is not None
                else "Waiting for sonar return"
            )
        elif stage == "magnetic":
            mag_keys = ("magx", "magX", "mx", "mag_x", "magy", "magY", "my", "mag_y", "magz", "magZ", "mz", "mag_z")
            if any(data.get(key) is not None for key in mag_keys):
                active["magnetometer"].set_mag_values(mx, my, mz)
                active["magnetometer"].setVisible(True)
                active["sensor_status"].setText("Live magnetic field telemetry")

    def toggle_manual_docking(self):
        """Start/stop precision GPS docking using the saved dock face geometry."""
        starting = not getattr(self, 'is_docking', False)

        if starting:
            # Always re-send the station geometry before START.  The backend may
            # have restarted since the UI last saved the dock, and DOCK_LINE_LENGTH
            # may also have changed.  This prevents using stale/default waypoints.
            dock_lat = getattr(self, 'docking_station_latitude', 13.0827000)
            dock_lon = getattr(self, 'docking_station_longitude', 80.2707000)
            dock_heading = getattr(self, 'docking_station_heading', 0.0)
            if hasattr(self, 'spin_docking_latitude'):
                dock_lat = self.spin_docking_latitude.value()
                dock_lon = self.spin_docking_longitude.value()
                dock_heading = self.spin_docking_heading.value()
            if not (-90.0 <= float(dock_lat) <= 90.0 and -180.0 <= float(dock_lon) <= 180.0) \
                    or (float(dock_lat) == 0.0 and float(dock_lon) == 0.0):
                self.show_toast_alert("Set valid docking station coordinates before starting Dock", is_critical=True)
                return
            if not (self.telemetry_thread and self.telemetry_thread.isRunning()):
                self.show_toast_alert("Connect to the vehicle before starting Dock", is_critical=True)
                return

            self.docking_station_latitude = float(dock_lat)
            self.docking_station_longitude = float(dock_lon)
            self.docking_station_heading = float(dock_heading) % 360.0
            self.save_docking_station_settings()
            route_cmd = f"$DOCK,SET,{self.docking_station_latitude:.7f},{self.docking_station_longitude:.7f},{self.docking_station_heading:.1f}"
            if not self.telemetry_thread.write_data(route_cmd):
                self.show_toast_alert("Could not send docking station geometry", is_critical=True)
                return
            self.docking_route_set = True

            # Arm first when needed.  START is delayed slightly so serial/UDP/TCP
            # processing cannot observe START before ARM. Backend START is made
            # idempotent, so a reliability retry cannot reset an active approach.
            was_armed = bool(getattr(self, 'is_armed', False))
            if not was_armed:
                print("[Docking] Vehicle was disarmed. Auto-arming for docking procedure.")
                self.arm_vehicle()

            def send_dock_start():
                if self.telemetry_thread and self.telemetry_thread.isRunning():
                    self.telemetry_thread.write_data("$DOCK,START")

            QTimer.singleShot(80 if was_armed else 250, send_dock_start)
            QTimer.singleShot(550, send_dock_start)
            print("[Docking] Precision route sent; DOCK START queued with reliability retry.")
        else:
            cmd = "$DOCK,STOP"
            if self.telemetry_thread and self.telemetry_thread.isRunning():
                sent = self.telemetry_thread.write_data(cmd)
                if not sent and getattr(self.telemetry_thread, 'port', None) != "SIMULATOR":
                    self.show_toast_alert("Could not send GPS docking stop command", is_critical=True)
                    return
                print(f"[Docking] Transmitted GPS docking command: {cmd}")
            else:
                print(f"[Docking Simulated] GPS command: {cmd}")

        self.is_docking = starting
        self.update_docking_ui_state(starting)
        if not starting:
            self._minimize_docking_stage_popup()
        if starting:
            self.show_toast_alert("GPS Docking ENGAGED: navigating to approach waypoint", is_critical=False)
            self.log_mission("GPS Docking ENGAGED: approach waypoint, then dock waypoint.")
        else:
            self.show_toast_alert("GPS Docking STOPPED", is_critical=False)
            self.log_mission("GPS Docking STOPPED. Thrusters commanded neutral.")

    def update_docking_ui_state(self, is_docking: bool):
        self.is_docking = is_docking
        if hasattr(self, 'btn_manual_dock'):
            if is_docking:
                self.btn_manual_dock.setText("STOP DOCKING")
                self.btn_manual_dock.setStyleSheet("""
                    QPushButton {
                        background-color: #DC2626;
                        border: 1px solid #EF4444;
                        border-radius: 4px;
                        padding: 8px 12px;
                        color: #FFFFFF;
                        font-family: 'Google Sans', sans-serif;
                        font-weight: bold;
                        font-size: 11px;
                        margin-top: 4px;
                    }
                    QPushButton:hover {
                        background-color: #EF4444;
                    }
                """)
            else:
                self.btn_manual_dock.setText("DOCK")
                self.btn_manual_dock.setStyleSheet("""
                    QPushButton {
                        background-color: #0284C7;
                        border: 1px solid #0369A1;
                        border-radius: 4px;
                        padding: 8px 12px;
                        color: #FFFFFF;
                        font-family: 'Google Sans', sans-serif;
                        font-weight: bold;
                        font-size: 11px;
                        margin-top: 4px;
                    }
                    QPushButton:hover {
                        background-color: #0EA5E9;
                        border-color: #38BDF8;
                    }
                    QPushButton:pressed {
                        background-color: #0369A1;
                    }
                """)
        if hasattr(self, 'dash_btn_dock'):
            if is_docking:
                self.dash_btn_dock.setText("STOP DOCKING")
                self.dash_btn_dock.setStyleSheet("""
                    QPushButton {
                        background-color: #DC2626;
                        border: 1px solid #EF4444;
                        border-radius: 6px;
                        padding: 7px 10px;
                        color: #FFFFFF;
                        font-family: 'Google Sans', sans-serif;
                        font-weight: bold;
                        font-size: 10px;
                        letter-spacing: 0.7px;
                    }
                    QPushButton:hover {
                        background-color: #EF4444;
                    }
                """)
            else:
                self.dash_btn_dock.setText("DOCK")
                self.dash_btn_dock.setStyleSheet("""
                    QPushButton {
                        background-color: #0284C7;
                        border: 1px solid #0369A1;
                        border-radius: 6px;
                        padding: 7px 10px;
                        color: #FFFFFF;
                        font-family: 'Google Sans', sans-serif;
                        font-weight: bold;
                        font-size: 10px;
                        letter-spacing: 0.7px;
                    }
                    QPushButton:hover {
                        background-color: #0EA5E9;
                        border-color: #38BDF8;
                    }
                    QPushButton:pressed {
                        background-color: #0369A1;
                    }
                """)
        if hasattr(self, 'btn_docking_engage'):
            if is_docking:
                self.btn_docking_engage.setText("DISENGAGE DOCKING")
                self.btn_docking_engage.setStyleSheet("""
                    QPushButton {
                        background-color: #DC2626;
                        border: 1px solid #EF4444;
                        border-radius: 5px;
                        padding: 8px 12px;
                        color: #FFFFFF;
                        font-family: 'Google Sans', sans-serif;
                        font-weight: bold;
                        font-size: 11px;
                        letter-spacing: 0.5px;
                    }
                    QPushButton:hover {
                        background-color: #EF4444;
                    }
                """)
            else:
                self.btn_docking_engage.setText("ENGAGE AUTONOMOUS DOCKING")
                self.btn_docking_engage.setStyleSheet("""
                    QPushButton {
                        background-color: #0284C7;
                        border: 1px solid #0369A1;
                        border-radius: 5px;
                        padding: 8px 12px;
                        color: #FFFFFF;
                        font-family: 'Google Sans', sans-serif;
                        font-weight: bold;
                        font-size: 11px;
                        letter-spacing: 0.5px;
                    }
                    QPushButton:hover {
                        background-color: #0369A1;
                    }
                    QPushButton:pressed {
                        background-color: #075985;
                    }
                """)

    def update_light_ui_state(self, is_checked):
        self.light_state = 1 if is_checked else 0
        buttons = []
        if hasattr(self, 'btn_light_toggle'):
            buttons.append(self.btn_light_toggle)
        if hasattr(self, 'btn_auto_light_toggle'):
            buttons.append(self.btn_auto_light_toggle)
        if hasattr(self, 'btn_manual_light_toggle'):
            buttons.append(self.btn_manual_light_toggle)
        if hasattr(self, 'dash_btn_light'):
            buttons.append(self.dash_btn_light)
        if hasattr(self, 'docking_btn_light'):
            buttons.append(self.docking_btn_light)
            
        for btn in buttons:
            btn.setChecked(is_checked)
            if is_checked:
                btn.setText("LIGHT: ON")
                btn.setStyleSheet("background-color: #00E676; border: 1px solid #00C853; border-radius: 4px; padding: 8px 12px; color: #121212; font-family: 'Google Sans', sans-serif; font-weight: bold; font-size: 11px;")
            else:
                btn.setText("LIGHT: OFF")
                btn.setStyleSheet("background-color: #0F223C; border: 1px solid #1C3B65; border-radius: 4px; padding: 8px 12px; color: #E2F1FF; font-family: 'Google Sans', sans-serif; font-weight: bold; font-size: 11px;")

    def toggle_light(self):
        sender = self.sender()
        is_checked = sender.isChecked() if sender else (self.light_state == 0)
        self.update_light_ui_state(is_checked)
        
        if self.telemetry_thread and self.telemetry_thread.isRunning():
            self.telemetry_thread.light_state = self.light_state
            self.send_immediate_cmd()

    def update_camera_ui_state(self, is_checked):
        self.camera_state = 1 if is_checked else 0
        buttons = []
        if hasattr(self, 'btn_camera_toggle'):
            buttons.append(self.btn_camera_toggle)
        if hasattr(self, 'btn_auto_camera_toggle'):
            buttons.append(self.btn_auto_camera_toggle)
        if hasattr(self, 'btn_manual_camera_toggle'):
            buttons.append(self.btn_manual_camera_toggle)
        if hasattr(self, 'dash_btn_camera'):
            buttons.append(self.dash_btn_camera)
        if hasattr(self, 'docking_btn_camera'):
            buttons.append(self.docking_btn_camera)
            
        for btn in buttons:
            btn.setChecked(is_checked)
            if is_checked:
                btn.setText("CAMERA: ON")
                btn.setStyleSheet("background-color: #00E676; border: 1px solid #00C853; border-radius: 4px; padding: 8px 12px; color: #121212; font-family: 'Google Sans', sans-serif; font-weight: bold; font-size: 11px;")
            else:
                btn.setText("CAMERA: OFF")
                btn.setStyleSheet("background-color: #0F223C; border: 1px solid #1C3B65; border-radius: 4px; padding: 8px 12px; color: #E2F1FF; font-family: 'Google Sans', sans-serif; font-weight: bold; font-size: 11px;")

    def toggle_camera(self):
        sender = self.sender()
        is_checked = sender.isChecked() if sender else (self.camera_state == 0)
        self.update_camera_ui_state(is_checked)
        
        if self.telemetry_thread and self.telemetry_thread.isRunning():
            self.telemetry_thread.camera_state = self.camera_state
            self.send_immediate_cmd()

    def start_camera_recording(self):
        """First turns on camera, begins 15s warmup delay, and sends start recording command to backend."""
        # 1. Turn ON camera power across all UI buttons and send command to vehicle
        self.update_camera_ui_state(True)
        if self.telemetry_thread and self.telemetry_thread.isRunning():
            self.telemetry_thread.camera_state = self.camera_state
            self.send_immediate_cmd()

        url = "rtsp://192.168.1.168:554/stream_1"
        if hasattr(self, 'input_cam_rtsp_url') and self.input_cam_rtsp_url.text().strip():
            url = self.input_cam_rtsp_url.text().strip()

        cmd = f"$REC,START,{url}"
        if self.telemetry_thread and self.telemetry_thread.isRunning():
            self.telemetry_thread.write_data(cmd)
            print(f"[Camera] Sent start recording command: {cmd}")
        else:
            print(f"[Camera Simulated] Start recording: {cmd}")

        # 2. Enter 15-second warmup state
        self._cam_rec_warming_up = True
        self._cam_rec_warmup_remaining = 15
        self._cam_rec_active = False

        if hasattr(self, 'lbl_cam_rec_timer'):
            self.lbl_cam_rec_timer.setText("00:00:00")

        self.update_cam_rec_ui_state(False)
        self.show_toast_alert("Camera turned ON. Warming up camera (15s) before recording...", is_critical=False)
        self.log_mission(f"Camera powered ON. Waiting 15s for camera startup before recording: {url}")

        # 3. Start warmup countdown timer
        if not hasattr(self, '_cam_rec_warmup_timer') or self._cam_rec_warmup_timer is None:
            self._cam_rec_warmup_timer = QTimer(self)
            self._cam_rec_warmup_timer.setInterval(1000)
            self._cam_rec_warmup_timer.timeout.connect(self._on_cam_rec_warmup_tick)
        self._cam_rec_warmup_timer.start()

    def _on_cam_rec_warmup_tick(self):
        """Countdown tick for the 15-second camera warmup."""
        if not getattr(self, '_cam_rec_warming_up', False):
            if hasattr(self, '_cam_rec_warmup_timer') and self._cam_rec_warmup_timer:
                self._cam_rec_warmup_timer.stop()
            return

        self._cam_rec_warmup_remaining -= 1
        if self._cam_rec_warmup_remaining > 0:
            if hasattr(self, 'lbl_cam_rec_badge'):
                self.lbl_cam_rec_badge.setText(f"WARMING UP ({self._cam_rec_warmup_remaining}s)...")
                self.lbl_cam_rec_badge.setStyleSheet("color: #F59E0B; font-family: 'Google Sans', sans-serif; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        else:
            # Warmup finished! Start duration timer
            self._cam_rec_warming_up = False
            if hasattr(self, '_cam_rec_warmup_timer') and self._cam_rec_warmup_timer:
                self._cam_rec_warmup_timer.stop()

            self._cam_rec_active = True
            import time
            self._cam_rec_start_time = time.time()
            if not hasattr(self, '_cam_rec_timer') or self._cam_rec_timer is None:
                self._cam_rec_timer = QTimer(self)
                self._cam_rec_timer.setInterval(1000)
                self._cam_rec_timer.timeout.connect(self._update_cam_rec_duration)
            self._cam_rec_timer.start()

            self.update_cam_rec_ui_state(True)
            self.show_toast_alert("Camera recording started on Jetson", is_critical=False)
            self.log_mission("Camera recording live.")

    def stop_camera_recording(self):
        """Send stop recording command to backend and stop duration / warmup timers."""
        cmd = "$REC,STOP"
        if self.telemetry_thread and self.telemetry_thread.isRunning():
            self.telemetry_thread.write_data(cmd)
            print(f"[Camera] Sent stop recording command: {cmd}")
        else:
            print(f"[Camera Simulated] Stop recording: {cmd}")

        was_warming_up = getattr(self, '_cam_rec_warming_up', False)
        self._cam_rec_warming_up = False
        if hasattr(self, '_cam_rec_warmup_timer') and self._cam_rec_warmup_timer:
            self._cam_rec_warmup_timer.stop()

        self._cam_rec_active = False
        if hasattr(self, '_cam_rec_timer') and self._cam_rec_timer:
            self._cam_rec_timer.stop()

        self.update_cam_rec_ui_state(False)
        if was_warming_up:
            self.show_toast_alert("Camera recording cancelled during warmup", is_critical=False)
            self.log_mission("Camera recording cancelled during warmup.")
        else:
            self.show_toast_alert("Camera recording saved", is_critical=False)
            self.log_mission("Camera recording stopped & saved.")

    def _update_cam_rec_duration(self):
        if getattr(self, '_cam_rec_active', False) and hasattr(self, '_cam_rec_start_time'):
            import time
            elapsed = int(time.time() - self._cam_rec_start_time)
            h = elapsed // 3600
            m = (elapsed % 3600) // 60
            s = elapsed % 60
            timer_str = f"{h:02d}:{m:02d}:{s:02d}"
            if hasattr(self, 'lbl_cam_rec_timer'):
                self.lbl_cam_rec_timer.setText(timer_str)

    def update_cam_rec_ui_state(self, is_recording: bool, file_name: str = ""):
        is_warming_up = getattr(self, '_cam_rec_warming_up', False)
        if hasattr(self, 'btn_cam_rec_start'):
            self.btn_cam_rec_start.setEnabled(not (is_recording or is_warming_up))
        if hasattr(self, 'btn_cam_rec_stop'):
            self.btn_cam_rec_stop.setEnabled(is_recording or is_warming_up)
        if hasattr(self, 'lbl_cam_rec_badge'):
            if is_recording:
                self.lbl_cam_rec_badge.setText("RECORDING LIVE")
                self.lbl_cam_rec_badge.setStyleSheet("color: #FF5252; font-family: 'Google Sans', sans-serif; font-size: 11px; font-weight: bold; border: none; background: transparent;")
            elif is_warming_up:
                rem = getattr(self, '_cam_rec_warmup_remaining', 15)
                self.lbl_cam_rec_badge.setText(f"WARMING UP ({rem}s)...")
                self.lbl_cam_rec_badge.setStyleSheet("color: #F59E0B; font-family: 'Google Sans', sans-serif; font-size: 11px; font-weight: bold; border: none; background: transparent;")
            else:
                self.lbl_cam_rec_badge.setText("● STANDBY")
                self.lbl_cam_rec_badge.setStyleSheet("color: #94A3B8; font-family: 'Google Sans', sans-serif; font-size: 11px; font-weight: bold; border: none; background: transparent;")
        if hasattr(self, 'lbl_cam_rec_file') and file_name:
            import os
            self.lbl_cam_rec_file.setText(f"File: {os.path.basename(file_name)}")

    def activate_sonar(self):
        """Send activate sonar command to vehicle and update UI states."""
        cmd = "$SONAR,START"
        if self.telemetry_thread and self.telemetry_thread.isRunning():
            self.telemetry_thread.write_data(cmd)
            print(f"[Sonar] Sent activate command: {cmd}")
        else:
            print(f"[Sonar Simulated] Activate: {cmd}")
        self._user_toggled_sonar = True
        self.sonar_active = True
        self.update_sonar_ui_state(True)
        self.show_toast_alert("Ping360 Sonar activated", is_critical=False)
        self.log_mission("Ping360 scanning sonar ACTIVATED.")

    def deactivate_sonar(self):
        """Send stop sonar command to vehicle and update UI states."""
        cmd = "$SONAR,STOP"
        if self.telemetry_thread and self.telemetry_thread.isRunning():
            self.telemetry_thread.write_data(cmd)
            print(f"[Sonar] Sent stop command: {cmd}")
        else:
            print(f"[Sonar Simulated] Stop: {cmd}")
        self._user_toggled_sonar = False
        self.sonar_active = False
        self.update_sonar_ui_state(False)
        if hasattr(self, 'hw_sonar_widget') and self.hw_sonar_widget:
            self.hw_sonar_widget.set_active(False)
            self.hw_sonar_widget.clear_scan()
        if hasattr(self, 'docking_sonar') and self.docking_sonar:
            self.docking_sonar.set_active(False)
            self.docking_sonar.clear_scan()
        if hasattr(self, 'lbl_sonar_hw_dist') and self.lbl_sonar_hw_dist:
            self.lbl_sonar_hw_dist.setText("Open Water")
        if hasattr(self, 'lbl_sonar_hw_bearing') and self.lbl_sonar_hw_bearing:
            self.lbl_sonar_hw_bearing.setText("—°")
        if hasattr(self, 'lbl_sonar_hw_angle') and self.lbl_sonar_hw_angle:
            self.lbl_sonar_hw_angle.setText("0.0°")
        self.show_toast_alert("Ping360 Sonar stopped", is_critical=False)
        self.log_mission("Ping360 scanning sonar STOPPED.")

    def _on_sonar_anim_tick(self):
        """Advances acoustic scanning radar sweep line continuously whenever sonar is active."""
        if not getattr(self, 'sonar_active', False):
            if hasattr(self, '_sonar_anim_timer') and self._sonar_anim_timer and self._sonar_anim_timer.isActive():
                self._sonar_anim_timer.stop()
            return

        import time
        # If genuine hardware is currently streaming real angles, let hardware drive the display
        if time.monotonic() - getattr(self, '_last_hw_sonar_time', 0.0) < 1.0:
            return

        # Advance fallback/simulated sector sweep angle
        self._docking_sonar_angle = (getattr(self, '_docking_sonar_angle', 0.0) + 3.0) % 360.0
        cur_angle = self._docking_sonar_angle

        # Simulated acoustic return array (60 range bins)
        bins = [15 + int(6 * math.sin(i * 0.35)) for i in range(60)]
        sim_target_dist = 4.2
        sim_target_bearing = 220.0
        has_target = (205.0 <= cur_angle <= 235.0)
        if has_target:
            target_bin = min(59, max(0, int((sim_target_dist / 10.0) * 60)))
            for offset in (-2, -1, 0, 1, 2):
                idx = target_bin + offset
                if 0 <= idx < 60:
                    bins[idx] = max(bins[idx], int(255 - abs(offset) * 45))

        tgt_d = sim_target_dist if has_target else None
        tgt_b = sim_target_bearing if has_target else None

        if hasattr(self, 'hw_sonar_widget') and self.hw_sonar_widget:
            self.hw_sonar_widget.set_active(True)
            self.hw_sonar_widget.set_target(tgt_d, tgt_b, 100)
            self.hw_sonar_widget.update_scan_line(cur_angle, getattr(self.hw_sonar_widget, 'max_range', 10.0), bins)

        if hasattr(self, 'docking_sonar') and self.docking_sonar:
            self.docking_sonar.set_active(True)
            self.docking_sonar.set_target(tgt_d, tgt_b, 100)
            self.docking_sonar.update_scan_line(cur_angle, getattr(self.docking_sonar, 'max_range', 10.0), bins)

        if hasattr(self, 'lbl_sonar_hw_angle') and self.lbl_sonar_hw_angle:
            self.lbl_sonar_hw_angle.setText(f"{cur_angle:05.1f}°")
        if hasattr(self, 'lbl_sonar_hw_dist') and self.lbl_sonar_hw_dist:
            self.lbl_sonar_hw_dist.setText(f"{tgt_d:.2f} m" if tgt_d is not None else "Open Water")
        if hasattr(self, 'lbl_sonar_hw_bearing') and self.lbl_sonar_hw_bearing:
            self.lbl_sonar_hw_bearing.setText(f"{tgt_b:.1f}°" if tgt_b is not None else "—°")

    def set_sonar_range(self, range_val: float):
        """Set acoustic scope display max range and highlight active button."""
        if hasattr(self, 'hw_sonar_widget') and self.hw_sonar_widget:
            self.hw_sonar_widget.set_range(range_val)
        if hasattr(self, 'docking_sonar') and self.docking_sonar:
            self.docking_sonar.set_range(range_val)
        if hasattr(self, 'sonar_range_btns'):
            for btn, val in self.sonar_range_btns:
                if val == range_val:
                    btn.setStyleSheet("background-color: #0284C7; border: 1px solid #00E5FF; color: #FFFFFF; font-weight: bold; border-radius: 3px; padding: 4px 10px; font-size: 10px;")
                else:
                    btn.setStyleSheet("background-color: #1E293B; border: 1px solid #334155; color: #94A3B8; border-radius: 3px; padding: 4px 10px; font-size: 10px;")

    def toggle_sonar(self):
        """Toggle Ping360 scanning sonar power state from Equipment Control."""
        is_on = self.btn_sonar_toggle.isChecked() if hasattr(self, 'btn_sonar_toggle') else False
        if is_on:
            self.activate_sonar()
        else:
            self.deactivate_sonar()

    def update_sonar_ui_state(self, is_active: bool):
        """Sync sonar buttons, equipment toggles, and status badges across pages."""
        self.sonar_active = is_active
        if hasattr(self, 'btn_hw_sonar_start'):
            self.btn_hw_sonar_start.setEnabled(not is_active)
        if hasattr(self, 'btn_hw_sonar_stop'):
            self.btn_hw_sonar_stop.setEnabled(is_active)
        if hasattr(self, 'btn_sonar_toggle') and self.btn_sonar_toggle.isChecked() != is_active:
            self.btn_sonar_toggle.blockSignals(True)
            self.btn_sonar_toggle.setChecked(is_active)
            self.btn_sonar_toggle.blockSignals(False)
        if hasattr(self, 'lbl_sonar_hw_badge') and self.lbl_sonar_hw_badge:
            if is_active:
                self.lbl_sonar_hw_badge.setText("ACTIVE SCANNING")
                self.lbl_sonar_hw_badge.setStyleSheet("background-color: #064E3B; border: 1px solid #10B981; border-radius: 4px; padding: 4px 10px; color: #00E676; font-family: 'Google Sans'; font-size: 10px; font-weight: bold;")
            else:
                self.lbl_sonar_hw_badge.setText("STANDBY")
                self.lbl_sonar_hw_badge.setStyleSheet("background-color: #1E293B; border: 1px solid #334155; border-radius: 4px; padding: 4px 10px; color: #94A3B8; font-family: 'Google Sans'; font-size: 10px; font-weight: bold;")
        if hasattr(self, 'hw_sonar_widget') and self.hw_sonar_widget:
            self.hw_sonar_widget.set_active(is_active)
        if hasattr(self, 'docking_sonar') and self.docking_sonar:
            self.docking_sonar.set_active(is_active)

        if is_active:
            if not hasattr(self, '_sonar_anim_timer') or self._sonar_anim_timer is None:
                self._sonar_anim_timer = QTimer(self)
                self._sonar_anim_timer.setInterval(60) # ~16 Hz sweep animation
                self._sonar_anim_timer.timeout.connect(self._on_sonar_anim_tick)
            if not self._sonar_anim_timer.isActive():
                self._sonar_anim_timer.start()
        else:
            if hasattr(self, '_sonar_anim_timer') and self._sonar_anim_timer and self._sonar_anim_timer.isActive():
                self._sonar_anim_timer.stop()
            
    def on_dash_slider_changed(self, thruster_idx, val):
        lbl = getattr(self, f'lbl_dash_t{thruster_idx}_val', None)
        badge = getattr(self, f'lbl_dash_t{thruster_idx}_badge', None)
        if lbl:
            lbl.setText(f"{val} µs")
        if badge:
            if val == 1500:
                badge.setText("NEUTRAL")
                badge.setStyleSheet("background-color: #1E293B; border: 1px solid #334155; border-radius: 8px; color: #94A3B8; font-size: 9px; font-weight: bold; letter-spacing: 0.5px; padding: 5px 10px;")
            elif val > 1500:
                badge.setText("FWD")
                badge.setStyleSheet("background-color: #15352D; border: 1px solid #27745A; border-radius: 6px; color: #6EE7B7; font-size: 9px; font-weight: bold; padding: 5px 10px;")
            else:
                badge.setText("REV")
                badge.setStyleSheet("background-color: #3A2025; border: 1px solid #7F343E; border-radius: 6px; color: #FCA5A5; font-size: 9px; font-weight: bold; padding: 5px 10px;")
            
        t1 = self.slider_dash_t1.value() if hasattr(self, 'slider_dash_t1') else 1500
        t2 = self.slider_dash_t2.value() if hasattr(self, 'slider_dash_t2') else 1500
        t3 = self.slider_dash_t3.value() if hasattr(self, 'slider_dash_t3') else 1500
        
        if hasattr(self, 'rc_output_fields'):
            if "rc1" in self.rc_output_fields: self.rc_output_fields["rc1"].setText(str(t1))
            if "rc2" in self.rc_output_fields: self.rc_output_fields["rc2"].setText(str(t2))
            if "rc3" in self.rc_output_fields: self.rc_output_fields["rc3"].setText(str(t3))
            
        self.send_dash_thruster_cmd(t1, t2, t3)

    def reset_dash_thruster(self, thruster_idx):
        slider = getattr(self, f'slider_dash_t{thruster_idx}', None)
        if slider:
            slider.setValue(1500)

    def reset_all_dash_thrusters(self):
        if hasattr(self, 'slider_dash_t1'): self.slider_dash_t1.setValue(1500)
        if hasattr(self, 'slider_dash_t2'): self.slider_dash_t2.setValue(1500)
        if hasattr(self, 'slider_dash_t3'): self.slider_dash_t3.setValue(1500)
        self.send_dash_thruster_cmd(1500, 1500, 1500)

    def send_dash_thruster_cmd(self, t1, t2, t3):
        if getattr(self, 'failsafe_active', False):
            t1, t2, t3 = 1500, 1500, 1500
        if self.telemetry_thread and self.telemetry_thread.isRunning():
            payload = f"CMD,{t1},{t2},{t3},{self.light_state},{self.camera_state}"
            self.telemetry_thread.write_data(payload)

    def master_emergency_stop(self):
        # Reset all thrusters to neutral 1500
        if hasattr(self, 'slider_dash_t1'): self.slider_dash_t1.setValue(1500)
        if hasattr(self, 'slider_dash_t2'): self.slider_dash_t2.setValue(1500)
        if hasattr(self, 'slider_dash_t3'): self.slider_dash_t3.setValue(1500)
        
        # Turn off lights and camera
        self.update_light_ui_state(False)
        self.update_camera_ui_state(False)
        
        # Transmit kill command to vehicle
        if self.telemetry_thread and self.telemetry_thread.isRunning():
            self.telemetry_thread.light_state = 0
            self.telemetry_thread.camera_state = 0
            self.telemetry_thread.write_data("CMD,1500,1500,1500,0,0")
            
        self.show_toast_alert("EMERGENCY STOP ACTIVATED: ALL THRUSTERS NEUTRAL, RELAYS OFF", is_critical=True)

    def send_immediate_cmd(self):
        # Autonomous waypoint and GPS-docking control are executed by sea_backend.py.
        # A non-neutral manual packet would be interpreted as an override and can
        # cancel AUTO, so the periodic manual stream must be completely silent.
        if getattr(self, 'mission_active', False) or getattr(self, 'is_docking', False):
            return

        rc1 = self.slider_dash_t1.value() if hasattr(self, 'slider_dash_t1') else (self.rc_output_fields["rc1"].text() if "rc1" in self.rc_output_fields else "1500")
        rc2 = self.slider_dash_t2.value() if hasattr(self, 'slider_dash_t2') else (self.rc_output_fields["rc2"].text() if "rc2" in self.rc_output_fields else "1500")
        rc3 = self.slider_dash_t3.value() if hasattr(self, 'slider_dash_t3') else (self.rc_output_fields["rc3"].text() if "rc3" in self.rc_output_fields else "1500")
        if getattr(self, 'failsafe_active', False):
            rc1, rc2, rc3 = 1500, 1500, 1500
        payload = f"CMD,{rc1},{rc2},{rc3},{self.light_state},{self.camera_state}"

        # Preserve 20 Hz responsiveness when the operator moves a control, but do
        # not write the same packet 20 times/s forever.  An unchanged command is
        # refreshed at 4 Hz, which keeps the link/watchdog alive while avoiding
        # needless synchronous socket/serial pressure on the Qt GUI thread.
        now = _time.monotonic()
        last_payload = getattr(self, '_last_manual_payload', None)
        last_tx = getattr(self, '_last_manual_tx_time', 0.0)
        if payload == last_payload and now - last_tx < 0.25:
            return
        if self.telemetry_thread and self.telemetry_thread.isRunning():
            if self.telemetry_thread.write_data(payload):
                self._last_manual_payload = payload
                self._last_manual_tx_time = now

    def start_mission(self):
        # --- Pre-start Autonomous Mission Safety Pre-checks ---
        # 1. Vehicle is armed
        if not getattr(self, 'is_armed', False):
            self.log_mission("Error: Cannot start route. Vehicle is disarmed! Please ARM the vehicle first.")
            self.show_toast_alert("Reject Start: Vehicle is disarmed!", is_critical=True)
            return

        # 2. At least one waypoint exists
        if not self.planned_waypoints:
            self.log_mission("Error: Cannot start route. Waypoint route list is empty!")
            self.show_toast_alert("Reject Start: No waypoints uploaded!", is_critical=True)
            return

        # 3. GPS is healthy (connected, sats >= 6, coordinates valid)
        last_sats = getattr(self, 'last_sats', 0)
        last_lat = getattr(self, 'last_lat', 0.0)
        last_lon = getattr(self, 'last_lon', 0.0)
        gps_connected = (self.top_bar.status_state == "connected")
        
        gps_ok = (gps_connected and last_sats >= 6 and last_lat != 0.0 and last_lon != 0.0)
        if not gps_ok:
            reasons = []
            if not gps_connected:
                reasons.append("datalink disconnected")
            if last_sats < 6:
                reasons.append(f"sats={last_sats} < 6")
            if last_lat == 0.0 and last_lon == 0.0:
                reasons.append("invalid coordinates")
            self.log_mission(f"Error: Cannot start route. GPS is unhealthy ({', '.join(reasons)}).")
            self.show_toast_alert("Reject Start: GPS is unhealthy!", is_critical=True)
            return

        # 4. AHRS is healthy (connected and receiving telemetry)
        ahrs_ok = (gps_connected and hasattr(self, 'watchdog_timer') and self.watchdog_timer.isActive())
        if not ahrs_ok:
            self.log_mission("Error: Cannot start route. AHRS heading sensor is unhealthy/disconnected.")
            self.show_toast_alert("Reject Start: AHRS is unhealthy!", is_critical=True)
            return

        active_style = """
            QPushButton {
                background-color: #1C3B65;
                border: 1px solid #2D5A8F;
                border-radius: 4px;
                padding: 8px 12px;
                color: #FFFFFF;
                font-family: 'Google Sans', sans-serif;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #2D5A8F;
                border-color: #3B72B0;
                color: #FFFFFF;
            }
        """
        inactive_style = """
            QPushButton {
                background-color: #0F223C;
                border: 1px solid #1C3B65;
                border-radius: 4px;
                padding: 8px 12px;
                color: #E2F1FF;
                font-family: 'Google Sans', sans-serif;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #1C3B65;
                border-color: #2D5A8F;
                color: #FFFFFF;
            }
        """
        self.btn_start.setStyleSheet(active_style)
        self.btn_stop.setStyleSheet(inactive_style)

        # Set this BEFORE transmitting $START so the 20 Hz manual command
        # timer cannot race with the autonomous start command.
        self.mission_active = True

        if self.telemetry_thread and self.telemetry_thread.isRunning():
            self.telemetry_thread.write_data("$START")
        print("[Mission Control] Autonomous waypoint route execution STARTED.")
        self.mission_active = True
        self.current_wp_idx = 0
        if self.telemetry_thread and self.telemetry_thread.isRunning():
            if hasattr(self.telemetry_thread, 'set_waypoints'):
                self.telemetry_thread.set_waypoints(self.planned_waypoints)
            if hasattr(self.telemetry_thread, 'start_mission'):
                self.telemetry_thread.start_mission()

    def stop_mission(self):
        active_style = """
            QPushButton {
                background-color: #1C3B65;
                border: 1px solid #2D5A8F;
                border-radius: 4px;
                padding: 8px 12px;
                color: #FFFFFF;
                font-family: 'Google Sans', sans-serif;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #2D5A8F;
                border-color: #3B72B0;
                color: #FFFFFF;
            }
        """
        inactive_style = """
            QPushButton {
                background-color: #0F223C;
                border: 1px solid #1C3B65;
                border-radius: 4px;
                padding: 8px 12px;
                color: #E2F1FF;
                font-family: 'Google Sans', sans-serif;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #1C3B65;
                border-color: #2D5A8F;
                color: #FFFFFF;
            }
        """
        self.btn_start.setStyleSheet(inactive_style)
        self.btn_stop.setStyleSheet(active_style)
        if self.telemetry_thread and self.telemetry_thread.isRunning():
            self.telemetry_thread.write_data("$STOP")
            self.telemetry_thread.write_data("$MANUAL,1500,1500,1500")
        print("[Mission Control] Autonomous waypoint route execution STOPPED.")
        self.mission_active = False
        if self.telemetry_thread and self.telemetry_thread.isRunning():
            if hasattr(self.telemetry_thread, 'stop_mission'):
                self.telemetry_thread.stop_mission()

    def refresh_joystick_devices(self):
        # Do not refresh if joystick is active/enabled
        if self.joystick is not None:
            return
            
        try:
            import pygame
            # Re-initialize pygame joystick module to detect hot-plugs on Windows
            pygame.joystick.quit()
            pygame.joystick.init()
            
            count = pygame.joystick.get_count()
            curr_text = self.combo_joystick.currentText()
            preferred_name = str(self.docking_settings.value("joystick/device_name", "") or "")
            self.combo_joystick.clear()
            
            if count == 0:
                self.combo_joystick.addItem("No Device Detected")
                self.combo_joystick.setEnabled(False)
                self.btn_joystick_enable.setEnabled(False)
            else:
                self.combo_joystick.setEnabled(True)
                self.btn_joystick_enable.setEnabled(True)
                    
                for i in range(count):
                    try:
                        joy = pygame.joystick.Joystick(i)
                        joy.init()
                        self.combo_joystick.addItem(f"{joy.get_name()} (Device {i})", i)
                        joy.quit()
                    except:
                        pass
                
                # Prefer the saved device identity; names are more stable than
                # SDL device indexes, which can change between launches.
                index = -1
                if preferred_name:
                    for i in range(self.combo_joystick.count()):
                        if self.combo_joystick.itemText(i).rsplit(" (Device ", 1)[0] == preferred_name:
                            index = i
                            break
                if index < 0:
                    index = self.combo_joystick.findText(curr_text)
                if index >= 0:
                    self.combo_joystick.setCurrentIndex(index)
                else:
                    self.combo_joystick.setCurrentIndex(0)
                    
                # Startup auto-connect is limited to the saved device.
                saved_device_selected = (
                    bool(preferred_name)
                    and self.combo_joystick.currentText().rsplit(" (Device ", 1)[0] == preferred_name
                )
                if self.joystick_auto_reconnect and saved_device_selected:
                    print("[Joystick] Saved device detected. Attempting automatic connection...")
                    self.load_joystick_config()
                    self.btn_joystick_enable.setChecked(True)
                    self.toggle_joystick_state(True)
        except Exception as e:
            print(f"[Joystick Scan Error] {e}")

    @Slot(bool)
    def toggle_joystick_state(self, checked):
        if checked:
            self.joystick_auto_reconnect = True
            import pygame
            idx = self.combo_joystick.currentData()
            if idx is not None:
                try:
                    self.joystick = pygame.joystick.Joystick(idx)
                    self.joystick.init()
                    j_name = self.joystick.get_name()
                    self.docking_settings.setValue("joystick/device_name", j_name)
                    print(f"[Joystick] Activated device {idx}: {j_name}")
                    
                    self.lbl_config_status.setText(f"Connected: {j_name}")
                    self.lbl_plan_joy_status.setText(f"CONNECTED ({j_name.upper()})")
                    self.lbl_plan_joy_status.setStyleSheet("color: #4CAF50; font-weight: bold; font-size: 11px;")
                    
                    self.joystick_timer.start()
                except Exception as e:
                    print(f"[Joystick Activation Error] {e}")
                    self.btn_joystick_enable.setChecked(False)
                    self.disable_joystick()
            else:
                self.btn_joystick_enable.setChecked(False)
        else:
            self.disable_joystick()

    def set_joystick_auto_enable(self, enabled):
        """Persist the startup auto-connect preference and selected device."""
        self.joystick_auto_enable = bool(enabled)
        self.docking_settings.setValue("joystick/auto_enable", self.joystick_auto_enable)
        if self.joystick_auto_enable:
            idx = self.combo_joystick.currentData()
            if idx is not None:
                try:
                    import pygame
                    device = pygame.joystick.Joystick(idx)
                    device.init()
                    self.docking_settings.setValue("joystick/device_name", device.get_name())
                    device.quit()
                except Exception as e:
                    print(f"[Joystick] Could not save selected device: {e}")
            self.joystick_auto_reconnect = True
            self.refresh_joystick_devices()
        elif self.joystick is None:
            self.joystick_auto_reconnect = False

    def disable_joystick(self, unexpected=False):
        self.joystick_timer.stop()
        if self.joystick is not None:
            try:
                self.joystick.quit()
            except:
                pass
            self.joystick = None
            
        self.btn_joystick_enable.setChecked(False)
        self.lbl_config_status.setText("Loaded Config for ArduSub")
        
        if unexpected:
            self.lbl_plan_joy_status.setText("DISCONNECTED (LINK LOST)")
            self.lbl_plan_joy_status.setStyleSheet("color: #FF5252; font-weight: bold; font-size: 11px;")
        else:
            self.joystick_auto_reconnect = False
            self.lbl_plan_joy_status.setText("DISCONNECTED")
            self.lbl_plan_joy_status.setStyleSheet("color: #EF5350; font-weight: bold; font-size: 11px;")
            
        # Reset progress bars
        self.bar_throttle.setValue(0)
        self.bar_steering.setValue(50)
        self.bar_pitch.setValue(50)
        
        # Reset live output labels to defaults
        for rc_key in ["rc1", "rc2", "rc3"]:
            if hasattr(self, 'rc_output_fields') and rc_key in self.rc_output_fields:
                self.rc_output_fields[rc_key].setText("1500")
        for btn_key in ["arm", "disarm", "start", "stop", "rth", "light", "camera",
                        "manual_mode", "auto_mode", "dock"]:
            if hasattr(self, 'btn_output_fields') and btn_key in self.btn_output_fields:
                self.btn_output_fields[btn_key].setText("0")
                
        # Force immediate scan to refresh the joystick list!
        self.refresh_joystick_devices()

    def poll_joystick_input(self):
        try:
            import pygame
            pygame.event.pump()
            
            if self.joystick is None:
                return
                
            num_axes = self.joystick.get_numaxes()
            num_buttons = self.joystick.get_numbuttons()
            
            # --- 1. Handle Auto-Detection ---
            if self.detecting_axis_key is not None:
                for i in range(num_axes):
                    val = self.joystick.get_axis(i)
                    init_val = self.detecting_initial_axes.get(i, 0.0)
                    if abs(val - init_val) > 0.3:
                        combo = self.rc_axis_combos[self.detecting_axis_key]
                        idx = combo.findData(i)
                        if idx >= 0:
                            combo.setCurrentIndex(idx)
                        print(f"[Joystick Detect] Detected Axis {i} for {self.detecting_axis_key}")
                        self.cancel_detection()
                        break
                        
            if self.detecting_button_key is not None:
                for i in range(num_buttons):
                    if self.joystick.get_button(i) == 1:
                        combo = self.btn_mapping_combos[self.detecting_button_key]
                        idx = combo.findData(i)
                        if idx >= 0:
                            combo.setCurrentIndex(idx)
                        print(f"[Joystick Detect] Detected Button {i} for {self.detecting_button_key}")
                        self.cancel_detection()
                        break
            
            # --- 2. Process Mapped Axes & Update Outputs ---
            axes_data = {}
            for rc_key in ["rc1", "rc2", "rc3"]:
                if getattr(self, 'failsafe_active', False):
                    self.rc_output_fields[rc_key].setText("1500")
                    axes_data[rc_key] = 50
                    continue
                combo = self.rc_axis_combos[rc_key]
                axis_idx = combo.currentData()
                
                if axis_idx is not None and axis_idx < num_axes:
                    val = self.joystick.get_axis(axis_idx)
                    
                    if self.rc_reverse_chks[rc_key].isChecked():
                        val = -val
                        
                    if val < 0.0:
                        pwm = int(1500 + val * (1500 - self.thruster_min_limit))
                    else:
                        pwm = int(1500 + val * (self.thruster_max_limit - 1500))
                    pwm = max(self.thruster_min_limit, min(self.thruster_max_limit, pwm))
                    
                    self.rc_output_fields[rc_key].setText(str(pwm))
                    
                    pct = int((val + 1.0) * 50.0)
                    pct = max(0, min(100, pct))
                    axes_data[rc_key] = pct
                else:
                    self.rc_output_fields[rc_key].setText("1500")
                    axes_data[rc_key] = 50
            
            # Update legacy progress bars
            if "rc3" in axes_data:
                self.bar_throttle.setValue(axes_data["rc3"])
            elif "rc1" in axes_data:
                self.bar_throttle.setValue(axes_data["rc1"])
                
            if "rc1" in axes_data:
                self.bar_steering.setValue(axes_data["rc1"])
            elif "rc2" in axes_data:
                self.bar_steering.setValue(axes_data["rc2"])
                
            if "rc2" in axes_data:
                self.bar_pitch.setValue(axes_data["rc2"])
            elif "rc3" in axes_data:
                self.bar_pitch.setValue(axes_data["rc3"])
                
            # --- 3. Process Mapped Buttons & Trigger Actions ---
            if not hasattr(self, '_prev_btn_states'):
                self._prev_btn_states = {}
                
            for btn_key in ["arm", "disarm", "start", "stop", "rth", "light", "camera",
                            "manual_mode", "auto_mode", "dock"]:
                if btn_key not in self.btn_mapping_combos or btn_key not in self.btn_output_fields:
                    continue
                combo = self.btn_mapping_combos[btn_key]
                btn_idx = combo.currentData()
                
                if btn_idx is not None and btn_idx < num_buttons:
                    curr_state = self.joystick.get_button(btn_idx)
                    self.btn_output_fields[btn_key].setText(str(curr_state))
                    
                    prev_state = self._prev_btn_states.get(btn_key, 0)
                    if curr_state == 1 and prev_state == 0:
                        if btn_key == "arm":
                            self.arm_vehicle()
                        elif btn_key == "disarm":
                            self.disarm_vehicle()
                        elif btn_key == "start":
                            self.start_mission()
                        elif btn_key == "stop":
                            self.stop_mission()
                        elif btn_key == "rth":
                            self.return_to_home()
                        elif btn_key == "light":
                            if not getattr(self, 'failsafe_active', False):
                                new_light_state = not (self.light_state == 1)
                                self.update_light_ui_state(new_light_state)
                                if self.telemetry_thread and self.telemetry_thread.isRunning():
                                    self.telemetry_thread.light_state = self.light_state
                                    self.send_immediate_cmd()
                        elif btn_key == "camera":
                            if not getattr(self, 'failsafe_active', False):
                                new_camera_state = not (self.camera_state == 1)
                                self.update_camera_ui_state(new_camera_state)
                                if self.telemetry_thread and self.telemetry_thread.isRunning():
                                    self.telemetry_thread.camera_state = self.camera_state
                                    self.send_immediate_cmd()
                        elif btn_key == "manual_mode":
                            self.set_navigation_mode("manual")
                        elif btn_key == "auto_mode":
                            self.set_navigation_mode("automatic")
                        elif btn_key == "dock":
                            self.toggle_manual_docking()
                            
                    self._prev_btn_states[btn_key] = curr_state
                else:
                    self.btn_output_fields[btn_key].setText("0")
            
            # --- 4. Manual Override Safety Check ---
            # If in AUTO mode, check if joystick sticks are moved (outside neutral bounds 1400-1600)
            if not self.btn_manual.isChecked():
                joystick_moved = False
                for rc_key in ["rc1", "rc2", "rc3"]:
                    try:
                        pwm_val = int(self.rc_output_fields[rc_key].text())
                        if abs(pwm_val - 1500) > 100:
                            joystick_moved = True
                    except:
                        pass
                if joystick_moved:
                    print("[Failsafe Override] Gamepad stick movement detected during autonomous mission. Swapping to MANUAL mode!")
                    self.log_mission("Warning: Manual joystick override detected! Mission aborted.")
                    self.set_navigation_mode("manual")
                    self.stop_mission()

            # If manual mode is active, transmit joystick state to telemetry link
            if self.btn_manual.isChecked():
                if getattr(self, 'is_armed', False):
                    rc1_val = self.rc_output_fields["rc1"].text() if "rc1" in self.rc_output_fields else "1500"
                    rc2_val = self.rc_output_fields["rc2"].text() if "rc2" in self.rc_output_fields else "1500"
                    rc3_val = self.rc_output_fields["rc3"].text() if "rc3" in self.rc_output_fields else "1500"
                else:
                    rc1_val, rc2_val, rc3_val = "1500", "1500", "1500"
                payload = f"$MANUAL,{rc1_val},{rc2_val},{rc3_val}"
                if self.telemetry_thread and self.telemetry_thread.isRunning():
                    self.telemetry_thread.write_data(payload)
                    
        except Exception as e:
            print(f"[Joystick Poll Error] {e}. Disconnecting joystick.")
            self.disable_joystick(unexpected=True)

    def start_auto_detect_axis(self, rc_key):
        self.cancel_detection()
        self.detecting_axis_key = rc_key
        
        import pygame
        pygame.event.pump()
        if self.joystick is not None:
            self.detecting_initial_axes = {}
            for i in range(self.joystick.get_numaxes()):
                self.detecting_initial_axes[i] = self.joystick.get_axis(i)
                
        btn = self.rc_auto_detect_btns[rc_key]
        btn.setText("Detecting...")
        btn.setStyleSheet("background-color: #C62828; color: #FFFFFF; font-weight: bold; font-size: 10px; padding: 4px 8px; min-width: 75px;")

    def start_auto_detect_button(self, btn_key):
        self.cancel_detection()
        self.detecting_button_key = btn_key
        
        btn = self.btn_auto_detect_btns[btn_key]
        btn.setText("Detecting...")
        btn.setStyleSheet("background-color: #C62828; color: #FFFFFF; font-weight: bold; font-size: 10px; padding: 4px 8px; min-width: 75px;")

    def cancel_detection(self):
        if hasattr(self, 'detecting_axis_key') and self.detecting_axis_key is not None:
            key = self.detecting_axis_key
            self.detecting_axis_key = None
            btn = self.rc_auto_detect_btns.get(key)
            if btn:
                btn.setText("Auto Detect")
                btn.setStyleSheet("""
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
                """)
                
        if hasattr(self, 'detecting_button_key') and self.detecting_button_key is not None:
            key = self.detecting_button_key
            self.detecting_button_key = None
            btn = self.btn_auto_detect_btns.get(key)
            if btn:
                btn.setText("Auto Detect")
                btn.setStyleSheet("""
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
                """)

    def save_joystick_config(self):
        config = {
            "axes": {},
            "buttons": {},
            "thruster_limits": {
                "min": self.thruster_min_limit,
                "max": self.thruster_max_limit
            }
        }
        
        for rc_key in ["rc1", "rc2", "rc3"]:
            config["axes"][rc_key] = {
                "axis": self.rc_axis_combos[rc_key].currentData(),
                "reverse": self.rc_reverse_chks[rc_key].isChecked()
            }
            
        for btn_key in ["arm", "disarm", "start", "stop", "rth", "light", "camera",
                        "manual_mode", "auto_mode", "dock"]:
            config["buttons"][btn_key] = {
                "button": self.btn_mapping_combos[btn_key].currentData()
            }
            
        import json
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "joystick_config.json")
        try:
            with open(config_path, "w") as f:
                json.dump(config, f, indent=4)
            print(f"[Joystick Config] Configuration saved to {config_path}")
            self.lbl_config_status.setText("Config Saved Successfully")
            QTimer.singleShot(2000, lambda: self.lbl_config_status.setText("Loaded Config for ArduSub"))
        except Exception as e:
            print(f"[Joystick Config Save Error] {e}")
            self.lbl_config_status.setText("Error Saving Config")

    def load_joystick_config(self):
        import json
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "joystick_config.json")
        if not os.path.exists(config_path):
            print("[Joystick Config] No config file found. Using defaults.")
            self.thruster_min_limit = 1100
            self.thruster_max_limit = 1900
            if hasattr(self, 'input_thruster_min'):
                self.input_thruster_min.setText("1100")
            if hasattr(self, 'input_thruster_max'):
                self.input_thruster_max.setText("1900")
                
            default_axes = {"rc1": 0, "rc2": 1, "rc3": 3}
            default_buttons = {"arm": 0, "disarm": 1, "start": 2, "stop": 3, "rth": 4, "light": 5, "camera": 6}
            
            for rc_key, val in default_axes.items():
                combo = self.rc_axis_combos[rc_key]
                idx = combo.findData(val)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                    
            for btn_key, val in default_buttons.items():
                combo = self.btn_mapping_combos[btn_key]
                idx = combo.findData(val)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
            return
            
        try:
            with open(config_path, "r") as f:
                config = json.load(f)
                
            # Load axes
            axes_cfg = config.get("axes", {})
            for rc_key, cfg in axes_cfg.items():
                if rc_key in self.rc_axis_combos:
                    axis_val = cfg.get("axis")
                    combo = self.rc_axis_combos[rc_key]
                    idx = combo.findData(axis_val)
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
                        
                    rev_val = cfg.get("reverse", False)
                    self.rc_reverse_chks[rc_key].setChecked(rev_val)
                    
            # Load buttons
            btns_cfg = config.get("buttons", {})
            for btn_key, cfg in btns_cfg.items():
                if btn_key in self.btn_mapping_combos:
                    btn_val = cfg.get("button")
                    combo = self.btn_mapping_combos[btn_key]
                    idx = combo.findData(btn_val)
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
                        
            # Load thruster speed limits
            limits = config.get("thruster_limits", {})
            self.thruster_min_limit = limits.get("min", 1100)
            self.thruster_max_limit = limits.get("max", 1900)
            if hasattr(self, 'input_thruster_min'):
                self.input_thruster_min.setText(str(self.thruster_min_limit))
            if hasattr(self, 'input_thruster_max'):
                self.input_thruster_max.setText(str(self.thruster_max_limit))
                
            print(f"[Joystick Config] Configuration loaded from {config_path}")
            self.lbl_config_status.setText("Loaded Config for ArduSub")
        except Exception as e:
            print(f"[Joystick Config Load Error] {e}")

    def closeEvent(self, event):
        # Clean shutdown of worker threads
        self.disable_joystick()
        if self.telemetry_thread:
            self.telemetry_thread.stop()
        if hasattr(self, 'shutdown_camera_detection'):
            self.shutdown_camera_detection()
        if hasattr(self, 'shutdown_sensor_fusion'):
            self.shutdown_sensor_fusion()
        event.accept()

    def on_alert_badge_clicked(self):
        # 1. Switch active page stack index to Settings (Index 5)
        self.stacked_widget.setCurrentIndex(5)
        
        # 2. Update sidebar active item highlight
        self.sidebar.select_page(5)
        
        # 3. Switch settings page split category stack to Diagnostics (Index 2)
        if hasattr(self, 'select_diagnostics_settings'):
            self.select_diagnostics_settings()

    def show_toast_alert(self, message, is_critical):
        if not hasattr(self, '_active_toasts'):
            self._active_toasts = {}
        if message in self._active_toasts:
            return
        toast = ToastNotification(message, is_critical, self)
        self._active_toasts[message] = toast
        self.reposition_toasts()

    def reposition_toasts(self):
        if not hasattr(self, '_active_toasts'):
            return
        # Filter hidden/deleted toasts
        self._active_toasts = {msg: t for msg, t in self._active_toasts.items() if not t.isHidden()}
        y_offset = 65  # Offset below the TopBar
        parent_w = self.width()
        for msg, toast in self._active_toasts.items():
            toast.setFixedWidth(320)
            toast.adjustSize()
            x = parent_w - toast.width() - 20
            toast.move(x, y_offset)
            toast.show()
            y_offset += toast.height() + 10

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reposition_toasts()


class ToastNotification(QFrame):
    def __init__(self, message, is_critical, main_win):
        super().__init__(main_win)
        self.main_win = main_win
        self.message = message
        
        self.setWindowFlags(Qt.SubWindow | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_StyledBackground, True)
        
        # Color schemes based on alert level
        self.is_critical = is_critical
        border_color = "#FF4757" if is_critical else "#FF9F43"
        bg_color = "rgba(45, 8, 13, 0.95)" if is_critical else "rgba(42, 24, 0, 0.95)"
        text_color = "#FFFFFF"
        
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {bg_color};
                border: 1.5px solid {border_color};
                border-left: 6px solid {border_color};
                border-radius: 6px;
            }}
            QLabel {{
                color: {text_color};
                font-family: 'Google Sans', sans-serif;
                font-size: 11px;
                font-weight: bold;
                border: none;
                background: transparent;
            }}
            QPushButton {{
                color: #888888;
                font-family: 'Google Sans', sans-serif;
                font-size: 12px;
                font-weight: bold;
                border: none;
                background: transparent;
            }}
            QPushButton:hover {{
                color: #FFFFFF;
            }}
        """)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 10, 10, 10)
        layout.setSpacing(10)
        
        icon_lbl = QLabel("!" if is_critical else "!")
        layout.addWidget(icon_lbl)
        
        self.msg_lbl = QLabel(message)
        self.msg_lbl.setWordWrap(True)
        layout.addWidget(self.msg_lbl, 1)
        
        close_btn = QPushButton("✕")
        close_btn.setFixedWidth(20)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.clicked.connect(self.close_toast)
        layout.addWidget(close_btn)
        
        # Auto-close timer for non-critical warnings
        if not is_critical:
            self.timer = QTimer(self)
            self.timer.setInterval(5000)
            self.timer.timeout.connect(self.close_toast)
            self.timer.start()
            
    def close_toast(self):
        self.hide()
        if hasattr(self.main_win, '_active_toasts') and self.message in self.main_win._active_toasts:
            self.main_win._active_toasts.pop(self.message, None)
        self.main_win.reposition_toasts()
        self.deleteLater()

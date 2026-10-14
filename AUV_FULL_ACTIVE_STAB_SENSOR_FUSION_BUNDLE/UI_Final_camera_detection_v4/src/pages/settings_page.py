import os
import math
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QVBoxLayout, QFrame, QLabel, 
                               QPushButton, QStackedWidget, QComboBox, QDoubleSpinBox, 
                               QLineEdit, QTextEdit, QSplitter, QCheckBox, QRadioButton, QFileDialog)
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QBrush, QColor, QPen

def browse_log_file(parent):
    folder_path = QFileDialog.getExistingDirectory(
        parent,
        "Select Telemetry Log Directory",
        parent.log_folder_path
    )
    if folder_path:
        parent.log_folder_path = folder_path
        parent.txt_log_path.setText(folder_path)
        parent.log_file_path = None
        print(f"[Settings] Log directory updated to: {folder_path}")

def toggle_logging_state(parent, state):
    parent.logging_enabled = parent.chk_logging.isChecked()
    status_str = "ENABLED" if parent.logging_enabled else "DISABLED"
    print(f"[Settings] Telemetry logging {status_str}")

def on_theme_changed(parent, text):
    clean_text = text.upper()
    if "COCKPIT" in clean_text or "DARK" in clean_text:
        selected_theme = "cockpit"
    elif "LIGHT" in clean_text or "CLEAN" in clean_text:
        selected_theme = "light"
    else:
        selected_theme = "ocean"
    parent.apply_theme(selected_theme)

def on_basemap_changed(parent, source):
    source = "street" if source == "street" else "satellite"
    parent.docking_settings.setValue("map/basemap", source)
    js = f"if (typeof setBaseMap === 'function') setBaseMap('{source}');"
    for attr in ("web_view", "plan_web_view"):
        view = getattr(parent, attr, None)
        if view is not None:
            view.page().runJavaScript(js)
    
def on_wp_threshold_changed(parent, value):
    import json, os
    from src.pages.setup_page import get_autopilot_config_path
    parent.wp_reach_threshold = float(value)
    # Mirror this Settings-page control into Hardware Setup and the backend so
    # there is never a separate UI-only "reached" threshold.
    field = getattr(parent, "ap_input_WAYPOINT_TOLERANCE", None)
    if field is not None:
        field.setText(str(parent.wp_reach_threshold))
    cfg = {}
    try:
        cfg_path = get_autopilot_config_path()
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        cfg["WAYPOINT_TOLERANCE"] = parent.wp_reach_threshold
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
    except Exception as exc:
        print(f"[Settings] Could not persist waypoint tolerance: {exc}")
    if parent.telemetry_thread and parent.telemetry_thread.isRunning():
        parent.telemetry_thread.write_data(f"$CONFIG,WAYPOINT_TOLERANCE={parent.wp_reach_threshold}")
    print(f"[Settings] Waypoint reached threshold updated UI+backend to: {parent.wp_reach_threshold} meters")

def on_docking_location_changed(parent, latitude, longitude, heading=None):
    parent.docking_station_latitude = float(latitude)
    parent.docking_station_longitude = float(longitude)
    if heading is not None:
        parent.docking_station_heading = float(heading)
    heading = getattr(parent, 'docking_station_heading', 0.0)
    dock_len = parent._dock_projection_length() if hasattr(parent, '_dock_projection_length') else 10.0
    if hasattr(parent, 'save_docking_station_settings'):
        parent.save_docking_station_settings()
    else:
        for view_name in ('web_view', 'plan_web_view'):
            view = getattr(parent, view_name, None)
            if view:
                view.page().runJavaScript(
                    f"if (typeof setDockingStation === 'function') setDockingStation({parent.docking_station_latitude:.7f}, {parent.docking_station_longitude:.7f}, {heading}, {dock_len});"
                )
    print(f"[Settings] Docking Station updated: {parent.docking_station_latitude:.7f}, {parent.docking_station_longitude:.7f}, heading {heading:.1f}")
    if hasattr(parent, 'docking_visual_widget') and parent.docking_visual_widget:
        auv_lat = getattr(parent, 'current_latitude', None)
        auv_lon = getattr(parent, 'current_longitude', None)
        try:
            valid_auv_position = (
                auv_lat is not None and auv_lon is not None
                and math.isfinite(float(auv_lat)) and math.isfinite(float(auv_lon))
                and -90.0 <= float(auv_lat) <= 90.0
                and -180.0 <= float(auv_lon) <= 180.0
                and not (float(auv_lat) == 0.0 and float(auv_lon) == 0.0)
            )
        except (TypeError, ValueError):
            valid_auv_position = False
        parent.docking_visual_widget.update_positions(
            station_lat=parent.docking_station_latitude,
            station_lon=parent.docking_station_longitude,
            auv_lat=auv_lat if valid_auv_position else None,
            auv_lon=auv_lon if valid_auv_position else None,
            heading=getattr(parent, 'current_heading', None),
            depth=getattr(parent, 'current_depth', None),
            pitch=getattr(parent, 'current_pitch', None),
            station_heading=heading
        )

def on_vessel_icon_changed(parent, choice):
    mapping = {
        "Pulse Dot (Default)": "dot",
        "Directional Arrow": "arrow",
        "Surface Vessel (Boat)": "boat",
        "Subsea Node (Submarine)": "submarine"
    }
    parent.vessel_icon_type = mapping.get(choice, "dot")
    
    # Immediately push update to the Leaflet maps
    if hasattr(parent, 'web_view') and parent.web_view:
        parent.web_view.page().runJavaScript(f"setVesselIcon('{parent.vessel_icon_type}');")
    if hasattr(parent, 'plan_web_view') and parent.plan_web_view:
        parent.plan_web_view.page().runJavaScript(f"setVesselIcon('{parent.vessel_icon_type}');")
        
    print(f"[Mission Control] Map vessel icon changed to: {parent.vessel_icon_type}")

def select_general_settings(parent):
    parent.btn_settings_general.setProperty("active", "true")
    parent.btn_settings_general.style().unpolish(parent.btn_settings_general)
    parent.btn_settings_general.style().polish(parent.btn_settings_general)
    
    parent.btn_settings_logger.setProperty("active", "false")
    parent.btn_settings_logger.style().unpolish(parent.btn_settings_logger)
    parent.btn_settings_logger.style().polish(parent.btn_settings_logger)
    
    parent.btn_settings_diagnostics.setProperty("active", "false")
    parent.btn_settings_diagnostics.style().unpolish(parent.btn_settings_diagnostics)
    parent.btn_settings_diagnostics.style().polish(parent.btn_settings_diagnostics)
    
    parent.settings_stacked.setCurrentIndex(0)
    parent.settings_breadcrumb.setText("SYSTEM SETTINGS > GENERAL")

def select_logger_settings(parent):
    parent.btn_settings_general.setProperty("active", "false")
    parent.btn_settings_general.style().unpolish(parent.btn_settings_general)
    parent.btn_settings_general.style().polish(parent.btn_settings_general)
    
    parent.btn_settings_logger.setProperty("active", "true")
    parent.btn_settings_logger.style().unpolish(parent.btn_settings_logger)
    parent.btn_settings_logger.style().polish(parent.btn_settings_logger)
    
    parent.btn_settings_diagnostics.setProperty("active", "false")
    parent.btn_settings_diagnostics.style().unpolish(parent.btn_settings_diagnostics)
    parent.btn_settings_diagnostics.style().polish(parent.btn_settings_diagnostics)
    
    parent.settings_stacked.setCurrentIndex(1)
    parent.settings_breadcrumb.setText("SYSTEM SETTINGS > TELEMETRY LOGGER")

def select_diagnostics_settings(parent):
    parent.btn_settings_general.setProperty("active", "false")
    parent.btn_settings_general.style().unpolish(parent.btn_settings_general)
    parent.btn_settings_general.style().polish(parent.btn_settings_general)
    
    parent.btn_settings_logger.setProperty("active", "false")
    parent.btn_settings_logger.style().unpolish(parent.btn_settings_logger)
    parent.btn_settings_logger.style().polish(parent.btn_settings_logger)
    
    parent.btn_settings_diagnostics.setProperty("active", "true")
    parent.btn_settings_diagnostics.style().unpolish(parent.btn_settings_diagnostics)
    parent.btn_settings_diagnostics.style().polish(parent.btn_settings_diagnostics)
    
    parent.settings_stacked.setCurrentIndex(2)
    parent.settings_breadcrumb.setText("SYSTEM SETTINGS > DATALINK DIAGNOSTICS")

def apply_theme(parent, theme_name):
    clean_name = str(theme_name).lower()
    if "cockpit" in clean_name or "dark" in clean_name:
        theme = "cockpit"
    elif "light" in clean_name or "clean" in clean_name:
        theme = "light"
    else:
        theme = "ocean"
        
    parent.current_theme = theme
    
    # 1. Update Dashboard theme (background, dial labels, sliders, controls)
    if hasattr(parent, 'apply_dashboard_theme'):
        parent.apply_dashboard_theme(theme)
        
    # 2. Update Docking theme (background, sonar profile, checklist, controls, logs table)
    if hasattr(parent, 'apply_docking_theme'):
        parent.apply_docking_theme(theme)

    # 2b. Camera Detection page
    if hasattr(parent, 'apply_camera_detection_theme'):
        parent.apply_camera_detection_theme(theme)
    if hasattr(parent, 'apply_sensor_fusion_theme'):
        parent.apply_sensor_fusion_theme(theme)
        
    # 3. Update all excavatable tiles on both Dashboard and Docking
    all_tiles = []
    if hasattr(parent, 'dash_tiles') and parent.dash_tiles:
        all_tiles.extend(parent.dash_tiles.values())
    if hasattr(parent, 'docking_tiles') and parent.docking_tiles:
        all_tiles.extend(parent.docking_tiles.values())
        
    for tile in all_tiles:
        if hasattr(tile, 'set_theme'):
            tile.set_theme(theme)
            
    # 4. Update all telemetry cards on both Dashboard and Docking
    all_cards = []
    if hasattr(parent, 'cards') and parent.cards:
        all_cards.extend(parent.cards.values())
    if hasattr(parent, 'docking_cards') and parent.docking_cards:
        all_cards.extend(parent.docking_cards.values())
        
    for card in all_cards:
        if hasattr(card, 'set_theme'):
            card.set_theme(theme)
        else:
            card.setProperty("theme", theme)
            card.style().unpolish(card)
            card.style().polish(card)
        
    # 5. Update custom gauges & visualizers
    gauges = [
        getattr(parent, 'horizon_widget', None),
        getattr(parent, 'horizon_3d_widget', None),
        getattr(parent, 'compass_widget', None),
        getattr(parent, 'map_horizon', None),
        getattr(parent, 'map_compass', None),
        getattr(parent, 'battery_gauge', None),
        getattr(parent, 'actuators_gauge', None),
        getattr(parent, 'docking_mag_vector', None),
        getattr(parent, 'docking_sonar', None),
        getattr(parent, 'docking_visual_widget', None)
    ]
    for gauge in gauges:
        if gauge is not None:
            if hasattr(gauge, 'set_theme'):
                gauge.set_theme(theme)
            elif hasattr(gauge, "theme"):
                gauge.theme = theme
                gauge.update()
            
    # 6. Update RealTime charts on both Dashboard and Docking
    charts = [
        getattr(parent, 'attitude_chart', None),
        getattr(parent, 'gps_chart', None),
        getattr(parent, 'env_chart', None),
        getattr(parent, 'power_chart', None),
        getattr(parent, 'actuators_chart', None),
        getattr(parent, 'docking_sonar_chart', None),
        getattr(parent, 'docking_mag_chart', None)
    ]
    for chart in charts:
        if chart is not None:
            if hasattr(chart, 'set_theme'):
                chart.set_theme(theme)
            elif hasattr(chart, "theme"):
                chart.theme = theme
                if theme == "cockpit":
                    bg_hex = "#1A1A1E"
                    title_color = "#FFFFFF"
                    label_color = "#A0A0A0"
                    grid_color = "#2E2E36"
                    border_color = "#2E2E36"
                elif theme == "light":
                    bg_hex = "#FFFFFF"
                    title_color = "#0A2540"
                    label_color = "#5A738E"
                    grid_color = "#EDF2F7"
                    border_color = "#D8E2ED"
                else:
                    bg_hex = "#0A1625"
                    title_color = "#8EB7E6"
                    label_color = "#8EB7E6"
                    grid_color = "#1C2A3A"
                    border_color = "#1C3B65"
                chart.chart().setBackgroundBrush(QBrush(QColor(bg_hex)))
                chart.chart().setTitleBrush(QBrush(QColor(title_color)))
                chart.setStyleSheet(f"border: 1px solid {border_color}; border-radius: 8px; background-color: {bg_hex};")
                chart.chart().legend().setLabelColor(QColor(title_color))
                chart.axis_y.setLabelsColor(QColor(label_color))
                chart.axis_y.setGridLinePen(QPen(QColor(grid_color), 1, Qt.DashLine))

    # 7. Keep Settings Page theme controls in sync
    if hasattr(parent, 'combo_theme') and parent.combo_theme:
        target_idx = 0 if theme == "cockpit" else (1 if theme == "ocean" else 2)
        if parent.combo_theme.currentIndex() != target_idx:
            parent.combo_theme.blockSignals(True)
            parent.combo_theme.setCurrentIndex(target_idx)
            parent.combo_theme.blockSignals(False)

    btn_active_style = """
        QPushButton {
            background-color: #0078D4;
            border: 1px solid #0078D4;
            border-radius: 5px;
            color: #FFFFFF;
            font-weight: bold;
            padding: 8px 12px;
            font-size: 11px;
        }
        QPushButton:hover {
            background-color: #1085E0;
        }
    """
    btn_inactive_style = """
        QPushButton {
            background-color: #252526;
            border: 1px solid #3c3c3d;
            border-radius: 5px;
            color: #CCCCCC;
            font-weight: bold;
            padding: 8px 12px;
            font-size: 11px;
        }
        QPushButton:hover {
            background-color: #323233;
            border-color: #555556;
            color: #FFFFFF;
        }
    """
    if hasattr(parent, 'btn_theme_cockpit') and parent.btn_theme_cockpit:
        parent.btn_theme_cockpit.setStyleSheet(btn_active_style if theme == "cockpit" else btn_inactive_style)
    if hasattr(parent, 'btn_theme_ocean') and parent.btn_theme_ocean:
        parent.btn_theme_ocean.setStyleSheet(btn_active_style if theme == "ocean" else btn_inactive_style)
    if hasattr(parent, 'btn_theme_light') and parent.btn_theme_light:
        parent.btn_theme_light.setStyleSheet(btn_active_style if theme == "light" else btn_inactive_style)

def send_diagnostics_command(parent):
    cmd = parent.input_diagnostics_cmd.text().strip()
    if not cmd:
        return
        
    parent.txt_diagnostics_terminal.append(f"<span style='color: #FF9100;'>&gt; [TX] {cmd}</span>")
    
    if parent.telemetry_thread and parent.telemetry_thread.isRunning():
        parent.telemetry_thread.write_data(cmd)
    else:
        parent.txt_diagnostics_terminal.append(f"<span style='color: #FF1744;'>[SYS] Datalink inactive. Command not sent.</span>")
        
    parent.input_diagnostics_cmd.clear()

def clear_diagnostics_terminal(parent):
    parent.txt_diagnostics_terminal.clear()

def log_diagnostics_raw(parent, line):
    parent.txt_diagnostics_terminal.append(f"<span style='color: #00FF66;'>&lt; [RX] {line}</span>")

def create_settings_page(parent):
    # Bind helper callbacks to parent dynamically so other components can access them
    parent.browse_log_file = lambda: browse_log_file(parent)
    parent.toggle_logging_state = lambda state: toggle_logging_state(parent, state)
    parent.on_theme_changed = lambda text: on_theme_changed(parent, text)
    parent.on_wp_threshold_changed = lambda value: on_wp_threshold_changed(parent, value)
    parent.on_docking_location_changed = lambda latitude, longitude, heading=None: on_docking_location_changed(
        parent, latitude, longitude, heading
    )
    parent.on_vessel_icon_changed = Slot(str)(lambda choice: on_vessel_icon_changed(parent, choice))
    parent.select_general_settings = lambda: select_general_settings(parent)
    parent.select_logger_settings = lambda: select_logger_settings(parent)
    parent.select_diagnostics_settings = lambda: select_diagnostics_settings(parent)
    parent.apply_theme = lambda theme_name: apply_theme(parent, theme_name)
    parent.send_diagnostics_command = lambda: send_diagnostics_command(parent)
    parent.clear_diagnostics_terminal = lambda: clear_diagnostics_terminal(parent)
    parent.log_diagnostics_raw = lambda line: log_diagnostics_raw(parent, line)
    
    # Resolve absolute paths for the settings page assets
    src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    down_arrow_path = os.path.join(src_dir, "down_arrow.png").replace("\\", "/")
    check_path = os.path.join(src_dir, "check.png").replace("\\", "/")
    
    page = QWidget()
    page.setObjectName("SettingsPage")
    page.setStyleSheet(f"""
        QWidget#SettingsPage {{
            background-color: #1e1e1f;
            color: #FFFFFF;
            font-family: 'Google Sans', sans-serif;
        }}
        QLabel {{
            color: #FFFFFF;
        }}
        QFrame#SettingCard {{
            background-color: #2a2a2b;
            border: 1px solid #3c3c3d;
            border-radius: 6px;
        }}
        QFrame#SettingCard:hover {{
            border-color: #0078D4;
            background-color: #323233;
        }}
        QLabel#SettingTitle {{
            color: #FFFFFF;
            font-size: 12px;
            font-weight: 600;
            background: transparent;
            border: none;
        }}
        QLabel#SettingDescription {{
            color: #cccccc;
            font-size: 10px;
            font-weight: normal;
            background: transparent;
            border: none;
        }}
        QComboBox {{
            background-color: #252526;
            border: 1px solid #444445;
            border-bottom: 1px solid #5a5a5b;
            border-radius: 4px;
            padding: 5px 30px 5px 10px;
            color: #FFFFFF;
            font-family: 'Google Sans', sans-serif;
            font-size: 11px;
            min-width: 180px;
        }}
        QComboBox:hover {{
            background-color: #2c2c2d;
            border-color: #0078D4;
        }}
        QComboBox::drop-down {{
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 25px;
            border: none;
        }}
        QComboBox::down-arrow {{
            image: url({down_arrow_path});
            width: 8px;
            height: 8px;
        }}
        QComboBox QAbstractItemView {{
            background-color: #1f1f1f;
            border: 1px solid #3c3c3d;
            color: #FFFFFF;
            selection-background-color: #0078D4;
            selection-color: #FFFFFF;
            outline: 0px;
            padding: 4px;
        }}
        QDoubleSpinBox {{
            background-color: #252526;
            border: 1px solid #444445;
            border-bottom: 1px solid #5a5a5b;
            border-radius: 4px;
            color: #FFFFFF;
            font-family: 'Google Sans', sans-serif;
            font-size: 11px;
            padding: 5px 8px;
            min-width: 100px;
        }}
        QDoubleSpinBox:focus {{
            border-color: #0078D4;
        }}
        QLineEdit#SettingLineEdit {{
            background-color: #252526;
            border: 1px solid #444445;
            border-bottom: 1px solid #5a5a5b;
            border-radius: 4px;
            color: #FFFFFF;
            font-family: 'Google Sans', sans-serif;
            font-size: 11px;
            padding: 5px 8px;
        }}
        QLineEdit#SettingLineEdit:focus {{
            border-color: #0078D4;
        }}
        QPushButton#SettingButton {{
            background-color: #2d2d2d;
            border: 1px solid #444445;
            border-bottom: 1px solid #5a5a5b;
            border-radius: 4px;
            color: #FFFFFF;
            font-family: 'Google Sans', sans-serif;
            font-size: 11px;
            font-weight: 600;
            padding: 5px 16px;
        }}
        QPushButton#SettingButton:hover {{
            background-color: #353536;
            border-color: #555556;
        }}
        QPushButton#SettingButton:pressed {{
            background-color: #202021;
        }}
        QCheckBox::indicator {{
            width: 16px;
            height: 16px;
            background-color: #252526;
            border: 1px solid #444445;
            border-radius: 3px;
        }}
        QCheckBox::indicator:unchecked:hover {{
            background-color: #2c2c2d;
            border-color: #555556;
        }}
        QCheckBox::indicator:checked {{
            image: url({check_path});
            background-color: #0078D4;
            border-color: #0078D4;
        }}
        QCheckBox::indicator:checked:hover {{
            background-color: #1085E0;
            border-color: #1085E0;
        }}
    """)
    
    main_layout = QHBoxLayout(page)
    main_layout.setContentsMargins(0, 0, 0, 0)
    main_layout.setSpacing(0)
    
    # Horizontal Splitter for Settings Menu and Content Panel
    parent.settings_splitter = QSplitter(Qt.Horizontal)
    parent.settings_splitter.setStyleSheet("""
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
    left_menu.setObjectName("SettingsLeftMenu")
    left_menu.setMinimumWidth(180)
    left_menu.setMaximumWidth(300)
    left_menu.setStyleSheet("""
        QFrame#SettingsLeftMenu {
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
            border: none;
            background: transparent;
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
    menu_layout.setAlignment(Qt.AlignTop)
    
    menu_title = QLabel("SETTINGS CATEGORY")
    menu_layout.addWidget(menu_title)
    
    parent.btn_settings_general = QPushButton("GENERAL")
    parent.btn_settings_general.setProperty("active", "true")
    menu_layout.addWidget(parent.btn_settings_general)
    
    parent.btn_settings_logger = QPushButton("TELEMETRY LOGGER")
    parent.btn_settings_logger.setProperty("active", "false")
    menu_layout.addWidget(parent.btn_settings_logger)
    
    parent.btn_settings_diagnostics = QPushButton("DATALINK DIAGNOSTICS")
    parent.btn_settings_diagnostics.setProperty("active", "false")
    menu_layout.addWidget(parent.btn_settings_diagnostics)
    
    menu_layout.addStretch()
    parent.settings_splitter.addWidget(left_menu)
    
    # 2. Right Content Container (With Breadcrumb at Top + Stacked Widget)
    right_container = QWidget()
    right_container.setObjectName("SettingsRightContainer")
    right_container.setStyleSheet("""
        QWidget#SettingsRightContainer {
            background-color: #1e1e1f;
        }
    """)
    right_container_layout = QVBoxLayout(right_container)
    right_container_layout.setContentsMargins(0, 0, 0, 0)
    right_container_layout.setSpacing(0)
    
    # Breadcrumb header
    parent.settings_breadcrumb = QLabel("SYSTEM SETTINGS > GENERAL")
    parent.settings_breadcrumb.setStyleSheet("""
        color: #FFFFFF;
        background-color: #161616;
        border-bottom: 1px solid #2d2d2d;
        font-family: 'Google Sans', sans-serif;
        font-size: 11px;
        font-weight: bold;
        padding: 15px 30px;
        letter-spacing: 0.5px;
    """)
    right_container_layout.addWidget(parent.settings_breadcrumb)
    
    # Stacked widget to display selected option
    parent.settings_stacked = QStackedWidget()
    right_container_layout.addWidget(parent.settings_stacked, 1)
    
    parent.settings_splitter.addWidget(right_container)
    parent.settings_splitter.setSizes([200, 800])
    main_layout.addWidget(parent.settings_splitter, 1)
    
    # --- Page 1: General Page ---
    general_page = QWidget()
    general_layout = QVBoxLayout(general_page)
    general_layout.setContentsMargins(30, 20, 30, 30)
    general_layout.setSpacing(12)
    general_layout.setAlignment(Qt.AlignTop)
    
    gen_title = QLabel("General System Settings")
    gen_title.setStyleSheet("color: #FFFFFF; font-size: 16px; font-weight: bold; letter-spacing: 0.5px; margin-bottom: 10px; border: none; background: transparent;")
    general_layout.addWidget(gen_title)
    
    # Theme Switcher Card
    theme_card = QFrame()
    theme_card.setObjectName("SettingCard")
    theme_card_lay = QVBoxLayout(theme_card)
    theme_card_lay.setContentsMargins(16, 14, 16, 14)
    theme_card_lay.setSpacing(12)
    
    theme_top_row = QHBoxLayout()
    theme_top_row.setSpacing(15)
    
    theme_info = QVBoxLayout()
    theme_info.setSpacing(2)
    theme_title = QLabel("Dashboard & Docking Background Theme")
    theme_title.setObjectName("SettingTitle")
    theme_desc = QLabel("Switch visual theme and backgrounds for Dashboard, Docking, Tiles, and Telemetry cards")
    theme_desc.setObjectName("SettingDescription")
    theme_info.addWidget(theme_title)
    theme_info.addWidget(theme_desc)
    
    parent.combo_theme = QComboBox()
    parent.combo_theme.addItems(["COCKPIT Theme (Dark)", "OCEAN Theme (Blue)", "LIGHT Theme (Clean)"])
    if parent.current_theme == "cockpit":
        parent.combo_theme.setCurrentIndex(0)
    elif parent.current_theme == "ocean":
        parent.combo_theme.setCurrentIndex(1)
    else:
        parent.combo_theme.setCurrentIndex(2)
    parent.combo_theme.currentTextChanged.connect(parent.on_theme_changed)
    
    theme_top_row.addLayout(theme_info, 1)
    theme_top_row.addWidget(parent.combo_theme)
    theme_card_lay.addLayout(theme_top_row)
    
    # Quick Switch Buttons Row
    btn_row = QHBoxLayout()
    btn_row.setSpacing(10)
    
    parent.btn_theme_cockpit = QPushButton("🌙  COCKPIT (DARK)")
    parent.btn_theme_cockpit.setToolTip("Tactical dark theme with high-contrast amber telemetry")
    parent.btn_theme_cockpit.setCursor(Qt.PointingHandCursor)
    parent.btn_theme_cockpit.clicked.connect(lambda: parent.apply_theme("cockpit"))
    
    parent.btn_theme_ocean = QPushButton("🌊  OCEAN (BLUE)")
    parent.btn_theme_ocean.setToolTip("Deep abyss subsea blue with glowing cyan accents")
    parent.btn_theme_ocean.setCursor(Qt.PointingHandCursor)
    parent.btn_theme_ocean.clicked.connect(lambda: parent.apply_theme("ocean"))
    
    parent.btn_theme_light = QPushButton("☀️  LIGHT (CLEAN)")
    parent.btn_theme_light.setToolTip("Daylight clean slate theme with high-contrast navy text")
    parent.btn_theme_light.setCursor(Qt.PointingHandCursor)
    parent.btn_theme_light.clicked.connect(lambda: parent.apply_theme("light"))
    
    btn_row.addWidget(parent.btn_theme_cockpit)
    btn_row.addWidget(parent.btn_theme_ocean)
    btn_row.addWidget(parent.btn_theme_light)
    theme_card_lay.addLayout(btn_row)
    
    general_layout.addWidget(theme_card)

    # Joystick startup connection preference
    joystick_auto_card = QFrame()
    joystick_auto_card.setObjectName("SettingCard")
    joystick_auto_layout = QHBoxLayout(joystick_auto_card)
    joystick_auto_layout.setContentsMargins(16, 12, 16, 12)
    joystick_auto_layout.setSpacing(15)
    joystick_auto_info = QVBoxLayout()
    joystick_auto_info.setSpacing(2)
    joystick_auto_title = QLabel("Joystick Auto Connect")
    joystick_auto_title.setObjectName("SettingTitle")
    joystick_auto_desc = QLabel("Automatically enable the saved joystick and control configuration when the app starts.")
    joystick_auto_desc.setObjectName("SettingDescription")
    joystick_auto_info.addWidget(joystick_auto_title)
    joystick_auto_info.addWidget(joystick_auto_desc)
    joystick_auto_layout.addLayout(joystick_auto_info, 1)
    parent.chk_joystick_auto_enable = QCheckBox("Enable")
    parent.chk_joystick_auto_enable.setChecked(bool(parent.joystick_auto_enable))
    parent.chk_joystick_auto_enable.toggled.connect(parent.set_joystick_auto_enable)
    joystick_auto_layout.addWidget(parent.chk_joystick_auto_enable)
    general_layout.addWidget(joystick_auto_card)

    # Map basemap selector
    basemap_card = QFrame()
    basemap_card.setObjectName("SettingCard")
    basemap_layout = QHBoxLayout(basemap_card)
    basemap_layout.setContentsMargins(16, 12, 16, 12)
    basemap_layout.setSpacing(15)
    basemap_info = QVBoxLayout()
    basemap_info.setSpacing(2)
    basemap_title = QLabel("Map Basemap")
    basemap_title.setObjectName("SettingTitle")
    basemap_desc = QLabel("Choose the background layer used on the navigation and planning maps.")
    basemap_desc.setObjectName("SettingDescription")
    basemap_info.addWidget(basemap_title)
    basemap_info.addWidget(basemap_desc)
    basemap_layout.addLayout(basemap_info, 1)
    basemap_options = QHBoxLayout()
    parent.radio_basemap_satellite = QRadioButton("Satellite")
    parent.radio_basemap_street = QRadioButton("Street")
    basemap_choice = str(parent.docking_settings.value("map/basemap", "satellite")).lower()
    parent.radio_basemap_satellite.setChecked(basemap_choice != "street")
    parent.radio_basemap_street.setChecked(basemap_choice == "street")
    parent.radio_basemap_satellite.toggled.connect(
        lambda checked: on_basemap_changed(parent, "satellite") if checked else None)
    parent.radio_basemap_street.toggled.connect(
        lambda checked: on_basemap_changed(parent, "street") if checked else None)
    basemap_options.addWidget(parent.radio_basemap_satellite)
    basemap_options.addWidget(parent.radio_basemap_street)
    basemap_layout.addLayout(basemap_options)
    general_layout.addWidget(basemap_card)
    
    # Waypoint Reached Threshold
    wp_card = QFrame()
    wp_card.setObjectName("SettingCard")
    wp_card_lay = QHBoxLayout(wp_card)
    wp_card_lay.setContentsMargins(16, 12, 16, 12)
    wp_card_lay.setSpacing(15)
    
    wp_info = QVBoxLayout()
    wp_info.setSpacing(2)
    wp_title = QLabel("Waypoint Reached Threshold")
    wp_title.setObjectName("SettingTitle")
    wp_desc = QLabel("Set proximity radius threshold (in meters) to mark a waypoint as successfully reached.")
    wp_desc.setObjectName("SettingDescription")
    wp_info.addWidget(wp_title)
    wp_info.addWidget(wp_desc)
    
    parent.spin_wp_threshold = QDoubleSpinBox()
    parent.spin_wp_threshold.setRange(0.5, 50.0)
    parent.spin_wp_threshold.setSingleStep(0.5)
    parent.spin_wp_threshold.setValue(parent.wp_reach_threshold)
    parent.spin_wp_threshold.setSuffix(" meters")
    parent.spin_wp_threshold.valueChanged.connect(parent.on_wp_threshold_changed)
    
    wp_card_lay.addLayout(wp_info, 1)
    wp_card_lay.addWidget(parent.spin_wp_threshold)
    general_layout.addWidget(wp_card)
    
    # Docking Station Location (GPS)
    dock_location_card = QFrame()
    dock_location_card.setObjectName("SettingCard")
    dock_location_layout = QHBoxLayout(dock_location_card)
    dock_location_layout.setContentsMargins(16, 12, 16, 12)
    dock_location_layout.setSpacing(15)
    
    dock_location_info = QVBoxLayout()
    dock_location_info.setSpacing(2)
    dock_location_title = QLabel("GPS Docking Route")
    dock_location_title.setObjectName("SettingTitle")
    dock_location_desc = QLabel("Set the docking station position and approach heading.")
    dock_location_desc.setObjectName("SettingDescription")
    dock_location_info.addWidget(dock_location_title)
    dock_location_info.addWidget(dock_location_desc)
    
    dock_location_controls = QVBoxLayout()
    dock_location_controls.setSpacing(6)

    def gps_coordinate(latitude, value):
        spin = QDoubleSpinBox()
        spin.setRange(-90.0 if latitude else -180.0, 90.0 if latitude else 180.0)
        spin.setDecimals(7)
        spin.setSingleStep(0.0000001)
        spin.setValue(value)
        spin.setMinimumWidth(160)
        return spin

    parent.spin_docking_latitude = gps_coordinate(
        True, getattr(parent, 'docking_station_latitude', 13.0827000))
    parent.spin_docking_longitude = gps_coordinate(
        False, getattr(parent, 'docking_station_longitude', 80.2707000))
    parent.spin_docking_heading = QDoubleSpinBox()
    parent.spin_docking_heading.setRange(0.0, 359.9)
    parent.spin_docking_heading.setDecimals(1)
    parent.spin_docking_heading.setSingleStep(1.0)
    parent.spin_docking_heading.setValue(getattr(parent, 'docking_station_heading', 0.0))
    parent.spin_docking_heading.setMinimumWidth(160)

    dock_lat_field = QVBoxLayout()
    dock_lat_field.setSpacing(4)
    dock_lat_field.addWidget(QLabel("DOCK LATITUDE"))
    dock_lat_field.addWidget(parent.spin_docking_latitude)
    dock_lon_field = QVBoxLayout()
    dock_lon_field.setSpacing(4)
    dock_lon_field.addWidget(QLabel("DOCK LONGITUDE"))
    dock_lon_field.addWidget(parent.spin_docking_longitude)
    dock_row = QHBoxLayout()
    dock_row.setSpacing(8)
    dock_row.addLayout(dock_lat_field)
    dock_row.addLayout(dock_lon_field)
    dock_location_controls.addLayout(dock_row)
    heading_row = QHBoxLayout()
    heading_row.setSpacing(8)
    heading_field = QVBoxLayout()
    heading_field.setSpacing(4)
    heading_field.addWidget(QLabel("DOCKING HEADING"))
    heading_field.addWidget(parent.spin_docking_heading)
    heading_row.addLayout(heading_field)
    dock_location_controls.addLayout(heading_row)

    parent.btn_set_docking_route = QPushButton("SET")
    parent.btn_set_docking_route.setCursor(Qt.PointingHandCursor)
    parent.btn_set_docking_route.setMinimumWidth(84)
    parent.btn_set_docking_route.clicked.connect(parent.set_docking_route)
    dock_location_controls.addWidget(parent.btn_set_docking_route, 0, Qt.AlignRight)

    parent.spin_docking_latitude.valueChanged.connect(
        lambda _: parent.on_docking_location_changed(parent.spin_docking_latitude.value(), parent.spin_docking_longitude.value())
    )
    parent.spin_docking_longitude.valueChanged.connect(
        lambda _: parent.on_docking_location_changed(parent.spin_docking_latitude.value(), parent.spin_docking_longitude.value())
    )
    parent.spin_docking_heading.valueChanged.connect(
        lambda _: parent.on_docking_location_changed(
            parent.spin_docking_latitude.value(),
            parent.spin_docking_longitude.value(),
            parent.spin_docking_heading.value()
        )
    )

    dock_location_layout.addLayout(dock_location_info, 1)
    dock_location_layout.addLayout(dock_location_controls)
    general_layout.addWidget(dock_location_card)
    
    # Vessel Icon Selection
    icon_card = QFrame()
    icon_card.setObjectName("SettingCard")
    icon_card_lay = QHBoxLayout(icon_card)
    icon_card_lay.setContentsMargins(16, 12, 16, 12)
    icon_card_lay.setSpacing(15)
    
    icon_info = QVBoxLayout()
    icon_info.setSpacing(2)
    icon_title = QLabel("Map Vessel Icon")
    icon_title.setObjectName("SettingTitle")
    icon_desc = QLabel("Choose the vehicle icon representation style on the Leaflet navigation maps.")
    icon_desc.setObjectName("SettingDescription")
    icon_info.addWidget(icon_title)
    icon_info.addWidget(icon_desc)
    
    parent.combo_vessel_icon = QComboBox()
    parent.combo_vessel_icon.addItems(["Pulse Dot (Default)", "Directional Arrow", "Surface Vessel (Boat)", "Subsea Node (Submarine)"])
    parent.combo_vessel_icon.setCurrentIndex(0)
    parent.combo_vessel_icon.currentTextChanged.connect(parent.on_vessel_icon_changed)
    
    icon_card_lay.addLayout(icon_info, 1)
    icon_card_lay.addWidget(parent.combo_vessel_icon)
    general_layout.addWidget(icon_card)
    
    parent.settings_stacked.addWidget(general_page)
    
    # --- Page 2: Logger Page ---
    logger_page = QWidget()
    logger_layout = QVBoxLayout(logger_page)
    logger_layout.setContentsMargins(30, 20, 30, 30)
    logger_layout.setSpacing(12)
    logger_layout.setAlignment(Qt.AlignTop)
    
    logger_title = QLabel("Telemetry Logger Configuration")
    logger_title.setStyleSheet("color: #FFFFFF; font-size: 16px; font-weight: bold; letter-spacing: 0.5px; margin-bottom: 10px; border: none; background: transparent;")
    logger_layout.addWidget(logger_title)
    
    # Enable logging card
    logging_card = QFrame()
    logging_card.setObjectName("SettingCard")
    logging_card_lay = QHBoxLayout(logging_card)
    logging_card_lay.setContentsMargins(16, 12, 16, 12)
    logging_card_lay.setSpacing(15)
    
    logging_info = QVBoxLayout()
    logging_info.setSpacing(2)
    logging_title_lbl = QLabel("Live CSV Telemetry Logging")
    logging_title_lbl.setObjectName("SettingTitle")
    logging_desc_lbl = QLabel("Logs each incoming telemetry packet with its decoded fields and the original packet text.")
    logging_desc_lbl.setObjectName("SettingDescription")
    logging_info.addWidget(logging_title_lbl)
    logging_info.addWidget(logging_desc_lbl)
    
    parent.chk_logging = QCheckBox()
    parent.chk_logging.setChecked(parent.logging_enabled)
    parent.chk_logging.stateChanged.connect(parent.toggle_logging_state)
    
    logging_card_lay.addLayout(logging_info, 1)
    logging_card_lay.addWidget(parent.chk_logging)
    logger_layout.addWidget(logging_card)
    
    # Log path card
    path_card = QFrame()
    path_card.setObjectName("SettingCard")
    path_card_lay = QHBoxLayout(path_card)
    path_card_lay.setContentsMargins(16, 12, 16, 12)
    path_card_lay.setSpacing(15)
    
    path_info = QVBoxLayout()
    path_info.setSpacing(2)
    path_title_lbl = QLabel("Logging Directory")
    path_title_lbl.setObjectName("SettingTitle")
    path_desc_lbl = QLabel("Configure the ground station data logging destination path.")
    path_desc_lbl.setObjectName("SettingDescription")
    path_info.addWidget(path_title_lbl)
    path_info.addWidget(path_desc_lbl)
    
    path_controls = QHBoxLayout()
    path_controls.setSpacing(8)
    
    parent.txt_log_path = QLineEdit()
    parent.txt_log_path.setObjectName("SettingLineEdit")
    parent.txt_log_path.setReadOnly(True)
    parent.txt_log_path.setText(parent.log_folder_path)
    parent.txt_log_path.setMinimumWidth(250)
    
    btn_browse = QPushButton("Browse")
    btn_browse.setObjectName("SettingButton")
    btn_browse.clicked.connect(parent.browse_log_file)
    
    path_controls.addWidget(parent.txt_log_path)
    path_controls.addWidget(btn_browse)
    
    path_card_lay.addLayout(path_info, 1)
    path_card_lay.addLayout(path_controls)
    logger_layout.addWidget(path_card)
    
    parent.settings_stacked.addWidget(logger_page)
    
    # --- Page 3: Diagnostics Page ---
    diagnostics_page = QWidget()
    diagnostics_layout = QVBoxLayout(diagnostics_page)
    diagnostics_layout.setContentsMargins(30, 20, 30, 30)
    diagnostics_layout.setSpacing(12)
    diagnostics_layout.setAlignment(Qt.AlignTop)
    
    diag_title = QLabel("Datalink Diagnostics Console")
    diag_title.setStyleSheet("color: #FFFFFF; font-size: 16px; font-weight: bold; letter-spacing: 0.5px; margin-bottom: 10px; border: none; background: transparent;")
    diagnostics_layout.addWidget(diag_title)
    
    diag_card = QFrame()
    diag_card.setObjectName("SettingCard")
    diag_lay = QVBoxLayout(diag_card)
    diag_lay.setContentsMargins(20, 20, 20, 20)
    diag_lay.setSpacing(15)
    
    diag_desc = QLabel("Transmit manual action packets (e.g. ARM, DISARM, START, STOP, RTH, WP,...) and observe raw subsea datagram frames in real-time.")
    diag_desc.setObjectName("SettingDescription")
    diag_desc.setWordWrap(True)
    diag_lay.addWidget(diag_desc)
    
    parent.txt_diagnostics_terminal = QTextEdit()
    parent.txt_diagnostics_terminal.setReadOnly(True)
    parent.txt_diagnostics_terminal.setMinimumHeight(240)
    parent.txt_diagnostics_terminal.setStyleSheet("""
        QTextEdit {
            background-color: #121212;
            border: 1px solid #323232;
            border-radius: 4px;
            color: #00FF66;
            font-family: 'Consolas', 'Courier New', monospace;
            font-size: 11px;
            padding: 10px;
        }
    """)
    diag_lay.addWidget(parent.txt_diagnostics_terminal)
    
    input_row = QHBoxLayout()
    input_row.setSpacing(10)
    
    parent.input_diagnostics_cmd = QLineEdit()
    parent.input_diagnostics_cmd.setPlaceholderText("Enter command (e.g. ARM, DISARM, RTH, WP,1,12.98,80.24)...")
    parent.input_diagnostics_cmd.setStyleSheet("""
        QLineEdit {
            background-color: #121212;
            border: 1px solid #323232;
            border-radius: 4px;
            color: #00FF66;
            font-family: 'Consolas', 'Courier New', monospace;
            font-size: 11px;
            padding: 8px;
        }
        QLineEdit:focus {
            border-color: #0078D4;
        }
    """)
    parent.input_diagnostics_cmd.returnPressed.connect(parent.send_diagnostics_command)
    
    btn_send_cmd = QPushButton("SEND")
    btn_send_cmd.setObjectName("SettingButton")
    btn_send_cmd.setStyleSheet("""
        QPushButton {
            background-color: #0078D4;
            border: 1px solid #0078D4;
            border-radius: 4px;
            color: #FFFFFF;
            font-weight: bold;
            padding: 8px 20px;
        }
        QPushButton:hover {
            background-color: #1085E0;
            border-color: #1085E0;
        }
    """)
    btn_send_cmd.clicked.connect(parent.send_diagnostics_command)
    
    btn_clear_cmd = QPushButton("CLEAR")
    btn_clear_cmd.setObjectName("SettingButton")
    btn_clear_cmd.clicked.connect(parent.clear_diagnostics_terminal)
    
    input_row.addWidget(parent.input_diagnostics_cmd, 1)
    input_row.addWidget(btn_send_cmd)
    input_row.addWidget(btn_clear_cmd)
    diag_lay.addLayout(input_row)
    
    diagnostics_layout.addWidget(diag_card)
    parent.settings_stacked.addWidget(diagnostics_page)
    
    # Connect menu selection click actions
    parent.btn_settings_general.clicked.connect(parent.select_general_settings)
    parent.btn_settings_logger.clicked.connect(parent.select_logger_settings)
    parent.btn_settings_diagnostics.clicked.connect(parent.select_diagnostics_settings)
    
    return page

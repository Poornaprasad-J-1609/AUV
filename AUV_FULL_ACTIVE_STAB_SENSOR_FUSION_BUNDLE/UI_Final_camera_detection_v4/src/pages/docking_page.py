import math
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QGridLayout, QFrame, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView, QScrollArea, QPushButton,
    QProgressBar
)
from PySide6.QtCore import Qt, QDateTime
from PySide6.QtGui import QColor, QFont
from src.widgets import TelemetryCard, Ping360SonarWidget, MagnetometerVectorWidget, RealTimeChart, ExcavatableTile


def apply_docking_theme(parent, theme):
    if theme in ("cockpit", "dark"):
        container_bg = "#121214"
        card_bg = "#1E1E22"
        card_border = "#323238"
        text_primary = "#FFFFFF"
        text_secondary = "#A0A0A0"
        btn_bg = "#26262B"
        btn_border = "#3A3A42"
        btn_text = "#E0E0E0"
        btn_hover = "#303037"
        tbl_bg = "#1A1A1E"
        tbl_alt = "#222228"
        tbl_header_bg = "#26262D"
        tbl_header_fg = "#A0A0A0"
        tbl_grid = "#2C2C32"
    elif theme == "ocean":
        container_bg = "#050B14"
        card_bg = "#0F223C"
        card_border = "#1C3B65"
        text_primary = "#E2F1FF"
        text_secondary = "#8EB7E6"
        btn_bg = "#0F223C"
        btn_border = "#1C3B65"
        btn_text = "#E2F1FF"
        btn_hover = "#152F53"
        tbl_bg = "#0A1625"
        tbl_alt = "#0F223C"
        tbl_header_bg = "#152F53"
        tbl_header_fg = "#8EB7E6"
        tbl_grid = "#1C3B65"
    else: # light
        container_bg = "#F4F7FB"
        card_bg = "#FFFFFF"
        card_border = "#CBD5E1"
        text_primary = "#0A2540"
        text_secondary = "#5A738E"
        btn_bg = "#FFFFFF"
        btn_border = "#CBD5E1"
        btn_text = "#334155"
        btn_hover = "#F8FAFC"
        tbl_bg = "#FFFFFF"
        tbl_alt = "#F8FAFC"
        tbl_header_bg = "#F1F5F9"
        tbl_header_fg = "#475569"
        tbl_grid = "#E2E8F0"

    # 1. Container Background
    if hasattr(parent, 'docking_container') and parent.docking_container:
        parent.docking_container.setStyleSheet(f"background-color: {container_bg};")

    # 2. Sonar Profile Frame & Labels
    if hasattr(parent, 'docking_profile_frame') and parent.docking_profile_frame:
        parent.docking_profile_frame.setStyleSheet(f"""
            QFrame#SonarProfileFrame {{
                background-color: {card_bg};
                border: 1px solid {card_border};
                border-radius: 8px;
            }}
        """)
    if hasattr(parent, 'lbl_sonar_profile_title') and parent.lbl_sonar_profile_title:
        parent.lbl_sonar_profile_title.setStyleSheet(f"color: {text_secondary}; font-size: 10px; font-weight: bold; letter-spacing: 1px; background: transparent; border: none;")
    if hasattr(parent, 'lbl_sonar_profile_dist') and parent.lbl_sonar_profile_dist:
        parent.lbl_sonar_profile_dist.setStyleSheet(f"color: {text_primary}; font-size: 20px; font-weight: bold; background: transparent; border: none;")
    if hasattr(parent, 'lbl_sonar_profile_angle') and parent.lbl_sonar_profile_angle:
        parent.lbl_sonar_profile_angle.setStyleSheet(f"color: {text_secondary}; font-size: 11px; font-weight: 600; background: transparent; border: none;")
    if hasattr(parent, 'lbl_sonar_profile_note') and parent.lbl_sonar_profile_note:
        parent.lbl_sonar_profile_note.setStyleSheet(f"color: {text_secondary}; font-size: 9px; background: transparent; border: none;")
    if hasattr(parent, 'docking_sonar_chart') and parent.docking_sonar_chart:
        parent.docking_sonar_chart.set_theme(theme)

    # 3. Telemetry Logs Table
    if hasattr(parent, 'tbl_docking_logs') and parent.tbl_docking_logs:
        parent.tbl_docking_logs.setStyleSheet(f"""
            QTableWidget {{
                background-color: {tbl_bg};
                alternate-background-color: {tbl_alt};
                color: {text_primary};
                border: 1px solid {card_border};
                border-radius: 6px;
                gridline-color: {tbl_grid};
                font-family: 'Consolas', monospace;
                font-size: 10px;
            }}
            QHeaderView::section {{
                background-color: {tbl_header_bg};
                color: {tbl_header_fg};
                padding: 4px 6px;
                font-family: 'Google Sans', sans-serif;
                font-size: 9.5px;
                font-weight: bold;
                border: 1px solid {card_border};
            }}
        """)


def create_docking_page(parent):
    """
    Creates and returns the dedicated Docking Data page.
    Matches the exact 6-tile layout from operational specifications:
      1. Ping360 Acoustic Scanning Sonar (with Radar visualizer & Sonar Profile side-card)
      2. Sonar Obstacle Distance Profile Real-Time Chart (Time (s) X-axis)
      3. RM3100 Geomagnetic Sensor & Vector (Compass & 2D vector visualizer)
      4. Tri-Axial Magnetic Field Chart (Time (s) X-axis)
      5. Docking Approach & Guidance Monitor (Checklist & 2x2 control buttons)
      6. Docking/Telemetry Logs (Table with colored pill badges for LVL)
    Every tile is an ExcavatableTile supporting double-click standalone floating window view.
    """
    parent.apply_docking_theme = lambda theme: apply_docking_theme(parent, theme)
    parent.docking_check_labels = []

    page = QWidget()
    page.setObjectName("DockingPage")
    
    # Scroll area for responsive rendering on all display resolutions
    scroll_area = QScrollArea()
    scroll_area.setWidgetResizable(True)
    scroll_area.setFrameShape(QFrame.NoFrame)
    scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    scroll_area.setStyleSheet("QScrollArea { background-color: transparent; border: none; }")
    
    container = QWidget()
    container.setObjectName("DockingContainer")
    container.setStyleSheet("background-color: #F4F7FB;")
    parent.docking_container = container
    main_layout = QVBoxLayout(container)
    main_layout.setContentsMargins(12, 10, 12, 10)
    main_layout.setSpacing(12)
    
    # Initialize docking_cards dictionary on parent with clean light theme and matching icons
    # Subsystem accent colours - sonar / magnetics / guidance
    AC_SONAR = "#0EA5E9"
    AC_MAG = "#8B5CF6"
    AC_GUIDE = "#0D9488"

    parent.docking_cards = {
        # Sonar cards
        "sonar_range": TelemetryCard("SONAR RANGE", "—", "m", theme="light", accent=AC_SONAR),
        "sonar_bearing": TelemetryCard("SONAR BEARING", "—", "°", theme="light", accent=AC_SONAR),
        "sonar_scan": TelemetryCard("SCAN ANGLE", "0.0", "°", theme="light", accent=AC_SONAR),
        "sonar_conf": TelemetryCard("CONFIDENCE", "100", "%", theme="light", accent=AC_SONAR),
        # Mag cards
        "mx": TelemetryCard("MAG X", "0.0", "µT", theme="light", accent=AC_MAG),
        "my": TelemetryCard("MAG Y", "0.0", "µT", theme="light", accent=AC_MAG),
        "mz": TelemetryCard("MAG Z", "0.0", "µT", theme="light", accent=AC_MAG),
        "magb": TelemetryCard("TOTAL MAG B", "0.00", "µT", theme="light", accent=AC_MAG),
        "mag_heading": TelemetryCard("HEADING", "0.00", "°", theme="light", accent=AC_MAG)
    }
    
    # 3-Column horizontal operational layout (1:1:1 balanced distribution)
    cols_layout = QHBoxLayout()
    cols_layout.setSpacing(12)
    
    col_sonar = QVBoxLayout()
    col_sonar.setSpacing(12)
    
    col_mag = QVBoxLayout()
    col_mag.setSpacing(12)
    
    col_status = QVBoxLayout()
    col_status.setSpacing(12)
    
    # =========================================================================
    # COLUMN 1 - TILE 1: PING360 ACOUSTIC SCANNING SONAR
    # =========================================================================
    parent.tile_docking_sonar = ExcavatableTile(
        "docking_sonar", "PING360 - ACOUSTIC SCANNING SONAR", accent=AC_SONAR
    )
    sonar_layout = parent.tile_docking_sonar.content_layout
    
    # Sonar Cards Row
    sonar_cards_row = QHBoxLayout()
    sonar_cards_row.setSpacing(6)
    sonar_cards_row.addWidget(parent.docking_cards["sonar_range"])
    sonar_cards_row.addWidget(parent.docking_cards["sonar_bearing"])
    sonar_cards_row.addWidget(parent.docking_cards["sonar_scan"])
    sonar_cards_row.addWidget(parent.docking_cards["sonar_conf"])
    sonar_layout.addLayout(sonar_cards_row)
    
    # Interactive Sonar Radar Visualizer (Centered with breathing room)
    sonar_vis_container = QHBoxLayout()
    parent.docking_sonar = Ping360SonarWidget()
    parent.docking_sonar.setMinimumSize(190, 190)
    sonar_vis_container.addStretch(1)
    sonar_vis_container.addWidget(parent.docking_sonar, 6)
    sonar_vis_container.addStretch(1)
    sonar_layout.addLayout(sonar_vis_container, 1)
    
    col_sonar.addWidget(parent.tile_docking_sonar, 5)
    
    # =========================================================================
    # COLUMN 1 - TILE 2: SONAR OBSTACLE DISTANCE PROFILE (Sonar Profile Panel)
    # =========================================================================
    parent.tile_docking_sonar_chart = ExcavatableTile(
        "docking_sonar_chart", "SONAR OBSTACLE DISTANCE PROFILE", accent=AC_SONAR
    )
    sonar_chart_layout = parent.tile_docking_sonar_chart.content_layout
    # Compact live summary above a real-time distance history graph.
    profile_frame = QFrame()
    profile_frame.setObjectName("SonarProfileFrame")
    parent.docking_profile_frame = profile_frame
    profile_frame.setStyleSheet("""
        QFrame#SonarProfileFrame {
            background-color: #FFFFFF;
            border: 1px solid #E4EAF2;
            border-radius: 8px;
        }
    """)
    profile_layout = QVBoxLayout(profile_frame)
    profile_layout.setContentsMargins(16, 12, 16, 12)
    profile_layout.setSpacing(8)
    
    lbl_prof_title = QLabel("LIVE SONAR RETURN")
    parent.lbl_sonar_profile_title = lbl_prof_title
    lbl_prof_title.setStyleSheet("color: #6B7F96; font-size: 9px; font-weight: bold; letter-spacing: 1px; background: transparent; border: none;")
    profile_layout.addWidget(lbl_prof_title)

    profile_values = QHBoxLayout()
    profile_values.setSpacing(12)
    parent.lbl_sonar_profile_dist = QLabel("Distance: — m")
    parent.lbl_sonar_profile_dist.setStyleSheet("color: #0A2540; font-size: 20px; font-weight: bold; background: transparent; border: none;")
    profile_values.addWidget(parent.lbl_sonar_profile_dist, 1)
    parent.lbl_sonar_profile_angle = QLabel("Scan angle  — / 25°")
    parent.lbl_sonar_profile_angle.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    parent.lbl_sonar_profile_angle.setStyleSheet("color: #64748B; font-size: 11px; font-weight: 600; background: transparent; border: none;")
    profile_values.addWidget(parent.lbl_sonar_profile_angle)
    profile_layout.addLayout(profile_values)
    
    lbl_prof_note = QLabel("Latest obstacle return")
    parent.lbl_sonar_profile_note = lbl_prof_note
    lbl_prof_note.setStyleSheet("color: #9AA9BC; font-size: 9px; background: transparent; border: none;")
    profile_layout.addWidget(lbl_prof_note)
    sonar_chart_layout.addWidget(profile_frame)

    parent.docking_sonar_chart = RealTimeChart(
        "OBSTACLE DISTANCE HISTORY", 0.0, 10.0, "m",
        theme="light", show_x_axis=True, x_title="RECENT SCANS"
    )
    parent.docking_sonar_chart.add_series("Range", AC_SONAR)
    parent.docking_sonar_chart.setMinimumHeight(190)
    sonar_chart_layout.addWidget(parent.docking_sonar_chart, 1)
    
    col_sonar.addWidget(parent.tile_docking_sonar_chart, 3)
    
    # =========================================================================
    # COLUMN 2 - TILE 3: RM3100 GEOMAGNETIC SENSOR & 2D VECTOR LOCALIZATION
    # =========================================================================
    parent.tile_docking_mag = ExcavatableTile(
        "docking_mag", "RM3100 - GEOMAGNETIC SENSOR & VECTOR", accent=AC_MAG
    )
    mag_layout = parent.tile_docking_mag.content_layout
    
    # Mag Cards Row
    mag_cards_row = QHBoxLayout()
    mag_cards_row.setSpacing(6)
    mag_cards_row.addWidget(parent.docking_cards["mx"])
    mag_cards_row.addWidget(parent.docking_cards["my"])
    mag_cards_row.addWidget(parent.docking_cards["mz"])
    mag_cards_row.addWidget(parent.docking_cards["magb"])
    mag_cards_row.addWidget(parent.docking_cards["mag_heading"])
    mag_layout.addLayout(mag_cards_row)
    
    # Mag 2D Vector Visualizer
    mag_vis_container = QHBoxLayout()
    parent.docking_mag_vector = MagnetometerVectorWidget(theme="light")
    parent.docking_mag_vector.setMinimumSize(190, 190)
    mag_vis_container.addStretch(1)
    mag_vis_container.addWidget(parent.docking_mag_vector, 6)
    mag_vis_container.addStretch(1)
    mag_layout.addLayout(mag_vis_container, 1)
    
    col_mag.addWidget(parent.tile_docking_mag, 5)
    
    # =========================================================================
    # COLUMN 2 - TILE 4: TRI-AXIAL MAGNETIC FIELD (µT) CHART
    # =========================================================================
    parent.tile_docking_mag_chart = ExcavatableTile(
        "docking_mag_chart", "TRI-AXIAL MAGNETIC FIELD (µT)", accent=AC_MAG
    )
    mag_chart_layout = parent.tile_docking_mag_chart.content_layout
    
    parent.docking_mag_chart = RealTimeChart(
        "TRI-AXIAL MAGNETIC FIELD (µT)", -60.0, 60.0, "", theme="light", show_x_axis=True, x_title="Time (s)"
    )
    parent.docking_mag_chart.add_series("Mag X", "#F59E0B") # Yellow / Amber
    parent.docking_mag_chart.add_series("Mag Y", "#0D6EFD") # Blue
    parent.docking_mag_chart.add_series("Mag Z", "#8B5CF6") # Purple
    parent.docking_mag_chart.setMinimumHeight(140)
    mag_chart_layout.addWidget(parent.docking_mag_chart)
    
    col_mag.addWidget(parent.tile_docking_mag_chart, 3)
    
    # =========================================================================
    # COLUMN 3 - TILE 5: DOCKING/TELEMETRY LOGS
    # =========================================================================
    parent.tile_docking_logs = ExcavatableTile(
        "docking_logs", "DOCKING/TELEMETRY LOGS", accent=AC_GUIDE
    )
    logs_layout = parent.tile_docking_logs.content_layout
    
    parent.tbl_docking_logs = QTableWidget()
    parent.tbl_docking_logs.setColumnCount(4)
    parent.tbl_docking_logs.setHorizontalHeaderLabels(["TIME", "LVL", "SRC", "MESSAGE"])
    parent.tbl_docking_logs.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
    parent.tbl_docking_logs.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
    parent.tbl_docking_logs.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
    parent.tbl_docking_logs.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
    parent.tbl_docking_logs.verticalHeader().setVisible(False)
    parent.tbl_docking_logs.setAlternatingRowColors(True)
    parent.tbl_docking_logs.setEditTriggers(QTableWidget.NoEditTriggers)
    parent.tbl_docking_logs.setSelectionBehavior(QTableWidget.SelectRows)
    parent.tbl_docking_logs.setMinimumHeight(150)
    parent.tbl_docking_logs.setStyleSheet("""
        QTableWidget {
            background-color: #FFFFFF;
            alternate-background-color: #F8FAFC;
            color: #0A2540;
            border: 1px solid #E4EAF2;
            border-radius: 8px;
            gridline-color: #EDF1F6;
            font-family: 'Consolas', monospace;
            font-size: 10px;
        }
        QHeaderView::section {
            background-color: #F4F7FB;
            color: #6B7F96;
            padding: 5px 6px;
            font-family: 'Google Sans', sans-serif;
            font-size: 8.5px;
            font-weight: bold;
            letter-spacing: 0.8px;
            border: none;
            border-bottom: 1px solid #E4EAF2;
        }
    """)
    logs_layout.addWidget(parent.tbl_docking_logs)
    
    def create_pill_badge(text, level):
        lbl = QLabel(f" {text} ")
        lbl.setAlignment(Qt.AlignCenter)
        if level == "INFO":
            lbl.setStyleSheet("background-color: #DBEAFE; color: #1D4ED8; font-size: 8.5px; font-weight: bold; border-radius: 8px; padding: 1px 6px; border: 1px solid #BFDBFE;")
        elif level in ("OK", "SUCCESS"):
            lbl.setStyleSheet("background-color: #DCFCE7; color: #15803D; font-size: 8.5px; font-weight: bold; border-radius: 8px; padding: 1px 6px; border: 1px solid #BBF7D0;")
        elif level in ("WARN", "WARNING"):
            lbl.setStyleSheet("background-color: #FEF3C7; color: #B45309; font-size: 8.5px; font-weight: bold; border-radius: 8px; padding: 1px 6px; border: 1px solid #FDE68A;")
        else: # ERROR / CRITICAL
            lbl.setStyleSheet("background-color: #FEE2E2; color: #B91C1C; font-size: 8.5px; font-weight: bold; border-radius: 8px; padding: 1px 6px; border: 1px solid #FECACA;")
        return lbl

    def append_docking_log(time_str, lvl, src, msg):
        if not hasattr(parent, 'tbl_docking_logs') or parent.tbl_docking_logs is None:
            return
        row = parent.tbl_docking_logs.rowCount()
        parent.tbl_docking_logs.insertRow(row)
        
        t_item = QTableWidgetItem(time_str)
        t_item.setTextAlignment(Qt.AlignCenter)
        s_item = QTableWidgetItem(src)
        s_item.setTextAlignment(Qt.AlignCenter)
        m_item = QTableWidgetItem(msg)
        
        badge = create_pill_badge(lvl, lvl)
        
        parent.tbl_docking_logs.setItem(row, 0, t_item)
        parent.tbl_docking_logs.setCellWidget(row, 1, badge)
        parent.tbl_docking_logs.setItem(row, 2, s_item)
        parent.tbl_docking_logs.setItem(row, 3, m_item)
        
        if parent.tbl_docking_logs.rowCount() > 100:
            parent.tbl_docking_logs.removeRow(0)
        parent.tbl_docking_logs.scrollToBottom()

    parent.append_docking_log = append_docking_log
    
    # Populate initial telemetry logs matching operational reference
    initial_logs = [
        ("12:00:01", "INFO", "PING360", "Acoustic sweep sensor initialized"),
        ("12:00:02", "OK", "RM3100", "Geomagnetic vector calibration locked"),
        ("12:00:03", "INFO", "DOCK", "Docking telemetry stream active"),
        ("12:00:04", "OK", "GUIDANCE", "Docking guidance standby mode confirmed")
    ]
    for t_str, lvl, src, msg in initial_logs:
        append_docking_log(t_str, lvl, src, msg)
        
    col_status.addWidget(parent.tile_docking_logs, 1)
    
    # =========================================================================
    # Registry of ALL 5 docking tiles on parent for double-click excavation
    # =========================================================================
    parent.docking_tiles = {
        "sonar": parent.tile_docking_sonar,
        "sonar_chart": parent.tile_docking_sonar_chart,
        "mag": parent.tile_docking_mag,
        "mag_chart": parent.tile_docking_mag_chart,
        "logs": parent.tile_docking_logs
    }
    
    # Assemble 3 columns in layout (1:1:1 balanced distribution)
    cols_layout.addLayout(col_sonar, 1)
    cols_layout.addLayout(col_mag, 1)
    cols_layout.addLayout(col_status, 1)
    
    main_layout.addLayout(cols_layout)
    
    scroll_area.setWidget(container)
    page_layout = QVBoxLayout(page)
    page_layout.setContentsMargins(0, 0, 0, 0)
    page_layout.addWidget(scroll_area)
    
    return page

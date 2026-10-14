from PySide6.QtWidgets import QWidget, QFrame, QHBoxLayout, QVBoxLayout, QLabel, QComboBox, QPushButton, QSizePolicy
from PySide6.QtCore import Qt, QTimer, QDateTime, Signal, QPoint, QPointF, QRect, QRectF, QMargins
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QFont, QFontMetrics, QPolygon, QPolygonF, QLinearGradient, QRadialGradient, QPainterPath, QImage
from PySide6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis
import math

class TelemetryCard(QFrame):
    """
    A custom card widget to display telemetry metrics with modern light/cockpit/ocean layouts.
    """
    def __init__(self, title, initial_value, unit, theme="light", icon=None, accent=None, parent=None):
        super().__init__(parent)
        self.setObjectName("TelemetryCard")
        self.theme = theme
        self.accent = accent
        self.setProperty("theme", theme)
        self.setFrameShape(QFrame.NoFrame)

        # Root: thin accent rail + content column
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Hug the content vertically - otherwise a card in a sparse tile
        # stretches into a tall empty box.
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

        self.accent_rail = QFrame()
        self.accent_rail.setFixedWidth(3)
        root.addWidget(self.accent_rail)

        layout = QVBoxLayout()
        layout.setContentsMargins(9, 6, 9, 6)
        layout.setSpacing(1)
        root.addLayout(layout, 1)
        
        # Title Label (Small, uppercase, secondary text)
        self.title_label = QLabel(title.upper())
        self.title_label.setObjectName("CardTitle")
        
        # Value & Unit layout
        value_layout = QHBoxLayout()
        value_layout.setContentsMargins(0, 0, 0, 0)
        value_layout.setSpacing(4)
        
        if icon:
            self.icon_label = QLabel(icon)
            self.icon_label.setStyleSheet("color: #5A738E; font-size: 12px; font-weight: bold; margin-bottom: 2px; font-family: 'Segoe UI Symbol', sans-serif;")
            value_layout.addWidget(self.icon_label)
        
        # Value Label (Large bold number)
        self.value_label = QLabel(initial_value)
        self.value_label.setObjectName("CardValue")
        self.value_label.setAlignment(Qt.AlignBottom | Qt.AlignLeft)
        
        # Unit Label (Accent colored, subscript style)
        self.unit_label = QLabel(unit)
        self.unit_label.setObjectName("CardUnit")
        self.unit_label.setAlignment(Qt.AlignBottom | Qt.AlignLeft)
        self.unit_label.setStyleSheet("margin-bottom: 2px;")
        
        value_layout.addWidget(self.value_label)
        value_layout.addWidget(self.unit_label)
        value_layout.addStretch()
        
        layout.addWidget(self.title_label)
        layout.addLayout(value_layout)

        self.set_theme(theme)

    def set_accent(self, color):
        self.accent = color
        self.set_theme(self.theme)

    def set_theme(self, theme):
        self.theme = theme
        self.setProperty("theme", theme)
        if theme == "light":
            accent = self.accent or "#0D6EFD"
            title_c, value_c, rail_bg = "#6B7F96", "#0A2540", accent
            card_bg, card_border, hover_bg = "#FFFFFF", "#E4EAF2", "#FBFCFE"
        elif theme in ("cockpit", "dark"):
            accent = self.accent or "#FF9100"
            title_c, value_c, rail_bg = "#9AA3AF", "#FFFFFF", accent
            card_bg, card_border, hover_bg = "#1E1E22", "#333338", "#26262C"
        else:  # ocean
            accent = self.accent or "#00E5FF"
            title_c, value_c, rail_bg = "#8EB7E6", "#FFFFFF", accent
            card_bg, card_border, hover_bg = "#0F223C", "#1C3B65", "#152F53"

        self.title_label.setStyleSheet(
            f"color: {title_c}; font-size: 8.5px; font-weight: bold; letter-spacing: 0.7px;"
            " background: transparent; border: none;"
        )
        self.value_label.setStyleSheet(
            f"color: {value_c}; font-size: 17px; font-weight: bold;"
            " font-family: 'Google Sans', 'Segoe UI', sans-serif; background: transparent; border: none;"
        )
        self.unit_label.setStyleSheet(
            f"color: {accent}; font-size: 9.5px; font-weight: bold; letter-spacing: 0.4px;"
            " margin-bottom: 3px; background: transparent; border: none;"
        )
        if hasattr(self, 'icon_label'):
            self.icon_label.setStyleSheet(
                f"color: {accent}; font-size: 12px; font-weight: bold; margin-bottom: 2px;"
                " background: transparent; border: none;"
            )
        if hasattr(self, 'accent_rail'):
            self.accent_rail.setStyleSheet(
                f"background-color: {rail_bg}; border: none;"
                " border-top-left-radius: 7px; border-bottom-left-radius: 7px;"
            )
        self._base_stylesheet = f"""
            QFrame#TelemetryCard {{
                background-color: {card_bg};
                border: 1px solid {card_border};
                border-radius: 7px;
            }}
            QFrame#TelemetryCard:hover {{
                border-color: {accent};
                background-color: {hover_bg};
            }}
        """
        self.setStyleSheet(self._base_stylesheet)
        self.style().unpolish(self)
        self.style().polish(self)

    def reset_style(self):
        self.set_theme(self.theme)

    def set_alert(self, active=True, is_critical=True):
        if active:
            bg = "#2D080D" if is_critical else "#2A1800"
            border = "#FF1744" if is_critical else "#FF9F43"
            text_color = "#FF8A80" if is_critical else "#FFD180"
            self.setStyleSheet(f"""
                QFrame#TelemetryCard {{
                    border: 1px solid {border};
                    background-color: {bg};
                    border-radius: 7px;
                }}
            """)
            self.title_label.setStyleSheet("color: #CBD5E1; font-size: 8.5px; font-weight: bold; letter-spacing: 0.7px; background: transparent; border: none;")
            self.value_label.setStyleSheet(f"color: {text_color}; font-size: 17px; font-weight: bold; background: transparent; border: none;")
            self.unit_label.setStyleSheet("color: #CBD5E1; font-size: 9.5px; font-weight: bold; margin-bottom: 3px; background: transparent; border: none;")
            if hasattr(self, 'accent_rail'):
                self.accent_rail.setStyleSheet(f"background-color: {border}; border: none; border-top-left-radius: 7px; border-bottom-left-radius: 7px;")
        else:
            self.reset_style()

    def set_value(self, value_text):
        self.value_label.setText(str(value_text))

    def set_status(self, status):
        self.setProperty("status", status)
        self.style().unpolish(self)
        self.style().polish(self)


class TileExcavationWindow(QWidget):
    """
    Independent floating top-level window for an excavated dashboard tile.
    """
    def __init__(self, tile, title="Excavated Tile", parent=None):
        super().__init__(parent, Qt.Window)
        self.tile = tile
        self.is_closing = False
        self.setWindowTitle(f"{title} - Standalone View")
        self.resize(520, 380)
        self.setMinimumSize(360, 240)
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(12, 10, 12, 12)
        self.main_layout.setSpacing(8)

        # Top Bar with Title and Re-dock Action
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(2, 0, 2, 4)
        
        self.lbl_info = QLabel(f"⤢ {title.upper()}")
        self.lbl_info.setObjectName("ExcavatedTitle")
        
        self.btn_redock = QPushButton("⤡ Re-dock Tile")
        self.btn_redock.setCursor(Qt.PointingHandCursor)
        self.btn_redock.setStyleSheet("""
            QPushButton {
                background-color: #0D6EFD;
                color: #FFFFFF;
                border: none;
                border-radius: 4px;
                padding: 4px 12px;
                font-weight: bold;
                font-size: 10.5px;
            }
            QPushButton:hover {
                background-color: #0B5ED7;
            }
        """)
        self.btn_redock.clicked.connect(self.tile.redock)
        
        top_bar.addWidget(self.lbl_info)
        top_bar.addStretch()
        top_bar.addWidget(self.btn_redock)
        self.main_layout.addLayout(top_bar)
        
        tile_theme = getattr(tile, 'theme', 'light')
        self.set_theme(tile_theme)

    def set_theme(self, theme):
        self.theme = theme
        if theme in ("cockpit", "dark"):
            bg_color = "#121214"
            text_color = "#E0E0E0"
        elif theme == "ocean":
            bg_color = "#050B14"
            text_color = "#E2F1FF"
        else: # light
            bg_color = "#F4F7FB"
            text_color = "#0A2540"
        self.setStyleSheet(f"QWidget {{ background-color: {bg_color}; font-family: 'Google Sans', 'Segoe UI', sans-serif; }}")
        if hasattr(self, 'lbl_info'):
            self.lbl_info.setStyleSheet(f"color: {text_color}; font-weight: bold; font-size: 11px; letter-spacing: 0.5px; background: transparent; border: none;")

    def set_content(self, widget):
        self.main_layout.addWidget(widget, 1)

    def closeEvent(self, event):
        if not self.is_closing:
            self.is_closing = True
            self.tile.redock()
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.tile.redock()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


class ExpandButton(QPushButton):
    """Small square button that paints its own expand / collapse glyph."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.collapsed_icon = True   # True -> show "expand", False -> show "collapse"
        self._fg = QColor(90, 115, 142)
        self._hover = False
        self.setFixedSize(20, 20)
        self.setCursor(Qt.PointingHandCursor)

    def set_colors(self, fg, hover_fg):
        self._fg = QColor(fg)
        self._hover_fg = QColor(hover_fg)
        self.update()

    def set_collapsed(self, collapsed):
        self.collapsed_icon = bool(collapsed)
        self.update()

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = getattr(self, '_hover_fg', self._fg) if self._hover else self._fg
        pen = QPen(color, 1.4)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        r = self.rect()
        m = 6.0
        a = QPointF(r.left() + m, r.top() + m)
        b = QPointF(r.right() - m + 1, r.bottom() - m + 1)
        arm = 3.0
        if self.collapsed_icon:
            # outward arrows -> expand
            painter.drawLine(a, b)
            painter.drawLine(a, QPointF(a.x() + arm, a.y()))
            painter.drawLine(a, QPointF(a.x(), a.y() + arm))
            painter.drawLine(b, QPointF(b.x() - arm, b.y()))
            painter.drawLine(b, QPointF(b.x(), b.y() - arm))
        else:
            # inward arrows -> collapse
            painter.drawLine(a, b)
            painter.drawLine(a, QPointF(a.x() + arm, a.y() + arm * 0.0))
            painter.drawLine(QPointF(a.x() + arm, a.y()), QPointF(a.x() + arm, a.y() + arm))
            painter.drawLine(QPointF(b.x() - arm, b.y()), QPointF(b.x() - arm, b.y() - arm))
            painter.drawLine(QPointF(b.x() - arm, b.y()), QPointF(b.x(), b.y()))


class TileHeaderFrame(QFrame):
    def __init__(self, tile, parent=None):
        super().__init__(parent)
        self.tile = tile
        self.setStyleSheet("background: transparent; border: none;")

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.tile.toggle_excavation()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


class ExcavatableTile(QFrame):
    """
    Dashboard tile that can be double-clicked to excavate into an independent floating window,
    and restored back to its original slot when closed or re-docked.
    """
    def __init__(self, tile_id, title, icon=None, status_badge=None, accent="#0D6EFD", parent=None):
        super().__init__(parent)
        self.tile_id = tile_id
        self.title_text = title
        self.accent = accent
        self.status_badge_text = status_badge
        self.is_excavated = False
        self.excavation_window = None

        self.setObjectName(f"Tile_{tile_id}")
        self.theme = "light"

        self.tile_layout = QVBoxLayout(self)
        self.tile_layout.setContentsMargins(12, 10, 12, 10)
        self.tile_layout.setSpacing(8)

        # Header bar with double click excavation
        self.header_frame = TileHeaderFrame(self)
        hdr_layout = QHBoxLayout(self.header_frame)
        hdr_layout.setContentsMargins(0, 0, 0, 0)
        hdr_layout.setSpacing(6)

        # Accent rail replaces the old emoji icon
        self.lbl_icon = QFrame()
        self.lbl_icon.setFixedSize(3, 14)

        self.lbl_title = QLabel(self.title_text)
        self.btn_excavate = ExpandButton()
        self.btn_excavate.setToolTip("Double-click tile or click here to excavate into a new window")
        self.btn_excavate.clicked.connect(self.toggle_excavation)

        self.set_theme("light")

        hdr_layout.addWidget(self.lbl_icon)
        hdr_layout.addWidget(self.lbl_title)
        hdr_layout.addStretch()
        if status_badge:
            self.lbl_status_badge = QLabel(status_badge)
            self.lbl_status_badge.setStyleSheet("color: #0F766E; font-size: 8px; font-weight: bold; letter-spacing: 0.5px; background: #ECFEFF; border: 1px solid #A5F3FC; border-radius: 8px; padding: 2px 8px; margin-right: 4px;")
            hdr_layout.addWidget(self.lbl_status_badge)
        hdr_layout.addWidget(self.btn_excavate)

        self.tile_layout.addWidget(self.header_frame)

        # Hairline separator under the header
        self.header_rule = QFrame()
        self.header_rule.setFixedHeight(1)
        self.tile_layout.addWidget(self.header_rule)

        # Content container
        self.content_widget = QWidget()
        self.content_widget.setStyleSheet("background: transparent; border: none;")
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(6)
        self.tile_layout.addWidget(self.content_widget, 1)

        # Placeholder widget (shown when excavated)
        self.placeholder_widget = QFrame()
        self.placeholder_widget.setStyleSheet("""
            QFrame {
                background-color: #F8FAFC;
                border: 1.5px dashed #CBD5E1;
                border-radius: 6px;
            }
        """)
        ph_layout = QVBoxLayout(self.placeholder_widget)
        ph_layout.setAlignment(Qt.AlignCenter)
        ph_layout.setSpacing(8)

        ph_icon = QLabel("[  ]")
        ph_icon.setAlignment(Qt.AlignCenter)
        ph_icon.setStyleSheet("color: #64748B; font-size: 16px; font-weight: bold; letter-spacing: 2px; border: none; background: transparent;")
        
        ph_text = QLabel(f"{self.title_text}\nExcavated to Standalone Window")
        ph_text.setAlignment(Qt.AlignCenter)
        ph_text.setStyleSheet("color: #475569; font-weight: bold; font-size: 10px; border: none; background: transparent;")

        btn_restore = QPushButton("Re-dock Tile")
        btn_restore.setCursor(Qt.PointingHandCursor)
        btn_restore.setStyleSheet("""
            QPushButton {
                background-color: #0D6EFD;
                color: #FFFFFF;
                border: none;
                border-radius: 4px;
                padding: 4px 12px;
                font-weight: bold;
                font-size: 10px;
            }
            QPushButton:hover {
                background-color: #0B5ED7;
            }
        """)
        btn_restore.clicked.connect(self.redock)

        ph_layout.addWidget(ph_icon)
        ph_layout.addWidget(ph_text)
        ph_layout.addWidget(btn_restore, 0, Qt.AlignCenter)

        self.placeholder_widget.hide()
        self.tile_layout.addWidget(self.placeholder_widget, 1)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle_excavation()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def toggle_excavation(self):
        if self.is_excavated:
            self.redock()
        else:
            self.excavate()

    def excavate(self):
        if self.is_excavated:
            if self.excavation_window:
                self.excavation_window.show()
                self.excavation_window.raise_()
                self.excavation_window.activateWindow()
            return

        self.is_excavated = True
        self.content_widget.hide()
        self.placeholder_widget.show()

        self.excavation_window = TileExcavationWindow(self, title=self.title_text, parent=None)
        self.excavation_window.set_theme(self.theme)
        self.excavation_window.set_content(self.content_widget)
        self.content_widget.show()
        self.btn_excavate.set_collapsed(False)
        self.btn_excavate.setToolTip("Click to re-dock tile to dashboard")
        self.excavation_window.show()
        self.excavation_window.raise_()
        self.excavation_window.activateWindow()

    def redock(self):
        if not self.is_excavated:
            return

        self.is_excavated = False
        win = self.excavation_window
        self.excavation_window = None

        self.content_widget.hide()
        self.tile_layout.insertWidget(2, self.content_widget, 1)
        self.content_widget.show()
        self.placeholder_widget.hide()
        self.btn_excavate.set_collapsed(True)
        self.btn_excavate.setToolTip("Double-click tile or click here to excavate into a new window")

        if win:
            win.close()

    def set_theme(self, theme):
        self.theme = theme
        accent = self.accent or "#0D6EFD"
        if theme in ("cockpit", "dark"):
            tile_bg = "#1A1A1E"
            tile_border = "#2E2E36"
            title_color = "#F2F4F7"
            btn_color = "#9AA3AF"
            btn_hover_color = accent
            rule_color = "#2E2E36"
            ph_bg = "#161619"
            ph_border = "#333338"
            ph_text_color = "#A0A0A0"
        elif theme == "ocean":
            tile_bg = "#0A1625"
            tile_border = "#1C3B65"
            title_color = "#E2F1FF"
            btn_color = "#8EB7E6"
            btn_hover_color = accent
            rule_color = "#16304F"
            ph_bg = "#08121F"
            ph_border = "#1C3B65"
            ph_text_color = "#8EB7E6"
        else:  # light
            tile_bg = "#FFFFFF"
            tile_border = "#E4EAF2"
            title_color = "#0A2540"
            btn_color = "#8496AC"
            btn_hover_color = accent
            rule_color = "#EDF1F6"
            ph_bg = "#F8FAFC"
            ph_border = "#CBD5E1"
            ph_text_color = "#475569"

        self.setStyleSheet(f"""
            QFrame#{self.objectName()} {{
                background-color: {tile_bg};
                border: 1px solid {tile_border};
                border-radius: 10px;
            }}
        """)
        if hasattr(self, 'lbl_icon'):
            self.lbl_icon.setStyleSheet(f"background-color: {accent}; border: none; border-radius: 1px;")
        if hasattr(self, 'header_rule'):
            self.header_rule.setStyleSheet(f"background-color: {rule_color}; border: none;")
        if hasattr(self, 'lbl_title'):
            self.lbl_title.setStyleSheet(f"""
                color: {title_color};
                font-size: 10.5px;
                font-weight: bold;
                letter-spacing: 0.9px;
                border: none;
                background: transparent;
            """)
        if hasattr(self, 'btn_excavate'):
            self.btn_excavate.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    border: none;
                }
            """)
            if hasattr(self.btn_excavate, 'set_colors'):
                self.btn_excavate.set_colors(btn_color, btn_hover_color)
        if hasattr(self, 'lbl_status_badge'):
            if theme in ("cockpit", "dark"):
                bdg_bg, bdg_fg, bdg_bd = "#14231F", "#34D399", "#245043"
            elif theme == "ocean":
                bdg_bg, bdg_fg, bdg_bd = "#082A2E", "#2DD4BF", "#115E59"
            else:
                bdg_bg, bdg_fg, bdg_bd = "#ECFEFF", "#0F766E", "#A5F3FC"
            self.lbl_status_badge.setStyleSheet(
                f"color: {bdg_fg}; font-size: 8px; font-weight: bold; letter-spacing: 0.8px;"
                f" background: {bdg_bg}; border: 1px solid {bdg_bd}; border-radius: 8px;"
                " padding: 2px 8px; margin-right: 4px;"
            )
        if hasattr(self, 'placeholder_widget'):
            self.placeholder_widget.setStyleSheet(f"""
                QFrame {{
                    background-color: {ph_bg};
                    border: 1.5px dashed {ph_border};
                    border-radius: 8px;
                }}
            """)
        if self.excavation_window and hasattr(self.excavation_window, 'set_theme'):
            self.excavation_window.set_theme(theme)





class InstrumentPalette:
    """
    Central palette for the custom-drawn flight/marine instruments.
    Keeps every dial visually consistent across themes.
    """
    @staticmethod
    def get(theme):
        if theme == "ocean":
            return {
                "bezel_hi": QColor(34, 74, 122),
                "bezel_lo": QColor(10, 24, 42),
                "face_hi": QColor(16, 38, 64),
                "face_lo": QColor(6, 15, 28),
                "tick": QColor(130, 170, 210),
                "tick_major": QColor(0, 229, 255),
                "text": QColor(226, 241, 255),
                "text_dim": QColor(142, 183, 230),
                "accent": QColor(0, 229, 255),
                "north": QColor(255, 82, 116),
                "vehicle": QColor(0, 229, 255),
                "sky_hi": QColor(16, 62, 110),
                "sky_lo": QColor(38, 118, 180),
                "gnd_hi": QColor(16, 86, 108),
                "gnd_lo": QColor(8, 46, 60),
                "readout_bg": QColor(4, 12, 22, 220),
            }
        if theme == "light":
            return {
                "bezel_hi": QColor(214, 226, 240),
                "bezel_lo": QColor(168, 187, 209),
                "face_hi": QColor(252, 253, 255),
                "face_lo": QColor(226, 235, 246),
                "tick": QColor(120, 142, 168),
                "tick_major": QColor(13, 110, 253),
                "text": QColor(10, 37, 64),
                "text_dim": QColor(90, 115, 142),
                "accent": QColor(13, 110, 253),
                "north": QColor(220, 38, 38),
                "vehicle": QColor(13, 110, 253),
                "sky_hi": QColor(96, 165, 232),
                "sky_lo": QColor(152, 205, 245),
                "gnd_hi": QColor(180, 128, 72),
                "gnd_lo": QColor(128, 84, 44),
                "readout_bg": QColor(255, 255, 255, 235),
            }
        # cockpit / dark (default instrument look)
        return {
            "bezel_hi": QColor(58, 68, 84),
            "bezel_lo": QColor(18, 22, 30),
            "face_hi": QColor(22, 30, 43),
            "face_lo": QColor(7, 11, 18),
            "tick": QColor(126, 143, 166),
            "tick_major": QColor(226, 235, 245),
            "text": QColor(236, 242, 250),
            "text_dim": QColor(150, 166, 188),
            "accent": QColor(255, 176, 32),
            "north": QColor(255, 77, 109),
            "vehicle": QColor(255, 196, 61),
            "sky_hi": QColor(28, 86, 148),
            "sky_lo": QColor(74, 160, 226),
            "gnd_hi": QColor(150, 98, 46),
            "gnd_lo": QColor(88, 55, 24),
            "readout_bg": QColor(6, 10, 16, 225),
        }


def _draw_instrument_bezel(painter, cx, cy, radius, pal):
    """Shared brushed-metal bezel + inner face used by every round instrument."""
    bezel_grad = QLinearGradient(cx - radius, cy - radius, cx + radius, cy + radius)
    bezel_grad.setColorAt(0.0, pal["bezel_hi"])
    bezel_grad.setColorAt(0.5, pal["bezel_lo"])
    bezel_grad.setColorAt(1.0, pal["bezel_hi"])
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(bezel_grad))
    painter.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))

    face_r = radius - max(2.0, radius * 0.065)
    face_grad = QRadialGradient(QPointF(cx, cy - face_r * 0.35), face_r * 1.45)
    face_grad.setColorAt(0.0, pal["face_hi"])
    face_grad.setColorAt(1.0, pal["face_lo"])
    painter.setBrush(QBrush(face_grad))
    painter.drawEllipse(QRectF(cx - face_r, cy - face_r, face_r * 2, face_r * 2))
    return face_r


def _draw_readout(painter, cx, cy, width, height, text, pal, accent=None):
    """Small glass readout chip drawn inside a dial."""
    rect = QRectF(cx - width / 2.0, cy - height / 2.0, width, height)
    painter.setPen(QPen(accent or pal["accent"], 1.0))
    painter.setBrush(QBrush(pal["readout_bg"]))
    painter.drawRoundedRect(rect, height * 0.3, height * 0.3)
    painter.setPen(QPen(accent or pal["accent"]))
    pt = max(6, int(height * 0.58))
    f = QFont("Consolas", pt)
    f.setBold(True)
    # Shrink until the value fits the chip so readouts never clip
    while pt > 5 and QFontMetrics(f).horizontalAdvance(text) > rect.width() - 6:
        pt -= 1
        f.setPointSize(pt)
    painter.setFont(f)
    painter.drawText(rect, Qt.AlignCenter, text)


class MarineCompass(QWidget):
    """
    High-tech tactical marine gyro compass HUD. Fully scale-aware: renders
    exceptionally crisp and readable both as a compact dashboard dial and as
    an enlarged tactical overlay on the Earth Navigation Map.
    """
    def __init__(self, parent=None, theme="ocean"):
        super().__init__(parent)
        self.theme = theme
        self.setMinimumSize(70, 70)
        self.yaw = 0.0
        self.wp_bearing = None

    def set_yaw(self, yaw):
        self.yaw = float(yaw) % 360.0
        self.update()

    def set_theme(self, theme):
        self.theme = theme
        self.update()

    def set_wp_bearing(self, bearing):
        self.wp_bearing = float(bearing) if bearing is not None else None
        self.update()

    @staticmethod
    def _get_cardinal_name(deg):
        cards = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                 "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
        idx = int((deg + 11.25) / 22.5) % 16
        return cards[idx]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        pal = InstrumentPalette.get(self.theme)
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        radius = min(w, h) / 2.0 - 2.0
        if radius <= 10:
            return

        # ------------------------------------------------------------------
        # 1. Machined Aerospace Bezel
        # ------------------------------------------------------------------
        # Outer dark shadow / vignette ring
        painter.setPen(Qt.NoPen)
        shadow_grad = QRadialGradient(cx, cy, radius + 2.0)
        shadow_grad.setColorAt(0.88, QColor(0, 0, 0, 0))
        shadow_grad.setColorAt(1.0, QColor(0, 0, 0, 190))
        painter.setBrush(QBrush(shadow_grad))
        painter.drawEllipse(QRectF(cx - radius - 2, cy - radius - 2, (radius + 2) * 2, (radius + 2) * 2))

        # Main Bezel Metal Ring (Dark titanium chamfer)
        bezel_grad = QLinearGradient(cx - radius, cy - radius, cx + radius, cy + radius)
        bezel_grad.setColorAt(0.0, QColor(42, 52, 68))
        bezel_grad.setColorAt(0.3, QColor(22, 28, 38))
        bezel_grad.setColorAt(0.5, QColor(13, 17, 24))
        bezel_grad.setColorAt(0.7, QColor(28, 36, 48))
        bezel_grad.setColorAt(1.0, QColor(16, 20, 28))
        painter.setBrush(QBrush(bezel_grad))
        painter.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))

        # Outer rim specular hairline
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(255, 255, 255, 35), max(0.8, radius * 0.012)))
        painter.drawEllipse(QRectF(cx - radius + 0.5, cy - radius + 0.5, (radius - 0.5) * 2, (radius - 0.5) * 2))

        # Milled bezel index notches (every 45 degrees on the metal ring)
        bezel_notch_pen = QPen(
            QColor(0, 229, 255, 75) if self.theme != "light" else QColor(13, 110, 253, 85),
            max(1.0, radius * 0.015)
        )
        painter.setPen(bezel_notch_pen)
        for b_deg in (45, 90, 135, 180, 225, 270, 315):
            rad = math.radians(b_deg)
            r_out = radius - 0.8
            r_in = radius - max(2.2, radius * 0.040)
            painter.drawLine(
                QPointF(cx + r_out * math.sin(rad), cy - r_out * math.cos(rad)),
                QPointF(cx + r_in * math.sin(rad), cy - r_in * math.cos(rad))
            )

        # Glowing accent inner rim
        face_r = radius - max(3.0, radius * 0.075)
        glow_color = QColor(pal["accent"])
        glow_color.setAlpha(60)
        painter.setPen(QPen(glow_color, max(1.0, radius * 0.016)))
        painter.drawEllipse(QRectF(cx - face_r, cy - face_r, face_r * 2, face_r * 2))

        # ------------------------------------------------------------------
        # 2. Dial Face Background & Depth Shadow
        # ------------------------------------------------------------------
        inner_r = face_r - max(1.0, face_r * 0.018)
        face_grad = QRadialGradient(QPointF(cx, cy - inner_r * 0.15), inner_r * 1.15)
        face_grad.setColorAt(0.0, pal["face_hi"])
        face_grad.setColorAt(0.70, pal["face_lo"])
        face_grad.setColorAt(1.0, QColor(4, 6, 10))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(face_grad))
        painter.drawEllipse(QRectF(cx - inner_r, cy - inner_r, inner_r * 2, inner_r * 2))

        # Inner bezel bevel shadow (creates depth recess)
        recess_grad = QRadialGradient(cx, cy, inner_r)
        recess_grad.setColorAt(0.85, QColor(0, 0, 0, 0))
        recess_grad.setColorAt(1.0, QColor(0, 0, 0, 140))
        painter.setBrush(QBrush(recess_grad))
        painter.drawEllipse(QRectF(cx - inner_r, cy - inner_r, inner_r * 2, inner_r * 2))

        # Fixed Reticle concentric rings & crosshairs
        reticle_color = QColor(pal["tick"])
        reticle_color.setAlpha(30)
        painter.setPen(QPen(reticle_color, max(0.6, face_r * 0.010), Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QRectF(cx - face_r * 0.35, cy - face_r * 0.35, face_r * 0.70, face_r * 0.70))
        painter.drawEllipse(QRectF(cx - face_r * 0.58, cy - face_r * 0.58, face_r * 1.16, face_r * 1.16))

        # Subtle crosshair hairlines (horizontal and vertical)
        ch_pen = QPen(reticle_color, max(0.6, face_r * 0.009), Qt.DotLine)
        painter.setPen(ch_pen)
        painter.drawLine(QPointF(cx - face_r * 0.82, cy), QPointF(cx - face_r * 0.28, cy))
        painter.drawLine(QPointF(cx + face_r * 0.28, cy), QPointF(cx + face_r * 0.82, cy))
        painter.drawLine(QPointF(cx, cy - face_r * 0.82), QPointF(cx, cy - face_r * 0.28))
        painter.drawLine(QPointF(cx, cy + face_r * 0.28), QPointF(cx, cy + face_r * 0.82))

        # ------------------------------------------------------------------
        # 3. Fixed Compass Card
        # ------------------------------------------------------------------
        painter.save()
        painter.translate(cx, cy)

        # Precise tick geometry
        r_outer = face_r * 0.96
        tick_minor_len = face_r * 0.036
        tick_mid_len = face_r * 0.062
        tick_major_len = face_r * 0.090

        pen_minor = QPen(pal["tick"], max(0.7, face_r * 0.011))
        pen_minor.setColor(QColor(pal["tick"].red(), pal["tick"].green(), pal["tick"].blue(), 130))

        pen_mid = QPen(pal["tick_major"], max(0.9, face_r * 0.015))
        pen_mid.setColor(QColor(pal["tick_major"].red(), pal["tick_major"].green(), pal["tick_major"].blue(), 180))

        pen_major = QPen(pal["tick_major"], max(1.2, face_r * 0.021))

        # Draw Graduation Ticks (0..355 by 5 deg)
        for deg in range(0, 360, 5):
            painter.save()
            painter.rotate(deg)

            if deg == 0:
                # True North triangular pointer on the card
                arrow_w = face_r * 0.08
                arrow_tip = -r_outer
                arrow_base = arrow_tip + face_r * 0.062
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(pal["north"]))
                painter.drawPolygon(QPolygonF([
                    QPointF(0, arrow_tip),
                    QPointF(-arrow_w * 0.5, arrow_base),
                    QPointF(arrow_w * 0.5, arrow_base)
                ]))
            elif deg % 90 == 0:
                # E, S, W cardinal tick (stops short of letters)
                painter.setPen(pen_major)
                painter.drawLine(QPointF(0, -r_outer), QPointF(0, -r_outer + face_r * 0.065))
            elif deg % 30 == 0:
                painter.setPen(pen_major)
                painter.drawLine(QPointF(0, -r_outer), QPointF(0, -r_outer + tick_major_len))
            elif deg % 10 == 0:
                painter.setPen(pen_mid)
                painter.drawLine(QPointF(0, -r_outer), QPointF(0, -r_outer + tick_mid_len))
            else:
                painter.setPen(pen_minor)
                painter.drawLine(QPointF(0, -r_outer), QPointF(0, -r_outer + tick_minor_len))

            painter.restore()

        # Degree Callouts (030, 060, 120, 150, 210, 240, 300, 330)
        if face_r >= 44:
            num_font = QFont("Consolas", max(5, int(face_r * 0.074)))
            num_font.setBold(True)
            painter.setFont(num_font)
            painter.setPen(QPen(QColor(pal["text_dim"].red(), pal["text_dim"].green(), pal["text_dim"].blue(), 155)))
            deg_r = face_r * 0.83
            deg_box = face_r * 0.26
            for deg in (30, 60, 120, 150, 210, 240, 300, 330):
                painter.save()
                painter.rotate(deg)
                painter.drawText(
                    QRectF(-deg_box / 2.0, -deg_r - deg_box / 2.0, deg_box, deg_box),
                    Qt.AlignCenter,
                    f"{deg:03d}"
                )
                painter.restore()

        # Intercardinal Labels (NE, SE, SW, NW)
        if face_r >= 38:
            inter_font = QFont("Google Sans", max(6, int(face_r * 0.095)))
            inter_font.setBold(True)
            painter.setFont(inter_font)
            painter.setPen(QPen(QColor(pal["text_dim"].red(), pal["text_dim"].green(), pal["text_dim"].blue(), 200)))
            ic_r = face_r * 0.72
            ic_box = face_r * 0.25
            for angle, ic_txt in ((45, "NE"), (135, "SE"), (225, "SW"), (315, "NW")):
                painter.save()
                painter.rotate(angle)
                painter.drawText(
                    QRectF(-ic_box / 2.0, -ic_r - ic_box / 2.0, ic_box, ic_box),
                    Qt.AlignCenter,
                    ic_txt
                )
                painter.restore()

        # Cardinal Letters (N, E, S, W) - strictly at radius >= 0.72 * face_r
        card_font = QFont("Google Sans", max(7, int(face_r * 0.170)))
        card_font.setBold(True)
        painter.setFont(card_font)
        letter_r = face_r * 0.72
        box = face_r * 0.30
        for angle, letter in ((0, "N"), (90, "E"), (180, "S"), (270, "W")):
            painter.save()
            painter.rotate(angle)
            if letter == "N":
                painter.setPen(QPen(pal["north"]))
            else:
                painter.setPen(QPen(pal["text"]))
            painter.drawText(
                QRectF(-box / 2.0, -letter_r - box / 2.0, box, box),
                Qt.AlignCenter,
                letter
            )
            painter.restore()

        # Waypoint Bearing Marker (if active)
        if self.wp_bearing is not None:
            painter.save()
            painter.rotate(float(self.wp_bearing))
            marker_col = QColor(255, 140, 0)
            painter.setPen(QPen(marker_col, max(1.0, face_r * 0.025)))
            painter.setBrush(QBrush(QColor(255, 140, 0, 220)))
            wp_tip = -face_r * 0.96
            wp_w = face_r * 0.11
            painter.drawPolygon(QPolygonF([
                QPointF(0, wp_tip),
                QPointF(-wp_w * 0.5, wp_tip + wp_w * 1.1),
                QPointF(0, wp_tip + wp_w * 0.75),
                QPointF(wp_w * 0.5, wp_tip + wp_w * 1.1),
            ]))
            painter.restore()

        painter.restore()  # End fixed compass card

        # ------------------------------------------------------------------
        # 4. Fixed Central Hydrodynamic AUV Silhouette (Compact)
        # ------------------------------------------------------------------
        painter.save()
        painter.translate(cx, cy)
        painter.rotate(self.yaw)
        s = face_r / 82.0

        # Bow Heading Guidance Light Beam (Translucent cone forward)
        beam_grad = QLinearGradient(0, -6 * s, 0, -face_r * 0.52)
        veh_col = pal["vehicle"]
        beam_grad.setColorAt(0.0, QColor(veh_col.red(), veh_col.green(), veh_col.blue(), 75))
        beam_grad.setColorAt(0.5, QColor(veh_col.red(), veh_col.green(), veh_col.blue(), 25))
        beam_grad.setColorAt(1.0, QColor(veh_col.red(), veh_col.green(), veh_col.blue(), 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(beam_grad))
        beam_cone = QPolygonF([
            QPointF(0, -8 * s),
            QPointF(-6 * s, -face_r * 0.50),
            QPointF(6 * s, -face_r * 0.50)
        ])
        painter.drawPolygon(beam_cone)

        # Hydrodynamic AUV Hull profile (Compact: nose at -20*s, stern at +14*s)
        hull = QPolygonF([
            QPointF(0, -20 * s),          # Bow nose
            QPointF(2.6 * s, -17 * s),    # Bow dome starboard
            QPointF(4.0 * s, -9 * s),     # Forward shoulder
            QPointF(4.2 * s, 2 * s),      # Midbody parallel
            QPointF(3.0 * s, 9 * s),      # Aft taper
            QPointF(5.6 * s, 11.5 * s),   # Starboard stern dive fin
            QPointF(5.6 * s, 13.5 * s),   # Starboard fin trailing edge
            QPointF(2.0 * s, 13.5 * s),   # Starboard thruster nozzle
            QPointF(1.2 * s, 15.0 * s),   # Rudder hub
            QPointF(-1.2 * s, 15.0 * s),
            QPointF(-2.0 * s, 13.5 * s),  # Port thruster nozzle
            QPointF(-5.6 * s, 13.5 * s),  # Port fin trailing edge
            QPointF(-5.6 * s, 11.5 * s),  # Port dive fin
            QPointF(-3.0 * s, 9 * s),     # Aft taper port
            QPointF(-4.2 * s, 2 * s),     # Midbody port
            QPointF(-4.0 * s, -9 * s),    # Forward shoulder port
            QPointF(-2.6 * s, -17 * s),   # Bow dome port
        ])

        # Hull glow fill
        hull_fill = QColor(veh_col.red(), veh_col.green(), veh_col.blue(), 40)
        painter.setBrush(QBrush(hull_fill))
        painter.setPen(QPen(veh_col, max(1.1, 1.3 * s)))
        painter.drawPolygon(hull)

        # Internal technical partition lines
        painter.setPen(QPen(veh_col, max(0.7, 0.9 * s)))
        painter.drawLine(QPointF(-3.0 * s, -9 * s), QPointF(3.0 * s, -9 * s))
        painter.drawLine(QPointF(-3.4 * s, 1 * s), QPointF(3.4 * s, 1 * s))

        # Twin thruster propulsion glow dots
        th_glow = QColor(pal["accent"])
        th_glow.setAlpha(220)
        painter.setBrush(QBrush(th_glow))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QRectF(1.2 * s, 13.5 * s, 1.4 * s, 1.4 * s))
        painter.drawEllipse(QRectF(-2.6 * s, 13.5 * s, 1.4 * s, 1.4 * s))

        # Central Pivot Reticle
        pivot_r = 1.8 * s
        painter.setPen(QPen(veh_col, max(0.8, 1.0 * s)))
        painter.setBrush(QBrush(QColor(6, 10, 18)))
        painter.drawEllipse(QRectF(-pivot_r, -pivot_r, pivot_r * 2, pivot_r * 2))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(veh_col))
        painter.drawEllipse(QRectF(-0.6 * s, -0.6 * s, 1.2 * s, 1.2 * s))

        painter.restore()  # End vehicle

        # ------------------------------------------------------------------
        # 5. Fixed Top Lubber Index (Vernier pointer at 12 o'clock bezel edge)
        # ------------------------------------------------------------------
        painter.save()
        painter.translate(cx, cy)
        lubber_col = pal["accent"]

        # Sharp tactical inverted chevron sitting on the bezel pointing right at the card
        lub_tip = -face_r * 0.97
        lub_h = max(4.0, radius * 0.08)
        lub_w = max(6.0, radius * 0.10)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(lubber_col))
        painter.drawPolygon(QPolygonF([
            QPointF(0, lub_tip),
            QPointF(-lub_w * 0.5, lub_tip - lub_h),
            QPointF(lub_w * 0.5, lub_tip - lub_h),
        ]))

        # Micro fluorescent index pip at the tip
        pip_r = max(0.9, radius * 0.015)
        painter.setBrush(QBrush(QColor(255, 255, 255)))
        painter.drawEllipse(QRectF(-pip_r, lub_tip - pip_r * 2.0, pip_r * 2.0, pip_r * 2.0))
        painter.restore()

        # ------------------------------------------------------------------
        # 6. Integrated Tactical Digital Heading HUD Capsule (Scale-Responsive)
        # ------------------------------------------------------------------
        if face_r >= 24:
            if face_r >= 52:
                # Full 3-column tactical HUD pod for 190px and large displays
                pod_h = max(15.0, face_r * 0.20)
                pod_w = min(108.0, max(78.0, face_r * 1.16))
                # Pod centered at 0.35 * face_r sits completely inside radius 0.52 * face_r!
                # With letters at radius >= 0.72 * face_r, collision is mathematically impossible.
                pod_y = cy + face_r * 0.35
                ch = pod_h * 0.25  # chamfer size

                hw = pod_w / 2.0
                hh = pod_h / 2.0
                pod_poly = QPolygonF([
                    QPointF(cx - hw + ch, pod_y - hh),
                    QPointF(cx + hw - ch, pod_y - hh),
                    QPointF(cx + hw, pod_y - hh + ch),
                    QPointF(cx + hw, pod_y + hh - ch),
                    QPointF(cx + hw - ch, pod_y + hh),
                    QPointF(cx - hw + ch, pod_y + hh),
                    QPointF(cx - hw, pod_y + hh - ch),
                    QPointF(cx - hw, pod_y - hh + ch),
                ])

                painter.setPen(QPen(QColor(pal["accent"].red(), pal["accent"].green(), pal["accent"].blue(), 190), 1.2))
                painter.setBrush(QBrush(QColor(7, 12, 22, 252)))
                painter.drawPolygon(pod_poly)

                # Top edge subtle highlight hairline
                painter.setPen(QPen(QColor(255, 255, 255, 45), 1.0))
                painter.drawLine(QPointF(cx - hw + ch + 2, pod_y - hh + 1), QPointF(cx + hw - ch - 2, pod_y - hh + 1))

                # Column 1: HDG label
                lbl_w = pod_w * 0.26
                lbl_rect = QRectF(cx - hw + 2, pod_y - hh, lbl_w, pod_h)
                lbl_font = QFont("Google Sans", max(5, int(pod_h * 0.40)))
                lbl_font.setBold(True)
                painter.setFont(lbl_font)
                painter.setPen(QPen(QColor(pal["text_dim"].red(), pal["text_dim"].green(), pal["text_dim"].blue(), 190)))
                painter.drawText(lbl_rect, Qt.AlignCenter, "HDG")

                # Column Divider 1
                div1_x = cx - hw + lbl_w + 1
                painter.setPen(QPen(QColor(255, 255, 255, 30), 1.0))
                painter.drawLine(QPointF(div1_x, pod_y - hh + 3), QPointF(div1_x, pod_y + hh - 3))

                # Column 2: Digital Heading value
                val_w = pod_w * 0.48
                val_rect = QRectF(div1_x + 1, pod_y - hh, val_w, pod_h)
                val_font = QFont("Consolas", max(7, int(pod_h * 0.58)))
                val_font.setBold(True)
                painter.setFont(val_font)
                painter.setPen(QPen(pal["accent"]))
                painter.drawText(val_rect, Qt.AlignCenter, f"{self.yaw:05.1f}\u00b0")

                # Column Divider 2
                div2_x = div1_x + 1 + val_w
                painter.setPen(QPen(QColor(255, 255, 255, 30), 1.0))
                painter.drawLine(QPointF(div2_x, pod_y - hh + 3), QPointF(div2_x, pod_y + hh - 3))

                # Column 3: Cardinal Abbreviation
                card_w = (cx + hw) - div2_x - 2
                card_rect = QRectF(div2_x + 1, pod_y - hh, card_w, pod_h)
                painter.setFont(lbl_font)
                painter.setPen(QPen(pal["text"]))
                painter.drawText(card_rect, Qt.AlignCenter, self._get_cardinal_name(self.yaw))
            else:
                # Compact tactical chip for smaller dashboard gauges
                pod_h = max(11.0, face_r * 0.28)
                pod_w = min(face_r * 1.15, max(38.0, face_r * 0.95))
                pod_y = cy + face_r * 0.40
                pod_rect = QRectF(cx - pod_w / 2.0, pod_y - pod_h / 2.0, pod_w, pod_h)

                painter.setPen(QPen(pal["accent"], 1.0))
                painter.setBrush(QBrush(QColor(7, 12, 22, 245)))
                painter.drawRoundedRect(pod_rect, pod_h * 0.25, pod_h * 0.25)

                f = QFont("Consolas", max(6, int(pod_h * 0.62)))
                f.setBold(True)
                painter.setFont(f)
                painter.setPen(QPen(pal["accent"]))
                painter.drawText(pod_rect, Qt.AlignCenter, f"{self.yaw:05.1f}\u00b0")

        # ------------------------------------------------------------------
        # 7. Convex Lens Specular Reflection (Glass Cockpit Effect)
        # ------------------------------------------------------------------
        painter.save()
        lens_clip = QPainterPath()
        lens_clip.addEllipse(QRectF(cx - inner_r, cy - inner_r, inner_r * 2, inner_r * 2))
        painter.setClipPath(lens_clip)

        lens_grad = QLinearGradient(cx, cy - inner_r, cx, cy)
        lens_grad.setColorAt(0.0, QColor(255, 255, 255, 30))
        lens_grad.setColorAt(0.4, QColor(255, 255, 255, 8))
        lens_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(lens_grad))
        painter.drawChord(
            QRectF(cx - inner_r * 1.05, cy - inner_r * 1.1, inner_r * 2.1, inner_r * 1.5),
            0 * 16, 180 * 16
        )
        painter.restore()

        # Outer bezel bevel rim line
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(10, 14, 20), max(1.0, radius * 0.02)))
        painter.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))


class MarineHorizon(QWidget):
    """
    Artificial horizon / attitude indicator.

    mode="horizon" -> full attitude ball with roll arc and pitch ladder
    mode="pitch"   -> dedicated pitch inclinometer with vehicle silhouette
    """
    def __init__(self, parent=None, theme="ocean", mode="horizon"):
        super().__init__(parent)
        self.theme = theme
        self.mode = mode
        self.setMinimumSize(70, 70)
        self.roll = 0.0
        self.pitch = 0.0

    def set_attitude(self, roll, pitch):
        self.roll = float(roll)
        self.pitch = float(pitch)
        self.update()

    def set_theme(self, theme):
        self.theme = theme
        self.update()

    def set_mode(self, mode):
        self.mode = mode
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        pal = InstrumentPalette.get(self.theme)
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        radius = min(w, h) / 2.0 - 2.0
        if radius <= 6:
            return

        face_r = _draw_instrument_bezel(painter, cx, cy, radius, pal)

        if self.mode == "pitch":
            self._paint_pitch(painter, cx, cy, radius, face_r, pal)
        else:
            self._paint_horizon(painter, cx, cy, radius, face_r, pal)

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(pal["bezel_lo"], max(1.0, radius * 0.02)))
        painter.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))

    # ------------------------------------------------------------------
    def _paint_horizon(self, painter, cx, cy, radius, face_r, pal):
        # Ball is inset so the roll scale has room on the rim
        ball_r = face_r * 0.80
        px_per_deg = ball_r / 26.0

        painter.save()
        clip = QPainterPath()
        clip.addEllipse(QRectF(cx - ball_r, cy - ball_r, ball_r * 2, ball_r * 2))
        painter.setClipPath(clip)

        painter.save()
        painter.translate(cx, cy)
        painter.rotate(-self.roll)
        offset = max(-ball_r * 2.2, min(ball_r * 2.2, self.pitch * px_per_deg))

        span = ball_r * 3.0
        sky = QLinearGradient(0, -span, 0, offset)
        sky.setColorAt(0.0, pal["sky_hi"])
        sky.setColorAt(1.0, pal["sky_lo"])
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(sky))
        painter.drawRect(QRectF(-span, -span, span * 2, span + offset))

        gnd = QLinearGradient(0, offset, 0, span)
        gnd.setColorAt(0.0, pal["gnd_hi"])
        gnd.setColorAt(1.0, pal["gnd_lo"])
        painter.setBrush(QBrush(gnd))
        painter.drawRect(QRectF(-span, offset, span * 2, span))

        # Horizon line
        painter.setPen(QPen(QColor(255, 255, 255), max(1.2, ball_r * 0.035)))
        painter.drawLine(QPointF(-span, offset), QPointF(span, offset))

        # Pitch ladder
        lad_font = QFont("Consolas", max(5, int(ball_r * 0.17)))
        lad_font.setBold(True)
        painter.setFont(lad_font)
        for p in range(-30, 31, 5):
            if p == 0:
                continue
            y = offset - p * px_per_deg
            if abs(y) > ball_r * 0.92:
                continue
            major = (p % 10 == 0)
            bar = ball_r * (0.40 if major else 0.20)
            painter.setPen(QPen(QColor(255, 255, 255, 220 if major else 140),
                                max(0.9, ball_r * 0.022)))
            painter.drawLine(QPointF(-bar, y), QPointF(bar, y))
            if major and ball_r >= 34 and abs(y) < ball_r * 0.62:
                painter.setPen(QPen(QColor(255, 255, 255, 235)))
                tw = ball_r * 0.36
                painter.drawText(QRectF(bar + ball_r * 0.05, y - ball_r * 0.14, tw, ball_r * 0.28),
                                 Qt.AlignVCenter | Qt.AlignLeft, str(abs(p)))
                painter.drawText(QRectF(-bar - ball_r * 0.05 - tw, y - ball_r * 0.14, tw, ball_r * 0.28),
                                 Qt.AlignVCenter | Qt.AlignRight, str(abs(p)))
        painter.restore()
        painter.restore()

        # Ball rim
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(pal["bezel_lo"], max(1.0, ball_r * 0.045)))
        painter.drawEllipse(QRectF(cx - ball_r, cy - ball_r, ball_r * 2, ball_r * 2))

        # --- Roll scale on the rim -------------------------------------
        painter.save()
        painter.translate(cx, cy)
        arc_r = (ball_r + face_r) / 2.0
        for ang in (-60, -45, -30, -20, -10, 0, 10, 20, 30, 45, 60):
            painter.save()
            painter.rotate(ang)
            major = ang in (-60, -30, 0, 30, 60)
            painter.setPen(QPen(pal["tick_major"] if major else pal["tick"],
                                max(0.9, face_r * (0.030 if major else 0.020))))
            length = face_r * (0.11 if major else 0.07)
            painter.drawLine(QPointF(0, -arc_r - length * 0.5),
                             QPointF(0, -arc_r + length * 0.5))
            painter.restore()

        # Moving roll pointer
        painter.save()
        painter.rotate(-self.roll)
        acc = pal["accent"]
        painter.setPen(QPen(acc, max(0.9, face_r * 0.022)))
        painter.setBrush(QBrush(acc))
        tip = -arc_r + face_r * 0.07
        wing = face_r * 0.085
        painter.drawPolygon(QPolygonF([
            QPointF(0, tip),
            QPointF(-wing, tip + wing * 1.35),
            QPointF(wing, tip + wing * 1.35),
        ]))
        painter.restore()
        painter.restore()

        # --- Fixed vehicle reference -----------------------------------
        painter.save()
        painter.translate(cx, cy)
        veh = pal["vehicle"]
        pen = QPen(veh, max(1.6, face_r * 0.055))
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        inner = face_r * 0.15
        outer = face_r * 0.46
        drop = face_r * 0.12
        painter.drawLine(QPointF(-outer, 0), QPointF(-inner, 0))
        painter.drawLine(QPointF(-inner, 0), QPointF(-inner, drop))
        painter.drawLine(QPointF(outer, 0), QPointF(inner, 0))
        painter.drawLine(QPointF(inner, 0), QPointF(inner, drop))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(veh))
        r = face_r * 0.055
        painter.drawEllipse(QRectF(-r, -r, r * 2, r * 2))
        painter.restore()

        # --- Digital readout -------------------------------------------
        if face_r >= 30:
            chip_h = max(10.0, face_r * 0.21)
            if face_r >= 56:
                txt = f"R{self.roll:+05.1f}  P{self.pitch:+05.1f}"
            else:
                txt = f"{self.roll:+.0f}\u00b0 {self.pitch:+.0f}\u00b0"
            chip_w = min(chip_h * 6.6, face_r * 1.32)
            _draw_readout(painter, cx, cy + face_r * 0.71, chip_w, chip_h, txt, pal)

    # ------------------------------------------------------------------
    def _paint_pitch(self, painter, cx, cy, radius, face_r, pal):
        """Dedicated pitch inclinometer: vehicle silhouette against a graduated scale."""
        painter.save()
        clip = QPainterPath()
        clip.addEllipse(QRectF(cx - face_r, cy - face_r, face_r * 2, face_r * 2))
        painter.setClipPath(clip)
        painter.translate(cx, cy)

        # Fixed graduated scale, drawn only on the rim so the centre stays clear
        px_per_deg = face_r / 34.0
        for p in range(-30, 31, 5):
            y = -p * px_per_deg
            major = (p % 10 == 0)
            inner = face_r * (0.60 if major else 0.70)
            outer = face_r * 0.88
            painter.setPen(QPen(pal["tick_major"] if major else pal["tick"],
                                max(0.8, face_r * (0.024 if major else 0.016))))
            painter.drawLine(QPointF(-outer, y), QPointF(-inner, y))
            painter.drawLine(QPointF(inner, y), QPointF(outer, y))

        # Zero reference line across the full dial
        acc = pal["accent"]
        painter.setPen(QPen(acc, max(1.0, face_r * 0.028)))
        painter.drawLine(QPointF(-face_r * 0.88, 0), QPointF(face_r * 0.88, 0))
        soft = QColor(acc)
        soft.setAlpha(60)
        painter.setPen(QPen(soft, max(0.7, face_r * 0.014)))
        painter.drawLine(QPointF(0, -face_r * 0.88), QPointF(0, face_r * 0.88))

        # Vehicle silhouette pitches about the centre
        painter.save()
        painter.rotate(-self.pitch)
        veh = pal["vehicle"]
        body_l = face_r * 0.52
        body_h = face_r * 0.15
        body = QPainterPath()
        body.moveTo(body_l, 0)
        body.cubicTo(body_l * 0.60, -body_h, body_l * 0.05, -body_h, -body_l * 0.70, -body_h * 0.78)
        body.lineTo(-body_l, -body_h * 0.30)
        body.lineTo(-body_l, body_h * 0.30)
        body.lineTo(-body_l * 0.70, body_h * 0.78)
        body.cubicTo(body_l * 0.05, body_h, body_l * 0.60, body_h, body_l, 0)
        fill = QColor(veh)
        fill.setAlpha(45)
        painter.setPen(QPen(veh, max(1.0, face_r * 0.030)))
        painter.setBrush(QBrush(fill))
        painter.drawPath(body)
        # Dorsal fin gives the silhouette a clear up direction
        painter.setBrush(QBrush(veh))
        painter.drawPolygon(QPolygonF([
            QPointF(-body_l * 0.46, -body_h * 0.75),
            QPointF(-body_l * 0.60, -body_h * 1.85),
            QPointF(-body_l * 0.20, -body_h * 0.88),
        ]))
        painter.restore()
        painter.restore()

        # Digital readout
        if face_r >= 26:
            chip_h = max(11.0, face_r * 0.25)
            chip_w = chip_h * 3.3
            _draw_readout(painter, cx, cy + face_r * 0.62, chip_w, chip_h,
                          f"{self.pitch:+05.1f}\u00b0", pal)


class TopBar(QFrame):
    """
    Top Bar of the Groundstation. Contains:
    - Time & Date (Left)
    - App Title (Center)
    - Connection controls: Port, Baud rate, and Connect button (Right)
    """
    # Signals for connection action
    # Emits (port_name, baudrate)
    connect_requested = Signal(str, str)
    disconnect_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("TopBarFrame")
        self.status_state = "disconnected"
        
        self.init_ui()
        self.start_clock()

    def init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 0, 15, 0)
        layout.setSpacing(20)
        layout.setAlignment(Qt.AlignVCenter)

        # Left Section: Date & Time
        self.clock_label = QLabel()
        self.clock_label.setObjectName("ClockLabel")
        layout.addWidget(self.clock_label)

        # Left Section: GPS Satellites Count
        self.gps_label = QLabel("GPS: --")
        self.gps_label.setObjectName("TopBarGpsLabel")
        layout.addWidget(self.gps_label)

        self.gps_fix_label = QLabel("NO FIX")
        self.gps_fix_label.setObjectName("TopBarGpsFixLabel")
        self.gps_fix_label.setAlignment(Qt.AlignCenter)
        self.set_gps_fix(False)
        layout.addWidget(self.gps_fix_label)

        # Left Section: Battery Percentage
        self.battery_label = QLabel("BATT: --")
        self.battery_label.setObjectName("TopBarBatteryLabel")
        layout.addWidget(self.battery_label)
        
        # Left Section: Chamber Temperature
        self.temp_label = QLabel("TEMP: --")
        self.temp_label.setObjectName("TopBarTempLabel")
        layout.addWidget(self.temp_label)
        
        # New Warning Badge (Pulsing Pill)
        self.alert_badge = QPushButton("NO ALERTS")
        self.alert_badge.setObjectName("TopBarAlertBadge")
        self.alert_badge.setVisible(False)
        self.alert_badge.setCursor(Qt.PointingHandCursor)
        self.alert_badge.setStyleSheet("""
            QPushButton#TopBarAlertBadge {
                background-color: #2A1800;
                border: 1px solid #FF9F43;
                border-radius: 10px;
                color: #FF9F43;
                font-family: 'Google Sans', sans-serif;
                font-size: 10px;
                font-weight: bold;
                padding: 2px 10px;
            }
        """)
        layout.addWidget(self.alert_badge)
        
        layout.addStretch()

        # Center Section: Title
        title_label = QLabel("MARINE GS")
        title_label.setObjectName("LogoLabel")
        title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(title_label)
        
        layout.addStretch()

        # Right Section: Control Layout
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(10)
        controls_layout.setAlignment(Qt.AlignVCenter)
        
        # Status Light and Label
        self.status_panel = QFrame()
        self.status_panel.setObjectName("StatusPanel")
        self.status_panel.setFixedHeight(22)
        
        status_layout = QHBoxLayout(self.status_panel)
        status_layout.setContentsMargins(8, 0, 8, 0)
        status_layout.setSpacing(6)
        status_layout.setAlignment(Qt.AlignVCenter)
        
        self.status_led = QFrame()
        self.status_led.setObjectName("StatusLED")
        self.status_led.setProperty("connected", False)
        
        self.status_text = QLabel("STANDBY")
        self.status_text.setObjectName("StatusLabel")
        self.status_text.setProperty("connected", False)
        
        status_layout.addWidget(self.status_led)
        status_layout.addWidget(self.status_text)
        controls_layout.addWidget(self.status_panel)

        # Port Select
        port_lbl = QLabel("PORT")
        port_lbl.setProperty("class", "TopBarControlLabel")
        self.port_combo = QComboBox()
        
        controls_layout.addWidget(port_lbl)
        controls_layout.addWidget(self.port_combo)

        # IP/Port Input for Ethernet Connection (TCP / UDP)
        from PySide6.QtWidgets import QLineEdit
        self.ip_port_lbl = QLabel("IP:PORT")
        self.ip_port_lbl.setProperty("class", "TopBarControlLabel")
        self.ip_port_lbl.setVisible(False)
        self.ip_port_input = QLineEdit()
        self.ip_port_input.setPlaceholderText("192.168.1.10:8888")
        self.ip_port_input.setText("127.0.0.1:8888")
        self.ip_port_input.setStyleSheet("""
            QLineEdit {
                background-color: #0F2030;
                border: 1px solid #162F4A;
                border-radius: 3px;
                color: #00E5FF;
                font-family: 'Consolas', monospace;
                font-size: 10px;
                padding: 2px 6px;
                width: 120px;
            }
            QLineEdit:focus {
                border-color: #00E5FF;
            }
        """)
        self.ip_port_input.setVisible(False)
        controls_layout.addWidget(self.ip_port_lbl)
        controls_layout.addWidget(self.ip_port_input)

        # Baud Rate Select
        self.baud_lbl = QLabel("BAUD")
        self.baud_lbl.setProperty("class", "TopBarControlLabel")
        self.baud_combo = QComboBox()
        self.baud_combo.addItems(["4800", "9600", "19200", "38400", "57600", "115200"])
        self.baud_combo.setCurrentText("57600")
        
        controls_layout.addWidget(self.baud_lbl)
        controls_layout.addWidget(self.baud_combo)

        # Connect button
        self.connect_btn = QPushButton("CONNECT")
        self.connect_btn.setObjectName("ConnectButton")
        self.connect_btn.setProperty("connected", False)
        self.connect_btn.clicked.connect(self.on_connect_clicked)
        controls_layout.addWidget(self.connect_btn)



        # Dynamic layout toggle slot
        def on_port_changed(text):
            is_ethernet = text in ("TCP CLIENT", "UDP CLIENT")
            self.baud_lbl.setVisible(not is_ethernet)
            self.baud_combo.setVisible(not is_ethernet)
            self.ip_port_lbl.setVisible(is_ethernet)
            self.ip_port_input.setVisible(is_ethernet)
            if is_ethernet:
                current_val = self.ip_port_input.text().strip()
                if text == "UDP CLIENT":
                    self.ip_port_lbl.setText("LOCAL PORT")
                    self.ip_port_input.setPlaceholderText("8888")
                    if not current_val or ":" in current_val:
                        self.ip_port_input.setText("8888")
                else:
                    self.ip_port_lbl.setText("IP:PORT")
                    self.ip_port_input.setPlaceholderText("192.168.1.10:8888")
                    if not current_val or not ":" in current_val:
                        self.ip_port_input.setText("127.0.0.1:8888")
            
        self.port_combo.currentTextChanged.connect(on_port_changed)

        layout.addLayout(controls_layout)



    def start_clock(self):
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_time)
        self.timer.start(1000)
        self.update_time()

    def update_time(self):
        # Format current time to show e.g. "04 Jul 2026 11:27:01"
        current_dt = QDateTime.currentDateTime()
        self.clock_label.setText(current_dt.toString("dd MMM yyyy  hh:mm:ss"))

    def set_gps_count(self, count):
        if count is None or count == "--":
            self.gps_label.setText("GPS: --")
        else:
            self.gps_label.setText(f"GPS: {count}")

    def set_gps_fix(self, has_fix):
        if has_fix:
            self.gps_fix_label.setText("FIX")
            self.gps_fix_label.setStyleSheet("QLabel#TopBarGpsFixLabel { background: #15352D; border: 1px solid #27745A; border-radius: 9px; color: #6EE7B7; font: bold 10px 'Google Sans'; padding: 2px 9px; }")
        else:
            self.gps_fix_label.setText("NO FIX")
            self.gps_fix_label.setStyleSheet("QLabel#TopBarGpsFixLabel { background: #3A2025; border: 1px solid #7F343E; border-radius: 9px; color: #FCA5A5; font: bold 10px 'Google Sans'; padding: 2px 9px; }")

    def set_battery_percentage(self, percentage):
        if percentage is None or percentage == "--":
            self.battery_label.setText("BATT: --")
        else:
            self.battery_label.setText(f"BATT: {percentage}%")

    def set_chamber_temp(self, temp):
        if temp is None or temp == "--":
            self.temp_label.setText("TEMP: --")
        else:
            self.temp_label.setText(f"TEMP: {temp:.1f}°C")

    def populate_ports(self, ports):
        # Compare current list to avoid redundant updates that clear user text and reset focus
        current_ports = [self.port_combo.itemText(i) for i in range(self.port_combo.count())]
        if current_ports == ports:
            return
            
        current_sel = self.port_combo.currentText()
        
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        self.port_combo.addItems(ports)
        if current_sel in ports:
            self.port_combo.setCurrentText(current_sel)
        self.port_combo.blockSignals(False)

    def on_connect_clicked(self):
        if self.status_state == "disconnected":
            port = self.port_combo.currentText()
            if port in ("TCP CLIENT", "UDP CLIENT"):
                baud = self.ip_port_input.text().strip()
            else:
                baud = self.baud_combo.currentText()
            self.connect_requested.emit(port, baud)
        else:
            self.disconnect_requested.emit()

    def set_connection_status(self, status, status_msg=""):
        self.status_state = status
        
        if status == "disconnected":
            self.gps_label.setText("GPS: --")
            self.set_gps_fix(False)
            
        # Toggle widgets state property for QSS update
        self.connect_btn.setProperty("status", status)
        self.status_led.setProperty("status", status)
        self.status_text.setProperty("status", status)
        
        if status == "connected":
            self.connect_btn.setText("DISCONNECT")
            self.status_text.setText("CONNECTED" if not status_msg else status_msg.upper())
            # Disable port selections during active connection
            self.port_combo.setEnabled(False)
            self.baud_combo.setEnabled(False)
            self.ip_port_input.setEnabled(False)
        elif status == "connecting":
            self.connect_btn.setText("CANCEL")
            self.status_text.setText("WAITING DATA" if not status_msg else status_msg.upper())
            self.port_combo.setEnabled(False)
            self.baud_combo.setEnabled(False)
            self.ip_port_input.setEnabled(False)
        else:
            self.connect_btn.setText("CONNECT")
            self.status_text.setText("STANDBY" if not status_msg else status_msg.upper())
            self.port_combo.setEnabled(True)
            self.baud_combo.setEnabled(True)
            self.ip_port_input.setEnabled(True)
            
        # Re-apply styles
        self.connect_btn.style().unpolish(self.connect_btn)
        self.connect_btn.style().polish(self.connect_btn)
        self.status_led.style().unpolish(self.status_led)
        self.status_led.style().polish(self.status_led)
        self.status_text.style().unpolish(self.status_text)
        self.status_text.style().polish(self.status_text)


class MagnetometerVectorWidget(QWidget):
    """
    Custom cockpit widget displaying a 2D vector plot of magnetometer X and Y values.
    """
    def __init__(self, theme="ocean", parent=None):
        super().__init__(parent)
        self.theme = theme
        self.setMinimumSize(120, 120)
        self.mx = 0.0
        self.my = 0.0
        self.mz = 0.0
        
    def set_mag_values(self, mx, my, mz):
        self.mx = float(mx)
        self.my = float(my)
        self.mz = float(mz)
        self.update()

    def set_theme(self, theme):
        self.theme = theme
        self.update()
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        w, h = self.width(), self.height()
        size = min(w, h) - 10
        center_x = w / 2
        center_y = h / 2
        radius = size / 2
        
        # Theme colors
        if self.theme == "light":
            bg_color = QColor(255, 255, 255)
            grid_color = QColor(203, 213, 225)
            grid_dash_color = QColor(203, 213, 225, 120)
            text_color = QColor(10, 37, 64)
            vector_color = QColor(13, 110, 253) # Vivid blue
        elif self.theme == "cockpit":
            bg_color = QColor(24, 24, 24)
            grid_color = QColor(51, 51, 51)
            grid_dash_color = QColor(51, 51, 51, 100)
            text_color = QColor(160, 160, 160)
            vector_color = QColor(255, 145, 0) # Warning amber/orange for cockpit vector sweep
        else:
            bg_color = QColor(10, 15, 25)
            grid_color = QColor(28, 59, 101)
            grid_dash_color = QColor(28, 59, 101, 100)
            text_color = QColor(142, 183, 230)
            vector_color = QColor(0, 230, 118) # Neon green for ocean
        
        # Draw background grid
        painter.fillRect(self.rect(), QBrush(bg_color))
        painter.setPen(QPen(grid_color, 1.5))
        painter.drawEllipse(center_x - radius, center_y - radius, size, size)
        painter.drawEllipse(center_x - radius/2, center_y - radius/2, radius, radius)
        
        # Crosshair lines
        painter.setPen(QPen(grid_dash_color, 1, Qt.DashLine))
        painter.drawLine(center_x - radius, center_y, center_x + radius, center_y)
        painter.drawLine(center_x, center_y - radius, center_x, center_y + radius)
        
        # Draw vector arrow representing (mx, my)
        magnitude = math.sqrt(self.mx**2 + self.my**2)
        max_scale = 50.0  # expected scale max magnitude
        scale = radius / max_scale if magnitude > 0 else 1.0
        
        vx = center_x + self.mx * scale
        vy = center_y - self.my * scale  # Inverted Y
        
        # Draw vector line
        vector_pen = QPen(vector_color, 2.5)
        painter.setPen(vector_pen)
        painter.drawLine(center_x, center_y, vx, vy)
        
        # Arrow tip
        painter.setBrush(QBrush(vector_color))
        painter.drawEllipse(int(vx - 3), int(vy - 3), 6, 6)
        
        # Draw text labels
        painter.setPen(QPen(text_color, 1))
        painter.setFont(QFont("Google Sans", 7))
        painter.drawText(10, h - 25, f"H-Mag: {magnitude:.1f} uT")
        painter.drawText(10, h - 12, f"Z-Axis: {self.mz:.1f} uT")


class RealTimeChart(QChartView):
    """
    High-performance real-time scrolling line chart.
    """
    def __init__(self, title, min_val, max_val, unit_str, theme="ocean", show_x_axis=False, x_title="", parent=None):
        chart = QChart()
        super().__init__(chart, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.theme = theme
        self.show_x_axis = show_x_axis
        
        self.max_points = 61 if show_x_axis else 100
        self.series_list = []
        
        # Theme configuration
        if theme == "light":
            bg_hex = "#FFFFFF"
            title_color = "#0A2540"
            label_color = "#5A738E"
            grid_color = "#EDF2F7"
            border_color = "#D8E2ED"
        elif theme == "cockpit":
            bg_hex = "#1A1A1A"
            title_color = "#E0E0E0"
            label_color = "#A0A0A0"
            grid_color = "#333333"
            border_color = "#333333"
        else:
            bg_hex = "#0A1625"
            title_color = "#8EB7E6"
            label_color = "#8EB7E6"
            grid_color = "#1C2A3A"
            border_color = "#1C3B65"
        
        # Chart styling
        chart.setTitle(title)
        chart.setTitleFont(QFont("Google Sans", 8, QFont.Bold))
        chart.setTitleBrush(QBrush(QColor(title_color)))
        chart.setBackgroundBrush(QBrush(QColor(bg_hex)))
        chart.setPlotAreaBackgroundBrush(QBrush(QColor(bg_hex)))
        chart.setPlotAreaBackgroundVisible(True)
        chart.setMargins(QMargins(2, 2, 2, 2))
        self.setStyleSheet(f"border: 1px solid {border_color}; border-radius: 6px; background-color: {bg_hex};")
        chart.legend().setVisible(True)
        chart.legend().setFont(QFont("Google Sans", 7.5))
        chart.legend().setLabelColor(QColor(title_color))
        
        # Axis setup
        self.axis_x = QValueAxis()
        if show_x_axis:
            self.axis_x.setRange(0, 60)
            self.axis_x.setTickCount(7)
            self.axis_x.setLabelFormat("%d")
            self.axis_x.setLabelsFont(QFont("Google Sans", 7.5))
            self.axis_x.setLabelsColor(QColor(label_color))
            if x_title:
                self.axis_x.setTitleText(x_title)
                self.axis_x.setTitleFont(QFont("Google Sans", 8))
                self.axis_x.setTitleBrush(QBrush(QColor(label_color)))
                self.axis_x.setTitleVisible(True)
            self.axis_x.setGridLinePen(QPen(QColor(grid_color), 1, Qt.DashLine))
            self.axis_x.setVisible(True)
        else:
            self.axis_x.setRange(0, self.max_points)
            self.axis_x.setVisible(False)
        
        self.axis_y = QValueAxis()
        self.axis_y.setRange(min_val, max_val)
        if unit_str:
            self.axis_y.setLabelFormat(f"%.0f {unit_str}" if abs(max_val - min_val) >= 10 else f"%.1f {unit_str}")
        else:
            self.axis_y.setLabelFormat("%.0f")
        self.axis_y.setLabelsFont(QFont("Google Sans", 8))
        self.axis_y.setLabelsColor(QColor(label_color))
        self.axis_y.setGridLinePen(QPen(QColor(grid_color), 1, Qt.DashLine))
        
        chart.addAxis(self.axis_x, Qt.AlignBottom)
        chart.addAxis(self.axis_y, Qt.AlignLeft)

    def set_theme(self, theme):
        self.theme = theme
        if theme == "light":
            bg_hex = "#FFFFFF"
            title_color = "#0A2540"
            label_color = "#5A738E"
            grid_color = "#EDF2F7"
            border_color = "#D8E2ED"
        elif theme in ("cockpit", "dark"):
            bg_hex = "#1A1A1E"
            title_color = "#FFFFFF"
            label_color = "#A0A0A0"
            grid_color = "#2E2E36"
            border_color = "#2E2E36"
        else: # ocean
            bg_hex = "#0A1625"
            title_color = "#8EB7E6"
            label_color = "#8EB7E6"
            grid_color = "#1C2A3A"
            border_color = "#1C3B65"
        
        self.chart().setBackgroundBrush(QBrush(QColor(bg_hex)))
        self.chart().setPlotAreaBackgroundBrush(QBrush(QColor(bg_hex)))
        self.chart().setTitleBrush(QBrush(QColor(title_color)))
        self.setStyleSheet(f"border: 1px solid {border_color}; border-radius: 6px; background-color: {bg_hex};")
        if self.chart().legend():
            self.chart().legend().setLabelColor(QColor(title_color))
        if hasattr(self, 'axis_y'):
            self.axis_y.setLabelsColor(QColor(label_color))
            self.axis_y.setGridLinePen(QPen(QColor(grid_color), 1, Qt.DashLine))
        if hasattr(self, 'axis_x'):
            self.axis_x.setLabelsColor(QColor(label_color))
            self.axis_x.setGridLinePen(QPen(QColor(grid_color), 1, Qt.DashLine))
            if hasattr(self.axis_x, 'titleBrush'):
                self.axis_x.setTitleBrush(QBrush(QColor(label_color)))

    def add_series(self, name, color_str):
        series = QLineSeries()
        series.setName(name)
        series.setPen(QPen(QColor(color_str), 2))
        self.chart().addSeries(series)
        
        series.attachAxis(self.axis_x)
        series.attachAxis(self.axis_y)
        
        self.series_list.append({
            "series": series,
            "data": []
        })
        
    def append_data(self, values):
        for i, val in enumerate(values):
            if i >= len(self.series_list):
                break
                
            item = self.series_list[i]
            history = item["data"]
            series = item["series"]
            
            history.append(val)
            if len(history) > self.max_points:
                history.pop(0)
                
            if self.show_x_axis:
                step = 60.0 / max(1, self.max_points - 1)
                points = [QPointF(x * step, y) for x, y in enumerate(history)]
            else:
                points = [QPointF(x, y) for x, y in enumerate(history)]
            series.replace(points)


class SidebarButton(QPushButton):
    """
    Custom sidebar navigation button with programmatically drawn vector icons.
    """
    def __init__(self, icon_name, tooltip_text, parent=None):
        super().__init__(parent)
        self.setObjectName("SidebarButton")
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip_text)
        self.icon_name = icon_name

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        w, h = self.width(), self.height()
        
        # Draw background based on state
        bg_color = QColor(0, 0, 0, 0)
        if self.isChecked():
            bg_color = QColor(28, 59, 101) # checked blue
        elif self.underMouse():
            bg_color = QColor(18, 40, 70) # hover blue
            
        if bg_color.alpha() > 0:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(bg_color))
            painter.drawRoundedRect(4, 4, w - 8, h - 8, 6, 6)
            
        # Determine icon color based on state
        icon_color = QColor(106, 137, 176) # standby dim light blue
        if self.isChecked():
            icon_color = QColor(255, 255, 255) # active white
        elif self.underMouse():
            icon_color = QColor(0, 229, 255) # hover cyan
            
        painter.setPen(QPen(icon_color, 2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        
        cx, cy = w / 2.0, h / 2.0
        
        # Draw vector icon by name
        if self.icon_name == "earth":
            # Draw Globe (Circle with latitude/longitude lines)
            r = 9
            painter.drawEllipse(cx - r, cy - r, r * 2, r * 2)
            painter.drawEllipse(cx - r/2, cy - r, r, r * 2)
            painter.drawLine(cx - r, cy, cx + r, cy)
            
        elif self.icon_name == "plan":
            # Draw Path/Route (Three connected dots with lines)
            painter.drawLine(cx - 8, cy + 8, cx + 2, cy - 8)
            painter.drawLine(cx + 2, cy - 8, cx + 8, cy + 4)
            
            painter.setBrush(QBrush(icon_color))
            painter.drawEllipse(cx - 10, cy + 6, 4, 4)
            painter.drawEllipse(cx, cy - 10, 4, 4)
            painter.drawEllipse(cx + 6, cy + 2, 4, 4)
            
        elif self.icon_name == "setup":
            # Draw Joystick (Crosshairs/Base with stick handle)
            painter.drawRect(cx - 8, cy + 4, 16, 4)
            painter.drawLine(cx, cy + 4, cx, cy - 4)
            painter.setBrush(QBrush(icon_color))
            painter.drawEllipse(cx - 4, cy - 8, 8, 8)
            
        elif self.icon_name == "dashboard":
            # Draw Grid Dashboard (4 tiny squares)
            painter.drawRect(cx - 8, cy - 8, 7, 7)
            painter.drawRect(cx + 1, cy - 8, 7, 7)
            painter.drawRect(cx - 8, cy + 1, 7, 7)
            painter.drawRect(cx + 1, cy + 1, 7, 7)
            
        elif self.icon_name == "navigation":
            # Draw Compass Needle (diamond shape rotated slightly)
            painter.save()
            painter.translate(cx, cy)
            painter.rotate(45)
            
            poly = QPolygon([
                QPoint(0, -9),
                QPoint(3, 0),
                QPoint(0, 9),
                QPoint(-3, 0)
            ])
            painter.drawPolygon(poly)
            painter.drawLine(0, -9, 0, 9)
            painter.restore()
            
        elif self.icon_name == "docking":
            # Draw Ship Anchor
            painter.drawLine(cx, cy - 8, cx, cy + 6)
            painter.drawEllipse(cx - 3, cy - 11, 6, 6) # Ring
            painter.drawLine(cx - 6, cy - 4, cx + 6, cy - 4) # Crossbar
            
            # Curved anchor hooks
            path = QPainterPath()
            path.moveTo(cx - 8, cy)
            path.quadTo(cx, cy + 12, cx + 8, cy)
            painter.drawPath(path)
            painter.drawLine(cx - 8, cy, cx - 10, cy - 3)
            painter.drawLine(cx + 8, cy, cx + 10, cy - 3)
            
        elif self.icon_name == "communication":
            # Draw Antenna/Radio waves
            painter.drawLine(cx, cy - 2, cx, cy + 10)
            painter.setBrush(QBrush(icon_color))
            painter.drawEllipse(cx - 2, cy - 4, 4, 4)
            
            # Radio waves arcs
            painter.setBrush(Qt.NoBrush)
            painter.drawArc(cx - 6, cy - 8, 12, 12, 45 * 16, 90 * 16)
            painter.drawArc(cx - 10, cy - 12, 20, 20, 45 * 16, 90 * 16)
            
        elif self.icon_name == "depth":
            # Draw vertical depth sonar lines
            painter.drawLine(cx - 8, cy - 8, cx + 8, cy - 8)
            painter.setPen(QPen(icon_color, 2, Qt.DashLine))
            painter.drawLine(cx - 8, cy - 1, cx + 8, cy - 1)
            painter.drawLine(cx - 8, cy + 6, cx + 8, cy + 6)
            
        elif self.icon_name in ("docking_visual", "docking_top"):
            # Draw docking station cone on left and approaching AUV on right
            # Docking station funnel/cone
            cone_path = QPainterPath()
            cone_path.moveTo(cx - 9, cy - 7)
            cone_path.lineTo(cx - 2, cy - 3)
            cone_path.lineTo(cx - 2, cy + 3)
            cone_path.lineTo(cx - 9, cy + 7)
            cone_path.closeSubpath()
            painter.setPen(QPen(icon_color, 1.4))
            painter.setBrush(QBrush(QColor(icon_color.red(), icon_color.green(), icon_color.blue(), 50)))
            painter.drawPath(cone_path)
            
            # Approaching AUV capsule on right
            painter.setBrush(QBrush(icon_color))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(QRectF(cx + 2, cy - 3, 8, 6), 2.5, 2.5)
            
            # Pulsing alignment guide line (dashed)
            painter.setPen(QPen(icon_color, 1.2, Qt.DashLine))
            painter.drawLine(cx - 2, cy, cx + 2, cy)

        elif self.icon_name == "camera_detection":
            # Draw camera body with lens, framed by detection corner brackets
            painter.drawRoundedRect(QRectF(cx - 7, cy - 4, 14, 10), 2, 2)
            painter.drawLine(QPointF(cx - 3, cy - 4), QPointF(cx - 1.5, cy - 6.5))
            painter.drawLine(QPointF(cx - 1.5, cy - 6.5), QPointF(cx + 1.5, cy - 6.5))
            painter.drawLine(QPointF(cx + 1.5, cy - 6.5), QPointF(cx + 3, cy - 4))
            painter.drawEllipse(QPointF(cx, cy + 1), 2.6, 2.6)
            painter.setPen(QPen(icon_color, 1.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                bx, by = cx + sx * 11, cy + sy * 10
                painter.drawLine(QPointF(bx, by), QPointF(bx - sx * 3.5, by))
                painter.drawLine(QPointF(bx, by), QPointF(bx, by - sy * 3.5))

        elif self.icon_name == "sensor_fusion":
            # Four sensor nodes feeding one fused centre
            painter.setBrush(Qt.NoBrush)
            for dx, dy in ((-8, -8), (8, -8), (-8, 8), (8, 8)):
                painter.drawLine(QPointF(cx + dx * 0.55, cy + dy * 0.55), QPointF(cx + dx * 0.25, cy + dy * 0.25))
                painter.drawEllipse(QPointF(cx + dx, cy + dy), 2.6, 2.6)
            painter.setBrush(QBrush(icon_color))
            painter.drawEllipse(QPointF(cx, cy), 3.4, 3.4)

        elif self.icon_name == "settings":
            # Draw gear cog wheel
            r_out = 8
            r_in = 3
            painter.drawEllipse(cx - r_out, cy - r_out, r_out * 2, r_out * 2)
            painter.drawEllipse(cx - r_in, cy - r_in, r_in * 2, r_in * 2)
            # Cog teeth
            for step in range(8):
                painter.save()
                painter.translate(cx, cy)
                painter.rotate(step * 45)
                painter.drawLine(0, -r_out, 0, -r_out - 3)
                painter.restore()
                
        elif self.icon_name == "about":
            # Draw Info symbol (i inside a circle)
            painter.drawEllipse(cx - 8, cy - 8, 16, 16)
            painter.drawLine(cx, cy - 1, cx, cy + 4)
            # Dot on top of i
            painter.setBrush(QBrush(icon_color))
            painter.drawEllipse(cx - 1, cy - 5, 2, 2)


class Sidebar(QFrame):
    """
    Vertical sidebar containing icon-only navigation buttons.
    """
    # Signal emitted when a page selection is changed
    page_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SidebarFrame")
        self.setFixedWidth(50)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 10)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignTop)
        
        self.buttons = []
        
        # Top Group: Main Navigation (Earth, Plan, Setup, Dashboard, Docking Operations, Docking)
        top_group = [
            ("earth", "Earth Map Operations", 1),
            ("plan", "Waypoint Plan", 2),
            ("setup", "Hardware Setup", 3),
            ("dashboard", "Dashboard (Waves)", 0),
            ("docking", "Docking Operations & Data", 4),
            ("docking_visual", "Docking", 7),
            ("camera_detection", "Camera Detection", 8),
            ("sensor_fusion", "Sensor Fusion", 9)
        ]
        for icon_name, tooltip, idx in top_group:
            btn = SidebarButton(icon_name, tooltip, self)
            btn.setProperty("page_index", idx)
            btn.clicked.connect(self.on_button_clicked)
            layout.addWidget(btn)
            self.buttons.append(btn)
            
        # Spacing stretch pushes settings and about to bottom
        layout.addStretch()
        
        # Bottom Group: Settings & About
        bottom_group = [
            ("settings", "Application Settings", 5),
            ("about", "About System", 6)
        ]
        for icon_name, tooltip, idx in bottom_group:
            btn = SidebarButton(icon_name, tooltip, self)
            btn.setProperty("page_index", idx)
            btn.clicked.connect(self.on_button_clicked)
            layout.addWidget(btn)
            self.buttons.append(btn)
            
        # Default select the Earth Map button (first in buttons)
        self.buttons[0].setChecked(True)

    def on_button_clicked(self):
        sender = self.sender()
        if sender:
            idx = sender.property("page_index")
            self.select_page(idx)
            self.page_changed.emit(idx)

    def select_page(self, index):
        for btn in self.buttons:
            if btn.property("page_index") == index:
                btn.setChecked(True)
            else:
                btn.setChecked(False)


class Ping360SonarWidget(QWidget):
    """
    State-of-the-art Marine Acoustic Scanning Sonar (PPI Radar Scope) Widget.
    Features:
      - 360-degree tactical PPI scope with outer azimuth compass ring & degree tick marks
      - Sector scanning cone visualization (180° - 360° / 200 - 400 grad)
      - Dynamic acoustic echo colormap (Navy -> Blue -> Copper -> Gold -> Peak White)
      - Trailing phosphor decay beam & sweep line indicator
      - Acoustic target contact lock (pulsing ping marker, distance vector, and target data tag)
      - Vehicle orientation chevron (pointing forward to vehicle bow)
      - Tactical corner HUD telemetry (Frequency, Range, Gain, Head Angle, Target Solution)
      - Standby overlay watermark when stopped
    """
    def __init__(self, parent=None, theme="cockpit"):
        super().__init__(parent)
        self.theme = theme
        self.setMinimumSize(200, 200)
        self.image = None
        self.max_range = 10.0
        self.current_angle = 0.0 # in degrees (0-360)
        self.scan_intensity = 0.0
        self.is_active = False
        self.target_dist = None
        self.target_bearing = None
        self.target_conf = 100
        self.sector_start = 180.0
        self.sector_stop = 360.0
        self.gain = 50
        self.frequency_khz = 750

    def set_theme(self, theme):
        self.theme = theme
        self.update()

    def set_active(self, is_active: bool):
        if self.is_active != is_active:
            self.is_active = is_active
            self.update()

    def set_target(self, dist, bearing, conf=100):
        self.target_dist = dist
        self.target_bearing = bearing
        self.target_conf = conf
        self.update()

    def set_range(self, max_range: float):
        self.max_range = float(max_range)
        self.update()

    def set_sector(self, start_angle: float, stop_angle: float):
        self.sector_start = float(start_angle)
        self.sector_stop = float(stop_angle)
        self.update()

    def clear_scan(self):
        self.target_dist = None
        self.target_bearing = None
        if self.image is not None:
            self.image.fill(QColor(6, 12, 22))
        self.update()
        
    def update_scan_line(self, angle, max_range, data):
        self.current_angle = float(angle)
        self.max_range = float(max_range)
        self.is_active = True
        
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return
            
        # Initialize or resize persistent QImage
        if self.image is None or self.image.size() != self.size():
            self.image = QImage(self.size(), QImage.Format_ARGB32_Premultiplied)
            self.image.fill(QColor(6, 12, 22))
            
        # Draw new ray onto persistent QImage
        painter = QPainter(self.image)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # Apply phosphor decay (fades out previous sweeps smoothly)
        painter.fillRect(self.image.rect(), QColor(6, 12, 22, 6))
        
        center_x = w / 2.0
        center_y = h / 2.0
        bezel_margin = 8
        outer_radius = min(center_x, center_y) - bezel_margin
        bezel_width = 18 if outer_radius > 90 else 10
        radius = max(20.0, outer_radius - bezel_width)
        
        # 0 degrees is straight up (Forward/Bow). Angles rotate clockwise.
        theta = math.radians(angle) - math.pi / 2
        end_x = center_x + radius * math.cos(theta)
        end_y = center_y + radius * math.sin(theta)
        
        grad = QLinearGradient(center_x, center_y, end_x, end_y)
        step = max(1, len(data) // 60)
        downsampled = data[::step]
        
        if downsampled:
            self.scan_intensity = sum(downsampled) / len(downsampled)
        
        for idx, val in enumerate(downsampled):
            pos = idx / max(1, len(downsampled) - 1)
            val = float(val)
            # High-definition acoustic colormap (Navy -> Blue -> Copper -> Gold -> Peak White)
            if val < 40:
                r = int(val * 0.3)
                g = int(val * 0.8)
                b = int(val * 2.2)
            elif val < 120:
                norm = (val - 40) / 80.0
                r = int(12 + norm * 200)
                g = int(32 + norm * 80)
                b = int(88 - norm * 60)
            elif val < 200:
                norm = (val - 120) / 80.0
                r = int(212 + norm * 43)
                g = int(112 + norm * 88)
                b = int(28 - norm * 28)
            else:
                norm = (val - 200) / 55.0
                r = 255
                g = int(200 + norm * 55)
                b = int(norm * 220)
            grad.setColorAt(pos, QColor(min(255, max(0, r)), min(255, max(0, g)), min(255, max(0, b))))
            
        # Draw ray segment with slight width for smooth continuous sector display
        pen = QPen(grad, max(2, int(radius * 0.038)))
        painter.setPen(pen)
        painter.drawLine(QPointF(center_x, center_y), QPointF(end_x, end_y))
        
        painter.end()
        self.update()
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        w = self.width()
        h = self.height()
        center_x = w / 2.0
        center_y = h / 2.0
        
        bezel_margin = 8
        outer_radius = min(center_x, center_y) - bezel_margin
        if outer_radius <= 20:
            return
            
        bezel_width = 18 if outer_radius > 90 else 10
        scope_radius = outer_radius - bezel_width
        
        # 1. Background
        painter.fillRect(self.rect(), QColor(10, 15, 25))
        
        # 2. Outer Bezel Ring
        bezel_grad = QLinearGradient(center_x - outer_radius, center_y - outer_radius, 
                                     center_x + outer_radius, center_y + outer_radius)
        bezel_grad.setColorAt(0.0, QColor(26, 36, 52))
        bezel_grad.setColorAt(1.0, QColor(13, 20, 32))
        painter.setPen(QPen(QColor(40, 58, 82), 1.5))
        painter.setBrush(bezel_grad)
        painter.drawEllipse(QPointF(center_x, center_y), outer_radius, outer_radius)
        
        # Scope clip path
        scope_path = QPainterPath()
        scope_path.addEllipse(QPointF(center_x, center_y), scope_radius, scope_radius)
        
        # Scope interior background
        screen_grad = QRadialGradient(center_x, center_y, scope_radius)
        screen_grad.setColorAt(0.0, QColor(8, 16, 28))
        screen_grad.setColorAt(0.75, QColor(5, 11, 20))
        screen_grad.setColorAt(1.0, QColor(3, 7, 14))
        painter.setPen(Qt.NoPen)
        painter.setBrush(screen_grad)
        painter.drawPath(scope_path)
        
        # 3. Compass Azimuth Ring & Degree Ticks
        painter.save()
        painter.setFont(QFont("Google Sans", 7, QFont.Bold))
        for deg in range(0, 360, 10):
            rad = math.radians(deg - 90)
            cos_a = math.cos(rad)
            sin_a = math.sin(rad)
            
            is_major = (deg % 30 == 0)
            is_cardinal = (deg % 90 == 0)
            
            tick_len = (bezel_width * 0.5) if is_major else (bezel_width * 0.28)
            p1_r = outer_radius - 2
            p2_r = p1_r - tick_len
            
            if is_cardinal:
                tick_color = QColor(0, 229, 255, 220)
                pen_w = 1.8
            elif is_major:
                tick_color = QColor(148, 163, 184, 180)
                pen_w = 1.2
            else:
                tick_color = QColor(71, 85, 105, 130)
                pen_w = 0.8
                
            painter.setPen(QPen(tick_color, pen_w))
            painter.drawLine(QPointF(center_x + p1_r * cos_a, center_y + p1_r * sin_a),
                             QPointF(center_x + p2_r * cos_a, center_y + p2_r * sin_a))
                             
            if is_major and outer_radius >= 110:
                label_r = outer_radius - bezel_width * 0.5 - 1
                lx = center_x + label_r * cos_a
                ly = center_y + label_r * sin_a
                
                if deg == 0:
                    lbl = "N"
                    painter.setPen(QColor(0, 229, 255))
                elif deg == 90:
                    lbl = "E"
                    painter.setPen(QColor(0, 229, 255))
                elif deg == 180:
                    lbl = "S"
                    painter.setPen(QColor(0, 229, 255))
                elif deg == 270:
                    lbl = "W"
                    painter.setPen(QColor(0, 229, 255))
                else:
                    lbl = f"{deg:03d}"
                    painter.setPen(QColor(100, 116, 139))
                    
                painter.drawText(QRectF(lx - 12, ly - 6, 24, 12), Qt.AlignCenter, lbl)
        painter.restore()
        
        # 4. Scope Screen Contents
        painter.save()
        painter.setClipPath(scope_path)
        
        # Draw persistent acoustic scan QImage
        if self.image is not None:
            painter.drawImage(0, 0, self.image)
            
        # Draw active scan sector pie highlight (180° - 360°)
        if self.sector_start is not None and self.sector_stop is not None:
            span_deg = (self.sector_stop - self.sector_start) % 360.0
            if span_deg == 0 and self.sector_stop != self.sector_start:
                span_deg = 360.0
            qt_start = int((90 - self.sector_stop) * 16)
            qt_span = int(span_deg * 16)
            painter.setPen(QPen(QColor(0, 229, 255, 30), 1, Qt.DashLine))
            painter.setBrush(QColor(0, 229, 255, 10))
            painter.drawPie(QRectF(center_x - scope_radius, center_y - scope_radius, 
                                  scope_radius * 2, scope_radius * 2), qt_start, qt_span)
        
        # Polar grid concentric rings
        for factor in [0.25, 0.50, 0.75, 1.00]:
            r = scope_radius * factor
            is_outer = (factor == 1.0)
            pen_color = QColor(0, 229, 255, 70) if is_outer else QColor(45, 68, 98, 90)
            pen_style = Qt.SolidLine if is_outer else Qt.DashLine
            painter.setPen(QPen(pen_color, 1, pen_style))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QPointF(center_x, center_y), r, r)
            
        # Radial crosshairs & diagonals
        for deg in [0, 45, 90, 135, 180, 225, 270, 315]:
            rad = math.radians(deg - 90)
            rx = center_x + scope_radius * math.cos(rad)
            ry = center_y + scope_radius * math.sin(rad)
            is_primary = (deg % 90 == 0)
            pen_color = QColor(45, 68, 98, 120) if is_primary else QColor(30, 46, 70, 70)
            painter.setPen(QPen(pen_color, 1, Qt.DashLine))
            painter.drawLine(QPointF(center_x, center_y), QPointF(rx, ry))

        # Range metric labels on 45° azimuth
        if scope_radius > 60:
            painter.setFont(QFont("Google Sans", 7, QFont.Bold))
            rad45 = math.radians(45 - 90)
            for factor in [0.25, 0.50, 0.75, 1.00]:
                r = scope_radius * factor
                rx = center_x + r * math.cos(rad45)
                ry = center_y + r * math.sin(rad45)
                rng_text = f"{self.max_range * factor:.1f}m"
                tw = 26
                th = 11
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(6, 12, 22, 210))
                painter.drawRoundedRect(QRectF(rx - tw/2, ry - th/2, tw, th), 2, 2)
                painter.setPen(QColor(148, 163, 184))
                painter.drawText(QRectF(rx - tw/2, ry - th/2, tw, th), Qt.AlignCenter, rng_text)
                
        # Sweep phosphor trailing wedge
        sweep_rad = math.radians(self.current_angle - 90)
        trail_deg = 16.0
        qt_sweep_start = int((90 - self.current_angle) * 16)
        qt_trail_span = int(trail_deg * 16)
        painter.setPen(Qt.NoPen)
        trail_grad = QRadialGradient(center_x, center_y, scope_radius)
        trail_grad.setColorAt(0.0, QColor(0, 229, 255, 75))
        trail_grad.setColorAt(1.0, QColor(0, 229, 255, 0))
        painter.setBrush(trail_grad)
        painter.drawPie(QRectF(center_x - scope_radius, center_y - scope_radius,
                              scope_radius * 2, scope_radius * 2), qt_sweep_start, qt_trail_span)

        # Active Sweep Line
        sx = center_x + scope_radius * math.cos(sweep_rad)
        sy = center_y + scope_radius * math.sin(sweep_rad)
        painter.setPen(QPen(QColor(0, 229, 255, 230), 1.8, Qt.SolidLine))
        painter.drawLine(QPointF(center_x, center_y), QPointF(sx, sy))
        painter.setBrush(QColor(0, 229, 255))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(QPointF(sx, sy), 2.5, 2.5)

        # Target Contact Marker (if tracked obstacle/target is present)
        if (self.target_dist is not None and self.target_dist > 0 and 
            self.target_bearing is not None):
            t_rad = math.radians(self.target_bearing - 90)
            r_ratio = min(1.0, self.target_dist / max(0.1, self.max_range))
            t_px = scope_radius * r_ratio
            tx = center_x + t_px * math.cos(t_rad)
            ty = center_y + t_px * math.sin(t_rad)
            
            # Tracking vector line from vehicle to target
            painter.setPen(QPen(QColor(255, 61, 0, 120), 1, Qt.DashLine))
            painter.drawLine(QPointF(center_x, center_y), QPointF(tx, ty))
            
            # Glowing target ping rings
            painter.setPen(QPen(QColor(255, 61, 0, 90), 1))
            painter.setBrush(QColor(255, 61, 0, 35))
            painter.drawEllipse(QPointF(tx, ty), 10, 10)
            
            # Center target reticle
            painter.setPen(QPen(QColor(255, 23, 68), 2))
            painter.setBrush(QColor(255, 255, 255))
            painter.drawEllipse(QPointF(tx, ty), 3.5, 3.5)
            
            # Crosshair ticks on target
            painter.setPen(QPen(QColor(255, 23, 68), 1.5))
            painter.drawLine(QPointF(tx - 7, ty), QPointF(tx - 4, ty))
            painter.drawLine(QPointF(tx + 4, ty), QPointF(tx + 7, ty))
            painter.drawLine(QPointF(tx, ty - 7), QPointF(tx, ty - 4))
            painter.drawLine(QPointF(tx, ty + 4), QPointF(tx, ty + 7))
            
            # Floating callout badge next to target
            if scope_radius >= 90:
                badge_text = f"{self.target_dist:.2f}m @ {self.target_bearing:.0f}°"
                bw = 72
                bh = 15
                bx = min(center_x + scope_radius - bw - 4, max(center_x - scope_radius + 4, tx + 9))
                by = min(center_y + scope_radius - bh - 4, max(center_y - scope_radius + 4, ty - 7))
                painter.setPen(QPen(QColor(255, 61, 0, 180), 1))
                painter.setBrush(QColor(15, 23, 42, 220))
                painter.drawRoundedRect(QRectF(bx, by, bw, bh), 3, 3)
                painter.setPen(QColor(255, 255, 255))
                painter.setFont(QFont("Google Sans", 7, QFont.Bold))
                painter.drawText(QRectF(bx, by, bw, bh), Qt.AlignCenter, badge_text)
                
        # Center Vehicle Chevron (AUV Forward Orientation Marker)
        painter.save()
        painter.translate(center_x, center_y)
        v_poly = QPolygonF([
            QPointF(0, -9),
            QPointF(5, 6),
            QPointF(0, 3),
            QPointF(-5, 6),
        ])
        painter.setPen(QPen(QColor(0, 229, 255), 1.5))
        painter.setBrush(QColor(0, 229, 255, 200))
        painter.drawPolygon(v_poly)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255))
        painter.drawEllipse(QPointF(0, 0), 1.5, 1.5)
        painter.restore()

        # Standby Overlay Watermark
        if not self.is_active:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(5, 11, 20, 160))
            painter.drawRect(QRectF(center_x - scope_radius, center_y - scope_radius,
                                   scope_radius * 2, scope_radius * 2))
            
            pill_w = 164
            pill_h = 38
            painter.setPen(QPen(QColor(51, 65, 85), 1.2))
            painter.setBrush(QColor(15, 23, 42, 230))
            painter.drawRoundedRect(QRectF(center_x - pill_w/2, center_y - pill_h/2, pill_w, pill_h), 6, 6)
            
            painter.setFont(QFont("Google Sans", 8, QFont.Bold))
            painter.setPen(QColor(148, 163, 184))
            painter.drawText(QRectF(center_x - pill_w/2, center_y - pill_h/2 + 5, pill_w, 14), 
                             Qt.AlignCenter, "PING360 STANDBY")
            painter.setFont(QFont("Google Sans", 7))
            painter.setPen(QColor(100, 116, 139))
            painter.drawText(QRectF(center_x - pill_w/2, center_y - pill_h/2 + 19, pill_w, 14), 
                             Qt.AlignCenter, "PRESS ACTIVATE TO SCAN")
                             
        painter.restore()

        # 5. Tactical Corner HUD Overlays
        if w >= 220 and h >= 220:
            painter.setFont(QFont("Consolas", 8, QFont.Bold))
            
            # Top-Left: Model & Frequency
            painter.setPen(QColor(0, 229, 255, 220))
            painter.drawText(12, 18, "PING360 SCANNER")
            painter.setFont(QFont("Consolas", 7.5))
            painter.setPen(QColor(148, 163, 184, 180))
            painter.drawText(12, 30, f"{self.frequency_khz} kHz • CHIRP")
            
            # Top-Right: Range & Gain
            painter.setFont(QFont("Consolas", 8, QFont.Bold))
            painter.setPen(QColor(226, 232, 240, 220))
            rng_s = f"RNG: {self.max_range:.1f}m"
            painter.drawText(int(w - 12 - painter.fontMetrics().horizontalAdvance(rng_s)), 18, rng_s)
            painter.setFont(QFont("Consolas", 7.5))
            painter.setPen(QColor(148, 163, 184, 180))
            gain_s = f"GAIN: {self.gain}%"
            painter.drawText(int(w - 12 - painter.fontMetrics().horizontalAdvance(gain_s)), 30, gain_s)
            
            # Bottom-Left: Current Scan Head
            painter.setFont(QFont("Consolas", 8, QFont.Bold))
            painter.setPen(QColor(0, 229, 255, 220))
            head_s = f"HEAD: {self.current_angle:05.1f}°"
            painter.drawText(12, h - 14, head_s)
            
            # Bottom-Right: Target Contact status
            painter.setFont(QFont("Consolas", 8, QFont.Bold))
            if self.target_dist is not None and self.target_dist > 0 and self.target_bearing is not None:
                painter.setPen(QColor(255, 61, 0, 240))
                tgt_s = f"TGT: {self.target_dist:.2f}m @ {self.target_bearing:05.1f}°"
            elif self.is_active:
                painter.setPen(QColor(0, 230, 118, 220))
                tgt_s = "SCANNING SECTOR"
            else:
                painter.setPen(QColor(148, 163, 184, 180))
                tgt_s = "OFFLINE"
            painter.drawText(int(w - 12 - painter.fontMetrics().horizontalAdvance(tgt_s)), h - 14, tgt_s)


class Marine3DHorizon(QWidget):
    """
    Premium 3D Spherical Gyroscopic Horizon displaying curved attitude scale markings,
    roll pointer indicators, and a radial 3D glass shading dome.
    """
    def __init__(self, parent=None, theme="cockpit"):
        super().__init__(parent)
        self.theme = theme
        self.setMinimumSize(70, 70)
        self.roll = 0.0
        self.pitch = 0.0

    def set_attitude(self, roll, pitch):
        self.roll = float(roll)
        self.pitch = float(pitch)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        width = self.width()
        height = self.height()
        size = min(width, height) - 10
        center_x = width / 2
        center_y = height / 2
        radius = size / 2

        # Outer clipping sphere
        painter.save()
        clip_path = QPainterPath()
        clip_path.addEllipse(center_x - radius, center_y - radius, size, size)
        painter.setClipPath(clip_path)

        # 1. Paint Sky background (with vertical gradient for height feel)
        sky_gradient = QLinearGradient(0, center_y - radius, 0, center_y + radius)
        if self.theme == "cockpit":
            sky_gradient.setColorAt(0.0, QColor(25, 90, 150))
            sky_gradient.setColorAt(1.0, QColor(42, 129, 198))
        else:
            sky_gradient.setColorAt(0.0, QColor(5, 15, 30))
            sky_gradient.setColorAt(1.0, QColor(15, 35, 60))
        painter.fillRect(self.rect(), QBrush(sky_gradient))

        # Rotate and translate for Pitch and Roll
        painter.save()
        painter.translate(center_x, center_y)
        painter.rotate(-self.roll)
        
        # 1 degree of pitch = 2.4 pixels shift
        pitch_offset = self.pitch * 2.4
        pitch_offset = max(-radius, min(radius, pitch_offset))

        # 2. Paint Ground/Sea block below the pitch horizon
        if self.theme == "cockpit":
            ground_brush = QBrush(QColor(120, 75, 35))
        else:
            ground_brush = QBrush(QColor(10, 60, 80))
        
        # We want to fill the ground area below the horizon line.
        # To handle rotation and curves properly, we can draw a large rect below pitch_offset.
        painter.setPen(Qt.NoPen)
        painter.setBrush(ground_brush)
        painter.drawRect(-int(size * 2), int(pitch_offset), int(size * 4), int(size * 4))

        # 3. Paint Curved Latitude / Pitch Scale Lines (Orthographic Sphere projection)
        import math
        if self.theme == "cockpit":
            line_pen = QPen(QColor(255, 255, 255, 180), 1.5)
            text_color = QColor(255, 255, 255, 220)
        else:
            line_pen = QPen(QColor(0, 229, 255, 150), 1.5)
            text_color = QColor(226, 241, 255, 220)
            
        painter.setFont(QFont("Google Sans", 7, QFont.Bold))
        
        # Draw central horizon line
        painter.setPen(line_pen)
        painter.drawLine(-int(radius), int(pitch_offset), int(radius), int(pitch_offset))
        
        # Curved pitch lines
        for p in [-30, -20, -10, 10, 20, 30]:
            p_y = pitch_offset - (p * 2.4)
            # Check if this pitch line is visible inside the sphere
            if -radius < p_y < radius:
                # Math for curvature (orthographic sphere projection projection)
                # Curve offset bends lines back towards center to look 3D
                curve_bend = p_y * 0.12
                
                # Determine width of tick line at this height
                rem_r_sq = radius**2 - p_y**2
                line_w = 24 if p % 10 == 0 else 12
                if rem_r_sq > 0:
                    max_w = math.sqrt(rem_r_sq)
                    line_w = min(line_w, max_w)
                
                # Draw curved path
                curve_path = QPainterPath()
                curve_path.moveTo(-line_w, p_y)
                curve_path.quadTo(0, p_y + curve_bend, line_w, p_y)
                painter.drawPath(curve_path)
                
                # Draw vertical tick legs at the ends of each pitch line (military HUD style)
                leg_h = 4 if p > 0 else -4
                painter.drawLine(-line_w, int(p_y), -line_w, int(p_y + leg_h))
                painter.drawLine(line_w, int(p_y), line_w, int(p_y + leg_h))
                
                # Text labels on sides of major ticks
                if p % 10 == 0 and line_w > 15:
                    painter.setPen(QPen(text_color))
                    painter.drawText(int(line_w + 4), int(p_y - 5), f"{abs(p)}")
                    painter.drawText(int(-line_w - 18), int(p_y - 5), f"{abs(p)}")
                    painter.setPen(line_pen)

        painter.restore() # Restore pitch/roll transform
        
        # 4. Paint Roll Graduation Arc and Ticks at the top boundary
        painter.save()
        painter.translate(center_x, center_y)
        
        arc_pen = QPen(QColor(255, 255, 255, 100), 1, Qt.SolidLine)
        painter.setPen(arc_pen)
        # Draw top roll scale arc
        arc_r = radius - 10
        painter.drawArc(-int(arc_r), -int(arc_r), int(arc_r * 2), int(arc_r * 2), 30 * 16, 120 * 16)
        
        # Draw roll scale tick marks
        roll_ticks = [-60, -45, -30, -20, -10, 0, 10, 20, 30, 45, 60]
        for tick in roll_ticks:
            painter.save()
            painter.rotate(tick)
            tick_len = 8 if tick % 30 == 0 else 5
            painter.drawLine(0, -int(arc_r), 0, -int(arc_r) + tick_len)
            painter.restore()
            
        # Draw current roll pointer triangle (counter-rotated relative to vehicle roll)
        painter.save()
        painter.rotate(-self.roll)
        pointer_pen = QPen(QColor(255, 214, 0), 1.5)
        painter.setPen(pointer_pen)
        painter.setBrush(QBrush(QColor(255, 214, 0)))
        poly = QPolygon([
            QPoint(0, -int(arc_r) + 2),
            QPoint(-5, -int(arc_r) + 10),
            QPoint(5, -int(arc_r) + 10)
        ])
        painter.drawPolygon(poly)
        painter.restore()
        
        painter.restore()

        # 5. Shading: 3D Spherical Radial Gradient overlay (creates glass dome and depth)
        radial_grad = QRadialGradient(center_x - radius*0.2, center_y - radius*0.2, radius * 1.4)
        radial_grad.setColorAt(0.0, QColor(255, 255, 255, 35)) # Top-left light reflection shine
        radial_grad.setColorAt(0.4, QColor(0, 0, 0, 0))        # Clear viewport center
        radial_grad.setColorAt(0.85, QColor(0, 0, 0, 60))      # Spherical dimming
        radial_grad.setColorAt(1.0, QColor(0, 0, 0, 180))      # Dark sphere shadow edges
        
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(radial_grad))
        painter.drawEllipse(center_x - radius, center_y - radius, size, size)
        
        painter.restore() # Restore clipping

        # 6. Stationary Central Vessel Reference Symbol (drawn on top of dome)
        ref_pen = QPen(QColor(255, 214, 0), 2.5) # Cockpit yellow outline
        painter.setPen(ref_pen)
        painter.setBrush(Qt.NoBrush)
        
        # Left Wing
        painter.drawLine(center_x - 28, center_y, center_x - 12, center_y)
        painter.drawLine(center_x - 12, center_y, center_x - 12, center_y + 5)
        # Right Wing
        painter.drawLine(center_x + 12, center_y, center_x + 28, center_y)
        painter.drawLine(center_x + 12, center_y, center_x + 12, center_y + 5)
        # Center reference point dot
        painter.setBrush(QBrush(QColor(255, 214, 0)))
        painter.drawEllipse(center_x - 3, center_y - 3, 6, 6)

        # 7. Draw Dial Border Ring
        border_pen = QPen(QColor(28, 59, 101), 2)
        painter.setPen(border_pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(center_x - radius, center_y - radius, size, size)


class VisionTargetWidget(QWidget):
    """
    Simulated computer vision target tracking viewport with crosshair, bounding box, and target metrics.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(180, 180)
        self.distance = 4.2
        self.angle = 12.5
        self.target_locked = True
        self.sweep_step = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate_sweep)
        self.timer.start(100)

    def animate_sweep(self):
        self.sweep_step = (self.sweep_step + 1) % 40
        self.update()

    def set_target(self, dist, ang, locked):
        self.distance = float(dist)
        self.angle = float(ang)
        self.target_locked = bool(locked)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        w, h = self.width(), self.height()
        painter.fillRect(self.rect(), QColor(10, 15, 25))
        
        # Draw camera viewfinder border corners
        pen_glow = QPen(QColor(0, 229, 255, 100), 2)
        painter.setPen(pen_glow)
        margin = 15
        line_len = 15
        
        # Top-left corner
        painter.drawLine(margin, margin, margin + line_len, margin)
        painter.drawLine(margin, margin, margin, margin + line_len)
        # Top-right corner
        painter.drawLine(w - margin, margin, w - margin - line_len, margin)
        painter.drawLine(w - margin, margin, w - margin, margin + line_len)
        # Bottom-left corner
        painter.drawLine(margin, h - margin, margin + line_len, h - margin)
        margin_x, margin_y = margin, h - margin
        painter.drawLine(margin_x, margin_y, margin_x, margin_y - line_len)
        # Bottom-right corner
        painter.drawLine(w - margin, h - margin, w - margin - line_len, h - margin)
        painter.drawLine(w - margin, h - margin, w - margin, h - margin - line_len)
        
        # Center crosshair
        cx, cy = w / 2.0, h / 2.0
        painter.setPen(QPen(QColor(0, 229, 255, 80), 1, Qt.DashLine))
        painter.drawLine(cx - 30, cy, cx + 30, cy)
        painter.drawLine(cx, cy - 30, cx, cy + 30)
        painter.drawEllipse(int(cx - 15), int(cy - 15), 30, 30)
        
        # Draw target bounding box (locked: green, search: red)
        if self.target_locked:
            box_color = QColor(0, 230, 118) # Neon green
            box_text = "LOCK"
        else:
            box_color = QColor(255, 23, 73) # Red search
            box_text = "SEARCHING"
            
        # Add slight animation bounce to target box
        bounce = math.sin(self.sweep_step * 0.3) * 2
        bw, bh = 70 + bounce, 70 + bounce
        bx = int(cx - bw/2 + math.sin(self.sweep_step * 0.15) * 5)
        by = int(cy - bh/2 + math.cos(self.sweep_step * 0.1) * 3)
        
        painter.setPen(QPen(box_color, 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(bx, by, int(bw), int(bh))
        
        # Draw corner marks inside bounding box
        painter.setBrush(QBrush(box_color))
        painter.drawRect(bx, by, 6, 6)
        painter.drawRect(bx + int(bw) - 6, by, 6, 6)
        painter.drawRect(bx, by + int(bh) - 6, 6, 6)
        painter.drawRect(bx + int(bw) - 6, by + int(bh) - 6, 6, 6)
        
        # Info overlays
        painter.setPen(QColor(226, 241, 255))
        painter.setFont(QFont("Google Sans", 7, QFont.Bold))
        painter.drawText(bx + 5, by + 15, box_text)
        
        # FLIR HUD overlays (horizontal and vertical ticks along borders)
        painter.setPen(QPen(QColor(0, 229, 255, 60), 1))
        painter.setFont(QFont("Consolas", 6))
        for tick_x in range(int(cx - 50), int(cx + 51), 10):
            painter.drawLine(tick_x, margin, tick_x, margin + 4)
        for tick_y in range(int(cy - 50), int(cy + 51), 10):
            painter.drawLine(w - margin - 4, tick_y, w - margin, tick_y)
            
        # Target details floating next to bounding box
        if self.target_locked:
            painter.setPen(QColor(0, 230, 118))
            painter.setFont(QFont("Consolas", 7, QFont.Bold))
            painter.drawText(bx + int(bw) + 6, by + 12, f"RNG: {self.distance:.1f}m")
            painter.drawText(bx + int(bw) + 6, by + 22, f"AZ : {self.angle:+.1f}°")
            painter.drawText(bx + int(bw) + 6, by + 32, "EL : -1.8°")
        
        painter.setFont(QFont("Google Sans", 7))
        painter.setPen(QColor(142, 183, 230))
        painter.drawText(15, h - 35, f"Dist: {self.distance:.2f} m")
        painter.drawText(15, h - 22, f"Angle: {self.angle:+.1f}°")


class ROVPositionWidget(QWidget):
    """
    Subsea Acoustic relative 3D tracking map (USBL relative grid).
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(180, 180)
        self.x = 2.5
        self.y = -3.8
        self.z = 1.2
        self.quality = 98.0
        self.sweep_angle = 0
        self.history = []
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.rotate_sweep)
        self.timer.start(50)

    def rotate_sweep(self):
        self.sweep_angle = (self.sweep_angle + 3) % 360
        self.update()

    def set_position(self, x, y, z, quality):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)
        self.quality = float(quality)
        self.history.append((self.x, self.y))
        if len(self.history) > 20:
            self.history.pop(0)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        w, h = self.width(), self.height()
        center_x, center_y = w / 2.0, h / 2.0
        radius = min(center_x, center_y) - 15
        
        # Dark ocean grid background
        painter.fillRect(self.rect(), QColor(5, 12, 22))
        
        # Draw concentric range rings
        painter.setPen(QPen(QColor(28, 59, 101, 150), 1))
        for r_factor in [0.33, 0.66, 1.0]:
            r = radius * r_factor
            painter.drawEllipse(center_x - r, center_y - r, r * 2, r * 2)
            
        # Draw crosshair grids
        painter.drawLine(center_x - radius, center_y, center_x + radius, center_y)
        painter.drawLine(center_x, center_y - radius, center_x, center_y + radius)
        
        # Draw rotating acoustic ping sweep line
        sweep_rad = math.radians(self.sweep_angle - 90)
        sx = center_x + radius * math.cos(sweep_rad)
        sy = center_y + radius * math.sin(sweep_rad)
        painter.setPen(QPen(QColor(0, 229, 255, 60), 2))
        painter.drawLine(center_x, center_y, sx, sy)
        
        # Plot target ROV (scale mapping: 10m range = radius)
        scale = radius / 10.0
        
        # Draw target history trail
        painter.setPen(Qt.NoPen)
        for idx, (hx, hy) in enumerate(self.history):
            alpha = int(120 * ((idx + 1) / (len(self.history) + 1)))
            htx = center_x + hx * scale
            hty = center_y - hy * scale
            painter.setBrush(QBrush(QColor(0, 230, 118, alpha)))
            painter.drawEllipse(htx - 2, hty - 2, 4, 4)
            
        tx = center_x + self.x * scale
        ty = center_y - self.y * scale  # Invert Y for cartesian grid
        
        # Target blip with glow ring
        painter.setPen(QPen(QColor(0, 230, 118, 120), 2))
        painter.setBrush(QBrush(QColor(0, 230, 118, 50)))
        painter.drawEllipse(tx - 10, ty - 10, 20, 20)
        
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(0, 230, 118)))
        painter.drawEllipse(tx - 4, ty - 4, 8, 8)
        
        # Label next to target
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(QFont("Google Sans", 7, QFont.Bold))
        painter.drawText(int(tx + 12), int(ty + 4), "ROV")
        
        # Text details
        painter.setPen(QColor(142, 183, 230))
        painter.setFont(QFont("Google Sans", 7))
        painter.drawText(10, h - 35, f"Rel: ({self.x:+.1f}, {self.y:+.1f}, {self.z:+.1f}) m")
        painter.drawText(10, h - 22, f"USBL Quality: {self.quality:.0f}%")


class AcousticFFTWidget(QWidget):
    """
    Real-time scrolling audio frequency spectrum FFT analyzer visualizer.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(180, 120)
        self.values = [0] * 20

    def set_values(self, values):
        if values:
            self.values = list(values)[:20]
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        w, h = self.width(), self.height()
        painter.fillRect(self.rect(), QColor(10, 15, 25))
        
        # Draw spectrum grid lines
        painter.setPen(QPen(QColor(28, 59, 101, 100), 1))
        for y_factor in [0.25, 0.5, 0.75]:
            painter.drawLine(0, int(h * y_factor), w, int(h * y_factor))
            
        bar_count = len(self.values)
        bar_width = max(2, int(w / bar_count) - 2)
        
        # Draw gradient colored FFT bars
        grad = QLinearGradient(0, h, 0, 0)
        grad.setColorAt(0.0, QColor(0, 229, 255))   # cyan base
        grad.setColorAt(0.7, QColor(0, 230, 118))   # green mid
        grad.setColorAt(1.0, QColor(255, 23, 73))   # red peak peaks
        
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(grad))
        
        for idx, val in enumerate(self.values):
            bar_h = int((val / 100.0) * (h - 20))
            x = idx * (bar_width + 2) + 5
            y = h - bar_h - 10
            painter.drawRect(x, y, bar_width, bar_h)
            
        # Draw bottom frequency axis line
        painter.setPen(QPen(QColor(28, 59, 101), 2))
        painter.drawLine(0, h - 10, w, h - 10)


class RadialGaugeWidget(QWidget):
    """
    Premium circular radial analog needle dial gauge widget for sensors.
    """
    def __init__(self, title, min_val, max_val, unit_str, parent=None):
        super().__init__(parent)
        self.setMinimumSize(120, 120)
        self.title = title
        self.min_val = min_val
        self.max_val = max_val
        self.unit_str = unit_str
        self.value = min_val

    def set_value(self, val):
        self.value = max(self.min_val, min(self.max_val, float(val)))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        w, h = self.width(), self.height()
        size = min(w, h) - 10
        center_x, center_y = w / 2.0, h / 2.0
        radius = size / 2.0
        
        # Dial backing
        painter.setPen(QPen(QColor(28, 59, 101), 2))
        painter.setBrush(QBrush(QColor(10, 18, 30)))
        painter.drawEllipse(center_x - radius, center_y - radius, size, size)
        
        # Draw dial gauge scale arc (-225 degrees to +45 degrees)
        painter.save()
        painter.translate(center_x, center_y)
        
        # Scale marks
        scale_pen = QPen(QColor(142, 183, 230), 1)
        painter.setPen(scale_pen)
        for i in range(11):
            angle = -225 + i * 27
            painter.save()
            painter.rotate(angle)
            painter.drawLine(0, -int(radius - 3), 0, -int(radius - 9))
            painter.restore()
            
        # Draw needle pointing to value
        val_pct = (self.value - self.min_val) / max(1.0, self.max_val - self.min_val)
        needle_angle = -225 + val_pct * 270
        
        painter.save()
        painter.rotate(needle_angle)
        needle_pen = QPen(QColor(255, 23, 73), 2.5) # Glowing red needle pointer
        painter.setPen(needle_pen)
        painter.drawLine(0, 0, 0, -int(radius - 12))
        painter.restore()
        
        # Needle cap
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(255, 23, 73)))
        painter.drawEllipse(-5, -5, 10, 10)
        
        painter.restore()
        
        # Labels
        painter.setPen(QColor(226, 241, 255))
        painter.setFont(QFont("Google Sans", 7, QFont.Bold))
        painter.drawText(0, int(center_y + radius - 28), w, 15, Qt.AlignCenter, f"{self.value:.1f} {self.unit_str}")
        
        painter.setPen(QColor(142, 183, 230))
        painter.setFont(QFont("Google Sans", 7))
        painter.drawText(0, int(center_y - radius + 20), w, 15, Qt.AlignCenter, self.title)


class VerticalDepthGauge(QWidget):
    """
    Vertical depth bar-gauge showing depth and altimeter height above seabed.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(80, 180)
        self.depth = 12.4
        self.altitude = 4.2

    def set_values(self, depth, altitude):
        self.depth = float(depth)
        self.altitude = float(altitude)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        w, h = self.width(), self.height()
        bar_w = 20
        bar_x = w / 2.0 - bar_w / 2.0
        bar_y = 15
        bar_h = h - 35
        
        painter.fillRect(self.rect(), QColor(10, 15, 25))
        
        # Track backing
        painter.setPen(QPen(QColor(28, 59, 101), 2))
        painter.setBrush(QBrush(QColor(5, 10, 18)))
        painter.drawRect(bar_x, bar_y, bar_w, bar_h)
        
        # Fill depth portion (e.g. max range 50m)
        max_range = 50.0
        pct = self.depth / max_range
        fill_h = int(bar_h * min(1.0, pct))
        
        depth_grad = QLinearGradient(bar_x, bar_y, bar_x, bar_y + bar_h)
        depth_grad.setColorAt(0.0, QColor(0, 229, 255, 100))
        depth_grad.setColorAt(1.0, QColor(0, 120, 255, 200))
        
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(depth_grad))
        painter.drawRect(bar_x + 1, bar_y + 1, bar_w - 2, fill_h - 1)
        
        # Draw ticks on sides
        painter.setPen(QPen(QColor(142, 183, 230), 1))
        painter.setFont(QFont("Google Sans", 7))
        for i in range(6):
            tick_y = bar_y + (i * 0.2) * bar_h
            painter.drawLine(int(bar_x - 6), int(tick_y), int(bar_x), int(tick_y))
            painter.drawText(int(bar_x - 32), int(tick_y + 4), f"{int(i * 0.2 * max_range)}m")
            
        # Draw Altitude seabed line reference
        seabed_y = bar_y + bar_h - 2
        painter.setPen(QPen(QColor(139, 90, 43), 3.5)) # Brown seabed line
        painter.drawLine(int(bar_x - 10), int(seabed_y), int(bar_x + bar_w + 10), int(seabed_y))
        
        # Overlay readings text
        painter.setPen(QColor(226, 241, 255))
        painter.drawText(0, h - 15, w, 15, Qt.AlignCenter, f"Alt: {self.altitude:.1f}m")
        painter.drawText(0, 2, w, 15, Qt.AlignCenter, f"Depth: {self.depth:.1f}m")


class DVLVelocityVectorWidget(QWidget):
    """
    Subsea DVL 2D Velocity Vector Crosshair target grid displaying drift velocity.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(120, 120)
        self.vx = 0.0
        self.vy = 0.0

    def set_velocities(self, vx, vy):
        self.vx = float(vx)
        self.vy = float(vy)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        w, h = self.width(), self.height()
        center_x, center_y = w / 2.0, h / 2.0
        radius = min(center_x, center_y) - 10
        
        painter.fillRect(self.rect(), QColor(10, 15, 25))
        
        # Draw target circles
        painter.setPen(QPen(QColor(28, 59, 101, 150), 1))
        for r_factor in [0.33, 0.66, 1.0]:
            r = radius * r_factor
            painter.drawEllipse(center_x - r, center_y - r, r * 2, r * 2)
            
        # Draw crosshairs
        painter.drawLine(center_x - radius, center_y, center_x + radius, center_y)
        painter.drawLine(center_x, center_y - radius, center_x, center_y + radius)
        
        # Draw velocity vector line (scale: 1.0 m/s = radius)
        scale = radius / 1.0
        target_x = center_x + self.vx * scale
        target_y = center_y - self.vy * scale  # Invert Y for cartesian grid
        
        # Draw glowing vector line
        vector_pen = QPen(QColor(255, 145, 0), 2)
        painter.setPen(vector_pen)
        painter.drawLine(center_x, center_y, target_x, target_y)
        
        # Draw target dot
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(255, 145, 0)))
        painter.drawEllipse(target_x - 3, target_y - 3, 6, 6)
        
        # Label velocities
        painter.setPen(QColor(142, 183, 230))
        painter.setFont(QFont("Google Sans", 7))
        painter.drawText(8, h - 18, f"V_vector: ({self.vx:+.2f}, {self.vy:+.2f}) m/s")


class PFDWidget(QWidget):
    """
    Subsea Primary Flight Display (PFD) HUD widget replicating the defense-grade cockpit overlay.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(480, 240)
        self.roll = 1.5
        self.pitch = -0.8
        self.yaw = 142.0
        self.depth = 245.0
        self.speed = 1.8
        self.course = 135.0
        self.dtw = 412.0
        
    def set_telemetry(self, roll, pitch, yaw, depth, speed, course, dtw):
        self.roll = float(roll)
        self.pitch = float(pitch)
        self.yaw = float(yaw)
        self.depth = float(depth)
        self.speed = float(speed)
        self.course = float(course)
        self.dtw = float(dtw)
        self.update()
        
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        w, h = self.width(), self.height()
        
        # Draw background canvas
        painter.fillRect(self.rect(), QColor(5, 12, 22))
        
        # =========================================================================
        # 1. FAR-LEFT DEPTH OVERVIEW TAPE (Static 0 - 500 meters)
        # =========================================================================
        left_tape_w = 45
        painter.setPen(QPen(QColor(28, 59, 101, 180), 1))
        painter.drawLine(left_tape_w, 0, left_tape_w, h)
        
        # PFD Header
        painter.setPen(QColor(0, 229, 255))
        painter.setFont(QFont("Google Sans", 7, QFont.Bold))
        painter.drawText(5, 12, "PFD")
        
        # 500 meters label at bottom
        painter.setFont(QFont("Google Sans", 6))
        painter.setPen(QColor(142, 183, 230))
        painter.drawText(5, h - 18, 38, 15, Qt.AlignCenter, "500\nmeters")
        
        # Draw vertical tape tick lines and numbers
        tape_y_start = 25
        tape_y_end = h - 30
        tape_h = tape_y_end - tape_y_start
        
        painter.setPen(QPen(QColor(142, 183, 230, 120), 1))
        for val in range(0, 501, 100):
            # Calculate Y coordinate
            val_pct = val / 500.0
            ty = tape_y_start + val_pct * tape_h
            painter.drawLine(left_tape_w - 8, int(ty), left_tape_w, int(ty))
            painter.drawText(5, int(ty - 5), 30, 10, Qt.AlignRight | Qt.AlignVCenter, str(val))
            
        # Draw green active range slider bar
        slider_x = left_tape_w - 4
        painter.setPen(QPen(QColor(0, 230, 118), 3))
        depth_pct = min(1.0, max(0.0, self.depth / 500.0))
        pointer_y = tape_y_start + depth_pct * tape_h
        painter.drawLine(slider_x, int(tape_y_start), slider_x, int(pointer_y))
        
        # Draw yellow pointer arrow pointing to the value
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(255, 214, 0)))
        poly_pointer = QPolygon([
            QPoint(slider_x - 3, int(pointer_y)),
            QPoint(slider_x - 9, int(pointer_y - 4)),
            QPoint(slider_x - 9, int(pointer_y + 4))
        ])
        painter.drawPolygon(poly_pointer)
        
        # =========================================================================
        # 2. MAIN PFD SKY/SEA ATTITUDE VIEWPORT (CX, CY based clipping)
        # =========================================================================
        pfd_x = left_tape_w + 1
        pfd_w = w - pfd_x
        pfd_rect = QRect(pfd_x, 0, pfd_w, h)
        
        painter.save()
        painter.setClipRect(pfd_rect)
        
        CX = pfd_x + pfd_w / 2.0
        CY = h / 2.0
        
        # DYNAMIC SCALE FACTOR (Adjusts HUD elements size dynamically)
        scale_f = min(pfd_w, h) / 250.0
        scale_f = max(0.9, min(2.8, scale_f))
        
        # Draw Sky & Sea background rotated for roll/pitch
        painter.save()
        painter.translate(CX, CY)
        painter.rotate(-self.roll)
        
        # Pitch offset: 1 degree = 2.5 * scale_f pixels
        pitch_offset = self.pitch * 2.5 * scale_f
        
        # Sky fill (gradient blue)
        sky_gradient = QLinearGradient(0, -h, 0, pitch_offset)
        sky_gradient.setColorAt(0.0, QColor(21, 101, 192))
        sky_gradient.setColorAt(1.0, QColor(66, 165, 245))
        painter.fillRect(QRect(-int(w * 1.5), -int(h * 1.5), int(w * 3), int(h * 1.5 + pitch_offset)), QBrush(sky_gradient))
        
        # Sea fill (gradient dark gray/blue)
        sea_gradient = QLinearGradient(0, pitch_offset, 0, h)
        sea_gradient.setColorAt(0.0, QColor(44, 62, 80))
        sea_gradient.setColorAt(1.0, QColor(33, 47, 61))
        painter.fillRect(QRect(-int(w * 1.5), int(pitch_offset), int(w * 3), int(h * 1.5 - pitch_offset)), QBrush(sea_gradient))
        
        # White Horizon line
        painter.setPen(QPen(QColor(255, 255, 255), 1.5))
        painter.drawLine(-int(w * 1.5), int(pitch_offset), int(w * 1.5), int(pitch_offset))
        
        # --- Pitch Ladder Lines ---
        font_sz_ticks = max(6, int(6.5 * scale_f))
        painter.setFont(QFont("Google Sans", font_sz_ticks, QFont.Bold))
        for p in [-30, -20, -10, 10, 20, 30]:
            p_y = pitch_offset - (p * 2.5 * scale_f)
            line_w = int(24 * scale_f)
            # Draw rung
            painter.setPen(QPen(QColor(255, 255, 255, 180), 1))
            painter.drawLine(-line_w, int(p_y), line_w, int(p_y))
            # Draw vertical tick legs
            leg_h = int(4 * scale_f) if p > 0 else -int(4 * scale_f)
            painter.drawLine(-line_w, int(p_y), -line_w, int(p_y + leg_h))
            painter.drawLine(line_w, int(p_y), line_w, int(p_y + leg_h))
            
            # Numeric labels
            painter.setPen(QColor(255, 255, 255, 220))
            painter.drawText(line_w + 4, int(p_y - 4), f"{abs(p)}")
            painter.drawText(-line_w - 15, int(p_y - 4), f"{abs(p)}")
            
        painter.restore() # Restore roll/pitch translation
        
        # =========================================================================
        # 3. HUD OVERLAYS (Fixed on screen coordinate system)
        # =========================================================================
        
        # --- Fixed Aircraft Reference Symbol (Orange Chevron) ---
        painter.setPen(QPen(QColor(255, 179, 0), 2.5))
        chev_w = int(28 * scale_f)
        chev_gap = int(9 * scale_f)
        # Left Wing
        painter.drawLine(int(CX - chev_w), int(CY + chev_gap), int(CX - chev_gap), int(CY + chev_gap))
        painter.drawLine(int(CX - chev_gap), int(CY + chev_gap), int(CX), int(CY))
        # Right Wing
        painter.drawLine(int(CX), int(CY), int(CX + chev_gap), int(CY + chev_gap))
        painter.drawLine(int(CX + chev_gap), int(CY + chev_gap), int(CX + chev_w), int(CY + chev_gap))
        
        # --- Roll Scale Arc at the Top ---
        arc_r = int(85 * scale_f)
        arc_rect = QRect(int(CX - arc_r), int(CY - arc_r), int(arc_r * 2), int(arc_r * 2))
        painter.setPen(QPen(QColor(255, 255, 255, 100), 1, Qt.SolidLine))
        painter.drawArc(arc_rect, 35 * 16, 110 * 16)
        # Ticks on roll scale
        for tick in [-60, -45, -30, -20, -10, 0, 10, 20, 30, 45, 60]:
            painter.save()
            painter.translate(CX, CY)
            painter.rotate(tick)
            tick_len = int(6 * scale_f) if tick % 30 == 0 else int(4 * scale_f)
            painter.setPen(QPen(QColor(255, 255, 255, 180), 1))
            painter.drawLine(0, -arc_r, 0, -arc_r + tick_len)
            painter.restore()
            
        # Draw roll pointer white triangle indicator
        painter.save()
        painter.translate(CX, CY)
        painter.rotate(-self.roll)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(255, 255, 255)))
        poly_roll = QPolygon([
            QPoint(0, -arc_r + 2),
            QPoint(-4, -arc_r + 8),
            QPoint(4, -arc_r + 8)
        ])
        painter.drawPolygon(poly_roll)
        painter.restore()
        
        # --- Left Vertical Altitude/Depth Tape (`units`) ---
        tape_x_left = CX - int(75 * scale_f)
        tape_y_range = int(90 * scale_f)
        painter.setPen(QPen(QColor(255, 255, 255, 150), 1))
        # Main vertical line
        painter.drawLine(int(tape_x_left), int(CY - tape_y_range), int(tape_x_left), int(CY + tape_y_range))
        
        font_sz_labels = max(7, int(7.5 * scale_f))
        painter.setFont(QFont("Consolas", font_sz_labels))
        
        unit_scale_pixels = 0.5 * scale_f
        range_val_span = int(tape_y_range / unit_scale_pixels)
        start_val = int((self.depth - range_val_span) // 50 * 50)
        end_val = int((self.depth + range_val_span) // 50 * 50)
        
        for val in range(start_val, end_val + 1, 50):
            if val < 0:
                continue
            y_pos = CY - (val - self.depth) * unit_scale_pixels
            if CY - tape_y_range <= y_pos <= CY + tape_y_range:
                painter.setPen(QPen(QColor(255, 255, 255, 120), 1))
                painter.drawLine(int(tape_x_left - 6), int(y_pos), int(tape_x_left), int(y_pos))
                painter.drawText(int(tape_x_left - 35), int(y_pos - 5), 26, 10, Qt.AlignRight | Qt.AlignVCenter, str(val))
                
        # Draw green active vertical band indicator along the ruler
        painter.setPen(QPen(QColor(0, 230, 118), 3))
        painter.drawLine(int(tape_x_left + 2), int(CY - tape_y_range / 2), int(tape_x_left + 2), int(CY + tape_y_range / 2))
        
        # units labels at top/bottom of tape
        painter.setPen(QColor(180, 220, 255))
        painter.setFont(QFont("Google Sans", font_sz_ticks))
        painter.drawText(int(tape_x_left - 25), int(CY - tape_y_range - 10), "units")
        painter.drawText(int(tape_x_left - 25), int(CY + tape_y_range + 2), "units")
        
        # Current Value black box pointer at CY
        box_w = int(45 * scale_f)
        box_h = int(16 * scale_f)
        box_rect_alt = QRect(int(tape_x_left - box_w - 2), int(CY - box_h/2), box_w, box_h)
        painter.setPen(QPen(QColor(255, 255, 255), 1.5))
        painter.setBrush(QBrush(QColor(0, 0, 0)))
        painter.drawRect(box_rect_alt)
        painter.setFont(QFont("Consolas", max(8, int(9 * scale_f)), QFont.Bold))
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(box_rect_alt, Qt.AlignCenter, f"{int(self.depth)}m")
        
        # --- Right Vertical Speed Tape (`knots`) ---
        tape_x_right = CX + int(75 * scale_f)
        painter.setPen(QPen(QColor(255, 255, 255, 150), 1))
        # Main vertical line
        painter.drawLine(int(tape_x_right), int(CY - tape_y_range), int(tape_x_right), int(CY + tape_y_range))
        
        unit_scale_speed = 80.0 * scale_f
        range_spd_span = tape_y_range / unit_scale_speed
        
        start_spd = max(0.0, round((self.speed - range_spd_span) / 0.2) * 0.2)
        end_spd = round((self.speed + range_spd_span) / 0.2) * 0.2
        for spd in [x * 0.1 for x in range(int(start_spd * 10), int(end_spd * 10) + 1, 2)]:
            y_pos = CY - (spd - self.speed) * unit_scale_speed
            if CY - tape_y_range <= y_pos <= CY + tape_y_range:
                painter.setPen(QPen(QColor(255, 255, 255, 120), 1))
                painter.drawLine(int(tape_x_right), int(y_pos), int(tape_x_right + 6), int(y_pos))
                painter.setFont(QFont("Consolas", font_sz_labels))
                painter.drawText(int(tape_x_right + 10), int(y_pos - 5), 25, 10, Qt.AlignLeft | Qt.AlignVCenter, f"{spd:.1f}")
                
        # Draw speed arrow pointer
        painter.setPen(QPen(QColor(0, 230, 118), 2))
        painter.setBrush(QBrush(QColor(0, 230, 118)))
        arrow_poly = QPolygon([
            QPoint(int(tape_x_right - 1), int(CY)),
            QPoint(int(tape_x_right - 6), int(CY - 4)),
            QPoint(int(tape_x_right - 6), int(CY + 4))
        ])
        painter.drawPolygon(arrow_poly)
        
        # knots labels at top/bottom of tape
        painter.setPen(QColor(180, 220, 255))
        painter.setFont(QFont("Google Sans", font_sz_ticks))
        painter.drawText(int(tape_x_right + 5), int(CY - tape_y_range - 10), "knots")
        painter.drawText(int(tape_x_right + 5), int(CY + tape_y_range + 2), "knots")
        
        # Current Speed Value box pointer
        box_w_spd = int(35 * scale_f)
        box_rect_spd = QRect(int(tape_x_right + 7), int(CY - box_h/2), box_w_spd, box_h)
        painter.setPen(QPen(QColor(255, 255, 255), 1.5))
        painter.setBrush(QBrush(QColor(0, 0, 0)))
        painter.drawRect(box_rect_spd)
        painter.setFont(QFont("Consolas", max(8, int(9 * scale_f)), QFont.Bold))
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(box_rect_spd, Qt.AlignCenter, f"{self.speed:.1f}")
        
        # --- Bottom Center Horizontal Situation Compass Dial (HSI Rose) ---
        X_comp = CX
        Y_comp = CY + int(90 * scale_f)
        R_comp = int(45 * scale_f)
        
        # Compass Card Background dial (semi-transparent black dome)
        painter.setPen(QPen(QColor(255, 255, 255, 40), 1))
        painter.setBrush(QBrush(QColor(10, 20, 35, 200)))
        painter.drawEllipse(int(X_comp - R_comp), int(Y_comp - R_comp), int(R_comp * 2), int(R_comp * 2))
        
        # Rotating Compass rose card
        painter.save()
        painter.translate(X_comp, Y_comp)
        painter.rotate(-self.yaw)
        
        # Draw dial ticks & degree marks
        painter.setFont(QFont("Google Sans", max(5, int(5.5 * scale_f)), QFont.Bold))
        for d in range(0, 360, 30):
            painter.save()
            painter.rotate(d)
            painter.setPen(QPen(QColor(255, 255, 255, 150), 1))
            painter.drawLine(0, -R_comp, 0, -R_comp + 4)
            label = ""
            if d == 0: label = "N"
            elif d == 90: label = "E"
            elif d == 180: label = "S"
            elif d == 270: label = "W"
            else: label = str(d // 10)
            
            painter.drawText(-8, -int(R_comp - 6), 16, 8, Qt.AlignCenter, label)
            painter.restore()
            
        # Draw yellow pointer line for Course Bearing relative to compass
        painter.setPen(QPen(QColor(255, 214, 0), 1.5))
        painter.rotate(self.course)
        painter.drawLine(0, 0, 0, -int(R_comp - 3))
        # Draw Arrowhead
        arrow_poly_crs = QPolygon([
            QPoint(0, -int(R_comp - 3)),
            QPoint(-3, -int(R_comp - 8)),
            QPoint(3, -int(R_comp - 8))
        ])
        painter.setBrush(QBrush(QColor(255, 214, 0)))
        painter.drawPolygon(arrow_poly_crs)
        
        painter.restore() # Restore compass rotation
        
        # Draw center crosshair over compass card
        painter.setPen(QPen(QColor(255, 255, 255, 80), 1))
        painter.drawLine(int(X_comp - 6), int(Y_comp), int(X_comp + 6), int(Y_comp))
        painter.drawLine(int(X_comp), int(Y_comp - 6), int(X_comp), int(Y_comp + 6))
        
        # Heading Pointer Box at the top of the compass rose
        hdg_box_w = int(40 * scale_f)
        hdg_box_h = int(12 * scale_f)
        hdg_box_rect = QRect(int(X_comp - hdg_box_w/2), int(Y_comp - R_comp - 9), hdg_box_w, hdg_box_h)
        painter.setPen(QPen(QColor(255, 255, 255, 120), 1))
        painter.setBrush(QBrush(QColor(0, 0, 0)))
        painter.drawRect(hdg_box_rect)
        painter.setFont(QFont("Consolas", max(7, int(8 * scale_f)), QFont.Bold))
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(hdg_box_rect, Qt.AlignCenter, f"{int(self.yaw)}°")
        
        # --- Top Text overlay bar (ROLL: +1.5° PITCH: -0.8° YAW: 142.0°) ---
        top_bar_rect = QRect(int(pfd_x + 10), 4, int(pfd_w - 20), 14)
        painter.setFont(QFont("Google Sans", 7, QFont.Bold))
        painter.setPen(QColor(180, 220, 255))
        telem_str = f"ROLL: {self.roll:+.1f}°   PITCH: {self.pitch:+.1f}°   YAW: {self.yaw:.1f}°"
        painter.drawText(top_bar_rect, Qt.AlignCenter, telem_str)
        
        # --- Bottom overlay text values ---
        font_sz_bottom = max(7, int(8.5 * scale_f))
        painter.setFont(QFont("Google Sans", font_sz_bottom, QFont.Bold))
        painter.setPen(QColor(255, 255, 255))
        # Left Bottom labels: Heading and Course
        painter.drawText(int(pfd_x + 15), int(h - 26), f"HDG: {int(self.yaw)}°")
        painter.setPen(QColor(255, 214, 0))
        painter.drawText(int(pfd_x + 15), int(h - 14), f"CRS: {int(self.course)}°")
        
        # Right Bottom labels: Distance to Waypoint (DTW)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(int(w - 85), int(h - 14), f"DTW: {int(self.dtw)}m")
        
        painter.restore() # Restore main clipping boundary

class BatteryGauge(QWidget):
    """
    A custom premium battery display widget showing charge percentage, state of health, and fill animations.
    """
    def __init__(self, parent=None, theme="light"):
        super().__init__(parent)
        self.theme = theme
        self.soc = 0
        self.setMinimumSize(45, 80)

    def set_soc(self, soc):
        self.soc = max(0, min(100, int(soc)))
        self.update()

    def set_theme(self, theme):
        self.theme = theme
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        
        # Dimensions (Dynamically scaled to fit widget height)
        bat_w = max(24, min(36, int(w * 0.55)))
        bat_h = max(40, min(75, int(h - 32)))
        x = cx - bat_w / 2
        y = cy - bat_h / 2 - 8
        
        # Draw battery tip (cap)
        cap_w = max(10, int(bat_w * 0.4))
        cap_h = 4
        if self.theme == "light":
            cap_pen = QPen(QColor(13, 110, 253, 140), 1.5)
            cap_brush = QBrush(QColor(13, 110, 253, 40))
            body_pen = QPen(QColor(13, 110, 253, 180), 1.5)
            body_brush = QBrush(QColor(248, 250, 252))
            text_pen = QPen(QColor(10, 37, 64))
        else:
            cap_pen = QPen(QColor(0, 229, 255, 100), 1.5)
            cap_brush = QBrush(QColor(0, 229, 255, 40))
            body_pen = QPen(QColor(0, 229, 255, 150), 1.5)
            body_brush = QBrush(QColor(10, 22, 37))
            text_pen = QPen(QColor(255, 255, 255))
            
        painter.setPen(cap_pen)
        painter.setBrush(cap_brush)
        painter.drawRect(cx - cap_w / 2, y - cap_h, cap_w, cap_h)
        
        # Draw battery body contour
        painter.setPen(body_pen)
        painter.setBrush(body_brush)
        painter.drawRoundedRect(x, y, bat_w, bat_h, 4, 4)
        
        # Fill percentage
        fill_h = int((bat_h - 6) * (self.soc / 100.0))
        fill_y = y + bat_h - 3 - fill_h
        
        # Determine battery color based on SOC
        if self.soc > 50:
            color = QColor(16, 185, 129) # Green
        elif self.soc > 10:
            color = QColor(245, 158, 11) # Orange (Warning)
        else:
            color = QColor(239, 68, 68) # Hot Red (Failsafe <= 10%)
            
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawRoundedRect(x + 3, fill_y, bat_w - 6, fill_h, 2, 2)
        
        # Percentage text overlay
        painter.setPen(text_pen)
        painter.setFont(QFont("Google Sans", 8.5, QFont.Bold))
        painter.drawText(0, int(y + bat_h + 4), w, 16, Qt.AlignCenter, f"{self.soc}%")


class ActuatorsGauge(QWidget):
    """
    A custom gauge showing output power levels of the 3 subsea thrusters side-by-side.
    """
    def __init__(self, parent=None, theme="light"):
        super().__init__(parent)
        self.theme = theme
        self.p1 = 1500
        self.p2 = 1500
        self.p3 = 1500
        self.setMinimumSize(90, 80)

    def set_values(self, p1, p2, p3):
        self.p1 = max(1000, min(2000, int(p1)))
        self.p2 = max(1000, min(2000, int(p2)))
        self.p3 = max(1000, min(2000, int(p3)))
        self.update()

    def set_theme(self, theme):
        self.theme = theme
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        w, h = self.width(), self.height()
        bar_w = max(12, min(20, int(w * 0.14)))
        spacing = max(10, min(24, int((w - (3 * bar_w)) / 4)))
        margin = (w - (3 * bar_w + 2 * spacing)) / 2
        
        pwms = [self.p1, self.p2, self.p3]
        labels = ["T1", "T2", "T3"]
        
        col_y = 6
        col_h = max(28, h - 38)
        
        is_light = (self.theme == "light")
        
        for i in range(3):
            # Calculate column x coordinates
            col_x = margin + i * (bar_w + spacing)
            
            # Draw background channel
            if is_light:
                painter.setPen(QPen(QColor(203, 213, 225), 1))
                painter.setBrush(QBrush(QColor(241, 245, 249)))
            else:
                painter.setPen(QPen(QColor(60, 60, 60), 1))
                painter.setBrush(QBrush(QColor(25, 25, 25)))
            painter.drawRoundedRect(col_x, col_y, bar_w, col_h, 3, 3)
            
            # Center neutral line
            mid_y = col_y + col_h / 2
            dash_color = QColor(148, 163, 184, 150) if is_light else QColor(100, 100, 100, 150)
            painter.setPen(QPen(dash_color, 1, Qt.DashLine))
            painter.drawLine(col_x - 3, mid_y, col_x + bar_w + 3, mid_y)
            
            # Draw actual thrust height from center (1500 neutral)
            val = pwms[i]
            offset = (val - 1500) / 500.0 # Range: -1.0 to +1.0
            
            if offset != 0:
                fill_h = int(abs(offset) * (col_h / 2.0))
                if offset > 0:
                    fill_y = mid_y - fill_h
                    color = QColor(13, 110, 253) if is_light else QColor(0, 229, 255)
                else:
                    fill_y = mid_y
                    color = QColor(239, 68, 68) if is_light else QColor(255, 23, 73)
                
                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(color))
                painter.drawRect(col_x + 1, fill_y, bar_w - 2, fill_h)
                
            # Draw cursor marker indicator (slider handle style)
            marker_y = mid_y - (offset * (col_h / 2.0))
            painter.setPen(QPen(QColor(255, 255, 255), 1.5))
            if offset > 0:
                painter.setBrush(QBrush(QColor(13, 110, 253) if is_light else QColor(0, 229, 255)))
            elif offset < 0:
                painter.setBrush(QBrush(QColor(239, 68, 68) if is_light else QColor(255, 23, 73)))
            else:
                painter.setBrush(QBrush(QColor(13, 110, 253) if is_light else QColor(150, 150, 150)))
            painter.drawRoundedRect(col_x - 2, int(marker_y - 2), bar_w + 4, 5, 2, 2)
                
            # Text label
            lbl_color = QColor(10, 37, 64) if is_light else QColor(180, 180, 180)
            painter.setPen(QPen(lbl_color))
            painter.setFont(QFont("Google Sans", 7.5, QFont.Bold))
            painter.drawText(col_x - 10, int(col_y + col_h + 3), bar_w + 20, 13, Qt.AlignCenter, labels[i])
            
            # Value label
            val_color = QColor(13, 110, 253) if is_light else QColor(0, 229, 255)
            painter.setPen(QPen(val_color))
            painter.setFont(QFont("Consolas", 7.5, QFont.Bold))
            painter.drawText(col_x - 15, int(col_y + col_h + 15), bar_w + 30, 13, Qt.AlignCenter, str(pwms[i]))







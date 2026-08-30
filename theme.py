import os
import tempfile

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPalette, QPainter, QPen, QPixmap

ACCENT = "#007aff"
SELECTION = "#0a6cff"

WINDOW = "#1e1e1e"
BASE = "#161616"
ALTERNATE = "#232323"
SURFACE = "#2b2b2b"
SURFACE_HOVER = "#353535"
SURFACE_PRESSED = "#3d3d3d"
BORDER = "#3a3a3a"
BORDER_SOFT = "#2f2f2f"
TEXT = "#e8e8e8"
TEXT_MUTED = "#a0a0a0"
DISABLED = "#6e6e6e"

_ARROW_UP_PATH = os.path.join(tempfile.gettempdir(), "clamshell_arrow_up.png")
_ARROW_DOWN_PATH = os.path.join(tempfile.gettempdir(), "clamshell_arrow_down.png")


def _dark_palette():
    p = QPalette()
    window = QColor(WINDOW)
    base = QColor(BASE)
    text = QColor(TEXT)
    disabled = QColor(DISABLED)

    p.setColor(QPalette.ColorRole.Window, window)
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Base, base)
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(ALTERNATE))
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_MUTED))
    p.setColor(QPalette.ColorRole.Button, QColor(SURFACE))
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.Link, QColor(ACCENT))

    for role in (
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.WindowText,
    ):
        p.setColor(QPalette.ColorGroup.Disabled, role, disabled)

    return p


THEME_QSS = f"""
* {{
    font-family: "SF Pro Text", "SF Pro Display", "Helvetica Neue",
                 "Segoe UI", sans-serif;
    font-size: 13px;
    color: {TEXT};
    selection-background-color: {ACCENT};
    selection-color: #ffffff;
}}

QWidget {{
    background-color: {WINDOW};
}}

QMainWindow,
QTabWidget::pane {{
    background-color: {WINDOW};
}}

QTabWidget::pane {{
    border: none;
    margin-top: -1px;
}}

QTabBar {{
    background-color: {WINDOW};
    alignment: center;
}}

QTabBar::tab {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-left: none;
    color: {TEXT_MUTED};
    padding: 5px 22px;
    margin-top: 8px;
    margin-bottom: 6px;
}}

QTabBar::tab:first {{
    border-left: 1px solid {BORDER};
    border-top-left-radius: 7px;
    border-bottom-left-radius: 7px;
}}

QTabBar::tab:last {{
    border-top-right-radius: 7px;
    border-bottom-right-radius: 7px;
}}

QTabBar::tab:selected {{
    background-color: {SURFACE_HOVER};
    color: {TEXT};
}}

QTabBar::tab:hover:!selected {{
    background-color: #333333;
}}

QPushButton {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 16px;
    color: {TEXT};
}}

QPushButton:hover {{
    background-color: {SURFACE_HOVER};
}}

QPushButton:pressed {{
    background-color: {SURFACE_PRESSED};
}}

QPushButton:focus {{
    border: 2px solid {ACCENT};
    padding: 4px 15px;
}}

QPushButton:disabled {{
    color: {DISABLED};
    border-color: {BORDER_SOFT};
    background-color: {WINDOW};
}}

QLineEdit,
QSpinBox {{
    background-color: {BASE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px 8px;
    selection-background-color: {ACCENT};
}}

QLineEdit:focus,
QSpinBox:focus {{
    border: 1px solid {ACCENT};
}}

QLineEdit:disabled,
QSpinBox:disabled {{
    color: {DISABLED};
    background-color: {WINDOW};
}}

QSpinBox::up-button,
QSpinBox::down-button {{
    width: 18px;
    border: none;
    background-color: transparent;
}}

QSpinBox::up-button:hover,
QSpinBox::down-button:hover {{
    background-color: {SURFACE_HOVER};
    border-radius: 4px;
}}

QSpinBox::up-arrow {{
    image: url({_ARROW_UP_PATH});
}}

QSpinBox::down-arrow {{
    image: url({_ARROW_DOWN_PATH});
}}

QListWidget {{
    background-color: {BASE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 2px;
    outline: none;
}}

QListWidget::item {{
    padding: 5px 8px;
    border-radius: 4px;
}}

QListWidget::item:hover {{
    background-color: {SURFACE};
}}

QListWidget::item:selected {{
    background-color: {SELECTION};
    color: #ffffff;
}}

QTextEdit {{
    background-color: {BASE};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px;
    selection-background-color: {ACCENT};
}}

QProgressBar {{
    background-color: {SURFACE};
    border: none;
    border-radius: 4px;
    max-height: 8px;
    text-align: center;
}}

QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 4px;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}

QScrollBar::handle:vertical {{
    background-color: rgba(255, 255, 255, 60);
    border-radius: 4px;
    min-height: 30px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: rgba(255, 255, 255, 90);
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}

QScrollBar::handle:horizontal {{
    background-color: rgba(255, 255, 255, 60);
    border-radius: 4px;
    min-width: 30px;
}}

QScrollBar::handle:horizontal:hover {{
    background-color: rgba(255, 255, 255, 90);
}}

QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}

QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}
"""


def _make_arrow(path, direction):
    width = 18
    height = 16
    pm = QPixmap(width, height)
    pm.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(
        QColor(TEXT_MUTED),
        2,
        Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.RoundCap,
        Qt.PenJoinStyle.RoundJoin,
    )
    painter.setPen(pen)
    if direction == "up":
        painter.drawPolyline(
            [QPointF(5, 11), QPointF(9, 7), QPointF(13, 11)]
        )
    else:
        painter.drawPolyline(
            [QPointF(5, 5), QPointF(9, 9), QPointF(13, 5)]
        )
    painter.end()

    pm.save(path, "PNG")


def apply_theme(app):
    _make_arrow(_ARROW_UP_PATH, "up")
    _make_arrow(_ARROW_DOWN_PATH, "down")
    app.setStyle("Fusion")
    app.setPalette(_dark_palette())
    app.setStyleSheet(THEME_QSS)

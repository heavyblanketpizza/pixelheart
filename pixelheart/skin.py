"""Pixelheart's look: warm-white paper pieces applied through one stylesheet."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile

from PySide6.QtCore import QStandardPaths
from PySide6.QtGui import QFontDatabase

from pixelheart_core.game_ui import REQUIRED_ROLES, ui_pieces
from . import game_import

FONT_FILE = Path(__file__).parent / "resources" / "fonts" / "PixelifySans.ttf"
EASY_READ_KEY = "view/easyRead"
READABLE_FAMILIES = '"Helvetica Neue", "Segoe UI", Arial'
INK = "#26221d"
# Warm white and one ink, so the characters people make carry the color.
COLORS = {
    "text": INK, "muted": "#6e675d", "link": "#4a443b", "disabled": "#aaa397", "error": "#b3261e",
    "page": "#f5f2ec", "paper": "#fdfbf7", "sidebar": "#edeae3",
    "highlight": "#efebe4", "hover": "#f3f0ea", "pressed": "#e6e1d8", "selection": "#e2ddd3", "selected": "#ebe7df",
    "outline": INK, "rule": "#d9d3c8", "rule_strong": "#aca598",
    "nav_icon": "#5a544a", "nav_icon_detail": "#9c9588", "nav_hover": "rgba(38, 34, 29, 16)",
    "primary_text": "#fdfbf7", "danger": "#9e3a2b", "danger_soft": "#f6e8e3", "danger_rule": "#c98d7d",
}
_STATE = {"pieces": {}, "family": None}


def ui_cache_dir():
    override = os.environ.get("PIXELHEART_CACHE_DIR")
    if override:
        return Path(override) / "game-ui"
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    return Path(base or Path.home() / ".pixelheart") / "game-ui"


def pixel_family():
    if _STATE["family"] is None:
        font_id = QFontDatabase.addApplicationFont(str(FONT_FILE))
        families = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
        _STATE["family"] = families[0] if families else "Courier New"
    return _STATE["family"]


def easy_read_enabled():
    return str(game_import.game_import_settings().value(EASY_READ_KEY, "false")).lower() == "true"


def set_easy_read(enabled):
    settings = game_import.game_import_settings()
    settings.setValue(EASY_READ_KEY, "true" if enabled else "false")
    settings.sync()


def refresh_pieces(content_root):
    """Rebuild the piece map, borrowing hearts and lettering from the game when connected.

    An unwritable cache (full disk, locked home folder) falls back to a temporary
    folder, and failing that to a plain look, so the app always starts.
    """
    pieces = {}
    for folder in (ui_cache_dir(), Path(tempfile.gettempdir()) / "pixelheart-game-ui"):
        try:
            pieces = ui_pieces(content_root, folder)
            break
        except OSError:
            continue
    _STATE["pieces"] = pieces
    return pieces


def current_pieces():
    return _STATE["pieces"]


def _image(pieces, role):
    return pieces[role].path.as_posix()


def _border(pieces, role, base=None):
    margin = pieces[base or role].margin
    return f'border-image: url("{_image(pieces, role)}") {margin} {margin} {margin} {margin} stretch stretch;'


def _slice(pieces, role):
    return f"{_border(pieces, role)} border-width: {pieces[role].margin}px;"


def _widget_rules(c):
    """Hand-built widgets, kept here so they follow the look like everything else."""
    return f"""
QPushButton#romanceMilestone {{ border-image: none; background: {c['paper']}; border: 2px solid {c['rule']}; padding: 0; }}
QPushButton#romanceMilestone:hover {{ background: {c['hover']}; border-color: {c['rule_strong']}; }}
QPushButton#romanceMilestone:checked {{ background: {c['selected']}; border-color: {c['outline']}; }}
QPushButton#romanceMilestone:pressed {{ background: {c['pressed']}; }}
QPushButton#romanceMilestone:focus {{ border-color: {c['rule_strong']}; }}
QPushButton#romanceMilestone:checked:focus {{ border-color: {c['outline']}; }}
QPushButton#romanceMilestone:disabled {{ background: {c['highlight']}; border-color: {c['rule']}; }}
QFrame#birthdayGrid {{ background: {c['rule']}; border: 2px solid {c['rule_strong']}; }}
QLabel#calendarWeekday {{ background: {c['highlight']}; color: {c['muted']}; padding: 8px 0; font-size: 11px; font-weight: bold; }}
QLabel#calendarLegend, QLabel#calendarNote {{ color: {c['muted']}; font-size: 11px; }}
QLabel#birthdaySelection {{ color: {c['text']}; font-size: 21px; }}
QLabel#birthdaySelection[conflict="true"], QLabel#birthdayStatus[conflict="true"] {{ color: {c['danger']}; }}
QLabel#birthdayStatus {{ color: {c['muted']}; font-size: 12px; }}
QFrame#birthdayReceipt {{ background: {c['selected']}; border: 1px solid {c['rule']}; }}
QFrame#birthdayReceipt[conflict="true"] {{ background: {c['danger_soft']}; border-color: {c['danger_rule']}; }}
QLabel#calendarFestivalDate {{ color: {c['muted']}; font-size: 12px; font-weight: bold; }}
QLabel#calendarFestivalName {{ color: {c['muted']}; font-size: 12px; }}
QLabel#calendarDetailsHeading {{ color: {c['muted']}; font-size: 10px; font-weight: bold; letter-spacing: 1px; }}
QListWidget#catalogueGallery {{ background: {c['highlight']}; border: 1px solid {c['rule_strong']}; padding: 4px; }}
QListWidget#catalogueGallery::item {{ border: 1px solid {c['rule']}; background: {c['paper']}; padding: 4px; }}
QListWidget#catalogueGallery::item:selected {{ background: {c['selected']}; border: 2px solid {c['outline']}; color: {c['text']}; }}
QListWidget#catalogueGallery::item:hover {{ background: {c['hover']}; }}
QLabel#painterHeading {{ color: {c['muted']}; font-size: 11px; font-weight: bold; letter-spacing: 1px; }}
QToolButton#painterTool {{ background: {c['paper']}; border: 2px solid {c['rule']}; padding: 4px; }}
QToolButton#painterTool:hover {{ background: {c['hover']}; border-color: {c['rule_strong']}; }}
QToolButton#painterTool:checked {{ background: {c['selected']}; border-color: {c['outline']}; }}
QToolButton#painterToggle {{ background: {c['paper']}; border: 2px solid {c['rule']}; padding: 2px 8px; color: {c['text']}; }}
QToolButton#painterToggle:hover {{ border-color: {c['rule_strong']}; }}
QToolButton#painterToggle:checked {{ background: {c['selected']}; border-color: {c['outline']}; }}
QScrollArea#painterCanvasArea {{ background: {c['sidebar']}; border: 2px solid {c['rule']}; }}
QScrollArea#painterCanvasArea > QWidget > QWidget {{ background: {c['sidebar']}; }}
QScrollArea#painterSide {{ border: none; }}
QListWidget#painterLayers::item {{ padding: 4px 6px; }}
"""


def _plain_stylesheet(c, pixel, body, size):
    """Colors and fonts only, for the rare case no interface pieces could be written."""
    return f"""
QWidget {{ font-family: {body}; font-size: {size}px; color: {c['text']}; }}
QMainWindow, QDialog, QWidget#workspace, QWidget#welcome, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {c['page']}; }}
QWidget#sidebar, QStatusBar {{ background: {c['sidebar']}; color: {c['text']}; }}
QWidget#sidebar QLabel, QStatusBar QLabel, QListWidget#navigation::item {{ color: {c['text']}; }}
QListWidget#navigation::item:selected {{ background: {c['paper']}; color: {c['text']}; }}
QFrame#card, QFrame#profile, QFrame#panel {{ background: {c['paper']}; border: 2px solid {c['outline']}; }}
QPushButton {{ font-family: "{pixel}"; background: {c['paper']}; border: 2px solid {c['outline']}; padding: 4px 10px; }}
QPushButton#primary {{ background: {c['outline']}; color: {c['primary_text']}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{ background: {c['paper']}; border: 2px solid {c['outline']}; padding: 3px; }}
""" + _widget_rules(c)


def build_stylesheet(pieces, *, easy_read=False):
    c, pixel = COLORS, pixel_family()
    body = READABLE_FAMILIES if easy_read else f'"{pixel}"'
    size = 14 if easy_read else 16
    if any(role not in pieces for role in REQUIRED_ROLES):
        return _plain_stylesheet(c, pixel, body, size)
    return f"""
QWidget {{ font-family: {body}; font-size: {size}px; color: {c['text']}; }}
QMainWindow, QDialog, QWidget#workspace, QWidget#welcome, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {c['page']}; }}
QWidget#sidebar {{ background-color: {c['sidebar']}; background-image: url("{_image(pieces, 'sidebar')}"); border-right: 2px solid {c['outline']}; }}
QLabel {{ background: transparent; }}
QLabel:disabled {{ color: {c['disabled']}; }}
QLabel#brand, QLabel#title, QLabel#sectionTitle, QLabel#profileName, QLabel#eyebrow, QLabel#breadcrumb {{ font-family: "{pixel}"; }}
QLabel#brand {{ font-size: 30px; color: {c['text']}; }}
QLabel#title {{ font-size: 32px; color: {c['text']}; }}
QLabel#sectionTitle {{ font-size: 20px; color: {c['text']}; }}
QLabel#profileName {{ font-size: 26px; color: {c['text']}; }}
QLabel#eyebrow, QLabel#breadcrumb {{ font-size: 13px; color: {c['muted']}; letter-spacing: 1px; }}
QLabel#muted, QLabel#hint, QLabel#saveState {{ color: {c['muted']}; font-size: {size - 2}px; }}
QLabel#sceneLocation {{ font-family: "{pixel}"; font-size: 16px; }}
QWidget#sidebar QLabel#eyebrow, QWidget#sidebar QLabel#hint, QWidget#sidebar QLabel#muted {{ color: {c['muted']}; }}
QLabel#badge {{ background: {c['highlight']}; border: 2px solid {c['rule']}; padding: 3px 8px; }}
QLabel#notice {{ {_slice(pieces, 'textbox')} padding: 4px 6px; }}
QFrame#card, QFrame#profile, QFrame#panel {{ {_slice(pieces, 'panel')} }}
QFrame#card[dropTarget="true"] {{ background: {c['highlight']}; }}
QFrame#workspaceSeparator, QFrame#sidebarSeparator {{ background: {c['rule']}; border: none; min-height: 2px; max-height: 2px; }}
QPushButton {{ font-family: "{pixel}"; font-size: 16px; color: {c['text']}; {_slice(pieces, 'button')} padding: 1px 8px; min-height: 22px; }}
QPushButton:hover {{ {_border(pieces, 'button_hover', 'button')} }}
QPushButton:pressed {{ {_border(pieces, 'button_pressed', 'button')} }}
QPushButton:disabled {{ color: {c['disabled']}; {_border(pieces, 'button_disabled', 'button')} }}
QPushButton#primary {{ color: {c['primary_text']}; {_slice(pieces, 'button_primary')} }}
QPushButton#danger {{ color: {c['danger']}; {_slice(pieces, 'button_danger')} }}
QPushButton#quiet, QPushButton#sidebarOpen {{ border-image: none; border: none; background: transparent; color: {c['link']}; padding: 3px 2px; text-align: left; }}
QPushButton#quiet:hover, QPushButton#sidebarOpen:hover {{ color: {c['text']}; text-decoration: underline; }}
QPushButton#quiet:disabled {{ color: {c['disabled']}; }}
QWidget#sidebar QPushButton#sidebarOpen {{ color: {c['text']}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{ {_slice(pieces, 'textbox')} background: transparent; padding: 2px 4px; selection-background-color: {c['selection']}; selection-color: {c['text']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {{ {_border(pieces, 'textbox_focus', 'textbox')} }}
QLineEdit:read-only, QPlainTextEdit:read-only {{ color: {c['muted']}; }}
QLineEdit:disabled, QPlainTextEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {c['disabled']}; }}
QLineEdit[error="true"], QPlainTextEdit[error="true"], QSpinBox[error="true"], QComboBox[error="true"] {{ color: {c['error']}; }}
QLineEdit#storyEventTitle {{ font-family: "{pixel}"; font-size: 22px; border-image: none; border: 2px solid transparent; background: transparent; }}
QLineEdit#storyEventTitle:focus {{ {_slice(pieces, 'textbox')} }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: right center; width: 20px; height: 22px; border: none; border-image: url("{_image(pieces, 'dropdown_arrow')}") 0 0 0 0 stretch stretch; }}
QComboBox::down-arrow {{ image: none; }}
QComboBox QAbstractItemView {{ background: {c['paper']}; color: {c['text']}; border: 2px solid {c['outline']}; selection-background-color: {c['selection']}; selection-color: {c['text']}; outline: none; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; border: none; background: transparent; }}
QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 18px; height: 18px; image: url("{_image(pieces, 'checkbox_off')}"); }}
QCheckBox::indicator:checked {{ image: url("{_image(pieces, 'checkbox_on')}"); }}
QListWidget, QTableWidget, QTreeWidget {{ background: {c['paper']}; border: 2px solid {c['rule']}; color: {c['text']}; outline: none; }}
QListWidget::item {{ padding: 8px 6px; border: 2px solid transparent; }}
QListWidget::item:hover {{ background: {c['highlight']}; }}
QListWidget::item:selected {{ background: {c['highlight']}; color: {c['text']}; border: 2px solid {c['outline']}; }}
QListWidget#navigation {{ background: transparent; border: none; font-family: "{pixel}"; font-size: 16px; }}
QListWidget#navigation::item {{ color: {c['text']}; padding: 6px 8px; margin: 1px 0; border: 2px solid transparent; }}
QListWidget#navigation::item:hover {{ background: {c['nav_hover']}; }}
QListWidget#navigation::item:selected {{ color: {c['text']}; background: transparent; {_slice(pieces, 'tab')} }}
QListWidget#giftTiles {{ background: {c['paper']}; font-size: 12px; }}
QListWidget#giftTiles[dropTarget="true"] {{ background: {c['highlight']}; border-color: {c['outline']}; }}
QTableWidget {{ gridline-color: {c['rule']}; alternate-background-color: {c['highlight']}; selection-background-color: {c['selection']}; selection-color: {c['text']}; }}
QHeaderView::section {{ background: {c['highlight']}; color: {c['text']}; font-family: "{pixel}"; padding: 6px; border: none; border-right: 2px solid {c['outline']}; }}
QTableCornerButton::section {{ background: {c['highlight']}; border: none; }}
QTabWidget::pane {{ border: none; border-top: 2px solid {c['outline']}; top: -2px; background: transparent; }}
QTabBar::tab {{ font-family: "{pixel}"; font-size: 16px; color: {c['muted']}; margin-right: 4px; padding: 0 8px; {_slice(pieces, 'tab_idle')} }}
QTabBar::tab:selected {{ color: {c['text']}; {_border(pieces, 'tab')} }}
QTabBar::tab:hover:!selected {{ color: {c['text']}; }}
QTabWidget#storyEventSteps > QTabBar::tab, QTabWidget#sceneInspector QTabBar::tab {{ font-size: 14px; padding: 0 6px; }}
QScrollArea {{ border: none; }}
QScrollBar:vertical {{ width: 14px; background: transparent; margin: 2px; }}
QScrollBar:horizontal {{ height: 14px; background: transparent; margin: 2px; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ {_slice(pieces, 'scroll_thumb')} min-height: 28px; min-width: 28px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ {_slice(pieces, 'scroll_track')} }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QStatusBar {{ background: {c['sidebar']}; color: {c['text']}; border-top: 2px solid {c['rule']}; }}
QStatusBar QLabel, QStatusBar QLabel#hint {{ color: {c['text']}; }}
QMenuBar {{ background: {c['page']}; font-family: "{pixel}"; }}
QMenuBar::item:selected {{ background: {c['highlight']}; }}
QMenu {{ background: {c['paper']}; border: 2px solid {c['outline']}; padding: 4px; font-family: "{pixel}"; }}
QMenu::item {{ padding: 6px 22px; }}
QMenu::item:selected {{ background: {c['selection']}; color: {c['text']}; }}
QMenu::item:disabled {{ color: {c['disabled']}; }}
QMenu::separator {{ height: 2px; background: {c['rule']}; margin: 4px 6px; }}
QToolTip {{ background: {c['paper']}; color: {c['text']}; border: 2px solid {c['outline']}; padding: 5px; }}
QSplitter::handle {{ background: transparent; width: 12px; }}
""" + _widget_rules(c)

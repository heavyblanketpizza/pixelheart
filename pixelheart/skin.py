"""Pixelheart's look: paper or Stardew menu pieces applied through one stylesheet."""
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
STARDEW_COLORS_KEY = "view/stardewColors"
READABLE_FAMILIES = '"Helvetica Neue", "Segoe UI", Arial'
INK = "#26221d"
# Warm white and one ink, so the characters people make carry the color.
PAPER_COLORS = {
    "text": INK, "muted": "#6e675d", "link": "#4a443b", "disabled": "#aaa397", "error": "#b3261e",
    "page": "#f5f2ec", "paper": "#fdfbf7", "highlight": "#efebe4", "selection": "#e2ddd3",
    "outline": INK, "frame": "#d9d3c8", "separator": "#d9d3c8", "selected_rule": INK,
    "sidebar": "#edeae3", "sidebar_text": INK, "sidebar_muted": "#6e675d", "nav_hover": "rgba(38, 34, 29, 16)",
    "header": "#efebe4", "header_text": INK, "primary_text": "#fdfbf7", "danger_text": "#9e3a2b",
    # Hand-painted widgets: calendar, milestones, placeholders and icons.
    "ink": INK, "ink_soft": "#6e675d", "ink_faint": "#aaa397",
    "surface": "#fdfbf7", "surface_hover": "#f3f0ea", "surface_pressed": "#e6e1d8", "surface_muted": "#efebe4",
    "rule": "#d9d3c8", "rule_strong": "#aca598", "accent": INK, "accent_soft": "#ebe7df", "accent_rule": INK,
    "warn": "#9e3a2b", "warn_soft": "#f6e8e3", "warn_rule": "#c98d7d",
    "nav_ink": "#5a544a", "nav_ink_hover": INK, "nav_ink_selected": INK,
    "nav_accent": "#9c9588", "nav_accent_hover": "#6e675d", "nav_accent_selected": "#6e675d",
}
# The game's own menu colors, available from View → Stardew menu colors.
STARDEW_COLORS = {
    "text": "#56160c", "muted": "#7a3f1a", "link": "#8a3b0c", "disabled": "#9c7a5a", "error": "#b3261e",
    "page": "#ffd9a0", "paper": "#fff0cf", "highlight": "#fff4d6", "selection": "#f6a13a",
    "outline": "#853605", "frame": "#dc7b05", "separator": "#853605", "selected_rule": "#dc7b05",
    "sidebar": "#5c3514", "sidebar_text": "#fff5dc", "sidebar_muted": "#fff5dc", "nav_hover": "rgba(255, 210, 132, 45)",
    "header": "#dc7b05", "header_text": "#fff5dc", "primary_text": "#fffbe6", "danger_text": "#fff3e6",
    "ink": "#554833", "ink_soft": "#796b56", "ink_faint": "#a19888",
    "surface": "#fffbf2", "surface_hover": "#f4ecdc", "surface_pressed": "#e8d6b1", "surface_muted": "#f0ebdf",
    "rule": "#d8ccb5", "rule_strong": "#b6a27e", "accent": "#536543", "accent_soft": "#e9eddf", "accent_rule": "#87986e",
    "warn": "#965d46", "warn_soft": "#f8e5d9", "warn_rule": "#b7755b",
    "nav_ink": "#c9bba4", "nav_ink_hover": "#f1e6cf", "nav_ink_selected": "#66553e",
    "nav_accent": "#c6a569", "nav_accent_hover": "#d8b477", "nav_accent_selected": "#aa8447",
}
LOOKS = {"paper": PAPER_COLORS, "stardew": STARDEW_COLORS}
# Rule and sidebar-edge widths: one art pixel on paper, the game's chunkier frames otherwise.
_LINES = {"paper": (2, 2), "stardew": (3, 4)}
# Updated in place by use_look(), so modules holding a reference see the change.
COLORS = dict(PAPER_COLORS)
_STATE = {"pieces": {}, "source": "original", "family": None, "look": "paper"}


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


def stardew_colors_enabled():
    return str(game_import.game_import_settings().value(STARDEW_COLORS_KEY, "false")).lower() == "true"


def set_stardew_colors(enabled):
    settings = game_import.game_import_settings()
    settings.setValue(STARDEW_COLORS_KEY, "true" if enabled else "false")
    settings.sync()


def use_look(look):
    """Switch between the ``paper`` and ``stardew`` palettes."""
    COLORS.clear()
    COLORS.update(LOOKS[look])
    _STATE["look"] = look


def current_look():
    return _STATE["look"]


def refresh_pieces(content_root):
    """Rebuild the piece map for the current look and connected game (or none).

    An unwritable cache (full disk, locked home folder) falls back to a temporary
    folder, and failing that to a plain look, so the app always starts.
    """
    pieces, source = {}, "original"
    for folder in (ui_cache_dir(), Path(tempfile.gettempdir()) / "pixelheart-game-ui"):
        try:
            pieces, source = ui_pieces(content_root, folder, look=_STATE["look"])
            break
        except OSError:
            continue
    _STATE["pieces"], _STATE["source"] = pieces, source
    return pieces, source


def current_pieces():
    return _STATE["pieces"]


def current_source():
    return _STATE["source"]


def _image(pieces, role):
    return pieces[role].path.as_posix()


def _border(pieces, role, base=None):
    margin = pieces[base or role].margin
    return f'border-image: url("{_image(pieces, role)}") {margin} {margin} {margin} {margin} stretch stretch;'


def _slice(pieces, role):
    return f"{_border(pieces, role)} border-width: {pieces[role].margin}px;"


def _widget_rules(c, line):
    """Hand-built widgets, kept here so they follow the look like everything else."""
    return f"""
QPushButton#romanceMilestone {{ border-image: none; background: {c['surface']}; border: 2px solid {c['rule']}; padding: 0; }}
QPushButton#romanceMilestone:hover {{ background: {c['surface_hover']}; border-color: {c['rule_strong']}; }}
QPushButton#romanceMilestone:checked {{ background: {c['accent_soft']}; border-color: {c['accent_rule']}; }}
QPushButton#romanceMilestone:pressed {{ background: {c['surface_pressed']}; }}
QPushButton#romanceMilestone:focus {{ border-color: {c['rule_strong']}; }}
QPushButton#romanceMilestone:checked:focus {{ border-color: {c['accent_rule']}; }}
QPushButton#romanceMilestone:disabled {{ background: {c['surface_muted']}; border-color: {c['rule']}; }}
QFrame#birthdayGrid {{ background: {c['rule']}; border: {line}px solid {c['rule_strong']}; }}
QLabel#calendarWeekday {{ background: {c['surface_muted']}; color: {c['ink_soft']}; padding: 8px 0; font-size: 11px; font-weight: bold; }}
QLabel#calendarLegend, QLabel#calendarNote {{ color: {c['ink_soft']}; font-size: 11px; }}
QLabel#birthdaySelection {{ color: {c['accent']}; font-size: 21px; }}
QLabel#birthdaySelection[conflict="true"], QLabel#birthdayStatus[conflict="true"] {{ color: {c['warn']}; }}
QLabel#birthdayStatus {{ color: {c['ink_soft']}; font-size: 12px; }}
QFrame#birthdayReceipt {{ background: {c['accent_soft']}; border: 1px solid {c['rule']}; }}
QFrame#birthdayReceipt[conflict="true"] {{ background: {c['warn_soft']}; border-color: {c['warn_rule']}; }}
QLabel#calendarFestivalDate {{ color: {c['ink_soft']}; font-size: 12px; font-weight: bold; }}
QLabel#calendarFestivalName {{ color: {c['ink_soft']}; font-size: 12px; }}
QLabel#calendarDetailsHeading {{ color: {c['ink_soft']}; font-size: 10px; font-weight: bold; letter-spacing: 1px; }}
QListWidget#catalogueGallery {{ background: {c['surface_muted']}; border: 1px solid {c['rule_strong']}; padding: 4px; }}
QListWidget#catalogueGallery::item {{ border: 1px solid {c['rule']}; background: {c['surface']}; padding: 4px; }}
QListWidget#catalogueGallery::item:selected {{ background: {c['accent_soft']}; border: 2px solid {c['accent_rule']}; color: {c['accent']}; }}
QListWidget#catalogueGallery::item:hover {{ background: {c['surface_hover']}; }}
"""


def _plain_stylesheet(c, pixel, body, size, line):
    """Colors and fonts only, for the rare case no interface pieces could be written."""
    return f"""
QWidget {{ font-family: {body}; font-size: {size}px; color: {c['text']}; }}
QMainWindow, QDialog, QWidget#workspace, QWidget#welcome, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {c['page']}; }}
QWidget#sidebar, QStatusBar {{ background: {c['sidebar']}; color: {c['sidebar_text']}; }}
QWidget#sidebar QLabel, QStatusBar QLabel, QListWidget#navigation::item {{ color: {c['sidebar_text']}; }}
QListWidget#navigation::item:selected {{ background: {c['paper']}; color: {c['text']}; }}
QFrame#card, QFrame#profile, QFrame#panel {{ background: {c['paper']}; border: {line}px solid {c['outline']}; }}
QPushButton {{ font-family: "{pixel}"; background: {c['paper']}; border: 2px solid {c['outline']}; padding: 4px 10px; }}
QPushButton#primary {{ background: {c['outline']}; color: {c['primary_text']}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{ background: {c['paper']}; border: 2px solid {c['outline']}; padding: 3px; }}
""" + _widget_rules(c, line)


def build_stylesheet(pieces, *, easy_read=False):
    c, pixel = COLORS, pixel_family()
    line, edge = _LINES[_STATE["look"]]
    body = READABLE_FAMILIES if easy_read else f'"{pixel}"'
    size = 14 if easy_read else 16
    if any(role not in pieces for role in REQUIRED_ROLES):
        return _plain_stylesheet(c, pixel, body, size, line)
    return f"""
QWidget {{ font-family: {body}; font-size: {size}px; color: {c['text']}; }}
QMainWindow, QDialog, QWidget#workspace, QWidget#welcome, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {c['page']}; }}
QWidget#sidebar {{ background-color: {c['sidebar']}; background-image: url("{_image(pieces, 'wood')}"); border-right: {edge}px solid {c['outline']}; }}
QLabel {{ background: transparent; }}
QLabel:disabled {{ color: {c['disabled']}; }}
QLabel#brand, QLabel#title, QLabel#sectionTitle, QLabel#profileName, QLabel#eyebrow, QLabel#breadcrumb {{ font-family: "{pixel}"; }}
QLabel#brand {{ font-size: 30px; color: {c['sidebar_text']}; }}
QLabel#title {{ font-size: 32px; color: {c['text']}; }}
QLabel#sectionTitle {{ font-size: 20px; color: {c['text']}; }}
QLabel#profileName {{ font-size: 26px; color: {c['text']}; }}
QLabel#eyebrow, QLabel#breadcrumb {{ font-size: 13px; color: {c['muted']}; letter-spacing: 1px; }}
QLabel#muted, QLabel#hint, QLabel#saveState {{ color: {c['muted']}; font-size: {size - 2}px; }}
QLabel#sceneLocation {{ font-family: "{pixel}"; font-size: 16px; }}
QWidget#sidebar QLabel#eyebrow, QWidget#sidebar QLabel#hint, QWidget#sidebar QLabel#muted {{ color: {c['sidebar_muted']}; }}
QLabel#badge {{ background: {c['highlight']}; border: 2px solid {c['frame']}; padding: 3px 8px; }}
QLabel#notice {{ {_slice(pieces, 'textbox')} padding: 4px 6px; }}
QFrame#card, QFrame#profile, QFrame#panel {{ {_slice(pieces, 'panel')} }}
QFrame#card[dropTarget="true"] {{ background: {c['highlight']}; }}
QFrame#workspaceSeparator, QFrame#sidebarSeparator {{ background: {c['separator']}; border: none; min-height: 2px; max-height: 2px; }}
QPushButton {{ font-family: "{pixel}"; font-size: 16px; color: {c['text']}; {_slice(pieces, 'button')} padding: 1px 8px; min-height: 22px; }}
QPushButton:hover {{ {_border(pieces, 'button_hover', 'button')} }}
QPushButton:pressed {{ {_border(pieces, 'button_pressed', 'button')} }}
QPushButton:disabled {{ color: {c['disabled']}; {_border(pieces, 'button_disabled', 'button')} }}
QPushButton#primary {{ color: {c['primary_text']}; {_slice(pieces, 'button_primary')} }}
QPushButton#danger {{ color: {c['danger_text']}; {_slice(pieces, 'button_danger')} }}
QPushButton#quiet, QPushButton#sidebarOpen {{ border-image: none; border: none; background: transparent; color: {c['link']}; padding: 3px 2px; text-align: left; }}
QPushButton#quiet:hover, QPushButton#sidebarOpen:hover {{ color: {c['text']}; text-decoration: underline; }}
QPushButton#quiet:disabled {{ color: {c['disabled']}; }}
QWidget#sidebar QPushButton#sidebarOpen {{ color: {c['sidebar_text']}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{ {_slice(pieces, 'textbox')} background: transparent; padding: 2px 4px; selection-background-color: {c['selection']}; selection-color: {c['text']}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {{ {_border(pieces, 'textbox_focus', 'textbox')} }}
QLineEdit:read-only, QPlainTextEdit:read-only {{ color: {c['muted']}; }}
QLineEdit:disabled, QPlainTextEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {c['disabled']}; }}
QLineEdit[error="true"], QPlainTextEdit[error="true"], QSpinBox[error="true"], QComboBox[error="true"] {{ color: {c['error']}; }}
QLineEdit#storyEventTitle {{ font-family: "{pixel}"; font-size: 22px; border-image: none; border: 2px solid transparent; background: transparent; }}
QLineEdit#storyEventTitle:focus {{ {_slice(pieces, 'textbox')} }}
QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: right center; width: 20px; height: 22px; border: none; border-image: url("{_image(pieces, 'dropdown_arrow')}") 0 0 0 0 stretch stretch; }}
QComboBox::down-arrow {{ image: none; }}
QComboBox QAbstractItemView {{ background: {c['paper']}; color: {c['text']}; border: {line}px solid {c['outline']}; selection-background-color: {c['selection']}; selection-color: {c['text']}; outline: none; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; border: none; background: transparent; }}
QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 18px; height: 18px; image: url("{_image(pieces, 'checkbox_off')}"); }}
QCheckBox::indicator:checked {{ image: url("{_image(pieces, 'checkbox_on')}"); }}
QListWidget, QTableWidget, QTreeWidget {{ background: {c['paper']}; border: {line}px solid {c['frame']}; color: {c['text']}; outline: none; }}
QListWidget::item {{ padding: 8px 6px; border: 2px solid transparent; }}
QListWidget::item:hover {{ background: {c['highlight']}; }}
QListWidget::item:selected {{ background: {c['highlight']}; color: {c['text']}; border: 2px solid {c['selected_rule']}; }}
QListWidget#navigation {{ background: transparent; border: none; font-family: "{pixel}"; font-size: 16px; }}
QListWidget#navigation::item {{ color: {c['sidebar_text']}; padding: 6px 8px; margin: 1px 0; border: 2px solid transparent; }}
QListWidget#navigation::item:hover {{ background: {c['nav_hover']}; }}
QListWidget#navigation::item:selected {{ color: {c['text']}; background: transparent; {_slice(pieces, 'tab')} }}
QListWidget#giftTiles {{ background: {c['paper']}; font-size: 12px; }}
QListWidget#giftTiles[dropTarget="true"] {{ background: {c['highlight']}; border-color: {c['outline']}; }}
QTableWidget {{ gridline-color: {c['frame']}; alternate-background-color: {c['highlight']}; selection-background-color: {c['selection']}; selection-color: {c['text']}; }}
QHeaderView::section {{ background: {c['header']}; color: {c['header_text']}; font-family: "{pixel}"; padding: 6px; border: none; border-right: 2px solid {c['outline']}; }}
QTableCornerButton::section {{ background: {c['header']}; border: none; }}
QTabWidget::pane {{ border: none; border-top: {line}px solid {c['outline']}; top: -{line}px; background: transparent; }}
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
QStatusBar {{ background: {c['sidebar']}; color: {c['sidebar_text']}; border-top: {line}px solid {c['separator']}; }}
QStatusBar QLabel, QStatusBar QLabel#hint {{ color: {c['sidebar_text']}; }}
QMenuBar {{ background: {c['page']}; font-family: "{pixel}"; }}
QMenuBar::item:selected {{ background: {c['highlight']}; }}
QMenu {{ background: {c['paper']}; border: {line}px solid {c['outline']}; padding: 4px; font-family: "{pixel}"; }}
QMenu::item {{ padding: 6px 22px; }}
QMenu::item:selected {{ background: {c['selection']}; color: {c['text']}; }}
QMenu::item:disabled {{ color: {c['disabled']}; }}
QMenu::separator {{ height: 2px; background: {c['frame']}; margin: 4px 6px; }}
QToolTip {{ background: {c['paper']}; color: {c['text']}; border: {line}px solid {c['outline']}; padding: 5px; }}
QSplitter::handle {{ background: transparent; width: 12px; }}
""" + _widget_rules(c, line)

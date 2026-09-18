"""A quiet parchment workspace with native, accessible controls."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication


def heart_icon(size=64):
    """Draw our own little pixel heart, with no external game artwork."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
    painter.scale(size / 12, size / 12)
    pattern = (
        "..oo..oo..",
        ".ohhoohho.",
        "ohrrrrrrro",
        "orrrrrrrro",
        ".orrrrrro.",
        "..orrrro..",
        "...orro...",
        "....oo....",
    )
    for y, row in enumerate(pattern):
        for x, cell in enumerate(row):
            if cell != ".":
                color = {"o": "#763c30", "h": "#f8b28d", "r": "#d76050" if y < 4 else "#bb493e"}[cell]
                painter.fillRect(x + 1, y + 2, 1, 1, QColor(color))
    painter.end()
    return QIcon(pixmap)


def apply_theme(app: QApplication):
    resources = Path(__file__).parent / "resources"
    stylesheet = (STYLESHEET.replace("__CHEVRON__", (resources / "chevron.svg").as_posix())
                  .replace("__CHECKMARK__", (resources / "checkmark.svg").as_posix())
                     .replace("__CHEVRON_UP__", (resources / "chevron-up.svg").as_posix()))
    if app.styleSheet() == stylesheet and app.style().objectName().casefold() == "fusion":
        return
    app.setStyle("Fusion")
    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: "#f4f0e5",
        QPalette.ColorRole.WindowText: "#443c30",
        QPalette.ColorRole.Base: "#fffcf5",
        QPalette.ColorRole.AlternateBase: "#f4f0e5",
        QPalette.ColorRole.Text: "#443c30",
        QPalette.ColorRole.Button: "#f7f1e3",
        QPalette.ColorRole.ButtonText: "#443c30",
        QPalette.ColorRole.Highlight: "#61734d",
        QPalette.ColorRole.HighlightedText: "#fffdf5",
        QPalette.ColorRole.PlaceholderText: "#958c7e",
        QPalette.ColorRole.Light: "#fffbf2",
        QPalette.ColorRole.Midlight: "#e8dfcd",
        QPalette.ColorRole.Mid: "#c2b69e",
        QPalette.ColorRole.Dark: "#796b55",
        QPalette.ColorRole.ToolTipBase: "#504433",
        QPalette.ColorRole.ToolTipText: "#fff9e9",
        QPalette.ColorRole.Link: "#61734d",
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#9b9283"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#9b9283"))
    app.setPalette(palette)
    font = QFont("Arial")
    font.setPixelSize(14)
    app.setFont(font)
    app.setWindowIcon(heart_icon())
    app.setStyleSheet(stylesheet)


STYLESHEET = """
QWidget { color: #443c30; font-size: 14px; }
QMainWindow, QDialog { background: #f4f0e5; }
QWidget#workspace, QScrollArea, QScrollArea > QWidget > QWidget { background: #f4f0e5; }
QWidget#sidebar { background: #504433; border-right: 1px solid #413829; }
QLabel { background: transparent; }
QLabel:disabled { color: #9b9283; }
QLabel#brand { font-family: Georgia; font-size: 26px; font-weight: 700; color: #fff5dc; letter-spacing: -1px; }
QLabel#eyebrow { color: #796b56; font-size: 10px; font-weight: 700; letter-spacing: 1.5px; }
QLabel#breadcrumb { color: #796b56; font-size: 10px; font-weight: 600; letter-spacing: 1.2px; }
QLabel#saveState { color: #796b56; font-size: 12px; }
QFrame#workspaceSeparator { background: #ded5c2; border: none; min-height: 1px; max-height: 1px; }
QFrame#sidebarSeparator { background: #74664f; border: none; min-height: 1px; max-height: 1px; }
QLabel#title { font-family: Georgia; font-size: 28px; color: #443c30; }
QLabel#sectionTitle { font-family: Georgia; font-size: 17px; font-weight: 700; color: #554833; }
QLabel#muted { color: #796b56; font-size: 12px; }
QLabel#hint { color: #796b56; font-size: 12px; }
QWidget#sidebar QLabel#eyebrow { color: #c5b58f; font-size: 9px; letter-spacing: 1.3px; }
QWidget#sidebar QLabel#muted, QWidget#sidebar QLabel#hint { color: #c7bcaa; }
QWidget#sidebar QLabel#hint { font-size: 10px; }
QLabel#profileName { font-family: Georgia; font-size: 26px; color: #514630; }
QLabel#badge { color: #61734d; background: #edf0e2; border: 1px solid #d6ddc5; border-radius: 3px; padding: 5px 9px; font-size: 11px; }
QLabel#notice { background: #f5ecd6; color: #836c44; border: 1px solid #e5d6b6; border-left: 3px solid #c0a06a; border-radius: 3px; padding: 12px; font-size: 12px; }
QFrame#card { background: #fffbf2; border: 1px solid #dcd1bb; border-radius: 4px; }
QFrame#profile { background: #efeddf; border: 1px solid #d8d4bc; border-radius: 4px; }
QPushButton { background: #f8f2e5; border: 1px solid #d3c6ad; border-radius: 3px; padding: 8px 14px; font-size: 13px; font-weight: 600; }
QPushButton:hover { background: #f0e7d5; border-color: #bcab8b; }
QPushButton:pressed { background: #e8ddc6; border-color: #a99a7a; }
QPushButton:focus { border-color: #87936e; }
QPushButton:disabled { color: #a19888; background: #f0ebdf; border-color: #e0d8c7; }
QPushButton#primary { background: #61734d; color: #fffdf5; border-color: #61734d; }
QPushButton#primary:hover { background: #6f805a; border-color: #6f805a; }
QPushButton#primary:pressed { background: #536443; border-color: #536443; }
QPushButton#primary:focus { border-color: #c7ac73; }
QPushButton#primary:disabled { color: #f2f1e9; background: #a3ad95; border-color: #a3ad95; }
QPushButton#quiet { background: transparent; border: 1px solid transparent; color: #61734d; padding: 5px 2px; text-align: left; }
QPushButton#quiet:hover { color: #465c35; background: #eeeddf; }
QPushButton#quiet:focus { border-color: #a7b194; }
QPushButton#quiet:disabled { color: #a19888; background: transparent; }
QWidget#sidebar QPushButton { background: #655740; border-color: #807055; color: #fbf3df; }
QWidget#sidebar QPushButton:hover { background: #746348; border-color: #ad956e; }
QWidget#sidebar QPushButton:pressed { background: #5c4e39; }
QWidget#sidebar QPushButton:focus { border-color: #cfb478; }
QWidget#sidebar QPushButton#sidebarAction { background: #655740; border-color: #8d7958; padding: 7px 10px; text-align: left; }
QWidget#sidebar QPushButton#sidebarAction:hover { background: #746348; border-color: #ad956e; }
QWidget#sidebar QPushButton#sidebarOpen { background: transparent; border-color: transparent; color: #d9cdb5; padding: 7px 10px; text-align: left; }
QWidget#sidebar QPushButton#sidebarOpen:hover { background: #605340; color: #fff5de; }
QWidget#sidebar QPushButton#sidebarOpen:focus { border-color: #a48e69; }
QWidget#sidebar QPushButton#primary { background: #e6d8b8; color: #51442f; border-color: #e6d8b8; }
QWidget#sidebar QPushButton#primary:hover { background: #f0e4ca; border-color: #f0e4ca; }
QWidget#sidebar QPushButton#primary:pressed { background: #d4c4a2; border-color: #d4c4a2; }
QWidget#sidebar QPushButton#quiet { background: transparent; border-color: transparent; color: #d9cdb5; }
QWidget#sidebar QPushButton#quiet:hover { color: #fff5de; background: #605340; }
QWidget#sidebar QPushButton#quiet:focus { border-color: #a48e69; }
QPushButton#danger { color: #9b5146; }
QPushButton#danger:hover { background: #f6e8df; border-color: #c79e91; }
QLineEdit, QPlainTextEdit, QSpinBox, QComboBox { background: #fffcf5; border: 1px solid #d9cfbb; border-radius: 3px; padding: 8px; selection-background-color: #61734d; selection-color: #fffdf5; }
QLineEdit:hover, QPlainTextEdit:hover, QSpinBox:hover, QComboBox:hover { border-color: #bfb198; }
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus { border: 1px solid #889875; background: #fffef9; }
QLineEdit:read-only, QPlainTextEdit:read-only { color: #796b56; background: #f6f2e8; }
QLineEdit:disabled, QPlainTextEdit:disabled, QSpinBox:disabled, QComboBox:disabled { color: #a19888; background: #eeeadf; border-color: #e0d8c7; }
QLineEdit[error="true"], QPlainTextEdit[error="true"], QSpinBox[error="true"], QComboBox[error="true"] { border-color: #b56d5d; background: #fff8f0; }
QSpinBox { padding-right: 28px; }
QSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 24px; background: #f5f0e3; border: none; border-left: 1px solid #e4dccb; border-bottom: 1px solid #e4dccb; border-top-right-radius: 3px; }
QSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 24px; background: #f5f0e3; border: none; border-left: 1px solid #e4dccb; border-bottom-right-radius: 3px; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #ebe4d2; }
QSpinBox::up-arrow { image: url("__CHEVRON_UP__"); width: 8px; height: 5px; }
QSpinBox::down-arrow { image: url("__CHEVRON__"); width: 8px; height: 5px; }
QComboBox { padding-right: 30px; }
QComboBox::drop-down { border: none; border-left: 1px solid #e4dccb; background: #f5f0e3; width: 26px; border-top-right-radius: 3px; border-bottom-right-radius: 3px; }
QComboBox::down-arrow { image: url("__CHEVRON__"); width: 12px; height: 8px; }
QComboBox QAbstractItemView { background: #fffcf5; border: 1px solid #cbbda4; selection-background-color: #e9eddf; selection-color: #4f623c; color: #443c30; padding: 4px; outline: none; }
QCheckBox { spacing: 9px; padding: 5px 0; }
QCheckBox:disabled { color: #a19888; }
QCheckBox::indicator { width: 16px; height: 16px; background: #fffcf5; border: 1px solid #bfb198; border-radius: 2px; }
QCheckBox::indicator:checked { image: url("__CHECKMARK__"); background: #61734d; border-color: #61734d; }
QCheckBox::indicator:hover, QCheckBox::indicator:focus { border-color: #87936e; }
QCheckBox::indicator:disabled { background: #ebe6d9; border-color: #d5ccb8; }
QCheckBox::indicator:checked:disabled { background: #a3ad95; border-color: #a3ad95; }
QListWidget { background: #fffbf2; border: 1px solid #dcd1bb; border-radius: 3px; padding: 5px; outline: none; }
QListWidget::item { padding: 11px 9px; border: 1px solid transparent; border-radius: 2px; }
QListWidget::item:selected { background: #e9eddf; border-color: #c5d0b3; color: #4f623c; }
QListWidget::item:hover { background: #f3eddf; }
QListWidget::item:selected:hover { background: #e2e8d6; }
QListWidget:focus { border-color: #97a27f; }
QListWidget:disabled { color: #a19888; background: #f2eee4; }
QListWidget#giftTiles { padding: 4px; border: 1px solid #ded3bd; background: #faf5e9; }
QListWidget#giftTiles::item { padding: 5px 4px; margin: 2px; border: 1px solid transparent; font-size: 11px; }
QListWidget#giftTiles::item:selected { background: #e5ebd8; border-color: #a8b68e; }
QListWidget#giftTiles::item:hover { background: #f1e9d6; border-color: #d6c8a9; }
QListWidget#giftTiles[dropTarget="true"] { background: #edf1e3; border: 1px solid #8e9e75; }
QListWidget#navigation { background: transparent; border: none; padding: 0; font-size: 13px; }
QListWidget#navigation::item { padding: 7px 10px; margin: 2px 0; color: #d9ceba; border: 1px solid transparent; border-left: 3px solid transparent; border-radius: 3px; }
QListWidget#navigation::item:hover { background: #625540; color: #fff8e9; }
QListWidget#navigation::item:selected { background: #f2e7cf; color: #51442f; border-color: #f2e7cf; border-left: 3px solid #c0a06a; font-weight: 600; }
QListWidget#navigation::item:selected:hover { background: #f8eedb; border-color: #f8eedb; border-left-color: #c0a06a; }
QListWidget#navigation:focus::item:selected { border-color: #c7ac73; border-left-color: #c0a06a; }
QTableWidget { background: #fffbf2; alternate-background-color: #f6f1e6; border: 1px solid #dcd1bb; gridline-color: #e9e0ce; selection-background-color: #e9eddf; selection-color: #4f623c; color: #443c30; }
QHeaderView::section { background: #f0e9da; color: #75674f; padding: 10px; border: none; border-right: 1px solid #e1d6c0; border-bottom: 1px solid #dcd1bb; text-align: left; font-size: 12px; font-weight: 600; }
QTableWidget QLineEdit, QTableWidget QSpinBox, QTableWidget QComboBox { border: none; border-radius: 0; padding: 5px; }
QTableCornerButton::section { background: #f0e9da; border: 1px solid #e1d6c0; }
QTabWidget::pane { background: #f4f0e5; border: none; border-top: 1px solid #dcd1bb; top: -1px; }
QTabBar::tab { background: transparent; color: #796b56; border: none; border-bottom: 2px solid transparent; padding: 10px 15px; margin-right: 4px; font-size: 13px; }
QTabBar::tab:selected { background: transparent; color: #536543; border-bottom: 2px solid #75885e; font-weight: 600; }
QTabBar::tab:hover:!selected { background: #eee7d7; color: #5d503c; border-bottom-color: #c0ae8c; }
QTabBar::tab:focus { border-bottom-color: #b89a62; }
QScrollArea { border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #c8bda6; border: none; border-radius: 3px; min-height: 36px; }
QScrollBar::handle:vertical:hover { background: #ad9d7f; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #c8bda6; border: none; border-radius: 3px; min-width: 36px; }
QScrollBar::handle:horizontal:hover { background: #ad9d7f; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
QStatusBar { background: #eee8d9; color: #796b56; border-top: 1px solid #ded3bd; font-size: 11px; }
QStatusBar::item { border: none; }
QStatusBar QLabel#hint { font-size: 10px; }
QMenuBar { background: #f4f0e5; }
QMenuBar::item:selected { background: #eae2d0; border-radius: 2px; }
QMenu { background: #fffbf2; border: 1px solid #d0c2a9; padding: 5px; }
QMenu::item { padding: 8px 23px; border-radius: 2px; }
QMenu::item:selected { background: #e9eddf; color: #4f623c; }
QMenu::item:disabled { color: #a19888; }
QMenu::separator { height: 1px; background: #e4dac6; margin: 5px 8px; }
QToolTip { background: #504433; color: #fff9e9; border: 1px solid #7f6d50; padding: 7px; }
QSplitter::handle { background: transparent; width: 15px; }
QSplitter::handle:hover { background: #e9e2d1; }
"""

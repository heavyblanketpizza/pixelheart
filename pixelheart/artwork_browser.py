"""Focused frame browsing with optional sheet guides and walking playback."""

from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)

from pixelheart_core.artwork import ArtworkValidationError, inspect_artwork
from .widgets import label, paint_artwork_placeholder


DIRECTIONS = ("Down", "Right", "Up", "Left")
EXPRESSION_NAMES = ("Neutral", "Happy", "Sad", "Unique", "Love", "Angry")


def _checkerboard(painter, rect):
    painter.save()
    painter.setClipRect(rect)
    for y in range(rect.top(), rect.bottom() + 1, 10):
        for x in range(rect.left(), rect.right() + 1, 10):
            color = "#f3eee3" if ((x - rect.left()) // 10 + (y - rect.top()) // 10) % 2 else "#eae4d5"
            painter.fillRect(x, y, 10, 10, QColor(color))
    painter.restore()


def _fitted_rect(source, bounds):
    size = source.size().scaled(bounds.size(), Qt.AspectRatioMode.KeepAspectRatio)
    target = QRect(0, 0, size.width(), size.height())
    target.moveCenter(bounds.center())
    return target


def _expression_title(index):
    name = EXPRESSION_NAMES[index] if index < len(EXPRESSION_NAMES) else "Expression"
    return f"{name} · ${index}"


class _FramePreview(QWidget):
    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser
        self.setFixedHeight(156)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(browser.kind.title() + " selected frame preview")
        self.setToolTip("Arrow keys change frames. In Sheet layout, click a frame to inspect it.")

    def image_rect(self):
        """Display coordinates of the source image, shared by paint and hit tests."""
        browser = self.browser
        if browser.pixmap.isNull():
            return QRect()
        source = browser.pixmap.rect() if browser.sheet_layout.isChecked() or not browser.frame_count else browser.frame_rect(browser.current_frame)
        return _fitted_rect(source, self.rect().adjusted(12, 12, -12, -12))

    def sheet_cell_rect(self, index):
        browser = self.browser
        source = browser.frame_rect(index)
        if source.isEmpty():
            return QRect()
        target = self.image_rect()
        x = target.left() + round(source.left() * target.width() / browser.pixmap.width())
        y = target.top() + round(source.top() * target.height() / browser.pixmap.height())
        right = target.left() + round((source.right() + 1) * target.width() / browser.pixmap.width())
        bottom = target.top() + round((source.bottom() + 1) * target.height() / browser.pixmap.height())
        return QRect(x, y, right - x, bottom - y)

    def paintEvent(self, event):
        painter = QPainter(self)
        browser = self.browser
        if not browser.pixmap.isNull():
            _checkerboard(painter, self.rect())
            source = browser.pixmap.rect() if browser.sheet_layout.isChecked() or not browser.frame_count else browser.frame_rect(browser.current_frame)
            target = self.image_rect()
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            painter.drawPixmap(target, browser.pixmap, source)
            if browser.sheet_layout.isChecked() and browser.frame_count:
                painter.setPen(QPen(QColor("#a78655"), 1))
                for index in range(browser.frame_count):
                    painter.drawRect(self.sheet_cell_rect(index).adjusted(0, 0, -1, -1))
                selected = self.sheet_cell_rect(browser.current_frame).adjusted(0, 0, -1, -1)
                painter.fillRect(selected, QColor(224, 230, 189, 65))
                painter.setPen(QPen(QColor("#fff4d6"), 4))
                painter.drawRect(selected)
                painter.setPen(QPen(QColor("#435c31"), 2))
                painter.drawRect(selected)
        else:
            paint_artwork_placeholder(painter, self.rect(), f"Add a {browser.kind} sheet to preview it", portrait=browser.kind == "portrait")
        if self.hasFocus():
            painter.setPen(QPen(QColor("#728348"), 2))
            painter.drawRect(self.rect().adjusted(1, 1, -2, -2))
        painter.end()

    def mousePressEvent(self, event):
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        browser = self.browser
        target = self.image_rect()
        position = event.position().toPoint()
        if event.button() == Qt.MouseButton.LeftButton and browser.sheet_layout.isChecked() and browser.frame_count and target.contains(position):
            column = min(browser.columns - 1, (position.x() - target.left()) * browser.columns // target.width())
            rows = browser.frame_count // browser.columns
            row = min(rows - 1, (position.y() - target.top()) * rows // target.height())
            browser.select_frame(row * browser.columns + column)
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        browser = self.browser
        deltas = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1, Qt.Key.Key_Up: -browser.columns, Qt.Key.Key_Down: browser.columns}
        if event.key() in deltas and browser.frame_count:
            browser.select_frame(max(0, min(browser.frame_count - 1, browser.current_frame + deltas[event.key()])))
        elif event.key() in (Qt.Key.Key_Home, Qt.Key.Key_End) and browser.frame_count:
            browser.select_frame(0 if event.key() == Qt.Key.Key_Home else browser.frame_count - 1)
        else:
            super().keyPressEvent(event)
            return
        event.accept()


class SheetBrowser(QWidget):
    """Read-only sheet preview; frame indices are zero-based in the Python API.

    Reference sheets may contain fewer rows than a finished custom character.
    Browsing never changes the source or relaxes project export validation.
    """

    frame_changed = Signal(int)

    def __init__(self, kind, parent=None):
        if kind not in ("portrait", "sprite"):
            raise ValueError("Choose portrait or sprite artwork.")
        super().__init__(parent)
        self.kind = kind
        self.columns = 2 if kind == "portrait" else 4
        self.pixmap = QPixmap()
        self.frame_count = 0
        self.current_frame = 0
        self.frame_size = QSize()
        self.setAccessibleName(kind.title() + " sheet browser")
        self.setMinimumWidth(220)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(7)
        self.preview = _FramePreview(self)
        root.addWidget(self.preview)

        browse = QHBoxLayout()
        browse.setSpacing(6)
        self.previous_button = QPushButton("‹")
        self.previous_button.setObjectName("quiet")
        self.previous_button.setFixedWidth(28)
        self.previous_button.setAccessibleName("Previous " + kind + " frame")
        self.previous_button.setToolTip("Previous frame")
        browse.addWidget(self.previous_button)
        if kind == "portrait":
            self.frame_selector = QComboBox()
            self.frame_selector.setAccessibleName("Portrait expression and dialogue index")
            self.frame_selector.currentIndexChanged.connect(self.select_frame)
        else:
            self.frame_selector = QSpinBox()
            self.frame_selector.setPrefix("Frame ")
            self.frame_selector.setAccessibleName("Sprite frame index")
            self.frame_selector.valueChanged.connect(self.select_frame)
        browse.addWidget(self.frame_selector, 1)
        self.next_button = QPushButton("›")
        self.next_button.setObjectName("quiet")
        self.next_button.setFixedWidth(28)
        self.next_button.setAccessibleName("Next " + kind + " frame")
        self.next_button.setToolTip("Next frame")
        browse.addWidget(self.next_button)
        root.addLayout(browse)
        self.previous_button.clicked.connect(lambda: self.select_frame(self.current_frame - 1))
        self.next_button.clicked.connect(lambda: self.select_frame(self.current_frame + 1))

        self.controls = QWidget(self)
        controls = QHBoxLayout(self.controls)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(6)
        self.direction = QComboBox()
        self.direction.addItems(DIRECTIONS)
        self.direction.setAccessibleName("Walking direction")
        self.direction.setToolTip("Preview the four standard walking rows")
        controls.addWidget(self.direction, 1)
        self.play_button = QPushButton("Play")
        self.play_button.setAccessibleName("Play walking animation")
        self.play_button.setCheckable(True)
        controls.addWidget(self.play_button)
        self.speed = QSpinBox()
        self.speed.setRange(1, 12)
        self.speed.setValue(6)
        self.speed.setSuffix(" fps")
        self.speed.setAccessibleName("Animation speed in frames per second")
        controls.addWidget(self.speed)
        root.addWidget(self.controls)
        self.controls.setVisible(kind == "sprite")

        summary = QHBoxLayout()
        summary.setSpacing(7)
        self.status = label("", "hint", True)
        self.status.setAccessibleName("Selected " + kind + " frame")
        summary.addWidget(self.status, 1)
        self.sheet_layout = QCheckBox("Sheet layout")
        self.sheet_layout.setAccessibleName(kind.title() + " sheet layout")
        self.sheet_layout.setToolTip("Show the full sheet with frame boundaries. Click a cell to select it.")
        self.sheet_layout.toggled.connect(self._layout_changed)
        summary.addWidget(self.sheet_layout)
        root.addLayout(summary)
        self.hint = label("", "hint", True)
        root.addWidget(self.hint)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.advance_frame)
        self.direction.currentIndexChanged.connect(self._direction_changed)
        self.play_button.toggled.connect(self._play_toggled)
        self.speed.valueChanged.connect(lambda fps: self.timer.setInterval(round(1000 / fps)))
        self.set_image()

    def set_image(self, path=None):
        """Safely load an image, or clear it; never alter the source artwork."""
        self.stop_playback()
        self.pixmap = QPixmap()
        self.frame_count = 0
        self.current_frame = 0
        self.frame_size = QSize()
        issue = ""
        if path:
            try:
                info = inspect_artwork(path)
                self.pixmap = QPixmap(str(Path(path)))
                if self.pixmap.isNull() or self.pixmap.width() != info["width"] or self.pixmap.height() != info["height"]:
                    self.pixmap = QPixmap()
                    raise ArtworkValidationError("This PNG could not be displayed. Choose a readable local PNG sheet.")
                width, height = info["width"], info["height"]
                frame_width = width // self.columns
                frame_height = frame_width if self.kind == "portrait" else frame_width * 2
                minimum_width = 128 if self.kind == "portrait" else 64
                if width >= minimum_width and width % self.columns == 0 and height % frame_height == 0 and height >= frame_height:
                    self.frame_size = QSize(frame_width, frame_height)
                    self.frame_count = self.columns * (height // frame_height)
                else:
                    layout = "2 columns of square expressions" if self.kind == "portrait" else "4 columns of 1:2 frames"
                    issue = f"Showing the whole sheet. Frame preview needs {layout} at the dimensions above or a larger proportional size."
            except (ArtworkValidationError, OSError, ValueError) as exc:
                self.pixmap = QPixmap()
                issue = str(exc)
        self.controls.setEnabled(self.frame_count >= 16)
        self.frame_selector.blockSignals(True)
        if self.kind == "portrait":
            self.frame_selector.clear()
            self.frame_selector.addItems([_expression_title(index) for index in range(self.frame_count)] or ["Choose a portrait sheet"])
        else:
            self.frame_selector.setRange(0, max(0, self.frame_count - 1))
            self.frame_selector.setValue(0)
        self.frame_selector.blockSignals(False)
        self.frame_selector.setEnabled(self.frame_count > 0)
        self.direction.blockSignals(True)
        self.direction.setCurrentIndex(0)
        self.direction.blockSignals(False)
        self.sheet_layout.setChecked(False)
        self.sheet_layout.setEnabled(self.frame_count > 0)
        if self.frame_count:
            self._show_frame(0)
            self.hint.setText("Walking preview needs the first four rows." if self.kind == "sprite" and self.frame_count < 16 else "")
        else:
            self.previous_button.setEnabled(False)
            self.next_button.setEnabled(False)
            self.status.setText("Whole sheet preview" if not self.pixmap.isNull() else "")
            self.hint.setText(issue or "Load a template or upload a PNG sheet to explore its frames.")
            self.preview.setAccessibleDescription(self.hint.text())
        self.hint.setVisible(bool(self.hint.text()))
        self.preview.update()

    def frame_rect(self, index):
        if not 0 <= index < self.frame_count:
            return QRect()
        return QRect((index % self.columns) * self.frame_size.width(), (index // self.columns) * self.frame_size.height(), self.frame_size.width(), self.frame_size.height())

    def select_frame(self, index):
        """Select any frame, including extra poses, and pause animation."""
        if 0 <= index < self.frame_count:
            self.stop_playback()
            self._show_frame(index)

    def _show_frame(self, index):
        if not 0 <= index < self.frame_count:
            return
        changed = index != self.current_frame
        self.current_frame = index
        self.frame_selector.blockSignals(True)
        if self.kind == "portrait":
            self.frame_selector.setCurrentIndex(index)
        else:
            self.frame_selector.setValue(index)
        self.frame_selector.blockSignals(False)
        self.previous_button.setEnabled(index > 0)
        self.next_button.setEnabled(index + 1 < self.frame_count)
        title = _expression_title(index) if self.kind == "portrait" else f"Frame {index}"
        suffix = ""
        if self.kind == "sprite" and index < 16:
            direction = index // 4
            self.direction.blockSignals(True)
            self.direction.setCurrentIndex(direction)
            self.direction.blockSignals(False)
            suffix = f" · {DIRECTIONS[direction].lower()} walk"
        elif self.kind == "sprite":
            suffix = " · extra pose"
        position = f"Row {index // self.columns + 1}, column {index % self.columns + 1}"
        self.status.setText(position if self.sheet_layout.isChecked() else f"{index + 1} of {self.frame_count}{suffix}")
        self.status.setToolTip(position)
        self.preview.setAccessibleDescription(f"{title} · {self.status.text()} · {position}")
        self.preview.update()
        if changed:
            self.frame_changed.emit(index)

    def _layout_changed(self, visible):
        self.preview.setFixedHeight(260 if visible else 156)
        self.preview.setAccessibleName(self.kind.title() + (" sheet layout preview" if visible else " selected frame preview"))
        if self.frame_count:
            self._show_frame(self.current_frame)
        self.preview.update()

    def _direction_changed(self, index):
        if self.frame_count >= 16 and self.kind == "sprite":
            self._show_frame(index * 4)

    def _play_toggled(self, playing):
        if playing and self.kind == "sprite" and self.frame_count >= 16 and self.isVisible():
            if self.current_frame >= 16:
                self._show_frame(self.direction.currentIndex() * 4)
            self.timer.start(round(1000 / self.speed.value()))
            self.play_button.setText("Pause")
            self.play_button.setAccessibleName("Pause walking animation")
        else:
            self.stop_playback()

    def advance_frame(self):
        if self.kind == "sprite" and self.frame_count >= 16:
            row_start = self.direction.currentIndex() * 4
            self._show_frame(row_start + (self.current_frame - row_start + 1) % 4)

    def stop_playback(self):
        self.timer.stop()
        self.play_button.blockSignals(True)
        self.play_button.setChecked(False)
        self.play_button.blockSignals(False)
        self.play_button.setText("Play")
        self.play_button.setAccessibleName("Play walking animation")

    def hideEvent(self, event):
        self.stop_playback()
        super().hideEvent(event)

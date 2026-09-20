"""Optional, read-only artwork contact sheets and synchronized comparisons."""

from math import ceil
from pathlib import Path

from PySide6.QtCore import QIODevice, QRect, QSize, Qt, QSaveFile, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QMenu,
    QScrollArea, QSpinBox, QSplitter, QTabBar, QVBoxLayout, QWidget,
)

from pixelheart_core.artwork import ArtworkValidationError
from pixelheart_core.artwork_review import load_review_sheet, frame_title, render_artwork_review_html
from .artwork_templates import ArtworkTemplateDialog
from .widgets import button, label


class FramePairs(QWidget):
    """Paint only visible cards; extra frames do not create hundreds of widgets."""

    selected = Signal(int)

    def __init__(self, *, detail=False, parent=None):
        super().__init__(parent)
        self.detail = detail
        self.sheet = self.reference = None
        self.pixmaps = {}
        self.zoom = 6
        self.background = "checker"
        self.selected_index = 0
        self.paired = False
        self.frame_count = 0
        self.grid_columns = 1
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Selected artwork comparison" if detail else "Artwork frame contact sheet")

    def set_sheets(self, sheet, reference):
        self.sheet, self.reference = sheet, reference
        self.pixmaps = {}
        for item in (sheet, reference):
            if item is not None:
                pixmap = QPixmap()
                pixmap.loadFromData(item.png, "PNG")
                self.pixmaps[id(item)] = pixmap
        self.frame_count = max((item.frame_count for item in (sheet, reference) if item), default=0)
        self.selected_index = min(self.selected_index, max(0, self.frame_count - 1))
        self.reflow()

    def frame_image(self, sheet, index, span=1):
        if (sheet is None or index < 0 or index + span > sheet.frame_count
                or index % sheet.columns + span > sheet.columns):
            return QPixmap()
        x, y, width, height = sheet.frame_rect(index)
        return self.pixmaps[id(sheet)].copy(x, y, width * span, height)

    def card_size(self):
        sheet = self.sheet or self.reference
        width, height = sheet.display_size if sheet else (16, 32)
        span = 2 if self.detail and self.paired else 1
        panel_width = max(108, width * self.zoom * span + 16)
        sides = 2 if self.reference else 1
        return QSize(panel_width * sides + 28, height * self.zoom + 102)

    def reflow(self):
        size = self.card_size()
        self.setMinimumWidth(size.width())
        self.grid_columns = 1 if self.detail else max(1, self.width() // size.width())
        count = 1 if self.detail or not self.frame_count else self.frame_count
        self.setMinimumHeight(ceil(count / self.grid_columns) * size.height())
        self.updateGeometry()
        self.update()

    def resizeEvent(self, event):
        self.reflow()
        super().resizeEvent(event)

    def frame_rect(self, index):
        size = self.card_size()
        position = 0 if self.detail else index
        return QRect(position % self.grid_columns * size.width(), position // self.grid_columns * size.height(),
                     size.width(), size.height()).adjusted(5, 5, -5, -5)

    def draw_stage(self, painter, bounds, sheet, index, span):
        painter.save()
        painter.setClipRect(bounds)
        painter.fillRect(bounds, QColor({"light": "#e7ece5", "dark": "#172630", "checker": "#263945"}[self.background]))
        if self.background == "checker":
            for y in range(bounds.top(), bounds.bottom() + 1, 10):
                for x in range(bounds.left(), bounds.right() + 1, 10):
                    if ((x - bounds.left()) // 10 + (y - bounds.top()) // 10) % 2:
                        painter.fillRect(x, y, 10, 10, QColor("#334c59"))
        image = self.frame_image(sheet, index, span)
        if not image.isNull():
            width, height = sheet.display_size
            target = QRect(0, 0, width * self.zoom * span, height * self.zoom)
            target.moveCenter(bounds.center())
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            painter.drawPixmap(target, image)
            empty = all(n in sheet.blank_frames for n in range(index, index + span))
            message = "Transparent" if empty else ""
        else:
            message = "No matching pair" if span > 1 else "No matching frame"
        if message:
            painter.setPen(QColor("#394955" if self.background == "light" else "#edf4f7"))
            painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, message)
        painter.restore()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setClipRect(event.rect())
        if not self.frame_count:
            painter.setPen(QColor("#796b56"))
            painter.drawText(self.rect().adjusted(20, 20, -20, -20), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                             "Upload a complete sheet in Artwork to review its frames.")
            return
        size = self.card_size()
        start = max(0, event.rect().top() // size.height() * self.grid_columns)
        stop = min(self.frame_count, (event.rect().bottom() // size.height() + 1) * self.grid_columns)
        indices = [self.selected_index] if self.detail else range(start, stop)
        sheet = self.sheet or self.reference
        span = 2 if self.detail and self.paired else 1
        for index in indices:
            rect = self.frame_rect(index)
            painter.setBrush(QColor("#fffbf2"))
            painter.setPen(QPen(QColor("#718558" if index == self.selected_index else "#d9ceba"), 2 if index == self.selected_index else 1))
            painter.drawRoundedRect(rect, 7, 7)
            painter.setPen(QColor("#443c30"))
            heading = f"{index:02}" + (f" + {index + 1}" if span == 2 else "") + " · " + frame_title(sheet.kind, index)
            painter.drawText(rect.adjusted(12, 7, -12, -7), Qt.AlignmentFlag.AlignTop,
                             painter.fontMetrics().elidedText(heading, Qt.TextElideMode.ElideRight, rect.width() - 24))
            sides = [(self.sheet, "Your artwork")]
            if self.reference:
                sides.append((self.reference, "Reference"))
            panel_width = (rect.width() - 20) // len(sides)
            for side, (item, fallback) in enumerate(sides):
                x = rect.left() + 10 + side * panel_width
                name = item.label if item else fallback
                painter.drawText(QRect(x, rect.top() + 30, panel_width - 8, 20), Qt.AlignmentFlag.AlignLeft,
                                 painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, panel_width - 8))
                stage = QRect(x, rect.top() + 55, panel_width - 8, size.height() - 99)
                self.draw_stage(painter, stage, item, index, span)
            painter.setPen(QColor("#796b56"))
            painter.drawText(QRect(rect.left() + 12, rect.bottom() - 26, rect.width() - 24, 20),
                             Qt.AlignmentFlag.AlignLeft, f"Row {index // sheet.columns + 1} · column {index % sheet.columns + 1}")

    def mousePressEvent(self, event):
        self.setFocus()
        if not self.detail and event.button() == Qt.MouseButton.LeftButton:
            size = self.card_size()
            point = event.position().toPoint()
            column = point.x() // size.width()
            index = point.y() // size.height() * self.grid_columns + column
            if column < self.grid_columns and 0 <= index < self.frame_count:
                self.selected.emit(index)
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        delta = {Qt.Key.Key_Left: -1, Qt.Key.Key_Right: 1,
                 Qt.Key.Key_Up: -self.grid_columns, Qt.Key.Key_Down: self.grid_columns}.get(event.key())
        if delta is not None and self.frame_count:
            self.selected.emit(max(0, min(self.frame_count - 1, self.selected_index + delta)))
            event.accept()
        else:
            super().keyPressEvent(event)


class ArtworkReviewDialog(QDialog):
    """Review immutable image snapshots. References never enter the project."""

    def __init__(self, title, sheets, *, appearance="Default", originals=None, notes=(), parent=None):
        super().__init__(parent)
        self.title, self.appearance = title, appearance
        self.sheets, self.references = dict(sheets), {}
        self.originals = dict(originals or {})
        self.notes = tuple(notes)
        self.setWindowTitle(f"Artwork review · {title}")
        self.resize(1120, 840)
        self.setMinimumSize(780, 640)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 18)
        root.addWidget(label(f"{title} · Artwork review", "title", True))
        root.addWidget(label(f"{appearance} appearance · Inspect every expression and pose at matching pixel scale.", "muted", True))
        self.kind_tabs = QTabBar()
        self.kind_tabs.addTab("Portraits")
        self.kind_tabs.addTab("Sprites")
        self.kind_tabs.setAccessibleName("Artwork review sheet type")
        root.addWidget(self.kind_tabs)
        self.summary = label("", "muted", True)
        root.addWidget(self.summary)
        controls = QHBoxLayout()
        controls.addWidget(label("Pixel scale"))
        self.zoom = QComboBox()
        self.zoom.setAccessibleName("Review pixel scale")
        for zoom in (1, 2, 4, 6, 8):
            self.zoom.addItem(f"{zoom}×" + (" · game size" if zoom == 1 else ""), zoom)
        controls.addWidget(self.zoom)
        self.background = QComboBox()
        self.background.setAccessibleName("Review background")
        for name, value in (("Checkerboard", "checker"), ("Light", "light"), ("Dark", "dark")):
            self.background.addItem(name, value)
        controls.addWidget(self.background)
        controls.addStretch()
        controls.addWidget(label("Frame"))
        self.frame = QSpinBox()
        self.frame.setAccessibleName("Review frame index")
        controls.addWidget(self.frame)
        self.reference_button = button("Compare with…", lambda: None)
        menu = QMenu(self.reference_button)
        menu.addAction("Choose reference PNG…", self.choose_reference)
        self.original_action = menu.addAction("Original upload", self.compare_original)
        menu.addAction("From my game…", self.compare_game)
        self.clear_action = menu.addAction("Clear reference", self.clear_reference)
        self.reference_button.setMenu(menu)
        controls.addWidget(self.reference_button)
        root.addLayout(controls)
        split = QSplitter()
        self.grid = FramePairs()
        self.grid_scroll = QScrollArea()
        self.grid_scroll.setWidgetResizable(True)
        self.grid_scroll.setWidget(self.grid)
        split.addWidget(self.grid_scroll)
        inspector = QWidget()
        detail_layout = QVBoxLayout(inspector)
        detail_layout.setContentsMargins(12, 0, 0, 0)
        detail_layout.addWidget(label("Selected frame", "sectionTitle"))
        self.detail = FramePairs(detail=True)
        detail_scroll = QScrollArea()
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setWidget(self.detail)
        detail_layout.addWidget(detail_scroll, 1)
        self.paired = QCheckBox("Join next cell")
        self.paired.setToolTip("Inspect a two-cell prop or pose without changing the sheet. Pairs cannot cross a row.")
        detail_layout.addWidget(self.paired)
        self.walk_controls = QWidget()
        walk = QHBoxLayout(self.walk_controls)
        walk.setContentsMargins(0, 0, 0, 0)
        self.direction = QComboBox()
        self.direction.addItems(("Down", "Right", "Up", "Left"))
        self.direction.setAccessibleName("Review walking direction")
        walk.addWidget(self.direction)
        self.play_button = button("Play walk", self.toggle_play)
        self.play_button.setCheckable(True)
        walk.addWidget(self.play_button)
        self.speed = QSpinBox()
        self.speed.setRange(1, 12)
        self.speed.setValue(6)
        self.speed.setSuffix(" fps")
        self.speed.setAccessibleName("Review walking speed")
        walk.addWidget(self.speed)
        detail_layout.addWidget(self.walk_controls)
        self.detail_info = label("", "hint", True)
        detail_layout.addWidget(self.detail_info)
        split.addWidget(inspector)
        split.setSizes([690, 380])
        root.addWidget(split, 1)
        self.status = label("", "notice", True)
        root.addWidget(self.status)
        root.addWidget(label("Frames count from 0. Compare by position; custom pose layouts may differ. Walking playback previews the first four rows and does not test game behavior.", "hint", True))
        footer = QHBoxLayout()
        self.save_button = button("Save HTML review…", self.save_html, "primary")
        footer.addWidget(self.save_button)
        footer.addStretch()
        footer.addWidget(button("Close", self.accept, "quiet"))
        root.addLayout(footer)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.advance_frame)
        self.kind_tabs.currentChanged.connect(self.refresh_kind)
        self.zoom.currentIndexChanged.connect(self.update_display)
        self.background.currentIndexChanged.connect(self.update_display)
        self.frame.valueChanged.connect(self.select_frame)
        self.grid.selected.connect(self.select_frame)
        self.detail.selected.connect(self.select_frame)
        self.paired.toggled.connect(self.pair_changed)
        self.direction.currentIndexChanged.connect(lambda index: self.select_frame(index * 4))
        self.speed.valueChanged.connect(lambda value: self.timer.setInterval(round(1000 / value)))
        self.set_kind("sprite" if "sprite" in sheets else "portrait")

    @property
    def kind(self):
        return "sprite" if self.kind_tabs.currentIndex() == 1 else "portrait"

    def set_kind(self, kind):
        self.kind_tabs.blockSignals(True)
        self.kind_tabs.setCurrentIndex(1 if kind == "sprite" else 0)
        self.kind_tabs.blockSignals(False)
        self.refresh_kind()

    def refresh_kind(self, *_):
        self.stop_playback()
        self.paired.setChecked(False)
        self.paired.setVisible(self.kind == "sprite")
        self.walk_controls.setVisible(self.kind == "sprite")
        sheet, reference = self.sheets.get(self.kind), self.references.get(self.kind)
        for canvas in (self.grid, self.detail):
            canvas.set_sheets(sheet, reference)
        self.frame.setRange(0, max(0, self.grid.frame_count - 1))
        self.frame.setEnabled(bool(self.grid.frame_count))
        self.zoom.setCurrentIndex(self.zoom.findData(6 if self.kind == "sprite" else 2))
        self.update_display()
        self.select_frame(min(self.frame.value(), max(0, self.grid.frame_count - 1)))
        self.original_action.setEnabled(self.kind in self.originals)
        self.clear_action.setEnabled(reference is not None)
        self.save_button.setEnabled(bool(self.sheets or self.references))
        self.walk_controls.setEnabled(bool(sheet and sheet.frame_count >= 16))
        descriptions, warnings = [], list(self.notes)
        for item in (sheet, reference):
            if item:
                descriptions.append(f"{item.label}: {item.width} × {item.height} px · {item.frame_count} frames · {item.frame_width} × {item.frame_height} per frame")
                warnings.extend(item.warnings)
        self.summary.setText("\n".join(descriptions) or "No sheet available for this appearance yet.")
        self.status.setText("\n".join(warnings))
        self.status.setVisible(bool(warnings))

    def update_display(self, *_):
        for canvas in (self.grid, self.detail):
            canvas.zoom = self.zoom.currentData() or 1
            canvas.background = self.background.currentData()
            canvas.reflow()

    def select_frame(self, index):
        self.stop_playback()
        self.show_frame(index)

    def show_frame(self, index):
        if not 0 <= index < self.grid.frame_count:
            return
        self.frame.blockSignals(True)
        self.frame.setValue(index)
        self.frame.blockSignals(False)
        for canvas in (self.grid, self.detail):
            canvas.selected_index = index
            canvas.update()
        self.grid_scroll.ensureVisible(self.grid.frame_rect(index).center().x(), self.grid.frame_rect(index).center().y(), 10, 10)
        self.detail_info.setText(f"Frame {index} · " + frame_title(self.kind, index) +
                                 ("\nAdjacent cells are shown together; no pixels are edited." if self.paired.isChecked() else ""))

    def pair_changed(self, checked):
        self.stop_playback()
        self.detail.paired = checked
        self.detail.reflow()
        self.show_frame(self.frame.value())

    def toggle_play(self, checked):
        sheet = self.sheets.get(self.kind)
        if checked and self.kind == "sprite" and sheet and sheet.frame_count >= 16 and self.isVisible():
            self.paired.setChecked(False)
            self.play_button.setChecked(True)
            self.play_button.setText("Pause")
            self.show_frame(self.direction.currentIndex() * 4)
            self.timer.start(round(1000 / self.speed.value()))
        else:
            self.stop_playback()

    def stop_playback(self):
        self.timer.stop()
        self.play_button.setChecked(False)
        self.play_button.setText("Play walk")

    def advance_frame(self):
        base = self.direction.currentIndex() * 4
        self.show_frame(base + (self.frame.value() - base + 1) % 4)

    def hideEvent(self, event):
        self.stop_playback()
        super().hideEvent(event)

    def done(self, result):
        self.stop_playback()
        super().done(result)

    def load_reference(self, kind, path, label=None):
        try:
            sheet = load_review_sheet(path, kind, label or Path(path).stem)
        except (ArtworkValidationError, OSError, ValueError) as exc:
            self.status.setText(f"Could not load reference: {exc}" + (" Previous reference kept." if kind in self.references else ""))
            self.status.show()
            return False
        self.references[kind] = sheet
        self.refresh_kind()
        return True

    def choose_reference(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a reference " + self.kind + " sheet", "", "PNG artwork (*.png)")
        if path:
            self.load_reference(self.kind, path)

    def compare_original(self):
        path = self.originals.get(self.kind)
        if path:
            self.load_reference(self.kind, path, "Original upload")

    def compare_game(self):
        self.stop_playback()
        dialog = ArtworkTemplateDialog(self.appearance, self, comparison=True)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                loaded = dialog.loaded
                references = {kind: load_review_sheet(loaded[kind], kind, loaded.get("name", "Game reference"))
                              for kind in ("portrait", "sprite")}
                self.references = references
                self.refresh_kind()
        except (ArtworkValidationError, OSError, ValueError) as exc:
            self.status.setText(f"Could not use reference: {exc}")
            self.status.show()
        finally:
            dialog.deleteLater()

    def clear_reference(self):
        self.references.pop(self.kind, None)
        self.refresh_kind()

    def export_to(self, path):
        path = Path(path)
        if path.suffix.lower() not in (".html", ".htm"):
            path = path.with_name(path.name + ".html")
        payload = render_artwork_review_html(self.title, self.sheets, self.references, appearance=self.appearance).encode("utf-8")
        output = QSaveFile(str(path))
        if not output.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(output.errorString())
        if output.write(payload) != len(payload):
            output.cancelWriting()
            raise OSError(output.errorString())
        if not output.commit():
            raise OSError(output.errorString())
        return path

    def save_html(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save standalone artwork review", "artwork-review.html", "HTML review (*.html)")
        if not path:
            return
        try:
            self.export_to(path)
            self.status.setText("HTML review saved with embedded artwork. It opens offline in a browser.")
        except (OSError, ValueError) as exc:
            self.status.setText(f"Could not save review: {exc}")
        self.status.show()

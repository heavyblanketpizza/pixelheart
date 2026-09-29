"""Paint portraits, sprites and tilesheets pixel by pixel, in layers.

The window edits a ``PixelDocument`` and returns the flattened PNG; callers store
it and its layer file in the project. Nothing is drawn or generated for the
creator: every pixel comes from their own strokes or images they bring in.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QMenuBar,
    QMessageBox, QPushButton, QScrollArea, QSpinBox, QToolButton, QVBoxLayout, QWidget, QCheckBox,
)

from pixelheart_core.pixel_document import PixelError
from pixelheart_core.pixel_sheets import MAX_TILESHEET_SIDE, SheetError, encode_png, open_png, sheet_spec
from pixelheart_core.pixel_tools import LABELS, PixelTools
from .pixel_canvas import ZOOM_LEVELS, PixelCanvas, image_from_qimage, qimage_from_image
from .pixel_panels import TOOL_NAMES, ColorPanel, LayerPanel, PreviewPanel, ToolStrip, format_hex
from .widgets import button, label

__all__ = ["PixelPainterDialog", "choose_tilesheet_size", "image_from_qimage", "qimage_from_image"]

TIP = ("Left button paints, right button paints the second color. Alt-click picks a color, "
       "Shift-click draws a straight line, and Space-drag moves around.")
ROW_NAMES = {"portrait": "expression row", "sprite": "frame row", "tilesheet": "tile row"}


class PixelPainterDialog(QDialog):
    def __init__(self, document, *, kind, title, context="", save_text="Save to project",
                 max_bytes=None, validate=None, references=(), parent=None):
        super().__init__(parent)
        self.document, self.kind = document, kind
        self.tools = PixelTools(document)
        self.max_bytes, self.validate = max_bytes, validate
        self.result_png = None
        self.actions = {}
        self.reference_actions = []
        self._frame_clip = None
        self._clipboard = None
        self._fitted = False
        self.setWindowTitle(f"{title} — Pixelheart")
        self.setMinimumSize(1000, 700)
        self.resize(1280, 860)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 14, 22, 18)
        root.setSpacing(10)
        self.menu_bar = QMenuBar(self)
        self.menu_bar.setNativeMenuBar(False)
        root.setMenuBar(self.menu_bar)
        header = QHBoxLayout()
        header.addWidget(label(title, "profileName"))
        header.addStretch()
        if context:
            header.addWidget(label(context, "muted"))
        root.addLayout(header)
        self.notice = label(TIP, "hint", True)
        self.notice.setAccessibleName("Painter status")
        root.addWidget(self.notice)

        self.canvas = PixelCanvas(document, self.tools)
        self.tool_strip = ToolStrip()
        self.color_panel = ColorPanel(document, self.tools)
        self.layer_panel = LayerPanel(document)
        self.preview = PreviewPanel(document, kind)

        body = QHBoxLayout()
        body.setSpacing(14)
        left = QVBoxLayout()
        left.addWidget(self.tool_strip)
        left.addStretch()
        body.addLayout(left)
        center = QVBoxLayout()
        center.setSpacing(6)
        center.addLayout(self._build_options())
        self.scroll = QScrollArea()
        self.scroll.setObjectName("painterCanvasArea")
        self.scroll.setWidget(self.canvas)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidgetResizable(False)
        center.addWidget(self.scroll, 1)
        center.addLayout(self._build_status())
        body.addLayout(center, 1)
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 6, 0)
        side_layout.setSpacing(16)
        for panel in (self.preview, self.color_panel, self.layer_panel):
            side_layout.addWidget(panel)
        side_layout.addStretch()
        side_scroll = QScrollArea()
        side_scroll.setObjectName("painterSide")
        side_scroll.setWidget(side)
        side_scroll.setWidgetResizable(True)
        side_scroll.setFixedWidth(side.sizeHint().width() + 24)
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.addWidget(side_scroll)
        root.addLayout(body, 1)

        footer = QHBoxLayout()
        footer.addWidget(label("Only visible, non-reference layers become the sheet. Your layers stay in the project for next time.", "hint", True), 1)
        footer.addWidget(button("Cancel", self.reject, "quiet"))
        self.save_button = button(save_text, self.save, "primary")
        footer.addWidget(self.save_button)
        root.addLayout(footer)

        self._build_menus(references)
        # Enter belongs to the painter (placing moved pixels), never to a default button.
        for widget in self.findChildren(QPushButton):
            widget.setAutoDefault(False)
            widget.setDefault(False)
        self.tool_strip.selected.connect(self.set_tool)
        self.canvas.edited.connect(self.sync)
        self.canvas.message.connect(self.show_message)
        self.canvas.colors_changed.connect(self.color_panel.refresh_colors)
        self.canvas.frame_changed.connect(self.frame_changed)
        self.canvas.hovered.connect(self.show_position)
        self.canvas.left.connect(lambda: self.position.setText(""))
        self.canvas.zoom_requested.connect(self.zoom_at)
        self.canvas.pan_requested.connect(self.pan)
        self.color_panel.colors_changed.connect(self.canvas.update)
        self.color_panel.edited.connect(self.sync)
        self.color_panel.message.connect(self.show_message)
        self.layer_panel.edited.connect(self.sync)
        self.layer_panel.message.connect(self.show_message)
        self.set_tool("pencil")
        self.show_message(TIP)
        self.frame_changed(self.canvas.active_frame)

    # Layout ------------------------------------------------------------------

    def _build_options(self):
        row = QHBoxLayout()
        row.setSpacing(12)
        self.brush_label = label("Brush", "hint")
        row.addWidget(self.brush_label)
        self.brush = QSpinBox()
        self.brush.setRange(1, 8)
        self.brush.setSuffix(" px")
        self.brush.setAccessibleName("Brush size")
        self.brush.valueChanged.connect(lambda value: setattr(self.tools, "brush", value))
        row.addWidget(self.brush)
        self.filled = QCheckBox("Filled")
        self.filled.toggled.connect(lambda checked: setattr(self.tools, "filled", checked))
        row.addWidget(self.filled)
        self.contiguous = QCheckBox("Connected area only")
        self.contiguous.setChecked(True)
        self.contiguous.setToolTip("Off: fill every pixel of the clicked color on this layer.")
        self.contiguous.toggled.connect(lambda checked: setattr(self.tools, "contiguous", checked))
        row.addWidget(self.contiguous)
        row.addStretch()
        self.toggle_row = row
        return row

    def _build_status(self):
        row = QHBoxLayout()
        self.position = label("", "hint")
        self.position.setMinimumWidth(260)
        row.addWidget(self.position)
        self.frame_label = label("", "hint")
        row.addWidget(self.frame_label, 1)
        self.zoom = QComboBox()
        self.zoom.setAccessibleName("Zoom")
        for level in ZOOM_LEVELS:
            self.zoom.addItem(f"{level * 100}%", level)
        self.zoom.currentIndexChanged.connect(lambda index: self.set_zoom(self.zoom.itemData(index)))
        row.addWidget(self.zoom)
        return row

    def _action(self, name, text, callback=None, shortcuts=(), *, checkable=False, checked=False, menu=None):
        action = QAction(text, self)
        action.setObjectName("painter_" + name)
        if shortcuts:
            action.setShortcuts([QKeySequence(shortcut) for shortcut in shortcuts])
            action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        if checkable:
            action.setCheckable(True)
            action.setChecked(checked)
            if callback is not None:
                action.toggled.connect(callback)
        elif callback is not None:
            action.triggered.connect(lambda checked=False: callback())
        self.addAction(action)
        if menu is not None:
            menu.addAction(action)
        self.actions[name] = action
        return action

    def _build_menus(self, references):
        bar = self.menu_bar
        edit = bar.addMenu("&Edit")
        self._action("undo", "Undo", self.undo, [QKeySequence.StandardKey.Undo], menu=edit)
        self._action("redo", "Redo", self.redo, ["Ctrl+Shift+Z", "Ctrl+Y"], menu=edit)
        edit.addSeparator()
        self._action("cut", "Cut", self.cut, [QKeySequence.StandardKey.Cut], menu=edit)
        self._action("copy", "Copy", self.copy, [QKeySequence.StandardKey.Copy], menu=edit)
        self._action("paste", "Paste", self.paste, [QKeySequence.StandardKey.Paste], menu=edit)
        self._action("delete", "Delete pixels", self.delete, ["Del", "Backspace"], menu=edit)
        edit.addSeparator()
        self._action("select_all", "Select all", self.select_all, [QKeySequence.StandardKey.SelectAll], menu=edit)
        self._action("deselect", "Deselect", self.deselect, ["Ctrl+D"], menu=edit)
        self._action("place", "Place moved pixels\tEnter", self.place, menu=edit)

        tools = bar.addMenu("&Tools")
        for name, (caption, key) in TOOL_NAMES.items():
            self._action("tool_" + name, caption, lambda n=name: self.set_tool(n), [key], menu=tools)
        tools.addSeparator()
        self._action("swap", "Swap colors", self.color_panel.swap, ["X"], menu=tools)
        self._action("brush_smaller", "Smaller brush", lambda: self.brush.setValue(self.brush.value() - 1), ["["], menu=tools)
        self._action("brush_larger", "Larger brush", lambda: self.brush.setValue(self.brush.value() + 1), ["]"], menu=tools)
        self._action("mirror", "Mirror drawing in each frame", self.set_mirror, ["Shift+M"], checkable=True, menu=tools)

        frame = bar.addMenu("F&rame")
        self._action("select_frame", "Select frame", self.select_frame, ["F"], menu=frame)
        self._action("previous_frame", "Previous frame", lambda: self.step_frame(-1), [","], menu=frame)
        self._action("next_frame", "Next frame", lambda: self.step_frame(1), ["."], menu=frame)
        frame.addSeparator()
        self._action("copy_frame", "Copy frame", self.copy_frame, ["Ctrl+Shift+C"], menu=frame)
        self._action("paste_frame", "Paste into frame", self.paste_frame, ["Ctrl+Shift+V"], menu=frame)
        self._action("flip_horizontal", "Flip horizontally", lambda: self.flip(True), ["Shift+H"], menu=frame)
        self._action("flip_vertical", "Flip vertically", lambda: self.flip(False), ["Shift+V"], menu=frame)
        self._action("clear_frame", "Clear frame", self.clear_frame, menu=frame)
        frame.addSeparator()
        self._action("all_layers", "Frame tools change all layers", checkable=True, menu=frame)

        layer = bar.addMenu("&Layer")
        self._action("new_layer", "New layer", lambda: self.layer_panel.run(self.document.add_layer), ["Ctrl+Shift+N"], menu=layer)
        self._action("duplicate_layer", "Duplicate layer", lambda: self.layer_panel.run(self.document.duplicate_layer), menu=layer)
        self._action("delete_layer", "Delete layer", lambda: self.layer_panel.run(self.document.delete_layer), menu=layer)
        self._action("merge_down", "Merge down", lambda: self.layer_panel.run(self.document.merge_down), ["Ctrl+E"], menu=layer)
        layer.addSeparator()
        self._action("reference_file", "Reference image from a PNG…", self.add_reference_file, menu=layer)
        for caption, loader in references:
            action = QAction(f"Reference: {caption}", self)
            action.triggered.connect(lambda checked=False, c=caption, load=loader: self.add_reference(c, load))
            layer.addAction(action)
            self.reference_actions.append(action)

        sheet = bar.addMenu("&Sheet")
        noun = ROW_NAMES[self.kind]
        self._action("add_row", f"Add {noun}", lambda: self.change_rows(1), menu=sheet)
        self._action("remove_row", f"Remove last {noun}", lambda: self.change_rows(-1), menu=sheet)

        view = bar.addMenu("&View")
        self._action("zoom_in", "Zoom in", lambda: self.step_zoom(1), [QKeySequence.StandardKey.ZoomIn, "Ctrl+="], menu=view)
        self._action("zoom_out", "Zoom out", lambda: self.step_zoom(-1), [QKeySequence.StandardKey.ZoomOut], menu=view)
        self._action("fit", "Fit sheet", self.fit, ["Ctrl+0"], menu=view)
        view.addSeparator()
        self._action("pixel_grid", "Pixel grid", self.set_pixel_grid, checkable=True, checked=True, menu=view)
        self._action("frame_grid", "Frame grid", self.set_frame_grid, checkable=True, checked=True, menu=view)
        self._action("onion", "Onion skin", self.set_onion, ["Shift+O"], checkable=True, menu=view)
        self._action("dark", "Dark background", self.set_dark, checkable=True, menu=view)
        for name in ("mirror", "onion"):
            toggle = QToolButton()
            toggle.setObjectName("painterToggle")
            toggle.setDefaultAction(self.actions[name])
            toggle.setText("Mirror" if name == "mirror" else "Onion skin")
            self.toggle_row.insertWidget(self.toggle_row.count() - 1, toggle)

    # Syncing -----------------------------------------------------------------

    def sync(self):
        """Refresh every view after the document changed."""
        self.canvas.refresh()
        self.layer_panel.refresh()
        self.color_panel.refresh()
        self.preview.refresh()
        self.update_actions()

    def update_actions(self):
        document = self.document
        undo, redo = self.actions["undo"], self.actions["redo"]
        undo.setEnabled(document.can_undo or document.floating)
        redo.setEnabled(document.can_redo)
        undo.setText(f"Undo {document.undo_label}" if document.undo_label else "Undo")
        redo.setText(f"Redo {document.redo_label}" if document.redo_label else "Redo")
        spec = sheet_spec(self.kind, document.width, document.height)
        self.actions["add_row"].setEnabled(spec.can_add_row(document.height))
        self.actions["remove_row"].setEnabled(spec.can_remove_row(document.height))
        framed = document.frame_count > 1
        for name in ("previous_frame", "next_frame", "onion"):
            self.actions[name].setEnabled(framed)
        self.actions["place"].setEnabled(document.floating)
        has_selection = document.selection is not None
        for name in ("cut", "delete", "deselect"):
            self.actions[name].setEnabled(has_selection)
        self.actions["paste_frame"].setEnabled(self._frame_clip is not None)

    def show_message(self, text):
        self.notice.setText(text)

    def run(self, action, *arguments, **keywords):
        """Apply a document command, report refusals, and refresh."""
        self.tools.cancel()
        try:
            result = action(*arguments, **keywords)
        except PixelError as exc:
            self.show_message(str(exc))
            result = None
        self.sync()
        return result

    # Tools -------------------------------------------------------------------

    def set_tool(self, name):
        if self.document.floating and name != "select":
            self.document.drop_floating()
        self.tools.cancel()
        self.tools.tool = name
        self.tool_strip.set_tool(name)
        shapes = name in ("rectangle", "ellipse")
        self.filled.setVisible(shapes)
        self.contiguous.setVisible(name == "fill")
        brushed = name in ("pencil", "eraser", "line")
        self.brush.setVisible(brushed)
        self.brush_label.setVisible(brushed)
        self.show_message(f"{LABELS[name]} · {TIP}")
        self.sync()

    def set_mirror(self, checked):
        self.tools.mirror = checked
        self.show_message("Mirror drawing reflects every stroke inside each frame." if checked else TIP)

    def set_onion(self, checked):
        self.canvas.onion = checked
        self.canvas.update()

    def set_pixel_grid(self, checked):
        self.canvas.pixel_grid = checked
        self.canvas.update()

    def set_frame_grid(self, checked):
        self.canvas.frame_grid = checked
        self.canvas.update()

    def set_dark(self, checked):
        self.canvas.set_scheme("dark" if checked else "light")

    # Frames ------------------------------------------------------------------

    def frame_changed(self, index):
        self.preview.set_frame(index)
        document = self.document
        if document.frame_count > 1:
            column, row = index % document.frame_columns, index // document.frame_columns
            self.frame_label.setText(f"Frame {index} · column {column}, row {row}")
        else:
            self.frame_label.setText(f"{document.width} × {document.height} px")

    def step_frame(self, step):
        count = self.document.frame_count
        if count > 1:
            self.canvas.set_active_frame((self.canvas.active_frame + step) % count)

    def active_frame_box(self):
        return self.document.frame_box(min(self.canvas.active_frame, self.document.frame_count - 1))

    def select_frame(self):
        self.run(self.document.drop_floating)
        self.document.set_selection(self.active_frame_box())
        self.sync()

    def copy_frame(self):
        clip = self.run(self.document.copy_region, self.active_frame_box(), all_layers=self.actions["all_layers"].isChecked())
        if clip is not None:
            self._frame_clip = clip
            self.show_message(f"Copied frame {self.canvas.active_frame}. Choose another frame and Paste into frame.")
            self.update_actions()

    def paste_frame(self):
        if self._frame_clip is None:
            return
        self.run(self.document.paste_region, self._frame_clip, *self.active_frame_box()[:2])

    def flip(self, horizontal):
        document = self.document
        if document.floating:
            self.run(document.flip_floating, horizontal=horizontal)
            return
        box = document.selection or self.active_frame_box()
        self.run(document.flip_region, box, horizontal=horizontal, all_layers=self.actions["all_layers"].isChecked())

    def clear_frame(self):
        self.run(self.document.clear_region, self.active_frame_box(), all_layers=self.actions["all_layers"].isChecked())

    def change_rows(self, step):
        document = self.document
        height = document.height + step * document.frame_size[1]
        self.run(document.resize_height, height)

    # Editing -----------------------------------------------------------------

    def undo(self):
        self.tools.cancel()
        self.document.undo()
        self.sync()

    def redo(self):
        self.tools.cancel()
        self.document.redo()
        self.sync()

    def copy(self):
        document = self.document
        image = document.copy_selection()
        if image is None:
            image = document.active_layer.image.crop(self.active_frame_box())
        self._clipboard = image
        QApplication.clipboard().setImage(qimage_from_image(image))
        self.show_message(f"Copied {image.width} × {image.height} pixels.")

    def cut(self):
        if self.document.selection is None:
            return
        self.copy()
        self.run(self.document.delete_selection)

    def paste(self):
        qimage = QApplication.clipboard().image()
        image = image_from_qimage(qimage) if not qimage.isNull() else self._clipboard
        if image is None:
            self.show_message("Copy some pixels first.")
            return
        document = self.document
        x, y = (document.selection or self.active_frame_box())[:2]
        self.run(document.paste, image, x, y)
        if document.floating:
            self.set_tool("select")
            self.show_message("Drag the pasted pixels into place, then press Enter.")

    def delete(self):
        self.run(self.document.delete_selection)

    def select_all(self):
        self.run(self.document.select_all)

    def deselect(self):
        self.run(self.document.clear_selection)

    def place(self):
        self.run(self.document.drop_floating)

    # References ----------------------------------------------------------------

    def add_reference_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a reference image", "", "PNG images (*.png)")
        if path:
            self.add_reference(Path(path).stem, lambda: (open_png(Path(path).read_bytes()), Path(path).stem))

    def add_reference(self, caption, loader):
        try:
            loaded = loader()
        except (SheetError, ValueError, OSError) as exc:
            self.show_message(f"Could not add the reference: {exc}")
            return
        if not loaded:
            return
        image, name = loaded
        self.run(self.document.add_reference, image, name)
        self.show_message(f"Added {name} as a faded, locked reference. It helps you trace and never reaches the game.")

    # View ----------------------------------------------------------------------

    def set_zoom(self, zoom):
        if zoom is None:
            return
        self.canvas.set_zoom(zoom)
        index = self.zoom.findData(self.canvas.zoom)
        if index >= 0 and index != self.zoom.currentIndex():
            blocked = self.zoom.blockSignals(True)
            self.zoom.setCurrentIndex(index)
            self.zoom.blockSignals(blocked)

    def step_zoom(self, step):
        levels = list(ZOOM_LEVELS)
        current = self.canvas.zoom
        if step > 0:
            larger = [level for level in levels if level > current]
            self.set_zoom(larger[0] if larger else levels[-1])
        else:
            smaller = [level for level in levels if level < current]
            self.set_zoom(smaller[-1] if smaller else levels[0])

    def zoom_at(self, step, position):
        old = self.canvas.zoom
        horizontal, vertical = self.scroll.horizontalScrollBar(), self.scroll.verticalScrollBar()
        viewport_point = self.canvas.mapTo(self.scroll.viewport(), position.toPoint())
        pixel = QPointF(position.x() / old, position.y() / old)
        self.step_zoom(step)
        new = self.canvas.zoom
        horizontal.setValue(int(pixel.x() * new - viewport_point.x()))
        vertical.setValue(int(pixel.y() * new - viewport_point.y()))

    def pan(self, delta):
        horizontal, vertical = self.scroll.horizontalScrollBar(), self.scroll.verticalScrollBar()
        horizontal.setValue(horizontal.value() - int(delta.x()))
        vertical.setValue(vertical.value() - int(delta.y()))

    def fit(self):
        viewport = self.scroll.viewport().size()
        document = self.document
        fitting = [level for level in ZOOM_LEVELS
                   if document.width * level <= viewport.width() - 8 and document.height * level <= viewport.height() - 8]
        if not fitting or fitting[-1] < 3:
            # Tall sheets fit their width so frames stay large enough to paint.
            fitting = [level for level in ZOOM_LEVELS if document.width * level <= viewport.width() - 8] or [1]
        self.set_zoom(fitting[-1])

    def show_position(self, x, y):
        document = self.document
        if not (0 <= x < document.width and 0 <= y < document.height):
            self.position.setText("")
            return
        color = document.pixel_at(x, y)
        shown = "transparent" if color[3] == 0 else format_hex(color)
        self.position.setText(f"X {x} · Y {y} · {shown}")

    def showEvent(self, event):
        super().showEvent(event)
        if not self._fitted:
            self._fitted = True
            self.fit()
            self.canvas.setFocus()

    # Closing -------------------------------------------------------------------

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.document.floating:
                self.place()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            if self.tools.active:
                self.tools.cancel()
                self.sync()
                return
            if self.document.floating:
                self.document.cancel_floating()
                self.sync()
                return
            if self.document.selection is not None:
                self.document.clear_selection()
                self.sync()
                return
        super().keyPressEvent(event)

    def save(self):
        """Flatten, check and accept; the painter stays open when a check fails."""
        self.tools.cancel()
        self.document.drop_floating()
        try:
            payload = encode_png(self.document.flatten(), max_bytes=self.max_bytes)
            if self.validate is not None:
                self.validate(payload)
        except (ValueError, OSError) as exc:
            self.sync()
            self.show_message(str(exc))
            return False
        self.result_png = payload
        self.document.mark_saved()
        self.accept()
        return True

    def reject(self):
        self.tools.cancel()
        if self.document.modified:
            answer = QMessageBox.question(
                self, "Close without saving?",
                "Your painting has changes that aren't saved to the project. Close and lose them?",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Discard:
                return
            if self.document.floating:
                self.document.cancel_floating()
        super().reject()


def choose_tilesheet_size(parent=None):
    """Ask how many 16 × 16 tiles a new sheet holds; returns pixels or None."""
    dialog = QDialog(parent)
    dialog.setWindowTitle("New tilesheet — Pixelheart")
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(22, 20, 22, 18)
    layout.addWidget(label("How big should the new tilesheet be?", "sectionTitle"))
    layout.addWidget(label("Each cell is one 16 × 16 game tile. You can add rows while painting; "
                           "the width stays fixed so tile numbers never shift.", "muted", True))
    form = QFormLayout()
    across, down = QSpinBox(), QSpinBox()
    limit = MAX_TILESHEET_SIDE // 16
    for spin, value in ((across, 8), (down, 8)):
        spin.setRange(1, limit)
        spin.setValue(value)
    across.setAccessibleName("Tiles across")
    down.setAccessibleName("Tiles down")
    form.addRow("Tiles across", across)
    form.addRow("Tiles down", down)
    layout.addLayout(form)
    size = label("", "hint")
    layout.addWidget(size)

    def show_size():
        size.setText(f"{across.value() * 16} × {down.value() * 16} pixels · {across.value() * down.value()} tiles")
    across.valueChanged.connect(show_size)
    down.valueChanged.connect(show_size)
    show_size()
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Start painting")
    buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    try:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return across.value() * 16, down.value() * 16
    finally:
        dialog.deleteLater()

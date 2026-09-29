"""Native furniture catalogue development workspace, separate from room drafts."""
from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLineEdit, QListWidget,
    QListWidgetItem, QSplitter, QFileDialog, QCheckBox, QSizePolicy, QMessageBox,
    QStyledItemDelegate,
)

from pixelheart_core.catalogue_development import (
    CataloguePackError, delete_development_item, discover_development_packs,
    load_development_pack, local_asset,
)
from .catalogue_canvas import CataloguePreviewCanvas
from .widgets import label, button


class _CatalogueRowDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        # The row widget draws the name; retain model text for keyboard search.
        option.text = ""


class CatalogueWorkshop(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.pack = None
        self.current_item = None
        self._paths = []
        self._loading = False
        self._project_file = None
        self._workspace_root = Path(__file__).resolve().parents[1]
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        heading = QHBoxLayout()
        self.packs = QComboBox()
        self.packs.setAccessibleName("Local catalogue development pack")
        self.packs.currentIndexChanged.connect(self._choose_pack)
        heading.addWidget(self.packs, 1)
        heading.addWidget(button("Open development pack…", self.open_dialog, "quiet"))
        heading.addWidget(button("Reload", self.reload, "quiet"))
        root.addLayout(heading)
        self.notice = label("Open a local development pack to compare furniture and preview a farmer in the room.", "hint", True)
        root.addWidget(self.notice)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        browse = QVBoxLayout(left)
        browse.setContentsMargins(0, 0, 8, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find furniture…")
        self.search.setAccessibleName("Search catalogue pieces and vanilla matches")
        self.search.textChanged.connect(self.filter_items)
        browse.addWidget(self.search)
        self.group = QComboBox()
        self.group.setAccessibleName("Furniture group")
        self.group.addItem("All pieces", "")
        self.group.currentIndexChanged.connect(self.filter_items)
        browse.addWidget(self.group)
        self.pieces = QListWidget()
        self.pieces.setItemDelegate(_CatalogueRowDelegate(self.pieces))
        self.pieces.setAccessibleName("Catalogue pieces")
        self.pieces.setIconSize(QSize(40, 40))
        self.pieces.setMinimumWidth(300)
        self.pieces.currentRowChanged.connect(self.select_item)
        browse.addWidget(self.pieces, 1)
        self.count = label("", "hint")
        browse.addWidget(self.count)
        splitter.addWidget(left)
        right = QWidget()
        detail = QVBoxLayout(right)
        detail.setContentsMargins(8, 0, 0, 0)
        self.title = label("Choose a piece", "profileName", True)
        detail.addWidget(self.title)
        self.description = label("", "muted", True)
        detail.addWidget(self.description)
        toolbar = QHBoxLayout()
        self.background = QComboBox()
        self.background.setAccessibleName("Preview background")
        self.background.addItem("Studio background", "studio")
        self.background.currentIndexChanged.connect(self.update_background)
        toolbar.addWidget(self.background)
        self.time_of_day = QComboBox()
        self.time_of_day.setAccessibleName("Preview time of day")
        self.time_of_day.addItem("Day", "day")
        self.time_of_day.addItem("Night", "night")
        self.time_of_day.currentIndexChanged.connect(self.update_environment)
        toolbar.addWidget(self.time_of_day)
        self.scale = QComboBox()
        self.scale.setAccessibleName("Preview pixel scale")
        for value in (1, 2, 3):
            self.scale.addItem(f"{value}× pixels", value)
        self.scale.setCurrentIndex(1)
        self.scale.currentIndexChanged.connect(self.update_scale)
        toolbar.addWidget(self.scale)
        self.farmer = QCheckBox("Show farmer")
        self.farmer.setChecked(True)
        self.farmer.toggled.connect(self.update_interaction)
        toolbar.addWidget(self.farmer)
        self.power = QCheckBox("Lights / fire on")
        self.power.setAccessibleName("Turn furniture lights and fireplaces on")
        self.power.setToolTip("Show the selected furniture's light and fire effects in both previews.")
        self.power.toggled.connect(self.update_environment)
        toolbar.addWidget(self.power)
        toolbar.addStretch()
        detail.addLayout(toolbar)
        previews = QHBoxLayout()
        previews.setSpacing(12)
        self.canvases, self.view_controls, self.names, self.dimensions, self.states = [], [], [], [], []
        self.preview_panels = []
        for title in ("COLLECTION", "VANILLA REFERENCE"):
            panel = QWidget()
            self.preview_panels.append(panel)
            panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
            column = QVBoxLayout(panel)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(6)
            column.addWidget(label(title, "eyebrow"))
            name = label("", "sectionTitle", True)
            column.addWidget(name)
            view = QComboBox()
            view.setAccessibleName(title.title() + " orientation")
            column.addWidget(view)
            canvas = CataloguePreviewCanvas()
            canvas.clicked.connect(self.play)
            column.addWidget(canvas, 1)
            dimensions = label("", "hint", True)
            column.addWidget(dimensions)
            state = label("", "hint", True)
            column.addWidget(state)
            canvas.state_changed.connect(lambda phase, side=len(self.canvases): self.update_state(side, phase))
            self.names.append(name)
            self.view_controls.append(view)
            self.canvases.append(canvas)
            self.dimensions.append(dimensions)
            self.states.append(state)
            view.currentIndexChanged.connect(lambda index, side=len(self.canvases)-1: self.change_view(side, index))
            previews.addWidget(panel, 1)
        detail.addLayout(previews, 1)
        self.match = label("", "muted", True)
        detail.addWidget(self.match)
        playback = QHBoxLayout()
        self.play_button = button("▶  Preview interaction", self.play, "primary")
        playback.addWidget(self.play_button)
        playback.addWidget(button("Stop interaction", self.stop, "quiet"))
        playback.addStretch()
        detail.addLayout(playback)
        detail.addWidget(label("Click either preview to replay the farmer’s interaction. Lighting and fire stay active while switched on.", "hint", True))
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 740])
        root.addWidget(splitter, 1)
        self.update_interaction()

    def refresh(self, project_file=None):
        self._project_file = Path(project_file) if project_file else None
        roots = ([Path(project_file).parent] if project_file else []) + [self._workspace_root / "projects"]
        known = discover_development_packs(roots)
        current = self.pack.path if self.pack else None
        if current and current not in [p for p, _ in known] and current.is_file():
            known.append((current, self.pack.data["title"]))
        self.packs.blockSignals(True)
        self.packs.clear()
        self._paths = [path for path, _ in known]
        for path, title in known:
            self.packs.addItem(title, str(path))
        if current in self._paths:
            self.packs.setCurrentIndex(self._paths.index(current))
        self.packs.blockSignals(False)
        if known and current not in self._paths:
            self.load_pack(known[0][0])
        elif not known:
            self.notice.setText("No development packs found in this project. Open a pack.json to begin.")

    def _choose_pack(self, index):
        if 0 <= index < len(self._paths):
            self.load_pack(self._paths[index])

    def open_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open furniture development pack", str(self._project_file.parent if self._project_file else self._workspace_root / "projects"), "Development pack (pack.json);;JSON (*.json)")
        if path and self.load_pack(path):
            self.refresh(self._project_file)

    def reload(self):
        if self.pack:
            self.load_pack(self.pack.path)
        else:
            self.refresh(self._project_file)

    def load_pack(self, path):
        try:
            candidate = load_development_pack(path)
        except (CataloguePackError, OSError) as exc:
            self.notice.setText(str(exc))
            if self.pack and self.pack.path in self._paths:
                self.packs.blockSignals(True)
                self.packs.setCurrentIndex(self._paths.index(self.pack.path))
                self.packs.blockSignals(False)
            return False
        self._display_pack(candidate)
        return True

    def _display_pack(self, candidate, *, preserve_controls=False, fallback_row=0):
        previous_group = self.group.currentData() if preserve_controls else ""
        previous_background = self.background.currentData() if preserve_controls else None
        self.stop()
        self.pack = candidate
        self.actor = None
        if candidate.actor:
            raw = candidate.actor
            self.actor = {key: value for key, value in raw.items() if key not in {"frames", "idle", "seated", "sleeping", "bed_awake", "root"}}
            for state in ("frames", "idle", "seated"):
                self.actor[state] = {direction: ([QImage(str(local_asset(raw["root"], p))) for p in value]
                                               if isinstance(value, list) else QImage(str(local_asset(raw["root"], value))))
                                     for direction, value in raw.get(state, {}).items()}
            for state in ("sleeping", "bed_awake"):
                if raw.get(state):
                    self.actor[state] = QImage(str(local_asset(raw["root"], raw[state])))
        self._loading = True
        self.group.clear()
        self.group.addItem("All pieces", "")
        for group in dict.fromkeys(item["group"] for item in candidate.data["items"]):
            self.group.addItem(group, group)
        self.group.setCurrentIndex(max(0, self.group.findData(previous_group)))
        self.background.clear()
        self.background.addItem("Studio background", "studio")
        for background in candidate.data.get("backgrounds", []):
            self.background.addItem(background["name"], background["id"])
        if self.background.count() > 1:
            self.background.setCurrentIndex(1)
        if previous_background is not None:
            self.background.setCurrentIndex(max(0, self.background.findData(previous_background)))
        self._loading = False
        self.farmer.setEnabled(self.actor is not None)
        self.notice.setText(f"{candidate.data['title']} · {len(candidate.data['items'])} pieces · Local development pack")
        self.filter_items(fallback_row=fallback_row)

    def delete_item(self, item_id):
        if not self.pack:
            return
        item = next((item for item in self.pack.data["items"] if item["id"] == item_id), None)
        if item is None:
            return
        answer = QMessageBox.question(
            self, "Delete catalogue item",
            f"Delete “{item['name']}” from this local catalogue?\n\n"
            "Its artwork files and exported packs will be kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            candidate = delete_development_item(self.pack.path, item_id)
        except (CataloguePackError, OSError) as exc:
            self.notice.setText(f"Could not delete “{item['name']}”: {exc}")
            return
        self._display_pack(candidate, preserve_controls=True, fallback_row=self.pieces.currentRow())
        self.notice.setText(f"Deleted “{item['name']}” from {candidate.data['title']} · "
                            f"{len(candidate.data['items'])} pieces")

    def filter_items(self, *_, fallback_row=0):
        if self._loading or not self.pack:
            return
        identity = self.current_item["id"] if self.current_item else None
        self.pieces.blockSignals(True)
        self.pieces.clear()
        selected = fallback_row
        query, group = self.search.text().strip().casefold(), self.group.currentData()
        for item in self.pack.data["items"]:
            searchable = " ".join(str(item.get(k, "")) for k in ("name", "description", "group", "note")) + " " + (item.get("vanilla") or {}).get("name", "")
            if (group and item["group"] != group) or (query and query not in searchable.casefold()):
                continue
            row = QListWidgetItem(item["name"])
            row.setData(Qt.ItemDataRole.UserRole, item)
            row.setData(Qt.ItemDataRole.AccessibleTextRole, item["name"])
            pixmap = QPixmap(str(local_asset(self.pack.root, item["collection"]["views"][0]["image"])))
            row.setToolTip(item.get("description", ""))
            row.setSizeHint(QSize(280, 72))
            if item["id"] == identity:
                selected = self.pieces.count()
            self.pieces.addItem(row)
            content = QWidget()
            layout = QHBoxLayout(content)
            layout.setContentsMargins(6, 4, 6, 4)
            layout.setSpacing(8)
            thumbnail = label("")
            thumbnail.setFixedSize(40, 40)
            thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumbnail.setPixmap(pixmap.scaled(40, 40, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation))
            thumbnail.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(thumbnail)
            name = label(item["name"], wrap=True)
            name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            layout.addWidget(name, 1)
            delete = button("Delete", lambda checked=False, identity=item["id"]: self.delete_item(identity), "danger")
            delete.setAccessibleName(f"Delete {item['name']} from catalogue")
            delete.setToolTip(f"Delete {item['name']} from this local catalogue")
            layout.addWidget(delete)
            self.pieces.setItemWidget(row, content)
        self.pieces.blockSignals(False)
        self.count.setText(f"{self.pieces.count()} of {len(self.pack.data['items'])} pieces")
        self.pieces.setCurrentRow(max(0, min(selected, self.pieces.count() - 1)) if self.pieces.count() else -1)
        if not self.pieces.count():
            self.stop()
            self.current_item = None
            empty = not self.pack.data["items"]
            self.title.setText("Catalogue is empty" if empty else "No matching pieces")
            self.description.setText("Open another development pack to browse furniture." if empty
                                     else "Try a different search or furniture group.")
            self.match.clear()
            self._loading = True
            for canvas, control, name, dimensions, state, panel in zip(
                    self.canvases, self.view_controls, self.names, self.dimensions, self.states, self.preview_panels):
                canvas.set_scene({}, self.pack.root)
                control.clear()
                name.clear()
                dimensions.clear()
                state.clear()
                panel.show()
            self._loading = False
            self.farmer.setEnabled(False)
            self.power.setEnabled(False)
            self.time_of_day.setEnabled(False)
            self.play_button.setEnabled(False)

    def select_item(self, index):
        if index < 0 or not self.pack:
            return
        self.stop()
        self.current_item = item = self.pieces.item(index).data(Qt.ItemDataRole.UserRole)
        self.title.setText(item["name"])
        self.description.setText(item.get("description", ""))
        self.match.setText(item.get("match", "") + " · " + item.get("note", ""))
        is_surface=item["collection"]["kind"] in ("wall","floor")
        self.farmer.setEnabled(self.actor is not None and not is_surface)
        self.power.setEnabled(not is_surface)
        self.time_of_day.setEnabled(not is_surface)
        self._loading = True
        background = self._room_image()
        for side_index, key in enumerate(("collection", "vanilla")):
            side, control, canvas = item.get(key), self.view_controls[side_index], self.canvases[side_index]
            self.preview_panels[side_index].setVisible(side is not None)
            if side is None:
                control.clear()
                canvas.set_scene({}, self.pack.root)
                continue
            self.names[side_index].setText(side["name"])
            control.clear()
            for view in side["views"]:
                control.addItem(view["label"])
            canvas.set_scene(side, self.pack.root, None if side["kind"] in ("wall", "floor") else self.actor, background)
            default = item.get("vanilla_default_rotation", 0) if key == "vanilla" else 0
            control.setCurrentIndex(next((n for n, v in enumerate(side["views"]) if v["rotation"] == default), 0))
        self._loading = False
        for index, control in enumerate(self.view_controls):
            self.change_view(index, control.currentIndex())
        self.update_background()
        self.update_scale()
        self.update_interaction()
        self.update_environment()

    def _room_image(self):
        backgrounds = self.pack.data.get("backgrounds", []) if self.pack else []
        selected = next((b for b in backgrounds if b["id"] == self.background.currentData()), backgrounds[0] if backgrounds else None)
        return QImage(str(local_asset(self.pack.root, selected["image"]))) if selected else None

    def change_view(self, side, index):
        if self._loading or not self.current_item or index < 0:
            return
        self.stop()
        definition = self.current_item.get(("collection", "vanilla")[side])
        if definition is None:
            return
        view = definition["views"][index]
        self.canvases[side].set_view(index)
        self._sync_wall_height()
        self.update_state(side, "idle")
        self.dimensions[side].setText(f"{view['width']} × {view['height']} px · {view['footprint'][0]} × {view['footprint'][1]} tiles")

    def _sync_wall_height(self):
        height = max(canvas.recommended_wall_height for canvas in self.canvases)
        for canvas in self.canvases:
            canvas.set_wall_height(height)

    def update_state(self, side, phase):
        if side >= len(self.canvases):
            return
        action = {"walk": "Walk around", "cross": "Walk across", "approach": "Approach",
                  "sit": "Sit", "sleep": "Sleep"}.get(self.canvases[side].action, "Preview")
        self.states[side].setText(f"{action} · {'Ready' if phase == 'idle' else phase.capitalize()}")

    def update_background(self, *_):
        if self._loading:
            return
        for index, canvas in enumerate(self.canvases):
            if self.current_item and self.pack:
                definition = self.current_item.get(("collection", "vanilla")[index])
                if definition is None:
                    continue
                canvas.set_scene(definition, self.pack.root,
                                 None if definition["kind"] in ("wall", "floor") else self.actor, self._room_image())
                canvas.set_view(self.view_controls[index].currentIndex())
            canvas.set_background("studio" if self.background.currentData() == "studio" else "room")
        self._sync_wall_height()
        self.update_environment()

    def update_environment(self, *_):
        if self._loading:
            return
        for canvas in self.canvases:
            canvas.set_time_of_day(self.time_of_day.currentData())
            canvas.set_power(self.power.isChecked())

    def update_scale(self, *_):
        for canvas in self.canvases:
            canvas.set_scale(self.scale.currentData())

    def update_interaction(self, *_):
        is_surface = bool(self.current_item and self.current_item["collection"]["kind"] in ("wall", "floor"))
        enabled = self.farmer.isChecked() and bool(getattr(self, "actor", None)) and not is_surface
        for canvas in self.canvases:
            canvas.set_interaction(enabled)
        self.play_button.setEnabled(enabled and self.current_item is not None)

    def play(self):
        if self.play_button.isEnabled():
            for canvas in self.canvases:
                canvas.play()

    def stop(self):
        for canvas in self.canvases:
            canvas.stop()

    def hideEvent(self, event):
        self.stop()
        super().hideEvent(event)

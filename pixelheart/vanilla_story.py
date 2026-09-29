"""Read-only vanilla story previews and explicit selection of adaptation drafts."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QThread, QTimer, QSignalBlocker
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
    QListWidget, QListWidgetItem, QPlainTextEdit, QSizePolicy, QSplitter,
    QTabWidget, QVBoxLayout, QWidget, QMessageBox,
)

from pixelheart_core.game_scene_assets import content_root, discover_game_root
from pixelheart_core.locations import location_name
from pixelheart_core.projects import new_project
from pixelheart_core.rehearsal import preview_dialogue
from pixelheart_core.scene_preview import resolve_scene_preview
from pixelheart_core.vanilla_story import (
    VANILLA_NPCS, load_vanilla_story, apply_vanilla_story,
    preview_vanilla_initialization, initialize_vanilla_story,
)
from pixelheart_core.game_install import GameInstallError
from .game_connection import game_connection
from .game_import import game_import_settings, LocalGameSourceWidget
from .stage_canvas import StageCanvas, image_from_pixels as _image
from .widgets import button, label
from .story_icons import story_icon


LEGACY_GAME_KEY = "localGame/installationFolder"
CATEGORIES = {"heart_event": "Heart event", "continuation": "Follow-up", "branch": "Branch", "appearance": "Other appearance"}


def _story_counts(counts, *, drafts=False):
    names = (("events", "draft" if drafts else "event"), ("chapters", "chapter"),
             ("relationships", "relationship arc"))
    return " · ".join(f"{counts[key]} {name}{'' if counts[key] == 1 else 's'}" for key, name in names)


def _event_caption(entry):
    hearts = entry.get("hearts")
    threshold = f"{hearts:g} hearts" if isinstance(hearts, (int, float)) else CATEGORIES.get(entry.get("category"), "Relationship scene")
    return threshold + " · " + (location_name(entry.get("location", "")) or "Location unspecified")


def _preview_transcript(transcript, farmer_gender):
    lines = []
    for line in transcript.split("\n"):
        speaker, separator, payload = line.partition(": ")
        prefix = speaker + separator if separator else ""
        lines.append(prefix + preview_dialogue(payload if separator else line, farmer_gender=farmer_gender))
    return "\n".join(lines)


def _game_folder(preferred, exported, cancelled):
    if cancelled():
        return None
    if preferred:
        content_root(preferred)
        return preferred
    if exported:
        try:
            content_root(exported)
            return exported
        except (OSError, ValueError, RuntimeError):
            pass
    found = discover_game_root()
    if cancelled():
        return None
    if found is None:
        raise ValueError("Locate your Stardew Valley installation to read its original events.")
    return str(found)


def _catalog(template_id, preferred, exported, cancelled):
    folder = _game_folder(preferred, exported, cancelled)
    if cancelled():
        return None
    bundle = load_vanilla_story(template_id, folder, cancelled=cancelled)
    return {"game_root": folder, "bundle": bundle}


def _scenery(entry, preferred, exported, farmer_gender, cancelled):
    folder = _game_folder(preferred, exported, cancelled)
    if cancelled():
        return None
    source_event = entry.get("source_event") or {
        "location": entry.get("location", ""),
        "story": {"actors": deepcopy(entry.get("actors", [])), "music": entry.get("music", "none")},
    }
    document = new_project()
    # Never let the selected project NPC's aliases substitute private artwork
    # for an original game actor in this source reference.
    document["character"].update(name="Source reference", internal_name="SourceReference")
    result = resolve_scene_preview(document, None, source_event, game_root=folder, farmer_gender=farmer_gender)
    if cancelled():
        return None
    return {"game_root": folder, "assets": result, "event": source_event}


class _Read(QThread):
    ready = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, token, action, parent):
        super().__init__(parent)
        self.token, self.action = token, action

    def run(self):
        try:
            result = self.action(self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.ready.emit(self.token, result)
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.failed.emit(self.token, str(exc))


class _ReferenceStage(StageCanvas):
    """Keep source positions immutable while allowing camera inspection."""

    def fit(self):
        # This stage shares the dialog with a transcript, so its compact height
        # needs less vertical context than the full authoring workspace.
        xs = [actor.get("x", 0) for actor in self.actors] or [8]
        ys = [actor.get("y", 0) for actor in self.actors] or [6]
        columns, rows = max(18, max(xs) - min(xs) + 6), max(8, max(ys) - min(ys) + 6)
        center_x, center_y = (min(xs) + max(xs) + 1) / 2, (min(ys) + max(ys)) / 2
        self.bounds = (center_x - columns / 2, center_y - rows / 2, columns, rows)
        self.zoom_factor = 1.0
        self._view_changed()

    def move_selected(self, x, y):
        pass

    def _update_accessibility(self):
        description = self.preview_note + ". " + self.source_note
        self.setAccessibleDescription(description + " Original actor positions are read-only.")
        self.setToolTip(description + "\nScroll to zoom. Middle-drag or hold Space and drag to pan.")


class VanillaStoryDialog(QDialog):
    """An isolated preview; accepting returns a selection, never edits a project."""

    def __init__(self, character, parent=None, *, reference=None, mode="initialize"):
        super().__init__(parent)
        self.target_character = deepcopy(character)
        self.reference = deepcopy(reference) if reference is not None else None
        self.bundle = None
        self.entries = []
        self.worker = None
        self.scene_worker = None
        self.closing = False
        self._catalog_token = self._scene_token = 0
        self._pending_catalog = self._pending_scene = None
        self.settings = game_import_settings()
        connection = game_connection()
        # The shared game connection wins; this dialog's older remembered
        # folder is only a fallback for installations it discovered itself.
        self.game_root = connection.folder() if connection.state() == "connected" else self._setting(LEGACY_GAME_KEY)
        self.export_root = self._setting(LocalGameSourceWidget.SETTINGS_KEY)
        self.setWindowTitle("Original vanilla event" if reference is not None else "Story from a vanilla NPC")
        self.resize(1000, 760)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(10)
        header = QHBoxLayout()
        header.addWidget(label("Original scene reference" if reference is not None else "A familiar story, made your own", "sectionTitle"), 1)
        self.mode = QComboBox()
        self.mode.addItem("Load template · replace story", "initialize")
        self.mode.addItem("Add scenes to current story", "append")
        self.mode.setCurrentIndex(max(0, self.mode.findData(mode)))
        self.mode.setAccessibleName("How to use this vanilla story")
        self.mode.setVisible(reference is None)
        header.addWidget(self.mode)
        root.addLayout(header)
        self.mapping = label("", "muted", True)
        self.mapping.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self.mapping)
        source_row = QHBoxLayout()
        self.source = label("Reading local game source…", "hint")
        self.source.setTextFormat(Qt.TextFormat.PlainText)
        self.source.setMinimumWidth(0)
        self.source.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        source_row.addWidget(self.source, 1)
        self.change_source_button = button("Change…", self.change_source, "quiet")
        source_row.addWidget(self.change_source_button)
        root.addLayout(source_row)
        self.status = label("", "notice", True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self.status)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(12)
        self.splitter.setChildrenCollapsible(False)
        selection = QWidget()
        selection_layout = QVBoxLayout(selection)
        selection_layout.setContentsMargins(0, 0, 0, 0)
        self.npc = QComboBox()
        self.npc.setAccessibleName("Vanilla reference NPC")
        for identity, npc in VANILLA_NPCS.items():
            self.npc.addItem(npc["name"], identity)
        selection_layout.addWidget(self.npc)
        self.show_appearances = QCheckBox("Show other appearances")
        self.show_appearances.setToolTip("Optional scenes in which this NPC appears, separate from their own relationship events.")
        selection_layout.addWidget(self.show_appearances)
        self.events = QListWidget()
        self.events.setWordWrap(True)
        self.events.setMinimumWidth(190)
        self.events.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.events.setAccessibleName("Vanilla scenes to use as a template")
        selection_layout.addWidget(self.events, 1)
        choices = QHBoxLayout()
        choices.addWidget(button("Select shown", lambda: self.select_shown(True), "quiet"))
        choices.addWidget(button("Clear", lambda: self.select_shown(False), "quiet"))
        choices.addStretch()
        selection_layout.addLayout(choices)
        self.splitter.addWidget(selection)
        selection.setVisible(reference is None)

        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        self.event_title = label("Choose an event to inspect its original scene.", "sectionTitle", True)
        self.event_title.setTextFormat(Qt.TextFormat.PlainText)
        detail_layout.addWidget(self.event_title)
        self.preview_splitter = QSplitter(Qt.Orientation.Vertical)
        stage = QWidget()
        stage_layout = QVBoxLayout(stage)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        self.canvas = _ReferenceStage()
        self.canvas.set_header_visible(False)
        self.canvas.setAccessibleName("Original event staging, read-only")
        # The compact fit uses eight tile rows; leave room for 16px tiles and
        # the canvas's 30px footer without cropping the cast.
        self.canvas.setMinimumHeight(160)
        stage_layout.addWidget(self.canvas, 1)
        camera = QHBoxLayout()
        self.farmer = QComboBox()
        self.farmer.addItem("Woman farmer", "female")
        self.farmer.addItem("Man farmer", "male")
        self.farmer.setAccessibleName("Farmer gender for source scene and dialogue preview")
        camera.addWidget(self.farmer)
        camera.addStretch()
        camera.addWidget(button("Fit cast", self.canvas.fit, "quiet"))
        camera.addWidget(button("Fit map", self.canvas.fit_map, "quiet"))
        detail_layout.addLayout(camera)
        self.stage_panel = stage
        stage.hide()
        self.preview_splitter.addWidget(stage)
        self.details = QTabWidget()
        self.details.setDocumentMode(True)
        self.texts = {}
        for key, title in (("transcript", "Scene"), ("conditions", "Conditions"), ("warnings", "Adaptation notes"), ("script", "Original script")):
            text = QPlainTextEdit()
            text.setReadOnly(True)
            text.setAccessibleName("Original event " + title.lower())
            self.texts[key] = text
            self.details.addTab(text, story_icon({"transcript": "scene", "conditions": "trigger", "warnings": "aftermath", "script": "storyline"}[key]), title)
        self.preview_splitter.addWidget(self.details)
        self.preview_splitter.setSizes([265, 230])
        detail_layout.addWidget(self.preview_splitter, 1)
        self.splitter.addWidget(detail)
        self.splitter.setSizes([280, 660])
        self.splitter.setStretchFactor(1, 1)
        root.addWidget(self.splitter, 1)

        self.impact = label("", "hint", True)
        self.impact.setTextFormat(Qt.TextFormat.PlainText)
        self.impact.setVisible(reference is None)
        root.addWidget(self.impact)
        self.summary = label("", "muted", True)
        root.addWidget(self.summary)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close if reference is not None else QDialogButtonBox.StandardButton.Cancel)
        self.use_button = self.buttons.addButton("Load template…", QDialogButtonBox.ButtonRole.AcceptRole) if reference is None else None
        if self.use_button is not None:
            self.use_button.setObjectName("primary")
            self.use_button.setEnabled(False)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        self.npc.currentIndexChanged.connect(self.reload)
        self.events.currentRowChanged.connect(self.show_event)
        self.events.itemChanged.connect(self.update_summary)
        self.show_appearances.toggled.connect(self.filter_events)
        self.mode.currentIndexChanged.connect(self.update_summary)
        self.farmer.currentIndexChanged.connect(self.refresh_event)
        QTimer.singleShot(0, self, self.reload)

    def _setting(self, key):
        value = self.settings.value(key, "")
        return value.strip() if isinstance(value, str) else ""

    def _remember_source(self, folder):
        self.game_root = str(folder)
        self.settings.setValue(LEGACY_GAME_KEY, self.game_root)
        connection = game_connection()
        current = connection.install()
        try:
            if current is None or Path(folder).resolve() != current.root:
                connection.set_folder(folder)
        except (GameInstallError, OSError):
            pass
        self.source.setText("Local game · " + Path(self.game_root).name)
        self.source.setToolTip(self.game_root)
        self.change_source_button.setText("Change…")

    def change_source(self):
        folder = QFileDialog.getExistingDirectory(self, "Locate Stardew Valley", self.game_root)
        if folder:
            self.game_root = folder
            self.reload()

    def reload(self, *_):
        if self.closing:
            return
        self._scene_token += 1
        self._pending_scene = None
        if self.scene_worker is not None:
            self.scene_worker.requestInterruption()
        self.stage_panel.hide()
        if self.reference is not None:
            self.entries = [deepcopy(self.reference)]
            self.show_event(0)
            return
        self._catalog_token += 1
        self.bundle = None
        self.entries = []
        self.events.clear()
        for text in self.texts.values():
            text.clear()
        self.mapping.setText(f"{self.npc.currentText()} → {self.target_character.get('name') or 'your character'} · editable adaptation drafts")
        self.status.setText("Reading original relationship events…")
        self._pending_catalog = (self._catalog_token, self.npc.currentData(), self.game_root, self.export_root)
        if self.worker is not None:
            self.worker.requestInterruption()
        else:
            self._start_catalog()
        self.update_summary()

    def _start_catalog(self):
        token, npc, preferred, exported = self._pending_catalog
        self._pending_catalog = None
        self.worker = _Read(token, lambda cancelled: _catalog(npc, preferred, exported, cancelled), self)
        self.worker.ready.connect(self.receive_catalog)
        self.worker.failed.connect(self.catalog_failed)
        self.worker.finished.connect(self.catalog_finished)
        self.worker.start()

    def receive_catalog(self, token, result):
        if self.closing or token != self._catalog_token or result is None:
            return
        self._remember_source(result["game_root"])
        self.bundle = result["bundle"]
        self.entries = self.bundle["events"]
        captions = [_event_caption(entry) for entry in self.entries]
        totals, seen = Counter(captions), Counter()
        with QSignalBlocker(self.events):
            self.events.clear()
            for entry, caption in zip(self.entries, captions):
                hearts = entry.get("hearts")
                threshold = f"{hearts:g} hearts" if isinstance(hearts, (int, float)) else "Relationship scene"
                category = CATEGORIES.get(entry.get("category"), "Relationship scene")
                seen[caption] += 1
                distinction = f" · Scene {seen[caption]}" if totals[caption] > 1 else ""
                optional = " · Optional" if entry.get("optional") else ""
                item = QListWidgetItem(f"{caption}{distinction}\n{category}{optional}")
                item.setData(Qt.ItemDataRole.UserRole, entry["key"])
                item.setToolTip(f"{threshold} · source event {entry.get('event_id', '')}")
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked if entry.get("optional") else Qt.CheckState.Checked)
                self.events.addItem(item)
        self.status.setText("Original scenes stay as references. Imported drafts need adaptation review before export.")
        self.filter_events()
        self.update_summary()

    def catalog_failed(self, token, message):
        if self.closing or token != self._catalog_token:
            return
        self.status.setText("Couldn’t read these events. " + message)
        self.source.setText("Game source unavailable")
        self.change_source_button.setText("Locate game…")

    def catalog_finished(self):
        worker, self.worker = self.worker, None
        if worker is not None:
            worker.deleteLater()
        if self.closing:
            self._finish_close()
        elif self._pending_catalog is not None:
            self._start_catalog()
        else:
            self.update_summary()

    def filter_events(self, *_):
        if self.reference is not None:
            return
        with QSignalBlocker(self.events):
            for index, entry in enumerate(self.entries):
                hidden = entry.get("category") == "appearance" and not self.show_appearances.isChecked()
                item = self.events.item(index)
                item.setHidden(hidden)
                if hidden:
                    item.setCheckState(Qt.CheckState.Unchecked)
        current = self.events.currentItem()
        if current is None or current.isHidden():
            first = next((i for i in range(self.events.count()) if not self.events.item(i).isHidden()), -1)
            self.events.setCurrentRow(first)
        self.update_summary()

    def select_shown(self, selected):
        with QSignalBlocker(self.events):
            for index in range(self.events.count()):
                item = self.events.item(index)
                if not item.isHidden():
                    item.setCheckState(Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked)
        self.update_summary()

    def selected_event_keys(self):
        return [self.events.item(index).data(Qt.ItemDataRole.UserRole) for index in range(self.events.count())
                if self.events.item(index).checkState() == Qt.CheckState.Checked]

    def update_summary(self, *_):
        if self.reference is not None:
            self.summary.setText("Read-only original reference. Your adaptation and project are unchanged.")
            return
        count = len(self.selected_event_keys())
        initialize = self.mode.currentData() == "initialize"
        name = self.npc.currentText()
        self.use_button.setText(f"Load {name} template…" if initialize else "Add selected scenes")
        self.summary.setText(f"{count} {'scene' if count == 1 else 'scenes'} selected · editable drafts")
        enabled = bool(self.bundle and count)
        message = "Select the scenes you want to work with."
        if enabled and initialize:
            try:
                impact = preview_vanilla_initialization(self.target_character, self.bundle, self.selected_event_keys())
                counts = impact["replace_counts"]
                message = (f"Replaces {_story_counts(counts)}. "
                           "Your character, artwork and daily life stay. You’ll review this before loading.")
                if impact["blocked"]:
                    enabled = False
                    issues = impact["issues"]
                    message = "Cannot replace this story: " + " ".join(issue["message"] for issue in issues[:2])
                    if len(issues) > 2:
                        message += f" {len(issues) - 2} more daily-life links need review."
                    message += " You can add scenes instead."
                    self.impact.setToolTip("\n\n".join(issue["message"] for issue in issues))
            except ValueError as exc:
                enabled = False
                message = str(exc)
        elif enabled:
            message = "Adds new scenes and their chapters to your current story. Existing writing stays."
        self.impact.setText(message)
        if enabled or not self.bundle or not count:
            self.impact.setToolTip("")
        self.use_button.setEnabled(enabled and self.worker is None and self.scene_worker is None and not self.closing)

    def refresh_event(self, *_):
        self.show_event(0 if self.reference is not None else self.events.currentRow())

    def show_event(self, index):
        if self.closing or not 0 <= index < len(self.entries):
            return
        entry = self.entries[index]
        reference = entry.get("event", {}).get("story", {}).get("vanilla_source", entry)
        npc = (self.bundle or entry).get("npc", {})
        npc_name = npc.get("name", self.npc.currentText())
        hearts = entry.get("hearts")
        threshold = f"{hearts:g} hearts" if isinstance(hearts, (int, float)) else "No simple heart threshold"
        self.event_title.setText(f"{npc_name} · {_event_caption(entry)}")
        if self.reference is not None:
            self.mapping.setText(f"{npc_name} · original event {entry.get('event_id', '')}")
        transcript = _preview_transcript(entry.get("transcript", ""), self.farmer.currentData())
        self.texts["transcript"].setPlainText(transcript or "This source has no dialogue transcript. Inspect its original script and adaptation notes.")
        self.texts["conditions"].setPlainText(
            f"Location: {location_name(entry.get('location', '')) or 'Unspecified'}\nMinimum friendship: {threshold}\n"
            f"Suggested chapter phase: {entry.get('phase', 'any').replace('_', ' ').capitalize()}\n"
            "Chapter phase is planning guidance. The original key governs source access.\n\n"
            "Original condition key:\n" + entry.get("event_key", ""))
        warnings = entry.get("warnings", [])
        self.texts["warnings"].setPlainText("\n\n".join(warnings) if warnings else "Review the adapted scene, source conditions, and character references before export.")
        asset = reference.get("asset") or entry.get("source", {}).get("asset", "")
        header = f"Source event: {entry.get('event_id', '')}\nAsset: {asset}\nCondition key: {entry.get('event_key', '')}"
        scripts = [header + "\n\n" + entry.get("script", "")]
        for related in reference.get("related_scripts", []):
            scripts.append("Related source script\nAsset: " + related.get("asset", "") +
                           "\nCondition key: " + related.get("event_key", "") +
                           "\n\n" + related.get("script", ""))
        self.texts["script"].setPlainText("\n\n──────────\n\n".join(scripts))
        self._scene_token += 1
        self._pending_scene = (self._scene_token, deepcopy(entry), self.game_root, self.export_root, self.farmer.currentData())
        self.status.setText("Loading original scene artwork…")
        self.stage_panel.hide()
        if self.scene_worker is not None:
            self.scene_worker.requestInterruption()
        else:
            self._start_scene()
        self.update_summary()

    def _start_scene(self):
        token, entry, preferred, exported, gender = self._pending_scene
        self._pending_scene = None
        self.scene_worker = _Read(token, lambda cancelled: _scenery(entry, preferred, exported, gender, cancelled), self)
        self.scene_worker.ready.connect(self.receive_scene)
        self.scene_worker.failed.connect(self.scene_failed)
        self.scene_worker.finished.connect(self.scene_finished)
        self.scene_worker.start()

    def receive_scene(self, token, result):
        if self.closing or token != self._scene_token or result is None:
            return
        self._remember_source(result["game_root"])
        assets, event = result["assets"], result["event"]
        actors = event.get("story", {}).get("actors", [])
        width, height = assets.get("map_size") or (0, 0)
        visible = [actor for actor in actors if 0 <= actor.get("x", -1) < width and 0 <= actor.get("y", -1) < height]
        self.canvas.load(visible, {"farmer": "Farmer"}, fit=bool(visible))
        self.canvas.set_scene_preview(background=_image(assets.get("background")), map_size=assets.get("map_size"),
                                      sprites={name: _image(pixels) for name, pixels in assets.get("sprites", {}).items()},
                                      location_label=assets.get("location_label") or event.get("location") or "Source scene",
                                      source_note=assets.get("source_note", ""), farmer_gender=self.farmer.currentData(),
                                      foreground=_image(assets.get("foreground")))
        if not visible:
            self.canvas.fit_map()
        self.stage_panel.setVisible(assets.get("background") is not None)
        if assets.get("background") is None:
            self.status.setText("The original script is available; map artwork for this scene is unavailable.")
        elif len(visible) < len(actors):
            self.status.setText("Offstage actors are omitted from the map preview. Their original positions remain in the source script.")
        else:
            self.status.setText("Read-only original scene." if self.reference is not None else
                               "Original scenes stay as references. Imported drafts need adaptation review before export.")

    def scene_failed(self, token, message):
        if not self.closing and token == self._scene_token:
            self.status.setText("The original script is available; its map preview could not load. " + message)

    def scene_finished(self):
        worker, self.scene_worker = self.scene_worker, None
        if worker is not None:
            worker.deleteLater()
        if self.closing:
            self._finish_close()
        elif self._pending_scene is not None:
            self._start_scene()
        else:
            self.update_summary()

    def accept(self):
        if self.reference is not None or self.use_button is None or not self.use_button.isEnabled():
            return
        try:
            # Validate the complete selection without changing the project;
            # the workshop applies it to its latest snapshot after acceptance.
            if self.mode.currentData() == "initialize":
                impact = preview_vanilla_initialization(self.target_character, self.bundle, self.selected_event_keys())
                initialize_vanilla_story(self.target_character, self.bundle, self.selected_event_keys())
                if not self.confirm_initialization(impact):
                    return
            else:
                apply_vanilla_story(self.target_character, self.bundle, self.selected_event_keys())
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        super().accept()

    def confirm_initialization(self, impact):
        old, new = impact["replace_counts"], impact["create_counts"]
        name = impact["npc"]["name"]
        warning = QMessageBox(self)
        warning.setIcon(QMessageBox.Icon.Warning)
        warning.setWindowTitle("Replace the current story?")
        warning.setText(f"Start this character’s story from {name}?")
        warning.setInformativeText(
            "Loading a template starts a connected story from the selected scenes. Mixing it with the current story could leave duplicate chapters and conflicting arcs.\n\n"
            f"Replace: {_story_counts(old)}\n"
            f"Load: {_story_counts(new, drafts=True)}\n\n"
            "Keep: character identity, artwork, dialogue, schedule, gifts, daily life and story brief.\n\n"
            "You can undo this change. Review the adapted drafts before export."
        )
        if impact["life_links"]:
            warning.setDetailedText("\n\n".join(impact["warnings"]))
        load = warning.addButton(f"Load {name} template", QMessageBox.ButtonRole.AcceptRole)
        load.setObjectName("primary")
        load.style().polish(load)
        cancel = warning.addButton(QMessageBox.StandardButton.Cancel)
        warning.setDefaultButton(cancel)
        warning.setEscapeButton(cancel)
        warning.exec()
        return warning.clickedButton() is load

    def reject(self):
        self.closing = True
        self._pending_catalog = self._pending_scene = None
        if self.worker is not None or self.scene_worker is not None:
            for worker in (self.worker, self.scene_worker):
                if worker is not None:
                    worker.requestInterruption()
            self.buttons.setEnabled(False)
            self.status.setText("Stopping local preview…")
        self._finish_close()

    def _finish_close(self):
        if self.closing and self.worker is None and self.scene_worker is None:
            super().reject()

    def closeEvent(self, event):
        if self.worker is not None or self.scene_worker is not None:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)


class VanillaSourceDialog(VanillaStoryDialog):
    def __init__(self, reference, parent=None):
        super().__init__({}, parent, reference=reference)

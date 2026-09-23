"""Install the current NPC pack and keep observations from in-game testing."""
from __future__ import annotations

from pathlib import Path
import io
import json
import zipfile

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPlainTextEdit, QSplitter, QFileDialog,
)

from pixelheart_core.playtesting import (
    playtest_cases, record_playtest, record_install, content_fingerprint,
    export_is_current,
)
from .story_page import prose
from .widgets import label, button, card


class PlaytestPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.loading = False
        self.current_test = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        setup, content = card("Set up the game once", "These tools let Stardew Valley load the pack you create here.")
        content.addWidget(label("1. Install SMAPI using its installer and follow the instructions for your operating system.\n2. Download Content Patcher and extract it into the game's Mods folder.\n3. Install any extra required mods listed by your pack, such as Event Repeater for recurring scenes.\n4. Start Stardew Valley through SMAPI. Use a separate test save while you develop the story.", "muted", True))
        links = QHBoxLayout()
        for title, url in (("Get SMAPI", "https://smapi.io/"), ("Get Content Patcher", "https://www.nexusmods.com/stardewvalley/mods/1915"), ("Find Mods & launch guide", "https://stardewvalleywiki.com/Modding:Player_Guide/Getting_Started")):
            links.addWidget(button(title, lambda checked=False, address=url: QDesktopServices.openUrl(QUrl(address)), "quiet"))
        content.addLayout(links)
        self.setup_panel = setup
        self.setup_panel.hide()
        self.setup_toggle = button("▸ Game setup && required tools", lambda: None, "quiet")
        self.setup_toggle.setCheckable(True)
        self.setup_toggle.setAccessibleName("Game setup and required tools")
        self.setup_toggle.toggled.connect(self.toggle_setup)
        export, content = card("Put this version in your game", "Export a version, install it into your chosen Mods folder, and launch Stardew Valley through SMAPI. Testing is recorded separately from exporting.")
        self.export_status = label("No version exported yet.", "muted", True)
        content.addWidget(self.export_status)
        self.dependency_status = label("", "notice", True)
        self.dependency_status.hide()
        content.addWidget(self.dependency_status)
        actions = QHBoxLayout()
        actions.addWidget(button("Check && export…", self.export_mod, "primary"))
        self.install_button = button("Install this version…", self.install_mod)
        actions.addWidget(self.install_button)
        actions.addWidget(button("Open Mods folder", self.open_mods, "quiet"))
        actions.addStretch()
        content.addLayout(actions)
        content.addWidget(label("Choose your Stardew Valley Mods folder. Only a previous installation of this exact Pixelheart pack can be replaced; other mods are kept intact.", "hint", True))
        layout.addWidget(export)
        layout.addWidget(self.setup_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.setup_panel)
        tests, content = card("Try the story as a player", "Follow each instruction in-game and record what happened. Editing the project makes older results out of date.")
        split = QSplitter()
        self.tests = QListWidget()
        self.tests.setWordWrap(True)
        self.tests.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.tests.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tests.setAccessibleName("In-game playtest cases")
        self.tests.setMinimumWidth(180)
        split.addWidget(self.tests)
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        self.test_title = label("Choose a playtest", "sectionTitle", True)
        self.test_instruction = label("", "muted", True)
        self.test_notes = prose("What happened? Record a blocked path, an awkward line, a missing expression, or a result to keep.", 90)
        self.test_notes.setAccessibleName("Playtest observations")
        detail_layout.addWidget(self.test_title)
        detail_layout.addWidget(self.test_instruction)
        detail_layout.addWidget(self.test_notes)
        row = QHBoxLayout()
        self.pass_button = button("Worked as intended", lambda: self.save_test("passed"), "primary")
        self.fail_button = button("Needs a fix", lambda: self.save_test("failed"))
        row.addWidget(self.pass_button)
        row.addWidget(self.fail_button)
        detail_layout.addLayout(row)
        self.fix_button = button("Open related editor →", self.fix_test, "quiet")
        detail_layout.addWidget(self.fix_button)
        split.addWidget(detail)
        split.setSizes([250, 570])
        content.addWidget(split)
        layout.addWidget(tests)
        log, content = card("If something does not work", "Use the in-game instructions and SMAPI's log to connect a problem to a character, scene, map, or missing dependency.")
        content.addWidget(button("Read a SMAPI log…", self.read_log))
        self.log_summary = QPlainTextEdit()
        self.log_summary.setReadOnly(True)
        self.log_summary.setPlaceholderText("Relevant log messages appear here. No log is uploaded or sent anywhere.")
        self.log_summary.setMaximumHeight(160)
        content.addWidget(self.log_summary)
        layout.addWidget(log)
        self.release_status = label("", "notice", True)
        layout.addWidget(self.release_status)
        layout.addStretch()
        self.tests.currentRowChanged.connect(self.select_test)
        self.test_notes.textChanged.connect(self.save_test_notes)

    def toggle_setup(self, expanded):
        self.setup_panel.setVisible(expanded)
        self.setup_toggle.setText(("▾ " if expanded else "▸ ") + "Game setup && required tools")

    def refresh(self):
        self.refresh_testing()

    def refresh_testing(self):
        document = self.window.document
        creator = document.get("creator", {})
        export = creator.get("last_export", {})
        current = content_fingerprint(document)
        revision = export.get("fingerprint", export.get("revision", ""))
        path = export.get("path", "")
        self.export_status.setText((f"Exported: {Path(path).name}\n" + ("This export matches the current project." if revision == current else "The project has changed. Export again before testing this version.")) if path else "No version exported yet. Start with one ready scene and the required artwork.")
        self.install_button.setEnabled(bool(path) and revision == current and Path(path).is_file())
        installation = creator.get("last_install", {})
        dependencies = installation.get("dependencies", [])
        current_install = installation.get("fingerprint") == current
        self.dependency_status.setVisible(bool(dependencies) and current_install)
        if dependencies:
            descriptions = {"found": "FOUND", "missing": "INSTALL", "old": "UPDATE", "review": "CHECK VERSION / DUPLICATES"}
            self.dependency_status.setText("Required mods in the selected Mods folder:\n" + "\n".join(f"{descriptions.get(row['status'], 'CHECK')} · {row['id']}" + (f" · {row['version']}" if row.get('version') else "") + (f" · requires {row['minimum_version']}+" if row.get('minimum_version') else "") for row in dependencies) + "\nLaunch through SMAPI to confirm these mods actually load. This scan reads installed manifests; it does not verify SMAPI or the game version.")
        selected = self.current_test
        self.loading = True
        self.tests.clear()
        self.cases = playtest_cases(document)
        for case in self.cases:
            state = "OUT OF DATE" if case.get("stale") else case["status"].upper()
            item = QListWidgetItem(state + "  ·  " + case["title"])
            item.setData(Qt.ItemDataRole.UserRole, case)
            self.tests.addItem(item)
            if case["id"] == selected:
                self.tests.setCurrentItem(item)
        self.loading = False
        if self.tests.currentRow() < 0 and self.tests.count():
            self.tests.setCurrentRow(0)
        else:
            self.select_test(self.tests.currentRow())
        passed = sum(case["status"] == "passed" and not case.get("stale") for case in self.cases)
        self.release_status.setText(f"{passed} of {len(self.cases)} checks passed for the current content. Exporting and installing do not mark a test as passed. Share the ZIP only after checking its behavior in-game.")

    def select_test(self, index):
        if self.loading:
            return
        self.loading = True
        enabled = 0 <= index < len(getattr(self, "cases", []))
        installed = self.window.document.get("creator", {}).get("last_install", {})
        current_install = installed.get("fingerprint") == content_fingerprint(self.window.document)
        self.pass_button.setEnabled(enabled and current_install)
        self.fail_button.setEnabled(enabled and current_install)
        self.pass_button.setToolTip("Install this revision before recording an in-game result." if not current_install else "Record your own in-game observation.")
        self.fail_button.setToolTip(self.pass_button.toolTip())
        self.test_notes.setEnabled(enabled)
        self.fix_button.setEnabled(enabled)
        if enabled:
            case = self.cases[index]
            self.current_test = case["id"]
            self.test_title.setText(case["title"])
            instructions = case.get("instructions", "")
            self.test_instruction.setText("\n".join(instructions) if isinstance(instructions, list) else instructions)
            self.test_notes.setPlainText(case.get("notes", ""))
        else:
            self.current_test = ""
            self.test_notes.clear()
        self.loading = False

    def save_test(self, status):
        if self.current_test:
            try:
                self.window.document = record_playtest(self.window.document, self.current_test, status, self.test_notes.toPlainText())
                self.window.dirty = True
                self.window.update_title()
                self.refresh_testing()
            except ValueError as exc:
                self.window.show_error("Cannot record this playtest", str(exc))

    def save_test_notes(self):
        if not self.loading and self.current_test:
            case = next((case for case in self.cases if case["id"] == self.current_test), None)
            if case:
                # Observations must survive navigation even before a result is chosen.
                status = "untested" if case.get("stale") else case["status"]
                try:
                    self.window.document = record_playtest(self.window.document, self.current_test, status, self.test_notes.toPlainText())
                    case["notes"] = self.test_notes.toPlainText()
                    self.window.dirty = True
                    self.window.update_title()
                except ValueError as exc:
                    self.window.statusBar().showMessage(str(exc), 8000)

    def fix_test(self):
        case = next((case for case in self.cases if case["id"] == self.current_test), {})
        event_id = case.get("event_id", "")
        if not event_id:
            event_id = next((event["id"] for event in self.window.events.records if event["id"] in self.current_test), "")
        if event_id:
            self.window.open_section("story")
            self.window.story.tabs.setCurrentIndex(0)
            self.window.events.search.clear()
            self.window.events.filter.setCurrentIndex(0)
            for index, event in enumerate(self.window.events.records):
                if event["id"] == event_id:
                    self.window.events.list.setCurrentRow(index)
                    self.window.events.phases.setCurrentIndex(0)
                    break
        elif case.get("section") == "life":
            self.window.open_life_editor("spouse_dialogue")
        else:
            section = case.get("section", "export")
            self.window.open_section(section)
            if section == "export":
                self.window.export_page.tabs.setCurrentIndex(0)

    def export_mod(self):
        if self.window.export_project():
            self.refresh()

    def install_mod(self):
        from pixelheart_core.world import install_archive
        self.window.collect()
        creator = self.window.document.get("creator", {})
        export = creator.get("last_export", {})
        path = Path(export.get("path", ""))
        if export.get("fingerprint", export.get("revision")) != content_fingerprint(self.window.document):
            self.window.show_error("Export this version first", "The saved ZIP is from an earlier version of this project.")
            return
        directory = QFileDialog.getExistingDirectory(self, "Choose your Stardew Valley Mods folder")
        if not directory:
            return
        try:
            payload = path.read_bytes()
            if not export_is_current(self.window.document, payload):
                raise ValueError("This ZIP no longer matches the recorded export. Export the project again before installing.")
            from pixelheart_core.world import exported_mod_id
            from pixelheart_core.installation import dependency_report
            character = self.window.document["character"]
            mod_id = exported_mod_id(character)
            installed = install_archive(payload, directory, mod_id)
            self.window.document = record_install(self.window.document, str(installed["installed_path"]), payload)
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                manifest = json.loads(archive.read(f"[CP] {character['internal_name']}/manifest.json"))
            self.window.document["creator"]["last_install"]["dependencies"] = dependency_report(directory, manifest)
            self.window.dirty = True
            self.window.update_title()
            self.refresh_testing()
            self.window.statusBar().showMessage("Installed. Launch the game through SMAPI, then follow the playtest steps here.", 15000)
        except (ValueError, OSError) as exc:
            self.window.show_error("Could not install this version", str(exc))

    def open_mods(self):
        creator = self.window.document.get("creator", {})
        installation = creator.get("last_install", {})
        path = installation.get("path", "")
        if path and Path(path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).parent)))
        else:
            self.window.statusBar().showMessage("Install an exported version first to remember its Mods folder.", 7000)

    def read_log(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Read a local SMAPI log", "", "Log files (*.txt *.log);;All files (*)")
        if not filename:
            return
        try:
            with Path(filename).open("r", encoding="utf-8", errors="replace") as stream:
                text = stream.read(4 * 1024 * 1024)
            names = [self.window.document["character"].get("internal_name", ""), "pixelheart", "error", "exception", "warn", "dependency"]
            matches = [entry for entry in text.splitlines() if any(name and name.casefold() in entry.casefold() for name in names)]
            self.log_summary.setPlainText("\n".join(matches[-200:]) or "No matching character, warning, or error lines were found in this log. This does not establish that the mod works; complete the in-game checks.")
        except OSError as exc:
            self.log_summary.setPlainText(str(exc))

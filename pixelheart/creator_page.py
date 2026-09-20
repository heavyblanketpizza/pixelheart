"""The beginner's path from a character idea to a tested local mod."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import io
import json
import zipfile

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget, QScrollArea,
    QListWidget, QListWidgetItem, QPlainTextEdit, QDialog, QDialogButtonBox,
    QSplitter, QFileDialog, QComboBox,
)

from pixelheart_core.creator import (
    new_brief, build_proposal, apply_proposal, creator_progress, playtest_cases,
    record_playtest, record_install, content_fingerprint, export_is_current,
)
from pixelheart_core.story import exported_npc_id
from pixelheart_core.world import cast_actor_id
from .editors import line, value, set_value, connect_change
from .story_page import prose, choices
from .widgets import label, button, card


class ProposalDialog(QDialog):
    """Review the actual authored output before touching the open project."""

    def __init__(self, proposal, parent=None):
        super().__init__(parent)
        self.proposal = proposal
        self.setWindowTitle("Your editable story — review before adding")
        self.resize(1040, 760)
        root = QVBoxLayout(self)
        root.addWidget(label("A starting point you can make your own.", "profileName", True))
        summary = proposal.get("summary", "")
        root.addWidget(label(str(summary), "muted", True))
        root.addWidget(label("This is an authored story framework shaped by your choices. Read the actual scenes below; you decide when a scene is ready to enter the game.", "notice", True))
        split = QSplitter()
        self.contents = QListWidget()
        self.contents.setWordWrap(True)
        self.contents.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.contents.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.contents.setMinimumWidth(210)
        self.contents.setAccessibleName("Proposed mod content")
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName("Proposed authored content")
        split.addWidget(self.contents)
        split.addWidget(self.preview)
        split.setSizes([270, 700])
        root.addWidget(split, 1)
        self.entries = []
        document = proposal["document"]
        character = document["character"]
        actor_names = {"$npc": character.get("name", "Your character"), "farmer": "Narration"}
        actor_names.update({exported_npc_id(record["character"]): record["character"]["name"]
                            for record in document.get("world", {}).get("characters", [])})
        actor_names.update({cast_actor_id(record): record["character"]["name"]
                            for record in document.get("world", {}).get("characters", [])})
        self.entries.append(("What will be added", "\n\n".join(
            f"{entry['title']}\n{entry['detail']}" for entry in proposal.get("additions", []))
            or "This framework is already in your project. Your existing edits will be kept."))
        self.entries.append(("The character", "\n\n".join([character.get("name", ""), character.get("tagline", ""), character.get("bio", "")])))
        self.entries.append(("Everyday conversations", "\n\n".join(f"{entry.get('trigger', 'Conversation')}\n{entry.get('text', '')}" for entry in character.get("dialogues", []))))
        self.entries.append(("Gifts and daily routine", "\n\n".join([
            *(f"{taste.title()}: {', '.join(items)}" for taste, items in character.get("gifts", {}).items() if isinstance(items, list)),
            *(f"{stop.get('time', '')} — {stop.get('location', '')}, tile {stop.get('x', 0)}, {stop.get('y', 0)}\n{stop.get('activity', '')}"
              for stop in character.get("schedule", [])),
            "The initial routine uses the home tile. Choose destinations and check the route in-game.",
        ])))
        for event in character.get("events", []):
            story = event.get("story", {})
            lines = [event.get("name", "Untitled"), f"{event.get('hearts', 0)} hearts · {event.get('location', '')}", event.get("description", "")]
            for key in ("premise", "conflict", "outcome"):
                if story.get(key):
                    lines.append(key.title() + ": " + story[key])
            lines.append("THE SCENE")
            for beat in story.get("beats", []):
                actor = actor_names.get(beat.get("actor"), beat.get("actor", ""))
                if beat.get("kind") == "dialogue":
                    lines.append(f"{actor}: {beat.get('text', '')}")
                elif beat.get("kind") == "choice":
                    lines.append("Player choice: " + beat.get("text", ""))
                    for option in beat.get("choices", []):
                        lines.append(f"  • {option.get('label', '')}: {option.get('text', '')} ({option.get('friendship', 0):+d} friendship)")
                else:
                    lines.append(f"[{beat.get('kind', 'action')} · {actor}]")
            self.entries.append((event.get("name", "Chapter"), "\n\n".join(lines)))
        for kind in ("dialogues", "spouse_dialogue", "routines"):
            for entry in character.get("life", {}).get(kind, []):
                status = "Included in the mod" if entry.get("enabled") else "Draft — review and enable in Life & reactions"
                conditions = entry.get("conditions", {})
                event_name = next((event.get("name", "Chapter") for event in character.get("events", []) if event.get("id") == conditions.get("after_event_id")), "")
                situation = ", ".join(str(value) for key, value in conditions.items() if value not in ("", "any", 0) and key != "after_event_id")
                if event_name:
                    situation += (", " if situation else "") + "after " + event_name
                detail = entry.get("text", "") if kind != "routines" else "\n".join(
                    f"{stop.get('time', '')} — {stop.get('location', '')}, tile {stop.get('x', 0)}, {stop.get('y', 0)}"
                    for stop in entry.get("stops", []))
                self.entries.append(("Everyday life · " + entry.get("name", "Conversation"), "\n\n".join([status, situation, detail])))
        for companion in document.get("world", {}).get("characters", []):
            npc = companion["character"]
            self.entries.append(("Supporting cast · " + npc.get("name", "Character"), "\n\n".join([
                npc.get("bio", ""), "This character needs their own portrait and sprite sheets in Cast & locations.",
                *(f"{line.get('trigger', '')}\n{line.get('text', '')}" for line in npc.get("dialogues", [])),
            ])))
        preserved = proposal.get("preserved", [])
        if preserved:
            self.entries.append(("Your existing work", "\n".join(str(item) for item in preserved)))
        self.contents.addItems([title for title, _ in self.entries])
        self.contents.currentRowChanged.connect(lambda index: self.preview.setPlainText(self.entries[index][1]) if index >= 0 else None)
        self.contents.setCurrentRow(0)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        accept = controls.addButton("Add this content to my project", QDialogButtonBox.ButtonRole.AcceptRole)
        accept.setObjectName("primary")
        controls.accepted.connect(self.accept)
        controls.rejected.connect(self.reject)
        root.addWidget(controls)


class CreatorPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.loading = False
        self.current_test = ""
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(16)
        banner, layout = card()
        heading = QHBoxLayout()
        heading.addWidget(label("YOUR IDEA, ONE PLAYABLE CHAPTER AT A TIME", "eyebrow"))
        heading.addStretch()
        self.progress = label("Start with the person you want to meet.", "badge")
        heading.addWidget(self.progress)
        layout.addLayout(heading)
        self.next_action = label("", "muted", True)
        layout.addWidget(self.next_action)
        layout.addWidget(button("Continue my next step →", self.continue_journey, "quiet"), 0, Qt.AlignmentFlag.AlignLeft)
        root.addWidget(banner)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setAccessibleName("Guided mod creation steps")
        root.addWidget(self.tabs, 1)
        self.build_brief()
        self.build_chapters()
        self.build_world()
        self.build_playtest()
        self.tabs.currentChanged.connect(self.refresh)

    def add_step(self, title):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 14, 10, 10)
        layout.setSpacing(16)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(widget)
        self.tabs.addTab(scroll, title.replace("&", "&&"))
        return layout

    def build_brief(self):
        layout = self.add_step("1  Your idea")
        intro, content = card("Who would you love to meet in the valley?", "Start with a person and a reason for them to stay. The framework gives you a complete story to edit, then helps you turn it into a mod.")
        self.fields = {
            "name": line("Their name", 64),
            "concept": prose("For example: a guarded traveler arrives with someone to protect. They need a place to hide, but slowly find a reason to call the valley home.", 100),
            "archetype": choices([("A guarded protector learns to trust", "guarded_guardian"), ("A wandering artist finds somewhere to belong", "wandering_artist"), ("A gentle healer learns to accept help", "gentle_healer")]),
            "motivation": line("What do they want more than anything?", 600),
            "flaw": line("What makes it hard for them to get close?", 600),
            "arrival": line("Why do they come to the valley now?", 600),
            "relationship": choices([("Friendship grows into romance", "romance"), ("A lasting friendship", "friendship")]),
            "companion_name": line("Optional: someone who arrives with them", 64),
        }
        form = QFormLayout()
        form.setSpacing(13)
        form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
        for caption, key in [("Character name", "name"), ("The spark", "concept"), ("Story framework", "archetype"), ("They want…", "motivation"), ("They struggle with…", "flaw"), ("They arrive because…", "arrival"), ("The player's connection", "relationship"), ("A supporting character", "companion_name")]:
            self.fields[key].setAccessibleName(caption)
            form.addRow(caption, self.fields[key])
        content.addLayout(form)
        content.addWidget(label("Your concept stays with the project. The selected framework supplies editable writing, conversations, and linked chapters; it does not interpret arbitrary prose with AI.", "hint", True))
        row = QHBoxLayout()
        row.addWidget(button("Try the guarded traveler example", self.load_example))
        row.addStretch()
        self.proposal_button = button("Preview my story →", self.preview_proposal, "primary")
        row.addWidget(self.proposal_button)
        content.addLayout(row)
        layout.addWidget(intro)
        self.brief_notice = label("", "notice", True)
        self.brief_notice.hide()
        layout.addWidget(self.brief_notice)
        layout.addStretch()
        for widget in self.fields.values():
            connect_change(widget, self.edit_brief)

    def build_chapters(self):
        layout = self.add_step("2  Make it playable")
        progress, content = card("Your next small win", "Get the first meeting working, then develop the rest of the relationship in order.")
        self.tasks = QListWidget()
        self.tasks.setWordWrap(True)
        self.tasks.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.tasks.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tasks.setMinimumHeight(200)
        self.tasks.setMaximumHeight(310)
        self.tasks.setAccessibleName("Creator milestones; activate to continue")
        self.tasks.itemActivated.connect(self.open_task)
        content.addWidget(self.tasks)
        layout.addWidget(progress)
        chapters, content = card("A relationship with a beginning, a middle, and a life afterward", "Each chapter contains an editable scene. Work on one at a time; later chapters can remain drafts while you test the first.")
        self.chapters = QListWidget()
        self.chapters.setWordWrap(True)
        self.chapters.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chapters.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.chapters.setMinimumHeight(210)
        self.chapters.setAccessibleName("Story chapters")
        self.chapters.itemActivated.connect(self.open_chapter)
        content.addWidget(self.chapters)
        row = QHBoxLayout()
        row.addWidget(button("Write this chapter", self.edit_chapter, "primary"))
        row.addWidget(button("Rehearse this chapter", self.rehearse_chapter))
        row.addStretch()
        content.addLayout(row)
        content.addWidget(label("Writing tips: give the player something to notice, something to do, and something that changes. Use a choice when the response should change how the moment ends.", "hint", True))
        layout.addWidget(chapters)
        layout.addStretch()

    def build_world(self):
        layout = self.add_step("3  Give them a life")
        for title, description, actions in [
            ("Somewhere to call home", "Assign an existing place or paint a home interior. Place the resident, connect the entrance, and choose whether to move their home route stops.", [("Assign & design home…", lambda: self.window.open_home_editor())]),
            ("A voice that remembers", "Let dialogue change after a chapter, with friendship, or when the relationship becomes romantic. Choose the situation in everyday language.", [("Edit responsive dialogue", lambda: self.open_editor(8))]),
            ("A routine for every part of their life", "Write rainy-day, seasonal, dating, and married routines. Later chapters can change where they spend their time.", [("Edit daily life", lambda: self.open_editor(8, "routines")), ("Edit the basic routine", lambda: self.open_editor(2))]),
            ("People and places that belong to the story", "Add supporting characters and import custom locations with entrances and exits. Keep their artwork and map dependencies in the portable project.", [("Build the supporting cast and world", lambda: self.open_editor(9))]),
            ("Give the character a face", "Upload your portrait and sprite sheets, or start from an available artwork template. The frame browser shows which expressions and poses your sheet contains.", [("Prepare character artwork", lambda: self.open_editor(5)), ("Choose gift preferences", lambda: self.open_editor(3))]),
        ]:
            panel, content = card(title, description)
            row = QHBoxLayout()
            for caption, callback in actions:
                row.addWidget(button(caption, callback, "primary" if len(actions) == 1 else None))
            row.addStretch()
            content.addLayout(row)
            layout.addWidget(panel)
        layout.addStretch()

    def build_playtest(self):
        layout = self.add_step("4  Play, fix & share")
        setup, content = card("Set up the game once", "These tools let Stardew Valley load the pack you create here.")
        content.addWidget(label("1. Install SMAPI using its installer and follow the instructions for your operating system.\n2. Download Content Patcher and extract it into the game's Mods folder.\n3. Install any extra required mods listed by your pack, such as Event Repeater for recurring scenes.\n4. Start Stardew Valley through SMAPI. Use a separate test save while you develop the story.", "muted", True))
        links = QHBoxLayout()
        for title, url in (("Get SMAPI", "https://smapi.io/"), ("Get Content Patcher", "https://www.nexusmods.com/stardewvalley/mods/1915"), ("Find Mods & launch guide", "https://stardewvalleywiki.com/Modding:Player_Guide/Getting_Started")):
            links.addWidget(button(title, lambda checked=False, address=url: QDesktopServices.openUrl(QUrl(address)), "quiet"))
        content.addLayout(links)
        layout.addWidget(setup)
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
        self.fix_button = button("Open the related scene →", self.fix_test, "quiet")
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

    def load(self, document):
        self.loading = True
        brief = {**new_brief(), **document.get("creator", {}).get("brief", {})}
        for key, widget in self.fields.items():
            set_value(widget, brief.get(key, ""))
        self.loading = False
        self.brief_notice.hide()
        self.refresh()

    def edit_brief(self):
        if self.loading:
            return
        creator = self.window.document.setdefault("creator", {"version": 1})
        creator["brief"] = {**creator.get("brief", {}), **{key: value(widget) for key, widget in self.fields.items()}}
        self.window.content_changed()

    def load_example(self):
        self.loading = True
        example = {"name": "Rowan", "concept": "A guarded traveler arrives with a companion to protect. A temporary refuge slowly becomes a home.", "archetype": "guarded_guardian", "motivation": "Keep a promise and protect the people who depend on them", "flaw": "They believe needing help makes them weak", "arrival": "They are looking for somewhere quiet to begin again", "relationship": "romance", "companion_name": "Pip"}
        for key, content in example.items():
            set_value(self.fields[key], content)
        self.loading = False
        self.edit_brief()

    def preview_proposal(self):
        self.window.collect()
        try:
            proposal = build_proposal(self.window.document, {key: value(widget) for key, widget in self.fields.items()})
            dialog = ProposalDialog(proposal, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.accept_proposal(proposal)
        except (ValueError, OSError) as exc:
            self.brief_notice.setText(str(exc))
            self.brief_notice.show()

    def accept_proposal(self, proposal):
        self.window.collect()
        updated = apply_proposal(self.window.document, proposal)
        self.window.load_document(updated, self.window.project_file)
        self.window.dirty = True
        self.window.update_title()
        self.window.navigation.setCurrentRow(7)
        self.tabs.setCurrentIndex(1)
        self.refresh()

    def refresh(self):
        if self.loading or not hasattr(self, "tests"):
            return
        document = self.window.document
        milestones = creator_progress(document)
        completed = sum(step["status"] == "complete" for step in milestones)
        self.progress.setText(f"{completed} of {len(milestones)} milestones complete")
        next_step = next((step for step in milestones if step["status"] != "complete"), None)
        self.next_action.setText("Next: " + next_step["title"] + " — " + next_step["detail"] if next_step else "Your recorded checks are complete for this version. Keep the project with the release so you can continue its story.")
        selected = self.tasks.currentRow()
        self.tasks.clear()
        for step in milestones:
            prefix = {"complete": "DONE", "blocked": "NEEDS ATTENTION", "in_progress": "IN PROGRESS", "todo": "NEXT"}.get(step["status"], "NEXT")
            item = QListWidgetItem(f"{prefix}  ·  {step['title']}\n{step['detail']}")
            item.setData(Qt.ItemDataRole.UserRole, step)
            self.tasks.addItem(item)
        if self.tasks.count():
            self.tasks.setCurrentRow(max(0, min(selected, self.tasks.count() - 1)))
        chapter_id = self.chapters.currentItem().data(Qt.ItemDataRole.UserRole) if self.chapters.currentItem() else None
        self.chapters.clear()
        for index, event in enumerate(document["character"].get("events", [])):
            stage = event.get("story", {}).get("stage", "idea")
            item = QListWidgetItem(f"{index + 1:02d}  {event.get('name') or 'Untitled'}\n{event.get('hearts', 0)} hearts · {stage.title()} · {event.get('story', {}).get('outcome') or event.get('description') or 'Develop this moment.'}")
            item.setData(Qt.ItemDataRole.UserRole, event["id"])
            self.chapters.addItem(item)
            if event["id"] == chapter_id:
                self.chapters.setCurrentItem(item)
        if self.chapters.count() and self.chapters.currentRow() < 0:
            self.chapters.setCurrentRow(0)
        self.refresh_testing()

    def open_editor(self, index, tab=None):
        self.window.navigation.setCurrentRow(index)
        if index == 8 and tab and hasattr(self.window.life, "tabs"):
            self.window.life.tabs.setCurrentIndex(1 if tab == "routines" else 0)

    def open_task(self, item):
        step = item.data(Qt.ItemDataRole.UserRole)
        if step.get("id") == "first_chapter":
            first = next((event for event in self.window.events.records if event.get("hearts") == 0), None)
            if first:
                chapter = QListWidgetItem()
                chapter.setData(Qt.ItemDataRole.UserRole, first["id"])
                self.open_chapter(chapter)
                return
        destination = step.get("section")
        if step.get("id") in {"install", "playtest", "release"}:
            self.tabs.setCurrentIndex(3)
            return
        if isinstance(destination, str):
            destination = destination.casefold()
        if isinstance(destination, int):
            self.open_editor(destination)
        else:
            mapping = {"creator": 7, "idea": 7, "brief": 7, "identity": 0, "dialogue": 1, "dialogues": 1, "schedule": 2, "gifts": 3, "story": 4, "events": 4, "artwork": 5, "export": 6, "life": 8, "everyday life": 8, "world": 9}
            if destination in {"install", "playtest", "release", "testing"}:
                self.tabs.setCurrentIndex(3)
            else:
                self.open_editor(mapping.get(destination, 7))
                if mapping.get(destination, 7) == 7:
                    self.tabs.setCurrentIndex(0)

    def continue_journey(self):
        self.window.collect()
        step = next((step for step in creator_progress(self.window.document) if step["status"] != "complete"), None)
        if step:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, step)
            self.open_task(item)
        else:
            self.tabs.setCurrentIndex(3)

    def open_chapter(self, item, rehearse=False):
        identity = item.data(Qt.ItemDataRole.UserRole)
        for index, event in enumerate(self.window.events.records):
            if event["id"] == identity:
                self.window.navigation.setCurrentRow(4)
                self.window.story.tabs.setCurrentIndex(0)
                self.window.events.search.clear()
                self.window.events.filter.setCurrentIndex(0)
                self.window.events.list.setCurrentRow(index)
                self.window.events.phases.setCurrentIndex(3 if rehearse else 0)
                break

    def edit_chapter(self):
        if self.chapters.currentItem():
            self.open_chapter(self.chapters.currentItem())

    def rehearse_chapter(self):
        if self.chapters.currentItem():
            self.open_chapter(self.chapters.currentItem(), True)

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
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, event_id)
            self.open_chapter(item)
        else:
            section = case.get("section", "").casefold()
            self.open_editor({"identity": 0, "artwork": 5, "schedule": 2, "dialogue": 1, "world": 9, "life": 8, "everyday life": 8, "gifts": 3, "story": 4}.get(section, 6))

    def export_mod(self):
        if self.window.export_project():
            self.window.navigation.setCurrentRow(7)
            self.tabs.setCurrentIndex(3)
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

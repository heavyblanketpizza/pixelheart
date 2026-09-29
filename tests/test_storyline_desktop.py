"""Chapter authoring must stay separate from playable conditions and identities."""
import os

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from copy import deepcopy
from types import SimpleNamespace
import unittest

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTabWidget, QWidget

from pixelheart.storyline_page import StorylinePage, StoryStarterDialog
from pixelheart.theme import apply_theme
from pixelheart_core.projects import new_project
from pixelheart_core.story import new_event, new_relationship
from pixelheart_core.story_planning import new_chapter, preview_story_starter, storyline_structure_issues
from tests.qt_support import QtTestCase


class WorkshopStub:
    def __init__(self, character):
        self.base = deepcopy(character)
        self.events = SimpleNamespace(records=deepcopy(character.get("events", [])), refresh=lambda *_: None)
        self.relationships = SimpleNamespace(records=deepcopy(character.get("relationships", [])))
        self.opened = []
        self.page = None

    def character(self):
        result = deepcopy(self.base)
        result.update(events=deepcopy(self.events.records), relationships=deepcopy(self.relationships.records))
        if self.page is not None:
            result["storyline"] = self.page.dump()
        return result

    def open_event(self, identity):
        self.opened.append(identity)


class StorylineDesktopTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])
        apply_theme(cls.application)

    def setUp(self):
        self.widgets = []
        self.character = new_project()["character"]
        self.first = new_event(self.character)
        self.first.update(name="A quiet afternoon", hearts=4)
        self.first["story"].update(stage="scene", relationship="dating")
        self.second = new_event(self.character)
        self.second.update(name="The next small step", hearts=8)
        self.second["story"]["previous_event_id"] = self.first["id"]
        self.arc = new_relationship()
        self.arc.update(name="Finding a voice")
        self.arc["story"]["target"] = ""
        self.chapter = new_chapter("First chapter")
        self.chapter.update(event_ids=[self.first["id"]], arc_ids=[self.arc["id"]],
                            purpose="An ordinary shared task.", extension={"keep": True})
        self.character.update(events=[self.first, self.second], relationships=[self.arc],
                              storyline={"brief": {"desire": "Make something of their own.", "custom": [1]},
                                         "chapters": [self.chapter], "custom": {"revision": 3}})

    def tearDown(self):
        for widget in reversed(self.widgets):
            widget.close()
            widget.deleteLater()
        self.application.processEvents()

    def make_page(self, character=None):
        workshop = WorkshopStub(character or self.character)
        page = StorylinePage(workshop)
        workshop.page = page
        self.widgets.append(page)
        page.load(workshop.base)
        # Mirror StoryPage: every authored edit refreshes the references.
        page.changed.connect(page.refresh_context)
        return page, workshop

    def make_dialog(self, character=None, **kwargs):
        dialog = StoryStarterDialog(character or self.character, **kwargs)
        self.widgets.append(dialog)
        return dialog

    def test_planning_edits_and_reordering_preserve_existing_executable_events(self):
        page, workshop = self.make_page()
        original = deepcopy(workshop.events.records)
        page.fields["hearts"].setValue(14)
        page.fields["phase"].setCurrentIndex(page.fields["phase"].findData("married"))
        page.fields["purpose"].setPlainText("A new story purpose.")
        page.add_chapter()
        added = page.current_chapter_id
        page.move_chapter(-1)
        self.assertEqual(page.records[0]["id"], added)
        self.assertEqual(workshop.events.records, original)
        self.assertEqual(page.records[1]["hearts"], 14)
        self.assertEqual(page.records[1]["phase"], "married")
        self.assertIn("Married life · 14 hearts", page.list.item(1).text())

    def test_typing_survives_context_refresh_and_preserves_unknown_metadata(self):
        page, _ = self.make_page()
        page.resize(1024, 800)
        page.show()
        name = page.fields["name"]
        name.setFocus()
        name.selectAll()
        QTest.keyClicks(name, "A title written without losing focus")
        self.assertEqual(page.chapter()["name"], "A title written without losing focus")
        self.assertEqual(name.cursorPosition(), len(name.text()))
        page.brief_fields["motif"].setPlainText("A mended string")
        data = page.dump()
        self.assertEqual(data["custom"], {"revision": 3})
        self.assertEqual(data["brief"]["custom"], [1])
        self.assertEqual(data["chapters"][0]["extension"], {"keep": True})

    def test_load_selection_and_reference_refresh_do_not_emit_authored_changes(self):
        page, _ = self.make_page()
        changes = []
        page.changed.connect(lambda: changes.append(True))
        before = page.dump()
        page.refresh_context()
        page.set_current_chapter(page.records[0]["id"])
        page.load({**self.character, "storyline": before})
        self.assertEqual(changes, [])
        self.assertEqual(page.dump(), before)

    def test_parts_link_reorder_open_and_unlink_without_deleting_events(self):
        page, workshop = self.make_page()
        original = deepcopy(workshop.events.records)
        self.assertEqual(page.loose_events.count(), 1)
        page.link_event()
        self.assertEqual(page.chapter()["event_ids"], [self.first["id"], self.second["id"]])
        page.event_list.setCurrentRow(1)
        page.move_event(-1)
        self.assertEqual(page.chapter()["event_ids"], [self.second["id"], self.first["id"]])
        page.open_selected_event()
        self.assertEqual(workshop.opened[-1], self.second["id"])
        page.unlink_event()
        self.assertEqual(page.chapter()["event_ids"], [self.first["id"]])
        self.assertEqual(workshop.events.records, original)
        self.assertEqual(page.loose_events.count(), 1)

    def test_multiple_arc_associations_and_missing_references_survive_refresh(self):
        other = new_relationship()
        other["name"] = "A place in town"
        self.character["relationships"].append(other)
        self.character["storyline"]["chapters"][0]["arc_ids"].append("missing-arc")
        page, _ = self.make_page()
        self.assertEqual(page.arc_list.count(), 3)
        page.arc_list.item(1).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(set(page.chapter()["arc_ids"]), {self.arc["id"], other["id"], "missing-arc"})
        page.refresh_context()
        self.assertEqual(page.arc_list.item(2).checkState(), Qt.CheckState.Checked)

    def test_remove_and_undo_only_change_the_chapter_organization(self):
        page, workshop = self.make_page()
        before, events = page.dump(), deepcopy(workshop.events.records)
        page.remove_chapter()
        self.assertEqual(page.records, [])
        self.assertEqual(page.loose_events.count(), 2)
        self.assertEqual(workshop.events.records, events)
        page.restore_removed()
        self.assertEqual(page.dump(), before)
        self.assertEqual(page.current_chapter_id, self.chapter["id"])

    def test_duplicate_copies_planning_without_duplicating_playable_parts(self):
        page, workshop = self.make_page()
        original = deepcopy(workshop.events.records)
        page.duplicate_chapter()
        self.assertNotEqual(page.chapter()["id"], self.chapter["id"])
        self.assertEqual(page.chapter()["event_ids"], [])
        self.assertEqual(page.chapter()["arc_ids"], self.chapter["arc_ids"])
        self.assertEqual(page.chapter()["extension"], self.chapter["extension"])
        self.assertEqual(workshop.events.records, original)
        self.assertEqual(storyline_structure_issues(page.dump()), [])

    def test_new_event_uses_suggestions_once_and_never_adds_a_prerequisite(self):
        page, workshop = self.make_page()
        page.fields["hearts"].setValue(10)
        page.fields["phase"].setCurrentIndex(page.fields["phase"].findData("dating"))
        original = deepcopy(workshop.events.records)
        page.add_event_to_chapter()
        created = workshop.events.records[-1]
        self.assertEqual(workshop.events.records[:-1], original)
        self.assertEqual(created["hearts"], 10)
        self.assertEqual(created["story"]["relationship"], "dating")
        self.assertEqual(created["story"]["previous_event_id"], "")
        self.assertEqual(created["story"]["stage"], "outline")
        self.assertIn(created["id"], page.chapter()["event_ids"])
        self.assertEqual(workshop.opened, [created["id"]])
        page.fields["hearts"].setValue(2)
        self.assertEqual(created["hearts"], 10)

    def test_starter_preview_is_editable_selective_and_does_not_mutate_on_cancel(self):
        before = deepcopy(self.character)
        dialog = self.make_dialog(starter="romance", relationship_id=self.arc["id"])
        self.assertEqual(len(dialog.rows), 6)
        for row in dialog.rows[1:]:
            row["include"].setChecked(False)
        dialog.rows[0]["name"].setText("An authored beginning")
        dialog.rows[0]["hearts"].setValue(3)
        edited = dialog.edited_preview()
        self.assertEqual(edited["chapters"][0]["name"], "An authored beginning")
        self.assertEqual(edited["chapters"][0]["hearts"], 3)
        self.assertEqual(len(dialog.selected_chapter_ids()), 1)
        dialog.reject()
        self.assertEqual(self.character, before)

    def test_nonromance_character_starts_with_a_complete_friendship_preview(self):
        self.character["romanceable"] = False
        dialog = self.make_dialog()
        self.assertEqual(dialog.starter.currentData(), "friendship")
        self.assertEqual(len(dialog.rows), 4)
        self.assertTrue(all(row["phase"].currentData() == "friendship" for row in dialog.rows))

    def test_selected_starter_adds_only_missing_drafts_and_keeps_existing_writing(self):
        page, workshop = self.make_page()
        original = deepcopy(workshop.events.records)
        dialog = self.make_dialog(workshop.character(), starter="romance")
        for row in dialog.rows[1:]:
            row["include"].setChecked(False)
        dialog.rows[0]["name"].setText("Our particular beginning")
        dialog.rows[0]["hearts"].setValue(3)
        preview, selected = dialog.edited_preview(), dialog.selected_chapter_ids()
        self.assertTrue(page.apply_starter_preview(preview, selected))
        self.assertEqual(len(page.records), 2)
        self.assertEqual(workshop.events.records[:2], original)
        created = workshop.events.records[-1]
        self.assertEqual(created["name"], "Our particular beginning")
        self.assertEqual(created["hearts"], 3)
        self.assertNotEqual(created["story"]["stage"], "ready")
        self.assertEqual(created["story"]["previous_event_id"], "")
        current = workshop.character()
        self.assertFalse(page.apply_starter_preview(preview, selected))
        self.assertEqual(workshop.character(), current)

    def test_ready_counts_do_not_claim_that_an_event_was_playtested(self):
        self.first["story"]["stage"] = "ready"
        page, _ = self.make_page()
        self.assertIn("1 ready", page.list.item(0).text())
        self.assertIn("1 of 1 parts ready for export", page.event_summary.text())
        self.assertIn("Gameplay testing is recorded in Play in Stardew", page.event_summary.toolTip())

    def test_optional_chapter_prompts_are_collapsed_and_reveal_existing_notes_without_changes(self):
        self.character["storyline"]["chapters"][0].update(before="Keeping a distance", after="Trusting the farmer")
        page, workshop = self.make_page()
        before = workshop.character()
        changes = []
        page.changed.connect(lambda: changes.append(True))
        self.assertTrue(page.prompts_panel.isHidden())
        self.assertIn("2 filled", page.prompts_toggle.text())
        self.assertFalse(page.fields["purpose"].isHidden())
        self.assertFalse(page.event_list.isHidden())
        page.prompts_toggle.setChecked(True)
        self.assertFalse(page.prompts_panel.isHidden())
        self.assertEqual(page.fields["before"].toPlainText(), "Keeping a distance")
        self.assertEqual(workshop.character(), before)
        self.assertEqual(changes, [])

    def test_empty_arc_editor_is_optional_and_issue_navigation_reveals_it(self):
        self.character["storyline"]["chapters"][0]["arc_ids"] = []
        page, _ = self.make_page()
        self.assertTrue(page.connections.isHidden())
        page.open_issue("storyline.chapters.0.arc_ids")
        self.assertTrue(page.arcs_toggle.isChecked())
        self.assertFalse(page.connections.isHidden())
        page.open_issue("storyline.chapters.0.after")
        self.assertTrue(page.prompts_toggle.isChecked())
        self.assertFalse(page.prompts_panel.isHidden())

    def test_missing_event_reference_is_visible_and_can_be_unlinked(self):
        self.character["storyline"]["chapters"][0]["event_ids"] = ["missing-event"]
        page, workshop = self.make_page()
        self.assertIn("Missing event", page.event_list.item(0).text())
        self.assertFalse(page.open_event_button.isEnabled())
        page.open_selected_event()
        self.assertEqual(workshop.opened, [])
        page.unlink_event()
        self.assertEqual(page.chapter()["event_ids"], [])

    def test_issue_navigation_and_narrow_layout_keep_controls_accessible(self):
        page, _ = self.make_page()
        page.resize(650, 900)
        page.show()
        self.application.processEvents()
        self.assertEqual(page.splitter.orientation(), Qt.Orientation.Vertical)
        page.open_issue("storyline.chapters.0.player_role")
        self.assertEqual(page.current_chapter_id, self.chapter["id"])
        page.open_issue("storyline.chapters.0.arc_ids.0")
        self.assertTrue(page.arc_list.hasFocus())
        page.open_issue("storyline.brief.motif")
        self.assertTrue(page.brief_toggle.isChecked())
        self.assertTrue(page.brief_panel.isVisible())

    def test_split_editor_does_not_require_horizontal_scrolling_at_desktop_minimum(self):
        page, _ = self.make_page()
        page.resize(726, 500)
        page.show()
        self.application.processEvents()
        self.assertEqual(page.editor_scroll.horizontalScrollBar().maximum(), 0)
        viewport = page.editor_scroll.viewport()
        self.assertTrue(viewport.rect().contains(page.open_event_button.mapTo(viewport, page.open_event_button.rect().center())))

    def test_new_chapter_from_the_brief_reveals_the_new_chapter_editor(self):
        page, _ = self.make_page()
        page.resize(650, 760)
        page.show()
        page.brief_toggle.click()
        page.brief_fields["motif"].setPlainText("An unfinished tune")
        self.assertFalse(page.splitter.isVisible())
        page.add_chapter()
        self.application.processEvents()
        self.assertFalse(page.brief_toggle.isChecked())
        self.assertTrue(page.chapter_panel.isVisible())
        self.assertEqual(page.chapter()["name"], "New chapter")
        self.assertEqual(page.dump()["brief"]["motif"], "An unfinished tune")

    def test_applying_a_starter_from_the_brief_reveals_its_first_new_chapter(self):
        page, workshop = self.make_page()
        page.show()
        page.brief_toggle.click()
        preview = preview_story_starter(workshop.character(), starter="dating")
        self.assertTrue(page.apply_starter_preview(preview))
        self.application.processEvents()
        self.assertFalse(page.brief_toggle.isChecked())
        self.assertTrue(page.chapter_panel.isVisible())
        self.assertEqual(page.current_chapter_id, preview["chapters"][0]["id"])

    def test_issue_navigation_waits_for_first_tab_layout_and_scrolls_both_editors(self):
        page, _ = self.make_page()
        tabs = QTabWidget()
        self.widgets.append(tabs)
        tabs.addTab(QWidget(), "Another page")
        tabs.addTab(page, "Storyline")
        tabs.resize(650, 760)
        tabs.show()
        self.application.processEvents()
        tabs.setCurrentWidget(page)
        page.open_issue("storyline.chapters.0.player_role")
        self.application.processEvents()
        target = page.fields["player_role"]
        self.assertTrue(page.editor_scroll.viewport().rect().contains(
            target.mapTo(page.editor_scroll.viewport(), target.rect().center())))
        page.open_issue("storyline.brief.motif")
        self.application.processEvents()
        target = page.brief_fields["motif"]
        self.assertTrue(page.brief_scroll.viewport().rect().contains(
            target.mapTo(page.brief_scroll.viewport(), target.rect().center())))


if __name__ == "__main__":
    unittest.main()

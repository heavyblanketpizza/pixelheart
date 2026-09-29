"""Synthetic local story references: asynchronous, read-only until acceptance."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QEventLoop, QSettings, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from pixelheart.vanilla_story import VanillaStoryDialog, VanillaSourceDialog, VANILLA_NPCS
from pixelheart_core.projects import new_project
from tests.qt_support import QtTestCase


def entry(key="1", *, npc="Abigail", hearts=2, optional=False, location="Town"):
    actors = [{"name": npc, "x": 10, "y": 10, "facing": 2},
              {"name": "farmer", "x": 12, "y": 10, "facing": 3}]
    return {
        "key": key, "event_id": key, "event_key": key + f"/f {npc} 500/t 900 1600",
        "location": location, "hearts": hearts, "phase": "friendship",
        "category": "appearance" if optional else "heart_event", "optional": optional,
        "event": {"name": f"A source scene {key}"},
        "source_event": {"location": location, "story": {"actors": actors}},
        "transcript": npc + ": Hello ${sir^ma'am}$ @!$h",
        "warnings": ["Original response branches need adaptation review."],
        "script": f"music/0 0/{npc} 10 10 2 farmer 12 10 3/speak {npc} \"Hello <friend>\"/end",
    }


def bundle(template_id="abigail"):
    name = VANILLA_NPCS[template_id]["name"]
    return {"npc": {"id": template_id, "name": name}, "source": {"provider": "local-game"},
            "events": [entry(npc=name), entry("2", npc=name, hearts=6.8, location="Beach"),
                       entry("3", npc=name, optional=True)]}


class VanillaStoryDialogTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temporary)
        self.settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.settings.setValue("localGame/installationFolder", str(self.root / "Game"))
        self.dialogs = []
        self.release = []
        self.character = new_project()["character"]
        self.character["name"] = "TestNPC test character"
        self.enterContext(patch("pixelheart.vanilla_story.game_import_settings", return_value=self.settings))
        self.enterContext(patch("pixelheart.vanilla_story.content_root", side_effect=lambda folder: Path(folder) / "Content"))
        self.discover = self.enterContext(patch("pixelheart.vanilla_story.discover_game_root", return_value=self.root / "Discovered Game"))
        self.loader = self.enterContext(patch("pixelheart.vanilla_story.load_vanilla_story", side_effect=lambda npc, *_args, **_kwargs: bundle(npc)))
        self.scene_loader = self.enterContext(patch("pixelheart.vanilla_story.resolve_scene_preview", side_effect=self.scene))
        self.apply = self.enterContext(patch("pixelheart.vanilla_story.apply_vanilla_story", side_effect=lambda character, *_args, **_kwargs: deepcopy(character)))
        self.preview = self.enterContext(patch("pixelheart.vanilla_story.preview_vanilla_initialization", side_effect=self.impact))
        self.initialize = self.enterContext(patch("pixelheart.vanilla_story.initialize_vanilla_story", side_effect=lambda character, *_args, **_kwargs: deepcopy(character)))

    def tearDown(self):
        for release in self.release:
            release.set()
        for dialog in self.dialogs:
            dialog.reject()
            self.wait_until(lambda: dialog.worker is None and dialog.scene_worker is None)
            dialog.close()
            dialog.deleteLater()
        self.app.processEvents()

    def wait_until(self, condition, timeout=3000):
        self.app.processEvents()
        if condition():
            return
        loop = QEventLoop()
        timer = QTimer()
        timer.setInterval(5)
        timer.timeout.connect(lambda: loop.quit() if condition() else None)
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        timer.start()
        deadline.start(timeout)
        loop.exec()
        timer.stop()
        deadline.stop()
        self.assertTrue(condition(), "Local preview did not settle before the test deadline")

    @staticmethod
    def scene(document, project_file, event, **kwargs):
        color = "#225566" if event["location"] == "Town" else "#aa7733"
        names = [actor["name"] for actor in event["story"]["actors"]]
        return {"background": Image.new("RGBA", (320, 240), color), "foreground": None,
                "map_size": (20, 15), "location_label": event["location"], "source_note": "Synthetic local fixture",
                "sprites": {name: Image.new("RGBA", (64, 128), "#554477") for name in names}}

    @staticmethod
    def impact(character, source, keys):
        return {"npc": deepcopy(source["npc"]), "selected_count": len(keys),
                "replace_counts": {"events": len(character.get("events", [])),
                                   "chapters": len(character.get("storyline", {}).get("chapters", [])),
                                   "relationships": len(character.get("relationships", []))},
                "create_counts": {"events": len(keys), "chapters": len(keys), "relationships": 1},
                "life_links": [], "blocked": False, "warnings": ["Imported scenes start as drafts."], "issues": []}

    def make_dialog(self, character=None, reference=None, mode="initialize"):
        dialog = VanillaSourceDialog(reference) if reference is not None else VanillaStoryDialog(character or self.character, mode=mode)
        self.dialogs.append(dialog)
        dialog.show()
        return dialog

    def loaded(self, dialog):
        self.wait_until(lambda: (dialog.reference is not None or dialog.bundle is not None)
                        and dialog.worker is None and dialog.scene_worker is None)

    def test_reuses_known_source_and_defaults_to_relationship_events(self):
        before = deepcopy(self.character)
        dialog = self.make_dialog()
        self.loaded(dialog)
        self.assertEqual(dialog.npc.count(), 12)
        self.discover.assert_not_called()
        self.assertEqual(self.loader.call_args.args[1], str(self.root / "Game"))
        self.assertEqual(dialog.selected_event_keys(), ["1", "2"])
        self.assertIn("2 hearts · Pelican Town", dialog.events.item(0).text())
        self.assertNotIn("source scene", dialog.events.item(0).text())
        self.assertIn("source event 1", dialog.events.item(0).toolTip())
        self.assertTrue(dialog.events.item(2).isHidden())
        self.assertIn("Abigail → TestNPC test character", dialog.mapping.text())
        self.assertEqual(dialog.mode.currentData(), "initialize")
        self.assertIn("Load Abigail template", dialog.use_button.text())
        self.assertEqual(self.character, before)
        self.apply.assert_not_called()
        self.initialize.assert_not_called()

    def test_discovers_source_off_the_gui_thread_and_remembers_only_preference(self):
        self.settings.remove("localGame/installationFolder")
        observed = []
        main_thread = threading.get_ident()
        def find():
            observed.append(threading.get_ident())
            return self.root / "Discovered Game"
        self.discover.side_effect = find
        dialog = self.make_dialog()
        self.loaded(dialog)
        self.assertNotEqual(observed, [main_thread])
        self.assertEqual(self.settings.value("localGame/installationFolder"), str(self.root / "Discovered Game"))
        self.assertFalse((self.root / "Discovered Game").exists())

    def test_optional_appearances_are_separate_and_never_silently_selected(self):
        dialog = self.make_dialog()
        self.loaded(dialog)
        dialog.show_appearances.setChecked(True)
        self.assertFalse(dialog.events.item(2).isHidden())
        self.assertEqual(dialog.selected_event_keys(), ["1", "2"])
        dialog.events.item(2).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(dialog.selected_event_keys(), ["1", "2", "3"])
        dialog.show_appearances.setChecked(False)
        self.assertEqual(dialog.selected_event_keys(), ["1", "2"])
        dialog.select_shown(False)
        self.assertFalse(dialog.use_button.isEnabled())

    def test_optional_branch_is_visible_without_enabling_other_appearances(self):
        data = bundle()
        data["events"][2]["category"] = "branch"
        self.loader.side_effect = lambda *_args, **_kwargs: data
        dialog = self.make_dialog()
        self.loaded(dialog)
        self.assertFalse(dialog.events.item(2).isHidden())
        self.assertEqual(dialog.selected_event_keys(), ["1", "2"])
        dialog.events.item(2).setCheckState(Qt.CheckState.Checked)
        dialog.filter_events()
        self.assertEqual(dialog.selected_event_keys(), ["1", "2", "3"])

    def test_exact_threshold_original_script_and_gendered_reading_are_preserved(self):
        dialog = self.make_dialog()
        self.loaded(dialog)
        source = deepcopy(dialog.bundle)
        dialog.events.setCurrentRow(1)
        self.loaded(dialog)
        self.assertIn("6.8 hearts", dialog.texts["conditions"].toPlainText())
        self.assertIn("Suggested chapter phase", dialog.texts["conditions"].toPlainText())
        self.assertIn("ma'am", dialog.texts["transcript"].toPlainText())
        self.assertIn("<friend>", dialog.texts["script"].toPlainText())
        dialog.farmer.setCurrentIndex(1)
        self.loaded(dialog)
        self.assertIn("sir", dialog.texts["transcript"].toPlainText())
        self.assertNotIn("ma'am", dialog.texts["transcript"].toPlainText())
        self.assertEqual(self.scene_loader.call_args.kwargs["farmer_gender"], "male")
        self.assertEqual(dialog.bundle, source)
        self.assertTrue(all(widget.isReadOnly() for widget in dialog.texts.values()))

    def test_legacy_gender_lines_preserve_speakers_and_surrounding_dialogue(self):
        data = bundle()
        data["events"][0]["transcript"] = (
            "Abigail: Before.\nAbigail: Young man.^Young woman.\nNarration: Between.\n"
            "Abigail: Boy @.$h^Girl @.$s\nAbigail: After.")
        self.loader.side_effect = lambda *_args, **_kwargs: deepcopy(data)
        self.character["name"] = "<b>My NPC</b>"
        dialog = self.make_dialog()
        self.loaded(dialog)
        original = deepcopy(dialog.bundle)
        self.assertEqual(dialog.texts["transcript"].toPlainText(),
                         "Abigail: Before.\nAbigail: Young woman.\nNarration: Between.\nAbigail: Girl Farmer.\nAbigail: After.")
        dialog.farmer.setCurrentIndex(1)
        self.loaded(dialog)
        self.assertEqual(dialog.texts["transcript"].toPlainText(),
                         "Abigail: Before.\nAbigail: Young man.\nNarration: Between.\nAbigail: Boy Farmer.\nAbigail: After.")
        self.assertEqual(dialog.bundle, original)
        self.assertIn(original["events"][0]["script"], dialog.texts["script"].toPlainText())
        self.assertEqual(dialog.mapping.textFormat(), Qt.TextFormat.PlainText)
        self.assertIn("<b>My NPC</b>", dialog.mapping.text())

    def test_initialization_reports_authored_story_impact_and_append_is_explicit(self):
        character = new_project(story_starter="romance")["character"]
        character["events"][0]["name"] = "My own scene"
        before = deepcopy(character)
        dialog = self.make_dialog(character)
        self.loaded(dialog)
        self.assertEqual(dialog.mode.currentData(), "initialize")
        self.assertIn(str(len(character["events"])), dialog.impact.text())
        self.assertIn("event", dialog.impact.text().lower())
        self.assertEqual(self.preview.call_args.args[0], before)
        self.assertEqual(self.preview.call_args.args[2], ["1", "2"])
        dialog.mode.setCurrentIndex(dialog.mode.findData("append"))
        self.assertIn("Add", dialog.use_button.text())
        self.assertTrue(dialog.use_button.isEnabled())
        self.assertEqual(character, before)

    def test_append_validates_without_replacement_confirmation_or_mutation(self):
        original = deepcopy(self.character)
        dialog = self.make_dialog(mode="append")
        self.loaded(dialog)
        dialog.events.item(1).setCheckState(Qt.CheckState.Unchecked)
        with patch.object(dialog, "confirm_initialization") as confirm:
            dialog.accept()
        confirm.assert_not_called()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(self.apply.call_args.args[2], ["1"])
        self.initialize.assert_not_called()
        self.assertEqual(self.character, original)

    def test_initialization_cancel_keeps_dialog_and_project_unchanged(self):
        original = deepcopy(self.character)
        dialog = self.make_dialog()
        self.loaded(dialog)
        with patch.object(dialog, "confirm_initialization", return_value=False) as confirm:
            dialog.accept()
        self.assertEqual(confirm.call_args.args[0]["selected_count"], 2)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertTrue(dialog.isVisible())
        self.apply.assert_not_called()
        self.assertEqual(self.character, original)

    def test_initialization_confirmation_validates_exact_selection_without_mutation(self):
        original = deepcopy(self.character)
        dialog = self.make_dialog()
        self.loaded(dialog)
        dialog.events.item(1).setCheckState(Qt.CheckState.Unchecked)
        with patch.object(dialog, "confirm_initialization", return_value=True) as confirm:
            dialog.accept()
        self.assertEqual(confirm.call_args.args[0]["selected_count"], 1)
        self.assertEqual(self.initialize.call_args.args[2], ["1"])
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.apply.assert_not_called()
        self.assertEqual(self.character, original)

    def test_initialization_warning_defaults_to_cancel_and_describes_replacement(self):
        character = new_project(story_starter="romance")["character"]
        dialog = self.make_dialog(character)
        self.loaded(dialog)
        impact = self.impact(character, bundle(), ["1", "2"])
        def inspect_box(box):
            self.assertEqual(box.defaultButton(), box.button(QMessageBox.StandardButton.Cancel))
            wording = " ".join((box.text(), box.informativeText(), box.detailedText())).lower()
            for word in ("event", "chapter", "relationship", "undo"):
                self.assertIn(word, wording)
            self.assertIn("Abigail", " ".join(button.text() for button in box.buttons()))
            return 0
        with patch("pixelheart.vanilla_story.QMessageBox.exec", new=inspect_box):
            self.assertFalse(dialog.confirm_initialization(impact))

    def test_blocked_life_links_prevent_confirmation_and_allow_append_mode(self):
        message = "Dialogue “Return visit” follows an event absent from the replacement."
        def blocked(*args):
            result = self.impact(*args)
            result.update(blocked=True, issues=[{"level": "error", "field": "life.dialogues.0.conditions.after_event_id", "message": message}])
            return result
        self.preview.side_effect = blocked
        dialog = self.make_dialog()
        self.loaded(dialog)
        self.assertIn(message, dialog.impact.text())
        self.assertFalse(dialog.use_button.isEnabled())
        with patch.object(dialog, "confirm_initialization") as confirm:
            dialog.accept()
        confirm.assert_not_called()
        self.initialize.assert_not_called()
        dialog.mode.setCurrentIndex(dialog.mode.findData("append"))
        self.assertTrue(dialog.use_button.isEnabled())

    def test_initialization_revalidation_failure_keeps_dialog_open(self):
        self.initialize.side_effect = ValueError("A daily-life link changed; replacement is blocked.")
        dialog = self.make_dialog()
        self.loaded(dialog)
        with patch.object(dialog, "confirm_initialization", return_value=True):
            dialog.accept()
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertIn("replacement is blocked", dialog.status.text())
        self.assertTrue(dialog.isVisible())

    def test_failed_selection_validation_leaves_dialog_open(self):
        self.apply.side_effect = ValueError("The story collection is full.")
        dialog = self.make_dialog(mode="append")
        self.loaded(dialog)
        dialog.accept()
        self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertIn("collection is full", dialog.status.text())
        self.assertTrue(dialog.isVisible())

    def test_catalog_failure_offers_recovery_without_an_import(self):
        self.loader.side_effect = ValueError("Missing event dictionary")
        dialog = self.make_dialog()
        self.wait_until(lambda: dialog.worker is None and "Missing event dictionary" in dialog.status.text())
        self.assertIsNone(dialog.bundle)
        self.assertFalse(dialog.use_button.isEnabled())
        self.assertEqual(dialog.change_source_button.text(), "Locate game…")
        with patch("pixelheart.vanilla_story.QFileDialog.getExistingDirectory", return_value=""):
            dialog.change_source()
        self.assertEqual(dialog.game_root, str(self.root / "Game"))

    def test_npc_switch_cancels_old_work_and_discards_late_results(self):
        started, release = threading.Event(), threading.Event()
        self.release.append(release)
        def read(npc, *_args, **_kwargs):
            if npc == "abigail":
                started.set()
                release.wait(2)
            return bundle(npc)
        self.loader.side_effect = read
        dialog = self.make_dialog()
        self.wait_until(started.is_set)
        old_token = dialog._catalog_token
        dialog.npc.setCurrentIndex(1)
        dialog.receive_catalog(old_token, {"game_root": "old source", "bundle": bundle()})
        self.assertIsNone(dialog.bundle)
        release.set()
        self.loaded(dialog)
        self.assertEqual(dialog.bundle["npc"]["id"], dialog.npc.currentData())
        self.assertNotEqual(dialog.game_root, "old source")

    def test_cancel_waits_for_worker_and_never_accepts_late_data(self):
        started, release = threading.Event(), threading.Event()
        self.release.append(release)
        def read(*_args, **_kwargs):
            started.set()
            release.wait(2)
            return bundle()
        self.loader.side_effect = read
        dialog = self.make_dialog()
        self.wait_until(started.is_set)
        dialog.reject()
        self.assertTrue(dialog.closing)
        dialog.receive_catalog(dialog._catalog_token, {"game_root": "late source", "bundle": bundle()})
        self.assertIsNone(dialog.bundle)
        release.set()
        self.wait_until(lambda: dialog.worker is None and not dialog.isVisible())
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.apply.assert_not_called()

    def test_scene_switch_discards_late_artwork_and_uses_latest_farmer(self):
        started, release = threading.Event(), threading.Event()
        self.release.append(release)
        def scenery(document, project_file, event, **kwargs):
            if event["location"] == "Town":
                started.set()
                release.wait(2)
            return self.scene(document, project_file, event, **kwargs)
        self.scene_loader.side_effect = scenery
        dialog = self.make_dialog()
        self.wait_until(started.is_set)
        old_token = dialog._scene_token
        dialog.events.setCurrentRow(1)
        dialog.farmer.setCurrentIndex(1)
        dialog.receive_scene(old_token, {"game_root": "late source", "assets": {}, "event": {}})
        self.assertNotEqual(dialog.game_root, "late source")
        release.set()
        self.loaded(dialog)
        self.assertEqual(dialog.canvas.location_label, "Beach")
        self.assertEqual(dialog.canvas.farmer_gender, "male")
        self.assertEqual(self.scene_loader.call_count, 2)

    def test_cancel_before_automatic_load_does_not_start_a_worker(self):
        dialog = VanillaStoryDialog(self.character)
        self.dialogs.append(dialog)
        dialog.reject()
        self.app.processEvents()
        self.loader.assert_not_called()
        self.scene_loader.assert_not_called()

    def test_persisted_source_reopens_without_catalog_or_project_artwork(self):
        original = entry()
        reference = {**original, "npc": {"id": "abigail", "name": "Abigail"},
                     "actors": original["source_event"]["story"]["actors"] + [{"name": "Offstage", "x": -1000, "y": -1000, "facing": 2}]}
        reference.pop("source_event")
        before = deepcopy(reference)
        dialog = self.make_dialog(reference=reference)
        self.loaded(dialog)
        self.loader.assert_not_called()
        self.assertIn(reference["script"], dialog.texts["script"].toPlainText())
        self.assertEqual([actor["name"] for actor in dialog.canvas.actors], ["Abigail", "farmer"])
        document = self.scene_loader.call_args.args[0]
        self.assertEqual(document["character"]["internal_name"], "SourceReference")
        self.assertIsNone(self.scene_loader.call_args.args[1])
        cast = deepcopy(dialog.canvas.actors)
        dialog.canvas.setFocus()
        QTest.keyClick(dialog.canvas, Qt.Key.Key_Right)
        dialog.canvas.move_selected(1, 1)
        start = dialog.canvas.actor_rect(0).center().toPoint()
        end = dialog.canvas.tile_point(14, 12).toPoint()
        QTest.mousePress(dialog.canvas, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(dialog.canvas, end)
        QTest.mouseRelease(dialog.canvas, Qt.MouseButton.LeftButton, pos=end)
        self.assertEqual(dialog.canvas.actors, cast)
        self.assertEqual(reference, before)
        self.assertIsNone(dialog.use_button)

    def test_related_branch_scripts_remain_accessible_without_importing_branch_rows(self):
        data = bundle()
        related = {"asset": "Data/Events/Temp", "event_id": "fork-a", "event_key": "fork-a",
                   "script": 'speak Abigail "A preserved <branch>"/end', "location": "Temp"}
        data["events"][0]["event"]["story"] = {"vanilla_source": {"related_scripts": [related]}}
        self.loader.side_effect = lambda *_args, **_kwargs: data
        dialog = self.make_dialog()
        self.loaded(dialog)
        scripts = dialog.texts["script"].toPlainText()
        self.assertIn(data["events"][0]["script"], scripts)
        self.assertIn("Data/Events/Temp", scripts)
        self.assertIn("Condition key: fork-a", scripts)
        self.assertIn(related["script"], scripts)
        self.assertEqual(dialog.selected_event_keys(), ["1", "2"])

    def test_compact_source_stage_frames_full_cast_at_readable_scale(self):
        dialog = self.make_dialog()
        dialog.resize(900, 650)
        self.loaded(dialog)
        self.assertGreaterEqual(dialog.canvas.geometry_grid()[2], 16)
        cast = deepcopy(dialog.canvas.actors)
        for index in range(len(cast)):
            self.assertTrue(dialog.canvas.scene_rect().contains(dialog.canvas.actor_rect(index)))
        dialog.canvas.fit_map()
        self.assertEqual(dialog.canvas.bounds, (0, 0, 20, 15))
        dialog.canvas.fit()
        self.assertEqual(dialog.canvas.actors, cast)

    def test_source_preview_failure_does_not_hide_preserved_script_or_stay_stale(self):
        def scenery(document, project_file, event, **kwargs):
            if event["location"] == "Town":
                raise ValueError("No map artwork")
            return self.scene(document, project_file, event, **kwargs)
        self.scene_loader.side_effect = scenery
        dialog = self.make_dialog()
        self.loaded(dialog)
        self.assertIn("No map artwork", dialog.status.text())
        self.assertTrue(dialog.texts["script"].toPlainText())
        dialog.events.setCurrentRow(1)
        self.loaded(dialog)
        self.assertNotIn("No map artwork", dialog.status.text())
        self.assertFalse(dialog.stage_panel.isHidden())

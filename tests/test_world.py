import copy
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image

from pixelheart_core.projects import new_project, load_project, save_project, copy_project, ProjectError
from pixelheart_core.world import (
    WorldError, new_world, new_companion, new_location, import_map, map_bundle,
    asset_path, exported_location_id, exported_mod_id, world_character,
    world_issues, install_archive, render_map_preview, cast_actor_id,
)


def make_map(root, name="room.tmx", width=8, height=10, external=True):
    root.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (16, 16), (120, 150, 80, 255)).save(root / "tiles.png")
    tileset = '<tileset tilewidth="16" tileheight="16" tilecount="1" columns="1"><image source="tiles.png" width="16" height="16"/></tileset>'
    if external:
        (root / "room.tsx").write_text(tileset)
        tileset = '<tileset firstgid="1" source="room.tsx"/>'
    else:
        tileset = tileset.replace('<tileset ', '<tileset firstgid="1" ')
    layers = "".join(f'<layer name="{layer}" width="{width}" height="{height}"><data encoding="csv">' + ",".join(["1" if layer == "Back" else "0"] * (width * height)) + '</data></layer>' for layer in ("Back", "Buildings", "Front"))
    file = root / name
    file.write_text(f'<map orientation="orthogonal" width="{width}" height="{height}" tilewidth="16" tileheight="16">{tileset}{layers}</map>')
    return file


class WorldAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.character = new_project()["character"]
        self.world = new_world()

    def test_map_import_copies_external_tilesets_and_images_as_immutable_bundle(self):
        source = make_map(self.root / "source")
        reference = import_map(source, self.root / "project" / "character.json")
        imported = asset_path(reference, self.root / "project")
        self.assertEqual(imported.read_bytes(), source.read_bytes())
        self.assertEqual((imported.parent / "room.tsx").read_bytes(), (source.parent / "room.tsx").read_bytes())
        self.assertEqual((imported.parent / "tiles.png").read_bytes(), (source.parent / "tiles.png").read_bytes())
        self.assertEqual(import_map(source, self.root / "project" / "character.json"), reference)
        self.assertEqual(map_bundle(imported)["width"], 8)
        preview = render_map_preview(imported)
        self.assertEqual(preview.size, (128, 160))
        self.assertEqual(preview.getpixel((24, 24)), (120, 150, 80, 255))

    def test_import_rejects_external_and_missing_dependencies_without_partial_copy(self):
        source = make_map(self.root / "source")
        original = source.read_text()
        for invalid in ("../room.tsx", "/tmp/room.tsx", "C:\\room.tsx", "missing.tsx", "https://example.com/room.tsx"):
            with self.subTest(source=invalid):
                source.write_text(original.replace('source="room.tsx"', f'source="{invalid}"'))
                with self.assertRaises(WorldError):
                    import_map(source, self.root / "project" / "character.json")
        self.assertFalse((self.root / "project").exists())

    def test_xml_entities_and_external_symlinks_are_rejected(self):
        source = make_map(self.root / "source")
        source.write_text('<!DOCTYPE map [<!ENTITY nope SYSTEM "file:///etc/passwd">]>' + source.read_text())
        with self.assertRaisesRegex(WorldError, "entities"):
            map_bundle(source)
        source = make_map(self.root / "source")
        outside = self.root / "outside.png"
        Image.new("RGBA", (16, 16)).save(outside)
        (source.parent / "tiles.png").unlink()
        (source.parent / "tiles.png").symlink_to(outside)
        with self.assertRaisesRegex(WorldError, "folder"):
            map_bundle(source)

    def test_save_as_copies_cast_and_complete_map_closure_and_preserves_metadata(self):
        project = self.root / "original" / "character.json"
        source = make_map(self.root / "source")
        location = new_location()
        location["map"] = import_map(source, project)
        companion = new_companion("Young companion", age="child")
        companion["custom"] = {"role": "found family"}
        companion["artwork"] = {"portrait": "cast.png", "sprite": None}
        Image.new("RGBA", (128, 192)).save(project.parent / "cast.png")
        document = new_project()
        document["world"] = {**new_world(), "characters": [companion], "locations": [location]}
        original = copy.deepcopy(document)
        save_project(document, project)
        destination = self.root / "copy" / "character.json"
        copy_project(document, project, destination)
        self.assertEqual(document, original)
        reopened = load_project(destination)
        self.assertEqual(reopened["world"]["characters"][0]["character"]["age"], "child")
        self.assertEqual(reopened["world"]["characters"][0]["custom"], companion["custom"])
        self.assertTrue((destination.parent / "cast.png").exists())
        self.assertEqual(map_bundle(asset_path(location["map"], destination.parent))["files"], map_bundle(source)["files"])

    def test_nonadult_romance_and_escaping_world_references_cannot_be_saved(self):
        document = new_project()
        companion = new_companion("Child", "child")
        companion["character"]["romanceable"] = True
        document["world"] = {**new_world(), "characters": [companion]}
        with self.assertRaisesRegex(ProjectError, "adult only"):
            save_project(document, self.root / "project.json")
        companion["character"]["romanceable"] = False
        companion["artwork"]["portrait"] = "../portrait.png"
        with self.assertRaisesRegex(ProjectError, "inside"):
            save_project(document, self.root / "project.json")

    def test_world_aliases_resolve_without_changing_authored_notes(self):
        companion = new_companion("Grogu")
        location = new_location()
        location["internal_name"] = "Workshop"
        world = {**new_world(), "characters": [companion], "locations": [location]}
        self.character["home_map"] = "Workshop"
        self.character["events"] = [{"id": "event", "location": "Workshop", "story": {
            "actors": [{"name": "Grogu"}], "beats": [{"actor": "Grogu"}]}}]
        original = copy.deepcopy(self.character)
        result = world_character(self.character, world)
        self.assertEqual(result["home_map"], exported_location_id(location, self.character))
        self.assertTrue(result["events"][0]["story"]["actors"][0]["name"].startswith("Pixelheart.Grogu_"))
        self.assertEqual(self.character, original)

    def test_stable_companion_actor_survives_rename_and_missing_actor_is_reported(self):
        companion = new_companion("Grogu")
        alias = cast_actor_id(companion)
        world = {**new_world(), "characters": [companion]}
        self.character["events"] = [{"id": "event", "story": {"actors": [{"name": alias}]}}]
        companion["character"]["internal_name"] = "LittleFriend"
        resolved = world_character(self.character, world)
        self.assertIn("LittleFriend", resolved["events"][0]["story"]["actors"][0]["name"])
        issues = world_issues(new_world(), self.character)
        self.assertTrue(any(issue["field"] == "events.0.story.actors.0.name" and "removed" in issue["message"] for issue in issues))

    def test_imported_places_cannot_have_ambiguous_entrances_or_closed_cycles(self):
        first, second = new_location(), new_location()
        first["internal_name"], second["internal_name"] = "First", "Second"
        reference = import_map(make_map(self.root / "source"), self.root / "project.json")
        first["map"] = second["map"] = reference
        world = {**new_world(), "locations": [first, second]}
        self.assertTrue(any("entrance tile" in issue["message"] for issue in world_issues(world, self.character, self.root)))
        first["entrance"]["map"], second["entrance"]["map"] = "Second", "First"
        self.assertTrue(any("closed loop" in issue["message"] for issue in world_issues(world, self.character, self.root)))

    def test_map_arrivals_and_warps_are_checked_against_dimensions(self):
        location = new_location()
        location["map"] = import_map(make_map(self.root / "source"), self.root / "project.json")
        location["entry_x"] = 8
        world = {**new_world(), "locations": [location]}
        issues = world_issues(world, self.character, self.root)
        self.assertTrue(any(issue["level"] == "error" and "inside" in issue["message"] for issue in issues))
        location["entry_x"] = location["exit_x"]
        location["entry_y"] = location["exit_y"]
        self.assertTrue(any("immediate return" in issue["message"] for issue in world_issues(world, self.character, self.root)))

    def test_known_map_bounds_cover_home_route_scene_moves_and_enabled_life(self):
        from pixelheart_core.story import new_event, new_beat
        from pixelheart_core.life import new_life_record
        location = new_location()
        location["map"] = import_map(make_map(self.root / "source"), self.root / "project.json")
        self.character["home_map"] = location["internal_name"]
        self.character["schedule"][0]["location"] = location["internal_name"]
        event = new_event(self.character)
        event["story"].update(stage="ready", beats=[{**new_beat("move"), "x": 20, "y": 0}])
        self.character["events"] = [event]
        routine = new_life_record("routines", self.character)
        routine["enabled"] = True
        self.character["life"] = {"routines": [routine]}
        issues = world_issues({**new_world(), "locations": [location]}, self.character, self.root)
        fields = {issue["field"] for issue in issues if issue["level"] == "error"}
        self.assertTrue({"home_x", "schedule.0.x", "events.0.story.actors.0.x", "events.0.story.beats.0.x", "life.routines.0.stops.0.x"} <= fields)


class WorldPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from pixelheart.world_page import WorldPage
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        class Window:
            document = new_project()
            project_file = None
            errors = []
            def ensure_saved(inner):
                if inner.project_file is None:
                    inner.project_file = self.root / "project" / "character.json"
                    inner.document["world"] = self.page.dump()
                    save_project(inner.document, inner.project_file)
                    self.page.load(load_project(inner.project_file)["world"])
                return True
            def show_error(inner, title, message):
                inner.errors.append((title, message))
        self.window = Window()
        self.page = WorldPage(self.window)
        self.page.load()
        self.addCleanup(self.page.deleteLater)

    def test_single_npc_ui_preserves_legacy_supporting_characters(self):
        companion = new_companion("Old companion")
        companion["private_notes"] = {"unmodified": "legacy extension"}
        legacy = {**new_world(), "characters": [companion]}
        self.page.load(legacy)
        self.page.add_location()
        self.window.ensure_saved()
        self.assertEqual(self.page.dump()["characters"], [companion])
        self.assertEqual(load_project(self.window.project_file)["world"]["characters"], [companion])
        self.assertFalse(hasattr(self.page, "cast_list"))
        self.assertEqual(self.page.tabs.currentIndex(), 0)
        self.assertFalse(self.page.tabs.isTabVisible(1))
        self.assertTrue(self.page.legacy_characters_button.isHidden())
        self.page.place_advanced_toggle.setChecked(True)
        self.assertFalse(self.page.legacy_characters_button.isHidden())

    def test_legacy_artwork_repair_keeps_character_selected_after_first_save(self):
        from pixelheart.legacy_characters import LegacyCharactersDialog
        records = [new_companion("First"), new_companion("Second")]
        self.page.load({**new_world(), "characters": records})
        dialog = LegacyCharactersDialog(self.page, index=1)
        self.addCleanup(dialog.deleteLater)
        portrait = self.root / "portrait.png"
        Image.new("RGBA", (128, 192)).save(portrait)
        with patch("pixelheart.legacy_characters.QFileDialog.getOpenFileName", return_value=(str(portrait), "")):
            dialog.import_artwork("portrait")
        self.assertFalse(self.window.errors)
        self.assertIsNone(self.page.world["characters"][0]["artwork"]["portrait"])
        self.assertTrue(self.page.world["characters"][1]["artwork"]["portrait"])
        self.assertEqual(self.page.world["characters"][1]["character"], records[1]["character"])

    def test_legacy_removal_requires_confirmation_and_keeps_saved_project_until_save(self):
        from PySide6.QtWidgets import QMessageBox
        from pixelheart.legacy_characters import LegacyCharactersDialog
        records = [new_companion("First"), new_companion("Second")]
        self.page.load({**new_world(), "characters": records})
        self.window.ensure_saved()
        dialog = LegacyCharactersDialog(self.page, index=1)
        self.addCleanup(dialog.deleteLater)
        with patch("pixelheart.legacy_characters.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            dialog.remove_character()
        self.assertEqual(self.page.world["characters"], records)
        with patch("pixelheart.legacy_characters.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            dialog.remove_character()
        self.assertEqual(self.page.world["characters"], records[:1])
        self.assertEqual(load_project(self.window.project_file)["world"]["characters"], records)

    def test_legacy_validation_issue_opens_repair_dialog_for_affected_record(self):
        self.page.load({**new_world(), "characters": [new_companion("First"), new_companion("Second")]})
        with patch("pixelheart.legacy_characters.LegacyCharactersDialog") as constructor:
            self.page.open_issue("world.characters.1.artwork.sprite")
            constructor.assert_called_once_with(self.page, index=1)
            constructor.return_value.exec.assert_called_once()

    def test_first_save_during_second_map_import_keeps_selected_place(self):
        self.page.add_location()
        self.page.add_location()
        second = self.page.world["locations"][1]["id"]
        source = make_map(self.root / "source")
        with patch("pixelheart.world_page.QFileDialog.getOpenFileName", return_value=(str(source), "")):
            self.page.import_location()
        self.assertFalse(self.window.errors)
        self.assertEqual(self.page.world["locations"][self.page.location_index]["id"], second)
        self.assertIsNone(self.page.world["locations"][0]["map"])
        self.assertTrue(self.page.world["locations"][1]["map"])

    def test_edit_painted_map_replaces_only_selected_map_and_preserves_rejected_maps(self):
        from PySide6.QtWidgets import QDialog
        self.page.add_location()
        self.page.add_location()
        self.window.ensure_saved()
        reference = import_map(make_map(self.root / "source"), self.window.project_file)
        self.page.world["locations"][1]["map"] = reference
        self.page.refresh_location()
        self.assertTrue(self.page.edit_map_button.isEnabled())
        with patch("pixelheart.map_workshop.MapWorkshop") as constructor:
            dialog = constructor.return_value
            dialog.exec.return_value = QDialog.DialogCode.Accepted
            dialog.result_reference = reference
            dialog.result_is_spouse_room = True
            self.page.edit_map()
            dialog.load_map.assert_called_once_with(reference)
        self.assertIsNone(self.page.world["locations"][0]["map"])
        self.assertTrue(self.page.world["locations"][1]["spouse_room"])
        before = self.page.dump()
        with patch("pixelheart.map_workshop.MapWorkshop") as constructor:
            constructor.return_value.load_map.side_effect = WorldError("Use Tiled to preserve custom map data")
            self.page.edit_map()
            constructor.return_value.exec.assert_not_called()
        self.assertEqual(self.page.dump(), before)
        self.assertIn("Use Tiled", self.window.errors[-1][1])


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mods = self.root / "Mods"
        self.mods.mkdir()
        self.identity = "Pixelheart.Mira_abc123"

    def archive(self, text="first", extra=None):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("[CP] Mira/manifest.json", json.dumps({"UniqueID": self.identity}))
            archive.writestr("[CP] Mira/content.json", json.dumps({"Changes": [], "test": text}))
            if extra:
                archive.writestr(*extra)
        return output.getvalue()

    def test_first_install_and_owned_update_preserve_previous_pack_outside_mods(self):
        first = install_archive(self.archive(), self.mods, self.identity)
        self.assertIsNone(first["backup_path"])
        second = install_archive(self.archive("second"), self.mods, self.identity)
        self.assertEqual(second["installed_path"], self.mods / "[CP] Mira")
        self.assertFalse(second["backup_path"].is_relative_to(self.mods))
        self.assertEqual(json.loads((second["backup_path"] / "content.json").read_text())["test"], "first")
        self.assertEqual(json.loads((second["installed_path"] / "content.json").read_text())["test"], "second")

    def test_foreign_folder_and_mismatched_identity_are_preserved(self):
        destination = self.mods / "[CP] Mira"
        destination.mkdir()
        sentinel = destination / "unrelated.txt"
        sentinel.write_text("preserve")
        with self.assertRaisesRegex(WorldError, "not installed by Pixelheart"):
            install_archive(self.archive(), self.mods, self.identity)
        self.assertEqual(sentinel.read_text(), "preserve")
        with self.assertRaisesRegex(WorldError, "identity"):
            install_archive(self.archive(), self.mods, "Pixelheart.Other_123")

    def test_traversal_symlink_and_duplicate_archive_entries_are_rejected(self):
        symlink = zipfile.ZipInfo("[CP] Mira/linked")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        for extra in (("[CP] Mira/../../outside", "bad"), (symlink, "/tmp/outside"), ("[CP] Mira/CONTENT.json", "duplicate")):
            with self.subTest(extra=str(extra[0])):
                with self.assertRaises(WorldError):
                    install_archive(self.archive(extra=extra), self.mods, self.identity)
        self.assertEqual(list(self.mods.iterdir()), [])

    def test_failed_final_replace_restores_previous_install(self):
        installed = install_archive(self.archive(), self.mods, self.identity)["installed_path"]
        import os
        original_replace = os.replace
        def fail_final(source, destination):
            if Path(source).name.startswith(".pixelheart-stage-"):
                raise OSError("simulated final rename failure")
            return original_replace(source, destination)
        with patch("pixelheart_core.world.os.replace", side_effect=fail_final):
            with self.assertRaisesRegex(WorldError, "simulated"):
                install_archive(self.archive("second"), self.mods, self.identity)
        self.assertEqual(json.loads((installed / "content.json").read_text())["test"], "first")
        self.assertFalse(any(path.name.startswith(".pixelheart-stage-") for path in self.mods.iterdir()))


if __name__ == "__main__":
    unittest.main()

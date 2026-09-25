"""A planned entrance stays a draft until confirmed, including portable saves."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from PIL import Image

from pixelheart.app import _export_completion_message
from pixelheart_core.exporting import build_mod_archive, ExportValidationError
from pixelheart_core.interiors import doorway_exit, ensure_doorway, import_atlas, new_interior
from pixelheart_core.projects import new_project, load_project, save_project
from pixelheart_core.world import (
    WorldError, import_map, new_location, new_world, normalize_world, world_issues,
)
try:
    from .test_world import make_map
except ImportError:
    from test_world import make_map


class HomeEntranceExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temporary)
        self.project = self.root / "character.json"
        self.document = new_project()
        self.character = self.document["character"]
        self.character.update(name="Mira", internal_name="Mira", romanceable=False)
        self.location = new_location()
        self.location["map"] = import_map(make_map(self.root / "source"), self.project)
        self.document["world"] = new_world()
        self.document["world"]["locations"].append(self.location)
        self.portrait = self.root / "portrait.png"
        self.sprite = self.root / "sprite.png"
        Image.new("RGBA", (128, 192)).save(self.portrait)
        Image.new("RGBA", (64, 128)).save(self.sprite)

    def issues(self):
        return world_issues(self.document["world"], self.character, self.root)

    def archive(self):
        return build_mod_archive(self.character, self.portrait, self.sprite,
                                 world=self.document["world"], project_root=self.root,
                                 project_document=self.document)

    def manifest(self):
        with zipfile.ZipFile(io.BytesIO(self.archive())) as archive:
            return json.loads(archive.read("[CP] Mira/manifest.json"))

    def test_legacy_entrance_needs_no_new_confirmation_and_keeps_its_saved_shape(self):
        self.assertNotIn("confirmed", self.location["entrance"])
        before = copy.deepcopy(self.document["world"])
        self.assertEqual(normalize_world(before), before)
        self.assertFalse([issue for issue in self.issues() if issue["level"] == "error"])
        self.assertTrue(self.archive())
        self.assertEqual(self.document["world"], before)

    def test_pending_entrance_survives_save_and_blocks_an_otherwise_valid_export(self):
        self.location["entrance"]["confirmed"] = False
        save_project(self.document, self.project)
        reopened = load_project(self.project)
        self.assertIs(reopened["world"]["locations"][0]["entrance"]["confirmed"], False)
        errors = [issue for issue in self.issues() if issue["level"] == "error"]
        self.assertEqual([issue["field"] for issue in errors], ["world.locations.0.entrance"])
        self.assertIn("Use this entrance", errors[0]["message"])
        with self.assertRaises(ExportValidationError) as raised:
            self.archive()
        self.assertTrue(any(issue["field"] == "world.locations.0.entrance" for issue in raised.exception.issues))

    def test_missing_artwork_does_not_hide_pending_entrance(self):
        self.location.update(map=None, interior=ensure_doorway(new_interior()))
        self.location["entrance"]["confirmed"] = False
        fields = {issue["field"] for issue in self.issues() if issue["level"] == "error"}
        self.assertEqual(fields, {"world.locations.0.map", "world.locations.0.entrance"})

    def test_confirmation_allows_export_and_is_preserved_in_portable_backup(self):
        self.location["entrance"]["confirmed"] = True
        with zipfile.ZipFile(io.BytesIO(self.archive())) as archive:
            backup = json.loads(archive.read("[CP] Mira/project.json"))
        self.assertIs(backup["world"]["locations"][0]["entrance"]["confirmed"], True)

    def test_pending_default_does_not_reserve_a_tile_before_or_after_a_confirmed_place(self):
        second = new_location()
        second.update(name="Second home", internal_name="SecondHome", map=self.location["map"])
        self.location["entrance"]["confirmed"] = False
        second["entrance"]["confirmed"] = True
        for locations in ([self.location, second], [second, self.location]):
            with self.subTest(pending_index=locations.index(self.location)):
                self.document["world"]["locations"] = locations
                errors = [issue for issue in self.issues() if issue["level"] == "error"]
                self.assertEqual(len(errors), 1)
                self.assertEqual(errors[0]["field"], f"world.locations.{locations.index(self.location)}.entrance")
                self.assertIn("Use this entrance", errors[0]["message"])
        self.location["entrance"]["confirmed"] = True
        self.assertTrue(any("Another place uses this entrance tile" in issue["message"] for issue in self.issues()))

    def test_spouse_room_does_not_need_an_outside_entrance(self):
        self.character["romanceable"] = True
        self.location["spouse_room"] = True
        self.location["entrance"]["confirmed"] = False
        self.assertFalse([issue for issue in self.issues() if issue["level"] == "error"])

    def test_confirmation_rejects_non_boolean_metadata(self):
        for invalid in (None, 0, 1, "false", [], {}):
            with self.subTest(confirmed=invalid):
                self.location["entrance"]["confirmed"] = invalid
                with self.assertRaisesRegex(WorldError, "confirmation must be true or false"):
                    normalize_world(self.document["world"])

    def test_export_message_uses_required_dependencies_from_the_archive(self):
        self.document["world"]["dependencies"] = [
            {"id": "Pixelheart.Interiors", "minimum_version": "0.1.0", "required": True},
            {"id": "Example.Furniture", "minimum_version": "2.0.0", "required": True},
            {"id": "Example.Optional", "minimum_version": "", "required": False},
        ]
        message = _export_completion_message("Mira.zip", self.manifest())
        self.assertIn("Pixelheart.Interiors 0.1.0+", message)
        self.assertIn("Example.Furniture 2.0.0+", message)
        self.assertNotIn("Example.Optional", message)
        self.assertIn("DLL is not included", message)
        self.assertIn("WORLD_BUILDING.md", message)
        self.assertIn("Stardew Valley 1.6.9+", message)

    def test_designed_home_automatically_produces_companion_install_guidance(self):
        sheet = self.root / "interior.png"
        Image.new("RGBA", (64, 16)).save(sheet)
        design = ensure_doorway(new_interior())
        design["atlas"] = import_atlas(sheet, self.root)
        self.location.update(map=None, interior=design,
                             entry_x=design["entry"][0], entry_y=design["entry"][1])
        self.location["exit_x"], self.location["exit_y"] = doorway_exit(design)
        self.assertFalse(self.document["world"]["dependencies"])
        message = _export_completion_message("Mira.zip", self.manifest())
        self.assertIn("Pixelheart.Interiors 0.1.0+", message)
        self.assertIn("DLL is not included", message)

    def test_export_without_required_companion_does_not_claim_it_is_needed(self):
        self.document["world"]["dependencies"] = [
            {"id": "Pixelheart.Interiors", "minimum_version": "0.1.0", "required": False},
        ]
        message = _export_completion_message("Mira.zip", self.manifest())
        self.assertNotIn("Pixelheart", message)
        self.assertIn("SMAPI and Content Patcher", message)


if __name__ == "__main__":
    unittest.main()

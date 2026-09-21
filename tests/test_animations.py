"""Explicit authored animations survive editing and compile to game behavior."""
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from PIL import Image

from pixelheart_core.animations import animation_issues
from pixelheart_core.exporting import build_mod_archive, validate_character, ExportValidationError
from pixelheart_core.life import new_life_record, compile_life, LifeValidationError
from pixelheart_core.projects import new_project, save_project, load_project, ProjectError
from pixelheart_core.story import exported_npc_id
from pixelheart_core.validation import validate_draft


class AnimationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.portrait = self.root / "portrait.png"
        self.sprite = self.root / "sprite.png"
        Image.new("RGBA", (128, 192)).save(self.portrait)
        Image.new("RGBA", (64, 416)).save(self.sprite)
        self.document = new_project()
        self.character = self.document["character"]
        self.character.update(name="Visitor", internal_name="Visitor", romanceable=False,
                              animations={"sleep": "51/51/51//laying_down"})
        self.character["schedule"] = [
            {"id": "morning", "time": "610", "location": "Town", "x": 32, "y": 62,
             "facing": "down", "activity": "sleep"},
            {"id": "night", "time": "2200", "location": "Town", "x": 33, "y": 62,
             "facing": "left", "activity": "", "animation": "sleep"},
        ]

    def errors(self, **kwargs):
        return [issue for issue in validate_character(self.character, self.portrait, self.sprite, **kwargs)
                if issue["level"] == "error"]

    def test_export_links_real_sleep_to_owned_namespaced_frames_and_keeps_notes_separate(self):
        before = deepcopy(self.document)
        blob = build_mod_archive(self.character, self.portrait, self.sprite)
        npc_id = exported_npc_id(self.character)
        key = npc_id.lower() + "_sleep"
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            content = json.loads(archive.read("[CP] Visitor/content.json"))
            patch = next(patch for patch in content["Changes"] if patch["Target"] == "Data/animationDescriptions")
            self.assertEqual(patch["Entries"], {key: "51/51/51//laying_down"})
            schedule = json.loads(archive.read("[CP] Visitor/assets/schedule.json"))
            self.assertEqual(schedule["spring"], f"610 Town 32 62 2/2200 Town 33 62 3 {key}")
            backup = json.loads(archive.read("[CP] Visitor/project.json"))
            self.assertEqual(backup["character"], self.character)
        self.assertEqual(self.document, before)

    def test_absent_definitions_do_not_add_animation_patch(self):
        self.character.pop("animations")
        self.character["schedule"][1].pop("animation")
        with zipfile.ZipFile(io.BytesIO(build_mod_archive(self.character, self.portrait, self.sprite))) as archive:
            content = json.loads(archive.read("[CP] Visitor/content.json"))
            self.assertFalse(any(patch["Target"] == "Data/animationDescriptions" for patch in content["Changes"]))

    def test_frame_bounds_include_every_appearance_sprite(self):
        Image.new("RGBA", (64, 128)).save(self.sprite)
        self.assertTrue(any("16-frame" in issue["message"] for issue in self.errors()))
        Image.new("RGBA", (64, 416)).save(self.sprite)
        self.assertEqual(self.errors(), [])
        winter = self.root / "winter.png"
        Image.new("RGBA", (64, 128)).save(winter)
        errors = self.errors(appearances={"winter": {"sprite": winter}})
        self.assertTrue(any("Winter appearance" in issue["message"] and "16-frame" in issue["message"] for issue in errors))

    def test_unsafe_or_unsupported_descriptions_are_rejected(self):
        invalid = [None, {}, "", "51/51", "51//51", "-1/51/51", "4096/51/51",
                   "51/51/51/message", "51/51/51//arbitrary", "51/51/51//offset 65 0",
                   "51/51/51//laying_down/laying_down", "51/51/51//offset 1 0/offset 2 0",
                   "51/51/51\n", "51/51/51//{{Token}}", "0 " * 300 + "/0/0"]
        for description in invalid:
            with self.subTest(description=description):
                self.assertTrue(animation_issues({"sleep": description}))
        for definitions in (None, [], {"Sleep": "0/0/0"}, {"../escape": "0/0/0"},
                            {f"pose_{index}": "0/0/0" for index in range(33)}):
            with self.subTest(definitions=definitions):
                self.assertTrue(animation_issues(definitions))
        for description in ("0 1 2/3 3/0", "51/51/51//offset -4 16/laying_down",
                            "51/51/51//laying_down/offset 0 16"):
            self.assertEqual(animation_issues({"pose": description}, (64, 416)), [])

    def test_stop_must_reference_owned_key_and_cannot_be_ignored_by_bed_shortcut(self):
        for key in ("missing", "OtherNpc_sleep", "sleep/2200", None, ["sleep"]):
            with self.subTest(key=key):
                self.character["schedule"][1]["animation"] = key
                self.assertTrue(any(issue["field"] == "schedule.1.animation" for issue in self.errors()))
                with self.assertRaises(ExportValidationError):
                    build_mod_archive(self.character, self.portrait, self.sprite)
        self.character["schedule"][1].update(animation="sleep", location="bed")
        self.assertTrue(any("special bed destination" in issue["message"] for issue in self.errors()))

    def test_project_and_draft_roundtrip_preserve_named_animation(self):
        path = self.root / "character.json"
        save_project(self.document, path)
        loaded = load_project(path)
        self.assertEqual(loaded["character"]["animations"], self.character["animations"])
        self.assertEqual(loaded["character"]["schedule"], self.character["schedule"])
        draft = validate_draft({"animations": self.character["animations"], "schedule": self.character["schedule"]})
        self.assertEqual(draft["animations"], self.character["animations"])
        self.assertEqual(draft["schedule"][1]["animation"], "sleep")
        self.character["animations"] = {"sleep": "malformed"}
        with self.assertRaises(ProjectError):
            save_project(self.document, path)

    def test_conditional_routines_keep_their_explicit_end_behavior(self):
        row = new_life_record("routines", self.character)
        row.update(enabled=True, name="Quiet evening")
        self.character["life"] = {"routines": [row]}
        output = compile_life(self.character)
        expected = exported_npc_id(self.character).lower() + "_sleep"
        self.assertTrue(output["patches"][0]["Entries"]["spring"].endswith(expected))
        row["stops"][-1]["animation"] = "not_owned"
        with self.assertRaises(LifeValidationError):
            compile_life(self.character)
        row["enabled"] = False
        self.assertEqual(compile_life(self.character)["patches"], [])


if __name__ == "__main__":
    unittest.main()

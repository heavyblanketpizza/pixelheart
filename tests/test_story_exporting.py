"""Exercise story output at the archive boundary, including legacy projects."""

import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from PIL import Image

from pixelheart_core.exporting import ExportValidationError, build_mod_archive, validate_character
from pixelheart_core.story import event_game_id


class StoryExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.portrait = Path(self.temporary.name) / "portrait.png"
        self.sprite = Path(self.temporary.name) / "sprite.png"
        Image.new("RGBA", (128, 192)).save(self.portrait)
        Image.new("RGBA", (64, 128)).save(self.sprite)
        self.character = {
            "id": "story-project", "name": "Mira", "internal_name": "Mira",
            "season": "spring", "day": 16, "romanceable": False,
            "home_map": "Town", "home_x": 28, "home_y": 67,
            "dialogues": [{"id": "hello", "trigger": "Introduction", "text": "Hello, @.$h"}],
            "schedule": [{"id": "home", "time": 900, "location": "Town", "x": 28, "y": 67, "facing": "down"}],
            "gifts": {}, "events": [], "relationships": [],
        }

    def ready_event(self, event_id="first", name="A small beginning"):
        return {
            "id": event_id, "name": name, "hearts": 2, "location": "Town",
            "description": "The original idea remains intact.",
            "extension": {"author_reference": "sketch-1"},
            "story": {
                "stage": "ready", "premise": "Mira shares her garden.",
                "conflict": "She fears another failure.", "outcome": "She accepts help.",
                "relationship_id": "", "previous_event_id": "",
                "season": "spring", "weather": "sunny", "time_start": 900, "time_end": 1800,
                "music": "continue",
                "actors": [
                    {"id": "cast-npc", "name": "$npc", "x": 28, "y": 67, "facing": 2},
                    {"id": "cast-farmer", "name": "farmer", "x": 28, "y": 69, "facing": 0},
                ],
                "beats": [
                    {"id": "beat-speech", "kind": "dialogue", "actor": "$npc", "text": "I grew these for you, @.$h"},
                    {"id": "beat-effect", "kind": "friendship", "actor": "$npc", "amount": 25},
                ],
                "test_notes": "Check the garden fence.",
            },
        }

    def archive(self):
        return zipfile.ZipFile(io.BytesIO(build_mod_archive(self.character, self.portrait, self.sprite)))

    @staticmethod
    def read(archive, name):
        return json.loads(archive.read("[CP] Mira/" + name))

    def test_ready_scene_uses_same_npc_as_character_patch_and_preserves_all_drafts(self):
        event = self.ready_event()
        self.character["events"] = [
            event,
            {"id": "legacy", "name": "An unwritten idea", "description": "Keep this note."},
        ]
        original = copy.deepcopy(self.character)
        with self.archive() as archive:
            content = self.read(archive, "content.json")
            changes = content["Changes"]
            npc_id = next(iter(next(patch for patch in changes if patch["Target"] == "Data/Characters")["Entries"]))
            events = [patch for patch in changes if patch["Target"].startswith("Data/Events/")]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["Action"], "EditData")
            self.assertEqual(events[0]["Target"], "Data/Events/Town")
            self.assertEqual(len(events[0]["Entries"]), 1)
            key, script = next(iter(events[0]["Entries"].items()))
            self.assertTrue(key.startswith(event_game_id(event, self.character) + "/"))
            self.assertIn(f"Friendship {npc_id} 500", key)
            self.assertIn(f"speak {npc_id} ", script)
            self.assertIn(f"friendship {npc_id} 25", script)
            self.assertTrue(script.endswith("/end"))
            self.assertEqual(self.read(archive, "project.json")["character"], original)
            self.assertIn("[CP] Mira/STORY_TESTING.txt", archive.namelist())
        self.assertEqual(self.character, original)

    def test_legacy_notes_do_not_create_playable_scripts_or_new_story_files(self):
        self.character["events"] = [{"id": "legacy", "name": "At the river", "description": "An idea only."}]
        self.character["relationships"] = [{"id": "friend", "name": "Leah", "relation": "Friend", "description": "They paint together."}]
        with self.archive() as archive:
            changes = self.read(archive, "content.json")["Changes"]
            self.assertFalse(any(patch["Target"].startswith("Data/Events/") for patch in changes))
            self.assertNotIn("[CP] Mira/STORY_TESTING.txt", archive.namelist())
            self.assertEqual(self.read(archive, "project.json")["character"], self.character)

    def test_invalid_ready_scene_blocks_export_instead_of_silently_omitting_it(self):
        event = self.ready_event()
        event["story"]["beats"] = []
        self.character["events"] = [event]
        with self.assertRaises(ExportValidationError) as raised:
            build_mod_archive(self.character, self.portrait, self.sprite)
        self.assertTrue(any(issue["level"] == "error" and issue["field"].startswith("events.0")
                            for issue in raised.exception.issues))
        event["story"]["stage"] = "idea"
        with self.archive() as archive:
            self.assertFalse(any(patch["Target"].startswith("Data/Events/")
                                 for patch in self.read(archive, "content.json")["Changes"]))

    def test_guide_has_resolved_ids_conditions_effects_and_prerequisite_order(self):
        first = self.ready_event()
        second = self.ready_event("second", "Trust takes root")
        for event in (first, second):
            event["story"]["relationship_id"] = "garden-friendship"
        second["hearts"] = 4
        second["location"] = "Forest"
        second["story"]["previous_event_id"] = first["id"]
        self.character["events"] = [second, first]
        self.character["relationships"] = [{
            "id": "garden-friendship", "name": "A friendship in bloom", "relation": "Friendship",
            "description": "Trust through shared work.",
            "story": {"stage": "outline", "target": "farmer", "desire": "Connection", "tension": "Fear", "progression": "Trust", "resolution": "Shared garden"},
        }]
        with self.archive() as archive:
            guide = archive.read("[CP] Mira/STORY_TESTING.txt").decode("utf-8")
            manifest = self.read(archive, "manifest.json")
            first_id = event_game_id(first, self.character).replace("{{ModId}}", manifest["UniqueID"])
            second_id = event_game_id(second, self.character).replace("{{ModId}}", manifest["UniqueID"])
            self.assertIn(first_id, guide)
            self.assertIn(second_id, guide)
            self.assertNotIn("{{ModId}}", guide)
            self.assertIn("1. A small beginning", guide)
            self.assertIn("2. Trust takes root", guide)
            self.assertIn("A friendship in bloom", guide)
            self.assertIn("SawEvent " + first_id, guide)
            self.assertIn("Time 900 1800", guide)
            self.assertIn("Season spring", guide)
            self.assertIn("Weather sunny", guide)
            self.assertIn("+25 player friendship points", guide)
            self.assertIn("Check the garden fence.", guide)
            self.assertIn("not proof of an in-game test", guide)

    def test_event_identity_survives_renaming_and_reordering_but_not_copying(self):
        first = self.ready_event()
        second = self.ready_event("second", "A sequel")
        self.character["events"] = [first, second]

        def exported_ids():
            with self.archive() as archive:
                return {key.split("/", 1)[0] for patch in self.read(archive, "content.json")["Changes"]
                        if patch["Target"].startswith("Data/Events/") for key in patch["Entries"]}

        expected = exported_ids()
        first["name"] = "A new title"
        self.character["events"].reverse()
        self.assertEqual(exported_ids(), expected)
        duplicate = copy.deepcopy(first)
        duplicate["id"] = "new-copy"
        self.character["events"].append(duplicate)
        self.assertEqual(len(exported_ids()), 3)

    def test_daily_choice_guide_describes_both_outcomes_without_counting_fork_as_scene(self):
        from pixelheart_core.story import new_beat
        event = self.ready_event()
        choice = new_beat("choice")
        choice["text"] = "Will you stay?"
        choice["choices"][0].update(label="Stay", text="Thank you.$h", friendship=25)
        choice["choices"][1].update(label="Go", text="Maybe tomorrow.", friendship=-10)
        event["story"].update(repeat="daily", beats=[choice])
        self.character["events"] = [event]
        with self.archive() as archive:
            guide = archive.read("[CP] Mira/STORY_TESTING.txt").decode()
            self.assertIn("Playable scenes in this pack: 1", guide)
            self.assertIn("Repeat: Daily", guide)
            self.assertIn("Answer: Stay", guide)
            self.assertIn("Answer: Go", guide)
            self.assertIn("+25 player friendship points", guide)
            self.assertIn("-10 player friendship points", guide)
            self.assertIn("Skipping before choosing should award no answer effect", guide)

    def test_ready_scene_portrait_indices_match_default_and_appearance_sheets(self):
        event = self.ready_event()
        self.character["events"] = [event]
        event["story"]["beats"][0]["text"] = "Hello.$9"
        issues = validate_character(self.character, self.portrait, self.sprite)
        self.assertTrue(any(issue["level"] == "error" and "portrait $9" in issue["message"] for issue in issues))
        for newline in ("\n", "\r\n", "\r"):
            event["story"]["beats"][0]["text"] = "Hello.$9" + newline + "A second line."
            issues = validate_character(self.character, self.portrait, self.sprite)
            self.assertTrue(any(issue["level"] == "error" and "portrait $9" in issue["message"] for issue in issues))
        event["story"]["beats"][0]["text"] = "Hello.$9"
        tall_portrait = Path(self.temporary.name) / "tall-portrait.png"
        Image.new("RGBA", (128, 320)).save(tall_portrait)
        issues = validate_character(self.character, tall_portrait, self.sprite, appearances={"winter": {"portrait": self.portrait}})
        self.assertTrue(any(issue["level"] == "error" and "Winter appearance" in issue["message"] and "portrait $9" in issue["message"] for issue in issues))
        event["story"]["stage"] = "idea"
        issues = validate_character(self.character, self.portrait, self.sprite)
        self.assertFalse(any("portrait $9" in issue["message"] for issue in issues))

    def test_scene_defaults_are_shared_by_compiler_guide_and_artwork_checks(self):
        event = self.ready_event()
        self.character["events"] = [event]
        # Fields omitted by an API client still use the compiler's defaults.
        del event["story"]["beats"][0]["actor"]
        del event["story"]["beats"][1]["actor"]
        del event["story"]["beats"][1]["amount"]
        with self.archive() as archive:
            guide = archive.read("[CP] Mira/STORY_TESTING.txt").decode("utf-8")
            self.assertIn("+25 player friendship points", guide)
        event["story"]["beats"][0]["text"] = "Hello.$9"
        issues = validate_character(self.character, self.portrait, self.sprite)
        self.assertTrue(any(issue["level"] == "error" and "portrait $9" in issue["message"] for issue in issues))

    def test_external_actor_portrait_does_not_use_authored_npc_sheet(self):
        event = self.ready_event()
        self.character["events"] = [event]
        event["story"]["actors"].append({"id": "leah", "name": "Leah", "x": 30, "y": 67, "facing": 3})
        event["story"]["beats"][0].update(actor="Leah", text="Hello.$9")
        issues = validate_character(self.character, self.portrait, self.sprite)
        self.assertFalse(any("portrait $9" in issue["message"] for issue in issues))


if __name__ == "__main__":
    unittest.main()

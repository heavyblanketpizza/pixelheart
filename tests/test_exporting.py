"""Behavioral tests for export boundaries; no game installation is required."""

import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

from pixelheart_core.exporting import ExportValidationError, build_mod_archive, validate_character
from pixelheart_core.projects import load_project, resolve_artwork


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.portrait = Path(self.temporary.name) / "portraits.png"
        self.sprite = Path(self.temporary.name) / "sprites.png"
        Image.new("RGBA", (128, 192), (180, 90, 120, 255)).save(self.portrait)
        Image.new("RGBA", (64, 128), (90, 180, 120, 255)).save(self.sprite)
        self.data = {
            "id": "project-123", "name": "Mira", "internal_name": "Mira",
            "tagline": "A new beginning", "pronouns": "she/her", "age": "adult",
            "season": "spring", "day": 16, "romanceable": False,
            "manners": "polite", "social_anxiety": "outgoing", "optimism": "positive",
            "home_map": "Town", "home_x": 28, "home_y": 67,
            "dialogues": [{"id": "line1", "trigger": "Introduction", "text": "Hi, @! I'm Mira.$h"},
                          {"id": "line2", "trigger": "Mon", "text": "A new week.$h"}],
            "schedule": [{"id": "stop1", "time": "09:00", "location": "Town", "x": 28, "y": 67, "facing": "down", "activity": "Read a book"},
                         {"id": "stop2", "time": "1800", "location": "Town", "x": 29, "y": 67, "facing": 1}],
            "gifts": {"love": ["Sunflower", "66"], "like": ["Coffee"], "dislike": ["Clay"], "hate": ["Trash"]},
            "events": [{"id": "event1", "name": "By the river", "description": "A story idea."}],
            "relationships": [{"id": "rel1", "name": "Leah", "relation": "friend"}],
        }

    def errors(self, data=None, portrait=None, sprite=None):
        return [issue for issue in validate_character(data or self.data, portrait or self.portrait, sprite or self.sprite)
                if issue["level"] == "error"]

    def test_archive_contains_resolvable_assets_and_game_entries(self):
        original = copy.deepcopy(self.data)
        blob = build_mod_archive(self.data, self.portrait, self.sprite)
        self.assertEqual(self.data, original)
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            root = "[CP] Mira/"
            read = lambda name: json.loads(archive.read(root + name))
            manifest, content = read("manifest.json"), read("content.json")
            self.assertEqual(content["Format"], "2.9.0")
            self.assertEqual(manifest["ContentPackFor"]["UniqueID"], "Pathoschild.ContentPatcher")
            changes = content["Changes"]
            for patch in changes:
                if patch["Action"] == "Load":
                    self.assertIn(root + patch["FromFile"], archive.namelist())
            character_patch = next(patch for patch in changes if patch["Target"] == "Data/Characters")
            npc_id, npc = next(iter(character_patch["Entries"].items()))
            self.assertTrue(npc_id.startswith(manifest["UniqueID"] + "_"))
            self.assertEqual(npc["DisplayName"], "Mira")
            self.assertEqual(npc["Home"][0]["Tile"], {"X": 28, "Y": 67})
            self.assertEqual(npc["Gender"], "Female")
            self.assertEqual(npc["Age"], "Adult")
            self.assertFalse(npc["CanBeRomanced"])
            self.assertEqual(read("assets/dialogue.json")["Introduction"], "Hi, @! I'm Mira.$h")
            self.assertEqual(read("assets/schedule.json")["spring"], "900 Town 28 67 2/1800 Town 29 67 1")
            taste = next(patch for patch in changes if patch["Target"] == "Data/NPCGiftTastes")["Entries"][npc_id]
            fields = taste.split("/")
            self.assertEqual([fields[index] for index in (1, 3, 5, 7, 9)], ["421 66", "395", "330", "168", ""])
            self.assertEqual(archive.read(root + "assets/portraits.png"), self.portrait.read_bytes())
            self.assertEqual(archive.read(root + "assets/sprites.png"), self.sprite.read_bytes())
            self.assertEqual(read("project.json")["character"], original)
            self.assertFalse(any(patch["Target"].startswith("Data/Events") for patch in changes))

    def test_missing_artwork_is_a_blocker_even_with_preview_urls(self):
        self.data["portrait_url"] = "preview-portrait.png"
        issues = validate_character(self.data)
        self.assertEqual({issue["field"] for issue in issues if issue["level"] == "error"}, {"portrait", "sprite"})
        with self.assertRaisesRegex(ExportValidationError, "Provide a complete local"):
            build_mod_archive(self.data)

    def test_shared_birthday_blocks_export_without_changing_character(self):
        self.data.update(season="summer", day=26)
        original = copy.deepcopy(self.data)
        issues = validate_character(self.data, self.portrait, self.sprite)
        conflicts = [issue for issue in issues if issue["field"] == "day"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["level"], "error")
        self.assertIn("Leo", conflicts[0]["message"])
        with self.assertRaisesRegex(ExportValidationError, "Shared birthdays are not allowed"):
            build_mod_archive(self.data, self.portrait, self.sprite)
        self.assertEqual(self.data, original)

    def test_open_birthday_has_no_overlap_error(self):
        self.assertFalse([issue for issue in validate_character(self.data) if issue["field"] == "day"])

    def test_invalid_birthday_reports_errors_without_catalog_lookup_failure(self):
        for season, day, expected in [("monsoon", 4, "season"), ("spring", 29, "day"), ("spring", True, "day")]:
            with self.subTest(season=season, day=day):
                self.data.update(season=season, day=day)
                issues = validate_character(self.data)
                self.assertTrue(any(issue["field"] == expected and issue["level"] == "error" for issue in issues))

    def test_archive_includes_output_permissions_and_unofficial_disclaimer(self):
        with zipfile.ZipFile(io.BytesIO(build_mod_archive(self.data, self.portrait, self.sprite))) as archive:
            readme = archive.read("[CP] Mira/README.txt").decode("utf-8")
        # Whitespace is presentation; the granted permissions and restrictions
        # must travel with every exported pack.
        notice = " ".join(readme.split())
        for required in (
            "use, copy, modify, and distribute Pixelheart-owned template material",
            "free Stardew Valley NPC packs",
            "Recipients receive the same permission",
            "Retain this output-permission notice with the pack",
            "may not be sold, paywalled, require payment or a subscription for access, or earn Donation Points",
            "gameplay videos, recordings, and livestreams, including monetized content",
            "Pixelheart claims no ownership of uploaded artwork, authored dialogue, character data",
            "does not authorize distributing the Pixelheart editor",
            "Pixelheart is not affiliated with, sponsored by, or endorsed by ConcernedApe or ConcernedApe LLC",
            "Pixelheart grants no rights in those third-party works or marks",
        ):
            with self.subTest(required=required):
                self.assertIn(required, notice)

    def test_each_missing_artwork_path_returns_a_validation_error(self):
        for portrait, sprite, fields in (
            (None, self.sprite, {"portrait"}),
            (self.portrait, None, {"sprite"}),
            (None, None, {"portrait", "sprite"}),
        ):
            with self.subTest(fields=fields):
                with self.assertRaises(ExportValidationError) as raised:
                    build_mod_archive(self.data, portrait, sprite)
                self.assertEqual(
                    {issue["field"] for issue in raised.exception.issues if issue["level"] == "error"},
                    fields,
                )

    def test_string_artwork_paths_export_original_image_bytes(self):
        blob = build_mod_archive(self.data, str(self.portrait), str(self.sprite))
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            self.assertEqual(archive.read("[CP] Mira/assets/portraits.png"), self.portrait.read_bytes())
            self.assertEqual(archive.read("[CP] Mira/assets/sprites.png"), self.sprite.read_bytes())

    def test_sources_survive_export_with_asset_specific_credits_and_portable_paths(self):
        source = {"provider": "local-content-patcher", "asset": "Characters/Dialogue/Abigail",
                  "sha256": "a" * 64, "source_name": "Local Content Patcher export",
                  "attribution": "Original game content © ConcernedApe",
                  "source_url": "https://github.com/Pathoschild/StardewMods", "modified_game_possible": True}
        self.data["dialogues"][0]["source"] = copy.deepcopy(source)
        portrait_source = {**source, "asset": "Portraits/Abigail"}
        sprite_source = {**source, "asset": "Characters/Elliott"}
        document = {"character": copy.deepcopy(self.data), "artwork": {
            "portrait": {"original": "artwork/private-original-name.png", "prepared": "artwork/private-prepared.png",
                         "selected": "prepared", "source": portrait_source},
            "sprite": {"original": "artwork/private-upload.png", "selected": "original", "source_history": [sprite_source]},
            "variants": {"winter": {"portrait": {"original": "artwork/private-winter.png", "source": portrait_source}}},
        }}
        before = copy.deepcopy(document)
        blob = build_mod_archive(self.data, self.portrait, self.sprite, project_document=document,
                                 appearances={"winter": {"portrait": self.portrait}})
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            backup = json.loads(archive.read("[CP] Mira/project.json"))
            credits = archive.read("[CP] Mira/CREDITS.txt").decode()
            self.assertEqual(backup["character"]["dialogues"][0]["source"], source)
            self.assertEqual(backup["artwork"]["portrait"], {
                "original": "assets/portraits.png", "selected": "original", "source": portrait_source})
            self.assertEqual(backup["artwork"]["sprite"]["source_history"], [sprite_source])
            self.assertNotIn("source", backup["artwork"]["sprite"])
            self.assertEqual(backup["artwork"]["variants"]["winter"]["portrait"]["source"], portrait_source)
            self.assertIn("Dialogue (1 imported entries)", credits)
            self.assertIn("Default sprite — previous-sheet source (history only)", credits)
            self.assertIn("not permission to redistribute", credits)
            self.assertIn("changes from installed mods", credits)
            for private in ("private-original-name", "private-prepared", "private-upload", "private-winter", str(self.temporary.name)):
                self.assertNotIn(private, json.dumps(backup))
                self.assertNotIn(private, credits)
            extracted = Path(self.temporary.name) / "credited-export"
            archive.extractall(extracted)
        project_file = extracted / "[CP] Mira" / "project.json"
        reopened = load_project(project_file)
        self.assertEqual(resolve_artwork(reopened, project_file, "portrait").read_bytes(), self.portrait.read_bytes())
        self.assertEqual(reopened["artwork"]["sprite"]["source_history"], [sprite_source])
        self.assertEqual(document, before)

    def test_export_rejects_private_source_metadata(self):
        for source in ({"source_name": "/Users/private/game"}, {"path": "C:\\private\\sheet.png"}):
            with self.subTest(source=source):
                self.data["dialogues"][0]["source"] = source
                with self.assertRaises(ExportValidationError):
                    build_mod_archive(self.data, self.portrait, self.sprite)
        self.data["dialogues"][0].pop("source")
        document = {"character": self.data, "artwork": {"portrait": {
            "original": "artwork/a.png", "source_history": [{"url": "file:///Users/private/game"}]}}}
        with self.assertRaises(ExportValidationError):
            build_mod_archive(self.data, self.portrait, self.sprite, project_document=document)

    def test_full_dialogue_over_250_exports_without_truncation_and_upper_bound_blocks(self):
        self.data["dialogues"] = [{"id": str(i), "trigger": f"custom_{i}", "text": f"Line {i}"} for i in range(350)]
        with zipfile.ZipFile(io.BytesIO(build_mod_archive(self.data, self.portrait, self.sprite))) as archive:
            self.assertEqual(len(json.loads(archive.read("[CP] Mira/assets/dialogue.json"))), 350)
        self.data["dialogues"] = [{"id": str(i), "trigger": f"custom_{i}", "text": "Line"} for i in range(2001)]
        with self.assertRaisesRegex(ExportValidationError, "up to 2000"):
            build_mod_archive(self.data, self.portrait, self.sprite)

    def test_appearance_assets_preserve_bytes_and_have_resolvable_game_entries(self):
        winter_portrait = Path(self.temporary.name) / "winter-portrait.png"
        beach_sprite = Path(self.temporary.name) / "beach-sprite.png"
        Image.new("RGBA", (128, 256), (60, 90, 210, 255)).save(winter_portrait)
        Image.new("RGBA", (64, 128), (220, 170, 70, 255)).save(beach_sprite)
        appearances = {
            "winter": {"portrait": winter_portrait, "sprite": self.sprite},
            "beach": {"sprite": beach_sprite},
        }
        original = copy.deepcopy(appearances)
        blob = build_mod_archive(self.data, self.portrait, self.sprite, appearances=appearances)
        self.assertEqual(appearances, original)
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            root = "[CP] Mira/"
            content = json.loads(archive.read(root + "content.json"))
            changes = content["Changes"]
            npc_id, npc = next(iter(next(patch for patch in changes if patch["Target"] == "Data/Characters")["Entries"].items()))
            self.assertEqual(npc["TextureName"], npc_id)
            self.assertEqual(npc["CanVisitIsland"], "FALSE")
            self.assertEqual(npc["Appearance"], [
                {"Id": "winter", "Season": "winter", "Portrait": f"Portraits/{npc_id}_winter", "Sprite": f"Characters/{npc_id}_winter"},
                {"Id": "beach", "IsIslandAttire": True, "Sprite": f"Characters/{npc_id}_beach"},
            ])
            loads = {patch["Target"]: patch["FromFile"] for patch in changes if patch["Action"] == "Load"}
            self.assertEqual(len(loads), sum(patch["Action"] == "Load" for patch in changes))
            for variant, sheets in appearances.items():
                appearance = next(entry for entry in npc["Appearance"] if entry["Id"] == variant)
                for kind, path in sheets.items():
                    filename = f"assets/appearances/{variant}/{kind}s.png"
                    self.assertEqual(loads[appearance[kind.title()]], filename)
                    self.assertEqual(archive.read(root + filename), path.read_bytes())
            issues = json.loads(archive.read(root + "validation.json"))
            self.assertTrue(any(issue["field"] == "appearances.beach" and "remain disabled" in issue["message"] for issue in issues))
            project = json.loads(archive.read(root + "project.json"))
            self.assertEqual(project["character"], self.data)
            self.assertEqual(project["artwork"], {
                "portrait": "assets/portraits.png", "sprite": "assets/sprites.png",
                "variants": {
                    "winter": {"portrait": "assets/appearances/winter/portraits.png", "sprite": "assets/appearances/winter/sprites.png"},
                    "beach": {"sprite": "assets/appearances/beach/sprites.png"},
                },
            })
            extracted = Path(self.temporary.name) / "extracted"
            archive.extractall(extracted)
        project_file = extracted / "[CP] Mira" / "project.json"
        reopened = load_project(project_file)
        self.assertEqual(resolve_artwork(reopened, project_file, "portrait").read_bytes(), self.portrait.read_bytes())
        self.assertEqual(resolve_artwork(reopened, project_file, "portrait", variant="winter").read_bytes(), winter_portrait.read_bytes())
        self.assertEqual(resolve_artwork(reopened, project_file, "sprite", variant="beach").read_bytes(), beach_sprite.read_bytes())
        self.assertIsNone(resolve_artwork(reopened, project_file, "portrait", variant="beach"))

    def test_partial_seasonal_sheets_fall_back_to_default_artwork(self):
        appearances = {
            "spring": {"portrait": self.portrait},
            "summer": {"sprite": self.sprite},
            "fall": {"portrait": None, "sprite": self.sprite},
        }
        with zipfile.ZipFile(io.BytesIO(build_mod_archive(self.data, self.portrait, self.sprite, appearances=appearances))) as archive:
            content = json.loads(archive.read("[CP] Mira/content.json"))
            npc = next(iter(next(patch for patch in content["Changes"] if patch["Target"] == "Data/Characters")["Entries"].values()))
            for appearance in npc["Appearance"]:
                self.assertEqual(appearance["Id"], appearance["Season"])
                self.assertNotIn("IsIslandAttire", appearance)
            self.assertNotIn("Sprite", npc["Appearance"][0])
            self.assertNotIn("Portrait", npc["Appearance"][1])
            self.assertNotIn("Portrait", npc["Appearance"][2])
            self.assertNotIn("[CP] Mira/assets/appearances/spring/sprites.png", archive.namelist())

    def test_empty_appearance_sets_preserve_default_archive_data(self):
        def contents(appearances):
            blob = build_mod_archive(self.data, self.portrait, self.sprite, appearances=appearances)
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                return {name: archive.read(name) for name in archive.namelist()}
        default = contents(None)
        for appearances in ({}, {"spring": None, "summer": {}, "winter": {"portrait": None, "sprite": None}}):
            with self.subTest(appearances=appearances):
                self.assertEqual(contents(appearances), default)

    def test_alternate_sheets_do_not_replace_required_default_sheets(self):
        appearances = {"winter": {"portrait": self.portrait, "sprite": self.sprite}}
        with self.assertRaises(ExportValidationError) as raised:
            build_mod_archive(self.data, appearances=appearances)
        self.assertEqual({issue["field"] for issue in raised.exception.issues if issue["level"] == "error"}, {"portrait", "sprite"})

    def test_appearance_validation_identifies_the_affected_sheet(self):
        invalid = Path(self.temporary.name) / "invalid.png"
        for kind, size in (("portrait", (64, 64)), ("sprite", (64, 96))):
            Image.new("RGBA", size).save(invalid)
            with self.subTest(kind=kind):
                appearances = {"winter": {kind: invalid}}
                with self.assertRaises(ExportValidationError) as raised:
                    build_mod_archive(self.data, self.portrait, self.sprite, appearances=appearances)
                self.assertEqual({issue["field"] for issue in raised.exception.issues if issue["level"] == "error"}, {f"appearances.winter.{kind}"})
        invalid.write_bytes(b"not an image")
        issues = validate_character(self.data, self.portrait, self.sprite, appearances={"beach": {"portrait": invalid}})
        self.assertTrue(any(issue["field"] == "appearances.beach.portrait" and issue["level"] == "error" for issue in issues))

    def test_appearance_sprite_requires_romance_frames_too(self):
        self.data["romanceable"] = True
        full_sprite = Path(self.temporary.name) / "full-sprite.png"
        Image.new("RGBA", (64, 416)).save(full_sprite)
        with self.assertRaises(ExportValidationError) as raised:
            build_mod_archive(self.data, self.portrait, full_sprite, appearances={"winter": {"sprite": self.sprite}})
        self.assertTrue(any(issue["field"] == "appearances.winter.sprite" and "416" in issue["message"] for issue in raised.exception.issues))

    def test_dialogue_portrait_indices_must_exist_in_each_appearance(self):
        tall_portrait = Path(self.temporary.name) / "tall-portrait.png"
        Image.new("RGBA", (128, 320)).save(tall_portrait)
        self.data["dialogues"][0]["text"] = "Hello.$9"
        issues = validate_character(self.data, tall_portrait, self.sprite, appearances={"winter": {"portrait": self.portrait}})
        errors = [issue for issue in issues if issue["level"] == "error"]
        self.assertEqual([issue["field"] for issue in errors], ["appearances.winter.portrait"])
        self.assertIn("Portrait $9", errors[0]["message"])
        self.assertIn("dialogue 1", errors[0]["message"])

    def test_malformed_appearances_block_export_without_creating_unsafe_paths(self):
        for appearances in (
            [], "winter", {"../outside": {"portrait": self.portrait}},
            {"winter": "winter.png"}, {"winter": {"other": self.portrait}},
            {"winter": {"portrait": []}}, {"winter": {"portrait": ""}},
        ):
            with self.subTest(appearances=appearances):
                with self.assertRaises(ExportValidationError) as raised:
                    build_mod_archive(self.data, self.portrait, self.sprite, appearances=appearances)
                self.assertTrue(any(issue["field"].startswith("appearances") and issue["level"] == "error" for issue in raised.exception.issues))

    def test_only_adult_characters_can_be_exported(self):
        Image.new("RGBA", (64, 416)).save(self.sprite)
        for romanceable in (True, False):
            for age in ("teen", "child", "unknown", "", None, True):
                with self.subTest(age=age, romanceable=romanceable):
                    data = {**self.data, "age": age, "romanceable": romanceable}
                    original = copy.deepcopy(data)
                    with self.assertRaisesRegex(ExportValidationError, "adult love-interest") as raised:
                        build_mod_archive(data, self.portrait, self.sprite)
                    self.assertEqual(
                        {issue["field"] for issue in raised.exception.issues if issue["level"] == "error"},
                        {"age"},
                    )
                    self.assertEqual(data, original)

    def test_missing_age_defaults_to_adult_without_mutating_project(self):
        del self.data["age"]
        blob = build_mod_archive(self.data, self.portrait, self.sprite)
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            content = json.loads(archive.read("[CP] Mira/content.json"))
            patch = next(patch for patch in content["Changes"] if patch["Target"] == "Data/Characters")
            self.assertEqual(next(iter(patch["Entries"].values()))["Age"], "Adult")
        self.assertNotIn("age", self.data)

    def test_corrupt_or_single_portrait_image_is_rejected(self):
        self.portrait.write_bytes(b"not an image")
        self.assertTrue(any(issue["field"] == "portrait" for issue in self.errors()))
        Image.new("RGB", (64, 64)).save(self.portrait)
        self.assertTrue(any("64×64" in issue["message"] for issue in self.errors()))

    def test_romance_requires_frames_and_warns_about_unimplemented_content(self):
        self.data["romanceable"] = True
        self.assertTrue(any("416" in issue["message"] for issue in self.errors()))
        Image.new("RGBA", (64, 416)).save(self.sprite)
        self.assertFalse(self.errors())
        issues = validate_character(self.data, self.portrait, self.sprite)
        self.assertTrue(any(issue["field"] == "romanceable" and "default spouse room" in issue["message"] for issue in issues))
        with zipfile.ZipFile(io.BytesIO(build_mod_archive(self.data, self.portrait, self.sprite))) as archive:
            content = json.loads(archive.read("[CP] Mira/content.json"))
            self.assertTrue(any(patch["Target"] == "Data/EngagementDialogue" for patch in content["Changes"]))

    def test_invalid_time_coordinate_and_direction_block_export(self):
        for field, value in [("time", "09:75"), ("time", "09:05"), ("time", "27:00"),
                             ("x", -1), ("x", 1.5), ("y", None), ("facing", 4), ("location", "Town/900 Beach 1 2")]:
            with self.subTest(field=field, value=value):
                invalid = copy.deepcopy(self.data)
                invalid["schedule"][0][field] = value
                self.assertTrue(any(issue["field"] == "schedule.0" for issue in self.errors(invalid)))
                with self.assertRaises(ExportValidationError):
                    build_mod_archive(invalid, self.portrait, self.sprite)

    def test_duplicate_times_and_dialogues_are_reported(self):
        self.data["schedule"][1]["time"] = "09:00"
        self.data["dialogues"].append(dict(self.data["dialogues"][0]))
        errors = self.errors()
        self.assertTrue(any("duplicate times" in issue["message"] for issue in errors))
        self.assertTrue(any("repeated" in issue["message"] for issue in errors))

    def test_unknown_gifts_are_not_silently_written_as_game_ids(self):
        self.data["gifts"]["love"] = ["imaginary cupcake"]
        self.assertTrue(any("Unknown gift" in issue["message"] for issue in self.errors()))
        self.data["gifts"]["love"] = ["id:Creator.Cupcake", "tag:category_fish", "-5", "(O)66"]
        self.assertFalse(self.errors())
        self.data["gifts"]["hate"].append("Amethyst")
        self.assertTrue(any("more than one taste" in issue["message"] for issue in self.errors()))

    def test_internal_name_cannot_create_unsafe_zip_paths(self):
        self.data["internal_name"] = "../../outside"
        with self.assertRaises(ExportValidationError):
            build_mod_archive(self.data, self.portrait, self.sprite)

    def test_custom_portrait_index_must_exist_in_sheet(self):
        self.data["dialogues"][0]["text"] = "Hello.$9"
        self.assertTrue(any("Portrait $9" in issue["message"] for issue in self.errors()))

    def test_oversized_portrait_indices_return_validation_errors_without_crashing(self):
        self.data["dialogues"][0]["text"] = "Hello.$" + "9" * 6000
        errors = self.errors()
        self.assertTrue(any("outside your 6-frame" in issue["message"] for issue in errors))
        self.assertTrue(all(len(issue["message"]) < 500 for issue in errors))
        with self.assertRaises(ExportValidationError):
            build_mod_archive(self.data, self.portrait, self.sprite)
        self.data["dialogues"][0]["text"] = "Hello.$" + "0" * 6000 + "1"
        self.assertFalse(self.errors())

    def test_malformed_nested_data_returns_actionable_issues(self):
        for field, value in [("dialogues", None), ("dialogues", [None]), ("schedule", None),
                             ("schedule", [None]), ("gifts", None), ("gifts", {"love": [None]})]:
            with self.subTest(field=field, value=value):
                invalid = copy.deepcopy(self.data)
                invalid[field] = value
                self.assertTrue(self.errors(invalid))

    def test_unserializable_author_notes_block_export_without_crashing(self):
        cyclic = []
        cyclic.append(cyclic)
        for notes in (b"bytes", {"not", "json"}, float("nan"), float("inf"), "\ud800", cyclic):
            with self.subTest(note_type=type(notes).__name__):
                invalid = {**self.data, "events": notes}
                errors = self.errors(invalid)
                self.assertTrue(any(issue["field"] == "project" and "JSON" in issue["message"] for issue in errors))
                with self.assertRaises(ExportValidationError):
                    build_mod_archive(invalid, self.portrait, self.sprite)

    def test_unicode_authoring_content_survives_archive_round_trip(self):
        self.data["name"] = "미라 🌻"
        self.data["bio"] = "春の花とカフェを愛する村人。"
        self.data["events"][0]["description"] = "Une rencontre au bord de l’eau."
        blob = build_mod_archive(self.data, self.portrait, self.sprite)
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            project = json.loads(archive.read("[CP] Mira/project.json"))
            self.assertEqual(project["character"], self.data)
            self.assertIn(self.data["name"], archive.read("[CP] Mira/README.txt").decode("utf-8"))

    def test_stable_project_id_produces_stable_mod_identity(self):
        def manifest(data):
            with zipfile.ZipFile(io.BytesIO(build_mod_archive(data, self.portrait, self.sprite))) as archive:
                return json.loads(archive.read("[CP] Mira/manifest.json"))
        first = manifest(self.data)
        self.data["name"] = "Mira's new display name"
        self.assertEqual(first["UniqueID"], manifest(self.data)["UniqueID"])
        self.data["id"] = "another-project"
        self.assertNotEqual(first["UniqueID"], manifest(self.data)["UniqueID"])


if __name__ == "__main__":
    unittest.main()

"""Explicit game gender stays stable across migration, editing, and export."""

import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from PIL import Image

from pixelheart_core.exporting import ExportValidationError, build_mod_archive, validate_character
from pixelheart_core.projects import ProjectError, load_project, new_project, save_project
from pixelheart_core.validation import DraftValidationError, GENDERS, validate_draft


class GenderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.file = self.root / "character.json"
        self.document = new_project()
        self.character = self.document["character"]
        self.character["romanceable"] = False
        self.portrait = self.root / "portrait.png"
        self.sprite = self.root / "sprite.png"
        Image.new("RGBA", (128, 192)).save(self.portrait)
        Image.new("RGBA", (64, 128)).save(self.sprite)

    def exported(self):
        blob = build_mod_archive(self.character, self.portrait, self.sprite)
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            root = "[CP] NewCharacter/"
            content = json.loads(archive.read(root + "content.json"))
            patch = next(patch for patch in content["Changes"] if patch["Target"] == "Data/Characters")
            npc = next(iter(patch["Entries"].values()))
            backup = json.loads(archive.read(root + "project.json"))
        return npc, backup

    def test_new_project_uses_explicit_undefined_gender(self):
        self.assertEqual(self.character["gender"], "Undefined")
        self.assertEqual(validate_draft({"gender": "Undefined"}), {"gender": "Undefined"})
        self.character["pronouns"] = "she/her"
        self.assertEqual(self.exported()[0]["Gender"], "Undefined")

    def test_legacy_envelopes_migrate_exact_pronouns_and_preserve_metadata(self):
        for pronouns, expected in (
            ("he/him", "Male"), ("she/her", "Female"), ("they/them", "Undefined"),
            ("she/they", "Undefined"), ("she / her", "Undefined"), ("She/Her", "Undefined"),
        ):
            for envelope in ("bare", "unversioned", "zero", "current"):
                with self.subTest(pronouns=pronouns, envelope=envelope):
                    character = copy.deepcopy(self.character)
                    character.pop("gender")
                    character.update(pronouns=pronouns, extension={"keep": "this"})
                    if envelope == "bare":
                        legacy = character
                    elif envelope == "unversioned":
                        legacy = {"character": character}
                    else:
                        legacy = {"format": "pixelheart-project", "version": 0 if envelope == "zero" else 1,
                                  "character": character}
                    self.file.write_text(json.dumps(legacy), encoding="utf-8")
                    loaded = load_project(self.file)["character"]
                    self.assertEqual(loaded["gender"], expected)
                    self.assertEqual({key: loaded[key] for key in character}, character)

    def test_migration_is_once_and_pronouns_remain_authoring_metadata(self):
        self.character.pop("gender")
        self.character["pronouns"] = "she/her"
        before = copy.deepcopy(self.document)
        save_project(self.document, self.file)
        self.assertEqual(self.document, before)
        loaded = load_project(self.file)
        self.assertEqual(loaded["character"]["gender"], "Female")
        loaded["character"]["pronouns"] = "he/him"
        save_project(loaded, self.file)
        self.assertEqual(load_project(self.file), loaded)
        self.assertEqual(load_project(self.file)["character"]["gender"], "Female")

    def test_missing_legacy_pronouns_migrate_to_undefined(self):
        self.character.pop("gender")
        self.character.pop("pronouns")
        save_project(self.document, self.file)
        self.assertEqual(load_project(self.file)["character"]["gender"], "Undefined")

    def test_all_explicit_genders_roundtrip_and_override_conflicting_pronouns(self):
        for gender in GENDERS:
            with self.subTest(gender=gender):
                self.character.update(gender=gender, pronouns="he/him" if gender == "Female" else "she/her")
                before = copy.deepcopy(self.document)
                save_project(self.document, self.file)
                self.assertEqual(load_project(self.file), before)
                npc, backup = self.exported()
                self.assertEqual(npc["Gender"], gender)
                self.assertEqual(backup["character"], before["character"])
                self.assertEqual(self.document, before)

    def test_invalid_explicit_gender_is_not_inferred_or_silently_corrected(self):
        for invalid in ("female", "Woman", "Unspecified", "", " Female ", None, False, 1, [], {}):
            with self.subTest(gender=invalid):
                self.character.update(gender=invalid, pronouns="she/her")
                with self.assertRaises(DraftValidationError) as draft_error:
                    validate_draft({"gender": invalid})
                self.assertIn("gender", draft_error.exception.errors)
                with self.assertRaisesRegex(ProjectError, "gender"):
                    save_project(self.document, self.file)
                issues = validate_character(self.character, self.portrait, self.sprite)
                self.assertTrue(any(issue["field"] == "gender" and issue["level"] == "error" for issue in issues))
                with self.assertRaises(ExportValidationError):
                    self.exported()

    def test_direct_legacy_exports_retain_pronoun_compatibility(self):
        self.character.pop("gender")
        for pronouns, expected in (("he/him", "Male"), (" She / Her ", "Female"), ("they/them", "Undefined")):
            with self.subTest(pronouns=pronouns):
                self.character["pronouns"] = pronouns
                npc, backup = self.exported()
                self.assertEqual(npc["Gender"], expected)
                self.assertNotIn("gender", backup["character"])

    def test_mod_home_ids_with_dots_and_hyphens_survive_storage(self):
        for location in ("Author.Mod_Custom-Home", "A" + "x" * 79):
            with self.subTest(location=location):
                self.character["home_map"] = location
                save_project(self.document, self.file)
                self.assertEqual(load_project(self.file)["character"]["home_map"], location)
                self.assertEqual(self.exported()[0]["Home"][0]["Location"], location)

    def test_home_ids_reject_paths_spaces_and_invalid_initial_characters(self):
        for location in ("Maps/CustomHome", "Custom Home", "123Home", "_Home", "A" + "x" * 80):
            with self.subTest(location=location):
                self.character["home_map"] = location
                with self.assertRaisesRegex(ProjectError, "home_map"):
                    save_project(self.document, self.file)


if __name__ == "__main__":
    unittest.main()

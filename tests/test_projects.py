"""Storage, migration, portability and interrupted-write regressions."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import uuid

from pixelheart_core.projects import (
    APPEARANCE_VARIANTS,
    ProjectError,
    copy_project,
    import_artwork,
    load_project,
    new_project,
    project_path,
    resolve_artwork,
    save_project,
)
from pixelheart_core.validation import EDITABLE_FIELDS, validate_draft


class ProjectTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.file = self.root / "project" / "character.json"
        self.document = new_project()

    def write_raw(self, value):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(value), encoding="utf-8")
        return self.file


class ProjectTests(ProjectTestCase):
    def test_new_project_is_independent_and_valid_adult_starter(self):
        another = new_project()
        character = self.document["character"]
        self.assertEqual(character["name"], "New character")
        self.assertEqual(character["internal_name"], "NewCharacter")
        self.assertEqual(character["age"], "adult")
        self.assertTrue(character["romanceable"])
        self.assertEqual(str(uuid.UUID(character["id"])), character["id"])
        self.assertNotEqual(character["id"], another["character"]["id"])
        self.assertNotEqual(character["dialogues"][0]["id"], another["character"]["dialogues"][0]["id"])
        self.assertEqual(character["dialogues"][0]["trigger"], "Introduction")
        validate_draft({key: value for key, value in character.items() if key in EDITABLE_FIELDS})
        self.document["character"]["gifts"]["love"].append("Coffee")
        self.assertEqual(another["character"]["gifts"]["love"], [])

    def test_roundtrip_retains_identity_text_timestamps_and_unknown_metadata(self):
        self.document["character"].update(id="old-project-id", internal_name="ExistingNPC", created_at="original timestamp", updated_at="saved timestamp", bio="  A story.\n또 만나요!  ", plugin={"author": "Ada"})
        self.document["character"]["dialogues"][0]["extension"] = {"mood": "warm"}
        self.document["character"]["gifts"]["extension"] = {"seasonal": True}
        self.document["artwork"]["license"] = "Creator owned"
        self.document["workspace"] = {"selected_tab": 2}
        before = copy.deepcopy(self.document)
        self.assertEqual(save_project(self.document, self.file), self.file)
        self.assertEqual(load_project(self.file), before)
        self.assertEqual(self.document, before)
        save_project(load_project(self.file), self.file)
        self.assertEqual(load_project(self.file), before)

    def test_existing_and_new_folders_target_character_json(self):
        folder = self.root / "new project"
        self.assertEqual(save_project(self.document, folder), folder / "character.json")
        self.assertEqual(load_project(folder), self.document)
        dotted = self.root / "project.v1"
        dotted.mkdir()
        self.assertEqual(project_path(dotted), dotted / "character.json")

    def test_incomplete_drafts_save_without_export_requirements(self):
        character = self.document["character"]
        character.update(name="", internal_name="", home_map="", dialogues=[], schedule=[], events=[], relationships=[], gifts={})
        save_project(self.document, self.file)
        self.assertEqual(load_project(self.file), self.document)
        self.assertIsNone(resolve_artwork(self.document, self.file, "portrait"))

    def test_invalid_save_does_not_replace_previous_file_or_mutate_input(self):
        save_project(self.document, self.file)
        previous = self.file.read_bytes()
        self.document["character"]["age"] = "teen"
        before = copy.deepcopy(self.document)
        with self.assertRaisesRegex(ProjectError, "adult love-interest"):
            save_project(self.document, self.file)
        self.assertEqual(self.file.read_bytes(), previous)
        self.assertEqual(self.document, before)

    def test_failed_replace_keeps_old_file_and_cleans_temporary_file(self):
        save_project(self.document, self.file)
        previous = self.file.read_bytes()
        self.document["character"]["name"] = "Changed"
        with patch("pixelheart_core.projects.os.replace", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(ProjectError, "disk full"):
                save_project(self.document, self.file)
        self.assertEqual(self.file.read_bytes(), previous)
        self.assertEqual(list(self.file.parent.iterdir()), [self.file])

    def test_failed_flush_keeps_old_file_and_cleans_temporary_file(self):
        save_project(self.document, self.file)
        previous = self.file.read_bytes()
        with patch("pixelheart_core.projects.os.fsync", side_effect=OSError("write failed")):
            with self.assertRaisesRegex(ProjectError, "write failed"):
                save_project(self.document, self.file)
        self.assertEqual(self.file.read_bytes(), previous)
        self.assertEqual(list(self.file.parent.iterdir()), [self.file])

    def test_missing_corrupt_and_non_object_files_have_actionable_errors(self):
        with self.assertRaisesRegex(ProjectError, "Could not open project"):
            load_project(self.file)
        self.file.parent.mkdir()
        for data in (b"{incomplete", b"\xff", b"[]", b"null", b'"hello"', b"{}"):
            with self.subTest(data=data):
                self.file.write_bytes(data)
                with self.assertRaises(ProjectError):
                    load_project(self.file)

    def test_future_versions_and_unrelated_formats_are_rejected(self):
        for version in (2, -1, "1", True, None, 1.0):
            with self.subTest(version=version):
                document = {**self.document, "version": version}
                with self.assertRaisesRegex(ProjectError, "Unsupported.*version"):
                    load_project(self.write_raw(document))
        with self.assertRaisesRegex(ProjectError, "not a Pixelheart"):
            load_project(self.write_raw({**self.document, "format": "another-app"}))

    def test_non_adult_load_and_save_are_rejected_even_without_romance(self):
        for age in ("teen", "child", "Adult", "", None, True, 18, [], {}):
            for romance in (False, True):
                with self.subTest(age=age, romance=romance):
                    self.document["character"].update(age=age, romanceable=romance)
                    with self.assertRaisesRegex(ProjectError, "adult love-interest"):
                        load_project(self.write_raw(self.document))
                    with self.assertRaisesRegex(ProjectError, "adult love-interest"):
                        save_project(self.document, self.file)

    def test_malformed_known_fields_are_rejected_without_dropping_metadata(self):
        for field, value in (("name", 7), ("id", None), ("dialogues", [None]), ("schedule", {}), ("gifts", []), ("romanceable", "yes"), ("events", [{"hearts": True}]), ("relationships", [{"name": []}])):
            with self.subTest(field=field):
                document = copy.deepcopy(self.document)
                document["character"][field] = value
                with self.assertRaises(ProjectError):
                    load_project(self.write_raw(document))
        for field, value in (("character", []), ("artwork", None), ("artwork", [])):
            with self.subTest(field=field, value=value):
                with self.assertRaises(ProjectError):
                    load_project(self.write_raw({**self.document, field: value}))

    def test_non_json_values_nonfinite_numbers_and_cycles_are_rejected(self):
        cycle = []
        cycle.append(cycle)
        for value in (float("nan"), float("inf"), Path("asset.png"), {1: "lost key type"}, (1, 2), cycle):
            with self.subTest(value=type(value)):
                with self.assertRaises(ProjectError):
                    save_project({**self.document, "metadata": value}, self.file)
        self.assertFalse(self.file.exists())

    def test_bare_character_and_unversioned_envelope_migrate_without_identity_change(self):
        character = {"id": "legacy-id", "name": "Ada", "internal_name": "ForeverAda", "dialogues": [{"id": "entry-1", "text": "Original text"}], "notes": {"keep": "me"}}
        for legacy in (character, {"character": character, "custom": 42}, {"format": "pixelheart-project", "version": 0, "character": character}):
            with self.subTest(legacy=legacy):
                loaded = load_project(self.write_raw(legacy))
                self.assertEqual(loaded["format"], "pixelheart-project")
                self.assertEqual(loaded["version"], 1)
                self.assertEqual(loaded["character"]["id"], "legacy-id")
                self.assertEqual(loaded["character"]["internal_name"], "ForeverAda")
                self.assertEqual(loaded["character"]["dialogues"], [{**character["dialogues"][0], "trigger": "Introduction"}])
                self.assertEqual(loaded["character"]["notes"], {"keep": "me"})
                self.assertEqual(loaded["character"]["age"], "adult")
                self.assertEqual(loaded["character"]["schedule"], [])
                if "custom" in legacy:
                    self.assertEqual(loaded["custom"], 42)
                save_project(loaded, self.file)
                self.assertEqual(load_project(self.file), loaded)

    def test_missing_legacy_ids_are_generated_once_and_preserved_on_save(self):
        loaded = load_project(self.write_raw({"name": "The Writer", "dialogues": [{"text": "Hi"}]}))
        self.assertEqual(loaded["character"]["internal_name"], "TheWriter")
        uuid.UUID(loaded["character"]["id"])
        uuid.UUID(loaded["character"]["dialogues"][0]["id"])
        save_project(loaded, self.file)
        self.assertEqual(load_project(self.file), loaded)

    def test_utf8_bom_project_opens(self):
        self.file.parent.mkdir()
        self.file.write_text(json.dumps(self.document), encoding="utf-8-sig")
        self.assertEqual(load_project(self.file), self.document)

    def test_missing_nested_fields_receive_editable_defaults_with_extensions_preserved(self):
        legacy = {"name": "Ada", "schedule": [{"extension": {"note": "Keep me"}}], "events": [{}], "relationships": [{}], "dialogues": [{}]}
        loaded = load_project(self.write_raw(legacy))
        character = loaded["character"]
        self.assertEqual(character["schedule"][0]["x"], 32)
        self.assertEqual(character["schedule"][0]["y"], 62)
        self.assertEqual(character["schedule"][0]["time"], "600")
        self.assertEqual(character["schedule"][0]["extension"], {"note": "Keep me"})
        self.assertEqual(character["events"][0]["hearts"], 2)
        self.assertEqual(character["relationships"][0]["relation"], "Friend")
        self.assertEqual(character["dialogues"][0]["trigger"], "Introduction")
        save_project(loaded, self.file)
        self.assertEqual(load_project(self.file), loaded)

    def test_unpaired_unicode_is_rejected_on_load_and_save_in_values_and_keys(self):
        for metadata in ({"notes": "\ud800"}, {"\udfff": "bad key"}):
            with self.subTest(metadata=repr(metadata)):
                document = {**self.document, "metadata": metadata}
                with self.assertRaisesRegex(ProjectError, "serialized"):
                    load_project(self.write_raw(document))
                with self.assertRaisesRegex(ProjectError, "serialized"):
                    save_project(document, self.file)


class ArtworkStorageTests(ProjectTestCase):
    def setUp(self):
        super().setUp()
        self.source = self.root / "My portrait.PNG"
        self.source.write_bytes(b"original image bytes")

    def test_import_preserves_bytes_and_deduplicates_by_content(self):
        relative = import_artwork(self.source, self.file, "portrait")
        self.assertTrue(relative.startswith("artwork/originals/portrait-"))
        self.assertTrue(relative.endswith(".png"))
        self.assertEqual((self.file.parent / relative).read_bytes(), self.source.read_bytes())
        self.assertEqual(import_artwork(self.source, self.file, "portrait"), relative)
        self.source.write_bytes(b"different image bytes")
        another = import_artwork(self.source, self.file, "portrait")
        self.assertNotEqual(another, relative)
        self.assertEqual((self.file.parent / relative).read_bytes(), b"original image bytes")

    def test_portable_relative_paths_resolve_after_moving_whole_folder(self):
        relative = import_artwork(self.source, self.file, "portrait")
        self.document["artwork"]["portrait"] = relative
        save_project(self.document, self.file)
        moved = self.root / "moved elsewhere"
        shutil.move(self.file.parent, moved)
        loaded = load_project(moved)
        self.assertEqual(resolve_artwork(loaded, moved, "portrait"), moved / relative)
        self.assertEqual(resolve_artwork(loaded, moved, "portrait").read_bytes(), self.source.read_bytes())

    def test_structured_records_choose_original_or_prepared_and_keep_options(self):
        original = import_artwork(self.source, self.file, "portrait")
        prepared = self.file.parent / "artwork" / "prepared.png"
        prepared.write_bytes(b"prepared bytes")
        record = {"original": original, "prepared": "artwork/prepared.png", "selected": "prepared", "options": {"pixel_size": 2}}
        self.document["artwork"]["portrait"] = record
        save_project(self.document, self.file)
        loaded = load_project(self.file)
        self.assertEqual(loaded["artwork"]["portrait"], record)
        self.assertEqual(resolve_artwork(loaded, self.file, "portrait"), prepared)
        record["selected"] = "original"
        self.assertEqual(resolve_artwork(self.document, self.file, "portrait"), self.file.parent / original)

    def test_missing_referenced_files_remain_editable_but_copy_reports_failure(self):
        self.document["artwork"]["sprite"] = "artwork/missing.png"
        save_project(self.document, self.file)
        self.assertEqual(resolve_artwork(load_project(self.file), self.file, "sprite"), self.file.parent / "artwork/missing.png")
        destination = self.root / "copy" / "character.json"
        with self.assertRaisesRegex(ProjectError, "Could not import artwork"):
            copy_project(self.document, self.file, destination)
        self.assertFalse(destination.exists())

    def test_traversal_absolute_and_windows_drive_paths_are_rejected(self):
        for path in ("../outside.png", "artwork/../../outside.png", "/tmp/outside.png", "C:\\outside.png", "C:outside.png", "\\\\server\\share\\file.png", "..\\outside.png", "artwork/image.png:stream", "", ".", "a\x00.png"):
            with self.subTest(path=path):
                self.document["artwork"]["portrait"] = path
                with self.assertRaises(ProjectError):
                    resolve_artwork(self.document, self.file, "portrait")
                with self.assertRaises(ProjectError):
                    save_project(self.document, self.file)
                with self.assertRaises(ProjectError):
                    load_project(self.write_raw(self.document))

    def test_windows_relative_separators_are_portable(self):
        self.document["artwork"]["portrait"] = "artwork\\portrait.png"
        self.assertEqual(resolve_artwork(self.document, self.file, "portrait"), self.file.parent / "artwork/portrait.png")

    def test_invalid_records_and_unselected_unsafe_paths_are_rejected(self):
        for record in ([], 42, {}, {"original": None}, {"original": "artwork/a.png", "selected": "missing"}, {"original": "artwork/a.png", "selected": "prepared"}, {"original": "artwork/a.png", "prepared": "../outside.png"}, {"original": "artwork/a.png", "selected": []}):
            with self.subTest(record=record):
                self.document["artwork"]["portrait"] = record
                with self.assertRaises(ProjectError):
                    save_project(self.document, self.file)
        for kind in ("other", "../../outside"):
            with self.assertRaises(ProjectError):
                import_artwork(self.source, self.file, kind)
            with self.assertRaises(ProjectError):
                resolve_artwork(self.document, self.file, kind)

    def make_symlink(self, link, target, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks are unavailable on this platform.")

    def test_external_symlinks_cannot_read_or_write_artwork(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "image.png").write_bytes(b"private outside bytes")
        self.file.parent.mkdir()
        self.make_symlink(self.file.parent / "artwork", outside, directory=True)
        self.document["artwork"]["portrait"] = "artwork/image.png"
        with self.assertRaises(ProjectError):
            resolve_artwork(self.document, self.file, "portrait")
        with self.assertRaises(ProjectError):
            import_artwork(self.source, self.file, "portrait")
        with self.assertRaises(ProjectError):
            save_project(self.document, self.file)
        self.assertEqual(list(outside.iterdir()), [outside / "image.png"])

    def test_project_file_symlink_cannot_overwrite_original(self):
        save_project(self.document, self.file)
        original = self.file.read_bytes()
        link = self.root / "linked.json"
        self.make_symlink(link, self.file)
        changed = copy.deepcopy(self.document)
        changed["character"]["name"] = "Changed"
        with self.assertRaisesRegex(ProjectError, "symlink"):
            save_project(changed, link)
        with self.assertRaisesRegex(ProjectError, "symlink"):
            copy_project(changed, self.file, link)
        self.assertEqual(self.file.read_bytes(), original)

    def test_save_as_folder_symlink_cannot_overwrite_source_project(self):
        save_project(self.document, self.file)
        original = self.file.read_bytes()
        link = self.root / "linked project"
        self.make_symlink(link, self.file.parent, directory=True)
        changed = copy.deepcopy(self.document)
        changed["character"]["name"] = "Changed"
        with self.assertRaisesRegex(ProjectError, "symlink"):
            copy_project(changed, self.file, link)
        self.assertEqual(self.file.read_bytes(), original)

    def test_import_does_not_overwrite_corrupted_existing_content_addressed_file(self):
        relative = import_artwork(self.source, self.file, "portrait")
        target = self.file.parent / relative
        target.write_bytes(b"unexpected replacement")
        with self.assertRaisesRegex(ProjectError, "conflicting contents"):
            import_artwork(self.source, self.file, "portrait")
        self.assertEqual(target.read_bytes(), b"unexpected replacement")
        self.assertEqual(len(list(target.parent.iterdir())), 1)

    def test_save_as_copies_every_asset_and_preserves_identity_and_metadata(self):
        original = import_artwork(self.source, self.file, "portrait")
        prepared = self.file.parent / "artwork" / "prepared.png"
        prepared.write_bytes(b"prepared image")
        self.document["artwork"]["portrait"] = {"original": original, "prepared": "artwork/prepared.png", "selected": "original", "options": {"pixel_size": 3}}
        self.document["artwork"]["sprite"] = original
        self.document["metadata"] = {"credit": "Creator"}
        save_project(self.document, self.file)
        before = copy.deepcopy(self.document)
        destination = copy_project(self.document, self.file, self.root / "copy")
        copied = load_project(destination)
        self.assertEqual(copied["character"], before["character"])
        self.assertEqual(copied["metadata"], before["metadata"])
        self.assertEqual(copied["artwork"]["portrait"]["options"], {"pixel_size": 3})
        shutil.rmtree(self.file.parent)
        self.assertEqual(resolve_artwork(copied, destination, "portrait").read_bytes(), b"original image bytes")
        self.assertEqual(resolve_artwork(copied, destination, "sprite").read_bytes(), b"original image bytes")
        copied["artwork"]["portrait"]["selected"] = "prepared"
        self.assertEqual(resolve_artwork(copied, destination, "portrait").read_bytes(), b"prepared image")
        self.assertEqual(self.document, before)

    def test_failed_save_as_keeps_existing_destination_project(self):
        self.document["artwork"]["portrait"] = "artwork/missing.png"
        destination = self.root / "copy" / "character.json"
        save_project(new_project(), destination)
        previous = destination.read_bytes()
        with self.assertRaises(ProjectError):
            copy_project(self.document, self.file, destination)
        self.assertEqual(destination.read_bytes(), previous)


class AppearanceStorageTests(ProjectTestCase):
    def test_all_appearance_sets_round_trip_with_metadata_and_independent_selection(self):
        self.assertEqual(list(APPEARANCE_VARIANTS), ["spring", "summer", "fall", "winter", "beach"])
        self.document["artwork"]["portrait"] = "artwork/default.png"
        self.document["artwork"]["variants"] = {
            variant: {
                "portrait": {"original": f"artwork/{variant}.png", "prepared": f"artwork/{variant}-prepared.png",
                             "selected": "prepared", "options": {"notes": "Keep this"}},
                "sprite": f"artwork/{variant}-sprite.png",
                "credit": {"artist": "Local artist"},
            }
            for variant in APPEARANCE_VARIANTS
        }
        before = copy.deepcopy(self.document)
        save_project(self.document, self.file)
        loaded = load_project(self.file)
        self.assertEqual(loaded, before)
        self.assertEqual(self.document, before)
        self.assertEqual(resolve_artwork(loaded, self.file, "portrait"), self.file.parent / "artwork/default.png")
        for variant in APPEARANCE_VARIANTS:
            with self.subTest(variant=variant):
                self.assertEqual(resolve_artwork(loaded, self.file, "portrait", variant=variant),
                                 self.file.parent / f"artwork/{variant}-prepared.png")
                self.assertEqual(resolve_artwork(loaded, self.file, "sprite", variant=variant),
                                 self.file.parent / f"artwork/{variant}-sprite.png")

    def test_unset_variants_do_not_fall_back_and_legacy_default_still_resolves(self):
        legacy = {"name": "Ada", "artwork": {"portrait": "artwork/default.png"}}
        loaded = load_project(self.write_raw(legacy))
        self.assertNotIn("variants", loaded["artwork"])
        self.assertEqual(resolve_artwork(loaded, self.file, "portrait"), self.file.parent / "artwork/default.png")
        self.assertIsNone(resolve_artwork(loaded, self.file, "portrait", variant="winter"))
        loaded["artwork"]["variants"] = {"winter": {"portrait": None}, "beach": {"notes": "Planned"}}
        save_project(loaded, self.file)
        self.assertEqual(load_project(self.file), loaded)
        self.assertIsNone(resolve_artwork(loaded, self.file, "sprite", variant="winter"))
        self.assertIsNone(resolve_artwork(loaded, self.file, "portrait", variant="winter"))
        self.assertIsNone(resolve_artwork(loaded, self.file, "portrait", variant="beach"))
        self.assertIsNone(resolve_artwork({}, self.file, "portrait", variant="spring"))

    def test_save_as_copies_original_and_prepared_images_in_every_set(self):
        self.file.parent.mkdir()
        self.document["artwork"]["variants"] = {}
        for variant in APPEARANCE_VARIANTS:
            original = self.file.parent / f"{variant}.png"
            original.write_bytes(f"{variant} original".encode())
            prepared = self.file.parent / f"{variant}-prepared.png"
            prepared.write_bytes(f"{variant} prepared".encode())
            self.document["artwork"]["variants"][variant] = {
                "portrait": {"original": original.name, "prepared": prepared.name,
                             "selected": "original", "options": {"palette": variant}},
                "sprite": original.name,
                "notes": {"preserve": variant},
            }
        save_project(self.document, self.file)
        before = copy.deepcopy(self.document)
        destination = copy_project(self.document, self.file, self.root / "copy")
        copied = load_project(destination)
        shutil.rmtree(self.file.parent)
        self.assertEqual(self.document, before)
        for variant in APPEARANCE_VARIANTS:
            with self.subTest(variant=variant):
                appearance = copied["artwork"]["variants"][variant]
                self.assertEqual(appearance["notes"], {"preserve": variant})
                self.assertEqual(appearance["portrait"]["options"], {"palette": variant})
                self.assertEqual(resolve_artwork(copied, destination, "portrait", variant=variant).read_bytes(),
                                 f"{variant} original".encode())
                self.assertEqual(resolve_artwork(copied, destination, "sprite", variant=variant).read_bytes(),
                                 f"{variant} original".encode())
                appearance["portrait"]["selected"] = "prepared"
                self.assertEqual(resolve_artwork(copied, destination, "portrait", variant=variant).read_bytes(),
                                 f"{variant} prepared".encode())

    def test_invalid_variant_structure_ids_and_records_are_rejected(self):
        for variants in (None, [], "winter", {"formal": {}}, {"winter": None}, {"winter": []},
                         {"winter": {"portrait": []}}, {"winter": {"sprite": {}}},
                         {"beach": {"sprite": {"original": "sprite.png", "selected": "prepared"}}}):
            with self.subTest(variants=variants):
                self.document["artwork"]["variants"] = variants
                with self.assertRaises(ProjectError):
                    save_project(self.document, self.file)
                with self.assertRaises(ProjectError):
                    load_project(self.write_raw(self.document))
                with self.assertRaises(ProjectError):
                    resolve_artwork(self.document, self.file, "portrait", variant="winter")
        for variant in ("formal", "", [], 1):
            with self.subTest(variant=variant), self.assertRaisesRegex(ProjectError, "Unknown artwork appearance"):
                resolve_artwork(new_project(), self.file, "portrait", variant=variant)

    def test_unsafe_variant_original_and_unselected_prepared_paths_are_rejected(self):
        for reference in ("../outside.png", "/tmp/outside.png", "C:\\outside.png", "..\\outside.png",
                          "artwork/image.png:stream", "", ".", "a\x00.png"):
            for record in (reference, {"original": "artwork/original.png", "prepared": reference, "selected": "original"}):
                with self.subTest(reference=reference, record=record):
                    self.document["artwork"]["variants"] = {"winter": {"portrait": record}}
                    with self.assertRaises(ProjectError):
                        save_project(self.document, self.file)
                    with self.assertRaises(ProjectError):
                        load_project(self.write_raw(self.document))
                    with self.assertRaises(ProjectError):
                        resolve_artwork(self.document, self.file, "portrait", variant="winter")

    def test_external_symlinks_in_unselected_variants_are_rejected(self):
        self.file.parent.mkdir()
        outside = self.root / "outside.png"
        outside.write_bytes(b"private bytes")
        link = self.file.parent / "linked.png"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks are unavailable on this platform.")
        for record in ("linked.png", {"original": "safe.png", "prepared": "linked.png", "selected": "original"}):
            with self.subTest(record=record):
                self.document["artwork"]["variants"] = {"beach": {"sprite": record}}
                with self.assertRaisesRegex(ProjectError, "symlinks"):
                    save_project(self.document, self.file)
                with self.assertRaisesRegex(ProjectError, "symlinks"):
                    load_project(self.write_raw(self.document))
                with self.assertRaisesRegex(ProjectError, "symlinks"):
                    copy_project(self.document, self.file, self.root / "copy")
        self.document["artwork"]["variants"]["beach"]["sprite"] = "linked.png"
        with self.assertRaisesRegex(ProjectError, "symlinks"):
            resolve_artwork(self.document, self.file, "sprite", variant="beach")
        self.assertEqual(outside.read_bytes(), b"private bytes")

    def test_missing_variant_files_remain_editable_but_save_as_preserves_destination(self):
        self.document["artwork"]["variants"] = {"spring": {"sprite": "artwork/missing.png"}}
        save_project(self.document, self.file)
        loaded = load_project(self.file)
        self.assertEqual(resolve_artwork(loaded, self.file, "sprite", variant="spring"),
                         self.file.parent / "artwork/missing.png")
        destination = self.root / "copy" / "character.json"
        save_project(new_project(), destination)
        previous = destination.read_bytes()
        with self.assertRaisesRegex(ProjectError, "Could not import artwork"):
            copy_project(loaded, self.file, destination)
        self.assertEqual(destination.read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()

"""Native furniture references and explicit, portable animation previews."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from pixelheart_core.interior_furniture import (
    ROOM_FRAME_DOORWAY, ROOM_FRAME_JOINS, ROOM_FRAME_TILES, FurnitureValidationError, _read_texture,
    attach_texture, clear_preview_cache,
    definition_assets, frame_at,
    import_catalog_textures, import_furniture_library, import_texture, preview_frame,
    qualified_furniture_id, read_native_catalog, validate_definition, validate_room_frame,
)


class InteriorFurnitureTests(unittest.TestCase):
    def setUp(self):
        clear_preview_cache()
        self.addCleanup(clear_preview_cache)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()

    def definition(self, **changes):
        value = {
            "id": "Example.AnimatedLamp", "name": "Animated lamp", "kind": "lamp",
            "footprint": [1, 1], "sprite_size": [1, 1], "rotations": 2,
            "texture": "Mods/Example/Lamp", "dependency": "Example.Furniture",
            "mod_data": {"Example/Variant": "winter"},
            "frames": [
                {"rotation": 0, "rect": [0, 0, 16, 16], "duration_ms": 100},
                {"rotation": 0, "rect": [16, 0, 16, 16], "duration_ms": 200},
                {"rotation": 1, "rect": [0, 16, 16, 16], "duration_ms": 50},
            ],
        }
        value.update(changes)
        return value

    def texture(self, path=None):
        path = path or self.root / "atlas.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        with Image.new("RGBA", (32, 32), (255, 0, 0, 255)) as image:
            image.paste((0, 255, 0, 255), (16, 0, 32, 16))
            image.paste((0, 0, 255, 128), (0, 16, 16, 32))
            image.save(path)
        return path

    def catalog(self, data):
        path = self.root / "Furniture.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_native_import_preserves_item_identity_and_does_not_guess_previews(self):
        path = self.catalog({
            "112": "Oak Chair/chair/-1/-1/4/350/-1/Oak Chair/112",
            "Example.Lamp": r"Lamp/lamp/1 2/1 1/1/100/0/Blue lamp/8/Mods\Example\Lamp/false",
        })
        original = path.read_bytes()
        result = read_native_catalog(path)
        chair, lamp = result["definitions"]
        self.assertEqual(chair["id"], "(F)112")
        self.assertIsNone(chair["footprint"])
        self.assertIsNone(chair["sprite_size"])
        self.assertEqual(chair["placement"], "default")
        self.assertEqual(lamp["texture"], "Mods/Example/Lamp")
        self.assertEqual(lamp["footprint"], [1, 1])
        self.assertEqual(lamp["sprite_size"], [1, 2])
        self.assertEqual(lamp["placement"], "indoors")
        self.assertEqual(lamp["frames"], [])
        self.assertEqual(lamp["preview_asset"], "")
        self.assertTrue(any("unresolved" in warning for warning in result["warnings"]))
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.project.iterdir()), [])

    def test_native_asset_names_normalize_repeated_separators_without_permitting_escape(self):
        path = self.catalog({
            "Example.Table": r"Table/table/2 2/2 1/1/100/0/Table/0/TileSheets\\furniture_3",
            "Bad.Root": r"Bad/table/2 2/2 1/1/100/0/Bad/0/\\outside\\sheet",
            "Bad.Parent": r"Bad/table/2 2/2 1/1/100/0/Bad/0/TileSheets\\..\\outside",
        })
        result = read_native_catalog(path)
        self.assertEqual([item["id"] for item in result["definitions"]], ["(F)Example.Table"])
        self.assertEqual(result["definitions"][0]["texture"], "TileSheets/furniture_3")

    def test_invalid_native_entries_do_not_discard_valid_records(self):
        result = read_native_catalog(self.catalog({
            "Good": "Desk/table/2 2/2 1/2/100/2/Desk/4",
            "Bad": "Chair/chair/1 1/1 1/3/100/0/Chair/3",
            "Broken": {"not": "native"},
            "Huge": "Rug/rug/1000000 1/1 1/1/100/0/Rug/5",
            "(O)Nope": "Object/other/1 1/1 1/1/100/0/Object/2",
        }))
        self.assertEqual([item["id"] for item in result["definitions"]], ["(F)Good"])
        self.assertEqual(sum("skipped" in item for item in result["warnings"]), 4)

    def test_invalid_json_duplicate_ids_and_input_size_are_rejected(self):
        path = self.root / "bad.json"
        for text in ('{"A": "one", "A": "two"}', '{"A": NaN}', '[]', '{bad'):
            path.write_text(text, encoding="utf-8")
            with self.subTest(text=text), self.assertRaises(FurnitureValidationError):
                read_native_catalog(path)
        path.write_bytes(b" " * 21)
        with patch("pixelheart_core.interior_furniture.MAX_CATALOG_BYTES", 20):
            with self.assertRaises(FurnitureValidationError):
                read_native_catalog(path)

    def test_definition_validation_detaches_nested_data_and_rejects_other_item_types(self):
        original = self.definition()
        normalized = validate_definition(original)
        normalized["frames"][0]["rect"][0] = 7
        normalized["mod_data"]["Example/Variant"] = "spring"
        self.assertEqual(original["frames"][0]["rect"][0], 0)
        self.assertEqual(original["mod_data"]["Example/Variant"], "winter")
        self.assertEqual(qualified_furniture_id("(F)112"), "(F)112")
        long_id = qualified_furniture_id("a" * 256)
        self.assertEqual(qualified_furniture_id(long_id), long_id)
        for item_id in ("(O)112", "", " ", "Item\nInjected", 112, "(F)"):
            with self.subTest(item_id=item_id), self.assertRaises(FurnitureValidationError):
                qualified_furniture_id(item_id)

    def test_invalid_definition_geometry_timing_and_paths_are_rejected(self):
        cases = [
            {"footprint": [True, 1]}, {"footprint": [1, 0]}, {"rotations": True},
            {"frames": [{"rect": [0, 0, 1, 1], "duration_ms": 0}]},
            {"frames": [{"rect": [-1, 0, 1, 1]}]},
            {"frames": [{"rect": [0, 0, 1, 1], "rotation": 2}]},
            {"preview_asset": "../escape.png"}, {"texture": "/absolute"},
            {"texture": "C:\\Users\\game"}, {"preview_asset": "https://host/image.png"},
            {"mod_data": {"key": {"nested": True}}},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(FurnitureValidationError):
                validate_definition(self.definition(**changes))

    def test_rotation_footprints_are_explicit_detached_and_bounded(self):
        original = self.definition(footprint=[2, 1], rotation_footprints={"1": [1, 2]})
        normalized = validate_definition(original)
        self.assertEqual(normalized["rotation_footprints"], {"1": [1, 2]})
        normalized["rotation_footprints"]["1"][0] = 2
        self.assertEqual(original["rotation_footprints"]["1"], [1, 2])
        self.assertEqual(validate_definition(self.definition())["rotation_footprints"], {})
        for override in ({1: [1, 2]}, {"2": [1, 2]}, {"01": [1, 2]},
                         {"1": [0, 1]}, {"1": None}, [], {"1": [True, 1]}):
            with self.subTest(override=override), self.assertRaises(FurnitureValidationError):
                validate_definition(self.definition(rotation_footprints=override))

    def test_animation_boundaries_use_durations_and_wrap_without_drift(self):
        definition = self.definition()
        for time, expected_x in ((0, 0), (99, 0), (100, 16), (299, 16), (300, 0), (400, 16), (10**12, 16)):
            with self.subTest(time=time):
                self.assertEqual(frame_at(definition, elapsed_ms=time)["rect"][0], expected_x)
        self.assertEqual(frame_at(definition, rotation=1, elapsed_ms=999)["rect"], [0, 16, 16, 16])
        with self.assertRaises(FurnitureValidationError):
            frame_at(definition, elapsed_ms=-1)
        with self.assertRaises(FurnitureValidationError):
            frame_at(self.definition(frames=[]))

    def test_import_keeps_exact_png_bytes_and_preview_survives_source_removal(self):
        source = self.texture()
        payload = source.read_bytes()
        definition = attach_texture(self.definition(), source, self.project)
        reference = definition["preview_asset"]
        self.assertTrue(reference.startswith("world_assets/interiors/textures/"))
        self.assertEqual((self.project / reference).read_bytes(), payload)
        self.assertEqual(import_texture(source, self.project), reference)
        self.assertEqual(definition_assets(definition), [reference])
        source.unlink()
        with preview_frame(definition, self.project, elapsed_ms=100) as image:
            self.assertEqual(image.size, (16, 16))
            self.assertEqual(image.getpixel((0, 0)), (0, 255, 0, 255))
        with preview_frame(definition, self.project, rotation=1) as image:
            self.assertEqual(image.getpixel((0, 0)), (0, 0, 255, 128))

    def test_out_of_bounds_frames_fail_before_asset_copy_and_on_preview(self):
        source = self.texture()
        invalid = self.definition(frames=[{"rect": [31, 0, 2, 2], "duration_ms": 50}])
        with self.assertRaisesRegex(FurnitureValidationError, "beyond"):
            attach_texture(invalid, source, self.project)
        self.assertEqual(list(self.project.iterdir()), [])
        invalid["preview_asset"] = import_texture(source, self.project)
        with self.assertRaisesRegex(FurnitureValidationError, "beyond"):
            preview_frame(invalid, self.project)

    def test_catalog_texture_import_uses_selected_tree_and_copies_shared_atlas_once(self):
        folder = self.root / "textures"
        self.texture(folder / "Mods/Example/Lamp.png")
        original = [self.definition(), self.definition(id="Example.Second")]
        result = import_catalog_textures(original, folder, self.project)
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["definitions"][0]["preview_asset"], result["definitions"][1]["preview_asset"])
        self.assertNotIn("preview_asset", original[0])
        self.assertEqual(len(list((self.project / "world_assets/interiors/textures").glob("*.png"))), 1)
        self.assertEqual(result["definitions"][0]["mod_data"], {"Example/Variant": "winter"})

    def test_missing_texture_does_not_discard_furniture_or_change_existing_preview(self):
        source = self.texture()
        existing = attach_texture(self.definition(), source, self.project)
        result = import_catalog_textures([existing], self.root / "missing", self.project)
        self.assertEqual(result["definitions"], [existing])
        self.assertEqual(len(result["warnings"]), 1)

    def test_selected_texture_folder_and_project_escape_symlinks_are_rejected(self):
        source = self.texture()
        folder = self.root / "selected"
        folder.mkdir()
        (folder / "linked.png").symlink_to(source)
        result = import_catalog_textures([self.definition(texture="linked")], folder, self.project)
        self.assertEqual(result["definitions"][0]["preview_asset"], "")
        self.assertIn("symlink", result["warnings"][0])
        outside = self.root / "outside"
        outside.mkdir()
        (self.project / "world_assets").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(FurnitureValidationError, "symlink"):
            import_texture(source, self.project)
        self.assertEqual(list(outside.iterdir()), [])

    def test_preview_cannot_follow_escaping_symlink(self):
        source = self.texture()
        (self.project / "linked.png").symlink_to(source)
        definition = self.definition(preview_asset="linked.png")
        with self.assertRaisesRegex(FurnitureValidationError, "symlink"):
            preview_frame(definition, self.project)

    def test_non_png_animated_png_and_pixel_limits_are_rejected(self):
        path = self.root / "notpng.png"
        with Image.new("RGB", (2, 2)) as image:
            image.save(path, format="JPEG")
        with self.assertRaises(FurnitureValidationError):
            import_texture(path, self.project)
        with Image.new("RGBA", (2, 2), "red") as first, Image.new("RGBA", (2, 2), "blue") as second:
            first.save(path, format="PNG", save_all=True, append_images=[second], duration=100)
        with self.assertRaisesRegex(FurnitureValidationError, "static PNG"):
            import_texture(path, self.project)
        self.texture(path)
        with patch("pixelheart_core.interior_furniture.MAX_TEXTURE_PIXELS", 100):
            with self.assertRaisesRegex(FurnitureValidationError, "pixel limit"):
                import_texture(path, self.project)

    def test_total_texture_import_budget_is_enforced(self):
        folder = self.root / "textures"
        source = self.texture(folder / "Mods/Example/Lamp.png")
        with patch("pixelheart_core.interior_furniture.MAX_IMPORT_BYTES", len(source.read_bytes()) - 1):
            result = import_catalog_textures([self.definition()], folder, self.project)
        self.assertEqual(result["definitions"][0]["preview_asset"], "")
        self.assertIn("combined", result["warnings"][0])
        self.assertEqual(list(self.project.iterdir()), [])

    def test_content_addressed_asset_corruption_is_not_silently_overwritten(self):
        source = self.texture()
        reference = import_texture(source, self.project)
        (self.project / reference).write_bytes(b"unexpected")
        with self.assertRaisesRegex(FurnitureValidationError, "unexpected contents"):
            import_texture(source, self.project)
        self.assertEqual((self.project / reference).read_bytes(), b"unexpected")

    def library(self, definitions, **changes):
        data = {"format": "pixelheart-interior-library", "version": 1,
                "definitions": definitions}
        data.update(changes)
        return self.catalog(data)

    def room_frame(self, **changes):
        value = {"preview_asset": "textures/frame.png",
                 "tiles": {role: [0, 0, 16, 16] for role in ROOM_FRAME_TILES}}
        value.update(changes)
        return value

    def test_room_frame_validation_detaches_tiles_and_requires_complete_safe_metadata(self):
        original = self.room_frame()
        normalized = validate_room_frame(original)
        normalized["tiles"]["top"][0] = 16
        self.assertEqual(original["tiles"]["top"], [0, 0, 16, 16])
        incomplete = dict(original["tiles"])
        del incomplete["bottom_right_outer"]
        invalid = [None, [], {}, self.room_frame(tiles=incomplete),
                   self.room_frame(tiles=dict(original["tiles"], unknown=[0, 0, 16, 16]))]
        for reference in ("", "../frame.png", "/frame.png", "C:\\frame.png", "frame.jpg"):
            invalid.append(self.room_frame(preview_asset=reference))
        for rect in ([True, 0, 16, 16], [0, -1, 16, 16], [65536, 0, 16, 16],
                     [0, 0.5, 16, 16], [0, 0, 15, 16], [0, 0, 16, 32], [0, 0, 16]):
            invalid.append(self.room_frame(tiles=dict(original["tiles"], top=rect)))
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(FurnitureValidationError):
                validate_room_frame(value)

    def test_room_frame_import_preserves_crops_bytes_and_shared_texture_deduplication(self):
        source = self.texture(self.root / "textures/frame.png")
        payload = source.read_bytes()
        room_frame = self.room_frame()
        room_frame["tiles"]["bottom_right_outer"] = [16, 16, 16, 16]
        path = self.library([self.definition(preview_asset="textures/frame.png")],
                            room_frame=room_frame)
        original_json = path.read_bytes()
        result = import_furniture_library(path, self.project)
        self.assertEqual(result["room_frame"]["tiles"], room_frame["tiles"])
        reference = result["room_frame"]["preview_asset"]
        self.assertEqual(reference, result["definitions"][0]["preview_asset"])
        self.assertTrue(reference.startswith("world_assets/interiors/textures/"))
        self.assertEqual((self.project / reference).read_bytes(), payload)
        self.assertEqual(path.read_bytes(), original_json)
        self.assertEqual(len(list((self.project / "world_assets/interiors/textures").glob("*.png"))), 1)
        source.unlink()
        with Image.open(self.project / reference) as image:
            self.assertEqual(image.size, (32, 32))
        self.assertNotIn("room_frame", import_furniture_library(self.library([]), self.project))

    def test_room_frame_optional_joins_and_doorway_corners_validate_every_supplied_rectangle(self):
        for role in ROOM_FRAME_JOINS + ROOM_FRAME_DOORWAY:
            with self.subTest(role=role):
                original = self.room_frame()
                original["tiles"][role] = [16, 16, 16, 16]
                normalized = validate_room_frame(original)
                self.assertEqual(normalized["tiles"][role], [16, 16, 16, 16])
                self.assertEqual(len(normalized["tiles"]), len(ROOM_FRAME_TILES) + 1)
                normalized["tiles"][role][0] = 0
                self.assertEqual(original["tiles"][role][0], 16)
                for rect in ([65536, 0, 16, 16], [False, 0, 16, 16], [0, 0, 32, 16]):
                    original["tiles"][role] = rect
                    with self.assertRaises(FurnitureValidationError):
                        validate_room_frame(original)
        complete = self.room_frame()
        complete["tiles"].update({role: [0, 0, 16, 16] for role in ROOM_FRAME_JOINS + ROOM_FRAME_DOORWAY})
        self.assertEqual(validate_room_frame(complete), complete)

    def test_bad_room_frame_preflight_prevents_all_furniture_and_surface_asset_copies(self):
        self.texture(self.root / "textures/good.png")
        self.texture(self.root / "textures/frame.png")
        (self.root / "textures/corrupt.png").write_bytes(b"not a PNG")
        first = self.definition(preview_asset="textures/good.png")
        surface = {"id": "(FL)0", "kind": "floor", "texture": "Maps/floor",
                   "preview_asset": "textures/good.png", "rect": [0, 0, 32, 32]}
        late_bad_rect = self.room_frame()
        late_bad_rect["tiles"]["bottom_right_outer"] = [17, 16, 16, 16]
        invalid = [None, self.room_frame(tiles={}), late_bad_rect,
                   self.room_frame(preview_asset="textures/missing.png"),
                   self.room_frame(preview_asset="textures/corrupt.png")]
        for role in ROOM_FRAME_JOINS + ROOM_FRAME_DOORWAY:
            frame = self.room_frame()
            frame["tiles"][role] = [17, 16, 16, 16]
            invalid.append(frame)
        for room_frame in invalid:
            path = self.library([first], surfaces=[surface], room_frame=room_frame)
            with self.subTest(room_frame=room_frame), self.assertRaises(FurnitureValidationError):
                import_furniture_library(path, self.project)
            self.assertEqual(list(self.project.iterdir()), [])

    def test_room_frame_preflight_rejects_escaping_symlink_before_copying_valid_furniture(self):
        outside = self.texture(self.root / "outside.png")
        folder = self.root / "bundle"
        folder.mkdir()
        self.texture(folder / "good.png")
        (folder / "frame.png").symlink_to(outside)
        path = folder / "library.json"
        path.write_text(json.dumps({
            "format": "pixelheart-interior-library", "version": 1,
            "definitions": [self.definition(preview_asset="good.png")],
            "room_frame": self.room_frame(preview_asset="frame.png"),
        }))
        with self.assertRaisesRegex(FurnitureValidationError, "symlink"):
            import_furniture_library(path, self.project)
        self.assertEqual(list(self.project.iterdir()), [])

    def test_room_frame_counts_toward_library_texture_and_byte_budgets_before_copy(self):
        source = self.texture(self.root / "textures/good.png")
        frame = self.texture(self.root / "textures/frame.png")
        path = self.library([self.definition(preview_asset="textures/good.png")],
                            room_frame=self.room_frame())
        for setting, limit, message in (
            ("MAX_IMPORT_TEXTURES", 1, "unique PNG"),
            ("MAX_IMPORT_BYTES", len(source.read_bytes()) + len(frame.read_bytes()) - 1, "combined"),
        ):
            with self.subTest(setting=setting), patch("pixelheart_core.interior_furniture." + setting, limit):
                with self.assertRaisesRegex(FurnitureValidationError, message):
                    import_furniture_library(path, self.project)
            self.assertEqual(list(self.project.iterdir()), [])

    def test_corrupt_stored_room_frame_aborts_import_before_new_assets_are_copied(self):
        self.texture(self.root / "textures/good.png")
        source = self.texture(self.root / "textures/frame.png")
        with Image.new("RGBA", (32, 32), "purple") as image:
            image.save(source)
        reference = import_texture(source, self.project)
        (self.project / reference).write_bytes(b"corrupt")
        path = self.library([self.definition(preview_asset="textures/good.png")],
                            room_frame=self.room_frame())
        with self.assertRaisesRegex(FurnitureValidationError, "unexpected contents"):
            import_furniture_library(path, self.project)
        self.assertEqual(list((self.project / "world_assets/interiors/textures").glob("*.png")),
                         [self.project / reference])
        self.assertEqual((self.project / reference).read_bytes(), b"corrupt")

    def test_resolved_library_copies_explicit_frames_rotations_and_original_png_bytes(self):
        source = self.texture(self.root / "textures/exported.png")
        payload = source.read_bytes()
        definition = self.definition(preview_asset="textures/exported.png",
                                     rotation_footprints={"1": [2, 1]})
        path = self.library([definition])
        original_json = path.read_bytes()
        result = import_furniture_library(path, self.project)
        self.assertEqual(result["warnings"], [])
        resolved = result["definitions"][0]
        self.assertEqual(resolved["id"], "(F)Example.AnimatedLamp")
        self.assertEqual(resolved["rotation_footprints"], {"1": [2, 1]})
        self.assertEqual(resolved["mod_data"], {"Example/Variant": "winter"})
        self.assertEqual(resolved["frames"], definition["frames"])
        self.assertTrue(resolved["preview_asset"].startswith("world_assets/interiors/textures/"))
        self.assertEqual((self.project / resolved["preview_asset"]).read_bytes(), payload)
        self.assertEqual(path.read_bytes(), original_json)
        source.unlink()
        with preview_frame(resolved, self.project, elapsed_ms=100) as image:
            self.assertEqual(image.getpixel((0, 0)), (0, 255, 0, 255))

    def test_library_keeps_native_dictionary_import_behavior_without_creating_assets(self):
        path = self.catalog({"112": "Oak Chair/chair/-1/-1/4/350/-1/Oak Chair/112"})
        self.assertEqual(import_furniture_library(path, self.project), read_native_catalog(path))
        self.assertEqual(list(self.project.iterdir()), [])

    def test_library_rejects_duplicate_qualified_ids_and_unsupported_versions_before_copy(self):
        self.texture(self.root / "textures/atlas.png")
        definition = self.definition(preview_asset="textures/atlas.png")
        for path in (
            self.library([definition, dict(definition, id="(F)Example.AnimatedLamp")]),
        ):
            with self.assertRaisesRegex(FurnitureValidationError, "unique"):
                import_furniture_library(path, self.project)
        for version in (True, 2, "1", None):
            path = self.library([definition], version=version)
            with self.subTest(version=version), self.assertRaisesRegex(FurnitureValidationError, "version"):
                import_furniture_library(path, self.project)
        self.assertEqual(list(self.project.iterdir()), [])

    def test_library_preflights_every_referenced_png_and_frame_before_any_asset_copy(self):
        self.texture(self.root / "textures/good.png")
        first = self.definition(preview_asset="textures/good.png")
        for second in (
            self.definition(id="Another", preview_asset="textures/missing.png"),
            self.definition(id="Another", preview_asset="textures/good.png",
                            frames=[{"rotation": 0, "rect": [30, 30, 16, 16]}]),
            {"id": "(O)NotFurniture"},
        ):
            path = self.library([first, second])
            with self.subTest(second=second), self.assertRaises(FurnitureValidationError):
                import_furniture_library(path, self.project)
            self.assertEqual(list(self.project.iterdir()), [])

    def test_library_preflight_rejects_traversal_and_escaping_symlinks(self):
        source = self.texture(self.root / "outside.png")
        folder = self.root / "export"
        folder.mkdir()
        (folder / "linked.png").symlink_to(source)
        for reference in ("../outside.png", "linked.png", str(source)):
            path = folder / "furniture.json"
            path.write_text(json.dumps({"format": "pixelheart-interior-library", "version": 1,
                                        "definitions": [self.definition(preview_asset=reference)]}))
            with self.subTest(reference=reference), self.assertRaises(FurnitureValidationError):
                import_furniture_library(path, self.project)
            self.assertEqual(list(self.project.iterdir()), [])

    def test_library_shared_texture_is_copied_once_and_content_hashes_deduplicate_aliases(self):
        first = self.texture(self.root / "textures/one.png")
        second = self.root / "textures/two.png"
        second.write_bytes(first.read_bytes())
        path = self.library([self.definition(preview_asset="textures/one.png"),
                             self.definition(id="Another", preview_asset="textures/two.png")])
        result = import_furniture_library(path, self.project)
        self.assertEqual(result["definitions"][0]["preview_asset"], result["definitions"][1]["preview_asset"])
        self.assertEqual(len(list((self.project / "world_assets/interiors/textures").glob("*.png"))), 1)
        self.assertEqual(import_furniture_library(path, self.project), result)

    def test_library_aggregate_limits_and_missing_preview_metadata_are_explicit(self):
        source = self.texture(self.root / "textures/atlas.png")
        path = self.library([self.definition(preview_asset="textures/atlas.png")])
        with patch("pixelheart_core.interior_furniture.MAX_IMPORT_BYTES", len(source.read_bytes()) - 1):
            with self.assertRaisesRegex(FurnitureValidationError, "combined"):
                import_furniture_library(path, self.project)
        with patch("pixelheart_core.interior_furniture.MAX_IMPORT_TEXTURES", 0):
            with self.assertRaisesRegex(FurnitureValidationError, "unique PNG"):
                import_furniture_library(path, self.project)
        with patch("pixelheart_core.interior_furniture.MAX_CATALOG_ITEMS", 0):
            with self.assertRaisesRegex(FurnitureValidationError, "definitions"):
                import_furniture_library(path, self.project)
        self.assertEqual(list(self.project.iterdir()), [])
        result = import_furniture_library(self.library([self.definition(frames=[])]), self.project)
        self.assertEqual(result["definitions"][0]["preview_asset"], "")
        self.assertIn("no preview PNG", result["warnings"][0])

    def test_library_detects_existing_asset_corruption_during_preflight_before_new_copies(self):
        first = self.texture(self.root / "textures/first.png")
        second = self.texture(self.root / "textures/second.png")
        with Image.new("RGBA", (32, 32), "black") as image:
            image.save(second)
        existing = import_texture(second, self.project)
        (self.project / existing).write_bytes(b"corrupt")
        path = self.library([self.definition(preview_asset="textures/first.png"),
                             self.definition(id="Another", preview_asset="textures/second.png")])
        with self.assertRaisesRegex(FurnitureValidationError, "unexpected contents"):
            import_furniture_library(path, self.project)
        self.assertEqual(list((self.project / "world_assets/interiors/textures").glob("*.png")), [self.project / existing])

    def test_preview_cache_decodes_shared_atlas_once_across_animation_frames(self):
        definition = attach_texture(self.definition(), self.texture(), self.project)
        with patch("pixelheart_core.interior_furniture._read_texture", wraps=_read_texture) as decode:
            for elapsed in (0, 100, 200, 300):
                with preview_frame(definition, self.project, elapsed_ms=elapsed) as image:
                    self.assertEqual(image.size, (16, 16))
            self.assertEqual(decode.call_count, 1)

    def test_preview_cache_invalidates_changed_files_and_does_not_keep_bad_pixels(self):
        definition = attach_texture(self.definition(), self.texture(), self.project)
        path = self.project / definition["preview_asset"]
        with patch("pixelheart_core.interior_furniture._read_texture", wraps=_read_texture) as decode:
            with preview_frame(definition, self.project) as image:
                self.assertEqual(image.getpixel((0, 0)), (255, 0, 0, 255))
            with Image.new("RGBA", (32, 32), "purple") as changed:
                changed.save(path)
            with preview_frame(definition, self.project) as image:
                self.assertEqual(image.getpixel((0, 0)), (128, 0, 128, 255))
            self.assertEqual(decode.call_count, 2)
            path.write_bytes(b"not an image")
            with self.assertRaises(FurnitureValidationError):
                preview_frame(definition, self.project)

    def test_preview_cache_is_bounded_by_decoded_bytes_and_entry_count(self):
        first = attach_texture(self.definition(), self.texture(), self.project)
        path = self.texture(self.root / "other.png")
        with Image.new("RGBA", (32, 32), "purple") as image:
            image.save(path)
        second = attach_texture(self.definition(), path, self.project)
        for setting, limit in (("MAX_PREVIEW_CACHE_BYTES", 4096), ("MAX_PREVIEW_CACHE_ENTRIES", 1)):
            clear_preview_cache()
            with patch("pixelheart_core.interior_furniture." + setting, limit):
                with patch("pixelheart_core.interior_furniture._read_texture", wraps=_read_texture) as decode:
                    for definition in (first, second, first):
                        with preview_frame(definition, self.project) as image:
                            self.assertEqual(image.size, (16, 16))
                    self.assertEqual(decode.call_count, 3)

    def test_texture_larger_than_preview_budget_is_not_retained(self):
        definition = attach_texture(self.definition(), self.texture(), self.project)
        with patch("pixelheart_core.interior_furniture.MAX_PREVIEW_CACHE_BYTES", 32):
            with patch("pixelheart_core.interior_furniture._read_texture", wraps=_read_texture) as decode:
                for _ in range(2):
                    with preview_frame(definition, self.project) as image:
                        self.assertEqual(image.size, (16, 16))
                self.assertEqual(decode.call_count, 2)


if __name__ == "__main__":
    unittest.main()

"""Native game tilesheets remain runtime references, not redistributed assets."""
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from pixelheart_core.projects import new_project
from pixelheart_core.world import (
    WorldError, asset_path, compile_world, copy_world_assets, import_map,
    map_bundle, new_location, new_world, render_map_preview, world_issues,
)


def make_game_map(root, source="townInterior.png", *, external=False, dimensions='width="32" height="32"'):
    root.mkdir(parents=True, exist_ok=True)
    tileset = ('<tileset name="townInterior" tilewidth="16" tileheight="16" columns="2" tilecount="4">'
               f'<image source="{source}" {dimensions}/></tileset>')
    if external:
        (root / "sets").mkdir(exist_ok=True)
        (root / "sets" / "interior.tsx").write_text(tileset)
        tileset = '<tileset firstgid="1" source="sets/interior.tsx"/>'
    else:
        tileset = tileset.replace("<tileset ", '<tileset firstgid="1" ')
    layers = []
    for name in ("Back", "Buildings", "Front"):
        gids = [1 if name == "Back" else 0] * 16
        if name == "Buildings":
            gids[5] = 2
        layers.append(f'<layer name="{name}" width="4" height="4"><data encoding="csv">'
                      + ",".join(map(str, gids)) + "</data></layer>")
    path = root / "room.tmx"
    path.write_text('<map orientation="orthogonal" width="4" height="4" tilewidth="16" tileheight="16">'
                    + tileset + "".join(layers) + "</map>")
    return path


class GameTilesheetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_supported_smapi_forms_are_recorded_without_packaging_game_images(self):
        for source in ("townInterior.png", "townInterior", "Maps/townInterior.png", "Maps\\townInterior", "TOWNINTERIOR.PNG"):
            with self.subTest(source=source):
                path = make_game_map(self.root / "source", source)
                bundle = map_bundle(path)
                self.assertEqual(bundle["game_assets"], ["Maps/townInterior"])
                self.assertEqual(bundle["files"], {"room.tmx": path.read_bytes()})

    def test_nested_external_tileset_is_preserved_byte_for_byte(self):
        source = make_game_map(self.root / "source", "walls_and_floors.png", external=True)
        bundle = map_bundle(source)
        self.assertEqual(bundle["game_assets"], ["Maps/walls_and_floors"])
        self.assertEqual(set(bundle["files"]), {"room.tmx", "sets/interior.tsx"})
        reference = import_map(source, self.root / "project" / "character.json")
        imported = asset_path(reference, self.root / "project")
        self.assertEqual(map_bundle(imported), bundle)

    def test_dot_prefixed_vanilla_editor_images_are_never_packaged(self):
        for number, source in enumerate((".townInterior.png", "Maps/.townInterior.png", ".townInterior", "Maps\\.townInterior.png")):
            with self.subTest(source=source):
                path = make_game_map(self.root / str(number), source, external=True)
                editor_image = path.parent / "sets" / source.replace("\\", "/")
                if not editor_image.suffix:
                    editor_image = editor_image.with_suffix(".png")
                editor_image.parent.mkdir(parents=True, exist_ok=True)
                # Even invalid/unreadable image contents are irrelevant: the
                # editing copy is not the asset SMAPI loads at runtime.
                editor_image.write_bytes(b"not a runtime image")
                bundle = map_bundle(path)
                self.assertEqual(bundle["game_assets"], ["Maps/townInterior"])
                self.assertEqual(set(bundle["files"]), {"room.tmx", "sets/interior.tsx"})
                reference = import_map(path, self.root / "imported" / str(number) / "character.json")
                copied = asset_path(reference, self.root / "imported" / str(number))
                self.assertEqual(map_bundle(copied), bundle)

    def test_dot_prefixed_preview_uses_selected_runtime_art_not_local_editor_copy(self):
        path = make_game_map(self.root / "source", ".townInterior.png")
        Image.new("RGBA", (32, 32), "red").save(path.parent / ".townInterior.png")
        with self.assertRaisesRegex(WorldError, "Preview needs the game's"):
            render_map_preview(path)
        content = self.root / "Content"
        (content / "Maps").mkdir(parents=True)
        Image.new("RGBA", (32, 32), "blue").save(content / "Maps" / "townInterior.png")
        self.assertEqual(render_map_preview(path, game_content_root=content).getpixel((0, 0)), (0, 0, 255, 255))

    def test_dot_prefixed_custom_images_are_rejected_instead_of_exported_incorrectly(self):
        path = make_game_map(self.root / "source", ".custom.png")
        Image.new("RGBA", (32, 32), "red").save(path.parent / ".custom.png")
        with self.assertRaisesRegex(WorldError, "Rename custom artwork"):
            map_bundle(path)

    def test_dot_prefixed_game_image_cannot_silently_ignore_runtime_override(self):
        path = make_game_map(self.root / "source", ".townInterior.png")
        Image.new("RGBA", (32, 32), "red").save(path.parent / "townInterior.png")
        with self.assertRaisesRegex(WorldError, "undotted local override"):
            map_bundle(path)

    def test_missing_custom_images_and_incorrect_game_paths_still_fail(self):
        for source in ("townInteriors.png", "z_chair.png", "tiles/townInterior.png", "Maps/custom.png", "townInterior.xnb"):
            with self.subTest(source=source), self.assertRaises(WorldError):
                map_bundle(make_game_map(self.root / "source", source))

    def test_unsafe_game_references_do_not_bypass_path_validation(self):
        for source in ("../townInterior.png", "/townInterior.png", "C:\\townInterior.png", "https://example.com/townInterior.png"):
            with self.subTest(source=source), self.assertRaises(WorldError):
                map_bundle(make_game_map(self.root / "source", source))
        path = make_game_map(self.root / "source")
        (path.parent / "townInterior.png").symlink_to(self.root / "outside.png")
        with self.assertRaisesRegex(WorldError, "folder"):
            map_bundle(path)

    def test_missing_game_image_requires_usable_declared_dimensions(self):
        for dimensions in ("", 'width="0" height="32"', 'width="31" height="32"', 'width="32" height="no"', 'width="8192" height="8192"'):
            with self.subTest(dimensions=dimensions), self.assertRaisesRegex(WorldError, "declared width and height"):
                map_bundle(make_game_map(self.root / "source", dimensions=dimensions))

    def test_local_image_wins_and_is_still_validated(self):
        path = make_game_map(self.root / "source")
        image = path.parent / "townInterior.png"
        Image.new("RGBA", (32, 32), "red").save(image)
        bundle = map_bundle(path)
        self.assertEqual(bundle["game_assets"], [])
        self.assertIn("townInterior.png", bundle["files"])
        self.assertEqual(render_map_preview(path).getpixel((0, 0)), (255, 0, 0, 255))
        image.write_bytes(b"invalid image")
        with self.assertRaisesRegex(WorldError, "Invalid tilesheet"):
            map_bundle(path)

    def test_extensionless_references_prefer_local_png_like_smapi(self):
        path = make_game_map(self.root / "source", "townInterior")
        Image.new("RGBA", (32, 32), "blue").save(path.parent / "townInterior.png")
        bundle = map_bundle(path)
        self.assertEqual(bundle["game_assets"], [])
        self.assertIn("townInterior.png", bundle["files"])
        self.assertEqual(render_map_preview(path).getpixel((0, 0)), (0, 0, 255, 255))
        (path.parent / "townInterior.png").unlink()
        (path.parent / "townInterior.xnb").write_bytes(b"unsupported packed override")
        with self.assertRaisesRegex(WorldError, "local TSX tilesets or PNG"):
            map_bundle(path)

    def test_preview_reads_unpacked_content_maps_and_cp_export_layouts(self):
        path = make_game_map(self.root / "source", external=True)
        for folder, filename in (("Content", "Maps/townInterior.png"), ("Maps", "townInterior.png"), ("patch-export", "Maps_townInterior.png")):
            with self.subTest(folder=folder):
                root = self.root / folder
                image = root / filename
                image.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGBA", (32, 32), (17, 93, 144, 255)).save(image)
                before = image.read_bytes()
                preview = render_map_preview(path, game_content_root=root)
                self.assertEqual(preview.size, (64, 64))
                self.assertEqual(preview.getpixel((0, 0)), (17, 93, 144, 255))
                self.assertEqual(image.read_bytes(), before)
                self.assertEqual(map_bundle(path)["game_assets"], ["Maps/townInterior"])

    def test_preview_errors_instead_of_substituting_missing_artwork(self):
        path = make_game_map(self.root / "source")
        with self.assertRaisesRegex(WorldError, "Preview needs the game's Maps/townInterior"):
            render_map_preview(path)
        with self.assertRaisesRegex(WorldError, "could not find Maps/townInterior"):
            render_map_preview(path, game_content_root=self.root / "empty")

    def test_preview_rejects_external_symlinks(self):
        path = make_game_map(self.root / "source")
        content = self.root / "Content"
        (content / "Maps").mkdir(parents=True)
        outside = self.root / "outside.png"
        Image.new("RGBA", (32, 32), "blue").save(outside)
        (content / "Maps" / "townInterior.png").symlink_to(outside)
        with self.assertRaisesRegex(WorldError, "symlink"):
            render_map_preview(path, game_content_root=content)

    def test_import_copy_and_export_retain_only_native_references(self):
        source = make_game_map(self.root / "source", "Maps/walls_and_floors", external=True)
        project = self.root / "project" / "character.json"
        location = new_location()
        location["map"] = import_map(source, project)
        world = {**new_world(), "locations": [location]}
        copy_world_assets(world, project.parent, self.root / "copy")
        copied = asset_path(location["map"], self.root / "copy")
        self.assertEqual(map_bundle(copied), map_bundle(source))
        compiled = compile_world(world, new_project()["character"], project.parent)
        files = compiled["files"]
        self.assertEqual(sorted(Path(name).suffix for name in files), [".tmx", ".tsx"])
        self.assertIn(source.read_bytes(), files.values())
        self.assertIn((source.parent / "sets/interior.tsx").read_bytes(), files.values())

    def test_authored_seat_cannot_override_vanilla_chair_tiles_globally(self):
        for source in ("townInterior.png", ".townInterior.png"):
            with self.subTest(source=source):
                path = make_game_map(self.root / "source", source)
                if source.startswith("."):
                    Image.new("RGBA", (32, 32), "red").save(path.parent / source)
                location = new_location()
                location["map"] = path.relative_to(self.root).as_posix()
                location["seats"] = [{"id": "chair", "x": 1, "y": 1, "direction": "right"}]
                issues = world_issues({**new_world(), "locations": [location]}, new_project()["character"], self.root)
                self.assertTrue(any("override vanilla seating globally" in item["message"] for item in issues), issues)

    def test_legacy_local_chair_with_vanilla_filename_remains_supported(self):
        path = make_game_map(self.root / "source")
        Image.new("RGBA", (32, 32), "red").save(path.parent / "townInterior.png")
        location = new_location()
        location["map"] = path.relative_to(self.root).as_posix()
        location["seats"] = [{"id": "chair", "x": 1, "y": 1, "direction": "right"}]
        compiled = compile_world({**new_world(), "locations": [location]}, new_project()["character"], self.root)
        patch = next(item for item in compiled["patches"] if item.get("Target") == "Data/ChairTiles")
        self.assertEqual(patch["Entries"], {"townInterior/1/0": "1/1/right/default/-1/-1/false"})


if __name__ == "__main__":
    unittest.main()

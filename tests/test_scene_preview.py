"""Local scene art resolution, frame layouts, provenance, and bounded fallback."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from pixelheart_core.projects import new_project
from pixelheart_core.scene_preview import (
    clear_preview_cache, crop_portrait_frame, crop_sprite_frame,
    load_actor_artwork, load_scene_assets, load_scene_map, resolve_scene_preview,
)
from pixelheart_core.story import new_event, exported_npc_id
from pixelheart_core.world import cast_actor_id, exported_location_id


class ScenePreviewTests(unittest.TestCase):
    def setUp(self):
        clear_preview_cache()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.file = self.root / "character.json"
        self.document = new_project()
        self.document["character"].update(internal_name="Mira", name="Mira")
        self.event = new_event(self.document["character"])

    def png(self, relative, size=(64, 128), color=(70, 140, 90, 255)):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", size, color).save(path)
        return path

    def tmx(self, relative="maps/room.tmx", texture="tiles.png"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        self.png(path.parent.relative_to(self.root) / texture, (16, 16))
        layers = "".join(f'<layer name="{name}" width="2" height="2"><data encoding="csv">{data}</data></layer>'
                         for name, data in (("Back", "1,1,1,1"), ("Buildings", "0,0,0,0"), ("Front", "0,0,0,0")))
        path.write_text('<map width="2" height="2" tilewidth="16" tileheight="16" orientation="orthogonal">'
                        f'<tileset firstgid="1" tilewidth="16" tileheight="16" columns="1"><image source="{texture}"/></tileset>'
                        + layers + '</map>')
        return path

    def test_selected_prepared_art_and_variant_fallback_do_not_mutate_project(self):
        original = self.png("art/original.png", color="red")
        prepared = self.png("art/prepared.png", color="blue")
        portrait = self.png("art/portrait.png", (128, 192), "green")
        self.document["artwork"] = {
            "sprite": {"original": "art/original.png", "prepared": "art/prepared.png", "selected": "prepared"},
            "portrait": "art/portrait.png", "extension": {"keep": True},
        }
        before = deepcopy(self.document)
        contents = {path: path.read_bytes() for path in (original, prepared, portrait)}
        result = load_actor_artwork(self.document, self.file, "$npc", variant="winter")
        self.assertEqual(result["sprite_path"], prepared)
        self.assertEqual(result["portrait_path"], portrait)
        self.assertEqual(result["sprite"].getpixel((0, 0)), (0, 0, 255, 255))
        self.assertEqual(self.document, before)
        self.assertEqual(contents, {path: path.read_bytes() for path in contents})
        result["sprite"].putpixel((0, 0), (0, 0, 0, 0))
        again = load_actor_artwork(self.document, self.file, "Mira")
        self.assertEqual(again["sprite"].getpixel((0, 0)), (0, 0, 255, 255))

    def test_season_art_and_supporting_character_aliases(self):
        winter = self.png("art/winter.png", color="white")
        self.document["artwork"]["variants"] = {"winter": {"sprite": "art/winter.png"}}
        self.event["story"]["season"] = "winter"
        assets = load_scene_assets(self.document, self.file, self.event)
        self.assertEqual(assets["actors"]["$npc"]["sprite_path"], winter)
        sprite = self.png("art/cast.png", color="orange")
        companion = {"id": "supporting-id", "character": {"id": "supporting-character", "internal_name": "Juniper"},
                     "artwork": {"sprite": "art/cast.png"}}
        self.document["world"] = {"characters": [companion]}
        for name in (cast_actor_id(companion), "Juniper", exported_npc_id(companion["character"])):
            with self.subTest(name=name):
                result = load_actor_artwork(self.document, self.file, name)
                self.assertEqual(result["sprite_path"], sprite)
                self.assertEqual(result["source"], "Supporting character artwork")

    def test_flat_and_nested_exports_resolve_from_known_game_folder(self):
        sprite = self.png("game/patch export/Characters_Abigail.png", color="purple")
        portrait = self.png("game/patch export/Portraits/Abigail.png", (128, 192), "pink")
        result = load_actor_artwork(self.document, self.file, "Abigail", export_root=self.root / "game")
        self.assertEqual(result["sprite_path"], sprite)
        self.assertEqual(result["portrait_path"], portrait)
        self.assertEqual(result["source"], "From my game")
        self.assertEqual(result["warning"], "")

    def test_unset_primary_art_never_substitutes_another_game_character(self):
        self.png("exports/Characters_Mira.png")
        result = load_actor_artwork(self.document, self.file, "$npc", export_root=self.root / "exports")
        self.assertIsNone(result["sprite"])
        self.assertIn("No selected sprite", result["warning"])

    def test_sprite_facing_rows_walk_columns_and_proportional_art(self):
        sheet = Image.new("RGBA", (128, 256))
        for row in range(4):
            for column in range(4):
                sheet.paste((row * 50, column * 50, 10, 255), (column * 32, row * 64, (column + 1) * 32, (row + 1) * 64))
        for facing, row in ((0, 2), (1, 1), (2, 0), (3, 3), ("up", 2)):
            frame = crop_sprite_frame(sheet, facing, 3)
            self.assertEqual(frame.size, (32, 64))
            self.assertEqual(frame.getpixel((0, 0)), (row * 50, 150, 10, 255))
        for facing, frame in ((True, 0), (5, 0), (2, 4), (2, -1), (2, True)):
            self.assertIsNone(crop_sprite_frame(sheet, facing, frame))
        self.assertIsNone(crop_sprite_frame(Image.new("RGBA", (63, 128))))
        self.assertIsNone(crop_sprite_frame(Image.new("RGBA", (64, 64))))

    def test_portrait_expression_crop_and_unavailable_frame(self):
        sheet = Image.new("RGBA", (128, 192), "red")
        sheet.paste("blue", (64, 64, 128, 128))
        self.assertEqual(crop_portrait_frame(sheet, 3).getpixel((0, 0)), (0, 0, 255, 255))
        self.assertEqual(crop_portrait_frame(sheet).size, (64, 64))
        self.assertIsNone(crop_portrait_frame(sheet, 6))
        self.assertIsNone(crop_portrait_frame(sheet, True))

    def test_farmer_bases_are_detected_without_claiming_complete_clothed_appearance(self):
        female = self.png("exports/Characters_Farmer_farmer_girl_base.png", (96, 672), "red")
        male = self.png("exports/Characters/Farmer/farmer_base.png", (96, 672), "blue")
        for gender, path in (("female", female), ("Male", male)):
            result = load_actor_artwork(self.document, self.file, "farmer", export_root=self.root / "exports", farmer_gender=gender)
            self.assertIsNone(result["sprite"])
            self.assertTrue(result["is_farmer"])
            self.assertEqual(result["base_path"], path)
            self.assertIn("clothing", result["warning"])

    def test_project_tmx_aliases_and_dependency_cache_invalidation(self):
        path = self.tmx()
        place = {"id": "place", "name": "Mira's refuge", "internal_name": "Refuge", "map": "maps/room.tmx"}
        self.document["world"] = {"locations": [place]}
        from pixelheart_core.scene_preview import render_map_preview
        with patch("pixelheart_core.scene_preview.render_map_preview", wraps=render_map_preview) as renderer:
            first = load_scene_map(self.document, self.file, "Refuge")
            again = load_scene_map(self.document, self.file, exported_location_id(place, self.document["character"]))
            self.assertEqual(first["map_size"], (2, 2))
            self.assertEqual(first["location_label"], "Mira's refuge")
            self.assertEqual(first["path"], path)
            self.assertEqual(first["image"].size, (32, 32))
            self.assertEqual(renderer.call_count, 1)
            self.assertEqual(first["cache_key"], again["cache_key"])
            self.png("maps/tiles.png", (16, 16), "blue")
            changed = load_scene_map(self.document, self.file, "Refuge")
            self.assertEqual(renderer.call_count, 2)
            self.assertNotEqual(first["cache_key"], changed["cache_key"])
            self.assertEqual(changed["image"].getpixel((0, 0)), (0, 0, 255, 255))

    def test_exported_tmx_and_flat_png_preserve_tile_size_when_downscaled(self):
        self.tmx("exports/Maps/Town.tmx")
        tmx = load_scene_map(self.document, self.file, "Town", export_root=self.root / "exports")
        self.assertEqual(tmx["map_size"], (2, 2))
        self.assertEqual(tmx["source"], "From my game")
        self.png("exports/Maps_Beach.png", (320, 160), "yellow")
        image = load_scene_map(self.document, self.file, "Beach", export_root=self.root / "exports", max_map_size=160)
        self.assertEqual(image["map_size"], (20, 10))
        self.assertEqual(image["image"].size, (160, 80))
        larger = load_scene_map(self.document, self.file, "Beach", export_root=self.root / "exports", max_map_size=320)
        self.assertNotEqual(image["cache_key"], larger["cache_key"])

    def test_missing_map_dependency_failure_is_cached_and_recovers_when_supplied(self):
        self.tmx("exports/Maps_Town.tmx")
        texture = self.root / "exports/tiles.png"
        texture.unlink()
        from pixelheart_core.scene_preview import _bounded_map_dependencies
        with patch("pixelheart_core.scene_preview._bounded_map_dependencies", wraps=_bounded_map_dependencies) as reader:
            first = load_scene_map(self.document, self.file, "Town", export_root=self.root / "exports")
            second = load_scene_map(self.document, self.file, "Town", export_root=self.root / "exports")
            self.assertIsNone(first["image"])
            self.assertIsNone(second["image"])
            self.assertEqual(reader.call_count, 1)
            self.png("exports/tiles.png", (16, 16))
            repaired = load_scene_map(self.document, self.file, "Town", export_root=self.root / "exports")
            self.assertIsNotNone(repaired["image"])
            self.assertEqual(reader.call_count, 2)

    def test_blank_location_default_and_unavailable_map_do_not_change_authored_location(self):
        self.png("exports/Maps_Town.png", (32, 32))
        self.event["location"] = ""
        before = deepcopy(self.event)
        result = load_scene_assets(self.document, self.file, self.event, export_root=self.root / "exports")
        self.assertEqual(result["preview_location"], "Town")
        self.assertIsNotNone(result["map"]["image"])
        self.assertEqual(self.event, before)
        self.event["location"] = "MissingMap"
        missing = load_scene_assets(self.document, self.file, self.event, export_root=self.root / "exports")
        self.assertIsNone(missing["map"]["image"])
        self.assertEqual(missing["preview_location"], "MissingMap")

    def test_asset_key_ignores_dialogue_positions_and_facing_but_tracks_gender(self):
        self.document["artwork"]["sprite"] = "art/sprite.png"
        self.png("art/sprite.png")
        first = load_scene_assets(self.document, self.file, self.event)
        self.event["story"]["actors"][0].update(x=10, y=10, facing=0)
        self.event["story"].update(premise="Changed prose", beats=[{"kind": "dialogue", "text": "New dialogue"}])
        second = load_scene_assets(self.document, self.file, self.event)
        self.assertEqual(first["key"], second["key"])
        third = load_scene_assets(self.document, self.file, self.event, farmer_gender="male")
        self.assertNotEqual(first["key"], third["key"])
        facade = resolve_scene_preview(self.document, self.file, self.event)
        self.assertIn("$npc", facade["sprites"])
        self.assertEqual(facade["sprites"]["$npc"].mode, "RGBA")
        self.assertEqual(facade["location_label"], "Pelican Town")
        self.assertIn("map not available", facade["source_note"])
        self.assertIn("artwork unavailable for the farmer", facade["source_note"])
        self.assertIsNone(facade["background"])

    def test_malformed_assets_and_path_escape_fail_without_loading_external_data(self):
        exports = self.root / "exports"
        exports.mkdir()
        external = self.png("outside.png")
        (exports / "Characters_Abigail.png").symlink_to(external)
        linked = load_actor_artwork(self.document, self.file, "Abigail", export_root=exports)
        self.assertIsNone(linked["sprite"])
        self.assertIn("linked", linked["warning"])
        self.document["artwork"]["sprite"] = "../outside.png"
        self.assertIsNone(load_actor_artwork(self.document, self.file, "$npc")["sprite"])
        for location in ("../Town", "/Town", None, {}, "Town\x00"):
            result = load_scene_map(self.document, self.file, location, export_root=exports)
            self.assertIsNone(result["image"])
            self.assertTrue(result["warning"])
        (exports / "Characters_Harvey.png").write_bytes(b"not a png")
        self.assertIsNone(load_actor_artwork(self.document, self.file, "Harvey", export_root=exports)["sprite"])
        self.png("exports/Maps_Town.png", (17, 16))
        self.assertIsNone(load_scene_map(self.document, self.file, "Town", export_root=exports)["image"])

    def test_limits_and_unsafe_tmx_return_clear_fallback(self):
        self.png("exports/Characters_Abigail.png")
        with patch("pixelheart_core.scene_preview.MAX_ARTWORK_BYTES", 10):
            result = load_actor_artwork(self.document, self.file, "Abigail", export_root=self.root / "exports")
        self.assertIsNone(result["sprite"])
        self.assertIn("size limit", result["warning"])
        (self.root / "exports/Maps_Town.tmx").write_text('<!DOCTYPE map [<!ENTITY content SYSTEM "file:///private/file">]><map/>')
        result = load_scene_map(self.document, self.file, "Town", export_root=self.root / "exports")
        self.assertIsNone(result["image"])
        self.assertIn("entities", result["warning"])
        self.assertIsNone(load_scene_map(self.document, self.file, "Town", max_map_size=True)["image"])


if __name__ == "__main__":
    unittest.main()

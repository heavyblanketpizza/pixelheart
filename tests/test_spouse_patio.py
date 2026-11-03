"""The spouse patio: data, export and game previews, with synthetic game files only."""
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image

from pixelheart_core.game_scene_assets import clear_game_cache
from pixelheart_core.projects import new_project
from pixelheart_core.spouse_patio import (
    DEFAULT_STAND, compile_patio, erase, farm_context, farm_ground, fill, game_image_name, game_patios,
    game_sheet_choices, game_texture, import_own_sheet, new_patio, normalize_patio, paint, patio_issues,
    patio_structure_issues, patio_tmx, prune_sheets, render_patio, use_own_sheet,
)
from pixelheart_core.world import (
    WorldError, compile_world, new_world, normalize_world, world_asset_references, world_issues,
    world_structure_issues,
)
from tests.test_game_scene_assets import container, i32, string, texture

OUTDOORS = {"source": "game", "image": "spring_outdoorsTileSheet", "columns": 4, "rows": 4}


def tbin_map(width, height, sheets, layers, properties=None):
    """A tBIN map: sheets {id: (image, columns, rows)}, layers {name: {(x, y): (id, index)}}."""
    def props(values):
        out = i32(len(values))
        for key, value in values.items():
            out += string(key, True) + b"\x03" + string(value, True)
        return out
    result = b"tBIN10" + string("Test", True) + string("", True) + props(properties or {})
    result += i32(len(sheets))
    for sheet_id, (image, columns, rows) in sheets.items():
        result += (string(sheet_id, True) + string("", True) + string(image, True)
                   + struct.pack("<8i", columns, rows, 16, 16, 0, 0, 0, 0) + props({}))
    result += i32(len(layers))
    for name, tiles in layers.items():
        result += string(name, True) + b"\x01" + string("", True) + struct.pack("<4i", width, height, 16, 16) + props({})
        current = None
        for y in range(height):
            x = 0
            while x < width:
                if (x, y) in tiles:
                    sheet_id, index = tiles[(x, y)]
                    if sheet_id != current:
                        result += b"T" + string(sheet_id, True)
                        current = sheet_id
                    result += b"S" + i32(index) + b"\x00" + props({})
                    x += 1
                else:
                    run = 1
                    while x + run < width and (x + run, y) not in tiles:
                        run += 1
                    result += b"N" + i32(run)
                    x += run
    return result


def map_xnb(*args, **kwargs):
    body = tbin_map(*args, **kwargs)
    return container("xTile.Pipeline.TideReader", i32(len(body)) + body, "lzx")


def grid_texture(columns, rows, season_shift=0):
    """A texture whose tile n is filled with the colour (n, season_shift, 0)."""
    image = Image.new("RGBA", (columns * 16, rows * 16))
    for index in range(columns * rows):
        x, y = index % columns * 16, index // columns * 16
        image.paste((index, season_shift, 0, 255), (x, y, x + 16, y + 16))
    data = struct.pack("<5i", 0, *image.size, 1, len(image.tobytes())) + image.tobytes()
    return container("Microsoft.Xna.Framework.Content.Texture2DReader", data)


def filled(key="spring_outdoorsTileSheet", tile=0, **changes):
    patio = new_patio()
    patio["sheets"][key] = dict(OUTDOORS) if key == "spring_outdoorsTileSheet" else changes.pop("sheet")
    patio["layers"]["Back"] = [[key, tile]] * 16
    patio.update(changes)
    return patio


def fake_game(root):
    maps = Path(root) / "Content" / "Maps"
    maps.mkdir(parents=True)
    (maps / "Town.xnb").write_bytes(texture())
    add_patio_maps(maps)
    return Path(root)


def add_patio_maps(maps):
    """A 12×10 farm with its spouse area at (6, 4), two game patios, and their sheets."""
    (maps / "spring_outdoorsTileSheet.xnb").write_bytes(grid_texture(4, 4))
    (maps / "summer_outdoorsTileSheet.xnb").write_bytes(grid_texture(4, 4, season_shift=9))
    (maps / "patioTiles.xnb").write_bytes(grid_texture(2, 2))
    ground = {(x, y): ("untitled", 5) for x in range(12) for y in range(10)}
    ground[(7, 4)] = ("untitled", 6)
    (maps / "Farm.xnb").write_bytes(map_xnb(
        12, 10, {"untitled": ("Maps\\spring_outdoorsTileSheet.png", 4, 4)},
        {"Back": ground, "Buildings": {(8, 5): ("untitled", 9)}},
        properties={"SpouseAreaLocation": "6 4"}))
    first = {(x, y): ("patio", 1) for x in range(4) for y in range(4)}
    second = {(x + 8, y): ("patio", 2) for x in range(4) for y in range(4)}
    (maps / "spousePatios.xnb").write_bytes(map_xnb(
        12, 4, {"patio": ("patioTiles", 2, 2), "paths": ("paths", 8, 1)},
        {"Back": {**first, **second}, "Buildings": {(1, 1): ("patio", 3)},
         "Paths": {(9, 2): ("paths", 7)}}))


class PatioDataTests(unittest.TestCase):
    def test_new_patio_is_empty_with_the_default_standing_spot(self):
        patio = new_patio()
        self.assertEqual(patio_structure_issues(patio), [])
        self.assertEqual(patio["stand"], list(DEFAULT_STAND))
        self.assertEqual(normalize_patio({}), patio)

    def test_structure_rejects_missing_tiles_bad_names_and_positions(self):
        bad_cell = filled()
        bad_cell["layers"]["Front"][0] = ["spring_outdoorsTileSheet", 16]
        unknown_sheet = filled()
        unknown_sheet["layers"]["Front"][0] = ["nowhere", 0]
        path_image = filled()
        path_image["sheets"]["spring_outdoorsTileSheet"]["image"] = "../secret"
        outside = filled(stand=[4, 0])
        frames = filled(animation={"frames": [[1, 0]], "offset": [0, 0]})
        loose = filled()
        loose["sheets"]["own"] = {"source": "own", "asset": "/etc/tiles.png", "columns": 1, "rows": 1}
        for patio in (bad_cell, unknown_sheet, path_image, outside, frames, loose, [], {"layers": {"Paths": []}}):
            with self.subTest(patio=patio):
                self.assertTrue(patio_structure_issues(patio))
                with self.assertRaises(WorldError):
                    normalize_patio(patio)

    def test_game_image_names_are_bare_content_maps_names(self):
        self.assertEqual(game_image_name("Maps\\spring_outdoorsTileSheet.png"), "spring_outdoorsTileSheet")
        self.assertEqual(game_image_name("untitled tile sheet"), "untitled tile sheet")
        self.assertIsNone(game_image_name("Maps/sub/folder"))
        self.assertIsNone(game_image_name("../x"))

    def test_prune_forgets_sheets_no_cell_uses(self):
        patio = filled()
        patio["sheets"]["townInterior"] = {"source": "game", "image": "townInterior", "columns": 2, "rows": 2}
        self.assertEqual(list(prune_sheets(patio)["sheets"]), ["spring_outdoorsTileSheet"])

    def test_issues_cover_ground_standing_spot_romance_and_name_clash(self):
        empty = new_patio()
        blocked = filled()
        blocked["layers"]["Buildings"][3 * 4 + 2] = ["spring_outdoorsTileSheet", 1]
        messages = lambda patio, **kwargs: [(level, field) for level, field, _ in
                                            patio_issues(patio, {"romanceable": True}, **kwargs)]
        self.assertIn(("error", "layers.Back"), messages(empty))
        self.assertIn(("error", "stand"), messages(blocked))
        self.assertEqual(messages(filled()), [])
        self.assertEqual(messages(filled(), taken_names=["spousepatio"]), [("error", "")])
        self.assertEqual([level for level, *_ in patio_issues(filled(), {"romanceable": False})], ["warning"])


class PatioPaintingTests(unittest.TestCase):
    def test_paint_stamps_a_block_clipped_to_the_patio_and_adds_its_sheet(self):
        patio = new_patio()
        self.assertTrue(paint(patio, "Buildings", 2, 3, "spring_outdoorsTileSheet", OUTDOORS, [[0, 1], [4, 5]]))
        self.assertEqual(patio["layers"]["Buildings"][3 * 4 + 2], ["spring_outdoorsTileSheet", 0])
        self.assertEqual(patio["layers"]["Buildings"][3 * 4 + 3], ["spring_outdoorsTileSheet", 1])
        self.assertEqual(sum(1 for cell in patio["layers"]["Buildings"] if cell), 2)
        self.assertEqual(patio["sheets"], {"spring_outdoorsTileSheet": OUTDOORS})
        self.assertFalse(paint(patio, "Buildings", 2, 3, "spring_outdoorsTileSheet", OUTDOORS, [[0]]))
        with self.assertRaises(WorldError):
            paint(patio, "Paths", 0, 0, "spring_outdoorsTileSheet", OUTDOORS, [[0]])

    def test_erase_and_fill_change_one_layer(self):
        patio = new_patio()
        self.assertTrue(fill(patio, "Back", "spring_outdoorsTileSheet", OUTDOORS, 3))
        self.assertEqual(patio["layers"]["Back"], [["spring_outdoorsTileSheet", 3]] * 16)
        self.assertFalse(fill(patio, "Back", "spring_outdoorsTileSheet", OUTDOORS, 3))
        self.assertTrue(erase(patio, "Back", 1, 1))
        self.assertIsNone(patio["layers"]["Back"][5])
        self.assertFalse(erase(patio, "Back", 1, 1))
        self.assertFalse(erase(patio, "Front", 9, 9))

    def test_a_new_own_sheet_must_keep_every_tile_the_patio_uses(self):
        patio = new_patio()
        small = {"source": "own", "asset": "world_assets/patio/a.png", "columns": 1, "rows": 1}
        large = {"source": "own", "asset": "world_assets/patio/b.png", "columns": 4, "rows": 1}
        use_own_sheet(patio, large)
        paint(patio, "Front", 0, 0, "own", large, [[3]])
        with self.assertRaises(WorldError):
            use_own_sheet(patio, small)
        use_own_sheet(patio, {**large, "asset": "world_assets/patio/c.png"})
        self.assertEqual(patio["sheets"]["own"]["asset"], "world_assets/patio/c.png")


class PatioExportTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def own_sheet(self, size=(32, 16)):
        stream = io.BytesIO()
        Image.new("RGBA", size, (200, 40, 40, 255)).save(stream, format="PNG")
        return import_own_sheet(stream.getvalue(), self.root, size[0] // 16, size[1] // 16)

    def test_map_names_game_sheets_ships_own_sheet_and_marks_the_standing_spot(self):
        patio = filled(stand=[1, 2])
        patio["sheets"]["own"] = self.own_sheet()
        patio["sheets"]["townInterior"] = {"source": "game", "image": "townInterior", "columns": 2, "rows": 2}
        patio["layers"]["Buildings"][0] = ["own", 1]
        root = ET.fromstring(patio_tmx(patio))
        tilesets = {node.get("name"): node for node in root.findall("tileset")}
        self.assertEqual(list(tilesets), ["own", "spring_outdoorsTileSheet", "pixelheart_paths"])
        self.assertEqual(tilesets["spring_outdoorsTileSheet"].find("image").get("source"), "spring_outdoorsTileSheet")
        self.assertEqual(tilesets["own"].find("image").get("source"), "patio_tiles.png")
        layers = {node.get("name"): [int(v) for v in node.find("data").text.replace("\n", "").split(",")]
                  for node in root.findall("layer")}
        self.assertEqual(list(layers), ["Back", "Buildings", "Front", "Paths"])
        self.assertEqual(layers["Back"], [3] * 16)
        self.assertEqual(layers["Buildings"][0], 2)
        paths_first = int(tilesets["pixelheart_paths"].get("firstgid"))
        self.assertEqual(layers["Paths"][2 * 4 + 1], paths_first + 7)
        self.assertEqual(sum(1 for value in layers["Paths"] if value), 1)
        self.assertEqual(root.find("layer[@name='Paths']").get("visible"), "0")

    def test_compile_gives_files_load_patch_and_spouse_patio_field(self):
        patio = filled()
        patio["sheets"]["own"] = self.own_sheet()
        patio["layers"]["Front"][5] = ["own", 0]
        result = compile_patio(patio, "Pixelheart_abc_SpousePatio", self.root, "assets/patio/")
        self.assertEqual(sorted(result["files"]), ["assets/patio/paths.png", "assets/patio/patio.tmx", "assets/patio/patio_tiles.png"])
        self.assertEqual(result["patches"], [{"Action": "Load", "Target": "Maps/Pixelheart_abc_SpousePatio",
                                              "FromFile": "assets/patio/patio.tmx"}])
        self.assertEqual(result["npc_field"], {"MapAsset": "Pixelheart_abc_SpousePatio",
                                               "MapSourceRect": {"X": 0, "Y": 0, "Width": 4, "Height": 4}})
        animated = filled(animation={"frames": [[16, 200], [17, 100]], "offset": [0, -8]})
        field = compile_patio(animated, "Id", self.root, "p/")["npc_field"]
        self.assertEqual(field["SpriteAnimationFrames"], [[16, 200], [17, 100]])
        self.assertEqual(field["SpriteAnimationPixelOffset"], {"X": 0, "Y": -8})
        self.assertNotIn("SpriteAnimationPixelOffset",
                         compile_patio(filled(animation={"frames": [[1, 100]], "offset": [0, 0]}), "Id", self.root, "p/")["npc_field"])

    def test_a_resized_own_sheet_is_reported(self):
        patio = filled()
        patio["sheets"]["own"] = self.own_sheet()
        patio["sheets"]["own"]["columns"] = 4
        patio["layers"]["Front"][0] = ["own", 0]
        self.assertIn(("error", "sheets.own"), [(l, f) for l, f, _ in patio_issues(patio, {"romanceable": True}, self.root)])

    def test_world_validates_exports_and_backs_up_the_patio(self):
        world = new_world()
        world["spouse_patio"] = filled()
        world["spouse_patio"]["sheets"]["own"] = self.own_sheet()
        world["spouse_patio"]["layers"]["Front"][0] = ["own", 0]
        self.assertEqual(world_structure_issues(world), [])
        self.assertIn(world["spouse_patio"]["sheets"]["own"]["asset"], list(world_asset_references(world)))
        character = new_project()["character"]
        character.update(name="Mira", internal_name="Mira", romanceable=True)
        self.assertEqual([i for i in world_issues(world, character, self.root) if i["level"] == "error"], [])
        compiled = compile_world(world, character, self.root)
        field = compiled["npc_fields"]["SpousePatio"]
        self.assertTrue(field["MapAsset"].endswith("_SpousePatio"))
        load = next(p for p in compiled["patches"] if p["Target"] == "Maps/" + field["MapAsset"])
        self.assertIn(load["FromFile"], compiled["files"])
        self.assertIn(world["spouse_patio"]["sheets"]["own"]["asset"], compiled["files"])
        self.assertEqual(compiled["world"]["spouse_patio"], world["spouse_patio"])

    def test_world_reports_patio_problems_with_world_fields(self):
        world = new_world()
        world["spouse_patio"] = new_patio()
        character = {**new_project()["character"], "romanceable": True}
        fields = [issue["field"] for issue in world_issues(world, character, self.root)]
        self.assertIn("world.spouse_patio.layers.Back", fields)
        broken = {**new_world(), "spouse_patio": {"stand": [9, 9]}}
        self.assertEqual([issue["field"] for issue in world_structure_issues(broken)], ["world.spouse_patio.stand"])
        with self.assertRaises(WorldError):
            normalize_world(broken)
        self.assertNotIn("spouse_patio", normalize_world(new_world()))
        world["spouse_patio"] = None
        self.assertEqual(world_structure_issues(world), [])

    def test_project_saves_loads_and_copies_the_patio_and_its_sheet(self):
        from pixelheart_core.projects import copy_project, load_project, save_project
        document = new_project()
        project = self.root / "Mira" / "character.json"
        project.parent.mkdir()
        self.root = project.parent
        patio = filled()
        patio["sheets"]["own"] = self.own_sheet()
        patio["layers"]["Front"][0] = ["own", 0]
        document["world"] = {**new_world(), "spouse_patio": patio}
        save_project(document, project)
        self.assertEqual(load_project(project)["world"]["spouse_patio"], normalize_patio(patio))
        copy = copy_project(load_project(project), project, self.root.parent / "Copy" / "character.json")
        self.assertTrue((copy.parent / patio["sheets"]["own"]["asset"]).is_file())

    def test_mod_archive_gives_the_character_their_patio(self):
        from pixelheart_core.exporting import build_mod_archive
        world = {**new_world(), "spouse_patio": filled()}
        document = new_project()
        character = document["character"]
        character.update(name="Mira", internal_name="Mira", romanceable=True)
        portrait, sprite = self.root / "portrait.png", self.root / "sprite.png"
        Image.new("RGBA", (128, 192)).save(portrait)
        Image.new("RGBA", (64, 416)).save(sprite)
        payload = build_mod_archive(character, portrait, sprite, world=world, project_root=self.root, project_document=document)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            name = next(n for n in archive.namelist() if n.endswith("content.json"))
            content = json.loads(archive.read(name))
            tmx = next(n for n in archive.namelist() if n.endswith("patio.tmx"))
            self.assertIn(b"spring_outdoorsTileSheet", archive.read(tmx))
            self.assertFalse(any("outdoorsTileSheet.png" in n for n in archive.namelist()))
        npc = next(entry for patch in content["Changes"] if patch["Target"] == "Data/Characters"
                   for entry in patch["Entries"].values())
        self.assertEqual(npc["SpousePatio"]["MapSourceRect"], {"X": 0, "Y": 0, "Width": 4, "Height": 4})


class PatioGameTests(unittest.TestCase):
    def setUp(self):
        clear_game_cache()
        self.addCleanup(clear_game_cache)
        self.game = fake_game(Path(self.enterContext(tempfile.TemporaryDirectory())) / "Stardew Valley")

    def test_farm_ground_copies_the_spouse_area_from_the_farm(self):
        patio = farm_ground(self.game)
        self.assertEqual(list(patio["sheets"]), ["spring_outdoorsTileSheet"])
        self.assertEqual(patio["sheets"]["spring_outdoorsTileSheet"],
                         {"source": "game", "image": "spring_outdoorsTileSheet", "columns": 4, "rows": 4})
        self.assertEqual(patio["layers"]["Back"][0], ["spring_outdoorsTileSheet", 5])
        self.assertEqual(patio["layers"]["Back"][1], ["spring_outdoorsTileSheet", 6])
        self.assertEqual(patio["layers"]["Buildings"][1 * 4 + 2], ["spring_outdoorsTileSheet", 9])
        self.assertEqual(patio["stand"], list(DEFAULT_STAND))

    def test_game_patios_are_the_non_empty_blocks_with_their_standing_spots(self):
        patios = game_patios(self.game)
        self.assertEqual(len(patios), 2)
        self.assertEqual(patios[0]["layers"]["Buildings"][5], ["patioTiles", 3])
        self.assertEqual(patios[0]["stand"], list(DEFAULT_STAND))
        self.assertEqual(patios[1]["layers"]["Back"][0], ["patioTiles", 2])
        self.assertEqual(patios[1]["stand"], [1, 2])
        self.assertEqual(patio_structure_issues(patios[1]), [])

    def test_sheet_choices_list_the_patio_sheet_first_and_skip_missing_ones(self):
        choices = game_sheet_choices(self.game)
        self.assertEqual([(label, sheet["image"]) for label, sheet in choices],
                         [("Game patios", "patioTiles"), ("Outdoors", "spring_outdoorsTileSheet")])
        self.assertEqual((choices[1][1]["columns"], choices[1][1]["rows"]), (4, 4))

    def test_textures_follow_the_season_and_render_the_patio(self):
        self.assertEqual(game_texture(self.game, "spring_outdoorsTileSheet", "summer").getpixel((16, 0)), (1, 9, 0, 255))
        self.assertEqual(game_texture(self.game, "spring_outdoorsTileSheet", "winter").getpixel((16, 0)), (1, 0, 0, 255))
        patio = filled(tile=2)
        patio["layers"]["Front"][15] = ["spring_outdoorsTileSheet", 3]
        textures = {"spring_outdoorsTileSheet": game_texture(self.game, "spring_outdoorsTileSheet")}
        back, front = render_patio(patio, textures)
        self.assertEqual(back.getpixel((0, 0)), (2, 0, 0, 255))
        self.assertEqual(front.getpixel((63, 63)), (3, 0, 0, 255))
        self.assertEqual(front.getpixel((0, 0))[3], 0)
        self.assertEqual(render_patio(patio, {})[0].getpixel((0, 0))[3], 0)

    def test_farm_context_centres_the_patio_corner(self):
        context = farm_context(self.game, margin=3)
        self.assertEqual(context["corner"], (6, 4))
        self.assertEqual(context["origin"], (48, 48))
        self.assertEqual(context["back"].size, ((12 - 3) * 16, (10 - 1) * 16))


if __name__ == "__main__":
    unittest.main()

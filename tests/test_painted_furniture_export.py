"""Painted furniture reaches the pack as a new Data/Furniture item with its own texture."""
from copy import deepcopy
import io
import unittest

from PIL import Image

from pixelheart_core.interior_furniture import import_texture, validate_definition
from pixelheart_core.interiors import InteriorDraft
from pixelheart_core.painted_furniture import build_painted, local_hex, painting_canvas
from pixelheart_core.world import exported_location_id, exported_npc_id
from tests import test_interior_exporting as export_tests
from tests.test_painted_furniture import chair, game_sheet, mirrored, png


class PaintedFurnitureExportTests(unittest.TestCase):
    setUp_export = export_tests.InteriorExportTests.setUp
    location = export_tests.InteriorExportTests.location
    compile = export_tests.InteriorExportTests.compile
    archive = export_tests.InteriorExportTests.archive
    runtime = staticmethod(export_tests.InteriorExportTests.runtime)

    def setUp(self):
        self.setUp_export()
        sheet = self.root / "furniture-sheet.png"
        mirrored(game_sheet()).save(sheet)
        library = validate_definition(chair(preview_asset=import_texture(sheet, self.project_root)))
        canvas = painting_canvas(library, self.project_root)
        canvas.putpixel((0, 0), (10, 120, 250, 255))
        self.painted = build_painted(library, png(canvas), self.project_root, price=350)
        self.hex = local_hex(self.painted)
        self.npc_id = exported_npc_id(self.character)
        self.key = f"{self.npc_id}_Furniture_{self.hex}"

    def place_painted(self, location, x, y):
        design = location["interior"]
        design["catalog"].append(deepcopy(self.painted))
        draft = InteriorDraft(design)
        draft.place_furniture(self.painted["id"], x, y)
        location["interior"] = draft.snapshot()

    def patches(self, compiled, action, target):
        return [patch for patch in compiled["patches"] if patch["Action"] == action and patch["Target"] == target]

    def test_painted_placement_uses_the_packs_item_and_texture(self):
        self.place_painted(self.residence, 9, 7)
        compiled = self.compile()
        identity = exported_location_id(self.residence, self.character)
        items = {item["item_id"] for item in self.runtime(compiled)[identity]["furniture"]}
        self.assertIn("(F)" + self.key, items)
        self.assertNotIn(self.painted["id"], items)
        self.assertIn("(F)0", items)
        texture = f"Mods/{self.npc_id}/Furniture/{self.hex}"
        load = self.patches(compiled, "Load", texture)
        self.assertEqual(load, [{"Action": "Load", "Target": texture, "FromFile": f"assets/furniture/{self.hex}.png"}])
        front = self.patches(compiled, "Load", texture + "Front")
        self.assertEqual(front[0]["FromFile"], f"assets/furniture/{self.hex}Front.png")
        with Image.open(io.BytesIO(compiled["files"][f"assets/furniture/{self.hex}.png"])) as image:
            self.assertEqual(image.size, (64, 32))
            self.assertEqual(image.convert("RGBA").getpixel((16, 0)), (10, 120, 250, 255))
        self.assertIn(f"assets/furniture/{self.hex}Front.png", compiled["files"])
        data = self.patches(compiled, "EditData", "Data/Furniture")
        self.assertEqual(data[0]["Entries"], {
            self.key: f"{self.key}/chair/1 2/1 1/4/350/-1/Painted Oak Chair/1/{texture.replace('/', chr(92))}/true"})

    def test_the_record_reads_back_as_native_furniture(self):
        import json
        from pixelheart_core.interior_furniture import read_native_catalog
        self.place_painted(self.residence, 9, 7)
        entries = self.patches(self.compile(), "EditData", "Data/Furniture")[0]["Entries"]
        path = self.root / "Furniture.json"
        path.write_text(json.dumps(entries), encoding="utf-8")
        definition = read_native_catalog(path)["definitions"][0]
        self.assertEqual(definition["id"], "(F)" + self.key)
        self.assertEqual(definition["texture"], f"Mods/{self.npc_id}/Furniture/{self.hex}")
        self.assertEqual((definition["sprite_index"], definition["rotations"], definition["sprite_size"],
                          definition["footprint"], definition["placement"]), (1, 4, [1, 2], [1, 1], "default"))

    def test_checklist_mentions_painted_pieces_only_when_used(self):
        self.assertNotIn(b"painted piece", self.compile()["files"]["INTERIOR_TESTING.txt"])
        self.place_painted(self.residence, 9, 7)
        self.assertIn(b"painted piece", self.compile()["files"]["INTERIOR_TESTING.txt"])

    def test_painted_piece_in_two_rooms_is_exported_once(self):
        self.place_painted(self.residence, 9, 7)
        self.place_painted(self.spouse, 4, 4)
        compiled = self.compile()
        texture = f"Mods/{self.npc_id}/Furniture/{self.hex}"
        self.assertEqual(len(self.patches(compiled, "Load", texture)), 1)
        self.assertEqual(len(self.patches(compiled, "Load", texture + "Front")), 1)
        data = self.patches(compiled, "EditData", "Data/Furniture")
        self.assertEqual(len(data), 1)
        self.assertEqual(list(data[0]["Entries"]), [self.key])

    def test_native_record_escapes_slashes(self):
        self.painted["name"] = "Painted A/B chair"
        self.place_painted(self.residence, 9, 7)
        record = self.patches(self.compile(), "EditData", "Data/Furniture")[0]["Entries"][self.key]
        self.assertEqual(len(record.split("/")), 11)
        self.assertIn("/Painted A-B chair/", record)

    def test_unpainted_designs_add_no_furniture_data(self):
        compiled = self.compile()
        self.assertEqual(self.patches(compiled, "EditData", "Data/Furniture"), [])
        self.assertFalse(any(path.startswith("assets/furniture/") for path in compiled["files"]))

    def test_archive_contains_the_painted_texture(self):
        self.place_painted(self.residence, 9, 7)
        with self.archive() as archive:
            names = archive.namelist()
        self.assertIn(f"[CP] Mira/assets/furniture/{self.hex}.png", names)
        self.assertIn(f"[CP] Mira/assets/furniture/{self.hex}Front.png", names)


if __name__ == "__main__":
    unittest.main()

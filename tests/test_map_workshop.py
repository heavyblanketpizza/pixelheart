"""The local map painter preserves supplied art and exports finite tile maps."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from PIL import Image
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from pixelheart.map_workshop import TileMapDraft, MapWorkshop, inspect_tilesheet, read_painted_map, LAYERS
from pixelheart_core.world import WorldError, map_bundle
from pixelheart.theme import apply_theme


class _MapFiles:
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="pixelheart-map-test-")
        self.root = Path(self.directory.name)
        self.source = self.root / "tiles.png"
        image = Image.new("RGBA", (32, 32), "#b9c4a0")
        image.paste("#745c83", (16, 0, 32, 16))
        image.save(self.source)
        self.sheet = inspect_tilesheet(self.source)

    def tearDown(self):
        self.directory.cleanup()


class MapDraftTests(_MapFiles, unittest.TestCase):
    def test_tilesheet_is_preserved_and_cell_count_matches(self):
        self.assertEqual(self.sheet["bytes"], self.source.read_bytes())
        self.assertEqual(self.sheet["tile_count"], 4)
        self.assertEqual(self.sheet["columns"], 2)

    def test_rejects_wrong_format_dimensions_and_oversized_images(self):
        for size, format in (((17, 16), "PNG"), ((16, 16), "GIF"), ((2064, 16), "PNG"), ((8, 16), "PNG")):
            with self.subTest(size=size, format=format):
                path = self.root / "invalid.png"
                Image.new("RGB", size).save(path, format=format)
                with self.assertRaises(WorldError):
                    inspect_tilesheet(path)
        self.source.write_bytes(b"not a png")
        with self.assertRaises(WorldError):
            inspect_tilesheet(self.source)

    def test_paint_erase_and_undo_are_layer_specific(self):
        draft = TileMapDraft("spouse_room")
        self.assertTrue(draft.paint("Buildings", 2, 3, 2))
        self.assertEqual(draft.layers["Buildings"][3 * 6 + 2], 2)
        self.assertEqual(draft.layers["Back"][3 * 6 + 2], 0)
        draft.paint("Buildings", 2, 3, 0)
        draft.undo()
        self.assertEqual(draft.layers["Buildings"][3 * 6 + 2], 2)
        draft.undo()
        self.assertEqual(draft.layers["Buildings"][3 * 6 + 2], 0)

    def test_one_drag_has_one_undo_entry(self):
        draft = TileMapDraft()
        draft.begin_stroke()
        draft.paint("Back", 1, 1, 1)
        draft.paint("Back", 2, 1, 1)
        draft.paint("Back", 3, 1, 1)
        draft.end_stroke()
        self.assertEqual(len(draft.history), 1)
        draft.undo()
        self.assertFalse(any(draft.layers["Back"]))

    def test_flood_respects_boundaries_and_fill_changes_only_one_layer(self):
        draft = TileMapDraft("spouse_room")
        for y in range(draft.height):
            draft.paint("Back", 2, y, 2)
        draft.flood("Back", 0, 0, 1)
        for y in range(draft.height):
            self.assertEqual(draft.layers["Back"][y * 6:y * 6 + 6], [1, 1, 2, 0, 0, 0])
        draft.fill("Front", 3)
        self.assertEqual(draft.layers["Front"], [3] * 54)
        self.assertFalse(any(draft.layers["Buildings"]))

    def test_resize_preserves_overlap_and_undo_restores_cropped_tiles(self):
        draft = TileMapDraft()
        draft.paint("Back", 19, 19, 2)
        draft.paint("Buildings", 2, 3, 1)
        draft.resize("spouse_room")
        self.assertEqual((draft.width, draft.height), (6, 9))
        self.assertEqual(draft.layers["Buildings"][3 * 6 + 2], 1)
        draft.undo()
        self.assertEqual((draft.width, draft.height), (20, 20))
        self.assertEqual(draft.layers["Back"][-1], 2)

    def test_blank_floor_and_missing_sheet_prevent_save(self):
        draft = TileMapDraft()
        with self.assertRaises(WorldError):
            draft.to_tmx(None)
        with self.assertRaises(WorldError):
            draft.to_tmx(self.sheet)
        draft.fill("Back", 5)
        with self.assertRaises(WorldError):
            draft.to_tmx(self.sheet)

    def test_tmx_is_accepted_as_a_complete_portable_game_map(self):
        draft = TileMapDraft("spouse_room")
        draft.fill("Back", 1)
        draft.paint("Buildings", 0, 0, 2)
        path = self.root / "place.tmx"
        path.write_bytes(draft.to_tmx(self.sheet))
        bundle = map_bundle(path)
        self.assertEqual((bundle["width"], bundle["height"]), (6, 9))
        self.assertEqual(set(bundle["layers"]), set(LAYERS))
        self.assertEqual(set(bundle["files"]), {"place.tmx", "tiles.png"})
        xml = ET.fromstring(bundle["files"]["place.tmx"])
        self.assertEqual(xml.find("tileset/image").get("source"), "tiles.png")
        self.assertEqual(xml.find("layer[@name='Paths']").get("visible"), "0")
        self.assertEqual(len(xml.find("layer/data").text.replace("\n", "").split(",")), 54)

    def test_invalid_paint_positions_do_not_mutate_map(self):
        draft = TileMapDraft("spouse_room")
        before = draft.snapshot()
        for args in (("Back", -1, 0, 1), ("Back", 6, 0, 1), ("Unknown", 0, 0, 1), ("Back", 0, 0, True)):
            with self.subTest(args=args), self.assertRaises(WorldError):
                draft.paint(*args)
        self.assertEqual(draft.snapshot(), before)
        self.assertEqual(draft.history, [])

    def test_saved_painting_reopens_without_losing_any_layers_or_pixels(self):
        draft = TileMapDraft("spouse_room")
        draft.fill("Back", 1)
        for index, layer in enumerate(LAYERS, 1):
            draft.paint(layer, 3, 4, index)
        path = self.root / "place.tmx"
        path.write_bytes(draft.to_tmx(self.sheet))
        loaded, sheet = read_painted_map(path)
        self.assertEqual(loaded.snapshot(), draft.snapshot())
        self.assertEqual(sheet["bytes"], self.source.read_bytes())
        self.assertEqual(loaded.to_tmx(sheet), path.read_bytes())

    def test_external_map_properties_are_not_silently_removed(self):
        draft = TileMapDraft()
        draft.fill("Back", 1)
        root = ET.fromstring(draft.to_tmx(self.sheet))
        properties = ET.SubElement(root, "properties")
        ET.SubElement(properties, "property", {"name": "Warp", "value": "1 1 Town 32 64"})
        path = self.root / "place.tmx"
        path.write_bytes(ET.tostring(root))
        before = path.read_bytes()
        with self.assertRaisesRegex(WorldError, "outside Pixelheart"):
            read_painted_map(path)
        self.assertEqual(path.read_bytes(), before)


class MapWorkshopTests(_MapFiles, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_theme(cls.app)

    def setUp(self):
        super().setUp()
        self.project = self.root / "project" / "character.json"
        self.dialog = MapWorkshop(self.project)

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        self.app.processEvents()
        super().tearDown()

    def test_widget_palette_canvas_and_undo(self):
        self.dialog.set_preset("spouse_room")
        self.dialog.load_tilesheet(self.source)
        self.dialog.show()
        self.app.processEvents()
        QTest.mouseClick(self.dialog.palette, Qt.MouseButton.LeftButton, pos=QPoint(42, 8))
        self.assertEqual(self.dialog.canvas.tile, 2)
        cell = 16 * self.dialog.canvas.scale
        QTest.mouseClick(self.dialog.canvas, Qt.MouseButton.LeftButton, pos=QPoint(cell + 8, cell + 8))
        self.assertEqual(self.dialog.draft.layers["Back"][7], 2)
        self.dialog.undo()
        self.assertEqual(self.dialog.draft.layers["Back"][7], 0)

    def test_save_returns_portable_reference_and_copies_original_png(self):
        self.dialog.set_preset("spouse_room")
        self.dialog.load_tilesheet(self.source)
        self.dialog.fill_layer()
        reference = self.dialog.save_map()
        self.assertTrue(reference.startswith("world_assets/maps/"))
        self.assertEqual(self.dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(self.dialog.result_size, (6, 9))
        self.assertTrue(self.dialog.result_is_spouse_room)
        target = self.project.parent / reference
        self.assertTrue(target.is_file())
        self.assertEqual((target.parent / "tiles.png").read_bytes(), self.source.read_bytes())
        self.assertEqual(map_bundle(target)["width"], 6)

    def test_new_saved_revision_never_overwrites_previous_map(self):
        self.dialog.load_tilesheet(self.source)
        self.dialog.fill_layer()
        first_reference = self.dialog.save_map()
        first = self.project.parent / first_reference
        first_bytes = first.read_bytes()
        self.dialog.load_map(first_reference)
        self.assertIsNone(self.dialog.result_reference)
        self.assertIsNone(self.dialog.result_size)
        self.dialog.draft.paint("Buildings", 2, 2, 2)
        second_reference = self.dialog.save_map()
        self.assertNotEqual(first_reference, second_reference)
        self.assertEqual(first.read_bytes(), first_bytes)

    def test_replacing_with_too_small_sheet_preserves_previous_selection(self):
        self.dialog.load_tilesheet(self.source)
        self.dialog.draft.paint("Back", 0, 0, 4)
        smaller = self.root / "small.png"
        Image.new("RGBA", (16, 16)).save(smaller)
        with self.assertRaises(WorldError):
            self.dialog.load_tilesheet(smaller)
        self.assertEqual(self.dialog.sheet["tile_count"], 4)

    def test_saved_place_can_be_edited_into_new_revision(self):
        self.dialog.set_preset("spouse_room")
        self.dialog.load_tilesheet(self.source)
        self.dialog.fill_layer()
        self.dialog.draft.paint("Buildings", 2, 2, 2)
        first_reference = self.dialog.save_map()
        before = (self.project.parent / first_reference).read_bytes()
        reopened = MapWorkshop(self.project)
        try:
            reopened.load_map(first_reference)
            self.assertEqual(reopened.draft.layers["Buildings"][2 * 6 + 2], 2)
            self.assertEqual((reopened.draft.width, reopened.draft.height), (6, 9))
            reopened.draft.paint("Front", 4, 4, 3)
            second_reference = reopened.save_map()
            self.assertNotEqual(first_reference, second_reference)
            self.assertEqual((self.project.parent / first_reference).read_bytes(), before)
        finally:
            reopened.close()
            reopened.deleteLater()

    def test_rejected_external_map_leaves_active_draft_unchanged(self):
        self.dialog.load_tilesheet(self.source)
        self.dialog.fill_layer()
        reference = self.dialog.save_map()
        path = self.project.parent / reference
        xml = ET.fromstring(path.read_bytes())
        ET.SubElement(xml, "objectgroup", {"name": "Important map objects"})
        path.write_bytes(ET.tostring(xml))
        before = self.dialog.draft.snapshot()
        with self.assertRaises(WorldError):
            self.dialog.load_map(reference)
        self.assertEqual(self.dialog.draft.snapshot(), before)


if __name__ == "__main__":
    unittest.main()

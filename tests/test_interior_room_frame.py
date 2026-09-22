"""Structural trim stays separate from room finishes in previews and exports."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from pixelheart.interior_editor import InteriorEditor
from pixelheart_core.interior_surface_design import (
    apply_surface, stage_room_frame, stage_surface_library,
)
from pixelheart_core.interiors import (
    InteriorError, floor_cells, interior_tmx, map_layers, new_interior,
    normalize_interior, render_interior,
)
from pixelheart_core.world import asset_path


ROLES = ("top_left", "top", "top_right", "left", "right",
         "bottom_left_outer", "bottom_left_inner", "bottom",
         "bottom_right_inner", "bottom_right_outer")


class InteriorRoomFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.enterContext(patch("pixelheart.interior_editor.game_import_settings", return_value=self.settings))
        self.dialogs = []
        self.frame_colors = {role: (20 + index * 20, 220 - index * 15, 55 + index * 11, 255)
                             for index, role in enumerate(ROLES)}

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.reject()
            dialog.deleteLater()
        self.app.processEvents()

    def frame(self, directory=None, *, joins=False):
        directory = directory or self.root
        directory.mkdir(parents=True, exist_ok=True)
        roles = ROLES + (("top_join_left", "top_join_right", "bottom_join_right", "bottom_join_left") if joins else ())
        colors = {**self.frame_colors, "top_join_left": (250, 30, 80, 255),
                  "top_join_right": (80, 250, 30, 255), "bottom_join_right": (30, 80, 250, 255),
                  "bottom_join_left": (230, 230, 80, 255)}
        with Image.new("RGBA", (len(roles) * 16, 16)) as image:
            for index, role in enumerate(roles):
                # A clear upper half catches compositing errors: the bottom
                # trim must leave the floor underneath visible.
                image.paste(colors[role], (index * 16, 8, index * 16 + 16, 16))
            image.save(directory / "frame.png")
        return {"preview_asset": "frame.png",
                "tiles": {role: [index * 16, 0, 16, 16] for index, role in enumerate(roles)}}

    def design(self, kind="residence", *, framed=True):
        surfaces = []
        for identity, surface_kind, color in (("(WP)Blue", "wall", "blue"),
                                              ("(WP)Red", "wall", "red"),
                                              ("(FL)Green", "floor", "green")):
            size = (16, 48) if surface_kind == "wall" else (32, 32)
            filename = identity[4:] + ".png"
            with Image.new("RGBA", size, color) as image:
                image.save(self.root / filename)
            surfaces.append({"id": identity, "name": identity[4:], "kind": surface_kind,
                             "texture": "Maps/Test", "preview_asset": filename,
                             "rect": [0, 0, *size]})
        data = stage_surface_library(new_interior(kind), surfaces, self.root)
        data = apply_surface(apply_surface(data, "(WP)Blue"), "(FL)Green")
        if framed:
            data = stage_room_frame(data, self.frame(), self.root)
        return data

    def editor(self, design):
        dialog = InteriorEditor(self.root / "character.json", design, design["kind"])
        self.dialogs.append(dialog)
        return dialog

    def test_native_frame_roles_form_a_continuous_shell_and_do_not_block_the_floor(self):
        data = self.design()
        layers = map_layers(data)
        room = data["rooms"][0]
        left, right = room["x"], room["x"] + room["width"] - 1
        top, bottom = room["y"] - 4, room["y"] + room["height"] - 1
        expected = {(left - 1, top): "top_left", (left, top): "top",
                    (right + 1, top): "top_right", (left - 1, top + 1): "left",
                    (right + 1, top + 1): "right", (left - 1, bottom): "bottom_left_outer",
                    (left, bottom): "bottom_left_inner", (left + 1, bottom): "bottom",
                    (right, bottom): "bottom_right_inner", (right + 1, bottom): "bottom_right_outer"}
        for (x, y), role in expected.items():
            with self.subTest(role=role):
                self.assertEqual(layers["Front"][y * data["width"] + x], data["room_frame"][role] + 1)
        for x, y in floor_cells(data):
            self.assertEqual(layers["Buildings"][y * data["width"] + x], 0, (x, y))

    def test_stepped_rooms_use_concave_joins_without_overwriting_caps_or_floor(self):
        cases = [
            ({"x": 12, "y": 6, "width": 4, "height": 4},
             {(12, 2): "top_join_left", (12, 9): "bottom_join_left"}),
            ({"x": 6, "y": 13, "width": 4, "height": 4},
             {(5, 12): "bottom_join_right", (10, 12): "bottom_join_left"}),
            ({"x": 0, "y": 6, "width": 2, "height": 4},
             {(1, 2): "top_join_right", (1, 9): "bottom_join_right"}),
        ]
        for rectangle, expected in cases:
            with self.subTest(rectangle=rectangle):
                data = stage_room_frame(self.design(framed=False), self.frame(joins=True), self.root)
                data["rooms"].append({"id": "branch", "name": "Branch", "optional": True,
                                      "enabled": True, **rectangle})
                data = normalize_interior(data)
                layers = map_layers(data)
                for (x, y), role in expected.items():
                    self.assertEqual(layers["Front"][y * data["width"] + x], data["room_frame"][role] + 1)
                for x, y in floor_cells(data):
                    self.assertEqual(layers["Buildings"][y * data["width"] + x], 0, (x, y))
                exported = ET.fromstring(interior_tmx(data))
                self.assertEqual([int(v.strip()) for v in exported.find("layer[@name='Front']/data").text.split(",")],
                                 layers["Front"])

    def test_wallpaper_changes_leave_frame_tiles_and_pixels_unchanged(self):
        original = self.design()
        changed = apply_surface(original, "(WP)Red")
        self.assertEqual(changed["room_frame"], original["room_frame"])
        self.assertEqual(map_layers(changed)["Front"], map_layers(original)["Front"])
        with render_interior(original, self.root) as before, render_interior(changed, self.root) as after:
            self.assertNotEqual(before.getpixel((3 * 16 + 8, 3 * 16 + 8)),
                                after.getpixel((3 * 16 + 8, 3 * 16 + 8)))
            for index, gid in enumerate(map_layers(original)["Front"]):
                if gid:
                    pixel = (index % original["width"] * 16 + 8, index // original["width"] * 16 + 12)
                    self.assertEqual(before.getpixel(pixel), after.getpixel(pixel))

    def test_preview_and_export_agree_and_clear_trim_pixels_show_floor_beneath(self):
        data = self.design()
        layers = map_layers(data)
        exported = ET.fromstring(interior_tmx(data))
        front = [int(value.strip()) for value in exported.find("layer[@name='Front']/data").text.split(",")]
        self.assertEqual(front, layers["Front"])
        role_colors = {data["room_frame"][role] + 1: color for role, color in self.frame_colors.items()}
        with render_interior(data, self.root) as preview:
            for index, gid in enumerate(front):
                if gid:
                    self.assertEqual(preview.getpixel((index % data["width"] * 16 + 8,
                                                       index // data["width"] * 16 + 12)), role_colors[gid])
            room = data["rooms"][0]
            x, y = room["x"] + 2, room["y"] + room["height"] - 1
            self.assertEqual(preview.getpixel((x * 16 + 8, y * 16 + 2)), (0, 128, 0, 255))
            self.assertEqual(preview.getpixel((x * 16 + 8, y * 16 + 12)), self.frame_colors["bottom"])

    def test_spouse_insert_has_no_front_frame_even_when_frame_metadata_exists(self):
        data = self.design("spouse", framed=False)
        residence = self.design()
        # An otherwise valid embedded frame must never enclose a spouse insert.
        data["atlas"] = residence["atlas"]
        data["room_frame"] = residence["room_frame"]
        data = normalize_interior(data)
        layers = map_layers(data)
        self.assertFalse(any(layers["Front"]))
        for x, y in floor_cells(data):
            self.assertEqual(layers["Buildings"][y * data["width"] + x], 0)

    def test_frame_staging_preserves_existing_tiles_and_is_idempotent(self):
        original = self.design(framed=False)
        before = deepcopy(original)
        payload = asset_path(original["atlas"]["asset"], self.root).read_bytes()
        framed = stage_room_frame(original, self.frame(), self.root)
        self.assertEqual(original, before)
        self.assertEqual(framed["style"], original["style"])
        self.assertEqual(framed["surfaces"], original["surfaces"])
        self.assertEqual(asset_path(original["atlas"]["asset"], self.root).read_bytes(), payload)
        self.assertEqual(stage_room_frame(framed, self.frame(), self.root), framed)

    def test_frame_with_an_out_of_bounds_crop_writes_no_partial_atlas(self):
        data = self.design(framed=False)
        frame = self.frame()
        frame["tiles"]["bottom_right_outer"] = [160, 0, 16, 16]
        before = set(self.root.rglob("*.png"))
        with self.assertRaises(InteriorError):
            stage_room_frame(data, frame, self.root)
        self.assertEqual(set(self.root.rglob("*.png")), before)

    def test_reopening_a_connected_legacy_design_adds_only_missing_frame(self):
        original = self.design(framed=False)
        original["catalog"] = [{"id": "(F)Local.Chair", "name": "Local chair", "kind": "chair",
                                "footprint": [1, 1], "rotations": 1}]
        original = normalize_interior(original)
        original["rooms"][0]["name"] = "My custom room"
        before = deepcopy(original)
        library = self.root / "library"
        frame = self.frame(library)
        (library / "library.json").write_text(json.dumps({
            "format": "pixelheart-interior-library", "version": 1,
            "definitions": [{"id": "(F)Replacement.Chair", "name": "Replacement chair", "kind": "chair",
                             "footprint": [1, 1], "rotations": 1}],
            "surfaces": [], "room_frame": frame,
        }), encoding="utf-8")
        self.settings.setValue("interiors/libraryFolder", str(library))
        dialog = self.editor(original)
        self.assertTrue(dialog.draft.data.get("room_frame"), dialog.status.text())
        for key in ("catalog", "surfaces", "style", "rooms", "furniture", "entry"):
            self.assertEqual(dialog.draft.data[key], before[key], key)
        self.assertEqual(original, before)
        self.assertTrue(asset_path(dialog.draft.data["atlas"]["asset"], dialog.stage_root).is_file())

    def test_loading_a_custom_atlas_removes_stale_frame_and_undo_restores_it(self):
        original = self.design()
        dialog = self.editor(original)
        with Image.new("RGBA", (16, 16), "purple") as image:
            image.save(self.root / "custom.png")
        dialog.load_atlas(self.root / "custom.png")
        self.assertFalse(dialog.draft.data.get("room_frame"))
        self.assertFalse(any(map_layers(dialog.draft.data)["Front"]))
        dialog.undo()
        self.assertEqual(dialog.draft.data["room_frame"], original["room_frame"])
        self.assertEqual(dialog.draft.data["atlas"], original["atlas"])


if __name__ == "__main__":
    unittest.main()

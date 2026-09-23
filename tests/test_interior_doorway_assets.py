"""Optional entrance artwork remains portable without replacing room finishes."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from pixelheart_core.interior_furniture import (
    ROOM_FRAME_DOORWAY, ROOM_FRAME_JOINS, ROOM_FRAME_TILES,
    import_furniture_library,
)
from pixelheart_core.interior_surface_design import stage_room_frame
from pixelheart_core.interiors import InteriorError, new_interior
from pixelheart_core.world import asset_path


class InteriorDoorwayAssetTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.library = self.root / "library"
        self.library.mkdir()
        self.project = self.root / "project"
        self.project.mkdir()
        roles = ROOM_FRAME_TILES + ROOM_FRAME_JOINS + ROOM_FRAME_DOORWAY
        self.colors = {role: (index * 13, 80, 150, 255)
                       for index, role in enumerate(roles)}
        with Image.new("RGBA", (len(roles) * 16, 16)) as image:
            for index, role in enumerate(roles):
                image.paste(self.colors[role], (index * 16, 0, (index + 1) * 16, 16))
            image.save(self.library / "frame.png")
        self.frame = {"preview_asset": "frame.png", "tiles": {
            role: [index * 16, 0, 16, 16] for index, role in enumerate(roles)}}
        self.path = self.library / "library.json"
        self.path.write_text(json.dumps({"format": "pixelheart-interior-library",
                                        "version": 1, "definitions": [],
                                        "room_frame": self.frame}), encoding="utf-8")

    def imported_frame(self):
        return import_furniture_library(self.path, self.project)["room_frame"]

    def test_imported_entrance_corners_survive_source_removal_and_staging(self):
        frame = self.imported_frame()
        self.assertEqual(frame["tiles"], self.frame["tiles"])
        (self.library / "frame.png").unlink()
        data = stage_room_frame(new_interior(), frame, self.project)
        with Image.open(asset_path(data["atlas"]["asset"], self.project)) as atlas:
            for role in ROOM_FRAME_DOORWAY:
                tile = data["room_frame"][role]
                pixel = (tile % data["atlas"]["columns"] * 16,
                         tile // data["atlas"]["columns"] * 16)
                self.assertEqual(atlas.getpixel(pixel), self.colors[role])

    def test_adding_entrance_corners_preserves_all_previous_frame_and_finish_indexes(self):
        frame = self.imported_frame()
        old_frame = deepcopy(frame)
        for role in ROOM_FRAME_DOORWAY:
            del old_frame["tiles"][role]
        original = stage_room_frame(new_interior(), old_frame, self.project)
        before = deepcopy(original)
        upgraded = stage_room_frame(original, frame, self.project)
        self.assertEqual(original, before)
        self.assertEqual(upgraded["style"], original["style"])
        self.assertEqual(upgraded["rooms"], original["rooms"])
        for role, index in original["room_frame"].items():
            self.assertEqual(upgraded["room_frame"][role], index)
        self.assertEqual(stage_room_frame(upgraded, frame, self.project), upgraded)

    def test_invalid_entrance_crop_does_not_leave_a_partial_atlas(self):
        frame = self.imported_frame()
        frame["tiles"]["door_right"] = [256, 0, 16, 16]
        before = {path: path.read_bytes() for path in self.project.rglob("*.png")}
        with self.assertRaisesRegex(InteriorError, "beyond"):
            stage_room_frame(new_interior(), frame, self.project)
        after = {path: path.read_bytes() for path in self.project.rglob("*.png")}
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()

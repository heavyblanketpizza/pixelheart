"""An automatic frame upgrade adds doorway art without changing authored trim."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from pixelheart.interior_editor import InteriorEditor
from pixelheart_core.interior_furniture import ROOM_FRAME_DOORWAY, ROOM_FRAME_TILES
from pixelheart_core.interior_surface_design import stage_room_frame, stage_surface_library
from pixelheart_core.interiors import new_interior
from pixelheart_core.world import asset_path
from tests.qt_support import QtTestCase


class InteriorFrameRefreshTests(QtTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_automatic_doorway_upgrade_keeps_authored_frame_and_cancel_keeps_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
            library = root / "library"
            library.mkdir()
            original_roles = ROOM_FRAME_TILES
            library_roles = ROOM_FRAME_TILES + ROOM_FRAME_DOORWAY
            for folder, roles, color in ((root, original_roles, "blue"),
                                          (library, library_roles, "red")):
                with Image.new("RGBA", (16 * len(roles), 16), color) as image:
                    image.save(folder / "frame.png")
            old_frame = {"preview_asset": "frame.png", "tiles": {
                role: [index * 16, 0, 16, 16] for index, role in enumerate(original_roles)}}
            new_frame = {"preview_asset": "frame.png", "tiles": {
                role: [index * 16, 0, 16, 16] for index, role in enumerate(library_roles)}}
            with Image.new("RGBA", (32, 32), "green") as image:
                image.save(root / "floor.png")
            design = stage_surface_library(new_interior(), [{
                "id": "(FL)Custom", "name": "Custom", "kind": "floor",
                "texture": "Example/Floor", "preview_asset": "floor.png",
                "rect": [0, 0, 32, 32]}], root)
            design = stage_room_frame(design, old_frame, root)
            design["catalog"] = [{"id": "(F)Custom", "name": "Custom chair", "kind": "chair",
                                  "footprint": [1, 1], "rotations": 1}]
            before = deepcopy(design)
            project_file = root / "character.json"
            project_file.write_text(json.dumps(design), encoding="utf-8")
            before_files = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
            (library / "library.json").write_text(json.dumps({
                "format": "pixelheart-interior-library", "version": 1,
                "definitions": [], "room_frame": new_frame}), encoding="utf-8")
            settings.setValue("interiors/libraryFolder", str(library))
            with patch("pixelheart.interior_editor.game_import_settings", return_value=settings):
                editor = InteriorEditor(project_file, design)
                try:
                    upgraded = editor.draft.data
                    for role, tile in before["room_frame"].items():
                        self.assertEqual(upgraded["room_frame"][role], tile)
                    for key in ("surfaces", "style", "rooms", "entry"):
                        self.assertEqual(upgraded[key], before[key])
                    self.assertEqual(upgraded["catalog"][0]["id"], "(F)Custom")
                    with Image.open(asset_path(upgraded["atlas"]["asset"], editor.stage_root)) as atlas:
                        for role, color in (("top", (0, 0, 255, 255)),
                                            ("door_left", (255, 0, 0, 255)),
                                            ("door_right", (255, 0, 0, 255))):
                            tile = upgraded["room_frame"][role]
                            self.assertEqual(atlas.getpixel((tile % upgraded["atlas"]["columns"] * 16,
                                                             tile // upgraded["atlas"]["columns"] * 16)), color)
                    editor.reject()
                    self.assertIsNone(editor.result_design)
                    self.assertEqual(design, before)
                    for path, payload in before_files.items():
                        self.assertEqual(path.read_bytes(), payload)
                finally:
                    editor.reject()
                    editor.deleteLater()
                    self.app.processEvents()


if __name__ == "__main__":
    unittest.main()

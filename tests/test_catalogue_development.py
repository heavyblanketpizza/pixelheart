"""Portable previews stay local and cannot change a room or game content."""
import json
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from pixelheart_core.catalogue_development import (
    CataloguePackError, discover_development_packs, load_development_pack,
)


def make_pack(root):
    root.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (16, 32), "#6699aa").save(root / "piece.png")
    Image.new("RGBA", (16, 32), "#aabbcc").save(root / "farmer.png")
    Image.new("RGBA", (176, 160), "#987654").save(root / "room.png")
    directions = ("south", "east", "north", "west")
    (root / "farmer.json").write_text(json.dumps({"format": "pixelheart-preview-actor",
        "frames": {d: ["farmer.png"] * 4 for d in directions},
        "idle": {d: "farmer.png" for d in directions}}))
    side = {"id": "Example.Globe", "name": "Example globe", "kind": "decor", "views": [
        {"label": "Default", "rotation": 0, "image": "piece.png", "width": 16, "height": 32,
         "footprint": [1, 1]}]}
    data = {"format": "pixelheart-catalogue-development", "version": 1, "title": "A private collection",
        "actor": "farmer.json", "backgrounds": [{"id": "room", "name": "Wood floor & wallpaper", "image": "room.png"}],
        "items": [{"id": "Example.Globe", "slug": "globe", "name": "Example globe", "group": "Study",
                   "description": "A decorative globe.", "match": "Direct counterpart", "note": "Same form.",
                   "collection": side, "vanilla": side}]}
    path = root / "pack.json"
    path.write_text(json.dumps(data))
    return path, data


class DevelopmentPackTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.path, self.data = make_pack(self.root / "private" / "development")

    def test_pack_relocates_with_assets_and_preserves_source_files(self):
        import shutil
        snapshots = {p.relative_to(self.path.parent): p.read_bytes() for p in self.path.parent.iterdir()}
        pack = load_development_pack(self.path)
        self.assertEqual(pack.actor["root"], self.path.parent)
        relocated = self.root / "relocated"
        shutil.copytree(self.path.parent, relocated)
        self.assertEqual(load_development_pack(relocated / "pack.json").data, pack.data)
        self.assertEqual(snapshots, {p.relative_to(self.path.parent): p.read_bytes() for p in self.path.parent.iterdir()})

    def test_rejects_external_paths_symlinks_and_dimension_mismatches(self):
        image = self.root / "external.png"
        Image.new("RGBA", (16, 32)).save(image)
        (self.path.parent / "linked.png").symlink_to(image)
        for reference in ("../../external.png", str(image), "linked.png", "C:/external.png"):
            with self.subTest(reference=reference):
                self.data["items"][0]["collection"]["views"][0]["image"] = reference
                self.path.write_text(json.dumps(self.data))
                with self.assertRaises(CataloguePackError):
                    load_development_pack(self.path)
        self.data["items"][0]["collection"]["views"][0].update(image="piece.png", width=99)
        self.path.write_text(json.dumps(self.data))
        with self.assertRaisesRegex(CataloguePackError, "dimensions"):
            load_development_pack(self.path)

    def test_missing_actor_direction_cannot_masquerade_as_a_complete_preview(self):
        actor = self.path.parent / "farmer.json"
        data = json.loads(actor.read_text())
        del data["frames"]["north"]
        actor.write_text(json.dumps(data))
        with self.assertRaisesRegex(CataloguePackError, "four walking directions"):
            load_development_pack(self.path)

    def test_discovery_ignores_linked_folders_and_unrelated_manifests(self):
        (self.root / "alias").symlink_to(self.path.parent, target_is_directory=True)
        junk = self.root / "junk"
        junk.mkdir()
        (junk / "pack.json").write_text('{"format":"other"}')
        self.assertEqual(discover_development_packs([self.root, self.path.parent]),
                         [(self.path, "A private collection")])

    def test_lighting_states_and_animation_assets_relocate_with_the_pack(self):
        view = self.data["items"][0]["collection"]["views"][0]
        view["states"] = {name: {"image": "piece.png", "animation_frames": [
            {"image": "piece.png", "duration_ms": 100}, {"image": "farmer.png", "duration_ms": 150}]}
            for name in ("day_off", "day_on", "night_off", "night_on")}
        view["lights"] = [{"offset": [8, -8], "radius": 40, "intensity": .8, "color": "#ffdd99",
                           "when": "always", "requires_power": True, "mask": "piece.png"}]
        self.path.write_text(json.dumps(self.data))
        before = self.path.read_bytes()
        self.assertEqual(load_development_pack(self.path).data, self.data)
        self.assertEqual(self.path.read_bytes(), before)

    def test_rejects_missing_or_external_effects_and_invalid_light_geometry(self):
        original = deepcopy(self.data)
        mutations = [
            {"states": {"night_on": {"image": "../external.png"}}},
            {"states": {"unknown": {"image": "piece.png"}}},
            {"states": {"day_on": {"image": "piece.png", "animation_frames": [{"image": "piece.png", "duration_ms": 0}]}}},
            {"animation_frames": [{"image": "room.png", "duration_ms": 100}]},
            {"lights": [{"offset": [float('nan'), 0], "radius": 40, "intensity": 1,
                         "color": "#ffffff", "when": "always", "requires_power": True}]},
            {"lights": [{"offset": [0, 0], "radius": -1, "intensity": 1,
                         "color": "#ffffff", "when": "always", "requires_power": True}]},
            {"lights": [{"offset": [0, 0], "radius": 40, "intensity": 1,
                         "color": "#ffffff", "when": "always", "requires_power": True, "mask": "missing.png"}]},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.data = deepcopy(original)
                self.data["items"][0]["collection"]["views"][0].update(mutation)
                self.path.write_text(json.dumps(self.data))
                with self.assertRaises(CataloguePackError):
                    load_development_pack(self.path)


if __name__ == "__main__":
    unittest.main()

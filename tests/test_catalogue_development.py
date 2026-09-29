"""Portable previews stay local and cannot change a room or game content."""
import json
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

import pixelheart_core.catalogue_development as catalogue
from pixelheart_core.catalogue_development import (
    CataloguePackError, delete_development_item, discover_development_packs, load_development_pack,
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
        self.deletions = self.path.with_name(f".{self.path.name}.deletions.json")

    def add_piece(self, identity="Example.Second"):
        item = deepcopy(self.data["items"][0])
        item.update(id=identity, slug=identity.lower(), name=identity)
        self.data["items"].append(item)
        return item

    def test_delete_persists_one_row_and_keeps_other_metadata_and_assets(self):
        survivor = self.add_piece()
        self.data.update(groups=["Study", "Empty group"], private_notes={"related_id": "Example.Globe", "keep": True})
        self.path.write_text(json.dumps(self.data))
        self.path.chmod(0o640)
        originals = {p: p.read_bytes() for p in self.path.parent.iterdir()}
        result = delete_development_item(self.path, "Example.Globe")
        expected = {**self.data, "items": [survivor]}
        self.assertEqual(result.path, self.path)
        self.assertEqual(result.data, expected)
        self.assertEqual(load_development_pack(self.path).data, expected)
        self.assertEqual(result.actor["root"], self.path.parent)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        self.assertEqual({p: p.read_bytes() for p in originals}, originals)
        self.assertEqual(set(self.path.parent.iterdir()), {*originals, self.deletions})
        self.assertEqual(json.loads(self.deletions.read_text())["deleted_item_ids"], ["Example.Globe"])

    def test_delete_last_piece_keeps_an_empty_discoverable_catalogue(self):
        originals = {p: p.read_bytes() for p in self.path.parent.iterdir()}
        result = delete_development_item(self.path, "Example.Globe")
        self.assertEqual(result.data["items"], [])
        self.assertEqual(load_development_pack(self.path).data["items"], [])
        self.assertEqual(discover_development_packs([self.root]), [(self.path, self.data["title"])])
        self.assertEqual({p: p.read_bytes() for p in originals}, originals)

    def test_empty_source_catalogue_is_valid(self):
        self.data["items"] = []
        self.path.write_text(json.dumps(self.data))
        self.assertEqual(load_development_pack(self.path).data, self.data)

    def test_deletions_survive_regeneration_and_preserve_unknown_ids(self):
        self.deletions.write_text(json.dumps({"format": "pixelheart-catalogue-deletions", "version": 1,
            "deleted_item_ids": ["Example.Previous"], "notes": "Retain local metadata"}))
        self.deletions.chmod(0o640)
        delete_development_item(self.path, "Example.Globe")
        state = self.deletions.read_bytes()
        self.assertEqual(json.loads(state)["deleted_item_ids"], ["Example.Previous", "Example.Globe"])
        self.assertEqual(json.loads(state)["notes"], "Retain local metadata")
        self.assertEqual(self.deletions.stat().st_mode & 0o777, 0o640)
        survivor = self.add_piece()
        self.data["title"] = "Regenerated collection"
        self.path.write_text(json.dumps(self.data))
        original = self.path.read_bytes()
        self.assertEqual(load_development_pack(self.path).data, {**self.data, "items": [survivor]})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(self.deletions.read_bytes(), state)

    def test_delete_unknown_id_does_not_write_any_files(self):
        before = {p.name: p.read_bytes() for p in self.path.parent.iterdir()}
        with patch("pixelheart_core.catalogue_development.os.replace") as replace:
            with self.assertRaisesRegex(CataloguePackError, "no longer"):
                delete_development_item(self.path, "Example.Missing")
        replace.assert_not_called()
        self.assertEqual({p.name: p.read_bytes() for p in self.path.parent.iterdir()}, before)

    def test_delete_write_or_replace_failure_preserves_original_and_cleans_temp(self):
        self.deletions.write_text(json.dumps({"format": "pixelheart-catalogue-deletions", "version": 1,
            "deleted_item_ids": ["Example.Previous"]}))
        before = {p.name: p.read_bytes() for p in self.path.parent.iterdir()}
        for operation in ("fsync", "replace"):
            with self.subTest(operation=operation):
                with patch("pixelheart_core.catalogue_development.os." + operation, side_effect=OSError("disk unavailable")):
                    with self.assertRaisesRegex(CataloguePackError, "Cannot save"):
                        delete_development_item(self.path, "Example.Globe")
                self.assertEqual({p.name: p.read_bytes() for p in self.path.parent.iterdir()}, before)

    def test_delete_reloads_edits_made_since_the_ui_loaded_the_pack(self):
        stale = load_development_pack(self.path)
        survivor = self.add_piece()
        self.data.update(title="Renamed elsewhere", unrelated={"new": "preserve this"})
        self.path.write_text(json.dumps(self.data))
        original = self.path.read_bytes()
        result = delete_development_item(stale.path, "Example.Globe")
        self.assertEqual(result.data, {**self.data, "items": [survivor]})
        self.assertEqual(stale.data["items"][0]["id"], "Example.Globe")
        self.assertEqual(self.path.read_bytes(), original)

    def test_delete_retries_edits_arriving_during_candidate_validation(self):
        self.add_piece()
        self.path.write_text(json.dumps(self.data))
        latest = deepcopy(self.data)
        latest.update(title="Concurrent title", notes="Keep this newer metadata")
        latest["items"][1]["description"] = "Updated by another writer"
        edited = False
        validator = catalogue._deletion_state

        def concurrent_edit(path):
            nonlocal edited
            if Path(path).suffix == ".tmp" and not edited:
                edited = True
                self.path.write_text(json.dumps(latest))
            return validator(path)

        with patch("pixelheart_core.catalogue_development._deletion_state", side_effect=concurrent_edit):
            result = delete_development_item(self.path, "Example.Globe")
        self.assertTrue(edited)
        self.assertEqual(result.data, {**latest, "items": [latest["items"][1]]})
        self.assertEqual(load_development_pack(self.path).data, result.data)
        self.assertEqual(json.loads(self.path.read_text()), latest)
        self.assertFalse(list(self.path.parent.glob(".*.tmp")))

    def test_delete_retries_concurrent_deletion_without_losing_it(self):
        self.add_piece()
        self.path.write_text(json.dumps(self.data))
        original = self.path.read_bytes()
        edited = False
        validator = catalogue._deletion_state

        def concurrent_delete(path):
            nonlocal edited
            if Path(path).suffix == ".tmp" and not edited:
                edited = True
                self.deletions.write_text(json.dumps({"format": "pixelheart-catalogue-deletions", "version": 1,
                    "deleted_item_ids": ["Example.Second"]}))
            return validator(path)

        with patch("pixelheart_core.catalogue_development._deletion_state", side_effect=concurrent_delete):
            result = delete_development_item(self.path, "Example.Globe")
        self.assertTrue(edited)
        self.assertEqual(result.data["items"], [])
        self.assertEqual(load_development_pack(self.path).data["items"], [])
        self.assertEqual(json.loads(self.deletions.read_text())["deleted_item_ids"], ["Example.Second", "Example.Globe"])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(list(self.path.parent.glob(".*.tmp")))

    def test_invalid_deletion_state_is_rejected_without_overwriting_it(self):
        valid = {"format": "pixelheart-catalogue-deletions", "version": 1, "deleted_item_ids": []}
        for invalid in ({**valid, "format": "other"}, {**valid, "deleted_item_ids": "Example.Globe"},
                        {**valid, "deleted_item_ids": ["Example.Globe"] * 2},
                        {**valid, "deleted_item_ids": ["x" * 257]},
                        {**valid, "deleted_item_ids": [str(n) for n in range(4097)]}):
            with self.subTest(invalid=invalid["format"], count=len(invalid["deleted_item_ids"])):
                self.deletions.write_text(json.dumps(invalid))
                originals = {p: p.read_bytes() for p in self.path.parent.iterdir()}
                with self.assertRaises(CataloguePackError):
                    load_development_pack(self.path)
                with self.assertRaises(CataloguePackError):
                    delete_development_item(self.path, "Example.Globe")
                self.assertEqual({p: p.read_bytes() for p in self.path.parent.iterdir()}, originals)

    def test_only_wall_and_floor_patterns_may_omit_vanilla(self):
        item=self.data["items"][0]
        item["vanilla"]=None
        for kind in ("wall","floor"):
            item["collection"]["kind"]=kind
            self.path.write_text(json.dumps(self.data))
            self.assertIsNone(load_development_pack(self.path).data["items"][0]["vanilla"])
        item["collection"]["kind"]="decor"
        self.path.write_text(json.dumps(self.data))
        with self.assertRaisesRegex(CataloguePackError,"Furniture needs"):
            load_development_pack(self.path)

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

    def test_optional_window_pane_light_preserves_local_pack_and_legacy_views(self):
        # Legacy views remain valid without a lighting profile.
        self.assertEqual(load_development_pack(self.path).data, self.data)
        side = self.data["items"][0]["collection"]
        side["kind"] = "window"
        side["views"][0]["window_light"] = {
            "pane": [3, 6, 10, 20], "color": "#dceeff", "intensity": .22}
        self.path.write_text(json.dumps(self.data))
        before = self.path.read_bytes()
        self.assertEqual(load_development_pack(self.path).data, self.data)
        self.assertEqual(self.path.read_bytes(), before)

    def test_optional_night_beam_opacity_preserves_profile_and_rejects_invalid_values(self):
        side = self.data["items"][0]["collection"]
        side["kind"] = "window"
        profile = {"pane": [3, 6, 10, 20], "color": "#dceeff", "intensity": .22}
        side["views"][0]["window_light"] = profile
        for opacity in (None, 0, .5, 1):
            with self.subTest(opacity=opacity):
                profile.pop("night_opacity", None)
                if opacity is not None:
                    profile["night_opacity"] = opacity
                self.path.write_text(json.dumps(self.data))
                before = self.path.read_bytes()
                self.assertEqual(load_development_pack(self.path).data, self.data)
                self.assertEqual(self.path.read_bytes(), before)
        for opacity in (-.01, 1.01, float("nan"), float("inf"), True, False, ".5", None):
            with self.subTest(invalid_opacity=opacity):
                profile["night_opacity"] = opacity
                self.path.write_text(json.dumps(self.data))
                with self.assertRaises(CataloguePackError):
                    load_development_pack(self.path)

    def test_window_pane_light_rejects_invalid_bounds_color_and_intensity(self):
        original = deepcopy(self.data)
        valid = {"pane": [3, 6, 10, 20], "color": "#dceeff", "intensity": .22}
        invalid = [None, [], {},
                   {**valid, "pane": [-1, 0, 8, 8]},
                   {**valid, "pane": [0, 0, 0, 8]},
                   {**valid, "pane": [0, 0, 8, -1]},
                   {**valid, "pane": [0, 0, 17, 8]},
                   {**valid, "pane": [0, 30, 8, 3]},
                   {**valid, "pane": [0, 0, 8]},
                   {**valid, "pane": [True, 0, 8, 8]},
                   {**valid, "pane": [0, 0, 8.5, 8]},
                   {**valid, "color": "not-a-color"},
                   {**valid, "color": None},
                   {**valid, "intensity": -0.01},
                   {**valid, "intensity": 1.01},
                   {**valid, "intensity": float("nan")},
                   {**valid, "intensity": True}]
        for profile in invalid:
            with self.subTest(profile=profile):
                self.data = deepcopy(original)
                side = self.data["items"][0]["collection"]
                side["kind"] = "window"
                side["views"][0]["window_light"] = profile
                self.path.write_text(json.dumps(self.data))
                with self.assertRaises(CataloguePackError):
                    load_development_pack(self.path)
        self.data = deepcopy(original)
        self.data["items"][0]["collection"]["views"][0]["window_light"] = valid
        self.path.write_text(json.dumps(self.data))
        with self.assertRaises(CataloguePackError):
            load_development_pack(self.path)

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

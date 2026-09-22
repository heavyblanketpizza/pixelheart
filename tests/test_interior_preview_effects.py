"""Portable observed furniture states, animation timing, and light metadata."""

import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from pixelheart_core.interior_furniture import (
    MAX_FRAMES, MAX_PREVIEW_LIGHTS, FurnitureValidationError, attach_texture,
    clear_preview_cache, definition_assets, frame_at, import_catalog_textures,
    import_furniture_library, preview_frame, preview_frame_offset, validate_definition,
)


def frame(x=0, y=0, rotation=0, duration_ms=100):
    return {"rotation": rotation, "rect": [x, y, 16, 16], "duration_ms": duration_ms}


def light(**changes):
    result = {"rotation": 0, "offset": [0.5, -0.25], "radius": 3,
              "color": "#fFdD80", "intensity": 0.7, "when": "night",
              "mask_rect": [16, 16, 16, 16]}
    result.update(changes)
    return result


def definition(**changes):
    result = {"id": "Example.Lamp", "name": "Lamp", "kind": "lamp",
              "footprint": [1, 1], "sprite_size": [1, 1], "rotations": 2,
              "texture": "TileSheets/lamp", "preview_asset": "atlas.png",
              "frames": [frame(), frame(rotation=1)],
              "preview_variants": {
                  "day_on": [frame(16), frame(0, 16, duration_ms=200)],
                  "day_off": [frame(0, 16)],
                  "night_on": [frame(16, 16)],
                  "night_off": [frame()],
              },
              "preview_lights": [light()]}
    result.update(changes)
    return result


class InteriorPreviewEffectsTests(unittest.TestCase):
    def setUp(self):
        clear_preview_cache()
        self.addCleanup(clear_preview_cache)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.atlas = self.root / "atlas.png"
        with Image.new("RGBA", (32, 32), "red") as image:
            image.paste("green", (16, 0, 32, 16))
            image.paste("blue", (0, 16, 16, 32))
            image.paste("yellow", (16, 16, 32, 32))
            image.save(self.atlas)

    def library(self, entries):
        path = self.root / "library.json"
        path.write_text(json.dumps({"format": "pixelheart-interior-library", "version": 1,
                                    "definitions": entries}), encoding="utf-8")
        return path

    def test_old_definitions_keep_their_normalized_shape_and_frames(self):
        original = {"id": "Old", "frames": [frame()]}
        result = validate_definition(original)
        self.assertNotIn("preview_variants", result)
        self.assertNotIn("preview_lights", result)
        for phase in ("day", "evening", "night"):
            for enabled in (False, True):
                self.assertEqual(frame_at(result, time_of_day=phase, lights_on=enabled), frame())

    def test_normalization_round_trips_detached_effect_metadata(self):
        original = definition()
        result = validate_definition(original)
        self.assertEqual(result, validate_definition(json.loads(json.dumps(result))))
        result["preview_variants"]["night_on"][0]["rect"][0] = 999
        result["preview_lights"][0]["offset"][0] = 99
        result["preview_lights"][0]["mask_rect"][0] = 99
        self.assertEqual(original, definition())

    def test_all_environment_states_choose_explicit_pixels(self):
        for phase, enabled, expected in (("day", True, (0, 128, 0, 255)),
                                         ("day", False, (0, 0, 255, 255)),
                                         ("night", True, (255, 255, 0, 255)),
                                         ("evening", True, (255, 255, 0, 255)),
                                         ("night", False, (255, 0, 0, 255))):
            with self.subTest(phase=phase, enabled=enabled):
                with preview_frame(definition(), self.root, time_of_day=phase, lights_on=enabled) as image:
                    self.assertEqual(image.getpixel((8, 8)), expected)

    def test_animation_timing_is_half_open_and_deterministic_inside_state(self):
        for elapsed, position in ((0, [16, 0]), (99, [16, 0]), (100, [0, 16]),
                                  (299, [0, 16]), (300, [16, 0]), (400, [0, 16])):
            with self.subTest(elapsed=elapsed):
                result = frame_at(definition(), elapsed_ms=elapsed)
                self.assertEqual(result["rect"][:2], position)
                self.assertEqual(result, frame_at(definition(), elapsed_ms=elapsed))
        self.assertEqual(frame_at(definition(), elapsed_ms=100, lights_on=False)["rect"][:2], [0, 16])

    def test_missing_state_or_rotation_falls_back_to_base_only(self):
        value = definition(preview_variants={"day_on": [frame(16)]})
        self.assertEqual(frame_at(value, time_of_day="night"), frame())
        self.assertEqual(frame_at(value, rotation=1), frame(rotation=1))
        self.assertEqual(frame_at(value, lights_on=False), frame())
        with self.assertRaises(FurnitureValidationError):
            frame_at(definition(frames=[]), rotation=1)

    def test_environment_arguments_are_explicit_and_bounded(self):
        for phase in (None, True, 42, [], "dawn"):
            with self.subTest(phase=phase), self.assertRaises(FurnitureValidationError):
                frame_at(definition(), time_of_day=phase)
        for enabled in (None, 0, 1, "on", []):
            with self.subTest(enabled=enabled), self.assertRaises(FurnitureValidationError):
                frame_at(definition(), lights_on=enabled)
        for elapsed in (-1, True, float("nan"), 2**63):
            with self.subTest(elapsed=elapsed), self.assertRaises(FurnitureValidationError):
                frame_at(definition(), elapsed_ms=elapsed)

    def test_variant_shapes_and_aggregate_frame_budget_are_validated(self):
        invalid = [None, [], {"dusk": []}, {"day_on": {}},
                   {"day_on": [None]}, {"day_on": [frame(rotation=2)]},
                   {"day_on": [frame(duration_ms=0)]},
                   {"day_on": [dict(frame(), rect=[0, 0, True, 16])]},
                   {"day_on": [frame()] * MAX_FRAMES}]
        for variants in invalid:
            with self.subTest(variants=variants), self.assertRaises(FurnitureValidationError):
                validate_definition(definition(preview_variants=variants))
        exact = definition(frames=[frame()], preview_variants={"day_on": [frame()] * (MAX_FRAMES-1)})
        self.assertEqual(len(validate_definition(exact)["preview_variants"]["day_on"]), MAX_FRAMES-1)

    def test_light_numbers_reject_booleans_nonfinite_values_and_excesses(self):
        invalid = []
        for field in ("radius", "intensity"):
            for value in (True, "1", None, float("nan"), float("inf"), -float("inf"), 10**1000):
                invalid.append(light(**{field: value}))
        for value in (True, float("nan"), 129, -129, 10**1000):
            invalid.append(light(offset=[value, 0]))
        invalid += [light(radius=0), light(radius=129), light(intensity=-.01), light(intensity=1.01),
                    light(rotation=True), light(rotation=2), light(offset=[0]),
                    light(color="#abcd"), light(color="red"), light(color=None),
                    light(when="evening"), light(mask_rect=[0, 0, 0, 16]),
                    light(mask_rect=[0, 0, 16, float("inf")])]
        for entry in invalid:
            with self.subTest(entry=entry), self.assertRaises(FurnitureValidationError):
                validate_definition(definition(preview_lights=[entry]))
        for entries in (None, {}, [None], [light()] * (MAX_PREVIEW_LIGHTS + 1)):
            with self.subTest(entries=entries), self.assertRaises(FurnitureValidationError):
                validate_definition(definition(preview_lights=entries))

    def test_light_bounds_allow_edges_and_optional_masks(self):
        entry = light(offset=[-128, 128], radius=.001, intensity=0, when="always")
        del entry["mask_rect"]
        value = validate_definition(definition(preview_lights=[entry]))
        self.assertEqual(value["preview_lights"], [entry])

    def test_light_mask_channel_is_optional_explicit_and_portable(self):
        self.assertNotIn("mask_channel", validate_definition(definition())["preview_lights"][0])
        for channel in ("alpha", "luminance"):
            value = definition(preview_lights=[light(mask_channel=channel)])
            path = self.library([value])
            result = import_furniture_library(path, self.project)["definitions"][0]
            self.assertEqual(result["preview_lights"][0]["mask_channel"], channel)
            self.assertEqual(validate_definition(result), result)
        for channel in (None, True, 1, [], {}, "red", "Alpha"):
            with self.subTest(channel=channel), self.assertRaises(FurnitureValidationError):
                validate_definition(definition(preview_lights=[light(mask_channel=channel)]))

    def test_light_blend_is_portable_and_overlay_requires_actual_art(self):
        self.assertNotIn("blend", validate_definition(definition())["preview_lights"][0])
        for blend in ("illuminate", "overlay"):
            value = definition(preview_lights=[light(blend=blend, mask_channel="alpha")])
            result = import_furniture_library(self.library([value]), self.project)["definitions"][0]
            self.assertEqual(result["preview_lights"], value["preview_lights"])
            self.assertEqual(validate_definition(result), result)
        for blend in (None, True, 1, [], {}, "screen", "Overlay"):
            with self.subTest(blend=blend), self.assertRaises(FurnitureValidationError):
                validate_definition(definition(preview_lights=[light(blend=blend)]))
        missing_art = light(blend="overlay")
        del missing_art["mask_rect"]
        with self.assertRaises(FurnitureValidationError):
            validate_definition(definition(preview_lights=[missing_art]))

    def test_library_import_preserves_states_masks_and_one_portable_asset(self):
        original = definition()
        path = self.library([original])
        original_json = path.read_bytes()
        result = import_furniture_library(path, self.project)["definitions"][0]
        self.assertEqual(result["preview_variants"], original["preview_variants"])
        self.assertEqual(result["preview_lights"], original["preview_lights"])
        self.assertEqual(definition_assets(result), [result["preview_asset"]])
        self.assertEqual((self.project / result["preview_asset"]).read_bytes(), self.atlas.read_bytes())
        self.assertEqual(path.read_bytes(), original_json)
        self.atlas.unlink()
        with preview_frame(result, self.project, time_of_day="night") as image:
            self.assertEqual(image.getpixel((8, 8)), (255, 255, 0, 255))

    def test_every_unused_variant_and_mask_is_preflighted_before_any_copy(self):
        invalid = [definition(id="Bad", preview_variants={"night_off": [frame(32)]}),
                   definition(id="Bad", preview_lights=[light(mask_rect=[17, 16, 16, 16])])]
        for value in invalid:
            path = self.library([definition(id="First"), value])
            with self.subTest(value=value), self.assertRaises(FurnitureValidationError):
                import_furniture_library(path, self.project)
            self.assertEqual(list(self.project.iterdir()), [])
            with self.assertRaises(FurnitureValidationError):
                preview_frame(value, self.root, time_of_day="day")
            with self.assertRaises(FurnitureValidationError):
                attach_texture(value, self.atlas, self.project)
            self.assertEqual(list(self.project.iterdir()), [])

    def test_variant_only_catalogue_attachment_still_checks_bounds(self):
        textures = self.root / "native"
        target = textures / "TileSheets/lamp.png"
        target.parent.mkdir(parents=True)
        target.write_bytes(self.atlas.read_bytes())
        value = definition(frames=[], preview_asset="", preview_variants={"night_on": [frame(32)]})
        result = import_catalog_textures([value], textures, self.project)
        self.assertIn("beyond", result["warnings"][0])
        self.assertEqual(result["definitions"][0]["preview_asset"], "")

    def test_frame_offsets_follow_state_rotation_and_animation_without_legacy_keys(self):
        self.assertNotIn("offset", frame_at(definition()))
        self.assertEqual(preview_frame_offset(definition()), (0, 0))
        value = definition(frames=[dict(frame(), offset=[-1, 2]),
                                   dict(frame(rotation=1), offset=[3, -4])],
                           preview_variants={"night_on": [dict(frame(16), offset=[5, 6]),
                                                          dict(frame(0, 16), offset=[7, 8])]})
        self.assertEqual(preview_frame_offset(value), (-1, 2))
        self.assertEqual(preview_frame_offset(value, rotation=1), (3, -4))
        self.assertEqual(preview_frame_offset(value, time_of_day="night", elapsed_ms=99), (5, 6))
        self.assertEqual(preview_frame_offset(value, time_of_day="night", elapsed_ms=100), (7, 8))
        self.assertEqual(preview_frame_offset(value, time_of_day="night", lights_on=False), (-1, 2))
        normalized = validate_definition(value)
        normalized["frames"][0]["offset"][0] = 99
        self.assertEqual(value["frames"][0]["offset"], [-1, 2])
        resolved = import_furniture_library(self.library([value]), self.project)["definitions"][0]
        self.assertEqual(resolved["frames"], value["frames"])
        self.assertEqual(resolved["preview_variants"], value["preview_variants"])

    def test_frame_offsets_are_bounded_integer_pixel_pairs(self):
        for offset in (None, [], [0], [0, 0, 0], [True, 0], [.5, 0], [float("nan"), 0],
                       [-2049, 0], [0, 2049], "1 2"):
            with self.subTest(offset=offset), self.assertRaises(FurnitureValidationError):
                validate_definition(definition(frames=[dict(frame(), offset=offset)]))
        value = definition(frames=[dict(frame(), offset=[-2048, 2048])], preview_variants={})
        self.assertEqual(preview_frame_offset(value), (-2048, 2048))


if __name__ == "__main__":
    unittest.main()

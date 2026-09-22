"""Room lighting uses portable masks without changing authored room state."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from pixelheart_core.interior_furniture import clear_preview_cache
from pixelheart_core.interior_lighting import apply_preview_lighting
from pixelheart_core.interiors import (
    InteriorDraft, floor_cells, new_interior, normalize_interior, render_interior, wall_cells,
)


def light(**changes):
    value = {"rotation": 0, "offset": [.5, .5], "radius": .5,
             "color": "#FFD080", "intensity": .8, "when": "always",
             "mask_rect": [32, 0, 16, 16]}
    value.update(changes)
    return value


class InteriorLightingTests(unittest.TestCase):
    def setUp(self):
        clear_preview_cache()
        self.addCleanup(clear_preview_cache)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        with Image.new("RGBA", (64, 32)) as image:
            image.paste((200, 60, 30, 255), (0, 0, 16, 16))
            image.paste((40, 90, 180, 255), (16, 0, 32, 16))
            # Opaque black must emit no light; transparent white must not emit.
            image.paste((255, 255, 255, 255), (32, 0, 40, 16))
            image.paste((0, 0, 0, 255), (40, 0, 48, 16))
            image.paste((255, 255, 255, 0), (48, 0, 56, 16))
            image.paste((128, 128, 128, 255), (56, 0, 64, 16))
            image.paste((255, 255, 255, 255), (32, 16, 48, 32))
            # Native game masks can store shape only in alpha, with black RGB.
            image.paste((0, 0, 0, 255), (48, 16, 56, 32))
            image.paste((0, 0, 0, 0), (56, 16, 64, 32))
            image.paste((200, 100, 50, 128), (0, 16, 8, 32))
            image.paste((30, 240, 160, 0), (8, 16, 16, 32))
            image.save(self.root / "effects.png")

    def design(self, *, kind="lamp", x=6, y=7, lights=None, **changes):
        data = new_interior()
        definition = {"id": "(F)Test.Light", "name": "Observed light", "kind": kind,
                      "footprint": [1, 1], "sprite_size": [1, 1], "rotations": 2,
                      "preview_asset": "effects.png",
                      "frames": [{"rotation": 0, "rect": [0, 0, 16, 16], "duration_ms": 100}],
                      "preview_lights": [light()] if lights is None else lights}
        definition.update(changes)
        data["catalog"] = [definition]
        data["furniture"] = [{"id": "placed", "item_id": "(F)Test.Light", "x": x, "y": y, "rotation": 0}]
        return normalize_interior(data)

    def input_image(self, data):
        return Image.new("RGBA", (data["width"] * 16, data["height"] * 16), (128, 128, 128, 97))

    def assert_same(self, left, right):
        self.assertEqual(left.size, right.size)
        self.assertEqual(left.tobytes(), right.tobytes())

    def test_night_tints_room_preserves_margins_alpha_and_input_ownership(self):
        data = self.design(lights=[])
        before = deepcopy(data)
        with self.input_image(data) as image:
            original = image.tobytes()
            with apply_preview_lighting(image, data, self.root, time_of_day="night") as night:
                self.assertLess(night.getpixel((100, 100))[0], image.getpixel((100, 100))[0])
                self.assertEqual(night.getpixel((0, 0)), image.getpixel((0, 0)))
                self.assertEqual(night.getchannel("A").tobytes(), image.getchannel("A").tobytes())
            self.assertEqual(image.tobytes(), original)
        self.assertEqual(data, before)

    def test_mask_uses_luminance_and_light_switch_changes_night_pixels(self):
        data = self.design()
        with self.input_image(data) as image:
            with apply_preview_lighting(image, data, self.root, time_of_day="night", lights_on=False) as off:
                with apply_preview_lighting(image, data, self.root, time_of_day="night") as on:
                    self.assertGreater(on.getpixel((99, 118))[0], off.getpixel((99, 118))[0])
                    self.assertEqual(on.getpixel((108, 118)), off.getpixel((108, 118)))
                    self.assertEqual(on.getpixel((99, 118))[3], 97)

    def test_transparent_mask_pixels_cannot_emit_light(self):
        data = self.design(lights=[light(mask_rect=[48, 0, 16, 16])])
        with self.input_image(data) as image:
            with apply_preview_lighting(image, data, self.root, lights_on=False) as off:
                with apply_preview_lighting(image, data, self.root) as on:
                    self.assertEqual(on.getpixel((99, 118)), off.getpixel((99, 118)))
                    self.assertGreater(on.getpixel((108, 118))[0], off.getpixel((108, 118))[0])

    def test_alpha_channel_mask_emits_from_opaque_black_and_not_transparency(self):
        data = self.design(lights=[light(mask_rect=[48, 16, 16, 16], mask_channel="alpha")])
        with self.input_image(data) as image:
            with apply_preview_lighting(image, data, self.root, lights_on=False) as off:
                with apply_preview_lighting(image, data, self.root) as on:
                    self.assertGreater(on.getpixel((99, 118))[0], off.getpixel((99, 118))[0])
                    self.assertEqual(on.getpixel((108, 118)), off.getpixel((108, 118)))
            data["catalog"][0]["preview_lights"][0]["mask_channel"] = "luminance"
            with apply_preview_lighting(image, data, self.root) as luminance:
                self.assert_same(luminance, image)

    def test_large_mask_clips_to_room_cells_and_canvas_edges(self):
        data = self.design(x=2, y=5, lights=[light(radius=8, mask_rect=[32, 16, 16, 16])])
        room = floor_cells(data) | wall_cells(data)
        with self.input_image(data) as image:
            with apply_preview_lighting(image, data, self.root, time_of_day="night", lights_on=False) as off:
                with apply_preview_lighting(image, data, self.root, time_of_day="night") as on:
                    self.assertGreater(on.getpixel((40, 88))[0], off.getpixel((40, 88))[0])
                    for y in range(data["height"]):
                        for x in range(data["width"]):
                            if (x, y) not in room:
                                bounds = (x * 16, y * 16, x * 16 + 16, y * 16 + 16)
                                with on.crop(bounds) as on_tile, off.crop(bounds) as off_tile:
                                    self.assert_same(on_tile, off_tile)

    def test_fully_off_canvas_masks_do_not_change_any_pixels(self):
        for offset in ([-128, -128], [128, 128]):
            data = self.design(lights=[light(offset=offset)])
            with self.subTest(offset=offset), self.input_image(data) as image:
                with apply_preview_lighting(image, data, self.root, lights_on=False) as off:
                    with patch("pixelheart_core.interior_lighting._preview_atlas", side_effect=AssertionError("Off-canvas mask loaded")):
                        with apply_preview_lighting(image, data, self.root) as on:
                            self.assert_same(on, off)

    def test_large_light_resizes_only_visible_canvas_region(self):
        data = self.design(lights=[light(radius=128)])
        dimensions = []
        original_resize = Image.Image.resize

        def recording_resize(image, size, *args, **kwargs):
            dimensions.append(size)
            return original_resize(image, size, *args, **kwargs)

        with self.input_image(data) as image:
            with patch.object(Image.Image, "resize", recording_resize):
                with apply_preview_lighting(image, data, self.root) as result:
                    self.assertEqual(result.size, image.size)
            self.assertTrue(dimensions)
            self.assertTrue(all(width <= image.width and height <= image.height for width, height in dimensions))

    def test_window_daylight_remains_with_artificial_lights_off(self):
        data = self.design(kind="window", x=6, y=2,
                           lights=[light(when="day", radius=2, offset=[.5, 2], mask_rect=[32, 16, 16, 16])])
        with self.input_image(data) as image:
            with apply_preview_lighting(image, data, self.root, lights_on=False) as off:
                with apply_preview_lighting(image, data, self.root) as on:
                    self.assert_same(on, off)
                    self.assertGreater(on.getpixel((104, 64))[0], image.getpixel((104, 64))[0])
            without_lights = deepcopy(data)
            without_lights["catalog"][0]["preview_lights"] = []
            with apply_preview_lighting(image, data, self.root, time_of_day="night") as night:
                with apply_preview_lighting(image, without_lights, self.root, time_of_day="night") as ambient:
                    self.assert_same(night, ambient)

    def test_phase_rotation_and_zero_intensity_suppress_nonapplicable_lights(self):
        cases = [(light(when="night"), "day"), (light(when="day"), "night"),
                 (light(rotation=1), "day"), (light(intensity=0), "day")]
        for entry, phase in cases:
            data = self.design(lights=[entry])
            with self.subTest(entry=entry, phase=phase), self.input_image(data) as image:
                with apply_preview_lighting(image, data, self.root, time_of_day=phase) as on:
                    with apply_preview_lighting(image, data, self.root, time_of_day=phase, lights_on=False) as off:
                        self.assert_same(on, off)

    def test_radial_fallback_brightens_center_and_preserves_outside(self):
        entry = light(radius=1)
        del entry["mask_rect"]
        data = self.design(lights=[entry])
        with self.input_image(data) as image:
            with apply_preview_lighting(image, data, self.root) as lit:
                center = lit.getpixel((104, 120))[0]
                edge = lit.getpixel((89, 120))[0]
                self.assertGreater(center, edge)
                self.assertEqual(lit.getpixel((0, 0)), image.getpixel((0, 0)))

    def test_rendered_variants_animate_and_paused_time_is_deterministic_without_edits(self):
        data = self.design(lights=[], preview_variants={
            "day_on": [{"rotation": 0, "rect": [0, 0, 16, 16], "duration_ms": 100},
                       {"rotation": 0, "rect": [16, 0, 16, 16], "duration_ms": 100}],
            "night_on": [{"rotation": 0, "rect": [16, 0, 16, 16], "duration_ms": 100}],
        })
        draft = InteriorDraft(data)
        before = draft.snapshot()
        with render_interior(draft.data, self.root, elapsed_ms=0) as first:
            with render_interior(draft.data, self.root, elapsed_ms=100) as second:
                self.assertNotEqual(first.getpixel((104, 120)), second.getpixel((104, 120)))
            with render_interior(draft.data, self.root, elapsed_ms=0) as paused:
                self.assert_same(first, paused)
            with render_interior(draft.data, self.root, elapsed_ms=0, time_of_day="night") as night:
                self.assertNotEqual(first.getpixel((104, 120)), night.getpixel((104, 120)))
                self.assertGreater(night.getpixel((104, 120))[2], night.getpixel((104, 120))[0])
        self.assertEqual(draft.snapshot(), before)
        self.assertFalse(draft._undo)

    def test_unavailable_mask_asset_does_not_break_room_preview(self):
        data = self.design(preview_asset="missing.png")
        with self.input_image(data) as image:
            with apply_preview_lighting(image, data, self.root) as lit:
                self.assert_same(lit, image)
        with render_interior(data, self.root) as result:
            self.assertEqual(result.size, (data["width"] * 16, data["height"] * 16))

    def test_changed_atlas_cannot_emit_from_an_out_of_bounds_mask(self):
        data = self.design()
        with Image.new("RGBA", (8, 8), "white") as replacement:
            replacement.save(self.root / "effects.png")
        with self.input_image(data) as image:
            with apply_preview_lighting(image, data, self.root) as result:
                self.assert_same(result, image)

    def test_render_offsets_align_each_animation_frame_to_footprint(self):
        data = self.design(lights=[], frames=[
            {"rotation": 0, "rect": [0, 0, 16, 16], "duration_ms": 100, "offset": [-1, 2]},
            {"rotation": 0, "rect": [16, 0, 16, 16], "duration_ms": 100, "offset": [3, -2]},
        ])
        with render_interior(data, self.root, elapsed_ms=0) as first:
            self.assertEqual(first.getpixel((95, 114)), (200, 60, 30, 255))
            self.assertEqual(first.getpixel((110, 129)), (200, 60, 30, 255))
            self.assertNotEqual(first.getpixel((94, 114)), (200, 60, 30, 255))
            self.assertNotEqual(first.getpixel((111, 129)), (200, 60, 30, 255))
        with render_interior(data, self.root, elapsed_ms=100) as second:
            self.assertEqual(second.getpixel((99, 110)), (40, 90, 180, 255))
            self.assertEqual(second.getpixel((114, 125)), (40, 90, 180, 255))
            self.assertNotEqual(second.getpixel((98, 110)), (40, 90, 180, 255))
            self.assertNotEqual(second.getpixel((115, 125)), (40, 90, 180, 255))

    def test_overlapping_lights_preserve_artwork_instead_of_whitening_it(self):
        data = self.design(lights=[light(mask_rect=[32, 16, 16, 16], color="#FFFFFF", intensity=1)] * 16)
        samples = {(99, 120): (4, 8, 12, 97), (103, 120): (160, 80, 40, 97),
                   (107, 120): (0, 0, 0, 97)}
        with self.input_image(data) as image:
            for point, color in samples.items():
                image.putpixel(point, color)
            for phase in ("day", "night"):
                with self.subTest(phase=phase):
                    with apply_preview_lighting(image, data, self.root, time_of_day=phase) as lit:
                        for point, original in samples.items():
                            actual = lit.getpixel(point)
                            for source, rendered in zip(original[:3], actual[:3]):
                                self.assertLessEqual(rendered, source + (255-source) * .1 + 1)
                            self.assertEqual(actual[3], original[3])
                        red, green, blue, _ = lit.getpixel((103, 120))
                        self.assertGreater(red, green)
                        self.assertGreater(green, blue)

    def test_rgba_overlay_preserves_source_color_alpha_and_transparency(self):
        data = self.design(lights=[light(mask_rect=[0, 16, 16, 16], color="#FFFFFF",
                                         intensity=1, blend="overlay", mask_channel="luminance")])
        with self.input_image(data) as image:
            with apply_preview_lighting(image, data, self.root) as result:
                self.assertEqual(result.getpixel((99, 118)), (164, 114, 89, 97))
                self.assertEqual(result.getpixel((108, 118)), image.getpixel((108, 118)))
            data["catalog"][0]["preview_lights"][0]["mask_channel"] = "alpha"
            with apply_preview_lighting(image, data, self.root) as alpha_mode:
                self.assertEqual(alpha_mode.getpixel((99, 118)), (164, 114, 89, 97))

    def test_overlay_applies_tint_intensity_and_daylight_window_light_switch_rule(self):
        data = self.design(kind="window", x=6, y=2,
                           lights=[light(mask_rect=[0, 16, 16, 16], color="#FF8000",
                                         intensity=.5, blend="overlay", when="day")])
        with self.input_image(data) as image:
            with apply_preview_lighting(image, data, self.root, lights_on=False) as result:
                # RGB tint produces (200, 50, 0); source alpha128 × .5 gives64.
                self.assertEqual(result.getpixel((99, 38)), (146, 108, 96, 97))
                self.assertEqual(result.getpixel((108, 38)), image.getpixel((108, 38)))
            with apply_preview_lighting(image, data, self.root, time_of_day="night") as night:
                without_rays = deepcopy(data)
                without_rays["catalog"][0]["preview_lights"] = []
                with apply_preview_lighting(image, without_rays, self.root, time_of_day="night") as ambient:
                    self.assert_same(night, ambient)

    def test_overlay_cannot_paint_into_empty_space_outside_room(self):
        data = self.design(x=2, y=5, lights=[light(mask_rect=[0, 16, 16, 16], radius=8,
                                               color="#FFFFFF", intensity=1, blend="overlay")])
        room = floor_cells(data) | wall_cells(data)
        with self.input_image(data) as image:
            with apply_preview_lighting(image, data, self.root) as result:
                self.assertNotEqual(result.getpixel((40, 88)), image.getpixel((40, 88)))
                for x, y in ((0, 0), (1, 5), (12, 5), (8, 14)):
                    self.assertNotIn((x, y), room)
                    self.assertEqual(result.getpixel((x * 16 + 8, y * 16 + 8)),
                                     image.getpixel((x * 16 + 8, y * 16 + 8)))


if __name__ == "__main__":
    unittest.main()

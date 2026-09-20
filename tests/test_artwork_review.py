import base64
from dataclasses import FrozenInstanceError
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, PngImagePlugin

from pixelheart_core.artwork import ArtworkValidationError
from pixelheart_core.artwork_review import (
    MAX_REVIEW_FRAMES,
    ReviewSheet,
    frame_title,
    load_review_sheet,
    render_artwork_review_html,
)


class ReportParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class ArtworkReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def png(self, name="sheet.png", size=(64, 416), color=(30, 90, 160, 255), **save_options):
        path = self.directory / name
        with Image.new("RGBA", size, color) as image:
            image.save(path, **save_options)
        return path

    def sheet(self, kind="sprite", name="sheet.png", size=(64, 416), **options):
        return load_review_sheet(self.png(name, size, **options), kind)

    def report_data(self, html):
        content = re.search(r'<script id="review-data" type="application/json">(.*?)</script>', html, re.S)
        self.assertIsNotNone(content)
        return json.loads(content.group(1))

    def test_native_sprite_coordinates_include_all_extra_cells(self):
        sheet = self.sheet()
        self.assertIsInstance(sheet, ReviewSheet)
        self.assertEqual((sheet.width, sheet.height, sheet.columns, sheet.frame_count), (64, 416, 4, 52))
        self.assertEqual((sheet.frame_width, sheet.frame_height, sheet.display_size), (16, 32, (16, 32)))
        self.assertEqual(sheet.frame_rect(0), (0, 0, 16, 32))
        self.assertEqual(sheet.frame_rect(3), (48, 0, 16, 32))
        self.assertEqual(sheet.frame_rect(4), (0, 32, 16, 32))
        self.assertEqual(sheet.frame_rect(51), (48, 384, 16, 32))
        self.assertTrue(sheet.native)
        self.assertEqual(sheet.warnings, ())
        self.assertEqual(sheet.blank_frames, ())

    def test_portrait_and_noninteger_scale_coordinates_use_source_pixels(self):
        native = self.sheet("portrait", "portrait.png", (128, 320))
        self.assertEqual(native.frame_count, 10)
        self.assertEqual(native.frame_rect(9), (64, 256, 64, 64))
        high = self.sheet("portrait", "larger.png", (130, 325))
        self.assertFalse(high.native)
        self.assertEqual(high.frame_rect(9), (65, 260, 65, 65))
        self.assertEqual(high.display_size, (64, 64))
        self.assertIn("not native 64×64", high.warnings[0])
        sprite = self.sheet("sprite", "large-sprite.png", (68, 442))
        self.assertEqual(sprite.frame_rect(51), (51, 408, 17, 34))
        self.assertEqual(sprite.display_size, (16, 32))

    def test_frame_rect_rejects_absent_and_invalid_indices(self):
        sheet = self.sheet()
        for index in (-1, 52, 200, 1.0, True, "0", None):
            with self.subTest(index=index), self.assertRaises(IndexError):
                sheet.frame_rect(index)

    def test_incomplete_minimum_sets_warn_but_remain_reviewable(self):
        portrait = self.sheet("portrait", "short-portrait.png", (128, 128))
        sprite = self.sheet("sprite", "short-sprite.png", (64, 64))
        self.assertEqual(portrait.frame_count, 4)
        self.assertEqual(sprite.frame_count, 8)
        self.assertIn("minimum is 6", portrait.warnings[0])
        self.assertIn("minimum is 16", sprite.warnings[0])

    def test_invalid_or_partial_grids_are_rejected(self):
        for kind, size in (("portrait", (128, 191)), ("portrait", (129, 194)),
                           ("portrait", (64, 64)), ("sprite", (64, 130)),
                           ("sprite", (32, 64)), ("sprite", (66, 132)),
                           ("sprite", (64, 16))):
            with self.subTest(kind=kind, size=size):
                with self.assertRaisesRegex(ArtworkValidationError, "complete columns"):
                    self.sheet(kind, size=size)

    def test_review_frame_limit_is_enforced_without_silent_truncation(self):
        limit = self.sheet("sprite", size=(64, 32 * MAX_REVIEW_FRAMES // 4))
        self.assertEqual(limit.frame_count, 512)
        with self.assertRaisesRegex(ArtworkValidationError, "at most 512"):
            self.sheet("sprite", size=(64, 32 * (MAX_REVIEW_FRAMES // 4 + 1)))

    def test_blank_detection_uses_actual_alpha_not_fixed_frame_indices(self):
        path = self.png()
        with Image.open(path) as image:
            for index in (2, 17, 51):
                x, y = index % 4 * 16, index // 4 * 32
                image.paste((99, 50, 12, 0), (x, y, x + 16, y + 32))
            image.putpixel((32, 0), (99, 50, 12, 1))  # cell 2 is faint, not blank
            image.save(path)
        sheet = load_review_sheet(path, "sprite")
        self.assertEqual(sheet.blank_frames, (17, 51))
        self.assertNotIn(23, sheet.blank_frames)
        self.assertNotIn(41, sheet.blank_frames)
        self.assertNotIn(43, sheet.blank_frames)

    def test_palette_transparency_and_no_alpha_sources(self):
        path = self.directory / "palette.png"
        with Image.new("P", (64, 32), 0) as image:
            image.putpalette([20, 30, 40, 50, 60, 70] + [0] * (768 - 6))
            image.putpixel((0, 0), 1)
            image.save(path, transparency=0)
        sheet = load_review_sheet(path, "sprite")
        self.assertEqual(sheet.blank_frames, (1, 2, 3))
        with Image.new("RGB", (64, 32), (0, 0, 0)) as image:
            image.save(path)
        self.assertEqual(load_review_sheet(path, "sprite").blank_frames, ())

    def test_read_once_sanitize_metadata_and_preserve_pixel_snapshot(self):
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("Source", str(self.directory / "private-file.png"))
        path = self.png(pnginfo=metadata)
        before = path.read_bytes()
        from pixelheart_core.artwork_review import _read_png
        with patch("pixelheart_core.artwork_review._read_png", wraps=_read_png) as read:
            sheet = load_review_sheet(path, "sprite", "Current artwork")
        read.assert_called_once()
        self.assertEqual(path.read_bytes(), before)
        with Image.open(io.BytesIO(sheet.png)) as snapshot, Image.open(path) as original:
            self.assertEqual(snapshot.tobytes(), original.tobytes())
            self.assertEqual(snapshot.info, {})
        captured_png = sheet.png
        self.png(color=(200, 2, 1, 255))
        self.assertIs(sheet.png, captured_png)
        path.unlink()
        report = render_artwork_review_html("Review", {"sprite": sheet})
        self.assertNotIn(str(self.directory), report)
        payload = self.report_data(report)["sprite-current"]
        self.assertEqual(base64.b64decode(payload["png"].split(",", 1)[1]), sheet.png)
        with Image.open(io.BytesIO(sheet.png)) as image:
            self.assertEqual(image.getpixel((0, 0)), (30, 90, 160, 255))
        with self.assertRaises(FrozenInstanceError):
            sheet.frame_count = 1
        with self.assertRaises(TypeError):
            sheet.png[0] = 0

    def test_unsafe_paths_and_invalid_containers_use_existing_validation(self):
        for path in (None, "bad\x00path", self.directory, self.directory / "missing.png"):
            with self.subTest(path=path), self.assertRaises(ArtworkValidationError):
                load_review_sheet(path, "sprite")
        path = self.directory / "pretend.png"
        path.write_text('<svg onload="alert(1)"></svg>')
        with self.assertRaises(ArtworkValidationError):
            load_review_sheet(path, "sprite")
        with Image.new("RGB", (64, 128)) as image:
            image.save(path, format="JPEG")
        with self.assertRaisesRegex(ArtworkValidationError, "Only PNG"):
            load_review_sheet(path, "sprite")
        path = self.png()
        path.write_bytes(path.read_bytes()[:-20])
        with self.assertRaises(ArtworkValidationError):
            load_review_sheet(path, "sprite")

    def test_animated_png_is_rejected(self):
        path = self.directory / "animated.png"
        with Image.new("RGBA", (64, 128), "red") as first, Image.new("RGBA", (64, 128), "blue") as second:
            first.save(path, save_all=True, append_images=[second], duration=100)
        with self.assertRaisesRegex(ArtworkValidationError, "static PNG"):
            load_review_sheet(path, "sprite")

    def test_unknown_kind_is_rejected_before_reading(self):
        with patch("pixelheart_core.artwork_review._read_png") as read:
            with self.assertRaisesRegex(ArtworkValidationError, "portrait or sprite"):
                load_review_sheet("anything", "map")
            read.assert_not_called()

    def test_titles_are_generic_and_do_not_infer_extra_sprite_actions(self):
        self.assertEqual(frame_title("portrait", 0), "Neutral · $0")
        self.assertEqual(frame_title("portrait", 5), "Angry · $5")
        self.assertEqual(frame_title("portrait", 9), "Expression · $9")
        for index, direction in ((0, "down"), (4, "right"), (8, "up"), (15, "left")):
            self.assertEqual(frame_title("sprite", index), f"Walk {direction} · {index}")
        for index in (16, 23, 35, 41, 43, 48, 51, 100):
            self.assertEqual(frame_title("sprite", index), f"Extra pose · {index}")

    def test_report_includes_all_extra_frames_and_distinguishes_missing_from_blank(self):
        current = self.sheet("sprite", "current.png", (64, 416), color=(0, 0, 0, 0))
        reference = self.sheet("sprite", "reference.png", (64, 448))
        report = render_artwork_review_html("Comparison", {"sprite": current}, {"sprite": reference})
        cards = [attrs for tag, attrs in ReportParser(report).tags if attrs.get("class") == "frame-card"]
        self.assertEqual([int(card["data-index"]) for card in cards], list(range(56)))
        self.assertEqual(report.count('class="missing">Missing frame'), 4)
        self.assertIn('id="sprite-frame-55"', report)
        self.assertIn('class="empty">Fully transparent', report)
        self.assertEqual(self.report_data(report)["sprite-current"]["blankFrames"], list(range(52)))

    def test_report_handles_both_kinds_and_reference_only_kind(self):
        portrait = self.sheet("portrait", "portrait.png", (128, 192))
        reference = self.sheet("sprite", "reference.png", (64, 128))
        report = render_artwork_review_html("Review", {"portrait": portrait}, {"sprite": reference}, "Winter")
        tags = ReportParser(report).tags
        tabs = [attrs for tag, attrs in tags if attrs.get("role") == "tab"]
        self.assertEqual([tab["data-kind"] for tab in tabs], ["portrait", "sprite"])
        self.assertIn("Appearance: <strong>Winter</strong>", report)
        self.assertEqual(report.count('class="missing">Missing frame'), 16)
        self.assertNotIn('data-sheet="sprite-current"', report)
        self.assertIn('data-sheet="sprite-reference"', report)

    def test_high_resolution_report_uses_game_sized_canvas_and_warns(self):
        sheet = self.sheet("portrait", size=(256, 384))
        report = render_artwork_review_html("Review", {"portrait": sheet})
        canvases = [attrs for tag, attrs in ReportParser(report).tags if tag == "canvas"]
        self.assertTrue(canvases)
        self.assertTrue(all((canvas["width"], canvas["height"]) == ("64", "64") for canvas in canvases))
        self.assertIn("not native 64×64", report)
        self.assertIn("128 × 128 source px", report)
        self.assertEqual(self.report_data(report)["portrait-current"]["frameWidth"], 128)

    def test_user_text_cannot_break_html_script_or_template_substitution(self):
        hostile = '</script><script>alert("x")</script><img src=x onerror=alert(1)> & " \' \u2028\u2029 __DATA__'
        sheet = load_review_sheet(self.png(), "sprite", hostile)
        report = render_artwork_review_html(hostile + " __PANELS__", {"sprite": sheet}, appearance=hostile)
        data = self.report_data(report)
        self.assertEqual(data["sprite-current"]["label"], hostile)
        tags = ReportParser(report).tags
        self.assertEqual(len([tag for tag, attrs in tags if tag == "script"]), 2)
        self.assertFalse(any(tag == "img" or "onerror" in attrs for tag, attrs in tags))
        self.assertIn("&lt;/script&gt;", report)
        self.assertIn("\\u003c/script\\u003e", report)
        self.assertIn("\\u2028\\u2029", report)
        self.assertIn("__PANELS__</h1>", report)

    def test_report_has_no_network_resources_or_paths(self):
        sheet = self.sheet()
        report = render_artwork_review_html("Review", {"sprite": sheet})
        tags = ReportParser(report).tags
        for tag, attrs in tags:
            self.assertNotIn("src", attrs)
            self.assertNotIn("href", attrs)
        self.assertIn("default-src 'none'", report)
        self.assertNotIn(str(self.directory), report)
        self.assertNotIn("file://", report)
        self.assertNotIn("fetch(", report)

    def test_report_rejects_empty_or_mismatched_sheet_mappings(self):
        sheet = self.sheet()
        for sheets, references in (({}, None), ({"portrait": sheet}, None), ({"map": sheet}, None),
                                   ({"sprite": object()}, None), ([], None), ({}, [])):
            with self.subTest(sheets=sheets, references=references), self.assertRaises(ArtworkValidationError):
                render_artwork_review_html("Review", sheets, references)


if __name__ == "__main__":
    unittest.main()

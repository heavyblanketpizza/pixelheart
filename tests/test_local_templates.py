"""Local game-reference imports using synthetic exports only."""

import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from pixelheart_core import local_templates as local


def png_bytes(size):
    output = io.BytesIO()
    with Image.new("RGBA", size, (80, 160, 120, 255)) as image:
        image.save(output, format="PNG")
    return output.getvalue()


class LocalTemplateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.exports = self.root / "patch export"
        self.exports.mkdir()
        self.dialogue = self.exports / "Characters_Dialogue_Abigail.json"
        self.data = {
            "Introduction": "A synthetic greeting, @.$h#$e#A second part.",
            "Mon": "Monday text.",
            "summer_Tue4": "$p 42#A synthetic yes.|A synthetic no.",
            "spring_28_*": "Synthetic calendar text.\nSecond line.",
            "answer_yes": "The player's choice is preserved.",
        }
        self.dialogue.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")

    def artwork(self, *, portrait_size=(128, 320), sprite_size=(64, 416), root=None):
        root = self.exports if root is None else root
        paths = {}
        for kind, filename, size in (
            ("portrait", "Portraits_Abigail.png", portrait_size),
            ("sprite", "Characters_Abigail.png", sprite_size),
        ):
            paths[kind] = root / filename
            paths[kind].write_bytes(png_bytes(size))
        return paths

    def test_complete_dialogue_preserves_text_order_and_provenance_offline(self):
        before = self.dialogue.read_bytes()
        with patch("urllib.request.urlopen", side_effect=AssertionError("Network forbidden")), \
                patch("urllib.request.build_opener", side_effect=AssertionError("Network forbidden")):
            result = local.load_local_dialogue("abigail", self.exports)
        self.assertEqual(result["id"], "abigail")
        self.assertEqual(result["name"], "Abigail")
        self.assertEqual([(entry["trigger"], entry["text"]) for entry in result["examples"]], list(self.data.items()))
        source = result["source"]
        self.assertEqual(source["provider"], "local-content-patcher")
        self.assertEqual(source["asset"], "Characters/Dialogue/Abigail")
        self.assertEqual(source["sha256"], hashlib.sha256(before).hexdigest())
        self.assertEqual(source["source_url"], local.CONTENT_PATCHER_EXPORT_URL)
        self.assertTrue(source["modified_game_possible"])
        for entry in result["examples"]:
            self.assertEqual(entry["source"], source)
            self.assertIsNot(entry["source"], source)
            self.assertTrue(all(entry[key] for key in ("title", "when", "lesson")))
            self.assertNotIn("archived", entry["title"].lower())
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertEqual(self.dialogue.read_bytes(), before)
        self.assertEqual(list(self.exports.iterdir()), [self.dialogue])

    def test_more_than_old_250_entry_limit_is_imported_without_truncation(self):
        data = {f"Custom_{i}": f"Synthetic line {i}." for i in range(local.MAX_DIALOGUE_ENTRIES)}
        self.dialogue.write_text(json.dumps(data), encoding="utf-8")
        result = local.load_local_dialogue("abigail", self.exports)
        self.assertEqual(len(result["examples"]), local.MAX_DIALOGUE_ENTRIES)
        self.assertEqual(result["examples"][-1]["text"], data[f"Custom_{local.MAX_DIALOGUE_ENTRIES - 1}"])

    def test_export_root_game_root_and_mac_bundle_layouts(self):
        self.assertEqual(local.resolve_export_folder(self.exports), self.exports)
        self.assertEqual(local.resolve_export_folder(self.root), self.exports)
        for selection, relative in (
            ("steam-windows", "patch export"),
            ("gog-linux", "patch export"),
            ("Stardew Valley.app", "Contents/MacOS/patch export"),
            ("gog-macos", "Stardew Valley.app/Contents/MacOS/patch export"),
        ):
            with self.subTest(selection=selection):
                selected = self.root / selection
                destination = selected / relative
                destination.mkdir(parents=True)
                (destination / self.dialogue.name).write_bytes(self.dialogue.read_bytes())
                self.assertEqual(local.resolve_export_folder(selected), destination)
                self.assertEqual(len(local.load_local_dialogue("abigail", selected)["examples"]), len(self.data))
        macos_root = self.root / "Stardew Valley.app" / "Contents" / "MacOS"
        self.assertEqual(local.resolve_export_folder(macos_root), macos_root / "patch export")

    def test_nested_unpacked_layout_is_read_without_renaming_files(self):
        game = self.root / "another game"
        unpacked = game / "Content (unpacked)"
        dialogue = unpacked / "Characters" / "Dialogue" / "Elliott.json"
        dialogue.parent.mkdir(parents=True)
        dialogue.write_bytes(self.dialogue.read_bytes())
        portrait = unpacked / "Portraits" / "Elliott.png"
        portrait.parent.mkdir()
        portrait.write_bytes(png_bytes((128, 320)))
        sprite = unpacked / "Characters" / "Elliott.png"
        sprite.write_bytes(png_bytes((64, 416)))
        result = local.load_local_dialogue("elliott", game)
        self.assertEqual(result["source"]["asset"], "Characters/Dialogue/Elliott")
        artwork = local.load_local_artwork("elliott", game)
        self.assertEqual(artwork["portrait"], portrait)
        self.assertEqual(artwork["sprite"], sprite)

    def test_commands_use_actual_game_asset_keys_and_validate_selection(self):
        self.assertEqual(local.export_commands("elliott", "dialogue"), 'patch export "Characters/Dialogue/Elliott"')
        self.assertEqual(local.export_commands("abigail", "artwork"), 'patch export "Portraits/Abigail" image\npatch export "Characters/Abigail" image')
        self.assertEqual(len(local.export_commands("abigail").splitlines()), 3)
        for template_id in (None, [], "../abigail", "Abigail", "leah"):
            with self.subTest(template_id=template_id):
                for call in (local.export_commands, local.load_local_dialogue, local.load_local_artwork):
                    with self.assertRaises(local.LocalTemplateError):
                        call(template_id, self.exports) if call != local.export_commands else call(template_id)
        with self.assertRaises(local.LocalTemplateError):
            local.export_commands("abigail", "unknown")

    def test_missing_assets_explain_required_command_without_exposing_path(self):
        for call, expected in (
            (local.load_local_dialogue, 'patch export "Characters/Dialogue/Elliott"'),
            (local.load_local_artwork, 'patch export "Portraits/Elliott"'),
        ):
            with self.subTest(call=call):
                with self.assertRaises(local.LocalTemplateError) as caught:
                    call("elliott", self.exports)
                self.assertIn(expected, str(caught.exception))
                self.assertNotIn(str(self.root), str(caught.exception))

    def test_malformed_nonstring_duplicate_and_unsafe_entries_are_rejected_atomically(self):
        invalid = (
            b'{"Mon":"First", "Mon":"Duplicate"}', b"not json", b"[]", b"{}", b"null", b"\xff",
            b'{"Mon": 3}', b'{"Mon": null}', b'{"Mon": {"x": "Nested"}}',
            json.dumps({"Bad trigger": "Text"}).encode(),
            json.dumps({"A" * 121: "Text"}).encode(),
            json.dumps({"Mon": ""}).encode(),
            json.dumps({"Mon": " \n\t"}).encode(),
            json.dumps({"Mon": "X" * (local.MAX_TEXT_LENGTH + 1)}).encode(),
            json.dumps({"Mon": "{{PlayerName}}"}).encode(),
            json.dumps({"Mon": "Null\x00text"}).encode(),
            json.dumps({"Mon": "Control\x01text"}).encode(),
            json.dumps({"Mon": "Surrogate\ud800text"}).encode(),
            json.dumps({f"Custom_{i}": "Text" for i in range(local.MAX_DIALOGUE_ENTRIES + 1)}).encode(),
        )
        for payload in invalid:
            with self.subTest(payload=payload[:50]):
                self.dialogue.write_bytes(payload)
                with self.assertRaises(local.LocalTemplateError):
                    local.load_local_dialogue("abigail", self.exports)
                self.assertEqual(self.dialogue.read_bytes(), payload)

    def test_utf8_bom_non_english_text_and_maximum_text_length_are_supported(self):
        data = {"Introduction": "안녕하세요。Hello, @.$h", "Mon": "é" * local.MAX_TEXT_LENGTH}
        self.dialogue.write_bytes(b"\xef\xbb\xbf" + json.dumps(data, ensure_ascii=False).encode())
        result = local.load_local_dialogue("abigail", self.exports)
        self.assertEqual({row["trigger"]: row["text"] for row in result["examples"]}, data)

    def test_dialogue_size_limit_is_enforced_before_parsing(self):
        with patch.object(local, "MAX_DIALOGUE_BYTES", 8):
            with self.assertRaisesRegex(local.LocalTemplateError, "size limit"):
                local.load_local_dialogue("abigail", self.exports)

    def test_directory_fifo_and_links_are_never_read_as_asset_files(self):
        self.dialogue.unlink()
        self.dialogue.mkdir()
        with self.assertRaisesRegex(local.LocalTemplateError, "regular files"):
            local.load_local_dialogue("abigail", self.exports)
        self.dialogue.rmdir()
        if hasattr(os, "mkfifo"):
            os.mkfifo(self.dialogue)
            with self.assertRaisesRegex(local.LocalTemplateError, "regular files"):
                local.load_local_dialogue("abigail", self.exports)
            self.dialogue.unlink()
        external = self.root / "private.json"
        external.write_text(json.dumps(self.data), encoding="utf-8")
        self.dialogue.symlink_to(external)
        with self.assertRaisesRegex(local.LocalTemplateError, "linked"):
            local.load_local_dialogue("abigail", self.exports)
        self.assertEqual(json.loads(external.read_text()), self.data)

    def test_linked_export_or_nested_folders_cannot_escape_selected_root(self):
        selected = self.root / "selected game"
        selected.mkdir()
        (selected / "patch export").symlink_to(self.exports, target_is_directory=True)
        with self.assertRaisesRegex(local.LocalTemplateError, "linked"):
            local.load_local_dialogue("abigail", selected)
        self.dialogue.unlink()
        target = self.root / "external characters"
        (target / "Dialogue").mkdir(parents=True)
        (target / "Dialogue" / "Abigail.json").write_text(json.dumps(self.data), encoding="utf-8")
        (self.exports / "Characters").symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(local.LocalTemplateError, "linked"):
            local.load_local_dialogue("abigail", self.exports)

    def test_invalid_root_is_actionable_without_leaking_local_path(self):
        for root in (None, "", self.root / "missing", self.dialogue, "bad\x00root"):
            with self.subTest(root=root):
                with self.assertRaises(local.LocalTemplateError) as caught:
                    local.resolve_export_folder(root)
                self.assertNotIn(str(self.root), str(caught.exception))

    def test_cancellation_before_or_during_read_never_modifies_files(self):
        paths = self.artwork()
        before = {path: path.read_bytes() for path in [self.dialogue, *paths.values()]}
        for call in (local.load_local_dialogue, local.load_local_artwork):
            with self.subTest(call=call):
                for cancelled in (lambda: True, iter((False, False, True)).__next__):
                    with self.assertRaisesRegex(local.LocalTemplateError, "cancelled"):
                        call("abigail", self.exports, cancelled=cancelled)
        for path, payload in before.items():
            self.assertEqual(path.read_bytes(), payload)

    def test_complete_artwork_returns_original_paths_and_hashes_without_assuming_vanilla(self):
        paths = self.artwork(portrait_size=(128, 384), sprite_size=(64, 448))
        before = {kind: path.read_bytes() for kind, path in paths.items()}
        result = local.load_local_artwork("abigail", self.root)
        for kind in paths:
            self.assertEqual(result[kind], paths[kind])
            self.assertEqual(paths[kind].read_bytes(), before[kind])
            source = result[kind + "_source"]
            self.assertEqual(source["sha256"], hashlib.sha256(before[kind]).hexdigest())
            self.assertTrue(source["modified_game_possible"])
            self.assertEqual(source["source_url"], local.CONTENT_PATCHER_EXPORT_URL)
            self.assertNotIn(str(self.root), json.dumps(source))
        self.assertNotIn("vanilla", result["source_name"].lower())
        self.assertNotIn(str(self.root), json.dumps(result["source"]))
        self.assertEqual(result["portrait_source"]["asset"], "Portraits/Abigail")
        self.assertEqual(result["sprite_source"]["asset"], "Characters/Abigail")

    def test_hd_proportional_sheets_remain_unchanged(self):
        paths = self.artwork(portrait_size=(256, 640), sprite_size=(128, 832))
        result = local.load_local_artwork("abigail", self.exports)
        self.assertEqual(result["portrait"].read_bytes(), paths["portrait"].read_bytes())
        self.assertEqual(result["sprite"].read_bytes(), paths["sprite"].read_bytes())

    def test_incomplete_invalid_oversized_or_animated_artwork_is_rejected(self):
        paths = self.artwork()
        bad_images = (b"not png", png_bytes((128, 128)), png_bytes((128, 321)), png_bytes((64, 192)))
        for payload in bad_images:
            with self.subTest(payload_size=len(payload)):
                paths["portrait"].write_bytes(payload)
                with self.assertRaises(local.LocalTemplateError):
                    local.load_local_artwork("abigail", self.exports)
        paths = self.artwork(sprite_size=(64, 128))
        with self.assertRaisesRegex(local.LocalTemplateError, "complete sprite sheet"):
            local.load_local_artwork("abigail", self.exports)
        self.artwork()
        with patch.object(local, "MAX_ARTWORK_BYTES", 8):
            with self.assertRaisesRegex(local.LocalTemplateError, "size limit"):
                local.load_local_artwork("abigail", self.exports)
        output = io.BytesIO()
        with Image.new("RGBA", (128, 320), "red") as first, Image.new("RGBA", (128, 320), "blue") as second:
            first.save(output, format="PNG", save_all=True, append_images=[second])
        paths["portrait"].write_bytes(output.getvalue())
        with self.assertRaisesRegex(local.LocalTemplateError, "static PNG"):
            local.load_local_artwork("abigail", self.exports)

    def test_artwork_changed_during_validation_is_rejected(self):
        paths = self.artwork()
        inspect = local.inspect_artwork

        def modify_after_inspecting(path):
            result = inspect(path)
            path.write_bytes(png_bytes((128, 384)))
            return result

        with patch.object(local, "inspect_artwork", side_effect=modify_after_inspecting):
            with self.assertRaisesRegex(local.LocalTemplateError, "changed while loading"):
                local.load_local_artwork("abigail", self.exports)
        self.assertTrue(paths["sprite"].exists())


if __name__ == "__main__":
    unittest.main()

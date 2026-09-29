"""Synthetic installs only: no Stardew files are needed or read."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pixelheart_core.game_install import (
    GameInstallError, candidate_folders, find_games, inspect_game,
)


def fake_game(root, *, mac=True, version="1.6.15", smapi=False, cp_version=None):
    root = Path(root)
    content = root / ("Contents/Resources/Content" if mac else "Content")
    (content / "Maps").mkdir(parents=True)
    (content / "Maps/Town.xnb").write_bytes(b"not decoded here")
    executable = root / "Contents/MacOS" if mac else root
    executable.mkdir(parents=True, exist_ok=True)
    (executable / "Stardew Valley.dll").write_bytes(b"")
    (executable / "Stardew Valley.deps.json").write_text(
        json.dumps({"libraries": {f"Stardew Valley/{version}.24356": {}}}))
    if smapi:
        (executable / ("StardewModdingAPI" if mac else "StardewModdingAPI.exe")).write_bytes(b"")
    if cp_version:
        manifest = executable / "Mods/ContentPatcher/manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps({"UniqueID": "Pathoschild.ContentPatcher", "Version": cp_version}))
    return root


class InspectGameTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_mac_steam_layout(self):
        root = fake_game(self.folder / "Stardew Valley")
        install = inspect_game(root)
        self.assertEqual(install.root, root.resolve())
        self.assertEqual(install.content, (root / "Contents/Resources/Content").resolve())
        self.assertEqual(install.executable_dir, (root / "Contents/MacOS").resolve())
        self.assertEqual(install.version, "1.6.15")
        self.assertEqual(install.mods, (root / "Contents/MacOS/Mods").resolve())
        self.assertFalse(install.smapi)
        self.assertIsNone(install.content_patcher)

    def test_flat_layout_with_smapi_and_content_patcher(self):
        root = fake_game(self.folder / "Game", mac=False, smapi=True, cp_version="2.5.3")
        install = inspect_game(root)
        self.assertTrue(install.smapi)
        self.assertEqual(install.content_patcher, "2.5.3")
        self.assertEqual(install.mods, (root / "Mods").resolve())

    def test_app_bundle_and_contents_levels_are_accepted(self):
        bundle = self.folder / "Stardew Valley.app"
        fake_game(bundle)
        self.assertEqual(inspect_game(bundle).version, "1.6.15")
        self.assertEqual(inspect_game(self.folder).content,
                         (bundle / "Contents/Resources/Content").resolve())

    def test_missing_folder_is_reported_as_unavailable(self):
        with self.assertRaisesRegex(GameInstallError, "isn't available right now"):
            inspect_game(self.folder / "Unplugged drive" / "Stardew Valley")

    def test_unrelated_folder_is_friendly_error(self):
        (self.folder / "Photos").mkdir()
        with self.assertRaisesRegex(GameInstallError, "doesn't look like Stardew Valley"):
            inspect_game(self.folder / "Photos")

    def test_missing_version_metadata_is_tolerated(self):
        root = fake_game(self.folder / "Game")
        (root / "Contents/MacOS/Stardew Valley.deps.json").unlink()
        self.assertIsNone(inspect_game(root).version)


class FindGamesTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.home = self.folder / "home"
        self.steam = self.home / "Library/Application Support/Steam"
        (self.steam / "steamapps").mkdir(parents=True)
        self.enterContext(patch("pixelheart_core.game_install.SYSTEM_CANDIDATES", ()))
        environment = {key: value for key, value in os.environ.items()
                       if key not in ("PROGRAMFILES", "PROGRAMFILES(X86)")}
        self.enterContext(patch.dict(os.environ, environment, clear=True))

    def library_file(self, *paths):
        rows = "".join(f'\t"{index}"\n\t{{\n\t\t"path"\t\t"{path}"\n\t}}\n' for index, path in enumerate(paths))
        (self.steam / "steamapps/libraryfolders.vdf").write_text('"libraryfolders"\n{\n' + rows + "}\n")

    def test_external_steam_library_is_found(self):
        drive = self.folder / "Just for Fun" / "SteamLibrary"
        fake_game(drive / "steamapps/common/Stardew Valley")
        self.library_file(self.steam, drive)
        found = find_games(home=self.home)
        self.assertEqual([install.root for install in found],
                         [(drive / "steamapps/common/Stardew Valley").resolve()])

    def test_unplugged_libraries_are_skipped_and_results_are_unique(self):
        game = fake_game(self.steam / "steamapps/common/Stardew Valley")
        self.library_file(self.steam, self.folder / "Unplugged", self.steam)
        self.assertEqual([install.root for install in find_games(home=self.home)], [game.resolve()])

    def test_nothing_found_returns_empty_list(self):
        self.assertEqual(find_games(home=self.home), [])

    def test_candidates_include_gog_home_folder(self):
        self.assertIn(self.home / "GOG Games/Stardew Valley", candidate_folders(self.home))

    def test_legacy_discover_game_root_uses_find_games(self):
        from pixelheart_core.game_scene_assets import discover_game_root
        game = fake_game(self.steam / "steamapps/common/Stardew Valley")
        with patch("pixelheart_core.game_install.Path.home", return_value=self.home):
            self.assertEqual(discover_game_root(), game.resolve())


if __name__ == "__main__":
    unittest.main()

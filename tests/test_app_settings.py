"""Tests must never read or write the player's real Pixelheart settings."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import tests  # noqa: F401  (sets the isolation environment)
from pixelheart.game_import import game_import_settings


class AppSettingsTests(unittest.TestCase):
    def test_settings_file_override_is_used(self):
        with tempfile.TemporaryDirectory() as folder:
            target = str(Path(folder) / "settings.ini")
            with patch.dict(os.environ, {"PIXELHEART_SETTINGS_FILE": target}):
                settings = game_import_settings()
                settings.setValue("probe/value", "ok")
                settings.sync()
                self.assertEqual(Path(settings.fileName()), Path(target))
                self.assertTrue(Path(target).is_file())

    def test_suite_isolates_settings_library_and_cache(self):
        for variable in ("PIXELHEART_SETTINGS_FILE", "PIXELHEART_LIBRARY", "PIXELHEART_CACHE_DIR"):
            with self.subTest(variable=variable):
                value = Path(os.environ[variable])
                self.assertTrue(any(part.startswith("pixelheart-tests-") for part in value.parts))


if __name__ == "__main__":
    unittest.main()

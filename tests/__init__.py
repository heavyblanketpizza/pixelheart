"""Pixelheart's local test suite and shared fixtures."""
import atexit
import os
import shutil
import tempfile

# Keep every test away from the player's real settings, library and game cache.
_ROOT = tempfile.mkdtemp(prefix="pixelheart-tests-")
atexit.register(shutil.rmtree, _ROOT, True)
os.environ.setdefault("PIXELHEART_SETTINGS_FILE", os.path.join(_ROOT, "settings.ini"))
os.environ.setdefault("PIXELHEART_LIBRARY", os.path.join(_ROOT, "library"))
os.environ.setdefault("PIXELHEART_CACHE_DIR", os.path.join(_ROOT, "cache"))

"""Find and describe a local Stardew Valley installation, read-only.

Only known install locations and Steam's own library list are inspected. Nothing
is searched recursively, nothing is written, and no game code is loaded.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re

from .game_scene_assets import content_root

MAX_METADATA_BYTES = 2 * 1024 * 1024
SYSTEM_CANDIDATES = (Path("/Applications/Stardew Valley.app"),)
_EXECUTABLES = ("Stardew Valley.dll", "Stardew Valley.deps.json", "Stardew Valley.exe", "StardewValley")
_SMAPI = ("StardewModdingAPI", "StardewModdingAPI.exe", "StardewModdingAPI.dll")
_CONTENT_PATCHER = "pathoschild.contentpatcher"


class GameInstallError(ValueError):
    """A player-facing reason a folder can't be used as the game."""


@dataclass(frozen=True)
class GameInstall:
    root: Path
    content: Path
    executable_dir: Path
    version: str | None
    mods: Path
    smapi: bool
    content_patcher: str | None


def _read_small(path, limit=MAX_METADATA_BYTES):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
            return None
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None


def _executable_dir(root, content):
    for candidate in (root / "Contents/MacOS", root / "Stardew Valley.app/Contents/MacOS", root, content.parent):
        if any((candidate / name).exists() for name in _EXECUTABLES):
            return candidate
    return root


def _version(executable_dir):
    text = _read_small(executable_dir / "Stardew Valley.deps.json") or ""
    match = re.search(r'"Stardew Valley/(\d+\.\d+(?:\.\d+)?)', text)
    return match.group(1) if match else None


def _content_patcher(mods):
    if not mods.is_dir():
        return None
    scanned = 0
    for directory, subdirs, files in os.walk(mods, followlinks=False):
        depth = len(Path(directory).relative_to(mods).parts)
        subdirs[:] = [] if depth >= 2 else [name for name in subdirs if not name.startswith(".")]
        scanned += 1
        if scanned > 512:
            break
        if "manifest.json" not in files:
            continue
        try:
            manifest = json.loads(_read_small(Path(directory) / "manifest.json", 1024 * 1024) or "null")
        except ValueError:
            continue
        if isinstance(manifest, dict) and str(manifest.get("UniqueID", "")).casefold() == _CONTENT_PATCHER:
            return str(manifest.get("Version") or "unknown")
    return None


def inspect_game(folder) -> GameInstall:
    """Describe one folder, or explain in plain words why it isn't the game."""
    if not folder or "\x00" in str(folder):
        raise GameInstallError("Choose the folder Stardew Valley is installed in.")
    path = Path(folder).expanduser()
    if not path.exists():
        raise GameInstallError("That folder isn't available right now. If the game is on an external drive, "
                               "plug it in and try again.")
    try:
        root = path.resolve(strict=True)
        content = content_root(root)
    except (OSError, ValueError, RuntimeError) as exc:
        raise GameInstallError("That folder doesn't look like Stardew Valley. Choose the folder the game is installed in "
                               "(on a Mac, the one that contains Contents or Stardew Valley.app).") from exc
    executable_dir = _executable_dir(root, content)
    mods = executable_dir / "Mods"
    return GameInstall(root=root, content=content, executable_dir=executable_dir,
                       version=_version(executable_dir), mods=mods,
                       smapi=any((executable_dir / name).exists() for name in _SMAPI),
                       content_patcher=_content_patcher(mods))


def _steam_roots(home):
    roots = [home / "Library/Application Support/Steam", home / ".steam/steam", home / ".local/share/Steam",
             home / ".var/app/com.valvesoftware.Steam/.local/share/Steam"]
    for variable in ("PROGRAMFILES(X86)", "PROGRAMFILES"):
        if os.environ.get(variable):
            roots.append(Path(os.environ[variable]) / "Steam")
    return roots


def _steam_libraries(root):
    libraries = [root]
    text = _read_small(root / "steamapps/libraryfolders.vdf", 128 * 1024) or ""
    libraries.extend(Path(path.replace("\\\\", "\\")) for path in re.findall(r'"path"\s+"([^"\n]{1,1024})"', text))
    return libraries


def candidate_folders(home=None):
    """Known install locations, including every Steam library (external drives too)."""
    home = Path.home() if home is None else Path(home)
    candidates = []
    for root in _steam_roots(home):
        if root.is_dir():
            candidates.extend(library / "steamapps/common/Stardew Valley" for library in _steam_libraries(root))
    candidates.extend(SYSTEM_CANDIDATES)
    candidates.extend((home / "Applications/Stardew Valley.app", home / "GOG Games/Stardew Valley"))
    for variable in ("PROGRAMFILES(X86)", "PROGRAMFILES"):
        if os.environ.get(variable):
            base = Path(os.environ[variable])
            candidates.extend((base / "GOG Galaxy/Games/Stardew Valley", base / "Stardew Valley"))
    return candidates


def find_games(*, limit=8, home=None):
    """Every distinct install at a known location, in discovery order."""
    found, seen = [], set()
    for candidate in candidate_folders(home)[:48]:
        try:
            install = inspect_game(candidate)
        except GameInstallError:
            continue
        if install.root not in seen:
            seen.add(install.root)
            found.append(install)
            if len(found) >= limit:
                break
    return found

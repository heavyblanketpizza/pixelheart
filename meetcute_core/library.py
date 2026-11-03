"""Where characters live by default, and small summaries for the welcome screen."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sys

LIBRARY_ENV = "MEETCUTE_LIBRARY"
PROJECTS_ENV = "MEETCUTE_PROJECTS"
MAX_RECENT = 12
TRASH_NAME = "Recycle Bin" if sys.platform.startswith("win") else "Trash"


def library_roots():
    """Writable default first, followed by any previous library to show on Home."""
    override = os.environ.get(LIBRARY_ENV) or os.environ.get("PIXELHEART_LIBRARY")
    if override:
        return (Path(override).expanduser(),)
    documents = Path.home() / "Documents"
    current, previous = documents / "MEETC#TE", documents / "Pixelheart"
    if previous.is_dir():
        return (current, previous) if current.is_dir() else (previous,)
    return (current,)


def default_library_root():
    return library_roots()[0]


def workspace_projects_root():
    """The projects folder beside MEETC#TE's own files, where characters were saved before the library."""
    override = os.environ.get(PROJECTS_ENV) or os.environ.get("PIXELHEART_PROJECTS")
    return Path(override).expanduser() if override else Path(__file__).resolve().parent.parent / "projects"


def folder_name(name):
    """A folder name that works on every desktop, keeping the name recognizable."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(name))
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".").strip()
    return cleaned[:48].strip() or "New character"


def new_project_path(root, name):
    root, base = Path(root), folder_name(name)
    for index in range(1, 1000):
        folder = root / (base if index == 1 else f"{base} {index}")
        if not folder.exists():
            return folder / "character.json"
    raise ValueError("Too many characters share this name. Choose a different name.")


def internal_name_for(name):
    """The game-facing ID, using the same rule older projects were migrated with."""
    internal = re.sub(r"[^A-Za-z0-9_]", "", str(name))
    return (internal if internal and internal[0].isalpha() else "NPC" + internal)[:64]


def remember_recent(recent, path, limit=MAX_RECENT):
    path = str(Path(path))
    rows = [path] + [str(Path(row)) for row in recent
                     if isinstance(row, str) and row and str(Path(row)) != path]
    return rows[:limit]


def library_projects(root, limit=50):
    """Character projects directly inside the library folder."""
    root = Path(root)
    try:
        children = sorted(root.iterdir())[:500] if root.is_dir() else []
    except OSError:
        return []
    found = []
    for child in children:
        candidate = child / "character.json"
        if child.is_dir() and not child.is_symlink() and candidate.is_file():
            found.append(candidate)
            if len(found) >= limit:
                break
    return found


def character_folder(path):
    """The character's own folder when it sits directly in the library or projects folder.

    Only such a folder is safe to move to the Trash as a whole; a character.json
    opened from anywhere else may share its folder with unrelated files.
    """
    path = Path(path)
    folder = path.parent
    if path.name != "character.json" or folder.is_symlink() or not path.is_file():
        return None
    try:
        parent = folder.parent.resolve()
        roots = {root.resolve() for root in (*library_roots(), workspace_projects_root())}
    except OSError:
        return None
    return folder if parent in roots and folder.resolve().parent == parent else None


def project_card(path):
    """A read-only summary for the welcome screen; never raises."""
    from .progress import hearts_earned, project_progress
    from .projects import ProjectError, load_project, resolve_artwork
    path = Path(path)
    try:
        document = load_project(path)
        modified = path.stat().st_mtime
    except (ProjectError, OSError, ValueError):
        return {"path": path, "name": path.parent.name or "Character", "tagline": "",
                "error": "This character couldn't be opened.", "portrait": None,
                "hearts": 0, "total": 8, "modified": None}
    try:
        portrait = resolve_artwork(document, path, "portrait")
    except (ProjectError, OSError, ValueError):
        portrait = None
    steps = project_progress(document)
    character = document["character"]
    return {"path": path, "name": character.get("name") or "Unnamed", "tagline": character.get("tagline", ""),
            "error": None, "portrait": portrait, "hearts": hearts_earned(steps), "total": len(steps),
            "modified": modified}


def edited_ago(timestamp, now=None):
    now = now or datetime.now(timezone.utc)
    then = datetime.fromtimestamp(timestamp, timezone.utc)
    seconds = max(0, (now - then).total_seconds())
    if seconds < 60:
        return "Edited just now"
    if seconds < 3600:
        minutes = int(seconds // 60)
        return f"Edited {minutes} minute{'s' if minutes != 1 else ''} ago"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f"Edited {hours} hour{'s' if hours != 1 else ''} ago"
    days = int(seconds // 86400)
    if days == 1:
        return "Edited yesterday"
    if days < 14:
        return f"Edited {days} days ago"
    return f"Edited {then.strftime('%b')} {then.day}"

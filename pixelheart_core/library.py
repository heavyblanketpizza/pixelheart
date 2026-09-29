"""Where characters live by default, and small summaries for the welcome screen."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re

LIBRARY_ENV = "PIXELHEART_LIBRARY"
MAX_RECENT = 12


def default_library_root():
    override = os.environ.get(LIBRARY_ENV)
    return Path(override).expanduser() if override else Path.home() / "Documents" / "Pixelheart"


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

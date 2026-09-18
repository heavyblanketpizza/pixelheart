"""Read-only, bounded dependency discovery in a user-selected Mods folder."""
import json
import os
from pathlib import Path
import re


def dependency_report(mods_directory, manifest):
    required = [manifest.get("ContentPackFor", {}), *manifest.get("Dependencies", [])]
    required = [row for row in required if isinstance(row, dict) and row.get("UniqueID") and row.get("IsRequired", True)]
    found = {}
    scanned = 0
    for directory, subdirs, files in os.walk(mods_directory, followlinks=False):
        subdirs[:] = [name for name in subdirs if not name.startswith(".") and not (Path(directory) / name).is_symlink()]
        scanned += 1
        if scanned > 2048:
            break
        if "manifest.json" not in files:
            continue
        path = Path(directory) / "manifest.json"
        try:
            if path.is_symlink() or path.stat().st_size > 1024 * 1024:
                continue
            entry = json.loads(path.read_text(encoding="utf-8-sig"))
            identity = entry.get("UniqueID")
            if isinstance(identity, str):
                found.setdefault(identity.casefold(), []).append(str(entry.get("Version", "unknown")))
        except (OSError, ValueError, AttributeError):
            continue
    result = []
    for entry in required:
        identity = entry["UniqueID"]
        versions = found.get(identity.casefold(), [])
        minimum = entry.get("MinimumVersion", "")
        status = "missing" if not versions else "review" if len(versions) > 1 else "found"
        version = versions[0] if len(versions) == 1 else ", ".join(versions)
        if status == "found" and minimum:
            a = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?", version)
            b = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?", str(minimum))
            if not a or not b:
                status = "review"
            elif tuple(int(v or 0) for v in a.groups()) < tuple(int(v or 0) for v in b.groups()):
                status = "old"
        result.append({"id": identity, "minimum_version": minimum, "version": version, "status": status})
    return result

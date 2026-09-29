"""The picture of a place a routine stop is at: a game map, a supplied map, or a Home room.

Nothing is substituted: a place without a map says why instead.
"""
from __future__ import annotations

from .projects import project_path
from .scene_preview import load_scene_map
from .world import exported_location_id


def _project_place(document, location):
    character = document.get("character", {}) if isinstance(document, dict) else {}
    for place in (document.get("world") or {}).get("locations", []) if isinstance(document, dict) else []:
        if isinstance(place, dict) and location in (place.get("internal_name"), exported_location_id(place, character)):
            return place
    return None


def load_route_map(document, project_file, location, *, game_root=None, export_root=None, max_map_size=2048):
    """Returns ``image``/``foreground`` (Pillow or None), ``map_size`` in tiles, ``label`` and ``note``."""
    result = {"image": None, "foreground": None, "map_size": None, "label": location or "", "note": ""}
    if not isinstance(location, str) or not location.strip():
        result["note"] = "Choose a place for this stop."
        return result
    place = _project_place(document, location)
    if place is not None and place.get("interior") and project_file:
        from .interiors import render_interior
        try:
            image = render_interior(place["interior"], project_path(project_file).parent)
        except (OSError, ValueError, TypeError) as exc:
            result.update(label=place.get("name") or location, note=f"This room can't be drawn yet: {exc}")
            return result
        result.update(image=image, map_size=(image.width // 16, image.height // 16),
                      label=place.get("name") or location, note="Your Home design")
        return result
    scene = load_scene_map(document, project_file, location, export_root=export_root, game_root=game_root,
                           max_map_size=max_map_size)
    result.update(image=scene["image"], foreground=scene["foreground"], map_size=scene["map_size"],
                  label=scene["location_label"] or location)
    if scene["image"] is not None:
        result["note"] = scene["source"]
    elif place is not None:
        result["note"] = "This place has no map to show yet."
    elif not (game_root or export_root):
        result["note"] = f"Connect your game to see {result['label']}."
    else:
        result["note"] = f"{result['label']} isn't in your game's maps. Mod places need their mod's map."
    return result

"""Placement semantics for architectural pieces, separate from readable storage.

Older projects stay openable. Edits may preserve existing unchanged problems so
an author can repair them individually; saving/exporting requires every piece
to satisfy its architectural role. Native identities retain their known role
when an older local library lacks explicit metadata.
"""
from __future__ import annotations

RULES = frozenset(("free", "steps_corridor", "wall_art", "wall_backed", "hearth", "column", "bar_run", "bar_return", "counter_end"))
NATIVE_RULES = {
    **{f"stardew.{name}": "wall_backed" for name in
       ("counter", "prep-counter", "stove", "sink", "refrigerator", "bookcase")},
    "stardew.work-counter": "counter_end",
    "stardew.dish-cupboard": "wall_art", "stardew.wall-cabinet": "wall_art",
    "stardew.brick-hearth": "hearth", "stardew.timber-column": "column",
    "stardew.stairs": "steps_corridor", "stardew.bar-counter": "bar_run", "stardew.bar-return": "bar_return",
}


def architecture_rule(definition):
    return NATIVE_RULES.get(definition["id"], definition.get("rules", "free"))


def architecture_rule_hint(definition):
    return {
        "free": "Place this static piece within one room.",
        "steps_corridor": "Add a raised room with steps. Keep the stairs, both landings and approaches clear. Drag connected steps sideways to reposition them.",
        "wall_art": "Mount the entire piece on the upper north wall, above its lower trim.",
        "wall_backed": "Align the bottom row with the first floor row against a solid north wall; leave its front clear.",
        "hearth": "Back the chimney with a continuous wall and ceiling cap; align the hearth base with the first floor row.",
        "column": "Attach the post to a solid north wall with its base on the first floor row.",
        "counter_end": "Back this right end against a wall and join its left edge to another counter or a side wall.",
        "bar_run": "Keep a clear, reachable staff approach behind the bar and serving approach in front.",
        "bar_return": "Join this left bar end to a counter on its right, align their bases, and leave the front clear.",
    }[architecture_rule(definition)]


def architecture_clearance_cells(item, definition):
    """World-space reserved floor cells, available even for an invalid preview."""
    x, y, width, height = item["x"], item["y"], definition["width"], definition["height"]
    rule = architecture_rule(definition)
    south = {(tx, y+height) for tx in range(x, x+width)}
    if rule == "steps_corridor":
        return {(tx, ty) for tx in range(x, x+width) for ty in range(y-1, y+height+1)}
    if rule in ("wall_backed", "hearth", "bar_return", "counter_end"):
        return south
    if rule == "bar_run":
        return south | {(tx, y-1) for tx in range(x, x+width)}
    return set()


def _geometry(data, enabled):
    from .interior_layout import partition_cells, partition_span, shell_floor_cells, shell_wall_regions
    floor = shell_floor_cells(data, enabled)
    structural = partition_cells(data, enabled)
    walls, caps = shell_wall_regions(data, enabled)
    for wall in data.get("partitions", []):
        if (wall["room_id"] not in enabled or wall["axis"] != "horizontal"
                or wall.get("thickness", 1) > 1):
            continue
        gaps = {offset for gap in wall["openings"] for offset in range(gap["offset"], gap["offset"]+gap["width"])}
        for offset, (x, y) in enumerate(partition_span(wall)):
            if offset not in gaps:
                walls[wall["room_id"]].update((x, y-d) for d in (0, 1, 2))
                caps[wall["room_id"]].add((x, y-3))
    return floor, structural, walls, caps


def architecture_rule_issues(data, enabled=None):
    """Return actionable issues without changing data or preventing old loads."""
    from .interior_architecture import architecture_cells, piece_cells
    from .interiors import placement_cells, spouse_entrance_tiles
    active = ({room["id"] for room in data["rooms"] if room["enabled"]}
              if enabled is None else set(enabled))
    definitions = {piece["id"]: piece for piece in data.get("architecture_catalog", [])}
    items = [item for item in data.get("architecture", []) if item["room_id"] in active]
    if not items:
        return []
    floor, structural, walls, caps = _geometry(data, active)
    fixtures = architecture_cells(data, active)
    furniture_definitions = {piece["id"]: piece for piece in data["catalog"]}
    furniture, furniture_blockers = set(), set()
    for item in data["furniture"]:
        definition = furniture_definitions[item["item_id"]]
        cells = placement_cells(item, definition)
        furniture.update(cells)
        if definition["kind"] != "rug":
            furniture_blockers.update(cells)
    walkable = floor - structural - fixtures - furniture_blockers
    entrances = spouse_entrance_tiles(data) if data["kind"] == "spouse" else {tuple(data["entry"])}
    reached = entrances & walkable
    queue = list(reached)
    while queue:
        x, y = queue.pop()
        for point in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
            if point in walkable and point not in reached:
                reached.add(point)
                queue.append(point)
    issues = []
    for item in items:
        definition = definitions[item["piece_id"]]
        rule = architecture_rule(definition)
        x, y, width, height = item["x"], item["y"], definition["width"], definition["height"]
        base_y = y+height-1
        name = definition["name"]
        occupied = piece_cells(item, definition)
        wall = walls[item["room_id"]]
        cap = caps[item["room_id"]]
        def issue(code, message, cells):
            if cells:
                issues.append({"placement_id": item["id"], "piece_id": item["piece_id"],
                               "code": code, "message": f"{name}: {message}",
                               "cells": [list(point) for point in sorted(cells)]})
        if rule == "steps_corridor":
            from .interior_levels import stair_connection_issues, stair_connection_clearance
            issues.extend(stair_connection_issues(data, item, definition, enabled))
            treads = {(tx, ty) for tx in range(x, x+width) for ty in range(y, y+height)}
            sides = {(tx, ty) for tx in (x-1, x+width) for ty in range(y, y+height)}
            issue("steps_floor", "place every tread on the floor of a north–south passage.", treads-floor)
            issue("steps_sides", "fit the steps between solid side walls along their full depth.",
                  {point for point in sides if not (0 <= point[0] < data["width"] and 0 <= point[1] < data["height"])
                   or point in floor and point not in structural})
            reserved = stair_connection_clearance(data, item, definition)
            issue("steps_clearance", "keep the treads and both full-width landings clear, including rugs.",
                  reserved & (structural | fixtures | furniture) | (reserved-floor))
            issue("steps_access", "keep both landings and every tread reachable from the entrance.", reserved-reached)
        elif rule == "wall_art":
            upper_wall = {point for point in wall if (point[0], point[1]+1) in wall}
            issue("wall_support", "mount the entire piece on the upper wall, above its lower trim and clear of openings.", occupied-upper_wall)
        elif rule in ("wall_backed", "hearth", "column", "counter_end"):
            base = {(tx, base_y) for tx in range(x, x+width)}
            backing = {(tx, ty) for tx in range(x, x+width) for ty in range(y, base_y)}
            support = wall | cap if rule == "hearth" else wall
            issue("wall_base", "align its bottom row with the first floor row against the wall.",
                  (base-(floor-structural)) | {(tx, base_y-1) for tx in range(x, x+width) if (tx, base_y-1) not in wall})
            issue("wall_backing", "keep its full height backed by a continuous solid wall.", backing-support)
            approach = architecture_clearance_cells(item, definition)
            issue("front_access", "leave a clear, reachable approach across its front.", approach-reached)
            if rule == "counter_end":
                left = {(x-1, ty) for ty in range(y, base_y+1)}
                supported = all(point not in floor or point in structural for point in left)
                joined = any(other["x"]+definitions[other["piece_id"]]["width"] == x
                             and other["y"]+definitions[other["piece_id"]]["height"]-1 == base_y
                             and (architecture_rule(definitions[other["piece_id"]]) == "counter_end"
                                  or other["piece_id"] in {"stardew.counter", "stardew.prep-counter", "stardew.stove", "stardew.sink"})
                             for other in items)
                issue("counter_join", "join its left edge to a compatible counter or a solid side wall.",
                      set() if supported or joined else left)
        elif rule in ("bar_run", "bar_return"):
            base = {(tx, base_y) for tx in range(x, x+width)}
            issue("bar_floor", "place the entire counter on the floor.", occupied-floor)
            approach = architecture_clearance_cells(item, definition)
            issue("bar_access", "keep the serving side and staff approach clear and reachable." if rule == "bar_run"
                  else "keep a clear, reachable approach in front of the bar end.", approach-reached)
            if rule == "bar_return":
                joined = any(other["x"] == x+width and other["y"]+definitions[other["piece_id"]]["height"]-1 == base_y
                             and architecture_rule(definitions[other["piece_id"]]) == "bar_run" for other in items)
                issue("bar_join", "attach this left end to a bar counter immediately on its right, with their bases aligned.",
                      set() if joined else {(x+width, base_y)})
    return issues


def validate_architecture_rules(data, before=None, *, enabled=None):
    """Reject new/worsened violations; permit unrelated repairs in older drafts."""
    from .interiors import InteriorError
    issues = architecture_rule_issues(data, enabled)
    if not issues:
        return
    previous = {} if before is None else {(issue["placement_id"], issue["code"]): issue
                                         for issue in architecture_rule_issues(before, enabled)}
    old_items = {} if before is None else {item["id"]: item for item in before.get("architecture", [])}
    new_items = {item["id"]: item for item in data.get("architecture", [])}
    old_definitions = {} if before is None else {item["id"]: item for item in before.get("architecture_catalog", [])}
    new_definitions = {item["id"]: item for item in data.get("architecture_catalog", [])}
    for issue in issues:
        old = previous.get((issue["placement_id"], issue["code"]))
        item = new_items[issue["placement_id"]]
        old_definition = old_definitions.get(item["piece_id"])
        definition = new_definitions[item["piece_id"]]
        unchanged_shape = old_definition is not None and all(old_definition.get(key) == definition.get(key)
                              for key in ("width", "height", "placement", "layers")) and architecture_rule(old_definition) == architecture_rule(definition)
        if (old is None or old_items.get(item["id"]) != item or not unchanged_shape
                or not {tuple(cell) for cell in issue["cells"]} <= {tuple(cell) for cell in old["cells"]}):
            raise InteriorError(issue["message"])

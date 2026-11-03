"""It's the thought that counts: item search and the four gift lists."""
from __future__ import annotations

from difflib import get_close_matches

from meetcute_core.catalog import CATEGORY_NAMES
from meetcute_core.validation import DraftValidationError, validate_nested

from ..catalog import catalog_items
from ..providers import ToolSpec
from . import ToolError, ToolOutcome


TASTES = ("love", "like", "dislike", "hate")
MAX_PER_TASTE = 40
SEARCH_SPEC = ToolSpec(
    "search_items",
    "Find items the character could be given, by part of their name. Returns up to 20 matches as "
    "'id · name · category'. Use the ids with set_gifts and the gift occasion of write_dialogue.",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Part of an item name, such as coffee or amethyst."},
            "category": {"type": ["string", "null"],
                         "description": "Optional category to narrow by, such as Fruit, Flower, Cooking, Mineral or Artisan Goods."},
        },
        "required": ["query", "category"],
        "additionalProperties": False,
    },
)
GIFTS_SPEC = ToolSpec(
    "set_gifts",
    "Replace all four gift lists at once with item ids from search_items: the gifts they love, like, dislike and hate. "
    "Each list holds up to 40 ids and an item may appear in only one list. Everything else counts as neutral.",
    {
        "type": "object",
        "properties": {taste: {"type": "array", "items": {"type": "string"}, "description": f"Item ids they {taste}."}
                       for taste in TASTES},
        "required": list(TASTES),
        "additionalProperties": False,
    },
)


def _name(item):
    return str(item.get("name", ""))


def _category(item):
    return str(item.get("category_name") or CATEGORY_NAMES.get(item.get("category"), "Other"))


def closest_names(query, items, n=3) -> list[str]:
    """Up to ``n`` '<id> · <name>' hints for a word that is not an item id."""
    by_name = {}
    for item in items:
        by_name.setdefault(_name(item), item)
    names = get_close_matches(str(query), list(by_name), n=n, cutoff=0.5)
    if not names:
        needle = str(query).casefold()
        names = [name for name in by_name if needle and needle in name.casefold()][:n]
    return [f"{by_name[name]['id']} · {name}" for name in names]


def search_items(document, input, context):
    query = str(input.get("query") or "").strip()
    if not query:
        raise ToolError("Give a word to search for.")
    category = input.get("category")
    category = category.strip().casefold() if isinstance(category, str) and category.strip() else None
    needle = query.casefold()
    matches = sorted((item for item in catalog_items(document)
                      if needle in _name(item).casefold() and (category is None or _category(item).casefold() == category)),
                     key=_name)
    if not matches:
        return ToolOutcome(document, None, f"No items match '{query}'. Try a shorter word.")
    lines = [f"{item['id']} · {_name(item)} · {_category(item)}" for item in matches[:20]]
    if len(matches) > 20:
        lines.append(f"…and {len(matches) - 20} more. Narrow the search.")
    return ToolOutcome(document, None, "\n".join(lines))


def set_gifts(document, input, context):
    items = catalog_items(document)
    known = {item["id"] for item in items}
    lists, owner = {}, {}
    for taste in TASTES:
        ids = input.get(taste)
        if not isinstance(ids, list) or len(ids) > MAX_PER_TASTE or any(not isinstance(item, str) for item in ids):
            raise ToolError(f"The {taste} list must hold up to {MAX_PER_TASTE} item ids.")
        cleaned = []
        for item_id in ids:
            item_id = item_id.strip()
            if item_id not in known:
                closest = closest_names(item_id, items)
                hint = f" Closest items: {', '.join(closest)}" if closest else " Use search_items to find ids."
                raise ToolError(f"'{item_id}' isn't an item id.{hint}")
            if owner.get(item_id, taste) != taste:
                raise ToolError(f"'{item_id}' appears in more than one list.")
            owner[item_id] = taste
            if item_id not in cleaned:
                cleaned.append(item_id)
        lists[taste] = cleaned
    try:
        validated = validate_nested("gifts", lists)
    except DraftValidationError as exc:
        raise ToolError("; ".join(exc.errors.values())) from None
    document["character"]["gifts"] = validated
    detail = (f"{len(validated['love'])} loved · {len(validated['like'])} liked · "
              f"{len(validated['dislike'])} disliked · {len(validated['hate'])} hated")
    receipt = {"section": "gifts", "title": "Updated Gifts", "detail": detail}
    return ToolOutcome(document, receipt, f"Done. {detail}.")


TOOLS = ((SEARCH_SPEC, search_items), (GIFTS_SPEC, set_gifts))

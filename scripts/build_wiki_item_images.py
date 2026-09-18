#!/usr/bin/env python3
"""Build factual item → original wiki image URL metadata; never download artwork.

The springobjects table is joined by exact sprite index (not item display name),
which distinguishes brown/white eggs and the two Strange Dolls. New Objects_2
items use wiki file names checked by the MediaWiki imageinfo API. The few naming
exceptions below were verified on the relevant wiki item pages.

Run from any directory with Python 3.10+. Only the generated JSON enters the
repository; optional --metadata-cache holds HTML/API responses, not images.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
HOST = "https://stardewvalleywiki.com"
TABLE_URL = f"{HOST}/Modding:Objects/Object_sprites"
TABLE_REVISION = 183077
API_URL = f"{HOST}/mediawiki/api.php"
PINNED_TABLE_URL = f"{API_URL}?" + urlencode({
    "action": "parse", "format": "json", "oldid": str(TABLE_REVISION), "prop": "text",
})
USER_AGENT = "Pixelheart/0.1 (item image metadata generator; no artwork redistribution)"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024

# Naming aliases verified against each item's wiki page and the imageinfo API.
# Preserves and targeted bait are base representations; instance colors and
# fish/fruit overlays are not inferred from a base object ID.
FILE_OVERRIDES = {
    "DriedFruit": "Dried Fruit.png",
    "DriedMushrooms": "Dried Mushrooms.png",
    "SmokedFish": "Smoked Fish.png",
    "SpecificBait": "Pink Bait.png",
    "Book_Horse": "Horse The Book.png",
    "Book_WildSeeds": "Ways Of The Wild.png",
}
UNAVAILABLE = {
    "925": "No corresponding static image was found on the wiki.",
    "927": "No corresponding static image was found on the wiki.",
    "929": "No corresponding static image was found on the wiki.",
    "GoldCoin": "No corresponding static image was found on the wiki.",
    "PetLicense": "No corresponding static image was found on the wiki.",
    "SeedSpot": "The wiki provides an animated GIF, not a static PNG.",
}


class SpriteTableParser(HTMLParser):
    """Pair each 24-column image row with its explicitly numbered index row."""

    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self.row = []
        elif tag in ("th", "td"):
            self.cell = {"text": "", "images": []}
        elif tag == "img" and self.cell is not None:
            self.cell["images"].append(attrs)

    def handle_data(self, text):
        if self.cell is not None:
            self.cell["text"] += text

    def handle_endtag(self, tag):
        if tag in ("th", "td") and self.cell is not None:
            if self.row is not None:
                self.row.append(self.cell)
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None

    def sprite_files(self):
        files = {}
        indices = set()
        for position, row in enumerate(self.rows):
            if len(row) != 24 or not all(cell["text"].strip().isdigit() for cell in row):
                continue
            if position == 0 or len(self.rows[position - 1]) != len(row):
                raise ValueError("The wiki sprite table has an unexpected row structure.")
            for id_cell, image_cell in zip(row, self.rows[position - 1]):
                index = int(id_cell["text"].strip())
                if index in indices:
                    raise ValueError(f"Duplicate wiki sprite index: {index}")
                indices.add(index)
                images = image_cell["images"]
                if len(images) == 1:
                    filename = images[0].get("alt", "")
                    if not filename.endswith(".png"):
                        raise ValueError(f"Unexpected sprite image filename: {filename}")
                    files[index] = filename
                elif len(images) > 1:
                    raise ValueError(f"Ambiguous wiki sprite index: {index}")
        if indices != set(range(936)):
            raise ValueError("The pinned wiki sprite table no longer has indices 0–935.")
        return files


def metadata_bytes(url, cache_dir=None):
    cache_path = None
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()}.metadata"
        if cache_path.exists():
            content = cache_path.read_bytes()
            if len(content) > MAX_RESPONSE_BYTES:
                raise ValueError("Cached metadata response is too large.")
            return content
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=30) as response:
        content = response.read(MAX_RESPONSE_BYTES + 1)
    if len(content) > MAX_RESPONSE_BYTES:
        raise ValueError("Wiki metadata response is too large.")
    if cache_path is not None:
        cache_path.write_bytes(content)
    return content


def verified_images(filenames, cache_dir=None):
    files = sorted(set(filenames))
    verified = {}
    for start in range(0, len(files), 50):
        batch = files[start:start + 50]
        query = urlencode({
            "action": "query", "format": "json", "prop": "imageinfo",
            "iiprop": "url|size", "titles": "|".join("File:" + name for name in batch),
        })
        data = json.loads(metadata_bytes(f"{API_URL}?{query}", cache_dir))
        if "error" in data:
            raise ValueError(f"Wiki imageinfo request failed: {data['error']}")
        for page in data["query"]["pages"].values():
            if not page.get("imageinfo"):
                continue
            info = page["imageinfo"][0]
            url = urlsplit(info["url"])
            if (url.scheme != "https" or url.netloc != "stardewvalleywiki.com"
                    or not url.path.startswith("/mediawiki/images/")
                    or not url.path.endswith(".png") or url.query or url.fragment):
                raise ValueError(f"Unexpected wiki image URL: {info['url']}")
            if not (0 < info["width"] <= 256 and 0 < info["height"] <= 256):
                raise ValueError(f"Unexpected icon dimensions: {page['title']}")
            verified[page["title"].removeprefix("File:")] = {
                "url": info["url"], "source_url": info["descriptionurl"],
            }
    missing = set(files) - verified.keys()
    if missing:
        raise ValueError(f"Wiki files are missing; review before updating: {sorted(missing)}")
    return verified


def build(catalog, cache_dir=None):
    parser = SpriteTableParser()
    table = json.loads(metadata_bytes(PINNED_TABLE_URL, cache_dir))
    if table["parse"]["revid"] != TABLE_REVISION:
        raise ValueError("The wiki returned the wrong sprite table revision.")
    parser.feed(table["parse"]["text"]["*"])
    sprite_files = parser.sprite_files()
    filenames = {}
    unavailable = []
    for item in sorted(catalog["items"], key=lambda value: value["id"]):
        item_id = item["id"]
        if item_id in UNAVAILABLE:
            unavailable.append({"id": item_id, "name": item["name"], "reason": UNAVAILABLE[item_id]})
            continue
        icon = item["icon"]
        if icon["texture"] == "Maps/springobjects":
            filename = sprite_files.get(icon["index"])
        elif icon["texture"] == "TileSheets/Objects_2":
            filename = FILE_OVERRIDES.get(item_id, item["name"] + ".png")
        else:
            raise ValueError(f"Unexpected vanilla icon texture: {icon['texture']}")
        if not filename:
            raise ValueError(f"Wiki table has no image for {item_id} ({item['name']}).")
        filenames[item_id] = filename
    verified = verified_images(filenames.values(), cache_dir)
    return {
        "format": "pixelheart-wiki-item-images", "version": 1,
        "source_url": TABLE_URL, "source_revision": TABLE_REVISION,
        "attribution": "Item artwork by ConcernedApe; image links from Stardew Valley Wiki. No image bytes are bundled.",
        "items": {item_id: verified[filename] for item_id, filename in filenames.items()},
        "unavailable": unavailable,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "pixelheart_core/data/vanilla_items.json")
    parser.add_argument("--output", type=Path, default=ROOT / "pixelheart_core/data/wiki_item_images.json")
    parser.add_argument("--metadata-cache", type=Path, help="Optional directory for HTML/API metadata responses; artwork is never fetched.")
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    result = build(catalog, args.metadata_cache)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {len(result['items'])} PNG links; {len(result['unavailable'])} catalog entries have no static wiki PNG.")


if __name__ == "__main__":
    main()

# Bundled vanilla object catalog

`vanilla_items.json` is an offline, deterministic catalog for Stardew Valley
**1.6.15**. It contains **663 object gift candidates**, derived from two
independently published extractions containing the same **807 object IDs**.
It is not a claim that every row can be obtained and gifted in every game state.

Only factual identifiers, short English item names, category numbers, sprite
asset keys/indexes, and Pixelheart's category filter labels are bundled. No sprites, descriptions,
dialogue, textures, or game implementation are redistributed in this catalog.
The application does not contact these sources at runtime.

## Pinned input sources

| Input | Immutable source | SHA-256 of downloaded bytes |
| --- | --- | --- |
| Object metadata | [juliaramosguedes/stardew-data, `data/en-US/objects.json`](https://raw.githubusercontent.com/juliaramosguedes/stardew-data/4e0d98119afefd766f15ee77a529db4eb71fa240/data/en-US/objects.json) | `9b4b1d3f574feb68207a9c55fdce10cbcbe3518f706db2946905f65369c37635` |
| English display names | [MateusAquino/stardewids, `dist/objects.json`](https://raw.githubusercontent.com/MateusAquino/stardewids/93a96574eadbc5b6f95dfb5f8b1f8d9cf68ae441/dist/objects.json) | `8b3068d79e60afa5e452b7f2c59e2b57d4e01b7bd9c35dae75d876d8ac0eaf05` |

The metadata source's `_meta.gameVersion` is `1.6.15`; its published
[`parseObjectEntry`](https://github.com/juliaramosguedes/stardew-data/blob/4e0d98119afefd766f15ee77a529db4eb71fa240/scripts/parsers/objects.ts)
extracts `Category`, `Type`, `CanBeGivenAsGift`, `ContextTags`, `Texture`, and `SpriteIndex` directly from
unpacked `Data/Objects`. Its names occasionally fall back to internal names,
so display names are joined by ID from the second source. That source's
[README](https://github.com/MateusAquino/stardewids/blob/93a96574eadbc5b6f95dfb5f8b1f8d9cf68ae441/README.md)
identifies the snapshot as the 1.6.15 update. The matching commit subject has a
version typo (`1.16.5`); the README and matching ID sets establish the intended
version.

### Attribution and rights

Stardew Valley and its original content are by ConcernedApe. Both source
projects are independent community tools, not official releases. The
`stardewids` package declares ISC; the `stardew-data` snapshot identifies its
data as extracted from Stardew Valley and does not grant a separate asset
license. Those declarations are not treated as permission to redistribute
game artwork or creative text. Pixelheart retains only the factual metadata
listed above and uses its own generation code and category labels.

### Local item icons

`wiki_item_images.json` contains verified Stardew Valley Wiki image links for
657 vanilla entries. The running desktop app downloads missing icons to the
OS user cache (`Pixelheart/wiki-items`) outside all repositories. Cached icons
work offline; images are never copied into project files or exported packs.
Local texture imports take precedence over wiki previews. The source metadata
records exact file attribution links and entries without a verified static PNG.

Each catalog item's optional `icon` record stores a relative game asset key and
16-by-16 sprite index. These are coordinates, not artwork. Missing `Texture`
values in the pinned data map to the game's default `Maps/springobjects`; the
other vanilla sheet is `TileSheets/Objects_2`. Indexes come directly from the
source, including objects whose IDs are not numeric.

Users can export these textures through Content Patcher's supported commands:

```text
patch export "Maps/springobjects" image
patch export "TileSheets/Objects_2" image
```

The [Content Patcher export implementation](https://github.com/Pathoschild/StardewMods/blob/develop/ContentPatcher/Framework/Commands/Commands/ExportCommand.cs)
accepts the `image` alias and saves PNGs with slash separators replaced by
underscores in its `patch export` folder. Pixelheart can import matching PNGs
from a user-selected folder, including additional textures named by imported
mod object records. Texture pixels are cached in Qt's app-local data directory
under `item-icons`, outside projects and exported mod packs. Updating an asset
replaces that asset's cached art for all projects on this computer. A project
opened on another computer downloads available vanilla wiki icons; mod-specific
textures need to be imported there. Missing images use initial badges.
Badges are UI placeholders; Pixelheart does not generate imitation game art.

Only base object sprites are shown. Instance colors, flavored-object overlays,
and runtime rendering changes are outside this snapshot.

## Eligibility and scope

The generator includes every object row except these established exclusions:

1. `CanBeGivenAsGift` is false, or `ContextTags` includes `not_giftable`.
   The pinned data has six explicit false rows: Wedding Ring (`801`), Pierre's
   Missing Stocklist (`897`), Horse Flute (`911`), Far Away Stone (`FarAwayStone`),
   Calico Egg (`CalicoEgg`), and Prize Ticket (`PrizeTicket`). There are no
   explicit `not_giftable` tags in this snapshot.
2. `Type` is `Ring` or an `item_type_ring` context tag is present. These rows
   instantiate equipment rather than ordinary inventory objects.
3. Category `-999`, representing world litter such as stones and weeds that
   normally exist on the map instead of in the player's inventory.
4. Wilted Bouquet (`277`), Bouquet (`458`), Mermaid's Pendant (`460`), and Movie
   Ticket (`809`). These enter dedicated NPC interaction logic before normal
   gift-taste processing.
5. Eleven known vanilla quest hand-ins: Ornate Necklace (`191`), Lost Axe
   (`788`), Lucky Purple Shorts (`789`), Berry Basket (`790`), War Memento
   (`864`), Gourmet Tomato Salt (`865`), Stardew Valley Rose (`866`), Advanced
   TV Remote (`867`), Arctic Shard (`868`), Wriggling Worm (`869`), and Pirate's
   Locket (`870`). The [quest item documentation](https://wiki.stardewvalley.net/Quests#List_of_Quest_Items)
   identifies the quest-only objects; the [Ornate Necklace documentation](https://wiki.stardewvalley.net/Ornate_Necklace)
   describes its special hand-in to Caroline or Abigail. This curated exclusion
   applies only to the bundled vanilla catalog. Local game imports may include
   these IDs because mods can repurpose their definitions.

These rules are based on the documented
[object data fields](https://wiki.stardewvalley.net/Modding:Objects),
[context tags](https://wiki.stardewvalley.net/Modding:Context_tags), and
[item categories](https://wiki.stardewvalley.net/Modding:Items#Categories).
The game's 1.6 behavior was also inspected via the public reference for
[`Object.canBeGivenAsGift`](https://github.com/Dannode36/StardewValleyDecompiled/blob/5225ef409e42a6159a82cf81200bf6eb315c9961/Stardew%20Valley/StardewValley/Object.cs),
[`ObjectDataDefinition.CreateItem`](https://github.com/Dannode36/StardewValleyDecompiled/blob/5225ef409e42a6159a82cf81200bf6eb315c9961/Stardew%20Valley/StardewValley.ItemTypeDefinitions/ObjectDataDefinition.cs),
and [`NPC.tryToReceiveActiveObject`](https://github.com/Dannode36/StardewValleyDecompiled/blob/5225ef409e42a6159a82cf81200bf6eb315c9961/Stardew%20Valley/StardewValley/NPC.cs).
That code reference predates 1.6.15; the catalog's data itself is pinned to
1.6.15, and the release still needs in-game verification.

`Type: Quest` is **not** an unconditional exclusion: Golden Coconut (`791`)
has that type and [supports ordinary gifting](https://wiki.stardewvalley.net/Golden_Coconut#Gifting).
The per-instance `questItem` flag is
separate from `Type` and cannot be inferred from this data export. The catalog
therefore includes a warning that some quest or unused definitions may not be
available for ordinary gifting. It deliberately does not invent an
obtainability rule from price, edibility, shipping eligibility, or item names.

Other boundaries:

- This is the `(O)` object catalog. Giftable trinkets and other item types
  require additional datasets and qualified-ID support; they are not included.
- Different qualities and flavored preserves share the base object ID and
  are not separate rows. For example, a Wine assignment addresses the base
  object, not one particular fruit's wine.
- Conditional NPC rejection dialogue, relationship interactions, quest
  instances, and mod code can affect gift acceptance independently of tastes.
- The catalog does not encode universal or character-specific gift tastes.
  Leaving an item unassigned allows the game's own default rules to apply.

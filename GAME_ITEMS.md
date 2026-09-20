# Gift catalogs and local game imports

The Gifts page starts with a bundled **Stardew Valley 1.6.15 object catalog**.
Search by name or ID, filter by item type, then drag icon tiles into Love, Like,
Dislike, or Hate. For example, search for **Tulip**, then drag its tile onto the
**Love** panel to make it a personal loved gift. The whole panel accepts drops,
including its heading, and highlights when ready to receive the item.
You can also select items and use the category selector and Assign button.
Dragging between tastes moves the assignment; dragging back to
the catalog, Reset selected, or Delete/Backspace restores the game default.
Unassigned items are not forced to Neutral.

## Game defaults are shown automatically

Love, Like, Dislike, and Hate show the inherited **vanilla 1.6.15 tastes** as soon
as you open Gifts. Items marked **Default** are the starting preferences;
your personal choices appear first. Drag a default item into another taste to
override it. **Reset selected**, Delete/Backspace, or dragging back to the catalog
removes the override and returns the item to its default taste.

**Show game defaults** hides or reveals inherited items without editing the
character. Neutral items remain in the catalog. The lists use item exceptions,
category rules, and the game's price/edibility fallback, rather than assuming
every item in a category has the same taste. Stardrop Tea has special gift
behavior and is not assigned an ordinary default taste here.

Only personal choices are saved and exported; inherited defaults keep using the
game's rules. This also preserves existing projects and lets the character
creator supply its themed preferences. When you import a modded object catalog,
the displayed defaults remain a vanilla reference for recognized item IDs;
that import does not contain modded gift rules, and new mod items are not given
guessed defaults. See the [gift taste data and precedence rules](https://stardewvalleywiki.com/Modding:Gift_taste_data).

## Start with a preset

**Everyday favorites** is selected by default at the top of Gifts. Choose
**Apply preset** to add a small starting set, or pick **Botanist**, **Baker**,
**Angler**, **Miner**, or **Artist**. Each preset has 11 gifts across Love, Like,
Dislike, and Hate. **Preview gifts** shows the choices before applying them;
browsing and previewing do not change your character.

Presets add personal choices for items that still use their inherited defaults.
Your existing personal choices, including mod items, stay in their current
categories. Items missing from an imported catalog are
skipped and counted. **Undo preset** restores the previous gift choices until
you make another gift edit, load a project, or change catalogs. You can then
drag or assign individual gifts as usual. Applied gifts save and export as
ordinary item preferences; no preset dependency is added to the NPC pack.

These are original authoring suggestions, not copies of an existing NPC's
tastes or the game's universal rules. Opening an existing project shows its
inherited defaults without adding or changing saved preferences.

Selections use stable object IDs, including nonnumeric vanilla 1.6 IDs and
mod-provided IDs. Duplicate names remain distinct items; hover to see the ID.
Recognized legacy names migrate to IDs. Unknown saved references are retained
and marked for review instead of disappearing when a catalog changes.

## Load from my game

1. Start Stardew Valley **1.6** through **SMAPI** with **Content Patcher** and
   the mods you want to use.
2. Load the intended save. Some mods add or change items based on the save,
   season, configuration, or other conditions.
3. In Pixelheart's Gifts page, select **Load from my game…** and copy the command:

   ```text
   patch export Data/Objects
   ```

4. Paste it into the SMAPI console and press Enter. Content Patcher prints the
   resulting path, normally `<game folder>/patch export/Data_Objects.json`.
5. Choose **Choose exported JSON…** and select that file, or use **Find in game
   folder…** to locate it within your selected game directory. If needed, the
   SMAPI command `show_game_files` opens the game directory.

The imported catalog replaces the vanilla catalog for that project. It includes
the currently loaded `Data/Objects` asset after mod patches, rather than guesses
from scanning mod source files. **Save project** keeps a normalized snapshot
inside `character.json`; Save As carries it along. The original export is not
needed to reopen the project. No game artwork or item descriptions are copied.

Run the command again and use Load from my game to refresh after changing mods
or game conditions. **Use vanilla catalog** switches back without deleting gift
assignments. Importing or cancelling never edits the game, installs a mod, or
launches an executable. Pixelheart does not keep a live connection to the game.

## Original item icons

Opening Gifts in the desktop app automatically loads missing vanilla item icons
from **Stardew Valley Wiki** in the background. Downloaded PNGs are kept in the
operating system's `Pixelheart/wiki-items` cache, outside the repository and
project folders. Once cached, they work offline. The repository includes only
verified source links; neither projects nor exported NPC packs contain these icons.

Every bundled gift has a verified static PNG source. Missing icons keep labeled
badges until downloaded. A failed download does not interrupt editing;
**Retry icons** retries missing files. Source artwork
is © ConcernedApe and credited to Stardew Valley Wiki in the editor.

For modded artwork or texture replacements, choose **Use local textures…**:

1. Select **Use local textures…** in Gifts.
2. Run the listed texture commands in the SMAPI console, one line at a time.
   For the base object atlas the command is `patch export "Maps/springobjects" image`.
   Modded catalogs may list additional textures.
3. Choose the game's **patch export** folder. Pixelheart reads the exported PNGs
   and uses each object's exact texture and sprite index.

Locally imported sprites take precedence over wiki images and are cached
separately from projects. The
cache is shared by texture name; refreshing a texture updates its previews in
projects using that name. Reimport after changing texture mods or game conditions.
Project files retain sprite references, but game textures are not bundled in the
project or exported NPC pack. Missing or invalid textures keep the labeled badges.
No game artwork is shipped with Pixelheart or written into its repository.

## Scope

- This is an **object-data snapshot**, not a complete live item registry.
  Trinkets, other item types, and dynamically flavored variants are outside
  this import. Game code, instance quest flags, or tags in other assets can
  further restrict giftability. The catalog does not guarantee that every
  candidate can be obtained or given to every NPC in every state.
- Known non-giftable flags, `not_giftable` tags, rings, world litter, and the
  dedicated bouquet/proposal/movie-ticket interactions are excluded.
- The bundled vanilla catalog also excludes known quest-only objects, internal
  props, world objects, and rewards that never stay in inventory. These include
  the `???` heart pickup, Pet License, SupplyCrates, digging spots, Golden
  Walnuts, Qi Gems, and Stardrops. Imported game catalogs retain those
  definitions when eligible because mods can repurpose them.
- Existing assignments to removed catalog items remain saved and are marked
  **not in catalog** for review. Select them and choose **Reset selected** to
  remove an old assignment.
- Unresolved game translation tokens fall back to the object's internal name.
- Item data does not identify which mod owns an item. Pixelheart preserves its
  exact ID but does not invent a mod dependency. When sharing a pack that uses
  mod items, declare the required mods in the pack's manifest and installation
  instructions. Importing an item does not bundle or recreate that item.
- The 637-entry default catalog is pinned to 1.6.15. Its inputs, checksums,
  filtering rules, and limitations are recorded in the
  [catalog provenance](pixelheart_core/data/README.md).

## Import limits

Imports must be UTF-8 JSON. Invalid IDs, duplicate keys, unsupported structures,
invalid Unicode, and oversized inputs are rejected before replacing the current
catalog. Gift lists support up to 5,000 assignments per taste. Check modded items
in the intended game before sharing the pack.

References:

- [Content Patcher export documentation](https://github.com/Pathoschild/StardewMods/blob/develop/ContentPatcher/docs/author-guide/troubleshooting.md#export)
- [Content Patcher export implementation](https://github.com/Pathoschild/StardewMods/blob/develop/ContentPatcher/Framework/Commands/Commands/ExportCommand.cs)
- [Object data fields](https://wiki.stardewvalley.net/Modding:Objects)
- [Gift taste rules](https://wiki.stardewvalley.net/Modding:Gift_taste_data)

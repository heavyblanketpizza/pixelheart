# Build the people and places around the story

Open **Cast & locations** to add supporting characters, places, a spouse room,
and required external mods. These become real content in the primary character's
pack. Save the project before importing files so all assets can travel together.

## Supporting cast

Choose **Add companion** in **Supporting cast**. Give the character a name and
unique Character ID, choose their age, birthday, and home, and import complete
portrait and sprite sheets. Write their dialogue, route, and gift preferences in
the same detail pane. Each companion needs their own selected artwork; a story
description alone does not supply it.

The main character stays adult. Supporting characters can be Adult, Teen, or
Child; teens and children are always nonromanceable. Supporting adult romance
is optional and requires the complete romance sprite layout.

Select the companion by name in a scene's cast and beat controls. The stored
reference uses a stable companion identity, so a name edit does not detach the
scene. Removing a referenced companion produces an export error; revise the
cast and beats that referred to them. Keep Character IDs stable after publishing
or using the pack in a save, since an ID change changes the game's NPC identity.

A relationship arc records how the connection develops through scenes. Its
friendship effects change the player's friendship with the chosen NPC; they do
not create a private family or friendship simulation between two companions.

## Create or import a place

In **Places & spouse room**, choose **Add place**, then give it a display name
and unique **Stable map ID**. That place appears by name in location selectors.

- **Create map from a tilesheet…** opens the simple painter for a 20×20 location
  or 6×9 spouse room, using your own 16×16 PNG tiles. **Edit painted map…** reopens
  those layers without changing earlier revisions. See [MAP_WORKSHOP.md](MAP_WORKSHOP.md).
- **Import Tiled map…** copies a supplied finite, orthogonal TMX map. It must use
  16×16 tiles and contain Back, Buildings, and Front layers. Keep all referenced
  TSX files and PNG sheets in its folder or subfolders. External references,
  parent traversal, and missing dependencies are rejected.

The map and its dependencies are copied together into `world_assets/maps/`.
The exporter preserves their bytes, and Save As copies the complete bundle.
The preview draws supplied tile artwork. It cannot certify collision, actions,
or walking paths. Rich imported maps that the simple painter cannot preserve
must be edited in Tiled and imported again.

## Connect the entrance and exit

Complete **Give the player a way in and out** for every standalone place:

1. Select the outside **Entrance map** and the X/Y tile that triggers entry.
2. Set the X/Y tile where the player arrives inside your place.
3. Set a different inside tile that triggers the exit.
4. Set the outside return tile, different from the entry trigger.

The export creates the location and both game warps. Other project locations can
be connected, provided the chain eventually leads to an existing outside map.
Closed cycles and duplicate entrance tiles are rejected. Arriving directly on
the reverse warp is also rejected to prevent an immediate return.

Check that all four tiles are walkable and reachable in the game. Imported map
dimensions are checked for entrances, homes, schedule stops, enabled life
routines, and ready scene actors and movement. Coordinates inside the rectangle
can still be walls, furniture, or unreachable spaces.

Keep Stable map IDs unchanged after using a pack in a save. Display names can
change without changing the authored map's identity.

## Give the spouse a room

Select **Use a section as the primary character's spouse room**, then choose the
top-left X/Y tile of a 6×9 section. The supplied map must contain the whole section.
The 6×9 painter preset selects X 0, Y 0 automatically. Only one primary spouse
room is supported, and the primary character must have romance enabled.

The game places this section in the farmhouse after marriage. It is not a
standalone location and should not be selected as a home, schedule, or event
destination. Use the game's farmhouse destination for married scenes and the
married routine's **Return to farmhouse** control for the spouse's return home.
See [LIFE_AND_BRANCHING.md](LIFE_AND_BRANCHING.md) for conversations and routines.

## Declare other mods

In **Mod dependencies**, use the UniqueID from another mod's `manifest.json`,
optionally require a minimum version, and specify whether the dependency is
required. Include mods that provide external maps, NPC actors, or modded items
your content needs. Declaring a dependency does not download or bundle that mod.

All packs need Stardew Valley 1.6, SMAPI 4+, and Content Patcher 2.9.0+.
Ready Daily scenes automatically add Event Repeater 6.5.8+ as a required
dependency. The installer can report missing requirements; install the external
frameworks separately and check SMAPI's log before playtesting.

## Export, test, and revise

Resolve the export review's errors, then export a ZIP or use the creator journey
to install in a selected Mods folder. Pixelheart updates only folders it owns
with a matching pack identity. The previous version is backed up under
`Pixelheart backups` beside Mods, outside the active mod directory. A manually
installed or unrelated folder is preserved rather than overwritten.

The archive includes `WORLD_TESTING.txt` with exact character IDs, home tiles,
map IDs, warp coordinates, and spouse-room information. `STORY_TESTING.txt`
covers ready primary and supporting scenes, their triggers, answer outcomes,
and repeat rules. Test on a backed-up save: meet each character, sleep and follow
routes, use both directions of every entrance, play every scene and answer, and
check the farmhouse after marriage. Record results in the creator journey,
revise the same records, and export/install again.

Desktop exports contain a portable `project.json` with the creator brief,
chapter plan, test records, catalog, all drafts, and references to the included
selected artwork and map bundles. Extract the entire pack before reopening it.
Keep the working project too: it retains original/prepared artwork versions
that were not selected for the export.

References: [Content Patcher map edits](https://github.com/Pathoschild/StardewMods/blob/stable/ContentPatcher/docs/author-guide/action-editmap.md),
[game location data](https://stardewvalleywiki.com/Modding:Location_data), and
[NPC spouse-room fields](https://stardewvalleywiki.com/Modding:NPC_data).

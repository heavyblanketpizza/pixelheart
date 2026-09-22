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

For an NPC home, start with **Build a home…** in **Places & spouse room**. To
import a map or create another kind of place, open **Advanced / Game connection**,
choose **Add place**, and give it a display name and unique **Stable map ID**.
That place appears by name in location selectors. The advanced section contains
the map tools:

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

## Design an interior

In **Places & spouse room**, choose **Build a home…** to start decorating a new
home for your character. Pixelheart names the place and creates its game ID for
you. Saving the design assigns it as their residence; cancelling leaves no
empty place behind. Choose **Design spouse room…** for their farmhouse room.
If a spouse room already exists, that action reopens it.

Select an existing place and **Design interior…** or **Edit interior…** to work
on it again. The spouse canvas stays 6×9 tiles. A residence can contain connected
floor regions and up to four optional rooms.

The room stays visible beside a picture catalogue. Start with **Connect game
library…** to bring in furniture, wallpaper, and flooring from your installed
game and mods. On first use, the setup explains how to install the companion,
open a save so it can prepare the library, and choose your game folder.
Pixelheart contains no bundled Stardew artwork: before the library is connected,
you can plan rooms in a neutral layout guide, but the real decorating catalogue
is unavailable.

- **Furnish:** browse pictures by category, search by name, or revisit favorites
  and recent choices. Click a piece to pick it up, move its preview around the
  room, and click to place it. The placement highlight shows whether it fits.
  Right-click to rotate a held piece; Escape puts it down without placing it.
  Choose **Move furniture**, then click or drag an existing piece. The selection
  controls let you rotate, duplicate, or remove it. Rugs can sit under furniture.
- **Walls & floors:** pick a wallpaper or flooring swatch, then click a room to
  decorate it. **Apply to every room** gives the whole home the same finish.
- **Rooms:** choose a room, pick a small, medium, or large addition, and attach it
  to the left, right, or below. **Draw a room…** lets you drag out a connected
  rectangular addition instead. Remove empty rooms or move the marked doorway
  directly on the canvas. Optional additions can also be offered to the player
  in-game. Spouse rooms keep their fixed size and a marked standing spot for
  the NPC. Existing homes keep their map coordinates: an addition on the left
  needs enough free space, so saved routes and doorways stay in place.

Use **Undo** and **Redo** while experimenting. **Grid**, zoom, and **Fit room**
help with placement, and **Play** previews available animations. The layout
protects occupied rooms, the doorway, and the spouse's standing spot.

**Advanced…** holds custom tilesheet imports, exact dimensions, furniture item
details, raw library imports, and tile animation authoring. These are not needed
for ordinary decorating with a connected library. Each placed piece retains its
actual game item ID and mod metadata. Tabletop arrangements and arbitrary custom
drawing effects are not simulated by the Python preview; installed items and
mods supply their behavior in-game. Exported map animations require equal frame
durations.

The room-and-catalogue workflow draws on the visible interactions in
[Happy Home Designer's gallery](https://www.nexusmods.com/stardewvalley/mods/19675?tab=images)
and [Nintendo's decorating manual](https://www.nintendo.com/eu/media/downloads/games_8/emanuals/nintendo_3ds_2/animal_crossing__happy_home_designer/ElectronicManual_Nintendo3DS_AnimalCrossingHappyHomeDesigner_EN.pdf).
Spouse rooms use the same finish-and-furniture approach demonstrated by
[Spouse Room Renovation](https://www.nexusmods.com/stardewvalley/mods/23529).

Imports are staged until **Save home** or **Save room**. Cancel discards edits and
staged assets. Undo and redo work across room, furniture, and surface edits.
Save As carries the private tilesheets and preview textures with the project.
**Set as their residence** updates the character's home; review their authored
schedules separately. Saving an interior replaces that place's imported map.

Designed interiors require the separate **Pixelheart Interiors** SMAPI companion,
Stardew Valley 1.6.9+, SMAPI 4.1+, and Content Patcher. The companion source is in
`runtime/Pixelheart.Interiors`; it is not a bundled, verified binary. On a
development machine with the .NET SDK and Stardew/SMAPI installed, build with:

```sh
dotnet build runtime/Pixelheart.Interiors/Pixelheart.Interiors.csproj -c Release -p:GamePath="/path/to/Stardew Valley/game-folder"
```

Build output contains the companion DLL and its manifest. Install those together
in a separate `Pixelheart Interiors` folder under Mods, alongside the exported
NPC pack. The Python exporter declares this dependency; it does not install or
download the companion automatically.

After a save loads, the companion automatically prepares a private library of
furniture and finishes in its `cache/library` folder. **Connect game library…**
finds it from your game folder, Mods folder, or the library folder itself, then
remembers the connection. After changing installed mods, restart the game, open
a save, and choose **Refresh game library…** in Pixelheart. The library reflects
default appearances; it does not enumerate every texture variant or custom
drawing effect. Record required provider mods in the item's advanced details
where needed.

For troubleshooting or a separate snapshot, `pixelheart_export_furniture` in
the SMAPI console writes a library to the companion's `exports` folder. You can
select its `library.json` using **I already have a library file…** during setup,
or **Advanced… → Import library file…**.

The companion initializes successful furniture placements once per save and
keeps those records when players move or collect items. Missing items or blocked
placements are deferred, never substituted or overwritten. Use
`pixelheart_interiors_retry` after resolving a reported obstruction.
Press **F8** inside a designed residence for optional room changes. Removal
refuses occupied rooms and protected home/schedule coordinates. Structural room
changes currently require single-player. Spouse rooms use their actual farmhouse
position, detected from an exported marker, rather than a fixed world coordinate.

The companion still requires compilation against an installed game and live
acceptance testing. Verify furniture callbacks, native wallpaper/floor regions,
initial surface appearance, spouse positioning, map reloads, and save/reload
before treating an exported design as ready for play.

## Connect the entrance and exit

Open **Advanced / Game connection** and complete **Give the player a way in and
out** for every standalone place:

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

Choose **Design spouse room…** to decorate the fixed room directly. Only one
primary spouse room is supported, and the primary character must have romance
enabled.

For a supplied map, open **Advanced / Game connection**, select **Use a section
as the primary character's spouse room**, and choose the top-left X/Y tile of a
6×9 section. The map must contain the whole section. The 6×9 painter preset
selects X 0, Y 0 automatically.

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

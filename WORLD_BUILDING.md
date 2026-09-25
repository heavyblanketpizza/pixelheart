# Give your character a home and places for their story

Open **Home & places** to design the loaded NPC's residence and spouse room.
Save the project before importing files so the assets travel together. Use **+ Story place** for additional locations. External mod dependencies and
import settings are under **Map tools & dependencies**.

Each project authors one custom NPC. Scene casts can include townspeople and
other installed characters. Older projects retain any previously bundled
supporting characters and their assets; this page no longer creates or edits
separate NPC projects inside the current one. For older projects only, **Map tools & dependencies → Legacy bundled characters…** can repair missing artwork or remove an old bundled character after confirmation. Review scenes that referenced a removed character before exporting.

## Create or import a place

For an NPC home, start with **Build residence…**. To
import a map or create another kind of place, choose **+ Story place** and give
it a display name. **Map tools & dependencies** contains the **Stable map ID**
and import tools. Renaming an ID updates references in this project; keep IDs
stable after using the pack in a game save. Places appear by name in location selectors:

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

In **Home & places**, choose **Build residence…** to start decorating a new
home for your character. Pixelheart names the place and creates its game ID for
you. **Apply home** assigns the design as their residence; cancelling leaves no
empty place behind. Choose **Design spouse room…** for their farmhouse room.
Once a place exists, select it and use its **Edit interior…** action. The creation
actions are hidden when that residence or spouse room already exists.

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
- **Rooms:** choose a small, medium, or large size and drag the room preview onto
  the layout. Drop beside another room when the outline turns green. Grab an
  existing room on the canvas to move it with its furniture and doorway;
  nearby edges snap together. Select a room to reveal its eight resize handles.
  Drag an edge or corner to resize while its contents stay in place. Escape
  cancels a gesture, and Undo restores the previous layout.

Under **Rooms & hallways**, **Draw a room…** creates a connected rectangular
addition, and **Draw hallway** creates a narrow connecting floor region. Draw
from an existing room edge, then add another room at the end. Hallways are
permanent parts of the home. New rooms are permanent by default; **Optional expansion** makes one available for player-controlled
addition/removal in-game.

Choose **Walls & openings**, then **Draw wall** and drag horizontally or
vertically inside a room. An interior wall can also divide adjoining rooms
along their shared edge. **Room wall** uses the game's cutaway appearance:
separate timber edges around a black cavity, with a full wallpaper face above
the passage. It reserves two columns for a vertical wall or five rows for a
horizontal wall. The preview shows the space the wall needs.

Select a wall on the canvas or in the list to choose
a solid wall, a one-tile doorway, or a wider passage. **Place opening** previews
the opening at the pointer; click the wall to position it. **Remove selected
wall**, or Delete while it is selected, removes it. Openings are permanent
passages; they do not add animated or locked doors.

**Slim divider** keeps the narrower timber style used by older designs. Existing
dividers keep their size when reopened. Change their wall type to **Room wall**
after making space; Pixelheart rejects a conversion that would cover furniture
or block a route, and Undo restores the previous wall.

Red previews explain rejected layouts: disconnected rooms, walls blocking
passages, cropped furniture, or unreachable authored NPC destinations. Resizing
keeps existing furniture, home positions, route stops and scene starts fixed;
moving a room carries its contents and authored destinations. An entrance on a
resized lower wall follows that edge. Review scene walking paths after moving
rooms, since authored movement beats are preserved.

The outside doorway is an opening in the lower wall. Drag the passage to move
it, or choose **Move the doorway** and click a clear bottom edge. The arrival
moves just inside it; the passage leads back outside. Spouse rooms keep their
fixed size and marked standing spot. Existing homes keep their map coordinates:
an addition on the left needs enough free space.

Room walls use the room's wallpaper and structural trim. Slim-divider faces
remain fixed map artwork and do not follow in-game wallpaper changes.

Choose **Architectural pieces** in the Rooms selector to browse built-in
counters, cupboards, columns, bookcases, hearths and stairs from the connected
game library. Search by name or filter by category. Click a picture to pick up
the piece, then click the canvas to place it. Drag a placed piece to move it;
**Duplicate** picks up another copy, and **Remove** or Delete removes it.
Escape cancels placement or a drag. Each successful edit is one Undo step.

Each piece has placement rules. Its catalogue hint explains the required
support, and the canvas outlines any space that must stay clear:

- **Steps:** connect a raised room to a lower room through an enclosed passage.
  Choose **Raised room with steps** when adding a room, and place it above the
  lower room with a four-tile gap. The room, two-tile-wide stairs, and both
  landings are added together. Drag the steps sideways to reposition the
  connection within the shared room width. Furniture and rugs cannot cover the
  treads or either landing. Wall strips inside a flat room do not make a valid
  staircase.
- **Kitchen fixtures and bookcases:** align their bottom row with the first
  floor row against a continuous north wall, and keep their front accessible.
  The sink includes its matching wall cabinet. Both it and the refrigerator
  are three tiles tall; place separate dish cupboards above the counters.
  A work counter end must join a compatible counter or a side wall on its left.
- **Cupboards and cabinets:** fit completely on the upper wall, above the
  lower trim and away from openings.
- **Wall posts and chimneys:** attach to the wall structure with their bases
  on the first floor row. The chimney must also meet the ceiling cap.
- **Bars:** leave access behind and in front of the straight counter. The
  left return attaches to a straight section on its right, with aligned bases.

These checks also apply when moving furniture, resizing rooms, changing walls,
or removing a supporting piece. Invalid previews explain what to fix. Older
designs remain openable; **Placements to fix** selects pieces that need repair.
Move or remove them before saving or exporting. Exported stair treads and
landings prevent furniture placement in-game as well.

These pieces retain their map artwork, collision and foreground layers. Tall
parts can stand in front of characters while their bases block walking. Wall
fixtures belong against a north wall or a horizontal interior wall. Room moves
carry their built-in pieces; resizing keeps them fixed and rejects cropping.
Blocked entrances, wall openings and authored NPC destinations are protected.

Raised rooms and their steps occupy the same map; they do not create a separate
map or warp. Their walls keep travel between the two levels within the stairway.
Remove a raised room to remove its attached stairs, and undo restores both.
Architectural pieces are fixed map structures; built-in hearths do not provide fire controls.
Movable windows and working furniture fireplaces remain in **Furnish**. If the
architectural catalogue is empty, open a save with the updated companion, then
use **Refresh game library…**. Artwork is read from your installed game and
stored privately with the design.

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

Imports are staged until **Apply home** or **Apply room**. Apply returns the
design to Home & places; **Save project** keeps those changes on disk. Cancel
discards edits and staged assets. Drafts can be applied before all artwork is
ready; the Home page keeps missing setup visible and export remains blocked.
Undo and redo work across room, furniture, and surface edits.
Save As carries the private tilesheets and preview textures with the project.
**Use this story location as their residence** updates the character's home; review their authored
schedules separately. Applying an interior replaces that place's imported map.

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
a save, and choose **Refresh game library…** in Pixelheart. Refresh keeps the
selected source; use **Change library…** to choose a different installation or
snapshot. **Library setup guide…** in the connection dialog includes build and
installation instructions. The library reflects
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

Choose **Connect entrance…** beside **Edit interior…** for each new standalone
place. New places remain unconnected until you choose **Use this entrance**:

1. Select the **Outside map** and the **Enter from** X/Y tile that triggers entry.
2. Choose a different outside **Return to** tile.
3. Designed residences derive their inside arrival and exit from the doorway.
   Use **Move doorway in designer…** to change it. Imported or painted maps
   expose separate **Arrive inside** and **Exit from** coordinates.
4. Choose **Use this entrance**, then **Save project**. Editing its map or
   coordinates requires confirming the entrance again. Existing projects retain
   their saved connections.

**Remove place** is blocked while routes, scenes, bundled characters, or other
entrances still use it. The page lists those references so you can update them.
**Undo removal** restores an unreferenced place until the next project save.

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

For a supplied map, open **Map tools & dependencies**, select **Use a section
as the primary character's spouse room**, and choose the top-left X/Y tile of a
6×9 section. The map must contain the whole section. The 6×9 painter preset
selects X 0, Y 0 automatically.

The game places this section in the farmhouse after marriage. It is not a
standalone location and should not be selected as a home, schedule, or event
destination. Use the game's farmhouse destination for married scenes and the
married routine's **Return to farmhouse** control for the spouse's return home.
See [LIFE_AND_BRANCHING.md](LIFE_AND_BRANCHING.md) for conversations and routines.

## Declare other mods

Open **Map tools & dependencies**, then **Mod dependencies**. Use the UniqueID from another mod's `manifest.json`,
optionally require a minimum version, and specify whether the dependency is
required. Include mods that provide external maps, NPC actors, or modded items
your content needs. Declaring a dependency does not download or bundle that mod.

All packs need Stardew Valley 1.6, SMAPI 4+, and Content Patcher 2.9.0+.
Ready Daily scenes automatically add Event Repeater 6.5.8+ as a required
dependency. The installer can report missing requirements; install the external
frameworks separately and check SMAPI's log before playtesting.

## Export, test, and revise

Resolve the export review's errors, then export a ZIP or use **Review & export → Install & playtest**
to install in a selected Mods folder. Pixelheart updates only folders it owns
with a matching pack identity. The previous version is backed up under
`Pixelheart backups` beside Mods, outside the active mod directory. A manually
installed or unrelated folder is preserved rather than overwritten.

The archive includes `WORLD_TESTING.txt` with exact character IDs, home tiles,
map IDs, warp coordinates, and spouse-room information. `STORY_TESTING.txt`
covers ready primary and supporting scenes, their triggers, answer outcomes,
and repeat rules. Test on a backed-up save: meet each character, sleep and follow
routes, use both directions of every entrance, play every scene and answer, and
check the farmhouse after marriage. Record results in **Review & export → Install & playtest**,
revise the same records, and export/install again.

Desktop exports contain a portable `project.json` with test records, catalog,
all drafts, preserved legacy planning, and references to the included
selected artwork and map bundles. Extract the entire pack before reopening it.
Keep the working project too: it retains original/prepared artwork versions
that were not selected for the export.

References: [Content Patcher map edits](https://github.com/Pathoschild/StardewMods/blob/stable/ContentPatcher/docs/author-guide/action-editmap.md),
[game location data](https://stardewvalleywiki.com/Modding:Location_data), and
[NPC spouse-room fields](https://stardewvalleywiki.com/Modding:NPC_data).

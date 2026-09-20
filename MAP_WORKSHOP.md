# Paint a place, play it, and come back to improve it

Open **Cast & locations → Places & spouse room**, choose **Add place**, and give
the place a name. Choose **Create map from a tilesheet…**. Save the project when
prompted so its map and artwork can travel together.

## Paint the first version

1. Choose **Open tilesheet PNG…** and select your own tile artwork. The PNG must
   already contain 16×16-pixel cells, with both image dimensions divisible by 16.
   Sheets can be up to 2048×2048 pixels and 16 MiB. Pixelheart copies the supplied
   PNG unchanged; it does not generate tile artwork.
2. Choose **Home interior · 12 × 12 tiles**, **Small location · 20 × 20 tiles**,
   or **Spouse room · 6 × 9 tiles**.
   Select a ground or floor tile in the palette. Keep **Back · Ground and floors**
   selected and click **Fill layer**. Every Back cell needs ground before saving.
3. Choose **Buildings · Walls and solid furniture** and paint walls or objects.
   Keep a clear route between the entrance, the NPC's standing positions, and the
   exit. **Front · Details above characters** is for details that draw over them.
   Leave **Paths · Hidden game metadata** empty unless you know the game's tile
   meanings; it is not a decoration layer.
4. Use **Paint tile** to click or drag, **Erase tile** to remove a cell from the
   selected layer, or **Fill connected area** to replace a connected region.
   **Fill layer** replaces the entire selected layer; **Clear layer** empties it.
   **Undo**, also available with Ctrl/Cmd+Z, reverses a stroke, fill, clear, or size
   change. Changing size keeps overlapping cells, and Undo restores cropped cells.
5. Adjust **Zoom** and **Grid** to inspect the painting. Hover over a cell to see
   its tile X and Y coordinates. Choose **Save place to project** when ready.

For a home interior, you can select a floor tile and a wall tile and use the
room starter. It replaces the four layers with a floor and border walls, leaving
one doorway at the bottom; Undo restores the previous painting. The home guides
suggest resident, arrival, and exit positions. Guides are visual aids and do not
create game warps. Set the actual positions and connections in the home editor.
Open it using **Identity → Assign & design home…** or a companion's profile.

## Connect the place to the game

For a small location, complete **Give the player a way in and out** in
**Cast & locations**. Choose the outside **Entrance map** and its trigger tile,
the tile where the player arrives inside, the inside exit trigger, and the return
tile outside. Keep arrival tiles separate from the corresponding exit triggers
so walking through a door does not immediately send the player back.

For a spouse room, the 6×9 preset selects the room option and its section at
X 0, Y 0. The game places that section in the farmhouse after marriage to the
primary character. A spouse room is not a separate destination for a daily route.

The place appears by name in the project's map selectors. Choose it for scene
staging or a route only after checking the intended standing tiles. Export checks
the bounds of supplied maps; it cannot establish collision or walking routes.

## Reopen a painting after testing

Select the same place and choose **Edit painted map…**. Pixelheart restores its
tilesheet and all four layers. Paint the changes, then choose **Save place to
project**. The place points to the new revision, while the previous map bundle
stays intact. Save the project and export/install again before testing the change.

**Import Tiled map…** also accepts richer supplied map bundles. If such a map has
properties, objects, extra layers, tile animations, or other features the simple
painter cannot preserve, **Edit painted map…** refuses to flatten it. Edit that
map in Tiled and import the revised bundle. A refused edit changes no map files
or project references.

Keep the project JSON and its `world_assets` folder together. Map bundles contain
the finite TMX map and its local PNG/TSX dependencies; the painter stores the
original PNG alongside its TMX revision.

## Verify the result in Stardew Valley

Test walking into and out of the place, reaching the intended standing tiles,
NPC routes, scene staging, furniture collision, and details drawn over characters.
For a spouse room, test after marriage and check the room's position in the
farmhouse. The app previews tile artwork and checks portable map structure; it
has not played the authored map inside the game.

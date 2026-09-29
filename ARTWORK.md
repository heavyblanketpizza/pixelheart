# Pixelheart artwork policy

Pixelheart uses **user-uploaded portraits and character sprite images**, with optional reference sheets read from your own installed game (or your Content Patcher exports). Pixelheart has no built-in AI artwork generation and does not connect to AI image services. If you want to use AI image tools, use them outside Pixelheart, then import the finished PNG like any other upload. The same sheet layouts, checks, and [artwork rights](#artwork-rights) apply.

## Supported image preparation

Image preparation runs locally with Pillow and is limited to **resizing and pixelation when needed**:

- Reduce an oversized portrait or sprite sheet proportionally to its supported width, preserving its aspect ratio and frame layout. Upscaling is not supported.
- Optionally pixelate each frame on a 2, 4, or 8-pixel grid, using ordinary resampling and nearest-neighbor reconstruction without blending adjacent frames.
- Keep correctly sized artwork byte-for-byte unchanged when processing is off. Downscaling uses nearest-neighbor resizing to preserve hard edges.

The editor previews original and prepared artwork. Users choose pixelation strength and whether the original, prepared, or painted file is selected for export; processing is optional. Pixel-art detection is not assumed to be reliable.

Imported originals are preserved in the project, and prepared and painted images are saved separately. **Save As** copies every referenced version, and the layers of painted versions, into the destination project. Pixelheart does not send images to a generation service or synthesize expressions, poses, animation frames, missing pixels, or backgrounds.

## Pixel painter

Choose **Paint…** on the portrait or sprite card to open the pixel painter. It edits the selected version of that sheet, or starts a blank sheet in the export layout (128 × 192 portraits; 64 × 128 sprites, or 64 × 416 for romance) when nothing is uploaded. On a seasonal or Beach appearance that uses Default, painting starts from the Default sheet and saves a separate sheet for that appearance, keeping Default's source notice.

Everything you paint is your own work: the painter places exactly the pixels you choose and never fills in, shades, or generates anything.

**Drawing.** Pencil, eraser, line, rectangle, ellipse (outline or filled), fill (the connected area, or every pixel of that color on the layer), color picker, and select-and-move. The left button paints the first color and the right button the second color, which starts transparent so it erases. Colors can be partly transparent, and painting replaces pixels exactly instead of blending. Brushes are 1 to 8 pixels. Shift-click draws a straight line from the last point; Shift while dragging a line keeps it at 0°, 45°, or 90°, and makes rectangles and ellipses square or round. Alt-click picks a color from any tool.

**Layers.** Add, duplicate, delete, reorder, rename, show or hide, lock, fade, and merge down, up to 32 layers. Hidden layers stay in the project but are left out of the sheet. **Layer → Reference image from a PNG…**, **Reference: Original upload**, and **Reference: A villager from my game…** add a faded, locked tracing layer on top. Reference layers never reach the game.

**Frames.** The canvas shows each frame, and the one you last clicked is outlined. **Frame** commands select, copy, paste into another frame, flip, or clear the active frame. Tick **Frame tools change all layers** to change every unlocked layer at once, for example to build the left-walking row from the right-walking row. **Mirror** reflects every stroke inside each frame, and **Onion skin** shows the previous (warm) and next (cool) frame of the row behind the one you're drawing. **Sheet → Add … row** and **Remove last … row** change the sheet's height; its width stays fixed so frames and tile numbers never shift.

**Colors.** The color panel lists every color in the sheet, most used first. Click one to paint with it, or choose **Replace…** to swap it for another color on the current layer, on every unlocked layer, or only inside the selection. Recent colors are kept while you paint, and the first color can be typed as `#rrggbb` or `#rrggbbaa`.

**Preview.** The preview shows the flattened sheet at game size: the active walking row playing (with a speed control), the active expression, or a tile repeated 3 × 3 so seams show.

**Undo.** Each stroke, fill, move, or layer change is one step in the painter's **Edit → Undo**, up to 200 steps. Choosing **Use painted sheet** adds one step to the project's **Edit → Undo**, which returns to the previous version.

**Saving.** The visible, non-reference layers are flattened into a PNG, checked against the same 5 MB and 4,194,304-pixel limits as uploads, saved as the sheet's **painted version**, and selected for export. The original upload and any prepared copy stay unchanged, and the export picker can switch between them. The layers are kept in the project's `artwork/layers` folder, matched to that exact PNG. If the PNG is changed outside Pixelheart, the painter opens it as a single layer. Layer files are never exported. Closing with unsaved changes asks first.

| Keys | Action |
| --- | --- |
| B, E, L, U, O, G, I, M | Pencil, eraser, line, rectangle, ellipse, fill, pick color, select |
| X · [ · ] | Swap colors · smaller brush · larger brush |
| Ctrl/Cmd+Z · Ctrl/Cmd+Shift+Z | Undo · redo |
| Ctrl/Cmd+C, X, V · Delete | Copy, cut, paste (the system clipboard works too) · delete selected pixels |
| Enter · Esc | Place moved pixels · cancel a move or clear the selection |
| F · , · . | Select frame · previous frame · next frame |
| Shift+H · Shift+V | Flip horizontally · flip vertically (selection, or the active frame) |
| Shift+M · Shift+O | Mirror drawing · onion skin |
| Ctrl/Cmd + scroll, Ctrl/Cmd+0 | Zoom · fit the sheet |
| Space-drag or middle-drag | Move around the canvas |

## Portraits and sprite sheets

A single portrait does not become a complete expression sheet or walking animation through resizing or pixelation. The preparation workflow requires a correctly arranged sheet; users supply the required expressions and sprite frames themselves.

Processing respects frame boundaries. If an image's aspect ratio or arrangement cannot fit the supported sheet layout through uniform resizing, preparation explains the mismatch and requires a correctly arranged source. It does not stretch, crop, duplicate, or invent frames to make validation pass.

The current export template expects:

- Portraits: two columns of 64 × 64 frames, at least six expressions, for a sheet 128 pixels wide and at least 192 pixels high.
- Character sprites: four columns of 16 × 32 frames, for a sheet 64 pixels wide and at least 128 pixels high. Romanceable characters need the additional frame positions described in [EXPORT_FORMAT.md](EXPORT_FORMAT.md).

Dimensions and file checks cannot establish whether frames contain the right expressions, poses, or animation. Prepared artwork still needs review and in-game testing.

## Preview and appearances

The desktop editor shows one expression or sprite frame at a time, with compact selectors and previous/next controls. Four-direction walking playback includes play/pause and speed controls. **Sheet layout** is an optional view of the full source sheet with frame boundaries and the selected cell highlighted. There is no permanent thumbnail grid. The first six portrait positions use standard dialogue labels; extra expressions and poses remain selectable by index. Preview playback does not author custom animation scripts.

The Default appearance is required. Optional Spring, Summer, Fall, Winter, and Beach appearances can each supply a separate portrait sheet, sprite sheet, or both. Unassigned sheets use Default, with that fallback shown in the editor. Each appearance preserves its own original/prepared/painted selection; Save As copies every referenced version. Seasonal sets export with season conditions; Beach exports as island attire, but island visits still require separate setup.

Inputs must be static PNG files, at most 5 MB and 4,194,304 pixels. Corrupt or animated PNGs are rejected.

The exporter validates the selected PNG sheets and writes their bytes unchanged. Preparing a sheet does not prove its expressions or animation frames work in the game. See [EXPORT_FORMAT.md](EXPORT_FORMAT.md) for the export checks.

## Detailed artwork review

Choose **Portraits & sprites → Detailed review…** to inspect every frame of the selected appearance. The review uses each sheet's selected original, prepared, or painted version and shows when an appearance falls back to Default. It opens separately from the compact editor preview.

- Browse portraits and sprites by frame, with pixel zoom and checkerboard, light, or dark backgrounds.
- Choose **Compare with…** to place a reference PNG, the original upload, or a sheet from **From my game…** beside each corresponding frame. Loading a comparison does not replace your artwork or change the project's export selection.
- Play the four walking directions, or use **Join next cell** to inspect a prop or pose drawn across two adjacent sprite cells. Cells cannot be joined across a row boundary.
- Check source dimensions and transparent cells. Larger proportional sheets are displayed at game-sized frame proportions using nearest-neighbor scaling; this preview does not prepare or resize the saved artwork. Missing reference frames stay visibly missing.
- Choose **Save HTML review…** to save an interactive, standalone report with embedded artwork and references. It opens offline in a browser, without requiring Pixelheart.

Frames are numbered from zero. Comparison matches frame positions; different characters may use extra poses differently. Walking playback previews the first four sprite rows and does not validate animation behavior in the game. The review accepts up to 512 frames per sheet.

## Templates from your game

Once Pixelheart has found your game (see **View → Find Stardew Valley…**), **Portraits & sprites → From my game…** lists every villager whose portrait and sprite sheets are in your installed game. Pick one and choose **Load template**; the sheets are read straight from the game's own files, with no console commands. A few villagers with unusual sheet layouts (such as Krobus) can't be previewed and say so. Console editions are not supported.

The dialog previews both complete sheets before **Use as starting artwork** copies them into the selected appearance. Loading alone does not change the project, and nothing in your game folder is changed. Source information follows the imported sheets into saved projects and pack credits; replacing a sheet keeps its previous source as history. Choose **Paint…** to repaint the sheets here, or **Sheet options → Save PNG copy for editing…** to make a separate copy for another pixel editor.

**Conversations → Load dialogue template…** works the same way for any villager's everyday dialogue. Every line is loaded with its text, commands, and order; the handful of vanilla lines the editor can't hold (for example keys with spaces) are left out, and the dialog says how many. Resolve matching triggers before applying; the editor's 2,000-entry limit still applies. This loads the villager's dialogue asset, not lines from festivals, events, or other game assets.

### Advanced: Content Patcher exports

To start from a modded version of a villager, choose **From a Content Patcher export (advanced)** in either dialog. With SMAPI and Content Patcher running, export the assets in the SMAPI console, for example:

```text
patch export "Portraits/Abigail" image
patch export "Characters/Abigail" image
patch export "Characters/Dialogue/Abigail"
```

Then choose the resulting **patch export** folder (artwork) or copy the dialogue JSON into the project's `dialogue` folder (dialogue). Exports include active mod changes and the game's current language.

Imports need no downloads, and importing local content does not grant permission to redistribute game or mod artwork or writing. Review rights and character-specific expressions, poses, and dialogue before sharing a pack.

Gift item previews use a separate cache. Opening Gifts automatically downloads missing vanilla icons from verified Stardew Valley Wiki image links into the OS user cache at `Pixelheart/wiki-items`. Those PNGs stay outside repositories, projects, and exported packs; cached icons work offline. Locally imported mod textures take precedence. See [GAME_ITEMS.md](GAME_ITEMS.md).

## Artwork rights

Users must have the rights needed to use, transform, and distribute their uploaded images. Pixelheart does not claim ownership of those images or the NPC content users author. Resizing and pixelation do not remove an image's existing license or attribution requirements.

The software license permits Pixelheart-owned template material to be included in free Stardew Valley NPC packs. Selling, paywalling, or earning Donation Points from that material is not permitted. Showing those NPCs in gameplay videos or streams is expressly allowed, including monetized content, provided the uploaded artwork and other third-party content allow that use. This does not claim ownership of a user's original artwork or restrict its independent use outside Pixelheart. See [LICENSE](LICENSE) for the terms.

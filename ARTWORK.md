# Pixelheart artwork policy

Pixelheart uses **user-uploaded portraits and character sprite images**, with optional reference sheets read from your own installed game (or your Content Patcher exports). Pixelheart has no built-in AI artwork generation and does not connect to AI image services. If you want to use AI image tools, use them outside Pixelheart, then import the finished PNG like any other upload. The same sheet layouts, checks, and [artwork rights](#artwork-rights) apply.

## Supported image preparation

Image preparation runs locally with Pillow and is limited to **resizing and pixelation when needed**:

- Reduce an oversized portrait or sprite sheet proportionally to its supported width, preserving its aspect ratio and frame layout. Upscaling is not supported.
- Optionally pixelate each frame on a 2, 4, or 8-pixel grid, using ordinary resampling and nearest-neighbor reconstruction without blending adjacent frames.
- Keep correctly sized artwork byte-for-byte unchanged when processing is off. Downscaling uses nearest-neighbor resizing to preserve hard edges.

The editor previews original and prepared artwork. Users choose pixelation strength and whether the original or prepared file is selected for export; processing is optional. Pixel-art detection is not assumed to be reliable.

Imported originals are preserved in the project, and prepared images are saved separately. **Save As** copies both referenced versions into the destination project. Pixelheart does not send images to a generation service or synthesize expressions, poses, animation frames, missing pixels, or backgrounds.

## Portraits and sprite sheets

A single portrait does not become a complete expression sheet or walking animation through resizing or pixelation. The preparation workflow requires a correctly arranged sheet; users supply the required expressions and sprite frames themselves.

Processing respects frame boundaries. If an image's aspect ratio or arrangement cannot fit the supported sheet layout through uniform resizing, preparation explains the mismatch and requires a correctly arranged source. It does not stretch, crop, duplicate, or invent frames to make validation pass.

The current export template expects:

- Portraits: two columns of 64 × 64 frames, at least six expressions, for a sheet 128 pixels wide and at least 192 pixels high.
- Character sprites: four columns of 16 × 32 frames, for a sheet 64 pixels wide and at least 128 pixels high. Romanceable characters need the additional frame positions described in [EXPORT_FORMAT.md](EXPORT_FORMAT.md).

Dimensions and file checks cannot establish whether frames contain the right expressions, poses, or animation. Prepared artwork still needs review and in-game testing.

## Preview and appearances

The desktop editor shows one expression or sprite frame at a time, with compact selectors and previous/next controls. Four-direction walking playback includes play/pause and speed controls. **Sheet layout** is an optional view of the full source sheet with frame boundaries and the selected cell highlighted. There is no permanent thumbnail grid. The first six portrait positions use standard dialogue labels; extra expressions and poses remain selectable by index. Preview playback does not author custom animation scripts.

The Default appearance is required. Optional Spring, Summer, Fall, Winter, and Beach appearances can each supply a separate portrait sheet, sprite sheet, or both. Unassigned sheets use Default, with that fallback shown in the editor. Each appearance preserves its own original/prepared selection; Save As copies every referenced version. Seasonal sets export with season conditions; Beach exports as island attire, but island visits still require separate setup.

Inputs must be static PNG files, at most 5 MB and 4,194,304 pixels. Corrupt or animated PNGs are rejected.

The exporter validates the selected PNG sheets and writes their bytes unchanged. Preparing a sheet does not prove its expressions or animation frames work in the game. See [EXPORT_FORMAT.md](EXPORT_FORMAT.md) for the export checks.

## Detailed artwork review

Choose **Portraits & sprites → Detailed review…** to inspect every frame of the selected appearance. The review uses each sheet's selected original or prepared version and shows when an appearance falls back to Default. It opens separately from the compact editor preview.

- Browse portraits and sprites by frame, with pixel zoom and checkerboard, light, or dark backgrounds.
- Choose **Compare with…** to place a reference PNG, the original upload, or a sheet from **From my game…** beside each corresponding frame. Loading a comparison does not replace your artwork or change the project's export selection.
- Play the four walking directions, or use **Join next cell** to inspect a prop or pose drawn across two adjacent sprite cells. Cells cannot be joined across a row boundary.
- Check source dimensions and transparent cells. Larger proportional sheets are displayed at game-sized frame proportions using nearest-neighbor scaling; this preview does not prepare or resize the saved artwork. Missing reference frames stay visibly missing.
- Choose **Save HTML review…** to save an interactive, standalone report with embedded artwork and references. It opens offline in a browser, without requiring Pixelheart.

Frames are numbered from zero. Comparison matches frame positions; different characters may use extra poses differently. Walking playback previews the first four sprite rows and does not validate animation behavior in the game. The review accepts up to 512 frames per sheet.

## Templates from your game

Once Pixelheart has found your game (see **View → Find Stardew Valley…**), **Portraits & sprites → From my game…** lists every villager whose portrait and sprite sheets are in your installed game. Pick one and choose **Load template**; the sheets are read straight from the game's own files, with no console commands. A few villagers with unusual sheet layouts (such as Krobus) can't be previewed and say so. Console editions are not supported.

The dialog previews both complete sheets before **Use as starting artwork** copies them into the selected appearance. Loading alone does not change the project, and nothing in your game folder is changed. Source information follows the imported sheets into saved projects and pack credits; replacing a sheet keeps its previous source as history. **Sheet options → Save PNG copy for editing…** creates a separate copy for your pixel editor.

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

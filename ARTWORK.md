# Pixelheart artwork policy

Pixelheart uses **user-uploaded portraits and character sprite images**, with optional reference sheets imported from your own Content Patcher exports. The application does not generate artwork or include generative-image features.

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

## Templates from your game

Choose **From my game…** in Artwork, select Abigail or Elliott, and follow the displayed export commands. This workflow uses SMAPI and Content Patcher on Windows, macOS, or Linux; Steam and GOG use the same exported file formats. Console editions are not supported.

For Abigail, run these in the SMAPI console:

```text
patch export "Portraits/Abigail" image
patch export "Characters/Abigail" image
```

Choose the resulting **patch export** folder (or its game folder), then **Load template**. The macOS game app bundle is also accepted. Use Elliott in the commands for Elliott's sheets. The selected folder is remembered only in local application settings.

The dialog previews both complete sheets before **Use as starting artwork** copies them into the selected appearance. Loading alone does not change the project. Original exports remain unchanged. Source information follows the imported sheets into saved projects and pack credits; replacing a sheet keeps its previous source as history. **Sheet options → Save PNG copy for editing…** creates a separate copy for your pixel editor.

**Dialogue → Load character dialogue…** uses the same folder. Its command is `patch export "Characters/Dialogue/Abigail"` (or Elliott). Every entry in that file is loaded and selected, preserving text, commands, and order. Search does not deselect entries. Resolve matching triggers before applying; files exceeding the editor's 2,000-entry or import-size limits are rejected rather than truncated. This loads the selected character dialogue asset, not every line the character may speak from festivals, events, or other game assets.

Exports include active mod changes and the game's current language. For vanilla references, run only SMAPI and Content Patcher. These imports require no template downloads, and importing local content does not grant permission to redistribute game or mod artwork or writing. Review rights and character-specific expressions, poses, and dialogue before sharing a pack.

Gift item previews use a separate cache. Opening Gifts automatically downloads missing vanilla icons from verified Stardew Valley Wiki image links into the OS user cache at `Pixelheart/wiki-items`. Those PNGs stay outside repositories, projects, and exported packs; cached icons work offline. Locally imported mod textures take precedence. See [GAME_ITEMS.md](GAME_ITEMS.md).

## Artwork rights

Users must have the rights needed to use, transform, and distribute their uploaded images. Pixelheart does not claim ownership of those images or the NPC content users author. Resizing and pixelation do not remove an image's existing license or attribution requirements.

The software license permits Pixelheart-owned template material to be included in free Stardew Valley NPC packs. Selling, paywalling, or earning Donation Points from that material is not permitted. Showing those NPCs in gameplay videos or streams is expressly allowed, including monetized content, provided the uploaded artwork and other third-party content allow that use. This does not claim ownership of a user's original artwork or restrict its independent use outside Pixelheart. See [LICENSE](LICENSE) for the terms.

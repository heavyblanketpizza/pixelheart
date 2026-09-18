# Pixelheart artwork policy

Pixelheart uses **user-uploaded portraits and character sprite images**, with optional vanilla reference sheets downloaded from verified sources only when requested. The application does not generate artwork or include generative-image features.

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

## Vanilla starting templates

Choose **Use vanilla template…**, select an NPC, then **Load template** to download a reference. The source and ConcernedApe credit are shown before downloading. The catalog includes:

- **Abigail:** complete [portrait sheet](https://stardewvalleywiki.com/File:Modding_-_creating_an_XNB_mod_-_example_portraits.png) and [sprite sheet](https://stardewvalleywiki.com/File:Abigail-sprite-sheet.png) from Stardew Valley Wiki.
- **Elliott:** complete 128 × 320 portrait and 64 × 416 sprite sheets from [stardew-data's original game asset extraction](https://github.com/juliaramosguedes/stardew-data/tree/4e0d98119afefd766f15ee77a529db4eb71fa240), documented as Stardew Valley 1.6.15. Downloads use this fixed commit and verified SHA-256 hashes matching its Git LFS records. The original sheets are imported unchanged, including unused cells.

These are starting references, not a complete catalog of NPC outfits. Ordinary wiki character icons are single portraits and cannot substitute for complete sheets. Selecting either reference changes only artwork, not the custom character's name, gender, or other identity details.

Downloads start only after **Load template**. Files are cached under the operating system's user cache in `Pixelheart/wiki-templates`. Each NPC has its own cache; a valid cache can be reused offline. No character data or user artwork is uploaded.

The dialog is a reference viewer until **Use as starting artwork** is selected. That action copies both sheets into the current project's selected appearance and retains source attribution in the artwork records. Original cached files stay unchanged. **Sheet options → Save PNG copy for editing…** writes a separate PNG for an external pixel editor; upload the edited sheet afterward. Vanilla-specific expressions and romance poses still need review for a custom NPC.

ConcernedApe retains ownership of the downloaded game artwork.

Gift item previews use a separate cache. Opening Gifts automatically downloads missing vanilla icons from verified Stardew Valley Wiki image links into the OS user cache at `Pixelheart/wiki-items`. Those PNGs stay outside repositories, projects, and exported packs; cached icons work offline. Locally imported mod textures take precedence. See [GAME_ITEMS.md](GAME_ITEMS.md).

## Artwork rights

Users must have the rights needed to use, transform, and distribute their uploaded images. Pixelheart does not claim ownership of those images or the NPC content users author. Resizing and pixelation do not remove an image's existing license or attribution requirements.

The software license permits Pixelheart-owned template material to be included in free Stardew Valley NPC packs. Selling, paywalling, or earning Donation Points from that material is not permitted. Showing those NPCs in gameplay videos or streams is expressly allowed, including monetized content, provided the uploaded artwork and other third-party content allow that use. This does not claim ownership of a user's original artwork or restrict its independent use outside Pixelheart. See [LICENSE](LICENSE) for the terms.

# Pixelheart export format

The exporter produces a **starter Content Patcher content pack** for Stardew Valley
1.6 with Content Patcher 2.9.0 or later. **No exported NPC has been verified in
the game by this application.**

Artwork is supplied by the user. The desktop editor can prepare separate PNG
copies with local resizing and optional pixelation. The exporter validates the
selected sheets and copies them unchanged; it does not generate images. See
[ARTWORK.md](ARTWORK.md) for the preparation workflow.

## Files in the archive

Each exported archive contains a `[CP] InternalName` directory:

| File | Purpose |
| --- | --- |
| `manifest.json` | Pack metadata, stable project-specific mod ID, Content Patcher dependency |
| `content.json` | Artwork loads; characters, gifts, ready events, conditional life rules, map data and warps; optional concrete `RepeatEvents` IDs |
| `assets/portraits.png` | Provided portrait sheet, unchanged |
| `assets/sprites.png` | Provided sprite sheet, unchanged |
| `assets/appearances/<appearance>/portraits.png` | Optional selected seasonal or beach portrait sheet, unchanged |
| `assets/appearances/<appearance>/sprites.png` | Optional selected seasonal or beach sprite sheet, unchanged |
| `assets/dialogue.json` | Authored trigger keys and dialogue text |
| `assets/schedule.json` | One daily route under the required `spring` fallback key |
| `assets/marriage-dialogue.json` | Present when enabled spouse conversations need a character-specific dialogue asset |
| `assets/cast/<id>/…` | Supporting characters' selected artwork and dialogue/schedule assets |
| `assets/maps/<id>/…` | Supplied TMX map and its local TSX/PNG dependencies, unchanged |
| `project.json` | Complete authoring data, including ideas which are not game scripts |
| `validation.json` | Export checks and warnings |
| `README.txt` | Installation, testing, template limitations, output permissions, and unofficial-fan disclaimer |
| `CREDITS.txt` | Present for imported references: recorded sources, asset hashes, and previous-sheet attribution history; does not grant redistribution rights |
| `STORY_TESTING.txt` | Ready primary or supporting scenes: resolved IDs, triggers, arcs, answer outcomes, repeat rules, and playtest checklist |
| `WORLD_TESTING.txt` | Authored cast/maps/dependencies: character IDs, homes, map entrances/exits, spouse-room section, and playtest checklist |

The mod and NPC IDs include a digest of the project's stable ID, so independently
created projects can use the same display name without overriding each other.
Changing the internal name changes the NPC identity; keep it stable after use in
a save. Change the default manifest author before publication. The desktop editor
can open `project.json` after the complete pack folder is extracted. Desktop
exports retain the creator brief, chapter plan, test records, item catalog,
unknown extension metadata, and relative references to exported artwork and maps.
Local template source notices remain in the project backup; the selected game
folder is stored only in this computer's application settings.
Export backups contain selected sheets, not every original/prepared version of
the working project.

## Supported authored content

Identity includes name, birthday, age, manner, social anxiety, optimism, spawn map,
spawn tile, and romance availability. The primary character must be adult,
including while romance is disabled. Supporting characters can export as Adult,
Teen, or Child; only adults can be romanceable. Each companion uses the same
character, dialogue, gift, schedule, and artwork compiler with separate assets.
The game supports three `Gender` values. The GUI stores Woman as `Female`,
Man as `Male`, and Unspecified as `Undefined`. Explicit gender is exported
directly. Older projects migrate exact `he/him` to `Male`, `she/her` to `Female`,
and other pronouns to `Undefined`; original pronouns remain in the project.
NPCs are social and giftable.
The default route applies every season and weather. Enabled Life & reactions
rules add conditional weekday dialogue, alternative routes, and spouse dialogue.
Conditions include season, weather, weekday, hearts, relationship status,
completed event, and minimum farmhouse upgrade. Rules update each morning;
later matching rules override earlier ones. Married routines use dated marriage
schedule keys. See [LIFE_AND_BRANCHING.md](LIFE_AND_BRANCHING.md).

Artwork can include optional `spring`, `summer`, `fall`, `winter`, and `beach`
appearances in addition to the required default portrait and sprite sheets.
The exporter adds image loads and `Data/Characters` `Appearance` entries with
the matching `Season`, or `IsIslandAttire: true` for beach artwork. A missing
portrait or sprite override falls back to the default texture. Every supplied
sheet is validated, including dialogue portrait indices for alternate sheets.
Beach artwork does not enable island visits; that participation still needs
separate authoring. See the [NPC appearance documentation](https://stardewvalleywiki.com/Modding:NPC_data#Appearance_.26_sprite).

The desktop gift catalog saves selections as `(O)`-qualified object IDs, including
the nonnumeric IDs used by vanilla 1.6 items and mods. Export preserves these IDs.
The core also accepts legacy named items, numeric IDs, negative category IDs,
and advanced entries like `id:CustomObject` or `tag:category_fish`. Unknown names
block export. IDs are checked for syntax; existence and runtime giftability
depend on the installed game and mods. Gift reactions are starter text, and
unlisted gifts follow the game's universal preferences. See [GAME_ITEMS.md](GAME_ITEMS.md)
for catalog imports and mod dependency requirements. A working project's imported
catalog is kept in its project file and desktop export backup. Mod-provided item
textures and other mods are not bundled; declare their required dependencies.

Romance enables the game's built-in romance flag and adds starter engagement
dialogue. Enabled authored spouse dialogue overrides supported morning and evening
slots; the game supplies other default interactions. Kiss frame 28 remains the
default. A supplied 6×9 map section can replace the primary character's spouse
room through `SpouseRoom.MapAsset` and `MapSourceRect`. Festival and island
participation remain disabled; sleep animations need further authoring.

Scenes marked **Ready** compile into `EditData` patches targeting
`Data/Events/<location>`. Structured scene beats support dialogue, emotes, movement,
pauses, friendship effects, and an optional final two-answer player choice. Each
answer has a closing response and independent friendship effect. Auxiliary fork
entries contain commands without a scene header and do not count as separate
playable roots in the testing guide. Choice answers do not create permanent flags;
trigger conditions and seen-event prerequisites let authors build a relationship
arc across several scenes. Friendship effects change the player's friendship
with the selected NPC. They do not simulate a private friendship score between
two NPCs or write custom family metadata. See [STORY_WORKSHOP.md](STORY_WORKSHOP.md)
for the authoring workflow and the supported scene controls.

Daily scenes add concrete event IDs to top-level `content.json.RepeatEvents`
and a required `misscoriel.eventrepeater` dependency, minimum version 6.5.8.
The framework resets completion after sleeping or loading a save, so effects
can recur even on a same-day reload. Daily scenes cannot serve as lasting event
or life-rule prerequisites. Normal Once scenes need no repeat framework.

Supplied custom maps compile to `Load Maps/<id>`, `Data/Locations` entries with
`CreateOnLoad.MapPath`, and two `EditMap.AddWarps` connections. A spouse-room
section is loaded as a map asset and placed in the farmhouse; it is not created
as a separate destination. TMX maps must be finite, orthogonal, use 16×16 tiles,
and contain Back, Buildings, and Front layers. Relative TSX/PNG dependencies
must stay in the map folder or subfolders. Imports and Save As copy the complete
closure, retaining file bytes. The simple painter uses supplied tiles, not
generated artwork. See [WORLD_BUILDING.md](WORLD_BUILDING.md).

Explicit external dependencies become manifest `Dependencies` entries with
UniqueID, required/optional status, and optional minimum version. The exporter
does not download external mods. Supporting-cast assets and events are namespaced
within the primary content pack; stable authoring aliases follow companion names.

Ideas and outlines are not exported as event scripts and do not need to be
complete before exporting other content. Ready scenes must pass their checks;
invalid ready scenes block export instead of being silently omitted. Existing
story notes open as ideas and never become playable just by opening or saving
the project. Every original idea, narrative outline, and relationship description
remains in `project.json` alongside the ready scenes. Stable event IDs survive
title changes and reordering; keep the project's identity and each event's ID
stable after playing the exported pack.

Schedule activity descriptions do not become custom animation instructions.
Biography, occupation, tagline, palette, and story notes are retained in
`project.json`; the tagline also supplies the manifest description.
Pixelheart does not generate artwork or supply generated concept illustrations.
Resizing or pixelating a user upload will not supply missing expressions or frames.

## Export blockers

- Missing or invalid identity, birthday, home map token, or tile coordinates.
- Empty dialogue, duplicate triggers, malformed keys, unsupported Content Patcher
  tokens, or numeric portrait selections outside the provided sheet.
- Missing schedule, malformed locations, negative/noninteger tiles, invalid
  facing, duplicate/out-of-order times, or times outside ten-minute steps from
  06:00 to 26:00. This is the exporter's supported simple-route subset, not a claim
  that every valid game schedule uses those restrictions.
- Unknown gift names or the same item assigned conflicting personal tastes.
- In a ready story scene: missing or unsafe trigger data, unsupported scene
  actions, invalid actor references, incomplete dialogue, unsafe command text,
  missing or cyclic prerequisites, or a prerequisite that is not ready.
- Missing, corrupt, oversized, animated, or non-PNG artwork. Each PNG is limited
  to 5 MB and 4,194,304 pixels, fully decoded, and checked for supported dimensions.
- Invalid enabled life rules, missing or unfinished prerequisites, or a repeated
  event used as lasting progress.
- Missing supporting-cast assets, a removed cast reference, duplicate identities,
  or a romanceable teen/child supporting character.
- Unsafe/missing map dependencies, invalid spouse-room bounds, disconnected map
  cycles, duplicate entrance tiles, immediate warp loops, or known custom-map
  tile bounds violated by homes, routes, scene actors, or movement.

Portrait sheets must be 128 pixels wide and at least 192 pixels high, in full
64-pixel rows. That gives two columns and at least six 64×64 expressions.
Sprites must be 64 pixels wide and at least 128 pixels high, in full 32-pixel
rows, using standard 16×32 frames. Romanceable characters require at least
416 pixels in height to include the default kissing and wedding frame positions
through frame 50. These are the template's supported layouts. File dimensions
cannot prove that meaningful or correctly ordered art has been drawn in a frame.

Supplied map dimensions are validated and supported tile layers can be previewed;
walkability, collision, tile actions, and pathfinding still require the game.
External map and actor names, unusual dialogue commands, modded gift IDs, artwork semantics,
spouse behavior, and save compatibility all require in-game testing. Test on a
backed-up save, check SMAPI logs, meet the NPC at their home tile, then sleep once
before checking the next day's full schedule.

When a pack contains story scenes, follow `STORY_TESTING.txt`. Enter each event
location under its listed conditions, play the scenes in prerequisite order,
and check movement, dialogue, friendship effects, completion, and subsequent
events. Structural validation and the editor preview do not load the game's
maps, resolve installed mod actors, or prove a scene will work at runtime.

The desktop installer writes only to a selected existing Mods folder. A new
installation is staged before becoming active. Updates require a matching
Pixelheart ownership marker and manifest ID; unrelated folders are never
replaced. The previous pack is retained under `Pixelheart backups` beside Mods,
outside the active mod search path. A failed final replacement restores it.

## Rights in exported packs

Pixelheart's software license permits users to publish, modify, and freely share
Stardew Valley NPC packs without separate permission from Pixelheart. The output
exception includes Pixelheart-owned template content intentionally written into
normal exports. Packs containing that material may not be sold, paywalled, or
used to earn Donation Points. The exception does not permit redistribution or
reuse of the editor's source code or executable.

NPCs may appear in gameplay videos and livestreams, including monetized content.
That exception does not permit charging for covered downloads. Uploaded images,
authored content, and other third-party material retain their own rights and
license requirements. Pixelheart is an unofficial fan tool and does not grant
rights on behalf of ConcernedApe or other creators. See [LICENSE](LICENSE).

## Source documentation

Reviewed September 19, 2026:

- [Content Patcher author guide](https://github.com/Pathoschild/StardewMods/blob/stable/ContentPatcher/docs/author-guide.md): current format 2.9.0, manifest and patches.
- [Content Patcher EditData](https://github.com/Pathoschild/StardewMods/blob/stable/ContentPatcher/docs/author-guide/action-editdata.md): edits to existing game dictionaries.
- [Content Patcher EditMap](https://github.com/Pathoschild/StardewMods/blob/stable/ContentPatcher/docs/author-guide/action-editmap.md): two-way `AddWarps` connections.
- [Location data](https://stardewvalleywiki.com/Modding:Location_data): `CreateOnLoad`, map paths, and arrival tiles.
- [Stardew Valley NPC data](https://stardewvalleywiki.com/Modding:NPC_data): `Data/Characters`, artwork, spawning, romance, and secondary assets.
- [Schedule data](https://stardewvalleywiki.com/Modding:Schedule_data): route syntax, facing, fallback schedules, and map behavior.
- [Gift taste data](https://stardewvalleywiki.com/Modding:Gift_taste_data): ordered reaction/reference fields and gift IDs.
- [Dialogue documentation](https://stardewvalleywiki.com/Modding:Dialogue): trigger keys, portrait selection, engagement and spouse dialogue.
- [Event data](https://stardewvalleywiki.com/Modding:Event_data): event preconditions, actor setup, commands, and friendship effects.
- [Bundled catalog provenance](pixelheart_core/data/README.md): pinned vanilla 1.6.15 IDs, names, and category metadata.

The NPC reference is linked from the Content Patcher maintainer's documentation.
The exporter targets its documented Stardew 1.6 model, not the pre-1.6
`Data/NPCDispositions` string format.

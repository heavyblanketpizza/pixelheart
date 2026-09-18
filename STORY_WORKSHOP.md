# From a story note to a playable scene

The Story workshop keeps an idea, its narrative outline, and its playable scene
together. You can save unfinished work at any point. Only scenes marked **Ready**
are included as event scripts in an exported Content Patcher pack; all stages
remain in the project backup.

Open **Story notes → Heart events** to work through **Idea → Outline → Scene
→ Rehearse**. The button above the steps moves to the next part of the flow.
Use **Relationship arcs** for a connection that develops over several events.

## Capture the moment

Choose a story starter and **New event**, or start a **New relationship**. Write the situation in the
character's own terms: what they want, what prevents it, and what they reveal
when the farmer becomes involved. A useful small scene has one change the player
can notice afterward.

For example: a guarded gardener wants to restore an abandoned plot, but is
afraid the town will laugh at another failed attempt. The farmer discovers the
seedlings before the gardener is ready to show anyone. The scene ends with an
invitation to return tomorrow.

Older event and relationship notes open as ideas. Their descriptions and IDs
are preserved, and opening or saving them does not opt them into export.

## Develop an outline

Use **They want…**, **But…**, and **By the end…** to develop the idea's premise,
conflict, and outcome. Describe
what the player witnesses and why it matters. Keep a relationship's current
dynamic and intended change alongside its events so that later scenes develop
the same thread.

In **Relationship arcs**, describe **A beginning**, **A complication**, **A turning
point**, and **A new connection**, then choose **Create four milestone events**.
This creates linked drafts at 2, 4, 6, and 8 hearts. Activate a milestone in the
relationship's list to open its event editor. Give each
milestone its own dramatic purpose: an introduction, a complication, a moment
of trust, and a payoff. Review the generated triggers, cast, and outline before
writing each scene. They are a starting structure for the author to complete.

**Part of a relationship** organizes the arc. **After event** supplies a
real in-game prerequisite: the player must have seen the earlier scene before
the next one can begin. A prerequisite needs a ready event, and circular or
missing prerequisites must be fixed before exporting a ready scene.

## Build the playable scene

Choose a location, minimum friendship, time window, season, weather, relationship
status, and optional farmhouse upgrade. Use the map's tile coordinates for the cast; the initial
camera centers on the authored NPC. The authored NPC (`$npc`) and player (`farmer`)
have dedicated actor references. Actor selectors show human-readable names for
vanilla and supporting characters. Supporting-cast references retain their
identity while their names are edited. Advanced external NPCs need their exact
internal names and the mod that supplies them. Imported places appear in location
selectors; known map dimensions are checked at export.

Choose a beat type and **Add beat**, then arrange the sequence with the up and
down buttons. **Dialogue** gives NPC speech or farmer narration, **Movement**
sets relative tile offsets and a final direction, **Expression** plays an emote,
**Pause** sets a delay, and **Friendship change** adds or removes points. Movement
uses the scene's current actor position, so inspect the starting tiles as well
as every movement. Friendship effects change the player's friendship with an
NPC, measured in points; 250 points equal one heart.

**Player choice** can be the final beat: write a question, two answers, and each
answer's closing response and friendship effect. Rehearse both outcomes. This
creates a real two-way event ending; it does not store a permanent answer flag
for later chapters. See [LIFE_AND_BRANCHING.md](LIFE_AND_BRANCHING.md).

Use one-time scenes for lasting chapters. A **Daily** scene requires Event
Repeater 6.5.8 or later, which the export lists as a required dependency. The
framework forgets those scene IDs after sleeping or reloading a save, including
the same day. Daily scenes cannot be prerequisites for lasting story or life
rules, and their friendship effects can happen again.

Use **Rehearse** to step through the sequence with **Previous**, **Next beat**,
and **Restart**. Enable **Show compiled event details** to inspect the event patch.
The rehearsal helps review pacing, continuity, and the connection between scene controls
and the export. It does not run Stardew Valley or prove that the chosen tiles
are walkable. The editor checks supported structure and flags missing or
unsafe input before a ready scene can enter an archive.

## Review and export

Activate a readiness message to move to the relevant scene control, then choose
**Mark ready for export** when the checks pass. **Review & export** takes you to
the character's export review. Editing a ready scene returns it to **Scene** so
you can rehearse and approve its inclusion again; **Keep as draft** also removes
it from the next export. Invalid ready scenes imported through project files
block export. Incomplete ideas and outlines stay in the project and do not
block the playable scenes.

A relationship can be marked ready when every linked scene is ready and valid.
Each linked scene must include the relationship's other character in its cast.
Review the relationship again after revising its scenes. Ready events can be
exported while the larger relationship remains in development.

Export still needs the character's normal identity, dialogue, schedule, and
complete portrait and sprite sheets. The pack includes:

- Ready events in `content.json`, as patches to the relevant location's events.
- Every draft, outline, relationship, and authored extension field in `project.json`.
- `STORY_TESTING.txt` when there are playable scenes, listing exact event IDs,
  trigger keys, relationship links, answer outcomes, repeat rules, and a testing checklist.

Event identity comes from the project's and event's stable IDs. Renaming or
reordering a scene keeps its event identity. Duplicating it creates an independent
idea with new identities and clears its prerequisite and relationship links.
Removing a prerequisite or a relationship is blocked while events refer to it.
An **Undo remove** action restores the last removed story, cast member, or beat
while you remain in that editor.
Keep the project ID, NPC internal name, and existing event IDs stable once a
player has used the pack in a save.

## Test the arc in Stardew Valley

Install the exported folder with SMAPI and Content Patcher on a backed-up test
save. Follow the included guide and test events in prerequisite order. Enter
the scene location under its listed conditions. Check the cast, dialogue,
movement, effects, completion, and return to normal gameplay. Check that a Once
scene stays complete on reentry and the following day. Check Daily scenes after
sleep and save reload with Event Repeater installed. Test each answer on separate
saves, including skipping before the question and during its closing response.
Then test the next milestone under its own conditions. Skip before and after
friendship beats and confirm the intended effects apply once and the arc can
continue. Inspect SMAPI's log for errors.

Record your findings alongside the scene's outline and revise it in Pixelheart.
An editor preview or a passing structural check is not evidence that the event
has been tested in-game. Test external NPCs and custom maps with the mods that
provide them installed.

The workshop supports structured scenes, final two-answer choices, and relationship
arcs through linked scenes and player friendship effects. [Life & reactions](LIFE_AND_BRANCHING.md)
connects progress to everyday conversations and routines, including married life.
[Cast & locations](WORLD_BUILDING.md) adds supporting characters and supplied maps.
Arbitrary nested event scripting and private family or relationship simulations
between NPCs remain outside this structured workflow. See [EXPORT_FORMAT.md](EXPORT_FORMAT.md)
for the complete supported scope.

References: [Content Patcher event patches](https://github.com/Pathoschild/StardewMods/blob/stable/ContentPatcher/docs/author-guide/action-editdata.md)
and [Stardew Valley event data](https://stardewvalleywiki.com/Modding:Event_data).

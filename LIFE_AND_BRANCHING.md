# Give the character a life beyond the first meeting

**Life & reactions** connects completed story chapters to ordinary conversations,
changing routines, and authored spouse dialogue. **Story notes** adds relationship
and farmhouse conditions, repeatable scenes, and a player choice with two endings.
Everything is authored locally. These tools do not generate content during play.

## Start with one visible change

1. Review the first story event and mark it ready in **Story notes**.
2. Open **Life & reactions → Conversations** and add a conversation.
3. Name it, choose that event under **After this story event**, and write what the
   character says after the player has helped them. Use `@` for the farmer's name.
4. Check **Include this rule in the next mod export** after reviewing the text.
5. Export, play the event, then sleep and speak to the character the next day.

Rules are applied each morning. An after-event conversation therefore takes
effect the next in-game day. The lower matching rule wins when several enabled
rules change the same conversation or routine. Use **Move up** and **Move down**
to put broad rules first and more specific changes afterward.

Unchecked rules are drafts. They stay in the project and are omitted from the
playable mod. An enabled rule that refers to a missing or unfinished event blocks
export with a link back to its editor. A repeating event cannot be used as a
lasting story prerequisite: its completion is forgotten by the repeat framework.

Conversations change weekday greetings. The game's special date, festival,
location, and more specific authored dialogue can still have higher priority.
Choosing **Married to this NPC** changes the spouse's home conversations instead.
The **Married life** tab lets you write a particular morning or evening separately.

## Seasonal, rainy, dating, and married routines

Open **Life & reactions → Routines**, add a routine, and select its conditions. The
initial stops copy the normal **Schedule** so there is something concrete to edit.
Season, weather, weekday, hearts, completed story, and relationship can be combined.
The lower matching routine takes priority over earlier alternatives.

Ordinary routines apply before marriage. Choose **Married to this NPC** explicitly
for a route after the wedding. The exporter uses dated marriage routes so your
rainy married routine can run on rainy days too.

Use **Return to farmhouse** to add the game's home destination at the end of a
married routine. That destination is resolved by the game; you do not need to guess
where the spouse's bed is. All other stops still need valid map tiles and connected
walking routes. The preview cannot establish whether a route is walkable.

Sleep after installing or changing a route before evaluating it. In co-op, NPC
schedules are shared and evaluated by the host; test with the intended host and
farmhand relationship setup before distributing a pack for multiplayer use.

## Write their married voice

In **Life & reactions → Married life**, choose a morning, rainy morning, evening, or
rainy evening. The authored text fills the random slots for that moment, so a
missing random slot does not unexpectedly replace your line with generic text.
More specific rules can change those words after a later chapter or in a season.

The game retains its default spouse interactions outside those four categories.
This editor does not claim to author every marriage interaction, festival, or
animation. Custom rooms and other characters belong in **Cast & locations**.
The [map workshop guide](MAP_WORKSHOP.md) covers painting a supplied tilesheet,
connecting the place, and reopening it for revisions after a game test.

## Give a scene two endings

Add **Player choice** as the last scene beat. Write a question, two player answers,
and the NPC's response to each answer. Each answer may have its own friendship
change. Rehearse both outcomes before marking the scene ready.

The exported event asks the player a real question and forks to the selected
closing response. Skipping before answering gives neither answer's friendship
effect. After answering, skipping preserves only the selected ending's remaining
effect. This is a final two-way choice; it is not an arbitrary nested conversation
tree, and it does not create a permanent choice flag for later chapters.

For later events, choose **Dating**, **Married**, or **Not married to this NPC**
and, if needed, a minimum farmhouse upgrade. Married conditions require an actual
marriage; an engagement alone does not satisfy them.

## Install a repeating scene

Normal packs require **Stardew Valley 1.6**, **SMAPI 4 or later**, and **Content
Patcher 2.9.0 or later**. A ready scene set to repeat also requires **Event Repeater
6.5.8 or later**. Export automatically adds that required dependency; it does not
download or install the framework for you.

1. Install [SMAPI](https://smapi.io/) using its included installer.
2. Put [Content Patcher](https://www.nexusmods.com/stardewvalley/mods/1915) in the
   game's `Mods` directory.
3. For a repeating scene, also put
   [Event Repeater](https://www.nexusmods.com/stardewvalley/mods/3642) in `Mods`.
4. Extract the exported NPC pack into `Mods` and start the game through SMAPI.
5. Follow the exported story testing guide on a backed-up test save. Confirm SMAPI
   loaded every required framework before testing the NPC.

Event Repeater forgets selected events at the beginning of a day, including a
save reload. A repeatable scene can therefore recur after reloading the same day.
Its friendship effects are repeatable too. Use a one-time scene for irreversible
story progress and a separate repeatable scene for a recurring visit or date.

## In-game verification and reference

Test each branch, each routine, the wedding transition, and repeat behavior
in the game. The editor does not run your exported character inside Stardew Valley.

The compiler follows [Content Patcher tokens and daily conditions](https://github.com/Pathoschild/StardewMods/blob/stable/ContentPatcher/docs/author-guide/tokens.md),
[schedule selection and home destinations](https://stardewvalleywiki.com/Modding:Schedule_data),
[spouse dialogue keys](https://stardewvalleywiki.com/Modding:Dialogue#Marriage_dialogue),
[event questions and forks](https://stardewvalleywiki.com/Modding:Event_data), and
[relationship and house game-state queries](https://stardewvalleywiki.com/Modding:Game_state_queries).
Event Repeater integration was checked against its
[string event-ID model](https://github.com/MissCoriel/Event-Repeater/blob/master/ThingstoForget.cs)
and [content-pack reader and daily reset](https://github.com/MissCoriel/Event-Repeater/blob/master/ModEntry.cs).
The repeat list uses concrete IDs because that reader does not expand Content
Patcher tokens.

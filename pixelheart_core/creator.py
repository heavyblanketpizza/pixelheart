"""A deterministic, offline creator journey backed by real authoring records.

Curated story families supply editable prose; free-form concepts are kept as the
creator's brief, never misrepresented as AI-understood story generation. All
public operations return copies. Applying a preview requires its exact source
revision, and playtest evidence belongs to an exported, installed revision.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import re
import uuid

from .story import event_game_id, event_issues, exported_npc_id, normalize_event, normalize_relationship
from .dialogue_templates import MAX_DIALOGUES


ARCHETYPES = {
    "guarded_guardian": "The guarded guardian",
    "wandering_artist": "The wandering artist",
    "gentle_healer": "The gentle healer",
}
RELATIONSHIPS = ("romance", "friendship")
PLAYTEST_STATUSES = ("untested", "passed", "failed")
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_TRACKING = {"tests", "exports", "installation", "installations", "last_export", "last_install", "updated_at"}


class CreatorError(ValueError):
    """The requested creator operation would lose work or record false evidence."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _id(character, role):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pixelheart:creator:{character['id']}:{role}"))


def new_brief():
    return {"name": "", "concept": "", "archetype": "guarded_guardian",
            "motivation": "Find somewhere safe to belong", "flaw": "Mistakes accepting help for weakness",
            "arrival": "Arrives in Pelican Town hoping for a fresh start",
            "relationship": "romance", "companion_name": ""}


def _brief(value):
    if not isinstance(value, dict):
        raise CreatorError("The character brief must be an object.")
    result = {**new_brief(), **copy.deepcopy(value)}
    for key, limit in (("name", 64), ("concept", 4000), ("motivation", 1000), ("flaw", 1000),
                       ("arrival", 1000), ("companion_name", 64)):
        if (not isinstance(result[key], str) or len(result[key]) > limit or "\x00" in result[key]
                or any(0xD800 <= ord(char) <= 0xDFFF for char in result[key])):
            raise CreatorError(f"{key.replace('_', ' ').title()} must be text of at most {limit} characters.")
        result[key] = result[key].strip()
    if not result["name"]:
        raise CreatorError("Give your character a name before building a preview.")
    if not isinstance(result["archetype"], str) or result["archetype"] not in ARCHETYPES:
        raise CreatorError("Choose one of the available story families.")
    if result["relationship"] not in RELATIONSHIPS:
        raise CreatorError("Choose romance or friendship.")
    if result["companion_name"].casefold() == result["name"].casefold():
        raise CreatorError("The supporting character needs a different name.")
    return result


def content_fingerprint(document):
    """Hash authoring content, excluding evidence records and volatile timestamps."""
    def clean(value, path=()):
        if isinstance(value, dict):
            result = {key: clean(item, path + (key,)) for key, item in value.items()
                      if key not in {"created_at", "updated_at"}
                      and not (path == ("creator",) and key in _TRACKING)}
            if not path and result.get("creator") == {}:
                result.pop("creator")
            return result
        if isinstance(value, list):
            return [clean(item, path) for item in value]
        return value
    try:
        payload = json.dumps(clean(document), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise CreatorError("Project content must be valid JSON.") from exc
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _spoken(value):
    """Render a display name as inert dialogue, preserving the original brief."""
    return value.translate(str.maketrans({'"': '“', '\\': ' ', '/': '∕', '$': '', '#': '', '[': '(', ']': ')', '{': '(', '}': ')'}))


def _family(archetype):
    """Original, authored dialogue, shared only within an explicitly chosen family."""
    return {
        "guarded_guardian": {
            "occupation": "Repairer and travelling guardian", "tagline": "Learning that a home is something you build with people.",
            "introduction": "I'm {name}. If a gate jams or a hinge breaks, I can fix it. Small jobs are a good way to learn a place.",
            "week": ["I checked the bridge this morning. It is sound. Checking twice is an old habit.",
                     "The quiet here still catches me off guard. I keep listening for something that isn't there.",
                     "Gus left a bowl out for me again. He says it was extra. I think he's being kind.",
                     "A good repair doesn't hide the old crack. It gives the thing another chance.",
                     "Do you know how to tell a useful knot from a fancy one? Pull on it when nobody is watching.",
                     "There is room beside me, if you want to sit. We don't have to talk.",
                     "I used to count the roads out of a town. Today I counted the people I would miss."],
            "gifts": {"love": ["Coffee", "Goat Cheese"], "like": ["Wood", "Leek"], "dislike": ["Clay"], "hate": ["Trash"]},
            "chapters": [
                ("A strap that holds", "A damaged supply bag interrupts an arrival.", "Accepting practical help feels dangerously close to owing someone.", "The farmer is trusted with a small task and invited to return.",
                 ["Hold a moment. The buckle has gone, and if I let go everything ends up in the dust.",
                  "You hold the bag steady while {name} threads the loose strap through a second loop.",
                  "That will hold. Thank you. I had a speech ready about managing on my own. It seems I don't need it.",
                  "I'm staying nearby for now. If your gate ever sags, find me. I'd like to return the favour."]),
                ("The spare cup", "A second cup appears during a familiar break.", "The guardian dismisses a deliberate invitation as an accident.", "Shared company becomes a chosen routine.",
                 ["I made too much coffee. Again. There's a spare cup if you have a minute.",
                  "There are two clean cups beside a pot just large enough for two people.",
                  "All right. It wasn't an accident. I thought you might come by, and I wanted you to have something warm.",
                  "Same time another day? You don't have to bring a problem for me to fix."]),
                ("The closed gate", "The guardian turns away help after a minor setback.", "Protection becomes control and pushes the farmer away.", "The guardian names the mistake and asks for space without shutting the farmer out.",
                 ["I moved the supplies myself. I know we agreed to do it together. I didn't want you getting hurt.",
                  "The farmer points out that being trusted includes being allowed to decide.",
                  "I made your choice for you. Calling it protection doesn't change that.",
                  "I need a little time to put this right. Will you come back when I can ask instead of decide?"]),
                ("Two hands on the rope", "A shared repair provides a chance to keep a difficult promise.", "The guardian must ask for support before the work becomes unsafe.", "The repair succeeds through cooperation and trust.",
                 ["I left the last knot for us. I could reach it alone, but that isn't what I promised.",
                  "Could you hold this end? Tell me when you're ready. We'll pull together.",
                  "The rope settles into place. Neither person has to carry the whole weight.",
                  "I was afraid asking would make me less useful. It made room for you. That's different."]),
                ("A key with no lock", "A keepsake turns the idea of home into a choice.", "The guardian still keeps an escape plan instead of making a commitment.", "The pair choose a shared future, with room for honest uncertainty.",
                 ["I kept this old key long after the door was gone. It was easier than admitting I needed a new home.",
                  "I don't need you to replace anything I lost. I want to make something here with you.",
                  "The farmer stays, and the silence between them is comfortable.",
                  "Tomorrow I'd like to start with something small. Breakfast, perhaps. And no excuse about spare cups."]),
                ("A promise said aloud", "A private invitation becomes an honest declaration.", "Vulnerability cannot be hidden behind practical favours.", "They name their love without promising to erase every difficulty.",
                 ["I've repaired three things this morning that weren't broken. I was putting off saying this.",
                  "I love you. Not because you need me. Because I can be myself beside you, even when I'm afraid.",
                  "The farmer takes {name}'s hand.",
                  "Let's choose each other in the ordinary days too. Especially those."]),
                ("Learning our mornings", "Life together reveals incompatible habits.", "A habit of leaving before dawn makes the shared home feel empty.", "The couple agree on one daily ritual and keep space for independent work.",
                 ["I left before sunrise again. I thought getting the work done early would make the day easier for you.",
                  "But a home isn't a list of jobs I can finish before anyone notices I'm here.",
                  "The farmer sets a second place at the table.",
                  "Breakfast together. Then we each have our own day. I can learn that rhythm."]),
                ("The door stays open", "The guardian prepares to welcome others into the life they built.", "Sharing a home tests old fears that safety must come from isolation.", "The relationship becomes a source of welcome and belonging.",
                 ["I used to think keeping everyone outside was the same as keeping us safe.",
                  "Today I asked our neighbours to stop by. Nothing grand. A table, a pot of something warm, enough chairs.",
                  "The farmer and {name} make room for one more chair.",
                  "This is the part I couldn't build alone. Thank you for building it with me."]),
            ],
        },
        "wandering_artist": {
            "occupation": "Travelling illustrator", "tagline": "Finding the courage to finish something and stay.",
            "introduction": "I'm {name}. I came to sketch the valley. At this rate, I may need more than one notebook.",
            "week": ["The morning light changes the same fence into a different picture every day.",
                     "I tore out a page yesterday. Today I wish I'd kept it. Even mistakes remember something.",
                     "The town has such good faces. I ask before drawing anyone; a portrait is a little piece of trust.",
                     "I used to call leaving inspiration. Sometimes it was just easier than finishing.",
                     "There is a shade of green in your fields that I still haven't managed to mix.",
                     "Will you sit for a sketch sometime? You can keep moving. I like a drawing that breathes.",
                     "I counted my notebooks this morning. For once, I didn't count how many would fit in my bag."],
            "gifts": {"love": ["Sunflower", "Amethyst"], "like": ["Daffodil", "Coffee"], "dislike": ["Clay"], "hate": ["Trash"]},
            "chapters": [
                ("A page in the wind", "A loose drawing reaches the farmer before its artist does.", "The artist is embarrassed by an unfinished work.", "The farmer's attention encourages a second meeting.",
                 ["Wait! That paper by your shoe is mine. The wind has stronger opinions about composition than I do.",
                  "The farmer returns a sketch of the town, full of small unfinished details.",
                  "Thank you. I'm {name}. I was going to hide that one until it was better. You looked at it like it already mattered.",
                  "If you pass this way again, I'll show you what I noticed next."]),
                ("One ordinary thing", "The artist invites the farmer to choose a subject.", "A fear of being unoriginal makes simple observation difficult.", "They discover a shared way of paying attention.",
                 ["Choose something ordinary. Not the prettiest thing. Something you would miss if it disappeared.",
                  "The farmer points to a worn patch of path where neighbours stop to talk.",
                  "I would have walked right past it. Now I can see all the people who made it.",
                  "Could we do this again? You notice things I forget to look for."]),
                ("The blank wall", "An invitation to show work exposes the artist's fear of judgement.", "The artist dismisses the farmer's encouragement as empty kindness.", "They acknowledge the hurt and ask for honest feedback.",
                 ["They asked me to show a few drawings. I said I was leaving soon. I haven't actually bought a ticket.",
                  "The farmer asks whether leaving is what {name} wants.",
                  "That wasn't fair, calling your encouragement easy. You have been paying attention the whole time.",
                  "Will you help me choose one drawing? You can tell me what doesn't work. I will try to listen."]),
                ("The first nail", "The artist hangs a finished piece with the farmer's help.", "Public commitment means accepting that a finished work can still be imperfect.", "They complete the small exhibition together.",
                 ["The frame isn't perfectly straight. I have checked it six times. Please stop me before seven.",
                  "The farmer steps back beside {name} to look at the drawing.",
                  "It looks different when I imagine someone enjoying it instead of catching me out.",
                  "Thank you for helping me finish. That was the brave part, wasn't it?"]),
                ("A place in the picture", "A new drawing includes a life the artist hopes to keep.", "The artist must choose belonging without giving up curiosity.", "They plan future adventures from a shared home.",
                 ["I drew the road out of town again. This time there is a road coming back.",
                  "I still want to see unfamiliar places. I just want somewhere, and someone, to tell about them.",
                  "The farmer finds two familiar figures in the corner of the drawing.",
                  "I left that part unfinished. I thought we might decide what happens there together."]),
                ("The portrait I kept", "A private portrait reveals the artist's feelings.", "Sincerity feels more exposed than any public artwork.", "They choose love as an ongoing collaboration.",
                 ["You have appeared in more of my drawings than I meant to admit. Even the ones without a person in them.",
                  "I love the way you make a place feel lived in. I love you.",
                  "The farmer draws a small heart in the margin, then offers the pencil back.",
                  "Yes. That is exactly the finishing touch it needed."]),
                ("Room for two desks", "Shared living creates a small, persistent disagreement.", "The artist mistakes a request for space as rejection of their work.", "They make room for each other's work and rest.",
                 ["I found another pencil in your breakfast bowl. I promise that wasn't a proposal for a new recipe.",
                  "I spread out when I feel at home. I forgot that this is your space too.",
                  "Together, the farmer and {name} set aside a place for work and a place to sit together.",
                  "There. Room for what we each make, and room for us."]),
                ("The valley, unfinished", "The couple begin a shared album of ordinary days.", "The artist learns that a continuing life doesn't need a perfect ending.", "Their work becomes a record of a home that keeps growing.",
                 ["I used to think the last page had to explain all the others. Now I hope we keep needing new pages.",
                  "I drew our first year here. Not just the beautiful days. The burned dinners deserve a place too.",
                  "The farmer adds a date beneath the first picture.",
                  "Shall we leave the next page open? We have a whole ordinary day to fill it."]),
            ],
        },
        "gentle_healer": {
            "occupation": "Herbalist and community carer", "tagline": "Learning that care can travel both ways.",
            "introduction": "Hello, I'm {name}. I'm learning which plants thrive here, and which neighbours might need a warm meal.",
            "week": ["I packed an extra lunch. This time I remembered one for myself too.",
                     "Plants tell you when they need a rest. People are a little harder to read.",
                     "A neighbour returned one of my bowls with soup in it. I think I understand what they were saying.",
                     "I am practising a difficult sentence: I can help tomorrow, but today I need to rest.",
                     "You do a lot for this town. I hope someone asks how you are, and waits for the answer.",
                     "Some days the best thing I can offer is company. Today can be one of those days.",
                     "I left the afternoon empty. It used to make me anxious. Now it feels like room to breathe."],
            "gifts": {"love": ["Peach", "Goat Cheese"], "like": ["Leek", "Daffodil"], "dislike": ["Joja Cola"], "hate": ["Trash"]},
            "chapters": [
                ("One bowl too many", "An overfilled basket forces the newcomer to stop.", "The healer wants to help everyone before attending to their own needs.", "The farmer shares the load and learns the healer's name.",
                 ["Could you catch that bowl? I was certain one more would fit in the basket.",
                  "The farmer steadies the basket while {name} puts the bowls back in place.",
                  "Thank you. I'm {name}. These are for neighbours who have had a long week. Mine is in here somewhere. I think.",
                  "You've already helped more than you know. Next time, let's share a meal while it's still warm."]),
                ("Something for yourself", "A shared break reveals how little the healer keeps for themselves.", "Accepting kindness feels like taking resources from someone else.", "They practise receiving care without earning it first.",
                 ["I brought something to share. Before you ask, yes, I brought enough for both of us this time.",
                  "The farmer makes space for {name} to sit down.",
                  "I nearly found another errand on the way here. Sitting still feels strange when someone might need me.",
                  "But you wanted my company. I think I can let that be enough."]),
                ("An empty basket", "The healer misses a promise after taking on too much.", "Good intentions cannot excuse the hurt caused by overcommitting.", "They admit their limits and ask to repair the broken promise.",
                 ["I forgot we were meeting. I kept saying yes to one more thing, and then the day was gone.",
                  "The farmer explains that they were worried, and that waiting hurt.",
                  "You deserved a message. Being needed elsewhere didn't make our promise less important.",
                  "I cannot do everything tomorrow. I can keep one promise to you. May we start there?"]),
                ("The answer is tomorrow", "A quiet afternoon tests a newly chosen boundary.", "The healer is tempted to abandon rest for another non-urgent task.", "They keep their commitment and accept support.",
                 ["Someone asked for a hand this afternoon. I checked: it can wait until tomorrow. So I said tomorrow.",
                  "That sounds small when I say it out loud. My hands are still shaking a little.",
                  "The farmer sits beside {name} until the tension eases.",
                  "Nothing terrible happened. I kept time for us, and the world kept turning."]),
                ("Care travels both ways", "A familiar bowl returns carrying a gesture of care.", "The healer must accept belonging that isn't based on usefulness.", "They build a relationship where both people can need support.",
                 ["You brought back my bowl. And you filled it. I seem to be running out of ways to insist I don't need anything.",
                  "I want to be here because I am happy here. Not because I have made myself impossible to do without.",
                  "The farmer offers the first spoonful to {name}.",
                  "All right. Today you can take care of me. Tomorrow we can see what each of us needs."]),
                ("A wish without a task", "The healer shares a desire without turning it into an obligation.", "Love requires asking for something they want for themselves.", "They confess their love and leave room for an honest answer.",
                 ["I kept trying to make this into a sensible plan. But it is a wish, and I should let it be one.",
                  "I love you. I want more ordinary afternoons beside you, and mornings when neither of us needs to hurry.",
                  "The farmer reaches for {name}'s hand.",
                  "Thank you for hearing what I wanted, even when I was still learning to say it."]),
                ("The list on the table", "Household work quietly falls into an uneven pattern.", "The healer must discuss resentment before it turns into self-sacrifice.", "The couple divide their work and protect shared rest.",
                 ["I made a list of everything I said I could manage. Looking at it now, I wasn't being honest with either of us.",
                  "Could we divide these together? I want to ask before I get tired enough to be cross with you.",
                  "The farmer and {name} cross out what can wait and share what needs doing.",
                  "There is a blank space at the bottom. Let's keep that for us."]),
                ("Enough places at the table", "The couple host a meal without doing everything themselves.", "Welcoming a community requires trusting it to contribute.", "A shared tradition replaces one person's impossible burden.",
                 ["I asked everyone to bring something. Someone brings bread, someone brings chairs, and we bring ourselves.",
                  "A year ago I would have tried to carry the whole evening in one basket.",
                  "The farmer places the last empty bowl on the table, ready for whoever arrives.",
                  "Look at this. Nobody has to earn a place here. Not even me."]),
            ],
        },
    }[archetype]


def _event(character, brief, family, index, hearts, previous, relationship_id):
    title, premise, conflict, outcome, lines = family["chapters"][index]
    event_id = _id(character, f"chapter:{hearts}")
    name = _spoken(character["name"])
    event = normalize_event({"id": event_id, "name": title, "hearts": hearts,
                             "location": character.get("home_map", "Town"), "description": premise,
                             "creator_role": f"chapter:{hearts}"})
    x, y = character.get("home_x", 32), character.get("home_y", 62)
    event["story"].update(stage="scene", premise=premise, conflict=conflict, outcome=outcome,
                           relationship_id=relationship_id, previous_event_id=previous,
                           time_start=900, time_end=1800,
                           actors=[{"id": _id(character, f"chapter:{hearts}:npc"), "name": "$npc", "x": x, "y": y, "facing": 2},
                                   {"id": _id(character, f"chapter:{hearts}:farmer"), "name": "farmer", "x": x, "y": max(0, y - 2) if y > 998 else y + 2, "facing": 0}],
                           beats=[])
    for line_index, line in enumerate(lines):
        # Narration belongs to farmer's message beat; the other lines are NPC speech.
        actor = "farmer" if line.startswith(("The farmer", "Together,", "The rope", "There are", "You hold")) else "$npc"
        event["story"]["beats"].append({"id": _id(character, f"chapter:{hearts}:line:{line_index}"),
                                       "kind": "dialogue", "actor": actor, "text": line.format(name=name)})
    choices = (
        ("Will I see you around?", "I'd like that.", "Then I'll look forward to it. Thank you for stopping.",
         "I'll stop by when I can.", "Of course. A familiar face is welcome whenever the day allows."),
        ("Shall we make time for this again?", "Let's make it a regular thing.", "I would like that more than I know how to say.$h",
         "Let's take it one day at a time.", "One day at a time sounds good. I'm glad you were here for this one."),
        ("What do you need from me now?", "A chance to put this right together.", "Then I'll listen, and this time we'll decide together.",
         "Some time before we try again.", "I understand. You don't owe me a quick answer. I'll be here when you're ready."),
        ("Could we try trusting each other with the next step?", "We make a good team.", "We do. And I want to keep learning how to be part of it.$h",
         "Let's keep talking as we go.", "Yes. Even when it's awkward. Especially then."),
        ("What should tomorrow look like?", "Something ordinary, together.", "I think ordinary might be the best kind of beginning.$h",
         "Let's leave room for a surprise.", "All right. For once, I can look forward to not knowing everything."),
        ("May I stay beside you a little longer?", "Stay as long as you like.", "Then there is nowhere else I'd rather be.$l",
         "Let's take a walk together.", "Yes. We have a whole evening, and I want to spend it with you.$l"),
        ("What shall we make time for tomorrow?", "Breakfast together.", "I'll leave the morning open. And I'll remember to sit down.$h",
         "A quiet evening at home.", "It's a promise. Whatever the day brings, we'll have that to come home to."),
        ("What do you hope we carry into the next year?", "Time for each other.", "Then we will keep making it. One ordinary day at a time.$l",
         "Room for new adventures.", "Together, and each in our own way. I love that our story still has room to grow.$h"),
    )
    question, label_a, response_a, label_b, response_b = choices[index]
    event["story"]["beats"].append({"id": _id(character, f"chapter:{hearts}:choice"),
        "kind": "choice", "actor": "$npc", "text": question,
        "choices": [{"id": _id(character, f"chapter:{hearts}:answer:{option}"), "label": label,
                     "text": response, "friendship": points}
                    for option, label, response, points in ((0, label_a, response_a, 25), (1, label_b, response_b, 10))]})
    if hearts >= 10:
        event["story"]["relationship"] = "married" if hearts >= 12 else "dating"
    return normalize_event(event)


def _seed_life(character, chapter_refs, additions, preserved):
    """Create actual exportable everyday rules and reviewable story followups."""
    life = character.setdefault("life", {})
    if not isinstance(life, dict):
        raise CreatorError("Everyday-life authoring data must be an object.")
    count = 0
    for collection in ("dialogues", "routines", "spouse_dialogue"):
        life.setdefault(collection, [])
        if not isinstance(life[collection], list):
            raise CreatorError("Everyday-life rules must be lists.")

    def add(collection, role, entry):
        nonlocal count
        identity = _id(character, "life:" + role)
        if any(item.get("id") == identity for item in life[collection]):
            return
        life[collection].append({"id": identity, **entry})
        count += 1

    for season, text in (
        ("spring", "The first green always catches me by surprise. It makes starting again seem possible."),
        ("summer", "I saved the quiet part of the afternoon for myself. Would you like to share it?"),
        ("fall", "The valley is changing colour. I like watching a familiar place find a new way to be beautiful."),
        ("winter", "It is easier to notice a warm window in winter. I hope someone leaves one lit for you."),
    ):
        add("dialogues", "season:" + season, {"name": season.title() + " reflection", "enabled": True,
            "text": text, "conditions": {"season": season, "weekday": "Sun"}})
    add("dialogues", "rain", {"name": "A rainy-day conversation", "enabled": True,
        "text": "You came all this way in the rain. Stay a moment. There is no need to hurry back into it.",
        "conditions": {"weather": "rainy", "weekday": "Sat"}})
    followups = [
        "I checked that repair this morning. It is holding. I'm glad our paths crossed.",
        "I made time for a break today. It helps to have something, or someone, to look forward to.",
        "I've been thinking about what you said. You deserved to be heard. I am trying to do better.",
        "We made a good team. Next time I need help, I'll try asking before making things difficult.",
        "I like having a tomorrow here to look forward to. Thank you for being part of it.",
        "I keep smiling for no practical reason. You probably know why.$h",
        "Our mornings feel more like ours now. Even when the breakfast goes a little wrong.$h",
        "There is room at our table, and room in this life. I love what we're making together.$h",
    ]
    # Family-independent followups describe relationship changes, not prop details.
    followups[0] = "I am glad our paths crossed. Small kindnesses have a way of staying with a person."
    for index, chapter in enumerate(chapter_refs):
        add("dialogues", "chapter:" + str(chapter["hearts"]), {
            "name": f"After the {chapter['hearts']}-heart chapter", "enabled": False,
            "text": followups[index], "conditions": {"after_event_id": chapter["id"], "min_hearts": chapter["hearts"]}})
    for season in ("summer", "fall", "winter"):
        stops = copy.deepcopy(character["schedule"])
        for index, stop in enumerate(stops):
            stop["id"] = _id(character, f"life:routine:{season}:{index}")
        add("routines", "routine:" + season, {"name": season.title() + " daily route", "enabled": False,
            "conditions": {"season": season}, "stops": stops})
    rainy = copy.deepcopy(character["schedule"])
    for index, stop in enumerate(rainy):
        stop["id"] = _id(character, f"life:routine:rain:{index}")
    add("routines", "routine:rain", {"name": "Rainy-day route", "enabled": False,
        "conditions": {"weather": "rainy"}, "stops": rainy})
    if character.get("romanceable"):
        for moment, title, text in (
            ("morning", "A morning together", "Good morning, love. I left time for breakfast together before the day carries us away.$h"),
            ("rainy_morning", "Rain at the window", "Listen to that rain. We can have a slow beginning today, can't we?$h"),
            ("evening", "Coming home", "There you are. Come and tell me about your day. The good parts and the difficult ones."),
            ("rainy_evening", "A warm evening", "I am glad you're home and out of the rain. Sit with me until you're warm.$h"),
        ):
            add("spouse_dialogue", "spouse:" + moment, {"name": title, "enabled": True,
                "moment": moment, "text": text, "conditions": {"relationship": "married"}})
        married_stops = copy.deepcopy(character["schedule"])
        for index, stop in enumerate(married_stops):
            stop["id"] = _id(character, f"life:routine:married:{index}")
        married_stops[-1].update(location="bed", x=0, y=0, time="2200", facing="down", activity="Return home to the farmhouse")
        add("routines", "routine:married", {"name": "A married day with time of their own", "enabled": False,
            "conditions": {"relationship": "married"}, "stops": married_stops})
    if count:
        additions.append({"section": "Everyday life", "title": f"{count} everyday-life rules",
            "detail": "Authored seasonal, rainy-day and spouse conversations; chapter followups and seasonal/rain routes are drafts to review and enable as chapters become ready."})
    from .life import normalize_life
    character["life"] = normalize_life(life)


def _seed_companion(document, brief, additions, preserved):
    if not brief["companion_name"]:
        return
    from .world import cast_actor_id, new_companion, new_world
    character = document["character"]
    world = document.setdefault("world", new_world())
    companion_id = _id(character, "companion")
    records = world.setdefault("characters", [])
    if any(record.get("id") == companion_id for record in records):
        preserved.append("Kept your supporting character, including their identity and artwork.")
        return
    companion = new_companion(brief["companion_name"], age="adult")
    companion["id"] = companion_id
    npc = companion["character"]
    npc["id"] = companion_id
    used_names = {character.get("internal_name"), *(record.get("character", {}).get("internal_name") for record in records)}
    if npc["internal_name"] in used_names:
        npc["internal_name"] = npc["internal_name"][:54] + "Companion"
    npc.update(tagline="An old travelling friend finding a life of their own.", occupation="Travelling craftsperson",
               bio=f"A trusted adult friend of {character['name']}. They arrived together, but each is learning to build a place in the valley in their own way.",
               home_map=character.get("home_map", "Town"), home_x=character.get("home_x", 32) - 2 if character.get("home_x", 32) > 998 else character.get("home_x", 32) + 2,
               home_y=character.get("home_y", 62))
    lines = [f"I'm {_spoken(npc['name'])}. {_spoken(character['name'])} and I travelled here together. Now I'm looking forward to finding my own favourite places.",
             "Travelling with someone teaches you a lot about them. So does watching them finally unpack.",
             "I found a small job to do today. It feels good to be useful somewhere I might stay.",
             "Have you tried sitting quietly near the square? The whole town tells its story if you listen.",
             "Old friends remember who you were. Good friends leave room for who you're becoming.",
             "I should write to someone from the road. I have proper news now, not just another change of address.",
             "We used to share every meal because there was nobody else around. Now we choose to. I like that better.",
             "A familiar face. That still feels like a gift to me.$h"]
    npc["dialogues"] = [{"id": _id(npc, "dialogue:" + trigger), "trigger": trigger, "text": text}
                        for trigger, text in zip(("Introduction", *_DAYS), lines)]
    npc["schedule"] = [{"id": _id(npc, "schedule:" + time), "time": time, "location": npc["home_map"],
                         "x": npc["home_x"], "y": npc["home_y"], "facing": "down", "activity": activity}
                        for time, activity in (("600", "Begin the day"), ("1200", "Work and greet neighbours"), ("2200", "Rest"))]
    npc["gifts"] = {"love": ["Peach"], "like": ["Coffee", "Wood"], "dislike": ["Clay"], "hate": ["Trash"]}
    records.append(companion)
    # Link an actual cast member into a new scene only. Never alter an existing
    # scene on a later regeneration; creators own every line after the first apply.
    intro = next((event for event in character["events"] if event.get("id") == _id(character, "chapter:0")), None)
    if intro and not document.get("creator", {}).get("chapters"):
        actor = cast_actor_id(companion)
        intro["story"]["actors"].append({"id": _id(character, "chapter:0:companion"), "name": actor,
                                          "x": npc["home_x"], "y": npc["home_y"], "facing": 3})
        intro["story"]["beats"].insert(-1, {"id": _id(character, "chapter:0:companion:line"),
            "kind": "dialogue", "actor": actor,
            "text": f"I'm {_spoken(npc['name'])}. We've been on the road a long time. It is good to meet someone who makes stopping feel easy."})
        normalized = normalize_event(intro)
        intro.clear()
        intro.update(normalized)
    additions.append({"section": "World", "title": "An independent supporting character",
        "detail": f"{npc['name']} has their own identity, weekday dialogue, gifts and routine. Import their portrait and sprite in World before exporting."})


def build_proposal(document, brief):
    """Preview a complete editable starter without mutating or replacing authored work.

    Untouched new-project identity, introduction and routine defaults are the only
    automatic replacements. Every other existing entry is preserved. Subsequent
    previews add missing role IDs and never reset edits or scene readiness.
    """
    brief = _brief(brief)
    if not isinstance(document, dict) or not isinstance(document.get("character"), dict):
        raise CreatorError("Open a character project before creating a preview.")
    result = copy.deepcopy(document)
    character = result["character"]
    if not isinstance(character.get("id"), str) or not character["id"]:
        raise CreatorError("Save this character with a stable project identity first.")
    additions, preserved = [], []
    family = _family(brief["archetype"])
    creator = result.setdefault("creator", {})
    if not isinstance(creator, dict):
        raise CreatorError("Creator metadata must be an object.")
    # The brief is saved while typing; it does not mean a proposal was applied.
    first = not creator.get("chapters")
    pristine_identity = document["character"].get("name") == "New character"
    prior_brief = creator.get("brief", {})
    if not first and prior_brief.get("archetype") != brief["archetype"]:
        preserved.append("The new story family applies only to missing content. Existing scenes and conversations keep your writing; changing the brief does not rewrite them.")
    defaults = {"name": "New character", "internal_name": "NewCharacter", "tagline": "", "occupation": "", "bio": ""}
    internal = re.sub(r"[^A-Za-z0-9_]", "", brief["name"])
    if not internal or not internal[0].isalpha():
        internal = "NPC" + internal
    bio = "\n\n".join(value for value in [brief["concept"], brief["arrival"],
           f"What they want: {brief['motivation']}" if brief["motivation"] else "",
           f"What gets in their way: {brief['flaw']}" if brief["flaw"] else ""] if value)
    identity = {"name": brief["name"], "internal_name": internal[:64], "tagline": family["tagline"],
                "occupation": family["occupation"], "bio": bio}
    updated_fields = []
    for key, value in identity.items():
        if not character.get(key) or (first and pristine_identity and character.get(key) == defaults[key]):
            if value != character.get(key):
                character[key] = value
                updated_fields.append(key)
        elif character.get(key) != value:
            preserved.append(f"Kept your existing {key.replace('_', ' ')}.")
    if first and pristine_identity:
        character["romanceable"] = brief["relationship"] == "romance"
    elif character.get("romanceable") != (brief["relationship"] == "romance"):
        preserved.append("Kept your existing romance setting; the chapter plan follows that setting.")
    romance = bool(character.get("romanceable", True))
    if updated_fields:
        additions.append({"section": "Identity", "title": "Character identity", "detail": "Fill " + ", ".join(updated_fields) + "."})
    dialogues = character.setdefault("dialogues", [])
    untouched_intro = next((line for line in dialogues if line.get("trigger") == "Introduction"
                            and line.get("text") == "Hello! It's lovely to meet you.$h"), None) if first else None
    if untouched_intro:
        dialogues.remove(untouched_intro)
    triggers = {line.get("trigger") for line in dialogues}
    dialogue_count = 0
    for trigger, text in [("Introduction", family["introduction"]), *zip(_DAYS, family["week"])]:
        if trigger in triggers:
            preserved.append(f"Kept your {trigger} dialogue.")
            continue
        dialogues.append({"id": _id(character, "dialogue:" + trigger), "trigger": trigger,
                          "text": text.format(name=_spoken(character["name"]))})
        dialogue_count += 1
    if dialogue_count:
        additions.append({"section": "Dialogue", "title": f"{dialogue_count} everyday conversations", "detail": "A complete introduction and one distinct line for each weekday; all editable."})
    gifts = character.setdefault("gifts", {})
    # Never put an item into two categories when the creator has already chosen a taste.
    from .exporting import _gift_id

    def gift_key(item):
        try:
            return _gift_id(item)
        except ValueError:
            return str(item).casefold()

    chosen = {gift_key(item) for values in gifts.values() if isinstance(values, list) for item in values}
    gift_count = 0
    for taste, values in family["gifts"].items():
        if gifts.get(taste):
            preserved.append(f"Kept your {taste} gift list.")
            continue
        values = [value for value in values if gift_key(value) not in chosen]
        gifts[taste] = values
        chosen.update(gift_key(value) for value in values)
        gift_count += len(values)
    if gift_count:
        additions.append({"section": "Gifts", "title": f"{gift_count} gift preferences", "detail": "Useful, expressive preferences selected for this story family."})
    schedule = character.setdefault("schedule", [])
    untouched_route = (first and len(schedule) == 1 and
                       all(schedule[0].get(key) == value for key, value in
                           {"time": "600", "location": "Town", "x": 32, "y": 62, "facing": "down", "activity": ""}.items()))
    if not schedule or untouched_route:
        x, y, location = character.get("home_x", 32), character.get("home_y", 62), character.get("home_map", "Town")
        character["schedule"] = [
            {"id": _id(character, f"schedule:{time}"), "time": time, "location": location, "x": x, "y": y,
             "facing": facing, "activity": activity}
            for time, facing, activity in (("600", "down", "Settle in and prepare for the day"),
                                           ("1000", "left", "Work and meet neighbours"),
                                           ("1700", "down", "Take a break and make time for a conversation"),
                                           ("2200", "up", "Finish the day"))]
        additions.append({"section": "Schedule", "title": "A complete daily routine", "detail": "Four timed stops at the current home tile. Choose destinations visually, then test tiles and routes in-game."})
    elif first:
        preserved.append("Kept your entire existing daily routine.")
    relationships = character.setdefault("relationships", [])
    relation_id = _id(character, "relationship:farmer")
    if not any(item.get("id") == relation_id for item in relationships):
        relationships.append(normalize_relationship({"id": relation_id, "name": f"{character['name']} and the farmer"[:80],
            "relation": "Romance" if romance else "Friendship", "description": family["tagline"],
            "creator_role": "relationship:farmer", "story": {"stage": "outline", "target": "farmer",
                "desire": brief["motivation"], "tension": brief["flaw"],
                "progression": "Small acts of trust lead to a setback, an honest repair, and a chosen future.",
                "resolution": "A shared life with room for independence." if romance else "A lasting friendship built on mutual trust."}}))
        additions.append({"section": "Story", "title": "A linked relationship arc", "detail": "A coherent desire, conflict, repair and resolution, linked to every chapter."})
    events = character.setdefault("events", [])
    known = {event.get("id") for event in events}
    hearts_values = (0, 2, 4, 6, 8, 10, 12, 14) if romance else (0, 2, 4, 6, 8)
    previous = ""
    event_count = 0
    chapter_refs = []
    for index, hearts in enumerate(hearts_values):
        event_id = _id(character, f"chapter:{hearts}")
        if event_id not in known:
            events.append(_event(character, brief, family, index, hearts, previous, relation_id))
            event_count += 1
        else:
            preserved.append(f"Kept your {hearts}-heart chapter, including its dialogue and readiness.")
        chapter_refs.append({"id": event_id, "hearts": hearts})
        previous = event_id
    if event_count:
        additions.append({"section": "Story", "title": f"{event_count} fully written chapters", "detail": "Cast, dialogue, narration, friendship effects and connected prerequisites. Each stays a scene draft until you review it and mark it ready."})
    _seed_life(character, chapter_refs, additions, preserved)
    _seed_companion(result, brief, additions, preserved)
    creator.update(version=1, brief=brief, chapters=chapter_refs,
                   source="Curated offline story family. Your free-form concept is saved as direction; the authored scenes are examples for you to adapt.")
    for collection, limit in (("dialogues", MAX_DIALOGUES), ("events", 100), ("relationships", 100)):
        if len(character[collection]) > limit:
            raise CreatorError(f"This preview would exceed the project's {limit}-{collection} limit. Make room before adding this framework; your project has not changed.")
    if any(len(character["life"][collection]) > 100 for collection in ("dialogues", "routines", "spouse_dialogue")):
        raise CreatorError("This preview would exceed 100 rules in an everyday-life category. Make room before adding it; your project has not changed.")
    if len(result.get("world", {}).get("characters", [])) > 32:
        raise CreatorError("This preview would exceed 32 supporting characters. Make room before adding a companion; your project has not changed.")
    return {"base_fingerprint": content_fingerprint(document),
            "summary": f"{character['name']}: {ARCHETYPES[brief['archetype']]} · {'romance' if romance else 'friendship'} · {len(hearts_values)} chapters",
            "additions": additions, "preserved": list(dict.fromkeys(preserved)), "document": result}


def apply_proposal(document, proposal):
    """Apply precisely the reviewed preview, rejecting changes made since preview."""
    if not isinstance(proposal, dict) or not isinstance(proposal.get("document"), dict):
        raise CreatorError("Build a preview before applying it.")
    if proposal.get("base_fingerprint") != content_fingerprint(document):
        raise CreatorError("This project changed after the preview. Build a fresh preview so your edits are preserved.")
    result = copy.deepcopy(proposal["document"])
    # Evidence may have been recorded while a preview was open. Preserve it.
    for key in _TRACKING:
        if key in document.get("creator", {}):
            result.setdefault("creator", {})[key] = copy.deepcopy(document["creator"][key])
    return result


def _current_record(document, key):
    record = document.get("creator", {}).get(key) or {}
    return record if record.get("fingerprint") == content_fingerprint(document) else {}


def creator_progress(document):
    """Derive milestones from authoring data and revision-specific evidence."""
    character = document.get("character", {})
    creator = document.get("creator", {})
    events = character.get("events", [])
    ready = [event for event in events if event.get("story", {}).get("stage") == "ready"
             and not any(issue["level"] == "error" for issue in event_issues(event, character))]
    intro = next((event for event in events if event.get("hearts") == 0), None)
    weekday = {line.get("trigger") for line in character.get("dialogues", []) if line.get("text", "").strip()}
    art = document.get("artwork", {})
    art_count = sum(bool(art.get(kind)) for kind in ("portrait", "sprite"))
    cases = playtest_cases(document)
    passed = sum(case["status"] == "passed" and not case["stale"] for case in cases)
    first_cases = [case for case in cases if case["id"] != "marriage" and
                   (not case.get("event_id") or intro and case["event_id"] == intro.get("id"))]
    first_passed = sum(case["status"] == "passed" and not case["stale"] for case in first_cases)
    planned = creator.get("chapters", [])
    by_id = {event.get("id"): event for event in events}
    missing = sum(ref.get("id") not in by_id for ref in planned)
    ready_ids = {event.get("id") for event in ready}
    all_chapters = bool(planned) and not missing and all(ref.get("id") in ready_ids for ref in planned)
    reactions = character.get("life", {}).get("dialogues", [])
    reactive_chapters = {row.get("conditions", {}).get("after_event_id") for row in reactions
                         if row.get("enabled") and row.get("text", "").strip()}
    reaction_count = sum(ref.get("id") in reactive_chapters and ref.get("id") in ready_ids for ref in planned)
    installed, exported = _current_record(document, "last_install"), _current_record(document, "last_export")
    return [
        {"id": "brief", "title": "Define the experience", "status": "complete" if creator.get("brief", {}).get("name") else "todo", "detail": "Choose a story family, what the character wants, and how the relationship should grow.", "section": "Creator"},
        {"id": "everyday", "title": "Give them an everyday life", "status": "complete" if set(_DAYS + ("Introduction",)) <= weekday and character.get("schedule") and any(character.get("gifts", {}).values()) else "in_progress" if weekday else "todo", "detail": f"{len(set(_DAYS) & weekday)}/7 weekday conversations; add an introduction, routine and gift preferences.", "section": "Dialogue"},
        {"id": "first_chapter", "title": "Review the first playable chapter", "status": "complete" if intro and intro.get("id") in ready_ids else "in_progress" if intro else "todo", "detail": "Rehearse the zero-heart meeting, review every line and tile, then mark it ready.", "section": "Story"},
        {"id": "artwork", "title": "Dress the character for the game", "status": "complete" if art_count == 2 else "in_progress" if art_count else "blocked", "detail": f"{art_count}/2 base sheets attached. Export checks dimensions; verify the actual expressions and animation frames in-game.", "section": "Artwork"},
        {"id": "export", "title": "Build a playable package", "status": "complete" if exported else "todo", "detail": "Current revision exported." if exported else "Save the project and export after resolving validation blockers. Earlier exports become stale when content changes.", "section": "Export"},
        {"id": "install", "title": "Install this revision", "status": "complete" if installed else "todo", "detail": "Current exported revision installed; launch Stardew Valley through SMAPI." if installed else "Install the exported pack into Stardew Valley's Mods folder with Content Patcher installed.", "section": "Creator"},
        {"id": "playtest", "title": "Play the first version", "status": "complete" if first_cases and first_passed == len(first_cases) else "in_progress" if first_passed else "todo", "detail": f"{first_passed}/{len(first_cases)} current-revision first-visit checks passed. Test the meeting and everyday life before expanding.", "section": "Creator"},
        {"id": "chapters", "title": "Develop the full relationship", "status": "complete" if all_chapters else "in_progress" if events else "todo", "detail": f"{sum(ref.get('id') in ready_ids for ref in planned)}/{len(planned)} planned chapters ready." + (f" {missing} planned chapters were removed." if missing else " Each chapter needs author review."), "section": "Story"},
        {"id": "reactions", "title": "Let daily life remember the story", "status": "complete" if planned and reaction_count == len(planned) else "in_progress" if reactions else "todo", "detail": f"{reaction_count}/{len(planned)} planned chapters have an enabled follow-up conversation. Review the draft reactions, then enable them as chapters become ready.", "section": "life"},
        {"id": "release", "title": "Prove the complete experience in-game", "status": "complete" if all_chapters and cases and passed == len(cases) else "in_progress" if passed else "todo", "detail": f"{passed}/{len(cases)} current-revision checks passed. Finish and test the planned chapters, daily life and supporting cast before sharing.", "section": "Creator"},
    ]


def playtest_cases(document):
    from .world import exported_location_id, exported_mod_id
    character = document.get("character", {})
    world = document.get("world", {})
    npc = exported_npc_id(character)
    mod_id = exported_mod_id(character)
    location = character.get("home_map", "Town")

    def map_name(reference):
        for place in world.get("locations", []):
            if place.get("spouse_room"):
                continue
            game_id = exported_location_id(place, character)
            if reference in (place.get("internal_name"), game_id):
                return f"{place.get('name') or place['internal_name']} (game map: {game_id})"
        return reference

    x, y = character.get("home_x", 32), character.get("home_y", 62)
    cases = [
        {"id": "load", "title": "The game loads the pack", "instructions": "Launch the installed revision through SMAPI with Content Patcher. Load a test save, check the SMAPI log for this pack, and confirm there are no red errors."},
        {"id": "meet", "title": "Meet your character", "instructions": f"On a fresh test save, find {character.get('name', 'your character')} in {map_name(location)}, starting near tile {x}, {y}. Talk once and confirm the introduction, portrait and display name. Internal NPC ID: {npc}."},
        {"id": "routine", "title": "Follow a whole day", "instructions": "Sleep once, then follow the character through every authored stop from 06:00 until the final stop. Check walkable tiles, routes between maps, facing and sprite frames. Repeat in each authored season and weather variant."},
        {"id": "dialogue", "title": "Check everyday conversation", "instructions": "Talk on each weekday. Confirm distinct authored lines and expressions. Check any season, heart-level and post-event conversation rules at their intended conditions."},
        {"id": "gifts", "title": "Give the chosen gifts", "instructions": "Give one item from each authored taste category on appropriate days. Confirm that the game reports the intended reactions and doesn't substitute an unknown item."},
    ]
    for event in character.get("events", []):
        if event.get("story", {}).get("stage") != "ready":
            continue
        story = event["story"]
        previous = story.get("previous_event_id")
        prior = next((item.get("name", "the previous chapter") for item in character.get("events", []) if item.get("id") == previous), "the prerequisite chapter")
        start, end = story.get("time_start", 600), story.get("time_end", 2400)
        instructions = f"Reach at least {event.get('hearts', 0)} hearts and enter {map_name(event.get('location', location))} between {start // 100:02d}:{start % 100:02d} and {end // 100:02d}:{end % 100:02d}."
        if previous:
            instructions += f" Watch {prior} first."
        if story.get("season", "any") != "any":
            instructions += f" Season: {story['season']}."
        if story.get("weather", "any") != "any":
            instructions += f" Weather: {story['weather']}."
        relationship = story.get("relationship", "any")
        if relationship == "unmarried":
            instructions += " The player must not be married to this character; marriage to someone else does not exclude this scene."
        elif relationship == "dating":
            instructions += " The player must be dating this character."
        elif relationship == "married":
            instructions += " The player must be married to this character."
        house_upgrade = story.get("min_house_upgrade", 0)
        if house_upgrade:
            instructions += f" The player's farmhouse must be at upgrade level {house_upgrade} or higher."
        instructions += " Watch each answer and skip once using separate test saves. Check every line, portrait, position and friendship effect."
        if story.get("repeat", "once") == "daily":
            instructions += " Install Event Repeater 6.5.8 or later. After completion, leave and re-enter on the same day without reloading: the scene should stay completed. Sleep to the next day and re-enter with its conditions satisfied: it should play again. Reload a saved game and check again, since reloading also resets this repeatable scene. Friendship effects can be earned on every replay."
        else:
            instructions += " This scene is one-time. After completion, leave and re-enter, then sleep and re-enter: it should not play again. After the game has saved that completion, reload that save and confirm it stays completed."
        instructions += f" Event ID: {event_game_id(event, character).replace('{{ModId}}', mod_id)}."
        cases.append({"id": "event:" + event["id"], "event_id": event["id"], "section": "Story", "title": "Play chapter: " + event.get("name", "Untitled"), "instructions": instructions})
    if character.get("romanceable"):
        cases.append({"id": "marriage", "title": "Test the relationship after marriage", "instructions": "On a separate test save, test bouquet dating, proposal, wedding, spouse conversations, kiss and wedding sprite frames, spouse room, and a full married day. Check each ready married chapter after its prerequisites."})
    if world.get("characters") or world.get("companions"):
        cases.append({"id": "companions", "title": "Meet the supporting cast", "instructions": "Find each supporting character, confirm their own portrait, sprite, dialogue and route, and watch each scene that includes them. Check that their internal IDs resolve correctly."})
    for place in world.get("locations", []):
        if place.get("spouse_room"):
            instructions = "On a married test save, enter the upgraded farmhouse and inspect the supplied spouse room. Check its walls, floors, furniture, entry path and the spouse's movement at different times."
        else:
            entrance = place.get("entrance", {})
            instructions = f"Visit {map_name(place.get('internal_name', ''))}. Enter from {map_name(entrance.get('map', 'the entrance map'))} at tile {entrance.get('x', 0)}, {entrance.get('y', 0)}. Check the entry tile, walkable space, tilesheet artwork and the return exit. Leave and re-enter, then follow any NPC routes that use this map."
        cases.append({"id": "location:" + str(place.get("id", "")), "section": "World",
                      "title": "Visit " + place.get("name", "the custom location"), "instructions": instructions})
    fingerprint = content_fingerprint(document)
    records = document.get("creator", {}).get("tests", {})
    for case in cases:
        case.setdefault("section", {"load": "Export", "meet": "Identity", "routine": "Schedule", "dialogue": "Dialogue", "gifts": "Gifts", "marriage": "Everyday life", "companions": "World"}.get(case["id"], "Creator"))
        record = records.get(case["id"], {})
        stale = bool(record and record.get("fingerprint") != fingerprint)
        case.update(status="untested" if stale else record.get("status", "untested"), stale=stale,
                    notes=record.get("notes", ""), previous_status=record.get("status", "untested"))
    return cases


def export_is_current(document, archive_bytes=None):
    """Whether recorded export evidence matches current content and optional bytes."""
    record = _current_record(document, "last_export")
    return bool(record) and (archive_bytes is None or isinstance(archive_bytes, bytes)
        and hashlib.sha256(archive_bytes).hexdigest() == record.get("sha256"))


def record_playtest(document, test_id, status, notes=""):
    if status not in PLAYTEST_STATUSES:
        raise CreatorError("Choose untested, passed or failed.")
    if not isinstance(notes, str) or len(notes) > 8000 or "\x00" in notes:
        raise CreatorError("Test notes must be text of at most 8000 characters.")
    if test_id not in {case["id"] for case in playtest_cases(document)}:
        raise CreatorError("This playtest no longer belongs to the current project.")
    if status != "untested" and not _current_record(document, "last_install"):
        raise CreatorError("Install the current exported revision before recording its in-game test results.")
    result = copy.deepcopy(document)
    result.setdefault("creator", {}).setdefault("tests", {})[test_id] = {
        "status": status, "notes": notes, "fingerprint": content_fingerprint(document), "recorded_at": _now()}
    return result


def record_export(document, path, archive_bytes):
    if not isinstance(archive_bytes, bytes) or not archive_bytes:
        raise CreatorError("Record an export only after a nonempty archive was successfully written.")
    if not str(path).strip():
        raise CreatorError("The exported archive needs a path.")
    result = copy.deepcopy(document)
    record = {"path": str(path), "fingerprint": content_fingerprint(document),
              "sha256": hashlib.sha256(archive_bytes).hexdigest(), "bytes": len(archive_bytes), "recorded_at": _now()}
    creator = result.setdefault("creator", {})
    creator["last_export"] = record
    creator.setdefault("exports", []).append(copy.deepcopy(record))
    return result


def record_install(document, path, archive_bytes=None):
    exported = _current_record(document, "last_export")
    if not exported:
        raise CreatorError("Export the current project revision before recording its installation.")
    if not str(path).strip():
        raise CreatorError("The installed content pack needs a destination path.")
    if archive_bytes is not None and (not isinstance(archive_bytes, bytes) or hashlib.sha256(archive_bytes).hexdigest() != exported["sha256"]):
        raise CreatorError("The installed archive does not match the recorded export.")
    result = copy.deepcopy(document)
    record = {"path": str(path), "fingerprint": content_fingerprint(document),
              "sha256": exported["sha256"], "recorded_at": _now()}
    creator = result.setdefault("creator", {})
    creator["last_install"] = record
    creator.setdefault("installations", []).append(copy.deepcopy(record))
    return result

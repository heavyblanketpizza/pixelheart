"""The designer's tools: document in, new document plus a receipt out."""
import unittest

from pixelheart_core.projects import new_project
from pixelheart_designer.summary import summarize
from pixelheart_designer.tools import ToolContext, ToolError, run_tool, tool_specs
from pixelheart_designer.tools.dialogue import occasion_key, provenance_source
from pixelheart_designer.tools.routine import known_places
from tests.designer_support import IDENTITY_FIELDS, NULL_IDENTITY, NULL_WHEN  # noqa: F401


class RegistryTests(unittest.TestCase):
    def test_read_character_returns_summary_and_no_receipt(self):
        document = new_project()
        outcome = run_tool("read_character", document, {})
        self.assertIsNone(outcome.receipt)
        self.assertEqual(outcome.message, summarize(document))
        self.assertEqual(outcome.document, document)

    def test_unknown_tool_is_an_error(self):
        with self.assertRaises(ToolError):
            run_tool("paint_portrait", new_project(), {})

    def test_every_spec_is_strict_shaped(self):
        for spec in tool_specs():
            schema = spec.input_schema
            self.assertEqual(schema["type"], "object")
            self.assertIs(schema["additionalProperties"], False)
            self.assertEqual(sorted(schema["required"]), sorted(schema["properties"]))



class IdentityToolTests(unittest.TestCase):
    def test_sets_fields_and_keeps_internal_name(self):
        document = new_project()
        outcome = run_tool("set_identity", document, {**NULL_IDENTITY, "name": "Mira", "birthday_season": "fall", "birthday_day": 3, "romanceable": False})
        character = outcome.document["character"]
        self.assertEqual((character["name"], character["internal_name"], character["season"], character["day"], character["romanceable"]),
                         ("Mira", "NewCharacter", "fall", 3, False))
        self.assertEqual(outcome.receipt["title"], "Updated About them")
        self.assertIn("Internal name stays NewCharacter", outcome.receipt["detail"])
        self.assertEqual(document["character"]["name"], "New character")   # input untouched

    def test_invalid_values_report_the_core_message(self):
        with self.assertRaises(ToolError) as caught:
            run_tool("set_identity", new_project(), {**NULL_IDENTITY, "home_map": "9 bad"})
        self.assertIn("Start the map ID with a letter", str(caught.exception))

    def test_all_null_is_an_error(self):
        with self.assertRaises(ToolError):
            run_tool("set_identity", new_project(), dict(NULL_IDENTITY))

    def test_storyline_brief_merges(self):
        document = new_project()
        document["character"]["storyline"]["brief"]["motif"] = "bees"
        outcome = run_tool("set_storyline_brief", document, {"desire": "to be known", "question": None, "boundaries": None, "motif": None})
        brief = outcome.document["character"]["storyline"]["brief"]
        self.assertEqual((brief["desire"], brief["motif"]), ("to be known", "bees"))
        self.assertEqual(outcome.receipt["section"], "story")


class DialogueToolTests(unittest.TestCase):
    def test_occasions_become_game_keys(self):
        cases = [({"occasion": "introduction"}, "Introduction"), ({"occasion": "weekday", "day": "Mon"}, "Mon"),
                 ({"occasion": "weekday", "day": "Thu", "season": "summer", "hearts": 4}, "summer_Thu4"),
                 ({"occasion": "date", "season": "spring", "date": 1}, "spring_1"),
                 ({"occasion": "place", "location": "Saloon", "day": "Fri"}, "Saloon_Fri"),
                 ({"occasion": "gift", "item": "109"}, "AcceptGift_(O)109"),
                 ({"occasion": "gift", "item": "(O)109"}, "AcceptGift_(O)109"),
                 ({"occasion": "birthday_gift", "liked": False}, "AcceptBirthdayGift_Negative")]
        for when, key in cases:
            self.assertEqual(occasion_key({**NULL_WHEN, **when}, known_places(new_project())), key)

    def test_three_hearts_is_rejected_with_the_core_message(self):
        with self.assertRaises(ToolError) as caught:
            occasion_key({**NULL_WHEN, "occasion": "weekday", "day": "Mon", "hearts": 3}, {})
        self.assertIn("2, 4, 6, 8 or 10 hearts", str(caught.exception))

    def test_unknown_place_is_rejected(self):
        with self.assertRaises(ToolError) as caught:
            occasion_key({**NULL_WHEN, "occasion": "place", "location": "Moon"}, known_places(new_project()))
        self.assertEqual(str(caught.exception), "Unknown place 'Moon'. Use list_places to see the places you can choose.")

    def test_fill_keeps_written_lines_and_stamps_provenance(self):
        document = new_project()
        lines = [{"when": {**NULL_WHEN, "occasion": "introduction"}, "text": "Oh! Hi.$h"},
                 {"when": {**NULL_WHEN, "occasion": "weekday", "day": "Mon"}, "text": "Mondays.#$b#Ugh.$s"}]
        outcome = run_tool("write_dialogue", document, {"lines": lines, "mode": "fill"}, ToolContext("claude-opus-5-5"))
        records = {row["trigger"]: row for row in outcome.document["character"]["dialogues"]}
        self.assertEqual(records["Introduction"]["text"], "Hello! It's lovely to meet you.$h")
        self.assertEqual(records["Mon"]["text"], "Mondays.#$b#Ugh.$s")
        self.assertEqual(records["Mon"]["source"], {"provider": "ai-designer", "source_name": "AI designer · claude-opus-5-5",
            "attribution": "Written by an AI model from the creator's own description in Pixelheart. Review before sharing."})
        self.assertEqual(outcome.receipt["detail"], "1 lines written · 1 kept")

    def test_fill_writes_over_blank_existing_lines(self):
        document = new_project()
        document["character"]["dialogues"].append({"id": "m", "trigger": "Mon", "text": ""})
        outcome = run_tool("write_dialogue", document, {"lines": [{"when": {**NULL_WHEN, "occasion": "weekday", "day": "Mon"}, "text": "Hi."}], "mode": "fill"})
        self.assertEqual([row["text"] for row in outcome.document["character"]["dialogues"] if row["trigger"] == "Mon"], ["Hi."])

    def test_replace_overwrites(self):
        document = new_project()
        outcome = run_tool("write_dialogue", document, {"lines": [{"when": {**NULL_WHEN, "occasion": "introduction"}, "text": "New."}], "mode": "replace"})
        self.assertEqual(outcome.document["character"]["dialogues"][0]["text"], "New.")
        self.assertEqual(outcome.receipt["detail"], "1 lines written · 0 kept")

    def test_advanced_syntax_and_tokens_are_rejected(self):
        for text in ("Quest time $q 1#yes", "Hello {{Token}}", ""):
            with self.assertRaises(ToolError):
                run_tool("write_dialogue", new_project(), {"lines": [{"when": {**NULL_WHEN, "occasion": "introduction"}, "text": text}], "mode": "replace"})

    def test_dollar_n_is_not_a_portrait_code_the_game_knows(self):
        with self.assertRaises(ToolError) as caught:
            run_tool("write_dialogue", new_project(), {"lines": [{"when": {**NULL_WHEN, "occasion": "introduction"}, "text": "Hi.$n"}], "mode": "replace"})
        self.assertIn("$h $s $l $a $u", str(caught.exception))
        self.assertNotIn("$n", str(caught.exception))

    def test_gift_occasion_needs_a_real_item_id(self):
        with self.assertRaises(ToolError) as caught:
            run_tool("write_dialogue", new_project(), {"lines": [{"when": {**NULL_WHEN, "occasion": "gift", "item": "Coffee"}, "text": "Thanks!"}], "mode": "replace"})
        self.assertEqual(str(caught.exception), "'Coffee' isn't an item id. Use search_items to find it.")
        outcome = run_tool("write_dialogue", new_project(), {"lines": [{"when": {**NULL_WHEN, "occasion": "gift", "item": "309"}, "text": "An acorn!$h"}], "mode": "replace"})
        self.assertEqual(outcome.document["character"]["dialogues"][-1]["trigger"], "AcceptGift_(O)309")

    def test_model_label_is_made_portable(self):
        self.assertEqual(provenance_source("Qwen/qwen2.5:7b")["source_name"], "AI designer · Qwen-qwen2.5-7b")


class GiftToolTests(unittest.TestCase):
    def test_search_lists_matches_with_ids(self):
        message = run_tool("search_items", new_project(), {"query": "acorn", "category": None}).message
        self.assertIn("309 · Acorn · Seeds", message)

    def test_search_needs_a_word_and_reports_no_matches(self):
        with self.assertRaises(ToolError):
            run_tool("search_items", new_project(), {"query": "  ", "category": None})
        self.assertEqual(run_tool("search_items", new_project(), {"query": "zzzz", "category": None}).message,
                         "No items match 'zzzz'. Try a shorter word.")

    def test_search_caps_at_twenty(self):
        message = run_tool("search_items", new_project(), {"query": "e", "category": None}).message
        self.assertEqual(len([line for line in message.splitlines() if " · " in line]), 20)
        self.assertIn("more", message.splitlines()[-1])

    def test_set_gifts_replaces_all_lists(self):
        document = new_project()
        outcome = run_tool("set_gifts", document, {"love": ["309"], "like": [], "dislike": [], "hate": []})
        self.assertEqual(outcome.document["character"]["gifts"], {"love": ["309"], "like": [], "dislike": [], "hate": []})
        self.assertEqual(outcome.receipt["detail"], "1 loved · 0 liked · 0 disliked · 0 hated")

    def test_unknown_and_duplicate_ids_fail_whole_call(self):
        with self.assertRaises(ToolError) as caught:
            run_tool("set_gifts", new_project(), {"love": ["Acorn"], "like": [], "dislike": [], "hate": []})
        self.assertIn("'Acorn' isn't an item id", str(caught.exception))
        self.assertIn("309 · Acorn", str(caught.exception))
        with self.assertRaises(ToolError):
            run_tool("set_gifts", new_project(), {"love": ["309"], "like": ["309"], "dislike": [], "hate": []})


class RoutineToolTests(unittest.TestCase):
    def test_list_places_has_vanilla_and_project_places(self):
        document = new_project()
        document["world"] = {"locations": [{"internal_name": "MiraShop", "name": "Mira's shop"}, {"internal_name": "Spouse", "name": "x", "spouse_room": True}]}
        message = run_tool("list_places", document, {}).message
        self.assertIn("Saloon · Stardrop Saloon", message)
        self.assertIn("Places you designed:", message)
        self.assertIn("MiraShop · Mira's shop", message)
        self.assertNotIn("Spouse ·", message)

    def test_plan_routine_replaces_schedule_and_reuses_tiles(self):
        document = new_project()
        document["character"]["schedule"] = [{"id": "a", "time": "600", "location": "Saloon", "x": 10, "y": 20, "facing": "left", "activity": ""}]
        outcome = run_tool("plan_routine", document, {"stops": [{"time": "9:00", "location": "Town", "activity": "errands"}, {"time": "1700", "location": "Saloon", "activity": None}]})
        stops = outcome.document["character"]["schedule"]
        self.assertEqual([(s["time"], s["location"], s["x"], s["y"], s["facing"], s["activity"]) for s in stops],
                         [("900", "Town", 32, 62, "down", "errands"), ("1700", "Saloon", 10, 20, "left", "")])
        self.assertEqual(outcome.receipt["detail"], "2 stops · place them on the map")

    def test_bad_time_order_and_place_are_rejected(self):
        for stops, fragment in (([{"time": "905", "location": "Town", "activity": None}], "ten-minute steps"),
                                ([{"time": "900", "location": "Town", "activity": None}, {"time": "800", "location": "Town", "activity": None}], "time order"),
                                ([{"time": "900", "location": "Moon", "activity": None}], "Unknown place 'Moon'")):
            with self.assertRaises(ToolError) as caught:
                run_tool("plan_routine", new_project(), {"stops": stops})
            self.assertIn(fragment, str(caught.exception))


class EventToolTests(unittest.TestCase):
    def script(self, *lines):
        return [{"speaker": "npc", "text": text, "feeling": feeling} for text, feeling in lines]

    def test_creates_event_with_actors_beats_and_scene_stage(self):
        document = new_project()
        outcome = run_tool("write_heart_event", document, {"hearts": 2, "name": "Honey", "location": "Town", "premise": "p", "conflict": "c", "outcome": "",
                                                            "script": self.script(("Try this.", "happy"), ("...", None))})
        event = outcome.document["character"]["events"][0]
        self.assertEqual((event["hearts"], event["name"], event["location"], event["story"]["stage"]), (2, "Honey", "Town", "scene"))
        self.assertEqual([actor["name"] for actor in event["story"]["actors"]], ["$npc", "farmer"])
        self.assertEqual([(b["kind"], b["actor"], b["text"]) for b in event["story"]["beats"]], [("dialogue", "$npc", "Try this.$h"), ("dialogue", "$npc", "...")])
        self.assertEqual(outcome.receipt["detail"], "2-heart event · Honey · 2 lines")

    def test_updates_existing_event_at_that_level_and_demotes_ready(self):
        document = new_project(story_starter="friendship")
        document["character"]["events"][0]["story"]["stage"] = "ready"
        outcome = run_tool("write_heart_event", document, {"hearts": 2, "name": "Renamed", "location": "Beach", "premise": "p", "conflict": "c", "outcome": "o", "script": []})
        events = outcome.document["character"]["events"]
        self.assertEqual(len(events), 4)
        self.assertEqual((events[0]["name"], events[0]["story"]["stage"]), ("Renamed", "scene"))
        self.assertTrue(outcome.receipt["detail"].endswith("staged positions need another look"))

    def test_empty_script_keeps_scene_and_outlines_ideas(self):
        document = new_project(story_starter="friendship")
        document["character"]["events"][1]["story"]["stage"] = "idea"
        outcome = run_tool("write_heart_event", document, {"hearts": 4, "name": "N", "location": "Town", "premise": "p", "conflict": "c", "outcome": "", "script": []})
        self.assertEqual(outcome.document["character"]["events"][1]["story"]["stage"], "outline")

    def test_straight_quotes_become_curly_so_the_scene_can_play(self):
        document = new_project()
        outcome = run_tool("write_heart_event", document, {"hearts": 2, "name": "N", "location": "Town", "premise": "p", "conflict": "c", "outcome": "",
                                                            "script": self.script(('She said "hi" and "bye".', "happy"))})
        self.assertEqual(outcome.document["character"]["events"][0]["story"]["beats"][0]["text"], "She said “hi” and “bye”.$h")

    def test_farmer_lines_and_unromanceable_fourteen_hearts(self):
        document = new_project()
        outcome = run_tool("write_heart_event", document, {"hearts": 2, "name": "N", "location": "Town", "premise": "p", "conflict": "c", "outcome": "",
                                                            "script": [{"speaker": "farmer", "text": "Hi", "feeling": None}]})
        self.assertEqual(outcome.document["character"]["events"][0]["story"]["beats"][0]["actor"], "farmer")
        document["character"]["romanceable"] = False
        with self.assertRaises(ToolError) as caught:
            run_tool("write_heart_event", document, {"hearts": 14, "name": "N", "location": "Town", "premise": "p", "conflict": "c", "outcome": "", "script": []})
        self.assertIn("cannot reach 11 hearts", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

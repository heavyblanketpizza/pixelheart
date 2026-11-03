"""The designer's married life, conditional lines and conditional routines: daily-life rules written through the core validators."""
import unittest

from pixelheart_core.life import life_issues
from pixelheart_core.projects import new_project
from pixelheart_designer.prompts import SYSTEM_PROMPT
from pixelheart_designer.summary import summarize
from pixelheart_designer.tools import ToolContext, ToolError, run_tool, tool_specs


NULL_WHEN = {"season": None, "weather": None, "weekday": None, "min_hearts": None, "relationship": None,
             "after_event_hearts": None, "min_house_upgrade": None}
NULL_HOME_WHEN = {"season": None, "weekday": None, "min_hearts": None, "after_event_hearts": None, "min_house_upgrade": None}
ATTRIBUTION = "Written by an AI model from the creator's own description in Pixelheart. Review before sharing."


def married_lines(*pairs, when=None):
    return [{"moment": moment, "text": text, "when": when} for moment, text in pairs]


def rule(name, text, **when):
    return {"name": name, "text": text, "when": {**NULL_WHEN, **when}}


def stop(time, location, activity=None):
    return {"time": time, "location": location, "activity": activity}


def routine(*stops, **when):
    return {"when": {**NULL_WHEN, **when}, "stops": list(stops)}


class RegistryTests(unittest.TestCase):
    def test_life_tools_follow_the_heart_event_tool(self):
        names = [spec.name for spec in tool_specs()]
        self.assertEqual(names[names.index("write_heart_event") + 1:], ["write_married_life", "write_conditional_lines", "plan_conditional_routine"])

    def test_prompt_offers_the_life_tools_and_no_longer_disowns_them(self):
        self.assertIn("write_married_life", SYSTEM_PROMPT)
        self.assertIn("write_conditional_lines", SYSTEM_PROMPT)
        self.assertIn("plan_conditional_routine", SYSTEM_PROMPT)
        self.assertIn("cannot make portraits, sprites, homes or exports", SYSTEM_PROMPT)
        self.assertNotIn("conditional routines or exports", SYSTEM_PROMPT)
        self.assertNotIn("married-life lines or exports", SYSTEM_PROMPT)

    def test_both_routine_tools_take_the_same_stops(self):
        specs = {spec.name: spec for spec in tool_specs()}
        self.assertEqual(specs["plan_conditional_routine"].input_schema["properties"]["stops"],
                         specs["plan_routine"].input_schema["properties"]["stops"])


class MarriedLifeTests(unittest.TestCase):
    def test_writes_one_line_per_moment_into_the_mod(self):
        document = new_project()
        lines = married_lines(("morning", "Morning, @.$l"), ("rainy_morning", "Rain again.$s"),
                              ("evening", "Long day?"), ("rainy_evening", "Listen to that rain.$h"))
        outcome = run_tool("write_married_life", document, {"lines": lines}, ToolContext("claude-opus-5-5"))
        records = outcome.document["character"]["life"]["spouse_dialogue"]
        self.assertEqual([(row["moment"], row["text"], row["name"], row["enabled"]) for row in records],
                         [("morning", "Morning, @.$l", "Morning at home", True), ("rainy_morning", "Rain again.$s", "Rainy morning at home", True),
                          ("evening", "Long day?", "Evening at home", True), ("rainy_evening", "Listen to that rain.$h", "Rainy evening at home", True)])
        self.assertEqual(records[0]["conditions"]["relationship"], "married")
        self.assertEqual(records[0]["source"], {"provider": "ai-designer", "source_name": "AI designer · claude-opus-5-5", "attribution": ATTRIBUTION})
        self.assertEqual(outcome.receipt, {"section": "dialogue", "title": "Updated Marriage dialogue", "detail": "4 lines written"})
        self.assertIsNone(document["character"].get("life"))   # input untouched

    def test_rewrites_the_same_moment_in_place(self):
        first = run_tool("write_married_life", new_project(), {"lines": married_lines(("morning", "One."), ("evening", "Two."))})
        second = run_tool("write_married_life", first.document, {"lines": married_lines(("morning", "Three."))})
        before, after = first.document["character"]["life"]["spouse_dialogue"], second.document["character"]["life"]["spouse_dialogue"]
        self.assertEqual([(row["id"], row["text"]) for row in after], [(before[0]["id"], "Three."), (before[1]["id"], "Two.")])
        self.assertEqual(second.receipt["detail"], "1 line written · 1 rewritten")

    def test_a_condition_makes_a_separate_more_specific_line(self):
        first = run_tool("write_married_life", new_project(), {"lines": married_lines(("morning", "Plain."))})
        second = run_tool("write_married_life", first.document, {"lines": married_lines(("morning", "Snowy."), when={**NULL_HOME_WHEN, "season": "winter"})})
        records = second.document["character"]["life"]["spouse_dialogue"]
        self.assertEqual([(row["text"], row["name"], row["conditions"]["season"]) for row in records],
                         [("Plain.", "Morning at home", "any"), ("Snowy.", "Morning at home · winter", "winter")])

    def test_two_lines_for_one_moment_in_a_call_are_rejected(self):
        with self.assertRaises(ToolError) as caught:
            run_tool("write_married_life", new_project(), {"lines": married_lines(("morning", "A."), ("evening", "B."), ("morning", "C."))})
        self.assertEqual(str(caught.exception), "Lines 1 and 3 are for the same moment with the same conditions.")

    def test_text_follows_the_everyday_rules(self):
        for text in ("Quest? $q 1#yes", "Hello {{Token}}", "", "Hi.$n", "Take this [O]"):
            with self.assertRaises(ToolError, msg=text):
                run_tool("write_married_life", new_project(), {"lines": married_lines(("morning", text))})

    def test_needs_romance_to_be_open(self):
        document = new_project()
        document["character"]["romanceable"] = False
        with self.assertRaises(ToolError) as caught:
            run_tool("write_married_life", document, {"lines": married_lines(("morning", "Hi."))})
        self.assertIn("Enable romance", str(caught.exception))

    def test_a_line_after_an_unfinished_event_stays_a_draft(self):
        document = new_project(story_starter="friendship")
        event = document["character"]["events"][0]
        outcome = run_tool("write_married_life", document, {"lines": married_lines(("evening", "Remember the bees?$h"), when={**NULL_HOME_WHEN, "after_event_hearts": 2})})
        row = outcome.document["character"]["life"]["spouse_dialogue"][0]
        self.assertEqual((row["enabled"], row["conditions"]["after_event_id"], row["name"]), (False, event["id"], "Evening at home · after A shared interest (2 hearts)"))
        self.assertEqual(outcome.receipt["detail"], "1 line written · 1 draft until its event is ready")
        self.assertIn("marks the 2-heart event ready", outcome.message)

    def test_a_line_after_a_ready_event_goes_into_the_mod(self):
        document = new_project(story_starter="friendship")
        document["character"]["events"][0]["story"]["stage"] = "ready"
        outcome = run_tool("write_married_life", document, {"lines": married_lines(("evening", "Remember?"), when={**NULL_HOME_WHEN, "after_event_hearts": 2})})
        self.assertTrue(outcome.document["character"]["life"]["spouse_dialogue"][0]["enabled"])
        self.assertEqual(outcome.receipt["detail"], "1 line written")

    def test_a_repeating_event_cannot_be_waited_for(self):
        document = new_project(story_starter="friendship")
        document["character"]["events"][0]["story"].update(stage="ready", repeat="daily")
        with self.assertRaises(ToolError) as caught:
            run_tool("write_married_life", document, {"lines": married_lines(("evening", "Again?"), when={**NULL_HOME_WHEN, "after_event_hearts": 2})})
        self.assertIn("one-time", str(caught.exception))

    def test_unknown_event_level_is_rejected(self):
        with self.assertRaises(ToolError) as caught:
            run_tool("write_married_life", new_project(), {"lines": married_lines(("evening", "Hm."), when={**NULL_HOME_WHEN, "after_event_hearts": 12})})
        self.assertEqual(str(caught.exception), "There is no heart event at 12 hearts. Write it first with write_heart_event.")


class ConditionalLinesTests(unittest.TestCase):
    def test_writes_named_rules_with_their_conditions(self):
        document = new_project()
        outcome = run_tool("write_conditional_lines", document, {"rules": [rule("Rainy winter days", "Snow and rain.$s", season="winter", weather="rainy"),
                                                                            rule("Once we're dating", "Hey you.$l", relationship="dating", min_hearts=8)]})
        records = outcome.document["character"]["life"]["dialogues"]
        self.assertEqual([(row["name"], row["text"], row["enabled"]) for row in records],
                         [("Rainy winter days", "Snow and rain.$s", True), ("Once we're dating", "Hey you.$l", True)])
        self.assertEqual(records[0]["conditions"], {"season": "winter", "weather": "rainy", "weekday": "any", "min_hearts": 0,
                                                    "relationship": "any", "after_event_id": "", "min_house_upgrade": 0})
        self.assertEqual(records[1]["conditions"]["relationship"], "dating")
        self.assertEqual(records[1]["conditions"]["min_hearts"], 8)
        self.assertEqual(records[0]["source"]["provider"], "ai-designer")
        self.assertEqual(outcome.receipt, {"section": "dialogue", "title": "Updated Story reactions", "detail": "2 lines written"})

    def test_a_rule_needs_a_condition(self):
        with self.assertRaises(ToolError) as caught:
            run_tool("write_conditional_lines", new_project(), {"rules": [rule("Always", "Hi.")]})
        self.assertEqual(str(caught.exception), "Rule 1: give at least one condition; lines that always apply belong in write_dialogue.")

    def test_a_rule_needs_a_name(self):
        with self.assertRaises(ToolError):
            run_tool("write_conditional_lines", new_project(), {"rules": [rule("  ", "Hi.", season="fall")]})

    def test_bad_condition_values_are_rejected(self):
        for when in ({"relationship": "engaged"}, {"min_hearts": 15}, {"weather": "foggy"}, {"min_house_upgrade": 4}, {"weekday": "Monday"}):
            with self.assertRaises(ToolError, msg=str(when)):
                run_tool("write_conditional_lines", new_project(), {"rules": [rule("R", "Hi.", **when)]})

    def test_rewrites_the_rule_with_the_same_conditions(self):
        first = run_tool("write_conditional_lines", new_project(), {"rules": [rule("Fall", "Leaves.", season="fall"), rule("Winter", "Snow.", season="winter")]})
        second = run_tool("write_conditional_lines", first.document, {"rules": [rule("Autumn", "Crunchy leaves.$h", season="fall")]})
        before, after = first.document["character"]["life"]["dialogues"], second.document["character"]["life"]["dialogues"]
        self.assertEqual([(row["id"], row["name"], row["text"]) for row in after],
                         [(before[0]["id"], "Autumn", "Crunchy leaves.$h"), (before[1]["id"], "Winter", "Snow.")])
        self.assertEqual(second.receipt["detail"], "1 line written · 1 rewritten")

    def test_story_reaction_after_a_ready_event(self):
        document = new_project(story_starter="friendship")
        document["character"]["events"][1]["story"]["stage"] = "ready"
        outcome = run_tool("write_conditional_lines", document, {"rules": [rule("After the talk", "You stayed.$h", after_event_hearts=4)]})
        row = outcome.document["character"]["life"]["dialogues"][0]
        self.assertEqual((row["enabled"], row["conditions"]["after_event_id"]), (True, document["character"]["events"][1]["id"]))

    def test_story_reaction_before_the_event_is_ready_stays_a_draft(self):
        document = new_project(story_starter="friendship")
        outcome = run_tool("write_conditional_lines", document, {"rules": [rule("After the talk", "You stayed.$h", after_event_hearts=4), rule("Winter", "Brr.", season="winter")]})
        self.assertEqual([row["enabled"] for row in outcome.document["character"]["life"]["dialogues"]], [False, True])
        self.assertEqual(outcome.receipt["detail"], "2 lines written · 1 draft until its event is ready")

    def test_married_rules_need_romance(self):
        document = new_project()
        document["character"]["romanceable"] = False
        with self.assertRaises(ToolError) as caught:
            run_tool("write_conditional_lines", document, {"rules": [rule("Married", "Dear.", relationship="married")]})
        self.assertIn("Enable romance", str(caught.exception))


class SummaryTests(unittest.TestCase):
    def test_new_character_has_no_rules(self):
        self.assertIn("Daily life (0 rules)", summarize(new_project()))

    def test_lists_every_kind_of_rule_with_status_and_conditions(self):
        document = new_project(story_starter="friendship")
        event = document["character"]["events"][0]
        document["character"]["life"] = {
            "spouse_dialogue": [{"id": "s", "name": "Morning at home", "enabled": False, "moment": "morning", "text": "Morning, love.$l",
                                 "conditions": {"relationship": "married"}}],
            "dialogues": [{"id": "d", "name": "After Honey", "enabled": True, "text": "Snow again.$s",
                           "conditions": {"season": "winter", "after_event_id": event["id"], "min_hearts": 6}}],
            "routines": [{"id": "r", "name": "Rainy days", "enabled": True, "conditions": {"weather": "rainy"},
                          "stops": [{"time": "900", "location": "Town", "x": 1, "y": 2, "facing": "down"}, {"time": "1200", "location": "Saloon", "x": 1, "y": 2, "facing": "down"}]}],
        }
        text = summarize(document)
        self.assertIn("Daily life (3 rules):", text)
        self.assertIn("  conditional line · After Honey · in mod · winter, 6+ hearts, after A shared interest (2 hearts) · Snow again.$s", text)
        self.assertIn("  routine · Rainy days · in mod · rainy · 2 stops", text)
        self.assertIn("  married life · Morning at home · draft · morning at home · Morning, love.$l", text)

    def test_rules_are_capped(self):
        document = new_project()
        document["character"]["life"] = {"dialogues": [{"id": str(i), "name": f"Rule {i}", "enabled": True, "text": "Hi.", "conditions": {"season": "fall"}} for i in range(50)]}
        text = summarize(document)
        self.assertIn("Daily life (50 rules):", text)
        self.assertIn("…and 20 more rules", text)
        self.assertNotIn("Rule 31 ", text)


class EdgeCaseTests(unittest.TestCase):
    def test_same_conditions_twice_in_one_call_are_rejected(self):
        with self.assertRaises(ToolError) as caught:
            run_tool("write_conditional_lines", new_project(), {"rules": [rule("A", "a", season="fall"), rule("B", "b", season="fall")]})
        self.assertEqual(str(caught.exception), "Rules 1 and 2 have the same conditions.")

    def test_unknown_condition_keys_are_rejected_without_a_strict_schema(self):
        with self.assertRaises(ToolError) as caught:
            run_tool("write_married_life", new_project(), {"lines": [{"moment": "morning", "text": "Hi.", "when": {**NULL_HOME_WHEN, "weather": "rainy"}}]})
        self.assertEqual(str(caught.exception), "Unknown conditions: weather.")

    def test_the_core_cap_of_a_hundred_rules_is_respected(self):
        document = new_project()
        document["character"]["life"] = {"dialogues": [{"id": str(i), "name": f"R{i}", "text": "Hi.", "conditions": {"min_hearts": 1}} for i in range(100)]}
        with self.assertRaises(ToolError) as caught:
            run_tool("write_conditional_lines", document, {"rules": [rule("New", "Hi.", season="fall")]})
        self.assertIn("already has 100 rules", str(caught.exception))

    def test_malformed_life_data_is_reported_not_overwritten(self):
        document = new_project()
        document["character"]["life"] = {"dialogues": "nope"}
        with self.assertRaises(ToolError) as caught:
            run_tool("write_married_life", document, {"lines": married_lines(("morning", "Hi."))})
        self.assertIn("need fixing in the app first", str(caught.exception))

    def test_several_drafts_name_every_waiting_event(self):
        document = new_project(story_starter="friendship")
        outcome = run_tool("write_conditional_lines", document, {"rules": [rule("A", "a", after_event_hearts=2), rule("B", "b", after_event_hearts=4)]})
        self.assertEqual(outcome.receipt["detail"], "2 lines written · 2 drafts until their events are ready")
        self.assertIn("marks the 2-heart, 4-heart events ready", outcome.message)

    def test_written_rules_show_up_in_the_next_summary(self):
        outcome = run_tool("write_married_life", new_project(), {"lines": married_lines(("morning", "Hi love.$l"))})
        self.assertIn("married life · Morning at home · in mod · morning at home · Hi love.$l", summarize(outcome.document))

    def test_written_lines_compile_for_export(self):
        from pixelheart_core.life import compile_life
        married = run_tool("write_married_life", new_project(), {"lines": married_lines(("morning", "Hi."), ("rainy_evening", "Rain."))})
        compiled = compile_life(married.document["character"])
        self.assertIn("assets/marriage-dialogue.json", compiled["files"])
        self.assertTrue(all(patch["Target"].startswith("Characters/Dialogue/MarriageDialogue") for patch in compiled["patches"][1:]))
        conditional = run_tool("write_conditional_lines", new_project(), {"rules": [rule("Winter Mondays", "Brr.", season="winter", weekday="Mon")]})
        patch = compile_life(conditional.document["character"])["patches"][-1]
        self.assertEqual((patch["When"], patch["Entries"]), ({"Season": "winter", "DayOfWeek": "Monday"}, {"Mon": "Brr."}))


class ConditionalRoutineTests(unittest.TestCase):
    def test_writes_an_enabled_routine_named_from_its_conditions(self):
        document = new_project()
        outcome = run_tool("plan_conditional_routine", document,
                           routine(stop("9:00", "ArchaeologyHouse", "reads by the window"), stop("1700", "Saloon"), weather="rainy"))
        records = outcome.document["character"]["life"]["routines"]
        self.assertEqual([(row["name"], row["enabled"]) for row in records], [("Routine · rainy", True)])
        self.assertEqual(records[0]["conditions"], {"season": "any", "weather": "rainy", "weekday": "any", "min_hearts": 0,
                                                    "relationship": "any", "after_event_id": "", "min_house_upgrade": 0})
        self.assertEqual([(s["time"], s["location"], s["x"], s["y"], s["facing"], s["activity"]) for s in records[0]["stops"]],
                         [("900", "ArchaeologyHouse", 32, 62, "down", "reads by the window"), ("1700", "Saloon", 32, 62, "down", "")])
        self.assertTrue(all(isinstance(s["id"], str) and s["id"] for s in records[0]["stops"]))
        self.assertEqual(outcome.receipt, {"section": "schedule", "title": "Updated Conditional routines", "detail": "2 stops · place them on the map"})
        self.assertIn("Conditional routines", outcome.message)
        self.assertEqual(outcome.document["character"]["schedule"], document["character"]["schedule"])   # the everyday routine stays
        self.assertIsNone(document["character"].get("life"))   # input untouched

    def test_borrows_tiles_from_the_everyday_routine_and_the_home_tile(self):
        document = new_project()
        document["character"].update(home_map="Forest", home_x=5, home_y=7)
        document["character"]["schedule"] = [{"id": "a", "time": "600", "location": "Saloon", "x": 10, "y": 20, "facing": "left", "activity": ""}]
        outcome = run_tool("plan_conditional_routine", document, routine(stop("900", "Saloon"), stop("1200", "Forest"), stop("1500", "Beach"), season="winter"))
        self.assertEqual([(s["location"], s["x"], s["y"], s["facing"]) for s in outcome.document["character"]["life"]["routines"][0]["stops"]],
                         [("Saloon", 10, 20, "left"), ("Forest", 5, 7, "down"), ("Beach", 32, 62, "down")])

    def test_needs_a_condition(self):
        with self.assertRaises(ToolError) as caught:
            run_tool("plan_conditional_routine", new_project(), routine(stop("900", "Town")))
        self.assertEqual(str(caught.exception), "Give at least one condition; the everyday routine belongs in plan_routine.")

    def test_bad_condition_values_are_rejected(self):
        with self.assertRaises(ToolError) as caught:
            run_tool("plan_conditional_routine", new_project(), routine(stop("900", "Town"), weather="foggy"))
        self.assertIn("Choose weather from", str(caught.exception))

    def test_stops_follow_the_everyday_routine_rules(self):
        cases = (([stop("905", "Town")], "ten-minute steps"),
                 ([stop("900", "Town"), stop("800", "Town")], "time order"),
                 ([stop("900", "Moon")], "Unknown place 'Moon'"),
                 ([], "between 1 and 30 stops"),
                 ([stop("900", "Town", "x" * 301)], "at most 300 characters"))
        for stops, fragment in cases:
            with self.assertRaises(ToolError, msg=fragment) as caught:
                run_tool("plan_conditional_routine", new_project(), {"when": {**NULL_WHEN, "weather": "rainy"}, "stops": stops})
            self.assertIn(fragment, str(caught.exception))

    def test_season_weekday_hearts_and_dating_conditions_are_kept(self):
        outcome = run_tool("plan_conditional_routine", new_project(), routine(stop("900", "Town"), season="fall", weekday="Mon", min_hearts=8, relationship="dating"))
        row = outcome.document["character"]["life"]["routines"][0]
        self.assertEqual({key: row["conditions"][key] for key in ("season", "weekday", "min_hearts", "relationship")},
                         {"season": "fall", "weekday": "Mon", "min_hearts": 8, "relationship": "dating"})
        self.assertEqual((row["name"], row["enabled"]), ("Routine · fall, Mon, 8+ hearts, dating", True))
        self.assertEqual(outcome.receipt["detail"], "1 stop · place it on the map")

    def test_rewrites_the_routine_with_the_same_conditions_and_keeps_placed_tiles(self):
        first = run_tool("plan_conditional_routine", new_project(), routine(stop("900", "ArchaeologyHouse"), weather="rainy"))
        first.document["character"]["life"]["routines"][0]["stops"][0].update(x=5, y=6, facing="up")   # the creator placed it
        winter = run_tool("plan_conditional_routine", first.document, routine(stop("900", "Beach"), season="winter"))
        second = run_tool("plan_conditional_routine", winter.document,
                          routine(stop("1000", "Town"), stop("1300", "ArchaeologyHouse", "shelters from the rain"), weather="rainy"))
        before, after = winter.document["character"]["life"]["routines"], second.document["character"]["life"]["routines"]
        self.assertEqual([(row["id"], row["name"]) for row in after], [(before[0]["id"], "Routine · rainy"), (before[1]["id"], "Routine · winter")])
        self.assertEqual([(s["time"], s["location"], s["x"], s["y"], s["facing"]) for s in after[0]["stops"]],
                         [("1000", "Town", 32, 62, "down"), ("1300", "ArchaeologyHouse", 5, 6, "up")])
        self.assertEqual(second.receipt["detail"], "2 stops · place them on the map")
        self.assertIn("same conditions", second.message)
        self.assertNotIn("same conditions", winter.message)

    def test_a_married_routine_returns_to_the_farmhouse(self):
        outcome = run_tool("plan_conditional_routine", new_project(),
                           routine(stop("900", "Town", "market"), stop("1700", "Saloon", "a drink with friends"), relationship="married"))
        row = outcome.document["character"]["life"]["routines"][0]
        self.assertEqual((row["name"], row["enabled"]), ("Routine · married", True))
        self.assertEqual([(s["time"], s["location"], s["x"], s["y"], s["facing"], s["activity"]) for s in row["stops"]],
                         [("900", "Town", 32, 62, "down", "market"), ("1700", "Saloon", 32, 62, "down", "a drink with friends"),
                          ("2200", "bed", 0, 0, "down", "Return to the spouse's home")])
        self.assertEqual(outcome.receipt["detail"], "3 stops · place them on the map")
        self.assertIn("10:00 PM", outcome.message)
        self.assertEqual([issue["message"] for issue in life_issues(outcome.document["character"]) if "finish at home" in issue["message"]], [])

    def test_a_late_married_routine_returns_an_hour_after_its_last_stop(self):
        outcome = run_tool("plan_conditional_routine", new_project(), routine(stop("900", "Town"), stop("2400", "Saloon"), relationship="married"))
        self.assertEqual([(s["time"], s["location"]) for s in outcome.document["character"]["life"]["routines"][0]["stops"][-2:]],
                         [("2400", "Saloon"), ("2500", "bed")])

    def test_a_married_routine_already_at_two_am_cannot_return_home(self):
        outcome = run_tool("plan_conditional_routine", new_project(), routine(stop("900", "Town"), stop("2600", "Saloon"), relationship="married"))
        row = outcome.document["character"]["life"]["routines"][0]
        self.assertEqual([s["location"] for s in row["stops"]], ["Town", "Saloon"])
        self.assertTrue(row["enabled"])
        self.assertIn("2:00 AM", outcome.message)
        self.assertIn("finish at home", outcome.message)

    def test_an_unmarried_routine_does_not_go_to_the_farmhouse(self):
        outcome = run_tool("plan_conditional_routine", new_project(), routine(stop("900", "Town"), relationship="dating"))
        self.assertEqual([s["location"] for s in outcome.document["character"]["life"]["routines"][0]["stops"]], ["Town"])

    def test_a_married_routine_needs_romance(self):
        document = new_project()
        document["character"]["romanceable"] = False
        with self.assertRaises(ToolError) as caught:
            run_tool("plan_conditional_routine", document, routine(stop("900", "Town"), relationship="married"))
        self.assertIn("Enable romance", str(caught.exception))

    def test_a_routine_after_an_unready_event_stays_a_draft(self):
        document = new_project(story_starter="friendship")
        event = document["character"]["events"][0]
        outcome = run_tool("plan_conditional_routine", document, routine(stop("900", "Beach"), stop("1400", "Town"), after_event_hearts=2))
        row = outcome.document["character"]["life"]["routines"][0]
        self.assertEqual((row["enabled"], row["conditions"]["after_event_id"], row["name"]),
                         (False, event["id"], "Routine · after A shared interest (2 hearts)"))
        self.assertEqual(outcome.receipt["detail"], "2 stops · place them on the map · draft until its event is ready")
        self.assertIn("marks the 2-heart event ready", outcome.message)

    def test_a_routine_after_a_ready_event_goes_into_the_mod(self):
        document = new_project(story_starter="friendship")
        document["character"]["events"][0]["story"]["stage"] = "ready"
        outcome = run_tool("plan_conditional_routine", document, routine(stop("900", "Beach"), after_event_hearts=2))
        self.assertTrue(outcome.document["character"]["life"]["routines"][0]["enabled"])
        self.assertEqual(outcome.receipt["detail"], "1 stop · place it on the map")
        self.assertNotIn("draft", outcome.message)

    def test_the_routine_shows_up_in_the_summary(self):
        outcome = run_tool("plan_conditional_routine", new_project(), routine(stop("900", "Town"), stop("1200", "Saloon"), weather="rainy"))
        self.assertIn("  routine · Routine · rainy · in mod · rainy · 2 stops", summarize(outcome.document))

    def test_the_routine_compiles_for_export(self):
        from pixelheart_core.life import compile_life
        rainy = run_tool("plan_conditional_routine", new_project(), routine(stop("900", "ArchaeologyHouse"), stop("1700", "Saloon"), weather="rainy"))
        patch = compile_life(rainy.document["character"])["patches"][-1]
        self.assertTrue(patch["Target"].startswith("Characters/schedules/"))
        self.assertEqual(patch["When"], {"Weather |locationContext=Default": "Rain, Storm, GreenRain"})
        self.assertEqual(patch["Entries"]["rain"], "900 ArchaeologyHouse 32 62 2/1700 Saloon 32 62 2")
        married = run_tool("plan_conditional_routine", new_project(), routine(stop("900", "Town"), relationship="married"))
        patch = compile_life(married.document["character"])["patches"][-1]
        self.assertEqual(patch["Entries"]["marriage_spring_1"], "900 Town 32 62 2/2200 bed 0 0 2")


if __name__ == "__main__":
    unittest.main()

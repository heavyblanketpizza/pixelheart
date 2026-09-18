"""Daily-life rules compile to usable game assets without losing drafts."""
from copy import deepcopy
import unittest

from pixelheart_core.projects import new_project
from pixelheart_core.life import (
    LifeValidationError, compile_life, life_issues, life_structure_issues,
    new_life_record, normalize_life,
)
from pixelheart_core.story import new_event, new_beat, event_game_id


class LifeTests(unittest.TestCase):
    def setUp(self):
        self.character = new_project()["character"]
        self.character.update(name="Sol", internal_name="Sol", id="stable-sol")
        self.character["life"] = normalize_life()

    def add(self, kind="dialogues", **fields):
        row = new_life_record(kind, self.character)
        row.update(enabled=True, name="A changed day", **fields)
        if kind != "routines":
            row.setdefault("text", "")
            if not row["text"]:
                row["text"] = "There is a place for us here, @.$h"
        self.character["life"][kind].append(row)
        return row

    def errors(self):
        return [issue for issue in life_issues(self.character) if issue["level"] == "error"]

    def test_empty_legacy_characters_compile_unchanged(self):
        self.character.pop("life")
        self.assertEqual(compile_life(self.character), {"patches": [], "files": {}})

    def test_normalization_keeps_extensions_and_drafts(self):
        raw = {"notes": ["keep"], "dialogues": [{"id": "x", "name": "unfinished", "source": "author", "conditions": {"extension": 4}}]}
        before = deepcopy(raw)
        life = normalize_life(raw)
        self.assertEqual(raw, before)
        self.assertEqual(life["notes"], ["keep"])
        self.assertFalse(life["dialogues"][0]["enabled"])
        self.assertEqual(life["dialogues"][0]["conditions"]["extension"], 4)
        self.character["life"] = life
        self.assertEqual(self.errors(), [])
        self.assertEqual(compile_life(self.character)["patches"], [])

    def test_conditions_emit_game_keys_and_stable_event_identity(self):
        event = new_event(self.character)
        event["story"].update(stage="ready", beats=[{**new_beat(), "text": "A promise."}])
        self.character["events"] = [event]
        row = self.add()
        row["conditions"].update(season="fall", weather="rainy", min_hearts=6,
                                 relationship="dating", after_event_id=event["id"], weekday="Fri", min_house_upgrade=1)
        before = deepcopy(self.character)
        compiled = compile_life(self.character, "Example.Sol")
        self.assertEqual(self.character, before)
        patch = compiled["patches"][0]
        self.assertEqual(patch["Target"], "Characters/Dialogue/Example.Sol")
        self.assertEqual(patch["Entries"], {"Fri": row["text"]})
        self.assertEqual(patch["When"], {"Season": "fall", "Weather |locationContext=Default": "Rain, Storm, GreenRain",
            "DayOfWeek": "Friday", "Hearts:Example.Sol": "{{Range: 6, 14}}", "Relationship:Example.Sol": "Dating",
            "FarmhouseUpgrade": "{{Range: 1, 3}}", "HasSeenEvent": event_game_id(event, self.character)})

    def test_later_matching_conversation_wins_and_disabled_rows_stay_out(self):
        first = self.add(text="Before.")
        last = self.add(text="After.")
        draft = self.add(text="Draft.")
        draft["enabled"] = False
        patches = compile_life(self.character)["patches"]
        self.assertEqual([patch["Entries"]["Mon"] for patch in patches], [first["text"], last["text"]])
        self.assertEqual(len(patches[0]["Entries"]), 7)

    def test_normal_routes_override_season_and_rain_fallbacks(self):
        row = self.add("routines")
        row["conditions"].update(season="winter", weather="rainy")
        row["stops"][0].update(time="09:30", location="Town", x=22, y=31, facing="left")
        patch = compile_life(self.character)["patches"][0]
        self.assertEqual(set(patch["Entries"]), {"spring", "summer", "fall", "winter", "rain", "rain2"})
        self.assertEqual(patch["Entries"]["rain"], "930 Town 22 31 3")
        self.assertEqual(patch["When"]["Season"], "winter")

    def test_married_routines_use_date_keys_to_work_in_rain(self):
        row = self.add("routines")
        row["conditions"].update(relationship="married", weekday="Mon", weather="rainy")
        row["stops"].append({"time": "2200", "location": "bed", "x": 0, "y": 0, "facing": "down"})
        patch = compile_life(self.character)["patches"][0]
        self.assertEqual(len(patch["Entries"]), 112)
        self.assertTrue(patch["Entries"]["marriage_winter_28"].endswith("2200 bed 0 0 2"))
        self.assertNotIn("marriage_Mon", patch["Entries"])
        self.assertEqual(patch["When"]["DayOfWeek"], "Monday")

    def test_authored_spouse_dialogue_fills_random_slots_including_named_evening(self):
        row = self.add("spouse_dialogue", moment="rainy_evening", text="Listen to the rain.$l")
        output = compile_life(self.character, "Example.Sol")
        self.assertEqual(output["files"], {"assets/marriage-dialogue.json": {}})
        self.assertEqual(output["patches"][0], {"Action": "Load", "Target": "Characters/Dialogue/MarriageDialogueExample.Sol", "FromFile": "assets/marriage-dialogue.json"})
        entries = output["patches"][1]["Entries"]
        self.assertEqual(set(entries), {*(f"Rainy_Night_{index}" for index in range(6)), "Rainy_Night_Example.Sol"})
        self.assertTrue(all(text == row["text"] for text in entries.values()))

    def test_married_conditional_conversation_reaches_actual_spouse_asset(self):
        row = self.add()
        row["conditions"]["relationship"] = "married"
        result = compile_life(self.character, "Example.Sol")
        self.assertEqual(result["patches"][-1]["Target"], "Characters/Dialogue/MarriageDialogueExample.Sol")
        self.assertIn("Indoor_Day_0", result["patches"][-1]["Entries"])
        self.assertIn("Rainy_Night_Example.Sol", result["patches"][-1]["Entries"])

    def test_missing_unready_and_repeating_prerequisites_block_enabled_rules(self):
        row = self.add()
        row["conditions"]["after_event_id"] = "missing"
        self.assertTrue(self.errors())
        event = new_event(self.character)
        self.character["events"] = [event]
        row["conditions"]["after_event_id"] = event["id"]
        self.assertIn("ready", self.errors()[0]["message"])
        event["story"].update(stage="ready", repeat="daily")
        self.assertIn("Repeating", self.errors()[0]["message"])
        row["enabled"] = False
        self.assertEqual(self.errors(), [])

    def test_romance_gates_are_enforced(self):
        self.character["romanceable"] = False
        self.add("spouse_dialogue")
        self.assertTrue(self.errors())
        self.character["life"]["spouse_dialogue"][0]["enabled"] = False
        self.assertEqual(self.errors(), [])

    def test_enabled_rule_needs_text_and_valid_ordered_stops(self):
        row = self.add(text="draft")
        row["text"] = ""
        route = self.add("routines")
        route["stops"] = [{"time": "0967", "location": "Bad/location", "x": 3, "y": 4},
                          {"time": "1200", "location": "Town", "x": 3, "y": 4},
                          {"time": "1000", "location": "Town", "x": 3, "y": 4}]
        self.assertEqual(len(self.errors()), 4)
        with self.assertRaises(LifeValidationError):
            compile_life(self.character)

    def test_plain_dialogue_cannot_hide_items_mail_or_trigger_actions(self):
        row = self.add()
        for text in ("[74]", "$1 hiddenMail#grant", "$action AddMoney 500", "%fork hidden", "{{Spouse}}"):
            with self.subTest(text=text):
                row["text"] = text
                self.assertTrue(self.errors())
        row["text"] = "We made it.$1#$b#Tomorrow will be better.$h"
        self.assertEqual(self.errors(), [])

    def test_malformed_structures_fail_without_crashing(self):
        examples = [None, [], {"dialogues": None}, {"dialogues": [None]},
                    {"spouse_dialogue": [{"moment": []}]}, {"dialogues": [{"conditions": []}]},
                    {"dialogues": [{"conditions": {"min_hearts": True}}]},
                    {"routines": [{"stops": [None]}]}, {"dialogues": [{"enabled": "yes"}]}]
        for raw in examples:
            with self.subTest(raw=raw):
                self.assertTrue(life_structure_issues(raw))

    def test_unknown_metadata_survives_compile(self):
        row = self.add()
        row["notes"] = {"revision": 3}
        row["conditions"]["external"] = [1, 2]
        snapshot = deepcopy(self.character)
        compile_life(self.character)
        self.assertEqual(self.character, snapshot)


if __name__ == "__main__":
    unittest.main()

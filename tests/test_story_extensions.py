"""Real relationship gates, repeatable events and final player choices."""
from copy import deepcopy
import unittest

from pixelheart_core.projects import new_project
from pixelheart_core.story import (
    StoryValidationError, new_event, new_beat, event_game_id, compile_story,
    event_issues, story_repeat_events, structure_issues,
)


class StoryExtensionTests(unittest.TestCase):
    def setUp(self):
        self.character = new_project()["character"]
        self.character.update(id="same-project", name="Sol", internal_name="Sol")
        self.event = new_event(self.character)
        self.event["story"].update(stage="ready", beats=[{**new_beat(), "text": "Stay a moment."}])
        self.character["events"] = [self.event]

    def entries(self):
        return compile_story(self.character, npc_id="Example.Sol")[0]["Entries"]

    def choice(self):
        beat = new_beat("choice")
        beat.update(text="How do you answer?", actor="$npc")
        beat["choices"][0].update(label="I will stay.", text="Then let us face tomorrow.$h", friendship=50)
        beat["choices"][1].update(label="I need time.", text="Take all the time you need.", friendship=0)
        self.event["story"]["beats"].append(beat)
        return beat

    def test_relationship_and_home_gates_are_real_game_queries(self):
        story = self.event["story"]
        for relationship, query in (("dating", "PLAYER_NPC_RELATIONSHIP Current Example.Sol Dating"),
                                    ("married", "PLAYER_NPC_RELATIONSHIP Current Example.Sol Married"),
                                    ("unmarried", "!PLAYER_NPC_RELATIONSHIP Current Example.Sol Married")):
            story.update(relationship=relationship, min_house_upgrade=2)
            key = next(iter(self.entries()))
            self.assertIn("/GameStateQuery " + query, key)
            self.assertIn("/GameStateQuery PLAYER_FARMHOUSE_UPGRADE Current 2", key)

    def test_nonromance_character_cannot_require_marriage(self):
        self.character["romanceable"] = False
        self.event["story"]["relationship"] = "married"
        with self.assertRaises(StoryValidationError):
            self.entries()

    def test_repeat_ids_are_concrete_and_independent_of_names(self):
        self.event["story"]["repeat"] = "daily"
        expected = event_game_id(self.event, self.character).replace("{{ModId}}", "Author.Pack")
        self.assertEqual(story_repeat_events(self.character, mod_id="Author.Pack"), [expected])
        self.event["name"] = "Renamed"
        self.assertEqual(story_repeat_events(self.character, mod_id="Author.Pack"), [expected])
        self.event["story"]["stage"] = "scene"
        self.assertEqual(story_repeat_events(self.character, mod_id="Author.Pack"), [])

    def test_repeating_events_cannot_be_story_prerequisites(self):
        self.event["story"]["repeat"] = "daily"
        later = new_event(self.character)
        later["story"].update(stage="ready", previous_event_id=self.event["id"], beats=[{**new_beat(), "text": "Later."}])
        self.character["events"].append(later)
        self.assertTrue(any("forget" in issue["message"] and issue["level"] == "error" for issue in event_issues(later, self.character)))

    def test_choice_compiles_two_distinct_outcomes_and_one_real_fork(self):
        self.choice()
        before = deepcopy(self.character)
        entries = self.entries()
        self.assertEqual(self.character, before)
        self.assertEqual(len(entries), 2)
        root_key = next(key for key in entries if "/Friendship" in key)
        branch_key = next(key for key in entries if "/" not in key)
        self.assertIn('question fork0 "How do you answer?#I will stay.#I need time."', entries[root_key])
        self.assertIn("/fork " + branch_key + "/", entries[root_key])
        self.assertIn("Take all the time you need.", entries[root_key])
        self.assertNotIn("friendship Example.Sol 50", entries[root_key])
        self.assertIn("Then let us face tomorrow.$h", entries[branch_key])
        self.assertIn("friendship Example.Sol 50", entries[branch_key])
        self.assertTrue(entries[branch_key].startswith("setSkipActions AddFriendshipPoints Example.Sol 50/"))
        self.assertTrue(entries[branch_key].endswith("/setSkipActions/endSimultaneousCommand/end"))

    def test_choice_effects_are_not_granted_by_skipping_before_decision(self):
        self.choice()
        entries = self.entries()
        root = next(value for key, value in entries.items() if "/Friendship" in key)
        self.assertNotIn("AddFriendshipPoints", root.split("/question", 1)[0])

    def test_choice_requires_two_safe_responses_and_final_placement(self):
        choice = self.choice()
        for key, bad in (("label", "break#answer"), ("text", "$action AddMoney 500"), ("friendship", True)):
            original = choice["choices"][0][key]
            choice["choices"][0][key] = bad
            with self.subTest(key=key), self.assertRaises(StoryValidationError):
                self.entries()
            choice["choices"][0][key] = original
        self.event["story"]["beats"].append({**new_beat(), "text": "After the choice."})
        self.assertTrue(any("end of the scene" in issue["message"] for issue in event_issues(self.event, self.character)))

    def test_imported_choice_limits_validate_without_execution(self):
        choice = self.choice()
        choice["choices"] = [None]
        self.assertTrue(structure_issues(self.event))
        choice["choices"] = []
        self.assertTrue(any("exactly two" in issue["message"] for issue in event_issues(self.event, self.character)))
        self.event["story"]["min_house_upgrade"] = True
        self.assertTrue(structure_issues(self.event))


if __name__ == "__main__":
    unittest.main()

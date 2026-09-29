"""Deleting a scene unlinks organization but preserves gameplay dependencies."""
from copy import deepcopy
import unittest

from pixelheart_core.life import new_life_record
from pixelheart_core.projects import new_project
from pixelheart_core.story import new_event, new_relationship
from pixelheart_core.story_planning import (
    StoryPlanningError, event_references, event_removal_issues, new_chapter, remove_event,
)


class StoryRemovalTests(unittest.TestCase):
    def test_seed_event_removes_its_membership_without_removing_or_rewriting_chapter(self):
        character = new_project(story_starter="romance")["character"]
        event = character["events"][0]
        before = deepcopy(character)
        self.assertTrue(event_references(character, event["id"]))
        self.assertEqual(event_removal_issues(character, event["id"]), [])
        result = remove_event(character, event["id"])
        self.assertEqual(character, before)
        self.assertEqual(result["events"], before["events"][1:])
        expected = deepcopy(before["storyline"])
        expected["chapters"][0]["event_ids"] = []
        self.assertEqual(result["storyline"], expected)
        self.assertEqual(len(result["storyline"]["chapters"]), 6)

    def test_shared_scene_unlinks_all_chapters_and_preserves_extensions_and_other_links(self):
        character = new_project()["character"]
        first, second = new_event(character), new_event(character)
        arc = new_relationship()
        first["story"].update(relationship_id=arc["id"], arc_ids=[arc["id"]])
        chapters = [new_chapter("One"), new_chapter("Two")]
        for chapter in chapters:
            chapter.update(event_ids=[second["id"], first["id"]], arc_ids=[arc["id"]],
                           purpose="Keep these authored notes.", extension={"nested": [1, 2]})
        character.update(events=[first, second], relationships=[arc], extension={"keep": True})
        character["storyline"].update(chapters=chapters, extension={"outline": "keep"})
        before = deepcopy(character)
        result = remove_event(character, first["id"])
        self.assertEqual(character, before)
        self.assertEqual(result["events"], [second])
        self.assertEqual(result["relationships"], [arc])
        for actual, original in zip(result["storyline"]["chapters"], chapters):
            self.assertEqual(actual, {**original, "event_ids": [second["id"]]})
        result["storyline"]["chapters"][0]["extension"]["nested"].append(3)
        self.assertEqual(character, before)

    def test_prerequisite_and_disabled_life_rules_block_with_actionable_editor_paths(self):
        character = new_project()["character"]
        first, later = new_event(character), new_event(character)
        later.update(name="A later scene")
        later["story"]["previous_event_id"] = first["id"]
        character["events"] = [first, later]
        character["life"] = {}
        expected = ["events.1.story.previous_event_id"]
        for kind in ("dialogues", "routines", "spouse_dialogue"):
            rule = new_life_record(kind, character)
            rule["name"] = "Remember the scene"
            rule["conditions"]["after_event_id"] = first["id"]
            self.assertFalse(rule["enabled"])
            character["life"][kind] = [rule]
            expected.append(f"life.{kind}.0.conditions.after_event_id")
        before = deepcopy(character)
        issues = event_removal_issues(character, first["id"])
        self.assertEqual([issue["field"] for issue in issues], expected)
        self.assertTrue(all(issue["level"] == "error" and "Choose another" in issue["message"] for issue in issues))
        with self.assertRaises(StoryPlanningError) as caught:
            remove_event(character, first["id"])
        self.assertEqual(caught.exception.issues, issues)
        self.assertEqual(character, before)

    def test_explicitly_resolving_gameplay_dependencies_allows_removal(self):
        character = new_project()["character"]
        first, later = new_event(character), new_event(character)
        later["story"]["previous_event_id"] = first["id"]
        character["events"] = [first, later]
        self.assertTrue(event_removal_issues(character, first["id"]))
        later["story"]["previous_event_id"] = ""
        self.assertEqual(remove_event(character, first["id"])["events"], [later])

    def test_invalid_self_reference_does_not_make_a_scene_impossible_to_remove(self):
        character = new_project()["character"]
        event = new_event(character)
        event["story"]["previous_event_id"] = event["id"]
        character["events"] = [event]
        self.assertEqual(remove_event(character, event["id"])["events"], [])

    def test_missing_or_ambiguous_event_never_removes_other_work(self):
        character = new_project(story_starter="romance")["character"]
        for identity in (None, "", "missing"):
            with self.assertRaises(StoryPlanningError):
                remove_event(character, identity)
        character["events"].append(deepcopy(character["events"][0]))
        before = deepcopy(character)
        with self.assertRaises(StoryPlanningError):
            remove_event(character, character["events"][0]["id"])
        self.assertEqual(character, before)

    def test_dependency_paths_preserve_original_indices_in_incomplete_imports(self):
        character = {"events": [None, {"id": "later", "story": {"previous_event_id": "first"}}],
                     "life": {"dialogues": [None, {"conditions": {"after_event_id": "first"}}]}}
        self.assertEqual([issue["field"] for issue in event_removal_issues(character, "first")],
                         ["events.1.story.previous_event_id", "life.dialogues.1.conditions.after_event_id"])
        for value in (None, {}, {"events": None, "life": None}):
            self.assertEqual(event_removal_issues(value, "first"), [])


if __name__ == "__main__":
    unittest.main()

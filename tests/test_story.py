"""Story draft, dependency, and executable event regressions without Qt."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from pixelheart_core.projects import load_project, new_project, save_project
from pixelheart_core.story import (
    StoryValidationError, compile_story, event_game_id, event_issues, exported_npc_id,
    new_actor, new_beat, new_event, new_relationship, normalize_event,
    normalize_relationship, relationship_events, story_issues,
)
from pixelheart_core.validation import DraftValidationError, validate_draft


class StoryTests(unittest.TestCase):
    def setUp(self):
        self.character = new_project()["character"]
        self.character.update(id="stable-project", internal_name="Ada", name="Ada")

    def ready_event(self, **changes):
        event = new_event(self.character)
        event.update(changes)
        event["story"].update(stage="ready", beats=[{**new_beat(), "text": "Hello, @.$h"}])
        self.character["events"].append(event)
        return event

    def errors(self, issues):
        return [item for item in issues if item["level"] == "error"]

    def test_new_stories_and_templates_are_independent_drafts(self):
        first, second = new_event(self.character), new_event(self.character)
        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(first["story"]["actors"][0]["id"], second["story"]["actors"][0]["id"])
        self.assertEqual(first["story"]["stage"], "idea")
        self.assertEqual([actor["name"] for actor in first["story"]["actors"]], ["$npc", "farmer"])
        for template in ("first_meeting", "conflict", "reconciliation"):
            event = new_event(self.character, template)
            self.assertEqual(event["story"]["stage"], "outline")
            self.assertTrue(event["story"]["premise"])
            self.assertTrue(event["story"]["conflict"])
            self.assertTrue(event["story"]["outcome"])
            self.assertEqual(event["story"]["beats"][0]["text"], "")
        self.assertEqual(new_relationship()["story"]["stage"], "idea")
        with self.assertRaises(ValueError):
            new_event(template="raw_script")
        with self.assertRaises(ValueError):
            new_beat("script")

    def test_legacy_notes_remain_drafts_and_compile_to_nothing(self):
        self.character["events"] = [{"name": "A seed", "description": "Keep every word."}, {"name": "Another seed"}]
        self.character["relationships"] = [{"name": "Robin", "relation": "Old friends", "description": "They share a past."}]
        original = copy.deepcopy(self.character)
        self.assertEqual(compile_story(self.character), [])
        self.assertEqual(self.character, original)
        self.assertFalse(self.errors(story_issues(self.character)))
        event = normalize_event(self.character["events"][0])
        self.assertEqual(event["description"], "Keep every word.")
        self.assertEqual(event["story"]["stage"], "idea")
        self.assertEqual(event["story"]["beats"], [])

    def test_save_load_round_trip_preserves_nested_metadata_and_stable_ids(self):
        project = new_project()
        event = {"name": "Legacy seed", "description": "  notes\n", "extension": {"source": "writer"},
                 "story": {"stage": "scene", "custom": ["keep"], "actors": [{"name": "$npc", "extra": 4}],
                           "beats": [{"kind": "dialogue", "text": "Draft", "annotation": {"color": "pink"}}]}}
        relation = {"name": "An old friend", "story": {"tension": "A difficult promise", "custom": True}}
        project["character"].update(events=[event], relationships=[relation])
        original = copy.deepcopy(project)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "character.json"
            save_project(project, path)
            loaded = load_project(path)
            save_project(loaded, path)
            again = load_project(path)
        self.assertEqual(project, original)
        self.assertEqual(loaded["character"]["events"], again["character"]["events"])
        saved = again["character"]["events"][0]
        self.assertEqual(saved["extension"], {"source": "writer"})
        self.assertEqual(saved["description"], "  notes\n")
        self.assertEqual(saved["story"]["custom"], ["keep"])
        self.assertEqual(saved["story"]["actors"][0]["extra"], 4)
        self.assertEqual(saved["story"]["beats"][0]["annotation"], {"color": "pink"})
        self.assertTrue(saved["story"]["beats"][0]["id"])
        self.assertEqual(again["character"]["relationships"][0]["story"]["target"], "farmer")

    def test_ready_compilation_has_real_header_preconditions_and_scene(self):
        event = self.ready_event(id="first", location="Town", hearts=4)
        event["story"].update(season="fall", weather="rainy", time_start=900, time_end=1730)
        event["story"]["beats"].extend([
            {**new_beat("emote"), "actor": "farmer", "emote": 20},
            {**new_beat("move"), "actor": "$npc", "x": -2, "y": 0, "facing": 3},
            {**new_beat("pause"), "duration": 800},
            {**new_beat("dialogue"), "actor": "farmer", "text": "I'll be here."},
        ])
        original = copy.deepcopy(self.character)
        patches = compile_story(self.character)
        self.assertEqual(self.character, original)
        self.assertEqual(patches[0]["Action"], "EditData")
        self.assertEqual(patches[0]["Target"], "Data/Events/Town")
        key, script = next(iter(patches[0]["Entries"].items()))
        npc = exported_npc_id(self.character)
        self.assertEqual(key, f"{event_game_id(event, self.character)}/Friendship {npc} 1000/Time 900 1730/Season fall/Weather rainy")
        self.assertTrue(script.startswith(f"none/32 62/{npc} 32 62 2 farmer 32 64 0/skippable/"))
        self.assertIn(f'speak {npc} "Hello, @.$h"', script)
        self.assertIn("/emote farmer 20/", script)
        self.assertIn(f"/move {npc} -2 0 3/pause 800/", script)
        self.assertIn('message "I\'ll be here."', script)
        self.assertTrue(script.endswith("/end"))
        self.assertNotIn("speak farmer", script)

    def test_optional_planning_prose_does_not_block_playability(self):
        event = self.ready_event()
        self.assertEqual(event["story"]["premise"], "")
        self.assertEqual(self.errors(event_issues(event, self.character)), [])
        self.assertEqual(self.errors(story_issues(self.character)), [])

    def test_ready_selection_and_grouping_ignore_drafts(self):
        self.ready_event(id="town-a")
        self.ready_event(id="town-b")
        self.ready_event(id="forest", location="Forest")
        draft = new_event(self.character)
        draft["story"]["beats"] = [{**new_beat(), "text": 'A draft may contain "quotes".'}]
        self.character["events"].append(draft)
        result = compile_story(self.character)
        self.assertEqual([patch["Target"] for patch in result], ["Data/Events/Town", "Data/Events/Forest"])
        self.assertEqual([len(patch["Entries"]) for patch in result], [2, 1])
        self.assertNotIn("A draft", json.dumps(result))

    def test_game_ids_survive_renaming_reordering_and_scene_edits(self):
        event, other = self.ready_event(id="event-a"), self.ready_event(id="event-b")
        expected = event_game_id(event, self.character)
        event.update(name="A different title", location="Forest", hearts=8)
        event["story"]["time_start"] = 1500
        self.character["events"].reverse()
        self.assertEqual(event_game_id(event, self.character), expected)
        self.assertNotEqual(event_game_id(other, self.character), expected)
        other_project = {**self.character, "id": "another-project"}
        self.assertNotEqual(event_game_id(event, other_project), expected)

    def test_actor_aliases_and_explicit_export_namespace_match(self):
        event = self.ready_event()
        event["story"]["actors"][0]["name"] = "Ada"
        event["story"]["beats"][0]["actor"] = "Ada"
        script = next(iter(compile_story(self.character, npc_id="Example.Mod_Ada")[0]["Entries"].values()))
        self.assertIn('speak Example.Mod_Ada "Hello', script)
        self.assertIn("Example.Mod_Ada 32 62 2", script)
        with self.assertRaises(StoryValidationError):
            compile_story(self.character, npc_id="Ada/end")

    def test_prerequisites_use_exported_ids_and_detect_missing_draft_cycles(self):
        first = self.ready_event(id="first")
        second = self.ready_event(id="second", hearts=4)
        second["story"]["previous_event_id"] = first["id"]
        keys = list(compile_story(self.character)[0]["Entries"])
        self.assertIn("/SawEvent " + event_game_id(first, self.character), keys[1])
        first["story"]["stage"] = "scene"
        self.assertTrue(any(item["field"] == "events.1.story.previous_event_id" for item in self.errors(story_issues(self.character))))
        first["story"]["stage"] = "ready"
        first["story"]["previous_event_id"] = second["id"]
        self.assertTrue(any("cycle" in item["message"] for item in event_issues(first, self.character)))
        first["story"]["previous_event_id"] = "first"
        self.assertTrue(any("itself" in item["message"] for item in event_issues(first, self.character)))
        first["story"]["previous_event_id"] = "removed"
        with self.assertRaises(StoryValidationError):
            compile_story(self.character)

    def test_dependency_cycle_with_three_events_and_duplicate_ids(self):
        records = [self.ready_event(id=letter) for letter in ("a", "b", "c")]
        for index, record in enumerate(records):
            record["story"]["previous_event_id"] = records[(index + 1) % 3]["id"]
        self.assertEqual(sum("cycle" in item["message"] for item in story_issues(self.character)), 3)
        records[-1]["id"] = "a"
        self.assertTrue(any(item["field"] == "events.2.id" for item in self.errors(story_issues(self.character))))

    def test_relationship_milestones_are_linked_repeatable_and_preserve_authored_work(self):
        relation = new_relationship()
        relation.update(name="Robin", description="Friends with a shared past.")
        relation["story"].update(target="Robin", desire="To be understood", tension="A forgotten promise",
                                  progression="A second chance", resolution="They choose trust")
        self.character["relationships"] = [relation]
        events = relationship_events(relation, self.character)
        self.assertEqual([event["hearts"] for event in events], [2, 4, 6, 8])
        self.assertEqual([event["story"]["stage"] for event in events], ["outline"] * 4)
        self.assertEqual([event["story"]["relationship_id"] for event in events], [relation["id"]] * 4)
        self.assertEqual([event["story"]["previous_event_id"] for event in events], [""] + [event["id"] for event in events[:-1]])
        self.assertEqual(events[0]["story"]["premise"], "To be understood")
        self.assertEqual(events[-1]["story"]["outcome"], "They choose trust")
        self.assertIn("Robin", [actor["name"] for actor in events[0]["story"]["actors"]])
        events[0]["name"] = "Keep this custom title"
        self.character["events"] = events
        repeated = relationship_events(relation, self.character)
        self.assertEqual(repeated, events)
        self.assertIsNot(repeated[0], events[0])
        self.assertEqual(compile_story(self.character), [])

    def test_relationship_readiness_requires_a_playable_link_and_valid_target(self):
        relation = new_relationship()
        relation.update(name="Growing closer")
        relation["story"]["stage"] = "ready"
        self.character["relationships"] = [relation]
        self.assertTrue(any(item["field"] == "relationships.0.story.stage" for item in self.errors(story_issues(self.character))))
        event = self.ready_event()
        event["story"]["relationship_id"] = relation["id"]
        self.assertEqual(self.errors(story_issues(self.character)), [])
        relation["story"]["target"] = "Ada"
        self.assertTrue(any(item["field"] == "relationships.0.story.target" for item in self.errors(story_issues(self.character))))
        self.character["relationships"] = []
        self.assertTrue(any(item["field"] == "story.relationship_id" for item in event_issues(event, self.character)))

    def test_ready_relationship_requires_every_linked_scene_ready_and_valid(self):
        relation = new_relationship()
        relation.update(name="Shared trust")
        relation["story"]["stage"] = "ready"
        self.character["relationships"] = [relation]
        first = self.ready_event(id="first")
        first["story"]["relationship_id"] = relation["id"]
        second = new_event(self.character)
        second["story"]["relationship_id"] = relation["id"]
        self.character["events"].append(second)
        self.assertTrue(any(item["field"] == "relationships.0.story.stage" for item in self.errors(story_issues(self.character))))
        second["story"]["stage"] = "ready"
        self.assertTrue(any("Resolve the linked" in item["message"] for item in self.errors(story_issues(self.character))))
        second["story"]["beats"] = [{**new_beat(), "text": "A new understanding."}]
        self.assertEqual(self.errors(story_issues(self.character)), [])
        relation["story"]["target"] = "Robin"
        self.assertTrue(any(item["field"] == "story.actors" and "Robin" in item["message"] for item in self.errors(event_issues(first, self.character))))
        for event in (first, second):
            event["story"]["actors"].append(new_actor("Robin"))
        self.assertEqual(self.errors(story_issues(self.character)), [])

    def test_dialogue_command_injection_is_blocked_but_literal_slashes_are_quoted(self):
        event = self.ready_event()
        for text in ('Hello"/end', "Hello\\", "{{ModId}}", "Hello\x1b", "#$action AddMoney 999", "$q 1 question", "$f Ada 250",
                     "%fork", "%revealtaste:Lewis:(O)258", "%revealtasteLewis258", "Take this [74]", "[(O)198 (O)202]",
                     "$1 secretMailFlag#First-time text", "$1\tsecretMailFlag#First-time text", "$1letter#Text"):
            with self.subTest(text=text):
                event["story"]["beats"][0]["text"] = text
                with self.assertRaises(StoryValidationError):
                    compile_story(self.character)
        event["story"]["beats"][0]["text"] = "A/B or /end?\n“Neither,” she says.$h"
        script = next(iter(compile_story(self.character)[0]["Entries"].values()))
        self.assertIn('"A/B or /end?#$b#“Neither,” she says.$h"', script)
        self.assertTrue(script.endswith('"/end'))

    def test_portrait_and_safe_replacement_tags_remain_supported(self):
        event = self.ready_event()
        event["story"]["beats"][0]["text"] = "Hello, @ from %farm!$1#$b#I'm 100% sure.$2\nSee you soon.$0"
        self.assertEqual(self.errors(event_issues(event, self.character)), [])
        self.assertTrue(compile_story(self.character))

    def test_unreachable_nonromance_heart_gates_block_ready_only(self):
        event = self.ready_event(hearts=11)
        self.character["romanceable"] = False
        self.assertTrue(any(item["field"] == "hearts" for item in self.errors(event_issues(event, self.character))))
        event["story"]["stage"] = "outline"
        self.assertEqual(compile_story(self.character), [])
        event.update(hearts=10)
        event["story"]["stage"] = "ready"
        self.assertEqual(self.errors(event_issues(event, self.character)), [])

    def test_script_fields_cannot_inject_commands_or_cp_tokens(self):
        event = self.ready_event()
        for field, bad in (("location", "Town/Forest"), ("location", "../Town"), ("location", "{{Target}}")):
            with self.subTest(field=field, value=bad):
                candidate = copy.deepcopy(event)
                candidate[field] = bad
                self.assertTrue(self.errors(event_issues(candidate, self.character)))
        for field, bad in (("music", "none/end"), ("music", "{{Dynamic}}")):
            candidate = copy.deepcopy(event)
            candidate["story"][field] = bad
            self.assertTrue(self.errors(event_issues(candidate, self.character)))
        for name in ("Evil/end", "{{NPC}}", "Robin Abigail", "../NPC"):
            candidate = copy.deepcopy(event)
            candidate["story"]["actors"].append({**new_actor(), "name": name})
            self.assertTrue(self.errors(event_issues(candidate, self.character)))

    def test_actor_presence_movement_time_and_emote_errors_are_actionable(self):
        event = self.ready_event()
        cases = [
            ("time_start", 960, "story.time_start"),
            ("time_end", 605, "story.time_end"),
            ("actors", [event["story"]["actors"][0]], "story.actors"),
            ("beats", [{**new_beat(), "actor": "Robin", "text": "Hello"}], "story.beats.0.actor"),
            ("beats", [{**new_beat("move"), "x": 1, "y": 1}], "story.beats.0.x"),
            ("beats", [{**new_beat("move"), "x": 0, "y": 0}], "story.beats.0.x"),
            ("beats", [{**new_beat("move"), "x": -100, "y": 0}], "story.beats.0.x"),
            ("beats", [{**new_beat("friendship"), "actor": "farmer"}], "story.beats.0.actor"),
            ("beats", [{**new_beat("emote"), "emote": 99}], "story.beats.0.emote"),
        ]
        for field, value, expected in cases:
            with self.subTest(field=field, value=value):
                candidate = copy.deepcopy(event)
                candidate["story"][field] = value
                self.assertIn(expected, [item["field"] for item in self.errors(event_issues(candidate, self.character))])
        event["story"].update(time_start=1800, time_end=1700)
        self.assertIn("story.time_end", [item["field"] for item in self.errors(event_issues(event, self.character))])

    def test_friendship_effects_are_preserved_when_skipping_without_double_awards(self):
        event = self.ready_event()
        event["story"]["beats"].extend([
            {**new_beat("friendship"), "amount": 25},
            {**new_beat("pause"), "duration": 1000},
            {**new_beat("friendship"), "amount": -10},
        ])
        script = next(iter(compile_story(self.character)[0]["Entries"].values()))
        npc = exported_npc_id(self.character)
        self.assertIn(f"/setSkipActions AddFriendshipPoints {npc} 25#AddFriendshipPoints {npc} -10/skippable/", script)
        self.assertIn(f"/beginSimultaneousCommand/friendship {npc} 25/setSkipActions AddFriendshipPoints {npc} -10/endSimultaneousCommand/", script)
        self.assertIn(f"/beginSimultaneousCommand/friendship {npc} -10/setSkipActions/endSimultaneousCommand/end", script)

    def test_typed_structural_bounds_reject_malformed_nested_data_during_save(self):
        cases = [
            ({"story": []}, "events.0.story"),
            ({"story": {"stage": []}}, "events.0.story.stage"),
            ({"story": {"actors": {}}}, "events.0.story.actors"),
            ({"story": {"beats": [None]}}, "events.0.story.beats.0"),
            ({"story": {"actors": [{"name": 3}]}}, "events.0.story.actors.0.name"),
            ({"story": {"actors": [{"x": True}]}}, "events.0.story.actors.0.x"),
            ({"story": {"beats": [{"kind": "script"}]}}, "events.0.story.beats.0.kind"),
            ({"story": {"beats": [{"amount": True}]}}, "events.0.story.beats.0.amount"),
            ({"story": {"beats": [{"duration": 60001}]}}, "events.0.story.beats.0.duration"),
            ({"story": {"beats": [{"text": "x" * 8001}]}}, "events.0.story.beats.0.text"),
            ({"story": {"beats": [{}] * 201}}, "events.0.story.beats"),
            ({"story": {"actors": [{}] * 17}}, "events.0.story.actors"),
            ({"story": {"time_start": "600"}}, "events.0.story.time_start"),
            ({"story": {"premise": "null\x00"}}, "events.0.story.premise"),
            ({"story": {"premise": "bad\ud800"}}, "events.0.story.premise"),
        ]
        for record, expected in cases:
            with self.subTest(record=str(record)[:100]):
                original = copy.deepcopy(record)
                with self.assertRaises(DraftValidationError) as raised:
                    validate_draft({"events": [record]})
                self.assertIn(expected, raised.exception.errors)
                self.assertEqual(record, original)
                self.assertTrue(self.errors(story_issues({"events": [record]})))

    def test_draft_save_does_not_require_readiness(self):
        event = new_event(self.character)
        event.update(location="", name="")
        event["story"].update(previous_event_id="removed", relationship_id="missing", time_start=960)
        cleaned = validate_draft({"events": [event]})
        self.assertEqual(cleaned["events"][0], event)
        self.assertFalse(self.errors(story_issues({**self.character, "events": [event]})))
        event["story"]["stage"] = "ready"
        self.assertTrue(self.errors(story_issues({**self.character, "events": [event]})))

    def test_export_reports_one_summary_per_draft_and_rehearsal_keeps_details(self):
        events = [normalize_event({"id": str(index), "name": f"Idea {index}"}) for index in range(100)]
        self.character["events"] = events
        issues = story_issues(self.character)
        self.assertEqual(len(issues), 100)
        self.assertTrue(all(item["level"] == "warning" and "3 readiness issues" in item["message"] for item in issues))
        self.assertEqual(len(self.errors(event_issues(events[0], self.character))), 3)
        events[0]["story"]["actors"] = "broken"
        self.assertTrue(self.errors(story_issues(self.character)))

    def test_malformed_collections_never_crash_export_checks(self):
        for value in (None, {}, "text", 4, True):
            with self.subTest(value=value):
                self.assertTrue(self.errors(story_issues({"events": value, "relationships": value})))
                with self.assertRaises(StoryValidationError):
                    compile_story({"events": value, "relationships": value})
        for record in ({"id": []}, {"id": {}}, {"id": None}, {"story": {"actors": [{"id": []}]}}):
            self.assertTrue(self.errors(story_issues({"events": [record]})))


if __name__ == "__main__":
    unittest.main()

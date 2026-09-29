"""Planning is additive, preserves gameplay identity, and exposes runtime limits."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from pixelheart_core.life import new_life_record
from pixelheart_core.projects import ProjectError, load_project, new_project, save_project
from pixelheart_core.story import (
    StoryValidationError, compile_story, event_game_id, event_issues, new_beat,
    new_event, new_planned_effect, new_relationship, story_issues,
)
from pixelheart_core.story_planning import (
    STORY_STARTERS, StoryPlanningError, apply_story_starter, event_references,
    new_chapter, normalize_storyline, preview_story_starter, related_events,
    relationship_event_ids, relationship_references, storyline_issues,
)
from pixelheart_core.validation import DraftValidationError, validate_draft


class StoryPlanningTests(unittest.TestCase):
    def setUp(self):
        self.character = new_project()["character"]
        self.character.update(id="stable-character", internal_name="Mira")

    def ready_event(self, name="A scene"):
        event = new_event(self.character)
        event["name"] = name
        event["story"].update(stage="ready", beats=[{**new_beat(), "text": "A moment together.$h"}])
        self.character["events"].append(event)
        return event

    def test_normalization_and_serialization_preserve_extensions_and_legacy_ids(self):
        legacy = self.ready_event()
        identity = event_game_id(legacy, self.character)
        chapter = new_chapter("A chapter")
        chapter.update(event_ids=[legacy["id"]], foreign={"keep": [1, 2]})
        effect = new_planned_effect("A future room change")
        effect.update(resolution="omitted", provenance={"keep": True})
        legacy["story"].update(planned_effects=[effect], before="Wary", after="At ease", motif="Window")
        self.character["storyline"] = {"brief": {"desire": "Compose", "foreign": ["keep"]},
                                       "chapters": [chapter], "foreign": {"version": 2}}
        relationship = new_relationship()
        relationship["story"].update(kind="personal", independent_desire="Compose", boundaries="A quiet hour")
        self.character["relationships"] = [relationship]
        original = deepcopy(self.character)
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "character.json"
            save_project({"character": self.character}, file)
            first = load_project(file)
            save_project(first, file)
            second = load_project(file)
        self.assertEqual(self.character, original)
        self.assertEqual(first, second)
        restored = second["character"]
        self.assertEqual(restored["storyline"]["foreign"], {"version": 2})
        self.assertEqual(restored["storyline"]["chapters"][0]["foreign"], {"keep": [1, 2]})
        self.assertEqual(restored["storyline"]["brief"]["foreign"], ["keep"])
        self.assertEqual(restored["events"][0]["story"]["planned_effects"][0], effect)
        self.assertEqual(event_game_id(restored["events"][0], restored), identity)
        self.assertEqual(restored["relationships"][0], relationship)

    def test_legacy_load_does_not_invent_chapters_or_game_conditions(self):
        legacy = self.ready_event()
        legacy["story"]["previous_event_id"] = ""
        self.character.pop("storyline")
        before = compile_story(self.character)
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "legacy.json"
            save_project({"character": self.character}, file)
            restored = load_project(file)["character"]
        self.assertEqual(restored["storyline"], normalize_storyline())
        self.assertEqual(restored["events"], self.character["events"])
        self.assertEqual(compile_story(restored), before)

    def test_preview_is_deterministic_and_selection_has_no_implicit_dependencies(self):
        original = deepcopy(self.character)
        preview = preview_story_starter(self.character)
        self.assertEqual(self.character, original)
        self.assertEqual(preview, preview_story_starter(self.character))
        self.assertEqual(len(preview["chapters"]), 6)
        chosen = preview["chapters"][2]
        chosen.update(name="A differently paced trust", hearts=5, phase="friendship")
        result = apply_story_starter(self.character, preview, [chosen["id"]])
        self.assertEqual(self.character, original)
        self.assertEqual(len(result["events"]), 1)
        event = result["events"][0]
        self.assertEqual((event["name"], event["hearts"]), (chosen["name"], 5))
        self.assertEqual(event["story"]["relationship"], "any")
        self.assertEqual(event["story"]["previous_event_id"], "")
        self.assertEqual(event["story"]["stage"], "outline")
        self.assertEqual(compile_story(result), [])

    def test_starters_share_semantic_milestones_and_never_overwrite(self):
        friendship = preview_story_starter(self.character, "friendship")
        applied = apply_story_starter(self.character, friendship)
        applied["events"][0]["name"] = "An authored title"
        applied["events"][0]["hearts"] = 3
        applied["storyline"]["chapters"][0].update(name="Author's chapter", hearts=3)
        authored = deepcopy(applied)
        self.assertEqual(apply_story_starter(applied, friendship), authored)
        continuation = preview_story_starter(applied, "romance")
        self.assertEqual(continuation["existing_count"], 4)
        self.assertEqual([chapter["phase"] for chapter in continuation["chapters"]], ["dating", "married"])
        continued = apply_story_starter(applied, continuation)
        self.assertEqual(continued["events"][:4], authored["events"])
        self.assertEqual([event["story"]["relationship"] for event in continued["events"][-2:]], ["dating", "married"])
        self.assertEqual(preview_story_starter(continued)["chapters"], [])
        for starter in STORY_STARTERS:
            self.assertEqual(preview_story_starter(continued, starter)["events"], [])

    def test_partial_application_and_missing_chapter_do_not_replace_existing_scene(self):
        preview = preview_story_starter(self.character, "friendship")
        first_id = preview["chapters"][0]["id"]
        result = apply_story_starter(self.character, preview, [first_id])
        result["storyline"]["chapters"] = []
        result["events"][0]["name"] = "Existing scene remains"
        restored_preview = preview_story_starter(result, "friendship")
        self.assertEqual(len(restored_preview["events"]), 3)
        applied = apply_story_starter(result, restored_preview, [first_id])
        self.assertEqual(applied["events"], result["events"])
        self.assertEqual(applied["storyline"]["chapters"][0]["event_ids"], [result["events"][0]["id"]])

    def test_arc_links_do_not_force_cast_membership(self):
        relationship = new_relationship()
        relationship.update(name="An absent friend")
        relationship["story"].update(stage="ready", target="Robin")
        self.character["relationships"] = [relationship]
        event = self.ready_event()
        event["story"]["arc_ids"] = [relationship["id"]]
        self.assertFalse([issue for issue in story_issues(self.character) if issue["level"] == "error"])
        event["story"]["relationship_id"] = relationship["id"]
        self.assertTrue(any("Robin" in issue["message"] for issue in event_issues(event, self.character)))
        relationship["story"].update(kind="personal", target="")
        self.assertFalse([issue for issue in story_issues(self.character) if issue["level"] == "error"])

    def test_multipart_chapters_share_hearts_without_changing_gameplay(self):
        first, second = self.ready_event("First part"), self.ready_event("Second part")
        first["hearts"] = second["hearts"] = 6
        before = compile_story(self.character)
        chapter = new_chapter("Two encounters")
        chapter.update(hearts=6, phase="married", event_ids=[first["id"], second["id"]])
        self.character["storyline"]["chapters"] = [chapter]
        self.character["storyline"]["chapters"].reverse()
        self.assertEqual(compile_story(self.character), before)
        self.assertEqual(first["story"]["relationship"], "any")
        self.assertEqual(second["story"]["previous_event_id"], "")

    def test_references_and_relationship_union_include_chapters_and_life_drafts(self):
        relation = new_relationship()
        relation["name"] = "A personal ambition"
        relation["story"].update(kind="personal", stage="ready", target="")
        self.character["relationships"] = [relation]
        first, second, third = [self.ready_event(name) for name in ("One", "Two", "Three")]
        first["story"]["relationship_id"] = relation["id"]
        second["story"].update(arc_ids=[relation["id"]], previous_event_id=first["id"])
        chapter = new_chapter("Shared chapter")
        chapter.update(arc_ids=[relation["id"]], event_ids=[third["id"], first["id"]])
        self.character["storyline"]["chapters"] = [chapter]
        self.character["life"] = {}
        for kind in ("dialogues", "routines", "spouse_dialogue"):
            rule = new_life_record(kind, self.character)
            rule["name"] = kind
            rule["conditions"]["after_event_id"] = first["id"]
            self.character["life"][kind] = [rule]
        self.assertEqual(relationship_event_ids(self.character, relation["id"]), [first["id"], second["id"], third["id"]])
        self.assertEqual(len(event_references(self.character, first["id"])), 5)
        self.assertEqual(len(relationship_references(self.character, relation["id"])), 3)
        copies = related_events(self.character, relation["id"])
        copies[0]["name"] = "Changed copy"
        self.assertEqual(first["name"], "One")
        self.assertFalse([issue for issue in story_issues(self.character) if issue["level"] == "error"])
        third["story"]["stage"] = "scene"
        self.assertTrue(any(issue["field"] == "relationships.0.story.stage" and issue["level"] == "error"
                            for issue in story_issues(self.character)))

    def test_pending_effect_blocks_only_ready_export_and_omission_is_explicit(self):
        event = self.ready_event()
        baseline = compile_story(self.character)
        event["story"]["planned_effects"] = [new_planned_effect("Send an invitation tomorrow")]
        with self.assertRaises(StoryValidationError):
            compile_story(self.character)
        self.assertTrue(any(issue["field"] == "story.planned_effects.0.resolution" for issue in event_issues(event, self.character)))
        event["story"]["stage"] = "outline"
        self.assertEqual(validate_draft({"events": [event]})["events"], [event])
        self.assertEqual(compile_story(self.character), [])
        event["story"].update(stage="ready")
        event["story"]["planned_effects"][0]["resolution"] = "omitted"
        self.assertEqual(compile_story(self.character), baseline)
        event["story"]["planned_effects"][0].update(resolution="pending", description="")
        self.assertTrue(any("Untitled planned effect" in issue["message"] for issue in event_issues(event, self.character)))

    def test_remembered_preference_cannot_be_silently_exported(self):
        event = new_event(self.character, "remembered_preference")
        event["story"].update(stage="ready", beats=[{**new_beat(), "text": "I remembered."}])
        self.character["events"] = [event]
        self.assertEqual(event["story"]["planned_effects"][0]["resolution"], "pending")
        with self.assertRaises(StoryValidationError):
            compile_story(self.character)

    def test_dangling_refs_have_clear_paths_and_preserve_draft_boundaries(self):
        event = self.ready_event()
        event["story"].update(stage="scene", arc_ids=["missing"])
        issues = story_issues(self.character)
        self.assertTrue(any(issue["field"] == "events.0.story.arc_ids.0" and issue["level"] == "warning" for issue in issues))
        event["story"]["stage"] = "ready"
        self.assertTrue(any(issue["field"] == "events.0.story.arc_ids.0" and issue["level"] == "error" for issue in story_issues(self.character)))
        event["story"]["arc_ids"] = []
        chapter = new_chapter()
        chapter.update(event_ids=["missing"], arc_ids=["gone"])
        self.character["storyline"]["chapters"] = [chapter]
        self.assertEqual({issue["field"] for issue in storyline_issues(self.character)},
                         {"storyline.chapters.0.event_ids.0", "storyline.chapters.0.arc_ids.0"})
        self.assertTrue(compile_story(self.character))

    def test_malformed_planning_and_new_fields_are_rejected_on_save(self):
        cases = [
            {"storyline": None}, {"storyline": {"brief": []}},
            {"storyline": {"brief": {"motif": "bad\ud800"}}},
            {"storyline": {"chapters": [None]}},
            {"storyline": {"chapters": [{"hearts": True}]}},
            {"storyline": {"chapters": [{"phase": []}]}},
            {"storyline": {"chapters": [{"event_ids": [["nested"]]}]}},
            {"storyline": {"chapters": [{"event_ids": ["same", "same"]}]}},
            {"storyline": {"chapters": [{"id": "same"}, {"id": "same"}]}},
            {"storyline": {"chapters": [{}] * 101}},
            {"events": [{"story": {"before": 1}}]},
            {"events": [{"story": {"arc_ids": [None]}}]},
            {"events": [{"story": {"arc_ids": "not-a-list"}}]},
            {"events": [{"story": {"arc_ids": ["same", "same"]}}]},
            {"events": [{"story": {"planned_effects": {}}}]},
            {"events": [{"story": {"planned_effects": [None]}}]},
            {"events": [{"story": {"planned_effects": [{"id": []}]}}]},
            {"events": [{"story": {"planned_effects": [{"description": "bad\ud800"}]}}]},
            {"events": [{"story": {"planned_effects": [{"id": "same"}, {"id": "same"}]}}]},
            {"events": [{"story": {"planned_effects": [{}] * 101}}]},
            {"events": [{"story": {"planned_effects": [{"resolution": "implemented"}]}}]},
            {"relationships": [{"story": {"kind": []}}]},
            {"relationships": [{"story": {"married": "x" * 8001}}]},
        ]
        for fields in cases:
            with self.subTest(fields=repr(fields)[:100]):
                with self.assertRaises(DraftValidationError):
                    validate_draft(fields)
                with tempfile.TemporaryDirectory() as directory:
                    with self.assertRaises(ProjectError):
                        save_project({"character": {**self.character, **fields}}, Path(directory) / "bad.json")

    def test_starter_input_bounds_and_selection_are_checked_without_mutation(self):
        preview = preview_story_starter(self.character)
        for broken in (None, {}, {"chapters": None, "events": []}, {"chapters": [], "events": [None]}):
            with self.assertRaises(StoryPlanningError):
                apply_story_starter(self.character, broken)
        with self.assertRaises(StoryPlanningError):
            apply_story_starter(self.character, preview, ["not-in-preview"])
        for starter in (None, [], "missing"):
            with self.assertRaises(StoryPlanningError):
                preview_story_starter(self.character, starter)
        with self.assertRaises(StoryPlanningError):
            preview_story_starter({**self.character, "home_x": "bad"})
        with self.assertRaises(StoryPlanningError):
            preview_story_starter(self.character, relationship_id="missing")
        self.character["events"] = [new_event(self.character) for _ in range(99)]
        original = deepcopy(self.character)
        with self.assertRaises(StoryPlanningError):
            apply_story_starter(self.character, preview)
        self.assertEqual(self.character, original)
        one = apply_story_starter(self.character, preview, [preview["chapters"][0]["id"]])
        self.assertEqual(len(one["events"]), 100)

    def test_starter_never_trusts_preview_readiness(self):
        preview = preview_story_starter(self.character, "dating")
        preview["events"][0]["story"].update(stage="ready", beats=[{**new_beat(), "text": "Hello."}])
        result = apply_story_starter(self.character, preview)
        self.assertEqual(result["events"][0]["story"]["stage"], "outline")
        self.assertEqual(compile_story(result), [])

    def test_nonromance_planning_never_enables_romance_or_exports_inaccessible_scenes(self):
        self.character["romanceable"] = False
        friendship = apply_story_starter(self.character, preview_story_starter(self.character, "friendship"))
        self.assertFalse(friendship["romanceable"])
        self.assertFalse(storyline_issues(friendship))
        self.assertTrue(all(event["story"]["relationship"] == "any" for event in friendship["events"]))
        extended = apply_story_starter(friendship, preview_story_starter(friendship, "romance"))
        self.assertFalse(extended["romanceable"])
        self.assertEqual(compile_story(extended), [])
        self.assertTrue(any(issue["level"] == "warning" for issue in storyline_issues(extended)))
        for event in extended["events"][-2:]:
            event["story"].update(stage="ready", beats=[{**new_beat(), "text": "A future idea."}])
            self.assertTrue(any(issue["field"] == "story.relationship" and issue["level"] == "error"
                                for issue in event_issues(event, extended)))
        with self.assertRaises(StoryValidationError):
            compile_story(extended)

    def test_reference_helpers_tolerate_incomplete_imports(self):
        for character in (None, {}, {"events": None, "storyline": []},
                          {"events": [None, {"story": []}], "life": {"dialogues": [None]}},
                          {"storyline": {"chapters": [{"arc_ids": ["arc"], "event_ids": None}]}}):
            self.assertEqual(event_references(character, "event"), [])
            self.assertEqual(relationship_event_ids(character, "arc"), [])
            self.assertEqual(related_events(character, "arc"), [])
        self.assertEqual(event_references(self.character, ""), [])
        self.assertEqual(relationship_references(self.character, ""), [])


if __name__ == "__main__":
    unittest.main()

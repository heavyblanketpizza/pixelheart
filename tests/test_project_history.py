"""Whole-character session history, save baselines, and snapshot isolation."""

from copy import deepcopy
import unittest

from pixelheart_core.project_history import ProjectHistory


def project(name="Mira", room_name="Kitchen"):
    return {
        "character": {"name": name, "dialogues": [{"text": "Hello!"}]},
        "world": {"rooms": [{"name": room_name}]},
        "extension": {"tags": ["private"], "enabled": True, "count": 2, "empty": None},
    }


class ProjectHistoryTests(unittest.TestCase):
    def test_edits_across_character_and_home_undo_in_chronological_order(self):
        original = project()
        renamed = project(name="River")
        furnished = project(name="River", room_name="Studio")
        history = ProjectHistory(original)
        self.assertFalse(history.is_dirty)
        self.assertFalse(history.can_undo)
        self.assertFalse(history.can_redo)

        self.assertTrue(history.record(renamed))
        self.assertTrue(history.record(furnished))
        self.assertEqual(history.undo_count, 2)
        self.assertTrue(history.is_dirty)
        self.assertEqual(history.undo(), renamed)
        self.assertEqual(history.undo(), original)
        self.assertFalse(history.is_dirty)
        self.assertIsNone(history.undo())
        self.assertEqual(history.redo_count, 2)
        self.assertEqual(history.redo(), renamed)
        self.assertEqual(history.redo(), furnished)
        self.assertIsNone(history.redo())

    def test_input_current_and_restored_documents_cannot_mutate_snapshots(self):
        original = project()
        saved_original = deepcopy(original)
        history = ProjectHistory(original)
        original["extension"]["tags"].append("caller mutation")
        current = history.current_document
        current["character"]["dialogues"][0]["text"] = "outside mutation"
        self.assertEqual(history.current_document, saved_original)
        self.assertFalse(history.is_dirty)

        changed = project(name="River")
        saved_changed = deepcopy(changed)
        history.record(changed)
        changed["world"]["rooms"][0]["name"] = "outside mutation"
        restored = history.undo()
        self.assertEqual(restored, saved_original)
        restored["extension"]["tags"].clear()
        self.assertEqual(history.current_document, saved_original)
        self.assertEqual(history.redo(), saved_changed)
        self.assertEqual(history.undo(), saved_original)

    def test_unchanged_capture_preserves_redo_and_does_not_add_steps(self):
        history = ProjectHistory(project())
        self.assertFalse(history.record(project()))
        history.record(project(name="River"))
        history.undo()
        self.assertFalse(history.record(project()))
        self.assertEqual(history.undo_count, 0)
        self.assertEqual(history.redo_count, 1)
        self.assertEqual(history.redo(), project(name="River"))

    def test_new_edit_after_undo_discards_only_the_future_branch(self):
        history = ProjectHistory(project())
        history.record(project(name="River"))
        history.record(project(name="River", room_name="Studio"))
        history.undo()
        history.record(project(name="Juniper"))
        self.assertFalse(history.can_redo)
        self.assertEqual(history.undo(), project(name="River"))
        self.assertEqual(history.undo(), project())
        self.assertEqual(history.redo(), project(name="River"))
        self.assertEqual(history.redo(), project(name="Juniper"))

    def test_consecutive_typing_with_same_key_is_one_complete_step(self):
        history = ProjectHistory(project())
        for name in ("R", "Ri", "River"):
            history.record(project(name=name), merge_key=("character", "name"))
        self.assertEqual(history.undo_count, 1)
        self.assertEqual(history.undo(), project())
        self.assertEqual(history.redo(), project(name="River"))

    def test_navigation_and_different_fields_end_a_typing_group(self):
        history = ProjectHistory(project())
        history.record(project(name="River"), merge_key="name")
        history.close_group()
        history.record(project(name="Juniper"), merge_key="name")
        history.record(project(name="Juniper", room_name="Studio"), merge_key="room")
        self.assertEqual(history.undo_count, 3)
        self.assertEqual(history.undo(), project(name="Juniper"))
        self.assertEqual(history.undo(), project(name="River"))
        self.assertEqual(history.undo(), project())

    def test_undo_and_redo_end_a_typing_group_before_next_edit(self):
        history = ProjectHistory(project())
        history.record(project(name="River"), merge_key="name")
        history.undo()
        history.redo()
        history.record(project(name="Juniper"), merge_key="name")
        self.assertEqual(history.undo_count, 2)
        self.assertEqual(history.undo(), project(name="River"))
        history.record(project(name="Willow"), merge_key="name")
        self.assertEqual(history.undo_count, 2)
        self.assertFalse(history.can_redo)
        self.assertEqual(history.undo(), project(name="River"))

    def test_unchanged_capture_does_not_break_a_typing_group(self):
        history = ProjectHistory(project())
        history.record(project(name="R"), merge_key="name")
        self.assertFalse(history.record(project(name="R")))
        history.record(project(name="River"), merge_key="name")
        self.assertEqual(history.undo_count, 1)
        self.assertEqual(history.undo(), project())

    def test_reverting_an_entire_typing_group_leaves_no_empty_undo_step(self):
        history = ProjectHistory(project())
        history.record(project(room_name="Studio"))
        history.record(project(name="River", room_name="Studio"), merge_key="name")
        history.record(project(room_name="Studio"), merge_key="name")
        self.assertEqual(history.undo_count, 1)
        history.record(project(name="Juniper", room_name="Studio"), merge_key="name")
        self.assertEqual(history.undo_count, 2)
        self.assertEqual(history.undo(), project(room_name="Studio"))
        self.assertEqual(history.undo(), project())

    def test_reverting_to_saved_document_is_clean_without_a_save(self):
        history = ProjectHistory(project())
        history.record(project(name="River"), merge_key="name")
        history.record(project(), merge_key="name")
        self.assertFalse(history.can_undo)
        self.assertFalse(history.is_dirty)
        history.record(project(name="River"))
        history.record(project())
        self.assertFalse(history.is_dirty)
        self.assertEqual(history.undo_count, 2)
        self.assertEqual(history.undo(), project(name="River"))
        self.assertTrue(history.is_dirty)

    def test_unkeyed_edits_do_not_merge_and_empty_string_is_a_valid_key(self):
        history = ProjectHistory(project())
        history.record(project(name="River"))
        history.record(project(name="Willow"))
        self.assertEqual(history.undo_count, 2)
        history.record(project(name="J"), merge_key="")
        history.record(project(name="Juniper"), merge_key="")
        self.assertEqual(history.undo_count, 3)
        self.assertEqual(history.undo(), project(name="Willow"))

    def test_successful_save_clears_undo_and_redo_and_starts_a_new_baseline(self):
        history = ProjectHistory(project())
        history.record(project(name="River"), merge_key="name")
        history.record(project(name="River", room_name="Studio"))
        history.undo()
        self.assertTrue(history.can_undo)
        self.assertTrue(history.can_redo)
        history.mark_saved()
        self.assertEqual(history.current_document, project(name="River"))
        self.assertFalse(history.is_dirty)
        self.assertIsNone(history.undo())
        self.assertIsNone(history.redo())
        history.record(project(name="Juniper"), merge_key="name")
        self.assertEqual(history.undo(), project(name="River"))
        self.assertFalse(history.is_dirty)

    def test_saved_document_can_include_persistence_metadata_without_an_undo_step(self):
        history = ProjectHistory(project())
        history.record(project(name="River"))
        saved = project(name="River")
        saved["character"]["updated_at"] = "saved timestamp"
        expected = deepcopy(saved)
        history.mark_saved(saved)
        saved["extension"]["tags"].clear()
        self.assertEqual(history.current_document, expected)
        self.assertFalse(history.can_undo)
        self.assertFalse(history.is_dirty)
        history.mark_saved()
        self.assertEqual(history.current_document, expected)
        self.assertFalse(history.can_redo)

    def test_failed_or_cancelled_save_can_end_group_without_losing_history(self):
        history = ProjectHistory(project())
        history.record(project(name="River"), merge_key="name")
        history.record(project(name="River", room_name="Studio"))
        history.undo()
        history.close_group()  # Persistence failed; mark_saved must not be called.
        self.assertEqual(history.undo_count, 1)
        self.assertEqual(history.redo_count, 1)
        self.assertTrue(history.is_dirty)
        self.assertEqual(history.redo(), project(name="River", room_name="Studio"))
        self.assertEqual(history.undo(), project(name="River"))
        self.assertEqual(history.undo(), project())

    def test_project_switch_discards_history_and_copies_the_new_baseline(self):
        history = ProjectHistory(project())
        history.record(project(name="River"))
        history.undo()
        another = project(name="Juniper", room_name="Study")
        expected = deepcopy(another)
        history.reset(another)
        another["extension"]["tags"].clear()
        self.assertEqual(history.current_document, expected)
        self.assertFalse(history.is_dirty)
        self.assertIsNone(history.undo())
        self.assertIsNone(history.redo())

    def test_default_capacity_preserves_latest_fifty_steps_and_the_saved_baseline(self):
        history = ProjectHistory({"revision": 0})
        for revision in range(1, 61):
            history.record({"revision": revision})
        self.assertEqual(history.undo_count, 50)
        for revision in range(59, 9, -1):
            self.assertEqual(history.undo(), {"revision": revision})
        self.assertIsNone(history.undo())
        self.assertTrue(history.is_dirty)
        self.assertEqual(history.redo_count, 50)
        for revision in range(11, 61):
            self.assertEqual(history.redo(), {"revision": revision})
        self.assertEqual(history.undo_count, 50)
        history.record({"revision": 0})
        self.assertFalse(history.is_dirty)

    def test_merging_at_capacity_keeps_the_groups_starting_snapshot(self):
        history = ProjectHistory({"revision": 0}, max_entries=2)
        history.record({"revision": 1})
        history.record({"revision": 2})
        history.record({"revision": 3}, merge_key="edit")
        history.record({"revision": 4}, merge_key="edit")
        self.assertEqual(history.undo_count, 2)
        self.assertEqual(history.undo(), {"revision": 2})
        self.assertEqual(history.undo(), {"revision": 1})
        self.assertIsNone(history.undo())

    def test_invalid_capacity_is_rejected(self):
        for max_entries in (0, -1, 1.5, "50", True, None):
            with self.subTest(max_entries=max_entries):
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    ProjectHistory(project(), max_entries=max_entries)


if __name__ == "__main__":
    unittest.main()

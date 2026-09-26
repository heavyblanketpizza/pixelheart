"""Safety checks for importing a small selection of teaching examples."""

import copy
import unittest
import uuid
from unittest.mock import patch

from pixelheart_core.dialogue_templates import (
    MAX_DIALOGUES,
    apply_dialogue_examples,
    dialogue_conflicts,
    dialogue_has_advanced_commands,
    dialogue_preview,
    overwrite_dialogue_examples,
)
from pixelheart_core.validation import validate_nested


class DialogueTemplateTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            {"id": "intro", "trigger": " Introduction ", "text": "My own introduction.",
             "notes": {"drafts": ["Preserve this note."]}},
            {"id": "monday", "trigger": "Mon", "text": "My Monday line."},
        ]
        self.examples = [
            {"trigger": "Introduction", "text": "Hello, @.$h", "explanation": "First meeting."},
            {"trigger": " spring_Mon ", "text": "Spring is here.#$b#Let's go outside."},
        ]

    def test_preview_expands_supported_commands_for_simple_dialogue(self):
        self.assertEqual(
            dialogue_preview("Hello, @.$h#$b#How are you?$s#$e#See you.$12"),
            "Hello, Farmer.\nHow are you?\n\nSee you.",
        )
        self.assertEqual(dialogue_preview("$h$s$l$a$n$u$0$123"), "")
        self.assertEqual(dialogue_preview(""), "")
        self.assertFalse(dialogue_has_advanced_commands("Hello, @.$h#$b#Goodbye.$12"))

    def test_mail_conditional_is_not_mistaken_for_numeric_portrait(self):
        script = "#$1 welcomeFlag#Hello, @.$h$k#$e#Good to see you again.$1"
        self.assertTrue(dialogue_has_advanced_commands(script))
        self.assertEqual(dialogue_preview(script), script)
        for text, expected in (
            ("Hello.$1", "Hello."),
            ("Hello.$1#$b#Goodbye.$9", "Hello.\nGoodbye."),
            ("Hello.$12 ", "Hello. "),
        ):
            with self.subTest(text=text):
                self.assertFalse(dialogue_has_advanced_commands(text))
                self.assertEqual(dialogue_preview(text), expected)

    def test_choice_scripts_and_conditional_branches_are_preserved_exactly(self):
        scripts = [
            "Hello, @.$h#$q 42/43 Pick an answer?#$r 42 10 reply_yes#Yes.#$r 43 0 reply_no#No.",
            "$p 42#You said yes, @.$h|You said no, @.$s",
            "$d eventFlag#Welcome back.$h|Nice to meet you.$s",
            "Hello, sir.$h^Hello, ma'am.$h",
            "Goodbye, @.$s%fork",
            "Hello, @.$h#$b#Take this.[74]",
            "Hello, @.$h#$e#You live at %farm.",
            "Hello, @.$h#$b#Unknown command: $happy $foo",
            "Hello, @.$h#$b#A token: {{PlayerName}}",
        ]
        for script in scripts:
            with self.subTest(script=script):
                self.assertTrue(dialogue_has_advanced_commands(script))
                self.assertEqual(dialogue_preview(script), script)

    def test_conflicts_match_trimmed_triggers_and_return_all_matching_rows(self):
        duplicate = {"id": "duplicate", "trigger": "Introduction", "text": "Another draft."}
        records = self.records + [duplicate]
        conflicts = dialogue_conflicts(records, self.examples)
        self.assertEqual(list(conflicts), ["Introduction"])
        self.assertEqual(conflicts["Introduction"], [self.records[0], duplicate])
        conflicts["Introduction"][0]["notes"]["drafts"].clear()
        self.assertEqual(records[0]["notes"]["drafts"], ["Preserve this note."])

    def test_default_import_preserves_existing_writing_and_appends_only_project_fields(self):
        before_records, before_examples = copy.deepcopy(self.records), copy.deepcopy(self.examples)
        imported = apply_dialogue_examples(self.records, self.examples)
        self.assertEqual(imported[:2], self.records)
        self.assertEqual(set(imported[2]), {"id", "trigger", "text"})
        self.assertEqual(imported[2]["trigger"], "spring_Mon")
        self.assertEqual(imported[2]["text"], self.examples[1]["text"])
        self.assertEqual(str(uuid.UUID(imported[2]["id"])), imported[2]["id"])
        self.assertNotIn(imported[2]["id"], {row["id"] for row in self.records})
        self.assertEqual(self.records, before_records)
        self.assertEqual(self.examples, before_examples)
        validate_nested("dialogues", [imported[2]])

    def test_replace_changes_only_selected_text_and_preserves_identity_and_metadata(self):
        imported = apply_dialogue_examples(self.records, self.examples, {"Introduction": "replace"})
        expected_intro = copy.deepcopy(self.records[0])
        expected_intro["text"] = self.examples[0]["text"]
        self.assertEqual(imported[0], expected_intro)
        self.assertEqual(imported[1], self.records[1])
        imported[0]["notes"]["drafts"].append("An imported-copy note.")
        self.assertEqual(self.records[0]["notes"]["drafts"], ["Preserve this note."])

    def test_all_kept_or_empty_selection_is_detached_noop(self):
        for examples, decisions in (([], None), (self.examples[:1], None),
                                    (self.examples[:1], {"Introduction": "keep"})):
            with self.subTest(examples=examples, decisions=decisions):
                imported = apply_dialogue_examples(self.records, examples, decisions)
                self.assertEqual(imported, self.records)
                self.assertIsNot(imported, self.records)
                self.assertIsNot(imported[0]["notes"], self.records[0]["notes"])

    def test_duplicate_existing_trigger_can_be_kept_but_never_replaced_ambiguously(self):
        records = self.records + [{"id": "duplicate", "trigger": "Introduction", "text": "Other draft."}]
        original = copy.deepcopy(records)
        self.assertEqual(apply_dialogue_examples(records, self.examples[:1]), records)
        with self.assertRaisesRegex(ValueError, "matches multiple existing entries"):
            apply_dialogue_examples(records, self.examples, {"Introduction": "replace"})
        self.assertEqual(records, original)

    def test_import_at_capacity_is_atomic_but_replacements_and_kept_rows_are_allowed(self):
        records = [{"id": str(i), "trigger": f"Mon{i}", "text": "Original."} for i in range(MAX_DIALOGUES)]
        original = copy.deepcopy(records)
        examples = [{"trigger": "Mon0", "text": "Replacement."}, {"trigger": "Tue", "text": "New."}]
        with self.assertRaisesRegex(ValueError, "up to 2000"):
            apply_dialogue_examples(records, examples, {"Mon0": "replace"})
        self.assertEqual(records, original)
        replaced = apply_dialogue_examples(records, examples[:1], {"Mon0": "replace"})
        self.assertEqual(len(replaced), MAX_DIALOGUES)
        self.assertEqual(replaced[0], {"id": "0", "trigger": "Mon0", "text": "Replacement."})
        self.assertEqual(apply_dialogue_examples(records, examples[:1]), records)
        self.assertEqual(len(apply_dialogue_examples(records[:-1], examples[1:])), MAX_DIALOGUES)

    def test_invalid_examples_cannot_partially_import_or_replace(self):
        invalid_examples = [
            None, {}, {"trigger": None, "text": "Hello"}, {"trigger": "", "text": "Hello"},
            {"trigger": "Bad trigger", "text": "Hello"}, {"trigger": "A" * 121, "text": "Hello"},
            {"trigger": "Tue", "text": ""}, {"trigger": "Tue", "text": "  \n"},
            {"trigger": "Tue", "text": 3}, {"trigger": "Tue", "text": "x" * 8001},
            {"trigger": "Tue", "text": "Contains\x00null"},
            {"trigger": "Tue", "text": "Hello, {{PlayerName}}"},
            {"trigger": " Introduction ", "text": "Duplicate selected trigger."},
        ]
        original = copy.deepcopy(self.records)
        for invalid in invalid_examples:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                apply_dialogue_examples(self.records, [self.examples[0], invalid], {"Introduction": "replace"})
            self.assertEqual(self.records, original)

    def test_maximum_schema_lengths_are_valid_and_unknown_decisions_fail_closed(self):
        example = {"trigger": "A" * 120, "text": "x" * 8000, "id": "not-the-import-id"}
        imported = apply_dialogue_examples([], [example])
        self.assertNotEqual(imported[0]["id"], example["id"])
        validate_nested("dialogues", imported)
        with self.assertRaisesRegex(ValueError, '"keep" or "replace"'):
            apply_dialogue_examples(self.records, self.examples, {"Introduction": "overwrite"})

    def test_separate_imports_get_distinct_ids_and_do_not_share_records(self):
        first = apply_dialogue_examples([], self.examples)
        second = apply_dialogue_examples([], self.examples)
        self.assertTrue({row["id"] for row in first}.isdisjoint({row["id"] for row in second}))
        first[0]["text"] = "Changed in first project."
        self.assertEqual(second[0]["text"], self.examples[0]["text"])

    def test_local_source_follows_imported_text_and_not_kept_or_replaced_old_text(self):
        old = {"provider": "local-content-patcher", "asset": "Characters/Dialogue/Elliott", "sha256": "e" * 64}
        source = {"provider": "local-content-patcher", "asset": "Characters/Dialogue/Abigail", "sha256": "a" * 64}
        self.records[0]["source"] = old
        self.examples[0]["source"] = source
        imported = apply_dialogue_examples(self.records, self.examples, {"Introduction": "replace"})
        self.assertEqual(imported[0]["source"], source)
        self.assertNotIn("explanation", imported[0])
        self.assertEqual(apply_dialogue_examples(self.records, self.examples)[0]["source"], old)
        imported[0]["source"]["sha256"] = "b" * 64
        self.assertEqual(self.examples[0]["source"], source)
        self.examples[0].pop("source")
        replaced = apply_dialogue_examples(self.records, self.examples, {"Introduction": "replace"})
        self.assertNotIn("source", replaced[0])

    def test_full_local_file_over_250_entries_keeps_every_entry_and_source(self):
        source = {"provider": "local-content-patcher", "asset": "Characters/Dialogue/Abigail", "sha256": "a" * 64}
        examples = [{"trigger": f"custom_{index}", "text": f"Entire line {index}", "source": source}
                    for index in range(350)]
        imported = apply_dialogue_examples([], examples)
        self.assertEqual(len(imported), 350)
        self.assertEqual([row["text"] for row in imported], [row["text"] for row in examples])
        self.assertEqual(validate_nested("dialogues", imported), imported)
        self.assertIsNot(imported[0]["source"], imported[1]["source"])

    def test_import_rejects_private_paths_in_source_metadata_atomically(self):
        for source in ({"path": "/Users/private/game/Characters_Dialogue_Abigail.json"},
                       {"source_name": "/Users/private/game"},
                       {"asset": "C:/private/game.json"},
                       {"source_url": "file:///Users/private/game"}):
            with self.subTest(source=source), self.assertRaises(ValueError):
                apply_dialogue_examples(self.records, [{**self.examples[0], "source": source}], {"Introduction": "replace"})
            self.assertEqual(self.records[0]["text"], "My own introduction.")

    def test_overwrite_keeps_other_dialogue_and_updates_all_matching_rows_in_place(self):
        records = self.records + [
            {"id": "duplicate", "trigger": "Introduction", "text": "Another draft."},
        ]
        examples = list(reversed(self.examples))
        original_records, original_examples = copy.deepcopy(records), copy.deepcopy(examples)
        replaced = overwrite_dialogue_examples(records, examples)
        self.assertEqual([row["trigger"] for row in replaced],
                         [" Introduction ", "Mon", "Introduction", "spring_Mon"])
        self.assertEqual([row["text"] for row in replaced],
                         [self.examples[0]["text"], self.records[1]["text"],
                          self.examples[0]["text"], self.examples[1]["text"]])
        self.assertEqual([row["id"] for row in replaced[:3]], ["intro", "monday", "duplicate"])
        self.assertEqual(replaced[1], records[1])
        self.assertEqual(replaced[0]["notes"], self.records[0]["notes"])
        self.assertNotIn("explanation", replaced[0])
        replaced[0]["notes"]["drafts"].clear()
        self.assertEqual(records, original_records)
        self.assertEqual(examples, original_examples)

    def test_overwrite_refreshes_only_matching_provenance_and_detaches_sources(self):
        old = {"provider": "local-content-patcher", "asset": "Characters/Dialogue/Elliott", "sha256": "e" * 64}
        source = {"provider": "local-content-patcher", "asset": "Characters/Dialogue/Abigail", "sha256": "a" * 64}
        self.records[0].update(source=old, source_history=[old])
        self.records[1].update(source=old, source_history=[old])
        examples = [{**self.examples[0], "source": source}, {**self.examples[1], "source": source}]
        original = copy.deepcopy(self.records)
        replaced = overwrite_dialogue_examples(self.records, examples)
        self.assertEqual(replaced[0]["source"], source)
        self.assertNotIn("source_history", replaced[0])
        replaced[0]["source"]["sha256"] = "b" * 64
        self.assertEqual(replaced[2]["source"], source)
        self.assertEqual(replaced[1], self.records[1])
        replaced[1]["source_history"][0]["sha256"] = "c" * 64
        self.assertEqual(examples[0]["source"]["sha256"], "a" * 64)
        without_source = overwrite_dialogue_examples(self.records, self.examples)
        self.assertNotIn("source", without_source[0])
        self.assertNotIn("source_history", without_source[0])
        self.assertEqual(self.records, original)

    def test_overwrite_appends_new_rows_in_order_with_unique_ids(self):
        examples = [{"trigger": "Tue", "text": "Tuesday."}, {"trigger": "Wed", "text": "Wednesday."}]
        with patch("pixelheart_core.dialogue_templates.uuid.uuid4",
                   side_effect=["intro", "new-first", "new-first", "new-second"]):
            replaced = overwrite_dialogue_examples(self.records, examples)
        self.assertEqual(replaced[:2], self.records)
        self.assertEqual([row["id"] for row in replaced[2:]], ["new-first", "new-second"])
        self.assertEqual([row["trigger"] for row in replaced[2:]], ["Tue", "Wed"])
        self.assertTrue(all(set(row) == {"id", "trigger", "text"} for row in replaced[2:]))

    def test_overwrite_validates_entire_template_before_changing_records(self):
        original = copy.deepcopy(self.records)
        for examples in (
            [], None, {},
            [self.examples[0], {"trigger": "Tue", "text": ""}],
            [self.examples[0], {"trigger": " Introduction ", "text": "Duplicate."}],
            [self.examples[0], {"trigger": "Tue", "text": "{{PlayerName}}"}],
            [self.examples[0], {"trigger": "Tue", "text": "Hi.", "source": {"path": "/private/game"}}],
        ):
            with self.subTest(examples=examples), self.assertRaises(ValueError):
                overwrite_dialogue_examples(self.records, examples)
            self.assertEqual(self.records, original)
        for records in (None, {}, [None]):
            with self.subTest(records=records), self.assertRaises(ValueError):
                overwrite_dialogue_examples(records, self.examples)

    def test_overwrite_capacity_counts_retained_rows_and_additions(self):
        records = [{"id": str(i), "trigger": f"Mon{i}", "text": "Original."} for i in range(MAX_DIALOGUES)]
        original = copy.deepcopy(records)
        examples = [{"trigger": "Mon0", "text": "Imported."}]
        replaced = overwrite_dialogue_examples(records, examples)
        self.assertEqual(len(replaced), MAX_DIALOGUES)
        self.assertEqual(replaced[0]["text"], "Imported.")
        self.assertEqual(replaced[1:], records[1:])
        self.assertEqual(len(overwrite_dialogue_examples(records[:-1], self.examples[:1])), MAX_DIALOGUES)
        with self.assertRaisesRegex(ValueError, "up to 2000"):
            overwrite_dialogue_examples(records, examples + [{"trigger": "Wed", "text": "Too many."}])
        self.assertEqual(records, original)


if __name__ == "__main__":
    unittest.main()

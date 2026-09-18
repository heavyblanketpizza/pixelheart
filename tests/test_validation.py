"""Draft validation regressions without an HTTP server or database."""
import copy
import unittest
import uuid

from pixelheart_core.validation import (
    DraftValidationError,
    validate_draft,
    validate_nested,
)


class DraftValidationTests(unittest.TestCase):
    def assert_invalid(self, payload, field):
        with self.assertRaises(DraftValidationError) as raised:
            validate_draft(payload)
        self.assertIn(field, raised.exception.errors)

    def test_rejects_non_object_bodies(self):
        for value in ([], None, "hello", 42, True):
            with self.subTest(value=value):
                self.assert_invalid(value, "body")

    def test_malformed_fields_and_nested_input_are_rejected_without_mutation(self):
        cases = [
            ({"name": "Changed", "day": True}, "day"),
            ({"season": []}, "season"),
            ({"romanceable": "yes"}, "romanceable"),
            ({"internal_name": "../oops"}, "internal_name"),
            ({"portrait": "outside.png"}, "portrait"),
            ({"dialogues": {"text": "wrong"}}, "dialogues"),
            ({"dialogues": [None]}, "dialogues.0"),
            ({"dialogues": [{"trigger": "Mon", "text": ["wrong"]}]}, "dialogues.0.text"),
            ({"schedule": [{"time": "06:00", "x": "12"}]}, "schedule.0.x"),
            ({"schedule": [{"time": "99:99"}]}, "schedule.0.time"),
            ({"gifts": {"love": [None]}}, "gifts.love"),
            ({"events": [{"hearts": -1}]}, "events.0.hearts"),
            ({"relationships": [{"name": 3}]}, "relationships.0.name"),
            ({"dialogues": [{"id": "same"}, {"id": "same"}]}, "dialogues.1.id"),
        ]
        for payload, field in cases:
            with self.subTest(payload=payload):
                original = copy.deepcopy(payload)
                self.assert_invalid(payload, field)
                self.assertEqual(payload, original)

    def test_server_fields_are_excluded_from_editable_result(self):
        self.assertEqual(validate_draft({
            "name": "Ada", "id": "other", "status": "ready",
            "created_at": "old", "updated_at": "old",
            "portrait_url": "/bad.png", "sprite_url": "/bad.png",
        }), {"name": "Ada"})

    def test_partial_draft_preserves_authored_content(self):
        payload = {
            "name": "  Ada  ", "bio": "  An astronomer.\n",
            "romanceable": False, "day": 28,
            "dialogues": [{"id": "hello", "trigger": "Introduction", "text": "Hello, farmer!"}],
        }
        original = copy.deepcopy(payload)
        cleaned = validate_draft(payload)
        self.assertEqual(cleaned, {**payload, "name": "Ada"})
        self.assertEqual(payload, original)
        self.assertEqual(validate_draft({}), {})

    def test_adult_age_is_accepted_with_either_romance_setting(self):
        for romanceable in (True, False):
            with self.subTest(romanceable=romanceable):
                payload = {"age": "adult", "romanceable": romanceable}
                self.assertEqual(validate_draft(payload), payload)
        self.assertEqual(validate_draft({"age": "adult"}), {"age": "adult"})

    def test_non_adult_ages_are_rejected_regardless_of_romance_setting(self):
        for age in ("teen", "child"):
            for romance_fields in ({}, {"romanceable": True}, {"romanceable": False}):
                with self.subTest(age=age, romance_fields=romance_fields):
                    with self.assertRaises(DraftValidationError) as raised:
                        validate_draft({"age": age, **romance_fields})
                    self.assertEqual(raised.exception.errors, {
                        "age": "Choose adult. Pixelheart creates adult love-interest NPCs only.",
                    })

    def test_invalid_ages_are_rejected(self):
        for age in ("", "unknown", "Adult", None, 18, True, [], {}):
            with self.subTest(age=age):
                self.assert_invalid({"age": age}, "age")

    def test_partial_updates_do_not_require_or_insert_age(self):
        for payload in ({}, {"name": "Ada"}, {"romanceable": True}, {"romanceable": False}):
            with self.subTest(payload=payload):
                cleaned = validate_draft(payload)
                self.assertEqual(cleaned, payload)
                self.assertNotIn("age", cleaned)

    def test_rejects_empty_required_fields_nulls_and_excessive_text(self):
        for payload, field in [
            ({"name": "  "}, "name"),
            ({"internal_name": ""}, "internal_name"),
            ({"home_map": "  "}, "home_map"),
            ({"home_map": "../Town"}, "home_map"),
            ({"bio": "x\x00y"}, "bio"),
            ({"name": "x" * 65}, "name"),
            ({"bio": "x" * 12001}, "bio"),
            ({"dialogues": [{"text": "x\x00y"}]}, "dialogues.0.text"),
        ]:
            with self.subTest(payload=payload):
                self.assert_invalid(payload, field)

    def test_integer_limits_reject_booleans_and_out_of_range_values(self):
        for field, lower, upper in (("day", 1, 28), ("home_x", 0, 1000), ("home_y", 0, 1000)):
            for value in (lower, upper):
                with self.subTest(field=field, value=value):
                    self.assertEqual(validate_draft({field: value}), {field: value})
            for value in (True, False, str(lower), float(lower), lower - 1, upper + 1):
                with self.subTest(field=field, value=value):
                    self.assert_invalid({field: value}, field)

    def test_reports_errors_for_multiple_fields_together(self):
        with self.assertRaises(DraftValidationError) as raised:
            validate_draft({"day": 29, "romanceable": "yes", "gifts": {"love": [None]}})
        self.assertEqual(set(raised.exception.errors), {"day", "romanceable", "gifts.love"})


class NestedValidationTests(unittest.TestCase):
    def test_gifts_are_trimmed_deduplicated_and_missing_categories_added(self):
        gifts = {"love": ["  Amethyst ", "Amethyst", "Coffee"], "hate": ["Clay"]}
        original = copy.deepcopy(gifts)
        self.assertEqual(validate_nested("gifts", gifts), {
            "love": ["Amethyst", "Coffee"], "like": [], "dislike": [], "hate": ["Clay"],
        })
        self.assertEqual(gifts, original)

    def test_schedule_defaults_and_numeric_time_and_facing(self):
        schedule = validate_nested("schedule", [{"id": "morning", "time": 600, "facing": 2}])
        self.assertEqual(schedule, [{
            "id": "morning", "time": "600", "location": "Town", "x": 32, "y": 62,
            "facing": "2", "activity": "",
        }])
        self.assertEqual(validate_nested("schedule", [{"time": "06:00"}])[0]["time"], "06:00")

    def test_missing_entry_ids_are_generated_uniquely(self):
        entries = validate_nested("dialogues", [{}, {}])
        self.assertNotEqual(entries[0]["id"], entries[1]["id"])
        for entry in entries:
            self.assertEqual(str(uuid.UUID(entry["id"])), entry["id"])
            self.assertEqual(entry["trigger"], "Introduction")
            self.assertEqual(entry["text"], "")

    def test_rejects_unknown_nested_fields_and_invalid_gifts(self):
        cases = [
            ("dialogues", [{"script": "unexpected"}]),
            ("gifts", {"neutral": []}),
            ("gifts", {"love": "Coffee"}),
            ("gifts", {"love": [" "]}),
            ("gifts", {"love": ["x" * 121]}),
            ("gifts", {"love": ["x\x00y"]}),
            ("schedule", [{"facing": True}]),
            ("events", [{"hearts": True}]),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                with self.assertRaises(DraftValidationError):
                    validate_nested(key, value)

    def test_rejects_excessive_nested_entries_and_invalid_ids(self):
        for key, limit in (("dialogues", 250), ("schedule", 100), ("events", 100), ("relationships", 100)):
            with self.subTest(key=key):
                with self.assertRaises(DraftValidationError) as raised:
                    validate_nested(key, [{} for _ in range(limit + 1)])
                self.assertIn(key, raised.exception.errors)
        with self.assertRaises(DraftValidationError):
            validate_nested("gifts", {"love": ["Coffee"] * 5001})
        for entry_id in (None, "", "x" * 101, 3):
            with self.subTest(entry_id=entry_id):
                with self.assertRaises(DraftValidationError) as raised:
                    validate_nested("dialogues", [{"id": entry_id}])
                self.assertIn("dialogues.0.id", raised.exception.errors)


if __name__ == "__main__":
    unittest.main()

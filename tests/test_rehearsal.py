"""Farmer preview changes must not rewrite dialogue or promise a game interpreter."""
import unittest

from pixelheart_core.rehearsal import preview_dialogue, portrait_expression


class DialogueRehearsalTests(unittest.TestCase):
    def test_name_gender_and_portrait_tags_are_resolved_together(self):
        text = "Welcome, ${sir^ma'am}$ @!$h#$b#Stay a while."
        self.assertEqual(preview_dialogue(text, farmer_name="Robin", farmer_gender="female"),
                         "Welcome, ma'am Robin!\nStay a while.")
        self.assertEqual(preview_dialogue(text, farmer_name="Alex", farmer_gender="male"),
                         "Welcome, sir Alex!\nStay a while.")
        self.assertEqual(text, "Welcome, ${sir^ma'am}$ @!$h#$b#Stay a while.")

    def test_legacy_and_alternative_delimiter_gender_dialogue(self):
        self.assertEqual(preview_dialogue("Hello, sir.^Hello, ma'am."), "Hello, ma'am.")
        self.assertEqual(preview_dialogue("${brother¦sister¦friend}$", farmer_gender="male"), "brother")
        self.assertEqual(preview_dialogue("${brother¦sister¦friend}$", farmer_gender="female"), "sister")
        self.assertEqual(preview_dialogue("${one^two¦three}$", farmer_gender="male"), "one^two")

    def test_unhandled_game_commands_remain_visible(self):
        self.assertEqual(preview_dialogue("$q 10/11 What now?"), "$q 10/11 What now?")
        self.assertEqual(preview_dialogue("Hello @.$0", farmer_name="  "), "Hello Farmer.")
        self.assertEqual(preview_dialogue("${unfinished}$"), "${unfinished}$")
        self.assertEqual(preview_dialogue("$1 mailFlag#First#Second"), "$1 mailFlag#First#Second")

    def test_portrait_uses_the_selected_gender_branch_and_standard_expression_order(self):
        text = "${Hello sir.$h^Hello ma'am.$l}$"
        self.assertEqual(portrait_expression(text, farmer_gender="male"), 1)
        self.assertEqual(portrait_expression(text, farmer_gender="female"), 4)
        self.assertEqual(portrait_expression("Unique.$u"), 3)
        self.assertEqual(portrait_expression("Angry.$a"), 5)
        self.assertEqual(portrait_expression("$1 flag#First#Second"), 0)

"""Dialogue split into game-style boxes, each with its portrait."""
import unittest

from pixelheart_core.dialogue_pages import BOX_LINES, EMOTIONS, LINE_CHARS, dialogue_pages, set_emotion


class DialoguePageTests(unittest.TestCase):
    def test_emotions_match_the_games_portrait_order(self):
        self.assertEqual([(e.key, e.command, e.index) for e in EMOTIONS], [
            ("neutral", "$0", 0), ("happy", "$h", 1), ("sad", "$s", 2),
            ("unique", "$u", 3), ("love", "$l", 4), ("angry", "$a", 5)])

    def test_boxes_follow_breaks_and_each_resets_its_portrait(self):
        pages = dialogue_pages("Hi, @!$h#$b#I'm tired.#$b#Anyway.$4")
        self.assertEqual([page.lines for page in pages], [("Hi, Farmer!",), ("I'm tired.",), ("Anyway.",)])
        self.assertEqual([page.portrait for page in pages], [1, 0, 4])
        self.assertFalse(any(page.new_conversation or page.continued or page.raw for page in pages))

    def test_end_marker_starts_the_next_conversation(self):
        pages = dialogue_pages("See you.#$e#Back again?$s")
        self.assertEqual([page.new_conversation for page in pages], [False, True])
        self.assertEqual(pages[1].portrait, 2)

    def test_long_text_continues_in_more_boxes(self):
        words = " ".join(["word"] * 60)
        pages = dialogue_pages(words + "$l")
        self.assertGreater(len(pages), 1)
        self.assertTrue(all(len(page.lines) <= BOX_LINES for page in pages))
        self.assertTrue(all(len(line) <= LINE_CHARS for page in pages for line in page.lines))
        self.assertEqual([page.continued for page in pages], [False] + [True] * (len(pages) - 1))
        self.assertTrue(all(page.portrait == 4 for page in pages))

    def test_a_very_long_word_is_split(self):
        pages = dialogue_pages("x" * (LINE_CHARS + 5))
        self.assertEqual(pages[0].lines, ("x" * LINE_CHARS, "x" * 5))

    def test_farmer_name_and_gender_switch(self):
        pages = dialogue_pages("Hello, @. You're a fine ${lad^lass}$.", farmer="Ada")
        self.assertEqual(pages[0].lines, ("Hello, Ada. You're a fine lad/lass.",))
        self.assertEqual(dialogue_pages("${a¦b¦c}$")[0].lines, ("a/b/c",))

    def test_other_game_commands_are_shown_as_written(self):
        pages = dialogue_pages("Plain first.#$b#$q 1/2 q#Yes#$r 1 0 a")
        self.assertEqual([page.raw for page in pages], [False, True])
        self.assertEqual(pages[1].lines, ("$q 1/2 q#Yes#$r 1 0 a",))

    def test_empty_and_command_only_text(self):
        self.assertEqual(dialogue_pages(""), [])
        pages = dialogue_pages("$h")
        self.assertEqual((pages[0].lines, pages[0].portrait), ((), 1))

    def test_set_emotion_replaces_the_command_in_the_box_with_the_cursor(self):
        text = "Hi!$h#$b#How are you?#$b#Bye."
        cursor = text.index("How") + 3
        changed, position = set_emotion(text, cursor, "$s")
        self.assertEqual(changed, "Hi!$h#$b#How are you?$s#$b#Bye.")
        self.assertTrue(changed.index("How") <= position <= changed.index("$s") + 2)
        again, _ = set_emotion(changed, cursor, "$l")
        self.assertEqual(again, "Hi!$h#$b#How are you?$l#$b#Bye.")
        neutral, _ = set_emotion(again, cursor, "$0")
        self.assertEqual(neutral, "Hi!$h#$b#How are you?#$b#Bye.")

    def test_set_emotion_cursor_edges(self):
        self.assertEqual(set_emotion("Hello", 5, "$h"), ("Hello$h", 7))
        self.assertEqual(set_emotion("", 0, "$a"), ("$a", 2))
        text = "One#$b#Two"
        inside_marker = text.index("#$b#") + 2
        self.assertEqual(set_emotion(text, inside_marker, "$h")[0], "One$h#$b#Two")
        self.assertEqual(set_emotion("Hi $h  ", 1, "$s")[0], "Hi$s")


if __name__ == "__main__":
    unittest.main()

"""The character summary the designer reads at the start of every turn."""
import unittest

from pixelheart_core.projects import new_project
from pixelheart_designer.summary import summarize


class SummaryTests(unittest.TestCase):
    def test_summary_names_every_section(self):
        document = new_project(story_starter="friendship")
        character = document["character"]
        character.update(name="Mira", tagline="Keeps bees", occupation="Beekeeper", season="summer", day=14)
        character["gifts"]["love"] = ["309"]            # Acorn
        text = summarize(document)
        for expected in ("Name: Mira", "Tagline: Keeps bees", "Birthday: Summer 14", "Storyline brief",
                         "Conversations (1 line)", "Introduction: Hello! It's lovely to meet you.",
                         "Gifts: love: Acorn", "Daily routine (1 stop)", "600 Town",
                         "Heart events (4)", "2 hearts · A shared interest · outline · Town",
                         "Places you designed: 0", "Artwork: portrait no · sprite no"):
            self.assertIn(expected, text)

    def test_dialogue_lines_are_cut_at_eighty_characters(self):
        document = new_project()
        document["character"]["dialogues"][0]["text"] = "x" * 200
        self.assertIn("x" * 80 + "…", summarize(document))
        self.assertNotIn("x" * 81, summarize(document))

    def test_sections_after_dialogue_survive_many_lines(self):
        document = new_project()
        document["character"]["dialogues"] = [{"id": str(i), "trigger": f"K{i}", "text": "word " * 15} for i in range(2000)]
        text = summarize(document)
        for expected in ("Gifts:", "Daily routine (1 stop)", "Heart events (0)", "Artwork: portrait no", "more lines"):
            self.assertIn(expected, text)
        self.assertFalse(text.endswith("…(more)"))

    def test_whole_summary_is_bounded(self):
        document = new_project()
        document["character"]["dialogues"] = [{"id": str(i), "trigger": f"K{i}", "text": "word " * 15} for i in range(2000)]
        text = summarize(document, limit=2000)
        self.assertLessEqual(len(text), 2000)
        self.assertIn("more lines", text)          # dialogue trimmed first, tail sections kept
        self.assertIn("Artwork:", text)
        tiny = summarize(document, limit=300)
        self.assertLessEqual(len(tiny), 300)
        self.assertTrue(tiny.endswith("…(more)"))  # the hard cut, when even the trimmed form is too long


if __name__ == "__main__":
    unittest.main()

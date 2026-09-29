"""Where characters live by default, and their welcome-screen summaries."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pixelheart_core.library import (
    default_library_root, edited_ago, folder_name, internal_name_for, library_projects,
    new_project_path, project_card, remember_recent,
)
from pixelheart_core.projects import new_project, save_project


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_default_root_is_documents_unless_overridden(self):
        with patch.dict(os.environ, {"PIXELHEART_LIBRARY": str(self.root)}):
            self.assertEqual(default_library_root(), self.root)
        with patch.dict(os.environ, {}, clear=True), \
             patch("pixelheart_core.library.Path.home", return_value=Path("/home/me")):
            self.assertEqual(default_library_root(), Path("/home/me/Documents/Pixelheart"))

    def test_unsafe_and_duplicate_names_get_safe_unique_folders(self):
        self.assertEqual(folder_name('Mira/"The Brave"?'), "MiraThe Brave")
        self.assertEqual(folder_name("   ...  "), "New character")
        self.assertEqual(folder_name("Rosé ✿"), "Rosé ✿")
        first = new_project_path(self.root, "Mira")
        self.assertEqual(first, self.root / "Mira" / "character.json")
        first.parent.mkdir()
        self.assertEqual(new_project_path(self.root, "Mira"), self.root / "Mira 2" / "character.json")

    def test_internal_name_from_unusual_names(self):
        self.assertEqual(internal_name_for("Mira Rose"), "MiraRose")
        self.assertEqual(internal_name_for("7 Stars"), "NPC7Stars")
        self.assertEqual(internal_name_for("✿✿"), "NPC")

    def test_recent_list_is_deduplicated_and_capped(self):
        rows = remember_recent(["/a/character.json", "/b/character.json"], "/b/character.json", limit=2)
        self.assertEqual(rows, ["/b/character.json", "/a/character.json"])
        self.assertEqual(len(remember_recent([f"/{n}" for n in range(20)], "/new")), 12)

    def test_library_projects_and_cards(self):
        document = new_project()
        document["character"]["name"] = "Mira"
        path = save_project(document, self.root / "Mira" / "character.json")
        (self.root / "Empty").mkdir()
        self.assertEqual(library_projects(self.root), [path])
        self.assertEqual(library_projects(self.root / "missing"), [])
        card = project_card(path)
        self.assertEqual((card["name"], card["hearts"], card["total"], card["error"]), ("Mira", 1, 8, None))
        (self.root / "Broken").mkdir()
        (self.root / "Broken/character.json").write_text("{nope")
        self.assertIsNotNone(project_card(self.root / "Broken/character.json")["error"])
        self.assertIsNotNone(project_card(self.root / "Gone/character.json")["error"])

    def test_edited_ago_reads_naturally(self):
        now = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
        self.assertEqual(edited_ago((now - timedelta(seconds=20)).timestamp(), now), "Edited just now")
        self.assertEqual(edited_ago((now - timedelta(minutes=5)).timestamp(), now), "Edited 5 minutes ago")
        self.assertEqual(edited_ago((now - timedelta(hours=1)).timestamp(), now), "Edited 1 hour ago")
        self.assertEqual(edited_ago((now - timedelta(days=1, hours=1)).timestamp(), now), "Edited yesterday")
        self.assertEqual(edited_ago((now - timedelta(days=3)).timestamp(), now), "Edited 3 days ago")
        self.assertEqual(edited_ago((now - timedelta(days=40)).timestamp(), now), "Edited Aug 20")


if __name__ == "__main__":
    unittest.main()

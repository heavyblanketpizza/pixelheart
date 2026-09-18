import json
from pathlib import Path
import tempfile
import unittest
from pixelheart_core.installation import dependency_report


class DependencyTests(unittest.TestCase):
    def test_missing_old_found_and_disabled_frameworks(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            for folder, identity, version in [("nested/CP", "Pathoschild.ContentPatcher", "2.9.0"), ("Repeater", "misscoriel.eventrepeater", "6.5.7"), (".disabled/Other", "Other.Mod", "1.0.0")]:
                target = path / folder
                target.mkdir(parents=True)
                (target / "manifest.json").write_text(json.dumps({"UniqueID": identity, "Version": version}))
            result = dependency_report(path, {"ContentPackFor": {"UniqueID": "Pathoschild.ContentPatcher", "MinimumVersion": "2.9.0"}, "Dependencies": [{"UniqueID": "misscoriel.eventrepeater", "MinimumVersion": "6.5.8"}, {"UniqueID": "Other.Mod"}, {"UniqueID": "Optional.Mod", "IsRequired": False}]})
            self.assertEqual([row["status"] for row in result], ["found", "old", "missing"])

    def test_symlinks_and_malformed_json_are_ignored_duplicates_require_review(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            for name in ("A", "B"):
                (path / name).mkdir()
                (path / name / "manifest.json").write_text('{"UniqueID":"Mod.A","Version":"1.0.0"}')
            (path / "broken").mkdir()
            (path / "broken/manifest.json").write_text("bad")
            (path / "linked").symlink_to(path / "A", target_is_directory=True)
            result = dependency_report(path, {"ContentPackFor": {"UniqueID": "Mod.A"}})
            self.assertEqual(result[0]["status"], "review")
            self.assertEqual(result[0]["version"], "1.0.0, 1.0.0")

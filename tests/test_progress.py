"""What still needs doing, derived only from the saved character document."""
import unittest
import uuid

from pixelheart_core.playtesting import record_export, record_install
from pixelheart_core.progress import STEP_KEYS, hearts_earned, next_step, project_progress
from pixelheart_core.projects import new_project


def by_key(document, **options):
    return {step["key"]: step for step in project_progress(document, **options)}


class ProgressTests(unittest.TestCase):
    def test_a_new_character_has_eight_unfinished_steps(self):
        steps = project_progress(new_project())
        self.assertEqual([step["key"] for step in steps], list(STEP_KEYS))
        self.assertEqual(hearts_earned(steps), 0)
        self.assertEqual(next_step(steps)["key"], "identity")
        status = {step["key"]: step["status"] for step in steps}
        self.assertEqual(status["dialogue"], "1 of 8 everyday lines")
        self.assertEqual(status["schedule"], "1 stop so far")
        self.assertEqual(status["home"], "Optional · not started")
        self.assertTrue(by_key(new_project())["home"]["optional"])

    def test_each_step_completes_from_authored_content(self):
        document = new_project()
        character = document["character"]
        character["name"] = "Mira"
        character["dialogues"] += [{"id": str(uuid.uuid4()), "trigger": day, "text": "Hi."}
                                   for day in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")]
        character["schedule"].append(dict(character["schedule"][0], id=str(uuid.uuid4()), time="900"))
        character["gifts"]["love"] = ["(O)72"]
        character["events"] = [{"id": "e1", "story": {"stage": "ready"}}, {"id": "e2", "story": {"stage": "scene"}}]
        document["artwork"] = {"portrait": "artwork/p.png", "sprite": "artwork/s.png"}
        document["world"] = {"locations": [{"interior": {"kind": "residence"}}]}
        steps = by_key(document)
        for key in ("identity", "dialogue", "schedule", "gifts", "story", "artwork", "home"):
            self.assertTrue(steps[key]["done"], key)
        self.assertEqual(steps["story"]["status"], "1 draft, 1 ready")
        self.assertFalse(steps["export"]["done"])
        self.assertEqual(next_step(list(steps.values()))["key"], "export")

    def test_blank_lines_do_not_count(self):
        document = new_project()
        document["character"]["dialogues"][0]["text"] = "   "
        self.assertEqual(by_key(document)["dialogue"]["status"], "0 of 8 everyday lines")

    def test_invalid_artwork_is_not_done(self):
        document = new_project()
        document["artwork"] = {"portrait": "artwork/p.png", "sprite": "artwork/s.png"}
        step = by_key(document, artwork_valid=False)["artwork"]
        self.assertFalse(step["done"])
        self.assertEqual(step["status"], "Artwork needs a fix")

    def test_install_counts_only_for_the_current_version(self):
        document = record_install(record_export(new_project(), "pack.zip", b"zip"), "Mods/[CP] Mira")
        self.assertTrue(by_key(document)["export"]["done"])
        self.assertEqual(by_key(document)["export"]["status"], "In your game")
        document["character"]["tagline"] = "Changed after installing"
        self.assertFalse(by_key(document)["export"]["done"])
        exported = record_export(new_project(), "pack.zip", b"zip")
        self.assertEqual(by_key(exported)["export"]["status"], "Exported, not installed yet")


if __name__ == "__main__":
    unittest.main()

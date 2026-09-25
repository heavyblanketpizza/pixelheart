"""Place edits must not silently orphan authored destinations."""
from copy import deepcopy
import unittest

from pixelheart_core.life import new_life_record, normalize_life
from pixelheart_core.place_references import place_references, place_removal_blockers, rename_place
from pixelheart_core.projects import new_project
from pixelheart_core.story import new_event
from pixelheart_core.world import WorldError, exported_location_id, new_companion, new_location, new_world


class PlaceReferencesTests(unittest.TestCase):
    def setUp(self):
        self.character = new_project()["character"]
        self.character.update(name="Mira", id="stable-mira")
        self.character["life"] = normalize_life()
        self.world = new_world()
        self.place = new_location()
        self.place.update(name="Mira's cottage", internal_name="Cottage")
        self.world["locations"].append(self.place)
        self.game_id = exported_location_id(self.place, self.character)

    def fields(self, references):
        return {reference["field"] for reference in references}

    def test_removal_excludes_only_primary_home_and_own_entrance(self):
        self.character["home_map"] = self.game_id
        self.place["entrance"]["map"] = "Cottage"
        companion = new_companion("Sol")
        companion["character"]["home_map"] = self.game_id
        self.world["characters"].append(companion)
        all_fields = self.fields(place_references(self.place, self.character, self.world))
        self.assertEqual(all_fields, {"home_map", "world.locations.0.entrance.map", "world.characters.0.character.home_map"})
        blockers = place_removal_blockers(self.place, self.character, self.world)
        self.assertEqual(self.fields(blockers), {"world.characters.0.character.home_map"})
        self.assertIn("Sol", blockers[0]["description"])

    def test_daily_routes_actorless_draft_scenes_and_other_entrances_block_removal(self):
        self.character["schedule"][0]["location"] = "Cottage"
        self.character["events"] = [{"name": "An unfinished visit", "location": self.game_id, "story": {"actors": []}}]
        annex = new_location()
        annex.update(name="Annex", internal_name="Annex")
        annex["entrance"]["map"] = self.game_id
        self.world["locations"].append(annex)
        before = deepcopy((self.character, self.world))
        blockers = place_removal_blockers(self.place, self.character, self.world)
        self.assertEqual(self.fields(blockers), {"schedule.0.location", "events.0.location", "world.locations.1.entrance.map"})
        self.assertTrue(all(row["description"] for row in blockers))
        self.assertEqual((self.character, self.world), before)

    def test_disabled_life_routes_and_explicit_imported_location_conditions_count(self):
        routine = new_life_record("routines")
        routine.update(name="Quiet mornings", stops=[{"location": self.game_id}])
        routine["conditions"]["map"] = "Cottage"
        dialogue = new_life_record("dialogues")
        dialogue["conditions"].update(location=self.game_id, after_event_id="Cottage", notes="Cottage")
        spouse = new_life_record("spouse_dialogue")
        spouse["conditions"]["map"] = "Cottage"
        self.character["life"].update(routines=[routine], dialogues=[dialogue], spouse_dialogue=[spouse])
        self.assertEqual(self.fields(place_removal_blockers(self.place, self.character, self.world)), {
            "life.routines.0.stops.0.location", "life.routines.0.conditions.map",
            "life.dialogues.0.conditions.location", "life.spouse_dialogue.0.conditions.map"})

    def test_rename_migrates_all_scopes_and_alias_forms_on_copies(self):
        self.character.update(home_map="Cottage", bio="Cottage")
        self.character["schedule"][0].update(location=self.game_id, activity="Cottage")
        event = new_event(self.character)
        event.update(location="Cottage", name="Cottage", story={"actors": []})
        self.character["events"] = [event]
        routine = new_life_record("routines")
        routine.update(stops=[{"location": "Cottage", "x": 4, "y": 6}])
        routine["conditions"]["map"] = self.game_id
        self.character["life"]["routines"] = [routine]
        dialogue = new_life_record("dialogues")
        dialogue.update(text="Cottage")
        dialogue["conditions"].update(location="Cottage", after_event_id="Cottage", notes="Cottage")
        self.character["life"]["dialogues"] = [dialogue]
        companion = new_companion("Sol")
        companion["character"] = deepcopy(self.character)
        companion["character"]["home_map"] = self.game_id
        self.world["characters"].append(companion)
        self.place["entrance"]["map"] = "Cottage"
        annex = new_location()
        annex["entrance"]["map"] = self.game_id
        self.world["locations"].append(annex)
        self.world["notes"] = {"map": "Cottage"}
        self.place["map"] = "Cottage"
        before = deepcopy((self.character, self.world))

        character, world = rename_place(self.character, self.world, self.place["id"], "Workshop")
        renamed = world["locations"][0]
        game_id = exported_location_id(renamed, character)
        self.assertEqual(renamed["internal_name"], "Workshop")
        self.assertEqual(character["home_map"], "Workshop")
        self.assertEqual(character["schedule"][0]["location"], game_id)
        self.assertEqual(character["events"][0]["location"], "Workshop")
        self.assertEqual(character["life"]["routines"][0]["stops"][0]["location"], "Workshop")
        self.assertEqual(character["life"]["routines"][0]["conditions"]["map"], game_id)
        self.assertEqual(character["life"]["dialogues"][0]["conditions"]["location"], "Workshop")
        self.assertEqual(world["characters"][0]["character"]["home_map"], game_id)
        self.assertEqual(world["characters"][0]["character"]["events"][0]["location"], "Workshop")
        self.assertEqual(renamed["entrance"]["map"], "Workshop")
        self.assertEqual(world["locations"][1]["entrance"]["map"], game_id)
        self.assertEqual(place_references(self.place, character, world), [])
        for value in (character["bio"], character["schedule"][0]["activity"], character["events"][0]["name"],
                      character["life"]["dialogues"][0]["text"], character["life"]["dialogues"][0]["conditions"]["after_event_id"],
                      character["life"]["dialogues"][0]["conditions"]["notes"], world["notes"]["map"], renamed["map"]):
            self.assertEqual(value, "Cottage")
        self.assertEqual((self.character, self.world), before)

    def test_invalid_or_conflicting_renames_leave_inputs_unchanged(self):
        other = new_location()
        other["internal_name"] = "Workshop"
        self.world["locations"].append(other)
        before = deepcopy((self.character, self.world))
        for name in ("", "1Home", "Not a map", "A" * 41, "Town", "town", "workshop"):
            with self.subTest(name=name), self.assertRaises(WorldError):
                rename_place(self.character, self.world, self.place["id"], name)
            self.assertEqual((self.character, self.world), before)
        with self.assertRaises(WorldError):
            rename_place(self.character, self.world, "missing-place", "NewHome")

    def test_duplicate_old_map_id_is_not_guessed(self):
        other = new_location()
        other["internal_name"] = "cottage"
        self.world["locations"].append(other)
        self.character["schedule"][0]["location"] = "Cottage"
        with self.assertRaisesRegex(WorldError, "shares its map ID"):
            rename_place(self.character, self.world, self.place["id"], "NewHome")

    def test_unused_duplicate_draft_ids_can_be_repaired(self):
        other = new_location()
        other["internal_name"] = "Cottage"
        self.world["locations"].append(other)
        character, world = rename_place(self.character, self.world, self.place["id"], "NewHome")
        self.assertEqual([place["internal_name"] for place in world["locations"]], ["NewHome", "Cottage"])
        self.assertEqual(character, self.character)

    def test_unrelated_and_malformed_draft_fields_do_not_become_references(self):
        self.character.update(schedule=[None, {"location": []}, {"location": "CottageAnnex"}], events=[None, {"location": "cottage"}])
        self.character["life"] = {"dialogues": [None, {"conditions": {"location": ["Cottage"]}}], "routines": None}
        self.world["characters"] = [None, {"character": None}]
        self.assertEqual(place_removal_blockers(self.place, self.character, self.world), [])


if __name__ == "__main__":
    unittest.main()

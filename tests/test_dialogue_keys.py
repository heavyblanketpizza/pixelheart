"""Plain-language occasions for dialogue, and the game keys they stand for."""
import unittest

from pixelheart_core.dialogue_keys import (
    DAYS, HEARTS, SEASONS, build_trigger, describe_trigger, parse_trigger,
)


class DialogueKeyTests(unittest.TestCase):
    def roundtrip(self, key, spec, **options):
        self.assertEqual(parse_trigger(key, **options), spec)
        self.assertEqual(build_trigger(spec), key)

    def test_first_meeting(self):
        self.roundtrip("Introduction", {"occasion": "introduction"})

    def test_days_of_the_week_with_season_and_hearts(self):
        self.roundtrip("Mon", {"occasion": "weekday", "day": "Mon", "season": "", "hearts": 0})
        self.roundtrip("Sun4", {"occasion": "weekday", "day": "Sun", "season": "", "hearts": 4})
        self.roundtrip("summer_Mon", {"occasion": "weekday", "day": "Mon", "season": "summer", "hearts": 0})
        self.roundtrip("summer_Thu10", {"occasion": "weekday", "day": "Thu", "season": "summer", "hearts": 10})

    def test_only_even_hearts_from_two_to_ten(self):
        self.assertEqual(HEARTS, (0, 2, 4, 6, 8, 10))
        self.assertEqual(parse_trigger("Mon3")["occasion"], "other")
        self.assertEqual(parse_trigger("Mon12")["occasion"], "other")
        with self.assertRaises(ValueError):
            build_trigger({"occasion": "weekday", "day": "Mon", "season": "", "hearts": 5})

    def test_dates(self):
        self.roundtrip("summer_1", {"occasion": "date", "season": "summer", "day": 1})
        self.roundtrip("winter_28", {"occasion": "date", "season": "winter", "day": 28})
        self.assertEqual(parse_trigger("winter_29")["occasion"], "other")
        with self.assertRaises(ValueError):
            build_trigger({"occasion": "date", "season": "spring", "day": 0})

    def test_places_with_an_optional_day(self):
        self.roundtrip("Saloon", {"occasion": "place", "location": "Saloon", "day": ""})
        self.roundtrip("Saloon_Tue", {"occasion": "place", "location": "Saloon", "day": "Tue"})
        self.roundtrip("MiraHome_Sat", {"occasion": "place", "location": "MiraHome", "day": "Sat"},
                       places=("MiraHome",))

    def test_place_names_with_underscores(self):
        self.roundtrip("Custom_Place", {"occasion": "place", "location": "Custom_Place", "day": ""},
                       places=("Custom_Place",))
        self.roundtrip("Custom_Place_Fri", {"occasion": "place", "location": "Custom_Place", "day": "Fri"},
                       places=("Custom_Place",))

    def test_gifts(self):
        self.roundtrip("AcceptGift_(O)109", {"occasion": "gift", "item": "(O)109"})
        self.roundtrip("AcceptBirthdayGift_Positive", {"occasion": "birthday_gift", "liked": True})
        self.roundtrip("AcceptBirthdayGift_Negative", {"occasion": "birthday_gift", "liked": False})
        with self.assertRaises(ValueError):
            build_trigger({"occasion": "gift", "item": ""})

    def test_unknown_keys_are_kept(self):
        for key in ("Resort_Bar", "eventSeen_3", "GreenRain", "Thu2_2", "", "spring_Mon_old"):
            self.roundtrip(key, {"occasion": "other", "key": key})

    def test_descriptions_are_plain(self):
        self.assertEqual(describe_trigger("Introduction"), "The first time they meet")
        self.assertEqual(describe_trigger("Mon"), "Mondays")
        self.assertEqual(describe_trigger("summer_Thu4"), "Summer Thursdays · 4+ hearts")
        self.assertEqual(describe_trigger("Sun10"), "Sundays · 10 hearts")
        self.assertEqual(describe_trigger("fall_12"), "Fall 12")
        self.assertEqual(describe_trigger("Saloon_Tue"), "At Stardrop Saloon on Tuesdays")
        self.assertEqual(describe_trigger("MiraHome", places={"MiraHome": "Mira's home"}), "At Mira's home")
        self.assertEqual(describe_trigger("AcceptGift_(O)109", item_names={"(O)109": "Poppy"}), "When given Poppy")
        self.assertEqual(describe_trigger("AcceptGift_(O)109"), "When given item (O)109")
        self.assertEqual(describe_trigger("AcceptBirthdayGift_Positive"), "When given a birthday gift they like")
        self.assertEqual(describe_trigger("AcceptBirthdayGift_Negative"), "When given a birthday gift they don't like")
        self.assertEqual(describe_trigger("Resort_Bar"), "Game key: Resort_Bar")
        self.assertEqual(describe_trigger(""), "Choose when they say it")

    def test_choices_are_listed_in_game_order(self):
        self.assertEqual(DAYS, ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))
        self.assertEqual(SEASONS, ("spring", "summer", "fall", "winter"))


if __name__ == "__main__":
    unittest.main()

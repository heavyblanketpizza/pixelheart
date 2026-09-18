"""The offline calendar must cover all NPCs without treating invalid dates as free."""

import unittest

from pixelheart_core.birthdays import (
    DAYS_PER_SEASON,
    SEASONS,
    birthdays_for_season,
    birthdays_on,
    festivals_for_season,
    festivals_on,
)


class BirthdayCatalogTests(unittest.TestCase):
    def test_all_base_game_npcs_are_present_once_on_valid_dates(self):
        names = []
        for season, count in zip(SEASONS, (8, 9, 8, 9), strict=True):
            calendar = birthdays_for_season(season)
            self.assertEqual(len(calendar), count)
            for day, residents in calendar.items():
                self.assertIs(type(day), int)
                self.assertTrue(1 <= day <= DAYS_PER_SEASON)
                self.assertTrue(residents)
                names.extend(residents)
        self.assertEqual(len(names), 34)
        self.assertEqual(len(set(names)), 34)

    def test_late_arrivals_and_non_town_residents_are_reserved(self):
        for season, day, name in (
            ("spring", 4, "Kent"),
            ("summer", 22, "Dwarf"),
            ("summer", 26, "Leo"),
            ("fall", 15, "Sandy"),
            ("winter", 1, "Krobus"),
            ("winter", 17, "Wizard"),
        ):
            with self.subTest(name=name):
                self.assertEqual(birthdays_on(season, day), (name,))

    def test_available_dates_and_festival_dates_are_distinct(self):
        self.assertEqual(birthdays_on("spring", 1), ())
        self.assertEqual(festivals_on("spring", 1), ())
        self.assertEqual(birthdays_on("spring", 13), ())
        self.assertEqual(festivals_on("spring", 13), ("Egg Festival",))
        self.assertEqual(birthdays_on("winter", 17), ("Wizard",))
        self.assertEqual(festivals_on("winter", 17), ("Night Market",))

    def test_passive_festivals_include_every_date(self):
        for season, days, name in (
            ("spring", (15, 16, 17), "Desert Festival"),
            ("summer", (20, 21), "Trout Derby"),
            ("winter", (12, 13), "SquidFest"),
            ("winter", (15, 16, 17), "Night Market"),
        ):
            with self.subTest(festival=name):
                calendar = festivals_for_season(season)
                self.assertEqual(
                    tuple(day for day, names in calendar.items() if name in names), days
                )

    def test_season_mappings_cannot_mutate_shared_catalog(self):
        birthdays = birthdays_for_season("spring")
        birthdays[4] = ("Changed",)
        birthdays[1] = ("New",)
        festivals = festivals_for_season("spring")
        festivals.clear()
        self.assertEqual(birthdays_on("spring", 4), ("Kent",))
        self.assertEqual(birthdays_on("spring", 1), ())
        self.assertEqual(festivals_on("spring", 13), ("Egg Festival",))

    def test_invalid_dates_are_rejected_instead_of_reported_as_available(self):
        for season in ("Spring", "autumn", "", None, []):
            for query in (birthdays_for_season, festivals_for_season):
                with self.subTest(query=query.__name__, season=season):
                    with self.assertRaises(ValueError):
                        query(season)
        for day in (0, 29, -1, True, 1.0, "4", None):
            for query in (birthdays_on, festivals_on):
                with self.subTest(query=query.__name__, day=day):
                    with self.assertRaises(ValueError):
                        query("spring", day)


if __name__ == "__main__":
    unittest.main()

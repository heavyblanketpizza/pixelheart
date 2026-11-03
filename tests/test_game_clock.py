"""The game clock lives in core so routine tools and editors share one rule."""
import unittest

from pixelheart_core.game_clock import clock_label, game_minutes, game_time


class GameClockTests(unittest.TestCase):
    def test_minutes_accept_both_spellings_in_ten_minute_steps(self):
        self.assertEqual(game_minutes("600"), 360)
        self.assertEqual(game_minutes("6:00"), 360)
        self.assertEqual(game_minutes("2600"), 1560)
        self.assertIsNone(game_minutes("605"))
        self.assertIsNone(game_minutes("2:00"))
        self.assertIsNone(game_minutes("noon"))

    def test_round_trip_and_labels(self):
        self.assertEqual(game_time(360), "600")
        self.assertEqual(game_time(1500), "2500")
        self.assertEqual(clock_label(570), "9:30 AM")
        self.assertEqual(clock_label(1500), "1:00 AM (next day)")


if __name__ == "__main__":
    unittest.main()

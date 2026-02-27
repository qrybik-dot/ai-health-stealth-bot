import datetime as dt
import unittest

import main


class WeeklyAndRefreshTests(unittest.TestCase):
    def test_derive_weekly_status_preliminary_when_low_data(self):
        now = dt.datetime(2026, 3, 8, 19, 0)
        history = {
            (now.date() - dt.timedelta(days=0)).isoformat(): {"sleep": {"sleepTimeSeconds": 7 * 3600}},
            (now.date() - dt.timedelta(days=1)).isoformat(): {},
        }
        days = main.collect_weekly_data(history, now)
        derived = main.derive_weekly_status(days)
        self.assertEqual(derived["hero_status"], "Неделя предварительная")
        self.assertEqual(len(derived["day_points"]), 7)

    def test_generate_weekly_quest_data_linked(self):
        derived = {
            "strongest_period": "утро",
            "partial_days": 3,
            "stability": 0.2,
            "best_days": 1,
            "tense_days": 3,
        }
        quest = main.generate_weekly_quest(derived, [])
        self.assertIn("синхронизац", quest.lower())

    def test_refresh_result_no_updates(self):
        msg = main.build_refresh_result_message({"updated_blocks": [], "after": {"data_completeness": 0.4}})
        self.assertIn("Новых данных пока нет", msg)

    def test_refresh_result_partial(self):
        msg = main.build_refresh_result_message(
            {
                "updated_blocks": ["sleep", "stress"],
                "after": {"data_completeness": 0.5, "missing_flags": {"rhr": True, "steps": True}},
            }
        )
        self.assertIn("Обновились", msg)
        self.assertIn("ещё не дошли", msg)

    def test_refresh_result_no_updates_with_missing_explained(self):
        msg = main.build_refresh_result_message(
            {"updated_blocks": [], "after": {"missing_flags": {"sleep": True, "steps": True}}}
        )
        self.assertIn("Garmin Connect", msg)
        self.assertIn("отсутствуют", msg)

    def test_collect_updated_blocks_detects_missing_to_present(self):
        before = {"missing_flags": {"sleep": True}, "sleep": None}
        after = {"missing_flags": {"sleep": False}, "sleep": {"sleepTimeSeconds": 25000}}
        updated = main._collect_updated_blocks(before, after)
        self.assertIn("sleep", updated)


if __name__ == "__main__":
    unittest.main()

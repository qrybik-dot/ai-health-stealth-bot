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
        self.assertIn("Данные уже актуальны", msg)

    def test_refresh_result_partial(self):
        msg = main.build_refresh_result_message(
            {
                "updated_blocks": ["sleep", "stress"],
                "after": {"data_completeness": 0.5, "missing_flags": {"rhr": True, "steps": True}},
            }
        )
        self.assertIn("Обновил данные", msg)
        self.assertIn("ещё ожидаю", msg)

    def test_refresh_result_no_updates_with_missing_explained(self):
        msg = main.build_refresh_result_message(
            {"updated_blocks": [], "after": {"missing_flags": {"sleep": True, "steps": True}}}
        )
        self.assertIn("Garmin Connect", msg)
        self.assertIn("не хватает", msg)

    def test_collect_updated_blocks_detects_missing_to_present(self):
        before = {"missing_flags": {"sleep": True}, "sleep": None}
        after = {"missing_flags": {"sleep": False}, "sleep": {"sleepTimeSeconds": 25000}}
        updated = main._collect_updated_blocks(before, after)
        self.assertIn("sleep", updated)


    def test_refresh_result_reports_actual_updates(self):
        msg = main.build_refresh_result_message(
            {
                "updated_blocks": ["sleep"],
                "new_completeness": 0.71,
                "after": {"missing_flags": {"hrv": True}},
            }
        )
        self.assertIn("Обновил данные: сон", msg)
        self.assertIn("ещё ожидаю", msg)

    def test_debug_sync_message_includes_cache_source(self):
        with unittest.mock.patch.object(main, "load_cache_with_meta", return_value=({}, {"source": "local", "available": False, "error": "local_missing_or_invalid"})):
            with unittest.mock.patch.object(main, "get_latest_sync_trace", return_value={"run_id": "r1", "stage": "refresh", "had_real_updates": False, "updated_blocks": []}):
                with unittest.mock.patch.object(main, "current_day_key", return_value="2026-01-14"):
                    msg = main.build_debug_sync_message()
        self.assertIn("cache source: local", msg)
        self.assertIn("latest run id: r1", msg)


if __name__ == "__main__":
    unittest.main()

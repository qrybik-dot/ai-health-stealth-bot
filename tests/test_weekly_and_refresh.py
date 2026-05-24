import datetime as dt
import unittest
from unittest.mock import patch

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
        self.assertIn("черновик", derived["hero_status"].lower())
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

    def test_weekly_payload_includes_metric_ranges_without_overload(self):
        now = dt.datetime(2026, 3, 8, 19, 0)
        history = {}
        for delta in range(7):
            day = (now.date() - dt.timedelta(days=delta)).isoformat()
            history[day] = {
                "body_battery": {"mostRecentValue": 40 + delta},
                "stress": {"avgStressLevel": 30 + delta},
                "sleep": {"sleepTimeSeconds": (6 * 3600) + delta * 900},
                "steps": {"totalSteps": 4000 + delta * 500},
                "rhr": {"restingHeartRate": 54 + delta},
                "hrv": {"weeklyAvg": 45 + delta},
            }

        payload = main.build_weekly_payload(history, now, "chat")

        self.assertIn("📏 <b>Диапазоны недели</b>", payload["caption"])
        self.assertLessEqual(len(payload["range_lines"]), 5)
        self.assertTrue(any("Body Battery" in line for line in payload["range_lines"]))

    def test_weekly_metric_ranges_handle_missing_data(self):
        now = dt.datetime(2026, 3, 8, 19, 0)
        history = {
            now.date().isoformat(): {"sleep": {"sleepTimeSeconds": 7 * 3600}},
            (now.date() - dt.timedelta(days=1)).isoformat(): {},
        }
        weekly_days = main.collect_weekly_data(history, now)

        lines = main.build_weekly_metric_range_lines(weekly_days)

        self.assertEqual(lines, ["• Сон: 7.0–7.0 ч (1 дн.)"])

    def test_weekly_stats_message_counts_feedback_votes(self):
        msg = main.build_weekly_stats_message(
            "2026-W10",
            {"yes_count": 2, "partial_count": 1, "no_count": 1, "total": 4, "accuracy": 0.625},
            {"yes_count": 1, "partial_count": 0, "no_count": 1, "total": 2, "accuracy": 0.5},
        )

        self.assertIn("Статистика 2026-W10", msg)
        self.assertIn("Цвет недели", msg)
        self.assertIn("✅ 2 · 🤷 1 · ❌ 1", msg)
        self.assertIn("индекс 62%", msg)
        self.assertIn("Всего откликов: 6", msg)

    def test_weekly_stats_message_handles_empty_votes(self):
        msg = main.build_weekly_stats_message(
            "2026-W10",
            {"yes_count": 0, "partial_count": 0, "no_count": 0, "total": 0, "accuracy": 0.0},
            {"yes_count": 0, "partial_count": 0, "no_count": 0, "total": 0, "accuracy": 0.0},
        )

        self.assertIn("Цвет недели:</b> пока нет откликов", msg)
        self.assertIn("Статус дня:</b> пока нет откликов", msg)
        self.assertIn("Откликов за неделю пока нет", msg)

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
        self.assertIn("частично", msg)
        self.assertIn("ещё ждём", msg)

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
        self.assertIn("частично", msg)
        self.assertIn("ещё ждём", msg)

    def test_debug_sync_message_includes_cache_source(self):
        with patch.object(main, "load_cache_with_meta", return_value=({}, {"source": "local", "available": False, "error": "local_missing_or_invalid"})):
            with patch.object(main, "get_latest_sync_trace", return_value={"run_id": "r1", "stage": "refresh", "had_real_updates": False, "updated_blocks": []}):
                with patch.object(main, "current_day_key", return_value="2026-01-14"):
                    msg = main.build_debug_sync_message()
        self.assertIn("cache source: local", msg)
        self.assertIn("latest run id: r1", msg)

    def test_cache_self_check_prints_weekly_and_sent_counts(self):
        cache_payload = {
            "2026-01-14": {"sleep": {"sleepTimeSeconds": 24000}},
            "_weekly_state": {"2026-W03": {"week_id": "2026-W03"}},
            "_push_state": {
                "2026-01-14|chat|morning|verdict": {"ts": "x"},
                "2026-01-13|chat|morning|verdict": {"ts": "x"},
            },
        }
        with patch.object(main, "load_cache_with_meta", return_value=(cache_payload, {"source": "gist", "available": True, "error": ""})):
            with patch.object(main, "_now_msk", return_value=dt.datetime(2026, 1, 14, 12, 0, tzinfo=dt.timezone(dt.timedelta(hours=3)))):
                with patch("builtins.print") as mock_print:
                    main.run_cache_self_check()

        printed = "\n".join(str(call.args[0]) for call in mock_print.call_args_list)
        self.assertIn("weekly_state_count=1", printed)
        self.assertIn("today_sent_registry_count=1", printed)


if __name__ == "__main__":
    unittest.main()

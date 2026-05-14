import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import patch

import cache
import communication
import main


class BackfillAndRenderingTests(unittest.TestCase):
    def test_get_day_summary_full_completeness(self):
        history = {
            "2026-02-20": {
                "sleep": {"sleepTimeSeconds": 25000},
                "stress": {"avgStressLevel": 29},
                "body_battery": {"mostRecentValue": 65},
                "rhr": {"restingHeartRate": 54},
                "data_completeness": 1.0,
            }
        }
        summary = cache.get_day_summary("2026-02-20", cache_data=history)
        self.assertEqual(summary["completeness_state"], "FULL")

    def test_snapshot_renderers_are_different_for_same_input(self):
        snapshot = {
            "body_battery": {"mostRecentValue": 59, "chargedValue": 75},
            "stress": {"avgStressLevel": 43},
            "sleep": {"sleepTimeSeconds": 24800},
            "rhr": {"restingHeartRate": 57},
            "steps": {"totalSteps": 6200},
        }
        facts = communication.render_facts_rich(snapshot)
        roast = communication.render_roast(snapshot, {"available_days_count": 5}, slot="midday")
        self.assertIn("По фактам", facts)
        self.assertIn("Body Battery", facts)
        self.assertIn("Средний стресс", facts)
        self.assertIn("Сон", facts)
        self.assertIn("Шаги", facts)
        self.assertIn("Остальное", facts)
        self.assertIn("Вывод", facts)
        self.assertIn("Пожарь", roast)
        self.assertIn("Гипотеза", roast)
        self.assertNotEqual(facts, roast)

    def test_backfill_stores_days_and_history_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "cache.json")
            with patch.object(cache, "CACHE_FILE", cache_path):
                day_1 = (date.today() - timedelta(days=2)).isoformat()
                day_2 = (date.today() - timedelta(days=1)).isoformat()
                payloads = {
                    day_1: {"source": "garmin", "date": day_1, "stress": {"avgStressLevel": 31}},
                    day_2: {"source": "garmin", "date": day_2, "sleep": {"sleepTimeSeconds": 25200}},
                }
                with patch.object(main, "fetch_range", return_value=payloads):
                    stored = main.run_backfill(90)
                loaded = cache.load_cache()

        self.assertEqual(stored, 2)
        self.assertEqual(len(cache.history_list(loaded)), 2)
        self.assertIn(day_1, loaded)
        self.assertIn(day_2, loaded)

    def test_restart_persistence_after_backfill_and_push_no_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "cache.json")
            with patch.object(cache, "CACHE_FILE", cache_path):
                cache.upsert_day_snapshot("2026-02-10", {"source": "garmin", "date": "2026-02-10", "stress": {"avgStressLevel": 30}})
                cache.upsert_day_snapshot("2026-02-11", {"source": "garmin", "date": "2026-02-11", "sleep": {"sleepTimeSeconds": 25000}})
                cache.upsert_day_snapshot("2026-02-11", {"source": "garmin", "date": "2026-02-11", "rhr": {"restingHeartRate": 55}})
                reloaded, _meta = cache.load_cache_with_meta()

        self.assertEqual(cache.history_list(reloaded), ["2026-02-10", "2026-02-11"])

    def test_garmin_down_then_recover_keeps_history_and_facts_rich_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "cache.json")
            with patch.object(cache, "CACHE_FILE", cache_path), patch.dict(
                os.environ,
                {"GARMIN_EMAIL": "e", "GARMIN_PASSWORD": "p"},
                clear=False,
            ), patch.object(main, "current_day_key", return_value="2026-02-12"):
                with patch.object(main, "fetch_garmin_minimal", side_effect=RuntimeError("garmin down")):
                    with self.assertRaises(RuntimeError):
                        main.run_sync()
                failed_context = cache.build_day_context("2026-02-12", cache.load_cache())

                recovered = {
                    "source": "garmin",
                    "date": "2026-02-12",
                    "stress": {"avgStressLevel": 29},
                    "sleep": {"sleepTimeSeconds": 25600},
                    "body_battery": {"mostRecentValue": 66},
                    "rhr": {"restingHeartRate": 54},
                }
                with patch.object(main, "fetch_garmin_minimal", return_value=recovered):
                    main.run_sync()

                history = cache.load_cache()
                ok_context = cache.build_day_context("2026-02-12", history)
                facts = communication.build_push_message("midday", ok_context.get("snapshot"), "2026-02-12", mode="facts")

        self.assertEqual(failed_context["day_status"], "partial")
        self.assertEqual(len(cache.history_list(history)), 1)
        self.assertEqual(ok_context["day_status"], "ready")
        self.assertIn("По фактам", facts)

    def test_refresh_after_backfill_does_not_reduce_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "cache.json")
            with patch.object(cache, "CACHE_FILE", cache_path):
                days = [(date.today() - timedelta(days=delta)).isoformat() for delta in range(29, -1, -1)]
                payloads = {day: {"source": "garmin", "date": day, "stress": {"avgStressLevel": 31}} for day in days}
                with patch.object(main, "fetch_range", return_value=payloads):
                    main.run_backfill(90)
                before = len(cache.history_list(cache.load_cache()))
                latest_day = days[-1]
                cache.upsert_day_snapshot(latest_day, {"source": "garmin", "date": latest_day, "sleep": {"sleepTimeSeconds": 25000}})
                after = len(cache.history_list(cache.load_cache()))
        self.assertGreaterEqual(before, 30)
        self.assertGreaterEqual(after, before)


if __name__ == "__main__":
    unittest.main()

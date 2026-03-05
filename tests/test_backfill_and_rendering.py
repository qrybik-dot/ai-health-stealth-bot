import os
import tempfile
import unittest
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
        expected_facts_snapshot = (
            "📊 <b>По фактам (top-5)</b>\n"
            "• 🔋 Battery: <b>75 → 59</b>\n"
            "• 😵 Стресс: <b>43</b>\n"
            "• 🛌 Сон: <b>6ч 53м</b>\n"
            "• 🫀 RHR: <b>57</b>\n"
            "• 🚶 Шаги: <b>6200</b>\n\n"
            "🧾 <b>Остальное</b>\n"
            "• дыхание/SpO2/этажи/интенсивность/тренировки — показываем по мере прихода данных\n\n"
            "<b>Вывод:</b> держим ровный режим, без лишних ускорений."
        )

        self.assertEqual(facts, expected_facts_snapshot)
        self.assertIn("По фактам", facts)
        self.assertIn("Пожарь", roast)
        self.assertIn("Гипотеза", roast)
        self.assertNotEqual(facts, roast)

    def test_backfill_stores_days_and_history_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "cache.json")
            with patch.object(cache, "CACHE_FILE", cache_path):
                payloads = {
                    "2026-01-01": {"source": "garmin", "date": "2026-01-01", "stress": {"avgStressLevel": 31}},
                    "2026-01-02": {"source": "garmin", "date": "2026-01-02", "sleep": {"sleepTimeSeconds": 25200}},
                }
                with patch.object(main, "fetch_range", return_value=payloads):
                    stored = main.run_backfill(30)
                loaded = cache.load_cache()

        self.assertEqual(stored, 2)
        self.assertEqual(len(cache.history_list(loaded)), 2)
        self.assertIn("2026-01-01", loaded)
        self.assertIn("2026-01-02", loaded)

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


if __name__ == "__main__":
    unittest.main()

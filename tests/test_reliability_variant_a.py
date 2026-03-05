import datetime as dt
import os
import tempfile
import unittest
from unittest.mock import patch

import cache
import main
import communication
import color_engine


class ReliabilityVariantATests(unittest.TestCase):
    def test_dedup_registry_message_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(cache, "CACHE_FILE", os.path.join(tmp, "cache.json")):
                cache.mark_sent_record("c1", "2026-01-01", "morning", "color", "t1", "schedule", "r1")
                self.assertTrue(cache.was_sent_record("c1", "2026-01-01", "morning", "color"))
                self.assertFalse(cache.was_sent_record("c1", "2026-01-01", "morning", "verdict"))

    def test_weekly_formatter_available_days(self):
        now = dt.datetime(2026, 3, 8, 20, 0)
        for n in (1, 2, 6):
            history = {}
            for i in range(n):
                day = (now.date() - dt.timedelta(days=i)).isoformat()
                history[day] = {"sleep": {"sleepTimeSeconds": 7 * 3600}, "stress": {"avgStressLevel": 31}, "body_battery": {"mostRecentValue": 64}}
            derived = main.derive_weekly_status(main.collect_weekly_data(history, now))
            self.assertEqual(derived["available_days"], n)
            if n < 3:
                self.assertIn("черновик", derived["hero_status"].lower())

    def test_cyrillic_render_probe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = color_engine.render_cyrillic_probe("Проверка кириллицы", out_dir=tmp)
            self.assertTrue(os.path.exists(path))
            self.assertGreater(os.path.getsize(path), 1000)

    def test_metric_formatting_guards(self):
        chips = communication.build_data_chips({"rhr": {"restingHeartRate": 123456789}, "sleep": {"sleepTimeSeconds": 9999999}})
        text = " ".join(chips)
        self.assertNotIn("123456789", text)

    def test_manual_run_respects_dedup_registry(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "c"}, clear=False):
            with patch.object(main, "env", side_effect=lambda n: os.environ[n]):
                with patch.object(main, "_build_schedule_decision", return_value={
                    "now_msk": "2026-01-01T09:30:00+03:00",
                    "window_matched": "morning",
                    "slot_id": "morning",
                    "already_sent": True,
                    "target_chat_id": "c",
                    "date": "2026-01-01",
                }):
                    with patch.object(main, "telegram_send") as send_mock:
                        main.run_push("morning")
                        send_mock.assert_not_called()

    def test_callback_dedup_ttl_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(cache, "CACHE_FILE", os.path.join(tmp, "cache.json")):
                self.assertFalse(cache.callback_dedup_hit("c1", "cb-1", ttl_seconds=30))
                self.assertTrue(cache.callback_dedup_hit("c1", "cb-1", ttl_seconds=30))


if __name__ == "__main__":
    unittest.main()

import datetime as dt
import os
import tempfile
import unittest
from unittest.mock import patch

import cache
import communication
import main


class V1ReleaseUpdateTests(unittest.TestCase):
    def test_why_has_three_numeric_reasons(self):
        snapshot = {
            "body_battery": {"mostRecentValue": 64},
            "stress": {"avgStressLevel": 39},
            "sleep": {"sleepTimeSeconds": 25200},
        }
        msg = communication.build_why_message(snapshot)
        bullets = [line for line in msg.splitlines() if line.strip().startswith("•")]
        self.assertEqual(len(bullets), 3)
        self.assertTrue(any(any(ch.isdigit() for ch in line) for line in bullets))

    def test_mode_output_constraints(self):
        snapshot = {
            "body_battery": {"mostRecentValue": 64},
            "stress": {"avgStressLevel": 39},
            "sleep": {"sleepTimeSeconds": 25200},
        }
        short = communication.build_push_message("day", snapshot, "2026-01-01", mode="short")
        facts = communication.build_push_message("day", snapshot, "2026-01-01", mode="facts")
        roast = communication.build_push_message("day", snapshot, "2026-01-01", mode="roast")
        self.assertIn("По фактам", facts)
        self.assertTrue("Картоха" in roast or "Пюрешка" in roast)
        self.assertGreater(len(short), len(facts) - 20)

    def test_weekly_split_no_data_vs_partial(self):
        now = dt.datetime(2026, 1, 7, 10, 0)
        history = {
            "2026-01-07": {"sleep": {"sleepTimeSeconds": 25000}},
            "2026-01-06": {},
        }
        weekly = main.collect_weekly_data(history, now)
        derived = main.derive_weekly_status(weekly)
        self.assertGreaterEqual(derived["no_data_days"], 1)
        self.assertIn("черновик", derived["hero_status"].lower())

    def test_sent_registry_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(cache, "CACHE_FILE", os.path.join(tmp, "cache.json")):
                cache.mark_sent_record("c", "2026-01-01", "morning", "verdict", "ts", "schedule", "r1")
                cache.mark_sent_record("c", "2026-01-01", "morning", "verdict", "ts2", "schedule", "r2")
                registry = cache.get_today_sent_registry("c", "2026-01-01")
                self.assertEqual(len(registry), 1)


if __name__ == "__main__":
    unittest.main()

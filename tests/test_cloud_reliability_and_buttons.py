import os
import tempfile
import unittest
from unittest.mock import patch

import cache
import communication


class CloudReliabilityAndButtonsTests(unittest.TestCase):
    def test_auth_state_persistence_local_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(cache, "CACHE_FILE", os.path.join(tmp, "cache.json")):
                cache.upsert_garmin_auth_state({"tokenstore": "abc"}, chat_id="c1")
                got = cache.get_garmin_auth_state(chat_id="c1")
                self.assertEqual(got.get("tokenstore"), "abc")

    def test_why_contains_human_line_causes_and_lever(self):
        snapshot = {
            "body_battery": {"mostRecentValue": 61},
            "stress": {"avgStressLevel": 41},
            "sleep": {"sleepTimeSeconds": 25200},
            "rhr": {"restingHeartRate": 56},
        }
        msg = communication.build_why_message(snapshot)
        self.assertIn("Почему так", msg)
        self.assertIn("Причины", msg)
        self.assertIn("Рычаг", msg)
        bullets = [line for line in msg.splitlines() if line.strip().startswith("•")]
        self.assertGreaterEqual(len(bullets), 2)

    def test_facts_and_roast_are_contentful_without_mode_stub(self):
        snapshot = {
            "body_battery": {"mostRecentValue": 61, "chargedValue": 77},
            "stress": {"avgStressLevel": 41, "maxStressLevel": 78},
            "sleep": {"sleepTimeSeconds": 25200},
            "rhr": {"restingHeartRate": 56},
            "steps": {"totalSteps": 5300},
        }
        facts = communication.build_push_message("midday", snapshot, "2026-01-01", mode="facts")
        roast = communication.build_push_message("midday", snapshot, "2026-01-01", mode="roast")
        self.assertIn("top-5", facts.lower())
        self.assertIn("Остальное", facts)
        self.assertIn("Вывод", facts)
        self.assertIn("Пожарь", roast)
        self.assertIn("Факты", roast)
        self.assertNotIn("mode:", facts.lower())
        self.assertNotIn("mode:", roast.lower())


if __name__ == "__main__":
    unittest.main()

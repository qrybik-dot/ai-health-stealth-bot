import datetime as dt
import unittest

from communication import (
    build_day_verdict_message,
    build_push_message,
    build_verdict_label,
    should_send_visual_bonus,
    tone_violations,
)


class CommunicationSystemTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            "body_battery": {"mostRecentValue": 64, "chargedValue": 80},
            "stress": {"avgStressLevel": 34, "maxStressLevel": 90},
            "sleep": {"sleepTimeSeconds": 7 * 3600 + 1200},
            "rhr": {"restingHeartRate": 55},
        }

    def test_verdict_rotation_is_deterministic(self):
        label1 = build_verdict_label(self.snapshot, "2026-02-28", "morning")
        label2 = build_verdict_label(self.snapshot, "2026-02-28", "morning")
        label3 = build_verdict_label(self.snapshot, "2026-03-01", "morning")
        self.assertEqual(label1, label2)
        self.assertTrue(isinstance(label3, str) and label3)

    def test_day_verdict_compact_and_with_data_anchor(self):
        msg = build_day_verdict_message({"snapshot": self.snapshot, "day_status": "ready"}, "2026-02-28")
        self.assertIn("Вердикт дня", msg)
        self.assertIn("Battery", msg)
        self.assertLess(len(msg), 1200)
        self.assertEqual(tone_violations(msg), [])

    def test_push_has_single_humor_line_not_clowning(self):
        msg = build_push_message("midday", self.snapshot, "2026-02-28", partial=False)
        humor_markers = ["Ещё едет, но без понтов", "Мотор живой, но коробку лучше не рвать"]
        found = sum(1 for marker in humor_markers if marker in msg)
        self.assertEqual(found, 1)

    def test_visual_trigger_blocked_at_night(self):
        now = dt.datetime(2026, 2, 28, 2, 30)
        allowed = should_send_visual_bonus(now, "2026-02-28", {"snapshot": self.snapshot, "day_status": "ready"}, 0)
        self.assertFalse(allowed)

    def test_visual_trigger_weekly_limit(self):
        now = dt.datetime(2026, 2, 28, 12, 30)
        allowed = should_send_visual_bonus(now, "2026-02-28", {"snapshot": self.snapshot, "day_status": "ready"}, 2)
        self.assertFalse(allowed)


if __name__ == "__main__":
    unittest.main()

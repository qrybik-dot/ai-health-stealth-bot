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
            "steps": {"totalSteps": 7800},
            "hrv_status": {"status": "balanced"},
        }

    def test_verdict_rotation_is_deterministic(self):
        label1 = build_verdict_label(self.snapshot, "2026-02-28", "morning")
        label2 = build_verdict_label(self.snapshot, "2026-02-28", "morning")
        self.assertEqual(label1, label2)
        self.assertNotIn("болид", label1.lower())

    def test_day_verdict_compact_and_with_data_anchor(self):
        msg = build_day_verdict_message({"snapshot": self.snapshot, "day_status": "ready"}, "2026-02-28")
        self.assertIn("Итог дня", msg)
        self.assertIn("Факты", msg)
        self.assertLess(len(msg), 1300)
        self.assertEqual(tone_violations(msg), [])

    def test_push_has_no_gendered_forms(self):
        msg = build_push_message("midday", self.snapshot, "2026-02-28", partial=False)
        banned = ("рад", "рада", "спросил", "спросила")
        self.assertFalse(any(w in msg.lower() for w in banned))

    def test_midday_and_evening_avoid_sleep_chip_by_default(self):
        midday = build_push_message("midday", self.snapshot, "2026-02-28", partial=False)
        evening = build_push_message("evening", self.snapshot, "2026-02-28", partial=False)
        self.assertNotIn("😴 Сон", midday)
        self.assertNotIn("😴 Сон", evening)

    def test_slot_messages_use_different_metric_priorities(self):
        morning = build_push_message("morning", self.snapshot, "2026-02-28", partial=False)
        midday = build_push_message("midday", self.snapshot, "2026-02-28", partial=False)
        evening = build_push_message("evening", self.snapshot, "2026-02-28", partial=False)
        self.assertIn("😴 Сон", morning)
        self.assertIn("↕️ С утра", midday)
        self.assertIn("↕️ С утра", evening)

    def test_slot_messages_have_distinct_product_focus(self):
        morning = build_push_message("morning", self.snapshot, "2026-02-28", partial=False)
        midday = build_push_message("midday", self.snapshot, "2026-02-28", partial=False)
        evening = build_push_message("evening", self.snapshot, "2026-02-28", partial=False)
        self.assertIn("восстановление после сна", morning)
        self.assertIn("короткая коррекция курса", midday)
        self.assertIn("подготовка восстановления", evening)

    def test_slot_actions_adapt_to_state(self):
        stressed = dict(self.snapshot)
        stressed["stress"] = {"avgStressLevel": 68, "maxStressLevel": 92}
        low_steps = dict(self.snapshot)
        low_steps["steps"] = {"totalSteps": 900}
        short_sleep = dict(self.snapshot)
        short_sleep["sleep"] = {"sleepTimeSeconds": 5 * 3600}
        self.assertIn("короткий сон", build_push_message("morning", short_sleep, "2026-02-28"))
        self.assertIn("высокого стресса", build_push_message("midday", stressed, "2026-02-28"))
        self.assertIn("сброса", build_push_message("midday", low_steps, "2026-02-28"))

    def test_visual_trigger_blocked_at_night(self):
        now = dt.datetime(2026, 2, 28, 2, 30)
        allowed = should_send_visual_bonus(now, "2026-02-28", {"snapshot": self.snapshot, "day_status": "ready"}, 0)
        self.assertFalse(allowed)


if __name__ == "__main__":
    unittest.main()

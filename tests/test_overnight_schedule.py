import datetime as dt
import os
import unittest
from unittest.mock import Mock, patch

import main


class OvernightScheduleTest(unittest.TestCase):
    def decision(self, at, cron):
        with patch.dict(os.environ, {"PUSH_SCHEDULE_CRON": cron}):
            with patch.object(main, "_already_sent_for_slot", return_value=False):
                return main._build_schedule_decision(dt.datetime.fromisoformat(at), "chat")

    def test_scheduled_evening_after_midnight_is_rejected(self):
        for at in ("2026-10-20T00:25:04+03:00", "2026-10-20T08:30:00+03:00"):
            result = self.decision(at, "7 20 * * *")
            self.assertTrue(result["stale_schedule"])
            self.assertEqual(result["slot_id"], "evening")

    def test_valid_slots_still_accepted(self):
        for at, cron in (
            ("2026-10-19T20:07:00+03:00", "7 20 * * *"),
            ("2026-10-20T20:07:00+03:00", "7 20 * * *"),
            ("2026-10-20T09:30:00+03:00", "30 9 * * *"),
            ("2026-10-20T14:07:00+03:00", "7 14 * * *"),
        ):
            self.assertFalse(self.decision(at, cron)["stale_schedule"])

    def test_premature_evening_registry_not_blocking_real_slot(self):
        key = "2026-10-20|chat|evening|verdict"
        bad = {"ts": "2026-10-19T21:25:24Z", "trigger_source": "schedule"}
        good = {"ts": "2026-10-20T17:08:00Z", "trigger_source": "schedule"}
        manual = {"ts": "2026-10-19T21:25:24Z", "trigger_source": "manual"}
        with patch.object(main, "was_sent_record", return_value=True):
            with patch.object(main, "get_today_sent_registry", return_value={key: bad}):
                self.assertFalse(main._already_sent_for_slot("chat", "2026-10-20", "evening"))
                sender = Mock()
                with patch.object(main, "mark_sent_record") as mark:
                    self.assertTrue(main._send_once("token", "chat", "2026-10-20", "evening", "verdict", "schedule", "run", sender))
                sender.assert_called_once()
                mark.assert_called_once()
            with patch.object(main, "get_today_sent_registry", return_value={key: good}):
                self.assertTrue(main._already_sent_for_slot("chat", "2026-10-20", "evening"))
            with patch.object(main, "get_today_sent_registry", return_value={key: manual}):
                self.assertTrue(main._already_sent_for_slot("chat", "2026-10-20", "evening"))


if __name__ == "__main__":
    unittest.main()

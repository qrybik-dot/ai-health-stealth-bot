import datetime as dt
import unittest
from zoneinfo import ZoneInfo

import main
from communication import tone_violations


class PushMessageTests(unittest.TestCase):
    def _full_payload(self):
        return {
            "body_battery": {"mostRecentValue": 58, "chargedValue": 82},
            "stress": {"avgStressLevel": 48, "maxStressLevel": 83},
            "sleep": {"sleepTimeSeconds": 7 * 3600 + 1800},
            "rhr": {"restingHeartRate": 56},
            "steps": {"totalSteps": 6400},
            "respiration": {"avgWakingRespirationValue": 15.8},
            "pulse_ox": {"avgSpo2": 97},
            "hrv_status": {"status": "balanced"},
        }

    def test_morning_message_structure_full_data(self):
        msg = main._build_scheduled_message(
            slot="morning",
            today_payload=self._full_payload(),
            color_name="Лазурный",
            color_story_lines=["Спокойный акцент недели."],
            day="2026-01-14",
            today_vote=None,
        )
        self.assertIn("<b>Старт дня</b>", msg)
        self.assertIn("<b>Вердикт:</b>", msg)
        self.assertIn("<b>Факты:</b>", msg)
        self.assertIn("<b>Смысл:</b>", msg)
        self.assertIn("<b>Действие:</b>", msg)
        self.assertIn("<b>Лимит:</b>", msg)

    def test_slot_fact_priorities_are_distinct(self):
        payload = self._full_payload()
        morning = main._build_scheduled_message("morning", payload, "Лазурный", [], "2026-01-14", None)
        midday = main._build_scheduled_message("midday", payload, "Лазурный", [], "2026-01-14", None)
        evening = main._build_scheduled_message("evening", payload, "Лазурный", [], "2026-01-14", None)
        self.assertIn("Сон", morning)
        self.assertIn("С утра", midday)
        self.assertIn("Шаги", evening)
        self.assertLess(midday.count("Сон"), 2)
        self.assertLess(evening.count("Сон"), 2)

    def test_partial_data_variant_for_each_slot(self):
        partial_payload = {"body_battery": {"mostRecentValue": 55}}
        for slot in ("morning", "midday", "evening"):
            msg = main._build_scheduled_message(slot, partial_payload, "Лазурный", [], "2026-01-14", None)
            self.assertIn("данных пока мало", msg.lower())

    def test_push_tone_guardrails(self):
        msg = main._build_scheduled_message("morning", self._full_payload(), "Лазурный", [], "2026-01-14", None)
        self.assertEqual(tone_violations(msg), [])

    def test_slot_routing_windows(self):
        tz = ZoneInfo("Europe/Moscow")
        morning = dt.datetime(2026, 1, 14, 9, 20, tzinfo=tz)
        midday = dt.datetime(2026, 1, 14, 14, 0, tzinfo=tz)
        evening = dt.datetime(2026, 1, 14, 20, 10, tzinfo=tz)
        self.assertEqual(main._resolve_scheduled_push_kind(morning), "morning")
        self.assertEqual(main._resolve_scheduled_push_kind(midday), "midday")
        self.assertEqual(main._resolve_scheduled_push_kind(evening), "evening")


if __name__ == "__main__":
    unittest.main()

import datetime as dt
import unittest
from zoneinfo import ZoneInfo

import main
from communication import tone_violations


class PushMessageTests(unittest.TestCase):
    def _full_payload(self):
        return {
            "body_battery": {"mostRecentValue": 72, "chargedValue": 83},
            "stress": {"avgStressLevel": 32, "maxStressLevel": 88},
            "sleep": {"sleepTimeSeconds": 7 * 3600 + 1800},
            "rhr": {"restingHeartRate": 56},
        }

    def test_morning_message_structure_full_data(self):
        msg = main._build_scheduled_message(
            slot="morning",
            today_payload=self._full_payload(),
            color_name="Лазурный",
            color_story_lines=["Спокойный акцент недели.", "Подходит для ровного темпа."],
            day="2026-01-14",
            today_vote=None,
        )
        self.assertIn("<b>Вердикт утра</b>", msg)
        self.assertIn("<b>По фактам:</b>", msg)
        self.assertIn("<b>Что делать:</b>", msg)
        self.assertIn("Battery", msg)

    def test_midday_message_compact_and_with_anchor(self):
        msg = main._build_scheduled_message(
            slot="midday",
            today_payload=self._full_payload(),
            color_name="Лазурный",
            color_story_lines=[],
            day="2026-01-14",
            today_vote=None,
        )
        self.assertIn("<b>Вердикт середины дня</b>", msg)
        self.assertTrue(any(anchor in msg for anchor in ("Battery", "Стресс", "Сон")))
        self.assertLess(len(msg), 900)

    def test_evening_message_structure(self):
        msg = main._build_scheduled_message(
            slot="evening",
            today_payload=self._full_payload(),
            color_name="Лазурный",
            color_story_lines=[],
            day="2026-01-14",
            today_vote={"vote": "partial"},
        )
        self.assertIn("<b>Вердикт вечера</b>", msg)
        self.assertIn("<b>Смысл:</b>", msg)
        self.assertIn("<b>Чего не делать:</b>", msg)

    def test_partial_data_variant_for_each_slot(self):
        partial_payload = {
            "body_battery": {"mostRecentValue": 55},
            "stress": {},
            "sleep": None,
            "rhr": None,
        }
        for slot in ("morning", "midday", "evening"):
            msg = main._build_scheduled_message(
                slot=slot,
                today_payload=partial_payload,
                color_name="Лазурный",
                color_story_lines=[],
                day="2026-01-14",
                today_vote=None,
            )
            self.assertIn("Данных маловато", msg)
            self.assertIn("следующей синхронизации", msg)

    def test_missing_payload_fallback_message(self):
        msg = main._build_scheduled_message(
            slot="midday",
            today_payload=None,
            color_name="Лазурный",
            color_story_lines=[],
            day="2026-01-14",
            today_vote=None,
        )
        self.assertIn("Данных маловато", msg)
        self.assertNotIn("None", msg)

    def test_push_tone_guardrails(self):
        msg = main._build_scheduled_message(
            slot="morning",
            today_payload=self._full_payload(),
            color_name="Лазурный",
            color_story_lines=[],
            day="2026-01-14",
            today_vote=None,
        )
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

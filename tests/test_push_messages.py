import datetime as dt
import unittest
from zoneinfo import ZoneInfo

import main


class PushMessageTests(unittest.TestCase):
    def _full_payload(self):
        return {
            "body_battery": {"mostRecentValue": 72},
            "stress": {"avgStressLevel": 32},
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
        self.assertIn("Старт дня", msg)
        self.assertIn("Главное действие:", msg)
        self.assertIn("Надёжность оценки: высокая", msg)
        self.assertNotIn("(morning)", msg)

    def test_midday_message_is_course_correction(self):
        msg = main._build_scheduled_message(
            slot="midday",
            today_payload=self._full_payload(),
            color_name="Лазурный",
            color_story_lines=[],
            day="2026-01-14",
            today_vote=None,
        )
        self.assertIn("коррекция курса", msg.lower())
        self.assertIn("не повтор утренней оценки", msg.lower())
        self.assertNotIn("Цвет недели", msg)

    def test_evening_message_structure_and_vote(self):
        msg = main._build_scheduled_message(
            slot="evening",
            today_payload=self._full_payload(),
            color_name="Лазурный",
            color_story_lines=[],
            day="2026-01-14",
            today_vote={"vote": "partial"},
        )
        self.assertIn("Мягкое завершение дня", msg)
        self.assertIn("Твой отклик по дню: 🤷", msg)

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
            self.assertIn("предварительная оценка", msg)
            self.assertIn("данные неполные", msg)
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
        self.assertIn("предварительная оценка", msg)
        self.assertNotIn("None", msg)
        self.assertNotIn("null", msg)

    def test_slot_routing_windows(self):
        tz = ZoneInfo("Europe/Moscow")
        morning = dt.datetime(2026, 1, 14, 8, 20, tzinfo=tz)
        midday = dt.datetime(2026, 1, 14, 13, 0, tzinfo=tz)
        evening = dt.datetime(2026, 1, 14, 19, 10, tzinfo=tz)
        self.assertEqual(main._resolve_scheduled_push_kind(morning), "morning")
        self.assertEqual(main._resolve_scheduled_push_kind(midday), "midday")
        self.assertEqual(main._resolve_scheduled_push_kind(evening), "evening")


if __name__ == "__main__":
    unittest.main()

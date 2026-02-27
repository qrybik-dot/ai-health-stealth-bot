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
        self.assertIn("<b>Старт дня</b>", msg)
        self.assertIn("<b>Лучшее действие:</b>", msg)
        self.assertIn("<b>Надёжность:</b>", msg)
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
        self.assertNotIn("утренней оценки", msg.lower())
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
        self.assertIn("<b>Мягкое завершение дня</b>", msg)
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
            self.assertIn("<b>Ограничение:</b>", msg)
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


    def test_morning_color_caption_structure(self):
        caption = main.build_morning_color_caption(
            {
                "week_id": "2026-W03",
                "hex": "#DB66B4",
                "hsl": {"h": 300, "s": 62, "l": 63},
                "name_ru": "Пурпурно-красный",
                "rarity_level": "rare",
                "is_rare_name": False,
            }
        )
        self.assertIn("<b>Цвет дня</b>", caption)
        self.assertIn("<b>HEX:</b> #DB66B4", caption)
        self.assertIn("<b>Тема недели:</b> 2026-W03", caption)

    def test_slot_routing_windows(self):
        tz = ZoneInfo("Europe/Helsinki")
        morning = dt.datetime(2026, 1, 14, 9, 20, tzinfo=tz)
        midday = dt.datetime(2026, 1, 14, 14, 0, tzinfo=tz)
        evening = dt.datetime(2026, 1, 14, 20, 10, tzinfo=tz)
        self.assertEqual(main._resolve_scheduled_push_kind(morning), "morning")
        self.assertEqual(main._resolve_scheduled_push_kind(midday), "midday")
        self.assertEqual(main._resolve_scheduled_push_kind(evening), "evening")


if __name__ == "__main__":
    unittest.main()

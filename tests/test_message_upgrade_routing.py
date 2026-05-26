import unittest

import communication
import main


class MessageUpgradeRoutingTests(unittest.TestCase):
    def setUp(self):
        self.history = {
            "2026-01-11": {"body_battery": {"mostRecentValue": 70}, "stress": {"avgStressLevel": 30}},
            "2026-01-12": {
                "body_battery": {"mostRecentValue": 52, "chargedValue": 79},
                "stress": {"avgStressLevel": 47, "maxStressLevel": 85},
                "sleep": {"sleepTimeSeconds": 25200},
                "rhr": {"restingHeartRate": 57},
                "steps": {"totalSteps": 6800},
                "respiration": {"avgWakingRespirationValue": 15.2},
                "pulse_ox": {"avgSpo2": 96},
                "hrv_status": {"status": "balanced"},
            },
        }
        self.context = {
            "snapshot": self.history["2026-01-12"],
            "day_key": "2026-01-12",
            "day_status": "ready",
            "available_days": ["2026-01-11", "2026-01-12"],
            "available_days_count": 2,
            "available_metrics": ["sleep", "stress", "body_battery", "respiration", "pulse_ox", "steps", "rhr", "hrv"],
            "missing_metrics": [],
        }

    def test_structured_respiration_and_oxygen_answer(self):
        msg = main._route_structured_reply("дыхание и кислород?", self.context, self.history)
        self.assertIn("Дыхание и кислород", msg)
        self.assertIn("SpO₂", msg)
        self.assertNotIn("**", msg)

    def test_structured_data_availability_answer(self):
        msg = main._route_structured_reply("какие данные есть и за сколько дней?", self.context, self.history)
        self.assertIn("Доступные данные", msg)
        self.assertIn("История", msg)

    def test_button_outputs_are_distinct(self):
        snapshot = self.context["snapshot"]
        why = communication.build_why_message(snapshot)
        facts = communication.build_push_message("midday", snapshot, "2026-01-12", mode="facts")
        roast = communication.build_push_message("midday", snapshot, "2026-01-12", mode="roast")
        self.assertIn("Почему так", why)
        self.assertIn("По фактам", facts)
        self.assertIn("Пожарь", roast)
        self.assertNotEqual(why, facts)
        self.assertNotEqual(facts, roast)

    def test_no_markdown_artifacts_in_structured(self):
        msg = main._route_structured_reply("стресс", self.context, self.history)
        self.assertNotIn("**", msg)

    def test_direct_metric_question_is_answered_before_day_verdict(self):
        msg = main._route_structured_reply("как мой день и что с пульсом?", self.context, self.history)
        self.assertIn("Пульс", msg)
        self.assertNotIn("Вердикт", msg)

    def test_month_question_gets_period_summary(self):
        history = {}
        for i in range(20):
            day = f"2026-01-{i + 1:02d}"
            history[day] = {
                "body_battery": {"mostRecentValue": 45 + i},
                "stress": {"avgStressLevel": 55 - (i % 10)},
                "sleep": {"sleepTimeSeconds": (6 * 3600) + i * 300},
                "steps": {"totalSteps": 3000 + i * 250},
            }
        ctx = {"available_days": sorted(history.keys()), "available_days_count": len(history), "snapshot": history["2026-01-20"], "day_key": "2026-01-20", "day_status": "ready"}
        msg = main._route_structured_reply("как прошёл месяц", ctx, history)
        self.assertIn("Месяц", msg)
        self.assertIn("Лучший день", msg)
        self.assertIn("Сложный день", msg)
        self.assertIn("Фокус", msg)

    def test_food_question_is_grounded_without_gemini(self):
        msg = main._route_structured_reply("что мне лучше поесть?", self.context, self.history)
        self.assertIn("Еда сейчас", msg)
        self.assertIn("Факты", msg)
        self.assertNotIn("Вердикт", msg)

    def test_training_question_is_grounded_without_medical_claim(self):
        msg = main._route_structured_reply("можно ли тренироваться сегодня?", self.context, self.history)
        self.assertIn("Нагрузка", msg)
        self.assertIn("По режиму", msg)
        self.assertNotIn("диагноз", msg.lower())

    def test_now_15m_question_is_structured(self):
        msg = main._route_structured_reply("что сделать сейчас за 15 минут?", self.context, self.history)
        self.assertIn("Что делать за 15 минут", msg)
        self.assertIn("1)", msg)

    def test_sanitize_user_text_removes_markdown_artifacts(self):
        self.assertEqual(main._sanitize_user_text("**тест**"), "тест")
        self.assertEqual(main._sanitize_user_text("```код```"), "код")
        self.assertEqual(main._sanitize_user_text("# заголовок"), "заголовок")

if __name__ == "__main__":
    unittest.main()

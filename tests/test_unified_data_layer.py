import unittest

import main
from cache import build_day_context


class UnifiedDataLayerTests(unittest.TestCase):
    def test_day_context_counts_and_range(self):
        history = {
            "2026-01-13": {"sleep": {"sleepTimeSeconds": 25000}, "data_completeness": 0.3, "confidence": 0.5},
            "2026-01-14": {
                "sleep": {"sleepTimeSeconds": 26000},
                "stress": {"avgStressLevel": 29},
                "data_completeness": 0.5,
                "confidence": 0.65,
            },
        }
        ctx = build_day_context(day_key="2026-01-14", cache_data=history)
        self.assertEqual(ctx["available_days_count"], 2)
        self.assertEqual(ctx["available_days"], ["2026-01-13", "2026-01-14"])
        self.assertEqual(ctx["key_metrics_present_count"], 2)

    def test_metrics_availability_response_uses_fact_only(self):
        ctx = {
            "available_metrics": ["sleep", "stress"],
            "missing_metrics": ["body_battery", "rhr", "hrv"],
            "key_metrics_present_count": 2,
            "key_metrics_total_count": 4,
            "available_days_count": 3,
            "available_days": ["2026-01-10", "2026-01-11", "2026-01-12"],
        }
        msg = main._format_metrics_availability(ctx)
        self.assertIn("<b>History</b>", msg)
        self.assertIn("<b>Data groups</b>", msg)
        self.assertIn("сон, стресс", msg)
        self.assertIn("Body Battery", msg)

    def test_detailed_analysis_guard_partial(self):
        ctx = {
            "key_metrics_present_count": 1,
            "key_metrics_total_count": 4,
            "available_metrics": ["sleep"],
            "missing_metrics": ["body_battery", "rhr", "stress"],
            "snapshot": {"sleep": {"sleepTimeSeconds": 25200}},
            "day_status": "partial",
        }
        msg = main._format_detailed_analysis(ctx)
        self.assertIn("Ограничения", msg)
        self.assertIn("частичный", msg)

    def test_date_query_uses_exact_day_without_fallback(self):
        history = {
            "2026-02-28": {
                "sleep": {"sleepTimeSeconds": 26000},
                "stress": {"avgStressLevel": 28},
                "data_completeness": 0.6,
                "confidence": 0.7,
            }
        }
        with unittest.mock.patch.object(main, "_now_msk", return_value=main.dt.datetime(2026, 2, 28, 10, 0, tzinfo=main.TZ_MSK_FIXED)):
            today_ctx = main.build_day_context(cache_data=history)
            msg = main._route_structured_reply("данные за вчера", today_ctx, history)

        self.assertIn("Вердикт дня", msg)
        self.assertIn("Данных маловато", msg)

    def test_current_date_query_does_not_expand_to_health_summary(self):
        history = {
            "2026-02-28": {"sleep": {"sleepTimeSeconds": 25000}, "data_completeness": 0.4, "confidence": 0.6}
        }
        with unittest.mock.patch.object(main, "_now_msk", return_value=main.dt.datetime(2026, 2, 28, 8, 0, tzinfo=main.TZ_MSK_FIXED)):
            today_ctx = main.build_day_context(cache_data=history)
            msg = main._route_structured_reply("Сегодня какое число?", today_ctx, history)

        self.assertIn("2026-02-28", msg)
        self.assertNotIn("Метрики", msg)

    def test_history_answer_with_single_and_multiple_days(self):
        single = main._format_history_answer({"available_days": ["2026-01-14"], "available_days_count": 1})
        self.assertIn("<b>Доступно:</b> 1", single)
        multi = main._format_history_answer({"available_days": ["2026-01-13", "2026-01-14"], "available_days_count": 2})
        self.assertIn("2026-01-13 — 2026-01-14", multi)


if __name__ == "__main__":
    unittest.main()

import os
import tempfile
import unittest
from unittest.mock import patch

import cache
import main


class Stage2StabilityTests(unittest.TestCase):
    def test_gist_consistency_path_and_debug_explanation(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "cache.json")
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write('{"2026-02-27":{"last_sync_time":"2026-02-27T15:22:43Z","stress":{"avgStressLevel":33}}}')

            with patch.object(cache, "CACHE_FILE", cache_path), patch.object(main, "current_day_key", return_value="2026-02-27"), patch.dict(os.environ, {"CACHE_GIST_ID": "gist-id"}, clear=False):
                mock_resp = type("Resp", (), {"status_code": 200, "json": lambda self: {"files": {"cache.json": {"content": '{"2026-02-27":{"last_sync_time":"2026-02-27T12:00:00Z"}}'}}}})()
                with patch("cache.requests.get", return_value=mock_resp):
                    msg = main.build_debug_sync_message()

        self.assertIn("cache source: local_fresher_than_gist", msg)
        self.assertIn("source of truth note:", msg)

    def test_missing_fields_raw_vs_normalized_diagnostics(self):
        raw = {
            "source": "garmin",
            "date": "2026-02-27",
            "sleep": {"dailySleepDTO": {"sleepTimeSeconds": 25000, "calendarDate": "2026-02-26"}},
            "body_battery": {"bodyBatteryValuesArray": [[1, 74]]},
            "rhr": {"value": 56},
            "steps": [{"summary": {"steps": 5432}}],
        }
        trimmed = cache._trim_daily_snapshot(raw, "2026-02-27")
        diagnostics = trimmed["sync_diagnostics"]["metrics"]

        self.assertTrue(diagnostics["body_battery"]["raw_present"])
        self.assertTrue(diagnostics["body_battery"]["normalized_present"])
        self.assertTrue(diagnostics["rhr"]["normalized_present"])
        self.assertTrue(diagnostics["steps"]["normalized_present"])
        self.assertTrue(diagnostics["sleep"]["raw_present"])
        self.assertTrue(diagnostics["sleep"]["normalized_present"])
        self.assertEqual(diagnostics["sleep"]["reason"], "date_mismatch")

    def test_noop_refresh_merge_stability(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "cache.json")
            with patch.object(cache, "CACHE_FILE", cache_path):
                day = "2026-02-27"
                first = cache.upsert_day_snapshot(day, {
                    "source": "garmin",
                    "date": day,
                    "sleep": {"sleepTimeSeconds": 25000},
                    "stress": {"avgStressLevel": 30},
                })
                second = cache.upsert_day_snapshot(day, {
                    "source": "garmin",
                    "date": day,
                    "sleep": {},
                    "stress": {"avgStressLevel": 30},
                })
        self.assertEqual(first["sleep"], second["sleep"])
        self.assertGreaterEqual(second["data_completeness"], first["data_completeness"])

    def test_partial_update_adds_new_block_without_wipe(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "cache.json")
            with patch.object(cache, "CACHE_FILE", cache_path):
                day = "2026-02-27"
                base = cache.upsert_day_snapshot(day, {
                    "source": "garmin",
                    "date": day,
                    "stress": {"avgStressLevel": 34},
                    "rhr": {"restingHeartRate": 55},
                })
                merged = cache.upsert_day_snapshot(day, {
                    "source": "garmin",
                    "date": day,
                    "sleep": {"sleepTimeSeconds": 26000},
                })
        self.assertIn("sleep", merged)
        self.assertEqual(merged["stress"], base["stress"])
        self.assertEqual(merged["rhr"], base["rhr"])

    def test_push_consistency_uses_same_snapshot_readiness(self):
        day = "2026-02-27"
        snapshot = {
            "stress": {"avgStressLevel": 30},
            "sleep": {},
            "body_battery": {},
            "rhr": {},
            "missing_flags": {"sleep": True, "body_battery": True, "rhr": True, "stress": False},
        }
        context = cache.build_day_context(day_key=day, cache_data={day: snapshot})
        quality = main._evaluate_data_quality(snapshot)
        self.assertEqual(context["key_metrics_present_count"], quality["present"])
        self.assertEqual(set(context["missing_metrics"]) & set(cache.KEY_METRICS), set(quality["missing_metrics"]))

    def test_restart_persistence_local_cache_survives_new_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "cache.json")
            with patch.object(cache, "CACHE_FILE", cache_path), patch.dict(os.environ, {}, clear=False):
                day = "2026-02-27"
                cache.upsert_day_snapshot(day, {
                    "source": "garmin",
                    "date": day,
                    "stress": {"avgStressLevel": 31},
                })
                loaded, meta = cache.load_cache_with_meta()
        self.assertIn(day, loaded)
        self.assertEqual(meta["source"], "local")


if __name__ == "__main__":
    unittest.main()

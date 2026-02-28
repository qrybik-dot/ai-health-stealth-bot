import datetime as dt
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import cache
import main


class PipelineReliabilityTests(unittest.TestCase):
    def test_incremental_merge_updates_blocks_and_quality_growth(self):
        before = {
            "sleep": {"sleepTimeSeconds": 25000.0},
            "data_completeness": 0.29,
            "confidence": 0.52,
        }
        after = {
            "sleep": {"sleepTimeSeconds": 25000.0},
            "stress": {"avgStressLevel": 32.0},
            "body_battery": {"mostRecentValue": 61.0},
            "data_completeness": 0.57,
            "confidence": 0.69,
        }
        diff = cache.build_snapshot_merge_diff(before, after)
        self.assertIn("stress", diff["updated_blocks"])
        self.assertIn("body_battery", diff["updated_blocks"])
        self.assertGreater(diff["new_completeness"], diff["old_completeness"])
        self.assertGreater(diff["new_confidence"], diff["old_confidence"])

    def test_noop_refresh_message_when_no_updates(self):
        message = main.build_refresh_result_message(
            {
                "updated_blocks": [],
                "after": {"missing_flags": {}},
            }
        )
        self.assertIn("Данные уже актуальны", message)

    def test_partial_update_message_when_one_block_changed(self):
        message = main.build_refresh_result_message(
            {
                "updated_blocks": ["sleep"],
                "after": {"missing_flags": {"hrv": True, "stress": True}},
            }
        )
        self.assertIn("частично", message)
        self.assertIn("сон", message)

    def test_cache_source_fallback_to_local_on_gist_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "cache.json")
            with open(cache_path, "w", encoding="utf-8") as f:
                f.write('{"2026-01-14":{"sleep":{"sleepTimeSeconds":24000}}}')
            with patch.object(cache, "CACHE_FILE", cache_path):
                with patch.dict(os.environ, {"CACHE_GIST_ID": "x"}, clear=False):
                    mock_resp = Mock()
                    mock_resp.status_code = 404
                    with patch("cache.requests.get", return_value=mock_resp):
                        data, meta = cache.load_cache_with_meta()
        self.assertTrue(meta["available"])
        self.assertEqual(meta["source"], "local_fallback")
        self.assertIn("2026-01-14", data)

    def test_push_timing_and_manual_not_breaking_scheduled(self):
        tz = dt.timezone(dt.timedelta(hours=3))
        now = dt.datetime(2026, 1, 14, 9, 20, tzinfo=tz)
        self.assertEqual(main._resolve_scheduled_push_kind(now), "morning")
        # /refresh command path does not mark push slot sent; verify with cache state
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "cache.json")
            with patch.object(cache, "CACHE_FILE", cache_path):
                self.assertFalse(cache.was_slot_sent("chat", "2026-01-14", "morning"))

    def test_delayed_data_catchup_without_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "cache.json")
            with patch.object(cache, "CACHE_FILE", cache_path):
                with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "c"}, clear=False):
                    with patch.object(main, "env", side_effect=lambda n: os.environ[n]):
                        with patch.object(main, "prune_cache", return_value={}):
                            with patch.object(main, "get_or_create_weekly_color_state", return_value={"week_id": "2026-W03", "hex": "#112233", "name_ru": "Тест", "rarity_level": "common", "hsl": {"h": 1, "s": 2, "l": 3}, "is_rare_name": False}):
                                with patch.object(main, "build_color_story", return_value="x\nline"):
                                    with patch.object(main, "generate_today_card_image", return_value="tests/fake.png"):
                                        with patch.object(main, "generate_color_card_image", return_value="tests/fake.png"):
                                            with patch.object(main, "telegram_send"):
                                                with patch.object(main, "telegram_send_photo_with_markup"):
                                                    # morning partial -> mark morning_deferred
                                                    with patch.object(main, "_now_msk", return_value=dt.datetime(2026, 1, 14, 9, 20, tzinfo=dt.timezone(dt.timedelta(hours=3)))):
                                                        with patch.object(main, "load_cache_with_meta", return_value=({"2026-01-14": {"sleep": {}, "stress": {}, "body_battery": {}, "rhr": {}, "missing_flags": {"sleep": True}}}, {"source": "local", "available": True, "error": ""})):
                                                            main.run_push("scheduled")
                                                    self.assertTrue(cache.was_slot_sent("c", "2026-01-14", "morning_deferred"))
                                                    self.assertFalse(cache.was_slot_sent("c", "2026-01-14", "morning"))
                                                    # midday with full data -> catch-up morning
                                                    with patch.object(main, "_now_msk", return_value=dt.datetime(2026, 1, 14, 14, 0, tzinfo=dt.timezone(dt.timedelta(hours=3)))):
                                                        with patch.object(main, "load_cache_with_meta", return_value=({"2026-01-14": {"sleep": {"sleepTimeSeconds": 24000}, "stress": {"avgStressLevel": 31}, "body_battery": {"mostRecentValue": 64}, "rhr": {"restingHeartRate": 55}, "missing_flags": {"sleep": False, "stress": False, "body_battery": False, "rhr": False}}}, {"source": "local", "available": True, "error": ""})):
                                                            main.run_push("scheduled")
                                                    self.assertTrue(cache.was_slot_sent("c", "2026-01-14", "morning"))


    def test_gist_and_local_history_are_merged_by_day_keys(self):
        gist_cache = {
            "2026-02-28": {"sleep": {"sleepTimeSeconds": 26000}, "last_sync_time": "2026-02-28T08:00:00Z"},
            "_weekly_state": {"x": 1},
        }
        local_cache = {
            "2026-02-27": {"sleep": {"sleepTimeSeconds": 25000}, "last_sync_time": "2026-02-27T08:00:00Z"},
            "_today_votes": {"a": 1},
        }
        merged = cache._merge_runtime_cache(gist_cache, local_cache)
        self.assertIn("2026-02-27", merged)
        self.assertIn("2026-02-28", merged)
        self.assertIn("_today_votes", merged)
        self.assertEqual(merged["_today_votes"], {"a": 1})
    def test_weekly_stability_fingerprint_same_without_new_source(self):
        now = dt.datetime(2026, 1, 18, 20, 0)
        history = {
            (now.date() - dt.timedelta(days=1)).isoformat(): {
                "sleep": {"sleepTimeSeconds": 25000},
                "stress": {"avgStressLevel": 35},
                "body_battery": {"mostRecentValue": 60},
                "rhr": {"restingHeartRate": 56},
                "last_sync_time": "2026-01-17T20:00:00Z",
                "fetched_at_utc": "2026-01-17T20:01:00Z",
            }
        }
        p1 = main.build_weekly_payload(history, now, "c")
        p2 = main.build_weekly_payload(history, now, "c")
        self.assertEqual(p1["source_fingerprint"], p2["source_fingerprint"])


if __name__ == "__main__":
    unittest.main()

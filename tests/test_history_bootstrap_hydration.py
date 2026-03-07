import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import patch

import cache
import main


class _FakeFirestoreStore:
    def __init__(self, days=None, enabled=True):
        self.enabled = enabled
        self.days = days or {}

    def list_days(self, chat_id: str, limit: int = 90, descending: bool = True):
        keys = sorted(self.days.keys(), reverse=descending)[:limit]
        return {k: dict(self.days[k]) for k in keys}

    def get_day(self, chat_id: str, day_key: str):
        return dict(self.days.get(day_key, {}))

    def upsert_day(self, chat_id: str, day_key: str, payload):
        self.days[day_key] = dict(payload)

    def get_sent(self, chat_id: str, key: str):
        return None

    def set_sent(self, chat_id: str, key: str, payload):
        return None

    def get_auth(self, chat_id: str, provider: str = "garmin"):
        return {}

    def set_auth(self, chat_id: str, payload, provider: str = "garmin"):
        return None


def _make_days(count: int):
    start = date(2026, 1, 1)
    out = {}
    for i in range(count):
        day = (start + timedelta(days=i)).isoformat()
        out[day] = {"source": "garmin", "date": day, "stress": {"avgStressLevel": 30 + (i % 10)}}
    return out


class HistoryBootstrapHydrationTests(unittest.TestCase):
    def test_startup_empty_local_with_firestore_90_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "cache.json")
            remote_days = _make_days(90)
            fake_store = _FakeFirestoreStore(days=remote_days, enabled=True)
            with patch.object(cache, "CACHE_FILE", cache_path), patch.object(cache, "FIRESTORE", fake_store):
                loaded = cache.load_cache()
        self.assertEqual(len(cache.history_list(loaded)), 90)

    def test_startup_local_3_days_and_firestore_90_days_hydrates_to_90(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "cache.json")
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(_make_days(3), f)
            fake_store = _FakeFirestoreStore(days=_make_days(90), enabled=True)
            with patch.object(cache, "CACHE_FILE", cache_path), patch.object(cache, "FIRESTORE", fake_store):
                loaded = cache.load_cache()
        self.assertEqual(len(cache.history_list(loaded)), 90)

    def test_startup_no_firestore_with_local_3_days_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "cache.json")
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(_make_days(3), f)
            fake_store = _FakeFirestoreStore(days={}, enabled=False)
            with patch.object(cache, "CACHE_FILE", cache_path), patch.object(cache, "FIRESTORE", fake_store):
                loaded = cache.load_cache()
        self.assertEqual(len(cache.history_list(loaded)), 3)

    def test_bootstrap_backfill_runs_when_history_less_than_90(self):
        with patch.object(main, "load_cache", return_value=_make_days(3)), patch.object(main, "history_list", return_value=sorted(_make_days(3).keys())), patch.object(main, "get_bootstrap_state", return_value={}), patch.object(main, "run_backfill", return_value=90) as mocked_backfill, patch.object(main, "upsert_bootstrap_state"):
            result = main.ensure_history_bootstrap(target_days=90)
        self.assertTrue(result["backfill_triggered"])
        mocked_backfill.assert_called_once_with(90)

    def test_bootstrap_backfill_not_rerun_when_history_is_ready(self):
        days = _make_days(90)
        with patch.object(main, "load_cache", return_value=days), patch.object(main, "history_list", return_value=sorted(days.keys())), patch.object(main, "run_backfill") as mocked_backfill, patch.object(main, "upsert_bootstrap_state"):
            result = main.ensure_history_bootstrap(target_days=90)
        self.assertFalse(result["backfill_triggered"])
        mocked_backfill.assert_not_called()

    def test_refresh_after_bootstrap_does_not_reduce_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "cache.json")
            fake_store = _FakeFirestoreStore(days=_make_days(90), enabled=True)
            with patch.object(cache, "CACHE_FILE", cache_path), patch.object(cache, "FIRESTORE", fake_store):
                before = len(cache.history_list(cache.load_cache()))
                cache.upsert_day_snapshot("2026-03-31", {"source": "garmin", "date": "2026-03-31", "sleep": {"sleepTimeSeconds": 25000}})
                after = len(cache.history_list(cache.load_cache()))
        self.assertGreaterEqual(after, before)

    def test_compare_and_history_functions_see_hydrated_90_days(self):
        remote_days = _make_days(90)
        fake_store = _FakeFirestoreStore(days=remote_days, enabled=True)
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "cache.json")
            with patch.object(cache, "CACHE_FILE", cache_path), patch.object(cache, "FIRESTORE", fake_store):
                history = cache.load_cache()
                ctx = cache.build_day_context(day_key="2026-03-31", cache_data=history)
                msg = main._route_structured_reply("сравни дни", ctx, history)
        self.assertEqual(ctx["available_days_count"], 90)
        self.assertIn("Сравнение", msg)

    def test_restart_after_local_reset_recovers_from_firestore(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "cache.json")
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(_make_days(2), f)
            fake_store = _FakeFirestoreStore(days=_make_days(90), enabled=True)
            with patch.object(cache, "CACHE_FILE", cache_path), patch.object(cache, "FIRESTORE", fake_store):
                first = cache.load_cache()
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump({}, f)
                second = cache.load_cache()
        self.assertEqual(len(cache.history_list(first)), 90)
        self.assertEqual(len(cache.history_list(second)), 90)


if __name__ == "__main__":
    unittest.main()

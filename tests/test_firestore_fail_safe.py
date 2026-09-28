import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cache


class _FailingFirestore:
    enabled = True

    def get_day(self, *args, **kwargs):
        raise RuntimeError("firestore unavailable")

    def upsert_day(self, *args, **kwargs):
        raise RuntimeError("firestore unavailable")

    def get_sent(self, *args, **kwargs):
        raise RuntimeError("firestore unavailable")

    def set_sent(self, *args, **kwargs):
        raise RuntimeError("firestore unavailable")

    def get_auth(self, *args, **kwargs):
        raise RuntimeError("firestore unavailable")

    def set_auth(self, *args, **kwargs):
        raise RuntimeError("firestore unavailable")


class FirestoreFailSafeTests(unittest.TestCase):
    def test_day_snapshot_survives_firestore_failure_locally(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            cache_path.write_text("{}", encoding="utf-8")
            with patch.object(cache, "CACHE_FILE", str(cache_path)), patch.object(cache, "FIRESTORE", _FailingFirestore()):
                result = cache.upsert_day_snapshot(
                    "2026-09-28",
                    {"source": "garmin", "date": "2026-09-28", "sleep": {"sleepTimeSeconds": 25000}},
                )
            persisted = json.loads(cache_path.read_text(encoding="utf-8"))
        self.assertIn("2026-09-28", persisted)
        self.assertEqual(result["date"], "2026-09-28")

    def test_push_state_survives_firestore_failure_locally(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            cache_path.write_text("{}", encoding="utf-8")
            with patch.object(cache, "CACHE_FILE", str(cache_path)), patch.object(cache, "FIRESTORE", _FailingFirestore()):
                cache.mark_sent_record(
                    chat_id="chat",
                    send_date="2026-09-28",
                    slot="morning",
                    message_type="verdict",
                    sent_ts="2026-09-28T06:30:00Z",
                    trigger_source="test",
                    run_id="run",
                )
                self.assertTrue(cache.was_sent_record("chat", "2026-09-28", "morning", "verdict"))

    def test_auth_state_survives_firestore_failure_locally(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "cache.json"
            cache_path.write_text("{}", encoding="utf-8")
            with patch.object(cache, "CACHE_FILE", str(cache_path)), patch.object(cache, "FIRESTORE", _FailingFirestore()):
                cache.upsert_garmin_auth_state({"tokenstore": "present"}, chat_id="chat")
                result = cache.get_garmin_auth_state(chat_id="chat")
        self.assertEqual(result.get("tokenstore"), "present")


if __name__ == "__main__":
    unittest.main()

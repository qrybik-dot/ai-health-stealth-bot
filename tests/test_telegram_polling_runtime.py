import os
import tempfile
import unittest
from unittest.mock import patch

import cache
import main


class TelegramPollingRuntimeTests(unittest.TestCase):
    def test_poll_state_roundtrip_local_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(cache, "CACHE_FILE", os.path.join(tmp, "cache.json")):
                saved = cache.upsert_telegram_poll_state(
                    {
                        "offset": 124,
                        "last_update_id": 123,
                        "last_poll_ts": "2026-01-01T00:00:00Z",
                        "processed_count": 2,
                        "last_error": "",
                    }
                )
                self.assertEqual(saved["offset"], 124)
                self.assertEqual(cache.get_telegram_poll_state()["last_update_id"], 123)

    def test_process_help_update_sends_help(self):
        sent = []
        with patch.object(main, "_send_typing_action"), patch.object(main, "telegram_send") as send:
            send.side_effect = lambda _token, chat_id, text, parse_mode=None: sent.append((chat_id, text, parse_mode))
            result = main.process_telegram_update(
                {"update_id": 1, "message": {"text": "/help", "chat": {"id": "c1"}}},
                tg_token="token",
                default_chat_id="default",
            )
        self.assertEqual(result["action"], "help")
        self.assertEqual(sent[0][0], "c1")
        self.assertIn("/today", sent[0][1])

    def test_process_debug_sync_update_sends_debug_text(self):
        sent = []
        with patch.object(main, "_send_typing_action"), \
             patch.object(main, "build_debug_sync_message", return_value="Debug sync:"), \
             patch.object(main, "telegram_send") as send:
            send.side_effect = lambda _token, chat_id, text, parse_mode=None: sent.append((chat_id, text, parse_mode))
            result = main.process_telegram_update(
                {"update_id": 2, "message": {"text": "/debug_sync", "chat": {"id": "c1"}}},
                tg_token="token",
                default_chat_id="default",
            )
        self.assertEqual(result["action"], "debug_sync")
        self.assertEqual(sent[0][1], "Debug sync:")

    def test_process_backfill_rejects_non_admin(self):
        sent = []
        with patch.dict(os.environ, {"TELEGRAM_CHAT_ID": "owner", "ADMIN_CHAT_IDS": ""}, clear=False), \
             patch.object(main, "_send_typing_action"), \
             patch.object(main, "run_backfill") as backfill, \
             patch.object(main, "telegram_send") as send:
            send.side_effect = lambda _token, chat_id, text, parse_mode=None: sent.append((chat_id, text, parse_mode))
            result = main.process_telegram_update(
                {"update_id": 3, "message": {"text": "/backfill 7", "chat": {"id": "guest"}}},
                tg_token="token",
                default_chat_id="default",
            )
        self.assertEqual(result["action"], "backfill_denied")
        self.assertFalse(backfill.called)
        self.assertIn("только владельцу", sent[0][1])

    def test_free_text_uses_structured_reply_before_gemini(self):
        sent = []
        history = {main.current_day_key(): {"body_battery": {"mostRecentValue": 60}}}
        with patch.object(main, "_send_typing_action"), \
             patch.object(main, "load_cache", return_value=history), \
             patch.object(main, "generate_chat_message", side_effect=AssertionError("Gemini should not be called")), \
             patch.object(main, "telegram_send") as send:
            send.side_effect = lambda _token, chat_id, text, parse_mode=None: sent.append((chat_id, text, parse_mode))
            result = main.process_telegram_update(
                {"update_id": 4, "message": {"text": "Сегодня какое число?", "chat": {"id": "c1"}}},
                tg_token="token",
                default_chat_id="default",
            )
        self.assertEqual(result["action"], "structured_reply")
        self.assertIn("Сегодня", sent[0][1])

    def test_callback_facts_sends_facts_message(self):
        sent = []
        snapshot = {
            "body_battery": {"mostRecentValue": 61, "chargedValue": 77},
            "stress": {"avgStressLevel": 41},
            "sleep": {"sleepTimeSeconds": 25200},
            "rhr": {"restingHeartRate": 56},
        }
        with patch.object(main, "callback_dedup_hit", return_value=False), \
             patch.object(main, "get_day_summary", return_value={"snapshot": snapshot, "completeness_state": "FULL"}), \
             patch.object(main, "telegram_send") as send:
            send.side_effect = lambda _token, chat_id, text, parse_mode=None: sent.append((chat_id, text, parse_mode))
            result = main.process_telegram_update(
                {
                    "update_id": 5,
                    "callback_query": {
                        "id": "cb1",
                        "data": "facts:midday:2026-01-01",
                        "message": {"message_id": 10, "chat": {"id": "c1"}},
                    },
                },
                tg_token="token",
                default_chat_id="default",
            )
        self.assertEqual(result["action"], "facts")
        self.assertIn("top-5", sent[0][1].lower())

    def test_duplicate_callback_is_skipped(self):
        answered = []
        with patch.object(main, "callback_dedup_hit", return_value=True), \
             patch.object(main, "telegram_answer_callback") as answer, \
             patch.object(main, "telegram_send") as send:
            answer.side_effect = lambda _token, callback_id, text="": answered.append((callback_id, text))
            result = main.process_telegram_update(
                {
                    "update_id": 6,
                    "callback_query": {
                        "id": "cb1",
                        "data": "facts:midday:2026-01-01",
                        "message": {"message_id": 10, "chat": {"id": "c1"}},
                    },
                },
                tg_token="token",
                default_chat_id="default",
            )
        self.assertEqual(result["action"], "dedupe_skip")
        self.assertFalse(send.called)
        self.assertEqual(answered[0][0], "cb1")

    def test_poll_once_empty_updates_keeps_offset(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(cache, "CACHE_FILE", os.path.join(tmp, "cache.json")), \
                 patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_CHAT_ID": "c1"}, clear=False), \
                 patch.object(main, "telegram_get_updates", return_value=[]):
                cache.upsert_telegram_poll_state({"offset": 50})
                main.run_poll_once()
                self.assertEqual(cache.get_telegram_poll_state()["offset"], 50)

    def test_poll_once_advances_offset_after_handler_error(self):
        updates = [
            {"update_id": 10, "message": {"text": "/help", "chat": {"id": "c1"}}},
            {"update_id": 11, "message": {"text": "/help", "chat": {"id": "c1"}}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(cache, "CACHE_FILE", os.path.join(tmp, "cache.json")), \
                 patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_CHAT_ID": "c1"}, clear=False), \
                 patch.object(main, "telegram_get_updates", return_value=updates), \
                 patch.object(main, "process_telegram_update", side_effect=[RuntimeError("boom"), {"action": "help"}]):
                main.run_poll_once()
                state = cache.get_telegram_poll_state()
                self.assertEqual(state["offset"], 12)
                self.assertEqual(state["last_update_id"], 11)

    def test_chat_poll_workflow_contains_required_runtime_contract(self):
        with open(".github/workflows/chat_poll.yml", "r", encoding="utf-8") as f:
            workflow = f.read()
        self.assertIn('cron: "*/5 * * * *"', workflow)
        self.assertIn("python main.py poll-once", workflow)
        self.assertIn("python scripts/gist_upload.py", workflow)
        for secret_name in (
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_CHAT_ID",
            "GEMINI_API_KEY",
            "GEMINI_MODEL",
            "CACHE_GIST_ID",
            "GIST_TOKEN",
            "GARMIN_EMAIL",
            "GARMIN_PASSWORD",
        ):
            self.assertIn(secret_name, workflow)


if __name__ == "__main__":
    unittest.main()

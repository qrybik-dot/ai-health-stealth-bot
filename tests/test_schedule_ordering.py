import datetime as dt
import os
import tempfile
import unittest
from unittest.mock import patch

import cache
import main


class ScheduleOrderingTests(unittest.TestCase):
    def test_morning_color_then_verdict_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(cache, "CACHE_FILE", os.path.join(tmp, "cache.json")):
                history = {
                    "2026-01-14": {
                        "sleep": {"sleepTimeSeconds": 25000},
                        "stress": {"avgStressLevel": 31},
                        "body_battery": {"mostRecentValue": 68},
                        "rhr": {"restingHeartRate": 55},
                        "missing_flags": {"sleep": False, "stress": False, "body_battery": False, "rhr": False},
                    }
                }
                calls = []
                def fake_photo(*args, **kwargs):
                    calls.append(args[2])
                with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "c"}, clear=False):
                    with patch.object(main, "env", side_effect=lambda n: os.environ[n]):
                        with patch.object(main, "prune_cache", return_value={}):
                            with patch.object(main, "_now_msk", return_value=dt.datetime(2026, 1, 14, 9, 20, tzinfo=dt.timezone(dt.timedelta(hours=3)))):
                                with patch.object(main, "load_cache_with_meta", return_value=(history, {"source": "local", "available": True, "error": ""})):
                                    with patch.object(main, "get_or_create_weekly_color_state", return_value={"week_id": "2026-W03", "hex": "#112233", "name_ru": "Тест", "rarity_level": "common", "hsl": {"h": 1, "s": 2, "l": 3}, "is_rare_name": False}):
                                        with patch.object(main, "telegram_send_photo_with_markup", side_effect=fake_photo):
                                            with patch.object(main, "telegram_send"):
                                                with patch.object(main, "generate_color_card_image", return_value="generated/color.png"):
                                                    with patch.object(main, "_state_to_asset", return_value="assets/coach_states/cruise.png"):
                                                        with patch("os.path.exists", return_value=True):
                                                            main.run_push("scheduled")
                                                            main.run_push("scheduled")
                self.assertEqual(calls, ["generated/color.png", "assets/coach_states/cruise.png"])


if __name__ == "__main__":
    unittest.main()

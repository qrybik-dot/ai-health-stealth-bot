import datetime as dt
import os
import tempfile
import unittest
from unittest.mock import patch

import cache


class SnapshotMergeTests(unittest.TestCase):
    def test_save_daily_snapshot_merges_and_recalculates(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = os.path.join(tmp, "cache.json")
            with patch.object(cache, "CACHE_FILE", cache_path):
                base = {
                    "source": "garmin",
                    "date": dt.date.today().isoformat(),
                    "fetched_at_utc": "2026-01-14T06:00:00Z",
                    "sleep": {"sleepTimeSeconds": 7 * 3600},
                    "rhr": {"restingHeartRate": 56},
                }
                cache.save_daily_snapshot(base)

                patch_data = {
                    "source": "garmin",
                    "date": dt.date.today().isoformat(),
                    "fetched_at_utc": "2026-01-14T07:00:00Z",
                    "sleep": None,
                    "hrv": {"lastNightAvg": 44},
                    "steps": {"totalSteps": 4000},
                }
                cache.save_daily_snapshot(patch_data)
                today = dt.date.today().isoformat()
                day = cache.load_cache()[today]

                self.assertIn("sleep", day)
                self.assertEqual(day["sleep"]["sleepTimeSeconds"], float(7 * 3600))
                self.assertIn("hrv", day)
                self.assertFalse(day["missing_flags"]["sleep"])
                self.assertFalse(day["missing_flags"]["steps"])
                self.assertGreater(day["data_completeness"], 0.4)
                self.assertGreater(day["confidence"], 0.5)


if __name__ == "__main__":
    unittest.main()

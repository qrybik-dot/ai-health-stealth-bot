import unittest

from scripts import gist_archive


class _FakeClient:
    def __init__(self):
        self.gist_id = "archive"
        self.files = {}
        self.writes = []

    def find_archive(self):
        return self.gist_id

    def ensure_archive(self):
        return self.gist_id

    def read_json(self, _gist_id, filename):
        value = self.files.get(filename, {})
        return gist_archive.deep_merge_preserve({}, value)

    def write_json(self, _gist_id, filename, payload):
        self.files[filename] = gist_archive.deep_merge_preserve({}, payload)
        self.writes.append(filename)


class GistArchiveTests(unittest.TestCase):
    def test_collect_days_ignores_runtime_state(self):
        payload = {
            "2026-09-01": {"source": "garmin"},
            "2026-09-02": {"source": "garmin"},
            "_push_state": {"x": 1},
            "junk": {},
        }
        self.assertEqual(
            sorted(gist_archive.collect_days(payload)),
            ["2026-09-01", "2026-09-02"],
        )

    def test_group_by_month_partitions_history(self):
        days = {
            "2026-08-31": {"a": 1},
            "2026-09-01": {"a": 2},
            "2026-09-02": {"a": 3},
        }
        grouped = gist_archive.group_by_month(days)
        self.assertEqual(sorted(grouped), ["garmin_2026_08.json", "garmin_2026_09.json"])
        self.assertEqual(len(grouped["garmin_2026_09.json"]), 2)

    def test_sync_all_preserves_remote_extra_fields_and_days(self):
        client = _FakeClient()
        client.files["garmin_2026_09.json"] = {
            "2026-09-01": {
                "stress": {"avgStressLevel": 20, "legacy_extra": 1},
                "archive_only": True,
            },
            "2026-09-03": {"source": "older_archive_only"},
        }
        source = {
            "2026-09-01": {"stress": {"avgStressLevel": 30}},
            "2026-09-02": {"sleep": {"sleepTimeSeconds": 25000}},
        }
        result = gist_archive.sync_archive(client, source, current_only=False)
        archived = client.files["garmin_2026_09.json"]

        self.assertEqual(result["days_verified"], 2)
        self.assertEqual(archived["2026-09-01"]["stress"]["avgStressLevel"], 30)
        self.assertEqual(archived["2026-09-01"]["stress"]["legacy_extra"], 1)
        self.assertTrue(archived["2026-09-01"]["archive_only"])
        self.assertIn("2026-09-03", archived)

    def test_sync_current_only_updates_latest_day(self):
        client = _FakeClient()
        source = {
            "2026-08-31": {"source": "garmin", "x": 1},
            "2026-09-01": {"source": "garmin", "x": 2},
        }
        result = gist_archive.sync_archive(client, source, current_only=True)
        self.assertEqual(result["selected_days"], 1)
        self.assertNotIn("garmin_2026_08.json", client.files)
        self.assertEqual(client.files["garmin_2026_09.json"]["2026-09-01"]["x"], 2)

    def test_verify_detects_mismatch(self):
        client = _FakeClient()
        client.files["garmin_2026_09.json"] = {
            "2026-09-01": {"stress": {"avgStressLevel": 99}}
        }
        source = {"2026-09-01": {"stress": {"avgStressLevel": 30}}}
        verified, mismatches = gist_archive.verify_archive(client, source)
        self.assertEqual(verified, 0)
        self.assertEqual(mismatches, ["2026-09-01"])

    def test_hash_is_stable_for_key_order(self):
        left = {"b": 2, "a": {"y": 2, "x": 1}}
        right = {"a": {"x": 1, "y": 2}, "b": 2}
        self.assertEqual(gist_archive.canonical_hash(left), gist_archive.canonical_hash(right))


if __name__ == "__main__":
    unittest.main()

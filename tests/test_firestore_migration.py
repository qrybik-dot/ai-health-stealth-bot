import unittest

from scripts import migrate_cache_to_firestore as migration


class _FakeStore:
    def __init__(self):
        self.days = {}

    def upsert_day(self, owner_id, day_key, payload):
        current = dict(self.days.get((owner_id, day_key), {}))
        current.update(payload)
        self.days[(owner_id, day_key)] = current

    def get_day(self, owner_id, day_key):
        return dict(self.days.get((owner_id, day_key), {}))


class FirestoreMigrationTests(unittest.TestCase):
    def test_collect_days_ignores_runtime_state(self):
        payload = {
            "2026-09-25": {"source": "garmin", "date": "2026-09-25"},
            "2026-09-26": {"source": "garmin", "date": "2026-09-26"},
            "_push_state": {"x": {"ts": "now"}},
            "junk": {"value": 1},
        }
        days = migration.collect_days(payload)
        self.assertEqual(sorted(days), ["2026-09-25", "2026-09-26"])

    def test_migration_is_idempotent_and_verifies_source_subset(self):
        store = _FakeStore()
        owner = "owner"
        days = {
            "2026-09-25": {"source": "garmin", "stress": {"avgStressLevel": 31}},
            "2026-09-26": {"source": "garmin", "sleep": {"sleepTimeSeconds": 25000}},
        }
        self.assertEqual(migration.migrate_days(store, owner, days), 2)
        store.days[(owner, "2026-09-25")]["legacy_extra"] = True
        self.assertEqual(migration.migrate_days(store, owner, days), 2)
        verified, mismatches = migration.verify_days(store, owner, days)
        self.assertEqual(verified, 2)
        self.assertEqual(mismatches, [])

    def test_verify_detects_missing_or_changed_data(self):
        store = _FakeStore()
        owner = "owner"
        days = {
            "2026-09-25": {
                "source": "garmin",
                "stress": {"avgStressLevel": 31},
            }
        }
        store.days[(owner, "2026-09-25")] = {
            "source": "garmin",
            "stress": {"avgStressLevel": 99},
        }
        verified, mismatches = migration.verify_days(store, owner, days)
        self.assertEqual(verified, 0)
        self.assertEqual(mismatches, ["2026-09-25"])

    def test_dry_run_makes_no_writes(self):
        store = _FakeStore()
        days = {"2026-09-25": {"source": "garmin"}}
        written = migration.migrate_days(store, "owner", days, dry_run=True)
        self.assertEqual(written, 0)
        self.assertEqual(store.days, {})


if __name__ == "__main__":
    unittest.main()

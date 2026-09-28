import argparse
import datetime as dt
import hashlib
import json
import os
from typing import Any, Dict, Tuple

from cache import load_cache_with_meta
from firestore_store import STORE


def is_day_key(key: Any) -> bool:
    if not isinstance(key, str) or len(key) != 10 or key[4] != "-" or key[7] != "-":
        return False
    try:
        dt.date.fromisoformat(key)
        return True
    except ValueError:
        return False


def collect_days(cache_payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        key: value
        for key, value in cache_payload.items()
        if is_day_key(key) and isinstance(value, dict)
    }


def canonical_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _subset_equal(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(key in actual and _subset_equal(value, actual[key]) for key, value in expected.items())
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            return False
        return all(_subset_equal(left, right) for left, right in zip(expected, actual))
    return expected == actual


def owner_id_from_env() -> str:
    raw = (
        os.getenv("DATA_OWNER_ID", "").strip()
        or os.getenv("DEFAULT_CHAT_ID", "").strip()
        or os.getenv("TELEGRAM_CHAT_ID", "").split(",", 1)[0].strip()
    )
    return raw or "default"


def source_summary(days: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    keys = sorted(days)
    total_bytes = sum(
        len(json.dumps(days[key], ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))
        for key in keys
    )
    return {
        "days": len(keys),
        "first_day": keys[0] if keys else "",
        "last_day": keys[-1] if keys else "",
        "payload_bytes": total_bytes,
        "dataset_sha256": canonical_hash({key: days[key] for key in keys}),
    }


def migrate_days(store: Any, owner_id: str, days: Dict[str, Dict[str, Any]], dry_run: bool = False) -> int:
    if dry_run:
        return 0
    written = 0
    for day_key in sorted(days):
        store.upsert_day(owner_id, day_key, days[day_key])
        written += 1
    return written


def verify_days(store: Any, owner_id: str, days: Dict[str, Dict[str, Any]]) -> Tuple[int, list[str]]:
    verified = 0
    mismatches: list[str] = []
    for day_key in sorted(days):
        remote = store.get_day(owner_id, day_key)
        if not isinstance(remote, dict) or not _subset_equal(days[day_key], remote):
            mismatches.append(day_key)
            continue
        verified += 1
    return verified, mismatches


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Safely migrate Garmin day snapshots from runtime cache/Gist to Firestore."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read and summarize source data without writing Firestore.",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Do not write; verify Firestore already contains every source day.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.dry_run and args.verify_only:
        raise SystemExit("Choose only one of --dry-run or --verify-only")

    if not STORE.enabled:
        detail = getattr(STORE, "init_error", "") or (
            "set FIRESTORE_PROJECT_ID and FIRESTORE_SERVICE_ACCOUNT_JSON"
        )
        raise SystemExit(f"Firestore disabled: {detail}")

    cache_payload, meta = load_cache_with_meta(hydrate_history=False)
    if not isinstance(cache_payload, dict) or not cache_payload:
        raise SystemExit(
            f"Source cache unavailable: source={meta.get('source', 'unknown')} "
            f"error={meta.get('error', '')}"
        )

    days = collect_days(cache_payload)
    if not days:
        raise SystemExit("No Garmin day snapshots found; refusing to migrate")

    owner_id = owner_id_from_env()
    summary = source_summary(days)
    print(
        "source_summary "
        f"source={meta.get('source', 'unknown')} owner_id={owner_id} "
        f"days={summary['days']} first={summary['first_day']} last={summary['last_day']} "
        f"payload_bytes={summary['payload_bytes']} dataset_sha256={summary['dataset_sha256']}"
    )

    if args.dry_run:
        print("migration_mode=dry-run writes=0 verification=skipped")
        return

    written = 0 if args.verify_only else migrate_days(STORE, owner_id, days, dry_run=False)
    verified, mismatches = verify_days(STORE, owner_id, days)
    mode = "verify-only" if args.verify_only else "migrate"
    print(
        f"migration_result mode={mode} written={written} "
        f"verified={verified}/{len(days)} mismatches={len(mismatches)}"
    )
    if mismatches:
        print("mismatch_days=" + ",".join(mismatches[:20]))
        raise SystemExit(2)
    print("firestore_archive_verification=ok")


if __name__ == "__main__":
    main()

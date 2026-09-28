import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cache import load_cache_with_meta


GITHUB_GISTS_API = "https://api.github.com/gists"
ARCHIVE_DESCRIPTION = os.getenv(
    "GARMIN_ARCHIVE_GIST_DESCRIPTION",
    "coach-potato-garmin-archive-v1",
).strip()


def is_day_key(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return dt.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def collect_days(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        key: value
        for key, value in payload.items()
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


def deep_merge_preserve(existing: Any, incoming: Any) -> Any:
    if isinstance(existing, dict) and isinstance(incoming, dict):
        merged = dict(existing)
        for key, value in incoming.items():
            merged[key] = deep_merge_preserve(merged.get(key), value)
        return merged
    return incoming


def subset_equal(expected: Any, actual: Any) -> bool:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(
            key in actual and subset_equal(value, actual[key])
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            return False
        return all(subset_equal(left, right) for left, right in zip(expected, actual))
    return expected == actual


def month_file(day_key: str) -> str:
    return f"garmin_{day_key[:7].replace('-', '_')}.json"


def group_by_month(days: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    grouped: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for day_key in sorted(days):
        grouped.setdefault(month_file(day_key), {})[day_key] = days[day_key]
    return grouped


def dataset_summary(days: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    keys = sorted(days)
    return {
        "days": len(keys),
        "first_day": keys[0] if keys else "",
        "last_day": keys[-1] if keys else "",
        "dataset_sha256": canonical_hash({key: days[key] for key in keys}),
    }


class GistArchiveClient:
    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("GIST_TOKEN is required")
        self.token = token

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "coach-potato-garmin-archive",
        }

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        response = requests.request(method, url, headers=self.headers, timeout=120, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(
                f"Gist API {method} failed status={response.status_code} body={response.text[:300]}"
            )
        return response

    def find_archive(self) -> Optional[str]:
        matches = []
        for page in range(1, 11):
            response = self._request(
                "GET",
                GITHUB_GISTS_API,
                params={"per_page": 100, "page": page},
            )
            rows = response.json()
            if not isinstance(rows, list):
                raise RuntimeError("Unexpected Gist list response")
            for row in rows:
                if isinstance(row, dict) and row.get("description") == ARCHIVE_DESCRIPTION:
                    if bool(row.get("public", False)):
                        raise RuntimeError(
                            "Garmin archive Gist must be private; refusing to use a public archive"
                        )
                    matches.append(str(row.get("id", "")))
            if len(rows) < 100:
                break
        matches = [item for item in matches if item]
        if len(matches) > 1:
            raise RuntimeError(
                f"Multiple archive Gists found for description={ARCHIVE_DESCRIPTION!r}"
            )
        return matches[0] if matches else None

    def ensure_archive(self) -> str:
        existing = self.find_archive()
        if existing:
            meta = self._metadata(existing)
            if bool(meta.get("public", False)):
                raise RuntimeError(
                    "Garmin archive Gist must be private; refusing to use a public archive"
                )
            return existing
        response = self._request(
            "POST",
            GITHUB_GISTS_API,
            json={
                "description": ARCHIVE_DESCRIPTION,
                "public": False,
                "files": {
                    "manifest.json": {
                        "content": json.dumps(
                            {
                                "format": "coach-potato-garmin-archive-v1",
                                "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                    }
                },
            },
        )
        gist_id = str((response.json() or {}).get("id", ""))
        if not gist_id:
            raise RuntimeError("Archive Gist creation returned no id")
        return gist_id

    def _metadata(self, gist_id: str) -> Dict[str, Any]:
        response = self._request("GET", f"{GITHUB_GISTS_API}/{gist_id}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Unexpected Gist metadata response")
        return payload

    def read_json(self, gist_id: str, filename: str) -> Dict[str, Any]:
        meta = self._metadata(gist_id)
        file_payload = (meta.get("files") or {}).get(filename)
        if not isinstance(file_payload, dict):
            return {}
        content = file_payload.get("content", "")
        if file_payload.get("truncated"):
            raw_url = file_payload.get("raw_url")
            if not raw_url:
                raise RuntimeError(f"{filename}: truncated but raw_url missing")
            raw = self._request("GET", str(raw_url))
            content = raw.text
        if not content:
            return {}
        payload = json.loads(content)
        if not isinstance(payload, dict):
            raise RuntimeError(f"{filename}: JSON root is not an object")
        return payload

    def write_json(self, gist_id: str, filename: str, payload: Dict[str, Any]) -> None:
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self._request(
            "PATCH",
            f"{GITHUB_GISTS_API}/{gist_id}",
            json={"files": {filename: {"content": content}}},
        )


def _selected_days(days: Dict[str, Dict[str, Any]], current_only: bool) -> Dict[str, Dict[str, Any]]:
    if not days:
        return {}
    if not current_only:
        return days
    latest = sorted(days)[-1]
    return {latest: days[latest]}


def sync_archive(
    client: Any,
    days: Dict[str, Dict[str, Any]],
    current_only: bool = False,
) -> Dict[str, Any]:
    selected = _selected_days(days, current_only=current_only)
    if not selected:
        raise RuntimeError("No Garmin day snapshots available for archive sync")

    gist_id = client.ensure_archive()
    groups = group_by_month(selected)
    months_written = 0
    days_verified = 0

    for filename, incoming_days in groups.items():
        remote = client.read_json(gist_id, filename)
        merged = dict(remote)
        for day_key, snapshot in incoming_days.items():
            merged[day_key] = deep_merge_preserve(merged.get(day_key, {}), snapshot)

        if canonical_hash(remote) != canonical_hash(merged):
            client.write_json(gist_id, filename, merged)
            months_written += 1

        verified_month = client.read_json(gist_id, filename)
        for day_key, snapshot in incoming_days.items():
            if not subset_equal(snapshot, verified_month.get(day_key)):
                raise RuntimeError(f"Archive verification failed for {day_key}")
            days_verified += 1

    manifest = client.read_json(gist_id, "manifest.json")
    source = dataset_summary(days)
    manifest_update = {
        **manifest,
        "format": "coach-potato-garmin-archive-v1",
        "updated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "last_source_day": source["last_day"],
        "last_source_day_count": source["days"],
        "last_source_dataset_sha256": source["dataset_sha256"],
        "last_sync_mode": "current" if current_only else "all",
    }
    client.write_json(gist_id, "manifest.json", manifest_update)

    return {
        "source_days": len(days),
        "selected_days": len(selected),
        "months_checked": len(groups),
        "months_written": months_written,
        "days_verified": days_verified,
    }


def verify_archive(client: Any, days: Dict[str, Dict[str, Any]]) -> Tuple[int, list[str]]:
    if not days:
        raise RuntimeError("No Garmin day snapshots available for archive verification")
    gist_id = client.find_archive()
    if not gist_id:
        raise RuntimeError("Garmin archive Gist not found")

    verified = 0
    mismatches: list[str] = []
    for filename, source_month in group_by_month(days).items():
        archived = client.read_json(gist_id, filename)
        for day_key, snapshot in source_month.items():
            if subset_equal(snapshot, archived.get(day_key)):
                verified += 1
            else:
                mismatches.append(day_key)
    return verified, mismatches


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Maintain an append-only private Gist mirror of Garmin day snapshots.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Summarize source data; no archive writes.")
    mode.add_argument("--sync-all", action="store_true", help="Merge all source Garmin days into monthly archive files.")
    mode.add_argument("--sync-current", action="store_true", help="Merge only the latest source day into its monthly archive file.")
    mode.add_argument("--verify", action="store_true", help="Verify every current source day exists in the archive.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cache_payload, meta = load_cache_with_meta(hydrate_history=False)
    days = collect_days(cache_payload if isinstance(cache_payload, dict) else {})
    if not days:
        raise SystemExit(
            f"No Garmin days in source cache; source={meta.get('source', 'unknown')} error={meta.get('error', '')}"
        )

    summary = dataset_summary(days)
    print(
        "archive_source_summary "
        f"source={meta.get('source', 'unknown')} days={summary['days']} "
        f"first={summary['first_day']} last={summary['last_day']} "
        f"dataset_sha256={summary['dataset_sha256']}"
    )

    if args.dry_run:
        print("archive_mode=dry-run writes=0")
        return

    token = os.getenv("GIST_TOKEN", "").strip()
    if not token:
        raise SystemExit("GIST_TOKEN=missing")
    client = GistArchiveClient(token)

    if args.verify:
        gist_id = client.find_archive()
        if not gist_id:
            raise RuntimeError("Garmin archive Gist not found")
        archive_meta = client._metadata(gist_id)
        print(f"archive_private={not bool(archive_meta.get('public', False))}")
        verified, mismatches = verify_archive(client, days)
        print(f"archive_verify verified={verified}/{len(days)} mismatches={len(mismatches)}")
        if mismatches:
            print("archive_mismatch_days=" + ",".join(mismatches[:20]))
            raise SystemExit(2)
        print("garmin_archive_verification=ok")
        return

    result = sync_archive(client, days, current_only=args.sync_current)
    print(
        "archive_sync_result "
        f"source_days={result['source_days']} selected_days={result['selected_days']} "
        f"months_checked={result['months_checked']} months_written={result['months_written']} "
        f"days_verified={result['days_verified']}"
    )
    if args.sync_all:
        verified, mismatches = verify_archive(client, days)
        print(f"archive_verify verified={verified}/{len(days)} mismatches={len(mismatches)}")
        if mismatches:
            print("archive_mismatch_days=" + ",".join(mismatches[:20]))
            raise SystemExit(2)
        print("garmin_archive_verification=ok")


if __name__ == "__main__":
    main()

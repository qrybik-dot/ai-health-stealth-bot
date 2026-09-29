import argparse
import datetime as dt
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import (
    GARMIN_CALLS,
    Garmin,
    GarminRateLimitError,
    _authenticate_garmin,
    _block_garmin_password_login_without_tokens,
    _fetch_garmin_metric,
    env,
    utc_now_iso,
)
from scripts.gist_archive import (
    GistArchiveClient,
    is_day_key,
    month_file,
    subset_equal,
)


CORE_METRICS = {
    "sleep",
    "body_battery",
    "stress",
    "steps",
    "heart_rate",
    "daily_activity",
}


def meaningful(value: Any) -> bool:
    return value not in (None, {}, [])


def iter_month_files(first_day: str, last_day: str) -> Iterable[str]:
    start = dt.date.fromisoformat(first_day).replace(day=1)
    end = dt.date.fromisoformat(last_day).replace(day=1)
    current = start
    while current <= end:
        yield f"garmin_{current:%Y_%m}.json"
        if current.month == 12:
            current = dt.date(current.year + 1, 1, 1)
        else:
            current = dt.date(current.year, current.month + 1, 1)


def calendar_days(first_day: str, last_day: str) -> List[str]:
    start = dt.date.fromisoformat(first_day)
    end = dt.date.fromisoformat(last_day)
    out: List[str] = []
    current = start
    while current <= end:
        out.append(current.isoformat())
        current += dt.timedelta(days=1)
    return out


def fetch_exact_day(api: Any, auth_info: Dict[str, str], day: str) -> Dict[str, Any]:
    dt.date.fromisoformat(day)
    payload: Dict[str, Any] = {
        "source": "garmin",
        "date": day,
        "fetched_at_utc": utc_now_iso(),
        "last_sync_time": utc_now_iso(),
        "auth_method": auth_info.get("method", ""),
        "auth_source": auth_info.get("source", ""),
        "errors": [],
    }
    meaningful_metrics: List[str] = []
    core_metrics: List[str] = []

    for key, method_name in GARMIN_CALLS.items():
        method = getattr(api, method_name, None)
        if not callable(method):
            continue
        try:
            value = _fetch_garmin_metric(api, method_name, day, key)
            payload[key] = value
            if meaningful(value):
                meaningful_metrics.append(key)
                if key in CORE_METRICS:
                    core_metrics.append(key)
        except GarminRateLimitError:
            raise
        except Exception as exc:
            payload["errors"].append({"metric": key, "error": type(exc).__name__})

    print(
        f"recover_fetch day={day} meaningful={len(meaningful_metrics)} "
        f"core={len(core_metrics)} errors={len(payload['errors'])} "
        f"metrics={','.join(sorted(meaningful_metrics))}"
    )
    if len(meaningful_metrics) < 3 or len(core_metrics) < 1:
        raise RuntimeError(
            f"{day}: Garmin did not return enough real historical data; archive untouched"
        )
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("days", nargs="+")
    args = parser.parse_args()

    targets = sorted(set(args.days))
    for day in targets:
        dt.date.fromisoformat(day)

    if not os.getenv("GIST_TOKEN", "").strip():
        raise SystemExit("GIST_TOKEN=missing")

    _block_garmin_password_login_without_tokens("recover-exact-days")
    api = Garmin(env("GARMIN_EMAIL"), env("GARMIN_PASSWORD"))
    auth_info = _authenticate_garmin(api)
    if auth_info.get("method") == "password":
        raise RuntimeError("Refusing password fallback for exact-day recovery")

    recovered: Dict[str, Dict[str, Any]] = {}
    for day in targets:
        recovered[day] = fetch_exact_day(api, auth_info, day)

    client = GistArchiveClient(os.environ["GIST_TOKEN"].strip())
    gist_id = client.ensure_archive()
    archive_meta = client._metadata(gist_id)
    if bool(archive_meta.get("public", False)):
        raise RuntimeError("Archive is public; refusing recovery")

    added = 0
    by_file: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for day, payload in recovered.items():
        by_file.setdefault(month_file(day), {})[day] = payload

    for filename, incoming in by_file.items():
        remote = client.read_json(gist_id, filename)
        merged = dict(remote)
        for day, payload in incoming.items():
            if day in remote:
                print(f"recover_skip_existing day={day}")
                continue
            merged[day] = payload
            added += 1
        if merged != remote:
            client.write_json(gist_id, filename, merged)

        verified = client.read_json(gist_id, filename)
        for day, payload in incoming.items():
            if not subset_equal(payload, verified.get(day)):
                raise RuntimeError(f"{day}: archive verification failed after write")

    manifest = client.read_json(gist_id, "manifest.json")
    previous_total = int(
        manifest.get("archived_day_count")
        or manifest.get("last_source_day_count")
        or 0
    )
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    manifest["updated_at_utc"] = now
    manifest["archived_day_count"] = previous_total + added
    manifest["last_recovery_at_utc"] = now
    manifest["last_recovery_days"] = targets
    client.write_json(gist_id, "manifest.json", manifest)

    archive_meta = client._metadata(gist_id)
    archive_files = sorted(
        name
        for name in (archive_meta.get("files") or {})
        if name.startswith("garmin_") and name.endswith(".json")
    )
    archived_days = set()
    for filename in archive_files:
        month_payload = client.read_json(gist_id, filename)
        archived_days.update(k for k in month_payload if is_day_key(k))

    if not archived_days:
        raise RuntimeError("Archive contains no Garmin day files")

    first_day = min(archived_days)
    last_day = max(archived_days)
    expected = calendar_days(first_day, last_day)
    missing = [day for day in expected if day not in archived_days]

    manifest = client.read_json(gist_id, "manifest.json")
    manifest["archived_day_count"] = len(archived_days)
    manifest["archived_first_day"] = first_day
    manifest["archived_last_day"] = last_day
    manifest["inventory_verified_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    client.write_json(gist_id, "manifest.json", manifest)

    print(
        f"recover_result requested={len(targets)} added={added} "
        f"archive_days={len(archived_days)} expected_calendar_days={len(expected)} "
        f"first={first_day} last={last_day} missing={len(missing)} "
        f"private={not bool(archive_meta.get('public', False))}"
    )
    if missing:
        print("recover_missing_days=" + ",".join(missing))
        raise SystemExit(2)
    print("garmin_archive_calendar_complete=ok")


if __name__ == "__main__":
    main()

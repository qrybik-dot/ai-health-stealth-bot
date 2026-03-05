import json
import os
import requests
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
from firestore_store import STORE as FIRESTORE

CACHE_FILE = "cache.json"
MEMORY_DAYS = 120
WEEKLY_STATE_KEY = "_weekly_state"
DAILY_VOTES_KEY = "_daily_votes"
TODAY_VOTES_KEY = "_today_votes"
TODAY_STATE_KEY = "_today_state"
PUSH_STATE_KEY = "_push_state"
CALLBACK_DEDUP_KEY = "_callback_dedup"
REFRESH_STATE_KEY = "_refresh_state"
SYNC_DEBUG_KEY = "_sync_debug"
USER_PREFS_KEY = "_user_prefs"
AUTH_STATE_KEY = "_auth_state"
RETENTION_DAYS = MEMORY_DAYS
WEEKLY_RETENTION_WEEKS = 26
PUSH_STATE_RETENTION_DAYS = 14
DEFAULT_BOT_TZ = "Europe/Moscow"
DEFAULT_CHAT_SCOPE = os.getenv("DEFAULT_CHAT_ID", "default")
KEY_METRICS: Tuple[str, ...] = ("sleep", "body_battery", "rhr", "stress")
METRIC_LABELS: Dict[str, str] = {
    "sleep": "сон",
    "body_battery": "Body Battery",
    "rhr": "RHR",
    "stress": "стресс",
    "steps": "шаги",
    "heart_rate": "пульс",
    "daily_activity": "активность",
    "respiration": "дыхание",
    "pulse_ox": "SpO2",
    "hrv": "ВСР",
    "hrv_status": "статус ВСР",
    "intensity_minutes": "интенсивные минуты",
    "calories": "калории",
    "floors": "этажи",
    "activity_summary": "сводка активности",
}


def _load_local_cache() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
            if isinstance(cache, dict):
                return cache, {"source": "local", "available": True, "error": ""}
            return {}, {"source": "local", "available": False, "error": "local_not_dict"}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}, {"source": "local", "available": False, "error": "local_missing_or_invalid"}


def _snapshot_freshness_score(snapshot: Any) -> str:
    if not isinstance(snapshot, dict):
        return ""
    for key in ("last_sync_time", "fetched_at_utc"):
        value = snapshot.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _is_day_key(key: Any) -> bool:
    if not isinstance(key, str) or key.startswith("_"):
        return False
    try:
        date.fromisoformat(key)
        return True
    except ValueError:
        return False


def _merge_runtime_cache(gist_cache: Dict[str, Any], local_cache: Dict[str, Any]) -> Dict[str, Any]:
    """
    Builds a single runtime cache source of truth:
    - daily snapshots: union by date key with freshness-aware merge
    - service keys (_*): keep local to preserve bot interaction state
    """
    merged: Dict[str, Any] = {}
    for key, value in local_cache.items():
        merged[key] = value

    for key, gist_value in gist_cache.items():
        if key.startswith("_"):
            continue
        local_value = merged.get(key)
        if _is_day_key(key):
            if isinstance(local_value, dict) and isinstance(gist_value, dict):
                gist_score = _snapshot_freshness_score(gist_value)
                local_score = _snapshot_freshness_score(local_value)
                if local_score and gist_score and local_score > gist_score:
                    merged[key] = _merge_trimmed_snapshot(gist_value, local_value)
                else:
                    merged[key] = _merge_trimmed_snapshot(local_value, gist_value)
            elif isinstance(gist_value, dict) and not isinstance(local_value, dict):
                merged[key] = gist_value
            elif key not in merged:
                merged[key] = gist_value
        else:
            merged[key] = gist_value
    return merged


def load_cache_with_meta() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    If CACHE_GIST_ID env var is set, fetches the cache from the Gist.
    Otherwise, reads the local cache.json file.
    This allows the Render server to get the latest cache from GitHub Actions.
    """
    gist_id = os.getenv("CACHE_GIST_ID")

    if gist_id:
        token = None
        token_source = "none"
        for source_name in ("GIST_TOKEN", "GIST_SYNC_TOKEN", "GITHUB_TOKEN"):
            source_value = os.getenv(source_name)
            if source_value:
                token = source_value
                token_source = source_name
                break
        token_present = bool(token)
        api_url = f"https://api.github.com/gists/{gist_id}"
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = requests.get(api_url, headers=headers, timeout=10)
            print(
                f"cache gist fetch: gist_id={gist_id} selected_token_source={token_source} token_present={token_present} http_status={response.status_code}"
            )

            if response.status_code != 200:
                reason_map = {
                    404: ("gist_404", "gist not found (id mismatch)"),
                    403: ("gist_403", "forbidden (token missing/insufficient)"),
                    401: ("gist_401", "unauthorized"),
                    429: ("rate_limit", "rate limited"),
                }
                error_code, short_reason = reason_map.get(
                    response.status_code,
                    (f"gist_http_{response.status_code}", "http error"),
                )
                print(
                    f"cache gist fetch failed: code={error_code} reason={short_reason}"
                )
                local_cache, local_meta = _load_local_cache()
                if local_meta.get("available"):
                    return local_cache, {
                        "source": "local_fallback",
                        "available": True,
                        "error": error_code,
                        "http_status": response.status_code,
                        "token_present": token_present,
                        "token_source": token_source,
                        "fallback_reason": short_reason,
                    }
                return {}, {
                    "source": "gist",
                    "available": False,
                    "error": error_code,
                    "http_status": response.status_code,
                    "token_present": token_present,
                    "token_source": token_source,
                }

            gist_data = response.json()
            content = gist_data["files"]["cache.json"]["content"]
            cache = json.loads(content)
            if isinstance(cache, dict):
                local_cache, local_meta = _load_local_cache()
                if local_meta.get("available") and isinstance(local_cache, dict):
                    cache = _merge_runtime_cache(cache, local_cache)
                if local_meta.get("available") and isinstance(local_cache, dict):
                    day_key = current_day_key()
                    gist_score = _snapshot_freshness_score(cache.get(day_key))
                    local_score = _snapshot_freshness_score(local_cache.get(day_key))
                    if local_score and local_score > gist_score:
                        return local_cache, {
                            "source": "local_fresher_than_gist",
                            "available": True,
                            "error": "",
                            "http_status": response.status_code,
                            "token_present": token_present,
                            "token_source": token_source,
                            "fallback_reason": "local_snapshot_is_newer",
                        }
                return cache, {
                    "source": "gist",
                    "available": True,
                    "error": "",
                    "http_status": response.status_code,
                    "token_present": token_present,
                    "token_source": token_source,
                }
            return {}, {
                "source": "gist",
                "available": False,
                "error": "gist_not_dict",
                "http_status": response.status_code,
                "token_present": token_present,
                "token_source": token_source,
            }
        except (requests.RequestException, KeyError, json.JSONDecodeError) as e:
            print(
                f"cache gist fetch exception: gist_id={gist_id} selected_token_source={token_source} token_present={token_present} error={e}"
            )
            local_cache, local_meta = _load_local_cache()
            if local_meta.get("available"):
                return local_cache, {
                    "source": "local_fallback",
                    "available": True,
                    "error": "gist_exception",
                    "detail": str(e),
                    "token_present": token_present,
                    "token_source": token_source,
                }
            return {}, {
                "source": "gist",
                "available": False,
                "error": "gist_exception",
                "detail": str(e),
                "token_present": token_present,
                "token_source": token_source,
            }
    return _load_local_cache()


def load_cache() -> Dict[str, Any]:
    cache, _meta = load_cache_with_meta()
    return cache


def _write_cache(cache: Dict[str, Any]) -> None:
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def _safe_number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _safe_date(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    candidate = raw.split("T", 1)[0]
    try:
        date.fromisoformat(candidate)
        return candidate
    except ValueError:
        return None


def normalize_day_key_msk(value: Any) -> str:
    tz = ZoneInfo(DEFAULT_BOT_TZ)
    if isinstance(value, str):
        raw = value.strip()
        if raw:
            parsed_date = _safe_date(raw)
            if parsed_date:
                return parsed_date
            try:
                parsed_dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if parsed_dt.tzinfo is None:
                    parsed_dt = parsed_dt.replace(tzinfo=tz)
                return parsed_dt.astimezone(tz).date().isoformat()
            except ValueError:
                pass
    return datetime.now(tz).date().isoformat()


def _collect_dates(node: Any) -> List[str]:
    found: List[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if "date" in str(key).lower():
                parsed = _safe_date(value)
                if parsed:
                    found.append(parsed)
            found.extend(_collect_dates(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_collect_dates(item))
    elif isinstance(node, str):
        parsed = _safe_date(node)
        if parsed:
            found.append(parsed)
    return sorted(set(found))


def _diagnose_missing_metric(
    metric: str,
    raw_value: Any,
    normalized_value: Any,
    expected_day: str,
    reason: str,
) -> Dict[str, Any]:
    dates = _collect_dates(raw_value)
    date_mismatch = bool(dates) and expected_day not in dates
    if reason:
        normalized_reason = reason
    elif not _is_meaningful(raw_value):
        normalized_reason = "raw_absent"
    elif date_mismatch:
        normalized_reason = "date_mismatch"
    elif not _is_meaningful(normalized_value):
        normalized_reason = "mapping_or_shape_mismatch"
    else:
        normalized_reason = "ok"
    return {
        "metric": metric,
        "raw_present": _is_meaningful(raw_value),
        "normalized_present": _is_meaningful(normalized_value),
        "expected_date_key": expected_day,
        "raw_dates": dates,
        "date_mismatch": date_mismatch,
        "reason": normalized_reason,
    }


def _first_number_from_any(node: Any) -> Optional[float]:
    direct = _safe_number(node)
    if direct is not None:
        return direct
    if isinstance(node, dict):
        for value in node.values():
            nested = _first_number_from_any(value)
            if nested is not None:
                return nested
    if isinstance(node, list):
        for item in node:
            nested = _first_number_from_any(item)
            if nested is not None:
                return nested
    return None


def _normalize_steps(raw_steps: Any) -> Optional[Dict[str, float]]:
    if isinstance(raw_steps, dict):
        for key in ("totalSteps", "steps", "stepCount", "value"):
            value = _safe_number(raw_steps.get(key))
            if value is not None:
                return {"totalSteps": value}
        nested = _first_number_from_any(raw_steps)
        if nested is not None:
            return {"totalSteps": nested}
    if isinstance(raw_steps, list):
        for item in raw_steps:
            normalized = _normalize_steps(item)
            if normalized:
                return normalized
    value = _safe_number(raw_steps)
    if value is not None:
        return {"totalSteps": value}
    return None


def _normalize_rhr(raw_rhr: Any) -> Optional[Dict[str, float]]:
    if isinstance(raw_rhr, dict):
        hr_avg = _safe_number(raw_rhr.get("lastSevenDaysAvgRestingHeartRate"))
        hr_rest = _safe_number(raw_rhr.get("restingHeartRate"))
        if hr_avg is None:
            hr_avg = _safe_number(raw_rhr.get("averageRestingHeartRate"))
        if hr_rest is None:
            hr_rest = _safe_number(raw_rhr.get("value"))
        if hr_rest is None:
            hr_rest = _first_number_from_any(raw_rhr)
        if hr_avg is not None or hr_rest is not None:
            return {
                "lastSevenDaysAvgRestingHeartRate": hr_avg,
                "restingHeartRate": hr_rest,
            }
    value = _safe_number(raw_rhr)
    if value is not None:
        return {
            "lastSevenDaysAvgRestingHeartRate": None,
            "restingHeartRate": value,
        }
    return None


def _normalize_sleep(raw_sleep: Any) -> Optional[Dict[str, float]]:
    if isinstance(raw_sleep, dict):
        sleep_seconds = raw_sleep.get("sleepTimeSeconds")
        if sleep_seconds is None:
            sleep_seconds = raw_sleep.get("totalSleepSeconds")
        score = _safe_number(raw_sleep.get("overallSleepScore"))
        normalized_seconds = _safe_number(sleep_seconds)
        if normalized_seconds is not None or score is not None:
            return {"sleepTimeSeconds": normalized_seconds, "overallSleepScore": score}
        for nested_key in ("dailySleepDTO", "sleepSummary", "summary"):
            nested = raw_sleep.get(nested_key)
            normalized = _normalize_sleep(nested)
            if normalized:
                return normalized
    if isinstance(raw_sleep, list):
        for item in raw_sleep:
            normalized = _normalize_sleep(item)
            if normalized:
                return normalized
    return None


def _normalize_body_battery(raw_body: Any) -> Optional[Dict[str, float]]:
    if isinstance(raw_body, dict):
        recent = _safe_number(raw_body.get("mostRecentValue"))
        charged = _safe_number(raw_body.get("chargedValue"))
        if recent is None:
            recent = _safe_number(raw_body.get("bodyBattery"))
        if recent is None:
            recent = _safe_number(raw_body.get("value"))
        if recent is None:
            series = raw_body.get("bodyBatteryValuesArray")
            if isinstance(series, list):
                for point in reversed(series):
                    if isinstance(point, list) and len(point) >= 2:
                        candidate = _safe_number(point[1])
                        if candidate is not None:
                            recent = candidate
                            break
        if recent is not None or charged is not None:
            return {"mostRecentValue": recent, "chargedValue": charged}
    if isinstance(raw_body, list):
        for item in raw_body:
            normalized = _normalize_body_battery(item)
            if normalized:
                return normalized
    return None


def _is_meaningful(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, dict):
        if not value:
            return False
        return any(_is_meaningful(v) for v in value.values())
    if isinstance(value, list):
        return len(value) > 0
    return True


def _history_day_keys(cache: Dict[str, Any]) -> List[str]:
    day_keys: List[str] = []
    for key in cache.keys():
        if not isinstance(key, str) or key.startswith("_"):
            continue
        try:
            date.fromisoformat(key)
            day_keys.append(key)
        except ValueError:
            continue
    return sorted(day_keys)


def history_list(cache_data: Optional[Dict[str, Any]] = None) -> List[str]:
    cache = cache_data if isinstance(cache_data, dict) else load_cache()
    return _history_day_keys(cache)


def _available_metric_keys(snapshot: Dict[str, Any]) -> List[str]:
    return [metric for metric in METRIC_LABELS.keys() if _is_meaningful(snapshot.get(metric))]


def build_day_context(day_key: Optional[str] = None, cache_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cache = cache_data if isinstance(cache_data, dict) else load_cache()
    keys = history_list(cache)
    target_day = normalize_day_key_msk(day_key) if day_key else current_day_key()
    snapshot = cache.get(target_day) if isinstance(cache.get(target_day), dict) else {}

    available_metrics = _available_metric_keys(snapshot)
    missing_metrics = [metric for metric in METRIC_LABELS.keys() if metric not in available_metrics]
    key_present = sum(1 for metric in KEY_METRICS if metric in available_metrics)
    completeness = float(snapshot.get("data_completeness", 0.0) or 0.0)
    confidence = float(snapshot.get("confidence", 0.0) or 0.0)
    if not snapshot:
        day_status = "no_data"
    elif key_present < len(KEY_METRICS):
        day_status = "partial"
    else:
        day_status = "ready"

    return {
        "latest_snapshot_date": target_day,
        "available_metrics": available_metrics,
        "missing_metrics": missing_metrics,
        "key_metrics_present_count": key_present,
        "key_metrics_total_count": len(KEY_METRICS),
        "data_completeness": completeness,
        "confidence": confidence,
        "available_days_count": len(keys),
        "available_days": keys,
        "day_status": day_status,
        "last_sync_time": str(snapshot.get("last_sync_time", snapshot.get("fetched_at_utc", ""))),
        "updated_blocks": [],
        "snapshot": snapshot,
    }


def _recalculate_quality(snapshot: Dict[str, Any]) -> None:
    snapshot["missing_flags"] = {
        "body_battery": not _is_meaningful(snapshot.get("body_battery")),
        "stress": not _is_meaningful(snapshot.get("stress")),
        "sleep": not _is_meaningful(snapshot.get("sleep")),
        "rhr": not _is_meaningful(snapshot.get("rhr")),
        "steps": not _is_meaningful(snapshot.get("steps")),
        "heart_rate": not _is_meaningful(snapshot.get("heart_rate")),
        "daily_activity": not _is_meaningful(snapshot.get("daily_activity")),
    }
    metrics_total = len(snapshot["missing_flags"])
    missing_total = sum(1 for missing in snapshot["missing_flags"].values() if missing)
    completeness = round(max(0.0, min(1.0, (metrics_total - missing_total) / max(1, metrics_total))), 2)
    snapshot["data_completeness"] = completeness
    snapshot["confidence"] = round(0.35 + completeness * 0.6, 2)


def _tz_name() -> str:
    return DEFAULT_BOT_TZ


def current_day_key() -> str:
    try:
        tz = ZoneInfo(_tz_name())
    except Exception:
        tz = timezone.utc
    return datetime.now(tz).date().isoformat()


def _is_emptyish(value: Any) -> bool:
    return value in (None, "", {}, [])


def _deep_merge(existing: Any, incoming: Any) -> Any:
    if isinstance(existing, dict) and isinstance(incoming, dict):
        merged: Dict[str, Any] = dict(existing)
        for key, incoming_value in incoming.items():
            existing_value = existing.get(key)
            if _is_emptyish(incoming_value):
                continue
            merged[key] = _deep_merge(existing_value, incoming_value)
        return merged
    if _is_emptyish(incoming):
        return existing
    return incoming


def _merge_trimmed_snapshot(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(existing)
    always_replace = {"source", "date", "fetched_at_utc", "last_sync_time", "error"}

    for key, value in incoming.items():
        if key in {"missing_flags", "data_completeness", "confidence"}:
            continue
        if key in always_replace:
            if value not in (None, ""):
                merged[key] = value
            continue
        if key == "errors":
            existing_errors = existing.get("errors") if isinstance(existing.get("errors"), list) else []
            incoming_errors = value if isinstance(value, list) else []
            merged_errors = (existing_errors + incoming_errors)[:10]
            if merged_errors:
                merged["errors"] = merged_errors
            continue
        if _is_meaningful(value):
            merged[key] = _deep_merge(existing.get(key), value)

    _recalculate_quality(merged)
    return merged


def _trim_daily_snapshot(snapshot_data: Dict[str, Any], day: str) -> Dict[str, Any]:
    raw_body = snapshot_data.get("body_battery")
    stress = snapshot_data.get("stress") if isinstance(snapshot_data.get("stress"), dict) else {}
    raw_sleep = snapshot_data.get("sleep")
    raw_rhr = snapshot_data.get("rhr")
    raw_steps = snapshot_data.get("steps")
    errors = snapshot_data.get("errors") if isinstance(snapshot_data.get("errors"), list) else []

    extra_metrics = {
        "heart_rate": snapshot_data.get("heart_rate"),
        "daily_activity": snapshot_data.get("daily_activity"),
        "intensity_minutes": snapshot_data.get("intensity_minutes"),
        "calories": snapshot_data.get("calories"),
        "floors": snapshot_data.get("floors"),
        "respiration": snapshot_data.get("respiration"),
        "pulse_ox": snapshot_data.get("pulse_ox"),
        "hrv": snapshot_data.get("hrv"),
        "hrv_status": snapshot_data.get("hrv_status"),
        "activity_summary": snapshot_data.get("activity_summary"),
    }

    out: Dict[str, Any] = {
        "source": str(snapshot_data.get("source", "garmin")),
        "date": str(snapshot_data.get("date", day)),
        "fetched_at_utc": str(snapshot_data.get("fetched_at_utc", "")),
        "last_sync_time": str(snapshot_data.get("last_sync_time", snapshot_data.get("fetched_at_utc", ""))),
    }

    normalized_body = _normalize_body_battery(raw_body)
    if normalized_body:
        out["body_battery"] = normalized_body

    stress_avg = _safe_number(stress.get("avgStressLevel"))
    stress_overall = _safe_number(stress.get("overallStressLevel"))
    if stress_avg is not None or stress_overall is not None:
        out["stress"] = {
            "avgStressLevel": stress_avg,
            "overallStressLevel": stress_overall,
        }

    normalized_sleep = _normalize_sleep(raw_sleep)
    if normalized_sleep:
        out["sleep"] = normalized_sleep

    normalized_rhr = _normalize_rhr(raw_rhr)
    if normalized_rhr:
        out["rhr"] = normalized_rhr

    normalized_steps = _normalize_steps(raw_steps)
    if normalized_steps:
        out["steps"] = normalized_steps

    for metric_name, metric_value in extra_metrics.items():
        if isinstance(metric_value, dict) and metric_value:
            out[metric_name] = metric_value

    diagnostics: Dict[str, Any] = {
        "expected_date_key": day,
        "metrics": {},
    }
    for metric, raw_value, normalized_value in (
        ("sleep", raw_sleep, out.get("sleep")),
        ("body_battery", raw_body, out.get("body_battery")),
        ("rhr", raw_rhr, out.get("rhr")),
        ("steps", raw_steps, out.get("steps")),
    ):
        diagnostics["metrics"][metric] = _diagnose_missing_metric(
            metric=metric,
            raw_value=raw_value,
            normalized_value=normalized_value,
            expected_day=day,
            reason="",
        )
    out["sync_diagnostics"] = diagnostics

    _recalculate_quality(out)

    if errors:
        out["errors"] = errors[:10]
    if snapshot_data.get("error"):
        out["error"] = str(snapshot_data.get("error"))
    return out


def _week_start(week_id: str) -> Optional[date]:
    try:
        return datetime.strptime(f"{week_id}-1", "%G-W%V-%u").date()
    except ValueError:
        return None


def prune_cache(retention_days: int = RETENTION_DAYS, weekly_retention_weeks: int = WEEKLY_RETENTION_WEEKS) -> Dict[str, int]:
    cache = load_cache()
    today = date.today()
    daily_cutoff = today - timedelta(days=retention_days)
    push_cutoff = today - timedelta(days=PUSH_STATE_RETENTION_DAYS)
    week_cutoff = today - timedelta(weeks=weekly_retention_weeks)

    daily_keys = [k for k in cache.keys() if not k.startswith("_")]
    removed_daily = 0
    for key in daily_keys:
        try:
            if date.fromisoformat(key) < daily_cutoff:
                cache.pop(key, None)
                removed_daily += 1
        except ValueError:
            continue

    for state_key in (TODAY_STATE_KEY, TODAY_VOTES_KEY, DAILY_VOTES_KEY, REFRESH_STATE_KEY):
        state = cache.get(state_key)
        if not isinstance(state, dict):
            continue
        remove_keys = []
        for composite_key in state.keys():
            day_part = composite_key.split("|", 1)[0]
            try:
                if date.fromisoformat(day_part) < daily_cutoff:
                    remove_keys.append(composite_key)
            except ValueError:
                continue
        for composite_key in remove_keys:
            state.pop(composite_key, None)

    weekly_state = cache.get(WEEKLY_STATE_KEY)
    removed_weekly = 0
    if isinstance(weekly_state, dict):
        for week_id in list(weekly_state.keys()):
            start = _week_start(week_id)
            if start and start < week_cutoff:
                weekly_state.pop(week_id, None)
                removed_weekly += 1

    push_state = cache.get(PUSH_STATE_KEY)
    removed_push = 0
    if isinstance(push_state, dict):
        for key in list(push_state.keys()):
            if key.startswith("weekly|"):
                parts = key.split("|")
                if len(parts) >= 3:
                    start = _week_start(parts[1])
                    if start and start < week_cutoff:
                        push_state.pop(key, None)
                        removed_push += 1
                continue
            send_date = key.split("|", 1)[0]
            try:
                if date.fromisoformat(send_date) < push_cutoff:
                    push_state.pop(key, None)
                    removed_push += 1
            except ValueError:
                continue

    _write_cache(cache)
    kept_daily = sum(1 for k in cache.keys() if not k.startswith("_"))
    summary = {
        "daily_removed": removed_daily,
        "daily_kept": kept_daily,
        "weekly_removed": removed_weekly,
        "weekly_kept": len(cache.get(WEEKLY_STATE_KEY, {})) if isinstance(cache.get(WEEKLY_STATE_KEY), dict) else 0,
        "push_removed": removed_push,
        "push_kept": len(cache.get(PUSH_STATE_KEY, {})) if isinstance(cache.get(PUSH_STATE_KEY), dict) else 0,
    }
    print(
        "cache prune summary: "
        f"daily removed={summary['daily_removed']} kept={summary['daily_kept']} "
        f"weekly removed={summary['weekly_removed']} kept={summary['weekly_kept']} "
        f"push removed={summary['push_removed']} kept={summary['push_kept']}"
    )
    return summary


def save_daily_snapshot(snapshot_data: Dict[str, Any]) -> None:
    today_str = normalize_day_key_msk(snapshot_data.get("date") or current_day_key())
    cache, _ = _load_local_cache()
    existing = cache.get(today_str) if isinstance(cache.get(today_str), dict) else {}
    incoming = _trim_daily_snapshot(snapshot_data, today_str)
    before = dict(existing)
    merged = _merge_trimmed_snapshot(existing, incoming)
    cache[today_str] = merged
    _write_cache(cache)
    prune_cache(retention_days=RETENTION_DAYS)


def build_snapshot_merge_diff(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    tracked_keys = [
        "sleep",
        "body_battery",
        "stress",
        "steps",
        "heart_rate",
        "rhr",
        "daily_activity",
        "respiration",
        "pulse_ox",
        "hrv",
        "intensity_minutes",
        "calories",
        "floors",
        "activity_summary",
    ]
    updated_blocks: list[str] = []
    for key in tracked_keys:
        if before.get(key) != after.get(key):
            updated_blocks.append(key)

    old_completeness = float(before.get("data_completeness", 0.0) or 0.0)
    new_completeness = float(after.get("data_completeness", 0.0) or 0.0)
    old_confidence = float(before.get("confidence", 0.0) or 0.0)
    new_confidence = float(after.get("confidence", 0.0) or 0.0)

    return {
        "updated_blocks": updated_blocks,
        "old_completeness": old_completeness,
        "new_completeness": new_completeness,
        "old_confidence": old_confidence,
        "new_confidence": new_confidence,
        "had_real_updates": bool(updated_blocks),
    }


def get_day_snapshot(day_key: str, chat_id: str = DEFAULT_CHAT_SCOPE) -> Dict[str, Any]:
    day_key = normalize_day_key_msk(day_key)
    cache, _ = _load_local_cache()
    snapshot = cache.get(day_key)
    if isinstance(snapshot, dict) and snapshot:
        return snapshot
    if FIRESTORE.enabled:
        remote = FIRESTORE.get_day(chat_id, day_key)
        if isinstance(remote, dict) and remote:
            return remote
    return snapshot if isinstance(snapshot, dict) else {}


def upsert_day_snapshot(day_key: str, snapshot_data: Dict[str, Any], chat_id: str = DEFAULT_CHAT_SCOPE) -> Dict[str, Any]:
    day_key = normalize_day_key_msk(day_key)
    cache, _ = _load_local_cache()
    existing = cache.get(day_key) if isinstance(cache.get(day_key), dict) else {}
    incoming = _trim_daily_snapshot(snapshot_data, day_key)
    merged = _merge_trimmed_snapshot(existing, incoming)
    cache[day_key] = merged
    _write_cache(cache)
    if FIRESTORE.enabled:
        FIRESTORE.upsert_day(chat_id, day_key, merged)
    prune_cache(retention_days=RETENTION_DAYS)
    return merged


def get_day_summary(date_msk: str, cache_data: Optional[Dict[str, Any]] = None, chat_id: str = DEFAULT_CHAT_SCOPE) -> Dict[str, Any]:
    normalized_day = normalize_day_key_msk(date_msk)
    source_cache = cache_data if isinstance(cache_data, dict) else load_cache()
    snapshot = get_day_snapshot(normalized_day, chat_id=chat_id)
    if isinstance(snapshot, dict) and snapshot:
        source_cache = dict(source_cache)
        source_cache[normalized_day] = snapshot
    context = build_day_context(day_key=normalized_day, cache_data=source_cache)
    return {
        "date_msk": normalized_day,
        "snapshot": context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {},
        "completeness_state": "FULL" if context.get("day_status") == "ready" else "PARTIAL",
        "data_completeness": float(context.get("data_completeness", 0.0) or 0.0),
        "available_metrics": list(context.get("available_metrics", [])),
        "missing_metrics": list(context.get("missing_metrics", [])),
        "available_days": list(context.get("available_days", [])),
        "available_days_count": int(context.get("available_days_count", 0)),
    }


def callback_dedup_hit(chat_id: str, callback_key: str, ttl_seconds: int = 30) -> bool:
    now_ts = datetime.now(timezone.utc).timestamp()
    cache, _ = _load_local_cache()
    state = cache.get(CALLBACK_DEDUP_KEY)
    if not isinstance(state, dict):
        state = {}
        cache[CALLBACK_DEDUP_KEY] = state

    expire_before = now_ts - max(1, ttl_seconds)
    for key in list(state.keys()):
        ts = state.get(key)
        if not isinstance(ts, (int, float)) or float(ts) < expire_before:
            state.pop(key, None)

    scoped_key = f"{chat_id}|{callback_key}"
    last_seen = state.get(scoped_key)
    if isinstance(last_seen, (int, float)) and float(last_seen) >= expire_before:
        _write_cache(cache)
        return True

    state[scoped_key] = now_ts
    _write_cache(cache)
    return False


def load_weekly_state() -> Dict[str, Any]:
    cache = load_cache()
    state = cache.get(WEEKLY_STATE_KEY, {})
    if isinstance(state, dict):
        return state
    return {}


def save_weekly_state(week_id: str, weekly_payload: Dict[str, Any]) -> None:
    cache, _ = _load_local_cache()
    if WEEKLY_STATE_KEY not in cache or not isinstance(cache[WEEKLY_STATE_KEY], dict):
        cache[WEEKLY_STATE_KEY] = {}
    cache[WEEKLY_STATE_KEY][week_id] = weekly_payload
    _write_cache(cache)


def _load_daily_votes(cache: Dict[str, Any]) -> Dict[str, Any]:
    votes = cache.get(DAILY_VOTES_KEY)
    if isinstance(votes, dict):
        return votes
    cache[DAILY_VOTES_KEY] = {}
    return cache[DAILY_VOTES_KEY]


def get_color_vote(chat_id: str, vote_date: str) -> Optional[Dict[str, Any]]:
    cache = load_cache()
    votes = cache.get(DAILY_VOTES_KEY, {})
    composite_key = f"{vote_date}|{chat_id}"
    raw_vote = votes.get(composite_key) if isinstance(votes, dict) else None
    if isinstance(raw_vote, dict) and raw_vote.get("vote_value") in {"yes", "partial", "no"}:
        return raw_vote

    state = cache.get(WEEKLY_STATE_KEY, {})
    if not isinstance(state, dict):
        return None
    for week_payload in state.values():
        if not isinstance(week_payload, dict):
            continue
        legacy_votes = week_payload.get("votes_by_date_chat", {})
        if not isinstance(legacy_votes, dict):
            continue
        legacy_vote = legacy_votes.get(composite_key)
        if legacy_vote in {"yes", "partial", "no"}:
            week_id = week_payload.get("week_id") if isinstance(week_payload.get("week_id"), str) else ""
            return {"vote_value": legacy_vote, "ts": "", "week_id": week_id}
    return None


def upsert_color_vote(chat_id: str, vote_date: str, vote_value: str, week_id: str, vote_ts: Optional[str] = None) -> bool:
    cache, _ = _load_local_cache()
    votes = _load_daily_votes(cache)
    composite_key = f"{vote_date}|{chat_id}"
    existing = votes.get(composite_key)
    if isinstance(existing, dict) and existing.get("vote_value") in {"yes", "partial", "no"}:
        if existing.get("vote_value") == vote_value:
            return False

    ts_value = vote_ts or f"{vote_date}T00:00:00"
    votes[composite_key] = {"vote_value": vote_value, "ts": ts_value, "week_id": week_id}

    if WEEKLY_STATE_KEY not in cache or not isinstance(cache[WEEKLY_STATE_KEY], dict):
        cache[WEEKLY_STATE_KEY] = {}
    if week_id not in cache[WEEKLY_STATE_KEY] or not isinstance(cache[WEEKLY_STATE_KEY][week_id], dict):
        cache[WEEKLY_STATE_KEY][week_id] = {}

    week_payload = cache[WEEKLY_STATE_KEY][week_id]
    votes_key = "votes_by_date_chat"
    if votes_key not in week_payload or not isinstance(week_payload[votes_key], dict):
        week_payload[votes_key] = {}
    week_payload[votes_key][composite_key] = vote_value

    _write_cache(cache)
    return True


def get_weekly_vote_stats(week_id: str) -> Dict[str, int]:
    state = load_weekly_state()
    week_payload = state.get(week_id, {})
    votes = week_payload.get("votes_by_date_chat", {}) if isinstance(week_payload, dict) else {}
    stats = {"yes": 0, "partial": 0, "no": 0}
    if not isinstance(votes, dict):
        return stats

    for vote in votes.values():
        if vote in stats:
            stats[vote] += 1
    return stats


def get_week_vote_accuracy(week_id: str, chat_id: Optional[str] = None) -> Dict[str, float]:
    cache = load_cache()
    votes = cache.get(DAILY_VOTES_KEY, {})
    stats = {"yes_count": 0, "partial_count": 0, "no_count": 0, "total": 0, "accuracy": 0.0}
    if not isinstance(votes, dict):
        return stats

    for composite_key, payload in votes.items():
        if not isinstance(payload, dict):
            continue
        if payload.get("week_id") != week_id:
            continue
        if chat_id and not composite_key.endswith(f"|{chat_id}"):
            continue
        vote_value = payload.get("vote_value")
        if vote_value == "yes":
            stats["yes_count"] += 1
        elif vote_value == "partial":
            stats["partial_count"] += 1
        elif vote_value == "no":
            stats["no_count"] += 1

    total = stats["yes_count"] + stats["partial_count"] + stats["no_count"]
    stats["total"] = total
    if total > 0:
        stats["accuracy"] = (stats["yes_count"] + 0.5 * stats["partial_count"]) / total
    return stats


def _composite_key(chat_id: str, value_date: str) -> str:
    return f"{value_date}|{chat_id}"


def get_today_vote(chat_id: str, vote_date: str) -> Optional[Dict[str, Any]]:
    cache = load_cache()
    votes = cache.get(TODAY_VOTES_KEY, {})
    raw = votes.get(_composite_key(chat_id, vote_date)) if isinstance(votes, dict) else None
    if isinstance(raw, dict) and raw.get("vote") in {"yes", "partial", "no"}:
        return raw
    return None


def upsert_today_vote(chat_id: str, vote_date: str, vote_value: str, vote_ts: str) -> bool:
    cache, _ = _load_local_cache()
    votes = cache.get(TODAY_VOTES_KEY)
    if not isinstance(votes, dict):
        votes = {}
        cache[TODAY_VOTES_KEY] = votes
    key = _composite_key(chat_id, vote_date)
    existing = votes.get(key)
    if isinstance(existing, dict) and existing.get("vote") in {"yes", "partial", "no"}:
        if existing.get("vote") == vote_value:
            return False
    votes[key] = {"vote": vote_value, "ts": vote_ts}
    _write_cache(cache)
    return True


def upsert_today_state(chat_id: str, value_date: str, state_payload: Dict[str, Any]) -> Dict[str, Any]:
    cache, _ = _load_local_cache()
    state = cache.get(TODAY_STATE_KEY)
    if not isinstance(state, dict):
        state = {}
        cache[TODAY_STATE_KEY] = state

    key = _composite_key(chat_id, value_date)
    existing = state.get(key)
    if isinstance(existing, dict):
        return existing

    payload = {
        "status_tag": str(state_payload.get("status_tag", "steady")),
        "confidence": float(state_payload.get("confidence", 0.0)),
        "amplitude": float(state_payload.get("amplitude", 0.0)),
        "accent_hex": str(state_payload.get("accent_hex", "#808080")),
        "week_id": str(state_payload.get("week_id", "")),
    }
    state[key] = payload
    _write_cache(cache)
    return payload


def get_today_state(chat_id: str, value_date: str) -> Optional[Dict[str, Any]]:
    cache = load_cache()
    state = cache.get(TODAY_STATE_KEY, {})
    raw = state.get(_composite_key(chat_id, value_date)) if isinstance(state, dict) else None
    return raw if isinstance(raw, dict) else None


def get_today_vote_accuracy(week_id: str, chat_id: Optional[str] = None) -> Dict[str, float]:
    cache = load_cache()
    votes = cache.get(TODAY_VOTES_KEY, {})
    states = cache.get(TODAY_STATE_KEY, {})
    stats = {
        "yes_count": 0,
        "partial_count": 0,
        "no_count": 0,
        "total": 0,
        "accuracy": 0.0,
        "yes_by_rarity": {"common": 0, "rare": 0, "exotic": 0},
    }
    if not isinstance(votes, dict):
        return stats

    weekly = load_weekly_state()
    rarity_by_week = {
        key: value.get("rarity_level", "common")
        for key, value in weekly.items()
        if isinstance(value, dict)
    }

    for composite_key, payload in votes.items():
        if chat_id and not composite_key.endswith(f"|{chat_id}"):
            continue
        if not isinstance(payload, dict):
            continue

        state = states.get(composite_key) if isinstance(states, dict) else None
        if not isinstance(state, dict):
            continue
        if state.get("week_id") != week_id:
            continue

        vote_value = payload.get("vote")
        if vote_value == "yes":
            stats["yes_count"] += 1
            rarity = rarity_by_week.get(week_id, "common")
            if rarity in stats["yes_by_rarity"]:
                stats["yes_by_rarity"][rarity] += 1
        elif vote_value == "partial":
            stats["partial_count"] += 1
        elif vote_value == "no":
            stats["no_count"] += 1

    total = stats["yes_count"] + stats["partial_count"] + stats["no_count"]
    stats["total"] = total
    if total > 0:
        stats["accuracy"] = (stats["yes_count"] + 0.5 * stats["partial_count"]) / total
    return stats


def mark_slot_sent(chat_id: str, send_date: str, slot: str, sent_ts: str) -> None:
    mark_sent_record(
        chat_id=chat_id,
        send_date=send_date,
        slot=slot,
        message_type="verdict",
        sent_ts=sent_ts,
        trigger_source="legacy",
        run_id="",
    )


def _sent_key(chat_id: str, send_date: str, slot: str, message_type: str) -> str:
    return f"{send_date}|{chat_id}|{slot}|{message_type}"


def mark_sent_record(
    chat_id: str,
    send_date: str,
    slot: str,
    message_type: str,
    sent_ts: str,
    trigger_source: str,
    run_id: str,
    manual_preview: bool = False,
) -> None:
    if FIRESTORE.enabled:
        key = _sent_key(chat_id=chat_id, send_date=send_date, slot=slot, message_type=message_type)
        FIRESTORE.set_sent(
            chat_id,
            key,
            {
                "sent_at": sent_ts,
                "slot": slot,
                "msg_type": message_type,
                "trigger_source": trigger_source,
                "run_id": run_id,
                "manual_preview": bool(manual_preview),
                "date_msk": send_date,
            },
        )
    cache, _ = _load_local_cache()
    state = cache.get(PUSH_STATE_KEY)
    if not isinstance(state, dict):
        state = {}
        cache[PUSH_STATE_KEY] = state
    key = _sent_key(chat_id=chat_id, send_date=send_date, slot=slot, message_type=message_type)
    state[key] = {
        "ts": sent_ts,
        "run_id": run_id,
        "trigger_source": trigger_source,
        "manual_preview": bool(manual_preview),
    }
    _write_cache(cache)


def was_slot_sent(chat_id: str, send_date: str, slot: str) -> bool:
    return was_sent_record(chat_id=chat_id, send_date=send_date, slot=slot, message_type="verdict")


def was_sent_record(chat_id: str, send_date: str, slot: str, message_type: str) -> bool:
    if FIRESTORE.enabled:
        key = _sent_key(chat_id=chat_id, send_date=send_date, slot=slot, message_type=message_type)
        if FIRESTORE.get_sent(chat_id, key):
            return True
    cache = load_cache()
    state = cache.get(PUSH_STATE_KEY, {})
    if not isinstance(state, dict):
        return False
    key = _sent_key(chat_id=chat_id, send_date=send_date, slot=slot, message_type=message_type)
    return key in state


def get_today_sent_registry(chat_id: str, send_date: str) -> Dict[str, Dict[str, Any]]:
    cache = load_cache()
    state = cache.get(PUSH_STATE_KEY, {})
    if not isinstance(state, dict):
        return {}
    result: Dict[str, Dict[str, Any]] = {}
    prefix = f"{send_date}|{chat_id}|"
    for key, payload in state.items():
        if not key.startswith(prefix):
            continue
        result[key] = payload if isinstance(payload, dict) else {"ts": ""}
    return result


def get_sent_registry_for_date(send_date: str) -> Dict[str, Dict[str, Any]]:
    cache = load_cache()
    state = cache.get(PUSH_STATE_KEY, {})
    if not isinstance(state, dict):
        return {}
    prefix = f"{send_date}|"
    return {k: (v if isinstance(v, dict) else {"ts": ""}) for k, v in state.items() if k.startswith(prefix)}


def get_user_prefs(chat_id: str) -> Dict[str, Any]:
    cache = load_cache()
    state = cache.get(USER_PREFS_KEY, {})
    if not isinstance(state, dict):
        return {}
    raw = state.get(chat_id)
    return raw if isinstance(raw, dict) else {}


def upsert_user_prefs(chat_id: str, prefs: Dict[str, Any]) -> Dict[str, Any]:
    cache, _ = _load_local_cache()
    state = cache.get(USER_PREFS_KEY)
    if not isinstance(state, dict):
        state = {}
        cache[USER_PREFS_KEY] = state
    existing = state.get(chat_id) if isinstance(state.get(chat_id), dict) else {}
    merged = {**existing, **prefs}
    state[chat_id] = merged
    _write_cache(cache)
    return merged


def mark_weekly_report_sent(chat_id: str, week_id: str, sent_ts: str) -> None:
    if FIRESTORE.enabled:
        FIRESTORE.set_sent(
            chat_id,
            f"weekly|{week_id}|{chat_id}",
            {"sent_at": sent_ts, "slot": "weekly", "msg_type": "weekly_map", "date_msk": ""},
        )
    cache, _ = _load_local_cache()
    state = cache.get(PUSH_STATE_KEY)
    if not isinstance(state, dict):
        state = {}
        cache[PUSH_STATE_KEY] = state
    key = f"weekly|{week_id}|{chat_id}"
    state[key] = {"ts": sent_ts}
    _write_cache(cache)


def was_weekly_report_sent(chat_id: str, week_id: str) -> bool:
    if FIRESTORE.enabled:
        if FIRESTORE.get_sent(chat_id, f"weekly|{week_id}|{chat_id}"):
            return True
    cache = load_cache()
    state = cache.get(PUSH_STATE_KEY, {})
    if not isinstance(state, dict):
        return False
    key = f"weekly|{week_id}|{chat_id}"
    return key in state


def log_refresh_attempt(
    chat_id: str,
    refresh_date: str,
    had_updates: bool,
    updated_blocks: list[str],
    message: str,
    refresh_ts: str,
) -> None:
    cache, _ = _load_local_cache()
    state = cache.get(REFRESH_STATE_KEY)
    if not isinstance(state, dict):
        state = {}
        cache[REFRESH_STATE_KEY] = state
    key = _composite_key(chat_id, refresh_date)
    existing = state.get(key) if isinstance(state.get(key), dict) else {}
    count = int(existing.get("count", 0)) + 1
    state[key] = {
        "count": count,
        "had_updates": bool(had_updates),
        "updated_blocks": updated_blocks[:8],
        "message": message,
        "ts": refresh_ts,
    }
    _write_cache(cache)


def get_refresh_state(chat_id: str, refresh_date: str) -> Optional[Dict[str, Any]]:
    cache = load_cache()
    state = cache.get(REFRESH_STATE_KEY, {})
    raw = state.get(_composite_key(chat_id, refresh_date)) if isinstance(state, dict) else None
    return raw if isinstance(raw, dict) else None


def log_sync_trace(run_id: str, trace: Dict[str, Any]) -> None:
    cache, _ = _load_local_cache()
    state = cache.get(SYNC_DEBUG_KEY)
    if not isinstance(state, dict):
        state = {}
        cache[SYNC_DEBUG_KEY] = state
    state[run_id] = trace
    recent = sorted(state.keys())[-20:]
    cache[SYNC_DEBUG_KEY] = {k: state[k] for k in recent}
    _write_cache(cache)


def get_latest_sync_trace() -> Optional[Dict[str, Any]]:
    cache = load_cache()
    state = cache.get(SYNC_DEBUG_KEY)
    if not isinstance(state, dict) or not state:
        return None
    latest_key = sorted(state.keys())[-1]
    payload = state.get(latest_key)
    if isinstance(payload, dict):
        return payload
    return None


def get_garmin_auth_state(chat_id: str = DEFAULT_CHAT_SCOPE) -> Dict[str, Any]:
    if FIRESTORE.enabled:
        return FIRESTORE.get_auth(chat_id, provider="garmin")
    cache = load_cache()
    state = cache.get(AUTH_STATE_KEY, {})
    if not isinstance(state, dict):
        return {}
    raw = state.get(chat_id)
    return raw if isinstance(raw, dict) else {}


def upsert_garmin_auth_state(payload: Dict[str, Any], chat_id: str = DEFAULT_CHAT_SCOPE) -> None:
    if FIRESTORE.enabled:
        FIRESTORE.set_auth(chat_id, payload, provider="garmin")
    cache, _ = _load_local_cache()
    state = cache.get(AUTH_STATE_KEY)
    if not isinstance(state, dict):
        state = {}
        cache[AUTH_STATE_KEY] = state
    state[chat_id] = payload
    _write_cache(cache)

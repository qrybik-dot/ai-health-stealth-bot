import json
import os
import requests
from datetime import date, timedelta
from typing import Any, Dict, Optional, Tuple

CACHE_FILE = "cache.json"
MEMORY_DAYS = 120
WEEKLY_STATE_KEY = "_weekly_state"
DAILY_VOTES_KEY = "_daily_votes"
TODAY_VOTES_KEY = "_today_votes"
TODAY_STATE_KEY = "_today_state"
PUSH_STATE_KEY = "_push_state"


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
                return {
                }, {
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
            return {}, {
                "source": "gist",
                "available": False,
                "error": "gist_exception",
                "detail": str(e),
                "token_present": token_present,
                "token_source": token_source,
            }

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
            if isinstance(cache, dict):
                return cache, {"source": "local", "available": True, "error": ""}
            return {}, {"source": "local", "available": False, "error": "local_not_dict"}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}, {"source": "local", "available": False, "error": "local_missing_or_invalid"}


def load_cache() -> Dict[str, Any]:
    cache, _meta = load_cache_with_meta()
    return cache


def _write_cache(cache: Dict[str, Any]) -> None:
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def save_daily_snapshot(snapshot_data: Dict[str, Any]) -> None:
    today_str = date.today().strftime("%Y-%m-%d")
    cache = load_cache()

    if today_str not in cache:
        cache[today_str] = {}
    cache[today_str].update(snapshot_data)

    pruned_cache = {}
    cutoff_date = date.today() - timedelta(days=MEMORY_DAYS)

    for date_str, data in cache.items():
        if date_str.startswith("_"):
            pruned_cache[date_str] = data
            continue
        try:
            entry_date = date.fromisoformat(date_str)
            if entry_date >= cutoff_date:
                pruned_cache[date_str] = data
        except (ValueError, TypeError):
            pass

    _write_cache(pruned_cache)


def load_weekly_state() -> Dict[str, Any]:
    cache = load_cache()
    state = cache.get(WEEKLY_STATE_KEY, {})
    if isinstance(state, dict):
        return state
    return {}


def save_weekly_state(week_id: str, weekly_payload: Dict[str, Any]) -> None:
    cache = load_cache()
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
    cache = load_cache()
    votes = _load_daily_votes(cache)
    composite_key = f"{vote_date}|{chat_id}"
    existing = votes.get(composite_key)
    if isinstance(existing, dict) and existing.get("vote_value") in {"yes", "partial", "no"}:
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
    cache = load_cache()
    votes = cache.get(TODAY_VOTES_KEY)
    if not isinstance(votes, dict):
        votes = {}
        cache[TODAY_VOTES_KEY] = votes
    key = _composite_key(chat_id, vote_date)
    existing = votes.get(key)
    if isinstance(existing, dict) and existing.get("vote") in {"yes", "partial", "no"}:
        return False
    votes[key] = {"vote": vote_value, "ts": vote_ts}
    _write_cache(cache)
    return True


def upsert_today_state(chat_id: str, value_date: str, state_payload: Dict[str, Any]) -> Dict[str, Any]:
    cache = load_cache()
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
    cache = load_cache()
    state = cache.get(PUSH_STATE_KEY)
    if not isinstance(state, dict):
        state = {}
        cache[PUSH_STATE_KEY] = state
    key = f"{send_date}|{chat_id}|{slot}"
    state[key] = {"ts": sent_ts}
    _write_cache(cache)


def was_slot_sent(chat_id: str, send_date: str, slot: str) -> bool:
    cache = load_cache()
    state = cache.get(PUSH_STATE_KEY, {})
    if not isinstance(state, dict):
        return False
    key = f"{send_date}|{chat_id}|{slot}"
    return key in state


def mark_weekly_report_sent(chat_id: str, week_id: str, sent_ts: str) -> None:
    cache = load_cache()
    state = cache.get(PUSH_STATE_KEY)
    if not isinstance(state, dict):
        state = {}
        cache[PUSH_STATE_KEY] = state
    key = f"weekly|{week_id}|{chat_id}"
    state[key] = {"ts": sent_ts}
    _write_cache(cache)


def was_weekly_report_sent(chat_id: str, week_id: str) -> bool:
    cache = load_cache()
    state = cache.get(PUSH_STATE_KEY, {})
    if not isinstance(state, dict):
        return False
    key = f"weekly|{week_id}|{chat_id}"
    return key in state

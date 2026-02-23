import json
import os
import requests
from datetime import date, timedelta
from typing import Any, Dict, Optional

CACHE_FILE = "cache.json"
MEMORY_DAYS = 120
WEEKLY_STATE_KEY = "_weekly_state"
DAILY_VOTES_KEY = "_daily_votes"
PUSH_STATE_KEY = "_push_state"


def load_cache() -> Dict[str, Any]:
    """
    If CACHE_GIST_ID env var is set, fetches the cache from the Gist.
    Otherwise, reads the local cache.json file.
    This allows the Render server to get the latest cache from GitHub Actions.
    """
    gist_id = os.getenv("CACHE_GIST_ID")

    if gist_id:
        try:
            api_url = f"https://api.github.com/gists/{gist_id}"
            response = requests.get(api_url, timeout=10)
            response.raise_for_status()
            gist_data = response.json()
            content = gist_data["files"]["cache.json"]["content"]
            return json.loads(content)
        except (requests.RequestException, KeyError, json.JSONDecodeError) as e:
            print(f"Error fetching or parsing cache from Gist: {e}")
            return {"error": "gist_fetch_failed", "detail": str(e)}

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


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

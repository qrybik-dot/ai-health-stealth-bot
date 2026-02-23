import json
import os
import requests
from datetime import date, timedelta
from typing import Any, Dict

CACHE_FILE = "cache.json"
MEMORY_DAYS = 120
WEEKLY_STATE_KEY = "_weekly_state"


def load_cache() -> Dict[str, Any]:
    """
    If CACHE_GIST_ID env var is set, fetches the cache from the Gist.
    Otherwise, reads the local cache.json file.
    This allows the Render server to get the latest cache from GitHub Actions.
    """
    gist_id = os.getenv("CACHE_GIST_ID")

    if gist_id:
        # Fetch from Gist for server environment
        try:
            api_url = f"https://api.github.com/gists/{gist_id}"
            # Use a short timeout to prevent hanging
            response = requests.get(api_url, timeout=10)
            response.raise_for_status()
            gist_data = response.json()
            # Assuming the file in Gist is named 'cache.json'
            content = gist_data["files"]["cache.json"]["content"]
            return json.loads(content)
        except (requests.RequestException, KeyError, json.JSONDecodeError) as e:
            # On failure, return an empty dict with an error for debugging
            print(f"Error fetching or parsing cache from Gist: {e}")
            return {"error": "gist_fetch_failed", "detail": str(e)}
    else:
        # Read from local file for sync/local testing
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}


def save_daily_snapshot(snapshot_data: Dict[str, Any]) -> None:
    """
    Reads the entire cache, updates the entry for today's date,
    prunes old entries, and writes it back.
    """
    today_str = date.today().strftime("%Y-%m-%d")
    cache = load_cache()

    # Add/update today's snapshot.
    # We preserve any existing keys for the day, in case partial data was written.
    if today_str not in cache:
        cache[today_str] = {}
    cache[today_str].update(snapshot_data)
    
    # Prune old entries to maintain a 120-day rolling window.
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
            # Ignore entries with invalid date keys.
            pass

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(pruned_cache, f, ensure_ascii=False, indent=2)


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

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def upsert_color_vote(chat_id: str, vote_date: str, vote_value: str, week_id: str) -> None:
    cache = load_cache()
    if WEEKLY_STATE_KEY not in cache or not isinstance(cache[WEEKLY_STATE_KEY], dict):
        cache[WEEKLY_STATE_KEY] = {}
    if week_id not in cache[WEEKLY_STATE_KEY] or not isinstance(cache[WEEKLY_STATE_KEY][week_id], dict):
        cache[WEEKLY_STATE_KEY][week_id] = {}

    week_payload = cache[WEEKLY_STATE_KEY][week_id]
    votes_key = "votes_by_date_chat"
    if votes_key not in week_payload or not isinstance(week_payload[votes_key], dict):
        week_payload[votes_key] = {}

    composite_key = f"{vote_date}|{chat_id}"
    week_payload[votes_key][composite_key] = vote_value

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


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

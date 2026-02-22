import json
import os
import requests
from datetime import date, timedelta
from typing import Any, Dict

CACHE_FILE = "cache.json"
MEMORY_DAYS = 120


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
        try:
            entry_date = date.fromisoformat(date_str)
            if entry_date >= cutoff_date:
                pruned_cache[date_str] = data
        except (ValueError, TypeError):
            # Ignore entries with invalid date keys.
            pass

    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(pruned_cache, f, ensure_ascii=False, indent=2)

import json
from datetime import date, timedelta
from typing import Any, Dict

CACHE_FILE = "cache.json"
MEMORY_DAYS = 120


def load_cache() -> Dict[str, Any]:
    """Reads the full JSON cache from disk. Returns empty dict if not found."""
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

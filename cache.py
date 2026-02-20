"""Cache for Garmin metrics. Persisted as cache.json for artifact handoff between workflows."""
import json
import time
from typing import Any, Dict

CACHE_FILE = "cache.json"


def write_cache(data: Dict[str, Any]) -> None:
    data["cached_at_unix"] = int(time.time())
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def write_minimal_error_cache(reason: str, detail: str) -> None:
    """Write a minimal cache so sync workflow still has an artifact to upload on total failure."""
    write_cache({
        "source": "garmin",
        "error": reason,
        "errors": [{"metric": "sync", "error": detail}],
        "fetched_at_utc": None,
        "cached_at_unix": int(time.time()),
    })


def read_cache() -> Dict[str, Any]:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"cached_at_unix": None, "error": "cache_missing"}

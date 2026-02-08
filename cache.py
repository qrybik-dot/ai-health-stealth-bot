import json
import time
from typing import Any, Dict

CACHE_FILE = "cache.json"

def write_cache(data: Dict[str, Any]) -> None:
    data["cached_at_unix"] = int(time.time())
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def read_cache() -> Dict[str, Any]:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"cached_at_unix": None, "error": "cache_missing"}

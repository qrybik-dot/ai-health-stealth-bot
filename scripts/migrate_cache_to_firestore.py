import json
from pathlib import Path

from firestore_store import STORE

CACHE_FILE = Path("cache.json")
CHAT_ID = "default"


def is_day_key(key: str) -> bool:
    return len(key) == 10 and key[4] == "-" and key[7] == "-"


def main() -> None:
    if not STORE.enabled:
        raise SystemExit("Firestore disabled: set FIRESTORE_PROJECT_ID and credentials")
    if not CACHE_FILE.exists():
        raise SystemExit("cache.json not found")

    cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    days = 0
    for key, value in cache.items():
        if not isinstance(key, str) or not is_day_key(key):
            continue
        if not isinstance(value, dict):
            continue
        STORE.upsert_day(CHAT_ID, key, value)
        days += 1

    push_state = cache.get("_push_state", {})
    sent = 0
    if isinstance(push_state, dict):
        for key, value in push_state.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            parts = key.split("|")
            if len(parts) >= 4:
                _, chat_id, slot, msg_type = parts[0], parts[1], parts[2], parts[3]
            else:
                chat_id, slot, msg_type = CHAT_ID, "unknown", "verdict"
            STORE.set_sent(chat_id, key, {
                "sent_at": value.get("ts", ""),
                "slot": slot,
                "msg_type": msg_type,
                "trigger_source": value.get("trigger_source", "legacy"),
                "run_id": value.get("run_id", ""),
            })
            sent += 1

    print(f"migrated days={days} sent={sent}")


if __name__ == "__main__":
    main()

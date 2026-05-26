import json
import os
import sys
import requests
from datetime import datetime, timezone


def env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing env var: {name}")
    return v


STATE_KEYS = ("_push_state", "_weekly_state", "_daily_votes", "_today_votes", "_today_state")


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "coach-potato-gist-upload",
    }


def _load_remote_cache(gist_id: str, token: str) -> dict:
    resp = requests.get(f"https://api.github.com/gists/{gist_id}", headers=_headers(token), timeout=30)
    print("remote cache status:", resp.status_code)
    if resp.status_code >= 300:
        raise RuntimeError(f"Failed to read current gist before upload: {resp.status_code}")
    gist = resp.json()
    file_payload = gist.get("files", {}).get("cache.json", {})
    content = file_payload.get("content", "")
    if file_payload.get("truncated"):
        raw_url = file_payload.get("raw_url")
        if not raw_url:
            raise RuntimeError("cache.json raw_url missing for truncated remote gist")
        raw_resp = requests.get(raw_url, headers=_headers(token), timeout=30)
        print("remote raw cache status:", raw_resp.status_code)
        raw_resp.raise_for_status()
        content = raw_resp.text
    if not content:
        return {}
    parsed = json.loads(content)
    return parsed if isinstance(parsed, dict) else {}


def _assert_no_state_loss(local_cache: dict, remote_cache: dict) -> None:
    if os.getenv("ALLOW_GIST_STATE_DROP", "").strip().lower() in ("1", "true", "yes"):
        print("state loss guard: bypassed by ALLOW_GIST_STATE_DROP")
        return
    problems = []
    for key in STATE_KEYS:
        remote_state = remote_cache.get(key)
        local_state = local_cache.get(key)
        if not isinstance(remote_state, dict) or len(remote_state) == 0:
            continue
        if not isinstance(local_state, dict):
            problems.append(f"{key}:missing_local_remote_count={len(remote_state)}")
            continue
        if len(local_state) < len(remote_state):
            problems.append(f"{key}:local_count={len(local_state)}<remote_count={len(remote_state)}")
    if problems:
        raise RuntimeError("state loss guard blocked gist upload: " + ",".join(problems))
    counts = {
        key: len(local_cache.get(key, {})) if isinstance(local_cache.get(key), dict) else 0
        for key in STATE_KEYS
    }
    print("state loss guard: ok " + " ".join(f"{key}={value}" for key, value in counts.items()))


def main() -> None:
    gist_id = env("CACHE_GIST_ID")
    token = None
    token_source = ""
    for source_name in ("GIST_TOKEN", "GIST_SYNC_TOKEN", "GITHUB_TOKEN"):
        source_value = os.getenv(source_name)
        if source_value:
            token = source_value
            token_source = source_name
            break
    if not token:
        raise RuntimeError("Missing env var: one of GIST_TOKEN, GIST_SYNC_TOKEN, GITHUB_TOKEN")

    print(f"gist upload auth: selected_token_source={token_source}")

    cache_path = "cache.json"
    if not os.path.exists(cache_path):
        raise RuntimeError("cache.json not found (sync step did not produce it)")

    guard_required = os.getenv("REQUIRE_RECOVERY_UPLOAD_OK", "").strip().lower() in ("1", "true", "yes")
    guard_path = os.getenv("RECOVERY_UPLOAD_OK_FILE", ".recovery_ok_to_upload")
    if guard_required and not os.path.exists(guard_path):
        raise RuntimeError(f"recovery upload guard missing: {guard_path}")

    with open(cache_path, "r", encoding="utf-8") as f:
        content = f.read()
    local_cache = json.loads(content)
    if not isinstance(local_cache, dict):
        raise RuntimeError("cache.json must contain a JSON object")
    remote_cache = _load_remote_cache(gist_id, token)
    _assert_no_state_loss(local_cache, remote_cache)

    print(f"gist upload file=cache.json bytes={len(content.encode('utf-8'))} ts_utc={datetime.now(timezone.utc).isoformat()}")

    payload = {
        "files": {
            "cache.json": {
                "content": content
            }
        }
    }

    resp = requests.patch(
        f"https://api.github.com/gists/{gist_id}",
        headers=_headers(token),
        json=payload,
        timeout=30,
    )

    print("Status:", resp.status_code)
    print("Response:", resp.text)

    if resp.status_code >= 300:
        raise SystemExit("Failed to update gist")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        raise

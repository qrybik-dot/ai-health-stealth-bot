import os
import sys
import requests
from datetime import datetime, timezone


def env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing env var: {name}")
    return v


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

    with open(cache_path, "r", encoding="utf-8") as f:
        content = f.read()

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
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
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

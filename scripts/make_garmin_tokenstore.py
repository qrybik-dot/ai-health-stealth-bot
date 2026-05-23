#!/usr/bin/env python3
"""Create a Garmin tokenstore with one interactive login attempt."""

from __future__ import annotations

import sys
from getpass import getpass

from garminconnect import Garmin


def _session(api: Garmin):
    return getattr(api, "client", None) or getattr(api, "garth", None)


def main() -> int:
    email = input("Garmin email: ").strip()
    password = getpass("Garmin password: ")

    def prompt_mfa() -> str:
        return input("Garmin MFA code: ").strip()

    try:
        api = Garmin(email, password, prompt_mfa=prompt_mfa)
        api.login()
        session = _session(api)
        if session is None or not hasattr(session, "dumps"):
            raise RuntimeError("Garmin session cannot serialize tokenstore")
        print(session.dumps())
        return 0
    except Exception as exc:
        print(f"Garmin tokenstore creation failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        if "429" in str(exc):
            print(
                "429 means Garmin rejected this login strategy. Do not retry in a loop; "
                "try another network/device or use the official export/API paths.",
                file=sys.stderr,
            )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

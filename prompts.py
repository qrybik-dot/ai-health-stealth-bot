import os

SYSTEM_PROMPT = os.getenv("GEMINI_SYSTEM_PROMPT", "").strip()
if not SYSTEM_PROMPT:
    raise RuntimeError("Missing GEMINI_SYSTEM_PROMPT (put it in GitHub Secrets)")

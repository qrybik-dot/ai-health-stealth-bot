# Working Bot Recovery Sequence

Дата: 2026-04-26  
Режим: audit-only plan.

## Goal

Вернуть рабочий Coach Potato без широкого rewrite:

- fresh current-day Garmin data;
- recent history/memory;
- morning/midday/evening push;
- weekly/history/compare;
- personal-data Q&A;
- Gemini interpretation fallback;
- no Render critical path;
- no paid always-on hosting.

## Recovery Principle

Сначала восстановить данные, потом runtime.

Если chat runtime поднять раньше sync/history, бот будет честно отвечать, что данных нет. Это технически живой endpoint, но продуктово бесполезный бот.

## Step 1: Data And Push Recovery

Implementation PR: PR2.

### Objective

Сделать существующий GitHub Actions path достаточно безопасным, чтобы:

- получить fresh today snapshot;
- не потерять старую историю;
- выполнить controlled backfill;
- отправлять scheduled pushes не из пустого кэша.

### Why first

Это самый маленький путь к ежедневной ценности:

- workflow уже есть;
- Python 3.11 уже работает;
- tests pass;
- push schedule passes self-check;
- Render не нужен для scheduled push.

### Expected implementation

- Harden `.github/workflows/sync.yml` or add `recovery.yml`.
- Ensure `GIST_TOKEN` is available when sync reads cache, not only when upload writes cache.
- Add manual backfill mode with `backfill_days`.
- Add cache check after sync/backfill.
- Keep Gist as transitional bridge.
- Keep Firestore optional.

### Manual run order

1. Configure secrets.
2. Run cache check.
3. Run sync once.
4. Confirm `has_today=true`.
5. Run backfill 7.
6. If Garmin auth/rate limits are stable, run backfill 30.
7. If stable, run backfill 90.
8. Run scheduled push manually/dry where possible.
9. Watch Telegram output.

### Rollback

- Disable scheduled workflows.
- Keep Gist untouched until new write path is proven.
- Restore previous Gist revision if upload corrupts cache.

## Step 2: Chat Runtime Without Render

Implementation PR: PR3.

### Objective

Restore commands, buttons and free-text Q&A without paid always-on hosting.

### Recommended transitional architecture

Telegram polling via GitHub Actions:

- `python main.py poll-once`
- scheduled workflow every few minutes
- same Python command/callback/free-text logic
- same Gemini fallback
- offset persisted in cache/Gist/Firestore

### Why not Cloudflare Worker first

Cloudflare Worker is a good target, but not the smallest safe next move:

- current router is Python/FastAPI;
- current storage helpers are Python;
- current Gemini prompt/routing code is Python;
- Worker would require porting behavior before the bot is even data-fresh.

Polling keeps the implementation local to existing code and avoids a cross-language migration in recovery mode.

### Required behavior

- `/today`, `/color`, `/week`, `/stats`, `/refresh`, `/debug_sync`, `/debug_sent` work.
- callbacks Why/Facts/Roast/15m work.
- free-text questions use deterministic-first routing.
- Gemini fallback still works for open questions.
- duplicate updates are prevented by offset persistence.

### Manual setup

- Call Telegram `deleteWebhook` before enabling polling.
- Confirm bot receives updates via `getUpdates`.
- Enable polling workflow.

### Rollback

- Disable polling workflow.
- Re-enable webhook later if a proper runtime exists.

## Step 3: Durable Store Hardening

Implementation PR: PR4.

### Objective

Stop treating Gist as long-term source of truth.

### Minimal option

Use existing Firestore code:

- configure `FIRESTORE_PROJECT_ID`;
- configure service account credentials;
- migrate cache to Firestore;
- verify days/sent/auth collections;
- keep Gist as fallback during rollback window.

### Later option

Cloudflare D1/KV:

- good future fit if Worker becomes the runtime;
- should not be mixed into PR2/PR3;
- requires schema/test parity work.

## What Can Stay As-Is During Recovery

- Current message rendering.
- Current deterministic-first Q&A.
- Gemini fallback.
- GitHub Actions push schedule.
- Garmin sync implementation.
- Gist bridge.
- Firestore code.
- Miniapp scaffold.

## What Must Be Deferred

- Cloudflare Worker webhook.
- D1/KV migration.
- removing Gist.
- removing Firestore.
- broad TOV rewrite.
- moving Garmin sync out of GitHub Actions.
- miniapp product work.

## Definition Of Working Bot

Minimum:

- sync produced today snapshot;
- bot sends morning/midday/evening scheduled messages;
- weekly works with available history and degrades honestly;
- `/refresh` works through the restored chat path or manual Actions path;
- free-text Q&A about personal data works with deterministic-first answers and Gemini fallback;
- no Render dependency.

Better:

- 30-90 days history available;
- duplicate pushes prevented;
- no Gist-only source of truth;
- chat runtime has acceptable latency.

## Exact First Implementation PR After This Audit

PR2 implementation should be:

**GitHub Actions recovery guardrails for sync, controlled backfill and cache preservation.**

Files likely in scope:

- `.github/workflows/sync.yml`
- optionally `.github/workflows/recovery.yml`
- `README.md`
- `docs/MIGRATION_NOTES.md` or a new runbook doc

Avoid touching:

- `communication.py`
- Gemini prompt strategy
- Firestore implementation internals
- Gist removal
- Cloudflare code


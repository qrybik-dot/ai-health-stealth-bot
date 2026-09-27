# Current Architecture

Актуально: 2026-09-27.

## Production flow

```text
Garmin Connect
    ↓
GitHub Actions: Sync Garmin Cache (каждые 3 часа)
    ↓
local cache.json during job
    ↓
private GitHub Gist: cache.json
    ├── Garmin day snapshots
    ├── push/dedup state
    ├── votes/preferences/runtime state
    └── Garmin auth state
          ↓
    ┌───────────────┬─────────────────┐
    ↓               ↓
GitHub Push       Cloudflare Worker
scheduled        Telegram webhook/chat
    ↓               ↓
Telegram          Telegram
```

## Storage truth

Сегодня private Gist — единственная подтверждённая полная production-копия накопленной Garmin-истории.

Firestore implementation и verified migration tooling уже присутствуют, но Firestore не считается active source of truth до настройки credentials и успешного `firestore_archive_verification=ok`.

### Hard rule

Ни один cleanup не имеет права удалять исторический Garmin day snapshot, пока не существует независимо проверенной durable-копии.

## Data model

Daily snapshots имеют ключ `YYYY-MM-DD`. Runtime/service state использует ключи с префиксом `_`, включая push registry, weekly/today state, votes, prefs, auth и diagnostics.

Дневные Garmin snapshots нужны для долгосрочного анализа и считаются архивными данными, а не мусором.

Технические state/diagnostic записи могут иметь ограниченный retention после проверки, что они не нужны для dedup/recovery.

## Runtime

- Garmin sync и scheduled push: Python 3.11 в GitHub Actions.
- Interactive Telegram runtime: Cloudflare Worker.
- Conversational open-text fallback: Gemini из Python chat/polling path.
- Scheduled push: deterministic renderer; Gemini secrets ему не нужны.
- Pull Request tests: `.github/workflows/ci.yml`.
- GitHub runner image: `ubuntu-24.04`.

## Scheduling

- morning: 09:30 MSK, retries 10:30/11:30;
- midday: 14:07 MSK;
- evening: 20:07 MSK.

Delayed scheduled run сохраняет исходный slot. После stale boundary он пропускается и не переинтерпретируется как следующий slot.

## Durable archive target

Перед уменьшением Gist требуется:

1. Зафиксировать source day count, first/last date и dataset hash.
2. Перенести каждый Garmin day в durable database без DELETE.
3. Прочитать все source days обратно и верифицировать.
4. Проверить, что новые sync автоматически дописывают durable archive.
5. Только после этого отделять compact runtime cache/state от archive.

## Known blocker

Firestore archive migration готова кодом, но на 2026-09-27 в GitHub отсутствуют `FIRESTORE_PROJECT_ID` и `FIRESTORE_SERVICE_ACCOUNT_JSON`.

Текущий Cloudflare API token также не имеет подтверждённого D1 access. Поэтому Gist cleanup до устранения одного из этих credential blockers запрещён.

## Recovery

См. `docs/RECOVERY_RUNBOOK.md` и `docs/MIGRATION_NOTES.md`.

# Recovery Runbook

Цель: вернуть свежие Garmin-данные и usable history без Render, Cloudflare migration или storage rewrite.

## Required Secrets

В GitHub Actions должны быть заданы:

- `GARMIN_EMAIL`
- `GARMIN_PASSWORD`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `GEMINI_API_KEY`
- `GEMINI_MODEL`
- `CACHE_GIST_ID`
- `GIST_TOKEN`

`GIST_TOKEN` нужен не только для upload, но и для read path: private Gist нельзя надёжно читать через `GITHUB_TOKEN` репозитория. Если `GIST_TOKEN` отсутствует, workflow попробует fallback, но это не считается надёжной recovery-конфигурацией.

## Manual Recovery Workflow

Откройте GitHub Actions -> **Recovery Controls** -> **Run workflow**.

### 1. Cache Check

Run:

- `operation=cache-check`

Ожидаемые полезные строки:

```text
cache_source=...
cache_available=true
has_today=...
today_status=...
today_key_metrics_present=...
history_days_count=...
latest_history_day=...
cache_self_check=ok
```

Если `cache_available=false`, сначала чините `CACHE_GIST_ID` / `GIST_TOKEN`.

### 2. Today Sync

Run:

- `operation=sync`

Workflow выполнит:

1. `python main.py sync`
2. `python main.py cache-self-check --require-today --require-usable-today`
3. `python scripts/gist_upload.py`

Success означает:

- today key существует;
- today содержит хотя бы одну ключевую метрику;
- cache можно прочитать после merge/hydration;
- Gist обновлён только после успешной проверки.

### 3. Staged Backfill

Запускайте поэтапно:

1. `operation=backfill`, `backfill_days=7`
2. если Garmin auth/rate-limit стабильны: `backfill_days=30`
3. если стабильно: `backfill_days=90`

Workflow проверит:

```bash
python main.py cache-self-check --require-today --require-usable-today --min-history-days <days>
```

Если backfill падает на 30 или 90, не повторяйте сразу в цикле. Garmin может rate-limit авторизацию/API.

## Scheduled Sync Guardrail

Обычный `.github/workflows/sync.yml` теперь:

- передаёт `GIST_TOKEN` в `Run sync`, чтобы sync мог прочитать existing Gist;
- запускает `cache-self-check --require-today --require-usable-today`;
- загружает `cache.json` в Gist только после успешной проверки.

Это должно предотвращать “зелёный sync”, который на самом деле не восстановил usable today data.

## Rollback

- Если recovery upload испортил Gist, восстановите предыдущую Gist revision вручную.
- Если scheduled sync начал падать из-за Garmin auth/rate-limit, временно disable workflow schedule и чините auth.
- Не удаляйте Gist и Firestore paths в recovery phase: они нужны как rollback bridge.

## What This Does Not Restore

Этот runbook не возвращает interactive Telegram chat после Render. Chat runtime восстановится в следующем PR через отдельный механизм. Scheduled pushes и данные восстанавливаются первыми.

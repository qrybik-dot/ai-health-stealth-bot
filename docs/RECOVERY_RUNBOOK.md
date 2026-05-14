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

## Chat Polling Runtime

PR3 возвращает interactive Telegram chat без Render:

1. Убедитесь, что secrets заданы: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `CACHE_GIST_ID`, `GIST_TOKEN`, `GARMIN_EMAIL`, `GARMIN_PASSWORD`.
2. Вручную вызовите Telegram `deleteWebhook` для бота. Это обязательный шаг перед `getUpdates`; workflow сам webhook не отключает.
3. Запустите GitHub Actions -> **Chat Polling Runtime** -> **Run workflow**.
4. Проверьте лог `python main.py poll-once`: ожидается строка вида `poll_once fetched=... processed=... errors=... next_offset=...`.
5. После ручной проверки оставьте schedule `*/5 * * * *` включённым.

Offset хранится в `cache.json` под `_telegram_poll_state` и загружается в Gist после каждого polling run. Если отдельный update падает, offset всё равно продвигается, чтобы GitHub Actions не повторял один и тот же сломанный update бесконечно.

Rollback:

- Disable workflow **Chat Polling Runtime**.
- При возврате к webhook заново установите Telegram webhook на нужный runtime endpoint.
- Не удаляйте `_telegram_poll_state`: он безвреден и нужен для повторного включения polling.

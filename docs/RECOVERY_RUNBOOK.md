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
- `GARMIN_TOKENSTORE` — preferred serialized Garmin session tokenstore.

`GIST_TOKEN` нужен не только для upload, но и для read path: private Gist нельзя надёжно читать через `GITHUB_TOKEN` репозитория. Если `GIST_TOKEN` отсутствует, workflow попробует fallback, но это не считается надёжной recovery-конфигурацией.

`GARMIN_TOKENSTORE` нужен для token-first auth. Recovery workflow блокирует password fallback (`GARMIN_PASSWORD_FALLBACK=0`), чтобы не сжечь попытки входа и не получить Garmin 429 из-за повторных password login.

### Garmin Tokenstore Prep

Локально один раз:

```bash
python - <<'PY'
import os
from garminconnect import Garmin
api = Garmin(os.environ["GARMIN_EMAIL"], os.environ["GARMIN_PASSWORD"])
api.login()
print(api.garth.dumps())
PY
```

Скопируйте напечатанную строку в GitHub secret `GARMIN_TOKENSTORE`.

Альтернатива для локального запуска: сохранить токены в директорию и передать путь:

```bash
python - <<'PY'
import os
from garminconnect import Garmin
api = Garmin(os.environ["GARMIN_EMAIL"], os.environ["GARMIN_PASSWORD"])
api.login()
api.garth.dump("~/.garminconnect")
PY
export GARMIN_TOKENSTORE_PATH=~/.garminconnect
```

В логах recovery ищите:

```text
garmin_auth_token_source ... exists=true
garmin_auth_token_load_attempt ...
garmin_auth_token_load_succeeded ...
```

Если видите `garmin_auth_password_fallback_blocked`, tokenstore не найден или сломан. Если видите `garmin_auth_rate_limited_429`, не повторяйте recovery сразу.

### После Garmin 429 (обязательно)

1. Сразу выключите scheduled workflows: **Sync Garmin Cache** и **Push Daily Insights**.
2. Проверьте очередь запусков, чтобы не осталось автоповторов:

```bash
gh run list --workflow sync.yml --limit 20
gh run list --workflow push.yml --limit 20
```

3. Не повторяйте password login в CI/локально до окончания cooldown.
4. После cooldown заново создайте свежий `GARMIN_TOKENSTORE` и сохраните в GitHub Secret.
5. Включайте scheduled workflows только после успешного ручного `Recovery Controls -> sync`.

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
- Gist обновлён только после успешной проверки и наличия `.recovery_ok_to_upload`.

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

- требует `GARMIN_TOKENSTORE` до `python main.py sync` (иначе fail-fast);
- запускает sync с `GARMIN_PASSWORD_FALLBACK=0`, чтобы не уходить в password login;
- передаёт `GIST_TOKEN` в `Run sync`, чтобы sync мог прочитать existing Gist;
- запускает `cache-self-check --require-today --require-usable-today`;
- загружает `cache.json` в Gist только после успешной проверки.

Это должно предотвращать “зелёный sync”, который на самом деле не восстановил usable today data.

## Ops Health Summary

Для штатной проверки без recovery запускайте GitHub Actions -> **Ops Health Summary**.

Ожидаемые блоки:

```text
Secrets preflight
Cache and today
Push registry
Schedule
Telegram webhook
```

Нормальное состояние:

```text
cache_available=true
has_today=true
today_status=ready
today_key_metrics_present>=1
cache_self_check=ok
dry_run=true telegram_send=skipped
schedule-self-check ok
webhook_configured=True
pending_update_count=0
last_error_message=
```

Workflow намеренно не отправляет synthetic update в Cloudflare Worker. GitHub Actions origin может получать Cloudflare `1010`, хотя реальные Telegram updates проходят нормально. Для проверки реальных кнопок используйте `/today` в Telegram и нажмите `По фактам`, затем повторите **Ops Health Summary**.

## Rollback

- Если recovery upload испортил Gist, восстановите предыдущую Gist revision вручную.
- Если scheduled sync начал падать из-за Garmin auth/rate-limit, временно disable workflow schedule и чините auth.
- Не удаляйте Gist и Firestore paths в recovery phase: они нужны как rollback bridge.

## Chat Runtime

Основной runtime для интерактивного Telegram-чата: Cloudflare Worker webhook.

GitHub Actions polling оставлен только как ручной fallback. Не включайте scheduled polling вместе с Cloudflare webhook: polling вызывает `deleteWebhook` и ломает webhook-режим.

### Cloudflare Webhook Setup

1. Deploy Worker из каталога `cloudflare/`.
2. Задайте Worker secrets:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `WEBHOOK_SECRET`
   - `CACHE_GIST_ID`
   - `GIST_TOKEN`
   - optional: `GITHUB_DISPATCH_TOKEN`
   - optional: `GITHUB_REPO=qrybik-dot/ai-health-stealth-bot`
3. Проверьте health:

```text
https://<worker-url>/health
```

4. Установите Telegram webhook:

```text
https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook?url=https://<worker-url>/telegram/<WEBHOOK_SECRET>
```

5. Напишите боту `/help`, затем `/debug_health`, затем `/today`, затем нажмите `По фактам`.

### Cloudflare Deploy Workflow

Для deploy через GitHub Actions задайте secrets:

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`

Затем запустите GitHub Actions -> **Cloudflare Worker Deploy**:

1. `operation=dry-run` — проверить сборку Worker без публикации.
2. `operation=deploy` — опубликовать Worker.

Если `deploy` пишет `Cloudflare secrets missing`, добавьте secrets. Если Wrangler пишет `Authentication error [code: 10000]`, токен Cloudflare не имеет прав на Workers для нужного account.

Rollback:

- Удалите webhook через Telegram `deleteWebhook`.
- Запустите вручную GitHub Actions -> **Chat Polling Runtime**.

### Manual Polling Fallback

GitHub Actions polling работает, но не гарантирует realtime-ответы. Используйте только для аварийной проверки:

1. Убедитесь, что secrets заданы: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `CACHE_GIST_ID`, `GIST_TOKEN`, `GARMIN_EMAIL`, `GARMIN_PASSWORD`.
2. Запустите GitHub Actions -> **Chat Polling Runtime** -> **Run workflow**.
3. Workflow сам вызовет Telegram `deleteWebhook` с `drop_pending_updates=false`, затем выполнит `getUpdates`.
4. Проверьте лог `python main.py poll-once`: ожидается строка вида `poll_once fetched=... processed=... errors=... next_offset=...`.

Offset хранится в `cache.json` под `_telegram_poll_state` и загружается в Gist после каждого polling run. Если отдельный update падает, offset всё равно продвигается, чтобы GitHub Actions не повторял один и тот же сломанный update бесконечно.

Rollback:

- Disable workflow **Chat Polling Runtime**.
- При возврате к webhook заново установите Telegram webhook на нужный runtime endpoint.
- Не удаляйте `_telegram_poll_state`: он безвреден и нужен для повторного включения polling.

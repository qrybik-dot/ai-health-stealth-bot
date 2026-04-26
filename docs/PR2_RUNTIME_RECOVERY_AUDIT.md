# PR2 Runtime Recovery Audit

Дата: 2026-04-26  
Режим: audit-only, без implementation changes.

## Executive Summary

Baseline уже восстановлен и тесты зелёные, но бот ещё не является рабочим продуктом:

- `cache-self-check` видит только локальный кэш и один старый день: `2026-02-22`.
- `has_today=false`, значит scheduled push не имеет свежего дневного контекста.
- Render умер, значит FastAPI `POST /webhook` больше не является рабочим chat runtime.
- GitHub Actions sync/push уже есть и являются самым маленьким путём вернуть scheduled value.
- Gemini fallback уже встроен в chat path и не требует переписывания.

Самый безопасный порядок восстановления: сначала sync/auth/history и scheduled push, потом chat runtime. Если сначала делать Cloudflare Worker, мы получим новый runtime вокруг старых/пустых данных и рискуем портировать большую часть Python-логики без нужды.

## A. Что ещё отсутствует для реально работающего бота

### 1. Свежие данные за сегодня

Подтверждение:

- `cache-self-check` сообщает `has_today=false`.
- `cache_source=local`.
- `top_level_keys=['2026-02-22']`.

Кодовый путь уже есть:

- `main.py run_sync()`
- `fetch_garmin_minimal()`
- `.github/workflows/sync.yml`

Но продуктово не доказано:

- что GitHub Actions secrets настроены;
- что Garmin auth проходит;
- что Gist read/write работает в обоих workflow steps;
- что текущий день не перетирает историю;
- что backfill можно выполнить контролируемо.

### 2. Долгая память / recent history

Кодовый путь уже есть:

- `main.py run_backfill(days)`
- `main.py ensure_history_bootstrap(target_days=90)`
- `cache.py load_cache_with_meta()`
- `firestore_store.py`
- `scripts/migrate_cache_to_firestore.py`

Проблема:

- локально есть только один старый день;
- Firestore не активен без `FIRESTORE_PROJECT_ID` и credentials;
- Gist всё ещё transitional bridge, но не должен стать долгосрочным source of truth.

### 3. Chat runtime после Render

Кодовый путь существует только как FastAPI server:

- `main.py run_serve()`
- `@app.post("/webhook")`
- command/callback routing внутри `webhook()`
- Gemini fallback via `generate_chat_message()`

Чего нет:

- бесплатного runtime endpoint вместо Render;
- setup-команды для Telegram `setWebhook`;
- polling fallback;
- pure function для обработки Telegram update вне FastAPI request.

### 4. Gemini interpretation path

Кодовый путь есть:

- `generate_chat_message()`
- `build_chat_prompt()`
- deterministic-first `_route_structured_reply()`, затем Gemini fallback.

Что отсутствует:

- live runtime для входящих сообщений.

Gemini не нужно переписывать в ближайшем PR.

### 5. Operational runbook

Нужны явные шаги:

- какие secrets включить;
- что запускать первым;
- как понять, что sync успешен;
- как делать backfill без rate-limit;
- как откатить scheduled push/chat.

## B. Что восстанавливать первым

Первым: **sync/auth/history**.

Причина:

- без свежих данных morning/midday/evening push превращается в fallback/no-data;
- chat runtime без данных будет отвечать на пустую историю;
- Gemini fallback полезен только если получает реальный history context.

Вторым: **scheduled push validation**.

Причина:

- он уже работает через GitHub Actions;
- не требует Render;
- возвращает ежедневную ценность быстрее, чем интерактивный chat.

Третьим: **runtime/chat**.

Причина:

- текущий chat path завязан на FastAPI webhook;
- замена runtime требует либо нового хостинга, либо polling mode;
- это больше риска, чем запуск существующих Actions.

Gemini path: оставить как есть.

## C. Smallest viable recovery architecture on this repo

### Рекомендуемый минимальный transitional path

1. GitHub Actions остаются для:
   - Garmin sync every 3 hours;
   - controlled backfill;
   - scheduled push.
2. Gist остаётся transitional cache bridge, но с guardrails:
   - sync step должен уметь читать существующий Gist перед записью;
   - нельзя случайно перетереть 90-day history одним свежим днём;
   - `GIST_TOKEN` должен быть доступен не только upload step, но и sync read path.
3. Firestore остаётся optional cloud-first store:
   - не удалять;
   - не делать обязательным в первом recovery PR;
   - включать после доказанного sync/backfill или отдельным hardening PR.
4. Chat runtime сначала можно вернуть через Telegram polling в GitHub Actions:
   - `poll-once` CLI получает updates через `getUpdates`;
   - обрабатывает те же commands/callbacks/free-text;
   - хранит `update_offset` в cache/Gist/Firestore;
   - workflow запускается по schedule и вручную.

Это меньше, чем Cloudflare Worker, потому что:

- сохраняет Python logic;
- не требует портировать `communication.py`, `cache.py`, callbacks и Gemini routing;
- не требует сразу проектировать D1/KV schema;
- использует уже установленный Python 3.11 workflow pattern.

Минусы polling:

- не instant chat;
- GitHub Actions schedule не идеально realtime;
- нужно удалить Telegram webhook (`deleteWebhook`) перед `getUpdates`;
- offset persistence должен быть аккуратным.

Но для восстановления продукта это самый маленький бесплатный путь.

### Cloudflare Worker оценка

Cloudflare Worker хорош как target runtime для webhook, но не как первый implementation PR на этом коде.

Почему не первый:

- текущий chat routing написан в Python/FastAPI;
- Worker потребует TypeScript/JS port или отдельный HTTP bridge;
- D1/KV schema ещё не закреплена как canonical store;
- быстрый Worker без полноценной логики рискует превратить бота в dumb proxy или metric mirror.

Когда Worker становится правильным:

- после доказанного sync/history;
- после понятного storage source of truth;
- после стабилизации polling/FastAPI routing contract;
- когда готовы портировать deterministic-first Q&A без потери Gemini fallback.

## D. PR2, PR3, PR4 в порядке

### PR2 Implementation: sync/history/push recovery guardrails

Scope:

- не менять архитектуру;
- усилить существующие GitHub Actions для recovery;
- добавить controlled backfill path;
- добавить runbook.

Вероятные изменения:

- `.github/workflows/sync.yml`
  - передавать `GIST_TOKEN` в `Run sync`, чтобы `load_cache_with_meta()` мог читать private Gist перед записью;
  - добавить workflow_dispatch inputs: `mode=sync|backfill|cache-check`, `backfill_days`;
  - после sync/backfill запускать `cache-self-check`;
  - upload Gist только после успешного local cache write.
- возможно новый workflow `recovery.yml`, если проще не перегружать `sync.yml`.
- docs/runbook for manual recovery.

Human actions:

- настроить secrets;
- вручную запустить sync;
- затем backfill 7 -> 30 -> 90;
- проверить `/debug_sync` или action logs;
- включить scheduled push.

Done when:

- GitHub Actions sync создаёт today snapshot;
- Gist/cache содержит today + existing history;
- push workflow может отправить не no-data fallback;
- локальный/Actions cache check показывает `has_today=true`.

### PR3 Implementation: free chat runtime via Telegram polling

Scope:

- не Cloudflare;
- не storage migration;
- добавить minimal polling runtime, переиспользуя Python routing.

Вероятные изменения:

- извлечь обработку Telegram update из FastAPI `webhook()` в pure function, например `process_telegram_update(update, default_chat_id)`;
- `webhook()` вызывает эту функцию;
- новый CLI `python main.py poll-once`;
- `poll-once`:
  - читает persisted `telegram_update_offset`;
  - вызывает Telegram `getUpdates`;
  - обрабатывает ограниченное число updates;
  - сохраняет offset после успешной обработки;
- workflow `.github/workflows/chat_poll.yml` по schedule/manual.

Human actions:

- выполнить Telegram `deleteWebhook`;
- убедиться, что polling не конкурирует с webhook;
- включить scheduled polling.

Done when:

- `/today`, `/week`, `/refresh`, кнопки и free-text Q&A работают без Render;
- личные вопросы используют deterministic-first path и Gemini fallback;
- offset не обрабатывает одни и те же сообщения повторно.

### PR4 Implementation: durable store hardening

Scope:

- выбрать canonical near-term durable store;
- не переписывать продуктовую логику.

Вариант 1, минимальный:

- включить Firestore path, который уже есть;
- добавить workflow env/credential support;
- мигрировать cache history via `scripts/migrate_cache_to_firestore.py`;
- оставить Gist fallback на rollback window.

Вариант 2, если принято идти к Cloudflare:

- проектировать D1 schema отдельно;
- не портировать chat в Worker до schema/test parity.

Done when:

- recent 90-day history не зависит от Gist;
- auth tokenstore и sent registry durable;
- Gist можно перевести в legacy bridge.

## E. Что может остаться как есть сейчас

- `communication.py` renderer system после PR1.
- Gemini fallback strategy.
- GitHub Actions push schedule.
- Dedup registry shape.
- Firestore code path.
- Gist bridge.
- FastAPI webhook code as reusable routing reference.
- Miniapp scaffold.
- Existing tests and self-checks.

## F. Что нужно убрать позже, но не сейчас

- Gist as source of truth.
- Render-specific assumptions/comments.
- root-level legacy `sync.yml`.
- FastAPI-only assumption for chat runtime, если polling/Worker станет основным.
- `GIST_SYNC_TOKEN` legacy fallback after token cleanup.

Не убивать сейчас, потому что они дают rollback path.

## G. Secrets/config вручную от человека

Required for sync/push:

- `GARMIN_EMAIL`
- `GARMIN_PASSWORD`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `GEMINI_API_KEY`
- `GEMINI_MODEL`
- `CACHE_GIST_ID`
- `GIST_TOKEN`

Optional / later for Firestore:

- `FIRESTORE_PROJECT_ID`
- Google service account credentials for GitHub Actions/runtime
- `GOOGLE_APPLICATION_CREDENTIALS` or equivalent secret-file setup
- `DEFAULT_CHAT_ID` if Firestore documents should not use `default`

For polling chat:

- same Telegram/Gemini/Gist secrets;
- persisted update offset key in cache;
- manual Telegram `deleteWebhook` before `getUpdates`.

For future Cloudflare:

- Cloudflare account/project resources;
- Worker secrets for Telegram/Gemini;
- D1/KV binding IDs;
- webhook secret token if used.

## Red Zones

- Do not start Cloudflare Worker before fresh data/history is restored.
- Do not delete Gist before another durable source is proven.
- Do not make Firestore mandatory in the first recovery PR.
- Do not move Garmin sync into Cloudflare Worker.
- Do not port message system to TypeScript just to replace Render.
- Do not remove free-text Q&A.
- Do not let Gemini become source of raw facts.


# AUDIT v1 — безопасный аудит и план cleanup

## 1) Core flows и entrypoints

### 1.1 Webhook handler
- HTTP entrypoint: `POST /webhook` в FastAPI (`main.py`), принимает update от Telegram и маршрутизирует:
  - callback-и для `color_story`, `color_vote`, `today_story`, `today_vote`;
  - текстовые команды `/today`, `/color`, `/week`, `/stats`, `/help`;
  - прочие сообщения — в Gemini chat flow с контекстом кэша.
- Запуск веб-сервера: `python main.py serve`.

### 1.2 Command handlers
- `/today` → `handle_today_command`: строит статус дня (режим/ритм), генерирует PNG-карту дня, показывает `🟡 Факт дня`, клавиатуру голосования.
- `/color` → `handle_color_command`: цвет недели + PNG + история цвета (через callback) + голосование.
- `/week` → алиас на `handle_stats_command`.
- `/help` → статический список команд (`build_help_message`).

### 1.3 Scheduled push flow
- Workflow: `.github/workflows/push.yml`.
- Шаг запускает: `python main.py push scheduled`.
- Внутри `run_push`:
  - выбирается слот (`morning|midday|evening`) по UTC;
  - читается кэш через `load_cache_with_meta`;
  - если кэш/данные недоступны — отправляется fallback;
  - иначе генерируется сообщение Gemini по `CODEX_RULES`.

### 1.4 Cache sync flow
- Workflow: `.github/workflows/sync.yml`.
- Шаги:
  - `python main.py sync` (Garmin -> `cache.json`),
  - `python scripts/gist_upload.py` (`cache.json` -> GitHub Gist).
- Runtime читает кэш из Gist при наличии `CACHE_GIST_ID`, иначе локальный файл.

---

## 2) Классификация файлов

### 2.1 Core runtime
- `main.py` — основной runtime: CLI, webhook, команды, push-flow.
- `cache.py` — кэш, состояния недели/дня, голоса, fallback загрузки.
- `color_engine.py` — детерминированный color engine + генерация PNG.
- `prompts.py` — системный prompt и CODEX для scheduled push.

### 2.2 Scripts / tools
- `scripts/gist_upload.py` — публикация `cache.json` в Gist.

### 2.3 CI/CD and ops
- `.github/workflows/push.yml` — cron + manual scheduled push.
- `.github/workflows/sync.yml` — cron + manual sync + gist upload.

### 2.4 Docs / product notes
- `README.md` — пользовательская и операционная документация.
- `docs/PROJECT_RULES.md`, `docs/BLUEPRINT_v1.md` — внутренние продуктовые документы.

### 2.5 Deprecated / unused candidates (без удаления)
- `sync.yml` (в корне репозитория) — выглядит как legacy-вариант workflow (artifact upload вместо gist upload), не исполняется GitHub Actions (используются только `.github/workflows/*.yml`). Рекомендация: пометить как deprecated и вынести в `docs/legacy/` в отдельном PR.
- В `main.py` есть неиспользуемая helper-функция `_build_fallback_message` (не вызывается). Рекомендация: оставить, но пометить как deprecated-комментарий в будущем PR.

---

## 3) Поиск TODO/dead code/duplications

### 3.1 TODO/FIXME
- Явных `TODO/FIXME` по коду не найдено.

### 3.2 Вероятно неиспользуемое
- `_build_fallback_message` в `main.py` не имеет call sites.

### 3.3 Дублирование
- `vote_label` и `today_vote_label` имеют одинаковую мапу (safe-кандидат на объединение).
- В `main.py` есть повторный импорт `import datetime as dt` (косметический cleanup).

### 3.4 Риск cleanup
- Удаление/слияние функций сейчас не делалось (требование low-risk, reversible).

---

## 4) Secrets & config audit

## 4.1 Переменные, найденные в коде

### Runtime (обязательные по текущей логике)
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `GEMINI_API_KEY`
- `GEMINI_MODEL`
- `GARMIN_EMAIL` (для `sync`)
- `GARMIN_PASSWORD` (для `sync`)

### Runtime / infra optional
- `CACHE_GIST_ID` (если задан — чтение кэша из Gist)
- `PORT` (webhook server)
- `DRY_RUN` (self-check/push dry run)

### Gist sync auth (runtime/actions)
- `GIST_TOKEN` (предпочтительный)
- `GIST_SYNC_TOKEN` (legacy fallback)
- `GITHUB_TOKEN` (fallback)

## 4.2 Сопоставление с известным списком GitHub Secrets

| Secret | Где используется | Рекомендация |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | runtime + push workflow | **KEEP** |
| `TELEGRAM_CHAT_ID` | runtime + push workflow | **KEEP** |
| `CACHE_GIST_ID` | cache load + gist upload + workflows | **KEEP** |
| `GIST_TOKEN` | gist upload + cache fetch fallback source | **KEEP (primary)** |
| `GIST_SYNC_TOKEN` | fallback token source в `cache.py` и `gist_upload.py` | **KEEP (legacy)**, удалить только после правки кода и проверок |
| `GITHUB_TOKEN` | fallback auth source + workflows | **KEEP** |
| `GARMIN_EMAIL` | `main.py sync` + sync workflow | **KEEP** |
| `GARMIN_PASSWORD` | `main.py sync` + sync workflow | **KEEP** |
| `GEMINI_API_KEY` | push/chat generation | **KEEP** |
| `GEMINI_MODEL` | push/chat generation | **KEEP** |
| `GEMINI_SYSTEM_PROMPT` | кодом не читается | **SAFE TO REMOVE NOW** (не referenced) |

> Авто-удаление secrets не выполнялось.

---

## 5) Workflow audit (scheduling correctness)

## 5.1 `push.yml`
- Trigger есть для `schedule` и `workflow_dispatch`.
- В env передаются `GIST_TOKEN` и `CACHE_GIST_ID`.
- Шаг push (`python main.py push scheduled`) выполняется всегда для обоих trigger-ов.
- Cron-расписание: `05:30`, `10:00`, `16:30` UTC (соответствует окнам MSK 08:30/13:00/19:30).

## 5.2 `sync.yml`
- Используется `GIST_TOKEN` при загрузке gist (`scripts/gist_upload.py`).
- Используется тот же `CACHE_GIST_ID`.
- Sync и upload разведены на отдельные шаги, что удобно для диагностики.

## 5.3 Минимальные безопасные улучшения
- Добавлены диагностические echo-шаги в workflows (без секретов): trigger/event + факт наличия `CACHE_GIST_ID`.
- Runtime behavior бота не менялся.

---

## 6) Quick code quality pass (safe)

- Проверено, что fallback-пути не silent:
  - `run_push` отправляет fallback для недоступного кэша / отсутствия данных / ошибки генерации.
  - webhook исключения логируются через `log.exception`.
- Сообщения default flows соответствуют «режим/ритм/состояние дня», без диагнозов.
- Для `/today`, `/color`, `/help`, scheduled push поведение не изменялось.

---

## 7) Безопасный cleanup plan (reversible)

### Phase 1 (документация и видимость) — можно делать сразу
1. Зафиксировать в README актуальный список secrets и workflow-схему.
2. Пометить `sync.yml` (root) как legacy в документации.
3. Явно обозначить `GIST_SYNC_TOKEN` как legacy fallback.

### Phase 2 (минимальные кодовые правки)
1. Добавить комментарий `deprecated` к `_build_fallback_message`.
2. Объединить `vote_label` и `today_vote_label` (без изменения текста/кнопок).
3. Убрать дублирующий `import datetime as dt`.

### Phase 3 (после дополнительной валидации)
1. Удалить поддержку `GIST_SYNC_TOKEN` из кода только после:
   - обновления secrets,
   - прохождения self-check,
   - проверки workflow run в Actions.
2. Перенести legacy `sync.yml` (root) в `docs/legacy/`.

Все шаги — обратимы и не затрагивают критичные пути `/today`, `/color`, `/help`, `push scheduled`.

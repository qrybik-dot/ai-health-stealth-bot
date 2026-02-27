# Garmin Visual Insight Bot

Telegram-бот про ритм дня/недели на базе Garmin Connect. Без медицинских формулировок: только режим, состояние дня и практичные короткие подсказки.

## Что делает бот

- `/today`: карточка дня (PNG 1080x1080) + короткий статус + обратная связь ✅/🤷/❌.
- Scheduled push: слоты `morning / midday / evening` в зоне `Europe/Moscow`.
- `/week`: недельная карточка с контрастами, диапазонами и квестом недели.
- `/color`: цвет недели (детерминированно по ISO-неделе).
- `/refresh`: инкрементально подтягивает новые Garmin-данные, делает merge и показывает, какие блоки реально обновились.
- `/debug_sync`: диагностика цепочки синхронизации (источник кэша, последний sync-trace, недостающие блоки).

## Сквозной pipeline синхронизации

1. **Garmin fetch** (`fetch_garmin_minimal`): забираются доступные блоки (сон, stress, body battery, RHR, шаги, HRV и др.).
2. **Local cache merge** (`upsert_day_snapshot`, `_merge_trimmed_snapshot`):
   - новый payload trim-ится до нормализованного snapshot;
   - выполняется merge c текущим днём;
   - пустые/`None` значения **не стирают** старые полезные поля;
   - пересчитываются `missing_flags`, `data_completeness`, `confidence`.
3. **Gist sync** (`scripts/gist_upload.py` + `.github/workflows/sync.yml`): после `python main.py sync` workflow отдельным шагом делает upload `cache.json` в gist. Внутри `sync/refresh` команд `gist_upload_ts` не заполняется, потому что upload выполняется вне процесса бота.
4. **Runtime read** (`load_cache_with_meta`): при `CACHE_GIST_ID` источник = gist; при ошибке gist или более свежем локальном snapshot используется `local_fallback`/`local_fresher_than_gist` (это явно видно в meta/debug).
5. **Refresh compare** (`refresh_available_data`): diff считается по merged-снимку, обновлённые блоки фиксируются явно.
6. **Push gating** (`run_push`): дедуп по слотам, manual-run не блокирует scheduled слот, добавлен deferred/catch-up сценарий утра.

## Как работает merge (вместо overwrite)

- Снимок дня не перезаписывается «целиком».
- Для каждого блока:
  - если в новом payload значение пустое — сохраняем старое;
  - если пришло валидное значение — обновляем;
  - вложенные dict-блоки мержатся глубоко (частичное обновление не удаляет старые под-поля).
- После merge всегда пересчёт качества данных и флагов неполноты.

## `/refresh`: что гарантирует и чего не гарантирует

`/refresh` делает честный incremental refresh:

1. fetch Garmin,
2. merge с текущим snapshot дня,
3. diff (updated blocks + completeness/confidence delta),
4. запись trace,
5. сообщение пользователю.

Сообщения:
- если обновился хотя бы 1 блок: «Обновил данные: …»;
- если Garmin не отдал новые блоки: «Новых блоков пока нет…»;
- если всё уже актуально: «Данные уже актуальны…».

`/refresh` не может ускорить появление метрик, если Garmin Connect ещё не досинхронизировал их.

## Почему push может быть предварительным

Утренний слот может быть частичным, если к моменту запуска Garmin отдал не все ключевые блоки. В этом случае:

- отправляется предварительный morning-сигнал;
- слот помечается как `morning_deferred`;
- при следующем scheduled run в retry-окне выполняется catch-up morning (без дублей), если данные стали полнее.

## Timezone и дата

- Day key считается timezone-aware через `BOT_TIMEZONE` (по умолчанию `Europe/Moscow`).
- Ключи дня в sync/refresh/push согласованы (`current_day_key`).
- GitHub cron работает в UTC, расписание в workflow уже сопоставлено с MSK.

## Ограничения

- Garmin может отдавать часть блоков с задержкой.
- Не все метрики доступны на всех моделях часов.
- Weekly включает `source_fingerprint` (снимок source-данных за 7 дней), чтобы исключать «косметические» скачки без новых Garmin-данных.
- Это не медицинский сервис.

## Проверка и debug

- `python -m unittest`
- `python main.py cache-self-check`
- `python main.py push-self-check`
- `python main.py debug-sync`

Что смотреть:
- источник кэша (`gist/local/local_fallback/local_fresher_than_gist`),
- причина fallback (`cache_error`, `fallback_reason`),
- note про устойчивость после рестарта (`source of truth note`),
- последний sync/refresh run-id,
- `had real updates` (реально ли появились новые блоки),
- `updated_blocks` (какие блоки изменились),
- `still missing` + по каждому ключевому полю: `raw`, `normalized`, `expected_day`, `raw_dates`, `reason`.

## Workflow

- Sync: `.github/workflows/sync.yml` (fetch + merge + gist upload).
- Push: `.github/workflows/push.yml` (утро/день/вечер + доп. retry-точки для catch-up после неполного утра).

## Env

- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- `GARMIN_EMAIL`, `GARMIN_PASSWORD`
- `GEMINI_API_KEY`, `GEMINI_MODEL`
- `CACHE_GIST_ID`
- `GIST_TOKEN` (или `GIST_SYNC_TOKEN` / `GITHUB_TOKEN`)
- `BOT_TIMEZONE` (опционально, default `Europe/Moscow`)


## Единый source of truth для ответов

- Введён единый слой `build_day_context` (файл `cache.py`), который возвращает нормализованный контекст дня и истории:
  - `latest_snapshot_date`, `available_metrics`, `missing_metrics`
  - `key_metrics_present_count`, `key_metrics_total_count`
  - `data_completeness`, `confidence`, `available_days_count`, `available_days`
  - `day_status`, `last_sync_time`
- Этот слой одинаково используется для push и для чат-ответов: «какие метрики есть», «детальнее», «за сколько дней есть данные».

## Ключевые метрики readiness / push gating

Фиксированный набор key metrics:
- `sleep` (сон)
- `body_battery` (Body Battery)
- `rhr` (RHR)
- `stress` (стресс)

Если в push написано «1 из 4», это всегда про этот набор.

## Почему ответы могут быть частичными

- Garmin часто отдаёт блоки не одновременно.
- Поэтому в течение дня снимок дообогащается через merge.
- `/refresh` подтягивает новые блоки, но не может «ускорить» появление данных на стороне Garmin.
- Если ключевых метрик мало, бот делает честный partial-answer и явно показывает ограничения.

## Как читать ответы бота

- **Push**: статус дня, что уже есть, чего не хватает, действие, ограничение, надёжность.
- **Какие метрики есть**: только фактически доступные поля + список отсутствующих.
- **Детальнее**: анализ только по реальным значениям; при дефиците данных добавляется блок ограничений.
- **За сколько дней есть данные**: фактическое число дней и диапазон дат из кэша.

## Ограничения текущей реализации истории

- История хранится по дням в `cache.json` и накапливается инкрементально.
- Глубина истории ограничена `RETENTION_DAYS`.
- Если fetch в текущем run вернул только текущий день, прошлые дни всё равно доступны из ранее сохранённого кэша.


## Что значит `local_fresher_than_gist`

Это состояние, когда:
- gist доступен,
- но у локального `cache.json` свежий `last_sync_time/fetched_at_utc` для текущего дня.

Тогда runtime выбирает локальный snapshot как более актуальный. Это безопасно для текущего процесса, но в другом runtime после рестарта без этого файла может произойти откат к состоянию gist. Поэтому в `/debug_sync` добавлен явный note про риск после рестарта.

## Почему могут отсутствовать `sleep/body_battery/rhr/steps`

Диагностика теперь разделяет причины по каждому полю:
- `raw_absent`: Garmin не прислал блок в raw payload.
- `date_mismatch`: в raw есть дата, но не совпадает с `expected_date_key` (часто ночной сон уходит в соседний день).
- `mapping_or_shape_mismatch`: raw есть, но нормализация не смогла извлечь поле.

Нормализация поддерживает дополнительные формы payload:
- `sleep`: `dailySleepDTO/sleepSummary` + стандартные поля;
- `body_battery`: `bodyBatteryValuesArray` и fallback-поля;
- `rhr`: `value/averageRestingHeartRate` + стандартные поля;
- `steps`: `steps/totalSteps/stepCount/value` и вложенные структуры.

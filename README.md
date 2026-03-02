# Coach Potato 🥔 — Garmin Visual Insight Bot

Coach Potato отправляет короткие data-driven вердикты по режиму дня и цветовые карточки без медицинского тона.

## Что делает бот

- 3 плановых пуша: утро / день / вечер.
- Утро: **сначала Color of Day**, затем **Вердикт утра**.
- Weekly summary по воскресенью вечером (текстовый формат, без сломанной картинки).
- Ответы на вопросы в чате: сначала компактный вердикт, детализация — только по запросу.

## Источники данных

- Garmin Connect (минимальный набор метрик): сон, stress, body battery, RHR, шаги и др.
- Локальный кэш `cache.json` + merge истории по датам (без перезаписи прошлых дней).

## Расписание

Окна по Москве:
- morning: 09:15–09:45
- midday: 13:40–14:20
- evening: 19:40–20:20

## Визуалы Variant A

- **Color of Day**: процедурная color/palette карточка (`generate_color_card_image`).
- **Verdict**: mascot state image из `assets/coach_states/{state}.png` + компактный текст.

Поддерживаемые state-ключи:
- `machine`
- `battle_club`
- `steady_bolid`
- `focused`
- `cruise`
- `soft_mash`
- `overheated`
- `zen`

> Если ассет отсутствует, бот отправляет только текст вердикта.

### Как добавить новый state-ассет

1. Положить PNG в `assets/coach_states/`.
2. Добавить маппинг в `main.py` (`_state_to_asset`).
3. Прогнать тесты.

## Команды

- `/today` — карточка дня
- `/color` — цвет недели
- `/week` — недельный отчёт
- `/stats` — статистика голосов
- `/refresh` — ручной sync
- `/debug_sync` — состояние sync/кэша
- `/debug_sent` — что отправлено сегодня и почему

## Dedup registry (надёжность отправок)

Бот пишет реестр отправок в `cache.json` (`_push_state`):

`{date}|{chat_id}|{slot}|{message_type}`

Где `message_type`:
- `verdict`
- `color`
- `weekly`

В записи сохраняются:
- `ts`
- `run_id`
- `trigger_source` (`schedule/manual/retry/catch-up`)
- `manual_preview`

Это гарантирует idempotent send: один тип сообщения на слот/дату отправляется только один раз.

### Сброс dedup для тестов

- Удалить нужные ключи из `_push_state` в `cache.json`.
- Или удалить `cache.json` целиком в локальной среде.

## Миграционная заметка

Схема `_push_state` расширена: ключ теперь включает `message_type`.
Старые ключи без типа не мешают работе, но для чистоты можно очистить `_push_state`.

## Проверка

- `python -m unittest discover -s tests`
- `python main.py push-self-check`
- `python main.py schedule-self-check`

# Garmin Visual Insight Bot (v1)

Garmin Visual Insight Bot — это Telegram-бот, который превращает сигналы Garmin в короткую ежедневную интерпретацию **ритма/режима дня**: карточка дня, цвет недели, голосование по попаданию и недельная сводка, без медицинского тона и без «мотивационного шума».

## Features (v1)

- Ежедневная карточка `/today`:
  - PNG 1080x1080 (процедурная генерация через Pillow),
  - краткий статус дня,
  - блок `🟡 Факт дня`,
  - голосование `✅ / ➖ / ❌`.
- Недельный цвет `/color`:
  - детерминированный цвет недели (ISO-week),
  - краткая подпись,
  - история цвета отдельным сообщением,
  - голосование без revote в тот же день.
- Недельная сводка `/week` (алиас на статистику):
  - агрегированные голоса по карточкам за текущую неделю.
- `/help` с коротким списком команд.
- Scheduled push в заданные окна времени (утро/день/вечер) с fallback при неполных данных.
- Синхронизация кэша в GitHub Gist для согласованного состояния между раннерами и runtime.

## Команды

- `/today` — карточка дня: изображение + короткая подпись + `🟡 Факт дня` + кнопки оценки.
- `/color` — карточка цвета недели: изображение + подпись + кнопка истории + голосование.
- `/week` — недельная сводка голосов по карточкам.
- `/help` — список команд.

> Дополнительно есть `/stats` как технический алиас `/week`.

## Setup

### 1) Локальный запуск

1. Установить зависимости:
   ```bash
   pip install -r requirements.txt
   ```
2. Заполнить переменные окружения (см. таблицу ниже).
3. Выполнить первичную синхронизацию Garmin:
   ```bash
   python main.py sync
   ```
4. Запустить webhook-сервер:
   ```bash
   python main.py serve
   ```
5. Настроить Telegram webhook на `POST /webhook` вашего публичного URL.

### 2) GitHub Actions

Используются workflows:
- `.github/workflows/sync.yml` — периодический sync + upload cache в gist.
- `.github/workflows/push.yml` — периодический push сообщений в Telegram.

### 3) Required secrets и назначение

| Secret / env | Где нужен | Для чего |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | runtime + `push.yml` | отправка сообщений/картинок в Telegram |
| `TELEGRAM_CHAT_ID` | runtime + `push.yml` | целевой чат для scheduled push |
| `GARMIN_EMAIL` | `sync.yml` / `main.py sync` | вход в Garmin Connect |
| `GARMIN_PASSWORD` | `sync.yml` / `main.py sync` | вход в Garmin Connect |
| `GEMINI_API_KEY` | runtime + `push.yml` | генерация текста через Gemini |
| `GEMINI_MODEL` | runtime + `push.yml` | имя модели Gemini |
| `CACHE_GIST_ID` | runtime + workflows | id gist-файла с `cache.json` |
| `GIST_TOKEN` | `sync.yml` + runtime fallback | PAT для чтения/записи gist |
| `GITHUB_TOKEN` | fallback | резервный токен-источник для gist auth |
| `GIST_SYNC_TOKEN` | legacy fallback | устаревающий fallback (пока поддерживается кодом) |

Опционально:
- `PORT` (по умолчанию `8080`) — порт FastAPI сервера.
- `DRY_RUN=1` — dry-run для self-check/push диагностики.

### 4) Как создать `GIST_TOKEN`

1. GitHub -> **Settings** -> **Developer settings** -> **Personal access tokens**.
2. Создать token (classic или fine-grained, если позволяет доступ к gist).
3. Дать scope **`gist`**.
4. Сохранить token как GitHub Secret `GIST_TOKEN`.

### 5) Как задать `CACHE_GIST_ID`

1. Создать gist с файлом `cache.json` (можно `{}` на старте).
2. Скопировать ID gist из URL (часть после имени пользователя).
3. Добавить ID в GitHub Secret `CACHE_GIST_ID`.

### 6) Как работает расписание

- `sync.yml`: каждые 3 часа (`0 */3 * * *`) + manual `workflow_dispatch`.
- `push.yml`: 3 запуска в сутки по UTC (соответствуют `08:30 / 13:00 / 19:00` Europe/Moscow):
  - `30 5 * * *`
  - `0 10 * * *`
  - `0 16 * * *`

В коде `push scheduled` использует окна MSK с допуском на drift: morning `08:15–09:45`, midday `12:40–13:20`, evening `18:40–19:20`.

## Testing

### Быстрый ручной чек-лист

1. `/color` -> приходит карточка цвета.
2. Нажать `🎨 История цвета` -> история приходит отдельным сообщением.
3. Проголосовать `✅/➖/❌` -> кнопки заменяются на `🗳 Ваш выбор: ...`.
4. `/today` -> есть блок `🟡 Факт дня`.
5. `/week` -> приходит недельная сводка по голосам.
6. При отсутствии данных за день ожидается fallback-сообщение (без ошибок).


### How to test scheduled push локально

```bash
PUSH_NOW_MSK=2026-02-26T08:31:00+03:00 python main.py push scheduled
PUSH_NOW_MSK=2026-02-26T13:06:00+03:00 python main.py push scheduled
PUSH_NOW_MSK=2026-02-26T19:12:00+03:00 python main.py push scheduled
python main.py push scheduled --slot morning
```

Проверка:
- morning отправляет `sendPhoto` (PNG 1080×1080) + caption.
- midday/evening отправляют только текст.

### Как симулировать воскресный weekly report

```bash
PUSH_NOW_MSK=2026-03-01T19:05:00+03:00 python main.py push scheduled
```

Если это воскресенье по MSK, после вечернего push бот отправит короткий weekly report в том же запуске.

### Как проверить dedupe

```bash
PUSH_NOW_MSK=2026-02-26T08:31:00+03:00 python main.py push scheduled
PUSH_NOW_MSK=2026-02-26T08:31:00+03:00 python main.py push scheduled
```

Второй запуск должен записать `dedupe_skip` и не отправлять дубль для того же `chat_id+date+slot`.

### Как проверить retention prune

1. Заполнить `cache.json` тестовыми днями старше 120 дней (и записями в `_today_state`, `_today_votes`, `_daily_votes`, `_push_state`).
2. Запустить:

```bash
python main.py cache-self-check
python main.py push-self-check
```

3. Затем выполнить любой `push` или `/today`: в логах появится `cache prune summary` с количеством удалённых/сохранённых записей.

### Self-check команды

```bash
python -m py_compile main.py cache.py scripts/gist_upload.py color_engine.py
python main.py cache-self-check
python main.py push-self-check
```

Что ожидать:
- `py_compile` завершается без ошибок.
- `cache-self-check` печатает источник кэша, доступность и `has_today`.
- `push-self-check` печатает `requested_push_kind`, `detected_push_kind`, состояние кэша и наличие данных за сегодня.

## Troubleshooting

- `gist_403`
  - Обычно означает, что `GIST_TOKEN` отсутствует, неверный или без scope `gist`.
  - Проверьте Secrets и лог шага `Upload cache.json to Gist`.

- «Push не пришёл»
  - Проверьте логи `.github/workflows/push.yml`.
  - Локально/в runner запустите `python main.py cache-self-check`.
  - Убедитесь, что заданы `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GEMINI_*`.

- «Сегодня нет данных»
  - Это штатный fallback-сценарий: бот должен отправить короткое fallback-сообщение.
  - Проверьте, что `sync.yml` отработал и `cache.json` обновился в gist.

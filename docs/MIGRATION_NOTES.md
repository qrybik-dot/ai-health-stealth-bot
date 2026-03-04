# Migration notes

## Cloud-first storage

Начиная с текущей версии бот умеет работать в cloud-first режиме через Firestore (если задан `FIRESTORE_PROJECT_ID`).

### Что хранится в Firestore

- `users/{chat_id}/days/{date_msk}` — дневные snapshots (merge без потери старых полей).
- `users/{chat_id}/sent/{date|chat|slot|type}` — dedup registry отправок.
- `users/{chat_id}/auth/garmin` — Garmin tokenstore для восстановления авторизации после рестарта.

### Fallback

Если Firestore не настроен, бот продолжает работать через `cache.json` (локальный dev fallback).

## Миграция старого cache.json

1. Настройте `FIRESTORE_PROJECT_ID` и сервисный аккаунт Google Cloud.
2. Выполните миграционный скрипт:

```bash
python scripts/migrate_cache_to_firestore.py
```

3. Проверьте наличие документов `days` и `sent` в Firestore.

## Совместимость

- Старый `_push_state` в `cache.json` остаётся рабочим.
- В режиме Firestore dedup-проверка сначала идёт в облако, потом в локальный fallback.

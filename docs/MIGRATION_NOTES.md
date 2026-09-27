# Migration notes

## Current storage state

На 2026-09-27 production продолжает использовать private GitHub Gist (`cache.json`) как подтверждённый источник накопленной Garmin-истории и runtime-state.

Firestore support присутствует в коде, но становится active только при наличии:
- `FIRESTORE_PROJECT_ID`
- `FIRESTORE_SERVICE_ACCOUNT_JSON` (или Application Default Credentials в другом runtime)

Само наличие `firestore_store.py` не означает, что production уже пишет в Firestore.

## Non-destructive Firestore archive migration

Мигратор `scripts/migrate_cache_to_firestore.py` переносит только YYYY-MM-DD day snapshots. Он не выполняет DELETE и допускает повторный запуск.

### Dry run

```bash
python scripts/migrate_cache_to_firestore.py --dry-run
```

Показывает:
- число дней;
- первую/последнюю дату;
- приблизительный размер payload;
- SHA-256 canonical dataset.

Записи в Firestore не создаются.

### Migration

```bash
python scripts/migrate_cache_to_firestore.py
```

Каждый source day upsert-ится в `users/{DATA_OWNER_ID}/days/{date}`, после чего читается обратно и сверяется как source subset. Миграция считается успешной только при:

```text
firestore_archive_verification=ok
```

### Verify only

```bash
python scripts/migrate_cache_to_firestore.py --verify-only
```

Не пишет данные, только проверяет наличие всех source days и их полей.

Те же операции доступны через GitHub Actions → **Recovery Controls**:
- `firestore-migrate-dry-run`
- `firestore-migrate`
- `firestore-verify`

## Retention rule

Garmin archive — накопительный. Автоматического удаления исторических day snapshots в durable archive быть не должно.

`CACHE_RETENTION_DAYS`, `PUSH_STATE_RETENTION_DAYS` и weekly/state retention относятся к runtime/cache слою, а не к политике хранения канонической Garmin-истории.

## Cleanup gate

До подтверждённого durable archive запрещено:
- удалять старые day snapshots из Gist;
- уменьшать Gist до recent-window;
- разделять cache/state с потерей rollback source.

После archive verification можно отдельно перевести Gist в компактный runtime cache/state и оставить durable store для долгосрочной аналитики.

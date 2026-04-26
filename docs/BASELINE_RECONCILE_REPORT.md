# Baseline Reconcile Report

Дата: 2026-04-26
Ветка: `pr0/baseline-reconcile`

## Scope

PR0 ограничен baseline reconciliation and validation. В этом PR не выполнялись:

- Cloudflare migration
- удаление Firestore
- удаление Gist
- redesign message system / tone of voice
- storage migration
- runtime architecture rewrite

## Branch State

Текущая ветка уже основана на `origin/main`. Старый локальный `main` не использовался как implementation base.

Проверка:

```text
git branch --show-current
pr0/baseline-reconcile
```

На момент финальной проверки рабочее дерево чистое.

## Environment

Валидация выполнялась через локальный virtualenv:

```text
.venv/bin/python --version
Python 3.11.15
```

Системный `python` в текущей shell-сессии отсутствует, а системный `python3` указывает на Python 3.9.6. Для PR0 это не кодовая проблема, но запуск validation-команд должен явно использовать `.venv/bin/python` или активированный `.venv`.

## Validation Summary

Команда:

```bash
.venv/bin/python -m unittest discover -s tests
```

Результат:

```text
Ran 81 tests in 0.235s
FAILED (failures=7)
```

Self-checks:

```bash
.venv/bin/python main.py schedule-self-check
```

Результат: passed.

```bash
.venv/bin/python main.py cache-self-check
```

Результат:

```text
cache_source=local
cache_available=true
cache_error=
has_today=false
top_level_keys=['2026-02-22']
```

## Baseline Interpretation

Оставшиеся 7 failures не являются Python-version/env failures. Это semantic / contract-level drift между текущими renderer outputs и ожиданиями тестов.

Большинство падений не доказывают runtime breakage. Они показывают, что тесты закрепили старые строки или старый стиль сообщения, а текущий код уже перешел на более нейтральный/структурированный вывод.

Есть один настоящий продуктовый риск: partial detailed analysis больше не показывает явный блок ограничений. Это соответствует требованию честности при неполных данных и должно быть вынесено в PR1, а не чиниться случайно в PR0.

## PR0 Changes

В PR0 добавлены только документы:

- `docs/BASELINE_RECONCILE_REPORT.md`
- `docs/BASELINE_VALIDATION_RESULTS.md`

Код, тесты, storage, workflows и runtime behavior не изменялись.

## Recommendation

PR0 можно считать успешным как baseline documentation PR, даже без зеленого test suite, если команда принимает, что оставшиеся failures классифицированы и не смешиваются с recovery implementation.

Следующий PR должен быть узким: зафиксировать продуктовый contract для deterministic renderers and partial-data truthfulness, затем привести тесты и минимальный код к одному контракту.


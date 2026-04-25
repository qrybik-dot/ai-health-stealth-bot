# Baseline Validation Results

Дата: 2026-04-26
Ветка: `pr0/baseline-reconcile`
Python: `.venv/bin/python` 3.11.15

## Commands Run

```bash
.venv/bin/python -m unittest discover -s tests
```

Result:

```text
Ran 81 tests in 0.235s
FAILED (failures=7)
```

```bash
.venv/bin/python main.py schedule-self-check
```

Result: passed.

```bash
.venv/bin/python main.py cache-self-check
```

Result:

```text
cache_source=local
cache_available=true
cache_error=
has_today=false
top_level_keys=['2026-02-22']
```

## Failure Classification

### 1. `test_snapshot_renderers_are_different_for_same_input`

File: `tests/test_backfill_and_rendering.py`

Observed failure:

- Test expects an exact legacy snapshot:
  - `Battery: 75 → 59`
  - `Стресс`
  - `Сон`
  - `RHR`
  - old extras line mentioning `SpO2` and `тренировки`
  - old conclusion text
- Current renderer returns the newer chip contract:
  - `Body Battery: 59`
  - `Средний стресс`
  - current wording for extras and conclusion

Classification: intended product-contract change not reflected in tests.

Root cause:

The test pins full copy instead of asserting durable semantics: facts renderer is distinct from roast, facts include available metrics, output is HTML-safe, and no raw-data dump occurs.

Fix belongs in: PR1.

Recommended PR1 action:

Replace full-string snapshot assertion with semantic assertions, after confirming final renderer contract. Do not revert message copy in PR0 just to satisfy this stale snapshot.

### 2. `test_cyrillic_render_probe`

File: `tests/test_reliability_variant_a.py`

Observed failure:

```text
AssertionError: 872 not greater than 1000
```

Classification: stale/brittle test hygiene.

Root cause:

`render_cyrillic_probe()` successfully creates a PNG, but the optimized PNG size is 872 bytes in this environment. File-size `>1000` is an indirect and brittle proxy for Cyrillic rendering. It depends on PNG optimization, installed fonts and compression, not only on whether Cyrillic text rendered.

Fix belongs in: PR1, or PR0 only if the team wants a tiny test-hygiene patch.

Recommended PR1 action:

Change the test to verify image existence, dimensions and non-blank pixels. If keeping a size guard, lower it and document that it is a smoke heuristic, not a rendering contract.

### 3. `test_compare_days_is_structured_html`

File: `tests/test_unified_data_layer.py`

Observed failure:

- Test expects `<b>🥔 Сравнение`.
- Current output starts with `🔍 <b>Сравнение ...</b>`.
- Current output is still structured HTML and contains no Markdown artifacts.

Classification: stale legacy expectation.

Root cause:

The test encodes an older Coach Potato flavor token as mandatory. Current product direction says Coach Potato is subtle flavor, not a costume in every line. The current output better matches that direction.

Fix belongs in: PR1.

Recommended PR1 action:

Update assertion to require structured HTML comparison and absence of Markdown artifacts, without requiring `🥔`.

### 4. `test_date_query_uses_exact_day_without_fallback`

File: `tests/test_unified_data_layer.py`

Observed failure:

- Test expects `Вердикт дня` and `Данных маловато`.
- Current output says `Итог дня` and `данных пока мало`.

Classification: mixed.

- Stale legacy expectation: exact phrases changed.
- Real product gap: date-specific no-data answer does not visibly state which requested date was checked.

Root cause:

The route appears to avoid falling back to today, which is the important behavior. But the output does not make the exact target date explicit, so a human cannot verify from the message that `вчера` was handled as the exact date.

Fix belongs in: PR1.

Recommended PR1 action:

Keep exact-date routing. Update message contract so no-data/date-specific answers include the target date, then update test assertions around behavior rather than old copy.

### 5. `test_detailed_analysis_guard_partial`

File: `tests/test_unified_data_layer.py`

Observed failure:

- Test expects `Ограничения` and `частичный`.
- Current output is a normal `Разбор дня` with the single available sleep chip and no explicit partial-data limitation block.

Classification: current code regression.

Root cause:

For partial contexts, `build_day_detail_message()` no longer surfaces missing-data limitations clearly. This conflicts with the product requirement: when data is partial/stale, the bot must lower confidence and be explicit about uncertainty.

Fix belongs in: PR1.

Recommended PR1 action:

Add a compact limitations block for `day_status == partial` or low key-metric count. Keep it deterministic and short. This is a real recovery prerequisite because PR1 focuses on memory/history and truthful analysis.

### 6. `test_metrics_availability_response_uses_fact_only`

File: `tests/test_unified_data_layer.py`

Observed failure:

- Test expects `<b>Какие данные есть</b>` and `<b>Группы метрик</b>`.
- Current output uses `📚 <b>Доступные данные</b>` with history, range, available metrics and missing metrics.

Classification: intended product-contract change not reflected in tests.

Root cause:

Current output is still factual and concise. The failure is caused by heading/section rename, not by loss of deterministic metrics availability behavior.

Fix belongs in: PR1.

Recommended PR1 action:

Update test to assert factual content:

- available history count/range
- available metric labels
- missing metric labels
- no Markdown artifacts
- no Gemini-style essay

### 7. `test_mode_output_constraints`

File: `tests/test_v1_release_updates.py`

Observed failure:

- Test requires roast output to contain `Картоха` or `Пюрешка`.
- Current roast starts with `🥔 <b>Пожарь</b>` and does not force those words.

Classification: stale legacy expectation.

Root cause:

The test codifies an older, more costume-like Coach Potato tone. Current product direction says the flavor layer should be subtle, adult and not clownish.

Fix belongs in: PR1.

Recommended PR1 action:

Replace `Картоха`/`Пюрешка` requirement with constraints that roast is distinct, slightly sharper, data-bound, concise and not insulting.

## Summary Table

| Test | Classification | Fix timing |
|---|---|---|
| `test_snapshot_renderers_are_different_for_same_input` | intended product-contract change not reflected in tests | PR1 |
| `test_cyrillic_render_probe` | stale/brittle test hygiene | PR1, or tiny PR0 if desired |
| `test_compare_days_is_structured_html` | stale legacy expectation | PR1 |
| `test_date_query_uses_exact_day_without_fallback` | stale phrase + real date-explicitness gap | PR1 |
| `test_detailed_analysis_guard_partial` | current code regression | PR1 |
| `test_metrics_availability_response_uses_fact_only` | intended product-contract change not reflected in tests | PR1 |
| `test_mode_output_constraints` | stale legacy expectation | PR1 |

## Recommended PR1 Scope

PR1 should be: **Renderer contract and partial-data truthfulness stabilization**.

Include:

1. Define durable message contracts for:
   - facts renderer
   - roast renderer
   - compare-days renderer
   - metrics availability answer
   - date-specific no-data answer
   - detailed partial analysis
2. Update stale tests to semantic assertions.
3. Add the missing partial-data limitations block.
4. Ensure date-specific answers include the target date when data is absent or partial.
5. Keep Gemini fallback untouched.
6. Keep storage architecture untouched.

Do not include:

- Cloudflare Worker
- Firestore migration changes
- Gist removal
- Render replacement
- broad communication redesign
- miniapp expansion

## Red Zones For PR1

- Do not reintroduce forced `Картоха` / `Пюрешка` copy just to satisfy stale tests.
- Do not make Gemini responsible for deterministic metric/date/history answers.
- Do not change storage source-of-truth while fixing renderer contracts.
- Do not remove Gist or Firestore in this PR.
- Do not make broad TOV rewrites; only stabilize contracts needed for truthful recovery.


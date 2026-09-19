---
name: project-key-levels
description: Weekly Key Levels feature — files, caching, chart bands, unit tests
metadata:
  type: project
---

Weekly S/R key levels UI tab — fully implemented.

**Why:** Read-only view of the `key_levels` table populated by the Saturday Claude job.

**How to apply:** Do not modify the Saturday write path. Cache expiry must target Saturday 11:00 AM IST.

## Backend
- `api/key_levels.py` — new FastAPI router; `GET /api/key-levels`
  - Queries `key_levels WHERE status='ACTIVE'`, groups by symbol (MAX analysis_date per symbol)
  - Backfills 120-day OHLCV per symbol via `price_history`
  - In-memory cache (same pattern as F&O stocks cache) keyed `'key-levels:all'`
  - `_next_saturday_11am_ist(now=None)` — accepts optional `now` for testability
- `main.py` — imports and registers `key_levels_router`

## Frontend
- `frontend/src/cache.ts` — added `'key-levels'` to CACHE_KEYS; added `nextSaturday11amIST()` (exported); `setCached` now accepts optional `expiry` ms; `getCached` checks stored expiry when present (backward-compatible)
- `frontend/src/types.ts` — added `KeyLevelsZone`, `KeyLevelsStock`, `KeyLevelsResponse`
- `frontend/src/api.ts` — added `fetchKeyLevels()` + `fetchKeyLevelsCached()` (passes `nextSaturday11amIST()` as expiry)
- `frontend/src/components/BottomNav.tsx` — added `'levels'` to `Screen` union and `TABS` (icon 📊)
- `frontend/src/App.tsx` — imports `KeyLevelsScreen`, mounts with `screen !== 'levels' ? 'hidden' : ''`
- `frontend/src/components/chart/LightweightChart.tsx` — added `PriceBand` interface (exported) and `priceBands?` prop; renders two price lines per zone (low↓/high↑); SUPPORT=#26a69a, RESISTANCE=#ef5350
- `frontend/src/screens/KeyLevelsScreen.tsx` — new screen with banner, search, collapsed/expanded stock cards, chart

## Tests
- `validation_tests/test_key_levels_cache.py` — 7 Python unit tests for `_next_saturday_11am_ist`; all pass
- `frontend/src/cache.test.ts` — 5 Vitest tests for `nextSaturday11amIST`; all pass
- `frontend/package.json` — added `vitest ^2.0.0` dev dep, `"test": "vitest run"` script

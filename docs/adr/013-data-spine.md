# ADR-013: Data spine — quote filtering + schema-versioned Parquet snapshot store

## Status
Accepted

## Context
Every downstream number (surface, Greeks, P&L explain, backtests) inherits the credibility
of the quotes it was computed from. NSE options data has stale quotes, crossed books, and
one-sided illiquid wings; feeding those into calibration produces confident nonsense. The
live paper loop (roadmap phase 4) also needs replayable history.

## Decision
`optitrade/data`:
- `RawQuote`/`RawChain` carry the microstructure fields the platform's Upstox tooling can
  populate (bid/ask/ltp/volume/OI/quote sizes/ltp age).
- Pure filter functions, each returning a plain-English rejection reason: crossed book,
  stale quote (no volume+OI or ltp age), wide spread (fraction of mid), zero-bid wing,
  non-positive mid. `filter_chain` reports clean quotes, per-quote rejection reasons, and
  per-reason stats — the filter report is itself an audit artifact.
- `SnapshotStore`: one Parquet file per capture at `root/{underlying}/{date}/{time}.parquet`
  with a `schema_version` column; lossless round-trip is test-enforced.
- `CaptureSource` protocol with a seeded `SyntheticSource` reference implementation; the
  Upstox adapter in `options_trading` implements the same protocol at the boundary.

Enforcing tests: `tests/unit/quant/test_quote_filters.py`,
`tests/unit/quant/test_snapshot_store.py`.

## Consequences
### Positive
- Calibration consumes only quotes that survived named filters; snapshots accumulate into
  replayable history for backtests and forensics.
### Negative
- Filter thresholds are policy choices that will need tuning against real NSE data.
### Risks
- Over-filtering thin expiries below the 4-quote calibration floor; the stats dict makes
  that visible per snapshot.

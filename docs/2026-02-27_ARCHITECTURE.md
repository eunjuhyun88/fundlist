# Fundlist Architecture

## Goal

Build an agent that:

1. Collects investment-related data from multiple external sources.
2. Normalizes records into a unified schema.
3. Stores records in SQLite with deduplication.
4. Returns list views for quick review.

## Sources (MVP)

- `openclaw`: GitHub events from `Virtual-Protocol/openclaw-acp` (releases, issues, commits)
- `sec`: SEC EDGAR filings by ticker
- `fred`: Macro series from FRED
- `coingecko`: Crypto market snapshots

## Data Flow

1. `collect` command decides which connectors to run.
2. Each connector fetches source data and maps to the `Item` model.
3. `SQLiteStore.insert_items` persists with `INSERT OR IGNORE`.
4. `list` command filters and returns recent records by source/symbol.

## Unified Item Schema

- `source`: origin system (`github_openclaw`, `sec_edgar`, `fred`, `coingecko`)
- `category`: source-specific type (`release`, `filing`, `macro_series`, ...)
- `symbol`: ticker/coin/repo key
- `title`: compact human-readable summary
- `url`: source URL
- `published_at`: event timestamp from source
- `payload_json`: full source payload (for downstream analysis)
- `fingerprint`: SHA-256 hash for deduplication

## Storage

SQLite table: `investment_items`

- Unique constraint on `fingerprint`
- Indexes:
  - `(source, published_at DESC)`
  - `(symbol, published_at DESC)`

Fundraising/operations tables:

- `fundraising_records`: CSV/TSV/XLSX imported fundraising rows
- `vc_submission_tasks`: deduped active submission management tasks
- `vc_ops_snapshots`: run-by-run summary history (deadline/speedrun counters)
- `vc_ops_events`: alert log (deadline soon/overdue, speedrun status)

## Reliability Rules

- Connector failures should not crash full collection.
- Missing optional API keys should produce warnings and skip the connector.
- SEC calls include a short delay to avoid aggressive request bursts.

## Extension Strategy

Add new source by creating:

1. `src/fundlist/sources/<name>.py`
2. `collect_<name>(...) -> list[Item]`
3. Registration in CLI source dispatch table.

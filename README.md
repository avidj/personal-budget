# personal-budget

Self-hosted, Docker-based budgeting pipeline around [Actual Budget](https://actualbudget.org):
pluggable source connectors (manual statement import today, FinTS/HBCI for German
banks next, market data later), a normaliser with a stable per-row key, an
idempotent importer into Actual, and a thin Postgres store for pipeline state.
An [n8n](https://n8n.io) workflow runs the scheduled refresh and writes a summary.

**Status: Phase 1 walking skeleton (2026-09).** Manual CSV import works end to end.

**User documentation: [`docs/`](docs/README.md)** (prerequisites, setup, daily use, configuration, troubleshooting).

## Design in one paragraph

Actual is the source of truth for accounts, transactions, categories, rules,
schedules and budgets. The pipeline only does what Actual cannot: ingest from
several sources, deduplicate with a stable per-row key mapped to Actual's
`imported_id`, and (later) value securities as balance adjustments on
investment accounts. Postgres holds only the import ledger, connector/session
state, prices and holdings lots. Nothing that touches a bank credential leaves
your machine. The connectors expose no payment operations.

## Quickstart (local, no bank API needed)

Prerequisites: Docker Desktop, a running n8n (any compose; note its network
name, default `n8n_default`).

```bash
cp .env.example .env               # set random passwords/token
cp sources.example.yaml sources.yaml
docker compose up -d --build       # Actual (localhost:5006), Postgres, pipeline (localhost:8000)
docker compose run --rm pipeline actual-setup --account Giro   # first start: server password, budget, account
```

Then drop a statement export into `inbox/postbank-giro/` and run:

```bash
docker compose exec pipeline pipeline run postbank-giro --dry-run   # what would happen
docker compose exec pipeline pipeline run postbank-giro             # import; file moves to processed/
docker compose exec pipeline pipeline summary
```

Import `n8n/refresh-workflow.json` into n8n, create a *Header Auth* credential
named `pipeline-api-token` with header `Authorization` and value
`Bearer <PIPELINE_API_TOKEN>`, and execute the workflow. The summary lands in
n8n's `local-files` folder as `budget-summary-<date>.md`.

To start over with an empty budget (after testing with sample data):
`scripts/reset-local.sh Giro`.

n8n note: the *Save summary* node writes into n8n's `/files` mount. n8n only
allows that when its container has `N8N_RESTRICT_FILE_ACCESS_TO=/files` set.
The scheduled trigger runs only once the workflow is activated in the n8n UI.

## HTTP API (used by n8n)

| Endpoint | Purpose |
|---|---|
| `POST /sources/{id}/fetch` | parse new statements → in-memory batch; returns counts and a `batch_id` |
| `POST /batches/{id}/dedup` | partition rows into new / changed / known via the ledger |
| `POST /batches/{id}/import` | write new rows into Actual (`{"dry_run": true}` to preview) |
| `POST /sources/{id}/run` | the three steps in one call (cron users) |
| `GET /summary` | balances, net worth, recent runs, inbox state |
| `GET /health` | database and Actual reachability |

All endpoints except `/health` require `Authorization: Bearer <PIPELINE_API_TOKEN>`.
Responses contain counts and identifiers only; transaction rows never leave the
pipeline process.

## Layout

```
pipeline/            Python 3.12: connectors, formats, keys, ledger, writer (actualpy), service, API, CLI
migrations/          versioned SQL for the pipeline database
n8n/                 sanitized workflow export
tests/               unit tests (pytest); fixtures contain fake data only
docker-compose.yml   actual + pipeline-db + pipeline; joins n8n's network
```

## Statement formats

`pipeline/formats` holds data-driven CSV profiles (`postbank-csv`, `generic-csv`).
Column names can be overridden per source in `sources.yaml` (`format_overrides`).
Check a file without importing: `pipeline inspect-csv path/to/file.csv --format postbank-csv`.

## Development

```bash
uv sync && uv run pytest && uv run ruff check
```

## License

Apache-2.0. See `LICENSE` and `NOTICE`.

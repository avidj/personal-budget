# 4. Configuration reference

## `.env`

| Variable | Required | Meaning |
|---|---|---|
| `POSTGRES_PASSWORD` | yes | password of the pipeline database |
| `ACTUAL_PASSWORD` | yes | Actual server password (set on first start by `actual-setup`) |
| `ACTUAL_FILE` | yes | name of the budget file, default `Household` |
| `ACTUAL_ENCRYPTION_PASSWORD` | no | only when the budget uses Actual's end-to-end encryption |
| `PIPELINE_API_TOKEN` | yes | bearer token n8n uses to call the pipeline |
| `LOG_LEVEL` | no | `INFO` (default) or `DEBUG` |
| `N8N_NETWORK` | no | Docker network of your n8n, default `n8n_default` |
| `TZ` | no | timezone for logs and summaries, default `Europe/Berlin` |
| `OLLAMA_URL` | no | local Ollama endpoint, default `http://host.docker.internal:11434` |
| `OLLAMA_MODEL` | no | model for category proposals, default `llama3.1:8b` |
| `CATEGORIZATION_BATCH` | no | payees per proposal run, default 25 |
| `CATEGORIZATION_EXAMPLES` | no | known payee→category pairs shown to the model (your rules first, then history), default 60 |

Set inside the container by compose (change only in `docker-compose.yml`):
`DATABASE_URL`, `ACTUAL_URL`, `ACTUAL_DATA_DIR`, `INBOX_DIR`, `SOURCES_FILE`,
`MIGRATIONS_DIR`, `BATCH_TTL_SECONDS`.

## `sources.yaml`

One entry per source (a bank account, a statement type). Example:

```yaml
sources:
  - id: postbank-giro            # used in commands, URLs, keys; keep it stable
    connector: manual_import     # statements dropped into the inbox
    format: postbank-csv         # statement format profile
    inbox_glob: "postbank-giro/*.csv"   # files to pick up, relative to the inbox
    actual_account: "Giro"       # exact account name in Actual
    account_ref: "pb-giro"       # short alias used in row keys and logs
    currency: EUR                # optional, default EUR
    format_overrides:            # optional: rename columns without code changes
      purpose: "Verwendungszweck"
```

Renaming `id` or `account_ref` changes every row key and would re-import
everything: treat them as permanent.

## Statement formats

| Profile | For | Details |
|---|---|---|
| `postbank-csv` | Postbank online-banking CSV export (2023+) | UTF-8, `;`, metadata lines then an 18-column header; Soll/Haben columns; the "Kontostand" metadata line supplies the bank's stated balance |
| `generic-csv` | hand-made statements (loans, cash) | UTF-8, `;`, header `booking_date;value_date;amount;currency;counterpart;counterpart_iban;purpose;type;reference`, ISO dates, `.` decimals, optional `balance;<amount>` metadata line |

Column names can be overridden per source with `format_overrides`, keyed by
the canonical field: `booking_date`, `value_date`, `amount`, `debit`, `credit`,
`currency`, `counterpart_name`, `counterpart_iban`, `purpose`, `posting_text`,
`bank_reference`, `end_to_end_id`.

Rows whose type ("Umsatzart") contains "vorgemerkt" are treated as pending and
skipped. Rows without a parsable booking date (metadata, footers) are skipped.

## Command line

Run as `docker compose exec pipeline pipeline <command>`.

| Command | What |
|---|---|
| `run <source> [--dry-run]` | fetch, dedup and import one source |
| `fetch <source>` | fetch and dedup only, print counts |
| `opening-balance <source> [--dry-run]` | set the Starting Balance from the bank's last stated balance |
| `summary` | balances, net worth, reconciliation, recent runs (JSON) |
| `apply-rules [--all]` | run Actual's existing rules over past transactions (default: uncategorised only) |
| `propose` | ask the local Ollama model for category proposals and queue them for review |
| `proposals [--status …]` | review table of proposed rules |
| `approve <ids…> \| --min-confidence X [--no-apply]` | create the rule in Actual, categorise the payee's past uncategorised rows |
| `reject <ids…>` | reject proposals; the payee is not offered again |
| `sources` | list configured sources with ledger size and last run; shows ledger data of removed sources |
| `forget-source <source> [--yes]` | delete a removed source's ledger and run history (Actual untouched) |
| `health` | database and Actual reachability |
| `inspect-csv <path> [--format …]` | parse a statement file and show columns and a few masked rows |
| `actual-setup [--account N]… [--off-budget-account N]…` | first start: server password, budget file, accounts |
| `migrate` | apply pending database migrations (done automatically on start) |
| `serve` | run the HTTP API (what the container does) |

## HTTP API

Base URL from n8n: `http://pipeline:8000`; from the host: `http://127.0.0.1:8000`.
All endpoints except `/health` need `Authorization: Bearer <PIPELINE_API_TOKEN>`.
Responses carry counts and identifiers, never transaction rows.

| Endpoint | Body | Returns |
|---|---|---|
| `GET /health` | | `{db, actual, sources}` |
| `POST /sources/{id}/fetch` | | `{batch_id, fetched, pending, files, window_from, window_to}` |
| `POST /batches/{id}/dedup` | | adds `{new, changed, unchanged}` |
| `POST /batches/{id}/import` | `{"dry_run": bool}` | adds `{added, updated, errors, account, statement_balance_cents, actual_balance_cents, balance_delta_cents}` |
| `POST /sources/{id}/run` | `?dry_run=` | the three steps in one call |
| `POST /sources/{id}/opening-balance` | `{"dry_run": bool}` | `{action, dated, starting_balance_cents, account_balance_after_cents, …}` |
| `POST /rules/apply` | `{"uncategorized_only": bool}` | `{considered, categorized, rules}` |
| `GET /categorization/candidates` | | candidates, categories, examples, rendered `prompt`, `schema` |
| `POST /categorization/propose` | | pipeline asks Ollama itself and stores proposals |
| `POST /categorization/proposals` | `{"proposals": [...], "model": "…"}` | validate and store proposals produced by any LLM |
| `GET /categorization/proposals` | `?status=pending` | the review table |
| `POST /categorization/approve` | `{"ids": [...], "min_confidence": x, "apply": bool}` | rules created in Actual |
| `POST /categorization/reject` | `{"ids": [...]}` | rejected payees |
| `GET /summary` | | accounts, net worth, reconciliation, pending proposals, ledger sizes, inbox state, recent runs |

Money is in integer cents. A batch lives one hour in the pipeline's memory;
after that, fetch again.

## Where data lives

| Data | Location | Back up? |
|---|---|---|
| Actual budget files | Docker volume `actual-data` | yes (or use Actual's export) |
| Pipeline state (ledger, runs) | Docker volume `pipeline-db-data` | nice to have; re-imports are deduplicated by Actual anyway |
| Statements | `inbox/` (bind mount) | your call; processed files can be deleted |
| Secrets | `.env`, `sources.yaml` | keep private |

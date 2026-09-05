# 5. Troubleshooting

First look: `docker compose ps` (all three containers up, `actual` and
`pipeline-db` healthy) and `docker compose logs pipeline --tail 50`.

## Errors from the pipeline

| Message | Cause | Fix |
|---|---|---|
| `401 invalid token` | wrong or missing bearer token | n8n credential value must be `Bearer <PIPELINE_API_TOKEN>` from `.env` |
| `503 PIPELINE_API_TOKEN is not configured` | empty token in `.env` | set it, `docker compose up -d pipeline` |
| `422 … header row with column 'Buchungstag' not found` | wrong format profile or the bank renamed columns | `pipeline inspect-csv <file>`; adjust `format` or `format_overrides` |
| `404 Actual account 'X' not found` | `actual_account` in `sources.yaml` does not match Actual | rename in Actual or in `sources.yaml`; create with `actual-setup --account X` |
| `409 unknown or expired batch` | more than an hour between fetch and import, or pipeline restarted | run the workflow again |
| `409 no statement balance known` | `opening-balance` before any import with a "Kontostand" line | import a statement first |
| `unknown source 'X'` | `sources.yaml` has no such `id` | check spelling; the file is mounted read-only, restart pipeline after edits: `docker compose restart pipeline` |

## Numbers look wrong in Actual

- Balance equals the sum of movements only → run `pipeline opening-balance <source>`
  once ([Daily use 3.3](03-daily-use.md#33-keeping-balances-right)).
- Summary says the balance differs from the bank → a period is missing between
  exports (export it), or a transaction was edited or deleted in Actual.
- Duplicates → should not happen; if it does, note the two rows' dates and
  amounts and report it. Delete one in Actual; the ledger will keep the other.

## n8n

<a id="n8n-network"></a>
- **Nodes fail with a connection error to `pipeline:8000`**: n8n and the
  pipeline are not on the same Docker network. Set `N8N_NETWORK` in `.env` to
  your n8n network name (`docker network ls`) and `docker compose up -d`.
  Without n8n at all, create the network once: `docker network create n8n_default`.
- **"Access to the file is not allowed"** on *Save summary*: add
  `N8N_RESTRICT_FILE_ACCESS_TO=/files` to the n8n container's environment and
  recreate it.
- **Credential not selected** after importing the workflow: open each HTTP node
  and pick `pipeline-api-token`.
- **Schedule does not fire**: the workflow must be *Active*; n8n's timezone is
  set in the workflow settings (Europe/Berlin).

## Actual

- Cannot log in: the server password is `ACTUAL_PASSWORD` in `.env`. It was set
  on the first `actual-setup`; changing `.env` later does not change it.
- Budget file missing: run `actual-setup` again (it recreates only what is missing).
- The pipeline fails after upgrading Actual by hand: the Actual server version
  and the pipeline's client library must match. Use the pinned image tag in
  `docker-compose.yml`; upgrades come with project releases.

## Reset everything local

`scripts/reset-local.sh Giro` — deletes Actual data and the pipeline database,
recreates an empty budget. `.env`, `sources.yaml` and n8n stay.

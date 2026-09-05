# 2. Setup

Time: about 15 minutes.

## 2.1 Get the code and create your local configuration

```bash
git clone <this repository> personal-budget
cd personal-budget
cp .env.example .env
cp sources.example.yaml sources.yaml
```

Edit `.env`: replace every `changeme` value with a random secret, e.g. from
`openssl rand -hex 24`. All fields are explained in the
[configuration reference](04-configuration.md#env). Both files are ignored by
git and never leave your machine.

Edit `sources.yaml` if your Actual account should not be called "Giro" or if
you use a different inbox folder. The example entry is ready for Postbank.

## 2.2 Start the stack

```bash
docker compose up -d --build
```

This starts three containers:

| Container | What | Where |
|---|---|---|
| `actual` | Actual Budget sync-server (pinned version) | http://localhost:5006 |
| `pipeline-db` | Postgres for the pipeline's own state | internal only |
| `pipeline` | the importer: HTTP API and CLI | http://127.0.0.1:8000 (API), `docker compose exec pipeline pipeline …` (CLI) |

If you do not run n8n, or its network has a different name, see
[Troubleshooting](05-troubleshooting.md#n8n-network).

## 2.3 Initialise Actual

```bash
docker compose run --rm pipeline actual-setup --account Giro
```

This sets the Actual server password (from `ACTUAL_PASSWORD`), creates the
budget file named in `ACTUAL_FILE` (default "Household") and an on-budget
account "Giro". Repeat with more `--account` or `--off-budget-account` options
for further accounts; existing ones are left alone.

Open http://localhost:5006, enter the server password, open "Household". The
account appears in the sidebar with a zero balance.

## 2.4 First import

Export a statement from your bank as CSV (Postbank: Umsätze → Export) and copy
the file into `inbox/postbank-giro/`. Then:

```bash
docker compose exec pipeline pipeline inspect-csv /inbox/postbank-giro/<file>.csv
docker compose exec pipeline pipeline run postbank-giro --dry-run
docker compose exec pipeline pipeline run postbank-giro
docker compose exec pipeline pipeline opening-balance postbank-giro
```

- `inspect-csv` shows how the file was parsed (columns, row count, whether the
  bank's balance was found). Nothing is imported.
- `run --dry-run` reports what would be imported.
- `run` imports; the file moves to `inbox/postbank-giro/processed/`.
- `opening-balance` adds one "Starting Balance" transaction so that the Actual
  balance equals the balance your bank stated in the export. Run it once after
  the first import; running it again is harmless.

Refresh Actual in the browser: the transactions are there, and the balance
matches your online banking.

## 2.5 n8n workflow (optional)

1. Make sure n8n can write into its shared folder: the n8n container needs
   `N8N_RESTRICT_FILE_ACCESS_TO=/files` in its environment and a volume such as
   `./local-files:/files`. Recreate the n8n container after changing this.
2. In n8n: **Credentials → Add credential → Header Auth**. Name it
   `pipeline-api-token`, header name `Authorization`, value
   `Bearer <your PIPELINE_API_TOKEN>`.
3. **Workflows → Import from file** → `n8n/refresh-workflow.json`. Open each
   HTTP node and make sure the credential `pipeline-api-token` is selected.
4. Click **Execute workflow**. Every node turns green and a file
   `budget-summary-<date>.md` appears in n8n's `local-files` folder.
5. Toggle the workflow **Active** for the daily 07:30 run.

Alternative import from the command line (n8n in a container named `n8n`,
file copied into its `local-files` folder):

```bash
docker exec n8n n8n import:workflow --input=/files/refresh-workflow.json
```

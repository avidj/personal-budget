# 3. Daily use

## 3.1 The routine

1. Export a statement as CSV from online banking. Overlap with the previous
   export is fine: rows already imported are recognised and skipped.
2. Copy the file into the inbox folder of the source, e.g. `inbox/postbank-giro/`.
   File names do not matter.
3. Let the n8n workflow run (07:30 daily when active) or trigger it manually
   with **Execute workflow**. Without n8n:
   `docker compose exec pipeline pipeline run postbank-giro`
4. Read the summary (`local-files/budget-summary-<date>.md` in n8n, or
   `docker compose exec pipeline pipeline summary`).
5. Review proposed rules (`pipeline proposals`, also listed in the summary) and
   approve the good ones ([Categorisation assistant](08-categorization.md));
   categorise the rest in Actual.

Processed files move to `inbox/<source>/processed/`. You can delete them at
any time; the import ledger remembers what was imported.

## 3.2 What the summary tells you

- **Accounts and net worth**: balances as Actual sees them.
- **This run**: rows fetched, added, updated, already known, pending skipped.
  Pending ("vorgemerkt") rows are never imported; they appear in a later export
  once booked.
- **Reconciliation**: for each source, whether Actual's balance matches the
  balance the bank stated in the newest export. A difference means either the
  opening balance was never set (run `pipeline opening-balance <source>`), or
  there is a gap between exports (export the missing period), or you edited a
  bank transaction in Actual.
- **Recent runs**: the last runs with their counts and status.

## 3.3 Keeping balances right

The bank export contains movements, not balances, except for one line stating
the current balance. The pipeline stores that stated balance with each run.
`pipeline opening-balance <source>` derives the starting balance from it and
posts a single "Starting Balance" transaction dated the day before your
earliest imported row. Running it again only adjusts that one transaction.
Prefer fixing gaps by exporting the missing period over re-running
`opening-balance`, otherwise the missing movements are hidden in the starting
balance.

## 3.4 More accounts, other banks

See [The n8n workflow](07-n8n-workflow.md#adding-a-source) for adding or removing
a source and for what to do when a bank changes its export format.

## 3.5 Starting over

`scripts/reset-local.sh Giro` deletes all local Actual data and the pipeline
database, recreates an empty budget with the given accounts and clears the
inbox. Your `.env`, `sources.yaml` and n8n are untouched. Use it after testing
with sample data.

## 3.6 Updating

```bash
git pull
docker compose up -d --build
```

Database migrations run automatically when the pipeline starts. The Actual
server image is pinned to a tested version; it is upgraded deliberately with a
release of this project, not by hand, because the pipeline's Actual client
library must match.

# 7. The n8n workflow, node by node

The workflow "Budget refresh" (`n8n/refresh-workflow.json`) is a straight line
with one branch. n8n only orchestrates: every working step is an HTTP call to
the pipeline, and n8n receives counts and identifiers, never transactions,
account numbers or credentials.

```
Every morning 07:30 ─┐
                     ├─▶ Fetch: Postbank Giro ─▶ Anything fetched? ─true──▶ Dedup against ledger ─▶ Import into Actual ─┐
When clicking        ┘                                │                                                                ├─▶ Apply existing rules ─▶ Categorisation candidates ─▶ Any candidates? ─true─▶ Propose categories ─▶ Store proposals ─┐
'Execute workflow'                                    └────────────────────────false───────────────────────────────────┘        (Ollama (local) + Proposal schema attached)      │                                          ├─▶ Summary from pipeline ─▶ Format summary ─▶ Markdown to file ─▶ Save summary
                                                                                                                                                                                  └──────────────────false──────────────────┘
```

| Node | Type | What it does | Output used downstream |
|---|---|---|---|
| **Every morning 07:30** | Schedule trigger | Starts the run daily at 07:30 (workflow timezone Europe/Berlin). Only fires while the workflow is *Active*. | — |
| **When clicking 'Execute workflow'** | Manual trigger | Same run, started by hand from the editor or the command line. | — |
| **Fetch: Postbank Giro** | HTTP Request, `POST /sources/postbank-giro/fetch` | Asks the pipeline to collect new rows for the source `postbank-giro`. What "fetch" means is decided by the source's connector in `sources.yaml`: today it parses the CSV files in `inbox/postbank-giro/`; with a bank connector it would call the bank. n8n never contacts the bank itself. The parsed rows stay inside the pipeline as a *batch*. | `batch_id`, `fetched`, `pending`, `files`, `window_from`, `window_to` |
| **Anything fetched?** | IF, `fetched > 0` | Skips the import steps when there is nothing new (inbox empty). Both branches end in the summary, so you still get a daily message. | — |
| **Dedup against ledger** | HTTP Request, `POST /batches/{batch_id}/dedup` | The pipeline computes each row's stable key and looks it up in its Postgres ledger: `new` (never seen), `changed` (seen, but the bank re-formatted the row), `unchanged`. | adds `new`, `changed`, `unchanged` |
| **Import into Actual** | HTTP Request, `POST /batches/{batch_id}/import` with `{"dry_run": false}` | The pipeline writes only the `new` rows into Actual through Actual's API with a deterministic `imported_id`, runs Actual's rules on them, records them in the ledger, moves the files to `processed/`, and stores the bank's stated balance with the run. Re-running never double-imports. | adds `added`, `updated`, `account`, `statement_balance_cents`, `actual_balance_cents`, `balance_delta_cents` |
| **Apply existing rules** | HTTP Request, `POST /rules/apply` | Runs the rules you created in Actual over past transactions that still have no category. Manual categorisations are never changed. Both branches of *Anything fetched?* lead here. | `considered`, `categorized`, `rules` |
| **Categorisation candidates** | HTTP Request, `GET /categorization/candidates` | Payees that still lack a category (most frequent first, one batch), your categories, examples from your own rules and history, and the rendered prompt for the model. | `count`, `prompt`, `system_prompt`, `schema` |
| **Any candidates?** | IF, `count > 0` | Skips the model when everything is categorised. | — |
| **Propose categories** | Basic LLM Chain with **Ollama (local)** as model and **Proposal schema** as output parser | Sends the prompt to the local Ollama model and parses the JSON answer: one category and confidence per payee. | `output.proposals` |
| **Store proposals** | HTTP Request, `POST /categorization/proposals` | The pipeline validates the proposals (known payee, existing category) and queues them for your review. | `stored`, `offered` |
| **Summary from pipeline** | HTTP Request, `GET /summary` | Balances per account, net worth, reconciliation (bank's stated balance vs Actual), ledger sizes, inbox state, recent runs. | the summary JSON |
| **Format summary** | Code (JavaScript) | Turns the JSON into Markdown: accounts and net worth, what this run did (or "nothing to import"), reconciliation line per source, the table of proposed rules awaiting review, recent runs. Reads the import node's output only if that node ran. | `text`, `filename` |
| **Markdown to file** | Convert to File (text) | Wraps the Markdown text as a binary file item. | binary `data` |
| **Save summary** | Read/Write Files from Disk (write) | Writes `/files/budget-summary-<date>.md` into n8n's shared `local-files` folder. Swap this node for Telegram, Signal, Slack or e-mail to change the channel; the text stays the same. | — |

Sticky notes on the canvas repeat the essentials for anyone looking at a screenshot.

## Credentials

All HTTP nodes use one n8n credential, *Header Auth* `pipeline-api-token`
(`Authorization: Bearer <PIPELINE_API_TOKEN>`). The Ollama model node uses an
*Ollama* credential `ollama-local` with base URL `http://host.docker.internal:11434`
(no key). These are the only two credentials n8n holds for this system.

## Adding a source

1. Create the account in Actual (or `pipeline actual-setup --account <Name>`).
2. Add an entry to `sources.yaml` (id, connector, format, inbox folder, Actual
   account name) and create the inbox folder. Restart the pipeline
   (`docker compose restart pipeline`) so it re-reads the file.
3. Test from the command line: `pipeline run <id> --dry-run`.
4. In n8n, copy the four nodes *Fetch → Anything fetched? → Dedup → Import*,
   rename them for the new source, change the URL of the copied Fetch node to
   `/sources/<id>/fetch`, and connect both ends to *Summary from pipeline*
   (the IF's false branch too). The summary picks up every source automatically.

## Removing a source

1. Delete its nodes in n8n.
2. Remove the entry from `sources.yaml`, restart the pipeline.
3. Optionally `pipeline forget-source <id>` to drop its ledger and run history.
   Transactions already in Actual stay; close or delete the account in Actual if
   you want it gone there too.

Do not remove and re-add a source to "reset" it: its `id` and `account_ref` are
part of every row key, and a new id would make the pipeline treat every old row
as new. Use `forget-source` or the reset script instead.

## When the bank changes its export

- **A column is added**: nothing to do; columns are matched by header name and
  unknown columns are ignored.
- **A column is renamed**: add a `format_overrides` entry for that field in
  `sources.yaml` (see [Configuration](04-configuration.md#statement-formats)).
- **The layout changes fundamentally**: a new format profile is needed in the
  software; until then `pipeline inspect-csv` shows what could not be parsed.
- Row keys use the *content* of a row (date, amount, counterpart, purpose,
  references), not column positions, so format changes do not cause
  re-imports as long as the content is the same.

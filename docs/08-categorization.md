# 8. Categorisation assistant (local AI)

New transactions arrive uncategorised unless an Actual rule matches them. The
assistant proposes a category per payee, you review the proposals, and the
pipeline turns approved ones into ordinary Actual rules. Everything runs on your
machine: the model is a local [Ollama](https://ollama.com) model.

## How it fits together

1. **Apply existing rules** — the rules you already created in Actual are run
   over past transactions that have no category yet. Manual categorisations are
   never touched. (No re-import needed; rules are applied in place.)
2. **Candidates** — payees that still have uncategorised transactions, most
   frequent first, up to `CATEGORIZATION_BATCH` per run. Payees with a pending
   or rejected proposal are skipped.
3. **Proposal** — the local model sees your category list, examples of how you
   already categorise (your rules first, then frequent payee→category pairs
   from history), and the candidates with masked purpose snippets. It answers
   with one category and a confidence per payee.
4. **Review** — proposals wait in a table until you decide.
5. **Approve** — the pipeline creates the rule `payee is X → set category Y` in
   Actual and categorises that payee's past uncategorised transactions. From
   then on Actual applies the rule on every import.

## Prerequisites

- Ollama installed and running on the host (`ollama serve` or the desktop app);
  a pulled model, default `llama3.1:8b` (`ollama pull llama3.1:8b`).
- Containers reach it via `http://host.docker.internal:11434` (Docker Desktop
  default). Override with `OLLAMA_URL` and `OLLAMA_MODEL` in `.env`.

## Using it from the command line

```bash
docker compose exec pipeline pipeline apply-rules          # your existing rules on past rows
docker compose exec pipeline pipeline propose              # ask the local model (one batch)
docker compose exec pipeline pipeline proposals            # the review table
docker compose exec pipeline pipeline approve 3f2a1c 9b7e  # ids or prefixes from the table
docker compose exec pipeline pipeline approve --min-confidence 0.95
docker compose exec pipeline pipeline reject 1d4e
```

`approve` creates the rule in Actual and categorises the payee's past
uncategorised transactions. Use `--no-apply` to only create the rule.
`reject` hides the payee from future proposals.

## Using it from the workflow

The n8n workflow runs steps 1–4 after every import ("Apply existing rules" →
"Categorisation candidates" → "Propose categories" with the local Ollama model
→ "Store proposals"). The daily summary lists pending proposals with their ids;
approve or reject them with the commands above. Approval stays a human step on
purpose.

## Reviewing well

- Confidence ≥ 0.9 proposals are usually right when the payee is a known chain
  or utility; still glance at them.
- The model only ever picks from your existing categories. If none fits, create
  the category in Actual first, then `propose` again for that payee.
- Rules created here are normal Actual rules: edit or delete them in Actual
  (More → Rules) like any other.
- Your own rules always win: they run first, and their payee→category pairs are
  shown to the model as examples.

## Privacy

Payee names, transaction counts, rounded average amounts and purpose snippets
with all numbers blanked are sent to the local model and pass through n8n's
execution data. No account numbers, balances or credentials are involved, and
nothing leaves the machine.

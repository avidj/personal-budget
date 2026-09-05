# 6. Actual Budget

Actual is the budgeting application and the single source of truth for your
accounts, transactions, categories, rules, schedules and budgets. The pipeline
only feeds it. Learn Actual from its own documentation:

- Documentation home: https://actualbudget.org/docs/
- Getting started and envelope budgeting: https://actualbudget.org/docs/getting-started/
- Accounts (on-budget vs off-budget): https://actualbudget.org/docs/accounts/
- Rules for automatic categorisation: https://actualbudget.org/docs/budgeting/rules/
- Schedules for recurring transactions: https://actualbudget.org/docs/schedules/
- Reports: https://actualbudget.org/docs/reports/
- Backups, sync and end-to-end encryption: https://actualbudget.org/docs/getting-started/sync/
- Release notes: https://actualbudget.org/docs/releases/

## What the pipeline does in Actual

- Creates transactions in the account named in `sources.yaml`, with the bank's
  counterpart as payee, the purpose text as notes, marked cleared, and a stable
  `imported_id` so the same bank row is never imported twice.
- Runs your Actual rules on newly imported transactions, so categorisation you
  set up in Actual applies automatically.
- Adds or adjusts one "Starting Balance" transaction per account on request
  (`opening-balance`), in the income category "Starting Balances", as Actual
  itself does when an account is opened with a balance.
- Reads balances for the summary.

## What the pipeline never does

- Change categories, notes or payees of existing transactions, or delete
  anything.
- Create accounts, categories or rules on its own (except `actual-setup`, which
  you run deliberately).
- Move money. There are no payment operations anywhere in the code.

## Recommended habits in Actual

- Categorise once, then create a **rule** from the transaction (right-click →
  Create rule) so future imports land in the right category.
- Use **schedules** for recurring bills; imported transactions are linked to
  them automatically when they match.
- Reconcile occasionally against your online banking; the summary's
  reconciliation line tells you when Actual and the bank disagree.
- Keep the Actual browser tab or app in sync: it pulls the pipeline's changes
  on open and periodically; the sync button (top left) forces it.

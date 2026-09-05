-- SPDX-License-Identifier: Apache-2.0
-- Bank-stated balance at fetch time and Actual's balance after import, for drift reporting.
alter table import_run add column if not exists statement_balance_cents bigint;
alter table import_run add column if not exists statement_balance_date date;
alter table import_run add column if not exists actual_balance_cents bigint;

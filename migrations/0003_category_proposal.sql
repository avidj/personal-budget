-- SPDX-License-Identifier: Apache-2.0
-- Categorisation proposals awaiting human review. Payee names and masked purpose
-- snippets only; no amounts per transaction, no identifiers.
create table if not exists category_proposal (
  id            uuid primary key,
  payee         text        not null,
  category      text        not null,
  confidence    numeric(4,3) not null,
  reason        text,
  tx_count      int,
  avg_amount_cents bigint,
  snippets      jsonb,
  model         text,
  status        text        not null check (status in ('pending', 'approved', 'rejected', 'superseded')),
  created_at    timestamptz not null default now(),
  decided_at    timestamptz,
  rule_id       text
);
create index if not exists category_proposal_status_idx on category_proposal (status, created_at desc);
create unique index if not exists category_proposal_pending_payee_uq
  on category_proposal (payee) where status = 'pending';

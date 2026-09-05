-- SPDX-License-Identifier: Apache-2.0
-- Thin pipeline state. No transaction content, no balances, no credentials.

-- 1. Source row -> Actual imported_id.
create table if not exists import_ledger (
  source_id          text        not null,
  source_key         text        not null,
  imported_id        text        not null,
  actual_account_id  text        not null,
  booking_date       date        not null,
  amount_cents       bigint      not null,
  raw_fingerprint    text        not null,
  first_run_id       uuid        not null,
  last_run_id        uuid        not null,
  first_seen_at      timestamptz not null default now(),
  last_seen_at       timestamptz not null default now(),
  primary key (source_id, source_key)
);
create unique index if not exists import_ledger_actual_uq
  on import_ledger (actual_account_id, imported_id);

-- 2. Connector/session state per source (used by FinTS-type connectors).
create table if not exists sync_state (
  source_id          text primary key,
  connector          text        not null,
  cursor_date        date,
  client_state       bytea,
  client_state_mac   bytea,
  tan_mechanism      text,
  tan_medium         text,
  statement_format   text,
  last_sca_at        timestamptz,
  last_success_at    timestamptz,
  last_error         text,
  updated_at         timestamptz not null default now()
);

-- 3. Pending strong-customer-authentication challenges (short-lived).
create table if not exists tan_challenge (
  id                 uuid primary key,
  source_id          text        not null references sync_state (source_id),
  created_at         timestamptz not null default now(),
  expires_at         timestamptz not null,
  decoupled          boolean     not null,
  challenge_text     text,
  challenge_media    bytea,
  dialog_data        bytea,
  tan_request_data   bytea,
  status             text        not null
                     check (status in ('pending', 'completed', 'expired', 'failed'))
);

-- 4. Daily prices per instrument, minor units.
create table if not exists price (
  isin               text        not null,
  price_date         date        not null,
  source             text        not null,
  close_cents        bigint      not null,
  currency           char(3)     not null,
  fetched_at         timestamptz not null default now(),
  primary key (isin, price_date, source)
);

-- 5. Holdings lots; a re-imported statement supersedes (valid_to), never deletes.
create table if not exists holdings_lot (
  id                 uuid primary key,
  source_id          text        not null,
  account_ref        text        not null,
  isin               text        not null,
  acquired_on        date        not null,
  pieces             numeric(18,6) not null,
  cost_basis_cents   bigint,
  plan_type          text,
  source_key         text        not null,
  valid_from         date        not null,
  valid_to           date,
  unique (source_id, source_key, valid_from)
);

-- 6. Observability.
create table if not exists import_run (
  id                 uuid primary key,
  source_id          text        not null,
  started_at         timestamptz not null default now(),
  finished_at        timestamptz,
  window_from        date,
  window_to          date,
  fetched            int,
  new_rows           int,
  updated_rows       int,
  unchanged_rows     int,
  pending_rows       int,
  status             text        not null,
  error              text,
  trigger_source     text
);
create index if not exists import_run_source_started_idx on import_run (source_id, started_at desc);

# SPDX-License-Identifier: Apache-2.0
"""Import ledger and run bookkeeping (first line of dedup defence)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import psycopg

from pipeline.dto import TransactionDTO


@dataclass
class Partition:
    new: list[TransactionDTO] = field(default_factory=list)
    changed: list[TransactionDTO] = field(default_factory=list)
    unchanged: list[TransactionDTO] = field(default_factory=list)


class Ledger:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    # --- dedup ------------------------------------------------------------

    def partition(self, source_id: str, rows: list[TransactionDTO]) -> Partition:
        part = Partition()
        if not rows:
            return part
        keys = [r.source_key for r in rows]
        known = {
            k: fp
            for k, fp in self.conn.execute(
                "select source_key, raw_fingerprint from import_ledger "
                "where source_id = %s and source_key = any(%s)",
                (source_id, keys),
            )
        }
        for row in rows:
            fp = known.get(row.source_key)
            if fp is None:
                part.new.append(row)
            elif fp != row.raw_fingerprint:
                part.changed.append(row)
            else:
                part.unchanged.append(row)
        return part

    def record(
        self, source_id: str, rows: list[TransactionDTO], actual_account_id: str, run_id: uuid.UUID
    ) -> None:
        if not rows:
            return
        with self.conn.cursor() as cur:
            cur.executemany(
                """
                insert into import_ledger
                  (source_id, source_key, imported_id, actual_account_id, booking_date,
                   amount_cents, raw_fingerprint, first_run_id, last_run_id)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (source_id, source_key) do update set
                  raw_fingerprint = excluded.raw_fingerprint,
                  amount_cents    = excluded.amount_cents,
                  booking_date    = excluded.booking_date,
                  last_run_id     = excluded.last_run_id,
                  last_seen_at    = now()
                """,
                [
                    (
                        source_id,
                        r.source_key,
                        r.imported_id,
                        actual_account_id,
                        r.booking_date,
                        r.amount_cents,
                        r.raw_fingerprint,
                        run_id,
                        run_id,
                    )
                    for r in rows
                ],
            )

    def touch(self, source_id: str, rows: list[TransactionDTO], run_id: uuid.UUID) -> None:
        if not rows:
            return
        self.conn.execute(
            "update import_ledger set last_seen_at = now(), last_run_id = %s "
            "where source_id = %s and source_key = any(%s)",
            (run_id, source_id, [r.source_key for r in rows]),
        )

    # --- runs -------------------------------------------------------------

    def start_run(self, source_id: str, trigger: str) -> uuid.UUID:
        run_id = uuid.uuid4()
        self.conn.execute(
            "insert into import_run (id, source_id, status, trigger_source) values (%s, %s, 'fetched', %s)",
            (run_id, source_id, trigger),
        )
        return run_id

    def finish_run(
        self,
        run_id: uuid.UUID,
        *,
        status: str,
        fetched: int,
        new_rows: int,
        updated_rows: int,
        unchanged_rows: int,
        pending_rows: int,
        window_from: date | None,
        window_to: date | None,
        error: str | None = None,
        statement_balance_cents: int | None = None,
        statement_balance_date: date | None = None,
        actual_balance_cents: int | None = None,
    ) -> None:
        self.conn.execute(
            """
            update import_run set finished_at = now(), status = %s, fetched = %s, new_rows = %s,
              updated_rows = %s, unchanged_rows = %s, pending_rows = %s, window_from = %s,
              window_to = %s, error = %s, statement_balance_cents = %s, statement_balance_date = %s,
              actual_balance_cents = %s
            where id = %s
            """,
            (
                status,
                fetched,
                new_rows,
                updated_rows,
                unchanged_rows,
                pending_rows,
                window_from,
                window_to,
                error,
                statement_balance_cents,
                statement_balance_date,
                actual_balance_cents,
                run_id,
            ),
        )

    def latest_statement_balance(self, source_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            """
            select statement_balance_cents, statement_balance_date, started_at
            from import_run
            where source_id = %s and statement_balance_cents is not null and status = 'ok'
            order by started_at desc limit 1
            """,
            (source_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "statement_balance_cents": row[0],
            "statement_balance_date": row[1],
            "seen_at": row[2],
        }

    def earliest_booking_date(self, source_id: str) -> date | None:
        row = self.conn.execute(
            "select min(booking_date) from import_ledger where source_id = %s", (source_id,)
        ).fetchone()
        return row[0] if row else None

    def recent_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        cur = self.conn.execute(
            """
            select id, source_id, started_at, finished_at, status, fetched, new_rows, updated_rows,
                   unchanged_rows, pending_rows, window_from, window_to, error, trigger_source,
                   statement_balance_cents, actual_balance_cents
            from import_run order by started_at desc limit %s
            """,
            (limit,),
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]

    def abandon_stale_runs(self) -> int:
        """Runs still 'fetched' belong to a batch of a previous process; nothing can import them."""
        cur = self.conn.execute(
            "update import_run set status = 'abandoned', finished_at = now(), "
            "error = 'pipeline restarted before import' where status = 'fetched'"
        )
        return cur.rowcount

    def forget_source(self, source_id: str) -> int:
        cur = self.conn.execute("delete from import_ledger where source_id = %s", (source_id,))
        self.conn.execute("delete from import_run where source_id = %s", (source_id,))
        return cur.rowcount

    def ledger_size(self) -> dict[str, int]:
        return {
            source: n
            for source, n in self.conn.execute(
                "select source_id, count(*) from import_ledger group by source_id order by source_id"
            )
        }

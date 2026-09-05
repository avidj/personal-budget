# SPDX-License-Identifier: Apache-2.0
"""Orchestration: fetch -> dedup -> import, plus summary. Rows never leave this process."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pipeline import db
from pipeline.categorization import (
    CategorizationContext,
    ProposalStore,
    invalid_reasons,
    validate_proposals,
)
from pipeline.config import Settings, SourcesConfig
from pipeline.connectors import Connector
from pipeline.dto import FetchResult
from pipeline.ledger import Ledger, Partition
from pipeline.llm import OllamaClient, schema_with_categories
from pipeline.writer import BudgetWriter

log = logging.getLogger(__name__)


@dataclass
class Batch:
    id: str
    source_id: str
    run_id: uuid.UUID
    trigger: str
    fetch: FetchResult
    created_at: float = field(default_factory=time.monotonic)
    partition: Partition | None = None
    imported: bool = False

    def describe(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "batch_id": self.id,
            "source_id": self.source_id,
            "fetched": len(self.fetch.rows),
            "pending": self.fetch.pending_rows,
            "files": self.fetch.files,
            "window_from": self.fetch.window_from,
            "window_to": self.fetch.window_to,
        }
        if self.partition is not None:
            d.update(
                new=len(self.partition.new),
                changed=len(self.partition.changed),
                unchanged=len(self.partition.unchanged),
            )
        return d


class PipelineError(Exception):
    pass


def _jsonable(value: Any) -> Any:
    """Timestamps in local time (container TZ), dates as ISO strings."""
    if isinstance(value, datetime):
        return value.astimezone().isoformat(timespec="minutes")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


class PipelineService:
    def __init__(
        self,
        settings: Settings,
        sources: SourcesConfig,
        connectors: dict[str, Connector],
        writer: BudgetWriter,
    ):
        self.settings = settings
        self.sources = sources
        self.connectors = connectors
        self.writer = writer
        self._batches: dict[str, Batch] = {}

    # --- helpers ------------------------------------------------------------

    def _connection(self):
        return db.connect(self.settings.database_url)

    def _expire_batches(self) -> None:
        now = time.monotonic()
        expired = [
            b
            for b in self._batches.values()
            if now - b.created_at > self.settings.batch_ttl_seconds
        ]
        for batch in expired:
            del self._batches[batch.id]
            if not batch.imported:
                with self._connection() as conn:
                    Ledger(conn).finish_run(
                        batch.run_id,
                        status="abandoned",
                        fetched=len(batch.fetch.rows),
                        new_rows=0,
                        updated_rows=0,
                        unchanged_rows=0,
                        pending_rows=batch.fetch.pending_rows,
                        window_from=batch.fetch.window_from,
                        window_to=batch.fetch.window_to,
                        error="batch expired before import",
                    )
                    conn.commit()

    def _batch(self, batch_id: str) -> Batch:
        self._expire_batches()
        try:
            return self._batches[batch_id]
        except KeyError as exc:
            raise PipelineError(f"unknown or expired batch '{batch_id}'") from exc

    # --- steps ---------------------------------------------------------------

    def fetch(self, source_id: str, *, trigger: str = "api") -> dict[str, Any]:
        source = self.sources.get(source_id)
        connector = self.connectors.get(source.connector)
        if connector is None:
            raise PipelineError(f"connector '{source.connector}' not available")
        with self._connection() as conn:
            ledger = Ledger(conn)
            run_id = ledger.start_run(source_id, trigger)
            try:
                result = connector.fetch_transactions(source)
            except Exception as exc:
                ledger.finish_run(
                    run_id,
                    status="error",
                    fetched=0,
                    new_rows=0,
                    updated_rows=0,
                    unchanged_rows=0,
                    pending_rows=0,
                    window_from=None,
                    window_to=None,
                    error=f"{type(exc).__name__}: {exc}",
                )
                conn.commit()
                raise
            conn.commit()
        batch = Batch(
            id=uuid.uuid4().hex[:12],
            source_id=source_id,
            run_id=run_id,
            trigger=trigger,
            fetch=result,
        )
        if not result.rows:
            # nothing to import: close the run right away so it does not linger as 'fetched'
            with self._connection() as conn:
                Ledger(conn).finish_run(
                    run_id,
                    status="ok",
                    fetched=0,
                    new_rows=0,
                    updated_rows=0,
                    unchanged_rows=0,
                    pending_rows=result.pending_rows,
                    window_from=None,
                    window_to=None,
                    statement_balance_cents=result.statement_balance_cents,
                    statement_balance_date=result.statement_balance_date,
                )
                conn.commit()
            batch.imported = True
        self._expire_batches()
        self._batches[batch.id] = batch
        log.info(
            "fetched %s: %d rows from %d file(s)", source_id, len(result.rows), len(result.files)
        )
        return batch.describe()

    def dedup(self, batch_id: str) -> dict[str, Any]:
        batch = self._batch(batch_id)
        with self._connection() as conn:
            batch.partition = Ledger(conn).partition(batch.source_id, batch.fetch.rows)
        log.info(
            "dedup %s: new=%d changed=%d unchanged=%d",
            batch.source_id,
            len(batch.partition.new),
            len(batch.partition.changed),
            len(batch.partition.unchanged),
        )
        return batch.describe()

    def import_batch(self, batch_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        batch = self._batch(batch_id)
        if batch.imported:
            raise PipelineError(f"batch '{batch_id}' was already imported")
        if batch.partition is None:
            self.dedup(batch_id)
        assert batch.partition is not None
        source = self.sources.get(batch.source_id)
        part = batch.partition
        status = "ok"
        error: str | None = None
        result = None
        actual_balance: int | None = None
        try:
            with self.writer.session() as session:
                account = session.resolve_account(source.actual_account)
                result = session.import_transactions(
                    source.actual_account, part.new, dry_run=dry_run
                )
            if not dry_run:
                with self.writer.session() as session:
                    actual_balance = session.resolve_account(source.actual_account).balance_cents
                with self._connection() as conn:
                    ledger = Ledger(conn)
                    ledger.record(
                        batch.source_id, part.new + part.changed, account.id, batch.run_id
                    )
                    ledger.touch(batch.source_id, part.unchanged, batch.run_id)
                    conn.commit()
                connector = self.connectors[source.connector]
                connector.mark_imported(source, batch.fetch)
                batch.imported = True
        except Exception as exc:
            status, error = "error", f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if not dry_run:
                with self._connection() as conn:
                    Ledger(conn).finish_run(
                        batch.run_id,
                        status=status,
                        fetched=len(batch.fetch.rows),
                        new_rows=result.added if result else 0,
                        updated_rows=result.updated if result else 0,
                        unchanged_rows=len(part.unchanged) + (result.unchanged if result else 0),
                        pending_rows=batch.fetch.pending_rows,
                        window_from=batch.fetch.window_from,
                        window_to=batch.fetch.window_to,
                        error=error,
                        statement_balance_cents=batch.fetch.statement_balance_cents,
                        statement_balance_date=batch.fetch.statement_balance_date,
                        actual_balance_cents=actual_balance,
                    )
                    conn.commit()
        out = batch.describe()
        out.update(
            dry_run=dry_run,
            added=result.added,
            updated=result.updated,
            errors=result.errors,
            account=account.name,
            statement_balance_cents=batch.fetch.statement_balance_cents,
            actual_balance_cents=actual_balance,
        )
        if batch.fetch.statement_balance_cents is not None and actual_balance is not None:
            out["balance_delta_cents"] = actual_balance - batch.fetch.statement_balance_cents
        log.info(
            "import %s: added=%d updated=%d dry_run=%s",
            batch.source_id,
            result.added,
            result.updated,
            dry_run,
        )
        return out

    def run(self, source_id: str, *, dry_run: bool = False, trigger: str = "cli") -> dict[str, Any]:
        batch = self.fetch(source_id, trigger=trigger)
        self.dedup(batch["batch_id"])
        return self.import_batch(batch["batch_id"], dry_run=dry_run)

    def opening_balance(self, source_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        """Seed/adjust the account's Starting Balance so it totals the bank's last stated balance.

        Uses the statement balance recorded by the most recent successful import and
        dates the starting balance the day before the earliest imported row.
        """
        source = self.sources.get(source_id)
        with self._connection() as conn:
            ledger = Ledger(conn)
            latest = ledger.latest_statement_balance(source_id)
            earliest = ledger.earliest_booking_date(source_id)
        if latest is None:
            raise PipelineError(
                f"no statement balance known for '{source_id}': import a statement whose metadata states the balance first"
            )
        if earliest is None:
            raise PipelineError(f"nothing imported yet for '{source_id}'")
        dated = earliest - timedelta(days=1)
        with self.writer.session() as session:
            res = session.set_starting_balance(
                source.actual_account,
                target_balance_cents=latest["statement_balance_cents"],
                dated=dated,
                imported_id=f"{source_id}:starting-balance",
                dry_run=dry_run,
            )
        log.info("opening balance %s: %s (dry_run=%s)", source_id, res.action, dry_run)
        return {
            "source_id": source_id,
            "account": source.actual_account,
            "action": res.action,
            "dry_run": dry_run,
            "dated": dated,
            "statement_balance_cents": latest["statement_balance_cents"],
            "statement_balance_date": latest["statement_balance_date"],
            "previous_starting_balance_cents": res.previous_amount_cents,
            "starting_balance_cents": res.amount_cents,
            "account_balance_after_cents": res.account_balance_after_cents,
        }

    # --- categorisation assistant --------------------------------------------

    def apply_rules(self, *, uncategorized_only: bool = True) -> dict[str, Any]:
        """Run Actual's existing rules over existing transactions (default: uncategorised only)."""
        with self.writer.session() as session:
            result = session.apply_rules(uncategorized_only=uncategorized_only)
        log.info("apply rules: %s", result)
        return {"uncategorized_only": uncategorized_only, **result}

    def categorization_context(self) -> CategorizationContext:
        with self._connection() as conn:
            excluded = ProposalStore(conn).excluded_payees()
        with self.writer.session() as session:
            return CategorizationContext(
                candidates=session.uncategorized_candidates(
                    limit=self.settings.categorization_batch, exclude_payees=excluded
                ),
                categories=session.categories(),
                examples=session.categorization_examples(
                    limit=self.settings.categorization_examples
                ),
                model_hint=self.settings.ollama_model,
            )

    def candidates(self) -> dict[str, Any]:
        ctx = self.categorization_context()
        d = ctx.to_dict()
        d["count"] = len(ctx.candidates)
        return d

    def store_proposals(self, raw: dict[str, Any], *, model: str | None = None) -> dict[str, Any]:
        """Validate proposals (from any LLM) against current candidates and store them for review."""
        ctx = self.categorization_context()
        # refs stay valid for the current batch; additionally accept any uncategorised payee by name
        with self.writer.session() as session:
            others = session.uncategorized_candidates(
                limit=10_000, exclude_payees={c.payee for c in ctx.candidates}
            )
        ctx.candidates = ctx.candidates + others
        proposals = validate_proposals(raw, ctx, model or self.settings.ollama_model)
        with self._connection() as conn:
            n = ProposalStore(conn).store(proposals)
            conn.commit()
        reasons = invalid_reasons(raw, ctx)
        log.info(
            "stored %d proposal(s) of %d offered; dropped: %s",
            n,
            len(raw.get("proposals", [])),
            reasons,
        )
        return {"stored": n, "offered": len(raw.get("proposals", [])), "dropped": reasons}

    def propose(self) -> dict[str, Any]:
        """Built-in path: ask the local Ollama model and store the proposals."""
        ctx = self.categorization_context()
        if not ctx.candidates:
            return {"candidates": 0, "stored": 0}
        client = OllamaClient(self.settings.ollama_url, self.settings.ollama_model)
        if not client.available():
            raise PipelineError(f"Ollama not reachable at {self.settings.ollama_url}")
        from pipeline.categorization import SYSTEM_PROMPT, render_prompt

        raw = client.structured(
            SYSTEM_PROMPT, render_prompt(ctx), schema_with_categories(ctx.category_names())
        )
        out = self.store_proposals(raw, model=self.settings.ollama_model)
        out["candidates"] = len(ctx.candidates)
        return out

    def proposals(self, status: str | None = "pending") -> list[dict[str, Any]]:
        with self._connection() as conn:
            return ProposalStore(conn).list(status)

    def approve(
        self, ids: list[str], *, min_confidence: float | None = None, apply: bool = True
    ) -> dict[str, Any]:
        with self._connection() as conn:
            store = ProposalStore(conn)
            chosen = store.resolve_ids(ids, min_confidence)
            results = []
            with self.writer.session() as session:
                for p in chosen:
                    created = session.create_category_rule(
                        p["payee"], p["category"], apply_to_uncategorized=apply
                    )
                    store.decide(p["id"], "approved", created["rule_id"])
                    results.append({"payee": p["payee"], "category": p["category"], **created})
            conn.commit()
        log.info("approved %d proposal(s)", len(results))
        return {"approved": results}

    def reject(self, ids: list[str]) -> dict[str, Any]:
        with self._connection() as conn:
            store = ProposalStore(conn)
            chosen = store.resolve_ids(ids, None)
            for p in chosen:
                store.decide(p["id"], "rejected")
            conn.commit()
        return {"rejected": [p["payee"] for p in chosen]}

    # --- read side -----------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        with self.writer.session() as session:
            accounts = session.list_accounts()
        with self._connection() as conn:
            ledger = Ledger(conn)
            runs = ledger.recent_runs(10)
            sizes = ledger.ledger_size()
        by_name = {a.name: a for a in accounts}
        reconciliation = []
        with self._connection() as conn:
            ledger = Ledger(conn)
            for source in self.sources.sources:
                latest = ledger.latest_statement_balance(source.id)
                acct = by_name.get(source.actual_account)
                if latest is None or acct is None:
                    continue
                delta = acct.balance_cents - latest["statement_balance_cents"]
                reconciliation.append(
                    {
                        "source_id": source.id,
                        "account": acct.name,
                        "statement_balance_cents": latest["statement_balance_cents"],
                        "statement_balance_date": _jsonable(latest["statement_balance_date"]),
                        "actual_balance_cents": acct.balance_cents,
                        "delta_cents": delta,
                        "reconciled": delta == 0,
                    }
                )
        with self._connection() as conn:
            pending = ProposalStore(conn).list("pending", limit=15)
        on_budget = sum(a.balance_cents for a in accounts if not a.off_budget)
        off_budget = sum(a.balance_cents for a in accounts if a.off_budget)
        inbox = {}
        for source in self.sources.sources:
            connector = self.connectors.get(source.connector)
            if connector is not None and hasattr(connector, "_files"):
                try:
                    inbox[source.id] = len(connector._files(source))  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    inbox[source.id] = -1
        return {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "accounts": [
                {"name": a.name, "off_budget": a.off_budget, "balance_cents": a.balance_cents}
                for a in accounts
            ],
            "on_budget_cents": on_budget,
            "off_budget_cents": off_budget,
            "net_worth_cents": on_budget + off_budget,
            "ledger_rows": sizes,
            "reconciliation": reconciliation,
            "proposals_pending": [
                {k: p[k] for k in ("short_id", "payee", "category", "confidence", "tx_count")}
                for p in pending
            ],
            "inbox_pending_files": inbox,
            "recent_runs": [
                {**{k: _jsonable(v) for k, v in r.items()}, "id": str(r["id"])} for r in runs
            ],
        }

    def health(self) -> dict[str, Any]:
        db_ok = True
        try:
            with self._connection() as conn:
                conn.execute("select 1")
        except Exception:  # noqa: BLE001
            db_ok = False
        return {
            "db": db_ok,
            "actual": self.writer.ping(),
            "sources": [s.id for s in self.sources.sources],
        }

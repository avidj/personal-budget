# SPDX-License-Identifier: Apache-2.0
"""Command line for people who do not use n8n (and for setup)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

import typer

from pipeline import db
from pipeline.config import Settings, load_sources
from pipeline.connectors import build_registry
from pipeline.formats import get_profile, parse_csv
from pipeline.ledger import Ledger
from pipeline.masking import configure_logging, mask_iban
from pipeline.service import PipelineService
from pipeline.writer.actualpy_writer import ActualpyWriter

app = typer.Typer(no_args_is_help=True, add_completion=False)
log = logging.getLogger("pipeline.cli")


def _service(settings: Settings) -> PipelineService:
    sources = load_sources(settings.sources_file)
    return PipelineService(
        settings, sources, build_registry(settings.inbox_dir), ActualpyWriter(settings)
    )


def _echo(obj) -> None:
    typer.echo(json.dumps(obj, indent=2, default=str))


@app.callback()
def _main(log_level: str = "INFO") -> None:
    configure_logging(log_level)


@app.command()
def migrate() -> None:
    """Apply pending SQL migrations to the pipeline database."""
    settings = Settings()
    applied = db.migrate(settings.database_url, settings.migrations_dir)
    typer.echo(f"applied: {applied or 'nothing (up to date)'}")


@app.command()
def serve(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Run the HTTP API."""
    import uvicorn

    uvicorn.run("pipeline.api:app", host=host, port=port, log_level=Settings().log_level.lower())


@app.command()
def run(
    source_id: str,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Fetch and dedup, report what would be imported")
    ] = False,
) -> None:
    """Fetch, dedup and import one source."""
    _echo(_service(Settings()).run(source_id, dry_run=dry_run, trigger="cli"))


@app.command()
def fetch(source_id: str) -> None:
    """Fetch and dedup only; prints counts."""
    svc = _service(Settings())
    batch = svc.fetch(source_id, trigger="cli")
    _echo(svc.dedup(batch["batch_id"]))


@app.command("opening-balance")
def opening_balance(
    source_id: str,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the starting balance that would be set")
    ] = False,
) -> None:
    """Seed or adjust the account's Starting Balance from the bank balance stated in the last import."""
    _echo(_service(Settings()).opening_balance(source_id, dry_run=dry_run))


@app.command("apply-rules")
def apply_rules(
    all_transactions: Annotated[
        bool, typer.Option("--all", help="Also re-run rules on categorised transactions")
    ] = False,
) -> None:
    """Run Actual's existing rules over past transactions (default: uncategorised only)."""
    _echo(_service(Settings()).apply_rules(uncategorized_only=not all_transactions))


@app.command()
def propose() -> None:
    """Ask the local Ollama model for category proposals and queue them for review."""
    _echo(_service(Settings()).propose())


@app.command()
def proposals(status: str = "pending") -> None:
    """Show the review table of proposed rules."""
    rows = _service(Settings()).proposals(status)
    if not rows:
        typer.echo(f"no {status} proposals")
        return
    typer.echo(f"{'id':<9} {'conf':>5} {'n':>4}  {'payee':<40} -> category")
    for p in rows:
        typer.echo(
            f"{p['short_id']:<9} {p['confidence']:>5.2f} {p['tx_count'] or 0:>4}  {p['payee'][:40]:<40} -> {p['category']}"
        )
    typer.echo(
        "\napprove: pipeline approve <id> [<id>…] | pipeline approve --min-confidence 0.9 | reject: pipeline reject <id>"
    )


@app.command()
def approve(
    ids: Annotated[
        list[str] | None, typer.Argument(help="Proposal ids (prefixes are fine)")
    ] = None,
    min_confidence: Annotated[float | None, typer.Option("--min-confidence")] = None,
    no_apply: Annotated[
        bool,
        typer.Option("--no-apply", help="Create the rule but leave past transactions untouched"),
    ] = False,
) -> None:
    """Approve proposals: create the rule in Actual and categorise past uncategorised rows of that payee."""
    if not ids and min_confidence is None:
        raise typer.BadParameter("give proposal ids or --min-confidence")
    _echo(
        _service(Settings()).approve(ids or [], min_confidence=min_confidence, apply=not no_apply)
    )


@app.command()
def reject(
    ids: Annotated[list[str], typer.Argument(help="Proposal ids (prefixes are fine)")],
) -> None:
    """Reject proposals; the payee is not offered again."""
    _echo(_service(Settings()).reject(ids))


@app.command()
def summary() -> None:
    """Balances, net worth, recent runs."""
    _echo(_service(Settings()).summary())


@app.command()
def health() -> None:
    _echo(_service(Settings()).health())


@app.command()
def sources() -> None:
    """List configured sources with ledger size and last run."""
    settings = Settings()
    cfg = load_sources(settings.sources_file)
    with db.connect(settings.database_url) as conn:
        ledger = Ledger(conn)
        sizes = ledger.ledger_size()
        runs = {r["source_id"]: r for r in reversed(ledger.recent_runs(200))}
    out = []
    for src in cfg.sources:
        last = runs.get(src.id)
        out.append(
            {
                "id": src.id,
                "connector": src.connector,
                "format": src.format,
                "actual_account": src.actual_account,
                "ledger_rows": sizes.get(src.id, 0),
                "last_run": (
                    last["started_at"].astimezone().isoformat(timespec="minutes"),
                    last["status"],
                )
                if last
                else None,
            }
        )
    orphans = sorted(set(sizes) - {s.id for s in cfg.sources})
    _echo({"sources": out, "ledger_only_sources": orphans})


@app.command("forget-source")
def forget_source(
    source_id: str,
    yes: Annotated[bool, typer.Option("--yes", help="Do not ask for confirmation")] = False,
) -> None:
    """Delete a removed source's ledger and run history. Transactions in Actual are kept."""
    settings = Settings()
    if not yes:
        typer.confirm(
            f"Delete ledger and run history of '{source_id}'? Actual is not touched.", abort=True
        )
    with db.connect(settings.database_url) as conn:
        n = Ledger(conn).forget_source(source_id)
        conn.commit()
    _echo({"source_id": source_id, "deleted_ledger_rows": n})


@app.command("inspect-csv")
def inspect_csv(
    path: Path,
    fmt: Annotated[str, typer.Option("--format")] = "postbank-csv",
    rows: int = 3,
) -> None:
    """Check that a statement file parses; prints columns and a few masked rows."""
    doc = parse_csv(path, get_profile(fmt))
    typer.echo(f"encoding: {doc.encoding}")
    typer.echo(f"header ({len(doc.header)} columns): {doc.header}")
    typer.echo(
        f"rows: {len(doc.rows)}  pending: {sum(r.pending for r in doc.rows)}  skipped lines: {doc.skipped_rows}"
    )
    typer.echo(
        f"statement balance: {'found' if doc.balance_cents is not None else 'not found'}"
        + (
            f" ({doc.balance_currency}, as of {doc.balance_date or 'export time'})"
            if doc.balance_cents is not None
            else ""
        )
    )
    for r in doc.rows[:rows]:
        payee = (r.counterpart_name or r.posting_text or "")[:12]
        typer.echo(
            f"  {r.booking_date} {r.amount_cents:>9d} {r.currency} payee={payee!r:<15} "
            f"iban={mask_iban(r.counterpart_iban)} purpose_len={len(r.purpose)} ref={'y' if r.end_to_end_id else 'n'}"
        )


@app.command("actual-setup")
def actual_setup(
    account: Annotated[
        list[str] | None,
        typer.Option("--account", help="On-budget account(s) to create if missing"),
    ] = None,
    off_budget_account: Annotated[list[str] | None, typer.Option("--off-budget-account")] = None,
) -> None:
    """Bootstrap the Actual server password (first start), create the budget file and accounts if missing."""
    from actual import Actual
    from actual.queries import create_account, get_account

    st = Settings()
    st.actual_data_dir.mkdir(parents=True, exist_ok=True)
    with Actual(
        base_url=st.actual_url,
        password=st.actual_password,
        bootstrap=True,
        data_dir=st.actual_data_dir,
    ) as a:
        files = a.list_user_files().data
        names = [f.name for f in files if not f.deleted]
        if st.actual_file in names:
            a.set_file(st.actual_file)
            a.download_budget(st.actual_encryption_password)
            typer.echo(f"budget '{st.actual_file}' exists")
        else:
            a.create_budget(st.actual_file)
            a.upload_budget()
            typer.echo(f"created budget '{st.actual_file}'")
        created = []
        wanted = [(n, False) for n in account or []] + [(n, True) for n in off_budget_account or []]
        for name, off in wanted:
            if get_account(a.session, name) is None:
                create_account(a.session, name, 0, off_budget=off)
                created.append(name)
        if created:
            a.commit()
        typer.echo(f"accounts created: {created or 'none'}")


if __name__ == "__main__":
    app()

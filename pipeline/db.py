# SPDX-License-Identifier: Apache-2.0
"""Postgres connection and plain-SQL migrations. The pipeline owns this schema."""

from __future__ import annotations

import logging
from pathlib import Path

import psycopg

log = logging.getLogger(__name__)


def connect(database_url: str) -> psycopg.Connection:
    return psycopg.connect(database_url)


def migrate(database_url: str, migrations_dir: Path) -> list[str]:
    """Apply *.sql files in name order that are not yet recorded. Returns applied ids."""
    applied: list[str] = []
    with connect(database_url) as conn:
        conn.execute(
            "create table if not exists schema_migrations "
            "(id text primary key, applied_at timestamptz not null default now())"
        )
        done = {r[0] for r in conn.execute("select id from schema_migrations")}
        for path in sorted(migrations_dir.glob("*.sql")):
            if path.name in done:
                continue
            log.info("applying migration %s", path.name)
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("insert into schema_migrations (id) values (%s)", (path.name,))
            applied.append(path.name)
        conn.commit()
    return applied

# SPDX-License-Identifier: Apache-2.0
"""Manual import: statements dropped into a local inbox folder.

Works without any bank API or registration. Each file is one document: keys
are assigned per file (ordinal within the file) and documents are merged by
source_key, so re-exporting an overlapping window is harmless.
"""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

from pipeline.config import SourceConfig
from pipeline.dto import FetchResult
from pipeline.formats import FormatError, get_profile, parse_csv
from pipeline.keys import assign_keys, merge_documents

log = logging.getLogger(__name__)

PROCESSED_DIR = "processed"


class ManualImportConnector:
    id = "manual_import"

    def __init__(self, inbox_dir: Path):
        self.inbox_dir = inbox_dir

    def _files(self, source: SourceConfig) -> list[Path]:
        if not source.inbox_glob:
            raise FormatError(f"source '{source.id}': inbox_glob is required for manual_import")
        files = [
            p
            for p in sorted(self.inbox_dir.glob(source.inbox_glob))
            if p.is_file() and PROCESSED_DIR not in p.relative_to(self.inbox_dir).parts
        ]
        return files

    def fetch_transactions(self, source: SourceConfig) -> FetchResult:
        if not source.format:
            raise FormatError(f"source '{source.id}': format is required for manual_import")
        profile = get_profile(source.format, source.format_overrides)
        result = FetchResult()
        documents = []
        newest: tuple[date, int, date | None] | None = (
            None  # (max booking date, balance, balance date)
        )
        for path in self._files(source):
            doc = parse_csv(path, profile)
            booked = [r for r in doc.rows if not r.pending]
            result.pending_rows += len(doc.rows) - len(booked)
            documents.append(
                assign_keys(booked, source_id=source.id, account_ref=source.account_ref)
            )
            result.files.append(path.name)
            if doc.balance_cents is not None and booked:
                latest = max(r.booking_date for r in booked)
                mtime = path.stat().st_mtime
                if newest is None or (latest, mtime) > (newest[0], newest[1]):
                    newest = (latest, mtime, doc.balance_cents, doc.balance_date)
            log.info(
                "parsed %s: %d rows (%d pending, %d skipped lines, %s)",
                path.name,
                len(doc.rows),
                len(doc.rows) - len(booked),
                doc.skipped_rows,
                doc.encoding,
            )
        result.rows = merge_documents(documents)
        if newest is not None:
            # A balance line without its own date is the balance at export time, not at the
            # last booking: use the file's modification time as the best available export date.
            result.statement_balance_cents = newest[2]
            result.statement_balance_date = (
                newest[3] or datetime.fromtimestamp(newest[1], tz=UTC).astimezone().date()
            )
        if result.rows:
            result.window_from = min(r.booking_date for r in result.rows)
            result.window_to = max(r.booking_date for r in result.rows)
        return result

    def mark_imported(self, source: SourceConfig, result: FetchResult) -> None:
        for path in self._files(source):
            if path.name in result.files:
                target_dir = path.parent / PROCESSED_DIR
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / path.name
                if target.exists():
                    target = target_dir / f"{path.stem}.{path.stat().st_mtime_ns}{path.suffix}"
                shutil.move(str(path), str(target))
                log.info("moved %s to %s/", path.name, PROCESSED_DIR)

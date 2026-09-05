# SPDX-License-Identifier: Apache-2.0
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

from pipeline.config import SourceConfig
from pipeline.connectors.manual_import import ManualImportConnector

FIXTURE = Path(__file__).parent / "fixtures" / "postbank_sample.csv"


def _mtime_date(path: Path) -> date:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).astimezone().date()


def _source() -> SourceConfig:
    return SourceConfig(
        id="pb",
        connector="manual_import",
        actual_account="Giro",
        account_ref="giro",
        format="postbank-csv",
        inbox_glob="pb/*.csv",
    )


def test_fetch_merges_files_and_carries_statement_balance(tmp_path: Path):
    inbox = tmp_path / "inbox"
    (inbox / "pb").mkdir(parents=True)
    shutil.copy(FIXTURE, inbox / "pb" / "a.csv")
    shutil.copy(FIXTURE, inbox / "pb" / "b.csv")  # overlapping export
    connector = ManualImportConnector(inbox)
    result = connector.fetch_transactions(_source())
    assert len(result.rows) == 4  # duplicates across files collapse; pending excluded
    assert result.pending_rows == 2
    assert result.files == ["a.csv", "b.csv"]
    assert result.window_from == date(2026, 1, 2) and result.window_to == date(2026, 1, 15)
    assert result.statement_balance_cents == 363856
    assert result.statement_balance_date == _mtime_date(
        inbox / "pb" / "a.csv"
    )  # no date in the file -> export (file) date
    connector.mark_imported(_source(), result)
    assert sorted(p.name for p in (inbox / "pb" / "processed").iterdir()) == ["a.csv", "b.csv"]
    assert connector.fetch_transactions(_source()).rows == []

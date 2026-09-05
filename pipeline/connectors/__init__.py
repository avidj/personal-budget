# SPDX-License-Identifier: Apache-2.0
"""Connector protocol and registry.

A connector turns one configured source into normalised DTOs. Transport
(FinTS, files in an inbox, an HTTP price feed) is a property of the connector;
the pipeline only sees DTOs. Deliberately no payment operations.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pipeline.config import SourceConfig
from pipeline.dto import FetchResult


class Connector(Protocol):
    id: str

    def fetch_transactions(self, source: SourceConfig) -> FetchResult: ...

    def mark_imported(self, source: SourceConfig, result: FetchResult) -> None:
        """Hook after a successful import (e.g. move processed files)."""
        ...


def build_registry(inbox_dir: Path) -> dict[str, Connector]:
    from pipeline.connectors.manual_import import ManualImportConnector

    connectors: list[Connector] = [ManualImportConnector(inbox_dir)]
    return {c.id: c for c in connectors}

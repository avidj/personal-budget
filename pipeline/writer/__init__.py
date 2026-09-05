# SPDX-License-Identifier: Apache-2.0
"""BudgetWriter port: the only way the pipeline talks to Actual.

The default adapter uses actualpy. A Node adapter wrapping @actual-app/api can
replace it behind this interface if version drift ever requires it (ADR-0004).
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import date
from typing import Any, Protocol

from pipeline.categorization import Candidate, Example
from pipeline.dto import AccountInfo, ImportResult, StartingBalanceResult, TransactionDTO


class BudgetSession(Protocol):
    def list_accounts(self) -> list[AccountInfo]: ...

    def resolve_account(self, name: str) -> AccountInfo: ...

    def set_starting_balance(
        self,
        account_name: str,
        *,
        target_balance_cents: int,
        dated: date,
        imported_id: str,
        dry_run: bool,
    ) -> StartingBalanceResult: ...

    def import_transactions(
        self, account_name: str, rows: list[TransactionDTO], *, dry_run: bool
    ) -> ImportResult: ...

    # --- categorisation assistant ---
    def categories(self) -> list[dict[str, str]]: ...

    def uncategorized_candidates(
        self, *, limit: int, exclude_payees: set[str]
    ) -> list[Candidate]: ...

    def categorization_examples(self, *, limit: int) -> list[Example]: ...

    def apply_rules(self, *, uncategorized_only: bool) -> dict[str, int]: ...

    def create_category_rule(
        self, payee: str, category: str, *, apply_to_uncategorized: bool
    ) -> dict[str, Any]: ...


class BudgetWriter(Protocol):
    def session(self) -> AbstractContextManager[BudgetSession]: ...

    def ping(self) -> bool: ...

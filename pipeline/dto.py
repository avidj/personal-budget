# SPDX-License-Identifier: Apache-2.0
"""Normalised data transfer objects. Money is in integer minor units (cents)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class RawTransaction:
    """A bank row after parsing but before key assignment. Field names are canonical."""

    booking_date: date
    amount_cents: int
    currency: str
    value_date: date | None = None
    counterpart_name: str | None = None
    counterpart_iban: str | None = None
    purpose: str = ""
    posting_text: str | None = None
    bank_reference: str | None = None
    end_to_end_id: str | None = None
    pending: bool = False


@dataclass(frozen=True)
class TransactionDTO:
    """A normalised transaction with its stable identity."""

    source_id: str
    account_ref: str
    booking_date: date
    amount_cents: int
    currency: str
    source_key: str
    imported_id: str
    raw_fingerprint: str
    value_date: date | None = None
    counterpart_name: str | None = None
    counterpart_iban: str | None = None
    purpose: str = ""
    posting_text: str | None = None
    bank_reference: str | None = None
    end_to_end_id: str | None = None
    pending: bool = False

    @property
    def payee_name(self) -> str:
        """Payee as Actual should see it: counterpart, else the posting text, else a constant."""
        return (self.counterpart_name or self.posting_text or "Unknown payee").strip()


@dataclass
class FetchResult:
    rows: list[TransactionDTO] = field(default_factory=list)
    files: list[str] = field(default_factory=list)  # basenames only (no paths in logs/API)
    pending_rows: int = 0
    window_from: date | None = None
    window_to: date | None = None
    notes: list[str] = field(default_factory=list)
    statement_balance_cents: int | None = (
        None  # bank's own balance as stated in the newest document
    )
    statement_balance_date: date | None = None  # None = as of export time


@dataclass
class ImportResult:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class StartingBalanceResult:
    action: str  # 'created' | 'updated' | 'unchanged'
    dated: date
    previous_amount_cents: int | None
    amount_cents: int
    account_balance_after_cents: int
    dry_run: bool


@dataclass(frozen=True)
class AccountInfo:
    id: str
    name: str
    off_budget: bool
    balance_cents: int

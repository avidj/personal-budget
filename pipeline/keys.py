# SPDX-License-Identifier: Apache-2.0
"""Stable per-row identity: source_key, imported_id and change fingerprint.

The key must be reproducible from the same bank row on every fetch, regardless
of fetch window or transport (CSV export, MT940, camt). See the architecture
document, section 4.3, for the rationale of each field.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable

from pipeline.dto import RawTransaction, TransactionDTO

KEY_VERSION = "v1"
_WS = re.compile(r"\s+")
_SEP = "\x1f"


def norm(value: str | None) -> str:
    """Canonical text form: NFKC, upper-case, whitespace collapsed, trimmed."""
    if not value:
        return ""
    return _WS.sub(" ", unicodedata.normalize("NFKC", value)).strip().upper()


def canonical_fields(row: RawTransaction, account_ref: str) -> list[str]:
    reference = row.end_to_end_id or row.bank_reference or ""
    return [
        KEY_VERSION,
        account_ref,
        row.booking_date.isoformat(),
        str(row.amount_cents),
        row.currency.upper(),
        norm(reference),
        norm(row.counterpart_iban),
        norm(row.counterpart_name),
        norm(row.purpose),
        norm(row.posting_text),
    ]


def _digest(parts: Iterable[str]) -> str:
    return hashlib.sha256(_SEP.join(parts).encode("utf-8")).hexdigest()


def fingerprint(row: RawTransaction) -> str:
    """Detects changed representations of the same row (e.g. re-formatted purpose)."""
    parts = [
        row.booking_date.isoformat(),
        row.value_date.isoformat() if row.value_date else "",
        str(row.amount_cents),
        row.currency,
        row.counterpart_name or "",
        row.counterpart_iban or "",
        row.purpose,
        row.posting_text or "",
        row.bank_reference or "",
        row.end_to_end_id or "",
    ]
    return _digest(parts)


def imported_id(source_id: str, source_key: str) -> str:
    return f"{source_id}:{source_key[:32]}"


def assign_keys(
    rows: list[RawTransaction], *, source_id: str, account_ref: str
) -> list[TransactionDTO]:
    """Assign source_key/imported_id to rows of ONE fetched document.

    Rows whose canonical fields are identical on the same booking day are
    disambiguated by their ordinal in document order. Call this per document
    (per CSV file, per statement), never on a concatenation of overlapping
    documents, otherwise duplicates across documents would receive distinct
    ordinals.
    """
    seen: dict[tuple[str, ...], int] = defaultdict(int)
    out: list[TransactionDTO] = []
    for row in rows:
        canon = canonical_fields(row, account_ref)
        group = tuple(canon)
        ordinal = seen[group]
        seen[group] += 1
        key = _digest([*canon, str(ordinal)])
        out.append(
            TransactionDTO(
                source_id=source_id,
                account_ref=account_ref,
                booking_date=row.booking_date,
                amount_cents=row.amount_cents,
                currency=row.currency.upper(),
                source_key=key,
                imported_id=imported_id(source_id, key),
                raw_fingerprint=fingerprint(row),
                value_date=row.value_date,
                counterpart_name=row.counterpart_name,
                counterpart_iban=row.counterpart_iban,
                purpose=row.purpose,
                posting_text=row.posting_text,
                bank_reference=row.bank_reference,
                end_to_end_id=row.end_to_end_id,
                pending=row.pending,
            )
        )
    return out


def merge_documents(batches: Iterable[list[TransactionDTO]]) -> list[TransactionDTO]:
    """Union of several documents' rows; identical source_keys collapse to one row."""
    by_key: dict[str, TransactionDTO] = {}
    for rows in batches:
        for row in rows:
            by_key.setdefault(row.source_key, row)
    return sorted(by_key.values(), key=lambda r: (r.booking_date, r.source_key))

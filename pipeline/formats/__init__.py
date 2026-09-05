# SPDX-License-Identifier: Apache-2.0
"""Data-driven CSV format profiles for manually exported bank statements.

A profile maps a bank's column headers onto the canonical RawTransaction
fields. Users can override header names per source in sources.yaml without
touching code (format_overrides), which covers most bank-side renames.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from pipeline.dto import RawTransaction

CANONICAL = (
    "booking_date",
    "value_date",
    "amount",
    "debit",
    "credit",
    "currency",
    "counterpart_name",
    "counterpart_iban",
    "purpose",
    "posting_text",
    "bank_reference",
    "end_to_end_id",
)

_PENDING_MARKERS = ("VORGEMERKT", "VORMERKUNG", "PENDING")


@dataclass(frozen=True)
class CsvProfile:
    id: str
    columns: dict[str, str]  # canonical field -> header text
    delimiter: str = ";"
    encodings: tuple[str, ...] = ("utf-8-sig", "cp1252")
    header_marker: str = "booking_date"  # canonical column whose header identifies the header row
    date_formats: tuple[str, ...] = ("%d.%m.%Y", "%Y-%m-%d")
    decimal_comma: bool = True
    default_currency: str = "EUR"
    balance_label: str | None = (
        None  # metadata row label (substring, case-insensitive) holding the balance
    )

    def with_overrides(self, overrides: dict[str, str]) -> CsvProfile:
        unknown = set(overrides) - set(CANONICAL)
        if unknown:
            raise ValueError(f"unknown canonical fields in format_overrides: {sorted(unknown)}")
        return replace(self, columns={**self.columns, **overrides})


@dataclass
class ParsedDocument:
    rows: list[RawTransaction] = field(default_factory=list)
    header: list[str] = field(default_factory=list)
    skipped_rows: int = 0
    encoding: str = ""
    # balance stated in the file's metadata block (e.g. Postbank "Letzter Kontostand")
    balance_cents: int | None = None
    balance_currency: str | None = None
    balance_date: date | None = None  # None = "as of export"


class FormatError(ValueError):
    pass


def parse_amount(text: str | None, *, decimal_comma: bool) -> int | None:
    """'1.234,56' -> 123456; '-12,30' -> -1230; '' -> None."""
    if text is None:
        return None
    s = text.strip().replace(" ", "").replace(" ", "")
    s = re.sub(r"[A-Za-z€$£]", "", s)
    if not s:
        return None
    if decimal_comma:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        value = Decimal(s)
    except InvalidOperation as exc:
        raise FormatError(f"cannot parse amount {text!r}") from exc
    return int((value * 100).to_integral_value())


def parse_date(text: str | None, formats: tuple[str, ...]) -> date | None:
    if not text:
        return None
    s = text.strip()
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).date()  # noqa: DTZ007 - dates only
        except ValueError:
            continue
    return None


def _read_text(path: Path, encodings: tuple[str, ...]) -> tuple[str, str]:
    raw = path.read_bytes()
    for enc in encodings:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise FormatError(f"{path.name}: none of the encodings {encodings} decode the file")


def parse_csv(path: Path, profile: CsvProfile) -> ParsedDocument:
    text, encoding = _read_text(path, profile.encodings)
    reader = csv.reader(io.StringIO(text), delimiter=profile.delimiter)
    marker = profile.columns[profile.header_marker]
    doc = ParsedDocument(encoding=encoding)
    header_index: dict[str, int] | None = None
    for cells in reader:
        stripped = [c.strip() for c in cells]
        if header_index is None:
            if marker in stripped:
                doc.header = stripped
                header_index = {h: i for i, h in enumerate(stripped)}
            elif (
                profile.balance_label
                and stripped
                and profile.balance_label.lower() in stripped[0].lower()
            ):
                _parse_balance_row(stripped, profile, doc)
            continue
        row = _parse_row(stripped, header_index, profile)
        if row is None:
            doc.skipped_rows += 1
        else:
            doc.rows.append(row)
    if header_index is None:
        raise FormatError(f"{path.name}: header row with column {marker!r} not found")
    return doc


_AMOUNT_CELL = re.compile(r"^[-+]?[\d.]*\d,\d{2}( ?[A-Z]{3})?$|^[-+]?\d[\d,]*\.\d{2}( ?[A-Z]{3})?$")


def _parse_balance_row(cells: list[str], profile: CsvProfile, doc: ParsedDocument) -> None:
    """Metadata row like 'Letzter Kontostand;;;;1.234,56;EUR' or 'Kontostand am 05.09.2026;...'."""
    for cell in cells[1:]:
        if cell and _AMOUNT_CELL.match(cell):
            doc.balance_cents = parse_amount(cell, decimal_comma=profile.decimal_comma)
            m = re.search(r"[A-Z]{3}$", cell)
            doc.balance_currency = m.group(0) if m else None
            break
    for cell in cells:
        if len(cell) == 3 and cell.isalpha() and cell.isupper():
            doc.balance_currency = cell
        d = parse_date(cell, profile.date_formats)
        if d is None and cell:
            found = re.search(r"\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2}", cell)
            d = parse_date(found.group(0), profile.date_formats) if found else None
        if d is not None:
            doc.balance_date = d
    if doc.balance_currency is None and doc.balance_cents is not None:
        doc.balance_currency = profile.default_currency


def _get(
    cells: list[str], index: dict[str, int], profile: CsvProfile, canonical: str
) -> str | None:
    header = profile.columns.get(canonical)
    if header is None or header not in index:
        return None
    i = index[header]
    return cells[i] if i < len(cells) else None


def _parse_row(
    cells: list[str], index: dict[str, int], profile: CsvProfile
) -> RawTransaction | None:
    booking = parse_date(_get(cells, index, profile, "booking_date"), profile.date_formats)
    if booking is None:
        return None  # metadata, footer or blank line
    credit = parse_amount(
        _get(cells, index, profile, "credit"), decimal_comma=profile.decimal_comma
    )
    debit = parse_amount(_get(cells, index, profile, "debit"), decimal_comma=profile.decimal_comma)
    if credit is not None and credit != 0:
        amount = abs(credit)
    elif debit is not None and debit != 0:
        amount = -abs(debit)
    else:
        signed = parse_amount(
            _get(cells, index, profile, "amount"), decimal_comma=profile.decimal_comma
        )
        if signed is None:
            return None
        amount = signed
    posting_text = (_get(cells, index, profile, "posting_text") or "").strip() or None
    pending = bool(posting_text) and any(m in posting_text.upper() for m in _PENDING_MARKERS)
    currency = (_get(cells, index, profile, "currency") or profile.default_currency).strip().upper()
    return RawTransaction(
        booking_date=booking,
        value_date=parse_date(_get(cells, index, profile, "value_date"), profile.date_formats),
        amount_cents=amount,
        currency=currency or profile.default_currency,
        counterpart_name=(_get(cells, index, profile, "counterpart_name") or "").strip() or None,
        counterpart_iban=(_get(cells, index, profile, "counterpart_iban") or "").replace(" ", "")
        or None,
        purpose=(_get(cells, index, profile, "purpose") or "").strip(),
        posting_text=posting_text,
        bank_reference=(_get(cells, index, profile, "bank_reference") or "").strip() or None,
        end_to_end_id=(_get(cells, index, profile, "end_to_end_id") or "").strip() or None,
        pending=pending,
    )


# --- profiles -------------------------------------------------------------

POSTBANK_CSV = CsvProfile(
    id="postbank-csv",
    # Postbank online banking (Deutsche Bank platform, 2023+): UTF-8 BOM, ';',
    # seven metadata lines, 18 columns, dates like 2.1.2026, Soll/Haben columns.
    columns={
        "booking_date": "Buchungstag",
        "value_date": "Wert",
        "posting_text": "Umsatzart",
        "counterpart_name": "Begünstigter / Auftraggeber",
        "purpose": "Verwendungszweck",
        "counterpart_iban": "IBAN / Kontonummer",
        "end_to_end_id": "Kundenreferenz",
        "amount": "Betrag",
        "debit": "Soll",
        "credit": "Haben",
        "currency": "Währung",
    },
    balance_label="Kontostand",
)

GENERIC_CSV = CsvProfile(
    id="generic-csv",
    # Template for hand-made statements (loans, cash). English headers, ISO dates.
    columns={
        "booking_date": "booking_date",
        "value_date": "value_date",
        "amount": "amount",
        "currency": "currency",
        "counterpart_name": "counterpart",
        "counterpart_iban": "counterpart_iban",
        "purpose": "purpose",
        "posting_text": "type",
        "bank_reference": "reference",
    },
    date_formats=("%Y-%m-%d", "%d.%m.%Y"),
    decimal_comma=False,
    encodings=("utf-8-sig",),
    balance_label="balance",
)

PROFILES: dict[str, CsvProfile] = {p.id: p for p in (POSTBANK_CSV, GENERIC_CSV)}


def get_profile(format_id: str, overrides: dict[str, str] | None = None) -> CsvProfile:
    try:
        profile = PROFILES[format_id]
    except KeyError as exc:
        raise FormatError(
            f"unknown format profile '{format_id}'; known: {sorted(PROFILES)}"
        ) from exc
    return profile.with_overrides(overrides or {})

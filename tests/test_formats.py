# SPDX-License-Identifier: Apache-2.0
from datetime import date
from pathlib import Path

import pytest

from pipeline.formats import FormatError, get_profile, parse_amount, parse_csv

FIXTURE = Path(__file__).parent / "fixtures" / "postbank_sample.csv"


def test_parse_amount_german():
    assert parse_amount("1.234,56", decimal_comma=True) == 123456
    assert parse_amount("-89,00", decimal_comma=True) == -8900
    assert parse_amount("", decimal_comma=True) is None
    assert parse_amount("12.30", decimal_comma=False) == 1230
    with pytest.raises(FormatError):
        parse_amount("abc,de", decimal_comma=True)


def test_postbank_sample_parses():
    doc = parse_csv(FIXTURE, get_profile("postbank-csv"))
    assert doc.encoding == "utf-8-sig"
    assert len(doc.header) == 18
    assert len(doc.rows) == 5  # metadata + footer skipped
    assert doc.skipped_rows == 1  # 'Kontostand am ...' footer
    first = doc.rows[0]
    assert first.booking_date == date(2026, 1, 2)
    assert first.amount_cents == -8900
    assert first.counterpart_name == "Muster Energie GmbH"
    assert first.counterpart_iban == "DE00111111111111111111"
    assert first.posting_text == "SEPA-Lastschrift"
    salary = doc.rows[3]
    assert salary.amount_cents == 250000
    assert salary.end_to_end_id == "E2E-2026-01"
    assert doc.rows[4].pending is True


def test_overrides_validate():
    with pytest.raises(ValueError):
        get_profile("postbank-csv", {"not_a_field": "X"})
    p = get_profile("postbank-csv", {"purpose": "Buchungstext"})
    assert p.columns["purpose"] == "Buchungstext"


def test_unknown_profile():
    with pytest.raises(FormatError):
        get_profile("nope")


def test_statement_balance_from_metadata_row():
    doc = parse_csv(FIXTURE, get_profile("postbank-csv"))
    assert doc.balance_cents == 363856
    assert doc.balance_currency == "EUR"
    assert doc.balance_date is None  # "Letzter Kontostand" carries no date -> as of export

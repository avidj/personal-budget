# SPDX-License-Identifier: Apache-2.0
from datetime import date

from pipeline.dto import RawTransaction
from pipeline.keys import assign_keys, merge_documents, norm


def row(**kw) -> RawTransaction:
    base = {
        "booking_date": date(2026, 1, 5),
        "amount_cents": -350,
        "currency": "EUR",
        "purpose": "Kauf 04.01.2026 12:03",
    }
    base.update(kw)
    return RawTransaction(**base)


def test_norm_collapses_case_and_whitespace():
    assert norm("  Muster   Energie\tGmbH ") == "MUSTER ENERGIE GMBH"
    assert norm(None) == ""


def test_identical_rows_get_distinct_keys_by_ordinal():
    rows = assign_keys([row(), row()], source_id="pb", account_ref="giro")
    assert rows[0].source_key != rows[1].source_key
    assert rows[0].imported_id.startswith("pb:") and len(rows[0].imported_id) == 3 + 32


def test_keys_are_stable_across_fetches_and_independent_of_value_date():
    a = assign_keys([row(value_date=date(2026, 1, 5))], source_id="pb", account_ref="giro")[0]
    b = assign_keys([row(value_date=date(2026, 1, 6))], source_id="pb", account_ref="giro")[0]
    assert a.source_key == b.source_key
    assert a.raw_fingerprint != b.raw_fingerprint  # representation changed, identity did not


def test_purpose_whitespace_and_case_do_not_change_key():
    a = assign_keys([row(purpose="Kauf 04.01.2026 12:03")], source_id="pb", account_ref="giro")[0]
    b = assign_keys([row(purpose="KAUF  04.01.2026 12:03 ")], source_id="pb", account_ref="giro")[0]
    assert a.source_key == b.source_key


def test_reference_participates_in_key():
    a = assign_keys([row(end_to_end_id="E2E-1")], source_id="pb", account_ref="giro")[0]
    b = assign_keys([row(end_to_end_id="E2E-2")], source_id="pb", account_ref="giro")[0]
    assert a.source_key != b.source_key


def test_merge_collapses_overlapping_documents():
    doc1 = assign_keys([row(), row(amount_cents=-100)], source_id="pb", account_ref="giro")
    doc2 = assign_keys(
        [row(amount_cents=-100), row(booking_date=date(2026, 1, 9))],
        source_id="pb",
        account_ref="giro",
    )
    merged = merge_documents([doc1, doc2])
    assert len(merged) == 3
    assert merged == sorted(merged, key=lambda r: (r.booking_date, r.source_key))

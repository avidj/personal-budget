# SPDX-License-Identifier: Apache-2.0
from pipeline.categorization import (
    Candidate,
    CategorizationContext,
    Example,
    mask_snippet,
    render_prompt,
    validate_proposals,
)
from pipeline.llm import schema_with_categories


def _ctx() -> CategorizationContext:
    return CategorizationContext(
        candidates=[
            Candidate("REWE SAGT DANKE", 12, -4321, ["KAUF #"]),
            Candidate("Arbeitgeber Beispiel AG", 3, 250000, ["GEHALT #/#"]),
        ],
        categories=[
            {"name": "Food", "group": "Usual Expenses"},
            {"name": "Income", "group": "Income"},
        ],
        examples=[Example("Cafe Beispiel", "Food", "rule")],
    )


def test_mask_snippet_hides_numbers_and_trims():
    assert (
        mask_snippet("Kauf 04.01.2026 12:03 Kd-Nr 1234567 DE00111122223333")
        == "Kauf 04.01.# 12:03 Kd-Nr # DE#"
    )
    assert len(mask_snippet("x" * 200)) == 60


def test_prompt_contains_categories_examples_and_candidates():
    text = render_prompt(_ctx())
    assert "- [c1] Food — Usual Expenses" in text
    assert "Cafe Beispiel → Food  (rule)" in text
    assert "[p1] REWE SAGT DANKE: 12 transactions, avg 43 EUR expense" in text
    assert "[p2] Arbeitgeber Beispiel AG: 3 transactions, avg 2500 EUR income" in text


def test_schema_enum_constrains_categories():
    schema = schema_with_categories(["Food", "Income"])
    assert schema["properties"]["proposals"]["items"]["properties"]["category"]["enum"] == [
        "Food",
        "Income",
    ]


def test_validate_drops_unknown_payees_and_categories_and_clamps():
    raw = {
        "proposals": [
            {
                "payee": "REWE SAGT DANKE",
                "category": "food",
                "confidence": 1.7,
                "reason": "groceries",
            },
            {"payee": "REWE SAGT DANKE", "category": "Food", "confidence": 0.5},  # duplicate payee
            {"payee": "Unknown Shop", "category": "Food", "confidence": 0.9},
            {
                "payee": "arbeitgeber beispiel ag",
                "category": "INCOME",
                "confidence": 0.8,
            },  # loose match
            {
                "payee": "Arbeitgeber Beispiel AG",
                "category": "Salary",
                "confidence": 0.9,
            },  # unknown category
        ]
    }
    out = validate_proposals(raw, _ctx(), "test-model")
    assert [p["payee"] for p in out] == ["REWE SAGT DANKE", "Arbeitgeber Beispiel AG"]
    assert out[1]["category"] == "Income"
    assert out[0]["category"] == "Food" and out[0]["confidence"] == 1.0 and out[0]["tx_count"] == 12

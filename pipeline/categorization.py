# SPDX-License-Identifier: Apache-2.0
"""Categorisation assistant: candidates -> proposals -> human approval -> Actual rules.

Actual stays the source of truth for categories and rules. The pipeline only
keeps the review queue (category_proposal) and renders the prompt so that any
LLM (the n8n AI node or the built-in Ollama client) works from the same text.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import psycopg

from pipeline.llm import schema_with_categories

_DIGITS = re.compile(r"\d{3,}")
_WS = re.compile(r"\s+")
SNIPPET_LEN = 60
MAX_SNIPPETS = 2


@dataclass
class Candidate:
    payee: str
    tx_count: int
    avg_amount_cents: int
    snippets: list[str] = field(default_factory=list)
    ref: str = ""  # short id used in the prompt, e.g. "p3"; assigned by CategorizationContext


@dataclass
class Example:
    payee: str
    category: str
    source: str  # 'rule' | 'history'


@dataclass
class CategorizationContext:
    candidates: list[Candidate]
    categories: list[dict[str, str]]  # {name, group}
    examples: list[Example]
    model_hint: str = ""

    def __post_init__(self) -> None:
        self.assign_refs()

    def assign_refs(self) -> None:
        for i, c in enumerate(self.candidates, start=1):
            if not c.ref:
                c.ref = f"p{i}"
        for i, cat in enumerate(self.categories, start=1):
            cat.setdefault("ref", f"c{i}")

    def category_by_ref(self) -> dict[str, str]:
        return {c["ref"]: c["name"] for c in self.categories if "ref" in c}

    def category_names(self) -> list[str]:
        return [c["name"] for c in self.categories]

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [asdict(c) for c in self.candidates],
            "categories": self.categories,
            "examples": [asdict(e) for e in self.examples],
            "system_prompt": SYSTEM_PROMPT,
            "prompt": render_prompt(self),
            "schema": schema_with_categories(self.category_names(), list(self.category_by_ref())),
        }


def mask_snippet(text: str) -> str:
    """Purpose text for the prompt: digits blanked (customer/IBAN/card numbers), trimmed."""
    s = _DIGITS.sub("#", text or "")
    s = _WS.sub(" ", s).strip()
    return s[:SNIPPET_LEN]


SYSTEM_PROMPT = (
    "You categorise bank transactions of a German household for its budget. "
    "You are given the budget's categories, examples of how the household already categorised "
    "payees, and a list of payees that still lack a category. Propose exactly one category per "
    "payee, chosen only from the given categories. Give a confidence between 0 and 1: use 0.9 or more "
    "only when the payee obviously belongs to that category, 0.5 or less when you are guessing. "
    "Answer with the reference ids: each payee's id (like p3) in 'ref' and the chosen category's id "
    "(like c7) in 'category_ref'; you may repeat the category name in 'category'. "
    "Keep reasons to one short sentence. Answer with JSON only."
)


def render_prompt(ctx: CategorizationContext) -> str:
    lines = ["## Categories ([ref] name — group)"]
    for c in ctx.categories:
        lines.append(f"- [{c.get('ref', '')}] {c['name']} — {c['group']}")
    if ctx.examples:
        lines += ["", "## How this household categorises (payee → category)"]
        for e in ctx.examples:
            lines.append(f"- {e.payee} → {e.category}  ({e.source})")
    lines += ["", "## Payees without a category"]
    for c in ctx.candidates:
        direction = "income" if c.avg_amount_cents > 0 else "expense"
        amount = abs(c.avg_amount_cents) / 100
        snippets = "; ".join(f'"{s}"' for s in c.snippets) or "(no purpose text)"
        lines.append(
            f"- [{c.ref}] {c.payee}: {c.tx_count} transactions, avg {amount:.0f} EUR {direction}, purpose: {snippets}"
        )
    lines += [
        "",
        "Return one proposal per payee listed above: its ref (p1, p2, ...) and a category_ref (c1, c2, ...).",
    ]
    return "\n".join(lines)


def _loose(text: str) -> str:
    """Comparison key tolerant to the model's small edits: case, spacing, punctuation."""
    return re.sub(r"[^a-z0-9äöüß]+", " ", text.casefold()).strip()


def _resolve(p: dict[str, Any], ctx: CategorizationContext) -> tuple[Candidate | None, str | None]:
    """Map one raw proposal to (candidate, category name) by reference ids first, then loose names."""
    by_ref = {c.ref: c for c in ctx.candidates if c.ref}
    by_payee = {_loose(c.payee): c for c in ctx.candidates}
    cat_by_ref = ctx.category_by_ref()
    cat_by_name = {_loose(c): c for c in ctx.category_names()}
    cand = by_ref.get(str(p.get("ref", "")).strip().lower()) or by_payee.get(
        _loose(str(p.get("payee", "")))
    )
    category = cat_by_ref.get(str(p.get("category_ref", "")).strip().lower()) or cat_by_name.get(
        _loose(str(p.get("category", "")))
    )
    return cand, category


def invalid_reasons(raw: dict[str, Any], ctx: CategorizationContext) -> dict[str, int]:
    """Why proposals were dropped (for logs and API responses; no payee names)."""
    reasons: dict[str, int] = {}
    for p in raw.get("proposals", []):
        cand, category = _resolve(p, ctx)
        if cand is None:
            reasons["unknown_payee"] = reasons.get("unknown_payee", 0) + 1
        elif category is None:
            reasons["unknown_category"] = reasons.get("unknown_category", 0) + 1
    return reasons


def validate_proposals(
    raw: dict[str, Any], ctx: CategorizationContext, model: str
) -> list[dict[str, Any]]:
    """Keep only proposals for known candidates and existing categories; clamp confidence."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for p in raw.get("proposals", []):
        cand, category = _resolve(p, ctx)
        if cand is None or category is None or cand.payee in seen:
            continue
        seen.add(cand.payee)
        try:
            confidence = max(0.0, min(1.0, float(p.get("confidence", 0))))
        except (TypeError, ValueError):
            confidence = 0.0
        out.append(
            {
                "payee": cand.payee,
                "category": category,
                "confidence": round(confidence, 3),
                "reason": str(p.get("reason", ""))[:300],
                "tx_count": cand.tx_count,
                "avg_amount_cents": cand.avg_amount_cents,
                "snippets": cand.snippets,
                "model": model,
            }
        )
    return out


class ProposalStore:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def excluded_payees(self) -> set[str]:
        """Payees with a pending or rejected proposal are not offered again."""
        return {
            r[0]
            for r in self.conn.execute(
                "select payee from category_proposal where status in ('pending', 'rejected')"
            )
        }

    def store(self, proposals: list[dict[str, Any]]) -> int:
        n = 0
        for p in proposals:
            self.conn.execute(
                """
                insert into category_proposal
                  (id, payee, category, confidence, reason, tx_count, avg_amount_cents, snippets, model, status)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                on conflict (payee) where status = 'pending' do update set
                  category = excluded.category, confidence = excluded.confidence, reason = excluded.reason,
                  tx_count = excluded.tx_count, avg_amount_cents = excluded.avg_amount_cents,
                  snippets = excluded.snippets, model = excluded.model, created_at = now()
                """,
                (
                    uuid.uuid4(),
                    p["payee"],
                    p["category"],
                    p["confidence"],
                    p.get("reason"),
                    p.get("tx_count"),
                    p.get("avg_amount_cents"),
                    json.dumps(p.get("snippets", [])),
                    p.get("model"),
                ),
            )
            n += 1
        return n

    def list(self, status: str | None = "pending", limit: int = 200) -> list[dict[str, Any]]:
        where = "where status = %s" if status else ""
        params: tuple = (status, limit) if status else (limit,)
        cur = self.conn.execute(
            f"""
            select id, payee, category, confidence, reason, tx_count, avg_amount_cents, snippets, model, status,
                   created_at, decided_at, rule_id
            from category_proposal {where}
            order by confidence desc, tx_count desc nulls last, created_at desc limit %s
            """,
            params,
        )
        cols = [d.name for d in cur.description]
        rows = []
        for r in cur.fetchall():
            d = dict(zip(cols, r, strict=True))
            d["id"] = str(d["id"])
            d["short_id"] = d["id"][:8]
            d["confidence"] = float(d["confidence"])
            for k in ("created_at", "decided_at"):
                if d[k] is not None:
                    d[k] = d[k].astimezone().isoformat(timespec="minutes")
            rows.append(d)
        return rows

    def resolve_ids(self, ids: list[str], min_confidence: float | None) -> list[dict[str, Any]]:
        pending = self.list("pending", limit=1000)
        chosen = []
        for p in pending:
            if any(p["id"].startswith(i) for i in ids) or (
                min_confidence is not None and p["confidence"] >= min_confidence
            ):
                chosen.append(p)
        return chosen

    def decide(self, proposal_id: str, status: str, rule_id: str | None = None) -> None:
        self.conn.execute(
            "update category_proposal set status = %s, decided_at = %s, rule_id = %s where id = %s",
            (status, datetime.now(UTC), rule_id, uuid.UUID(proposal_id)),
        )

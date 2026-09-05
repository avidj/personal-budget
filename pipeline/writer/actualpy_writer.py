# SPDX-License-Identifier: Apache-2.0
"""BudgetWriter adapter on top of actualpy.

Matching semantics (deliberately stricter than actualpy's reconcile_transaction):
- exact `imported_id` match -> update fields we own (date, cleared), never merge
  into a transaction that carries a *different* imported_id;
- fuzzy match (same amount, +-7 days, payee preferred) only adopts transactions
  that have NO imported_id yet (manually entered ones), mirroring Actual's own
  import behaviour;
- otherwise create.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
from actual import Actual
from actual.database import Categories, CategoryGroups, Payees, Rules, Transactions
from actual.queries import (
    create_rule,
    create_transaction,
    get_account,
    get_accounts,
    get_or_create_category,
    get_or_create_payee,
    get_ruleset,
    match_transaction,
)
from actual.rules import Action, ActionType, Condition, ConditionType, Rule
from sqlmodel import col, or_, select

from pipeline.categorization import MAX_SNIPPETS, Candidate, Example, mask_snippet
from pipeline.config import Settings
from pipeline.dto import AccountInfo, ImportResult, StartingBalanceResult, TransactionDTO

log = logging.getLogger(__name__)

NOTES_MAX = 500


def _cents_to_decimal(cents: int) -> Decimal:
    return Decimal(cents) / Decimal(100)


def _decimal_to_cents(value: Decimal) -> int:
    return int((value * 100).to_integral_value())


class ActualpySession:
    def __init__(self, actual: Actual):
        self.actual = actual
        self.s = actual.session

    def list_accounts(self) -> list[AccountInfo]:
        return [
            AccountInfo(
                id=a.id,
                name=a.name,
                off_budget=bool(a.offbudget),
                balance_cents=_decimal_to_cents(a.balance),
            )
            for a in get_accounts(self.s, closed=False)
        ]

    def resolve_account(self, name: str) -> AccountInfo:
        acct = get_account(self.s, name)
        if acct is None:
            raise LookupError(f"Actual account '{name}' not found; create it in Actual first")
        return AccountInfo(
            id=acct.id,
            name=acct.name,
            off_budget=bool(acct.offbudget),
            balance_cents=_decimal_to_cents(acct.balance),
        )

    def import_transactions(
        self, account_name: str, rows: list[TransactionDTO], *, dry_run: bool
    ) -> ImportResult:
        acct = get_account(self.s, account_name)
        if acct is None:
            raise LookupError(
                f"Actual account '{account_name}' not found; create it in Actual first"
            )
        result = ImportResult()
        matched: list[Transactions] = []
        created: list[Transactions] = []
        for row in rows:
            amount = _cents_to_decimal(row.amount_cents)
            existing = match_transaction(
                self.s, row.booking_date, acct, row.payee_name, amount, row.imported_id, matched
            )
            if existing is not None and existing.financial_id not in (None, "", row.imported_id):
                existing = None  # belongs to another imported row; never merge
            if existing is None:
                result.added += 1
                if not dry_run:
                    created.append(
                        create_transaction(
                            self.s,
                            row.booking_date,
                            acct,
                            row.payee_name,
                            row.purpose[:NOTES_MAX],
                            None,
                            amount,
                            row.imported_id,
                            True,
                            row.payee_name,
                        )
                    )
                continue
            matched.append(existing)
            if existing.financial_id == row.imported_id:
                changed = existing.get_date() != row.booking_date or not existing.cleared
                if changed and not dry_run:
                    existing.set_date(row.booking_date)
                    existing.cleared = True
                result.updated += int(changed)
                result.unchanged += int(not changed)
            else:
                # manually entered transaction adopted by the import
                result.updated += 1
                if not dry_run:
                    existing.financial_id = row.imported_id
                    existing.cleared = True
                    existing.imported_description = row.payee_name
                    if not existing.payee_id:
                        existing.payee_id = get_or_create_payee(self.s, row.payee_name).id
                    if not existing.notes:
                        existing.notes = row.purpose[:NOTES_MAX]
        if not dry_run:
            if created:
                self.actual.run_rules(created)
            self.actual.commit()
        return result


STARTING_BALANCE_PAYEE = "Starting Balance"
STARTING_BALANCE_CATEGORY = "Starting Balances"
INCOME_GROUP = "Income"


def _set_starting_balance(
    session: ActualpySession,
    account_name: str,
    *,
    target_balance_cents: int,
    dated: date,
    imported_id: str,
    dry_run: bool,
) -> StartingBalanceResult:
    """Create or adjust the one 'Starting Balance' transaction so the account totals target_balance.

    Mirrors what Actual does when an account is created with an initial balance
    (payee 'Starting Balance', income category 'Starting Balances', starting_balance_flag).
    Idempotent through imported_id; never touches other transactions.
    """
    s = session.s
    acct = get_account(s, account_name)
    if acct is None:
        raise LookupError(f"Actual account '{account_name}' not found")
    existing = s.exec(
        select(Transactions).where(
            col(Transactions.acct) == acct.id,
            col(Transactions.financial_id) == imported_id,
            col(Transactions.tombstone) == 0,
        )
    ).first()
    current = _decimal_to_cents(acct.balance)
    previous = existing.amount if existing is not None else None
    without_opening = current - (previous or 0)
    amount = target_balance_cents - without_opening
    if existing is not None and previous == amount and existing.get_date() == dated:
        return StartingBalanceResult("unchanged", dated, previous, amount, current, dry_run)
    action = "updated" if existing is not None else "created"
    if not dry_run:
        if existing is None:
            existing = create_transaction(
                s,
                dated,
                acct,
                STARTING_BALANCE_PAYEE,
                "Opening balance derived from the bank statement",
                None,
                _cents_to_decimal(amount),
                imported_id,
                True,
                STARTING_BALANCE_PAYEE,
            )
            existing.starting_balance_flag = 1
            if not acct.offbudget:
                existing.category_id = get_or_create_category(
                    s, STARTING_BALANCE_CATEGORY, INCOME_GROUP
                ).id
        else:
            existing.amount = amount
            existing.set_date(dated)
        session.actual.commit()
    return StartingBalanceResult(action, dated, previous, amount, without_opening + amount, dry_run)


ActualpySession.set_starting_balance = _set_starting_balance  # type: ignore[method-assign]


# --- categorisation assistant -------------------------------------------------


def _category_maps(s) -> tuple[dict[str, Categories], dict[str, str]]:
    groups = {
        g.id: g for g in s.exec(select(CategoryGroups).where(CategoryGroups.tombstone == 0)).all()
    }
    cats = {c.id: c for c in s.exec(select(Categories).where(Categories.tombstone == 0)).all()}
    group_name = {
        cid: (groups[c.cat_group].name if c.cat_group in groups else "") for cid, c in cats.items()
    }
    return cats, group_name


def _categories(self: ActualpySession) -> list[dict[str, str]]:
    cats, group_name = _category_maps(self.s)
    return sorted(
        (
            {"name": c.name, "group": group_name[cid]}
            for cid, c in cats.items()
            if not getattr(c, "hidden", 0)
        ),
        key=lambda d: (d["group"], d["name"]),
    )


def _uncategorized_query(self: ActualpySession):
    on_budget = [a.id for a in get_accounts(self.s, closed=False, off_budget=False)]
    return (
        select(Transactions, Payees)
        .join(Payees, col(Payees.id) == col(Transactions.payee_id))
        .where(
            col(Transactions.tombstone) == 0,
            col(Transactions.is_parent) == 0,
            col(Transactions.category_id).is_(None),
            col(Transactions.acct).in_(on_budget),
            or_(
                col(Transactions.starting_balance_flag).is_(None),
                col(Transactions.starting_balance_flag) == 0,
            ),
            col(Payees.transfer_acct).is_(None),
        )
    )


def _uncategorized_candidates(
    self: ActualpySession, *, limit: int, exclude_payees: set[str]
) -> list[Candidate]:
    grouped: dict[str, list[Transactions]] = {}
    for t, payee in self.s.exec(_uncategorized_query(self)).all():
        if payee.name in exclude_payees:
            continue
        grouped.setdefault(payee.name, []).append(t)
    out: list[Candidate] = []
    for name, txs in grouped.items():
        snippets: list[str] = []
        for t in txs:
            snip = mask_snippet(t.notes or "")
            if snip and snip not in snippets:
                snippets.append(snip)
            if len(snippets) >= MAX_SNIPPETS:
                break
        out.append(
            Candidate(
                payee=name,
                tx_count=len(txs),
                avg_amount_cents=int(sum(t.amount for t in txs) / len(txs)),
                snippets=snippets,
            )
        )
    out.sort(key=lambda c: (-c.tx_count, c.payee))
    return out[:limit]


def _categorization_examples(self: ActualpySession, *, limit: int) -> list[Example]:
    cats, _ = _category_maps(self.s)
    payees = {p.id: p.name for p in self.s.exec(select(Payees).where(Payees.tombstone == 0)).all()}
    examples: list[Example] = []
    seen: set[str] = set()
    # 1) the household's own rules. Actual's internal name for the payee field is "description",
    #    for the bank's original payee text "imported_description".
    for rule in self.s.exec(select(Rules).where(Rules.tombstone == 0)).all():
        try:
            conditions = json.loads(rule.conditions or "[]")
            actions = json.loads(rule.actions or "[]")
        except json.JSONDecodeError:
            continue
        cat_ids = [
            a.get("value") for a in actions if a.get("field") == "category" and a.get("op") == "set"
        ]
        if not cat_ids or cat_ids[0] not in cats:
            continue
        category = cats[cat_ids[0]].name
        names: list[str] = []
        for c in conditions:
            field, op, value = c.get("field"), c.get("op"), c.get("value")
            if field == "description" and op == "is" and value in payees:
                names.append(payees[value])
            elif field == "description" and op == "oneOf" and isinstance(value, list):
                names.extend(payees[v] for v in value if v in payees)
            elif field == "imported_description" and op == "contains" and isinstance(value, str):
                names.append(f"*{value}*")
        for name in names:
            if name not in seen:
                seen.add(name)
                examples.append(Example(payee=name, category=category, source="rule"))
    # 2) manually categorised history, most frequent pairs first
    counts: dict[tuple[str, str], int] = {}
    rows = self.s.exec(
        select(Transactions.payee_id, Transactions.category_id).where(
            col(Transactions.tombstone) == 0,
            col(Transactions.is_parent) == 0,
            col(Transactions.category_id).is_not(None),
        )
    ).all()
    for pid, cid in rows:
        if pid in payees and cid in cats:
            key = (payees[pid], cats[cid].name)
            counts[key] = counts.get(key, 0) + 1
    for (payee, category), _n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if payee not in seen:
            seen.add(payee)
            examples.append(Example(payee=payee, category=category, source="history"))
        if len(examples) >= limit:
            break
    return examples[:limit]


def _apply_rules(self: ActualpySession, *, uncategorized_only: bool) -> dict[str, int]:
    if uncategorized_only:
        txs = [t for t, _ in self.s.exec(_uncategorized_query(self)).all()]
    else:
        txs = list(
            self.s.exec(
                select(Transactions).where(
                    col(Transactions.tombstone) == 0, col(Transactions.is_parent) == 0
                )
            ).all()
        )
    before = {t.id: t.category_id for t in txs}
    ruleset = get_ruleset(self.s)
    ruleset.run(txs)
    changed = sum(1 for t in txs if t.category_id != before[t.id])
    if changed:
        self.actual.commit()
    return {"considered": len(txs), "categorized": changed, "rules": len(ruleset.rules)}


def _create_category_rule(
    self: ActualpySession, payee: str, category: str, *, apply_to_uncategorized: bool
) -> dict[str, Any]:
    payee_obj = self.s.exec(
        select(Payees).where(col(Payees.name) == payee, col(Payees.tombstone) == 0)
    ).first()
    if payee_obj is None:
        raise LookupError(f"payee '{payee}' not found in Actual")
    cats, _ = _category_maps(self.s)
    cat_obj = next((c for c in cats.values() if c.name == category), None)
    if cat_obj is None:
        raise LookupError(f"category '{category}' not found in Actual")
    rule = Rule(
        conditions=[Condition(field="description", op=ConditionType.IS, value=payee_obj)],
        actions=[Action(field="category", op=ActionType.SET, value=cat_obj)],
    )
    db_rule = create_rule(self.s, rule)
    applied = 0
    if apply_to_uncategorized:
        for t, p in self.s.exec(_uncategorized_query(self)).all():
            if p.id == payee_obj.id:
                t.category_id = cat_obj.id
                applied += 1
    self.actual.commit()
    return {"rule_id": db_rule.id, "applied_to": applied}


ActualpySession.categories = _categories  # type: ignore[method-assign]
ActualpySession.uncategorized_candidates = _uncategorized_candidates  # type: ignore[method-assign]
ActualpySession.categorization_examples = _categorization_examples  # type: ignore[method-assign]
ActualpySession.apply_rules = _apply_rules  # type: ignore[method-assign]
ActualpySession.create_category_rule = _create_category_rule  # type: ignore[method-assign]


class ActualpyWriter:
    def __init__(self, settings: Settings):
        self.settings = settings

    def ping(self) -> bool:
        try:
            r = httpx.get(f"{self.settings.actual_url.rstrip('/')}/info", timeout=5)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    @contextmanager
    def session(self) -> Iterator[ActualpySession]:
        st = self.settings
        st.actual_data_dir.mkdir(parents=True, exist_ok=True)
        with Actual(
            base_url=st.actual_url,
            password=st.actual_password,
            file=st.actual_file,
            encryption_password=st.actual_encryption_password,
            data_dir=st.actual_data_dir,
        ) as actual:
            yield ActualpySession(actual)

"""A cashier's salary as one running balance, not a figure that resets.

Every finalized sale adds its reward to the balance; every "Kassir" expense
(money handed to the cashier, or spent on their behalf) takes from it. The
balance is never cut at a week or month boundary:

- an expense bigger than what was earned leaves the cashier in minus, and the
  minus follows them into the next periods until later rewards cover it;
- with no expenses at all, the balance just keeps growing.

So the salary shown for a period is what was carried in from before it plus
what the period earned, minus what the period took.
"""

from sqlalchemy import func, select

import database as db
from reporting.sale_period import report_date_expr


def balances_before(session, before_date):
    """``{cashier_id: earned - taken}`` in UZS for everything before ``before_date``."""
    balances = {}
    earned = session.execute(
        select(db.Sale.cashier_id, func.coalesce(func.sum(db.Sale.cashier_reward), 0))
        .where(
            db.Sale.cashier_id.is_not(None),
            func.coalesce(db.Sale.is_finalized, 0) == 1,
            report_date_expr(db.Sale.created_at, db.Sale.finalized_at) < before_date,
        )
        .group_by(db.Sale.cashier_id)
    )
    for cashier_id, amount in earned:
        balances[cashier_id] = balances.get(cashier_id, 0.0) + (amount or 0)

    taken = session.execute(
        select(
            db.Expense.cashier_id,
            func.coalesce(func.sum(db.Expense.amount * func.coalesce(db.Currency.rate_to_uzs, 1)), 0),
        )
        .outerjoin(db.Currency, db.Currency.code == db.Expense.currency_code)
        .where(
            db.Expense.cashier_id.is_not(None),
            db._date_expr(db.Expense.created_at) < before_date,
        )
        .group_by(db.Expense.cashier_id)
    )
    for cashier_id, amount in taken:
        balances[cashier_id] = balances.get(cashier_id, 0.0) - (amount or 0)
    return balances


def get_opening_balance(before_date, cashier_id=None):
    """Balance carried into a period starting on ``before_date``.

    For one cashier when ``cashier_id`` is given, otherwise summed over every
    staff member (the account owner is not a cashier).
    """
    with db.session_scope() as session:
        balances = balances_before(session, before_date)
        if cashier_id is not None:
            return balances.get(cashier_id, 0.0)
        staff = db._staff_user_ids(session)
        return sum(amount for owner, amount in balances.items() if owner in staff)

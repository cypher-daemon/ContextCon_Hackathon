"""Row -> model helpers for payments and attempts. Owner: lane A."""
from __future__ import annotations

import sqlite3

from paypilot.schemas import AttemptStep, Payment, PaymentAttempt, PaymentStatus, SettlementStatus


def payment_from_row(r: sqlite3.Row) -> Payment:
    return Payment(payment_id=r["payment_id"], item_id=r["item_id"], approval_id=r["approval_id"], payment_key=r["payment_key"],
                   provider=r["provider"], amount_paise=r["amount_paise"], currency=r["currency"], status=PaymentStatus(r["status"]),
                   settlement_status=SettlementStatus(r["settlement_status"]), current_attempt=r["current_attempt"],
                   created_at=r["created_at"], updated_at=r["updated_at"])


def attempt_from_row(r: sqlite3.Row) -> PaymentAttempt:
    return PaymentAttempt(attempt_key=r["attempt_key"], payment_id=r["payment_id"], attempt_no=r["attempt_no"], approval_id=r["approval_id"],
                          provider_ref=r["provider_ref"], step=AttemptStep(r["step"]), outcome=r["outcome"], last_error=r["last_error"],
                          created_at=r["created_at"], updated_at=r["updated_at"])


def get_payment(con: sqlite3.Connection, payment_id: str) -> Payment:
    return payment_from_row(con.execute("SELECT * FROM payments WHERE payment_id=?", (payment_id,)).fetchone())

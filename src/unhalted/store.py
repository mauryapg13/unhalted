"""Case storage.

SQLite, accessed directly. The audit table is append-only by convention: nothing
in this module updates or deletes a row in it, because the timeline is the only
account of what happened that anyone should trust.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from unhalted.models import AuditRecord, Case, CaseState, Diagnosis, FailureSignal

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id            TEXT PRIMARY KEY,
    customer_ref  TEXT NOT NULL,
    amount_paise  INTEGER NOT NULL,
    state         TEXT NOT NULL,
    opened_at     TEXT NOT NULL,
    retry_count   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS signals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id       TEXT NOT NULL REFERENCES cases(id),
    payment_id    TEXT NOT NULL,
    occurred_at   TEXT NOT NULL,
    source        TEXT NOT NULL,
    payload       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS diagnoses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id       TEXT NOT NULL REFERENCES cases(id),
    at            TEXT NOT NULL,
    payload       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id       TEXT NOT NULL REFERENCES cases(id),
    at            TEXT NOT NULL,
    decision_type TEXT NOT NULL,
    action        TEXT NOT NULL,
    payload       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signals_case ON signals(case_id);
CREATE INDEX IF NOT EXISTS idx_audit_case   ON audit(case_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_payment ON signals(payment_id);
"""


def default_db_path() -> str:
    return os.environ.get("UNHALTED_DB", "unhalted.db")


class Store:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or default_db_path()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """One transaction. Used where several writes must land together."""
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # -- cases ---------------------------------------------------------------

    def open_case(self, signal: FailureSignal) -> Case:
        """Open a case for a signal, or return the existing one.

        Razorpay retries webhook delivery, so the same payment can arrive more
        than once. A duplicate must not open a second case or it would be
        counted twice in recovery figures.
        """
        existing = self.case_for_payment(signal.payment_id)
        if existing:
            return existing

        case = Case(
            id=f"CASE-{uuid.uuid4().hex[:8].upper()}",
            customer_ref=signal.customer_ref,
            amount_paise=signal.amount_paise,
            opened_at=signal.occurred_at,
        )
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO cases (id, customer_ref, amount_paise, state, opened_at, retry_count)"
                " VALUES (?,?,?,?,?,?)",
                (
                    case.id,
                    case.customer_ref,
                    case.amount_paise,
                    case.state.value,
                    case.opened_at.isoformat(),
                    case.retry_count,
                ),
            )
            conn.execute(
                "INSERT INTO signals (case_id, payment_id, occurred_at, source, payload)"
                " VALUES (?,?,?,?,?)",
                (
                    case.id,
                    signal.payment_id,
                    signal.occurred_at.isoformat(),
                    signal.source,
                    signal.model_dump_json(),
                ),
            )
        return case

    def get_case(self, case_id: str) -> Case | None:
        row = self._conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        return self._row_to_case(row) if row else None

    def case_for_payment(self, payment_id: str) -> Case | None:
        row = self._conn.execute(
            "SELECT c.* FROM cases c JOIN signals s ON s.case_id = c.id WHERE s.payment_id = ?",
            (payment_id,),
        ).fetchone()
        return self._row_to_case(row) if row else None

    def set_state(self, case_id: str, state: CaseState) -> None:
        with self._tx() as conn:
            conn.execute("UPDATE cases SET state = ? WHERE id = ?", (state.value, case_id))

    @staticmethod
    def _row_to_case(row: sqlite3.Row) -> Case:
        return Case(
            id=row["id"],
            customer_ref=row["customer_ref"],
            amount_paise=row["amount_paise"],
            state=CaseState(row["state"]),
            opened_at=datetime.fromisoformat(row["opened_at"]),
            retry_count=row["retry_count"],
        )

    # -- diagnoses and audit -------------------------------------------------

    def record_diagnosis(self, case_id: str, diagnosis: Diagnosis, at: datetime) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO diagnoses (case_id, at, payload) VALUES (?,?,?)",
                (case_id, at.isoformat(), diagnosis.model_dump_json()),
            )

    def latest_diagnosis(self, case_id: str) -> Diagnosis | None:
        row = self._conn.execute(
            "SELECT payload FROM diagnoses WHERE case_id = ? ORDER BY id DESC LIMIT 1", (case_id,)
        ).fetchone()
        return Diagnosis.model_validate_json(row["payload"]) if row else None

    def record(self, record: AuditRecord) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO audit (case_id, at, decision_type, action, payload) VALUES (?,?,?,?,?)",
                (
                    record.case_id,
                    record.at.isoformat(),
                    record.decision_type,
                    record.action,
                    record.model_dump_json(),
                ),
            )

    def timeline(self, case_id: str) -> list[AuditRecord]:
        rows = self._conn.execute(
            "SELECT payload FROM audit WHERE case_id = ? ORDER BY id ASC", (case_id,)
        ).fetchall()
        return [AuditRecord.model_validate_json(r["payload"]) for r in rows]

    def signals(self, case_id: str) -> list[FailureSignal]:
        rows = self._conn.execute(
            "SELECT payload FROM signals WHERE case_id = ? ORDER BY id ASC", (case_id,)
        ).fetchall()
        return [FailureSignal.model_validate_json(r["payload"]) for r in rows]

    def all_cases(self) -> list[Case]:
        rows = self._conn.execute("SELECT * FROM cases ORDER BY opened_at DESC").fetchall()
        return [self._row_to_case(r) for r in rows]

"""Case storage.

SQLite, accessed directly. The audit table is append-only by convention: nothing
in this module updates or deletes a row in it, because the timeline is the only
account of what happened that anyone should trust.

Thread safety: a sqlite3 connection is not safe for concurrent use, and FastAPI
runs sync handlers in a threadpool, so two webhooks arriving together reach this
class at the same time. Every use of the connection is therefore serialised
behind a re-entrant lock. Re-entrant because the write path reads first —
`open_case` checks for an existing case before opening a transaction.

Serialising all access is the right trade here: the workload is one writer and
low volume. It is not a reason to reach for a server database.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from unhalted import config
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

CREATE TABLE IF NOT EXISTS pending_actions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id       TEXT NOT NULL REFERENCES cases(id),
    customer_ref  TEXT NOT NULL,
    kind          TEXT NOT NULL,
    scheduled_for TEXT,
    state         TEXT NOT NULL DEFAULT 'pending',
    cancel_reason TEXT,
    created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pending_customer
    ON pending_actions(customer_ref, state);

CREATE TABLE IF NOT EXISTS processed_events (
    event_id      TEXT PRIMARY KEY,
    at            TEXT NOT NULL,
    case_id       TEXT
);

CREATE INDEX IF NOT EXISTS idx_signals_case ON signals(case_id);
CREATE INDEX IF NOT EXISTS idx_audit_case   ON audit(case_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_payment ON signals(payment_id);
"""


def default_db_path() -> str:
    return config.database_path()


class Store:
    def __init__(self, path: str | None = None) -> None:
        self.path = path or default_db_path()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        # Write-ahead logging: a reader no longer blocks the writer, and a
        # crash mid-write leaves the database recoverable from the log rather
        # than dependent on a rollback journal surviving.
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        # FULL is the default and is kept deliberately: every commit fsyncs
        # before returning. A case that the agent believes it recorded must
        # survive the power going out, because Razorpay will not redeliver
        # forever and a lost case is money nobody chases.
        self._conn.execute("PRAGMA synchronous = FULL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """One transaction, held for the duration against other threads.

        Without the lock, a concurrent request's commit lands this one's
        half-written rows, and its rollback discards them.
        """
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        """A read, serialised against writers on the same connection."""
        with self._lock:
            yield self._conn

    # -- cases ---------------------------------------------------------------

    def open_case(self, signal: FailureSignal) -> Case:
        """Open a case for a signal, or return the existing one.

        Razorpay retries webhook delivery, so the same payment can arrive more
        than once. A duplicate must not open a second case or it would be
        counted twice in recovery figures.

        The check and the insert are held under one lock. Taking the lock
        separately for each would let a second thread read "no such case"
        between them and try to insert the same payment — check-then-act with a
        gap in the middle.

        The unique index on `signals.payment_id` is the backstop. The lock is
        per-process, so two uvicorn workers would not see each other; the
        constraint holds regardless, and losing that race returns the case the
        winner opened rather than raising.
        """
        with self._lock:
            return self._open_case_locked(signal)

    def _open_case_locked(self, signal: FailureSignal) -> Case:
        existing = self.case_for_payment(signal.payment_id)
        if existing:
            return existing

        case = Case(
            id=f"CASE-{uuid.uuid4().hex[:8].upper()}",
            customer_ref=signal.customer_ref,
            amount_paise=signal.amount_paise,
            opened_at=signal.occurred_at,
        )
        try:
            self._insert_case(case, signal)
        except sqlite3.IntegrityError:
            # Another process won the race on signals.payment_id.
            winner = self.case_for_payment(signal.payment_id)
            if winner is None:
                raise
            return winner
        return case

    def _insert_case(self, case: Case, signal: FailureSignal) -> None:
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

    def get_case(self, case_id: str) -> Case | None:
        with self._read() as conn:
            row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        return self._row_to_case(row) if row else None

    def case_for_payment(self, payment_id: str) -> Case | None:
        with self._read() as conn:
            row = conn.execute(
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
        with self._read() as conn:
            row = conn.execute(
                "SELECT payload FROM diagnoses WHERE case_id = ? ORDER BY id DESC LIMIT 1",
                (case_id,),
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
        with self._read() as conn:
            rows = conn.execute(
                "SELECT payload FROM audit WHERE case_id = ? ORDER BY id ASC", (case_id,)
            ).fetchall()
        return [AuditRecord.model_validate_json(r["payload"]) for r in rows]

    def signals(self, case_id: str) -> list[FailureSignal]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT payload FROM signals WHERE case_id = ? ORDER BY id ASC", (case_id,)
            ).fetchall()
        return [FailureSignal.model_validate_json(r["payload"]) for r in rows]

    # -- idempotency ---------------------------------------------------------

    def event_seen(self, event_id: str) -> str | None:
        """Return the case a previously processed event produced, if any.

        Razorpay redelivers webhooks, and prescribes `x-razorpay-event-id` —
        unique per event — as the way to recognise a repeat. Without this a
        redelivery would open a second case and the same rupees would be
        counted twice in recovery figures.
        """
        with self._read() as conn:
            row = conn.execute(
                "SELECT case_id FROM processed_events WHERE event_id = ?", (event_id,)
            ).fetchone()
        return row["case_id"] if row else None

    def mark_event(self, event_id: str, case_id: str, at: datetime) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO processed_events (event_id, at, case_id) VALUES (?,?,?)",
                (event_id, at.isoformat(), case_id),
            )

    # -- pending actions -----------------------------------------------------

    def schedule_action(
        self,
        case_id: str,
        customer_ref: str,
        kind: str,
        scheduled_for: datetime | None,
        at: datetime,
    ) -> int:
        """Record an action the agent intends to take."""
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO pending_actions"
                " (case_id, customer_ref, kind, scheduled_for, state, created_at)"
                " VALUES (?,?,?,?,'pending',?)",
                (
                    case_id,
                    customer_ref,
                    kind,
                    scheduled_for.isoformat() if scheduled_for else None,
                    at.isoformat(),
                ),
            )
            return int(cur.lastrowid or 0)

    def pending_actions(
        self, *, customer_ref: str | None = None, case_id: str | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM pending_actions WHERE state = 'pending'"
        params: list[Any] = []
        if customer_ref:
            sql += " AND customer_ref = ?"
            params.append(customer_ref)
        if case_id:
            sql += " AND case_id = ?"
            params.append(case_id)
        with self._read() as conn:
            return [dict(r) for r in conn.execute(sql + " ORDER BY id", params).fetchall()]

    def cancel_pending(
        self,
        reason: str,
        *,
        customer_ref: str | None = None,
        case_id: str | None = None,
    ) -> int:
        """Cancel every pending action in scope, in one transaction.

        The specification requires that a revocation cancels a retry, two nudges
        and a voice callback together, with no partial execution possible. One
        UPDATE inside one transaction is what makes that true: either every
        action is cancelled or none is, and no other thread sees a half-cancelled
        customer because all connection use is serialised.
        """
        if not customer_ref and not case_id:
            raise ValueError("cancel_pending needs a customer_ref or a case_id")

        sql = "UPDATE pending_actions SET state = 'cancelled', cancel_reason = ? WHERE state = 'pending'"
        params: list[Any] = [reason]
        if customer_ref:
            sql += " AND customer_ref = ?"
            params.append(customer_ref)
        if case_id:
            sql += " AND case_id = ?"
            params.append(case_id)

        with self._tx() as conn:
            return int(conn.execute(sql, params).rowcount)

    def all_cases(self) -> list[Case]:
        with self._read() as conn:
            rows = conn.execute("SELECT * FROM cases ORDER BY opened_at DESC").fetchall()
        return [self._row_to_case(r) for r in rows]

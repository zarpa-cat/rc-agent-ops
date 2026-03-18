from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from enum import Enum


class SubscriberRisk(Enum):
    CLEAN = "CLEAN"
    SUSPECTED = "SUSPECTED"
    BLOCKED = "BLOCKED"


@dataclass
class RiskEvent:
    subscriber_id: str
    risk: SubscriberRisk
    reason: str
    ts: float


class RiskTracker:
    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS risk_events("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "subscriber_id TEXT, risk TEXT, reason TEXT, ts REAL)"
        )
        self._conn.commit()

    def mark(self, subscriber_id: str, risk: SubscriberRisk, reason: str) -> None:
        self._conn.execute(
            "INSERT INTO risk_events(subscriber_id, risk, reason, ts) "
            "VALUES(?,?,?,?)",
            (subscriber_id, risk.value, reason, time.time()),
        )
        self._conn.commit()

    def get(self, subscriber_id: str) -> SubscriberRisk:
        row = self._conn.execute(
            "SELECT risk FROM risk_events "
            "WHERE subscriber_id=? ORDER BY id DESC LIMIT 1",
            (subscriber_id,),
        ).fetchone()
        if row is None:
            return SubscriberRisk.CLEAN
        return SubscriberRisk(row[0])

    def history(self, subscriber_id: str, limit: int = 20) -> list[RiskEvent]:
        rows = self._conn.execute(
            "SELECT subscriber_id, risk, reason, ts FROM risk_events "
            "WHERE subscriber_id=? ORDER BY id DESC LIMIT ?",
            (subscriber_id, limit),
        ).fetchall()
        return [
            RiskEvent(r[0], SubscriberRisk(r[1]), r[2], r[3]) for r in rows
        ]

    def list_at_risk(self) -> list[tuple[str, SubscriberRisk]]:
        rows = self._conn.execute(
            "SELECT subscriber_id, risk FROM risk_events "
            "WHERE id = (SELECT MAX(id) FROM risk_events e2 "
            "WHERE e2.subscriber_id = risk_events.subscriber_id) "
            "AND risk != ?",
            (SubscriberRisk.CLEAN.value,),
        ).fetchall()
        return [(r[0], SubscriberRisk(r[1])) for r in rows]

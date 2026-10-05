"""Durable record of bridge sessions and what happened in them (SQLite)."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  id TEXT PRIMARY KEY,
  claude_session_id TEXT,
  project_path TEXT NOT NULL,
  label TEXT,
  status TEXT NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  last_error TEXT
);
CREATE TABLE IF NOT EXISTS events(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id),
  ts REAL NOT NULL,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_session_seq ON events(session_id, seq);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
"""

UPDATABLE = {"claude_session_id", "label", "status", "last_error"}


class SessionNotFound(KeyError):
    pass


class Store:
    def __init__(self, path: str | Path, clock: Callable[[], float] = time.time) -> None:
        if str(path) != ":memory:":
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            path = Path(path).expanduser()
        self.clock = clock
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._recover_interrupted()

    def _recover_interrupted(self) -> None:
        # A session cannot really be running when the bridge has just started.
        for row in self.db.execute("SELECT id FROM sessions WHERE status='running'").fetchall():
            self.update_session(row["id"], status="interrupted")
            self.add_event(row["id"], "interrupted", {"reason": "bridge restarted"})

    def create_session(self, project_path: str, label: str | None = None) -> dict[str, Any]:
        now = self.clock()
        sid = str(uuid.uuid4())
        with self.db:
            self.db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?)",
                (sid, None, project_path, label, "idle", now, now, None),
            )
        return self.get_session(sid)

    def get_session(self, session_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise SessionNotFound(session_id)
        return dict(row)

    def update_session(self, session_id: str, **fields: Any) -> None:
        unknown = set(fields) - UPDATABLE
        if unknown:
            raise ValueError(f"cannot update {sorted(unknown)}")
        self.get_session(session_id)
        fields["updated_at"] = self.clock()
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.db:
            self.db.execute(f"UPDATE sessions SET {cols} WHERE id=?", (*fields.values(), session_id))

    def list_sessions(
        self, project_path: str | None = None, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        q, args = "SELECT * FROM sessions WHERE 1=1", list[Any]()
        if project_path:
            q += " AND project_path=?"
            args.append(project_path)
        if status:
            q += " AND status=?"
            args.append(status)
        q += " ORDER BY updated_at DESC, rowid DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.db.execute(q, args)]

    def add_event(self, session_id: str, kind: str, payload: dict[str, Any]) -> int:
        self.get_session(session_id)
        with self.db:
            cur = self.db.execute(
                "INSERT INTO events(session_id, ts, kind, payload) VALUES(?,?,?,?)",
                (session_id, self.clock(), kind, json.dumps(payload, default=str)),
            )
        assert cur.lastrowid is not None  # always set after an INSERT
        return cur.lastrowid

    def events(self, session_id: str, after: int = 0, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT seq, session_id, ts, kind, payload FROM events WHERE session_id=? AND seq>? ORDER BY seq LIMIT ?",
            (session_id, after, limit),
        )
        return [_decode(r) for r in rows]

    def tail(self, session_id: str, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT seq, session_id, ts, kind, payload FROM events WHERE session_id=? ORDER BY seq DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [_decode(r) for r in reversed(rows)]

    def recent_events(self, since: float, project_path: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        q = (
            "SELECT e.seq, e.session_id, e.ts, e.kind, e.payload, s.project_path, s.label"
            " FROM events e JOIN sessions s ON s.id=e.session_id WHERE e.ts>=?"
        )
        args: list[Any] = [since]
        if project_path:
            q += " AND s.project_path=?"
            args.append(project_path)
        q += " ORDER BY e.seq DESC LIMIT ?"
        args.append(limit)
        return [_decode(r) for r in reversed(self.db.execute(q, args).fetchall())]


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["payload"] = json.loads(d["payload"])
    return d

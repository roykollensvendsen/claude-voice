"""What has happened since the client last asked: a delta feed for polling clients.

MCP clients pull; they cannot be pushed to. So the bridge keeps the news:
bridge sessions already journal their events, and a LiveWatcher turns status
changes of running Claude Code sessions (finished, waiting for input, started,
ended) into feed entries. `whats_new` returns both after a cursor.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .store import Store

SCHEMA = """
CREATE TABLE IF NOT EXISTS live_snapshot(name TEXT PRIMARY KEY, status TEXT, session_id TEXT);
CREATE TABLE IF NOT EXISTS feed(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  session TEXT NOT NULL,
  kind TEXT NOT NULL,
  text TEXT,
  project TEXT
);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""

BRIDGE_KINDS = {
    "result",
    "error",
    "approval_requested",
    "approval_expired",
    "cancelled",
    "interrupted",
}


def read_live(live_dir: Path) -> list[dict[str, Any]]:
    """Claude Code's registry of running sessions, keeping only live processes."""
    out = []
    for f in sorted(live_dir.glob("*.json")) if live_dir.is_dir() else []:
        try:
            d = json.loads(f.read_text())
            os.kill(int(d["pid"]), 0)
        except (ValueError, KeyError, TypeError, OSError):
            continue  # unreadable, or the process is gone
        out.append(d)
    return out


class LiveWatcher:
    def __init__(
        self,
        store: Store,
        live_dir: Path,
        root: Path,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.db = store.db
        self.db.executescript(SCHEMA)
        self.live_dir = live_dir
        self.root = Path(root).resolve()
        self.clock = clock

    def _project(self, cwd: str | None) -> str | None:
        if not cwd:
            return None
        path = Path(cwd).resolve()
        if path != self.root and self.root not in path.parents:
            return None
        return str(path.relative_to(self.root)) or "."

    def check(self) -> list[dict[str, Any]]:
        """Diff the running sessions against the last look; record and return the changes."""
        now = {}
        for d in read_live(self.live_dir):
            project = self._project(d.get("cwd"))
            if project is not None and d.get("name"):
                now[d["name"]] = (d.get("status"), d.get("sessionId"), project)

        before = {
            r[0]: (r[1], r[2]) for r in self.db.execute("SELECT * FROM live_snapshot").fetchall()
        }
        baselined = self.db.execute("SELECT 1 FROM meta WHERE key='baselined'").fetchone()

        changes: list[tuple[str, str, str | None, str | None]] = []
        if baselined:
            for name, (status, _, project) in now.items():
                if name not in before:
                    changes.append((name, "started", status, project))
                elif before[name][0] != status:
                    kind = _transition(before[name][0], status)
                    if kind:
                        changes.append((name, kind, status, project))
            for name in before.keys() - now.keys():
                changes.append((name, "ended", None, None))

        ts = self.clock()
        with self.db:
            self.db.execute("DELETE FROM live_snapshot")
            self.db.executemany(
                "INSERT INTO live_snapshot VALUES(?,?,?)",
                [(n, s, sid) for n, (s, sid, _) in now.items()],
            )
            self.db.execute("INSERT OR IGNORE INTO meta VALUES('baselined','1')")
            seqs = []
            for name, kind, status, project in changes:
                cur = self.db.execute(
                    "INSERT INTO feed(ts, session, kind, text, project) VALUES(?,?,?,?,?)",
                    (ts, name, kind, status, project),
                )
                seqs.append(cur.lastrowid)
        return self.feed_after(min(seqs) - 1) if seqs else []

    def feed_after(self, seq: int, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT seq, ts, session, kind, text, project FROM feed"
            " WHERE seq>? ORDER BY seq LIMIT ?",
            (seq, limit),
        ).fetchall()
        return [
            {"seq": r[0], "ts": r[1], "session": r[2], "kind": r[3], "text": r[4], "project": r[5]}
            for r in rows
        ]

    def last_seq(self) -> int:
        return self.db.execute("SELECT COALESCE(MAX(seq), 0) FROM feed").fetchone()[0]


def _transition(old: str | None, new: str | None) -> str | None:
    if new == "waiting":
        return "needs_input"
    if old in ("busy", "shell") and new == "idle":
        return "finished"
    if old in ("idle", "waiting") and new == "busy":
        return "working"
    return None


def bridge_events(store: Store, after: int, limit: int = 200) -> list[dict[str, Any]]:
    """Newsworthy events from sessions run by the bridge, in voice-sized form."""
    placeholders = ",".join("?" * len(BRIDGE_KINDS))
    rows = store.db.execute(
        "SELECT e.seq, e.ts, e.kind, e.payload, s.label, s.project_path FROM events e"
        f" JOIN sessions s ON s.id=e.session_id WHERE e.seq>? AND e.kind IN ({placeholders})"
        " ORDER BY e.seq LIMIT ?",
        (after, *sorted(BRIDGE_KINDS), limit),
    ).fetchall()
    out = []
    for seq, ts, kind, payload, label, project_path in rows:
        p = json.loads(payload)
        name = label or Path(project_path).name
        if kind == "result":
            kind, text = ("error" if p.get("is_error") else "finished"), p.get("text")
        elif kind == "error":
            text = p.get("error")
        elif kind == "approval_requested":
            kind = "needs_approval"
            text = f"#{p.get('id')} {p.get('tool')}: {json.dumps(p.get('input'))[:200]}"
        else:
            text = None
        out.append({"seq": seq, "ts": ts, "session": name, "kind": kind, "text": text})
    return out


def last_bridge_seq(store: Store) -> int:
    return store.db.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]

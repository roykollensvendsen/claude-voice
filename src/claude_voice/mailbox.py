"""The voice mailbox: a session's answer to the voice waits here until the voice has read it out.

A session answers the voice by messaging the courier, the bridge's own helper. Each answer is
put here, in the bridge's database. Every voice call that can carry it returns it until a later
call confirms the voice heard it: a whats_new whose cursor reaches past it, or one that names
it in `ack`. Returning it is not enough, since a connection can drop before it is read aloud.

After three returns with no confirmation, or after a day, a reply is no longer pushed, only
counted, and the voice is told to list it. Read replies are removed after seven days; an unread
one is never removed. The design is voice-mailbox v1.1, with the owner's notes.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Iterable
from typing import Any

from .transcripts import clip

SCHEMA = """
CREATE TABLE IF NOT EXISTS mailbox(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  sender TEXT NOT NULL,
  text TEXT NOT NULL,
  delivered_at REAL,
  pushes INTEGER NOT NULL DEFAULT 0,
  read_at REAL
);
"""
PUSHES = 3  # returns with no confirmation before a reply is only counted
FRESH = 24 * 3600.0  # after a day unread, a reply is only counted
PER_ANSWER = 5  # the most replies one answer carries, newest last
CHARS = 600  # each reply is clipped to this
KEEP = 7 * 24 * 3600.0  # how long a read reply is kept
LISTED = 20  # the most replies list_replies returns


def age(seconds: float) -> str:
    """How long ago, in a few spoken words: '42 s ago', '5 min ago', '3 h ago'."""
    if seconds < 60:
        return f"{max(0, round(seconds))} s ago"
    if seconds < 3600:
        return f"{round(seconds / 60)} min ago"
    return f"{round(seconds / 3600)} h ago"


class Mailbox:
    def __init__(self, db: sqlite3.Connection, clock: Callable[[], float] = time.time) -> None:
        self.db = db
        self.db.executescript(SCHEMA)
        self.clock = clock

    def put(self, sender: str, text: str) -> int:
        """Keep a reply, and drop read ones past their time."""
        now = self.clock()
        with self.db:
            # RULE: only read replies are purged after seven days
            self.db.execute("DELETE FROM mailbox WHERE read_at IS NOT NULL AND ts < ?", (now - KEEP,))
            cur = self.db.execute(
                "INSERT INTO mailbox(ts, sender, text) VALUES(?,?,?)", (now, sender, clip(text, CHARS))
            )
        return cur.lastrowid or 0

    def push(self) -> list[dict[str, Any]]:
        """The unread replies still to be pushed, newest last, each counted as one more push."""
        now = self.clock()
        rows = self.db.execute(
            # RULE: after three pushes a reply is counted not pushed
            "SELECT seq, ts, sender, text FROM mailbox WHERE read_at IS NULL AND pushes < ?"
            # RULE: an unread reply older than a day is counted not pushed
            " AND ts >= ? ORDER BY seq DESC LIMIT ?",
            (PUSHES, now - FRESH, PER_ANSWER),
        ).fetchall()[::-1]
        with self.db:
            self.db.executemany(
                "UPDATE mailbox SET delivered_at=?, pushes=pushes+1 WHERE seq=?", [(now, r[0]) for r in rows]
            )
        return [_shown(r, now) for r in rows]

    def unread(self) -> list[dict[str, Any]]:
        """Every unread reply, the held ones too, oldest first; they count as returned, not pushed."""
        now = self.clock()
        rows = self.db.execute(
            "SELECT seq, ts, sender, text FROM mailbox WHERE read_at IS NULL ORDER BY seq LIMIT ?", (LISTED,)
        ).fetchall()
        with self.db:
            self.db.executemany("UPDATE mailbox SET delivered_at=? WHERE seq=?", [(now, r[0]) for r in rows])
        return [_shown(r, now) for r in rows]

    def ack(self, upto: int | None = None, seqs: Iterable[int] = ()) -> None:
        """Mark read the returned replies up to `upto`, and those named in `seqs`."""
        named = [int(s) for s in seqs]
        marks = ",".join("?" * len(named)) or "NULL"
        with self.db:
            self.db.execute(
                # RULE: a reply is read only once a later call acknowledges it
                "UPDATE mailbox SET read_at=? WHERE read_at IS NULL AND delivered_at IS NOT NULL"
                f" AND (seq <= ? OR seq IN ({marks}))",
                (self.clock(), -1 if upto is None else upto, *named),
            )

    def held(self) -> dict[str, int]:
        """How many unread replies are no longer pushed: after three pushes, and after a day."""
        now = self.clock()
        tired, older = self.db.execute(
            "SELECT COALESCE(SUM(pushes >= ?), 0), COALESCE(SUM(pushes < ? AND ts < ?), 0)"
            " FROM mailbox WHERE read_at IS NULL",
            (PUSHES, PUSHES, now - FRESH),
        ).fetchone()
        return {"not_confirmed": tired, "older": older}

    def waiting(self) -> str | None:
        """A sentence for the voice when replies wait unheard; None when none do."""
        held = self.held()
        parts = []
        if held["not_confirmed"]:
            n = held["not_confirmed"]
            parts.append(f"{n} {'reply' if n == 1 else 'replies'} not confirmed heard")
        if held["older"]:
            n = held["older"]
            parts.append(f"{n} older {'reply' if n == 1 else 'replies'}")
        if not parts:
            return None
        return f"{' and '.join(parts)}; list_replies reads {'it' if sum(held.values()) == 1 else 'them'}."


def _shown(row: tuple[Any, ...], now: float) -> dict[str, Any]:
    seq, ts, sender, text = row
    return {
        "reply_id": seq,
        "ts": ts,
        "session": sender,
        "kind": "reply",
        "text": f"Reply from {sender}: {text}",
        "age": age(now - ts),
    }

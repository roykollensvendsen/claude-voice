"""Usage: the plan's limits and each session's context, and an estimate of voice activity.

The plan and the context come from Claude Code's status line. Its input reaches
only a status line script, so the owner's script also writes it, one file per
session, to a folder the bridge reads. The plan figures cover every way of using
Claude, chat and voice included, and move only when a session talks to the model.

The voice estimate counts what the bridge itself serves: calls from the Claude
app over the internet, and calls from the voice app on this computer, which
carry a trace id. It is an estimate of activity through the bridge, not a
measure of the plan.

No dollar figure is passed on: on a subscription it is notional.
"""

from __future__ import annotations

import ipaddress
import json
import math
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

STATUSLINE_DIR = Path("~/.local/state/claude-voice/statusline")
PLAN_LABEL = "includes chat and voice; as of the last answer"
VOICE_LABEL = "estimate, voice activity via the bridge"
WARN_AT = 80
CONNECTION_GAP = 600  # calls further apart than this belong to separate conversations
CHARS_PER_TOKEN = 4


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds")


def _lines(folder: Path) -> list[dict[str, Any]]:
    found = []
    for path in sorted(folder.expanduser().glob("*.json")) if folder.expanduser().is_dir() else []:
        try:
            found.append(json.loads(path.read_text()))
        except (OSError, ValueError):
            continue
    return found


def plan(folder: Path = STATUSLINE_DIR, now: float | None = None) -> dict[str, Any] | None:
    """The plan's five-hour and weekly use, from the newest status line that had them."""
    now = time.time() if now is None else now
    newest = max(
        (line for line in _lines(folder) if isinstance(line.get("rate_limits"), dict)),
        key=lambda line: float(line.get("at") or 0),
        default=None,
    )
    if newest is None:
        return None
    out: dict[str, Any] = {"as_of": iso(float(newest.get("at") or 0)), "label": PLAN_LABEL}
    for window in ("five_hour", "seven_day"):
        figure = newest["rate_limits"].get(window)
        if not isinstance(figure, dict):
            out[window] = None
            continue
        resets = float(figure["resets_at"]) if figure.get("resets_at") else None
        out[window] = {
            "used_percentage": figure.get("used_percentage"),
            "resets_at": iso(resets) if resets else None,
            "stale": resets is not None and resets < now,
        }
    return out


def warning(current: dict[str, Any] | None) -> dict[str, Any] | None:
    """A warning while a live figure is near or at its limit."""
    if not current:
        return None
    worst = max(
        (
            w["used_percentage"]
            for k in ("five_hour", "seven_day")
            if (w := current.get(k)) and not w["stale"] and isinstance(w.get("used_percentage"), (int, float))
        ),
        default=None,
    )
    if worst is None or worst < WARN_AT:
        return None
    return {
        "status": "rejected" if worst >= 100 else "allowed_warning",
        "used_percentage": worst,
        "at": current["as_of"],
    }


def context(folder: Path, session_id: str) -> dict[str, Any] | None:
    """How full a session's context was at its latest status line."""
    try:
        line = json.loads((folder.expanduser() / f"{session_id}.json").read_text())
    except (OSError, ValueError):
        return None
    used = (line.get("context_window") or {}).get("used_percentage")
    if used is None:
        return None
    return {"used_percentage": used, "as_of": iso(float(line.get("at") or 0))}


# -- voice activity --------------------------------------------------------------------


def client_of(address: str, trace: str | None) -> str | None:
    """Who a call is from, as far as voice goes: the Claude app reaches the bridge from
    elsewhere; the voice app calls from this computer with a trace id; anything else
    from this computer is not voice."""
    try:
        here = ipaddress.ip_address(address).is_loopback
    except ValueError:
        here = False
    if not here:
        return "claude_app"
    return "voice_app" if trace else None


def record(store: Any, client: str, connection: str, tool: str, chars: int, at: float | None = None) -> None:
    store.add_voice_call(time.time() if at is None else at, client, connection, tool, chars)


def _summary(calls: list[tuple[float, str, str, int]]) -> dict[str, Any]:
    by_connection: dict[str, list[float]] = {}
    for ts, _, connection, _ in calls:
        by_connection.setdefault(connection, []).append(ts)
    connections, seconds = 0, 0.0
    for stamps in by_connection.values():
        stamps.sort()
        start = previous = stamps[0]
        connections += 1
        for ts in stamps[1:]:
            if ts - previous > CONNECTION_GAP:
                seconds += previous - start
                connections += 1
                start = ts
            previous = ts
        seconds += previous - start
    chars = sum(c[3] for c in calls)
    return {
        "connections": connections,
        "minutes": math.ceil(seconds / 60),
        "tool_calls": len(calls),
        "chars_out": chars,
        "est_tokens": chars // CHARS_PER_TOKEN,
    }


def voice(store: Any, now: float | None = None) -> dict[str, Any]:
    """Voice activity through the bridge today and on each of the last seven days."""
    now = time.time() if now is None else now
    today = datetime.fromtimestamp(now, UTC).date()
    days = [today - timedelta(days=n) for n in range(6, -1, -1)]
    start = datetime(days[0].year, days[0].month, days[0].day, tzinfo=UTC).timestamp()
    calls = store.voice_calls_since(start)
    per_day = {d: [c for c in calls if datetime.fromtimestamp(c[0], UTC).date() == d] for d in days}
    return {
        "label": VOICE_LABEL,
        "today": _summary(per_day[today]),
        "last_7_days": [{"day": d.isoformat(), **_summary(per_day[d])} for d in days],
    }

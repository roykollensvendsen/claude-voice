"""The bridge's call log: one line per tool call, and daily totals, never what was said."""

from __future__ import annotations

import json
import os
import stat
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from claude_voice import calllog
from claude_voice.store import Store


class Clock:
    def __init__(self, at: str = "2026-11-17T10:00:00+00:00") -> None:
        self.now = datetime.fromisoformat(at).timestamp()

    def __call__(self) -> float:
        return self.now

    def go(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "bridge.db")


def opened(tmp_path: Path, store: Store, clock: Clock, **kwargs) -> calllog.CallLog:
    log = calllog.CallLog(tmp_path / "state" / "calls.jsonl", store, clock=clock, **kwargs)
    log.start_up()
    return log


def lines(log: calllog.CallLog) -> list[dict]:
    if not log.path.exists():
        return []
    return [json.loads(line) for line in log.path.read_text(encoding="utf-8").splitlines()]


def call(log: calllog.CallLog, clock: Clock, tool: str = "garage_status", ms: float = 120, **end) -> str:
    started = log.start(tool, "voice_app", end.pop("caller", None))
    clock.go(ms / 1000)
    log.end(started, end.pop("outcome", "ok"), end.pop("error_class", None), ms)
    return started


# -- what a line holds ---------------------------------------------------------------------


def test_a_failed_call_keeps_a_plain_error_class_never_its_message(tmp_path, store, clock):
    log = opened(tmp_path, store, clock)
    outcome, error_class = calllog.classify(error=RuntimeError("token=sk-very-secret-value"))
    call(log, clock, outcome=outcome, error_class=error_class)
    text = log.path.read_text(encoding="utf-8")
    assert "sk-very-secret-value" not in text and "token" not in text
    line = lines(log)[0]
    assert set(line) == {"id", "tool", "source", "caller", "at", "ended_at", "outcome", "error_class", "ms"}
    assert (line["outcome"], line["error_class"]) == ("error", "bridge_failed")


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ({}, ("ok", None)),
        ({"refused": True}, ("refused", "refused")),
        ({"cancelled": True}, ("cancelled", "caller_gone")),
        ({"error": TimeoutError()}, ("timeout", "timeout")),
        ({"error": TimeoutError()}, ("timeout", "timeout")),
        ({"error": ConnectionRefusedError()}, ("error", "no_answer")),
        ({"error": ValueError("x")}, ("error", "bridge_failed")),
    ],
)
def test_an_outcome_is_one_of_five_with_a_plain_error_class(given, expected):
    assert calllog.classify(**given) == expected


def test_the_caller_is_a_claim_of_at_most_64_characters(tmp_path, store, clock):
    log = opened(tmp_path, store, clock)
    call(log, clock, caller="x" * 200)
    call(log, clock, caller="")
    assert [len(line["caller"] or "") for line in lines(log)] == [64, 0]
    assert lines(log)[1]["caller"] is None
    assert calllog.caller_of({"_meta": {"caller": "y" * 200}}) == "y" * 64


def test_the_caller_comes_from_meta_never_from_the_arguments():
    params = {"name": "send_task", "arguments": {"session": "victim", "caller": "victim"}, "_meta": {"caller": "me"}}
    assert calllog.caller_of(params) == "me"
    assert calllog.caller_of({"arguments": {"caller": "victim"}}) is None


@pytest.mark.skipif(sys.platform == "win32", reason="file modes are POSIX")
def test_the_call_log_is_the_owners_alone(tmp_path, store, clock):
    log = calllog.CallLog(tmp_path / "state" / "calls.jsonl", store, clock=clock)  # the first line makes the file
    call(log, clock)
    assert stat.S_IMODE(os.stat(log.path).st_mode) == 0o600
    log.prune()
    assert stat.S_IMODE(os.stat(log.path).st_mode) == 0o600


# -- polling, totals and days ---------------------------------------------------------------


def test_a_polling_tool_is_counted_but_never_written_line_by_line(tmp_path, store, clock):
    log = opened(tmp_path, store, clock)
    call(log, clock, tool="whats_new")
    call(log, clock, tool="get_active_session")
    assert lines(log) == []
    assert {row["tool"]: row["calls"] for row in log.today_rows()} == {"whats_new": 1, "get_active_session": 1}


def test_only_errors_and_timeouts_count_as_failures(tmp_path, store, clock):
    log = opened(tmp_path, store, clock)
    for outcome in ("ok", "error", "timeout", "refused", "cancelled"):
        call(log, clock, outcome=outcome, error_class=None if outcome == "ok" else "x")
    (row,) = log.today_rows()
    assert (row["calls"], row["errors"]) == (5, 2)


def test_a_day_is_an_oslo_day(tmp_path, store):
    late = Clock("2026-11-16T23:30:00+00:00")  # 00.30 on 17.11 in Oslo
    log = opened(tmp_path, store, late)
    call(log, late)
    assert log.today_rows()[0]["day"] == "2026-11-17"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no time zone database without tzdata")
def test_the_oslo_day_rule_agrees_with_the_time_zone_database_all_year():
    from zoneinfo import ZoneInfo

    oslo = ZoneInfo("Europe/Oslo")
    start = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    for hour in range(0, 2 * 366 * 24, 1):
        ts = start + hour * 1800
        assert calllog.oslo_day(ts) == datetime.fromtimestamp(ts, oslo).date().isoformat(), ts


def test_durations_fall_into_fixed_buckets():
    assert calllog.bucket(10) == 0 and calllog.bucket(50) == 0 and calllog.bucket(51) == 1
    assert calllog.bucket(23_000) == calllog.BOUNDS.index(30_000)
    assert calllog.bucket(10**7) == len(calllog.BOUNDS)


# -- pruning --------------------------------------------------------------------------------


def test_old_lines_and_lines_beyond_the_cap_are_pruned_through_a_rename(tmp_path, store, clock, monkeypatch):
    log = opened(tmp_path, store, clock, max_lines=3, prune_every=1000)
    call(log, clock)
    clock.go(91 * 86400)
    for _ in range(4):
        call(log, clock)
    renamed = []
    real = os.replace
    monkeypatch.setattr(calllog.os, "replace", lambda a, b: (renamed.append(b), real(a, b)))
    log.prune()
    kept = lines(log)
    assert len(kept) == 3 and renamed == [log.path]
    assert all(line["ended_at"] > "2026-12" for line in kept)


def test_the_log_is_pruned_on_write_and_at_start(tmp_path, store, clock):
    log = opened(tmp_path, store, clock, max_lines=2, prune_every=3)
    for _ in range(3):
        call(log, clock)
    assert len(lines(log)) == 2  # the third write pruned
    for _ in range(2):
        call(log, clock)
    assert len(lines(log)) == 4
    again = opened(tmp_path, store, clock, max_lines=2)
    assert len(lines(again)) == 2


# -- a crash between flushes ----------------------------------------------------------------


def test_todays_totals_are_rebuilt_after_a_crash_between_flushes(tmp_path, store, clock):
    log = opened(tmp_path, store, clock)
    for _ in range(3):
        call(log, clock)
    log.flush()
    call(log, clock, outcome="error", error_class="bridge_failed")
    call(log, clock, tool="whats_new")  # a polling count since the flush is lost
    # the bridge dies here, with no flush
    again = opened(tmp_path, store, clock)
    rows = {row["tool"]: (row["calls"], row["errors"]) for row in again.today_rows()}
    assert rows == {"garage_status": (4, 1)}


def test_a_call_that_spans_a_flush_is_counted_once(tmp_path, store, clock):
    log = opened(tmp_path, store, clock)
    started = log.start("ask_active_session", "voice_app", None)
    clock.go(10)
    log.flush()
    clock.go(13)
    log.end(started, "ok", None, 23_000)
    log.flush()
    again = opened(tmp_path, store, clock)
    assert [row["calls"] for row in again.today_rows()] == [1]


def test_totals_are_flushed_every_30_seconds_of_calls(tmp_path, store, clock):
    log = opened(tmp_path, store, clock)
    call(log, clock)
    assert store.call_totals("2026-11-17", "2026-11-17") == []
    clock.go(31)
    call(log, clock)
    assert [row["calls"] for row in store.call_totals("2026-11-17", "2026-11-17")] == [2]


def test_totals_older_than_two_years_are_dropped(tmp_path, store, clock):
    store.save_call_totals([("2024-11-01", "garage_status", 1, 0, [1] + [0] * 11)], "")
    store.save_call_totals([("2025-12-01", "garage_status", 1, 0, [1] + [0] * 11)], "")
    opened(tmp_path, store, clock)
    assert [row["day"] for row in store.call_totals("2024-01-01", "2026-12-31")] == ["2025-12-01"]


# -- the middleware ------------------------------------------------------------------------


async def test_every_tool_call_is_written_by_the_middleware_once(tmp_path, store, clock, monkeypatch):
    from claude_voice import server

    log = opened(tmp_path, store, clock)
    monkeypatch.setattr(server, "CALLS", [log])
    ctx = SimpleNamespace(
        method="tools/call", params={"name": "garage_status", "arguments": {"pin": "1234"}}, request_id=1
    )

    async def answers(_ctx):
        return {"content": [{"type": "text", "text": "closed, pin 1234"}]}

    async def breaks(_ctx):
        raise RuntimeError("the pin 1234 was wrong")

    await server.log_requests(ctx, answers)
    with pytest.raises(RuntimeError):
        await server.log_requests(ctx, breaks)
    await server.log_requests(SimpleNamespace(method="resources/read", params={}, request_id=2), answers)
    written = lines(log)
    assert [(line["tool"], line["outcome"]) for line in written] == [
        ("garage_status", "ok"),
        ("garage_status", "error"),
    ]
    assert "1234" not in log.path.read_text(encoding="utf-8")
    assert log.running() == []

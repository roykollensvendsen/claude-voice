"""The bridge's call log: one line per tool call, and daily totals, never what was said."""

from __future__ import annotations

import asyncio
import contextlib
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


def now(fn) -> None:
    fn()


def opened(tmp_path: Path, store: Store, clock: Clock, **kwargs) -> calllog.CallLog:
    kwargs.setdefault("later", now)
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
    log = calllog.CallLog(tmp_path / "state" / "calls.jsonl", store, clock=clock, later=now)  # the first line makes it
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


# -- the review notes on the capture -------------------------------------------------------


def test_a_call_counted_while_a_flush_copies_is_in_the_database_or_recovered(tmp_path, store, clock, monkeypatch):
    """A call that ends while a flush runs is counted exactly once, whichever side of the copy it lands."""
    log = opened(tmp_path, store, clock)
    started = log.start("garage_status", "voice_app", None)
    real = log.totals.save_call_totals

    def a_call_ends_meanwhile(rows, flushed_to):
        clock.go(0.0004)  # less than a millisecond later
        log.end(started, "ok", None, 5)
        real(rows, flushed_to)

    monkeypatch.setattr(log.totals, "save_call_totals", a_call_ends_meanwhile)
    log.flush()
    monkeypatch.undo()
    again = opened(tmp_path, store, clock)
    assert [row["calls"] for row in again.today_rows()] == [1]


def test_calls_after_the_last_flush_are_recovered_on_every_day_since(tmp_path, store):
    late = Clock("2026-11-16T22:59:00+00:00")  # 23.59 in Oslo
    log = opened(tmp_path, store, late)
    log.flush()
    call(log, late)  # the last call of 16.11, never flushed
    late.go(3600)  # the bridge comes back at 00.59 on 17.11
    opened(tmp_path, store, late)
    assert [(row["day"], row["calls"]) for row in store.call_totals("2026-11-16", "2026-11-17")] == [("2026-11-16", 1)]


def test_the_totals_are_written_off_the_calling_thread(tmp_path, store, clock):
    import threading

    log = calllog.CallLog(tmp_path / "calls.jsonl", store, clock=clock)
    seen = []
    real = store.save_call_totals
    done = threading.Event()

    def save(rows, flushed_to):
        seen.append(threading.current_thread())
        real(rows, flushed_to)
        done.set()

    store.save_call_totals = save
    clock.go(31)
    call(log, clock)
    assert done.wait(5)
    assert seen and seen[0] is not threading.current_thread()


def test_the_file_work_is_one_writer_at_a_time_without_a_file_lock(tmp_path, store, clock, monkeypatch):
    import threading

    monkeypatch.setattr(calllog.sys, "platform", "win32")  # no flock, as on Windows
    log = opened(tmp_path, store, clock, prune_every=7)
    threads = [threading.Thread(target=lambda: [call(log, clock) for _ in range(50)]) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(lines(log)) == 200


# -- what the health page reads ------------------------------------------------------------

OFFERED = ["garage_status", "weather_now", "whats_new", "ask_active_session"]


def test_the_totals_give_median_and_p95_per_day_and_tool_and_the_tools_with_no_calls(tmp_path, store, clock):
    store.save_call_totals([("2026-11-16", "weather_now", 2, 1, [0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 1])], "")
    log = opened(tmp_path, store, clock)
    for ms in (30, 40, 120, 900):
        call(log, clock, ms=ms)
    out = log.totals_between("2026-11-16", "2026-11-17", OFFERED)
    assert out["today"] == "2026-11-17"
    assert out["rows"] == [
        {"day": "2026-11-16", "tool": "weather_now", "calls": 2, "errors": 1, "median_ms": 200, "p95_ms": 60_000},
        {"day": "2026-11-17", "tool": "garage_status", "calls": 4, "errors": 0, "median_ms": 50, "p95_ms": 1_000},
    ]
    assert out["unused"] == ["ask_active_session", "whats_new"]


def test_recent_lists_the_last_calls_newest_first_and_the_calls_running_now(tmp_path, store, clock):
    log = opened(tmp_path, store, clock)
    first = call(log, clock)
    second = call(log, clock, tool="weather_now")
    running = log.start("ask_active_session", "voice_app", "builder")
    log.start("whats_new", "claude_app", None)  # polling is never shown running
    out = log.recent(1)
    assert [c["id"] for c in out["calls"]] == [second] and first != second
    assert out["running"] == [
        {
            "id": running,
            "tool": "ask_active_session",
            "source": "voice_app",
            "caller": "builder",
            "at": out["running"][0]["at"],
        }
    ]


def test_a_days_calls_for_one_tool_are_read_from_the_lines(tmp_path, store, clock):
    log = opened(tmp_path, store, clock)
    mine = call(log, clock)
    call(log, clock, tool="weather_now")
    out = log.day_calls("2026-11-17", "garage_status", OFFERED)
    assert (out["day"], out["tool"], out["counted_only"]) == ("2026-11-17", "garage_status", False)
    assert [c["id"] for c in out["calls"]] == [mine]
    assert log.day_calls("2026-11-17", "whats_new", OFFERED)["counted_only"] is True


@pytest.mark.parametrize("day", ["17.11.2026", "2026-02-30", "../x", "2026-11-17T00:00"])
def test_a_day_that_is_not_a_real_date_as_yyyy_mm_dd_is_refused(tmp_path, store, clock, day):
    log = opened(tmp_path, store, clock)
    with pytest.raises(ValueError):
        log.day_calls(day, "garage_status", OFFERED)
    with pytest.raises(ValueError):
        log.totals_between(day, "2026-11-17", OFFERED)


def test_a_tool_the_bridge_does_not_offer_is_refused(tmp_path, store, clock):
    log = opened(tmp_path, store, clock)
    with pytest.raises(ValueError):
        log.day_calls("2026-11-17", "rm -rf", OFFERED)


def test_a_tool_no_longer_offered_can_still_be_read_for_its_past_days(tmp_path, store, clock):
    log = opened(tmp_path, store, clock)
    gone = call(log, clock, tool="old_plugin_tool")
    assert [c["id"] for c in log.day_calls("2026-11-17", "old_plugin_tool", OFFERED)["calls"]] == [gone]


def test_a_damaged_line_is_skipped_when_a_day_is_read(tmp_path, store, clock):
    log = opened(tmp_path, store, clock)
    kept = call(log, clock)
    with log.path.open("a", encoding="utf-8") as out:
        out.write('{"tool":"garage_status"}\n[1,2]\n{"tool":"garage_status","ended_at":"junk"}\n')
    assert [c["id"] for c in log.day_calls("2026-11-17", "garage_status", OFFERED)["calls"]] == [kept]


def test_each_call_publishes_its_start_and_end_but_polling_publishes_nothing(tmp_path, store, clock):
    heard = []
    log = opened(tmp_path, store, clock, publish=lambda event, data: heard.append((event, data)))
    started = call(log, clock, caller="builder")
    call(log, clock, tool="whats_new")
    assert [event for event, _ in heard] == ["call_start", "call_end"]
    assert set(heard[0][1]) == {"id", "tool", "source", "caller", "at"} and heard[0][1]["id"] == started
    assert set(heard[1][1]) == {"id", "tool", "outcome", "error_class", "ms", "ended_at"}


async def test_a_call_ending_on_a_worker_thread_reaches_an_open_stream(tmp_path, store, clock):
    from claude_voice import api

    hub = api.EventHub()
    log = opened(tmp_path, store, clock, publish=hub.publish)
    with hub.subscribe() as queue:
        await asyncio.to_thread(call, log, clock)
        got = [await asyncio.wait_for(queue.get(), 2) for _ in range(2)]
    assert [event for event, _ in got] == ["call_start", "call_end"]


@contextlib.asynccontextmanager
async def signed_in_page(tmp_path, store, clock, monkeypatch):
    """The bridge's page API with the call log on, and a client signed in to it."""
    import httpx
    from fakes import FakeClaude

    from claude_voice import server
    from claude_voice.oauth import OAuthProvider
    from claude_voice.sessions import SessionManager

    secret = "s3cret-token-for-the-call-log-tests"
    (tmp_path / "home").mkdir()
    oauth = OAuthProvider(store, secret, "http://127.0.0.1:8811", static_token=secret)
    manager = SessionManager(store, project_root=tmp_path / "home", client_factory=FakeClaude())
    mcp = server.build_server(manager, oauth=oauth, conversations=lambda **k: [], live_dir=tmp_path / "live")
    log = opened(tmp_path, store, clock)
    monkeypatch.setattr(server, "CALLS", [log])
    transport = httpx.ASGITransport(app=server.build_app(mcp), client=("127.0.0.1", 4321))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://127.0.0.1:8811", headers={"Authorization": f"Bearer {secret}"}
    ) as c:
        yield c, log


async def test_the_three_reads_need_sign_in_and_refuse_a_bad_day_or_tool(tmp_path, store, clock, monkeypatch):
    async with signed_in_page(tmp_path, store, clock, monkeypatch) as (c, log):
        done = call(log, clock, tool="list_projects")
        assert (await c.get("/api/v1/calls/recent", headers={"Authorization": "Bearer wrong"})).status_code == 401
        recent = (await c.get("/api/v1/calls/recent?limit=5")).json()
        assert [x["id"] for x in recent["calls"]] == [done]
        totals = (await c.get("/api/v1/calls/totals?from=2026-11-17&to=2026-11-17")).json()
        assert totals["rows"][0]["tool"] == "list_projects"
        assert "session_recap" in totals["unused"]  # offered, never called
        day = (await c.get("/api/v1/calls/day/2026-11-17/list_projects")).json()
        assert [x["id"] for x in day["calls"]] == [done]
        for bad in (
            "/api/v1/calls/day/2026-11-17/no_such_tool",
            "/api/v1/calls/day/yesterday/list_projects",
            "/api/v1/calls/totals?from=2026-11-17&to=junk",
        ):
            assert (await c.get(bad)).status_code == 400, bad


@pytest.mark.parametrize(
    ("limit", "status"), [("1", 200), ("100", 200), ("0", 400), ("101", 400), ("x", 400), ("-1", 400)]
)
async def test_the_recent_calls_limit_is_a_whole_number_from_1_to_100(
    tmp_path, store, clock, monkeypatch, limit, status
):
    async with signed_in_page(tmp_path, store, clock, monkeypatch) as (c, _):
        assert (await c.get(f"/api/v1/calls/recent?limit={limit}")).status_code == status


def test_a_span_of_more_than_400_days_is_refused(tmp_path, store, clock):
    log = opened(tmp_path, store, clock)
    assert log.totals_between("2025-10-13", "2026-11-17", OFFERED)["rows"] == []  # 400 days
    with pytest.raises(ValueError):
        log.totals_between("2025-10-12", "2026-11-17", OFFERED)


def test_a_failed_save_keeps_the_counts_and_is_logged(tmp_path, store, clock, monkeypatch, caplog):
    import logging

    late = Clock("2026-11-16T22:59:00+00:00")  # 23.59 in Oslo
    log = opened(tmp_path, store, late)
    call(log, late, tool="whats_new")  # a polling count, which no line can bring back
    late.go(120)  # past midnight
    monkeypatch.setattr(store, "save_call_totals", lambda rows, flushed_to: (_ for _ in ()).throw(OSError("disk")))
    with caplog.at_level(logging.WARNING, logger="claude_voice.calllog"), pytest.raises(OSError):
        log.flush()
    assert any("could not save" in r.getMessage() for r in caplog.records)
    monkeypatch.undo()
    log.flush()
    assert [(r["day"], r["calls"]) for r in store.call_totals("2026-11-16", "2026-11-16")] == [("2026-11-16", 1)]

"""The activity view: per-session numbers read from transcripts and /proc, only while switched on."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from claude_voice import activity
from claude_voice.config import ConfigError
from claude_voice.config import load as load_config
from claude_voice.store import Store
from claude_voice.transcript_files import transcript_path

T0 = datetime(2026, 11, 18, 17, 0, tzinfo=UTC).timestamp()
PLANTED = ("sk-ant-PLANTED-SECRET", "/home/someone/very/secret/path", "hunter2-password")


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> float:
        return self.now

    def go(self, seconds: float) -> None:
        self.now += seconds


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).isoformat().replace("+00:00", "Z")


# -- a fake machine: transcripts, a sessions registry, /proc --------------------------


class Machine:
    """Transcripts in a projects folder, running sessions, and a /proc to read them from."""

    def __init__(self, tmp: Path, clock: Clock) -> None:
        self.tmp, self.clock = tmp, clock
        self.projects = tmp / "projects"
        self.proc = tmp / "proc"
        self.proc.mkdir()
        (self.proc / "uptime").write_text("100000.00 0\n")
        self.rows: list[dict[str, Any]] = []
        self.places: dict[str, tuple[str, str]] = {}
        self.listed = True  # whether the kernel keeps each process's list of children

    def session(self, name: str, org: str | None, role: str = "work", pid: int = 4000, status: str = "busy") -> Path:
        sid = f"sid-{name}"
        cwd = str(self.tmp / "work" / name)
        self.rows.append(
            {"pid": pid, "sessionId": sid, "cwd": cwd, "name": name, "status": status, "statusUpdatedAt": 0}
        )
        if org:
            self.places[name] = (org, role)
        self.process(pid, 1, "claude", cpu=100)
        path = transcript_path(self.projects, cwd, sid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return path

    def process(self, pid: int, ppid: int, comm: str, cpu: int = 0, rss_pages: int = 25600, start: int = 0) -> None:
        folder = self.proc / str(pid)
        folder.mkdir(exist_ok=True)
        # after "(comm)": state ppid pgrp session tty tpgid flags minflt cminflt majflt cmajflt
        # utime stime cutime cstime priority nice threads itrealvalue starttime vsize rss
        fields = ["S", ppid, 0, 0, 0, 0, 0, 0, 0, 0, 0, cpu, 0, 0, 0, 20, 0, 1, 0, start, 0, rss_pages]
        (folder / "stat").write_text(f"{pid} ({comm}) " + " ".join(map(str, fields)) + "\n")
        if not self.listed:
            return
        (folder / "task" / str(pid)).mkdir(parents=True, exist_ok=True)
        (folder / "task" / str(pid) / "children").touch()
        if ppid > 1:
            with (self.proc / str(ppid) / "task" / str(ppid) / "children").open("a") as f:
                f.write(f"{pid} ")

    def place(self, row: dict[str, Any]) -> tuple[str, str] | None:
        return self.places.get(str(row.get("name")))


def write(path: Path, *entries: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def prompt(at: float, text: str = "please do the thing") -> dict[str, Any]:
    return {"type": "user", "timestamp": iso(at), "message": {"role": "user", "content": text}}


def use(at: float, call: str, name: str = "Bash", msg: str = "m1", **usage: int) -> dict[str, Any]:
    tool_input = {"command": "cd /x && FOO=1 pytest -q tests", "description": "run the tests"}
    return {
        "type": "assistant",
        "timestamp": iso(at),
        "message": {
            "id": msg,
            "usage": {"input_tokens": 10, "output_tokens": 5, **usage},
            "content": [{"type": "tool_use", "id": call, "name": name, "input": tool_input}],
        },
    }


def result(at: float, call: str, text: str = "ok") -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": iso(at),
        "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call, "content": text}]},
    }


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def machine(tmp_path: Path, clock: Clock) -> Machine:
    return Machine(tmp_path, clock)


def make(tmp_path: Path, machine: Machine, clock: Clock, **kwargs: Any) -> activity.Activity:
    kwargs.setdefault("orgs", ("voice", "quest", "guarded"))
    kwargs.setdefault("phone_orgs", ("voice", "quest"))
    return activity.Activity(
        Store(tmp_path / "bridge.db"),
        tmp_path / "activity.db",
        live=lambda: machine.rows,
        place=machine.place,
        projects_dir=machine.projects,
        proc=activity.ProcTree(machine.proc, hz=100, page_kb=4),
        clock=clock,
        **kwargs,
    )


@pytest.fixture
def opened(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    real = activity._open

    def spy(path: Path) -> Any:
        seen.append(str(path))
        return real(path)

    monkeypatch.setattr(activity, "_open", spy)
    return seen


def on(act: activity.Activity, duration: str = "8h") -> None:
    act.switch({"on": True, "for": duration})


# -- off reads nothing ----------------------------------------------------------------


def test_activity_reads_nothing_while_off(tmp_path, machine, clock, opened):
    path = machine.session("worker", "voice")
    write(path, prompt(T0), use(T0 + 1, "c1"))
    act = make(tmp_path, machine, clock)
    for _ in range(3):
        act.tick()
        clock.go(10)
    assert opened == []
    assert not (tmp_path / "activity.db").exists()
    assert act.now("all", local=True) is None


def test_switching_on_reads_the_sessions(tmp_path, machine, clock, opened):
    path = machine.session("worker", "voice")
    write(path, prompt(T0), use(T0 + 1, "c1"))
    act = make(tmp_path, machine, clock)
    on(act)
    act.tick()
    assert str(path) in opened
    assert act.state(local=True)["on"] is True


# -- what a transcript says --------------------------------------------------------------


def test_a_tool_call_lasts_from_its_use_to_its_result(tmp_path, machine, clock):
    path = machine.session("worker", "voice")
    act = make(tmp_path, machine, clock)
    on(act)
    act.tick()
    write(path, prompt(T0 + 1), use(T0 + 2, "c1"), result(T0 + 32, "c1"))
    clock.go(40)
    act.tick()
    points = act.series("session:worker", "1h", local=True)["points"]
    assert sum(p["tool_ms"] for p in points) == 30_000
    assert sum(p["n_run"] for p in points) == 1


def test_a_use_without_a_result_is_the_current_call(tmp_path, machine, clock):
    path = machine.session("worker", "voice")
    act = make(tmp_path, machine, clock)
    on(act)
    write(path, prompt(T0), use(T0 + 2, "c1"))
    clock.go(10)
    act.tick()
    row = act.now("all", local=True)["sessions"][0]
    assert row["call"]["kind"] == "run"
    assert row["call"]["tool"] == "Bash"
    assert row["call"]["program"] == "pytest"
    assert row["call"]["started"] == activity.local_iso(T0 + 2)
    assert row["turn_started"] == activity.local_iso(T0)


def test_a_messages_tokens_are_counted_once(tmp_path, machine, clock):
    path = machine.session("worker", "voice")
    act = make(tmp_path, machine, clock)
    on(act)
    same = use(T0 + 1, "c1", msg="m1", cache_read_input_tokens=100)
    write(path, same, same, result(T0 + 2, "c1"))
    clock.go(10)
    act.tick()
    points = act.series("session:worker", "1h", local=True)["points"]
    assert sum(p["tok_in"] for p in points) == 110
    assert sum(p["tok_out"] for p in points) == 5


def test_only_the_bytes_appended_since_the_last_look_are_read(tmp_path, machine, clock):
    path = machine.session("worker", "voice")
    act = make(tmp_path, machine, clock)
    on(act)
    write(path, use(T0 + 1, "c1", msg="m1"), result(T0 + 2, "c1"))
    clock.go(10)
    act.tick()
    write(path, use(T0 + 11, "c2", msg="m2"), result(T0 + 12, "c2"))
    clock.go(10)
    act.tick()
    points = act.series("session:worker", "1h", local=True)["points"]
    assert sum(p["n_run"] for p in points) == 2
    assert sum(p["tok_out"] for p in points) == 10


def test_a_transcript_that_shrank_is_read_again_from_the_start(tmp_path, machine, clock):
    path = machine.session("worker", "voice")
    act = make(tmp_path, machine, clock)
    on(act)
    write(path, use(T0 + 1, "c1", msg="m1"), result(T0 + 2, "c1"), use(T0 + 3, "c2", msg="m2"))
    clock.go(10)
    act.tick()
    path.write_text("")
    write(path, use(T0 + 11, "c3", msg="m3"))
    clock.go(10)
    act.tick()
    row = act.now("all", local=True)["sessions"][0]
    assert row["call"]["started"] == activity.local_iso(T0 + 11)


def test_a_helper_agents_transcript_counts_for_its_session(tmp_path, machine, clock):
    path = machine.session("worker", "voice")
    helper = path.parent / path.stem / "subagents" / "agent-1.jsonl"
    helper.parent.mkdir(parents=True)
    act = make(tmp_path, machine, clock)
    on(act)
    write(helper, use(T0 + 1, "h1", name="Read", msg="hm1"), result(T0 + 2, "h1"))
    clock.go(10)
    act.tick()
    points = act.series("session:worker", "1h", local=True)["points"]
    assert sum(p["n_read"] for p in points) == 1


def test_lines_from_before_the_switch_are_not_counted(tmp_path, machine, clock):
    path = machine.session("worker", "voice")
    write(path, use(T0 - 600, "old", msg="old"), result(T0 - 590, "old"))
    act = make(tmp_path, machine, clock)
    on(act)
    clock.go(10)
    act.tick()
    points = act.series("session:worker", "1h", local=True)["points"]
    assert sum(p["n_run"] + p["tok_in"] + p["tool_ms"] for p in points) == 0


# -- kinds and program names ------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "kind"),
    [
        ("Read", "read"),
        ("Grep", "read"),
        ("Glob", "read"),
        ("LS", "read"),
        ("Edit", "write"),
        ("Write", "write"),
        ("NotebookEdit", "write"),
        ("Bash", "run"),
        ("mcp__claude-voice__health", "mcp"),
        ("Agent", "agent"),
        ("Task", "agent"),
        ("WebFetch", "other"),
    ],
)
def test_each_tool_has_a_kind(tool, kind):
    assert activity.kind_of(tool) == kind


@pytest.mark.parametrize(
    ("command", "program"),
    [
        ("git status", "git"),
        ("cd /a/b && git log", "git"),
        ("FOO=1 BAR=2 uv run pytest", "uv"),
        ("/usr/bin/python3 -I x.py", "python"),
        ("pytest -q", "pytest"),
        ("cd x && cd y && make all", "make"),
        ("  gh pr list", "gh"),
        ("my-secret-internal-tool --flag", "other"),
        ("", "other"),
    ],
)
def test_a_program_outside_the_fixed_list_is_other(command, program):
    assert activity.program_of(command) == program


# -- /proc ------------------------------------------------------------------------------------


def test_a_sessions_cpu_includes_its_children(tmp_path, machine):
    machine.process(4000, 1, "claude", cpu=100, rss_pages=256)
    machine.process(4001, 4000, "bash", cpu=50, rss_pages=256)
    machine.process(4002, 4001, "pytest", cpu=25, rss_pages=256, start=100)
    machine.process(5000, 1, "unrelated", cpu=999, rss_pages=99999)
    seen = activity.ProcTree(machine.proc, hz=100, page_kb=4).sample([4000])[4000]
    assert seen.cpu_ms == 1750
    assert seen.rss_mb == 3
    assert seen.child == ("other", 100000)  # bash started first; its name is not on the list


def test_without_children_lists_no_other_process_is_read(tmp_path, machine, opened):
    machine.listed = False
    machine.process(4000, 1, "claude", cpu=100, rss_pages=256)
    machine.process(4001, 4000, "git", cpu=50, rss_pages=256)
    machine.process(6000, 1, "claude", cpu=1, rss_pages=256)
    machine.process(6001, 6000, "make", cpu=1, rss_pages=256)
    seen = activity.ProcTree(machine.proc, hz=100, page_kb=4).sample([4000])
    assert seen[4000].cpu_ms == 1000  # its children are unknown here, so only its own
    assert seen[4000].child is None
    own = str(machine.proc / "4000")
    others = [p for p in opened if p.startswith(str(machine.proc) + os.sep) and not p.startswith(own)]
    assert others == [str(machine.proc / "uptime")]


def test_where_there_is_no_proc_no_process_is_read(tmp_path, machine, clock, monkeypatch):
    monkeypatch.setattr(activity.sys, "platform", "win32")
    monkeypatch.delattr(activity.os, "sysconf", raising=False)  # Windows has none to remove
    path = machine.session("worker", "voice")
    write(path, prompt(T0), use(T0 + 1, "c1"))
    act = activity.Activity(
        Store(tmp_path / "bridge.db"),
        tmp_path / "activity.db",
        live=lambda: machine.rows,
        place=machine.place,
        orgs=("voice",),
        projects_dir=machine.projects,
        clock=clock,
    )
    on(act)
    act.tick()
    (row,) = act.now("all", local=True)["sessions"]
    assert row["call"]["kind"] == "run"
    assert (row["cpu_pct"], row["rss_mb"], row["child"]) == (0.0, 0, None)  # the contract keeps numbers
    act.switch({"on": False})
    assert act.state(local=True)["on"] is False


def test_the_longest_running_child_is_named_from_the_fixed_list(tmp_path, machine):
    machine.process(4000, 1, "claude")
    machine.process(4001, 4000, "pytest", start=500)
    machine.process(4002, 4000, "git", start=900_000)
    seen = activity.ProcTree(machine.proc, hz=100, page_kb=4).sample([4000])[4000]
    assert seen.child == ("pytest", 100000 - 5)


# -- yellow and red ---------------------------------------------------------------------------


def test_a_long_call_whose_cpu_stands_still_is_red(tmp_path, machine, clock):
    path = machine.session("worker", "voice")
    act = make(tmp_path, machine, clock)
    on(act)
    write(path, prompt(T0), use(T0 + 1, "c1"))
    states = {}
    for second in range(0, 11 * 60, 10):
        clock.now = T0 + second
        act.tick()
        states[second] = act.now("all", local=True)["sessions"][0]["state"]
    assert states[4 * 60] == "ok"
    assert states[6 * 60] == "yellow"
    assert states[10 * 60 + 50] == "red"


def test_a_long_call_whose_cpu_grows_stays_yellow(tmp_path, machine, clock):
    path = machine.session("worker", "voice")
    act = make(tmp_path, machine, clock)
    on(act)
    write(path, prompt(T0), use(T0 + 1, "c1"))
    for second in range(0, 11 * 60, 10):
        clock.now = T0 + second
        machine.process(4000, 1, "claude", cpu=100 + second)
        act.tick()
    assert act.now("all", local=True)["sessions"][0]["state"] == "yellow"


# -- the status band --------------------------------------------------------------------------


def test_status_time_is_counted_per_minute(tmp_path, machine, clock):
    machine.session("worker", "voice", status="waiting")
    act = make(tmp_path, machine, clock)
    on(act)
    for _ in range(7):
        act.tick()
        clock.go(10)
    points = act.series("session:worker", "1h", local=True)["points"]
    assert sum(p["waiting_s"] for p in points) == 60
    assert sum(p["busy_s"] for p in points) == 0


# -- nothing but numbers is kept ---------------------------------------------------------------


def test_no_text_from_a_transcript_is_kept(tmp_path, machine, clock):
    path = machine.session("worker", "voice")
    act = make(tmp_path, machine, clock)
    on(act)
    secret_use = use(T0 + 1, "c1")
    secret_use["message"]["content"][0]["input"] = {"command": f"{PLANTED[2]} {PLANTED[1]}", "x": PLANTED[0]}
    secret_use["message"]["content"].append({"type": "text", "text": PLANTED[0]})
    write(path, prompt(T0, PLANTED[0]), secret_use, result(T0 + 2, "c1", PLANTED[1]), use(T0 + 3, "c2"))
    clock.go(10)
    act.tick()
    act.flush()
    answers = json.dumps(
        [act.state(local=True), act.now("all", local=True), act.series("all", "1h", local=True), repr(vars(act))]
    )
    stored = (tmp_path / "activity.db").read_bytes()
    for planted in PLANTED:
        assert planted not in answers
        assert planted.encode() not in stored


@pytest.mark.skipif(sys.platform == "win32", reason="Windows keeps no owner-only mode bits")
def test_the_activity_file_is_the_owners_alone(tmp_path, machine, clock):
    machine.session("worker", "voice")
    act = make(tmp_path, machine, clock)
    on(act)
    act.tick()
    act.flush()
    assert stat.S_IMODE(os.stat(tmp_path / "activity.db").st_mode) == 0o600


# -- the switch ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("duration", "seconds"), [("1h", 3600), ("8h", 8 * 3600), ("24h", 86400), ("7d", 7 * 86400)])
def test_the_switch_turns_itself_off_when_its_time_ends(tmp_path, machine, clock, duration, seconds, opened):
    machine.session("worker", "voice")
    act = make(tmp_path, machine, clock)
    on(act, duration)
    assert act.state(local=True)["until"] == activity.local_iso(T0 + seconds)
    clock.go(seconds - 1)
    assert act.state(local=True)["on"] is True
    clock.go(1)
    assert act.state(local=True) | {"durations": None, "scopes": None} == {
        "on": False,
        "until": None,
        "durations": None,
        "scopes": None,
    }
    opened.clear()
    act.tick()
    assert opened == []


def test_the_switch_defaults_to_eight_hours(tmp_path, machine, clock):
    act = make(tmp_path, machine, clock)
    act.switch({"on": True})
    assert act.state(local=True)["until"] == activity.local_iso(T0 + 8 * 3600)
    assert act.state(local=True)["durations"] == ["1h", "8h", "24h", "7d"]


@pytest.mark.parametrize("body", [None, [], {"on": "yes"}, {"on": True, "for": "3h"}, {"on": True, "other": 1}])
def test_a_switch_that_does_not_fit_is_refused(tmp_path, machine, clock, body):
    act = make(tmp_path, machine, clock)
    with pytest.raises(ValueError):
        act.switch(body)
    assert act.state(local=True)["on"] is False


def test_the_switch_survives_a_restart(tmp_path, machine, clock):
    on(make(tmp_path, machine, clock))
    assert make(tmp_path, machine, clock).state(local=True)["on"] is True


# -- the series ----------------------------------------------------------------------------------


@pytest.mark.parametrize(("window", "bucket"), [("1h", 60), ("6h", 300), ("24h", 900), ("7d", 3600)])
def test_the_bucket_grows_with_the_window(tmp_path, machine, clock, window, bucket):
    path = machine.session("worker", "voice")
    act = make(tmp_path, machine, clock)
    on(act)
    write(path, use(T0 + 1, "c1", msg="m1"), result(T0 + 2, "c1"), use(T0 + 61, "c2", msg="m2"))
    clock.go(70)
    act.tick()
    out = act.series("session:worker", window, local=True)
    assert out["bucket_s"] == bucket
    assert out["window"] == window
    assert all(datetime.fromisoformat(p["t"]).timestamp() % bucket == 0 for p in out["points"])
    assert sum(p["n_run"] for p in out["points"]) == 2
    assert len(out["points"]) == (2 if bucket == 60 else 1)


def test_not_recording_gaps_are_not_zeros(tmp_path, machine, clock):
    machine.session("worker", "voice")
    act = make(tmp_path, machine, clock)
    on(act)
    act.tick()
    clock.go(600)
    act.tick()
    act.switch({"on": False})
    clock.go(1200)
    on(act)
    act.tick()
    clock.go(60)
    act.tick()
    spans = act.series("all", "1h", local=True)["recording"]
    assert [(s["from"], s["to"]) for s in spans] == [
        (activity.local_iso(T0), activity.local_iso(T0 + 600)),
        (activity.local_iso(T0 + 1800), activity.local_iso(T0 + 1860)),
    ]


def test_a_bucket_sums_its_sessions(tmp_path, machine, clock):
    a = machine.session("a", "voice", pid=4000)
    b = machine.session("b", "voice", pid=4100)
    c = machine.session("c", "quest", pid=4200)
    act = make(tmp_path, machine, clock)
    on(act)
    for path, msg in ((a, "ma"), (b, "mb"), (c, "mc")):
        write(path, use(T0 + 1, "x", msg=msg), result(T0 + 2, "x"))
    clock.go(10)
    act.tick()
    org = act.series("org:voice", "1h", local=True)["points"]
    one = [act.series(f"session:{n}", "1h", local=True)["points"] for n in ("a", "b")]
    assert sum(p["tok_out"] for p in org) == sum(p["tok_out"] for s in one for p in s) == 10
    assert sum(p["rss_mb_max"] for p in org) == sum(p["rss_mb_max"] for s in one for p in s) == 200
    assert sum(p["tok_out"] for p in act.series("all", "1h", local=True)["points"]) == 15


def test_an_unknown_window_is_refused(tmp_path, machine, clock):
    act = make(tmp_path, machine, clock)
    on(act)
    with pytest.raises(ValueError):
        act.series("all", "2h", local=True)


def test_an_unknown_scope_is_not_found(tmp_path, machine, clock):
    act = make(tmp_path, machine, clock)
    on(act)
    assert act.series("org:nobody", "1h", local=True) is None
    assert act.series("session:nobody", "1h", local=True) is None
    assert act.series("nonsense", "1h", local=True) is None


# -- keeping and forgetting ---------------------------------------------------------------------------


def test_rows_older_than_seven_days_are_deleted(tmp_path, machine, clock):
    path = machine.session("worker", "voice")
    act = make(tmp_path, machine, clock)
    on(act, "7d")
    write(path, use(T0 + 1, "c1"), result(T0 + 2, "c1"))
    clock.go(10)
    act.tick()
    act.flush()
    clock.go(8 * 86400)
    act.daily()
    rows = sqlite3.connect(tmp_path / "activity.db").execute("SELECT COUNT(*) FROM minutes").fetchone()[0]
    assert rows == 0


def test_the_daily_check_runs_while_off_and_opens_no_transcript(tmp_path, machine, clock, opened):
    path = machine.session("worker", "voice")
    act = make(tmp_path, machine, clock)
    on(act, "1h")
    write(path, use(T0 + 1, "c1"), result(T0 + 2, "c1"))
    clock.go(10)
    act.tick()
    act.switch({"on": False})
    opened.clear()
    clock.go(8 * 86400)
    act.tick()  # off: only the daily delete runs
    assert opened == []
    rows = sqlite3.connect(tmp_path / "activity.db").execute("SELECT COUNT(*) FROM minutes").fetchone()[0]
    assert rows == 0


def test_clearing_empties_the_file(tmp_path, machine, clock):
    path = machine.session("worker", "voice")
    act = make(tmp_path, machine, clock)
    on(act)
    write(path, use(T0 + 1, "c1"), result(T0 + 2, "c1"))
    clock.go(10)
    act.tick()
    act.clear()
    assert act.series("all", "1h", local=True)["points"] == []


def test_a_look_and_a_question_never_run_at_once(tmp_path, machine, clock):
    machine.session("worker", "voice")
    act = make(tmp_path, machine, clock)
    on(act)
    waited: list[bool] = []
    questions: list[threading.Thread] = []
    real = act.proc.sample

    def sample(pids: Any) -> Any:
        question = threading.Thread(target=act.series, args=("all", "1h", True))
        question.start()
        question.join(0.3)
        waited.append(question.is_alive())  # still waiting for the look to end
        questions.append(question)
        return real(pids)

    act.proc.sample = sample  # type: ignore[method-assign]
    act.tick()
    questions[0].join(5)
    assert waited == [True]
    assert not questions[0].is_alive()


def test_a_session_row_that_is_not_plain_is_skipped(tmp_path, machine, clock, opened):
    good = machine.session("worker", "voice", pid=4000)
    write(good, use(T0 + 1, "c1"))
    machine.session("nopid", "voice", pid=4100)
    del machine.rows[-1]["pid"]
    outside = machine.session("escape", "voice", pid=4200)
    machine.rows[-1]["sessionId"] = "../../outside"
    act = make(tmp_path, machine, clock)
    on(act)
    act.tick()
    assert str(good) in opened
    assert not any("outside" in p for p in opened)
    assert outside.exists()
    assert [s["name"] for s in act.now("all", local=True)["sessions"]] == ["worker"]


# -- which programmes, and where they may be shown -----------------------------------------------------


def test_a_programme_not_switched_on_is_never_read(tmp_path, machine, clock, opened):
    kept = machine.session("kept", "guarded", pid=4000)
    machine.session("worker", "voice", pid=4100)
    loose = machine.session("loose", None, pid=4200)
    act = make(tmp_path, machine, clock, orgs=("voice",), phone_orgs=("voice",))
    on(act)
    act.tick()
    assert str(kept) not in opened
    assert str(loose) not in opened
    assert not any(p.startswith(str(machine.proc / "4000")) for p in opened)
    assert [s["name"] for s in act.now("all", local=True)["sessions"]] == ["worker"]
    assert act.series("org:guarded", "1h", local=True) is None


def test_a_programme_kept_off_the_phone_is_served_only_here(tmp_path, machine, clock):
    path = machine.session("kept", "guarded")
    act = make(tmp_path, machine, clock)
    on(act)
    write(path, use(T0 + 1, "c1"), result(T0 + 2, "c1"))
    clock.go(10)
    act.tick()
    assert act.series("org:guarded", "1h", local=False) is None
    assert act.series("session:kept", "1h", local=False) is None
    assert act.series("all", "1h", local=False)["points"] == []
    assert act.now("all", local=False)["sessions"] == []
    assert "guarded" not in act.state(local=False)["scopes"]["orgs"]
    assert act.state(local=False)["scopes"]["sessions"] == []
    assert act.series("org:guarded", "1h", local=True)["points"] != []
    assert "guarded" in act.state(local=True)["scopes"]["orgs"]


def test_a_programme_switched_off_loses_its_rows(tmp_path, machine, clock):
    path = machine.session("kept", "guarded")
    act = make(tmp_path, machine, clock)
    on(act)
    write(path, use(T0 + 1, "c1"), result(T0 + 2, "c1"))
    clock.go(10)
    act.tick()
    act.flush()
    make(tmp_path, machine, clock, orgs=("voice",), phone_orgs=("voice",))
    orgs = sqlite3.connect(tmp_path / "activity.db").execute("SELECT DISTINCT org FROM minutes").fetchall()
    assert orgs == []


# -- the bridge's settings -------------------------------------------------------------------------------


def test_the_programmes_to_read_come_from_the_settings_file(tmp_path):
    file = tmp_path / "config.toml"
    file.write_text('[activity]\norgs = ["voice", "quest"]\nphone_orgs = ["voice"]\n')
    cfg = load_config(file, env={"HOME": str(tmp_path)})
    assert cfg.activity_orgs == ("voice", "quest")
    assert cfg.activity_phone_orgs == ("voice",)
    assert load_config(None, env={"HOME": str(tmp_path)}).activity_orgs == ()


def test_a_phone_programme_that_is_not_read_is_refused(tmp_path):
    file = tmp_path / "config.toml"
    file.write_text('[activity]\norgs = ["voice"]\nphone_orgs = ["quest"]\n')
    with pytest.raises(ConfigError):
        load_config(file, env={"HOME": str(tmp_path)})

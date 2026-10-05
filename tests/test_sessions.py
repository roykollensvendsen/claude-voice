import pytest
from fakes import Boom, FakeClaude, Pause, init, result, say, use_tool

from claude_voice.sessions import SessionBusy, SessionClosed, SessionManager
from claude_voice.store import Store


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "src"
    (r / "app").mkdir(parents=True)
    (r / "lib").mkdir()
    return r


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return Store(tmp_path / "bridge.db", clock=clock)


def manager(store, root, claude, clock=None):
    return SessionManager(
        store, project_root=root, client_factory=claude, clock=clock or store.clock
    )


def kinds(store, sid):
    return [e["kind"] for e in store.events(sid, limit=500)]


async def test_projects_must_be_existing_directories_under_the_root(store, root, tmp_path):
    m = manager(store, root, FakeClaude())

    assert m.create(str(root / "app"))["project_path"] == str(root / "app")
    with pytest.raises(ValueError, match="under"):
        m.create(str(tmp_path))
    with pytest.raises(ValueError, match="under"):
        m.create(str(root / "app" / ".." / ".."))
    with pytest.raises(ValueError, match="not a directory"):
        m.create(str(root / "missing"))


async def test_relative_project_names_resolve_against_the_root(store, root):
    m = manager(store, root, FakeClaude())
    assert m.create("app")["project_path"] == str(root / "app")


async def test_a_turn_records_what_claude_did_and_returns_to_idle(store, root):
    claude = FakeClaude(
        [
            init("c-1"),
            say("Looking."),
            use_tool("Read", {"file_path": "a.py"}),
            result("Done.", "c-1"),
        ]
    )
    m = manager(store, root, claude)
    s = m.create("app")

    started = await m.send(s["id"], "fix the bug")
    assert started["status"] == "running"
    await m.wait(s["id"])

    assert claude.clients[0].prompts == ["fix the bug"]
    assert claude.clients[0].options.cwd == str(root / "app")
    assert claude.clients[0].options.resume is None
    assert kinds(store, s["id"]) == ["prompt", "text", "tool_use", "result"]
    got = store.get_session(s["id"])
    assert got["status"] == "idle"
    assert got["claude_session_id"] == "c-1"


async def test_follow_up_turn_resumes_the_same_claude_conversation(store, root):
    claude = FakeClaude([init("c-1"), result("one", "c-1")], [result("two", "c-1")])
    m = manager(store, root, claude)
    s = m.create("app")

    await m.send(s["id"], "first")
    await m.wait(s["id"])
    await m.send(s["id"], "second")
    await m.wait(s["id"])

    assert claude.clients[1].options.resume == "c-1"


async def test_claude_session_id_is_kept_even_if_the_turn_is_cut_short(store, root):
    pause = Pause()
    m = manager(store, root, FakeClaude([init("c-9"), pause]))
    s = m.create("app")

    await m.send(s["id"], "long job")
    await pause.reached.wait()
    assert store.get_session(s["id"])["claude_session_id"] == "c-9"
    await m.cancel(s["id"])


async def test_a_restarted_bridge_resumes_the_stored_conversation(store, root, tmp_path, clock):
    m = manager(store, root, FakeClaude([init("c-1"), result("ok", "c-1")]))
    s = m.create("app")
    await m.send(s["id"], "first")
    await m.wait(s["id"])

    claude = FakeClaude([result("again", "c-1")])
    m2 = manager(Store(tmp_path / "bridge.db", clock=clock), root, claude)
    await m2.send(s["id"], "continue")
    await m2.wait(s["id"])

    assert claude.clients[0].options.resume == "c-1"


async def test_sending_while_running_is_refused(store, root):
    pause = Pause()
    m = manager(store, root, FakeClaude([pause, result("ok", "c-1")]))
    s = m.create("app")

    await m.send(s["id"], "one")
    await pause.reached.wait()
    with pytest.raises(SessionBusy):
        await m.send(s["id"], "two")
    pause.release.set()
    await m.wait(s["id"])


async def test_cancel_interrupts_claude_and_leaves_session_usable(store, root):
    pause = Pause()
    claude = FakeClaude([init("c-1"), pause, say("never")], [result("after", "c-1")])
    m = manager(store, root, claude)
    s = m.create("app")

    await m.send(s["id"], "long job")
    await pause.reached.wait()
    out = await m.cancel(s["id"])

    assert out["cancelled"] is True
    assert claude.clients[0].interrupted.is_set()
    assert store.get_session(s["id"])["status"] == "cancelled"
    assert "text" not in kinds(store, s["id"])

    await m.send(s["id"], "again")
    await m.wait(s["id"])
    assert store.get_session(s["id"])["status"] == "idle"


async def test_cancel_when_nothing_runs_is_a_no_op(store, root):
    m = manager(store, root, FakeClaude())
    s = m.create("app")
    assert (await m.cancel(s["id"]))["cancelled"] is False


async def test_a_crash_inside_claude_is_recorded_as_an_error(store, root):
    m = manager(store, root, FakeClaude([Boom(RuntimeError("cli died"))]))
    s = m.create("app")

    await m.send(s["id"], "go")
    await m.wait(s["id"])

    got = store.get_session(s["id"])
    assert got["status"] == "error"
    assert "cli died" in got["last_error"]
    assert kinds(store, s["id"])[-1] == "error"


async def test_an_error_result_is_reported_as_an_error(store, root):
    m = manager(store, root, FakeClaude([result("rate limited", "c-1", is_error=True)]))
    s = m.create("app")

    await m.send(s["id"], "go")
    await m.wait(s["id"])

    got = store.get_session(s["id"])
    assert got["status"] == "error"
    assert got["last_error"] == "rate limited"


async def test_closed_sessions_refuse_work(store, root):
    m = manager(store, root, FakeClaude())
    s = m.create("app")
    m.close(s["id"])

    with pytest.raises(SessionClosed):
        await m.send(s["id"], "go")


async def test_closing_a_running_session_is_refused(store, root):
    pause = Pause()
    m = manager(store, root, FakeClaude([pause]))
    s = m.create("app")
    await m.send(s["id"], "go")
    await pause.reached.wait()

    with pytest.raises(SessionBusy):
        m.close(s["id"])
    await m.cancel(s["id"])


async def test_attach_continues_a_conversation_started_elsewhere(store, root):
    claude = FakeClaude([result("picked up", "term-7")])
    m = manager(store, root, claude)

    s = m.attach("term-7", "app", label="terminal work")
    await m.send(s["id"], "where were we?")
    await m.wait(s["id"])

    assert claude.clients[0].options.resume == "term-7"


async def test_claude_runs_with_normal_permissions_and_the_users_settings(store, root):
    claude = FakeClaude([result("ok", "c-1")])
    m = manager(store, root, claude)
    s = m.create("app")
    await m.send(s["id"], "go")
    await m.wait(s["id"])

    opts = claude.clients[0].options
    assert opts.permission_mode == "default"
    assert opts.setting_sources == ["user", "project", "local"]


async def test_recap_gives_a_short_spoken_summary_of_the_last_turn(store, root):
    claude = FakeClaude(
        [
            init("c-1"),
            use_tool("Edit", {"file_path": "a.py"}),
            use_tool("Bash", {"command": "pytest"}, "tu_2"),
            say("All tests pass now."),
            result("Fixed the off-by-one in a.py; tests pass.", "c-1"),
        ]
    )
    m = manager(store, root, claude)
    s = m.create("app", label="bugfix")
    await m.send(s["id"], "fix the off-by-one")
    await m.wait(s["id"])

    r = m.recap(s["id"])
    assert r["label"] == "bugfix"
    assert r["status"] == "idle"
    assert r["last_prompt"] == "fix the off-by-one"
    assert r["last_result"] == "Fixed the off-by-one in a.py; tests pass."
    assert r["tools_used"] == {"Edit": 1, "Bash": 1}
    assert r["last_error"] is None


async def test_recap_mid_turn_shows_latest_text(store, root):
    pause = Pause()
    m = manager(store, root, FakeClaude([say("Reading the code."), pause]))
    s = m.create("app")
    await m.send(s["id"], "go")
    await pause.reached.wait()

    r = m.recap(s["id"])
    assert r["status"] == "running"
    assert r["latest_text"] == "Reading the code."
    await m.cancel(s["id"])


async def test_fleet_recap_covers_only_recently_active_sessions(store, root, clock):
    m = manager(store, root, FakeClaude([result("old work", "c-1")], [result("new work", "c-2")]))
    old = m.create("app", label="old")
    await m.send(old["id"], "a")
    await m.wait(old["id"])
    clock.t += 3 * 3600
    new = m.create("lib", label="new")
    await m.send(new["id"], "b")
    await m.wait(new["id"])

    fleet = m.fleet_recap(since_minutes=60)
    assert [r["label"] for r in fleet["sessions"]] == ["new"]

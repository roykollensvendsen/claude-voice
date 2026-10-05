import asyncio

import pytest
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny, ToolPermissionContext
from fakes import Call, FakeClaude, Pause, init, result

from claude_voice.approvals import ApprovalBroker, ApprovalNotFound
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


@pytest.fixture
def root(tmp_path):
    (tmp_path / "src" / "app").mkdir(parents=True)
    return tmp_path / "src"


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "bridge.db")


def ask(tool, tool_input, outcomes):
    """Script step: Claude asks permission for a tool; the answer lands in `outcomes`."""

    async def step(options):
        outcomes.append(await options.can_use_tool(tool, tool_input, ToolPermissionContext()))

    return Call(step)


def setup(store, root, *scripts, timeout=60.0):
    broker = ApprovalBroker(store, timeout_seconds=timeout)
    m = SessionManager(store, root, client_factory=FakeClaude(*scripts), approvals=broker)
    return m, broker


async def until_pending(broker, n=1):
    for _ in range(200):
        if len(broker.pending()) >= n:
            return broker.pending()
        await asyncio.sleep(0.01)
    raise AssertionError("no approval request arrived")


async def test_read_only_tools_are_allowed_without_asking(store, root):
    outcomes = []
    m, broker = setup(
        store, root, [ask("Read", {"file_path": "a.py"}, outcomes), result("ok", "c")]
    )
    s = m.create("app")
    await m.send(s["id"], "look")
    await m.wait(s["id"])

    assert isinstance(outcomes[0], PermissionResultAllow)
    assert broker.pending() == []


async def test_risky_tools_wait_for_approval_then_run(store, root):
    outcomes = []
    m, broker = setup(
        store,
        root,
        [init("c"), ask("Bash", {"command": "rm -rf build"}, outcomes), result("ok", "c")],
    )
    s = m.create("app")
    await m.send(s["id"], "clean")

    [req] = await until_pending(broker)
    assert req["session_id"] == s["id"]
    assert req["tool"] == "Bash"
    assert req["input"] == {"command": "rm -rf build"}
    assert m.recap(s["id"])["pending_approvals"] == [req]
    assert outcomes == []

    broker.approve(req["id"])
    await m.wait(s["id"])

    assert isinstance(outcomes[0], PermissionResultAllow)
    kinds = [e["kind"] for e in store.events(s["id"])]
    assert "approval_requested" in kinds and "approval_granted" in kinds


async def test_denied_tools_are_refused_with_the_reason(store, root):
    outcomes = []
    m, broker = setup(store, root, [ask("Write", {"file_path": "x"}, outcomes), result("ok", "c")])
    s = m.create("app")
    await m.send(s["id"], "write")

    [req] = await until_pending(broker)
    broker.deny(req["id"], "not that file")
    await m.wait(s["id"])

    assert isinstance(outcomes[0], PermissionResultDeny)
    assert outcomes[0].message == "not that file"


async def test_unanswered_requests_are_denied_after_the_timeout(store, root):
    outcomes = []
    m, broker = setup(store, root, [ask("Bash", {"command": "ls"}, outcomes)], timeout=0.05)
    s = m.create("app")
    await m.send(s["id"], "go")
    await m.wait(s["id"])

    assert isinstance(outcomes[0], PermissionResultDeny)
    assert "time" in outcomes[0].message
    assert broker.pending() == []


async def test_approval_ids_are_short_enough_to_say(store, root):
    outcomes = []
    m, broker = setup(store, root, [ask("Bash", {"command": "ls"}, outcomes), Pause()])
    s = m.create("app")
    await m.send(s["id"], "go")
    [req] = await until_pending(broker)

    assert len(req["id"]) <= 4
    await m.cancel(s["id"])


async def test_answering_twice_or_unknown_ids_fail(store, root):
    m, broker = setup(store, root, [ask("Bash", {"command": "ls"}, []), result("ok", "c")])
    s = m.create("app")
    await m.send(s["id"], "go")
    [req] = await until_pending(broker)
    broker.approve(req["id"])

    with pytest.raises(ApprovalNotFound):
        broker.approve(req["id"])
    with pytest.raises(ApprovalNotFound):
        broker.deny("zz")
    await m.wait(s["id"])


async def test_cancelling_a_session_withdraws_its_pending_requests(store, root):
    outcomes = []
    m, broker = setup(store, root, [ask("Bash", {"command": "ls"}, outcomes)])
    s = m.create("app")
    await m.send(s["id"], "go")
    await until_pending(broker)

    await m.cancel(s["id"])

    assert broker.pending() == []
    assert store.get_session(s["id"])["status"] == "cancelled"


async def test_pending_can_be_filtered_by_session(store, root):
    (root / "lib").mkdir()
    m, broker = setup(
        store,
        root,
        [ask("Bash", {"command": "a"}, []), result("ok", "c1")],
        [ask("Bash", {"command": "b"}, []), result("ok", "c2")],
    )
    a = m.create("app")
    b = m.create("lib")
    await m.send(a["id"], "go")
    await m.send(b["id"], "go")
    await until_pending(broker, 2)

    assert [r["input"]["command"] for r in broker.pending(b["id"])] == ["b"]
    for r in broker.pending():
        broker.approve(r["id"])
    await m.wait(a["id"])
    await m.wait(b["id"])

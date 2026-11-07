"""rename_session: a running session gets a new name, and its old one still reaches it."""

import json
import logging
import os

import pytest
from mcp.client import Client

from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store


class Recorder:
    def __init__(self):
        self.sent = []

    async def __call__(self, name, text):
        self.sent.append((name, text))
        return {"delivered": True}


class FakeTmux:
    """A tmux with one pane per process id given, typing into which renames like Claude Code."""

    def __init__(self, live, panes=None):
        self.live, self.panes, self.typed, self.renamed = live, panes or {}, [], []

    def pane(self, pid):
        return self.panes.get(pid)

    def type(self, pane, text):
        self.typed.append((pane, text))
        for f in self.live.glob("*.json"):
            d = json.loads(f.read_text())
            if self.panes.get(d["pid"], (None, None))[1] == pane and text.startswith("/rename "):
                f.write_text(json.dumps({**d, "name": text.removeprefix("/rename ")}))

    def rename(self, old, new):
        self.renamed.append((old, new))


@pytest.fixture
def root(tmp_path):
    (tmp_path / "src" / "math-quest").mkdir(parents=True)
    return tmp_path / "src"


def write(live, pid, sid, name, root, status="idle"):
    record = {"pid": pid, "sessionId": sid, "cwd": str(root / "math-quest"), "name": name, "status": status}
    (live / f"{pid}.json").write_text(json.dumps({**record, "kind": "interactive", "updatedAt": 0}))


@pytest.fixture
def live(tmp_path, root):
    d = tmp_path / "live"
    d.mkdir()
    write(d, os.getpid(), "mq-id", "math-quest", root)
    return d


def server(tmp_path, root, live, tmux=None, deliver=None):
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root)
    srv = build_server(
        m,
        conversations=lambda directory=None, limit=None: [],
        live_dir=live,
        deliver=deliver or Recorder(),
        read_transcript=lambda sid, directory: [],
        tmux=tmux or FakeTmux(live),
    )
    return srv


async def call(c, name, **args):
    res = await c.call_tool(name, args)
    assert not res.is_error, res.content
    return res.structured_content


async def names(c):
    return [s["name"] for s in (await call(c, "list_active_sessions"))["sessions"]]


async def test_a_renamed_session_is_listed_by_its_new_name(tmp_path, root, live):
    async with Client(server(tmp_path, root, live)) as c:
        out = await call(c, "rename_session", session="math-quest", new_name="math-quest-curriculum")
        assert await names(c) == ["math-quest-curriculum"]
        fleet = await call(c, "fleet_recap")
        tree = await call(c, "session_tree")
    assert out["name"] == "math-quest-curriculum" and out["was"] == "math-quest"
    assert [s["name"] for s in fleet["sessions"]] == ["math-quest-curriculum"]
    assert "math-quest-curriculum" in [n["name"] for n in tree["nodes"]]


async def test_a_message_to_an_old_name_reaches_the_renamed_session(tmp_path, root, live):
    deliver = Recorder()
    async with Client(server(tmp_path, root, live, deliver=deliver)) as c:
        await call(c, "rename_session", session="math-quest", new_name="math-quest-curriculum")
        await call(c, "message_active_session", session="math-quest", message="old")
        await call(c, "message_active_session", session="math-quest-curriculum", message="new")
    # Outside tmux Claude Code still answers to the old name, so that is where it goes.
    assert deliver.sent == [("math-quest", "old"), ("math-quest", "new")]


async def test_a_message_to_an_old_name_arrives_once_claude_code_has_the_new_one(tmp_path, root, live):
    deliver = Recorder()
    tmux = FakeTmux(live, {os.getpid(): ("math-quest", "%1")})
    async with Client(server(tmp_path, root, live, tmux=tmux, deliver=deliver)) as c:
        out = await call(c, "rename_session", session="math-quest", new_name="math-quest-curriculum")
        await call(c, "message_active_session", session="math-quest", message="old")
        assert await names(c) == ["math-quest-curriculum"]
    assert out["claude_code"] == "renamed"
    assert tmux.typed == [("%1", "/rename math-quest-curriculum")]
    assert tmux.renamed == [("math-quest", "math-quest-curriculum")]
    assert deliver.sent == [("math-quest-curriculum", "old")]


@pytest.mark.parametrize("bad", ["", "math quest", "-flag", "mq/x", "æøå", "a" * 65, "mq;rm"])
async def test_a_session_name_is_letters_digits_dots_underscores_and_dashes(tmp_path, root, live, bad):
    async with Client(server(tmp_path, root, live)) as c:
        res = await c.call_tool("rename_session", {"session": "math-quest", "new_name": bad})
        assert res.is_error and "letters" in res.content[0].text
        assert await names(c) == ["math-quest"]


async def test_a_new_name_already_in_use_is_refused(tmp_path, root, live):
    write(live, 1, "other-id", "math-quest-manager", root)
    async with Client(server(tmp_path, root, live)) as c:
        res = await c.call_tool("rename_session", {"session": "math-quest", "new_name": "math-quest-manager"})
        assert res.is_error and "in use" in res.content[0].text
        await call(c, "rename_session", session="math-quest-manager", new_name="mq-manager")
        # An old name still answers, so it is taken too.
        res = await c.call_tool("rename_session", {"session": "math-quest", "new_name": "math-quest-manager"})
        assert res.is_error and "in use" in res.content[0].text


async def test_a_busy_session_is_never_typed_into(tmp_path, root, live):
    write(live, os.getpid(), "mq-id", "math-quest", root, status="busy")
    tmux = FakeTmux(live, {os.getpid(): ("math-quest", "%1")})
    async with Client(server(tmp_path, root, live, tmux=tmux)) as c:
        out = await call(c, "rename_session", session="math-quest", new_name="math-quest-curriculum")
        assert await names(c) == ["math-quest-curriculum"]
        assert tmux.typed == []
        assert out["claude_code"] == "busy" and "again" in out["note"]
        # Asked again once it is idle, Claude Code gets the name too.
        write(live, os.getpid(), "mq-id", "math-quest", root, status="idle")
        out = await call(c, "rename_session", session="math-quest-curriculum", new_name="math-quest-curriculum")
    assert out["claude_code"] == "renamed"
    assert tmux.typed == [("%1", "/rename math-quest-curriculum")]


async def test_a_session_outside_tmux_is_renamed_in_the_bridge_only(tmp_path, root, live, caplog):
    caplog.set_level(logging.INFO, logger="claude_voice")
    async with Client(server(tmp_path, root, live)) as c:
        out = await call(c, "rename_session", session="math-quest", new_name="mq")
    assert out["claude_code"] == "not_in_tmux" and "/rename mq" in out["note"]
    assert any("renamed math-quest -> mq" in r.getMessage() for r in caplog.records)


async def test_renaming_an_unknown_session_is_refused(tmp_path, root, live):
    async with Client(server(tmp_path, root, live)) as c:
        res = await c.call_tool("rename_session", {"session": "nope", "new_name": "x"})
    assert res.is_error and "list_active_sessions" in res.content[0].text


async def test_a_name_set_in_claude_code_afterwards_wins(tmp_path, root, live):
    async with Client(server(tmp_path, root, live)) as c:
        await call(c, "rename_session", session="math-quest", new_name="mq")
        write(live, os.getpid(), "mq-id", "typed-by-roy", root)
        assert await names(c) == ["typed-by-roy"]

"""The tree of sessions: what runs, what the bridge runs, and who talks to whom."""

import json
import os
from datetime import UTC, datetime, timedelta

import pytest
from claude_agent_sdk import project_key_for_directory

from claude_voice import tree as tree_module
from claude_voice.store import Store
from claude_voice.tree import BRIDGE_NODE, SessionTree, _Transcript

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
PID = os.getpid()


def stamp(minutes_ago=1):
    return (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def peer_message(from_name, address, minutes_ago=1):
    body = f'<cross-session-message from="{address}" from-name="{from_name}" from-mode="prompting">\nhi\n'
    return {
        "type": "user",
        "timestamp": stamp(minutes_ago),
        "message": {"role": "user", "content": body + "</cross-session-message>"},
    }


def send(to, minutes_ago=1):
    return {
        "type": "assistant",
        "timestamp": stamp(minutes_ago),
        "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "SendMessage", "input": {"to": to}}]},
    }


class World:
    def __init__(self, tmp_path):
        self.root = tmp_path / "home"
        self.live = tmp_path / "live"
        self.projects = tmp_path / "projects"
        for d in (self.root, self.live, self.projects):
            d.mkdir()
        self.store = Store(tmp_path / "b.db", clock=lambda: NOW.timestamp())
        self.subagents = {}
        self.next_pid = PID

    def session(self, name, project="app", kind="interactive", status="idle", pid=None):
        cwd = self.root / project
        cwd.mkdir(exist_ok=True)
        pid = pid or self.next_pid
        self.next_pid += 1_000_000  # only the first is a live pid; the others are faked alive below
        sid = f"sid-{name}"
        (self.live / f"{name}.json").write_text(
            json.dumps({"pid": pid, "sessionId": sid, "cwd": str(cwd), "name": name, "kind": kind, "status": status})
        )
        return sid, cwd

    def transcript(self, sid, cwd, entries, append=False):
        key = project_key_for_directory(cwd)
        path = self.projects / key / f"{sid}.jsonl"
        path.parent.mkdir(exist_ok=True)
        with path.open("a" if append else "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    def tree(self):
        return SessionTree(
            self.store,
            self.live,
            self.root,
            projects_dir=self.projects,
            clock=lambda: NOW.timestamp(),
            subagents=lambda sid, directory: self.subagents.get(sid, []),
            alive=lambda pid: True,
        )


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def by_id(tree):
    return {n["id"]: n for n in tree["nodes"]}


def test_running_sessions_become_nodes_and_couriers_do_not(world):
    world.session("app-1", kind="interactive")
    world.session("bg-1", kind="bg", status="busy")
    world.session("claude-voice-msg-x1-y2")
    nodes = by_id(world.tree().build())
    assert nodes["sid-app-1"]["kind"] == "terminal"
    assert nodes["sid-bg-1"]["kind"] == "background"
    assert nodes["sid-bg-1"]["status"] == "busy"
    assert nodes["sid-app-1"]["project"] == "app"
    assert all(not n["name"].startswith("claude-voice-msg-") for n in nodes.values())
    assert nodes[BRIDGE_NODE]["kind"] == "bridge"


def test_sessions_outside_the_root_are_left_out(world, tmp_path):
    (world.live / "x.json").write_text(
        json.dumps({"pid": PID, "sessionId": "sid-x", "cwd": str(tmp_path), "name": "x", "kind": "interactive"})
    )
    assert "sid-x" not in by_id(world.tree().build())


def test_sessions_the_bridge_runs_hang_under_the_bridge_or_the_session_they_copy(world):
    sid, cwd = world.session("app-1")
    own = world.store.create_session(str(cwd), label="fix")
    copy = world.store.create_session(str(cwd), label="copy")
    world.store.update_session(copy["id"], claude_session_id=sid)
    nodes = by_id(world.tree().build())
    assert nodes[own["id"]]["kind"] == "bridge-session"
    assert nodes[own["id"]]["parent_id"] == BRIDGE_NODE
    assert nodes[copy["id"]]["parent_id"] == sid


def test_closed_bridge_sessions_are_left_out(world):
    _, cwd = world.session("app-1")
    s = world.store.create_session(str(cwd), label="old")
    world.store.update_session(s["id"], status="closed")
    assert s["id"] not in by_id(world.tree().build())


def test_who_talks_to_whom_comes_from_the_transcripts(world):
    a, a_cwd = world.session("alpha", pid=PID)
    b, b_cwd = world.session("beta")
    world.transcript(a, a_cwd, [send("beta")])
    world.transcript(b, b_cwd, [peer_message("alpha", f"uds:/run/user/1000/cc-socks/{PID}.sock")])
    nodes = by_id(world.tree().build())
    assert nodes[a]["talks_to"] == [b]
    assert nodes[b]["talks_to"] == []


def test_a_sender_known_only_by_its_socket_address_is_still_found(world):
    a, _ = world.session("alpha", pid=PID)
    b, b_cwd = world.session("beta")
    world.transcript(b, b_cwd, [peer_message("renamed-meanwhile", f"uds:/run/user/1000/cc-socks/{PID}.sock")])
    assert by_id(world.tree().build())[a]["talks_to"] == [b]


def test_messages_from_the_bridges_couriers_come_from_the_bridge(world):
    b, b_cwd = world.session("beta")
    world.transcript(b, b_cwd, [peer_message("claude-voice-msg-ab-cd", "uds:/run/user/1000/cc-socks/1.sock")])
    assert by_id(world.tree().build())[BRIDGE_NODE]["talks_to"] == [b]


def test_old_messages_do_not_make_edges(world):
    a, a_cwd = world.session("alpha")
    world.session("beta")
    world.transcript(a, a_cwd, [send("beta", minutes_ago=60 * 25)])
    assert by_id(world.tree().build())[a]["talks_to"] == []


def test_new_lines_in_a_transcript_are_picked_up(world):
    a, a_cwd = world.session("alpha")
    b, _ = world.session("beta")
    tree = world.tree()
    world.transcript(a, a_cwd, [{"type": "user", "timestamp": stamp(), "message": {"content": "hello"}}])
    assert by_id(tree.build())[a]["talks_to"] == []
    world.transcript(a, a_cwd, [send("beta")], append=True)
    assert by_id(tree.build())[a]["talks_to"] == [b]


def subagent_file(world, sid, cwd, agent, minutes_ago):
    path = world.projects / project_key_for_directory(cwd) / sid / "subagents" / f"agent-{agent}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n")
    when = NOW.timestamp() - minutes_ago * 60
    os.utime(path, (when, when))


def test_subagents_at_work_in_the_last_hour_hang_under_their_session(world):
    a, cwd = world.session("alpha")
    world.subagents[a] = ["a7", "a8"]
    subagent_file(world, a, cwd, "a7", minutes_ago=5)
    subagent_file(world, a, cwd, "a8", minutes_ago=300)  # long finished
    nodes = by_id(world.tree().build())
    assert nodes[f"{a}/a7"]["kind"] == "subagent"
    assert nodes[f"{a}/a7"]["parent_id"] == a
    assert f"{a}/a8" not in nodes


def test_the_version_changes_with_the_shape_and_not_with_a_status(world):
    a, a_cwd = world.session("alpha")
    world.session("beta")
    tree = world.tree()
    first = tree.build()["version"]
    world.session("alpha", status="busy")  # same node, new status
    assert tree.build()["version"] == first
    world.transcript(a, a_cwd, [send("beta")])
    assert tree.build()["version"] != first


# -- through the bridge ----------------------------------------------------------


async def test_the_bridge_offers_the_tree_and_says_when_its_shape_changes(world):
    from fakes import FakeClaude
    from mcp.client import Client

    from claude_voice.server import build_server
    from claude_voice.sessions import SessionManager

    world.session("alpha", pid=PID)
    m = SessionManager(world.store, project_root=world.root, client_factory=FakeClaude())
    srv = build_server(m, conversations=lambda **k: [], live_dir=world.live, projects_dir=world.projects)
    async with Client(srv) as c:
        tree = (await c.call_tool("session_tree", {})).structured_content
        assert "sid-alpha" in {n["id"] for n in tree["nodes"]}
        start = (await c.call_tool("whats_new", {})).structured_content
        world.session("beta", pid=PID)
        news = (await c.call_tool("whats_new", {"cursor": start["cursor"]})).structured_content
    changed = [e for e in news["events"] if e["kind"] == "tree_changed"]
    assert len(changed) == 1
    assert changed[0]["text"] != tree["version"]  # carries the new version


def test_a_session_continued_elsewhere_is_marked_moved(world):
    a, a_cwd = world.session("alpha")
    b, _ = world.session("beta")
    world.transcript(a, a_cwd, [{"type": "continued-in", "sessionId": a, "continuedInSessionId": b}])
    nodes = by_id(world.tree().build())
    assert nodes[a]["status"] == "moved"
    assert nodes[a]["moved_to"] == b
    assert "moved_to" not in nodes[b] or nodes[b]["moved_to"] is None


def test_a_courier_with_a_friendly_name_is_still_the_bridge(world, tmp_path):
    courier_dir = tmp_path / "claude-voice-msg-abc"
    courier_dir.mkdir()
    (world.live / "courier.json").write_text(
        json.dumps({"pid": 4242, "sessionId": "sid-c", "cwd": str(courier_dir), "name": "Roy via stemmen"})
    )
    b, b_cwd = world.session("beta")
    world.transcript(b, b_cwd, [peer_message("Roy via stemmen", "uds:/run/user/1000/cc-socks/4242.sock")])
    nodes = by_id(world.tree().build())
    assert nodes[BRIDGE_NODE]["talks_to"] == [b]


def test_a_session_whose_helpers_cannot_be_listed_still_shows(world):
    a, _ = world.session("alpha")

    def broken(sid, directory):
        raise RuntimeError("no helpers here")

    tree = world.tree()
    tree.subagents = broken
    nodes = by_id(tree.build())
    assert nodes[a]["kind"] == "terminal"
    assert not any(n["parent_id"] == a for n in nodes.values())


def test_bridge_sessions_quiet_for_a_day_are_left_out(world):
    _, cwd = world.session("app-1")
    world.store.clock = lambda: (NOW - timedelta(hours=25)).timestamp()
    stale = world.store.create_session(str(cwd), label="stale")
    assert stale["id"] not in by_id(world.tree().build())


def test_messages_with_a_session_that_is_not_running_make_no_edge(world):
    a, a_cwd = world.session("alpha")
    world.transcript(
        a,
        a_cwd,
        [send("nobody"), peer_message("stranger", "uds:/run/user/1000/cc-socks/999999999.sock")],
    )
    nodes = by_id(world.tree().build())
    assert all(n["talks_to"] == [] for n in nodes.values())


def test_a_message_a_session_sends_itself_makes_no_edge(world):
    a, a_cwd = world.session("alpha")
    world.transcript(a, a_cwd, [send("alpha")])
    assert by_id(world.tree().build())[a]["talks_to"] == []


def test_the_tree_is_built_by_one_caller_at_a_time(world):
    import threading
    import time

    sid, _ = world.session("app-1")
    inside, most = [0], [0]

    def counting(session, directory):
        inside[0] += 1
        most[0] = max(most[0], inside[0])
        time.sleep(0.2)  # long enough that a second caller would arrive while the first is in
        inside[0] -= 1
        return []

    tree = world.tree()
    tree.subagents = counting
    callers = [threading.Thread(target=tree.build) for _ in range(2)]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join()
    assert most[0] == 1


# -- how many messages each pair of sessions exchanged, for the programme hints ---------


def test_a_message_is_counted_once_where_it_was_read(world):
    a, a_cwd = world.session("alpha", pid=PID)
    b, b_cwd = world.session("beta")
    world.transcript(a, a_cwd, [send("beta"), send("beta")])
    world.transcript(b, b_cwd, [peer_message("alpha", "uds:/x.sock"), peer_message("alpha", "uds:/x.sock")])
    assert world.tree().message_counts(7 * 24 * 3600) == {tuple(sorted((a, b))): 2}


def test_only_messages_inside_the_window_are_counted(world):
    world.session("alpha")
    b, b_cwd = world.session("beta")
    world.transcript(b, b_cwd, [peer_message("alpha", "uds:/x.sock", minutes_ago=60 * 24 * 8)])
    world.transcript(b, b_cwd, [peer_message("alpha", "uds:/x.sock", minutes_ago=60 * 24 * 6)], append=True)
    assert list(world.tree().message_counts(7 * 24 * 3600).values()) == [1]


def test_messages_from_the_bridge_are_not_counted_between_sessions(world):
    b, b_cwd = world.session("beta")
    world.transcript(b, b_cwd, [peer_message("claude-voice-msg-ab-cd", "uds:/run/user/1000/cc-socks/1.sock")])
    assert world.tree().message_counts(7 * 24 * 3600) == {}


class RecordedReads:
    """A transcript path whose open file records the size asked of every read."""

    def __init__(self, path):
        self.path = path
        self.sizes = []

    def open(self, mode):
        real = self.path.open(mode)
        sizes = self.sizes

        class File:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                real.close()

            def seek(self, offset):
                return real.seek(offset)

            def read(self, size=-1):
                sizes.append(size)
                return real.read(size)

        return File()


def test_a_large_transcript_is_read_a_bounded_chunk_at_a_time(tmp_path, monkeypatch):
    monkeypatch.setattr(tree_module, "CHUNK", 256)
    path = tmp_path / "t.jsonl"
    path.write_text("".join(json.dumps(send(f"peer-{i}")) + "\n" for i in range(20)))
    recorded = RecordedReads(path)
    transcript = _Transcript(recorded)
    transcript.update()
    assert len(transcript.seen) == 20
    assert recorded.sizes
    assert all(0 < size <= 256 for size in recorded.sizes)


def test_chunked_reading_keeps_every_message_and_the_unfinished_last_line(tmp_path, monkeypatch):
    monkeypatch.setattr(tree_module, "CHUNK", 64)  # lines straddle chunk boundaries
    entries = [
        json.dumps(send("beta")),
        json.dumps({"type": "user", "timestamp": stamp(), "message": {"content": "x" * 500}}),  # spans many chunks
        "not json",
        json.dumps(peer_message("gamma", "uds:/g.sock")),
        json.dumps({"type": "continued-in", "continuedInSessionId": "sid-next"}),
    ]
    unfinished = json.dumps(send("delta"))
    path = tmp_path / "t.jsonl"
    path.write_bytes(("\n".join(entries) + "\n" + unfinished[:30]).encode())
    transcript = _Transcript(path)
    transcript.update()
    when = (NOW - timedelta(minutes=1)).timestamp()
    assert transcript.seen == [(when, "sent", "beta", ""), (when, "received", "gamma", "uds:/g.sock")]
    assert transcript.moved_to == "sid-next"
    assert transcript.rest == unfinished[:30].encode()
    assert transcript.offset == path.stat().st_size
    with path.open("ab") as f:
        f.write((unfinished[30:] + "\n").encode())
    transcript.update()
    assert transcript.seen[-1] == (when, "sent", "delta", "")
    assert transcript.rest == b""
    assert transcript.moved_to is None

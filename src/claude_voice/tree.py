"""The tree of sessions for a voice client to choose from, and who talks to whom.

Nodes are the Claude Code sessions running on the computer, the sessions the
bridge runs (under the bridge, or under the session they copy), and helper
agents still at work in the last hour (under their session). Edges, `talks_to`, are the messages one session
sent another in the last day, read from the transcripts: a SendMessage is an
edge from its sender, a received cross-session message an edge to its reader.
Transcripts grow to tens of megabytes, so each is read once and then only from
where reading stopped.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import list_subagents, project_key_for_directory

from . import procs, renames
from .store import Store
from .transcripts import PROJECTS_DIR

BRIDGE_NODE = "claude-voice"
COURIER_PREFIX = "claude-voice-msg-"
WINDOW_SECONDS = 24 * 3600
SUBAGENT_SECONDS = 3600
CHUNK = 1 << 20  # bytes of a transcript read at a time
MESSAGE = re.compile(r'<cross-session-message from="(?P<address>[^"]*)" from-name="(?P<name>[^"]*)"')
SOCKET_PID = re.compile(r"/(\d+)\.sock$")


def _pid_alive(pid: int) -> bool:
    return procs.alive(pid)


def _timestamp(entry: dict[str, Any]) -> float | None:
    try:
        return datetime.fromisoformat(str(entry["timestamp"]).replace("Z", "+00:00")).timestamp()
    except (KeyError, ValueError):
        return None


def _messages(entry: dict[str, Any]) -> list[tuple[str, str, str]]:
    """('sent', to, '') and ('received', from_name, address) found in one transcript line."""
    found: list[tuple[str, str, str]] = []
    if entry.get("type") == "assistant":
        for block in entry.get("message", {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "SendMessage":
                found.append(("sent", str(block.get("input", {}).get("to", "")), ""))
    texts: list[str] = []
    if entry.get("type") == "user":
        content = entry.get("message", {}).get("content")
        texts = [content] if isinstance(content, str) else []
    elif entry.get("type") == "attachment":
        prompt = entry.get("attachment", {}).get("prompt")
        texts = [prompt] if isinstance(prompt, str) else []
    for text in texts:
        found.extend(("received", m.group("name"), m.group("address")) for m in MESSAGE.finditer(text))
    return found


class _Transcript:
    """Messages seen in one transcript file, read on from where reading stopped."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.rest = bytearray()  # an unfinished last line; grown in place, so a long one costs its length once
        self.seen: list[tuple[float | None, str, str, str]] = []
        self.moved_to: str | None = None  # set by a continued-in line, cleared by later talk

    def update(self) -> None:
        # A transcript can pass a gigabyte, so it is read a chunk at a time: reading it whole
        # once needed twice its size, and the allocator kept most of that after.
        try:
            with self.path.open("rb") as f:
                f.seek(self.offset)
                # RULE: a large transcript is read a bounded chunk at a time
                while chunk := f.read(CHUNK):
                    self.offset += len(chunk)
                    done, newline, unfinished = chunk.rpartition(b"\n")
                    if not newline:  # still inside one long line, grown in place
                        self.rest += unfinished
                        continue
                    self.rest += done
                    lines = self.rest.split(b"\n")
                    self.rest = bytearray(unfinished)
                    for line in lines:
                        self._read(line)
        except OSError:
            return

    def _read(self, line: bytes | bytearray) -> None:
        try:
            entry = json.loads(line)
        except ValueError:
            return
        if entry.get("type") == "continued-in":
            self.moved_to = str(entry.get("continuedInSessionId") or "") or None
        elif entry.get("type") in ("user", "assistant"):
            self.moved_to = None
        when = _timestamp(entry)
        self.seen.extend((when, kind, who, address) for kind, who, address in _messages(entry))


class SessionTree:
    def __init__(
        self,
        store: Store,
        live_dir: Path,
        root: Path,
        projects_dir: Path = PROJECTS_DIR,
        clock: Callable[[], float] = time.time,
        subagents: Callable[[str, str], list[str]] = lambda sid, directory: list_subagents(sid, directory=directory),
        alive: Callable[[int], bool] = _pid_alive,
        rename: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] = lambda rows: rows,
    ) -> None:
        self.store = store
        self.live_dir = live_dir
        self.root = Path(root).resolve()
        self.projects_dir = projects_dir
        self.clock = clock
        self.subagents = subagents
        self.alive = alive
        self.rename = rename
        self._transcripts: dict[Path, _Transcript] = {}
        self._couriers: tuple[set[int], set[str]] = (set(), set())  # pids, names
        self._building = threading.Lock()

    def _project(self, cwd: str | None) -> str | None:
        if not cwd:
            return None
        path = Path(cwd).resolve()
        if path != self.root and self.root not in path.parents:
            return None
        return str(path.relative_to(self.root)) or "."

    def _live(self) -> list[dict[str, Any]]:
        out = []
        pids: set[int] = set()
        names: set[str] = set()
        for f in sorted(self.live_dir.glob("*.json")) if self.live_dir.is_dir() else []:
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                pid = int(d["pid"])
            except (ValueError, KeyError, TypeError, OSError):
                continue
            # A courier is known by its working folder; its name is whatever the owner chose.
            if Path(str(d.get("cwd"))).name.startswith(COURIER_PREFIX) or str(d.get("name", "")).startswith(
                COURIER_PREFIX
            ):
                pids.add(pid)
                names.add(str(d.get("name", "")))
                continue
            if not self.alive(pid) or self._project(d.get("cwd")) is None:
                continue
            out.append(d)
        self._couriers = (pids, names)
        return self.rename(out)

    def _transcript(self, sid: str, cwd: str) -> _Transcript:
        path = self.projects_dir / project_key_for_directory(cwd) / f"{sid}.jsonl"
        if path not in self._transcripts:
            self._transcripts[path] = _Transcript(path)
        transcript = self._transcripts[path]
        transcript.update()
        return transcript

    def _recent_subagent(self, sid: str, cwd: str, agent: str) -> bool:
        name = agent if agent.startswith("agent-") else f"agent-{agent}"
        path = self.projects_dir / project_key_for_directory(cwd) / sid / "subagents" / f"{name}.jsonl"
        try:
            return path.stat().st_mtime >= self.clock() - SUBAGENT_SECONDS
        except OSError:
            return False

    def _node(
        self, nid: str, name: Any, kind: str, project: str | None, status: Any, parent: str | None
    ) -> dict[str, Any]:
        return {
            "id": nid,
            "name": name,
            "kind": kind,
            "project": project,
            "status": status,
            "parent_id": parent,
            "talks_to": set(),
        }

    def _live_nodes(self, live: list[dict[str, Any]], nodes: dict[str, dict[str, Any]]) -> None:
        """A node for each running session, and under it the helpers still at work."""
        for d in live:
            sid = str(d["sessionId"])
            cwd = str(d.get("cwd"))
            project = self._project(d.get("cwd"))
            kind = "terminal" if d.get("kind") == "interactive" else "background"
            nodes[sid] = self._node(sid, d.get("name"), kind, project, d.get("status"), None)
            try:
                agents = self.subagents(sid, cwd)
            except Exception:
                agents = []
            for agent in agents:
                if not self._recent_subagent(sid, cwd, agent):
                    continue  # long sessions keep every helper they ever ran; show those at work
                nodes[f"{sid}/{agent}"] = self._node(f"{sid}/{agent}", agent, "subagent", project, None, sid)

    def _bridge_nodes(self, nodes: dict[str, dict[str, Any]], cutoff: float) -> None:
        """A node for each session the bridge runs, under the session it copies or the bridge."""
        for s in self.store.list_sessions(limit=200):
            if s["status"] == "closed" or s["updated_at"] < cutoff:
                continue
            parent = s["claude_session_id"] if s["claude_session_id"] in nodes else BRIDGE_NODE
            name = s["label"] or Path(s["project_path"]).name
            project = self._project(s["project_path"])
            nodes[s["id"]] = self._node(s["id"], name, "bridge-session", project, s["status"], parent)

    def _resolver(self, live: list[dict[str, Any]]) -> Callable[[str, str], str | None]:
        """Who a name or socket address in a transcript is: the bridge, a running session, or nobody."""
        by_name = renames.by_name(live)
        by_pid = {int(d["pid"]): str(d["sessionId"]) for d in live}
        courier_pids, courier_names = self._couriers

        def resolve(who: str, address: str = "") -> str | None:
            pid_match = SOCKET_PID.search(address)
            if (
                who.startswith(COURIER_PREFIX)
                or who in courier_names
                or (pid_match is not None and int(pid_match.group(1)) in courier_pids)
            ):
                return BRIDGE_NODE
            name = re.sub(r"\s*\[[0-9a-f]+\]$", "", who)
            if name in by_name:
                return by_name[name]
            for text in (address, who):
                pid = SOCKET_PID.search(text)
                if pid and int(pid.group(1)) in by_pid:
                    return by_pid[int(pid.group(1))]
            return None

        return resolve

    def _talk(self, live: list[dict[str, Any]], nodes: dict[str, dict[str, Any]], cutoff: float) -> None:
        """Mark sessions continued elsewhere, and draw who talks to whom from the transcripts."""
        resolve = self._resolver(live)
        for d in live:
            sid = str(d["sessionId"])
            transcript = self._transcript(sid, str(d.get("cwd")))
            if transcript.moved_to:
                nodes[sid]["status"] = "moved"
                nodes[sid]["moved_to"] = transcript.moved_to
            for when, kind, who, address in transcript.seen:
                if when is not None and when < cutoff:
                    continue
                other = resolve(who, address)
                if other is None or other == sid:
                    continue
                if kind == "sent":
                    nodes[sid]["talks_to"].add(other)
                else:
                    nodes[other]["talks_to"].add(sid)

    def build(self) -> dict[str, Any]:
        # The watcher and the tools build it from different threads, and a transcript's
        # place in its file must move on for one reader at a time.
        # RULE: the tree is built by one caller at a time
        with self._building:
            return self._build()

    def _build(self) -> dict[str, Any]:
        live = self._live()
        nodes = {BRIDGE_NODE: self._node(BRIDGE_NODE, "claude-voice", "bridge", None, "running", None)}
        self._live_nodes(live, nodes)
        cutoff = self.clock() - WINDOW_SECONDS
        self._bridge_nodes(nodes, cutoff)
        self._talk(live, nodes, cutoff)
        out = [{**n, "talks_to": sorted(n["talks_to"])} for n in nodes.values()]
        shape = sorted((n["id"], n["parent_id"] or "", n["kind"], tuple(n["talks_to"])) for n in out)
        version = hashlib.sha1(json.dumps(shape).encode()).hexdigest()[:12]
        return {"version": version, "nodes": out}

    def message_counts(self, seconds: float) -> dict[tuple[str, str], int]:
        """How many messages each pair of running sessions exchanged in the last `seconds`,
        keyed by the two ids in order. Each transcript is read on from where it stopped."""
        with self._building:
            live = self._live()
            resolve = self._resolver(live)
            cutoff = self.clock() - seconds
            counts: dict[tuple[str, str], int] = {}
            for d in live:
                sid = str(d["sessionId"])
                for when, kind, who, address in self._transcript(sid, str(d.get("cwd"))).seen:
                    # A message shows twice, sent and read: the reader's copy names its sender.
                    # RULE: a message is counted once, where it was read
                    if kind != "received":
                        continue
                    # RULE: only messages inside the window are counted
                    if when is not None and when < cutoff:
                        continue
                    other = resolve(who, address)
                    if other in (None, sid, BRIDGE_NODE):
                        continue
                    pair = (min(sid, str(other)), max(sid, str(other)))
                    counts[pair] = counts.get(pair, 0) + 1
            return counts

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
import os
import re
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import list_subagents, project_key_for_directory

from .store import Store
from .transcripts import PROJECTS_DIR

BRIDGE_NODE = "claude-voice"
COURIER_PREFIX = "claude-voice-msg-"
WINDOW_SECONDS = 24 * 3600
SUBAGENT_SECONDS = 3600
MESSAGE = re.compile(r'<cross-session-message from="(?P<address>[^"]*)" from-name="(?P<name>[^"]*)"')
SOCKET_PID = re.compile(r"/(\d+)\.sock$")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


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
        self.rest = b""
        self.seen: list[tuple[float | None, str, str, str]] = []
        self.moved_to: str | None = None  # set by a continued-in line, cleared by later talk

    def update(self) -> None:
        try:
            with self.path.open("rb") as f:
                f.seek(self.offset)
                data = f.read()
        except OSError:
            return
        self.offset += len(data)
        *lines, self.rest = (self.rest + data).split(b"\n")
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
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
    ) -> None:
        self.store = store
        self.live_dir = live_dir
        self.root = Path(root).resolve()
        self.projects_dir = projects_dir
        self.clock = clock
        self.subagents = subagents
        self.alive = alive
        self._transcripts: dict[Path, _Transcript] = {}
        self._couriers: tuple[set[int], set[str]] = (set(), set())  # pids, names

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
                d = json.loads(f.read_text())
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
        return out

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

    def build(self) -> dict[str, Any]:
        live = self._live()
        nodes: dict[str, dict[str, Any]] = {
            BRIDGE_NODE: {
                "id": BRIDGE_NODE,
                "name": "claude-voice",
                "kind": "bridge",
                "project": None,
                "status": "running",
                "parent_id": None,
                "talks_to": set(),
            }
        }
        by_name = {str(d.get("name")): str(d["sessionId"]) for d in live}
        by_pid = {int(d["pid"]): str(d["sessionId"]) for d in live}

        for d in live:
            sid = str(d["sessionId"])
            nodes[sid] = {
                "id": sid,
                "name": d.get("name"),
                "kind": "terminal" if d.get("kind") == "interactive" else "background",
                "project": self._project(d.get("cwd")),
                "status": d.get("status"),
                "parent_id": None,
                "talks_to": set(),
            }
            try:
                agents = self.subagents(sid, str(d.get("cwd")))
            except Exception:
                agents = []
            for agent in agents:
                if not self._recent_subagent(sid, str(d.get("cwd")), agent):
                    continue  # long sessions keep every helper they ever ran; show those at work
                nodes[f"{sid}/{agent}"] = {
                    "id": f"{sid}/{agent}",
                    "name": agent,
                    "kind": "subagent",
                    "project": self._project(d.get("cwd")),
                    "status": None,
                    "parent_id": sid,
                    "talks_to": set(),
                }

        cutoff = self.clock() - WINDOW_SECONDS
        for s in self.store.list_sessions(limit=200):
            if s["status"] == "closed" or s["updated_at"] < cutoff:
                continue
            parent = s["claude_session_id"] if s["claude_session_id"] in nodes else BRIDGE_NODE
            nodes[s["id"]] = {
                "id": s["id"],
                "name": s["label"] or Path(s["project_path"]).name,
                "kind": "bridge-session",
                "project": self._project(s["project_path"]),
                "status": s["status"],
                "parent_id": parent,
                "talks_to": set(),
            }

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

        out = [{**n, "talks_to": sorted(n["talks_to"])} for n in nodes.values()]
        shape = sorted((n["id"], n["parent_id"] or "", n["kind"], tuple(n["talks_to"])) for n in out)
        version = hashlib.sha1(json.dumps(shape).encode()).hexdigest()[:12]
        return {"version": version, "nodes": out}

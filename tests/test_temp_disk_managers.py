"""When the shared temporary disk fills, each programme's manager hears its own sessions'
largest folders (#434, the second part)."""

import asyncio
import json
import subprocess
import sys

import pytest
from fakes import FakeClaude

from claude_voice import temp_disk
from claude_voice.server import build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

ORG = """
[programme]
code = "acme"
name = "Acme Robots"

[roles.manager]
name = "Manager"
sessions = ["acme-boss"]

[roles.lane]
name = "Lane"
sessions = ["lane-a"]
builder = true
"""


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Delivered:
    def __init__(self):
        self.sent = []

    async def __call__(self, name, text):
        self.sent.append((name, text))
        return {"delivered": True, "detail": ""}


@pytest.fixture
def pids():
    started = [subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"]) for _ in range(2)]
    yield [p.pid for p in started]
    for p in started:
        p.kill()
        p.wait()


def bridge(tmp_path, pids, percent, deliver):
    """A bridge whose disk reads `percent`, with acme's manager and one lane running: the lane
    in a worktree of its own, so its folder differs from the manager's."""
    root = tmp_path / "src"
    (root / "acme" / ".claude").mkdir(parents=True)
    (root / "acme" / ".claude" / "org.toml").write_text(ORG)
    lane = root / "acme" / "lane"
    lane.mkdir()
    live = tmp_path / "live"
    live.mkdir()
    for pid, name, cwd in zip(pids, ("acme-boss", "lane-a"), (root / "acme", lane), strict=True):
        record = {"pid": pid, "sessionId": f"{name}-id", "cwd": str(cwd), "name": name, "status": "idle"}
        (live / f"{pid}.json").write_text(json.dumps(record))
    folders = [
        {"folder": "-somebody-else", "mb": 9000},
        {"folder": temp_disk.folder_for(str(lane)), "mb": 3000},
        {"folder": temp_disk.folder_for(str(root / "acme")), "mb": 400},
    ]
    watch = temp_disk.Watch(lambda: {"percent": percent, "largest": folders[:1], "folders": folders}, clock=Clock())
    m = SessionManager(Store(tmp_path / "b.db"), project_root=root, client_factory=FakeClaude())
    return build_server(
        m,
        conversations=lambda **k: [],
        live_dir=live,
        read_transcript=lambda s, d: [],
        deliver=deliver,
        org_folders=[],
        temp_watch=watch,
        manager_delivery_seconds=0.05,
        proof=True,  # starts no plugins, whatever settings it finds
    )


async def settle():
    for _ in range(5):
        await asyncio.sleep(0)


async def test_each_programme_s_manager_hears_its_own_sessions_largest_folders(tmp_path, pids):
    deliver = Delivered()
    server = bridge(tmp_path, pids, 92, deliver)
    await asyncio.wait_for(server.temp_disk_tell(), 5)
    await settle()
    assert [name for name, _ in deliver.sent] == ["acme-boss"]
    text = deliver.sent[0][1]
    assert "92 %" in text and "Acme Robots" in text and "lane (3.0 GB)" in text and "acme (400 MB)" in text
    assert "somebody-else" not in text


async def test_a_disk_below_the_first_level_tells_no_manager(tmp_path, pids):
    deliver = Delivered()
    server = bridge(tmp_path, pids, 60, deliver)
    await asyncio.wait_for(server.temp_disk_tell(), 5)
    await settle()
    assert deliver.sent == []

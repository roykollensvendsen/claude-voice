"""Checking whether a session is still running must never touch its process.

On Windows, os.kill(pid, 0) is not a probe: it ends the process. The bridge checks
every running Claude Code session this way, every few seconds.
"""

import json
import os
import subprocess
import sys
import types

from claude_voice import events, tree


def refuse_signals(monkeypatch, module):
    """Give `module` an os whose kill fails the test, and everything else real."""

    def kill(pid, sig):
        raise AssertionError(f"{module.__name__} called os.kill({pid}, {sig})")

    proxy = types.SimpleNamespace(**{name: getattr(os, name) for name in dir(os) if not name.startswith("__")})
    proxy.kill = kill
    monkeypatch.setattr(module, "os", proxy, raising=False)


def gone_pid():
    """The pid of a process that has already exited."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_checking_whether_a_session_is_alive_never_signals_it(tmp_path, monkeypatch):
    refuse_signals(monkeypatch, events)
    refuse_signals(monkeypatch, tree)
    live = tmp_path / "live"
    live.mkdir()
    (live / "me.json").write_text(json.dumps({"pid": os.getpid(), "sessionId": "s-me", "cwd": str(tmp_path)}))
    (live / "gone.json").write_text(json.dumps({"pid": gone_pid(), "sessionId": "s-gone", "cwd": str(tmp_path)}))

    assert [d["sessionId"] for d in events.read_live(live)] == ["s-me"]
    assert tree._pid_alive(os.getpid())

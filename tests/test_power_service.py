"""The helper as a service: where it runs from, its units and rule, its socket, its start.

Nothing here touches the real system: folders and their owners are faked, sockets are made
in a scratch folder, and the units are only text.
"""

import ast
import json
import os
import re
import socket
import threading
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
from power_fakes import MACHINE, Hub
from power_scan import commands, words

from claude_voice_power import __main__ as main
from claude_voice_power import selfcheck, server, units

SOURCE = Path(__file__).parent.parent / "src/claude_voice_power"
HELPER = "/usr/local/lib/claude-voice-power"
SITE = f"{HELPER}/lib/python3.12/site-packages"


def owned(table):
    """A stand-in for os.stat over a table of path -> (owner, mode); unlisted folders are root's."""

    def status(path):
        if path not in table and not path.startswith("/nowhere"):
            return SimpleNamespace(st_uid=0, st_mode=0o40755)
        if path.startswith("/nowhere"):
            raise FileNotFoundError(path)
        uid, mode = table[path]
        return SimpleNamespace(st_uid=uid, st_mode=mode)

    return status


def same(path):
    return path


# -- where the helper runs from ---------------------------------------------------------------


def test_the_helper_refuses_to_start_from_a_home_folder_or_a_folder_others_can_write():
    allowed = (HELPER, "/usr/lib/python3.12")
    status = owned({})
    assert selfcheck.problems([SITE, "/usr/lib/python3.12/lib-dynload"], allowed, status, same) == []
    home = selfcheck.problems(["/home/alex/claude-voice/src"], allowed, status, same)
    assert home and "home folder" in home[0]
    assert selfcheck.problems(["/root/x"], allowed, status, same)
    group = owned({f"{HELPER}/lib": (0, 0o40775)})
    assert "can be written" in selfcheck.problems([SITE], allowed, group, same)[0]
    other = owned({SITE: (0, 0o40757)})
    assert selfcheck.problems([SITE], allowed, other, same)
    theirs = owned({HELPER: (1000, 0o40755)})
    assert "can be written" in selfcheck.problems([SITE], allowed, theirs, same)[0]
    assert "not under" in selfcheck.problems(["/opt/elsewhere"], allowed, status, same)[0]


def test_an_empty_entry_is_the_current_folder_and_a_link_into_home_is_followed():
    allowed = (HELPER,)
    status = owned({})
    here = selfcheck.problems([""], allowed, status, lambda path: "/home/alex" if path == "." else path)
    assert here and "/home/alex" in here[0]
    link = selfcheck.problems([SITE], allowed, status, lambda path: "/home/alex/venv")
    assert link


def test_a_path_that_does_not_exist_is_not_a_problem():
    assert selfcheck.problems(["/nowhere/python312.zip"], ("/nowhere",), owned({}), same) == []


def test_the_check_says_why_it_refuses_and_the_running_tests_are_not_a_safe_place():
    told = []
    assert selfcheck.check(told.append) is False  # these tests run from a home checkout
    assert told and all(line.startswith("refusing to start: ") for line in told)
    assert selfcheck.system_folders()


# -- the units and the rule -------------------------------------------------------------------


@pytest.mark.parametrize("dynamic", [True, False])
def test_the_service_runs_from_the_root_folder_and_locks_itself_in(dynamic):
    text = units.service_text(dynamic)
    lines = text.splitlines()
    assert f"ExecStart={HELPER}/bin/python -I -m claude_voice_power" in lines
    assert f"WorkingDirectory={HELPER}" in lines
    for locked in ("ProtectHome=yes", "NoNewPrivileges=yes", "ProtectSystem=strict", "PrivateTmp=yes", "Environment="):
        assert locked in lines
    assert "/home" not in text and "PYTHONPATH" not in text
    assert ("DynamicUser=yes" in lines) is dynamic and ("User=cv-power" in lines) is not dynamic


def test_the_socket_is_for_one_group_only():
    text = units.socket_text()
    assert "ListenStream=/run/claude-voice-power/sock" in text
    assert "SocketMode=0660" in text and "SocketGroup=cv-power-clients" in text


def test_only_cv_power_holds_a_power_right():
    fixed, dynamic = units.polkit_rule(False), units.polkit_rule(True)
    assert 'subject.user == "cv-power"' in fixed and 'subject.system_unit == "claude-voice-power.service"' in dynamic
    for rule in (fixed, dynamic):
        assert rule.count("polkit.Result.YES") == 1
        for action in units.ACTIONS:
            assert f'"{action}"' in rule
        assert "ignore-inhibit" not in rule and "isInGroup" not in rule and "subject.local" not in rule
        assert rule.count("subject.") == 1  # one condition, on the helper alone
    assert "subject.user" not in dynamic


# -- what the code must never contain ---------------------------------------------------------


def test_the_helper_code_has_no_privilege_tool_no_password_and_no_way_round_an_inhibitor():
    for source in sorted(SOURCE.glob("*.py")):
        assert words(source.read_text()) == [], source.name


def test_the_helper_code_runs_no_command_that_the_login_manager_s_own_calls_replace():
    for source in sorted(SOURCE.glob("*.py")):
        assert commands(source.read_text()) == [], source.name


def test_the_scans_find_what_they_look_for():
    assert words("run sudo systemctl; use --ignore-inhibit") == ["ignore-inhibit", "sudo"]
    assert words("PASSWORD or pkexec") == ["password", "pkexec"]
    assert commands('import subprocess\nsubprocess.run(["systemctl", "poweroff"])') == ["poweroff", "systemctl"]
    assert commands('import os\nos.system("/usr/bin/loginctl reboot")') == ["loginctl", "reboot"]
    assert commands('import subprocess\nsubprocess.run(["wall", "hello"])') == []
    assert commands('x = "systemctl"') == []  # named, not run


def test_the_helper_imports_nothing_from_the_bridge():
    for source in sorted(SOURCE.glob("*.py")):
        for node in ast.walk(ast.parse(source.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            names += [node.module or ""] if isinstance(node, ast.ImportFrom) and not node.level else []
            assert not [n for n in names if n.split(".")[0] == "claude_voice"], source.name


# -- the socket and the start -----------------------------------------------------------------


def test_the_socket_must_come_from_systemd():
    with pytest.raises(SystemExit, match="claude-voice-power.socket"):
        server.listening_socket({})
    with pytest.raises(SystemExit):
        server.listening_socket({"LISTEN_PID": "1", "LISTEN_FDS": "1"})
    with socket.socket(socket.AF_UNIX) as listener:
        env = {"LISTEN_PID": str(os.getpid()), "LISTEN_FDS": "1"}
        adopted = server.listening_socket(env, first_fd=os.dup(listener.fileno()))
        adopted.close()


class Answers:
    def __init__(self):
        self.seen, self.ticks = [], 0

    def handle(self, message):
        self.seen.append(message)
        return {"ok": True, "echo": message.get("op")}

    def tick(self):
        self.ticks += 1


def test_one_connection_is_one_line_in_and_one_line_out_with_a_tick_after(tmp_path, monkeypatch):
    path = str(tmp_path / "sock")
    helper = Answers()
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(path)
        listener.listen()
        got = {}
        thread = threading.Thread(target=lambda: got.update(server.ask({"op": "cancel"}, path)))
        thread.start()
        server.serve_one(listener, helper)
        thread.join()
        assert got == {"ok": True, "echo": "cancel"} and helper.ticks == 1
        monkeypatch.setattr(server, "TICK_SECONDS", 0.01)
        server.serve_one(listener, helper)  # nobody came: the clock is still looked at
        assert helper.ticks == 2


def test_a_line_that_is_not_json_is_answered_as_a_message_nobody_understands(tmp_path):
    path = str(tmp_path / "sock")
    from power_fakes import Clock, FakeLogind, Notes

    from claude_voice_power import core, signing

    helper = core.Helper(FakeLogind(), signing.Verifier(Hub().public), MACHINE, Notes(), now=Clock())
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(path)
        listener.listen()
        reply = {}

        def junk():
            with socket.socket(socket.AF_UNIX) as client:
                client.connect(path)
                client.sendall(b"not json\n")
                reply.update(json.loads(client.makefile("rb").readline()))

        thread = threading.Thread(target=junk)
        thread.start()
        server.serve_one(listener, helper)
        thread.join()
    assert reply["code"] == "malformed"


def test_the_settings_are_this_machine_the_hub_key_and_the_night_hours(tmp_path):
    hub = Hub()
    import base64

    (tmp_path / "machine").write_text(MACHINE + "\n")
    (tmp_path / "hub.pub").write_text(base64.b64encode(hub.public).decode() + "\n")
    assert server.read_settings(tmp_path) == (MACHINE, hub.public, (23, 7))
    (tmp_path / "config.toml").write_text("night_start = 22\nnight_end = 6\n")
    assert server.read_settings(tmp_path)[2] == (22, 6)


def test_the_wall_message_never_fails_the_helper(monkeypatch):
    sent = []
    monkeypatch.setattr(server.subprocess, "run", lambda args, **kw: sent.append(args))
    server.WallNotifier().tell("going down")
    assert sent == [["wall", "going down"]]

    def missing(args, **kw):
        raise FileNotFoundError("wall")

    monkeypatch.setattr(server.subprocess, "run", missing)
    server.WallNotifier().tell("going down")


def test_the_helper_does_not_start_from_an_unsafe_place(monkeypatch):
    monkeypatch.setattr(selfcheck, "check", lambda log: (log("no"), False)[1])
    assert main.serve() == 1


def test_the_helper_starts_and_serves_until_told_to_stop(monkeypatch, tmp_path):
    hub = Hub()
    monkeypatch.setattr(selfcheck, "check", lambda log: True)
    monkeypatch.setattr(server, "read_settings", lambda: (MACHINE, hub.public, (23, 7)))
    monkeypatch.setattr(server, "listening_socket", lambda env: "a listener")
    served = []
    monkeypatch.setattr(server, "serve_one", lambda listener, helper: served.append((listener, helper.machine)))
    rounds = iter([True, True, False])
    assert main.serve(lambda: next(rounds)) == 0
    assert served == [("a listener", MACHINE)] * 2


def test_cancel_from_a_terminal_says_what_the_helper_answered(monkeypatch, capsys):
    monkeypatch.setattr(server, "ask", lambda message: {"say": "Cancelled."})
    assert main.cancel() == 0 and capsys.readouterr().out == "Cancelled.\n"

    def refused(message):
        raise ConnectionRefusedError("no socket")

    monkeypatch.setattr(server, "ask", refused)
    assert main.cancel() == 1 and "claude-voice-power.socket" in capsys.readouterr().err


def test_the_entry_point_picks_cancel_or_serve(monkeypatch):
    monkeypatch.setattr(main, "cancel", lambda: 7)
    monkeypatch.setattr(main, "serve", lambda: 9)
    monkeypatch.setattr(main.sys, "argv", ["claude-voice-power", "cancel"])
    with pytest.raises(SystemExit) as stopped:
        main.entry()
    assert stopped.value.code == 7
    monkeypatch.setattr(main.sys, "argv", ["claude-voice-power"])
    with pytest.raises(SystemExit) as stopped:
        main.entry()
    assert stopped.value.code == 9


# -- the helper's own package -------------------------------------------------------------------


def test_the_helper_is_a_package_of_its_own_with_every_dependency_locked_by_hash():
    root = SOURCE.parent.parent
    package = tomllib.loads((root / "power-helper/pyproject.toml").read_text())
    names = {re.split(r"[<>=]", dependency)[0] for dependency in package["project"]["dependencies"]}
    lock = (root / "power-helper/requirements.lock").read_text()
    entries = re.split(r"\n(?=\S)", lock.strip())
    locked = {entry.split("==")[0] for entry in entries}
    assert names <= locked, "a dependency the helper names has no locked entry"
    for entry in entries:
        assert "==" in entry.split()[0] and "--hash=sha256:" in entry, entry.split()[0]
    assert package["project"]["scripts"] == {"claude-voice-power": "claude_voice_power.__main__:entry"}
    assert package["build-system"]["build-backend"] == "uv_build"

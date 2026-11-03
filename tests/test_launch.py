"""How a session the bridge starts is started: with Remote Control, in a window on the desktop or in the background."""

import json
import os

import httpx
import pytest
from fakes import FakeClaude
from mcp.client import Client

from claude_voice import api, config, launch, transcripts
from claude_voice.oauth import OAuthProvider
from claude_voice.server import build_app, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store
from claude_voice.transcripts import background_command

PID = os.getpid()
SECRET = "s3cret-token-for-the-launch-tests-x"
BEARER = {"Authorization": f"Bearer {SECRET}"}
HOST = "http://127.0.0.1:8811"


# -- the command Claude Code is started with ---------------------------------------------


def test_a_session_in_a_window_runs_claude_code_inside_the_terminal_not_in_the_background():
    terminal = ["gnome-terminal", "--working-directory=/src/app", "--"]
    cmd = background_command("claude", "app-1", prompt="hello", remote_control=True, terminal=terminal)
    assert cmd[:3] == terminal and "--bg" not in cmd
    assert cmd[3:] == ["claude", "--name", "app-1", "--remote-control", "app-1", "--", "hello"]


def test_a_background_session_can_have_remote_control_too():
    cmd = background_command("claude", "app-1", remote_control=True)
    assert cmd == ["claude", "--bg", "--name", "app-1", "--remote-control", "app-1"]
    assert "--remote-control" not in background_command("claude", "app-1")


# -- which terminal ----------------------------------------------------------------------


def finder(*present):
    return lambda name: f"/usr/bin/{name}" if name in present else None


DESKTOP = {"WAYLAND_DISPLAY": "wayland-0"}
GNOME = finder("gnome-terminal")


def test_the_first_terminal_found_on_the_desktop_is_used():
    found = launch.terminal_for("/src/app", "app-1", None, finder("xterm", "gnome-terminal"), DESKTOP)
    assert found == (["gnome-terminal", "--working-directory=/src/app", "--"], "window")


def test_a_terminal_command_in_the_settings_file_wins_and_is_given_the_folder():
    found = launch.terminal_for("/src/app", "app-1", "kitty --directory {cwd}", finder("gnome-terminal"), DESKTOP)
    assert found == (["kitty", "--directory", "/src/app"], "window")


def test_without_a_desktop_the_session_opens_in_tmux_where_it_can_be_attached():
    found = launch.terminal_for("/src/app", "app-1", None, finder("gnome-terminal", "tmux"), {})
    assert found == (["tmux", "new-session", "-d", "-s", "app-1", "-c", "/src/app"], "tmux")


def test_with_no_terminal_at_all_there_is_none():
    assert launch.terminal_for("/src/app", "app-1", None, finder(), DESKTOP) == (None, None)


# -- the settings file -------------------------------------------------------------------


def test_new_sessions_get_remote_control_and_a_window_unless_the_settings_file_says_otherwise(tmp_path):
    assert config.load(tmp_path / "none.toml", env={"HOME": str(tmp_path)}).sessions == config.Sessions(
        remote_control=True, launch="terminal", terminal_command=None
    )
    f = tmp_path / "c.toml"
    f.write_text('[sessions]\nremote_control = false\nlaunch = "background"\nterminal_command = "kitty"\n')
    assert config.load(f, env={"HOME": str(tmp_path)}).sessions == config.Sessions(False, "background", "kitty")


def test_a_way_of_starting_a_session_that_does_not_exist_is_refused(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('[sessions]\nlaunch = "window"\n')
    with pytest.raises(config.ConfigError, match="launch"):
        config.load(f, env={"HOME": str(tmp_path)})


# -- starting, through the tools ---------------------------------------------------------


class Starter:
    def __init__(self, live):
        self.live, self.calls = live, []

    async def __call__(self, cwd, name, model=None, effort=None, prompt=None, settings=None, **how):
        self.calls.append(how)
        (self.live / f"{name}.json").write_text(
            json.dumps({"pid": PID, "sessionId": f"sid-{name}", "cwd": cwd, "name": name, "status": "idle"})
        )


@pytest.fixture
def world(tmp_path):
    (tmp_path / "home" / "billing").mkdir(parents=True)
    (tmp_path / "live").mkdir()
    return tmp_path


def bridge(world, starter, sessions=None, find=GNOME, env=DESKTOP):
    store = Store(world / "b.db")
    m = SessionManager(store, project_root=world / "home", client_factory=FakeClaude())
    repos = config.Config(config.Repos(), config.Paths(None, None, None, None), None, sessions or config.Sessions())
    return build_server(
        m,
        oauth=OAuthProvider(store, SECRET, HOST, static_token=SECRET),
        conversations=lambda **k: [],
        live_dir=world / "live",
        start_background=starter,
        start_seconds=1.0,
        repo_config=repos,
        find_program=find,
        desktop_env=env,
    )


async def test_a_started_session_opens_in_a_window_with_remote_control_by_default(world):
    starter = Starter(world / "live")
    async with Client(bridge(world, starter)) as c:
        out = (await c.call_tool("start_active_session", {"project": "billing", "name": "inv"})).structured_content
    assert starter.calls[0]["remote_control"] is True
    assert starter.calls[0]["terminal"][0] == "gnome-terminal"
    assert out["started_in"] == "window"


async def test_one_start_can_go_to_the_background_without_changing_the_setting(world):
    starter = Starter(world / "live")
    async with Client(bridge(world, starter)) as c:
        once = (
            await c.call_tool("start_active_session", {"project": "billing", "name": "a", "start_in": "background"})
        ).structured_content
        after = (await c.call_tool("start_active_session", {"project": "billing", "name": "b"})).structured_content
    assert starter.calls[0]["terminal"] is None and once["started_in"] == "background"
    assert after["started_in"] == "window"


async def test_with_no_terminal_to_open_the_session_starts_in_the_background_and_says_so(world):
    starter = Starter(world / "live")
    async with Client(bridge(world, starter, find=finder())) as c:
        out = (await c.call_tool("start_active_session", {"project": "billing", "name": "inv"})).structured_content
    assert starter.calls[0]["terminal"] is None and out["started_in"] == "background"
    assert "terminal" in out["note"]


# -- the settings page -------------------------------------------------------------------


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=HOST)


async def test_the_settings_page_reads_and_changes_how_new_sessions_start(world):
    starter = Starter(world / "live")
    server = bridge(world, starter)
    async with client(build_app(server)) as c:
        first = (await c.get("/api/v1/settings", headers=BEARER)).json()
        with api.HUB.subscribe() as events:
            changed = await c.put("/api/v1/settings", json={"start_in": "background"}, headers=BEARER)
            event = events.get_nowait()
        again = (await c.get("/api/v1/settings", headers=BEARER)).json()
    async with Client(server) as mcp:
        out = (await mcp.call_tool("start_active_session", {"project": "billing", "name": "x"})).structured_content
    assert first["remote_control"] is True and first["start_in"] == "terminal"
    assert changed.status_code == 200 and again["start_in"] == "background" and again["remote_control"] is True
    assert event[0] == "settings" and event[1]["start_in"] == "background"
    assert out["started_in"] == "background"


@pytest.mark.parametrize(
    "body", [{"start_in": "window"}, {"remote_control": "yes"}, {"terminal_command": "rm -rf ~"}, []]
)
async def test_a_setting_that_does_not_exist_or_does_not_fit_is_refused(world, body):
    async with client(build_app(bridge(world, Starter(world / "live")))) as c:
        refused = await c.put("/api/v1/settings", json=body, headers=BEARER)
    assert refused.status_code == 400


def test_a_started_session_does_not_inherit_the_identity_of_the_session_that_started_it():
    env = transcripts.clean_env(
        {
            "HOME": "/home/x",
            "CLAUDECODE": "1",
            "CLAUDE_CODE_SESSION_ID": "abc",
            "CLAUDE_CODE_CHILD_SESSION": "1",
            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
            "WAYLAND_DISPLAY": "wayland-0",
        }
    )
    assert env == {"HOME": "/home/x", "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1", "WAYLAND_DISPLAY": "wayland-0"}


def test_a_window_runs_claude_code_through_the_owners_login_shell():
    """So it gets the owner's PATH and settings from their shell files, as a terminal they open does."""
    terminal = ["gnome-terminal", "--working-directory=/src/app", "--"]
    cmd = background_command("claude", "app-1", terminal=terminal, shell="/bin/bash")
    assert cmd[:3] == terminal
    assert cmd[3:7] == ["/bin/bash", "-lic", 'exec "$@"', "claude-voice"]
    assert cmd[7:] == ["claude", "--name", "app-1"]
    assert "/bin/bash" not in background_command("claude", "app-1", shell="/bin/bash")  # not in the background


@pytest.mark.parametrize(
    ("shell", "used"),
    [("/bin/bash", "/bin/bash"), ("/usr/bin/zsh", "/usr/bin/zsh"), ("/usr/bin/fish", None), ("", None)],
)
def test_only_a_shell_that_takes_these_options_is_used(shell, used):
    assert transcripts.login_shell({"SHELL": shell}) == used

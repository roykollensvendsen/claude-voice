"""Roles: a background session started for a set purpose, such as contributing back to claude-voice."""

import json
import os
import subprocess

import httpx
import pytest
from fakes import FakeClaude
from mcp.client import Client

from claude_voice import guard, roles
from claude_voice.oauth import OAuthProvider
from claude_voice.server import build_app, build_server
from claude_voice.sessions import SessionManager
from claude_voice.store import Store

PID = os.getpid()
UPSTREAM = "roykollensvendsen/claude-voice"
SECRET = "s3cret-token-for-the-role-tests-xx"


# -- the guard that keeps a contribute session off the upstream repository ------------


def remotes(table):
    return lambda name: table.get(name)


@pytest.mark.parametrize(
    "command",
    [
        "git push origin HEAD",
        "git push",
        "git push -u origin feature",
        "git status && git push origin main",
        "git -C /x push upstream main",
        "git push https://github.com/roykollensvendsen/claude-voice.git HEAD:main",
    ],
)
def test_a_contribute_session_never_pushes_to_the_upstream_repository(command):
    urls = {"origin": "git@github.com:roykollensvendsen/claude-voice.git", "upstream": f"https://github.com/{UPSTREAM}"}
    reason = guard.refusal(command, UPSTREAM, remote_url=remotes(urls), default_remote=lambda: "origin")
    assert reason and UPSTREAM in reason


@pytest.mark.parametrize("command", ["git push fork feature", "git status", "echo git push is a phrase", "ls"])
def test_a_push_to_the_users_own_fork_or_no_push_at_all_is_let_through(command):
    urls = {"origin": f"https://github.com/{UPSTREAM}", "fork": "git@github.com:someone/claude-voice.git"}
    assert guard.refusal(command, UPSTREAM, remote_url=remotes(urls), default_remote=lambda: "fork") is None


def test_the_guard_answers_claude_code_as_a_pre_tool_use_hook(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "remote", "add", "origin", f"https://github.com/{UPSTREAM}"], check=True
    )
    hook_input = {"tool_name": "Bash", "tool_input": {"command": "git push origin HEAD"}, "cwd": str(tmp_path)}
    out = guard.answer(hook_input, UPSTREAM)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert guard.answer({"tool_name": "Read", "tool_input": {}, "cwd": str(tmp_path)}, UPSTREAM) == {}


# -- the roles themselves ----------------------------------------------------------------


def ready_clone(world):
    (world[1] / ".git").mkdir(exist_ok=True)


def test_the_built_in_contribute_role_is_there_and_a_users_file_can_change_or_add_roles(tmp_path):
    builtin = roles.load(None)
    assert builtin["contribute"]["upstream"] == UPSTREAM and builtin["contribute"]["guard"] is True
    mine = tmp_path / "roles.toml"
    mine.write_text('[roles.contribute]\nname = "my-contributor"\n\n[roles.review]\ntitle = "Review"\nfolder = "/x"\n')
    merged = roles.load(mine)
    assert merged["contribute"]["name"] == "my-contributor" and merged["contribute"]["upstream"] == UPSTREAM
    assert merged["review"]["title"] == "Review"


class Starter:
    def __init__(self, live):
        self.live = live
        self.calls = []

    async def __call__(self, cwd, name, model=None, effort=None, prompt=None, settings=None, **how):
        self.calls.append({"cwd": cwd, "name": name, "prompt": prompt, "settings": settings})
        (self.live / f"{name}.json").write_text(
            json.dumps(
                {"pid": PID, "sessionId": f"sid-{name}", "cwd": cwd, "name": name, "kind": "bg", "status": "busy"}
            )
        )


class Stopper:
    """Stands in for `claude stop`: the session's registry entry goes away."""

    def __init__(self, live):
        self.live = live

    async def __call__(self, session_id):
        for f in self.live.glob("*.json"):
            if json.loads(f.read_text()).get("sessionId") == session_id:
                f.unlink()


@pytest.fixture
def world(tmp_path):
    home = tmp_path / "home"
    clone = home / "claude-voice"
    clone.mkdir(parents=True)
    live = tmp_path / "live"
    live.mkdir()
    table = tmp_path / "roles.toml"
    table.write_text(f"[roles.contribute]\nfolder = '{clone}'\n")  # a literal string: Windows paths have backslashes
    return home, clone, live, table


def bridge(tmp_path, world, starter, oauth=None, host=None):
    home, _, live, table = world
    store = Store(tmp_path / "b.db")
    m = SessionManager(store, project_root=home, client_factory=FakeClaude())
    server = build_server(
        m,
        oauth=oauth(store) if oauth else None,
        conversations=lambda **k: [],
        live_dir=live,
        start_background=starter,
        find_program=lambda name: None,  # no terminal here: sessions start in the background
        stop_background=Stopper(live),
        start_seconds=1.0,
        roles_file=table,
        doctor_host=host or ReadyHost(),
    )
    return server


async def test_starting_the_contribute_role_runs_a_guarded_session_in_the_users_clone(tmp_path, world):
    _, clone, live, _ = world
    ready_clone(world)
    starter = Starter(live)
    async with Client(bridge(tmp_path, world, starter)) as c:
        out = (await c.call_tool("start_role", {"role": "contribute", "task": "Add a dark theme"})).structured_content
        stopped = (await c.call_tool("stop_active_session", {"name": out["name"]})).structured_content
    call = starter.calls[0]
    assert (
        call["cwd"] == str(clone) and out["role"] == "contribute" and out["name"].startswith("claude-voice-contribute")
    )
    assert "Add a dark theme" in call["prompt"] and UPSTREAM in call["prompt"] and "gh pr create" in call["prompt"]
    hook = json.loads(call["settings"])["hooks"]["PreToolUse"][0]
    assert hook["matcher"] == "Bash" and "claude_voice.guard" in hook["hooks"][0]["command"]
    assert stopped["status"] == "stopped"  # the bridge may stop what it started


async def test_an_unknown_role_is_refused(tmp_path, world):
    async with Client(bridge(tmp_path, world, Starter(world[2]))) as c:
        bad = await c.call_tool("start_role", {"role": "nobody"})
    assert bad.is_error and "contribute" in bad.content[0].text


async def test_the_event_api_lists_the_roles_and_starts_one(tmp_path, world):
    starter = Starter(world[2])
    ready_clone(world)

    def oauth(store):
        return OAuthProvider(store, SECRET, "http://127.0.0.1:8811", static_token=SECRET)

    app = build_app(bridge(tmp_path, world, starter, oauth=oauth))
    auth = {"Authorization": f"Bearer {SECRET}"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8811") as c:
        before = (await c.get("/api/v1/roles", headers=auth)).json()
        started = await c.post("/api/v1/roles/contribute/start", json={"task": "Fix a typo"}, headers=auth)
        after = (await c.get("/api/v1/roles", headers=auth)).json()
    role = before["roles"][0]
    assert role["name"] == "contribute" and role["title"] and role["session"] is None
    assert started.status_code == 200 and after["roles"][0]["session"]["name"] == started.json()["name"]


class ReadyHost:
    """A computer with every tool a contribute session needs, signed in and trusting every folder."""

    def which(self, name):
        return f"/usr/bin/{name}"

    def run(self, *command):
        return 0, ""

    def login(self):
        return True, "signed in"

    def trusted(self, folder):
        return True


class NoGitHubHost(ReadyHost):
    def which(self, name):
        return None if name == "gh" else super().which(name)


async def test_a_contribute_session_does_not_start_while_something_it_needs_is_missing(tmp_path, world):
    starter = Starter(world[2])
    (world[1] / ".git").mkdir()
    async with Client(bridge(tmp_path, world, starter, host=NoGitHubHost())) as c:
        bad = await c.call_tool("start_role", {"role": "contribute"})
    assert bad.is_error and "GitHub CLI" in bad.content[0].text and "gh auth login" in bad.content[0].text
    assert starter.calls == []


async def test_the_contribute_session_works_where_the_settings_say_and_sends_to_their_repository(tmp_path, world):
    from claude_voice import config

    home, _, live, table = world
    table.write_text("")  # no folder in the roles file: the settings decide
    mine = tmp_path / "elsewhere" / "cv"
    (mine / ".git").mkdir(parents=True)
    settings = tmp_path / "config.toml"
    settings.write_text(f"[repos]\nclaude_voice = 'someone/claude-voice'\n[paths]\ncontribute = '{mine}'\n")
    starter = Starter(live)
    store = Store(tmp_path / "b.db")
    m = SessionManager(store, project_root=home, client_factory=FakeClaude())
    server = build_server(
        m,
        conversations=lambda **k: [],
        live_dir=live,
        start_background=starter,
        find_program=lambda name: None,  # no terminal here: sessions start in the background
        stop_background=Stopper(live),
        start_seconds=1.0,
        roles_file=table,
        doctor_host=ReadyHost(),
        repo_config=config.load(settings, own_clone=None, env={}),
    )
    async with Client(server) as c:
        await c.call_tool("start_role", {"role": "contribute"})
    call = starter.calls[0]
    assert call["cwd"] == str(mine)
    assert "someone/claude-voice" in call["prompt"] and "someone/claude-voice" in call["settings"]

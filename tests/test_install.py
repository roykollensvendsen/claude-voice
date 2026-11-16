"""claude-voice plugins install: a plugin added from its checkout in one go, with a made-up
plugin in tmp_path. HOME and every config path are inside tmp_path; the owner's own files
are never read."""

import builtins
import getpass
import logging
import os
import socket
import stat
import subprocess
import sys
import tomllib

import pytest
from mcp.client import Client
from mcp.server.mcpserver import MCPServer

from claude_voice import cli, config, plugin_venv, plugins

# Owner-only file modes, symlinks and owner ids are what these tests check, and Windows has none
# of them as POSIX does. The bridge that installs plugins runs on Linux.
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes and symlinks")

MANIFEST = """\
manifest = 1
name = "fuel"
command = "uv"
args = ["run", "fuel-mcp", "serve"]
tools = ["fuel_price", "fuel_cheapest"]
timeout = 30
uses = ["home"]

[config]
default = "config.default.toml"
dir = "fuel-mcp"

[[config.personal]]
key = "sources.min_age_minutes"
type = "int"
range = [0, 1440]
ask = "Ignore prices older than how many minutes?"
"""
DEFAULT = 'fuel = "petrol"\n\n[sources]\nname = "drivstoffpriser"\nmin_age_minutes = 60\n'
# What the owner types: home, then the plugin's one personal value. None appears in any output.
LAT, LON, MINUTES = "59,91234", "10.74871", "1234"


def git(folder, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        cwd=folder,
        check=True,
        capture_output=True,
    )


def checkout(home, folder="fuel-mcp", manifest=MANIFEST, default=DEFAULT):
    """A made-up plugin's clean git checkout."""
    path = home / folder
    path.mkdir(parents=True)
    (path / "claude-voice-plugin.toml").write_text(manifest)
    (path / "config.default.toml").write_text(default)
    git(path, "init", "-q", "-b", "main")
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", "first")
    return path


def stand_in():
    server = MCPServer("fuel", instructions="Read prices aloud as they are.")

    @server.tool(description="The price at one station.")
    async def fuel_price(station: str) -> dict:
        return {"price": 20.0}

    @server.tool(description="The cheapest station near home.")
    async def fuel_cheapest() -> dict:
        return {"station": "x"}

    return server


class Owner:
    """What the owner types: getpass answers, and input() answers for the folder question and
    the approval. Records every prompt."""

    def __init__(self, secrets=(LAT, LON, MINUTES), lines=("fuel",)):
        self.secrets, self.lines, self.prompts = list(secrets), list(lines), []

    def secret(self, prompt=""):
        self.prompts.append(prompt)
        if not self.secrets:
            raise AssertionError(f"asked for a value that was not expected: {prompt}")
        return self.secrets.pop(0)

    def line(self, prompt=""):
        self.prompts.append(prompt)
        if not self.lines:
            raise AssertionError(f"asked a question that was not expected: {prompt}")
        return self.lines.pop(0)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home folder of its own, a terminal, the stand-in plugin and an approvals file, all
    in tmp_path."""
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("HOME", str(folder))
    monkeypatch.setattr(cli, "_terminal", lambda env: True)
    monkeypatch.setattr(plugins, "APPROVALS", folder / ".config/claude-voice/plugins-approved.json")
    monkeypatch.setattr(plugins, "CLIENT", lambda params: Client(stand_in()))
    # The checkout here is a manifest and no project, so there is nothing for uv to sync; the
    # stand-in answers however it is started.
    monkeypatch.setattr(plugin_venv, "ready", lambda spec: None)
    return folder


def env(home):
    return {"HOME": str(home), "CLAUDE_VOICE_CONFIG": str(bridge_file(home))}


def bridge_file(home):
    return home / ".config/claude-voice/config.toml"


def plugin_config(home):
    return home / ".config/fuel-mcp/config.toml"


def home_file(home):
    return home / ".config/claude-voice-home/home.toml"


def install(home, monkeypatch, owner=None, *extra, name="fuel", source="fuel-mcp"):
    owner = owner or Owner()
    monkeypatch.setattr(getpass, "getpass", owner.secret)
    monkeypatch.setattr(builtins, "input", owner.line)
    args = ["plugins", "install", name, *(["--from", str(home / source)] if source else []), *extra]
    return cli.main(args, env=env(home)), owner


def files(root):
    """Every file under root with its bytes and mode, to show that nothing changed."""
    return {
        p.relative_to(root): (p.read_bytes(), stat.S_IMODE(p.lstat().st_mode))
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.parts
    }


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


# -- the terminal -----------------------------------------------------------------------


def test_plugins_install_asks_only_in_a_real_terminal(tmp_path, monkeypatch, capsys):
    def never(prompt=""):
        raise AssertionError("asked a question outside a terminal")

    monkeypatch.setattr(getpass, "getpass", never)
    monkeypatch.setattr(builtins, "input", never)
    code = cli.main(["plugins", "install", "fuel", "--from", str(tmp_path / "none")], env={"HOME": str(tmp_path)})
    err = capsys.readouterr().err
    assert code == 1 and "plugins install asks you questions; run it in a terminal of your own" in err
    assert "does not exist" not in err  # refused before reading anything
    assert list(tmp_path.iterdir()) == []


# -- a first install ----------------------------------------------------------------------


def test_an_install_adds_the_entry_the_home_and_the_config_then_asks_for_approval(home, monkeypatch, capsys):
    checkout(home)
    bridge_file(home).parent.mkdir(parents=True)
    bridge_file(home).write_text('[sessions]\nlaunch = "background"\n')
    code, owner = install(home, monkeypatch)
    out = capsys.readouterr().out
    assert code == 0, out
    [spec] = config.load(bridge_file(home), env=env(home)).plugins
    assert spec.name == "fuel" and spec.command == "uv" and spec.cwd == str(home / "fuel-mcp")
    assert spec.tools == ["fuel_price", "fuel_cheapest"] and spec.timeout == 30
    assert tomllib.loads(bridge_file(home).read_text())["sessions"] == {"launch": "background"}
    assert tomllib.loads(home_file(home).read_text()) == {"lat": 59.91, "lon": 10.75}
    written = tomllib.loads(plugin_config(home).read_text())
    assert written == {
        "home_file": "~/.config/claude-voice-home/home.toml",
        "sources": {"name": "drivstoffpriser", "min_age_minutes": 1234},
        "fuel": "petrol",
    }
    for private in (bridge_file(home), home_file(home), plugin_config(home)):
        assert mode(private) == 0o600
    assert mode(home_file(home).parent) == 0o700 and mode(plugin_config(home).parent) == 0o700
    assert plugins.approved(spec)
    assert "Type fuel to approve it" in owner.prompts[-1]
    for step in ("1. Read the manifest", "2. Bridge config", "3. Home position", "4. Plugin config", "5. Approve"):
        assert step in out
    [backup] = bridge_file(home).parent.glob("config.toml.*.bak")
    assert mode(backup) == 0o600 and backup.read_text() == '[sessions]\nlaunch = "background"\n'
    assert f"undo: cp ~/.config/claude-voice/{backup.name} ~/.config/claude-voice/config.toml" in out
    assert "undo: rm ~/.config/claude-voice-home/home.toml" in out
    assert "undo: rm ~/.config/fuel-mcp/config.toml" in out
    assert "undo: claude-voice plugins revoke fuel" in out


def test_a_personal_value_never_appears_in_output_or_logs(home, monkeypatch, capsys, caplog):
    checkout(home)
    caplog.set_level(logging.DEBUG)
    install(home, monkeypatch)
    captured = capsys.readouterr()
    seen = captured.out + captured.err + caplog.text
    for value in (LAT, LON, MINUTES, "59.91", "10.75"):
        assert value not in seen


def test_a_value_out_of_range_is_asked_again_naming_the_range(home, monkeypatch, capsys):
    checkout(home)
    code, owner = install(home, monkeypatch, Owner(secrets=(LAT, LON, "5000", MINUTES)))
    out = capsys.readouterr().out
    assert code == 0 and "from 0 to 1440" in out and "5000" not in out
    assert tomllib.loads(plugin_config(home).read_text())["sources"]["min_age_minutes"] == 1234


def test_running_install_twice_changes_nothing(home, monkeypatch, capsys):
    checkout(home)
    install(home, monkeypatch)
    before = files(home)
    code, owner = install(home, monkeypatch, Owner(secrets=(), lines=()))
    out = capsys.readouterr().out
    assert code == 0 and files(home) == before
    assert "Already in your config" in out and "Using your home position" in out
    assert "kept your config at ~/.config/fuel-mcp/config.toml" in out and "already approved" in out
    assert "Nothing changed" in out


def test_no_network_call_is_made(home, monkeypatch):
    checkout(home)

    def no_network(*args, **kwargs):
        raise AssertionError("install reached for the network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    code, _ = install(home, monkeypatch)
    assert code == 0


# -- when something is already there ----------------------------------------------------


def test_a_different_plugin_entry_is_never_rewritten(home, monkeypatch, capsys):
    checkout(home)
    bridge_file(home).parent.mkdir(parents=True)
    mine = '[plugins.fuel]\ncommand = "fuel-mcp"\ncwd = "~/fuel-mcp"\ntools = ["fuel_price"]\n'
    bridge_file(home).write_text(mine)
    code, _ = install(home, monkeypatch, Owner(secrets=(), lines=()))
    out = capsys.readouterr().out
    assert code == 1 and bridge_file(home).read_text() == mine
    assert '-command = "fuel-mcp"' in out and '+command = "uv"' in out
    assert list(bridge_file(home).parent.glob("*.bak")) == []
    assert not home_file(home).exists() and not plugin_config(home).exists()


def test_the_new_bridge_config_must_parse_as_the_old_one_plus_exactly_the_new_entry(home, monkeypatch, capsys):
    checkout(home)
    bridge_file(home).parent.mkdir(parents=True)
    inline = 'plugins = { tv = { command = "tv-mcp", cwd = "/t", tools = ["tv_status"] } }\n'
    bridge_file(home).write_text(inline)
    code, _ = install(home, monkeypatch, Owner(secrets=(), lines=()))
    out = capsys.readouterr()
    assert code == 1 and bridge_file(home).read_text() == inline
    assert "[plugins.fuel]" in out.err  # the entry, printed to add by hand
    assert not home_file(home).exists() and not plugin_config(home).exists()


def test_a_hostile_command_cannot_add_a_table(home, monkeypatch):
    hostile = 'uv\\"\\n[plugins.evil]\\ncommand = \\"sh'  # as TOML text: a quote and newlines
    checkout(home, manifest=MANIFEST.replace('command = "uv"', f'command = "{hostile}"'))
    code, _ = install(home, monkeypatch)
    assert code == 0
    [spec] = config.load(bridge_file(home), env=env(home)).plugins
    assert spec.name == "fuel" and spec.command == 'uv"\n[plugins.evil]\ncommand = "sh'


def test_an_existing_plugin_config_is_kept(home, monkeypatch, capsys):
    checkout(home)
    plugin_config(home).parent.mkdir(parents=True)
    plugin_config(home).write_text("[sources]\nmin_age_minutes = 5\n")
    code, _ = install(home, monkeypatch, Owner(secrets=(LAT, LON)))  # no personal question
    out = capsys.readouterr().out
    assert code == 0 and plugin_config(home).read_text() == "[sources]\nmin_age_minutes = 5\n"
    assert "kept your config" in out
    assert 'home_file = "~/.config/claude-voice-home/home.toml"' in out  # the line it lacks, to add


def test_an_existing_home_position_is_kept_and_not_asked_again(home, monkeypatch, capsys):
    checkout(home)
    home_file(home).parent.mkdir(parents=True)
    home_file(home).write_text("lat = 60.39\nlon = 5.32\n")
    os.chmod(home_file(home), 0o600)
    code, _ = install(home, monkeypatch, Owner(secrets=(MINUTES,)))
    assert code == 0 and home_file(home).read_text() == "lat = 60.39\nlon = 5.32\n"
    assert "Using your home position" in capsys.readouterr().out


# -- what is refused ----------------------------------------------------------------------


def test_a_config_folder_that_is_neither_the_plugin_nor_its_checkout_is_refused(home, monkeypatch, capsys):
    checkout(home, manifest=MANIFEST.replace('dir = "fuel-mcp"', 'dir = "weather-mcp"'))
    code, _ = install(home, monkeypatch, Owner(secrets=(), lines=()))
    assert code == 1 and "weather-mcp" in capsys.readouterr().err
    assert not (home / ".config").exists()


def test_a_config_folder_no_known_plugin_uses_is_written_only_on_a_yes(home, monkeypatch, capsys):
    toaster = MANIFEST.replace('name = "fuel"', 'name = "toaster"').replace('"fuel-mcp"', '"toaster"')
    toaster = toaster.replace('tools = ["fuel_price", "fuel_cheapest"]', 'tools = ["fuel_price"]')
    checkout(home, folder="toaster", manifest=toaster)
    code, owner = install(home, monkeypatch, Owner(secrets=(LAT, LON), lines=("",)), name="toaster", source="toaster")
    out = capsys.readouterr().out
    assert code == 1 and not (home / ".config/toaster").exists()
    assert any("write it here? [y/N]" in p for p in owner.prompts)
    assert "undo: rm ~/.config/claude-voice-home/home.toml" in out  # what was done so far, and its undo
    code, _ = install(
        home, monkeypatch, Owner(secrets=(MINUTES,), lines=("y", "toaster")), name="toaster", source="toaster"
    )
    assert code == 0 and (home / ".config/toaster/config.toml").exists()


def test_a_symlinked_plugin_config_folder_is_refused_before_anything_is_written(home, monkeypatch, capsys):
    checkout(home)
    (home / "elsewhere").mkdir()
    (home / ".config").mkdir()
    (home / ".config/fuel-mcp").symlink_to(home / "elsewhere")
    code, _ = install(home, monkeypatch, Owner(secrets=(), lines=()))
    assert code == 1 and "symlink" in capsys.readouterr().err
    assert not bridge_file(home).exists() and list((home / "elsewhere").iterdir()) == []


def test_a_plugin_checkout_that_is_not_clean_or_not_on_its_branch_is_refused(home, monkeypatch, capsys):
    folder = checkout(home)
    (folder / "notes.txt").write_text("not committed")
    code, _ = install(home, monkeypatch, Owner(secrets=(), lines=()))
    assert code == 1 and "not committed" in capsys.readouterr().err
    (folder / "notes.txt").unlink()
    git(folder, "switch", "-q", "-c", "other")
    code, _ = install(home, monkeypatch, Owner(secrets=(), lines=()))
    assert code == 1 and "not on main" in capsys.readouterr().err
    assert not (home / ".config").exists()


def test_a_manifest_for_another_plugin_is_refused(home, monkeypatch, capsys):
    checkout(home)
    code, _ = install(home, monkeypatch, Owner(secrets=(), lines=()), name="tv")
    assert code == 1 and "fuel" in capsys.readouterr().err
    assert not (home / ".config").exists()


def test_a_missing_checkout_prints_the_clone_command_and_clones_nothing(home, monkeypatch, capsys):
    code, _ = install(home, monkeypatch, Owner(secrets=(), lines=()), source=None)
    err = capsys.readouterr().err
    assert code == 1 and "git clone https://github.com/roykollensvendsen/fuel-mcp ~/fuel-mcp" in err
    assert list(home.iterdir()) == []


# -- a dry run, and stopping halfway -----------------------------------------------------


def test_an_install_dry_run_writes_nothing(home, monkeypatch, capsys):
    checkout(home)
    bridge_file(home).parent.mkdir(parents=True)
    bridge_file(home).write_text('[sessions]\nlaunch = "background"\n')
    before = files(home)
    code, owner = install(home, monkeypatch, Owner(secrets=(), lines=()), "--dry-run")
    out = capsys.readouterr().out
    assert code == 0 and files(home) == before and owner.prompts == []
    assert "+[plugins.fuel]" in out and "+lat = ••.••" in out and '+min_age_minutes = "•••"' in out
    assert '+home_file = "~/.config/claude-voice-home/home.toml"' in out
    assert "would ask you to type fuel" in out and "nothing was written" in out.lower()


def test_install_stopped_halfway_says_what_it_changed_and_how_to_undo_it(home, monkeypatch, capsys):
    checkout(home)
    owner = Owner(secrets=(LAT,))

    def stop_at_longitude(prompt=""):
        if owner.secrets:
            return owner.secret(prompt)
        raise KeyboardInterrupt

    monkeypatch.setattr(getpass, "getpass", stop_at_longitude)
    monkeypatch.setattr(builtins, "input", owner.line)
    code = cli.main(["plugins", "install", "fuel", "--from", str(home / "fuel-mcp")], env=env(home))
    out = capsys.readouterr().out
    assert code == 1 and "Stopped" in out
    assert "new      ~/.config/claude-voice/config.toml" in out and "undo: rm ~/.config/claude-voice/config.toml" in out
    assert not home_file(home).exists()
    assert [p.name for p in home_file(home).parent.iterdir()] == [] if home_file(home).parent.exists() else True


def test_the_summary_names_the_plugins_own_step_for_its_secret(home, monkeypatch, capsys):
    checkout(home, manifest=MANIFEST + "")
    folder = home / "fuel-mcp"
    text = (
        (folder / "claude-voice-plugin.toml")
        .read_text()
        .replace("uses = [", 'secrets_step = "uv run fuel-mcp login"\nuses = [')
    )
    (folder / "claude-voice-plugin.toml").write_text(text)
    git(folder, "commit", "-q", "-am", "secret step")
    code, _ = install(home, monkeypatch)
    assert code == 0 and "uv run fuel-mcp login" in capsys.readouterr().out


# -- what the reviews asked for ------------------------------------------------------------


def test_the_known_plugins_are_the_ones_the_owner_runs():
    from claude_voice import install as installing

    names = set(installing.known())
    assert names == {
        "garage",
        "tv",
        "vacuum",
        "weather",
        "fuel",
        "easee",
        "grocery",
        "mister",
        "heatpump",
        "ams",
        "xbox",
    }
    assert installing.known()["grocery"].repo == "roykollensvendsen/grocery-mcp"


def test_the_old_checkout_folder_of_a_renamed_plugin_is_still_found(home, monkeypatch, capsys):
    grocery = MANIFEST.replace('name = "fuel"', 'name = "grocery"').replace('dir = "fuel-mcp"', 'dir = "grocery-mcp"')
    grocery = grocery.replace('tools = ["fuel_price", "fuel_cheapest"]', 'tools = ["fuel_price"]')
    checkout(home, folder="kassalapp-mcp", manifest=grocery)  # not renamed yet
    code, _ = install(home, monkeypatch, Owner(lines=("grocery",)), name="grocery", source=None)
    assert code == 0, capsys.readouterr()
    [spec] = config.load(bridge_file(home), env=env(home)).plugins
    assert spec.cwd == str(home / "kassalapp-mcp")
    assert (home / ".config/grocery-mcp/config.toml").exists()


def test_the_stop_for_a_different_entry_names_the_entry_and_how_to_compare(home, monkeypatch, capsys):
    checkout(home)
    bridge_file(home).parent.mkdir(parents=True)
    bridge_file(home).write_text('[plugins.fuel]\ncommand = "fuel-mcp"\ncwd = "~/fuel-mcp"\ntools = ["fuel_price"]\n')
    code, _ = install(home, monkeypatch, Owner(secrets=(), lines=()))
    err = capsys.readouterr().err
    assert code == 1 and "[plugins.fuel]" in err
    assert "claude-voice plugins install fuel --from ~/fuel-mcp --dry-run" in err


def test_the_approval_prompt_says_the_plugin_starts_after_its_name_is_typed(home, monkeypatch):
    checkout(home)
    code, owner = install(home, monkeypatch)
    assert code == 0 and "starts it once to read its tools" in owner.prompts[-1]


def test_a_plugin_config_inside_a_git_repository_is_refused_before_anything_is_written(home, monkeypatch, capsys):
    checkout(home, manifest=MANIFEST.replace('uses = ["home"]\n', ""))
    dotfiles = home / "dotfiles"
    (dotfiles / ".git").mkdir(parents=True)
    (dotfiles / "config").mkdir()
    (home / ".config").symlink_to(dotfiles / "config")
    code, _ = install(home, monkeypatch, Owner(secrets=(), lines=()))
    err = capsys.readouterr().err
    assert code == 1 and "git repository" in err and str(dotfiles) in err
    assert list((dotfiles / "config").iterdir()) == []

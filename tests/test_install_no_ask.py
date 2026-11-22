"""`plugins install <name> --no-ask`: a known plugin that needs nothing personal, installed
with no one at a terminal; it never approves (plugin-config-commands-v1, 2.4 and f2).
Every path is inside tmp_path; the owner's own files are never read."""

import sys
import tomllib

import pytest
from test_install import bridge_file, env, git, home_file, plugin_config

from claude_voice import cli, plugin_tip, plugins

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes, as in test_install")

PLAIN = """\
manifest = 1
name = "fuel"
command = "uv"
args = ["run", "fuel-mcp", "serve"]
tools = ["fuel_price", "fuel_cheapest"]
secrets_step = "fuel-mcp key set"
"""
WITH_HOME = PLAIN + 'uses = ["home"]\n\n[config]\ndefault = "config.default.toml"\ndir = "fuel-mcp"\n'
PERSONAL = (
    PLAIN
    + '\n[config]\ndefault = "config.default.toml"\ndir = "fuel-mcp"\n\n[[config.personal]]\n'
    + 'key = "sources.min_age_minutes"\ntype = "int"\nrange = [0, 1440]\nask = "How old?"\n'
)
ORIGIN = "git@github.com:roykollensvendsen/fuel-mcp.git"


def known_checkout(home, manifest=PLAIN, origin=ORIGIN):
    """The fuel plugin's checkout at ~/fuel-mcp, clean, pushing to its known repository, and
    level with the branch it follows."""
    path = home / "fuel-mcp"
    path.mkdir(parents=True)
    (path / "claude-voice-plugin.toml").write_text(manifest)
    (path / "config.default.toml").write_text("[sources]\nmin_age_minutes = 60\n")
    git(path, "init", "-q", "-b", "main")
    git(path, "add", ".")
    git(path, "commit", "-q", "-m", "first")
    git(path, "remote", "add", "origin", origin)
    git(path, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(path, "branch", "-q", "--set-upstream-to", "origin/main")
    return path


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home of its own, and no terminal: the session runs this, not the owner."""
    folder = tmp_path / "home"
    folder.mkdir()
    monkeypatch.setenv("HOME", str(folder))
    monkeypatch.setattr(cli, "_terminal", lambda env: False)
    monkeypatch.setattr(plugins, "APPROVALS", folder / ".config/claude-voice/plugins-approved.json")
    # GitHub agrees with the checkout: its branch's tip is the commit the checkout has out.
    monkeypatch.setattr(plugin_tip, "github_tip", lambda repo, branch: plugin_tip.head(str(folder / "fuel-mcp")))
    return folder


def no_ask(home, *extra):
    return cli.main(["plugins", "install", "fuel", "--no-ask", *extra], env=env(home))


def test_a_known_plugin_that_needs_nothing_personal_is_installed_with_no_one_at_a_terminal(home, capsys):
    known_checkout(home)
    assert no_ask(home) == 0
    said = capsys.readouterr().out
    table = tomllib.loads(bridge_file(home).read_text())["plugins"]["fuel"]
    assert table["tools"] == ["fuel_price", "fuel_cheapest"] and table["cwd"] == "~/fuel-mcp"
    assert "fuel-mcp key set" in said, "the plugin's own key step is the owner's next step"
    assert "claude-voice plugins approve fuel" in said
    assert no_ask(home) == 0
    assert "Nothing changed" in capsys.readouterr().out


def test_an_install_with_no_one_asked_never_approves(home):
    known_checkout(home)
    no_ask(home)
    assert plugins.read_approvals() == {}


@pytest.mark.parametrize(
    "spoil",
    [
        lambda path: (path / "stray.txt").write_text("x"),  # not clean
        lambda path: git(path, "commit", "-q", "--allow-empty", "-m", "ahead"),  # not level
        lambda path: git(path, "remote", "set-url", "origin", "git@github.com:someone/fuel-mcp.git"),
        lambda path: git(path, "remote", "set-url", "--add", "--push", "origin", "git@github.com:x/y.git"),
    ],
)
def test_a_checkout_not_clean_level_and_pushing_to_its_known_repository_is_refused(home, capsys, spoil):
    spoil(known_checkout(home))
    assert no_ask(home) == 1
    err = capsys.readouterr().err
    assert "claude-voice plugins install fuel" in err, "it names the owner's own command"
    assert not bridge_file(home).exists()


@pytest.mark.parametrize("manifest", [WITH_HOME, PERSONAL])
def test_a_plugin_that_would_ask_the_owner_anything_is_refused_before_anything_is_written(home, capsys, manifest):
    known_checkout(home, manifest)
    assert no_ask(home) == 1
    assert "asks you" in capsys.readouterr().err
    assert not bridge_file(home).exists() and not home_file(home).exists() and not plugin_config(home).exists()


def test_a_plugin_whose_answers_are_all_in_place_needs_no_one(home):
    known_checkout(home, WITH_HOME)
    home_file(home).parent.mkdir(parents=True)
    home_file(home).write_text("lat = 59.91\nlon = 10.75\n")
    assert no_ask(home) == 0


def test_a_checkout_outside_its_known_folder_is_refused(home, capsys):
    known_checkout(home)
    (home / "fuel-mcp").rename(home / "elsewhere")
    assert no_ask(home, "--from", str(home / "elsewhere")) == 1
    assert "--from" in capsys.readouterr().err


def test_without_no_ask_install_still_asks_only_in_a_real_terminal(home, capsys):
    known_checkout(home)
    assert cli.main(["plugins", "install", "fuel"], env=env(home)) == 1
    assert "terminal" in capsys.readouterr().err


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/github.com/roykollensvendsen/fuel-mcp",
        "git@evil.example:github.com/roykollensvendsen/fuel-mcp.git",
        "ext::sh -c x github.com/roykollensvendsen/fuel-mcp",
    ],
)
def test_only_a_github_address_of_the_plugins_own_repository_counts(home, capsys, url):
    git(known_checkout(home), "remote", "set-url", "origin", url)
    assert no_ask(home) == 1
    assert "push" in capsys.readouterr().err
    assert not bridge_file(home).exists()


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/roykollensvendsen/fuel-mcp",
        "ssh://git@github.com/roykollensvendsen/fuel-mcp.git",
        "git@github.com:roykollensvendsen/fuel-mcp.git/",
    ],
)
def test_each_github_address_form_of_the_plugins_own_repository_is_accepted(home, url):
    git(known_checkout(home), "remote", "set-url", "origin", url)
    assert no_ask(home) == 0


def test_a_checkout_whose_tracking_ref_was_moved_by_hand_is_refused(home, capsys, monkeypatch):
    """A local origin/main moved to match HEAD passes the level check; GitHub's tip does not."""
    known_checkout(home)
    monkeypatch.setattr(plugin_tip, "github_tip", lambda repo, branch: "a" * 40)
    assert no_ask(home) == 1
    assert "GitHub" in capsys.readouterr().err
    assert not bridge_file(home).exists()

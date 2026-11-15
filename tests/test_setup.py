"""`claude-voice setup --check`: every setup step, each done, missing or the person's own to run.

It is read-only, and that is proven rather than claimed: the command runs in a temporary
home, and every file's path, mode and contents are the same afterwards.
"""

import hashlib
import json
import os
import stat

from test_reposync import commit, git

from claude_voice import cli, setup

SIGNED_OUT = (False, "Claude Code is not signed in")
SIGNED_IN = (True, "Claude Code is signed in (claude.ai)")


def home_for(tmp_path, monkeypatch, config=""):
    """A home with nothing set up, the settings file pointed into it, and its env for the command."""
    home = tmp_path / "home"
    (home / "src").mkdir(parents=True)
    (home / ".config/claude-voice").mkdir(parents=True)
    settings = home / ".config/claude-voice/config.toml"
    settings.write_text(paths_config(home) + config, encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CLAUDE_VOICE_CONFIG", str(settings))
    return home, {
        "HOME": str(home),
        "CLAUDE_VOICE_ROOT": str(home / "src"),
        "CLAUDE_VOICE_CONFIG": str(settings),
        "PATH": os.environ.get("PATH", ""),
    }


def paths_config(home):
    return (
        "[paths]\n"
        f"claude_voice = '{home}/src/claude-voice'\n"
        f"claude_voice_ui = '{home}/src/claude-voice-ui'\n"
        f"claude_voice_mobile = '{home}/src/claude-voice-mobile'\n"
    )


def run(argv, env, capsys, login=SIGNED_OUT):
    code = cli.main(argv, env=env, login=lambda: login)
    out = capsys.readouterr()
    return code, out.out, out.err


def by_name(text):
    return {s["name"]: s for s in json.loads(text)["steps"]}


def snapshot(root):
    """Every path under `root`, with its mode and, for a file, a hash of its contents."""
    found = {}
    for path in sorted(root.rglob("*")):
        info = path.lstat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if stat.S_ISREG(info.st_mode) else ""
        found[str(path.relative_to(root))] = (stat.filemode(info.st_mode), digest)
    return found


def test_setup_check_reports_every_step_in_order(tmp_path, monkeypatch, capsys):
    home, env = home_for(tmp_path, monkeypatch)
    code, out, _ = run(["setup", "--check", "--json"], env, capsys)
    names = list(by_name(out))
    assert names[:4] == ["uv", "node", "claude_code", "bridge"]
    assert names[4:10] == ["config", "bridge_check", "signed_in", "service", "ui_build", "phone_route"]
    assert names[10:] == ["repo:claude-voice", "repo:claude-voice-ui", "repo:claude-voice-mobile"]
    assert code == 1


def test_every_step_is_done_missing_or_manual_and_says_what_to_do(tmp_path, monkeypatch, capsys):
    _, env = home_for(tmp_path, monkeypatch)
    _, out, _ = run(["setup", "--check", "--json"], env, capsys)
    for step in by_name(out).values():
        assert step["status"] in ("done", "missing", "manual")
        assert step["detail"]
        assert bool(step["fix"]) == (step["status"] != "done"), step


def test_a_step_only_a_person_can_take_is_theirs_to_run(tmp_path, monkeypatch, capsys):
    _, env = home_for(tmp_path, monkeypatch)
    _, out, _ = run(["setup", "--check", "--json"], env, capsys)
    steps = by_name(out)
    assert steps["signed_in"]["status"] == "manual" and "/login" in steps["signed_in"]["fix"]
    assert steps["phone_route"]["status"] == "manual" and "tailscale funnel" in steps["phone_route"]["fix"]


def test_a_signed_in_claude_code_is_done(tmp_path, monkeypatch, capsys):
    _, env = home_for(tmp_path, monkeypatch)
    _, out, _ = run(["setup", "--check", "--json"], env, capsys, login=SIGNED_IN)
    assert by_name(out)["signed_in"]["status"] == "done"


def test_a_missing_repository_says_how_to_clone_it(tmp_path, monkeypatch, capsys):
    home, env = home_for(tmp_path, monkeypatch)
    _, out, _ = run(["setup", "--check", "--json"], env, capsys)
    step = by_name(out)["repo:claude-voice-ui"]
    assert step["status"] == "missing"
    folder = home / "src" / "claude-voice-ui"
    assert f"git clone https://github.com/roykollensvendsen/claude-voice-ui {folder}" in step["fix"]


def test_an_optional_step_missing_does_not_fail_the_check():
    steps = [setup.Step("one", "done", "it is"), setup.Step("two", "missing", "it is not", "do it", optional=True)]
    assert setup.missing(steps) == []
    assert [s.name for s in setup.missing([*steps, setup.Step("three", "missing", "no", "do it")])] == ["three"]


def test_a_checkout_without_the_commit_hook_is_flagged(tmp_path, monkeypatch, capsys):
    home, env = home_for(tmp_path, monkeypatch)
    clone = home / "src/claude-voice"
    clone.mkdir()
    git(clone, "init", "-q", "-b", "main")
    git(clone, "remote", "add", "origin", "https://github.com/roykollensvendsen/claude-voice")
    (clone / ".githooks").mkdir()
    commit(clone, ".githooks/commit-msg")
    _, out, _ = run(["setup", "--check", "--json"], env, capsys)
    step = by_name(out)["hooks:claude-voice"]
    assert step["status"] == "missing" and "git config core.hooksPath .githooks" in step["fix"]
    git(clone, "config", "core.hooksPath", ".githooks")
    _, out, _ = run(["setup", "--check", "--json"], env, capsys)
    assert by_name(out)["hooks:claude-voice"]["status"] == "done"


def test_a_profile_plugin_that_is_not_configured_is_missing_and_one_not_approved_is_manual(
    tmp_path, monkeypatch, capsys
):
    home, env = home_for(tmp_path, monkeypatch)
    mine = home / "mine.toml"
    mine.write_text(
        '[[repo]]\nname = "weather-mcp"\nurl = "https://github.com/someone/weather-mcp"\n'
        '[[plugin]]\nname = "weather"\nrepo = "weather-mcp"\n'
        'block = """\n[plugins.weather]\ncommand = "weather-mcp"\ncwd = "{plugins_dir}"\n'
        'tools = ["weather_now"]\n"""\n',
        encoding="utf-8",
    )
    settings = home / ".config/claude-voice/config.toml"
    settings.write_text(f"profile = '{mine}'\n" + paths_config(home), encoding="utf-8")
    _, out, _ = run(["setup", "--check", "--json"], env, capsys)
    assert by_name(out)["plugin:weather"]["status"] == "missing"
    assert "[plugins.weather]" in by_name(out)["plugin:weather"]["fix"]
    settings.write_text(
        f"profile = '{mine}'\n"
        + paths_config(home)
        + f"[plugins.weather]\ncommand = 'weather-mcp'\ncwd = '{home}'\ntools = ['weather_now']\n",
        encoding="utf-8",
    )
    _, out, _ = run(["setup", "--check", "--json"], env, capsys)
    step = by_name(out)["plugin:weather"]
    assert step["status"] == "manual" and "claude-voice plugins approve weather" in step["fix"]


def test_setup_check_in_words_counts_the_steps_and_says_nothing_changed(tmp_path, monkeypatch, capsys):
    _, env = home_for(tmp_path, monkeypatch)
    code, out, _ = run(["setup", "--check"], env, capsys)
    first, *rest = out.splitlines()
    assert "missing" in first and "nothing was changed" in first
    assert any(line.startswith("manual") and "signed_in" in line for line in rest)
    assert code == 1


def test_setup_without_check_says_only_the_check_is_built(tmp_path, monkeypatch, capsys):
    _, env = home_for(tmp_path, monkeypatch)
    code, _, err = run(["setup"], env, capsys)
    assert code == 2 and "claude-voice setup --check" in err


def test_a_broken_profile_says_which_file_and_what_to_do(tmp_path, monkeypatch, capsys):
    home, env = home_for(tmp_path, monkeypatch)
    mine = home / "mine.toml"
    mine.write_text("[[repo]\n", encoding="utf-8")
    (home / ".config/claude-voice/config.toml").write_text(
        f"profile = '{mine}'\n" + paths_config(home), encoding="utf-8"
    )
    code, _, err = run(["setup", "--check"], env, capsys)
    assert code == 1 and "mine.toml" in err and "Fix it" in err


SECRET = "planted-s3cret-value-7f3a9c"


def test_setup_never_opens_the_service_s_env_file(tmp_path, monkeypatch, capsys):
    """The env file holds the login secret, so neither check may open it, even to read a name.

    It is planted with a known secret, and any open or read of its path raises.
    """
    import builtins
    import io
    import pathlib

    home, env = home_for(tmp_path, monkeypatch)
    planted = home / ".config/claude-voice/env"
    planted.write_text(
        f'CLAUDE_VOICE_TOKEN={SECRET}\nCLAUDE_VOICE_PUBLIC_HOSTS="a=b {SECRET}"\nnot a line with a key\n',
        encoding="utf-8",
    )
    env["CLAUDE_VOICE_ENV_FILE"] = str(planted)
    monkeypatch.setenv("CLAUDE_VOICE_ENV_FILE", str(planted))
    touched = []

    def guard(real):
        def opened(target, *args, **kwargs):
            if str(target) == str(planted) or (isinstance(target, pathlib.Path) and target == planted):
                touched.append(real.__name__)
                raise PermissionError(f"setup opened the env file through {real.__name__}")
            return real(target, *args, **kwargs)

        return opened

    for owner, name in ((builtins, "open"), (io, "open"), (pathlib.Path, "read_text"), (pathlib.Path, "read_bytes")):
        monkeypatch.setattr(owner, name, guard(getattr(owner, name)))
    monkeypatch.setattr(pathlib.Path, "open", guard(pathlib.Path.open))
    said = [run(argv, env, capsys) for argv in (["setup", "--check"], ["setup", "--check", "--json"])]
    said.append(run(["repos", "sync", "--check"], env, capsys))
    assert touched == []
    for _, out, err in said:
        assert SECRET not in out and SECRET not in err
    phone = by_name(said[1][1])["phone_route"]
    assert phone["status"] == "manual" and "CLAUDE_VOICE_PUBLIC_HOSTS" in phone["fix"]


def test_a_public_address_in_the_environment_makes_the_phone_step_done(tmp_path, monkeypatch, capsys):
    _, env = home_for(tmp_path, monkeypatch)
    env["CLAUDE_VOICE_PUBLIC_HOSTS"] = "bridge.example.org"
    _, out, _ = run(["setup", "--check", "--json"], env, capsys)
    assert by_name(out)["phone_route"]["status"] == "done"


def test_the_service_unit_path_agrees_with_the_install_script():
    """Two places name the unit file until the platform layer holds it; they must agree."""
    import pathlib
    import re

    script = (pathlib.Path(__file__).parent.parent / "deploy/install.sh").read_text(encoding="utf-8")
    [written] = re.findall(r'^unit="\$HOME/([^"]+)"', script, re.MULTILINE)
    assert written == setup.UNIT


def test_setup_check_and_repos_sync_check_change_nothing(tmp_path, monkeypatch, capsys):
    """The proof that both checks are read-only: every file under the home, before and after."""
    home, env = home_for(tmp_path, monkeypatch)
    clone = home / "src/claude-voice"
    clone.mkdir()
    git(clone, "init", "-q", "-b", "main")
    git(clone, "remote", "add", "origin", "https://github.com/roykollensvendsen/claude-voice")
    commit(clone, "a")
    os.utime(clone / "a", (1, 1))  # a stale index, which a plain `git status` would write back
    (home / "src/claude-voice-ui").mkdir()  # there, and not a clone
    before = snapshot(tmp_path)
    run(["setup", "--check"], env, capsys)
    run(["setup", "--check", "--json"], env, capsys)
    run(["repos", "sync", "--check"], env, capsys)
    assert snapshot(tmp_path) == before


def test_repos_sync_check_says_what_it_would_do_for_each_folder(tmp_path, monkeypatch, capsys):
    home, env = home_for(tmp_path, monkeypatch)
    (home / "src/claude-voice-ui").mkdir()
    code, out, _ = run(["repos", "sync", "--check"], env, capsys)
    lines = out.splitlines()
    assert lines[0].startswith("claude-voice:") and "would clone" in lines[0]
    assert lines[1].startswith("claude-voice-ui:") and "left alone" in lines[1]
    assert code == 0


def test_repos_sync_without_check_says_only_the_check_is_built(tmp_path, monkeypatch, capsys):
    _, env = home_for(tmp_path, monkeypatch)
    code, _, err = run(["repos", "sync"], env, capsys)
    assert code == 2 and "claude-voice repos sync --check" in err

"""A plugin's new tools are shown in full and added only by the owner's typed `<name> +<n>`.

The plain name stays a re-approval that never widens what the voice may call, since the owner
types it out of habit. Every test here writes a settings file in tmp_path and never touches the
owner's own.
"""

import contextlib
import threading
import time

import pytest
import tomlkit
from mcp.client import Client
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from claude_voice import cli, config, install, plugin_venv, plugins, safefile, tool_list

READS = ToolAnnotations(readOnlyHint=True)
SETTINGS = """\
# the owner's own note, kept through every write
[plugins.garage]
command = "garage-door-mcp"
cwd = "/g"
# the tools the voice may call
tools = ["garage_status"]
timeout = 30

[plugins.other]
command = "other-mcp"
cwd = "/o"
tools = ["other_status"]
"""


def stand_in(*, hours=True, arm=False, status=True, said="The opening hours."):
    """garage-door with the tools asked for: `garage_hours` only reads, `garage_arm` changes."""
    server = MCPServer("garage", instructions="Ask before arming.")
    if status:

        @server.tool(description="Where the door is.", annotations=READS)
        async def garage_status() -> dict:
            return {"door": "closed"}

    if hours:

        @server.tool(description=said, annotations=READS)
        async def garage_hours(day: str) -> dict:
            return {"open": "07-23", "day": day}

    if arm:

        @server.tool(description="Arm the alarm.")
        async def garage_arm() -> dict:
            return {"armed": True}

    return server


@pytest.fixture
def owner(tmp_path, monkeypatch):
    """A settings file and approvals in tmp_path, a terminal, and garage approved once before."""
    settings = tmp_path / "config.toml"
    settings.write_text(SETTINGS)
    approvals = tmp_path / "approved.json"
    monkeypatch.setattr(plugins, "APPROVALS", approvals)
    monkeypatch.setattr(plugin_venv, "ready", lambda spec: None)
    monkeypatch.setattr(cli, "_terminal", lambda env: True)
    env = {"HOME": str(tmp_path), "CLAUDE_VOICE_CONFIG": str(settings)}
    spec = config.load(settings, env=env).plugins[0]
    plugins.approve(spec, {"garage_status": {"description": "Where the door is.", "schema": {}}}, approvals)
    return settings, env


def serve(monkeypatch, **kw):
    monkeypatch.setattr(plugins, "CLIENT", lambda params: Client(stand_in(**kw)))


def answer(monkeypatch, *typed):
    lines = list(typed)

    def typed_line(prompt=""):
        if not lines:
            raise EOFError
        return lines.pop(0)

    monkeypatch.setattr("builtins.input", typed_line)


def tools_in(settings, name="garage"):
    return list(tomlkit.parse(settings.read_text())["plugins"][name]["tools"])


def approve(env):
    return cli.main(["plugins", "approve", "garage"], env=env)


# -- what the owner sees ------------------------------------------------------------------


def test_a_new_tool_is_shown_in_full_and_added_by_its_count(owner, monkeypatch, capsys):
    settings, env = owner
    serve(monkeypatch)
    answer(monkeypatch, "garage +1")
    assert approve(env) == 0
    shown = capsys.readouterr().out
    assert "garage_hours" in shown and "takes: day" in shown and "The opening hours." in shown
    assert "says it only reads" in shown
    assert "Type garage +1 to add these 1 tools and approve, or garage to approve without them" in shown
    assert tools_in(settings) == ["garage_status", "garage_hours"]
    text = settings.read_text()
    assert text == SETTINGS.replace('tools = ["garage_status"]', 'tools = ["garage_status", "garage_hours"]')
    spec = config.load(settings, env=env).plugins[0]
    assert plugins.approved(spec)


def test_the_plain_name_approves_and_adds_nothing(owner, monkeypatch, capsys):
    settings, env = owner
    serve(monkeypatch)
    answer(monkeypatch, "garage")
    assert approve(env) == 0
    assert settings.read_text() == SETTINGS
    assert plugins.approved(config.load(settings, env=env).plugins[0])
    assert not list(settings.parent.glob("*.bak"))


def test_a_wrong_count_is_refused_and_the_screen_is_shown_again(owner, monkeypatch, capsys):
    settings, env = owner
    serve(monkeypatch)
    answer(monkeypatch, "garage +2", "garage +1")
    assert approve(env) == 0
    shown = capsys.readouterr().out
    assert "garage +2 does not match" in shown
    assert shown.count("NEW TOOL garage_hours") == 2
    assert tools_in(settings) == ["garage_status", "garage_hours"]


def test_a_tool_gone_from_the_plugin_is_shown_and_dropped(owner, monkeypatch, capsys):
    settings, env = owner
    text = SETTINGS.replace('tools = ["garage_status"]', 'tools = ["garage_status", "garage_old"]')
    settings.write_text(text)
    serve(monkeypatch, hours=False)
    answer(monkeypatch, "garage")
    assert approve(env) == 0
    assert "garage_old: gone from the plugin, will be removed" in capsys.readouterr().out
    assert tools_in(settings) == ["garage_status"]


def test_a_new_write_tool_needs_plus_write(owner, monkeypatch, capsys):
    settings, env = owner
    serve(monkeypatch, hours=False, arm=True)
    answer(monkeypatch, "garage +1", "garage +1 +write")
    assert approve(env) == 0
    shown = capsys.readouterr().out
    assert "CAN CHANGE THINGS" in shown and "Type garage +1 +write to add" in shown
    assert tools_in(settings) == ["garage_status", "garage_arm"]


def test_plus_write_with_no_write_tool_is_refused(owner, monkeypatch, capsys):
    settings, env = owner
    serve(monkeypatch)
    answer(monkeypatch, "garage +1 +write")
    assert approve(env) == 1
    assert settings.read_text() == SETTINGS


def test_adding_tools_from_a_pipe_is_refused(owner, monkeypatch, capsys):
    settings, env = owner
    serve(monkeypatch)
    monkeypatch.setattr(cli, "_terminal", lambda env: False)
    answer(monkeypatch, "garage +1")
    assert approve(env) == 1
    assert "in a terminal" in capsys.readouterr().out
    assert settings.read_text() == SETTINGS


def test_a_new_tool_is_not_offered_to_the_voice_before_it_is_approved(owner, monkeypatch):
    settings, env = owner
    serve(monkeypatch)
    spec = config.load(settings, env=env).plugins[0]
    assert set(plugins.Plugin(spec, plugins.CLIENT).listing()) == {"garage_status"}
    assert set(plugins.Plugin(spec, plugins.CLIENT).listing(everything=True)) == {"garage_status", "garage_hours"}


# -- the plugin's own text ----------------------------------------------------------------


def test_plugin_text_is_shown_with_every_control_character_escaped():
    assert tool_list.shown("a\x1b[2Jb\rc") == "a\\x1b[2Jb\\x0dc"
    assert tool_list.shown("one\n\ttwo") == "one\n\ttwo"
    assert tool_list.shown("x\x9by") == "x\\x9by"


@pytest.mark.parametrize(
    ("name", "description", "why"),
    [
        ("garage_hours", "Hides \x1b[8m this", "control character"),
        ("garage_hours", "Back\rto the start", "control character"),
        ("garage_hours\n", "Fine.", "not a plain tool name"),
        ("Garage-Hours", "Fine.", "not a plain tool name"),
    ],
)
def test_a_tool_whose_name_or_description_holds_a_control_character_is_refused(name, description, why):
    spec = config.PluginSpec(name="garage", command="g", args=[], cwd="/g", tools=["garage_status"], timeout=1.0)
    found = tool_list.offer(spec, {name: {"description": description, "schema": {}, "read_only": True}})
    assert found.new == [] and [n for n, _ in found.refused] == [name]
    assert why in found.refused[0][1]


def test_a_multi_line_description_is_offered():
    spec = config.PluginSpec(name="garage", command="g", args=[], cwd="/g", tools=[], timeout=1.0)
    found = tool_list.offer(spec, {"garage_hours": {"description": "One.\n\tTwo.", "schema": {}, "read_only": True}})
    assert [n for n, _ in found.new] == ["garage_hours"]


def test_an_ansi_sequence_in_a_description_is_shown_escaped(owner, monkeypatch, capsys):
    settings, env = owner
    serve(monkeypatch, said="Harmless.\x1b[2K\x1b[1A")
    answer(monkeypatch, "garage")
    assert approve(env) == 0
    shown = capsys.readouterr().out
    assert "\x1b" not in shown and "Harmless.\\x1b[2K" in shown and "refused" in shown


# -- writing the settings file ------------------------------------------------------------


def test_only_the_plugins_tools_list_changes_and_comments_survive(owner):
    settings, _ = owner
    tool_list.write_tools(settings, "garage", ["garage_status", "garage_hours"])
    after = settings.read_text()
    assert "# the owner's own note" in after and "# the tools the voice may call" in after
    assert tools_in(settings, "other") == ["other_status"]


@pytest.mark.parametrize(
    "table",
    [
        '[plugins.garage]\ncommand = "g"\ncwd = "/g"\n# no list yet\n',
        '[plugins.garage]\ncommand = "g"\ncwd = "/g"\ntools = ["garage_status"] # one line\n',
        '[plugins.garage]\ncommand = "g"\ncwd = "/g"\ntools = [\n  "garage_status", # first\n]\n',
    ],
    ids=["no-tools-key", "one-line", "multi-line"],
)
def test_each_shape_of_tools_list_gets_the_right_list_with_comments_kept(tmp_path, table):
    settings = tmp_path / "config.toml"
    settings.write_text("# top\n" + table)
    tool_list.write_tools(settings, "garage", ["garage_status", "garage_hours"])
    assert tools_in(settings) == ["garage_status", "garage_hours"]
    assert "# top" in settings.read_text()
    for note in ("# no list yet", "# one line", "# first"):
        if note in table:
            assert note in settings.read_text()


def test_the_settings_file_keeps_its_mode(owner):
    settings, _ = owner
    settings.chmod(0o640)
    tool_list.write_tools(settings, "garage", ["garage_status", "garage_hours"])
    assert settings.stat().st_mode & 0o777 == 0o640


def test_a_plugin_never_approved_here_is_started_only_after_its_name_is_typed(owner, monkeypatch, capsys):
    """Its command and folder are new, so nothing of it runs before the owner's typed yes."""
    settings, env = owner
    plugins.APPROVALS.unlink()
    started = []
    monkeypatch.setattr(plugins, "CLIENT", lambda params: started.append(1) or Client(stand_in()))
    answer(monkeypatch, "no")
    assert approve(env) == 1
    assert started == [] and settings.read_text() == SETTINGS


def test_a_symlinked_settings_file_stays_a_link_and_its_target_changes(tmp_path):
    real = tmp_path / "dotfiles" / "config.toml"
    real.parent.mkdir()
    real.write_text(SETTINGS)
    link = tmp_path / "config.toml"
    link.symlink_to(real)
    tool_list.write_tools(link, "garage", ["garage_status", "garage_hours"])
    assert link.is_symlink() and link.resolve() == real
    assert tools_in(real) == ["garage_status", "garage_hours"]


def test_a_change_between_the_read_and_the_rename_writes_nothing(owner, monkeypatch):
    settings, _ = owner
    real = tomlkit.dumps

    def meanwhile(doc):
        settings.write_text(SETTINGS + "\n# an editor saved this\n")
        return real(doc)

    monkeypatch.setattr(tool_list.tomlkit, "dumps", meanwhile)
    with pytest.raises(tool_list.Changed, match="changed while approving; nothing was written; run it again"):
        tool_list.write_tools(settings, "garage", ["garage_status", "garage_hours"])
    assert settings.read_text() == SETTINGS + "\n# an editor saved this\n"


def test_a_change_while_the_owner_reads_the_screen_writes_nothing(owner, monkeypatch, capsys):
    """The list written comes from the file as it was when the screen was built, so a change made
    while the owner reads it, by hand or by install, must stop the write, not be overwritten."""
    settings, env = owner
    serve(monkeypatch)
    edited = SETTINGS.replace("# the tools the voice may call", "# edited by hand meanwhile")
    approvals = plugins.APPROVALS.read_bytes()

    def typed_line(prompt=""):
        settings.write_text(edited)
        return "garage +1"

    monkeypatch.setattr("builtins.input", typed_line)
    assert approve(env) == 1
    assert "changed while approving; nothing was written; run it again" in capsys.readouterr().out
    assert settings.read_text() == edited
    assert plugins.APPROVALS.read_bytes() == approvals


def test_a_list_changed_after_the_settings_were_loaded_is_not_used(owner, monkeypatch, capsys):
    """The screen is built from the file read for this approval, so a change between loading the
    settings and that read stops it too."""
    settings, env = owner
    serve(monkeypatch)
    answer(monkeypatch, "garage +1")
    edited = SETTINGS.replace('tools = ["garage_status"]', 'tools = ["garage_status", "garage_hours"]')
    known = plugins.known_place

    def and_meanwhile(spec):
        settings.write_text(edited)
        return known(spec)

    monkeypatch.setattr(plugins, "known_place", and_meanwhile)
    assert approve(env) == 1
    assert "changed while approving; nothing was written; run it again" in capsys.readouterr().out
    assert settings.read_text() == edited


def test_a_second_writer_waits_for_the_lock(owner):
    settings, _ = owner
    done = threading.Event()

    def write():
        tool_list.write_tools(settings, "garage", ["garage_status", "garage_hours"])
        done.set()

    with safefile.locked(settings):
        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        assert not done.wait(0.5), "the write went ahead while another writer held the lock"
    assert done.wait(30), "the write never went ahead once the lock was free"
    assert tools_in(settings) == ["garage_status", "garage_hours"]


def test_plugins_install_writes_the_settings_file_under_the_same_lock(tmp_path, monkeypatch):
    held = []

    @contextlib.contextmanager
    def record(path):
        held.append(path)
        yield

    monkeypatch.setattr(install.safefile, "locked", record)
    settings = tmp_path / "config.toml"
    run = install.Run.__new__(install.Run)
    run.dry = False
    with run.locked(settings):
        pass
    assert held == [settings]


def test_a_sixth_backup_removes_the_oldest(owner):
    settings, _ = owner
    for n in range(6):
        tool_list.write_tools(settings, "garage", ["garage_status"] + [f"garage_t{n}"])
        time.sleep(0.01)
    backups = sorted(settings.parent.glob("config.toml.*.bak"))
    assert len(backups) == 5
    assert SETTINGS not in {b.read_text() for b in backups}, "the oldest backup, the original, was kept"


def test_a_write_whose_re_parse_differs_puts_the_backup_back(owner, monkeypatch):
    settings, _ = owner
    real = tomlkit.dumps
    monkeypatch.setattr(tool_list.tomlkit, "dumps", lambda doc: real(doc).replace('"/o"', '"/elsewhere"'))
    with pytest.raises(tool_list.Mismatch):
        tool_list.write_tools(settings, "garage", ["garage_status", "garage_hours"])
    assert settings.read_text() == SETTINGS


def test_a_tool_name_that_is_not_plain_is_refused_with_nothing_written(owner):
    settings, _ = owner
    with pytest.raises(tool_list.Mismatch, match="not a plain tool name"):
        tool_list.write_tools(settings, "garage", ["garage_status", 'x"]\n[evil]\na = ["'])
    assert settings.read_text() == SETTINGS

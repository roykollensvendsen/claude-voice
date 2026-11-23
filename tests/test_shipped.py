"""Shipped rules reaching the repositories after an update (designs: rolling restart v2, §8a, §8b)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from claude_voice import accepted, org, permissions, shipped, waivers
from claude_voice.permissions import Rules

OWN = """
[programme]
code = "claude-voice"
name = "Voice"

[components.bridge]
name = "Bridge"
folders = ["."]

[components.plugins]
name = "Plugins"
folders = ["../garage", "../tv"]
"""

OTHER = """
[programme]
code = "org-b"
name = "Other work"

[components.main]
name = "Main"
folders = ["."]
"""

OLD = Rules(allow=["Bash(ls*)", "Bash(git status*)"], deny=["Bash(git reset --hard*)"], ask=["Bash(rm -r*)"])
SHA = "abc1234def"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(accepted, "RECORD", tmp_path / "state" / "accepted.json")
    monkeypatch.setattr(accepted, "COPIES", tmp_path / "state" / "accepted")
    for name, text in (("cv", OWN), ("other", OTHER)):
        chart_file = tmp_path / name / org.FILE
        chart_file.parent.mkdir(parents=True)
        chart_file.write_text(text, encoding="utf-8")
    for name in ("garage", "tv", "loose"):
        (tmp_path / name).mkdir()
    return tmp_path


def chart(home: Path) -> org.Chart:
    return org.load([home / "cv", home / "other"], remotes=lambda folder: ())


def settings_file(folder: Path) -> Path:
    return folder / permissions.SETTINGS


def lagging(home: Path, name: str, rules: Rules = OLD) -> Path:
    """A repository whose settings were written from an older table and accepted by the owner."""
    folder = home / name
    permissions.apply(folder, rules, backup_root=home / "backups")
    accepted.accept(accepted.show(folder))
    return folder


def sync(
    home: Path, rules: Rules, folders: list[str], the_chart: org.Chart | None = None
) -> dict[str, shipped.Outcome]:
    done = shipped.sync(
        [home / f for f in folders],
        the_chart if the_chart is not None else chart(home),
        home / "cv",
        rules,
        SHA,
        backup_root=home / "backups",
    )
    return {o.folder.name: o for o in done}


def with_(rules: Rules, **more: list[str]) -> Rules:
    return Rules(
        allow=rules.allow + more.get("allow", []),
        deny=rules.deny + more.get("deny", []),
        ask=rules.ask + more.get("ask", []),
        retired=more.get("retired", []),
    )


# -- what is written by itself ----------------------------------------------------------


def test_the_startup_sync_keeps_a_waived_ask_out_too(home):
    garage, tv = lagging(home, "garage"), lagging(home, "tv")
    review = "Bash(gh pr review*)"
    rules = replace(with_(OLD, ask=[review]), waivable=[review])
    file = home / "ask-waivers.toml"
    path = json.dumps(str(garage))  # a TOML string, so a Windows path's backslashes stay as they are
    file.write_text(f'[[folder]]\npath = {path}\nasks = ["{review}"]\nwhy = "its gate decides"\n', encoding="utf-8")
    found = waivers.read(rules, file)
    shipped.sync([garage, tv], chart(home), home / "cv", rules, SHA, backup_root=home / "backups", found=found)
    assert review not in json.loads(settings_file(garage).read_text())["permissions"].get("ask", [])
    assert review in json.loads(settings_file(tv).read_text())["permissions"]["ask"]


def test_a_shipped_rule_that_only_takes_rights_away_is_written_without_an_accept(home):
    folder = lagging(home, "garage")
    done = sync(home, with_(OLD, deny=["Bash(git clean -f*)"]), ["garage"])
    assert done["garage"].action == "written"
    assert "Bash(git clean -f*)" in json.loads(settings_file(folder).read_text())["permissions"]["deny"]
    entry = accepted.read_record()[str(settings_file(folder).resolve())]
    assert entry["source"] == f"shipped@{SHA}"
    assert entry["sha256"] == accepted.sha256(settings_file(folder).read_bytes())


def test_a_retired_allow_is_taken_out_without_an_accept(home):
    lagging(home, "garage")
    done = sync(home, Rules(allow=["Bash(git status*)"], deny=OLD.deny, ask=OLD.ask, retired=["Bash(ls*)"]), ["garage"])
    assert done["garage"].action == "written"


def test_a_repository_already_up_to_date_is_left_alone(home):
    folder = lagging(home, "garage")
    before = settings_file(folder).read_bytes()
    assert sync(home, OLD, ["garage"])["garage"].action == "same"
    assert settings_file(folder).read_bytes() == before


# -- what waits for the owner's accept --------------------------------------------------


def test_a_shipped_rule_that_widens_waits_for_the_owners_accept(home):
    folder = lagging(home, "garage")
    before = settings_file(folder).read_bytes()
    done = sync(home, with_(OLD, allow=["Bash(curl *)"], deny=["Bash(git clean -f*)"]), ["garage"])
    assert done["garage"].action == "waits"
    assert "permissions --accept" in done["garage"].why and "1 widens" in done["garage"].why
    assert settings_file(folder).read_bytes() == before


@pytest.mark.parametrize("rule", ["Bash(*)", "Edit", "Bash(*anything)", "mcp__garage-door"])
def test_a_broad_narrowing_waits_for_the_owners_accept(home, rule):
    folder = lagging(home, "garage")
    before = settings_file(folder).read_bytes()
    done = sync(home, with_(OLD, deny=[rule]), ["garage"])
    assert done["garage"].action == "waits" and "BROAD" in done["garage"].why
    assert settings_file(folder).read_bytes() == before


def test_a_file_the_owner_never_accepted_waits(home):
    folder = home / "garage"
    permissions.apply(folder, OLD, backup_root=home / "backups")
    before = settings_file(folder).read_bytes()
    done = sync(home, with_(OLD, deny=["Bash(git clean -f*)"]), ["garage"])
    assert done["garage"].action == "waits" and "not accepted" in done["garage"].why
    assert settings_file(folder).read_bytes() == before


def test_a_hand_edited_file_is_reported_and_never_overwritten(home):
    folder = lagging(home, "garage")
    edited = json.loads(settings_file(folder).read_text())
    edited["permissions"]["allow"].append("Bash(make *)")
    settings_file(folder).write_text(json.dumps(edited, indent=2) + "\n")
    before = settings_file(folder).read_bytes()
    done = sync(home, with_(OLD, deny=["Bash(git clean -f*)"]), ["garage"])
    assert done["garage"].action == "waits" and "changed since it was accepted" in done["garage"].why
    assert settings_file(folder).read_bytes() == before


def test_the_managers_own_repository_is_never_written_by_itself(home):
    folder = lagging(home, "cv")
    before = settings_file(folder).read_bytes()
    done = sync(home, with_(OLD, deny=["Bash(git clean -f*)"]), ["cv"])
    assert done["cv"].action == "waits" and "manager" in done["cv"].why
    assert settings_file(folder).read_bytes() == before


@pytest.mark.parametrize(
    "change",
    [
        {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]}},
        {"env": {"X": "1"}},
        {"permissions": {"defaultMode": "acceptEdits"}},
        {"permissions": {"additionalDirectories": ["/tmp"]}},
    ],
)
def test_a_change_to_anything_but_the_rule_lists_counts_as_widening(change):
    old = {"permissions": {"allow": ["Bash(ls*)"], "deny": []}}
    new = json.loads(json.dumps(old))
    for key, value in change.items():
        new.setdefault(key, {}).update(value) if isinstance(value, dict) and key in new else new.update({key: value})
    widens, broad = shipped.classify(json.dumps(old).encode(), json.dumps(new).encode())
    assert widens and not broad


def test_an_edited_allow_counts_as_widening():
    old = {"permissions": {"allow": ["Bash(git log*)"]}}
    new = {"permissions": {"allow": ["Bash(git log --oneline*)"]}}
    widens, _ = shipped.classify(json.dumps(old).encode(), json.dumps(new).encode())
    assert widens == ["+ allow Bash(git log --oneline*)"]


# -- only the bridge's own programme ----------------------------------------------------


def test_only_folders_the_chart_places_in_the_bridges_own_programme_are_written(home):
    other = lagging(home, "other")
    loose = lagging(home, "loose")
    before = {f: settings_file(f).read_bytes() for f in (other, loose)}
    done = sync(home, with_(OLD, deny=["Bash(git clean -f*)"]), ["other", "loose"])
    assert done["other"].action == "skipped" and "org-b" in done["other"].why
    assert done["loose"].action == "skipped" and "no programme" in done["loose"].why
    assert {f: settings_file(f).read_bytes() for f in (other, loose)} == before


@pytest.mark.parametrize("broken", ["missing", "unreadable"])
def test_a_chart_that_cannot_be_read_writes_nothing(home, broken):
    garage = lagging(home, "garage")
    before = settings_file(garage).read_bytes()
    own = home / "cv" / org.FILE
    if broken == "missing":
        own.unlink()
    else:
        own.write_text("[programme\ncode = ", encoding="utf-8")
    done = sync(home, with_(OLD, deny=["Bash(git clean -f*)"]), ["garage"])
    assert done["garage"].action == "skipped" and "org chart" in done["garage"].why
    assert settings_file(garage).read_bytes() == before


def test_the_report_names_each_folder_and_what_happened(home):
    lagging(home, "garage")
    lagging(home, "tv")
    done = shipped.sync(
        [home / "garage", home / "tv", home / "other"],
        chart(home),
        home / "cv",
        with_(OLD, deny=["Bash(git clean -f*)"]),
        SHA,
        backup_root=home / "backups",
    )
    lines = shipped.report(done)
    assert lines[0].startswith(f"{home / 'garage'}: written")
    assert any(line.startswith(f"{home / 'other'}: skipped") for line in lines)


def test_the_command_says_for_each_folder_what_happened(home, monkeypatch, capsys):
    from claude_voice import cli, roles

    lagging(home, "garage")
    monkeypatch.setattr(roles, "own_clone", lambda: home / "cv")
    env = {"HOME": str(home), "CLAUDE_VOICE_ROOT": str(home / "nowhere")}
    assert cli.main(["permissions", "--shipped", str(home / "garage"), str(home / "other")], env=env) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith(f"{(home / 'garage').resolve()}: ")
    assert out[1].startswith(f"{(home / 'other').resolve()}: skipped")


def test_a_file_changed_while_being_written_is_never_recorded_as_shipped(home, monkeypatch):
    """A session that writes an allow line between the check and the write must not have it
    recorded as accepted from the shipped rules."""
    folder = lagging(home, "garage")
    checked = shipped._waits

    def then_a_session_writes(file, old, new):
        why = checked(file, old, new)
        sneaked = json.loads(file.read_text())
        sneaked["permissions"]["allow"].append("Bash(curl *)")
        file.write_text(json.dumps(sneaked, indent=2) + "\n")
        return why

    monkeypatch.setattr(shipped, "_waits", then_a_session_writes)
    done = sync(home, with_(OLD, deny=["Bash(git clean -f*)"]), ["garage"])
    assert done["garage"].action == "waits" and "changed while" in done["garage"].why
    assert "Bash(curl *)" in settings_file(folder).read_text()
    entry = accepted.read_record()[str(settings_file(folder).resolve())]
    assert entry["source"] == "typed"


def test_the_shipped_rules_are_recorded_under_the_commit_that_is_installed(home, monkeypatch, capsys):
    import subprocess

    from claude_voice import cli, roles

    for args in (
        ["init", "-q"],
        ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "a"],
    ):
        subprocess.run(["git", "-C", str(home / "cv"), *args], check=True)
    head = subprocess.run(
        ["git", "-C", str(home / "cv"), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    folder = lagging(home, "garage")
    monkeypatch.setattr(roles, "own_clone", lambda: home / "cv")
    monkeypatch.setattr(permissions, "load", lambda **_: with_(OLD, deny=["Bash(git clean -f*)"]))
    env = {"HOME": str(home), "CLAUDE_VOICE_ROOT": str(home / "nowhere")}
    assert cli.main(["permissions", "--shipped", str(folder)], env=env) == 0
    assert accepted.read_record()[str(settings_file(folder).resolve())]["source"] == f"shipped@{head}"


@pytest.mark.parametrize(
    ("rule", "wide"),
    [("Edit", True), ("Bash", True), ("mcp__garage-door", True), ("mcp__claude-voice__approve", False)],
)
def test_a_whole_tool_or_a_whole_server_is_a_broad_rule_but_one_server_tool_is_not(rule, wide):
    assert shipped.broad(rule) is wide


def test_a_write_leaves_no_spare_file_behind(home):
    folder = lagging(home, "garage")
    done = sync(home, with_(OLD, deny=["Bash(git clean -f*)"]), ["garage"])
    assert done["garage"].action == "written"
    assert [p.name for p in settings_file(folder).parent.iterdir() if p.name.endswith(".shipped")] == []

"""Where the project's repositories are, on GitHub and on this computer: one file, with defaults."""

import subprocess

import pytest

from claude_voice import config


def git(folder, *args):
    subprocess.run(["git", "-C", str(folder), *args], check=True, capture_output=True)


def clone_with_remote(folder, url):
    folder.mkdir(parents=True)
    git(folder, "init", "-q")
    git(folder, "remote", "add", "origin", url)


def test_without_a_file_the_defaults_follow_the_clone_the_bridge_runs_from(tmp_path):
    here = tmp_path / "src" / "claude-voice"
    here.mkdir(parents=True)
    out = config.load(tmp_path / "missing.toml", own_clone=here, env={})
    assert out.repos.claude_voice == "roykollensvendsen/claude-voice"
    assert out.repos.claude_voice_ui == "roykollensvendsen/claude-voice-ui"
    assert out.repos.fork is None
    assert out.paths.claude_voice == here
    assert out.paths.claude_voice_ui == here.parent / "claude-voice-ui"
    assert out.paths.ui_dist == here.parent / "claude-voice-ui" / "dist"
    assert out.paths.contribute == here


def test_a_file_names_other_repositories_and_folders(tmp_path):
    file = tmp_path / "config.toml"
    file.write_text(
        "[repos]\n"
        'claude_voice = "someone/claude-voice"\n'
        'fork = "me/claude-voice"\n'
        "[paths]\n"
        "claude_voice = '~/code/cv'\n"
        "contribute = '~/code/cv-work'\n"
    )
    out = config.load(file, own_clone=None, env={"HOME": str(tmp_path)})
    assert out.repos.claude_voice == "someone/claude-voice" and out.repos.fork == "me/claude-voice"
    assert out.paths.claude_voice == tmp_path / "code" / "cv"
    assert out.paths.contribute == tmp_path / "code" / "cv-work"
    assert out.paths.ui_dist == tmp_path / "code" / "claude-voice-ui" / "dist"


def test_the_file_can_name_the_ui_repository_and_its_folders(tmp_path):
    file = tmp_path / "config.toml"
    file.write_text(
        "[repos]\n"
        'claude_voice_ui = "someone/claude-voice-ui"\n'
        "[paths]\n"
        "claude_voice_ui = '~/ui'\n"
        "ui_dist = '~/built'\n"
    )
    out = config.load(file, own_clone=tmp_path / "cv", env={"HOME": str(tmp_path)})
    assert out.repos.claude_voice_ui == "someone/claude-voice-ui"
    assert out.paths.claude_voice == tmp_path / "cv"
    assert out.paths.claude_voice_ui == tmp_path / "ui"
    assert out.paths.ui_dist == tmp_path / "built"
    assert out.file == file


def test_without_a_clone_or_a_file_no_folder_is_known(tmp_path):
    out = config.load(None, own_clone=None, env={"HOME": str(tmp_path)})
    assert out.file is None
    assert (out.paths.claude_voice, out.paths.claude_voice_ui, out.paths.ui_dist, out.paths.contribute) == (
        None,
        None,
        None,
        None,
    )


def test_the_settings_file_named_in_the_environment_wins_over_the_given_one(tmp_path):
    given, named = tmp_path / "given.toml", tmp_path / "named.toml"
    given.write_text('[repos]\nfork = "given/claude-voice"\n')
    named.write_text('[repos]\nfork = "named/claude-voice"\n')
    out = config.load(given, own_clone=None, env={"HOME": str(tmp_path), "CLAUDE_VOICE_CONFIG": "~/named.toml"})
    assert out.file == named
    assert out.repos.fork == "named/claude-voice"
    folder = tmp_path / "a-folder.toml"
    folder.mkdir()
    assert config.load(folder, own_clone=None, env={}).repos.fork is None  # not a file: the defaults


def test_the_ui_folder_setting_still_wins_over_the_file(tmp_path):
    out = config.load(None, own_clone=tmp_path, env={"CLAUDE_VOICE_UI_DIR": str(tmp_path / "built")})
    assert out.paths.ui_dist == tmp_path / "built"


@pytest.mark.parametrize("bad", ['claude_voice = "not a repo"', 'fork = "a/b/c"', 'claude_voice = "../etc"'])
def test_a_repository_that_is_not_owner_slash_name_is_refused(tmp_path, bad):
    file = tmp_path / "config.toml"
    file.write_text(f"[repos]\n{bad}\n")
    with pytest.raises(config.ConfigError, match="owner/name"):
        config.load(file, own_clone=tmp_path, env={})


def test_the_doctor_checks_each_folder_exists_is_a_clone_and_points_at_the_right_repository(tmp_path):
    good = tmp_path / "cv"
    clone_with_remote(good, "https://github.com/roykollensvendsen/claude-voice.git")
    wrong = tmp_path / "ui"
    clone_with_remote(wrong, "git@github.com:someone/other.git")
    out = config.load(None, own_clone=good, env={})
    out.paths.claude_voice_ui = wrong
    checks = {c["id"]: c for c in config.check_paths(out)}
    assert checks["path_claude_voice"]["ok"] is True
    assert checks["path_claude_voice_ui"]["ok"] is False and "someone/other" in checks["path_claude_voice_ui"]["detail"]
    assert "config.toml" in checks["path_claude_voice_ui"]["fix"]
    assert checks["path_ui_dist"]["ok"] is False  # never built here


def test_a_fork_counts_as_the_right_repository(tmp_path):
    mine = tmp_path / "cv"
    clone_with_remote(mine, "git@github.com:me/claude-voice.git")
    out = config.load(None, own_clone=mine, env={})
    assert {c["id"]: c for c in config.check_paths(out)}["path_claude_voice"]["ok"] is False
    out.repos.fork = "me/claude-voice"
    assert {c["id"]: c for c in config.check_paths(out)}["path_claude_voice"]["ok"] is True

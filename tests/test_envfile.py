"""The env file check: lines a shell or systemd would misread, named by line and KEY only.

Every value here is made up. No test reads the owner's real file (see conftest.py).
"""

import json
import logging

from claude_voice import envfile
from claude_voice.cli import main


def found(text):
    return [(p.line, p.key) for p in envfile.problems(text)]


def test_values_in_double_or_single_quotes_pass():
    text = 'A="made up words"\nB=\'two $words; here\'\nC="has \\"escaped\\" quotes"\nD=plain\nE=\n'
    assert envfile.problems(text) == []


def test_comments_and_blank_lines_pass():
    assert envfile.problems("# a comment with spaces $ ;\n\n   \n  # indented comment\nA=b\n") == []


def test_export_before_the_key_is_allowed_and_still_checked():
    assert found("export A=fine\nexport B=two words\n") == [(2, "B")]


def test_a_hash_inside_a_word_is_not_a_comment_and_passes():
    assert envfile.problems("A=abc#def\n") == []


def test_an_unquoted_value_with_a_space_is_flagged():
    (problem,) = envfile.problems("A=ok\nB=fine\nC=ok\nSOME_KEY=made via up\n")
    assert (problem.line, problem.key) == (4, "SOME_KEY")
    assert str(problem) == (
        'env line 4 (SOME_KEY): the value has a space and no quotes. Put the value in double quotes: SOME_KEY="…"'
    )


def test_an_unquoted_value_with_a_shell_character_is_flagged():
    for value in ("a$b", "a`b", "a;b", "a&b", "a|b", "a<b", "a>b", "a(b", "a)b", "a'b", 'a"b', "a\\b"):
        assert found(f"K={value}\n") == [(1, "K")], value


def test_a_quote_that_is_never_closed_is_flagged():
    assert found("A=\"never closed\nB='nor this\n") == [(1, "A"), (2, "B")]
    (problem,) = envfile.problems('A="ends with escaped \\"\n')
    assert "never closes" in problem.what


def test_text_after_the_closing_quote_is_flagged():
    assert found('A="quoted"tail\nB="quoted"   \n') == [(1, "A")]


def test_a_line_that_is_not_key_equals_value_is_flagged():
    assert found("just words\n1BAD=x\nexport\nGOOD=x\n") == [(1, None), (2, None), (3, None)]
    (problem,) = envfile.problems("just words\n")
    assert str(problem).startswith("env line 1: the line is not KEY=value")


MARK = "zqx"  # every made-up value carries it; nothing the check says may


def bad_file(tmp_path):
    path = tmp_path / "env"
    path.write_text(
        f"TOKEN={MARK}first {MARK}second\n"
        f'QUOTED="{MARK}open\n'
        f"SPECIAL={MARK}a;{MARK}b\n"
        f'AFTER="{MARK}in"{MARK}tail\n'
        f"{MARK}notakey words\n"
        f"export SPACED={MARK}a {MARK}b\n"
    )
    return path


def test_no_value_text_ever_appears_in_what_the_check_says(tmp_path, monkeypatch, capsys, caplog):
    path = bad_file(tmp_path)
    monkeypatch.setattr(envfile, "ENV_FILE", path)
    monkeypatch.setenv("CLAUDE_VOICE_ENV_FILE", str(path))
    said = [str(p) for p in envfile.problems(path.read_text())]
    main(["check"], env={"CLAUDE_VOICE_ROOT": str(tmp_path)}, login=lambda: (True, "signed in"))
    main(["check", "--json"], env={"CLAUDE_VOICE_ROOT": str(tmp_path)}, login=lambda: (True, "signed in"))
    with caplog.at_level(logging.WARNING):
        envfile.warn(logging.getLogger("claude_voice"))
    out = capsys.readouterr()
    assert len(said) == 6
    assert MARK not in "\n".join([*said, out.out, out.err, caplog.text])


def test_the_check_names_each_problem_with_its_fix(tmp_path, monkeypatch, capsys):
    path = tmp_path / "env"
    path.write_text("A=ok\nB=ok\nC=ok\nSOME_KEY=made up\n")
    monkeypatch.setattr(envfile, "ENV_FILE", path)
    code = main(["check"], env={"CLAUDE_VOICE_ROOT": str(tmp_path)}, login=lambda: (True, "signed in"))
    assert code == 0
    assert (
        'env line 4 (SOME_KEY): the value has a space and no quotes. Put the value in double quotes: SOME_KEY="…"'
        in capsys.readouterr().out
    )


def test_a_misread_env_file_line_is_a_warning_and_never_stops_the_check(tmp_path, monkeypatch, capsys):
    path = tmp_path / "env"
    path.write_text("SOME_KEY=made up\n")
    monkeypatch.setattr(envfile, "ENV_FILE", path)
    code = main(["check", "--json"], env={"CLAUDE_VOICE_ROOT": str(tmp_path)}, login=lambda: (True, "signed in"))
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["ready"] is True
    (warning,) = [c for c in out["checks"] if c["name"] == "env_file"]
    assert warning["ok"] is False and warning["required"] is False
    assert warning["detail"] == "env line 1 (SOME_KEY): the value has a space and no quotes"


def test_a_missing_env_file_says_nothing(tmp_path, capsys):
    code = main(["check", "--json"], env={"CLAUDE_VOICE_ROOT": str(tmp_path)}, login=lambda: (True, "signed in"))
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and not [c for c in out["checks"] if c["name"] == "env_file"]
    assert envfile.read({"CLAUDE_VOICE_ENV_FILE": str(tmp_path / "nowhere")}) == []


def test_the_bridge_logs_one_warning_line_per_problem(tmp_path, caplog):
    path = tmp_path / "env"
    path.write_text("A=one two\n# fine\nB=ok\nC=$x\n")
    with caplog.at_level(logging.WARNING):
        envfile.warn(logging.getLogger("claude_voice"), {"CLAUDE_VOICE_ENV_FILE": str(path)})
    lines = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(lines) == 2
    assert lines[0].startswith("env line 1 (A):") and lines[1].startswith("env line 4 (C):")


def test_a_check_that_fails_never_stops_the_bridge(monkeypatch, caplog):
    def broken(env):
        raise ValueError("made up")

    monkeypatch.setattr(envfile, "read", broken)
    with caplog.at_level(logging.WARNING):
        envfile.warn(logging.getLogger("claude_voice"))
    assert "could not check the env file" in caplog.text

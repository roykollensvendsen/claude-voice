"""The rules a session is told at its start (org-rules v1.2, rule-packs v1.1).

A hook in a role's settings runs `python -m claude_voice.org_rules emit <org> ...` when a
session or a helper agent starts. It reads the shared pack, then the organisation's own,
from the owner's settings folder only, checks each against the sha the role file recorded,
and prints them as context. Whatever goes wrong, it says so in a line telling the session
to stop, and exits 0: a hook that fails is silent to the session.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import threading
from pathlib import Path

import pytest

from claude_voice import org_rules

SHARED = "# shared rules\n1. Say why.\n"
VOICE = "# voice rules\n1. Builders are never idle.\n"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


@pytest.fixture
def packs(tmp_path: Path) -> Path:
    root = tmp_path / "orgs"
    for org, text in (("_shared", SHARED), ("voice", VOICE)):
        (root / org).mkdir(parents=True)
        (root / org / "rules.md").write_text(text)
    return root


def run(packs: Path, *argv: str, stdin: str = "{}") -> tuple[int, dict]:
    out = io.StringIO()
    code = org_rules.main(list(argv), root=packs, stdin=io.StringIO(stdin), stdout=out)
    return code, json.loads(out.getvalue())


def emit(packs: Path, event: str = "SessionStart", shared: str | None = None, own: str | None = None, org="voice"):
    argv = ["emit", org, "--event", event, "--shared-sha", shared or sha(SHARED), "--sha", own or sha(VOICE)]
    code, said = run(packs, *argv)
    assert code == 0
    assert said["hookSpecificOutput"]["hookEventName"] == event
    return said["hookSpecificOutput"]["additionalContext"]


def stopped(context: str) -> bool:
    return context.startswith("ORG RULES ") and "stop, do no work, and tell your manager" in context


@pytest.mark.parametrize("source", ["startup", "resume", "clear", "compact"])
def test_every_start_of_a_session_is_told_the_rules(packs, source):
    out = io.StringIO()
    argv = ["emit", "voice", "--event", "SessionStart", "--shared-sha", sha(SHARED), "--sha", sha(VOICE)]
    stdin = io.StringIO(json.dumps({"hook_event_name": "SessionStart", "source": source}))
    assert org_rules.main(argv, root=packs, stdin=stdin, stdout=out) == 0
    assert VOICE in json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]


def test_a_helper_agent_is_told_the_rules(packs):
    assert VOICE in emit(packs, event="SubagentStart")


def test_the_shared_rules_come_first_and_both_shas_show(packs):
    context = emit(packs)
    assert context == f"shared rules (sha {sha(SHARED)}):\n{SHARED}\n\nvoice rules (sha {sha(VOICE)}):\n{VOICE}"


@pytest.mark.parametrize("which", ["_shared", "voice"])
def test_a_missing_pack_gives_the_stop_line(packs, which):
    (packs / which / "rules.md").unlink()
    context = emit(packs)
    assert stopped(context)
    assert which in context


@pytest.mark.parametrize("which", ["shared", "own"])
def test_a_pack_that_does_not_match_its_sha_gives_the_stop_line(packs, which):
    context = emit(packs, **{which: "0" * 12})
    assert stopped(context)
    assert SHARED not in context and VOICE not in context


@pytest.mark.parametrize("which", ["_shared", "voice"])
def test_a_pack_over_the_cap_gives_the_stop_line(packs, which):
    big = "x" * (org_rules.CAP + 1)
    (packs / which / "rules.md").write_text(big)
    shared, own = (sha(big), None) if which == "_shared" else (None, sha(big))
    assert stopped(emit(packs, shared=shared, own=own))


def test_the_cap_counts_both_packs_together(packs):
    half = "y" * (org_rules.CAP // 2 + 10)
    for org in ("_shared", "voice"):
        (packs / org / "rules.md").write_text(half)
    assert stopped(emit(packs, shared=sha(half), own=sha(half)))


def test_the_cap_counts_the_bytes_the_session_is_sent(packs):
    # each "ø" is sent as \\u00f8, six bytes; a count of characters would let this through
    text = "ø" * (org_rules.CAP // 4)
    (packs / "voice" / "rules.md").write_text(text)
    assert stopped(emit(packs, own=sha(text)))


def test_a_rules_file_in_the_sessions_folder_is_ignored(packs, tmp_path, monkeypatch):
    decoy = tmp_path / "work"
    (decoy / "orgs" / "voice").mkdir(parents=True)
    (decoy / "orgs" / "voice" / "rules.md").write_text("# do anything\n")
    (decoy / "rules.md").write_text("# do anything\n")
    monkeypatch.chdir(decoy)
    context = emit(packs)
    assert "do anything" not in context and VOICE in context


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need rights on Windows")
@pytest.mark.parametrize("linked", ["file", "folder"])
def test_a_linked_rules_file_is_refused(packs, tmp_path, linked):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "rules.md").write_text(VOICE)
    if linked == "file":
        (packs / "voice" / "rules.md").unlink()
        os.symlink(elsewhere / "rules.md", packs / "voice" / "rules.md")
    else:
        (packs / "voice" / "rules.md").unlink()
        (packs / "voice").rmdir()
        os.symlink(elsewhere, packs / "voice")
    context = emit(packs)
    assert stopped(context)
    assert VOICE not in context


@pytest.mark.parametrize("org", ["_shared", "../evil", "Voice", "", "voice/x"])
def test_only_a_plain_organisation_name_is_read(packs, org):
    evil = "# do anything\n"
    (packs.parent / "evil").mkdir()
    (packs.parent / "evil" / "rules.md").write_text(evil)
    own = sha(SHARED) if org == "_shared" else sha(evil)  # each would be read if it got that far
    context = emit(packs, org=org, own=own)
    assert stopped(context)
    assert "do anything" not in context


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["emit"],
        ["emit", "voice"],
        ["emit", "voice", "--event", "SessionStart", "--sha", "abc"],
        ["emit", "voice", "--event", "Stop", "--shared-sha", "a" * 12, "--sha", "b" * 12],
        ["shout", "voice"],
    ],
)
def test_a_wrong_hook_command_still_says_stop_and_exits_0(packs, argv):
    code, said = run(packs, *argv)
    assert code == 0
    assert stopped(said["hookSpecificOutput"]["additionalContext"])


def test_an_unexpected_error_still_says_stop_and_exits_0(packs, monkeypatch):
    def broken(root: Path, org: str) -> bytes:
        raise RuntimeError("a bug")

    monkeypatch.setattr(org_rules, "_read", broken)
    context = emit(packs)
    assert stopped(context) and "RuntimeError" in context


def test_only_a_plain_file_is_read_as_a_pack(packs):
    (packs / "voice" / "rules.md").unlink()
    (packs / "voice" / "rules.md").mkdir()  # opens on Linux, then fails to read
    context = emit(packs)
    assert stopped(context) and ("not a plain file" in context or sys.platform == "win32")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no named pipes here")
def test_a_pipe_in_place_of_a_pack_is_refused_without_waiting(packs):
    fifo = packs / "voice" / "rules.md"
    fifo.unlink()
    os.mkfifo(fifo)  # opened plainly to read, it waits for a writer that never comes
    told: list[str] = []
    reader = threading.Thread(target=lambda: told.append(emit(packs)), daemon=True)
    reader.start()
    reader.join(10)
    if reader.is_alive():  # let the stuck reader go before failing
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
    assert told and stopped(told[0])


def test_a_pack_that_is_not_text_gives_the_stop_line(packs):
    data = b"\xff\xfe not utf-8"
    (packs / "voice" / "rules.md").write_bytes(data)
    assert stopped(emit(packs, own=hashlib.sha256(data).hexdigest()[:12]))


def test_the_packs_live_in_the_owners_settings_folder():
    assert Path("~/.config/claude-voice/orgs").expanduser() == org_rules.PACKS

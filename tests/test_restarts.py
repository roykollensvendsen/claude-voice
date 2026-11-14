"""Why the bridge last restarted: the reason file, systemd's count, the machine's uptime."""

import json
from datetime import UTC, datetime

from claude_voice import restarts

NOW = datetime(2026, 11, 14, 8, 45, tzinfo=UTC).timestamp()
SYSTEMD = {"INVOCATION_ID": "abc"}


class Git:
    """Stands in for git: the commit the bridge runs from, and a count per range."""

    def __init__(self, head="bbbbbbb", counts=None):
        self.head = head
        self.counts = counts or {}
        self.asked = []

    def __call__(self, *args):
        self.asked.append(args)
        if args[:1] == ("rev-parse",):
            return self.head
        return self.counts.get(args[-1])


def written(state, seconds_ago, **body):
    at = datetime.fromtimestamp(NOW - seconds_ago, UTC).isoformat(timespec="seconds")
    (state / restarts.REASON_FILE).write_text(json.dumps({"at": at, **body}))


def info(state, *, total=3, seen=3, up=3600.0, git=None, env=SYSTEMD):
    if seen is not None:
        (state / restarts.COUNT_FILE).write_text(str(seen))
    return restarts.restart_info(
        state, environ=env, nrestarts=lambda: total, uptime=lambda: up, git=git or Git(), now=lambda: NOW
    )


def test_a_fresh_reason_file_gives_its_reason(tmp_path):
    written(tmp_path, 30, reason="update", by="update_bridge", **{"from": "aaaaaaa1", "to": "ccccccc2"})
    record = info(tmp_path, git=Git(counts={"aaaaaaa1..ccccccc2": "4"}))
    assert record["reason"] == "update" and record["by"] == "update_bridge"
    assert (record["from"], record["to"], record["changes"]) == ("aaaaaaa", "ccccccc", 4)


def test_a_planned_restart_keeps_what_it_was_for(tmp_path):
    path = restarts.note(tmp_path, "planned", "cli", text="a new plugin")
    body = json.loads(path.read_text())
    body["at"] = datetime.fromtimestamp(NOW - 5, UTC).isoformat()
    path.write_text(json.dumps(body))
    record = info(tmp_path)
    assert (record["reason"], record["by"], record["note"]) == ("planned", "cli", "a new plugin")


def test_the_reason_file_is_read_once(tmp_path):
    written(tmp_path, 30, reason="update")
    assert restarts.take(tmp_path)["reason"] == "update"
    assert not (tmp_path / restarts.REASON_FILE).exists()
    assert restarts.take(tmp_path) is None


def test_a_reason_file_older_than_ten_minutes_is_ignored(tmp_path):
    written(tmp_path, 11 * 60, reason="update", by="update_bridge")
    record = info(tmp_path)
    assert record["reason"] == "unplanned" and record["by"] is None
    assert not (tmp_path / restarts.REASON_FILE).exists()


def test_a_restart_by_systemd_with_no_reason_file_is_a_crash(tmp_path):
    assert info(tmp_path, total=4, seen=3)["reason"] == "crash"
    assert (tmp_path / restarts.COUNT_FILE).read_text() == "4"


def test_a_stale_reason_file_does_not_hide_a_crash(tmp_path):
    written(tmp_path, 60 * 60, reason="update")
    assert info(tmp_path, total=4, seen=3)["reason"] == "crash"


def test_a_start_soon_after_the_machine_booted_is_a_boot(tmp_path):
    assert info(tmp_path, up=90.0, seen=None)["reason"] == "boot"


def test_any_other_start_is_unplanned(tmp_path):
    assert info(tmp_path)["reason"] == "unplanned"


def test_changes_counts_the_commits_since_the_last_start(tmp_path):
    (tmp_path / restarts.COMMIT_FILE).write_text("aaaaaaa")
    record = info(tmp_path, git=Git(head="bbbbbbb", counts={"aaaaaaa..bbbbbbb": "4"}))
    assert (record["from"], record["to"], record["changes"]) == ("aaaaaaa", "bbbbbbb", 4)
    assert (tmp_path / restarts.COMMIT_FILE).read_text() == "bbbbbbb"


def test_changes_is_unknown_on_the_first_start_or_without_git(tmp_path):
    assert info(tmp_path)["changes"] is None
    assert info(tmp_path, git=Git(head=None))["changes"] is None


def test_a_start_outside_systemd_leaves_the_reason_file_for_the_service(tmp_path):
    written(tmp_path, 30, reason="update")
    record = info(tmp_path, env={})
    assert record["reason"] is None and record["total"] is None
    assert (tmp_path / restarts.REASON_FILE).exists()


def test_an_unreadable_count_is_not_a_crash(tmp_path):
    record = info(tmp_path, total=None)
    assert record["reason"] == "unplanned" and record["total"] is None


def test_the_real_readers_give_an_answer_or_none():
    assert restarts._uptime() is None or restarts._uptime() > 0
    assert restarts.systemctl(["/nonexistent/systemctl"]) == 1
    assert restarts.changes(lambda *a: "not a number", "a", "b") is None


def test_a_note_is_one_short_line(tmp_path):
    long = "first line\nsecond\tline \x1b[31mred\x07 " + "x" * 300
    body = json.loads(restarts.note(tmp_path, "planned", "cli", text=long).read_text())
    assert "\n" not in body["note"] and "\x1b" not in body["note"] and "\x07" not in body["note"]
    assert body["note"].startswith("first line second line [31mred")
    assert len(body["note"]) <= restarts.NOTE_CHARS


def test_a_stale_reason_file_is_logged_once_as_ignored(tmp_path, caplog):
    written(tmp_path, 11 * 60, reason="update", by="update_bridge")
    with caplog.at_level("INFO", logger="claude_voice.restarts"):
        info(tmp_path)
    ignored = [r for r in caplog.records if "ignored" in r.getMessage()]
    assert len(ignored) == 1 and "update" in ignored[0].getMessage()


def test_a_note_written_by_hand_is_still_quoted_as_one_short_line(tmp_path):
    written(tmp_path, 30, reason="planned\nplus", by="someone\x1b", note="a\nb" + "y" * 500)
    record = info(tmp_path)
    assert record["reason"] == "planned plus" and record["by"] == "someone"
    assert record["note"].startswith("a b") and len(record["note"]) <= restarts.NOTE_CHARS

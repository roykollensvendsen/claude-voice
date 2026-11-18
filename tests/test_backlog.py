"""A programme's work list: how many items wait, and reading it only inside the state folder."""

import os

import pytest

from claude_voice import backlog

QUEUE = """# night queue
Each item: claim it by writing "lane1: taken HH:MM" under it.

1. A wait time that is not a number is refused.
   lane1: taken 23:55
   done: branch fix/timeout, refused at once
2. The log says once that a machine is off.
   lane1: taken 00:04
3. Plugin tools carry their read-only mark.
4. A reminder before the sign-in runs out.
   blocked: needs the owner's login
- A doc change for the first-line rule.
  * a note that is no item of its own
"""


def test_an_item_with_no_taken_or_done_line_under_it_waits():
    assert backlog.count(QUEUE) == backlog.Counts(waiting=2, blocked=1)


def test_an_item_marked_blocked_counts_apart():
    counts = backlog.count("1. one\n   blocked: waits on a review\n2. two\n   blocked: no key yet\n")
    assert counts == backlog.Counts(waiting=0, blocked=2)


def test_an_empty_list_has_nothing_waiting():
    assert backlog.count("# heading\n\nSome prose.\n") == backlog.Counts(waiting=0, blocked=0)


def test_a_taken_item_stays_taken_when_it_is_also_blocked():
    assert backlog.count("1. one\n   lane1: taken 10:00\n   blocked: stuck\n") == backlog.Counts(0, 0)


def test_the_counts_are_said_with_the_blocked_ones():
    assert backlog.Counts(3, 2).said() == "3 items waiting, 2 blocked"
    assert backlog.Counts(1, 0).said() == "1 item waiting"


@pytest.fixture
def state(tmp_path):
    folder = tmp_path / "state"
    folder.mkdir()
    (folder / "queue.md").write_text(QUEUE)
    return folder


def test_a_list_inside_the_state_folder_is_read(state):
    assert backlog.read(state, "queue.md") == backlog.Counts(2, 1)


def test_a_backlog_is_read_only_inside_the_state_folder(state, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text(QUEUE)
    for written in ("../outside.md", str(outside), "sub/../../outside.md"):
        with pytest.raises(backlog.Outside):
            backlog.read(state, written)


def test_a_backlog_that_links_out_of_the_state_folder_is_refused(state, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text(QUEUE)
    os.symlink(outside, state / "link.md")
    with pytest.raises(backlog.Outside):
        backlog.read(state, "link.md")


def test_a_backlog_that_is_missing_has_nothing_waiting(state):
    assert backlog.read(state, "gone.md") == backlog.Counts(0, 0)

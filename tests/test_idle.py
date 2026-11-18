"""Builders left idle while work waits: when the manager and the voice are told."""

import pytest

from claude_voice import idle, org

ORG = """
[programme]
code = "acme"
name = "Acme Robots"
backlog = "queue.md"
idle_report = true

[roles.manager]
name = "Manager"
sessions = ["acme-boss"]

[roles.lane1]
name = "Lane one"
sessions = ["lane-a"]
builder = true

[roles.lane2]
name = "Lane two"
sessions = ["lane-b"]
builder = true
"""

QUEUE = "1. one\n2. two\n   lane1: taken 10:00\n3. three\n4. four\n   blocked: no key\n"
MIN = 60.0
TO_MANAGER = "All builders in Acme Robots idle 15 min; 2 items waiting, 1 blocked."
TO_VOICE = "Acme Robots: all builders idle 15 minutes with 2 items waiting, 1 blocked."


class Clock:
    def __init__(self):
        self.now = 1_000_000.0
        self.hour = 12

    def __call__(self):
        return self.now


@pytest.fixture
def world(tmp_path):
    (tmp_path / "acme" / ".claude").mkdir(parents=True)
    (tmp_path / "acme" / ".claude" / "org.toml").write_text(ORG)
    state = tmp_path / "state"
    state.mkdir()
    (state / "queue.md").write_text(QUEUE)
    clock = Clock()
    watch = idle.Watch(state, clock=clock, hour=lambda t: clock.hour)
    return tmp_path, state, clock, watch


def chart(home, text=ORG):
    (home / "acme" / ".claude" / "org.toml").write_text(text)
    return org.load([home / "acme"])


def session(name, status="idle", since=0.0):
    return {"sessionId": name, "name": name, "cwd": "/elsewhere", "status": status, "statusUpdatedAt": since * 1000}


def look(world, rows, text=ORG):
    home, _, _, watch = world
    c = chart(home, text)
    return watch.look(c, rows, c.placer())


def idle_since(clock, minutes):
    return clock.now - minutes * MIN


def test_all_builders_idle_with_an_item_waiting_is_flagged_once_per_episode(world):
    _, _, clock, _ = world
    rows = [session("lane-a", since=idle_since(clock, 16)), session("lane-b", since=idle_since(clock, 20))]
    managers, voice = look(world, rows)
    assert [f.to_manager() for f in managers] == [TO_MANAGER]
    assert [f.to_voice() for f in voice] == [TO_VOICE]
    clock.now += MIN
    assert look(world, rows) == ([], [])


def test_builders_idle_for_less_than_the_wait_are_not_flagged(world):
    _, _, clock, _ = world
    rows = [session("lane-a", since=idle_since(clock, 14)), session("lane-b", since=idle_since(clock, 20))]
    assert look(world, rows) == ([], [])


def test_a_busy_builder_is_not_idle(world):
    _, _, clock, _ = world
    for status in ("busy", "shell"):
        rows = [session("lane-a", status, since=idle_since(clock, 30)), session("lane-b", since=idle_since(clock, 30))]
        assert look(world, rows) == ([], [])


def test_a_builder_waiting_on_a_prompt_is_not_idle(world):
    _, _, clock, _ = world
    rows = [session("lane-a", "waiting", since=idle_since(clock, 30)), session("lane-b", since=idle_since(clock, 30))]
    assert look(world, rows) == ([], [])


def test_nothing_is_flagged_when_every_item_is_taken_done_or_blocked(world):
    _, state, clock, _ = world
    (state / "queue.md").write_text("1. one\n   lane1: taken 10:00\n2. two\n   done: it\n3. three\n   blocked: why\n")
    rows = [session("lane-a", since=idle_since(clock, 30)), session("lane-b", since=idle_since(clock, 30))]
    assert look(world, rows) == ([], [])


def test_a_programme_with_no_backlog_is_never_flagged(world):
    _, _, clock, _ = world
    rows = [session("lane-a", since=idle_since(clock, 30)), session("lane-b", since=idle_since(clock, 30))]
    assert look(world, rows, ORG.replace('backlog = "queue.md"\n', "")) == ([], [])


def test_a_backlog_outside_the_state_folder_is_never_read(world):
    home, _, clock, _ = world
    (home / "outside.md").write_text(QUEUE)
    rows = [session("lane-a", since=idle_since(clock, 30)), session("lane-b", since=idle_since(clock, 30))]
    assert look(world, rows, ORG.replace("queue.md", "../outside.md")) == ([], [])


def test_a_builder_role_with_no_running_session_counts_as_idle(world):
    _, _, clock, _ = world
    rows = [session("lane-a", since=idle_since(clock, 30))]
    managers, _ = look(world, rows)
    assert len(managers) == 1
    for _ in range(3):
        clock.now += MIN
        assert look(world, rows) == ([], [])


def test_an_episode_ends_when_a_builder_works_or_nothing_waits(world):
    _, state, clock, _ = world
    rows = [session("lane-a", since=idle_since(clock, 30)), session("lane-b", since=idle_since(clock, 30))]
    assert len(look(world, rows)[0]) == 1
    clock.now += MIN
    assert look(world, [rows[0], session("lane-b", "busy", since=clock.now)]) == ([], [])
    clock.now += 16 * MIN
    rows = [rows[0], session("lane-b", since=idle_since(clock, 16))]
    assert len(look(world, rows)[0]) == 1
    clock.now += MIN
    (state / "queue.md").write_text("1. one\n   done: it\n")
    assert look(world, rows) == ([], [])
    clock.now += MIN
    (state / "queue.md").write_text(QUEUE)
    assert len(look(world, rows)[0]) == 1


def test_the_bridge_looks_at_most_once_a_minute(world):
    _, _, clock, _ = world
    rows = [session("lane-a", "busy"), session("lane-b", since=idle_since(clock, 30))]
    assert look(world, rows) == ([], [])
    clock.now += 10
    rows = [session("lane-a", since=idle_since(clock, 30)), session("lane-b", since=idle_since(clock, 30))]
    assert look(world, rows) == ([], [])
    clock.now += MIN
    assert len(look(world, rows)[0]) == 1


def test_the_voice_hears_of_idle_builders_at_night_only_in_the_morning(world):
    _, _, clock, _ = world
    clock.hour = 23
    rows = [session("lane-a", since=idle_since(clock, 30)), session("lane-b", since=idle_since(clock, 30))]
    managers, voice = look(world, rows)
    assert len(managers) == 1 and voice == []
    clock.now += 8 * 60 * MIN
    clock.hour = 6
    assert look(world, rows) == ([], [])
    clock.now += 60 * MIN
    clock.hour = 7
    managers, voice = look(world, rows)
    assert managers == [] and [f.to_voice() for f in voice] == [TO_VOICE]


def test_a_held_item_is_dropped_when_the_episode_ends_before_morning(world):
    _, _, clock, _ = world
    clock.hour = 2
    rows = [session("lane-a", since=idle_since(clock, 30)), session("lane-b", since=idle_since(clock, 30))]
    look(world, rows)
    clock.now += MIN
    look(world, [rows[0], session("lane-b", "busy", since=clock.now)])
    clock.now += 6 * 60 * MIN
    clock.hour = 8
    assert look(world, [rows[0], session("lane-b", "busy", since=clock.now)]) == ([], [])


def test_the_voice_hears_only_when_the_programme_says_so(world):
    _, _, clock, _ = world
    rows = [session("lane-a", since=idle_since(clock, 30)), session("lane-b", since=idle_since(clock, 30))]
    managers, voice = look(world, rows, ORG.replace("idle_report = true\n", ""))
    assert len(managers) == 1 and voice == []


def test_a_builder_by_name_only_counts(world):
    _, _, clock, _ = world
    text = ORG.replace("[roles.lane2]", "[roles.arm-builder]").rsplit("builder = true", 1)[0]
    rows = [session("lane-a", since=idle_since(clock, 30)), session("lane-b", "busy")]
    assert look(world, rows, text) == ([], [])  # the busy one builds, by its name
    clock.now += MIN
    rows = [rows[0], session("lane-b", since=idle_since(clock, 16))]
    assert len(look(world, rows, text)[0]) == 1


def test_a_programme_with_no_builders_is_never_flagged(world):
    text = ORG.replace("builder = true\n", "")
    assert look(world, [], text) == ([], [])

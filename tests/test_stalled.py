"""When a question left waiting is said, to whom, and how often."""

import pytest

from claude_voice import config, org, stalled

MIN = 60.0


class Clock:
    def __init__(self):
        self.now = 1_000_000.0
        self.hour = 12

    def __call__(self):
        return self.now


def make(after_min=5):
    clock = Clock()
    return stalled.Watch(after_min=after_min, clock=clock, hour=lambda t: clock.hour), clock


def waiting(clock, minutes, key="p1", session="fix-gripper", asked=None, held=True, **more):
    reads = []

    def read():
        reads.append(1)
        return asked if asked is not None else stalled.Asked("Run the tests?", ["Yes", "No"])

    item = stalled.Waiting(key, session, clock.now - minutes * MIN, read, held=held, **more)
    return item, reads


def test_a_question_waiting_past_the_limit_with_the_owner_away_is_said_once():
    watch, clock = make()
    item, _ = waiting(clock, 6)
    managers, voice = watch.look([item], away=True)
    line = "fix-gripper has waited 6 minutes on a question: 'Run the tests?'. Options: 1 Yes, 2 No."
    assert voice == [line] and [text for _, text in managers] == [line]
    clock.now += MIN
    assert watch.look([item], away=True) == ([], [])


def test_a_question_waiting_less_than_the_limit_is_not_said():
    watch, clock = make()
    item, reads = waiting(clock, 4)
    assert watch.look([item], away=True) == ([], [])
    assert reads == []


def test_nothing_is_said_while_the_owner_is_at_the_laptop():
    watch, clock = make()
    item, reads = waiting(clock, 9)
    assert watch.look([item], away=False) == ([], [])
    assert reads == []  # not even read
    clock.now += MIN
    assert len(watch.look([item], away=True)[1]) == 1  # said once the owner is away


def test_a_new_prompt_in_the_same_session_is_said_again():
    watch, clock = make()
    first, _ = waiting(clock, 6, key="p1")
    watch.look([first], away=True)
    clock.now += MIN
    second, _ = waiting(clock, 6, key="p2")
    assert len(watch.look([second], away=True)[1]) == 1


def test_a_question_read_off_the_pane_says_it_is_in_its_window():
    watch, clock = make()
    item, reads = waiting(clock, 6, held=False)
    _, voice = watch.look([item], away=True)
    assert voice[0].endswith("Options: 1 Yes, 2 No. It's in its window. Its manager has been told.")
    clock.now += MIN
    watch.look([item], away=True)
    assert reads == [1]  # the pane is read once per prompt


def test_a_question_that_cannot_be_read_says_so():
    watch, clock = make()
    item = stalled.Waiting("p1", "loose", clock.now - 6 * MIN, lambda: None, held=False)
    _, voice = watch.look([item], away=True)
    assert voice == ["loose has waited 6 minutes on a question I can't read; it's in its own window."]


def test_the_voice_hears_of_a_question_at_night_only_in_the_morning():
    watch, clock = make()
    clock.hour = 23
    item, _ = waiting(clock, 6)
    managers, voice = watch.look([item], away=True)
    assert len(managers) == 1 and voice == []
    clock.now += 8 * 60 * MIN
    clock.hour = 7
    managers, voice = watch.look([item], away=True)
    assert managers == [] and len(voice) == 1


def test_a_held_question_is_dropped_when_it_is_answered_before_morning():
    watch, clock = make()
    clock.hour = 1
    item, _ = waiting(clock, 6)
    watch.look([item], away=True)
    clock.now += 6 * 60 * MIN
    clock.hour = 7
    assert watch.look([], away=True) == ([], [])


def test_a_programme_that_has_not_said_so_hears_no_question_text():
    watch, clock = make()
    item, _ = waiting(clock, 6, text_ok=False)
    managers, voice = watch.look([item], away=True)
    assert voice == ["fix-gripper has a question waiting."]
    assert "Run the tests?" in managers[0][1]


def test_a_prompt_only_the_owner_may_answer_is_marked_for_the_manager():
    watch, clock = make()
    asked = stalled.Asked("Bash command git push --force origin main Do you want to proceed?", ["Yes", "No"])
    item, _ = waiting(clock, 6, asked=asked)
    managers, voice = watch.look([item], away=True)
    assert managers[0][1].startswith("The owner's: fix-gripper has waited")
    assert not voice[0].startswith("The owner's")


def test_the_bridge_looks_at_most_every_30_seconds():
    watch, clock = make()
    early, _ = waiting(clock, 4)
    watch.look([early], away=True)
    clock.now += 10
    late, _ = waiting(clock, 6, key="p2")
    assert watch.look([late], away=True) == ([], [])
    clock.now += 30
    assert len(watch.look([late], away=True)[1]) == 1


def test_only_the_owners_prompts_are_marked():
    assert stalled.owners_only("git push --force origin x")
    assert stalled.owners_only("git push origin main")
    assert stalled.owners_only("claude-voice plugins approve weather")
    assert stalled.owners_only("git branch -D old")
    assert stalled.owners_only("gh auth login")
    assert not stalled.owners_only("uv run pytest -q")
    assert not stalled.owners_only("git push --force-with-lease origin feat/x")


@pytest.mark.parametrize(
    "command",
    ["git push origin HEAD:main", "git push origin feat/x:master", "git push origin :feat/x",
     "git push origin +feat/x", "git push origin +HEAD:feat/x"],
)  # fmt: skip
def test_a_push_that_lands_on_main_deletes_or_forces_by_refspec_is_the_owners(command):
    assert stalled.owners_only(command)


@pytest.mark.parametrize(
    "question",
    ["Bash: {'command': 'deploy', 'api_key': 'hunter2'}", 'Bash: {"password": "hunter2"}',
     "Run mytool --password hunter2?"],
)  # fmt: skip
def test_a_held_prompt_is_masked_before_its_quotes_are_said(question):
    said = stalled.said_question(stalled.Asked(question, ["Yes", "No"]))
    assert "hunter2" not in said and "[hidden]" in said


# -- the settings, and what a programme lets the voice hear -------------------------------


def test_a_question_is_said_after_5_minutes_unless_the_settings_say_otherwise(tmp_path):
    default = config.load(tmp_path / "none.toml", env={"HOME": str(tmp_path)}).stalled_prompt
    assert (default.enabled, default.after_min) == (True, 5)
    f = tmp_path / "c.toml"
    f.write_text("[stalled_prompt]\nenabled = false\nafter_min = 10\n")
    chosen = config.load(f, env={"HOME": str(tmp_path)}).stalled_prompt
    assert (chosen.enabled, chosen.after_min) == (False, 10)


def test_a_wait_for_a_stalled_prompt_that_is_not_a_positive_number_is_refused(tmp_path):
    f = tmp_path / "c.toml"
    for body in ('after_min = "soon"', "after_min = 0", 'enabled = "yes"'):
        f.write_text(f"[stalled_prompt]\n{body}\n")
        with pytest.raises(config.ConfigError, match="stalled_prompt"):
            config.load(f, env={"HOME": str(tmp_path)})


def test_a_programme_lets_the_voice_hear_the_question_only_when_it_says_so(tmp_path):
    (tmp_path / "a" / ".claude").mkdir(parents=True)
    (tmp_path / "a" / ".claude" / "org.toml").write_text('[programme]\ncode = "a"\nstalled_prompt_text = true\n')
    (tmp_path / "b" / ".claude").mkdir(parents=True)
    (tmp_path / "b" / ".claude" / "org.toml").write_text('[programme]\ncode = "b"\n')
    chart = org.load([tmp_path / "a", tmp_path / "b"])
    assert [p.stalled_prompt_text for p in chart.programmes] == [True, False]


def test_an_approval_input_is_masked_before_it_is_said():
    watch, clock = make()
    token = "ghp_" + "Z9y8X7w6V5u4T3s2R1q0P9o8N7m6L5k4J3i2"
    asked = stalled.Asked(f"Bash: curl -H 'Authorization: Bearer {token}' https://x.test/?code=abc", ["Yes", "No"])
    item, _ = waiting(clock, 6, asked=asked)
    managers, voice = watch.look([item], away=True)
    for line in (managers[0][1], voice[0]):
        assert token not in line and "abc" not in line and "[hidden]" in line

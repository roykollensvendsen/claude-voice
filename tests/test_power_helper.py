"""The power helper: it moves only on a yes the hub signed, and looks before it acts.

Every test uses a fake login manager and a key pair made for the test. Nothing here powers
anything off, reaches a real login manager, or runs a command.
"""

import base64

import pytest
from power_fakes import MACHINE, Clock, FakeLogind, Hub, Notes, inhibitor, planned, session

from claude_voice_power import core, signing
from claude_voice_power.logind import LogindError

IDLE_LONG = 5000.0 - 700  # seconds on the fake monotonic clock: idle for over ten minutes


class Rig:
    """A helper over a fake login manager, with the hub's key and a clock the test moves."""

    def __init__(self, sessions=(), inhibitors=(), scheduled=None, night=False):
        self.hub, self.clock, self.notes = Hub(), Clock(), Notes()
        self.logind = FakeLogind(sessions, inhibitors, scheduled)
        if night:
            self.clock.hour = (23, 40)
        self.helper = core.Helper(
            self.logind,
            signing.Verifier(self.hub.public),
            MACHINE,
            self.notes,
            now=self.clock,
            monotonic=self.clock.monotonic,
            local_time=self.clock.local,
        )

    def ask(self, action="poweroff", at=None):
        at = int(self.clock.now) if at is None else at
        return self.helper.request(**self.hub.request(action, at))

    def yes(self, nonce, **changes):
        return self.helper.confirm(**self.hub.confirm(nonce, **{"expires": int(self.clock.now) + 50, **changes}))

    def go(self, action="poweroff"):
        """A question and a yes: the minute has started."""
        answer = self.yes(self.ask(action)["nonce"], action=action)
        assert answer["ok"], answer
        return answer


def away(**changes):
    """A local session whose person has gone: idle for over ten minutes."""
    return session(idle=True, idle_since=int(IDLE_LONG * 1_000_000), **changes)


# -- the signature and the nonce -------------------------------------------------------------


def test_a_request_needs_the_hub_signature_and_a_time_under_10_seconds_old():
    rig = Rig()
    stranger = Hub()
    forged = {**stranger.request(at=1000)}
    assert rig.helper.request(**forged)["code"] == "unsigned"
    unsigned = {**rig.hub.request(), "signature": ""}
    assert rig.helper.request(**unsigned)["code"] == "unsigned"
    assert rig.ask(at=980)["code"] == "unsigned"  # twenty seconds old
    assert rig.ask(at=1020)["code"] == "unsigned"  # far ahead of this machine's clock
    assert rig.helper.open is None
    assert rig.ask(at=995)["ok"] is True


def test_a_request_with_a_changed_field_opens_no_question():
    rig = Rig()
    signed = rig.hub.request("poweroff", 1000)
    assert rig.helper.request(**{**signed, "action": "reboot"})["code"] == "unsigned"
    assert rig.helper.request(**{**signed, "at": 1001})["code"] == "unsigned"
    other = rig.hub.request(machine="laptop-c")
    assert rig.helper.request(**other)["code"] == "other-machine"
    assert rig.helper.request("laptop-b", "halt", 1000, "x")["code"] == "malformed"
    assert rig.helper.open is None


def test_a_new_request_replaces_the_open_question():
    rig = Rig()
    first = rig.ask()["nonce"]
    second = rig.ask()["nonce"]
    assert first != second
    old = rig.yes(first)
    assert old["ok"] is False and old["say"] == "That question was replaced by a new one."
    assert rig.yes(second)["ok"] is True


def test_a_refused_request_still_ends_the_open_question():
    rig = Rig()
    first = rig.ask()["nonce"]
    rig.logind._sessions.append(session())  # someone sits down
    assert rig.ask()["code"] == "refused"
    assert rig.yes(first)["ok"] is False


def test_a_nonce_is_128_random_bits():
    rig = Rig()
    nonce = rig.ask()["nonce"]
    assert len(nonce) == 32 and int(nonce, 16) >= 0
    assert rig.ask()["nonce"] != nonce


def test_a_power_action_needs_the_hub_signature_over_this_machine_this_action_this_nonce_and_an_unexpired_time():
    rig = Rig()
    nonce = rig.ask()["nonce"]
    signed = rig.hub.confirm(nonce, expires=1050)
    for changed in (
        {"nonce": "0" * 32},
        {"action": "reboot"},
        {"machine": "laptop-c"},
        {"expires": 1051},
        {"confirm_name": "laptop-b"},
        {"signature": Hub().sign(b"anything")},
        {"signature": ""},
    ):
        assert rig.helper.confirm(**{**signed, **changed})["ok"] is False, changed
    assert rig.helper.due is None
    rig.clock.pass_seconds(70)  # past its expiry
    assert rig.helper.confirm(**signed)["ok"] is False
    assert rig.helper.due is None


def test_a_nonce_works_once_for_its_own_action_within_60_seconds():
    rig = Rig()
    nonce = rig.ask("poweroff")["nonce"]
    wrong_action = rig.helper.confirm(**rig.hub.confirm(nonce, action="reboot"))
    assert wrong_action["ok"] is False and rig.helper.due is None
    assert rig.yes(nonce)["ok"] is True
    again = rig.yes(nonce)
    assert again["ok"] is False and again["code"] == "no-question"
    late = Rig()
    nonce = late.ask()["nonce"]
    late.clock.pass_seconds(61)
    expired = late.helper.confirm(**late.hub.confirm(nonce, expires=int(late.clock.now) + 1))
    assert expired["code"] == "no-question" and late.helper.due is None


def test_confirm_name_is_part_of_the_signed_payload():
    hub = Hub()
    signed = hub.confirm("ab" * 16, confirm_name="")
    changed = {**signed, "confirm_name": MACHINE}
    verifier = signing.Verifier(hub.public)
    payload = signing.confirm_payload(MACHINE, "poweroff", "ab" * 16, 1050, MACHINE)
    assert verifier.ok(payload, changed["signature"]) is False
    night = Rig(night=True)
    nonce = night.ask()["nonce"]
    sent = night.hub.confirm(nonce, expires=1050, confirm_name="")
    added = night.helper.confirm(**{**sent, "confirm_name": MACHINE})  # the name added after signing
    assert added["ok"] is False and night.helper.due is None


def test_signed_fields_that_are_not_plain_words_are_refused():
    with pytest.raises(signing.BadField):
        signing.confirm_payload(MACHINE, "poweroff", "ab", 1, "x|poweroff")
    with pytest.raises(signing.BadField):
        signing.request_payload("a|b", "poweroff", 1)
    with pytest.raises(signing.BadField):
        signing.parse_public_key("not base64!")
    assert signing.Verifier(Hub().public).ok(b"x", "###") is False


def test_a_key_has_a_fingerprint_to_compare_by_eye():
    key = bytes(range(32))
    groups = signing.fingerprint(key).split(" ")
    assert len(groups) == 8 and all(len(g) == 8 for g in groups)
    assert signing.parse_public_key(base64.b64encode(key).decode()) == key


# -- who is at the machine --------------------------------------------------------------------


def test_a_machine_in_use_by_someone_at_it_is_never_shut_down_or_restarted():
    rig = Rig([session()])
    refused = rig.ask()
    assert refused["ok"] is False and "using" in refused["say"]
    assert rig.helper.open is None and rig.logind.calls == []
    assert Rig([session()]).ask("reboot")["ok"] is False


def test_someone_who_has_been_idle_for_over_ten_minutes_is_asked_about_not_refused():
    rig = Rig([away()])
    assert rig.ask()["ok"] is True
    just_now = Rig([session(idle=True, idle_since=int(4990.0 * 1_000_000))])
    assert just_now.ask()["ok"] is False  # idle for ten seconds only
    no_hint = Rig([session(idle=False)])
    assert no_hint.ask()["ok"] is False


def test_a_locked_screen_with_a_login_open_needs_the_machine_name_in_the_yes():
    rig = Rig([session(locked=True)])
    question = rig.ask()
    assert question["ok"] and question["needs_name"] is True
    assert f"yes, shut down {MACHINE}" in question["say"]
    without = rig.yes(question["nonce"])
    assert without["ok"] is False and without["code"] == "needs-name"
    assert rig.helper.open is not None  # the question stays open for a yes that carries the name
    assert rig.helper.confirm(**rig.hub.confirm(question["nonce"], expires=1050, confirm_name=MACHINE))["ok"]


def test_remote_and_text_logins_are_named_and_do_not_refuse():
    rig = Rig(
        [session(id="2", name="sam", remote=True, type="tty"), session(id="3", name="kim", type="tty", tty="tty2")]
    )
    question = rig.ask()
    assert question["ok"] and question["needs_name"] is False
    assert "1 remote login: sam" in question["say"] and "1 text login: kim on tty2" in question["say"]
    assert Rig([session(klass="greeter")]).ask()["ok"] is True  # the login screen is nobody


# -- what is planned and what holds it up -------------------------------------------------------


def test_a_shutdown_not_made_by_the_helper_is_never_cancelled_or_replaced():
    rig = Rig(scheduled=planned())
    refused = rig.ask()
    assert refused["ok"] is False and "already has a poweroff planned" in refused["say"]
    rig.helper.cancel()
    assert rig.logind.calls == [] and rig.logind._scheduled is not None


def test_an_inhibitor_is_named_and_confirm_then_reports_the_login_managers_refusal():
    rig = Rig(inhibitors=[inhibitor()])
    question = rig.ask()
    assert "Editor holds it up: a file is unsaved" in question["say"]
    assert "will stop it" in question["say"]
    rig.go()
    rig.clock.pass_seconds(61)
    stopped = rig.helper.tick()
    assert stopped["ok"] is False and "Editor is blocking it" in stopped["say"]
    assert rig.logind.powered == []
    assert any("stays on" in told for told in rig.notes.told)


def test_an_inhibitor_that_only_delays_does_not_stop_the_question():
    rig = Rig(inhibitors=[inhibitor(mode="delay", what="sleep")])
    assert "will stop it" not in rig.ask()["say"]


# -- the minute ---------------------------------------------------------------------------------


def test_a_yes_starts_one_minute_and_tells_the_machine_then_powers_off():
    rig = Rig()
    assert rig.go()["say"] == f"{MACHINE} will shut down in one minute."
    assert "claude-voice-power cancel" in rig.notes.told[0]
    assert rig.helper.tick() is None  # not yet
    rig.clock.pass_seconds(59)
    assert rig.helper.tick() is None
    rig.clock.pass_seconds(2)
    assert rig.helper.tick()["ok"] is True
    assert rig.logind.powered == ["poweroff"]
    assert rig.helper.tick() is None  # once only


def test_a_restart_is_said_as_a_restart_and_asks_for_a_reboot():
    rig = Rig()
    assert "Restart" in rig.ask("reboot")["say"]
    rig.go("reboot")
    rig.clock.pass_seconds(61)
    rig.helper.tick()
    assert rig.logind.powered == ["reboot"]


def test_the_helper_looks_again_just_before_it_acts():
    rig = Rig()
    rig.go()
    rig.logind._sessions.append(session())  # someone sat down during the minute
    rig.clock.pass_seconds(61)
    stopped = rig.helper.tick()
    assert stopped["ok"] is False and "using" in stopped["say"]
    assert rig.logind.calls == []


def test_a_planned_shutdown_that_appears_during_the_minute_stops_the_helper_too():
    rig = Rig()
    rig.go()
    rig.logind._scheduled = planned()
    rig.clock.pass_seconds(61)
    assert rig.helper.tick()["ok"] is False and rig.logind.calls == []


def test_cancel_works_without_a_token_and_stops_the_minute():
    rig = Rig()
    assert rig.helper.cancel()["say"] == "Nothing was planned."
    rig.go()
    assert rig.helper.cancel()["say"] == "Cancelled."
    rig.clock.pass_seconds(120)
    assert rig.helper.tick() is None and rig.logind.calls == []
    assert any("cancelled" in told for told in rig.notes.told)


def test_a_new_question_while_counting_down_is_refused():
    rig = Rig()
    rig.go()
    assert rig.ask()["code"] == "counting"


def test_at_night_the_confirm_needs_the_machine_name():
    rig = Rig(night=True)
    question = rig.ask()
    assert question["needs_name"] is True
    assert f"It's 23:40 on {MACHINE}" in question["say"]
    assert f"Say 'yes, shut down {MACHINE}' to go ahead." in question["say"]
    assert rig.yes(question["nonce"])["code"] == "needs-name"
    assert rig.yes(question["nonce"], confirm_name="other-machine")["code"] == "needs-name"
    assert rig.yes(question["nonce"], confirm_name=MACHINE)["ok"] is True
    day = Rig()
    assert day.ask()["needs_name"] is False


def test_the_night_hours_run_across_midnight():
    assert core.at_night(23) and core.at_night(0) and core.at_night(6)
    assert not core.at_night(7) and not core.at_night(14) and not core.at_night(22)
    assert core.at_night(2, (1, 5)) and not core.at_night(6, (1, 5))


# -- the socket's messages --------------------------------------------------------------------


def test_handle_answers_each_message_and_refuses_what_it_does_not_understand():
    rig = Rig()
    asked = rig.helper.handle({"op": "request", **rig.hub.request(at=1000)})
    assert asked["ok"] is True
    sent = rig.hub.confirm(asked["nonce"], expires=1050)
    assert rig.helper.handle({"op": "confirm", **sent})["ok"] is True
    assert rig.helper.handle({"op": "cancel"})["ok"] is True
    for junk in ({}, {"op": "poweroff"}, {"op": "request"}, {"op": "confirm", "machine": 3}):
        assert rig.helper.handle(junk)["code"] == "malformed"


def test_the_login_managers_own_refusal_is_a_logind_error():
    assert issubclass(LogindError, RuntimeError)

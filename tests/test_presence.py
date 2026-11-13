"""When the bridge counts the owner as away from the laptop.

A session that asks for a yes waits for the phone only while he is away, because
waiting while he sits at the laptop delays the box he would answer at once
(ADR-CV-008). Away is: the companion app open, the screen locked, the voice used
in the last ten minutes, or no keyboard or mouse for five minutes.
"""

from claude_voice import presence


class Desk:
    """A stand-in for the desktop: whether the screen is locked, and the idle time."""

    def __init__(self, locked=False, idle=0.0):
        self.locked, self.idle = locked, idle

    def __call__(self):
        return presence.Desktop(locked=self.locked, idle_seconds=self.idle)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def watcher(desk=None, clock=None):
    return presence.Presence(desktop=desk or Desk(), clock=clock or Clock())


def test_at_the_laptop_the_owner_is_not_away():
    assert watcher().away(app_open=False) is False


def test_the_owner_is_away_while_the_app_is_open():
    assert watcher().away(app_open=True) is True


def test_the_owner_is_away_while_the_screen_is_locked():
    assert watcher(Desk(locked=True)).away(app_open=False) is True


def test_the_owner_is_away_after_five_minutes_without_keyboard_or_mouse():
    assert watcher(Desk(idle=301)).away(app_open=False) is True
    assert watcher(Desk(idle=299)).away(app_open=False) is False


def test_the_owner_counts_as_away_for_ten_minutes_after_the_voice_called():
    clock = Clock()
    seen = watcher(clock=clock)
    seen.heard_voice()
    clock.now += 599
    assert seen.away(app_open=False) is True
    clock.now += 2
    assert seen.away(app_open=False) is False


def test_a_desktop_that_cannot_be_read_counts_as_at_the_laptop():
    def broken():
        raise OSError("no session bus")

    assert presence.Presence(desktop=broken, clock=Clock()).away(app_open=False) is False


def test_the_lock_is_read_from_loginctl_and_the_idle_time_from_the_desktop():
    calls = []

    def run(command):
        calls.append(command[0])
        if command[0] == "loginctl" and "-p" not in command:
            return "4\n"
        if command[0] == "loginctl":
            return "yes\n"
        return "(uint64 302000,)\n"

    desk = presence.read_desktop(run=run)
    assert desk == presence.Desktop(locked=True, idle_seconds=302.0)
    assert calls == ["loginctl", "loginctl", "gdbus"]

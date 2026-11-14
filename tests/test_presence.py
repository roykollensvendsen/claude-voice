"""When the bridge counts the owner as away from the laptop.

A session that asks for a yes waits for the phone only while he is away, because
waiting while he sits at the laptop delays the box he would answer at once
(ADR-CV-008). Away is: the companion app open, the screen locked, the voice used
in the last ten minutes, or no keyboard or mouse for five minutes.
"""

import logging
import subprocess

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
    seen = watcher(Desk(idle=None), clock)
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


def test_a_desktop_source_that_cannot_be_read_leaves_the_other_one_counted():
    def run(command):
        if command[0] == "gdbus":  # a desktop without GNOME's idle monitor, such as KDE
            raise subprocess.CalledProcessError(1, command)
        return "4\n" if "-p" not in command else "yes\n"

    desk = presence.read_desktop(run=run)
    assert desk == presence.Desktop(locked=True, idle_seconds=None)
    assert watcher(lambda: desk).away(app_open=False) is True


def test_an_unreadable_desktop_source_is_logged_once(caplog, monkeypatch):
    monkeypatch.setattr(presence, "_REPORTED", set())

    def run(command):
        raise FileNotFoundError(command[0])

    with caplog.at_level(logging.WARNING, logger="claude_voice.presence"):
        presence.read_desktop(run=run)
        presence.read_desktop(run=run)
    assert len(caplog.records) == 2  # the lock and the idle time, once each


def test_keyboard_or_mouse_used_since_the_voice_called_counts_as_at_the_laptop():
    clock = Clock()
    desk = Desk(idle=None)
    seen = watcher(desk, clock)
    seen.heard_voice()
    clock.now += 60
    assert seen.away(app_open=False) is True  # nothing says he is at the laptop
    desk.idle = 70.0
    clock.now += 10
    assert seen.away(app_open=False) is True  # untouched since before the voice called
    desk.idle = 5.0
    clock.now += 10
    assert seen.away(app_open=False) is False  # he typed after the voice called


def test_the_desktop_is_read_at_most_once_every_few_seconds():
    clock = Clock()
    reads = []

    def desktop():
        reads.append(clock.now)
        return presence.Desktop(locked=False, idle_seconds=0.0)

    seen = presence.Presence(desktop=desktop, clock=clock)
    seen.away(app_open=False)
    clock.now += 1
    seen.away(app_open=False)
    assert len(reads) == 1
    clock.now += presence.DESK_CACHE
    seen.away(app_open=False)
    assert len(reads) == 2

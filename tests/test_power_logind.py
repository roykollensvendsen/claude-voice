"""The login manager seam: the D-Bus adapter, over a fake bus and a fake connection.

Nothing here connects to the system bus. The real adapter is checked only for how it turns
D-Bus replies into the helper's own types, and for what it asks of the login manager.
"""

import pytest
from jeepney import HeaderFields, new_error, new_method_return

from claude_voice_power import logind
from claude_voice_power.logind import DBusLogind, Inhibitor, JeepneyBus, LogindError, Scheduled


class FakeBus:
    """Replies as login1 does, and keeps every call."""

    def __init__(self, replies=None, refuse=None):
        self.replies, self.refuse, self.calls = replies or {}, refuse, []

    def call(self, path, interface, member, signature="", body=()):
        self.calls.append((path, interface, member, signature, body))
        if self.refuse and member in self.refuse:
            raise LogindError(self.refuse[member])
        return self.replies.get(member, ())


SESSION_PROPS = {
    "Type": ("s", "wayland"),
    "Class": ("s", "user"),
    "Remote": ("b", False),
    "TTY": ("s", ""),
    "Active": ("b", True),
    "LockedHint": ("b", True),
    "IdleHint": ("b", True),
    "IdleSinceHintMonotonic": ("t", 123),
}


def test_sessions_are_read_with_their_properties():
    bus = FakeBus({"ListSessions": ([("2", 1000, "alex", "seat0", "/org/s/2")],), "GetAll": (SESSION_PROPS,)})
    (found,) = DBusLogind(bus).sessions()
    assert (found.id, found.name, found.type, found.klass) == ("2", "alex", "wayland", "user")
    assert found.active and found.locked and found.idle and found.idle_since == 123
    assert found.graphical and not found.remote
    assert bus.calls[1][:3] == ("/org/s/2", logind.PROPERTIES, "GetAll")


def test_inhibitors_say_whether_they_block_a_shutdown():
    listed = [("shutdown", "Editor", "unsaved", "block", 1000, 55), ("sleep:idle", "Player", "film", "block", 1, 2)]
    found = DBusLogind(FakeBus({"ListInhibitors": (listed,)})).inhibitors()
    assert found[0] == Inhibitor("shutdown", "Editor", "unsaved", "block") and found[0].blocks
    assert not found[1].blocks
    assert not Inhibitor("shutdown", "Editor", "unsaved", "delay").blocks


def test_a_planned_shutdown_is_read_and_none_is_none():
    planned = FakeBus({"Get": (("(st)", ("poweroff", 99)),)})
    assert DBusLogind(planned).scheduled() == Scheduled("poweroff", 99)
    assert DBusLogind(FakeBus({"Get": (("(st)", ("", 0)),)})).scheduled() is None


def test_power_asks_for_the_plain_form_that_honours_inhibitors():
    bus = FakeBus()
    DBusLogind(bus).power("poweroff")
    DBusLogind(bus).power("reboot")
    assert [call[2:] for call in bus.calls] == [("PowerOff", "b", (False,)), ("Reboot", "b", (False,))]


def test_a_refusal_from_the_login_manager_reaches_the_caller():
    refusing = FakeBus(refuse={"PowerOff": "the Editor blocks it"})
    with pytest.raises(LogindError, match="Editor"):
        DBusLogind(refusing).power("poweroff")


class Connection:
    def __init__(self, error=None):
        self.error, self.sent = error, []

    def send_and_get_reply(self, message, timeout=None):
        self.sent.append(message)
        if self.error:
            return new_error(message, "org.freedesktop.DBus.Error.AccessDenied", "s", (self.error,))
        return new_method_return(message, "as", (["a", "b"],))


def test_the_jeepney_bus_sends_one_call_and_returns_the_reply_body():
    connection, opened = Connection(), []

    def connect(bus):
        opened.append(bus)
        return connection

    bus = JeepneyBus(connect)
    assert bus.call(logind.MANAGER_PATH, logind.MANAGER, "ListSessions") == (["a", "b"],)
    bus.call(logind.MANAGER_PATH, logind.MANAGER, "PowerOff", "b", (False,))
    assert opened == ["SYSTEM"] and len(connection.sent) == 2  # one connection, kept
    header = connection.sent[1].header
    assert header.fields[HeaderFields.destination] == logind.DESTINATION


def test_the_jeepney_bus_turns_an_error_reply_into_a_logind_error():
    bus = JeepneyBus(lambda bus: Connection(error="Operation not permitted"))
    with pytest.raises(LogindError, match="not permitted"):
        bus.call(logind.MANAGER_PATH, logind.MANAGER, "PowerOff", "b", (False,))

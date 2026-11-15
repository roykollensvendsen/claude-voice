"""The one door to the machine's login manager, and a thin adapter for the real one.

`Logind` is what the helper needs and nothing more: who is using the machine, what holds a
shutdown up, whether one is planned, and the two actions. The adapter speaks D-Bus to
org.freedesktop.login1 directly: no shell, no subprocess and no privilege tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

DESTINATION = "org.freedesktop.login1"
MANAGER_PATH = "/org/freedesktop/login1"
MANAGER = "org.freedesktop.login1.Manager"
SESSION = "org.freedesktop.login1.Session"
PROPERTIES = "org.freedesktop.DBus.Properties"


class LogindError(RuntimeError):
    """The login manager said no, in its own words."""


@dataclass(frozen=True)
class Session:
    id: str
    name: str
    type: str  # x11, wayland, tty, ...
    klass: str  # user, greeter, ...
    remote: bool
    tty: str
    active: bool
    locked: bool
    idle: bool
    idle_since: int  # microseconds on the monotonic clock; 0 when unknown

    @property
    def graphical(self) -> bool:
        return self.type in ("x11", "wayland", "mir")


@dataclass(frozen=True)
class Inhibitor:
    what: str
    who: str
    why: str
    mode: str  # block or delay

    @property
    def blocks(self) -> bool:
        return self.mode == "block" and ("shutdown" in self.what.split(":"))


@dataclass(frozen=True)
class Scheduled:
    kind: str
    at: int  # microseconds since the epoch


class Logind(Protocol):
    def sessions(self) -> list[Session]: ...

    def inhibitors(self) -> list[Inhibitor]: ...

    def scheduled(self) -> Scheduled | None: ...

    def power(self, action: str) -> None:
        """PowerOff(false) for "poweroff", Reboot(false) for "reboot"; LogindError on refusal."""
        ...


class Bus(Protocol):
    def call(self, path: str, interface: str, member: str, signature: str = "", body: tuple[Any, ...] = ()) -> Any:
        """One method call on org.freedesktop.login1; the reply's body, or LogindError."""
        ...


class DBusLogind:
    """`Logind` over a `Bus`."""

    def __init__(self, bus: Bus) -> None:
        self.bus = bus

    def sessions(self) -> list[Session]:
        (listed,) = self.bus.call(MANAGER_PATH, MANAGER, "ListSessions")
        found = []
        for ident, _uid, name, _seat, path in listed:
            (props,) = self.bus.call(path, PROPERTIES, "GetAll", "s", (SESSION,))
            got = {key: value[1] for key, value in props.items()}
            found.append(
                Session(
                    id=ident,
                    name=name,
                    type=str(got.get("Type", "")),
                    klass=str(got.get("Class", "")),
                    remote=bool(got.get("Remote", False)),
                    tty=str(got.get("TTY", "")),
                    active=bool(got.get("Active", False)),
                    locked=bool(got.get("LockedHint", False)),
                    idle=bool(got.get("IdleHint", False)),
                    idle_since=int(got.get("IdleSinceHintMonotonic", 0)),
                )
            )
        return found

    def inhibitors(self) -> list[Inhibitor]:
        (listed,) = self.bus.call(MANAGER_PATH, MANAGER, "ListInhibitors")
        return [Inhibitor(what, who, why, mode) for what, who, why, mode, _uid, _pid in listed]

    def scheduled(self) -> Scheduled | None:
        (variant,) = self.bus.call(MANAGER_PATH, PROPERTIES, "Get", "ss", (MANAGER, "ScheduledShutdown"))
        kind, at = variant[1]
        return Scheduled(str(kind), int(at)) if kind and at else None

    def power(self, action: str) -> None:
        # The plain forms only: they honour a block inhibitor, and never skip one.
        self.bus.call(MANAGER_PATH, MANAGER, "PowerOff" if action == "poweroff" else "Reboot", "b", (False,))


class JeepneyBus:
    """The system bus, through the pure-Python jeepney library."""

    def __init__(self, connect: Any = None) -> None:
        self._connect: Any = connect
        self._connection: Any = None

    def call(self, path: str, interface: str, member: str, signature: str = "", body: tuple[Any, ...] = ()) -> Any:
        from jeepney import DBusAddress, DBusErrorResponse, new_method_call
        from jeepney.wrappers import unwrap_msg

        if self._connection is None:
            from jeepney.io.blocking import open_dbus_connection

            self._connection = (self._connect or open_dbus_connection)(bus="SYSTEM")
        message = new_method_call(DBusAddress(path, DESTINATION, interface), member, signature, body)
        try:
            return unwrap_msg(self._connection.send_and_get_reply(message, timeout=10))
        except DBusErrorResponse as refused:
            raise LogindError(" ".join(str(part) for part in refused.data)) from None

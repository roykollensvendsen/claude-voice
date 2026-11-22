"""What every test shares."""

import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

#: The owner's real home, read before any test can move it. Only the checkout the suite runs in and
#: the machine's temporary folder may be written under it.
REAL_HOME = Path.home().resolve()
UV_CACHE = Path(os.environ.get("UV_CACHE_DIR") or REAL_HOME / ".cache" / "uv").resolve()
CHECKOUT = Path(__file__).resolve().parents[1]
ALLOWED = (CHECKOUT, Path(tempfile.gettempdir()).resolve(), UV_CACHE)
#: The Python running the suite writes its own compiled files where it is installed, which uv
#: puts under the home. Only those are open: a Python installed in ~/.local opens no more of it.
PYTHON = tuple({Path(p).resolve() for p in (sys.base_prefix, sys.base_exec_prefix, sys.prefix)})
#: The owner's own files in a checkout, which git leaves out: never written, checkout or not.
OWNERS_IN_CHECKOUT = {CHECKOUT / ".claude" / "settings.local.json", CHECKOUT / ".claude" / "org.toml"}
OWNERS_IN_CHECKOUT |= {CHECKOUT / "CLAUDE.local.md"}
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
#: For each change, where among its audit arguments each written path is, and the folder handle a
#: relative path is read against (shutil.rmtree removes by name inside an open folder). A link's
#: source is not written, only where it is made; a database file is made without an open event.
WRITTEN: dict[str, list[tuple[int, int | None]]] = {
    "os.remove": [(0, 1)],
    "os.rmdir": [(0, 1)],
    "os.mkdir": [(0, 2)],
    "os.chmod": [(0, 2)],
    "os.chown": [(0, 3)],
    "os.utime": [(0, 3)],
    "os.truncate": [(0, None)],
    "os.rename": [(0, 2), (1, 3)],
    "os.link": [(1, 3)],
    "os.symlink": [(1, 2)],
    "sqlite3.connect": [(0, None)],
}
#: Writes seen while no test runs, from conftest's import on: at collection, or in a shared fixture.
_outside_tests: list[str] = []
_watching: list[list[str]] = [_outside_tests]  # the writes seen during the running test, on top


class WroteToTheOwnersHome(PermissionError):
    """A test tried to write where the owner's own files live."""


def _owners(target: object, folder: object = None) -> Path | None:
    """The path, when it is the owner's own: under the real home, outside the checkout and temp.
    `folder` is the open folder a relative path is read against, when the call named one."""
    if not isinstance(target, (str, bytes, os.PathLike)):
        return None  # a file descriptor: its path was checked when it was opened
    try:
        path = Path(os.fsdecode(target)).expanduser()
        if isinstance(folder, int) and folder >= 0 and not path.is_absolute():
            path = Path(os.readlink(f"/proc/self/fd/{folder}")) / path
        path = path.absolute()
    except (TypeError, ValueError):
        return None
    except OSError:
        return None  # no /proc here, so the folder can't be named; the moved home still holds
    if path in OWNERS_IN_CHECKOUT or path.resolve() in OWNERS_IN_CHECKOUT:
        return path.resolve()
    if not path.is_relative_to(REAL_HOME) or _open(path):
        return None
    resolved = path.resolve()  # a link from the checkout into the home is the home
    return None if _open(resolved) else resolved


def _open(path: Path) -> bool:
    """Whether a write to `path` is open: in the checkout, temp or uv's cache, or a compiled file
    of the Python running the suite."""
    # A compiled file, or the temporary one Python writes it as first: <name>.pyc or <name>.pyc.<n>
    named = path.parent.name == "__pycache__" and re.fullmatch(r".+\.pyc(\.\d+)?", path.name) is not None
    compiled = named and any(path.is_relative_to(p) for p in PYTHON)
    return compiled or any(path.is_relative_to(a) for a in ALLOWED)


def _written(event: str, args: tuple) -> list[tuple[object, object]]:
    """The paths a call writes, each with the open folder it is read against, if any."""
    if event == "open" and len(args) >= 3:
        mode, flags = args[1], args[2]
        writes = (isinstance(mode, str) and any(c in mode for c in "wax+")) or bool(flags & WRITE_FLAGS)
        return [(args[0], None)] if writes else []
    at = lambda i: args[i] if i is not None and i < len(args) else None  # noqa: E731
    return [(at(path), at(folder)) for path, folder in WRITTEN.get(event, [])]


def _guard(event: str, args: tuple) -> None:
    """Refuse, and note, a write under the owner's home: the bridge's state folder, the owner's
    settings, the owner's Claude Code files. A process the test starts is not seen here."""
    for path in filter(None, (_owners(target, folder) for target, folder in _written(event, args))):
        _watching[-1].append(f"{event} {path}")
        raise WroteToTheOwnersHome(f"a test may not write under the owner's home: {path}")


sys.addaudithook(_guard)


def pytest_sessionfinish(session):
    """A write refused outside any test, such as at collection, fails the run as well."""
    if _outside_tests:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        print(f"\nwritten under the owner's home outside any test: {_outside_tests}", file=sys.stderr)


@pytest.fixture(autouse=True)
def no_writes_to_the_owners_home(tmp_path_factory, monkeypatch):
    """Give every test a home of its own, and fail any test that writes under the owner's real one.

    Two parts, since each covers what the other cannot. The home variables point at a fresh folder,
    so a path the code reads from them at the time, and every process a test starts (git, uv, a
    bridge), lands there. The audit hook above sees only this Python process, so it catches a path
    fixed before the test began, such as one read from the home at import. uv keeps its real cache,
    so a uv a test starts does not rebuild it. A test of the guard itself takes the list and empties it.
    """
    home = tmp_path_factory.mktemp("home")
    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("UV_CACHE_DIR", str(UV_CACHE))
    seen: list[str] = []
    _watching.append(seen)
    try:
        yield seen
    finally:
        _watching.pop()
    assert not seen, f"this test wrote under the owner's home: {seen}"


@pytest.fixture(autouse=True)
def not_the_bridges_own_service(tmp_path_factory, monkeypatch):
    """A session can run under systemd, as the bridge does, so a test could take itself for the
    service starting and write the bridge's restart record. The suite is never the service, and
    the bridge's state folder is a fresh one."""
    from claude_voice import config

    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.setattr(config, "STATE_DIR", tmp_path_factory.mktemp("bridge-state"))


@pytest.fixture(autouse=True)
def no_owner_settings(tmp_path_factory, monkeypatch):
    """Keep the owner's own settings file out of the suite, so a plugin the owner adds on this
    computer changes no test's answer. A test that wants settings writes its own."""
    monkeypatch.setenv("CLAUDE_VOICE_CONFIG", str(tmp_path_factory.mktemp("settings") / "none.toml"))


@pytest.fixture(autouse=True)
def no_checkout_programme_file(tmp_path_factory, monkeypatch):
    """The bridge counts the programme file in its own clone, and the suite runs in a clone. A
    clone the bridge updates keeps its owner's file there, which git leaves out, so the suite
    gives the bridge an empty folder as its clone instead."""
    from claude_voice import roles

    clone = tmp_path_factory.mktemp("clone")
    monkeypatch.setattr(roles, "own_clone", lambda: clone)


@pytest.fixture(autouse=True)
def no_owner_developer_mode(tmp_path_factory, monkeypatch):
    """Keep the owner's own developer mode out of the suite: a test never reads it or ends it."""
    from claude_voice import devmode

    monkeypatch.setattr(devmode, "STATE", tmp_path_factory.mktemp("state") / "developer-mode.json")


@pytest.fixture(autouse=True)
def no_real_side_reader(monkeypatch):
    """A watched server warms the shared side-question reader. A real one starts Claude Code,
    which slows the suite and holds its folder open after it, so the suite gets a fake."""
    from fakes import FakeClaude

    from claude_voice import transcripts

    reader = transcripts.SideReader(client_factory=FakeClaude())
    monkeypatch.setattr(transcripts, "_reader", reader)
    yield
    if reader._cwd is not None:
        reader._cwd.cleanup()


@pytest.fixture(autouse=True)
def helpers_in_a_folder_of_their_own(tmp_path_factory, monkeypatch):
    """The bridge's message helpers make their folders here, and its start sweeps only here,
    so the suite never touches what a real bridge keeps in the machine's temporary folder."""
    from claude_voice import transcripts

    monkeypatch.setattr(transcripts, "HELPERS_IN", tmp_path_factory.mktemp("helpers"))


@pytest.fixture(autouse=True)
def no_github(monkeypatch):
    """A watched server asks GitHub for the open pull requests, and waits for the answer before
    its next look at the sessions. The suite answers as if GitHub's command line were missing."""
    from claude_voice import software

    monkeypatch.setattr(software, "_gh", lambda args: None)


@pytest.fixture(autouse=True)
def no_real_github_for_the_software_report(monkeypatch):
    """A bridge started in a test builds its software report behind; it finds no `gh` to ask,
    so no test reaches GitHub. A test that wants answers passes its own fake runner."""
    import types

    from claude_voice import procs, software

    finder = types.SimpleNamespace(find=lambda name: None if name == "gh" else procs.find(name))
    monkeypatch.setattr(software, "procs", finder)


@pytest.fixture(autouse=True)
def no_owner_env_file(tmp_path_factory, monkeypatch):
    """Keep the owner's own environment file out of the suite: it holds the owner's token, and a
    test never reads it. A test that wants one writes its own."""
    from claude_voice import envfile

    none = tmp_path_factory.mktemp("env") / "none"
    monkeypatch.setattr(envfile, "ENV_FILE", none)
    monkeypatch.setenv("CLAUDE_VOICE_ENV_FILE", str(none))  # for the commands the docs run


@pytest.fixture(autouse=True)
def no_real_claude_sign_in(monkeypatch):
    """The work page asks Claude Code when its sign-in runs out. The suite answers that it
    cannot say, so no test runs the owner's Claude Code or depends on the owner's sign-in date."""
    from claude_voice import work

    monkeypatch.setattr(work, "read_login_expiry", lambda: None)

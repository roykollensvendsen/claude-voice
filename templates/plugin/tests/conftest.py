"""What every test shares: a private temporary folder, no owner's config, and a guarded recovery file.

**A temporary folder of the run's own.** Before any test, the run makes
``<system temp>/gizmo-mcp-tests-<checkout hash>/run-<n>`` and points ``TMPDIR``,
``tempfile.tempdir`` and pytest's ``--basetemp`` into it, so every subprocess a test starts uses
it too. At the end it must hold nothing but pytest's own folder: anything else fails the run,
naming each path and the test file that ran last. Then it is removed. A run that was killed
leaves its folder behind, and the next start sweeps it, by its lock (``scripts/temproot.py``,
design A: the folder is locked before it takes a run's name).

TODO: pytest-xdist is not a dependency, and a run split over workers is not handled: each worker
would make a run folder of its own, and pytest's --basetemp would differ per worker. Give each
worker a subfolder of the controller's, with a test that runs -n 2, before adding it.

pytest's shared ``<tmp>/pytest-of-<user>/`` is never used or swept: with a retention policy of
"none" pytest would sweep other sessions' running tests there, so that setting is refused
(``tests/test_temp_files.py``). Only other programs' files are in ``/tmp``, so the run checks
its own folder, never all of ``/tmp``.

**The real recovery file.** `scripts/mutate.py` keeps the file it is mutating in
`scripts/.mutate-recovery.json` while the suite runs. A test that ran the runner without its
own `RECOVERY` would overwrite or remove it, so the run fails when that file changed.
"""

import os
import pathlib
import signal
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import temproot

RECOVERY = ROOT / "scripts/.mutate-recovery.json"
BEFORE = pytest.StashKey[bytes | None]()
#: pytest's own folder inside the run's, which pytest prunes itself.
BASETEMP = "pytest"
KEEP = {BASETEMP, temproot.LOCK}
_state: dict[str, object] = {}


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """Make the run's folder before pytest's own tmp_path factory reads --basetemp."""
    _state["saved"] = (os.environ.get("TMPDIR"), tempfile.tempdir)
    root = temproot.start(temproot.parent_for("tests", ROOT))
    config.option.basetemp = str(root / BASETEMP)
    _state["root"] = root
    os.environ["TMPDIR"] = str(root)
    tempfile.tempdir = str(root)


def pytest_sessionstart(session: pytest.Session) -> None:
    session.config.stash[BEFORE] = _now()


def pytest_runtest_logfinish(nodeid: str) -> None:
    _state["last"] = nodeid.split("::", 1)[0]


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session) -> None:
    if _now() != session.config.stash[BEFORE]:
        print(
            "\nA test changed scripts/.mutate-recovery.json, the rule check's real recovery file. "
            "A test that runs scripts/mutate.py must point RECOVERY at its own temporary file."
        )
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    root = pathlib.Path(str(_state["root"]))
    left = temproot.leftovers(root, KEEP)
    if left:
        print(f"\nThe tests left temporary files behind; the last test file to run was {_state.get('last')}:")
        for path in left:
            print(f"  {path}")
        print("Make each in tmp_path, or remove it in a finally.")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    _state["passed"] = session.exitstatus == 0


def pytest_unconfigure(config: pytest.Config) -> None:  # noqa: ARG001 - pytest's own signature
    """Put TMPDIR back, and remove the run's folder; a failed run keeps pytest's, for reading."""
    saved_env, saved_tempdir = _state.get("saved", (None, None))  # type: ignore[misc]
    if saved_env is None:
        os.environ.pop("TMPDIR", None)
    else:
        os.environ["TMPDIR"] = saved_env
    tempfile.tempdir = saved_tempdir
    root = _state.get("root")
    if root is None:
        return
    root = pathlib.Path(str(root))
    if _state.get("passed"):
        temproot.finish(root)
    else:
        for path in temproot.leftovers(root, KEEP):
            temproot.remove(path)


@pytest.fixture(autouse=True)
def stop_handlers_put_back():
    """Put SIGINT, SIGTERM and SIGHUP back after each test, as a test calling a main() changes them.

    Left in place, they would make a later test fail for a reason not its own
    (`tests/test_stop_handlers.py`).
    """
    stops = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    before = {each: signal.getsignal(each) for each in stops}
    yield
    for each, handler in before.items():
        signal.signal(each, handler)


@pytest.fixture(autouse=True)
def no_owner_config(tmp_path_factory, monkeypatch):
    """Keep the owner's own config out of the suite; a test that wants one writes its own."""
    monkeypatch.setenv("GIZMO_MCP_CONFIG", str(tmp_path_factory.mktemp("config") / "none.toml"))


def _now() -> bytes | None:
    return RECOVERY.read_bytes() if RECOVERY.is_file() else None

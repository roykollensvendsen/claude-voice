"""What every test shares: no owner's config, and no test may touch the real recovery file.

`scripts/mutate.py` keeps the file it is mutating in `scripts/.mutate-recovery.json` while the
suite runs. A test that ran the runner without its own `RECOVERY` would overwrite or remove
it, and in the middle of a mutation run the original would be lost. So the whole run fails
when that file changed. `tests/test_real_recovery_file_is_guarded.py` proves it.
"""

import pathlib

import pytest

ROOT = pathlib.Path(__file__).parent.parent
RECOVERY = ROOT / "scripts/.mutate-recovery.json"
BEFORE = pytest.StashKey[bytes | None]()


@pytest.fixture(autouse=True)
def no_owner_config(tmp_path_factory, monkeypatch):
    """Keep the owner's own config out of the suite; a test that wants one writes its own."""
    monkeypatch.setenv("GIZMO_MCP_CONFIG", str(tmp_path_factory.mktemp("config") / "none.toml"))


def _now() -> bytes | None:
    return RECOVERY.read_bytes() if RECOVERY.is_file() else None


def pytest_sessionstart(session: pytest.Session) -> None:
    session.config.stash[BEFORE] = _now()


def pytest_sessionfinish(session: pytest.Session) -> None:
    if _now() != session.config.stash[BEFORE]:
        print(
            "\nA test changed scripts/.mutate-recovery.json, the rule check's real recovery file. "
            "A test that runs scripts/mutate.py must point RECOVERY at its own temporary file."
        )
        session.exitstatus = pytest.ExitCode.TESTS_FAILED

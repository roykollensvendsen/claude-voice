"""No run leaves a temporary file behind, and no run removes temporary files it did not make.

The real proof is the session check in `tests/conftest.py`: a run whose tests leave anything in
its private folder fails. The tests here prove that check in a scratch project, and add a static
check that refuses the usual ways a temporary file is made and forgotten. That one is only a
slip guard: it reads the code, it does not run it.
"""

import ast
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).parent.parent
FORGOTTEN = "a temporary file or folder made with no removal beside it"


def _calls(node, name):
    return [
        c
        for c in ast.walk(node)
        if isinstance(c, ast.Call) and (getattr(c.func, "attr", None) == name or getattr(c.func, "id", None) == name)
    ]


def _names(stmt):
    """The names an assignment binds; none for any other statement."""
    if isinstance(stmt, ast.Assign):
        targets = stmt.targets
    elif isinstance(stmt, ast.AnnAssign):
        targets = [stmt.target]
    else:
        return set()
    return {n.id for target in targets for n in ast.walk(target) if isinstance(n, ast.Name)}


def _handed(calls, names):
    """Whether any of `calls` is handed one of `names`, as an argument or as what it is called on."""
    return any(isinstance(n, ast.Name) and n.id in names for call in calls for n in ast.walk(call))


def _all_calls(statements):
    return [c for stmt in statements for c in ast.walk(stmt) if isinstance(c, ast.Call)]


def _forgotten_folders(tree, where):
    """A folder made by mkdtemp whose name no `finally` beside it, or around it, removes."""
    safe = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        statements = body if isinstance(body, list) else []
        for i, stmt in enumerate(statements):
            names = _names(stmt)
            later = [s for s in statements[i + 1 :] if isinstance(s, ast.Try)]
            around = [node] if isinstance(node, ast.Try) else []
            if names and any(_handed(_all_calls(s.finalbody), names) for s in later + around):
                safe |= {id(c) for c in ast.walk(stmt)}
    return [f"{where}:{c.lineno}: mkdtemp, {FORGOTTEN}" for c in _calls(tree, "mkdtemp") if id(c) not in safe]


def _forgotten_files(tree, where):
    """A file made by mkstemp whose name its function never removes or moves, and NamedTemporaryFile(delete=False)."""
    found = []
    for function in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
        gone = [c for name in ("unlink", "remove", "replace") for c in _calls(function, name)]
        for stmt in ast.walk(function):
            made = _calls(stmt, "mkstemp") if _names(stmt) else []
            if made and not _handed(gone, _names(stmt)):
                found += [f"{where}:{c.lineno}: mkstemp, {FORGOTTEN}" for c in made]
        bare = [
            c for c in _calls(function, "mkstemp") if not any(c in ast.walk(s) for s in ast.walk(function) if _names(s))
        ]
        found += [f"{where}:{c.lineno}: mkstemp, {FORGOTTEN}" for c in bare]
    found += [
        f"{where}:{call.lineno}: NamedTemporaryFile(delete=False), {FORGOTTEN}"
        for call in _calls(tree, "NamedTemporaryFile")
        if any(k.arg == "delete" and getattr(k.value, "value", True) is False for k in call.keywords)
    ]
    return found


def _kept_paths(tree, where):
    """A temporary path a module keeps as a constant, read before the test run moves the folder."""
    return [
        f"{where}:{stmt.lineno}: a temporary path kept as a module constant"
        for stmt in tree.body
        if isinstance(stmt, ast.Assign | ast.AnnAssign)
        and stmt.value is not None
        and any(_calls(stmt.value, n) for n in ("gettempdir", "mkdtemp", "TemporaryDirectory"))
    ]


def offences(source, where="x.py", *, src=False):
    """The ways `source` makes a temporary file and may leave it, by line."""
    tree = ast.parse(source)
    found = _forgotten_folders(tree, where) + _forgotten_files(tree, where)
    if src:
        found += _kept_paths(tree, where)
    return sorted(set(found))


def test_no_code_makes_a_temporary_file_it_may_leave_behind():
    found = []
    for folder, pattern in (("src", "**/*.py"), ("scripts", "*.py")):
        for path in sorted((ROOT / folder).glob(pattern)):
            text = path.read_text(encoding="utf-8")
            found += offences(text, path.relative_to(ROOT).as_posix(), src=folder == "src")
    assert found == []


@pytest.mark.parametrize(
    ("source", "said"),
    [
        ("import tempfile\nd = tempfile.mkdtemp()\n", "mkdtemp"),
        ("import tempfile\ndef f():\n    fd, name = tempfile.mkstemp()\n", "mkstemp"),
        ("import tempfile\nf = tempfile.NamedTemporaryFile(delete=False)\n", "NamedTemporaryFile"),
        (
            (
                "import tempfile, shutil\ndef f(other):\n    d = tempfile.mkdtemp()\n"
                "    try:\n        pass\n    finally:\n        shutil.rmtree(other)\n"
            ),
            "mkdtemp",
        ),
        (
            "import tempfile\ndef f(text):\n    fd, name = tempfile.mkstemp()\n    return text.replace('a', 'b')\n",
            "mkstemp",
        ),
    ],
    ids=["mkdtemp", "mkstemp", "NamedTemporaryFile", "a finally that removes another name", "str.replace"],
)
def test_the_static_check_refuses_each_forgotten_temporary_file(source, said):
    found = offences(source)
    assert len(found) == 1
    assert said in found[0]


def test_the_static_check_lets_a_removed_temporary_file_through():
    removed = (
        "import tempfile, shutil, os\n"
        "def f():\n"
        "    d = tempfile.mkdtemp()\n"
        "    try:\n        pass\n    finally:\n        shutil.rmtree(d)\n"
        "def g():\n"
        "    fd, name = tempfile.mkstemp()\n"
        "    os.replace(name, 'there')\n"
        "def h():\n"
        "    with tempfile.TemporaryDirectory() as d:\n        pass\n"
    )
    assert offences(removed) == []


def test_a_module_in_src_keeps_no_temporary_path_as_a_constant():
    """It would be read before the test run moves the temporary folder, so it would escape it."""
    assert offences("import tempfile\nHERE = tempfile.gettempdir()\n", src=True)
    assert offences("import tempfile\nHERE = tempfile.gettempdir()\n") == []


def test_nothing_sets_pytest_to_sweep_the_shared_folder():
    """With a policy of "none", or a count of 0, pytest sweeps every numbered folder in the shared
    pytest-of-<user>, other sessions' running tests included.
    """
    for name in ("pyproject.toml", "tests/conftest.py"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert 'tmp_path_retention_policy = "none"' not in text, name
        assert "tmp_path_retention_count = 0" not in text, name
        assert "pytest-of-" not in text.replace("pytest-of-<user>", ""), name
    settings = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'tmp_path_retention_policy = "failed"' in settings
    assert "tmp_path_retention_count = 1" in settings


def test_a_run_s_temporary_files_lie_under_its_own_folder(tmp_path):
    root = pathlib.Path(tempfile.gettempdir())
    assert root.name.startswith("run-")
    assert os.environ["TMPDIR"] == str(root)
    assert tmp_path.is_relative_to(root)
    assert "pytest-of-" not in str(tmp_path)
    assert pathlib.Path(tempfile.gettempdir()) == root
    child = subprocess.run(
        [sys.executable, "-c", "import tempfile; print(tempfile.gettempdir())"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert pathlib.Path(child.stdout.strip()) == root


# --- The session check, proved in a scratch project -----------------------------------


LEAVES = "import tempfile\ndef test_it():\n    tempfile.mkstemp()\n"
REMOVES = (
    "import os, tempfile\ndef test_it():\n    fd, name = tempfile.mkstemp()\n    os.close(fd)\n    os.unlink(name)\n"
)
CHILD_LEAVES = (
    "import subprocess, sys\n"
    "def test_it():\n"
    "    subprocess.run([sys.executable, '-c', 'import tempfile; tempfile.mkdtemp()'], check=True)\n"
)
TMP_PATH = "def test_it(tmp_path):\n    (tmp_path / 'kept.txt').write_text('x')\n"


def _project(tmp_path, test):
    project = tmp_path / "project"
    (project / "tests").mkdir(parents=True)
    (project / "scripts").mkdir()
    shutil.copy(ROOT / "tests/conftest.py", project / "tests/conftest.py")
    shutil.copy(ROOT / "scripts/temproot.py", project / "scripts/temproot.py")
    (project / "tests/test_scratch.py").write_text(test)
    return project


def _run(project, base):
    env = {**os.environ, "TMPDIR": str(base)}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(project), "tests"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _runs(base):
    return [p for parent in base.iterdir() for p in parent.iterdir()] if base.is_dir() else []


@pytest.mark.parametrize(
    ("test", "passes"), [(LEAVES, False), (REMOVES, True), (CHILD_LEAVES, False), (TMP_PATH, True)]
)
def test_a_test_that_leaves_a_temporary_file_fails_the_run(tmp_path, test, passes):
    base = tmp_path / "tmp"
    base.mkdir()
    done = _run(_project(tmp_path, test), base)
    assert (done.returncode == 0) == passes, done.stdout + done.stderr
    if not passes:
        assert "left temporary files behind" in done.stdout
        assert "tests/test_scratch.py" in done.stdout
    # A failed run keeps pytest's own folder, for reading, until the next start sweeps it.
    kept = {p.name for run in _runs(base) for p in run.iterdir()}
    assert kept <= {"pytest", temproot_lock()}
    assert (_runs(base) == []) == passes


def test_a_killed_test_run_is_swept_at_the_next_start(tmp_path):
    """A run killed outright leaves its folder, its lock no longer held; the next start sweeps it."""
    base = tmp_path / "tmp"
    base.mkdir()
    project = _project(tmp_path, "def test_it():\n    pass\n")
    sys.path.insert(0, str(ROOT / "scripts"))
    import temproot

    left = temproot.parent_for("tests", project, base) / "run-1"
    left.parent.mkdir(mode=0o700)
    left.mkdir()
    (left / temproot.LOCK).touch()
    (left / "made-by-the-killed-run.txt").write_text("x")
    (left / "shut").mkdir()
    (left / "shut" / "inside.txt").write_text("x")
    (left / "shut").chmod(0)  # as a test that took its own folder's rights away would leave it
    other = base / "gizmo-mcp-tests-00000000"  # another checkout's: never ours to sweep
    other.mkdir()
    done = _run(project, base)
    assert done.returncode == 0, done.stdout
    assert not left.exists()
    assert other.is_dir()


def temproot_lock():
    sys.path.insert(0, str(ROOT / "scripts"))
    import temproot

    return temproot.LOCK

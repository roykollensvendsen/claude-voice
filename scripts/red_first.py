#!/usr/bin/env python3
"""Show that a pull request's new and changed tests fail on the code before it.

A test that passes on its first run proves nothing. This runs the tests a pull
request adds or changes against its merge base, with only the changes under
tests/ applied, and each must fail there. A helper a test file already had keeps
its old body there, since a fix may live in the helper itself. It runs them
again on the branch, where each must pass. Run it from the repository root:

    uv run python scripts/red_first.py --base origin/main --head HEAD [--body-file body.md]

The base is narrowed to the merge base of the two. A pull request with no new or
changed tests passes at once. A test that pins behaviour that already exists is
excused by a line of its own in the pull request body (ADR-CV-015):

    Red-first: existing behaviour - test_name
    Red-first: existing behaviour - all, why

The body is read from --body-file, or else from the RED_FIRST_BODY environment variable.
Exit 1 names every test that proved nothing or fails on the branch, and what to
do about it. The module imports only the standard library: the before-tree's
pytest loads it as a plugin, to report each test's outcome.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import fnmatch
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable

#: What one test did in one tree: failed (or errored, or could not import), passed, or skipped
#: (which includes never being collected, as an e2e test is not).
Outcome = str
Run = Callable[[list[str]], tuple[dict[str, Outcome], dict[str, Outcome]]]

MARKER = re.compile(r"^[ \t]*Red-first:[ \t]*existing behaviou?r[ \t]*[-–—][ \t]*([^\s,]+)", re.MULTILINE)
#: The environment variables the pytest plugin below reads, set only for the runs this script starts.
WANTED, REPORT = "RED_FIRST_IDS", "RED_FIRST_REPORT"
#: The name the plugin's copy is loaded under in each tree.
PLUGIN = "red_first_outcomes_plugin"


def _git(*args: str, cwd: pathlib.Path | str | None = None, stdin: str | None = None) -> str:
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        input=stdin,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


# --- Which tests count -------------------------------------------------------


def _shape(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """What a test checks, apart from its name, its docstring, its formatting and its comments."""
    copy = ast.parse(ast.unparse(node)).body[0]
    assert isinstance(copy, ast.FunctionDef | ast.AsyncFunctionDef)
    copy.name = ""
    first = copy.body[0] if copy.body else None
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
        copy.body = copy.body[1:] or [ast.Pass()]
    return ast.dump(copy)


def _tests(source: str, where: str) -> dict[str, str]:
    """Each test in one file, by its id within the file (``name`` or ``Class::name``), to its shape."""
    try:
        module = ast.parse(source)
    except SyntaxError as e:
        msg = f"{where} does not parse (line {e.lineno}: {e.msg}); fix it and push again"
        raise SystemExit(msg) from e
    found = {}
    for node in module.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name.startswith("test_"):
            found[node.name] = _shape(node)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for inner in node.body:
                if isinstance(inner, ast.FunctionDef | ast.AsyncFunctionDef) and inner.name.startswith("test_"):
                    found[f"{node.name}::{inner.name}"] = _shape(inner)
    return found


def shapes_of(sources: Iterable[str]) -> set[str]:
    """The shape of every test in these sources: the base's tests, to tell a move from a new test."""
    return {shape for source in sources for shape in _tests(source, "a test file on the base").values()}


def count(changes: dict[str, tuple[str | None, str | None]], before: set[str]) -> tuple[list[str], list[str]]:
    """The tests a change must prove, and the tests it removed.

    `changes` maps each changed test file to its text on the base (None when new) and on the
    branch (None when deleted); `before` is the shape of every test on the base.
    """
    counted, removed = [], []
    for path, (old, new) in sorted(changes.items()):
        was = _tests(old, f"{path} on the base") if old is not None else {}
        now = _tests(new, path) if new is not None else {}
        removed += [f"{path}::{key}" for key in was if key not in now]
        for key, shape in now.items():
            if key in was:
                if was[key] != shape:
                    counted.append(f"{path}::{key}")
                continue
            # RULE: a test whose body only moved or was renamed is not counted
            if shape in before:
                continue
            counted.append(f"{path}::{key}")
    return counted, removed


# --- Whether they proved it --------------------------------------------------


def excuses(body: str) -> set[str]:
    """The tests the pull request body excuses, by name; ``all`` excuses every one."""
    return set(MARKER.findall(body or ""))


def _excused(test: str, names: set[str]) -> bool:
    within = test.split("::", 1)[1]
    return bool(names & {"all", test, within, within.rsplit("::", 1)[-1]})


def verdict(before: Outcome, after: Outcome, excused: bool) -> str:
    """One test's result: proved, broken, excused, or not proved."""
    # RULE: the red-first marker never excuses a test that fails on the branch
    if after not in ("passed", "skipped"):
        return "broken"
    if before == "failed" and after == "passed":
        return "proved"
    # RULE: a new or changed test must fail before the change
    return "excused" if excused else "not proved"


def _plural(ids: list[str], one: str, many: str) -> str:
    return f"{len(ids)} {one if len(ids) == 1 else many}"


def prove(counted: list[str], run: Run, body: str) -> tuple[bool, list[str]]:
    """Run the counted tests before the change and on the branch, and say whether they prove it."""
    # RULE: a pull request with no new or changed tests passes red-first
    if not counted:
        return True, ["nothing to prove: no new or changed tests"]
    before, after = run(counted)
    names = excuses(body)
    result = {t: verdict(before.get(t, "skipped"), after.get(t, "skipped"), _excused(t, names)) for t in counted}
    said = []
    if proved := [t for t in counted if result[t] == "proved"]:
        said.append(f"{_plural(proved, 'test', 'tests')} failed before this change and pass on it: {', '.join(proved)}")
    if excused := [t for t in counted if result[t] == "excused"]:
        said.append(f"excused by the pull request body, for the reviewer to weigh: {', '.join(excused)}")
    if weak := [t for t in counted if result[t] == "not proved"]:
        listed = ", ".join(
            f"{t} (skipped)" if "skipped" in (before.get(t, "skipped"), after.get(t, "skipped")) else t for t in weak
        )
        first = weak[0].rsplit("::", 1)[-1]
        said.append(
            f"{_plural(weak, 'test', 'tests')} passed before this change, or skipped, so"
            f" {'it doesn' if len(weak) == 1 else 'they don'}'t show it was needed: {listed}."
            " Make each fail without the change, or, if it pins behaviour that already exists,"
            f" add this line to the pull request body: `Red-first: existing behaviour - {first}`"
        )
    if broken := [t for t in counted if result[t] == "broken"]:
        verb = "fails" if len(broken) == 1 else "fail"
        said.append(f"{', '.join(broken)} {verb} on this branch. Fix the test or the code; no marker excuses that.")
    return not weak and not broken, said


# --- The two trees -----------------------------------------------------------


def changed_test_files(base: str, head: str, cwd: pathlib.Path | str | None = None) -> list[str]:
    """The test files added, changed or deleted between the two."""
    names = _git("diff", "--name-only", "--no-renames", base, head, "--", "tests", cwd=cwd).split()
    return sorted(n for n in names if fnmatch.fnmatch(pathlib.PurePosixPath(n).name, "test_*.py"))


def _show(rev: str, path: str, cwd: pathlib.Path | str | None) -> str | None:
    try:
        return _git("show", f"{rev}:{path}", cwd=cwd)
    except subprocess.CalledProcessError:
        return None


def before_tree(base: str, head: str, dest: pathlib.Path, cwd: pathlib.Path | str | None = None) -> None:
    """A checkout of the base with every change under tests/ from the branch, and none of the code."""
    _git("worktree", "add", "--detach", "--quiet", str(dest), base, cwd=cwd)
    patch = _git("diff", "--binary", "--no-renames", base, head, "--", "tests", cwd=cwd)
    if not patch:
        return
    try:
        _git("apply", "--whitespace=nowarn", cwd=dest, stdin=patch)
    except subprocess.CalledProcessError as e:
        msg = (
            f"The before-tree couldn't be built: `git apply` refused the tests' changes on {base}"
            f" ({e.stderr.strip()}). Rebase on main and push again."
        )
        raise SystemExit(msg) from e
    for path in changed_test_files(base, head, cwd=cwd):
        old, new = _show(base, path, cwd), _show(head, path, cwd)
        if old is not None and new is not None:
            (dest / path).write_text(with_old_helpers(old, new), encoding="utf-8")


def _blocks(source: str) -> dict[str, tuple[int, int]]:
    """Each top-level definition or assignment by name, as its first and last line, decorators included."""
    found: dict[str, tuple[int, int]] = {}
    for node in ast.parse(source).body:
        names: list[str] = []
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        first = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])
        for name in names:
            found[name] = (first, node.end_lineno or node.lineno)
    return found


def _is_test(name: str) -> bool:
    return name.startswith("test") or name.startswith("Test")


def with_old_helpers(old: str, new: str) -> str:
    """The branch's test file with every helper the base already had put back as it was there.

    A fix may live in a test file's own helper; the new test only proves it against the old one.
    A helper new on the branch, the imports and the tests themselves come from the branch."""
    try:
        before, after = _blocks(old), _blocks(new)
    except SyntaxError:
        return new
    old_lines, lines = old.splitlines(keepends=True), new.splitlines(keepends=True)
    # RULE: a helper in a test file keeps its old body in the before-tree
    kept = {n: span for n, span in after.items() if n in before and not _is_test(n)}
    for name, (first, last) in sorted(kept.items(), key=lambda item: -item[1][0]):
        was_first, was_last = before[name]
        lines[first - 1 : last] = old_lines[was_first - 1 : was_last]
    return "".join(lines)


class Unreported(Exception):
    """pytest stopped before it reported any test: a conftest that cannot import, a plugin that
    fails to load, or a crash. Reading that as every test skipped would hide it."""

    def __init__(self, code: int) -> None:
        super().__init__(f"pytest stopped before it reported any test (exit code {code})")
        self.code = code


def outcomes(ids: list[str], tree: pathlib.Path, python: list[str]) -> dict[str, Outcome]:
    """Run only these tests in one tree, and give each one's outcome."""
    files = sorted({i.split("::", 1)[0] for i in ids})
    with tempfile.TemporaryDirectory(prefix="red-first-") as scratch:
        wanted, report = pathlib.Path(scratch, "ids.json"), pathlib.Path(scratch, "report.json")
        wanted.write_text(json.dumps(ids), encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
        env |= {WANTED: str(wanted), REPORT: str(report), "PYTHONDONTWRITEBYTECODE": "1"}
        # A copy, alone in the scratch folder and under a name no test imports: on the path as
        # scripts/, the old code's tests could import the branch's own scripts and pass.
        shutil.copyfile(__file__, pathlib.Path(scratch, f"{PLUGIN}.py"))
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [scratch, env.get("PYTHONPATH")]))
        cmd = [*python, "-m", "pytest", "-q", "--tb=short", "-p", "no:cacheprovider", "-p", PLUGIN]
        args = [*cmd, "--continue-on-collection-errors", *files]
        code = subprocess.run(args, cwd=tree, env=env, check=False).returncode  # noqa: S603
        # Exit codes 3 and 4 are pytest's own internal and usage errors.
        if not report.exists() or code in (3, 4):
            raise Unreported(code)
        seen = json.loads(report.read_text(encoding="utf-8"))
    broken_files = set(seen.get("uncollectable", []))
    tests = seen.get("tests", {})
    return {i: "failed" if i.split("::", 1)[0] in broken_files else tests.get(i, "skipped") for i in ids}


def _synced(tree: pathlib.Path) -> list[str]:
    """Set up a tree's own environment, and give the command that runs its Python."""
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    subprocess.run(["uv", "sync", "--locked", "--all-extras", "--quiet"], cwd=tree, env=env, check=True)  # noqa: S607
    return ["uv", "run", "--no-sync", "python"]


def _before(ids: list[str], tree: pathlib.Path, python: list[str]) -> dict[str, Outcome]:
    """The outcomes on the old code, where a run that cannot even start is every test failing."""
    try:
        return outcomes(ids, tree, python)
    except Unreported as stopped:
        print(f"On the code before the change, {stopped}; so every counted test counts as failed there.")  # noqa: T201
        return dict.fromkeys(ids, "failed")


def _branch(ids: list[str], tree: pathlib.Path, python: list[str]) -> dict[str, Outcome]:
    """The outcomes on the branch, where a run that cannot even start fails the check."""
    try:
        return outcomes(ids, tree, python)
    except Unreported as stopped:
        files = " ".join(sorted({i.split("::", 1)[0] for i in ids}))
        msg = (
            f"On the branch, {stopped}: a conftest that cannot import, a plugin that fails to load, or a crash."
            f" Run `uv run pytest {files}` on the branch to see why, fix it, and push again."
        )
        raise SystemExit(msg) from stopped


def run_both(
    base: str,
    head: str,
    cwd: pathlib.Path | str | None = None,
    setup: Callable[[pathlib.Path], list[str]] = _synced,
) -> Run:
    """A run of the counted tests on the before-tree and on the head, each in a checkout of its own.

    `setup` prepares one tree and gives the command that runs its Python; a test passes its own.
    """

    def run(ids: list[str]) -> tuple[dict[str, Outcome], dict[str, Outcome]]:
        place = pathlib.Path(tempfile.mkdtemp(prefix="red-first-", dir=os.environ.get("RUNNER_TEMP")))
        trees = [place / "before", place / "head"]
        try:
            before_tree(base, head, trees[0], cwd=cwd)
            _git("worktree", "add", "--detach", "--quiet", str(trees[1]), head, cwd=cwd)
            return _before(ids, trees[0], setup(trees[0])), _branch(ids, trees[1], setup(trees[1]))
        finally:
            for tree in trees:
                with contextlib.suppress(subprocess.CalledProcessError):
                    _git("worktree", "remove", "--force", str(tree), cwd=cwd)
            # RULE: a run leaves no checkout folder behind
            shutil.rmtree(place, ignore_errors=True)

    return run


# --- The pytest plugin, loaded in each tree from a copy (see outcomes) -------

_seen: dict[str, Outcome] = {}
_uncollectable: list[str] = []
_RANK = {"skipped": 0, "passed": 1, "failed": 2}


def pytest_collection_modifyitems(config, items):  # type: ignore[no-untyped-def]
    """Keep only the tests asked for: a whole file is collected, so an id not in it is never an error."""
    if WANTED not in os.environ:
        return
    wanted = set(json.loads(pathlib.Path(os.environ[WANTED]).read_text(encoding="utf-8")))
    keep = [item for item in items if item.nodeid.split("[", 1)[0] in wanted]
    config.hook.pytest_deselected(items=[item for item in items if item not in keep])
    items[:] = keep


def pytest_collectreport(report):  # type: ignore[no-untyped-def]
    if WANTED in os.environ and report.failed:
        _uncollectable.append(report.nodeid)


def pytest_runtest_logreport(report):  # type: ignore[no-untyped-def]
    """One test's outcome: any failed case or phase fails it, a passed call passes it, else skipped."""
    if WANTED not in os.environ:
        return
    test = report.nodeid.split("[", 1)[0]
    # An expected failure that passed is marked as not meant to, so it is no pass here.
    passed = report.when == "call" and report.passed and not hasattr(report, "wasxfail")
    now = "failed" if report.failed else "passed" if passed else "skipped"
    if _RANK[now] > _RANK[_seen.get(test, "skipped")] or test not in _seen:
        _seen[test] = now


def pytest_sessionfinish(session):  # type: ignore[no-untyped-def]
    if REPORT in os.environ:
        report = {"tests": _seen, "uncollectable": _uncollectable}
        pathlib.Path(os.environ[REPORT]).write_text(json.dumps(report), encoding="utf-8")


# --- The command -------------------------------------------------------------


def publish(said: list[str]) -> None:
    """Print the result, and add it to the run's summary page in a check."""
    text = "\n".join(f"- {line}" for line in said)
    print(text)  # noqa: T201
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with pathlib.Path(summary).open("a", encoding="utf-8") as page:
            page.write(f"## Red first\n\n{text}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="origin/main", help="the branch merged into; its merge base is used")
    parser.add_argument("--head", default="HEAD", help="the pull request's last commit")
    parser.add_argument("--body-file", type=pathlib.Path, help="a file holding the pull request body")
    args = parser.parse_args(argv)
    head = _git("rev-parse", "--verify", f"{args.head}^{{commit}}").strip()
    base = _git("merge-base", args.base, head).strip()
    files = changed_test_files(base, head)
    everywhere = _git("ls-tree", "-r", "--name-only", base, "--", "tests").split()
    on_base = [_show(base, p, None) for p in everywhere if fnmatch.fnmatch(pathlib.PurePosixPath(p).name, "test_*.py")]
    counted, removed = count(
        {p: (_show(base, p, None), _show(head, p, None)) for p in files}, shapes_of(filter(None, on_base))
    )
    body = args.body_file.read_text(encoding="utf-8") if args.body_file else os.environ.get("RED_FIRST_BODY", "")
    ok, said = prove(counted, run_both(base, head), body)
    said.insert(0, f"base {base[:12]}, head {head[:12]}; test files looked at: {', '.join(files) or 'none'}")
    if removed:
        said.append(f"removed, for the reviewer to weigh: {', '.join(removed)}")
    publish(said)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

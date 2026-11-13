#!/usr/bin/env python3
"""The quality ratchet: the code may get better or stay as it is, never worse unless a change says why.

It measures test coverage, the share of rules whose test notices them switched off, the
number of tests, lint and type errors, import cycles and layers among the modules, copied
code, and the most tangled function in each module, and compares them with the levels main
reached. A change that makes one worse fails, unless its pull request has a line of its own

    Ratchet-Exception: coverage - the reason

naming the measure. Main keeps its levels: continuous integration measures them after every
push to main and keeps them with that run, so a pull request never records them
(ADR-CV-009). When those levels are missing, the base is measured in a copy of it, which
takes as long again. `quality.json` holds only the suite's time budget, a limit set by hand.

    uv run python scripts/ratchet.py check --base REF   # measure and compare with REF
    uv run python scripts/ratchet.py check --base REF --levels FILE --out FILE
    uv run python scripts/ratchet.py measure --out FILE  # measure only

`check` reads exceptions from the environment variable RATCHET_EXCEPTIONS (continuous
integration passes the pull request's text). `--skip-mutation` keeps the base's
mutation figures, for a quick local look; continuous integration never skips it.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# The tree to measure: this one, or a copy of the base named by RATCHET_ROOT (see measure_at).
ROOT = Path(os.environ.get("RATCHET_ROOT") or Path(__file__).resolve().parent.parent)
FILE = ROOT / "quality.json"
SOURCE = ROOT / "src"
PACKAGE = SOURCE / "claude_voice"
COVERAGE_WOBBLE = 0.5  # percentage points: coverage differs slightly between machines and versions
HIGHER = ("coverage", "mutation", "tests")
LOWER = ("lint_errors", "type_errors", "cycles", "layer_violations", "duplication")
DEFAULT_BUDGET = {"seconds": 120, "tolerance": 0.25}
NEW_MODULE_CEILING = 15  # the most tangled function a new module may have
BLOCK = 6  # lines in a row that count as a copied block
# Each module's layer: a module may use, when it loads, only modules in the layers below it.
LAYERS = {
    # foundations
    **dict.fromkeys(("procs", "config", "store", "metrics", "titles", "tailnet", "envfile", "presence"), 0),
    **dict.fromkeys(
        (
            "transcripts",
            "sessions",
            "gitstatus",
            "doctor",
            "launch",
            "roles",
            "guard",
            "usage",
            "updater",
            "software",
            "work",
            "plugins",
            "detail",
            "oauth",
            "renames",
            "devmode",
            "terminals",
            "permissions",
            "hints",
            "prompts",
        ),
        1,
    ),  # parts, each about one thing
    **dict.fromkeys(("events", "tree", "approvals", "machines", "plugin_desk", "org"), 2),  # parts built from parts
    **dict.fromkeys(("api", "phone_hook", "agent", "fleet"), 3),  # the doors in from outside
    "server": 4,  # where it is all put together
    "cli": 5,  # the command line that starts it
}
EXCEPTION = re.compile(r"^\s*ratchet-exception:\s*([a-z_]+)", re.IGNORECASE | re.MULTILINE)


# -- measuring ---------------------------------------------------------------------------

DECISIONS = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp, ast.Assert, ast.match_case)
NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _function_complexity(fn: ast.AST) -> int:
    """McCabe's count for one function, leaving out the functions defined inside it."""
    count = 1
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, NESTED):
            continue
        if isinstance(node, DECISIONS):
            count += 1
        elif isinstance(node, ast.BoolOp):
            count += len(node.values) - 1
        elif isinstance(node, ast.comprehension):
            count += 1 + len(node.ifs)
        stack.extend(ast.iter_child_nodes(node))
    return count


def complexity(folder: Path) -> dict[str, int]:
    """The most tangled function in each module under `folder`, by McCabe's count."""
    out = {}
    for path in sorted(folder.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        scores = [
            _function_complexity(n) for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if scores:
            out[path.relative_to(folder).as_posix()] = max(scores)
    return out


def _imports(path: Path, package: str, names: set[str]) -> set[str]:
    """The package's modules `path` imports when it is loaded (not inside a function)."""
    found = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                found.update(alias.name for alias in node.names if not base and alias.name in names)
                if base:
                    found.add(base.split(".")[0])
            elif base.startswith(package + "."):
                found.add(base.split(".")[1])
            elif base == package:
                found.update(alias.name for alias in node.names if alias.name in names)
        elif isinstance(node, ast.Import):
            found.update(a.name.split(".")[1] for a in node.names if a.name.startswith(package + "."))
    return found & names


def cycles(package_dir: Path) -> int:
    """How many groups of modules import each other in a circle when they load."""
    names = {p.stem for p in package_dir.glob("*.py") if p.stem != "__init__"}
    graph = {n: _imports(package_dir / f"{n}.py", package_dir.name, names) - {n} for n in names}
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    groups = 0

    def visit(node: str) -> None:  # Tarjan's strongly connected components
        nonlocal groups
        index[node] = low[node] = len(index)
        stack.append(node)
        on_stack.add(node)
        for nxt in graph[node]:
            if nxt not in index:
                visit(nxt)
                low[node] = min(low[node], low[nxt])
            elif nxt in on_stack:
                low[node] = min(low[node], index[nxt])
        if low[node] == index[node]:
            size = 0
            while True:
                top = stack.pop()
                on_stack.discard(top)
                size += 1
                if top == node:
                    break
            groups += size > 1

    for name in sorted(names):
        if name not in index:
            visit(name)
    return groups


def layer_breaches(package_dir: Path, layers: dict[str, int]) -> list[str]:
    """Every load-time import that reaches up or sideways, and every module with no layer."""
    names = {p.stem for p in package_dir.glob("*.py") if p.stem != "__init__"}
    found = []
    for name in sorted(names):
        if name not in layers:
            found.append(f"{name} has no layer")
            continue
        for used in sorted(_imports(package_dir / f"{name}.py", package_dir.name, names) - {name}):
            if used in layers and layers[used] >= layers[name]:
                found.append(f"{name} (layer {layers[name]}) imports {used} (layer {layers[used]})")
    return found


def duplication(folder: Path) -> float:
    """The share of non-blank source lines, in percent, that sit in a block of BLOCK or more
    lines appearing more than once, within a file or across files, ignoring indentation."""
    files = {
        p: [" ".join(line.split()) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
        for p in sorted(folder.rglob("*.py"))
    }
    where: dict[tuple[str, ...], list[tuple[Path, int]]] = {}
    for path, lines in files.items():
        for i in range(len(lines) - BLOCK + 1):
            where.setdefault(tuple(lines[i : i + BLOCK]), []).append((path, i))
    copied = {(p, j) for spots in where.values() if len(spots) > 1 for p, i in spots for j in range(i, i + BLOCK)}
    total = sum(len(lines) for lines in files.values())
    return round(100 * len(copied) / total, 2) if total else 0.0


def _run(*command: str, cwd: Path = ROOT, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False)


def measure(skip_mutation: bool = False, recorded: dict[str, Any] | None = None) -> dict[str, Any]:
    """Every measure, from the tree as it is."""
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "coverage.json"
        started = time.monotonic()
        suite = _run("uv", "run", "pytest", "-q", "-p", "no:cacheprovider", "--cov", f"--cov-report=json:{report}")
        seconds = round(time.monotonic() - started, 1)
        if suite.returncode:
            raise SystemExit("The suite fails; the ratchet measures a green tree.\n" + suite.stdout[-2000:])
        coverage = round(json.loads(report.read_text())["totals"]["percent_covered"], 2)
    passed = re.search(r"(\d+) passed", suite.stdout)
    lint = _run("uv", "run", "ruff", "check", ".", "--output-format", "json")
    types = _run("uv", "run", "mypy", "src")
    type_errors = re.search(r"Found (\d+) errors?", types.stdout)
    if skip_mutation and recorded:
        mutation = recorded["mutation"]
    else:
        mutate = _run("uv", "run", "python", "scripts/mutate.py")
        tally = re.search(r"(\d+)/(\d+) rules turned off", mutate.stdout)
        killed, total = (int(tally[1]), int(tally[2])) if tally else (0, 1)
        mutation = {"killed": killed if mutate.returncode == 0 else min(killed, total - 1), "total": total}
    return {
        "coverage": coverage,
        "mutation": mutation,
        "tests": int(passed[1]) if passed else 0,
        "lint_errors": len(json.loads(lint.stdout or "[]")),
        "type_errors": int(type_errors[1]) if type_errors else 0,
        "cycles": cycles(PACKAGE),
        "layer_violations": len(layer_breaches(PACKAGE, LAYERS)),
        "duplication": duplication(SOURCE),
        "complexity": complexity(SOURCE),
        "test_seconds": seconds,
        "test_budget": None,
    }


# -- comparing ---------------------------------------------------------------------------


def _score(value: Any) -> float:
    if isinstance(value, dict):  # mutation: the share of rules whose test noticed
        return 100.0 * value["killed"] / max(value["total"], 1)
    return float(value)


def _worse(metric: str, base: Any, now: Any) -> bool:
    wobble = COVERAGE_WOBBLE if metric == "coverage" else 0.0
    if metric in HIGHER:
        return _score(now) < _score(base) - wobble
    return _score(now) > _score(base)


def _differs(metric: str, recorded: Any, now: Any) -> bool:
    wobble = COVERAGE_WOBBLE if metric == "coverage" else 0.0
    return abs(_score(now) - _score(recorded)) > wobble


def compare(base: dict[str, Any], now: dict[str, Any], allowed: set[str], budget: dict[str, Any]) -> list[str]:
    """What is wrong: a measure worse than the base without an exception, or the suite over
    its time budget. Better levels need no recording: main measures its own."""
    found = []
    for metric in (*HIGHER, *LOWER):
        if metric not in base:
            continue  # a measure added since the base: nothing to compare with yet
        # RULE: a measure that gets worse fails the check
        if _worse(metric, base[metric], now[metric]) and metric not in allowed:
            found.append(f"{metric} got worse: {base[metric]} -> {now[metric]}")
    for module, score in now["complexity"].items():
        before = base["complexity"].get(module)
        if before is not None and score > before and "complexity" not in allowed:
            found.append(f"complexity of {module} got worse: {before} -> {score}")
        if before is None and score > NEW_MODULE_CEILING and "complexity" not in allowed:
            found.append(f"{module} is new and has a function of complexity {score}; keep it at {NEW_MODULE_CEILING}")
    limit = budget["seconds"] * (1 + budget["tolerance"])
    if now["test_seconds"] > limit:
        found.append(
            f"test_seconds is {now['test_seconds']}, over the budget of {budget['seconds']}"
            f" (+{budget['tolerance']:.0%})"
        )
    return found


def exceptions(text: str) -> set[str]:
    """The measures a pull request allows to get worse, each on a `Ratchet-Exception:` line."""
    return {m.lower() for m in EXCEPTION.findall(text or "")}


def budget(path: Path) -> dict[str, Any]:
    """The suite's time budget, the one thing `quality.json` keeps."""
    try:
        return dict(json.loads(path.read_text())["test_budget"])
    except (OSError, ValueError, KeyError, TypeError):
        return dict(DEFAULT_BUDGET)


def write_levels(path: Path, now: dict[str, Any]) -> None:
    """Write the measured levels, for main to keep with its run."""
    levels = {k: v for k, v in now.items() if k != "test_budget"}
    path.write_text(json.dumps(levels, indent=2, sort_keys=True) + "\n")


def base_levels(kept: Path | None, measure_base: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Main's kept levels for the base, or, when they are missing, the base measured."""
    try:
        levels = json.loads(kept.read_text()) if kept else None
    except (OSError, ValueError):
        levels = None
    if isinstance(levels, dict) and "coverage" in levels:
        return levels
    # RULE: when main's levels are missing, the base is measured
    return measure_base()


def measure_at(ref: str) -> dict[str, Any]:
    """Measure `ref` in a copy of it, with this ratchet, so both sides are measured alike.

    The copy keeps its own tests and scripts unchanged: this ratchet runs from where it is
    and is pointed at the copy, so the base is measured exactly as it stands."""
    with tempfile.TemporaryDirectory() as tmp:
        copy, out = Path(tmp) / "base", Path(tmp) / "levels.json"
        added = _run("git", "worktree", "add", "--detach", str(copy), ref)
        if added.returncode:
            raise SystemExit(f"Cannot check out {ref} to measure it:\n{added.stderr}")
        try:
            synced = _run("uv", "sync", "--locked", "--all-extras", cwd=copy)
            if synced.returncode:
                raise SystemExit(f"Cannot install {ref} to measure it:\n{synced.stderr[-2000:]}")
            env = {**os.environ, "RATCHET_ROOT": str(copy)}
            here = str(Path(__file__).resolve())
            measured = _run("uv", "run", "python", here, "measure", "--out", str(out), cwd=copy, env=env)
            if measured.returncode:
                raise SystemExit(f"Measuring {ref} failed:\n{measured.stdout[-2000:]}{measured.stderr[-2000:]}")
            return dict(json.loads(out.read_text()))
        finally:
            _run("git", "worktree", "remove", "--force", str(copy))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("check", "measure"))
    parser.add_argument("--base", help="the git ref to compare with, usually main")
    parser.add_argument("--levels", type=Path, help="main's kept levels for the base, if there are any")
    parser.add_argument("--out", type=Path, help="where to write the levels measured now")
    parser.add_argument("--skip-mutation", action="store_true")
    args = parser.parse_args()
    if args.action == "measure":
        now = measure()
        if args.out:
            write_levels(args.out, now)
        print(json.dumps(now, indent=2, sort_keys=True))
        return 0
    if not args.base:
        print("check needs --base, the ref to compare with (usually origin/main).", file=sys.stderr)
        return 1
    base = base_levels(args.levels, lambda: measure_at(args.base))
    now = measure(args.skip_mutation, base)
    if args.out:
        write_levels(args.out, now)
    found = compare(base, now, exceptions(os.environ.get("RATCHET_EXCEPTIONS", "")), budget(FILE))
    for problem in found:
        print(problem, file=sys.stderr)
    if not found:
        print("The ratchet holds: nothing got worse than the base.")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""The quality ratchet: the code may get better or stay as it is, never worse unless a change says why.

It measures test coverage, the share of rules whose own test notices them switched off, the
number of tests, lint and type errors, import cycles among the modules, imports that cross
the layers, copied code, and the most tangled function in each module, and compares them
with the levels main reached. A change that makes one worse fails, unless its pull request
has a line of its own

    Ratchet-Exception: coverage - the reason

naming the measure. Main keeps its levels: continuous integration measures them after every
push to main and keeps them with that run, so a pull request never records them (after
claude-voice's ADR-CV-009). When those levels are missing, the base is measured in a copy of
it, which takes as long again. The time the suite takes is shown, never judged: a slow run
fails nothing (claude-voice's ADR-CV-021).

Every tool runs on the interpreter this script runs on, so a local run, CI and any other CI
measure alike, and a run needs no network once the environment is installed.

    uv run python scripts/ratchet.py check --base REF   # measure and compare with REF
    uv run python scripts/ratchet.py check --base REF --levels FILE --out FILE
    uv run python scripts/ratchet.py measure --out FILE  # measure only

`check` reads exceptions from the environment variable RATCHET_EXCEPTIONS (continuous
integration passes the pull request's text). `--skip-mutation` keeps the base's mutation
figures. A pull request's check in continuous integration skips it, because the
`mutation` job already demands a full score there; main's own run never skips it.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import own_code
import temproot

if TYPE_CHECKING:
    from collections.abc import Callable

# The tree to measure: this one, or a copy of the base named by RATCHET_ROOT (see measure_at).
ROOT = Path(os.environ.get("RATCHET_ROOT") or Path(__file__).resolve().parent.parent)
SOURCE = ROOT / "src"
PACKAGE = SOURCE / "gizmo_mcp"
COVERAGE_WOBBLE = 0.5  # percentage points: coverage differs slightly between machines and versions
HIGHER = ("coverage", "mutation", "tests")
LOWER = ("lint_errors", "type_errors", "cycles", "layer_violations", "duplication")
#: Where each run keeps its folder, held by a lock and removed at its end (scripts/temproot.py).
RATCHET_TMP = temproot.parent_for("ratchet", ROOT)
NEW_MODULE_CEILING = 15  # the most tangled function a new module may have
BLOCK = 6  # lines in a row that count as a copied block
EXCEPTION = re.compile(r"^\s*ratchet-exception:\s*([a-z_]+)", re.IGNORECASE | re.MULTILINE)
TALLY = re.compile(r"(\d+)/(\d+) rules turned off")
#: The layers, lowest first. The core asks the gizmo and words the answers, and knows no MCP;
#: only the top, the server and the command line, knows MCP and puts the core together.
CORE = {"__init__", "device", "fake", "service", "settings", "platform", "platform.base", "platform.linux"}
TOP = {"server", "cli"}
#: Which layers each layer may import from this package, and the outside packages it never may.
ALLOWED = {
    "core": {"core"},
    "top": {"core", "top"},
}
FORBIDDEN = {"core": {"mcp"}}
#: The device adapter, and the functions in it that send to the device: the platform boundary
#: test lets a device's own command through only as a string passed to one of these.
DEVICE = {"file": "device.py", "sends": ["send"]}


# -- measuring ---------------------------------------------------------------------------

DECISIONS = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.IfExp,
    ast.Assert,
    ast.match_case,
)
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
        functions = (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
        scores = [_function_complexity(n) for n in functions]
        if scores:
            out[path.relative_to(folder).as_posix()] = max(scores)
    return out


def _modules(package_dir: Path) -> dict[str, Path]:
    """Every module in the package by its dotted name inside it; a subpackage is its __init__."""
    found = {}
    for path in sorted(package_dir.rglob("*.py")):
        parts = path.relative_to(package_dir).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:  # the package's own __init__ is where every import starts, not a module in a circle
            found[".".join(parts)] = path
    return found


def _resolve(target: str, names: set[str], imported: list[str]) -> set[str]:
    """The modules an import of `imported` names from `target` reaches, inside the package."""
    found = set()
    for name in imported:
        full = f"{target}.{name}" if target else name
        if full in names:
            found.add(full)
        elif target in names:
            found.add(target)
    return found


def _imports(name: str, path: Path, package: str, names: set[str]) -> set[str]:
    """The package's modules `path` imports when it is loaded (not inside a function or an if)."""
    here = name.split(".") if path.stem == "__init__" else name.split(".")[:-1]
    found: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ImportFrom):
            if node.level:
                if node.level - 1 > len(here):
                    continue
                parts = here[: len(here) - (node.level - 1)] + (node.module.split(".") if node.module else [])
            elif node.module and (node.module == package or node.module.startswith(package + ".")):
                parts = node.module.split(".")[1:]
            else:
                continue
            found |= _resolve(".".join(parts), names, [alias.name for alias in node.names])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == package:
                    prefixes = (".".join(parts[1:i]) for i in range(len(parts), 1, -1))
                    found |= {next((p for p in prefixes if p in names), "")} & names
    return found - {name}


def cycles(package_dir: Path) -> int:
    """How many groups of modules import each other in a circle when they load."""
    modules = _modules(package_dir)
    names = set(modules)
    graph = {n: _imports(n, p, package_dir.name, names) for n, p in modules.items()}
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
        for nxt in sorted(graph[node]):
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


def layer(module: str) -> str:
    """The layer of a module, by its dotted name inside the package; "unassigned" when it has none."""
    parts = module.split(".")
    if parts[0] in TOP:
        return "top"
    return "core" if module in CORE else "unassigned"


def _named(path: Path, package_dir: Path) -> set[str]:
    """Every name `path` imports, anywhere in it, relative imports made absolute.

    Imports made only for type checking or inside a function count too: whenever the import
    would run, the layer has to allow it.
    """
    here = path.relative_to(package_dir.parent).with_suffix("").parts[:-1]
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:  # `from . import x` inside the package is gizmo_mcp.x
                base = ".".join([*here[: len(here) - node.level + 1], *filter(None, base.split("."))])
            found |= {f"{base}.{alias.name}" for alias in node.names} | {base}
    return found


def _breaches(module: str, path: Path, package_dir: Path, names: set[str]) -> list[str]:
    """What `module` imports that its layer may not use."""
    mine, package = layer(module), package_dir.name
    found = []
    for name in sorted(_named(path, package_dir)):
        inside = name.removeprefix(package + ".")
        if name.startswith(package + ".") and inside in names:
            # RULE: an import a layer may not use is a breach
            if layer(inside) not in ALLOWED[mine]:
                found.append(f"{module} ({mine}) imports {name} ({layer(inside)})")
        elif name.split(".")[0] in FORBIDDEN.get(mine, set()):
            found.append(f"{module} ({mine}) imports {name}")
    return found


def layer_breaches(package_dir: Path) -> list[str]:
    """Every import that crosses the layers, and every module that has no layer."""
    names = {".".join(p.relative_to(package_dir).with_suffix("").parts) for p in package_dir.rglob("*.py")}
    names |= {n.removesuffix(".__init__") for n in names}
    found = []
    for path in sorted(package_dir.rglob("*.py")):
        module = ".".join(path.relative_to(package_dir).with_suffix("").parts).removesuffix(".__init__")
        # RULE: a module with no layer is a breach
        if layer(module) == "unassigned":
            found.append(f"{module} has no layer")
            continue
        found += _breaches(module, path, package_dir, names)
    return sorted(found)


def duplication(folder: Path) -> float:
    """The share of non-blank source lines, in percent, in a copied block.

    A copied block is BLOCK or more lines appearing more than once, within a file or across
    files, ignoring indentation.
    """
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


def mutation_tally(output: str, returncode: int) -> dict[str, int]:
    """Read `scripts/mutate.py`'s last line; a run that failed is never a full score."""
    tally = TALLY.search(output)
    killed, total = (int(tally[1]), int(tally[2])) if tally else (0, 1)
    return {"killed": killed if returncode == 0 else min(killed, total - 1), "total": total}


def _run(*command: str, cwd: Path = ROOT, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run one step, and stop it as Ctrl-C would if the ratchet is stopped while it runs.

    The step gets a session of its own, so a Ctrl-C in the terminal reaches it only through the
    ratchet, once. On a stop the step's whole session gets SIGINT, uv's children included, and
    is waited for with no time limit: a mutation run puts its line back before it ends, however
    long that takes. A step that hangs even so is ended by hand with `kill -9 -- -<its pid>`,
    which the ratchet prints. Its output goes to temporary files, so a step with a lot to say
    never blocks on a full pipe, and one that outlives the ratchet never dies of a closed one.
    """
    with tempfile.TemporaryFile("w+", encoding="utf-8") as out, tempfile.TemporaryFile("w+", encoding="utf-8") as err:
        step = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=out,
            stderr=err,
            text=True,
            # RULE: a quality step hears terminal stops only through the ratchet
            start_new_session=True,
        )
        try:
            step.wait()
        except BaseException:
            sys.stderr.write(f"stopping {command[0]}; if it hangs, end it with: kill -9 -- -{step.pid}\n")
            try:
                # RULE: a stopped ratchet lets its mutation run put the line back
                os.killpg(step.pid, signal.SIGINT)
            except ProcessLookupError:
                # Only a step that has ended may be missing; a live one with no group was never told.
                # RULE: a stop that cannot reach a running step is not ignored
                if step.poll() is None:
                    raise
            step.wait()
            raise
        out.seek(0)
        err.seek(0)
        return subprocess.CompletedProcess(command, step.returncode, out.read(), err.read())


def _python(*args: str, cwd: Path = ROOT, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run this interpreter, so the tools measured are the ones installed beside it."""
    return _run(sys.executable, *args, cwd=cwd, env=env)


def measure(*, skip_mutation: bool = False, recorded: dict[str, Any] | None = None) -> dict[str, Any]:
    """Every measure, from the tree as it is; every tool it starts keeps its files in this run's folder."""
    with temproot.run_folder(RATCHET_TMP) as tmp:
        env = {**os.environ, "TMPDIR": str(tmp)}
        own_code.require(ROOT, sys.executable, env)
        report = tmp / "coverage.json"
        started = time.monotonic()
        suite = _python("-m", "pytest", "-q", "-p", "no:cacheprovider", "--cov", f"--cov-report=json:{report}", env=env)
        seconds = round(time.monotonic() - started, 1)
        if suite.returncode:
            raise SystemExit("The suite fails; the ratchet measures a green tree.\n" + suite.stdout[-2000:])
        coverage = round(json.loads(report.read_text(encoding="utf-8"))["totals"]["percent_covered"], 2)
        passed = re.search(r"(\d+) passed", suite.stdout)
        lint = _python("-m", "ruff", "check", ".", "--output-format", "json", env=env)
        types = _python("-m", "mypy", "src", env=env)
        type_errors = re.search(r"Found (\d+) errors?", types.stdout)
        if skip_mutation and recorded:
            mutation = recorded["mutation"]
        else:
            mutate = _python("scripts/mutate.py", env=env)
            mutation = mutation_tally(mutate.stdout, mutate.returncode)
    return {
        "coverage": coverage,
        "mutation": mutation,
        "tests": int(passed[1]) if passed else 0,
        "lint_errors": len(json.loads(lint.stdout or "[]")),
        "type_errors": int(type_errors[1]) if type_errors else 0,
        "cycles": cycles(PACKAGE),
        "layer_violations": len(layer_breaches(PACKAGE)),
        "duplication": duplication(SOURCE),
        "complexity": complexity(SOURCE),
        "test_seconds": seconds,
    }


# -- comparing ---------------------------------------------------------------------------


def _score(value: Any) -> float:  # noqa: ANN401 - a level is a number or the mutation tally
    if isinstance(value, dict):  # mutation: the share of rules whose test noticed
        return 100.0 * value["killed"] / max(value["total"], 1)
    return float(value)


def _worse(metric: str, base: Any, now: Any) -> bool:  # noqa: ANN401
    wobble = COVERAGE_WOBBLE if metric == "coverage" else 0.0
    if metric in HIGHER:
        return _score(now) < _score(base) - wobble
    return _score(now) > _score(base)


def _complexity_problems(base: dict[str, int], now: dict[str, int]) -> list[str]:
    found = []
    for module, score in now.items():
        before = base.get(module)
        if before is not None and score > before:
            found.append(f"complexity of {module} got worse: {before} -> {score}")
        if before is None and score > NEW_MODULE_CEILING:
            found.append(f"{module} is new and has a function of complexity {score}; keep it at {NEW_MODULE_CEILING}")
    return found


def compare(base: dict[str, Any], now: dict[str, Any], allowed: set[str]) -> list[str]:
    """What is wrong: a measure worse than the base without an exception.

    Better levels need no recording: main measures its own. The time the suite takes is
    shown, never judged (claude-voice's ADR-CV-021).
    """
    found = []
    for metric in (*HIGHER, *LOWER):
        if metric not in base:
            continue  # a measure added since the base: nothing to compare with yet
        # RULE: a measure that gets worse fails the check
        if _worse(metric, base[metric], now[metric]) and metric not in allowed:
            found.append(f"{metric} got worse: {base[metric]} -> {now[metric]}")
    if "complexity" not in allowed:
        found += _complexity_problems(base.get("complexity", {}), now["complexity"])
    return found


def exceptions(text: str | None) -> set[str]:
    """The measures a pull request allows to get worse, each on a `Ratchet-Exception:` line."""
    return {m.lower() for m in EXCEPTION.findall(text or "")}


def write_levels(path: Path, now: dict[str, Any]) -> None:
    """Write the measured levels, for main to keep with its run."""
    path.write_text(json.dumps(now, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def base_levels(kept: Path | None, measure_base: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Main's kept levels for the base, or, when they are missing, the base measured."""
    try:
        levels = json.loads(kept.read_text(encoding="utf-8")) if kept else None
    except (OSError, ValueError):
        levels = None
    if isinstance(levels, dict) and "coverage" in levels:
        return levels
    # RULE: when the levels main kept are missing, the base is measured
    return measure_base()


#: git, set to fetch a commit by its id whether or not a branch points at it. The newer protocol
#: does by default; the older one, still chosen by an old git or a person's own settings,
#: refuses unless the serving side allows it.
ANY_COMMIT = ("git", "-c", "protocol.version=2", "-c", "uploadpack.allowAnySHA1InWant=true")


def copy_of(ref: str, copy: Path, repo: Path | None = None) -> None:
    """Check `ref` out into `copy`, a repository of its own holding that one commit and no ref.

    A worktree would share every branch of this clone, and git keeps a record of it that a
    killed run leaves behind; clearing such records with a prune would drop other sessions'
    worktrees too. `ref` may be any commit this clone has, a branch tip or not.
    """
    repo = ROOT if repo is None else repo
    sha = _run("git", "rev-parse", "--verify", f"{ref}^{{commit}}", cwd=repo)
    if sha.returncode:
        msg = f"Cannot find {ref} to measure it:\n{sha.stderr}"
        raise SystemExit(msg)
    copy.mkdir(parents=True)
    # RULE: the base is measured where no other branch can be seen
    steps = (
        ("git", "init", "-q"),
        # RULE: the base may be a commit no branch points at
        (*ANY_COMMIT, "fetch", "-q", "--depth", "1", str(repo), sha.stdout.strip()),
        ("git", "checkout", "-q", "--detach", "FETCH_HEAD"),
    )
    for step in steps:
        done = _run(*step, cwd=copy)
        if done.returncode:
            msg = f"Cannot check out {ref} to measure it:\n{done.stderr}"
            raise SystemExit(msg)


#: What would point uv at another checkout's environment instead of the copy's own.
OTHER_ENVIRONMENT = ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")


def measure_at(ref: str, repo: Path | None = None) -> dict[str, Any]:
    """Measure `ref` in a copy of it, with this ratchet, so both sides are measured alike.

    The copy keeps its own tests and scripts unchanged: this ratchet runs from where it is
    and is pointed at the copy, so the base is measured exactly as it stands. The copy lies in
    this run's folder, with an environment of its own, and goes with it however the run ends.
    """
    with temproot.run_folder(RATCHET_TMP) as tmp:
        copy, out = tmp / "base", tmp / "levels.json"
        copy_of(ref, copy, repo)
        env = {k: v for k, v in os.environ.items() if k not in OTHER_ENVIRONMENT}
        env |= {"RATCHET_ROOT": str(copy), "TMPDIR": str(tmp)}
        synced = _run("uv", "sync", "--locked", "--all-extras", cwd=copy, env=env)
        if synced.returncode:
            msg = f"Cannot install {ref} to measure it:\n{synced.stderr[-2000:]}"
            raise SystemExit(msg)
        here = str(Path(__file__).resolve())
        measured = _run("uv", "run", "--no-sync", "python", here, "measure", "--out", str(out), cwd=copy, env=env)
        if measured.returncode:
            msg = f"Measuring {ref} failed:\n{measured.stdout[-2000:]}{measured.stderr[-2000:]}"
            raise SystemExit(msg)
        return dict(json.loads(out.read_text(encoding="utf-8")))


def main() -> int:
    """Measure, or measure and compare with the base."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("action", choices=("check", "measure"))
    parser.add_argument("--base", help="the git ref to compare with, usually origin/main")
    parser.add_argument("--levels", type=Path, help="main's kept levels for the base, if there are any")
    parser.add_argument("--out", type=Path, help="where to write the levels measured now")
    parser.add_argument("--skip-mutation", action="store_true")
    args = parser.parse_args()
    temproot.exit_on_signals()
    if args.action == "measure":
        now = measure()
        if args.out:
            write_levels(args.out, now)
        sys.stdout.write(json.dumps(now, indent=2, sort_keys=True) + "\n")
        return 0
    if not args.base:
        sys.stderr.write("check needs --base, the ref to compare with (usually origin/main).\n")
        return 1
    base = base_levels(args.levels, lambda: measure_at(args.base))
    now = measure(skip_mutation=args.skip_mutation, recorded=base)
    if args.out:
        write_levels(args.out, now)
    found = compare(base, now, exceptions(os.environ.get("RATCHET_EXCEPTIONS")))
    for problem in found:
        sys.stderr.write(problem + "\n")
    if not found:
        sys.stdout.write("The ratchet holds: nothing got worse than the base.\n")
    sys.stdout.write(json.dumps({k: v for k, v in now.items() if k != "complexity"}, sort_keys=True) + "\n")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())

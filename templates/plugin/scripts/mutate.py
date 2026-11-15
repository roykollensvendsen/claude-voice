#!/usr/bin/env python3
"""Turn every rule off in turn and see which tests notice.

Green on a first run proves nothing: a test that has never been red tests
nothing. Where a rule is written before its test, the test is run red first.
Where a rule already exists, this is the substitute. Every rule has one row,
in a file of its own under scripts/mutations/. Run it from the repository root:

    uv run python scripts/mutate.py [--write evidence/tests/rule-mutations.md] [--json mutations.json]

`--json` writes the run's figures, rule by rule, for the checks to keep with
main's run; in a check, the readable table also goes to the run's summary page.
Neither is ever committed.

A kill is not enough on its own. A mutation breaks whatever it breaks, and it
can break a test for an incidental reason — a documented example whose output
happened to change — leaving a rule looking guarded when nothing guards it. So
this also checks that the test named after the rule is one of the ones that went
red, and fails the run when it is not. `tests/test_rules_have_evidence.py` is
what requires that test to exist in the first place.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import itertools
import json
import os
import pathlib
import re
import subprocess
import sys
import tomllib
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import temproot

ROOT = pathlib.Path(__file__).resolve().parent.parent
#: One row per file: scripts/mutations/<the row's file, whole>/<the rule's words>.toml. Every pull
#: request that added a row at the end of the one shared table collided with every
#: other; a file per row only meets another pull request adding the same rule.
ROWS = ROOT / "scripts/mutations"
#: Where the one mutated file is recorded while it is mutated. A `finally` does
#: not run when the process is killed, so without this a hard stop leaves a
#: source file switched off and the next person wondering why the suite is red.
RECOVERY = ROOT / "scripts/.mutate-recovery.json"
#: Where each mutation run keeps its folder, ``run-<n>``, held by a lock and removed when the run
#: ends; a killed run's is swept at the next start (scripts/temproot.py). Outside the checkout,
#: where a test making a git repository of its own would find this one instead, and named after
#: the checkout, so two never sweep each other's.
MUTATE_TMP = temproot.parent_for("mutate", ROOT)


def test_name_for(rule: str) -> str:
    """The test name a rule requires: its own words, as an identifier.

    Imported by ``tests/test_rules_have_evidence.py`` so the rule is written
    once. A rule named "a spoken reply is capped" wants a test called
    ``test_a_spoken_reply_is_capped``.
    """
    words = re.sub(r"[^a-z0-9]+", "_", rule.lower())
    return "test_" + re.sub(r"_+", "_", words).strip("_")


def row_path(rule: dict) -> pathlib.PurePosixPath:
    """Where a rule's row belongs, under ``ROWS``: its file's whole path, then the rule's words.

    A rule named "a read is kept" in ``src/gizmo_mcp/service.py`` goes in
    ``src/gizmo_mcp/service.py/a-read-is-kept.toml``. The whole path, extension
    included, is the same layout in every repository and every language, and no
    two source files can share a folder.
    """
    words = re.sub(r"[^a-z0-9]+", "-", str(rule["name"]).lower()).strip("-")
    if not words:
        msg = f"the rule {rule['name']!r} has no letter or digit to file its row under; reword it"
        raise SystemExit(msg)
    return pathlib.PurePosixPath(rule["file"]) / f"{words}.toml"


def _rows_in(path: pathlib.Path) -> list[dict]:
    return tomllib.loads(path.read_text(encoding="utf-8")).get("rule", [])


def all_rows(folder: pathlib.Path | None = None) -> list[dict]:
    """Every row, one file each, in path order, from `folder` or else from ROWS as it is now."""
    folder = ROWS if folder is None else folder
    found: list[tuple[str, dict]] = []
    for path in sorted(folder.rglob("*.toml")) if folder.is_dir() else []:
        found += [(f"{folder.name}/{path.relative_to(folder).as_posix()}", rule) for rule in _rows_in(path)]
    where: dict[str, str] = {}
    for place, rule in found:
        if rule["name"] in where:
            msg = (
                f"the rule {rule['name']!r} has two rows, in {where[rule['name']]} and in {place};"
                f" delete one and keep {folder.name}/{row_path(rule)}"
            )
            raise SystemExit(msg)
        where[rule["name"]] = place
    return [rule for _, rule in found]


def misfiled(folder: pathlib.Path | None = None) -> list[str]:
    """Row files that hold other than one row, or sit where their rule does not put them."""
    folder = ROWS if folder is None else folder
    found = []
    for path in sorted(folder.rglob("*.toml")) if folder.is_dir() else []:
        here = pathlib.PurePosixPath(path.relative_to(folder).as_posix())
        rows = _rows_in(path)
        if len(rows) != 1:
            found.append(f"{here} holds {len(rows)} rows; give each rule a file of its own")
        elif here != (belongs := row_path(rows[0])):
            found.append(f"{here} should be {belongs}; move it there")
    return found


#: The comment that marks a rule, and where rules are marked: the source and the project's tools.
MARKER = re.compile(r"^[ \t]*#\s*RULE:\s*(.+?)\s*$", re.MULTILINE)
MARKED_IN = (("src", "**/*.py"), ("scripts", "*.py"))


def marked_sources(root: pathlib.Path | None = None) -> list[str]:
    """The files under ``root`` that mark at least one rule, as paths from it."""
    root = ROOT if root is None else root
    return [
        path.relative_to(root).as_posix()
        for folder, pattern in MARKED_IN
        for path in sorted((root / folder).glob(pattern))
        if path.is_file() and MARKER.search(path.read_text(encoding="utf-8"))
    ]


def line_of(text: str, pattern: str, occurrence: int) -> int:
    """Find the one line a rule lives on, by pattern rather than by number."""
    seen = 0
    for i, line in enumerate(text.splitlines()):
        if re.search(pattern, line):
            seen += 1
            if seen == occurrence:
                return i
    msg = f"no line {occurrence} matching {pattern!r}"
    raise SystemExit(msg)


def switched_off(text: str, rule: dict[str, object]) -> str:
    """The file's text with the rule's whole line replaced, keeping its indentation."""
    lines = text.splitlines()
    i = line_of(text, str(rule["find"]), int(str(rule.get("occurrence", 1))))
    # Keep the line's own indentation: a replacement that carries its own
    # would have to be kept in step with the source, and silently would not be.
    indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
    lines[i] = indent + str(rule["replace"]).strip()
    return "\n".join(lines) + "\n"


def broken(file: str, mutated: str) -> str | None:
    """Why a mutated Python file no longer compiles, or None when it does.

    A replacement cut off mid-expression stops the module importing, so every
    test in its files errors and none is the rule's own: the run would call the
    rule killed by another test when the row itself is wrong.
    """
    if not file.endswith(".py"):
        return None
    try:
        compile(mutated, file, "exec")
    except SyntaxError as e:
        return f"line {e.lineno}: {e.msg}"
    return None


def repair() -> None:
    """Put back a file an earlier run was killed in the middle of mutating."""
    if not RECOVERY.is_file():
        return
    kept = json.loads(RECOVERY.read_text(encoding="utf-8"))
    path = ROOT / kept["file"]
    if path.read_text(encoding="utf-8") == kept["mutated"]:
        path.write_text(kept["original"], encoding="utf-8")
        print(f"repaired {kept['file']}, left mutated by a run that was killed")
    else:
        print(f"{kept['file']} was left mutated and has since changed; not touching it")
    RECOVERY.unlink()


def restore(path: pathlib.Path, original: str, mutated: str) -> bool:
    """Put a file back, unless somebody else changed it while the suite ran."""
    if path.read_text(encoding="utf-8") != mutated:
        # Restoring now would overwrite an edit made while this was running,
        # which is a silent way to lose somebody's work.
        # RULE: a file changed during the run is never restored over
        return False
    path.write_text(original, encoding="utf-8")
    return True


#: Numbers each suite run's folder within this run.
_SUITES = itertools.count(1)
#: Colour codes pytest writes around FAILED, which a line-anchored pattern
#: would otherwise never match.
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def files_naming(test: str) -> list[str]:
    """The test files that define `test`; the whole suite when none does."""
    pattern = re.compile(rf"^\s*(?:async )?def {re.escape(test)}\(", re.MULTILINE)
    return [
        p.relative_to(ROOT).as_posix()
        for p in sorted((ROOT / "tests").rglob("test_*.py"))
        if pattern.search(p.read_text(encoding="utf-8"))
    ]


def run_suite(targets: list[str] | None = None, place: pathlib.Path | None = None) -> tuple[int, list[str]]:
    """Run the tests (only `targets` when given) and give back how many failed, and which ones.

    The suite keeps its temporary files in a folder of its own inside `place`, this run's folder,
    and so do the processes it starts: its TMPDIR points there too. The folder is removed after.
    """
    # Two mutations that replace equal-length text leave files of identical
    # size, and CPython validates a cached .pyc from the source's modification
    # time and size -- both at one-second resolution. Inside the same second
    # the next run would import the previous mutation's bytecode and measure
    # the wrong rule, so no bytecode is written at all.
    # RULE: two mutations never share a bytecode cache
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    temporary = (MUTATE_TMP if place is None else place) / f"suite-{next(_SUITES)}"
    temporary.mkdir(parents=True)
    env["TMPDIR"] = str(temporary)
    try:
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "--tb=no",
                "--color=no",
                "-p",
                "no:cacheprovider",
                f"--basetemp={temporary}",
                *(targets or []),
            ],
            capture_output=True,
            text=True,
            cwd=ROOT,
            check=False,
            env=env,
        )
    finally:
        # RULE: a suite run leaves no temporary files behind
        temproot.remove(temporary)
    # --color=no covers the usual run; stripping covers a configuration that
    # turns colour back on, which would otherwise make every kill unattributable.
    # RULE: a coloured failure line is still attributed
    plain = ANSI.sub("", r.stdout)
    m = re.search(r"(\d+) (?:failed|error)", plain)
    failed = re.findall(r"^FAILED (\S+)", plain, re.MULTILINE)
    return int(m.group(1)) if m else 0, [f.split("::")[-1].split("[")[0] for f in failed]


#: The checkout this process holds, kept open until it ends; the kernel lets go however it ends.
_held: list[int] = []


def hold_checkout() -> bool:
    """Hold the checkout for this run alone; False when another run holds it now."""
    fd = os.open(ROOT, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        # RULE: two mutation runs never share a checkout
        return False  # another run holds it
    _held.append(fd)
    return True


def main() -> int:
    """Mutate every rule in turn, restore the tree, and report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", type=pathlib.Path, help="write the evidence table here")
    parser.add_argument("--json", type=pathlib.Path, help="write the run's figures here, for the checks to keep")
    parser.add_argument(
        "--repair-only", action="store_true", help="put back a line a stopped run left switched off, and stop"
    )
    args = parser.parse_args()
    temproot.exit_on_signals()
    if not hold_checkout():
        # Before repair(): the recovery file is the running one's, its line switched off on purpose.
        print(f"a mutation run is already going in {ROOT}; wait for it, or stop it with SIGTERM")
        return 1
    repair()
    # RULE: a repair-only run puts the line back and switches nothing off
    if args.repair_only:
        return 0
    rules = all_rows()
    if not rules:
        return no_rows()
    with temproot.run_folder(MUTATE_TMP) as here:
        return mutate_all(rules, here, args)


def mutate_all(rules: list[dict], here: pathlib.Path, args: argparse.Namespace) -> int:
    """Switch each rule off in turn, keeping every suite's files in `here`; report, and restore."""
    results = []
    lost: list[str] = []
    bad_rows: list[str] = []
    standing: tuple[pathlib.Path, str, str] | None = None
    try:
        for rule in rules:
            path = ROOT / rule["file"]
            # Read each file as it is now, not as it was when the run began: a
            # bulk snapshot taken up front is restored over anything edited
            # since, and losing an edit that way costs more than the run.
            text = path.read_text(encoding="utf-8")
            mutated = switched_off(text, rule)
            why = broken(rule["file"], mutated)
            if why:
                bad_rows.append(rule["name"])
                print(f"BROKEN   {rule['name']}: the replacement is not valid code ({why})")
                continue
            RECOVERY.write_text(
                json.dumps({"file": rule["file"], "original": text, "mutated": mutated}), encoding="utf-8"
            )
            path.write_text(mutated, encoding="utf-8")
            standing = (path, text, mutated)
            # Only the files holding the rule's own test: the suite once per rule outgrew
            # the time the check has. A rule with no test of its own runs the whole suite.
            count, failed = run_suite(files_naming(test_name_for(rule["name"])), here)
            if not restore(path, text, mutated):
                lost.append(rule["file"])
            standing = None
            RECOVERY.unlink(missing_ok=True)
            # A kill by some other test is not evidence about this rule.
            own = test_name_for(rule["name"])
            by_its_own = own in failed
            results.append((rule["name"], rule["file"], count, own, by_its_own))
            verdict = "KILLED  " if by_its_own else ("BY LUCK " if count else "SURVIVED")
            print(f"{verdict} {rule['name']}: {count}")
    finally:
        if standing is not None:
            restore(*standing)
        RECOVERY.unlink(missing_ok=True)

    survived, lucky = _weak(results)
    proven = len(results) - len(survived) - len(lucky)
    print(f"\n{proven}/{len(results)} rules turned off the test that names them")
    if survived:
        print("SURVIVED, so nothing protects them:", ", ".join(survived))
    if lucky:
        print("KILLED BY ANOTHER TEST, so the evidence is not about them:", ", ".join(lucky))
    if bad_rows:
        print("BROKEN ROW, so nothing was tested; make the replacement a whole valid line:", ", ".join(bad_rows))
    if lost:
        print("CHANGED WHILE MUTATED, so left as they are:", ", ".join(sorted(set(lost))))
        print("Edit the tree while this runs and the result is not about your code.")
    if args.write:
        args.write.write_text(report(results, survived, lucky), encoding="utf-8")
    publish(args.json, results)
    return 1 if survived or lucky or lost or bad_rows else 0


def no_rows() -> int:
    """The run's result when there are no rows: a pass only if nothing marks a rule.

    Rules marked with no rows fail: a rows folder emptied or moved by mistake would leave
    every rule unproven and the run green.
    """
    marked = marked_sources()
    # RULE: rules marked with no rows fail the run
    if marked:
        print(
            f"nothing to switch off: {', '.join(marked)} mark rules, but {ROWS.relative_to(ROOT)}"
            " has no rows. Restore it from main (git restore --source=origin/main scripts/mutations),"
            " or add a row per rule."
        )
        return 1
    print(f"no rules marked yet, and no rows in {ROWS.relative_to(ROOT)}")
    return 0


#: One rule's run: its name, its file, the tests gone red, its own test's name, and whether that one went red.
Result = tuple[str, str, int, str, bool]


def _weak(results: list[Result]) -> tuple[list[str], list[str]]:
    """The rules nothing noticed, and those only another test noticed."""
    survived = [name for name, _, count, _, _ in results if count == 0]
    lucky = [name for name, _, count, _, own in results if count and not own]
    return survived, lucky


def figures(results: list[Result]) -> dict[str, Any]:
    """The run as numbers, in the shape every voice repository writes."""
    rules = [
        {"name": name, "file": file, "tests_red": count, "own_test_red": by_own}
        for name, file, count, _, by_own in results
    ]
    # RULE: the mutation report counts only rules whose own test went red
    return {"killed": sum(rule["own_test_red"] for rule in rules), "total": len(rules), "rules": rules}


def publish(to: pathlib.Path | None, results: list[Result]) -> None:
    """Give the run to the checks: its figures to `to`, its table to the run's summary page."""
    if to:
        to.write_text(json.dumps(figures(results), indent=2) + "\n", encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with pathlib.Path(summary).open("a", encoding="utf-8") as page:
            page.write(report(results, *_weak(results)))


def report(results: list[Result], survived: list[str], lucky: list[str]) -> str:
    """Render the run as the evidence page."""
    rows = "\n".join(
        f"| {name} | {count} | `{own}` | {'yes' if by_own else 'NO'} |" for name, _, count, own, by_own in results
    )
    proven = len(results) - len(survived) - len(lucky)
    verdict = (
        f"**Result: {proven} of {len(results)} rules turned off the test that names them.**"
        + (" None survived." if not survived else f" Survived: {', '.join(survived)}.")
        + ("" if not lucky else f" Killed only by other tests: {', '.join(lucky)}.")
    )
    return f"""# Every rule, turned off in turn

*Run {dt.datetime.now(dt.UTC).date()} by `python scripts/mutate.py --write
evidence/tests/rule-mutations.md`, which is how to repeat it. The script
restores every file it touched before it exits. Each rule runs the test files
that hold its own test, so a count is the failures in those files.*

Green on a first run proves nothing: a test that has never been red tests
nothing. Where a rule is written before its test, the test is run red first.
Where a rule already exists, this is the substitute. Each rule is disabled in
turn, by replacing its one line with something that can never hold, and the
suite is run. A rule whose removal breaks no test is a rule nothing protects.

{verdict}

| Rule turned off | Tests that went red | The test that names it | Did that one go red? |
|---|---|---|---|
{rows}

## What this does not prove

That the rules are the right rules, or that a rule catches every case of what
it names. It proves each rule is load-bearing: switch it off and a named test
says so. The last column is why that is worth more than a count: a mutation can
break a test for an incidental reason, and a `NO` there fails this script even
though something went red. A new rule follows the order in `CONTRIBUTING.md`
instead, a failing test first, and gets a row of its own under `scripts/mutations/`,
which `tests/test_rules_have_evidence.py` requires.
"""


if __name__ == "__main__":
    sys.exit(main())

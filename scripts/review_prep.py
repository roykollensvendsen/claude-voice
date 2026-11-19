"""Write what a reviewer works out by hand for a pull request into one file, so they only read the change.

    uv run python scripts/review_prep.py --repo <owner>/<name> --pr <N> --clone <path>

The clone is a checkout of the repository under review; the pull request's head and base
are fetched into it when it lacks them. The file goes to
``~/.local/state/claude-voice/reviews/prep/<name>-<N>-<head12>.json``, with the patch beside
it as ``.patch``. A head already prepared is never written again, so nothing is overwritten.

It runs locally, never in CI: it counts personal terms from a private file
(``~/.config/personal-terms``). That file is read only by ``grep -F -f``, and only counts
come back, never the text that matched.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from datetime import datetime
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
STATE = pathlib.Path.home() / ".local/state/claude-voice/reviews"
TERMS = pathlib.Path.home() / ".config/personal-terms"
#: The fields the one gh call asks for.
FIELDS = "headRefOid,baseRefOid,baseRefName,body,commits,statusCheckRollup"
#: The block every pull request workflow carries: a newer push stops only that pull request's
#: stale run, and every other run is its own group.
CONCURRENCY = (
    "concurrency:\n"
    "  group: ${{ github.workflow }}-${{ github.event_name == 'pull_request' && github.ref || github.run_id }}\n"
    "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}\n"
)
#: The owner's first name as a whole word. It is no term on its own, and it is also an API value
#: that is being renamed, so each hit is shown in context for reading by eye. Built from
#: pieces, so this file holds no name.
OWNER_WORD = re.compile(r"\b" + "r" + "oy" + r"\b", re.IGNORECASE)
RULE = re.compile(r"#\s*RULE:\s*(.+?)\s*$")
TEST = re.compile(r"^\+\s*(?:async\s+)?def\s+(test_\w+)\s*\(")
ALLOWED_TRAILERS = {"co-authored-by"}

Gh = Callable[[list[str]], str]


def _mutate() -> Any:
    """The mutation script beside this one, for how a row switches its line off."""
    spec = importlib.util.spec_from_file_location("mutate", HERE / "mutate.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_gh(args: list[str]) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


class Clone:
    def __init__(self, path: pathlib.Path) -> None:
        self.path = path

    def git(self, *args: str, stdin: str | None = None, ok: tuple[int, ...] = (0,)) -> str:
        done = subprocess.run(
            ["git", "-C", str(self.path), *args], input=stdin, capture_output=True, text=True, check=False
        )
        if done.returncode not in ok:
            raise SystemExit(f"git {' '.join(args)} failed: {done.stderr.strip()}")
        return done.stdout

    def has(self, sha: str) -> bool:
        return not self.git("cat-file", "-t", sha, ok=(0, 1, 128)).strip() == ""

    def show(self, sha: str, path: str) -> str | None:
        text = self.git("show", f"{sha}:{path}", ok=(0, 128))
        return text if text else None


def count_terms(text: str, terms: pathlib.Path) -> int | None:
    """How many lines hold a personal term; None without the terms file. Never what matched."""
    if not terms.is_file():
        return None
    done = subprocess.run(
        ["grep", "-c", "-i", "-F", "-f", str(terms)], input=text, capture_output=True, text=True, check=False
    )
    return int(done.stdout.strip() or 0) if done.returncode in (0, 1) else None


def owner_contexts(text: str) -> list[str]:
    return [text[max(0, m.start() - 30) : m.end() + 30].replace("\n", " ") for m in OWNER_WORD.finditer(text)]


def changed_lines(diff: str) -> list[str]:
    return [line for line in diff.splitlines() if line[:1] in "+-" and not line.startswith(("+++", "---"))]


def files_changed(clone: Clone, base: str, head: str) -> list[dict[str, Any]]:
    found = []
    for line in clone.git("diff", "--raw", "-M", "--no-abbrev", base, head).splitlines():
        meta, _, paths = line.partition("\t")
        old_mode, new_mode, _, _, status = meta.lstrip(":").split()
        modes = f"{old_mode} -> {new_mode}"
        changed = old_mode != new_mode and "000000" not in (old_mode, new_mode)
        found.append({"path": paths.split("\t")[-1], "status": status[0], "mode_change": modes if changed else None})
    return found


def commit_facts(clone: Clone, sha: str, login: str | None, terms: pathlib.Path) -> dict[str, Any]:
    message = clone.git("log", "-1", "--format=%B", sha)
    diff = clone.git("show", "--format=", "--patch", sha)
    text = message + "\n".join(changed_lines(diff))
    trailers = [t for t in clone.git("log", "-1", "--format=%(trailers:only,unfold)", sha).splitlines() if t]
    return {
        "sha": sha,
        "subject": message.splitlines()[0] if message else "",
        "author_login": login,
        "trailers": trailers,
        "terms": count_terms(text, terms),
        "owner_word": len(OWNER_WORD.findall(text)),
        "owner_contexts": owner_contexts(text),
    }


def trailers_ok(commits: list[dict[str, Any]], owner: str) -> bool:
    for c in commits:
        for trailer in c["trailers"]:
            key = trailer.split(":", 1)[0].strip().lower()
            contributor = c["author_login"] not in (None, owner)
            if key not in ALLOWED_TRAILERS and not (key == "signed-off-by" and contributor):
                return False
    return True


def rows(clone: Clone, head: str, changed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mutate = _mutate()
    found = []
    for f in changed:
        if f["status"] == "D" or not f["path"].startswith("scripts/mutations/") or not f["path"].endswith(".toml"):
            continue
        for rule in tomllib.loads(clone.show(head, f["path"]) or "").get("rule", []):
            text = clone.show(head, rule["file"]) or ""
            lines = text.splitlines()
            matches = [i + 1 for i, line in enumerate(lines) if re.search(rule["find"], line)]
            whole = bool(matches) and all(re.fullmatch(rule["find"], lines[i - 1].strip()) for i in matches)
            compiles = bool(matches) and mutate.broken(rule["file"], mutate.switched_off(text, rule)) is None
            found.append(
                {
                    "row": f["path"],
                    "rule": rule["name"],
                    "file": rule["file"],
                    "find": rule["find"],
                    "replace": rule["replace"],
                    "matches": matches,
                    "whole_line_replace": whole,
                    "compiles": compiles,
                }
            )
    return found


def added_rules(clone: Clone, base: str, head: str) -> list[dict[str, str]]:
    found = []
    path = None
    for line in clone.git("diff", "--unified=0", base, head).splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("+") and path and (match := RULE.search(line)):
            found.append({"file": path, "rule": match[1]})
    return found


def rule_rows(clone: Clone, head: str, rules: list[dict[str, str]], found_rows: list[dict[str, Any]]) -> tuple:
    mutate = _mutate()
    without_row = [
        r for r in rules if clone.show(head, f"scripts/mutations/{mutate.row_path({'name': r['rule'], **r})}") is None
    ]
    without_rule = [
        {"row": r["row"], "rule": r["rule"]}
        for r in found_rows
        if f"RULE: {r['rule']}" not in (clone.show(head, r["file"]) or "")
    ]
    return without_row, without_rule


def previous(reviews: pathlib.Path, name: str, pr: int) -> dict[str, Any]:
    try:
        return dict(json.loads((reviews / f"{name}-{pr}.quality.json").read_text()))
    except (OSError, ValueError):
        return {}


def prepare(
    repo: str,
    pr: int,
    clone_path: pathlib.Path,
    *,
    terms: pathlib.Path = TERMS,
    out: pathlib.Path = STATE / "prep",
    reviews: pathlib.Path = STATE,
    gh: Gh = run_gh,
) -> pathlib.Path:
    """Write the prep file for one pull request at its current head, and return its path."""
    owner, name = repo.split("/", 1)
    view = json.loads(gh(["gh", "pr", "view", str(pr), "--repo", repo, "--json", FIELDS]))
    head, base = view["headRefOid"], view["baseRefOid"]
    written = out / f"{name}-{pr}-{head[:12]}.json"
    if written.exists():
        print(f"{written} is already prepared for this head; nothing written")
        return written
    clone = Clone(clone_path)
    if not (clone.has(head) and clone.has(base)):
        clone.git("fetch", "-q", "origin", f"refs/pull/{pr}/head", f"refs/heads/{view['baseRefName']}")
    merge_base = clone.git("merge-base", base, head).strip()
    logins = {c["oid"]: (c.get("authors") or [{}])[0].get("login") for c in view.get("commits") or []}
    shas = clone.git("rev-list", "--reverse", f"{merge_base}..{head}").split()
    commits = [commit_facts(clone, sha, logins.get(sha), terms) for sha in shas]
    patch_id = (clone.git("patch-id", "--stable", stdin=clone.git("diff", merge_base, head)).split() or [""])[0]
    prev = previous(reviews, name, pr)
    changed = files_changed(clone, merge_base, head)
    found_rows = rows(clone, head, changed)
    without_row, without_rule = rule_rows(clone, head, added_rules(clone, merge_base, head), found_rows)
    diff = clone.git("diff", merge_base, head)
    workflows = [f["path"] for f in changed if f["path"].startswith(".github/workflows/") and f["status"] != "D"]
    facts = {
        "repo": repo,
        "pr": pr,
        "head": head,
        "base": base,
        "merge_base": merge_base,
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "files_changed": changed,
        "commits": commits,
        "patch_id_stable": patch_id,
        "prev_patch_id": prev.get("patch_id"),
        "prev_head": prev.get("head"),
        "restamp_candidate": bool(prev.get("patch_id")) and prev.get("patch_id") == patch_id,
        "rows": found_rows,
        "rules_without_row": without_row,
        "rows_without_rule": without_rule,
        "new_tests": [m[1] for line in diff.splitlines() if (m := TEST.match(line))],
        "red_first_lines": [line.strip() for line in (view.get("body") or "").splitlines() if "Red-first" in line],
        "concurrency": {path: CONCURRENCY in (clone.show(head, path) or "") for path in workflows},
        "checks": {
            (c.get("name") or c.get("context")): (c.get("conclusion") or c.get("state"))
            for c in view.get("statusCheckRollup") or []
        },
        "trailers_ok": trailers_ok(commits, owner),
        "notes": [] if terms.is_file() else [f"the terms file {terms} is missing; terms are not counted"],
    }
    out.mkdir(parents=True, exist_ok=True)
    written.with_suffix(".patch").write_text(clone.git("format-patch", "--stdout", f"{merge_base}..{head}"))
    written.write_text(json.dumps(facts, indent=2, ensure_ascii=False) + "\n")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--clone", required=True, type=pathlib.Path, help="a checkout of the repository")
    parser.add_argument("--terms", type=pathlib.Path, default=TERMS)
    parser.add_argument("--out", type=pathlib.Path, default=STATE / "prep")
    parser.add_argument("--reviews", type=pathlib.Path, default=STATE)
    args = parser.parse_args(argv)
    print(prepare(args.repo, args.pr, args.clone, terms=args.terms, out=args.out, reviews=args.reviews))
    return 0


if __name__ == "__main__":
    sys.exit(main())

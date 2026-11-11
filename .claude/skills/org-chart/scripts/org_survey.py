"""Help the org-chart skill: survey the sessions, check an org file, keep it out of git.

Run it with a Python that has claude_voice, as the bridge does:

    uv run python .claude/skills/org-chart/scripts/org_survey.py survey
    uv run python .claude/skills/org-chart/scripts/org_survey.py check <repo> [--draft <file>]
    uv run python .claude/skills/org-chart/scripts/org_survey.py exclude <repo>

It only reads, except `exclude`, which adds one line to the repository's own
.git/info/exclude.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    from claude_voice import config, events, org, roles
except ImportError:
    sys.exit(
        "org_survey: claude_voice cannot be imported. Run this with the bridge's Python: "
        "`uv run python ...` in a claude-voice clone, or `$(uv tool dir)/claude-voice/bin/python ...`."
    )

ROLE_WORDS = ("manager", "quality", "analyst", "reviewer", "tester", "contribute", "release")
NOTES = ("CLAUDE.md", "CLAUDE.local.md", "AGENTS.md")


def _git(folder: Path, *args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def main_repo(folder: Path) -> Path:
    """The main checkout a folder belongs to, so a worktree counts with its repository."""
    common = _git(folder, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common:
        return Path(common).parent if Path(common).name == ".git" else Path(common)
    return folder


def remote_name(url: str) -> str:
    """owner/name from a git remote address, as an org file's `remotes` wants it."""
    m = re.search(r"[:/]([^/:]+/[^/]+?)(?:\.git)?/?$", url.strip())
    return m.group(1) if m else ""


def setting() -> tuple[Path, list[Path]]:
    """The projects folder and every folder whose org file the bridge reads."""
    cfg = config.load(config.FILE, own_clone=roles.own_clone(), env=os.environ)
    root = Path(os.environ.get("CLAUDE_VOICE_ROOT", "~/src")).expanduser()
    given = [cfg.paths.claude_voice, *(p.cwd for p in cfg.plugins)]
    return root, org.known_folders(root, given)


def _notes(repo: Path, others: list[Path]) -> dict[str, Any]:
    text = ""
    for name in NOTES:
        try:
            text += (repo / name).read_text(encoding="utf-8").lower()
        except OSError:
            pass
    return {
        "role_words": [w for w in ROLE_WORDS if w in text],
        "mentions": sorted(o.name for o in others if o != repo and o.name.lower() in text),
    }


def _groups(repos: dict[Path, dict[str, Any]], root: Path, bridge: list[Path]) -> list[dict[str, Any]]:
    """Repositories that look as if they belong together, each link with its reason."""
    links: dict[Path, set[Path]] = defaultdict(set)
    why: list[tuple[Path, Path, str]] = []

    def link(a: Path, b: Path, reason: str) -> None:
        if a != b and b not in links[a]:
            links[a].add(b)
            links[b].add(a)
            why.append((a, b, reason))

    paths = list(repos)
    shared = Counter(p.parent for p in paths)
    common = {Path.home().resolve(), root.resolve()}
    for a in paths:
        for b in paths:
            # a folder that holds most of the owner's repositories says nothing about them
            if a.parent == b.parent and a.parent not in common and shared[a.parent] <= max(4, len(paths) // 4):
                link(a, b, "same parent folder")
            if a.name.split("-")[0] == b.name.split("-")[0] and len(a.name.split("-")[0]) >= 3:
                link(a, b, "same name prefix")
            if b.name in repos[a]["mentions"]:
                link(a, b, f"{a.name}'s notes mention {b.name}")
    if bridge:
        for p in bridge[1:]:
            if p.resolve() in repos and bridge[0].resolve() in repos:
                link(bridge[0].resolve(), p.resolve(), "a plugin of the bridge")
    seen: set[Path] = set()
    out = []
    for start in paths:
        if start in seen or not links[start]:
            continue
        group, todo = set(), [start]
        while todo:
            p = todo.pop()
            if p not in group:
                group.add(p)
                todo += links[p]
        seen |= group
        reasons = [f"{a.name} + {b.name}: {r}" for a, b, r in why if a in group]
        out.append({"repos": sorted(str(p) for p in group), "why": reasons})
    return out


def survey() -> dict[str, Any]:
    root, folders = setting()
    chart = org.load(folders)
    live = events.read_live(Path.home() / ".claude" / "sessions")
    place = chart.placer()
    sessions: list[dict[str, Any]] = []
    repos: dict[Path, dict[str, Any]] = {}
    for d in live:
        cwd = Path(str(d.get("cwd") or ".")).expanduser().resolve()
        repo = main_repo(cwd)
        repos.setdefault(repo, {"sessions": []})["sessions"].append(d.get("name"))
        sessions.append({"name": d.get("name"), "cwd": str(cwd), "repo": str(repo), **place(d).fields()})
    for repo, info in repos.items():
        info["remotes"] = sorted({r for u in org.git_remotes(str(repo)) if (r := remote_name(u))})
        info["placed"] = any(s["programme"] for s in sessions if s["repo"] == str(repo))
        info.update(_notes(repo, list(repos)))
    # a session in a temporary folder is a side question, not part of any programme
    unplaced = {r: i for r, i in repos.items() if not i["placed"] and not str(r).startswith(tempfile.gettempdir())}
    plugin_folders = [f for f in folders if f.parent != root]
    out: dict[str, Any] = {
        "projects_folder": str(root),
        "programmes": [
            {
                "code": p.code,
                "name": p.name,
                "file": str(p.repo / org.FILE),
                "components": {c.code: list(c.written) for c in p.components},
                "roles": {r.code: {"component": r.component, "sessions": list(r.sessions)} for r in p.roles},
            }
            for p in chart.programmes
        ],
        "problems": org.check(chart, {str(d.get("name")) for d in live}),
        "sessions": sessions,
        "repos": {str(r): i for r, i in repos.items()},
        "candidate_groups": _groups(unplaced, root, plugin_folders),
    }
    hints = getattr(org, "programme_hints", None)
    if hints is not None:
        try:
            out["bridge_hints"] = hints(chart, live)
        except TypeError as exc:
            out["bridge_hints"] = f"org.programme_hints exists but was not called: {exc}; read its docstring"
    return out


def check(repo: Path, draft: Path | None) -> tuple[list[str], bool]:
    """What the bridge would say about `repo`'s org file, or about a draft placed there,
    and whether any of it is an error rather than a warning about how things stand now."""
    _, folders = setting()
    others = org.load([f for f in folders if Path(f).expanduser().resolve() != repo])
    written = org.FILE
    try:
        if draft is not None:
            org.FILE = draft.resolve()  # an absolute FILE makes `repo / FILE` the draft itself
        chart = org.load([repo])
    finally:
        org.FILE = written
    if not chart.programmes and not chart.problems:
        return [f"{repo}: no org file found; write {written} there, or pass --draft <file>"], True
    live = events.read_live(Path.home() / ".claude" / "sessions")
    problems = org.check(chart, {str(d.get("name")) for d in live})
    clashes = [
        f"{p.code}: already the code of the programme in {taken.repo}; choose another code"
        for p in chart.programmes
        if (taken := others.programme(p.code)) is not None
    ]
    return problems + clashes, bool(chart.problems or clashes)


def exclude(repo: Path) -> str:
    """Add the org file to the repository's local ignore list, which is never committed."""
    found = _git(repo, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude")
    if not found:
        sys.exit(f"org_survey: {repo} is not a git repository; nothing to exclude it from")
    path = Path(found)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    if str(org.FILE) in lines or f"/{org.FILE}" in lines:
        return f"{path}: already excludes {org.FILE}"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(("\n" if lines and lines[-1] else "") + f"/{org.FILE}\n")
    return f"{path}: now excludes /{org.FILE}"


def entry() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("survey", help="print the sessions, programmes and candidate groups as JSON")
    c = sub.add_parser("check", help="load an org file the way the bridge does and print its problems")
    c.add_argument("repo", type=Path)
    c.add_argument("--draft", type=Path, help="check this file as if it were the repository's org file")
    e = sub.add_parser("exclude", help="keep the org file out of git through .git/info/exclude")
    e.add_argument("repo", type=Path)
    args = ap.parse_args()
    if args.command == "survey":
        print(json.dumps(survey(), indent=2, default=str))
        return 0
    if args.command == "check":
        problems, errors = check(args.repo.expanduser().resolve(), args.draft)
        print("\n".join(problems) or "no problems")
        return 1 if errors else 0
    print(exclude(args.repo.expanduser().resolve()))
    return 0


if __name__ == "__main__":
    sys.exit(entry())

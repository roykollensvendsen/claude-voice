#!/usr/bin/env python3
"""Approved pull requests, tested together once before they reach main (ADR-CV-023).

A batch is main with each cleared pull request's commits applied on top, in the order they
were cleared, on a branch named integration/<date>[-n]. Continuous integration runs the
quality job (the mutation run and the ratchet) once there, rather than on each pull request.

This module holds the judgement, with GitHub and git kept out, so each part can be tested on
its own:

- `why_not`: why a pull request may not go into a batch, or nothing when it may;
- `exceptions`: the Ratchet-Exception lines the batch body copies;
- `pinned`: the members' "Red-first: existing behaviour" lines the batch body copies;
- `culprit`: the pull request that breaks a batch, found by merge order;
- `merge_list`: the commands the manager runs once a batch is green, and the records each
  one rests on. This script never merges;
- `tree_check` and `after_failed_merge`: what to do once the merges ran;
- `status_targets`: the heads that get the `batch` status main requires.

The steps, each run by the batch owner except where it says otherwise:

    batch.py --repo O/R build --work DIR --body FILE [--owner-yes N:SHA ...]
        prints the push and the draft pull request that publish the batch
    batch.py --repo O/R bisect --pr N --work DIR --copy NEWDIR -- COMMAND ...
        runs COMMAND in the sandbox on halves of the batch, by merge order. The one step
        outside the sandbox is `uv sync --frozen`, which installs each prefix's locked
        dependencies and builds the project: a member that changes the build system runs
        its build there, with the network
    batch.py --repo O/R merge-list --pr N [--owner-yes N:SHA ...]
        prints the status and merge lines; the manager runs them
    batch.py --repo O/R verify --pr N [--clone DIR]
        after the last merge: main's tree, and main's own run there
    batch.py --repo O/R close --pr N --note FILE
        writes where each member landed, and prints the comment and close commands
    batch.py --repo O/R doctor
        read-only: main requires `batch`, and no longer `quality`

The verdict files and the owner's yes records are plain files on this machine. They are read
as the reviewers' and the manager's word, as every reviewer reads them today. They guard
against slips and habits, not against a session that means harm.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from claude_voice.tracker import verdicts

FULL_SHA = re.compile(r"[0-9a-f]{40}")
#: The reviewers a risky pull request needs, by the name each signs its verdicts with.
RISKY_NEEDS = ("quality-claude-voice", "voice-architect")
#: The shortest patch-id a stamp may record and still be compared.
SHORT = 8
BATCH = "integration/"


def full_sha(value: str) -> str:
    """`value`, when it is a full 40-character lowercase sha."""
    if not FULL_SHA.fullmatch(value):
        raise ValueError(f"not a full sha: {value!r}")
    return value


@dataclass
class Member:
    """One pull request as the batch sees it."""

    number: int
    head: str
    patch_id: str  # as applied onto the batch so far, in full
    draft: bool
    ci_green: bool
    stamps: list[dict[str, Any]] = field(default_factory=list)
    yes: dict[str, Any] | None = None
    body: str = ""


def _approving(m: Member) -> list[dict[str, Any]]:
    return [s for s in m.stamps if s.get("verdict") == "approve" and s.get("head") == m.head]


def _risky(m: Member) -> bool:
    # RULE: a pull request is low only when every stamp says low
    return not all(s.get("tier") == "low" for s in m.stamps)


def _when(at: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(at)).timestamp()
    except ValueError:
        return None


def _yes_holds(m: Member, built: str) -> bool:
    yes = m.yes or {}
    # RULE: a risky pull request needs the owners yes recorded before the build
    if yes.get("head") != m.head:
        return False
    if yes.get("source") == "manager-attested":
        return True
    at, start = _when(yes.get("at")), _when(built)
    return yes.get("source") == "checked" and at is not None and start is not None and at < start


def why_not(m: Member, built: str) -> list[str]:
    """Why `m` may not go into a batch built at `built`; empty when it is cleared."""
    # RULE: a draft is not cleared
    if m.draft:
        return ["it is a draft"]
    # RULE: a pull request whose own checks are not green is not cleared
    if not m.ci_green:
        return ["its own checks are not green at its head"]
    stamps = _approving(m)
    at_head = [s for s in m.stamps if s.get("head") == m.head]
    # RULE: a stamp at its head that does not approve leaves the pull request out
    against = [f"{s.get('by')} says {s.get('verdict')} at its head" for s in at_head if s.get("verdict") != "approve"]
    # RULE: a stamp at another head does not count
    if not stamps:
        return ["no approving stamp at its head", *against]
    found = list(against)
    for s in stamps:
        recorded = str(s.get("patch_id") or "")
        # RULE: a pull request whose patch moved is not cleared
        if len(recorded) < SHORT or not m.patch_id.startswith(recorded):
            found.append(f"{s.get('by')} stamped patch {recorded[:SHORT]}, and as applied it is {m.patch_id[:SHORT]}")
        # RULE: a stamp that does not record its exceptions leaves the pull request out
        if not isinstance(s.get("ratchet_exceptions"), list):
            found.append(f"{s.get('by')}'s stamp does not record its ratchet exceptions")
    found += unrecorded(m)
    if _risky(m):
        by = {str(s.get("by")) for s in stamps}
        # RULE: a risky pull request needs both stamps
        found += [f"risky, and {who} has not stamped its head" for who in RISKY_NEEDS if who not in by]
        if not _yes_holds(m, built):
            found.append("risky, and the owner's yes for its head is not recorded")
    return found


def checked_yes(rows: Iterable[dict[str, Any]], repo: str, number: int, head: str) -> dict[str, Any] | None:
    """The owner's yes for pull request `number` at `head`, from the decision checks' log.

    Only an accepted check counts, whose decision names this pull request and whose sha is
    `head` and was matched when it was checked.
    """
    names = {f"#{number}", f"{repo}#{number}"}
    for row in rows:
        decision = row.get("decision") or {}
        # RULE: the owners yes is taken only from an accepted check of this pull request at its head
        if row.get("result") != "accepted" or row.get("sha_matched") is not True:
            continue
        if str(decision.get("item")) in names and decision.get("sha") == head:
            return {"source": "checked", "head": head, "at": row.get("at"), "uuid": row.get("uuid")}
    return None


ATTESTED = re.compile(r"(?P<number>[1-9][0-9]*):(?P<sha>[0-9a-f]{40})")


def attested(flags: Iterable[str]) -> dict[int, dict[str, Any]]:
    """The owner's yes the manager attests to, from repeated --owner-yes N:sha flags."""
    out = {}
    for flag in flags:
        match = ATTESTED.fullmatch(flag)
        # RULE: an attested yes must be a number and a full sha
        if not match:
            raise ValueError(f"--owner-yes takes N:<40-character sha>, not {flag!r}")
        out[int(match["number"])] = {"source": "manager-attested", "head": match["sha"]}
    return out


def exceptions(members: Iterable[Member]) -> list[str]:
    """The Ratchet-Exception lines every approving stamp of a pull request recorded, tagged."""
    out = []
    for m in members:
        recorded = [s.get("ratchet_exceptions") for s in _approving(m)]
        if not recorded or not all(isinstance(r, list) for r in recorded):
            continue
        # RULE: only exceptions every stamp recorded are copied tagged with their pull request
        shared = set.intersection(*(set(map(str, r)) for r in recorded if isinstance(r, list)))
        out += [f"{line} (#{m.number})" for line in sorted(shared)]
    return out


#: A line saying a test only pins behaviour that was already there, as red_first.py reads it.
PINNED = re.compile(r"^[ \t]*(Red-first:[ \t]*existing behaviou?r[ \t]*[-–—].*?)[ \t]*$", re.MULTILINE)


#: The test an excuse line names, as red_first.py reads it.
EXCUSED = re.compile(r"existing behaviou?r[ \t]*[-–—][ \t]*([^\s,]+)")


def _excused(lines: Iterable[Any]) -> set[str]:
    return {m.group(1) for line in lines if (m := EXCUSED.search(str(line)))}


def _recorded(s: dict[str, Any]) -> set[str]:
    """The tests a stamp saw excused in the body at its head; none when it kept no record."""
    lines = s.get("red_first")
    return _excused(lines) if isinstance(lines, list) else set()


def unrecorded(m: Member) -> list[str]:
    """Each approving stamp that did not see every excuse the body holds now, and the tests it missed.

    The body is not covered by a stamp, so a line added after review would excuse its own test (#538).
    """
    live = _excused(PINNED.findall(m.body))
    found = []
    for s in _approving(m):
        # RULE: an excuse in the body that a stamp did not record leaves the pull request out
        missed = sorted(live - _recorded(s))
        if missed:
            found.append(f"{s.get('by')}'s stamp did not record the red-first excuse for {', '.join(missed)}")
    return found


def pinned(members: Iterable[Member]) -> list[str]:
    """Each member's lines excusing a test that passed before, tagged with its pull request.

    The batch runs red-first over all its members' tests at once, so it needs their excuses too.
    Only a line every approving stamp recorded is copied.
    """
    out: list[str] = []
    for m in members:
        stamps = _approving(m)
        seen_by_all = set.intersection(*(_recorded(s) for s in stamps)) if stamps else set()
        # RULE: only an excuse every stamp recorded is copied
        lines = [line for line in dict.fromkeys(PINNED.findall(m.body)) if _excused([line]) <= seen_by_all]
        # RULE: a members existing behaviour lines are copied tagged with its pull request
        out += [f"{line} (#{m.number})" for line in lines]
    return out


def branch_name(day: str, taken: set[str]) -> str:
    """integration/<day>, or with -2, -3 ... when that name was used before."""
    name, n = f"{BATCH}{day}", 1
    # RULE: the batch branch is named after the day and never reuses a name
    while name in taken:
        n += 1
        name = f"{BATCH}{day}-{n}"
    return name


def culprit(order: Sequence[int], fails: Callable[[Sequence[int]], bool]) -> int | None:
    """The pull request whose prefix first fails, found by halving; None when nothing is to blame.

    `fails(prefix)` runs the batch made of that prefix of `order`. The whole batch has already
    failed once, in its own run. Before it names anyone, the prefix that names the culprit is
    run once more and must fail, and the prefix just before it must pass. So one flaky run,
    whether a false fail or a false pass, never blames a pull request.
    """
    low, high = 0, len(order)  # the shortest failing prefix is longer than low, at most high
    while high - low > 1:
        mid = (low + high) // 2
        if fails(order[:mid]):
            high = mid
        else:
            low = mid
    # RULE: a failing prefix is run again before a culprit is named
    if not fails(order[:high]):
        return None
    # RULE: a culprit is named only when the prefix before it passes again
    if fails(order[: high - 1]):
        return None
    return order[high - 1]


def merge_list(members: Sequence[Member], repo: str) -> list[str]:
    """One merge command per pull request, in batch order, each with the records it rests on."""
    lines = []
    for m in members:
        # RULE: the merge list prints one rebase line per pull request in order with its records
        lines.append(f"gh pr merge {m.number} --repo {repo} --rebase --match-head-commit {full_sha(m.head)}")
        for s in _approving(m):
            lines.append(f"  stamp: {s.get('by')}, head {s.get('head')}, patch {s.get('patch_id')}, {s.get('file')}")
        if m.yes:
            source = m.yes.get("source")
            detail = f", {m.yes.get('uuid')}" if source == "checked" else ""
            lines.append(f"  owner's yes: {source}{detail}")
    return lines


def tree_check(main_tree: str, batch_tree: str) -> list[str]:
    """Nothing when main's tree is the one the batch proved; otherwise what to do."""
    # RULE: the tree check reports a difference and passes only on equal trees
    if main_tree != batch_tree:
        return [f"main's tree is {main_tree}, and the batch's is {batch_tree}: stop, and open an issue"]
    return []


def after_failed_merge(order: Sequence[int], failed_at: int) -> list[str]:
    """What to report when the merge of `failed_at` failed part-way through `order`."""
    # RULE: a merge that fails part way stops and names the prefix that landed
    landed = list(order[: order.index(failed_at)])
    return [
        f"merged: {', '.join(f'#{n}' for n in landed) or 'nothing'}",
        f"failed: #{failed_at}; nothing after it was merged",
        "main now holds a prefix that was never tested as such: wait for main's own run there, and tell the manager",
    ]


def read_stamps(reviews: Path, repo: str, number: int) -> list[dict[str, Any]]:
    """The verdict files for pull request `number` of `repo` (its bare name), each with its file name.

    A file counts only when it is plain JSON under the size limit, and its `by` is a reviewer
    who writes under that file's suffix. The rest of the judgement is `why_not`'s.
    """
    out = []
    for path in sorted(reviews.glob(f"{repo}-{number}.*.json")):
        match = verdicts.NAME.match(path.name)
        if not match or match["repo"] != repo or int(match["number"]) != number:
            continue
        body = verdicts._body(path)
        # RULE: only a stamp from a known reviewer under its own file name counts
        if body is None or match["suffix"] not in verdicts.REVIEWERS.get(str(body.get("by")), ()):
            continue
        if body.get("pr") not in (None, number, str(number)):
            continue
        out.append({**body, "file": path.name})
    return out


GREEN = {"SUCCESS", "SKIPPED", "NEUTRAL"}


def _age(c: dict[str, Any]) -> tuple[bool, str]:
    """Oldest first; a run with no start time yet (one still queued) counts as the newest."""
    return c.get("startedAt") is None, str(c.get("startedAt") or "")


def _finished(c: dict[str, Any]) -> bool:
    if c.get("__typename") == "StatusContext":
        return c.get("state") not in (None, "PENDING", "EXPECTED")
    return c.get("status") == "COMPLETED"


def ci_green(rollup: Sequence[dict[str, Any]]) -> bool:
    """Whether every check GitHub reports for a head finished, and none failed.

    GitHub lists every run at the head, reruns too. A run still going or queued counts as not
    green; otherwise the latest run of each check decides. The `batch` status is left out: it
    is what a batch sets, so a member waits on it until its batch is green.
    """
    # RULE: a pull requests own checks leave out the batch status
    rollup = [c for c in rollup if not (c.get("__typename") == "StatusContext" and c.get("context") == "batch")]
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for c in sorted(rollup, key=_age):
        latest[str(c.get("workflowName") or ""), str(c.get("name") or c.get("context") or "")] = c
    states = [
        c.get("state") if c.get("__typename") == "StatusContext" else c.get("conclusion") for c in latest.values()
    ]
    # RULE: own checks are green only when every check finished and none failed
    return bool(states) and all(_finished(c) for c in rollup) and all(state in GREEN for state in states)


def cleared_order(members: Iterable[Member]) -> list[Member]:
    """`members` in the order they were cleared: by their last approving stamp at their head."""

    def cleared(m: Member) -> tuple[float, int]:
        times = [_when(s.get("at")) for s in _approving(m)]
        return max((t for t in times if t is not None), default=float("inf")), m.number

    # RULE: the batch keeps the order the pull requests were cleared in
    return sorted(members, key=cleared)


@dataclass(frozen=True)
class Entry:
    """One member as the batch body records it, with the batch's commit after it."""

    number: int
    head: str
    patch_id: str
    tier: str
    tip: str


@dataclass(frozen=True)
class Recorded:
    branch: str
    base: str
    members: list[Entry]


RECORD = re.compile(r"<!-- batch-record\n(?P<lines>.*?)\n-->", re.S)


def render_body(
    record: Recorded, copied: Sequence[str], left_out: dict[int, list[str]], pins: Sequence[str] = ()
) -> str:
    """The batch pull request's body: for people at the top, and the record the script reads back."""
    lines = [
        "Do not merge. This is a batch: main with each cleared pull request applied in order (ADR-CV-023).",
        "",
        f"Base: `{record.base}`",
        "",
        "| Pull request | Head | Patch-id | Tier |",
        "|---|---|---|---|",
        *(f"| #{e.number} | `{e.head}` | `{e.patch_id}` | {e.tier} |" for e in record.members),
        "",
        "Ratchet exceptions the stamps recorded, each at the start of its line, where the ratchet reads it:",
        *(list(copied) or ["none"]),
        "",
        "Tests the members say only pin behaviour already there, each at the start of its line,"
        " where red-first reads it:",
        *(list(pins) or ["none"]),
        "",
        "Left out:",
        *([f"- #{n}: {'; '.join(why)}" for n, why in sorted(left_out.items())] or ["- none"]),
        "",
        "<!-- batch-record",
        f"branch {record.branch}",
        f"base {record.base}",
        *(f"member {e.number} {e.head} {e.patch_id} {e.tier} {e.tip}" for e in record.members),
        "-->",
    ]
    return "\n".join(lines) + "\n"


def parse_body(body: str) -> Recorded | None:
    """The record `render_body` wrote, or None when the body holds none, or a broken one."""
    found = RECORD.search(body)
    if found is None:
        return None
    fields = [line.split() for line in found["lines"].splitlines()]
    named = {f[0]: f[1] for f in fields if len(f) == 2}
    try:
        members = [Entry(int(f[1]), f[2], f[3], f[4], f[5]) for f in fields if f[0] == "member" and len(f) == 6]
        return Recorded(named["branch"], named["base"], members)
    except (KeyError, ValueError):
        return None


def merge_refusals(base: str, main_now: str, reasons: dict[int, list[str]]) -> list[str]:
    """Why the merge list may not be printed: main moved, or a member is no longer cleared."""
    out = []
    # RULE: the merge list refuses when main moved off the base or a member is no longer cleared
    if main_now != base:
        out.append(f"main is at {main_now}, not the batch's base {base}: build the batch again")
    out += [f"#{n} is no longer cleared: {'; '.join(why)}; nothing is merged" for n, why in reasons.items() if why]
    return out


def status_lines(repo: str, heads: Sequence[str], branch: str, run_url: str) -> list[str]:
    """The commands that set the `batch` status on each member head, for the manager to run."""
    return [
        f"gh api --method POST repos/{repo}/statuses/{full_sha(head)} -f state=success -f context=batch"
        f" -f description={branch} -f target_url={run_url}"
        for head in heads
    ]


def main_run(since: Sequence[str], runs: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """The newest complete main run at the batch's tree or after it; None while there is none.

    `since` holds main's commits from its tip back to the first one with the batch's tree.
    """
    ours = set(since)
    for r in runs:
        # RULE: verify waits for a complete main run at the batch tree or later
        if r.get("headSha") in ours and r.get("status") == "completed" and r.get("conclusion") != "cancelled":
            return dict(r)
    return None


def protection_drift(contexts: Iterable[str]) -> list[str]:
    """What is wrong with main's required checks, read-only: `batch` must be there, `quality` not."""
    have = set(contexts)
    out = []
    # RULE: doctor reports when main does not require batch or still requires quality
    if "batch" not in have:
        out.append("main's protection does not require batch")
    if "quality" in have:
        out.append("main's protection still requires quality")
    return out


def status_targets(recorded: dict[int, str], quality_head: str, batch_head: str, green: bool) -> list[str]:
    """The heads that get the `batch` status: every recorded member head, and only after a
    green quality run at the batch head as it is now."""
    # RULE: the batch status goes only on recorded member heads after a green quality at the batch head
    if not green or quality_head != batch_head:
        return []
    return [full_sha(head) for _, head in sorted(recorded.items())]


# The command. GitHub comes in as `gh`, a callable that runs `gh` with its arguments and returns
# what it printed, so the tests can stand in for it. Git runs for real, in folders the caller names.

Gh = Callable[[Sequence[str]], str]
STATE = Path("~/.local/state/claude-voice").expanduser()
SANDBOX = Path(__file__).resolve().parent.parent / ".claude/skills/review-contribution/sandboxed.sh"
CHECKS_LOG = "decision-checks.jsonl"
FIELDS = "number,headRefOid,headRefName,isDraft,statusCheckRollup,body"


class Stop(Exception):
    """A step that cannot go on; its message says why, and nothing was changed on GitHub."""


def git(where: Path, *args: str, stdin: str | None = None) -> str:
    # A repository's own hooks never run here: the batch applies other sessions' commits.
    command = ["git", "-C", str(where), "-c", "core.hooksPath=/dev/null", *args]
    return subprocess.run(command, input=stdin, text=True, capture_output=True, check=True).stdout.strip()


def real_gh(args: Sequence[str]) -> str:
    return subprocess.run(["gh", *args], text=True, capture_output=True, check=True).stdout


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _member(
    pr: dict[str, Any], repo: str, reviews: Path, rows: list[dict[str, Any]], vouched: dict[int, Any]
) -> Member:
    number, head = int(pr["number"]), str(pr["headRefOid"])
    name = repo.rpartition("/")[2]
    yes = checked_yes(rows, name, number, head) or vouched.get(number)
    stamps = read_stamps(reviews, name, number)
    green = ci_green(pr.get("statusCheckRollup") or [])
    return Member(number, head, "", bool(pr["isDraft"]), green, stamps, yes, str(pr.get("body") or ""))


def _apply(work: Path, tip: str, number: int) -> str | None:
    """The batch's new tip with pull request `number` rebased onto `tip`, or None on a conflict."""
    git(work, "fetch", "--quiet", "origin", f"pull/{number}/head:refs/batch/{number}")
    since = git(work, "merge-base", "origin/main", f"refs/batch/{number}")
    git(work, "checkout", "--quiet", "--detach", f"refs/batch/{number}")
    try:
        git(work, "rebase", "--quiet", "--onto", tip, since)
    except subprocess.CalledProcessError:
        git(work, "rebase", "--abort")
        git(work, "checkout", "--quiet", "--detach", tip)
        return None
    return git(work, "rev-parse", "HEAD")


@dataclass(frozen=True)
class Where:
    """The folders a step reads and writes."""

    reviews: Path
    state: Path


def build(
    gh: Gh, repo: str, url: str, work: Path, body_file: Path, where: Where, owner_yes: Sequence[str], day: str
) -> list[str]:
    """Build the batch in `work`, a new clone of `url`, and return the two commands that publish it."""
    vouched = attested(owner_yes)
    built = datetime.now().astimezone().isoformat()
    rows = read_jsonl(where.state / CHECKS_LOG)
    prs = json.loads(gh(["pr", "list", "--repo", repo, "--state", "open", "--limit", "100", "--json", FIELDS]))
    candidates = [
        _member(pr, repo, where.reviews, rows, vouched) for pr in prs if not str(pr["headRefName"]).startswith(BATCH)
    ]
    # A pull request whose stamps are all at an older head is not a candidate, so it is not listed.
    candidates = [m for m in candidates if _approving(m)]
    subprocess.run(["git", "clone", "--quiet", url, str(work)], check=True)
    base = tip = git(work, "rev-parse", "origin/main")
    git(work, "checkout", "--quiet", "--detach", base)
    entries, chosen, left_out = [], [], {}
    for m in cleared_order(candidates):
        if m.draft or not m.ci_green:
            left_out[m.number] = why_not(m, built)
            continue
        after = _apply(work, tip, m.number)
        if after is None:
            left_out[m.number] = ["it does not apply cleanly on the batch so far"]
            continue
        m.patch_id = git(work, "patch-id", "--stable", stdin=git(work, "diff", tip, after) + "\n").partition(" ")[0]
        why = why_not(m, built)
        if why:
            left_out[m.number] = why
            git(work, "checkout", "--quiet", "--detach", tip)
            continue
        entries.append(Entry(m.number, m.head, m.patch_id, "risky" if _risky(m) else "low", after))
        chosen.append(m)
        tip = after
    if not entries:
        raise Stop("nothing is cleared: " + "; ".join(f"#{n}: {', '.join(w)}" for n, w in sorted(left_out.items())))
    heads = git(work, "ls-remote", "--heads", "origin", f"{BATCH}*")
    branch = branch_name(day, {line.split("refs/heads/")[-1] for line in heads.splitlines()})
    git(work, "branch", branch, tip)
    record = Recorded(branch, base, entries)
    body_file.write_text(render_body(record, exceptions(chosen), left_out, pinned(chosen)), encoding="utf-8")
    return [
        f"git -C {work} push origin {branch}",
        f"gh pr create --repo {repo} --draft --base main --head {branch}"
        f" --title 'batch: {branch}' --body-file {body_file}",
    ]


def _record(gh: Gh, repo: str, number: int) -> tuple[Recorded, dict[str, Any]]:
    pr = json.loads(gh(["pr", "view", str(number), "--repo", repo, "--json", "body,headRefOid,statusCheckRollup"]))
    record = parse_body(str(pr.get("body") or ""))
    if record is None:
        raise Stop(f"#{number} holds no batch record")
    return record, pr


def bisect(
    gh: Gh, repo: str, number: int, work: Path, copy: Path, command: Sequence[str], run: Callable[..., int]
) -> str:
    """Find the member that breaks the batch, running `command` in the sandbox on each prefix."""
    record, _ = _record(gh, repo, number)
    tips = {e.number: e.tip for e in record.members}
    subprocess.run(["git", "clone", "--quiet", "--no-checkout", str(work), str(copy)], check=True)

    def fails(prefix: Sequence[int]) -> bool:
        commit = tips[prefix[-1]] if prefix else record.base
        git(copy, "checkout", "--quiet", "--detach", commit)
        run(["uv", "sync", "--frozen", "--quiet"], cwd=copy)
        failed = run([str(SANDBOX), str(copy), *command], cwd=copy) != 0
        print(f"{' '.join(f'#{n}' for n in prefix) or 'base'}: {'fails' if failed else 'passes'}")
        return failed

    found = culprit([e.number for e in record.members], fails)
    return f"culprit: #{found}" if found else "no culprit: the failure did not come back"


def _quality(pr: dict[str, Any]) -> dict[str, Any] | None:
    """The latest quality run in a pull request's checks, or None when there is none."""
    runs = sorted((c for c in pr.get("statusCheckRollup") or [] if c.get("name") == "quality"), key=_age)
    return runs[-1] if runs else None


def merge_commands(gh: Gh, repo: str, number: int, where: Where, owner_yes: Sequence[str]) -> list[str]:
    """The status and merge lines for a green batch, after checking every member again."""
    record, batch_pr = _record(gh, repo, number)
    vouched = attested(owner_yes)
    rows = read_jsonl(where.state / CHECKS_LOG)
    built = datetime.now().astimezone().isoformat()
    members, reasons = [], {}
    for e in record.members:
        pr = json.loads(gh(["pr", "view", str(e.number), "--repo", repo, "--json", FIELDS]))
        m = _member(pr, repo, where.reviews, rows, vouched)
        m.patch_id = e.patch_id
        reasons[e.number] = ["it has a new commit since the batch was built"] if m.head != e.head else why_not(m, built)
        members.append(m)
    main_now = json.loads(gh(["api", f"repos/{repo}/commits/main"]))["sha"]
    refusals = merge_refusals(record.base, main_now, reasons)
    quality = _quality(batch_pr) or {}
    green = quality.get("status") == "COMPLETED" and quality.get("conclusion") == "SUCCESS"
    last = record.members[-1].tip
    heads = status_targets({e.number: e.head for e in record.members}, batch_pr["headRefOid"], last, green)
    if not heads:
        refusals.append(f"the batch's quality run is not green at its recorded head {last}")
    # RULE: the merge list refuses a batch whose own checks are not all green
    if not ci_green(batch_pr.get("statusCheckRollup") or []):
        refusals.append("the batch's own checks are not green at its head")
    if refusals:
        raise Stop("\n".join(refusals))
    return status_lines(repo, heads, record.branch, str(quality.get("detailsUrl"))) + merge_list(members, repo)


def verify(gh: Gh, repo: str, number: int, clone: Path) -> list[str]:
    """Whether main now holds the batch's tree, and how main's own run there went."""
    record, _ = _record(gh, repo, number)
    states = {
        e.number: json.loads(gh(["pr", "view", str(e.number), "--repo", repo, "--json", "state"]))["state"]
        for e in record.members
    }
    order = [e.number for e in record.members]
    unmerged = [n for n in order if states[n] != "MERGED"]
    if unmerged:
        raise Stop("\n".join(after_failed_merge(order, unmerged[0])))
    git(clone, "fetch", "--quiet", "origin", "main", record.branch)
    want = git(clone, "rev-parse", f"{record.members[-1].tip}^{{tree}}")
    since = []
    for line in git(clone, "log", "--first-parent", "--format=%H %T", f"{record.base}..origin/main").splitlines():
        sha, tree = line.split()
        since.append(sha)
        if tree == want:
            break
    else:
        raise Stop("\n".join(tree_check(git(clone, "rev-parse", "origin/main^{tree}"), want)))
    runs = json.loads(
        gh(
            [
                "run",
                "list",
                "--repo",
                repo,
                "--branch",
                "main",
                "--event",
                "push",
                "--workflow",
                "checks.yml",
                "--limit",
                "30",
                "--json",
                "headSha,status,conclusion,url",
            ]
        )
    )
    found = main_run(since, runs)
    if found is None:
        raise Stop(f"main holds the batch's tree {want}; waiting for a complete main run there")
    lines = [f"main holds the batch's tree {want}", f"main's run: {found['conclusion']}, {found['url']}"]
    if found["conclusion"] != "success":
        raise Stop("\n".join(lines))
    return lines


def close(gh: Gh, repo: str, number: int, note: Path) -> list[str]:
    """Write where each member landed, and return the commands that post it and close the batch.

    The batch's branch is left for the manager to delete.
    """
    record, _ = _record(gh, repo, number)
    lines = [f"Closed: this batch's pull requests now stand on main as follows ({record.branch}).", ""]
    for e in record.members:
        pr = json.loads(gh(["pr", "view", str(e.number), "--repo", repo, "--json", "state,mergeCommit"]))
        landed = (pr.get("mergeCommit") or {}).get("oid")
        merged = pr.get("state") == "MERGED" and landed
        lines.append(
            f"- #{e.number} landed as {landed}" if merged else f"- #{e.number} is {pr.get('state')}, not merged"
        )
    note.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return [f"gh pr comment {number} --repo {repo} --body-file {note}", f"gh pr close {number} --repo {repo}"]


def doctor(gh: Gh, repo: str) -> list[str]:
    checks = json.loads(gh(["api", f"repos/{repo}/branches/main/protection/required_status_checks"]))
    return protection_drift(checks.get("contexts") or [])


def main(argv: Sequence[str] | None = None, gh: Gh = real_gh) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="owner/name on GitHub")
    parser.add_argument("--reviews", type=Path, default=STATE / "reviews")
    parser.add_argument("--state", type=Path, default=STATE)
    steps = parser.add_subparsers(dest="step", required=True)
    one = steps.add_parser("build", help="apply the cleared pull requests onto main in a new clone")
    one.add_argument("--work", type=Path, required=True, help="a new folder for the batch's clone")
    one.add_argument("--body", type=Path, required=True, help="where to write the batch's body")
    one.add_argument("--owner-yes", action="append", default=[], metavar="N:SHA")
    one.add_argument("--origin", help="where to clone from; GitHub's address for --repo when left out")
    for name in ("bisect", "merge-list", "verify", "close"):
        step = steps.add_parser(name)
        step.add_argument("--pr", type=int, required=True, help="the batch's pull request")
    steps.choices["bisect"].add_argument("--work", type=Path, required=True, help="the batch's clone")
    steps.choices["bisect"].add_argument("--copy", type=Path, required=True, help="a new folder for the runs")
    steps.choices["bisect"].add_argument("command", nargs="+")
    steps.choices["merge-list"].add_argument("--owner-yes", action="append", default=[], metavar="N:SHA")
    steps.choices["verify"].add_argument("--clone", type=Path, default=Path("."))
    steps.choices["close"].add_argument("--note", type=Path, required=True, help="where to write the comment")
    steps.add_parser("doctor", help="check main's required checks, read-only")
    args = parser.parse_args(argv)
    where = Where(args.reviews, args.state)
    try:
        if args.step == "build":
            day = datetime.now().astimezone().date().isoformat()
            url = args.origin or json.loads(gh(["repo", "view", args.repo, "--json", "sshUrl"]))["sshUrl"]
            lines = build(gh, args.repo, url, args.work, args.body, where, args.owner_yes, day)
        elif args.step == "bisect":
            lines = [bisect(gh, args.repo, args.pr, args.work, args.copy, args.command, _run)]
        elif args.step == "merge-list":
            lines = merge_commands(gh, args.repo, args.pr, where, args.owner_yes)
        elif args.step == "verify":
            lines = verify(gh, args.repo, args.pr, args.clone)
        elif args.step == "close":
            lines = close(gh, args.repo, args.pr, args.note)
        else:
            lines = doctor(gh, args.repo)
            print("\n".join(lines or ["main's protection requires batch and not quality"]))
            return 1 if lines else 0
    except (Stop, ValueError) as stop:
        print(stop, file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


def _run(command: Sequence[str], cwd: Path) -> int:
    return subprocess.run(list(command), cwd=cwd, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())

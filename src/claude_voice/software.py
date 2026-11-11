"""How the software is doing: each repository's quality measures, their trend, and its checks on main.

Read from the levels main keeps with each run of its checks on GitHub (ADR-CV-009), and,
for the time before that or a repository that still records them, from each clone's
``quality.json`` and its git history. The time budget is read from ``quality.json``. So the
trend is what was measured change by change. Nothing is estimated: a repository with
nothing recorded says so.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import tomllib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import procs

TREND = 30  # points of history per measure
FILE = "quality.json"
EXEMPTIONS = "quality.toml"  # the measures the owner agreed to leave outside their limits
ARTIFACT = "quality-levels"  # the name main's checks keep their measured levels under
NEW_PER_LOOK = 5  # downloads of main's levels per look; the rest wait for the next one


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _git(repo: Path, *args: str) -> str | None:
    git = procs.find("git") or "git"
    try:
        out = subprocess.run([git, "-C", str(repo), *args], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _gh(args: list[str]) -> str | None:
    """GitHub's command line, or None when it is missing or the call fails."""
    gh = procs.find("gh")
    if not gh:
        return None
    try:
        out = subprocess.run([gh, *args], capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _cache() -> Path:
    return Path.home() / ".cache" / "claude-voice" / "levels"


def _run_ids(listed: str | None) -> list[int]:
    try:
        return [int(r["databaseId"]) for r in json.loads(listed or "[]")]
    except (ValueError, KeyError, TypeError):
        return []


def _download(repo: str, run_id: int, saved: Path, run: Callable[[list[str]], str | None]) -> None:
    """Keep one run's levels on disk, or `null` for a run that kept none."""
    with tempfile.TemporaryDirectory() as tmp:
        got = run(["run", "download", str(run_id), "--repo", repo, "--name", ARTIFACT, "--dir", tmp])
        levels = Path(tmp) / "levels.json"
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_text(levels.read_text() if got is not None and levels.exists() else "null")


def _read_saved(saved: Path) -> dict[str, Any] | None:
    try:
        levels = json.loads(saved.read_text())
    except (OSError, ValueError):
        return None
    return levels if isinstance(levels, dict) else None


def kept_levels(
    repo: str,
    cache: Path | None = None,
    limit: int = TREND,
    run: Callable[[list[str]], str | None] = _gh,
    branch: str = "main",
) -> list[dict[str, Any]]:
    """The levels the followed branch kept with its latest runs, oldest first.

    Only finished runs: a finished run's levels never change, so each is downloaded once
    and then read from disk, and one that kept none is remembered as having none. The newest
    are downloaded first, so a page shows today's numbers before it fills in their history.
    """
    folder = (cache or _cache()) / repo.replace("/", "__")
    listed = run(
        ["run", "list", "--repo", repo, "--branch", branch, "--event", "push", "--workflow", "checks"]
        + ["--status", "completed", "--limit", str(limit), "--json", "databaseId"]
    )
    out, fetched = [], 0
    for run_id in _run_ids(listed):  # newest first
        saved = folder / f"{run_id}.json"
        if not saved.exists() and fetched < NEW_PER_LOOK:
            fetched += 1
            _download(repo, run_id, saved, run)
        levels = _read_saved(saved)
        if levels is not None:
            out.append(levels)
    return out[::-1]


def history(repo: Path, limit: int = TREND) -> list[dict[str, Any]]:
    """The recorded measures, oldest first: the newest `limit` versions of quality.json in git."""
    log = _git(repo, "log", f"-n{limit}", "--format=%H", "--", FILE) or ""
    out = []
    for sha in reversed(log.split()):
        shown = _git(repo, "show", f"{sha}:{FILE}")
        try:
            out.append(json.loads(shown or ""))
        except ValueError:
            continue
    return out


def _mutation(q: dict[str, Any]) -> float:
    m = q["mutation"]
    return round(100.0 * m["killed"] / max(m["total"], 1), 1)


def _worst(q: dict[str, Any]) -> tuple[str, int]:
    module, score = max(q["complexity"].items(), key=lambda kv: kv[1], default=("", 0))
    return module, score


def _status(value: float, ok: float, warn: float, higher: bool = True) -> str:
    if higher:
        return "ok" if value >= ok else "warn" if value >= warn else "bad"
    return "ok" if value <= ok else "warn" if value <= warn else "bad"


# id, label, unit, how to read the value, how to word it, how to judge it
# RULE: a repository with no time budget still shows how long its tests take
def _time_text(q: dict[str, Any], v: float) -> str:
    budget = q.get("test_budget")
    return f"{v:.0f} s of a {budget['seconds']} s budget" if budget else f"{v:.0f} s"


def _time_status(q: dict[str, Any], v: float) -> str:
    budget = q.get("test_budget")
    if not budget:
        return "unknown"
    return _status(v, budget["seconds"], budget["seconds"] * (1 + budget["tolerance"]), False)


MEASURES: tuple[tuple[str, str, str, Callable[..., Any], Callable[..., str], Callable[..., str]], ...] = (
    (
        "coverage",
        "Test coverage",
        "%",
        lambda q: q["coverage"],
        lambda q, v: f"{v:.1f} % of the code runs in the tests",
        lambda q, v: _status(v, 80, 60),
    ),
    (
        "mutation",
        "Rules proven by a test",
        "%",
        _mutation,
        lambda q, v: f"{q['mutation']['killed']} of {q['mutation']['total']} rules",
        lambda q, v: _status(v, 100, 90),
    ),
    (
        "tests",
        "Tests",
        "",
        lambda q: q["tests"],
        lambda q, v: f"{v} tests pass",
        lambda q, v: "ok" if v > 0 else "bad",
    ),
    (
        "problems",
        "Lint and type errors",
        "",
        lambda q: q["lint_errors"] + q["type_errors"],
        lambda q, v: "no errors" if v == 0 else f"{v} errors",
        lambda q, v: "ok" if v == 0 else "bad",
    ),
    (
        "cycles",
        "Import cycles",
        "",
        lambda q: q["cycles"],
        lambda q, v: "none" if v == 0 else f"{v} groups of modules import each other",
        lambda q, v: "ok" if v == 0 else "warn",
    ),
    (
        "layers",
        "Modules in their layers",
        "",
        lambda q: q["layer_violations"],
        lambda q, v: "every module keeps to its layer" if v == 0 else f"{v} imports reach a higher layer",
        lambda q, v: "ok" if v == 0 else "bad",
    ),
    (
        "duplication",
        "Copied code",
        "%",
        lambda q: q["duplication"],
        lambda q, v: f"{v:.1f} % of the lines sit in repeated blocks",
        lambda q, v: _status(v, 3, 6, higher=False),
    ),
    (
        "complexity",
        "Most tangled function",
        "",
        lambda q: _worst(q)[1],
        lambda q, v: f"{v}, in {Path(_worst(q)[0]).name}",
        lambda q, v: _status(v, 15, 25, higher=False),
    ),
    (
        "test_time",
        "Time the tests take",
        "s",
        lambda q: q["test_seconds"],
        _time_text,
        _time_status,
    ),
)


def _read(q: dict[str, Any], read: Callable[..., Any]) -> Any:
    try:
        return read(q)
    except (KeyError, TypeError, ZeroDivisionError):
        return None


def ci_metric(run: dict[str, Any] | None) -> dict[str, Any]:
    """The latest run of the checks on main, as a measure."""
    if not run:
        return {
            "id": "ci",
            "label": "Checks on main",
            "value": None,
            "unit": "",
            "text": "not known",
            "status": "unknown",
            "url": None,
            "trend": [],
        }
    conclusion, status = run.get("conclusion") or "", run.get("status") or ""
    if status != "completed":
        text, judged = "running", "unknown"
    elif conclusion == "success":
        text, judged = "passing", "ok"
    else:
        text, judged = conclusion or "failed", "bad"
    return {
        "id": "ci",
        "label": "Checks on main",
        "value": conclusion or status,
        "unit": "",
        "text": text,
        "status": judged,
        "url": run.get("url"),
        "trend": [],
    }


def _measured(folder: Path | None, kept: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Every measured version, oldest first: the file's history, then main's kept levels,
    each carrying the time budget the file sets."""
    try:
        recorded = json.loads((folder / FILE).read_text()) if folder else None
    except (OSError, ValueError):
        recorded = None
    budget = (recorded or {}).get("test_budget")
    past = [q for q in history(folder) if "coverage" in q] if folder and recorded is not None else []
    return past + [{**q, "test_budget": budget} for q in kept or []]


def exemptions(folder: Path | None) -> dict[str, str]:
    """The reason for each measure the owner agreed to leave outside its limit, by its id."""
    try:
        entries = tomllib.loads((folder / EXEMPTIONS).read_text()).get("exempt", []) if folder else []
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    out = {}
    for entry in entries if isinstance(entries, list) else []:
        reason, approved = str(entry.get("reason", "")).strip(), str(entry.get("approved", "")).strip()
        # RULE: an exemption counts only with a reason and the owners yes
        if reason and approved:
            out[str(entry.get("measure", ""))] = reason
    return out


def _exempt(row: dict[str, Any], reason: str | None) -> dict[str, Any]:
    if reason is None or row["status"] in ("ok", "unknown"):
        return row
    # RULE: an exempt measure is shown as exempt and never as ok
    return {**row, "status": "exempt", "text": f"{row['text']}; exempt: {reason}"}


def _metric(measure: tuple[Any, ...], past: list[dict[str, Any]], link: str) -> dict[str, Any]:
    """One measure's row: its value now, how it reads, its status and its trend."""
    id_, label, unit, read, word, judge = measure
    current = past[-1] if past else None
    value = _read(current, read) if current is not None else None
    row = {"id": id_, "label": label, "unit": unit, "url": link}
    if current is None or value is None:
        return {**row, "value": None, "text": "Nothing measured yet", "status": "unknown", "trend": []}
    trend = [v for v in (_read(q, read) for q in past) if v is not None][-TREND:]
    return {**row, "value": value, "text": word(current, value), "status": judge(current, value), "trend": trend}


def repo_report(
    name: str,
    url: str,
    folder: Path | None,
    ci: dict[str, Any] | None = None,
    kept: list[dict[str, Any]] | None = None,
    branch: str = "main",
) -> dict[str, Any]:
    """One repository's measures, each with its value, status and trend."""
    past = _measured(folder, kept)
    link = f"{url}/actions/workflows/checks.yml?query=branch%3A{branch}" if kept else f"{url}/blob/{branch}/{FILE}"
    exempt = exemptions(folder)
    metrics = [_exempt(_metric(measure, past, link), exempt.get(measure[0])) for measure in MEASURES]
    metrics.append(ci_metric(ci))
    return {"name": name, "url": url, "metrics": metrics, "as_of": _now()}


def latest_run(repo: str, branch: str = "main") -> dict[str, Any] | None:
    """The latest run of the checks on the followed branch, from GitHub's command line, or None."""
    gh = procs.find("gh")
    if not gh:
        return None
    try:
        out = subprocess.run(
            [
                gh,
                "run",
                "list",
                "--repo",
                repo,
                "--branch",
                branch,
                "--workflow",
                "checks",
                "--limit",
                "1",
                "--json",
                "conclusion,status,url",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        runs = json.loads(out.stdout or "[]") if out.returncode == 0 else []
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    return runs[0] if runs else None

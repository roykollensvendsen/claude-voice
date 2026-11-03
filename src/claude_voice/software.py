"""How the software is doing: each repository's quality measures, their trend, and its checks on main.

Read from each clone's ``quality.json`` (the quality ratchet's record) and its git history,
so the trend is what was recorded change by change, and from the latest run of the checks
on GitHub. Nothing is estimated: a repository with nothing recorded says so.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import procs

TREND = 30  # points of history per measure
FILE = "quality.json"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _git(repo: Path, *args: str) -> str | None:
    git = procs.find("git") or "git"
    try:
        out = subprocess.run([git, "-C", str(repo), *args], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


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
        lambda q, v: f"{v:.0f} s of a {q['test_budget']['seconds']} s budget",
        lambda q, v: _status(
            v, q["test_budget"]["seconds"], q["test_budget"]["seconds"] * (1 + q["test_budget"]["tolerance"]), False
        ),
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


def repo_report(name: str, url: str, folder: Path | None, ci: dict[str, Any] | None = None) -> dict[str, Any]:
    """One repository's measures, each with its value, status and trend."""
    try:
        current = json.loads((folder / FILE).read_text()) if folder else None
    except (OSError, ValueError):
        current = None
    past = history(folder) if folder and current is not None else []
    link = f"{url}/blob/main/{FILE}"
    metrics: list[dict[str, Any]] = []
    for id_, label, unit, read, word, judge in MEASURES:
        value = _read(current, read) if current is not None else None
        if value is None:
            metrics.append(
                {
                    "id": id_,
                    "label": label,
                    "value": None,
                    "unit": unit,
                    "text": f"No {FILE} yet",
                    "status": "unknown",
                    "url": link,
                    "trend": [],
                }
            )
            continue
        trend = [v for v in (_read(q, read) for q in past) if v is not None][-TREND:]
        metrics.append(
            {
                "id": id_,
                "label": label,
                "value": value,
                "unit": unit,
                "text": word(current, value),
                "status": judge(current, value),
                "url": link,
                "trend": trend,
            }
        )
    metrics.append(ci_metric(ci))
    return {"name": name, "url": url, "metrics": metrics, "as_of": _now()}


def latest_run(repo: str) -> dict[str, Any] | None:
    """The latest run of the checks on main, from GitHub's command line, or None."""
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
                "main",
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

"""The org chart: which programme, component and role each session belongs to.

A programme is a larger piece of work, made of components, with a few roles such as a
manager. Each programme describes itself in one optional file, ``.claude/org.toml`` in
its main repository (docs/org-chart.md is the format). A session is placed from what the
bridge already knows: its folder gives the programme and component, its name the role.
Nobody tags a session, and nothing here changes one: the chart is only read.
"""

from __future__ import annotations

import functools
import re
import subprocess
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Any

from . import config, renames

FILE = Path(".claude/org.toml")
CODE = re.compile(r"[a-z][a-z0-9-]{0,31}")
KINDS = ("facts", "proposals", "decisions", "quality")  # what may be sent to a role
LAST = 1_000_000  # the place of a programme that gives no order

Remotes = Callable[[str], Iterable[str]]


class OrgError(ValueError):
    pass


@dataclass(frozen=True)
class Component:
    code: str
    name: str
    aliases: tuple[str, ...] = ()
    folders: tuple[Path, ...] = ()
    remotes: tuple[str, ...] = ()
    written: tuple[str, ...] = ()  # the folders as the file gives them, for warnings

    def words(self) -> set[str]:
        return _words(self.code, self.name, self.aliases)


@dataclass(frozen=True)
class Role:
    code: str
    name: str
    purpose: str = ""
    limits: str = ""
    sessions: tuple[str, ...] = ()
    component: str | None = None
    send_here: tuple[str, ...] = ()

    def words(self) -> set[str]:
        return _words(self.code, self.name, ())


@dataclass(frozen=True)
class Programme:
    code: str
    name: str
    aliases: tuple[str, ...] = ()
    order: int = LAST
    repo: Path = Path()
    components: tuple[Component, ...] = ()
    roles: tuple[Role, ...] = ()

    def words(self) -> set[str]:
        return _words(self.code, self.name, self.aliases)

    def component(self, code: str | None) -> Component | None:
        return next((c for c in self.components if c.code == code), None)


def _words(code: str, name: str, aliases: Iterable[str]) -> set[str]:
    return {w.strip().lower() for w in (code, name, *aliases) if w.strip()}


def _id(thing: Programme | Component | Role | None) -> dict[str, str] | None:
    return {"id": thing.code, "name": thing.name} if thing is not None else None


@dataclass(frozen=True)
class Place:
    """Where one session sits in the chart; all empty for a session in no programme."""

    programme: Programme | None = None
    component: Component | None = None
    role: Role | None = None

    def parts(self) -> list[Programme | Component | Role]:
        return [p for p in (self.programme, self.component, self.role) if p is not None]

    @property
    def address(self) -> str | None:
        return "/".join(p.code for p in self.parts()) or None

    def fields(self) -> dict[str, Any]:
        return {
            "programme": _id(self.programme),
            "component": _id(self.component),
            "role": _id(self.role),
            "programme_wide": self.role is not None and self.component is None,
            "address": self.address,
        }


# -- reading the files -----------------------------------------------------------------


def _text(body: dict[str, Any], key: str) -> str:
    return str(body.get(key) or "").strip()


def _list(body: dict[str, Any], key: str) -> tuple[str, ...]:
    value = body.get(key) or []
    return tuple(str(v) for v in (value if isinstance(value, list) else [value]))


def _code(value: Any, where: str) -> str:
    if not isinstance(value, str) or not CODE.fullmatch(value):
        raise OrgError(f"{where}: the code {value!r} is not lowercase letters, digits and dashes")
    return value


def _relative(text: str) -> bool:
    """Whether `text` is a folder relative to the repository on every system: no `~`, and no
    root or drive as either Windows or this system reads it ("/x", "\\x", "C:/x", "C:x")."""
    return not (text.startswith("~") or PureWindowsPath(text).anchor or Path(text).anchor)


def _folders(repo: Path, written: tuple[str, ...], where: str, problems: list[str]) -> tuple[Path, ...]:
    out = []
    for text in written:
        # RULE: a folder in an org file must be relative
        if not _relative(text):
            problems.append(f"{where}: folder {text} is left out; give it relative to the repository")
            continue
        out.append((repo / text).resolve())
    return tuple(out)


def _component(code: str, body: dict[str, Any], repo: Path, where: str, problems: list[str]) -> Component:
    code = _code(code, where)
    written = _list(body, "folders")
    return Component(
        code,
        _text(body, "name") or code,
        _list(body, "aliases"),
        _folders(repo, written, f"{where} component {code}", problems),
        _list(body, "remotes"),
        written,
    )


def _role(code: str, body: dict[str, Any], where: str) -> Role:
    code = _code(code, where)
    component = body.get("component")
    return Role(
        code,
        _text(body, "name") or code,
        _text(body, "purpose"),
        _text(body, "limits"),
        _list(body, "sessions"),
        str(component) if component else None,
        _list(body, "send_here"),
    )


def _tables(raw: dict[str, Any], key: str) -> list[tuple[str, dict[str, Any]]]:
    return [(str(k), v if isinstance(v, dict) else {}) for k, v in (raw.get(key) or {}).items()]


def read(repo: Path, problems: list[str]) -> Programme | None:
    """The programme in `repo`'s org file; None when it has none. A file that cannot be
    used at all raises OrgError; a part of it that cannot be used goes to `problems`."""
    path = repo / FILE
    if not path.is_file():
        return None
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise OrgError(f"{path}: cannot be read: {exc}") from None
    found = raw.get("programme")
    head: dict[str, Any] = found if isinstance(found, dict) else {}
    code = _code(head.get("code"), str(path))
    order = head.get("order")
    return Programme(
        code,
        _text(head, "name") or code,
        _list(head, "aliases"),
        order if isinstance(order, int) and not isinstance(order, bool) else LAST,
        repo,
        tuple(_component(k, v, repo, code, problems) for k, v in _tables(raw, "components")),
        tuple(_role(k, v, code) for k, v in _tables(raw, "roles")),
    )


@functools.lru_cache(maxsize=512)
def git_remotes(folder: str) -> tuple[str, ...]:
    """The git remote addresses of `folder`, asked once per folder while the bridge runs."""
    if not Path(folder).is_dir():
        return ()
    try:
        return tuple(config._remotes(Path(folder)))
    except (OSError, subprocess.SubprocessError):
        return ()


def known_folders(root: Path, given: Iterable[Path | str | None]) -> list[Path]:
    """The folders whose org files count: the ones `given` (the bridge's own clone and its
    plugins' folders), then every project folder directly under `root`."""
    out = [Path(f) for f in given if f]
    if root.is_dir():
        out += sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    return out


def load(folders: Iterable[Path], remotes: Remotes = git_remotes) -> Chart:
    """Every programme whose file is in one of `folders`, each read once."""
    programmes: dict[str, Programme] = {}
    problems: list[str] = []
    for repo in dict.fromkeys(Path(f).expanduser().resolve() for f in folders):
        try:
            found = read(repo, problems)
        except OrgError as exc:
            problems.append(str(exc))
            continue
        if found is None:
            continue
        if found.code in programmes:
            problems.append(f"{found.code}: given twice, in {programmes[found.code].repo} and {repo}; the first counts")
            continue
        programmes[found.code] = found
    ordered = sorted(programmes.values(), key=lambda p: (p.order, p.name.lower()))
    return Chart(ordered, problems, remotes)


# -- placing a session -----------------------------------------------------------------


def _under(folder: Path, cwd: Path) -> bool:
    return folder == cwd or folder in cwd.parents


@dataclass
class Chart:
    programmes: list[Programme]
    problems: list[str] = field(default_factory=list)
    remotes: Remotes = git_remotes

    def programme(self, code: str) -> Programme | None:
        return next((p for p in self.programmes if p.code == code), None)

    def listing(self) -> list[dict[str, Any]]:
        """The programmes, in order, as the phone page lists them."""
        return [{"id": p.code, "name": p.name, "order": i} for i, p in enumerate(self.programmes)]

    def _by_folder(self, cwd: Path) -> tuple[Programme, Component] | None:
        best: tuple[int, Programme, Component] | None = None
        for p in self.programmes:
            for c in p.components:
                for folder in c.folders:
                    # RULE: the deepest matching folder places a session
                    if _under(folder, cwd) and (best is None or len(folder.parts) > best[0]):
                        best = (len(folder.parts), p, c)
        return (best[1], best[2]) if best else None

    def _by_remote(self, cwd: str) -> tuple[Programme, Component] | None:
        wanted = [(p, c, r) for p in self.programmes for c in p.components for r in c.remotes]
        if not wanted:
            return None
        urls = list(self.remotes(cwd))
        return next(((p, c) for p, c, r in wanted if any(config._names(u, r) for u in urls)), None)

    def _by_name(self, names: Iterable[str]) -> tuple[Programme, Role] | None:
        names = set(names)
        return next(((p, r) for p in self.programmes for r in p.roles if names & set(r.sessions)), None)

    def place(self, cwd: str | None, names: Iterable[str]) -> Place:
        """Where a session working in `cwd`, answering to `names`, sits in the chart."""
        held = self._by_name(names)
        if held is not None:
            programme, role = held
            return Place(programme, programme.component(role.component), role)
        if not cwd:
            return Place()
        found = self._by_folder(Path(cwd).expanduser().resolve()) or self._by_remote(cwd)
        return Place(*found) if found else Place()

    def placer(self) -> Callable[[dict[str, Any]], Place]:
        """Places a running session's row, by its folder and every name it answers to."""
        return lambda d: self.place(d.get("cwd"), renames.answers_to(d))


# -- finding a session by its address --------------------------------------------------


class Ambiguous(LookupError):
    """The words fit more than one session; the owner is asked which."""

    def __init__(self, words: str, choices: list[str]) -> None:
        self.words, self.choices = words, choices
        super().__init__(f"{words!r} could mean {', '.join(choices)}. Ask the owner which one.")


def _fits(place: Place, wanted: list[str]) -> bool:
    """Whether `wanted` is the end of the session's address, a part by any of its words."""
    parts = place.parts()
    if not parts or len(wanted) > len(parts):
        return False
    return all(w in p.words() for w, p in zip(reversed(wanted), reversed(parts), strict=False))


def find(rows: list[dict[str, Any]], words: str, place: Callable[[dict[str, Any]], Place]) -> dict[str, Any] | None:
    """The one running session whose address `words` gives, whole or by its end."""
    wanted = [w.strip().lower() for w in str(words).split("/")]
    if not all(wanted):
        return None
    hits = {str(d.get("sessionId")): d for d in rows if _fits(place(d), wanted)}
    # RULE: an address that fits several sessions is answered with the choices
    if len(hits) > 1:
        raise Ambiguous(words, sorted(f"{place(d).address} ({d.get('name')})" for d in hits.values()))
    return next(iter(hits.values()), None)


# -- the check, the voice's overview and the page's view -------------------------------


def _role_warnings(p: Programme, role: Role, running: set[str]) -> list[str]:
    out = []
    for name in role.sessions:
        # RULE: a role naming a session that is not running is warned about
        if name not in running:
            out.append(f"{p.code}: role {role.code} names session {name!r}, which is not running")
    if role.component is not None and p.component(role.component) is None:
        out.append(f"{p.code}: role {role.code} is in component {role.component}, which the file does not declare")
    out += [
        f"{p.code}: role {role.code} sends {kind!r}, not one of {', '.join(KINDS)}"
        for kind in role.send_here
        if kind not in KINDS
    ]
    return out


def check(chart: Chart, running: set[str]) -> list[str]:
    """What is wrong with the chart: the files' problems, then each programme's warnings."""
    out = list(chart.problems)
    for p in chart.programmes:
        for c in p.components:
            for text, folder in zip(c.written, c.folders, strict=False):
                if not folder.is_dir():
                    out.append(f"{p.code}: component {c.code}: folder {text} does not exist")
        for role in p.roles:
            out += _role_warnings(p, role, running)
    return out


def _holders(
    p: Programme, role: Role, rows: list[dict[str, Any]], place: Callable[[dict[str, Any]], Place]
) -> list[dict[str, Any]]:
    return [d for d in rows if (at := place(d)).programme is p and at.role is role]


def _label(p: Programme, role: Role) -> str:
    return f"{role.component} {role.code}" if role.component else role.code


def overview(chart: Chart, rows: list[dict[str, Any]], place: Callable[[dict[str, Any]], Place]) -> list[str]:
    """The chart for the voice, a line per role, and one for the sessions in no programme."""
    lines = []
    for p in chart.programmes:
        for role in p.roles:
            running = "running" if _holders(p, role, rows, place) else "not running"
            send = f"; send {', '.join(role.send_here)}" if role.send_here else ""
            lines.append(
                f"{p.name} ({p.code}): {_label(p, role)}, {', '.join(role.sessions) or 'nobody'}, {running}{send}."
            )
    outside = [str(d.get("name")) for d in rows if place(d).programme is None]
    if outside:
        lines.append(f"Outside any programme: {', '.join(outside)}.")
    return lines


def view(p: Programme, rows: list[dict[str, Any]], place: Callable[[dict[str, Any]], Place]) -> dict[str, Any]:
    """One programme for the phone page: its components, and each role with who fills it."""
    roles = []
    for role in p.roles:
        holders = [str(d.get("sessionId")) for d in _holders(p, role, rows, place)]
        at = Place(p, p.component(role.component), role)
        roles.append(
            {
                **{k: at.fields()[k] for k in ("component", "programme_wide", "address")},
                "id": role.code,
                "name": role.name,
                "purpose": role.purpose,
                "limits": role.limits,
                "routing": list(role.send_here),
                "session_names": list(role.sessions),
                "sessions": holders,
                "running": bool(holders),
            }
        )
    return {
        "id": p.code,
        "name": p.name,
        "aliases": list(p.aliases),
        "components": [{"id": c.code, "name": c.name, "aliases": list(c.aliases)} for c in p.components],
        "roles": roles,
        "unfilled": [r["id"] for r in roles if not r["running"]],
    }

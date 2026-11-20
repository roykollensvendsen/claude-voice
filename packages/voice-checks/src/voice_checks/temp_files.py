"""No run leaves a temporary file behind: a static check of a plugin's code and tests.

It reads the code and never runs it, so it is a slip guard. The proof that a run leaves
nothing is each plugin's own session check in its conftest. Moved from the plugin template's
tests/test_temp_files.py (R24), with the `monkeypatch.undo(` rule of no-duplicates-v1's n6.
"""

import ast
import pathlib

FORGOTTEN = "a temporary file or folder made with no removal beside it"
UNDONE = (
    "monkeypatch.undo() part way through a test also undoes what the fixtures set, such as a network guard "
    "or a temporary root; use `with monkeypatch.context() as patch:` instead"
)


def _calls(node: ast.AST, name: str) -> list[ast.Call]:
    return [
        c
        for c in ast.walk(node)
        if isinstance(c, ast.Call) and (getattr(c.func, "attr", None) == name or getattr(c.func, "id", None) == name)
    ]


def _names(stmt: ast.AST) -> set[str]:
    """The names an assignment binds; none for any other statement."""
    if isinstance(stmt, ast.Assign):
        targets = stmt.targets
    elif isinstance(stmt, ast.AnnAssign):
        targets = [stmt.target]
    else:
        return set()
    return {n.id for target in targets for n in ast.walk(target) if isinstance(n, ast.Name)}


def _handed(calls: list[ast.Call], names: set[str]) -> bool:
    """Whether any of `calls` is handed one of `names`, as an argument or as what it is called on."""
    return any(isinstance(n, ast.Name) and n.id in names for call in calls for n in ast.walk(call))


def _all_calls(statements: list[ast.stmt]) -> list[ast.Call]:
    return [c for stmt in statements for c in ast.walk(stmt) if isinstance(c, ast.Call)]


def _forgotten_folders(tree: ast.AST, where: str) -> list[str]:
    """A folder made by mkdtemp whose name no `finally` beside it, or around it, removes."""
    safe: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        statements = body if isinstance(body, list) else []
        for i, stmt in enumerate(statements):
            names = _names(stmt)
            later = [s for s in statements[i + 1 :] if isinstance(s, ast.Try)]
            around = [node] if isinstance(node, ast.Try) else []
            if names and any(_handed(_all_calls(s.finalbody), names) for s in later + around):
                safe |= {id(c) for c in ast.walk(stmt)}
    return [f"{where}:{c.lineno}: mkdtemp, {FORGOTTEN}" for c in _calls(tree, "mkdtemp") if id(c) not in safe]


def _forgotten_files(tree: ast.AST, where: str) -> list[str]:
    """A file made by mkstemp whose name its function never removes or moves, and NamedTemporaryFile(delete=False)."""
    found = []
    for function in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
        gone = [c for name in ("unlink", "remove", "replace") for c in _calls(function, name)]
        for stmt in ast.walk(function):
            made = _calls(stmt, "mkstemp") if _names(stmt) else []
            if made and not _handed(gone, _names(stmt)):
                found += [f"{where}:{c.lineno}: mkstemp, {FORGOTTEN}" for c in made]
        bare = [
            c for c in _calls(function, "mkstemp") if not any(c in ast.walk(s) for s in ast.walk(function) if _names(s))
        ]
        found += [f"{where}:{c.lineno}: mkstemp, {FORGOTTEN}" for c in bare]
    found += [
        f"{where}:{call.lineno}: NamedTemporaryFile(delete=False), {FORGOTTEN}"
        for call in _calls(tree, "NamedTemporaryFile")
        if any(k.arg == "delete" and getattr(k.value, "value", True) is False for k in call.keywords)
    ]
    return found


def _kept_paths(tree: ast.Module, where: str) -> list[str]:
    """A temporary path a module keeps as a constant, read before the test run moves the folder."""
    return [
        f"{where}:{stmt.lineno}: a temporary path kept as a module constant"
        for stmt in tree.body
        if isinstance(stmt, ast.Assign | ast.AnnAssign)
        and stmt.value is not None
        and any(_calls(stmt.value, n) for n in ("gettempdir", "mkdtemp", "TemporaryDirectory"))
    ]


def offences(source: str, where: str = "x.py", *, src: bool = False) -> list[str]:
    """The ways `source` makes a temporary file and may leave it, by line."""
    tree = ast.parse(source)
    found = _forgotten_folders(tree, where) + _forgotten_files(tree, where)
    if src:
        found += _kept_paths(tree, where)
    return sorted(set(found))


def _annotated_monkeypatch(annotation: ast.expr | None) -> bool:
    """Whether a parameter is annotated as pytest's MonkeyPatch, by either spelling."""
    return getattr(annotation, "id", None) == "MonkeyPatch" or getattr(annotation, "attr", None) == "MonkeyPatch"


def _fixture_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """The names a test function gets the monkeypatch fixture under."""
    params = [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]
    return {p.arg for p in params if p.arg == "monkeypatch" or _annotated_monkeypatch(p.annotation)}


def undone(source: str, where: str) -> list[str]:
    """Each `.undo()` on the monkeypatch fixture, under any name; a MonkeyPatch of its own may be undone."""
    found = []
    for function in (n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)):
        names = _fixture_names(function)
        found += [
            f"{where}:{c.lineno}: {UNDONE}"
            for c in _calls(function, "undo")
            if isinstance(c.func, ast.Attribute) and isinstance(c.func.value, ast.Name) and c.func.value.id in names
        ]
    return sorted(set(found))


def scan(root: pathlib.Path) -> list[str]:
    """Every finding in a plugin's src/, scripts/ and tests/, by path and line."""
    found: list[str] = []
    for folder in ("src", "scripts", "tests"):
        # A folder can be named like a file: a plugin's mutation rows sit in one per source file.
        for path in sorted(p for p in (root / folder).rglob("*.py") if p.is_file()):
            where = path.relative_to(root).as_posix()
            text = path.read_text(encoding="utf-8")
            if folder == "tests":
                found += undone(text, where)
            else:
                found += offences(text, where, src=folder == "src")
    return sorted(found)

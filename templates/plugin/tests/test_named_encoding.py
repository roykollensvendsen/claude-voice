"""Every file this plugin reads or writes as text names its encoding.

Windows reads text in its own code page unless told otherwise, so a file written here and read
there can come back changed. The lint (PLW1514) only sees a call on something it knows is a
Path, so this walks the code itself and refuses any text read or write that names no encoding.
"""

import ast
import pathlib

ROOT = pathlib.Path(__file__).parent.parent
FOLDERS = ("src", "scripts", "tests")
#: Methods that read or write a whole file as text, and take an encoding.
TEXT_METHODS = {"read_text", "write_text"}
#: Calls that open text unless their mode says binary. TemporaryFile and its kin are binary by
#: default, so they only count when a mode without "b" is given.
OPENERS = {"open"}
TEMPORARY = {"TemporaryFile", "NamedTemporaryFile", "SpooledTemporaryFile"}


def _name(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


def _mode(call: ast.Call, position: int) -> str | None:
    """The mode as written, when it is a plain string; None when it is not given."""
    found = _keyword(call, "mode") or (call.args[position] if len(call.args) > position else None)
    if isinstance(found, ast.Constant) and isinstance(found.value, str):
        return found.value
    return None if found is None else "?"  # a mode worked out at run time is taken as text


def _on_os(call: ast.Call) -> bool:
    func = call.func
    return isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "os"


def _unnamed(call: ast.Call) -> bool:
    """Whether this call reads or writes text without naming its encoding."""
    if _keyword(call, "encoding") is not None or any(k.arg is None for k in call.keywords):
        return False
    name = _name(call)
    if name in TEXT_METHODS:  # read_text(encoding) or write_text(data, encoding)
        return len(call.args) < (2 if name == "write_text" else 1)
    if name in OPENERS and not _on_os(call):  # os.open gives a file descriptor, never text
        # open(file, mode) as a function; path.open(mode) as a method
        mode = _mode(call, 1 if isinstance(call.func, ast.Name) else 0)
        return "b" not in (mode or "r")
    if name in TEMPORARY:
        mode = _mode(call, 0)
        return mode is not None and "b" not in mode
    return False


def unnamed_encodings(root: pathlib.Path) -> list[str]:
    """Each text read or write under the folders that names no encoding, as path:line."""
    found = []
    for folder in FOLDERS:
        for path in sorted(p for p in (root / folder).rglob("*.py") if p.is_file()):
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            found += [
                f"{path.relative_to(root)}:{node.lineno}"
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and _unnamed(node)
            ]
    return found


def test_every_text_read_or_write_names_its_encoding():
    assert unnamed_encodings(ROOT) == []


def test_the_walk_finds_each_kind_of_text_access_that_names_no_encoding(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "bad.py").write_text(
        "import pathlib, tempfile\n"
        "p = pathlib.Path('x')\n"
        "p.read_text()\n"
        "p.write_text('a')\n"
        "open('x')\n"
        "p.open('w')\n"
        "tempfile.TemporaryFile('w+')\n"
        "p.read_text(encoding='utf-8')\n"
        "p.write_text('a', encoding='utf-8')\n"
        "open('x', 'rb')\n"
        "p.open('rb')\n"
        "tempfile.TemporaryFile()\n"
        "p.read_text('utf-8')\n"
        "import os\n"
        "os.open('x', os.O_RDONLY)\n",
        encoding="utf-8",
    )
    assert unnamed_encodings(tmp_path) == [f"src/bad.py:{n}" for n in (3, 4, 5, 6, 7)]

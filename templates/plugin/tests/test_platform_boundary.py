"""Everything about the operating system lives in one place, `src/gizmo_mcp/platform/`.

claude-voice runs on Linux only today. The rule is that another platform can later be one
change to that folder, so nothing outside it may name an OS path, a service manager, a package
manager or a desktop command, or ask which OS it is on. This reads the code in `src/` and
`scripts/` (docstrings and comments are prose, and are left alone).

Two ways through, both narrow:
- the device adapter named by `DEVICE` in `scripts/ratchet.py` may pass a device's own command
  as a string to one of its send functions, since that runs on the device, not here;
- `[tool.platform-boundary] allow` in `pyproject.toml` lists `"<file>: <mark>"`, each a place
  another platform would break, so keep it short and say why in a comment beside it.
"""

import ast
import importlib.util
import pathlib
import re
import tomllib

ROOT = pathlib.Path(__file__).parent.parent
PACKAGE = ROOT / "src" / "gizmo_mcp"
PLACE = PACKAGE / "platform"
MARKS = re.compile(
    r"(?<![\w.])/(?:etc|run/user|proc|sys|dev)(?=/|\b)"
    r"|\bXDG_[A-Z_]+"
    r"|\b(?:systemctl|journalctl|loginctl|notify-send|xdg-open)\b"
    r"|(?:^|\s)(?:apt-get|apt|pacman|dnf|brew)\s+(?:install|remove|-S\w*)\b"
)
#: Questions about which OS this is, which belong in the platform place too.
ASKS = {("sys", "platform"), ("os", "name"), ("platform", "system"), ("os", "uname")}


def _ratchet():
    spec = importlib.util.spec_from_file_location("ratchet_for_platform", ROOT / "scripts/ratchet.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _docstrings(tree):
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def _sent(tree, sends):
    """The strings passed, anywhere in their arguments, to the device's own send functions."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name in sends:
                found |= {id(c) for arg in node.args for c in ast.walk(arg) if isinstance(c, ast.Constant)}
    return found


def breaches(path, *, sends=()):
    """Each mark in `path`'s code, as "<file>: <mark>"."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skip = _docstrings(tree) | _sent(tree, sends)
    where = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            found += [(node.lineno, f"{where}: {m.group().strip()}") for m in MARKS.finditer(node.value)]
        elif (
            isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and (node.value.id, node.attr) in ASKS
        ):
            found.append((node.lineno, f"{where}: {node.value.id}.{node.attr}"))
    return [said for _, said in sorted(found)]


def _sources():
    return [
        p
        for p in [*sorted((ROOT / "src").rglob("*.py")), *sorted((ROOT / "scripts").glob("*.py"))]
        if not p.is_relative_to(PLACE)
    ]


def test_nothing_outside_the_platform_place_names_the_operating_system():
    device = _ratchet().DEVICE
    allowed = set(tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["platform-boundary"]["allow"])
    found = []
    for path in _sources():
        sends = device["sends"] if path == PACKAGE / device["file"] else ()
        found += [b for b in breaches(path, sends=sends) if b not in allowed]
    assert found == [], "move these into src/gizmo_mcp/platform/ and ask it there: " + "; ".join(found)


def test_the_check_sees_each_kind_of_mark(tmp_path):
    """Proof the check above can fail, one kind at a time."""
    source = tmp_path / "leaky.py"
    source.write_text(
        '"""A docstring may say /etc/hosts and systemctl."""\n'
        "import os, sys\n"
        'A = "/run/user/1000"\n'
        'B = os.environ["XDG_STATE_HOME"]\n'
        'C = ["systemctl", "--user", "restart"]\n'
        'D = "sudo apt install thing"\n'
        "E = sys.platform\n"
    )
    found = breaches(source)
    assert found == [
        "leaky.py: /run/user",
        "leaky.py: XDG_STATE_HOME",
        "leaky.py: systemctl",
        "leaky.py: apt install",
        "leaky.py: sys.platform",
    ]


def test_a_device_s_own_command_passes_only_through_its_send_function(tmp_path):
    source = tmp_path / "device.py"
    source.write_text('def read(link):\n    link.send("cat /proc/uptime")\n    return "/proc/uptime"\n')
    assert breaches(source, sends=("send",)) == ["device.py: /proc"]


def test_the_device_adapter_named_for_the_check_exists():
    device = _ratchet().DEVICE
    assert (PACKAGE / device["file"]).is_file()
    assert device["sends"]

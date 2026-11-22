"""The mutation run and the ratchet measure this checkout's own code, never another's installed copy."""

import ast
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import own_code


def _checkout(where: pathlib.Path) -> pathlib.Path:
    (where / "src" / own_code.MODULE).mkdir(parents=True)
    (where / "src" / own_code.MODULE / "__init__.py").write_text("", encoding="utf-8")
    return where


def test_a_check_measures_only_this_checkout_s_own_code(tmp_path):
    """A package loaded first from somewhere else is refused, as another checkout's install hook would load it.

    The hook is played by a sitecustomize that imports the other checkout's copy at start-up,
    before the suite's own path to src/ is read.
    """
    here = _checkout(tmp_path / "here")
    elsewhere = _checkout(tmp_path / "elsewhere")
    own_code.require(here, sys.executable, dict(os.environ))
    hook = tmp_path / "hook"
    hook.mkdir()
    (hook / "sitecustomize.py").write_text(
        f"import sys\nsys.path.insert(0, {str(elsewhere / 'src')!r})\nimport {own_code.MODULE}\nsys.path.pop(0)\n",
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": str(hook)}
    with pytest.raises(SystemExit) as refused:
        own_code.require(here, sys.executable, env)
    said = str(refused.value)
    assert str(elsewhere / "src") in said
    assert str(here / "src") in said


def test_a_checkout_whose_code_cannot_be_imported_is_refused(tmp_path, monkeypatch):
    """Asked for a package nothing installs: `uv sync` installs this one, so it imports from anywhere."""
    missing = "no_package_installs_this"
    monkeypatch.setattr(own_code, "ASK", own_code.ASK.replace(own_code.MODULE, missing))
    monkeypatch.setattr(own_code, "MODULE", missing)
    with pytest.raises(SystemExit, match="cannot import"):
        own_code.require(tmp_path, sys.executable, dict(os.environ))


@pytest.mark.parametrize(("script", "where"), [("mutate.py", "main"), ("ratchet.py", "measure")])
def test_both_tools_check_where_their_code_comes_from(script, where):
    """In the function that runs the suite, never at import: the scripts import nothing of the package."""
    tree = ast.parse((ROOT / "scripts" / script).read_text(encoding="utf-8"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == where)
    calls = [ast.unparse(c.func) for c in ast.walk(function) if isinstance(c, ast.Call)]
    assert "own_code.require" in calls
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import | ast.ImportFrom) for a in n.names}
    imported |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert own_code.MODULE not in imported

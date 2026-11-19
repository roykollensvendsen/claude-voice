"""The plugin template renders, carries nothing personal, and passes its own checks once rendered.

`templates/plugin/` is what `claude-voice new-plugin` will copy for any developer. These tests
render it into a temporary folder under a new name, offline, and run its own gates there with
this interpreter, so no network and no GitHub are needed. The folder is removed afterwards.

The heavy ones, the rendered copy's whole suite, its mutation run and its ratchet, are marked
`template` and run in a job of their own (`uv run pytest -m template`), with no time limit.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import tomllib

import jsonschema
import pytest
from plugin_template import TEMPLATE, TOKEN, render

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "docs/plugin-manifest/v1.json"
GUIDE = ROOT / "docs/write-a-plugin.md"
#: The name the tests render the template under: any made-up plugin will do.
NAME = "lamp"

linux_only = pytest.mark.skipif(sys.platform != "linux", reason="the template is for Linux only, as the platform is")


def _load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _texts(folder: pathlib.Path) -> dict[str, str]:
    found = {}
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        try:
            found[path.relative_to(folder).as_posix()] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
    return found


@pytest.fixture
def rendered(tmp_path):
    return render(NAME, tmp_path / f"{NAME}-mcp")


def test_a_rendered_plugin_keeps_no_trace_of_the_template_name(rendered):
    """Every path and every text has the new name; a leftover token would ship as someone else's plugin."""
    paths = [p.relative_to(rendered).as_posix() for p in rendered.rglob("*")]
    assert [p for p in paths if TOKEN in p.lower()] == []
    left = [where for where, text in _texts(rendered).items() if TOKEN in text.lower()]
    assert left == []
    assert (rendered / f"src/{NAME}_mcp/server.py").is_file()
    assert f'name = "{NAME}"' in (rendered / "claude-voice-plugin.toml").read_text(encoding="utf-8")


def test_a_name_that_cannot_be_a_package_is_refused(tmp_path):
    for bad in ("Lamp", "9lamp", "lamp-light", "lamp_light", "", "l" * 25):
        with pytest.raises(ValueError, match="not a plugin name") as refused:
            render(bad, tmp_path / "out")
        assert "24" in str(refused.value), "the refusal names the length limit too"
    assert not (tmp_path / "out").exists()
    assert render("l" * 24, tmp_path / "longest").is_dir()


def test_a_missing_template_is_refused(tmp_path):
    """A copy from a folder that is not there would make an empty plugin and call it done."""
    with pytest.raises(FileNotFoundError, match="no template"):
        render(NAME, tmp_path / "out", template=tmp_path / "gone")
    assert not (tmp_path / "out").exists()


def test_rendering_into_a_folder_that_exists_is_refused(tmp_path):
    (tmp_path / "taken").mkdir()
    with pytest.raises(FileExistsError, match="exists already"):
        render(NAME, tmp_path / "taken")


#: Built from parts, so this file does not itself hold what it looks for. Lower-case, matched
#: against lower-cased text.
PERSONAL = ("roy" + "kollen", "kollen" + "svendsen", "roy kollen", "@" + "gmail", "/home/" + "roy")


def test_the_template_holds_nothing_personal():
    """Any developer renders it, so no owner, account, path or host of ours may ride along."""
    found = [
        f"{where}: {word}" for where, text in _texts(TEMPLATE).items() for word in PERSONAL if word in text.lower()
    ]
    assert found == []
    scan = _load(TEMPLATE / "scripts/private_data_scan.py", "template_private_data_scan")
    offences = {where: scan.offences(text, set()) for where, text in _texts(TEMPLATE).items()}
    assert {where: said for where, said in offences.items() if said} == {}


def test_the_template_ships_the_published_manifest_schema():
    """The rendered plugin checks its manifest offline, so it carries a copy, which must be this one.

    All but its `$id`: that is this repository's address, which names its owner's account, and
    nothing in a template may name a person.
    """
    copy = json.loads((TEMPLATE / "tests/plugin-manifest-v1.json").read_text(encoding="utf-8"))
    published = json.loads(SCHEMA.read_text(encoding="utf-8"))
    published.pop("$id", None)
    assert "$id" not in copy
    assert copy == published


def _code(path: pathlib.Path) -> str:
    """The module's code alone: its docstrings dropped, so their wording may differ and nothing else."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.dump(tree)


def test_the_template_s_red_first_is_this_repository_s():
    """The template carries a copy of red-first; a fix to one must reach the other, so their code is held equal."""
    assert _code(TEMPLATE / "scripts/red_first.py") == _code(ROOT / "scripts/red_first.py")


def test_the_template_s_workflow_test_is_this_repository_s():
    """Both carry the same check of how pull request runs are grouped; a fix to one must reach
    the other, so the two files are held byte for byte equal."""
    ours = (ROOT / "tests/test_workflows.py").read_bytes()
    assert (TEMPLATE / "tests/test_workflows.py").read_bytes() == ours


def test_the_template_manifest_meets_the_published_schema():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    manifest = tomllib.loads((TEMPLATE / "claude-voice-plugin.toml").read_text(encoding="utf-8"))
    jsonschema.validate(manifest, schema)


def test_the_published_schema_refuses_a_key_it_does_not_know():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    manifest = tomllib.loads((TEMPLATE / "claude-voice-plugin.toml").read_text(encoding="utf-8"))
    with pytest.raises(jsonschema.ValidationError, match="surprise"):
        jsonschema.validate({**manifest, "surprise": 1}, schema)


def test_no_sentence_in_the_guide_or_the_template_pages_is_over_thirty_words():
    readable = _load(TEMPLATE / "scripts/readable.py", "template_readable")
    pages = [GUIDE, *sorted(TEMPLATE.rglob("*.md"))]
    long = [
        f"{page.relative_to(ROOT)}: {sentence[:60]}..."
        for page in pages
        for sentence in readable.long_sentences(page.read_text(encoding="utf-8"))
    ]
    assert long == []


# --- The rendered copy's own gates, offline --------------------------------------------


@pytest.fixture(scope="module")
def checkout():
    """The template rendered and committed in a temporary folder, removed afterwards."""
    with tempfile.TemporaryDirectory(prefix="plugin-template-") as scratch:
        home = pathlib.Path(scratch, "home")
        home.mkdir()
        repo = render(NAME, pathlib.Path(scratch, f"{NAME}-mcp"))
        env = _env(home)
        for args in (
            ["init", "-q", "-b", "main"],
            ["add", "-A"],
            ["commit", "-q", "-m", "feat: Start the plugin from the template"],
        ):
            subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)
        yield repo, env


def _env(home: pathlib.Path) -> dict[str, str]:
    """No owner's files, and no network tool's settings: a home of its own, and a made-up committer."""
    keep = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT")}
    identity = {"GIT_AUTHOR_NAME": "Example Owner", "GIT_AUTHOR_EMAIL": "owner@example.invalid"}
    identity |= {"GIT_COMMITTER_NAME": "Example Owner", "GIT_COMMITTER_EMAIL": "owner@example.invalid"}
    return {**keep, **identity, "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1", "PYTHONDONTWRITEBYTECODE": "1"}


def _run(checkout, *args: str) -> subprocess.CompletedProcess[str]:
    repo, env = checkout
    return subprocess.run([sys.executable, *args], cwd=repo, env=env, capture_output=True, text=True, check=False)


@linux_only
@pytest.mark.template
@pytest.mark.parametrize(
    "gate",
    [
        ("-m", "ruff", "check", "."),
        ("-m", "ruff", "format", "--check", "."),
        ("-m", "mypy", "src"),
        ("scripts/private_data_scan.py", "--tracked", "--history"),
        ("-m", "pytest", "-q", "-p", "no:cacheprovider", "--strict-markers"),
    ],
    ids=["lint", "format", "types", "private-data", "suite"],
)
def test_a_rendered_plugin_passes_its_own_gates(checkout, gate):
    done = _run(checkout, *gate)
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-4000:]


@linux_only
@pytest.mark.template
def test_a_rendered_plugin_proves_every_rule_it_marks(checkout):
    """Its mutation run switches each rule off and the test named after it goes red; no limit on time."""
    done = _run(checkout, "scripts/mutate.py")
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-4000:]
    assert " rules turned off the test that names them" in done.stdout


@linux_only
@pytest.mark.template
def test_a_rendered_plugin_s_ratchet_measures_it_clean(checkout):
    done = _run(checkout, "scripts/ratchet.py", "measure")
    assert done.returncode == 0, done.stdout[-4000:] + done.stderr[-4000:]
    levels = json.loads(done.stdout)
    assert levels["mutation"]["killed"] == levels["mutation"]["total"] > 0
    assert (levels["lint_errors"], levels["type_errors"], levels["cycles"], levels["layer_violations"]) == (0, 0, 0, 0)

"""The temporary-file check: R24, moved here from the plugin template's tests/test_temp_files.py,
and the `monkeypatch.undo(` rule from no-duplicates-v1's note n6."""

import pytest

from voice_checks.temp_files import offences, scan, undone


@pytest.mark.parametrize(
    ("source", "said"),
    [
        ("import tempfile\nd = tempfile.mkdtemp()\n", "mkdtemp"),
        ("import tempfile\ndef f():\n    fd, name = tempfile.mkstemp()\n", "mkstemp"),
        ("import tempfile\nf = tempfile.NamedTemporaryFile(delete=False)\n", "NamedTemporaryFile"),
        (
            (
                "import tempfile, shutil\ndef f(other):\n    d = tempfile.mkdtemp()\n"
                "    try:\n        pass\n    finally:\n        shutil.rmtree(other)\n"
            ),
            "mkdtemp",
        ),
        (
            "import tempfile\ndef f(text):\n    fd, name = tempfile.mkstemp()\n    return text.replace('a', 'b')\n",
            "mkstemp",
        ),
    ],
    ids=["mkdtemp", "mkstemp", "NamedTemporaryFile", "a finally that removes another name", "str.replace"],
)
def test_the_static_check_refuses_each_forgotten_temporary_file(source, said):
    found = offences(source)
    assert len(found) == 1
    assert said in found[0]


def test_the_static_check_lets_a_removed_temporary_file_through():
    removed = (
        "import tempfile, shutil, os\n"
        "def f():\n"
        "    d = tempfile.mkdtemp()\n"
        "    try:\n        pass\n    finally:\n        shutil.rmtree(d)\n"
        "def g():\n"
        "    fd, name = tempfile.mkstemp()\n"
        "    os.replace(name, 'there')\n"
        "def h():\n"
        "    with tempfile.TemporaryDirectory() as d:\n        pass\n"
    )
    assert offences(removed) == []


def test_a_module_in_src_keeps_no_temporary_path_as_a_constant():
    """It would be read before the test run moves the temporary folder, so it would escape it."""
    assert offences("import tempfile\nHERE = tempfile.gettempdir()\n", src=True)
    assert offences("import tempfile\nHERE = tempfile.gettempdir()\n") == []


RUN_FOLDERS = """\
import shutil, tempfile

def remove(folder):
    shutil.rmtree(folder)

def start_run():
    {marker_above}
    return tempfile.mkdtemp(){marker_on}

def end_run(folder):
    remove(folder)

def dead_runs(folders):
    for folder in folders:
        shutil.rmtree(folder)

def says_hello():
    print("hello")
"""


def _run_folders(*, above="", on=""):
    return RUN_FOLDERS.format(marker_above=above or "pass", marker_on=f"  {on}" if on else "")


@pytest.mark.parametrize("place", ["above", "on"], ids=["on the line above", "on the same line"])
def test_a_folder_its_named_functions_remove_is_let_through(place):
    """A run folder made here and removed by the run's end and by the sweep is not forgotten."""
    marker = "# temp-files: removed by end_run, dead_runs"
    assert offences(_run_folders(**{place: marker})) == []


@pytest.mark.parametrize(
    ("marker", "said"),
    [
        ("# temp-files: removed by", "names no function"),
        ("# temp-files: removed by nowhere", "nowhere is not a function in this module"),
        ("# temp-files: removed by says_hello", "says_hello removes nothing"),
        ("# temp-files: removed by end_run, says_hello", "says_hello removes nothing"),
    ],
    ids=["a bare marker", "an unknown name", "a function that removes nothing", "one of two removes nothing"],
)
def test_a_marker_that_proves_nothing_still_fails(marker, said):
    found = offences(_run_folders(above=marker))
    assert len(found) == 1
    assert said in found[0]
    assert "name the function that removes it" in found[0]


@pytest.mark.parametrize(
    "source",
    [
        "def test_it(monkeypatch):\n    monkeypatch.setattr('a.b', 1)\n    monkeypatch.undo()\n",
        "import pytest\ndef test_it(mp: pytest.MonkeyPatch):\n    mp.undo()\n",
        "from pytest import MonkeyPatch\ndef test_it(patch: MonkeyPatch):\n    patch.undo()\n",
    ],
    ids=["the fixture", "pytest.MonkeyPatch by another name", "MonkeyPatch by another name"],
)
def test_a_test_never_undoes_the_monkeypatch_fixture_part_way(source):
    """undo() mid-test also undoes what the fixtures set, a network guard or a temporary root."""
    found = undone(source, "tests/test_x.py")
    assert len(found) == 1
    assert "monkeypatch.context()" in found[0]


def test_a_monkeypatch_of_its_own_may_be_undone():
    """A MonkeyPatch made outside the fixture holds only its own patches, and must be undone."""
    own = "import pytest\ndef test_it():\n    mp = pytest.MonkeyPatch()\n    mp.setattr('a.b', 1)\n    mp.undo()\n"
    assert undone(own, "tests/test_x.py") == []


def _tree(tmp_path, files):
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def test_the_scan_reads_src_scripts_and_every_test_folder(tmp_path):
    root = _tree(
        tmp_path,
        {
            "src/pkg/deep/core.py": "import tempfile\nHERE = tempfile.gettempdir()\n",
            "scripts/tool.py": "import tempfile\nd = tempfile.mkdtemp()\n",
            "tests/deep/test_it.py": "def test_it(monkeypatch):\n    monkeypatch.undo()\n",
        },
    )
    found = scan(root)
    assert [f.split(":")[0] for f in found] == ["scripts/tool.py", "src/pkg/deep/core.py", "tests/deep/test_it.py"]


def test_a_folder_named_like_a_python_file_is_not_read(tmp_path):
    """A plugin's mutation rows sit in folders named after the file they mutate."""
    root = _tree(tmp_path, {"scripts/mutations/scripts/tool.py/a-rule.toml": "[[rule]]\n"})
    assert scan(root) == []


def test_a_clean_tree_has_nothing_to_say(tmp_path):
    root = _tree(tmp_path, {"src/pkg/core.py": "x = 1\n", "tests/test_it.py": "def test_it():\n    pass\n"})
    assert scan(root) == []

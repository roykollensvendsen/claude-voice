"""Which of CI's slower jobs a pull request runs, and the gate that adds the jobs up."""

import ast
import fnmatch
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "scripts"))

import ci_scope

ROOT = pathlib.Path(__file__).parent.parent
WORKFLOW = (ROOT / ".github" / "workflows" / "checks.yml").read_text(encoding="utf-8")


def ok(**outputs):
    return {"result": "success", "outputs": {k: str(v).lower() for k, v in outputs.items()}}


def test_only_a_pull_request_may_leave_out_the_slower_jobs():
    for event in ("push", "schedule", "workflow_dispatch"):
        assert ci_scope.scope(event, []) == {"windows": True, "python313": True, "template": True}
    assert ci_scope.scope("pull_request", ["src/claude_voice/mailbox.py"]) == {
        "windows": False,
        "python313": False,
        "template": False,
    }


def test_each_slower_job_runs_when_a_path_of_its_own_changes():
    assert ci_scope.scope("pull_request", ["src/claude_voice/safe_git.py"])["windows"]
    assert ci_scope.scope("pull_request", ["deploy/install.sh"])["windows"]
    assert ci_scope.scope("pull_request", ["templates/plugin/README.md"])["template"]
    assert not ci_scope.scope("pull_request", ["templates/plugin/README.md"])["windows"]
    # a change to the lock or to CI itself can break any job
    for path in ("uv.lock", ".github/workflows/checks.yml", "scripts/ci_scope.py"):
        assert all(ci_scope.scope("pull_request", [path]).values()), path


def test_a_change_to_the_shared_test_helpers_runs_everything():
    for path in ("tests/conftest.py", "tests/fakes.py"):
        assert all(ci_scope.scope("pull_request", [path]).values()), path


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_a_moved_file_counts_under_its_old_path_as_well_as_its_new_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "safe_git.py").write_text("x = 1\n" * 20, encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "one")
    git(tmp_path, "mv", "safe_git.py", "moved.py")
    git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "two")
    assert sorted(ci_scope.changed("HEAD~1", "HEAD")) == ["moved.py", "safe_git.py"]


def test_every_path_the_scope_lists_names_a_file():
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files"], check=True, capture_output=True, text=True)
    files = tracked.stdout.split()
    for job, globs in ci_scope.JOBS.items():
        for glob in globs:
            assert any(fnmatch.fnmatch(f, glob) for f in files), f"{job}: {glob} names no file"


def touches_processes_or_files(path):
    """Whether a module starts or stops processes, copies files, sets their mode or joins
    paths by hand: the things that behave differently on Windows."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name in RISKY for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module in RISKY:
            return True
        if isinstance(node, ast.Attribute) and ast.unparse(node) in RISKY_CALLS:
            return True
    return False


RISKY = {"subprocess", "signal", "shutil"}
RISKY_CALLS = {
    "os.chmod",
    "os.kill",
    "os.path.join",
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
}


def test_every_module_that_touches_processes_or_files_runs_windows_or_says_why_not():
    modules = sorted((ROOT / "src" / "claude_voice").glob("*.py"))
    for module in modules:
        name = module.relative_to(ROOT).as_posix()
        listed = any(fnmatch.fnmatch(name, glob) for glob in ci_scope.WINDOWS)
        if touches_processes_or_files(module):
            assert listed or ci_scope.WINDOWS_EXEMPT.get(name), f"{name} touches processes or files"
        assert not (listed and name in ci_scope.WINDOWS_EXEMPT), f"{name} is both listed and exempt"
    for name in ci_scope.WINDOWS_EXEMPT:
        assert (ROOT / name).is_file(), f"{name} is exempt but names no file"


def test_the_gate_passes_a_skipped_job_only_when_the_scope_left_it_out():
    ran = {"checks": {"result": "success"}, "quality": {"result": "success"}}
    left_out = {"windows": {"result": "skipped"}, "template": {"result": "skipped"}}
    assert ci_scope.gate({"scope": ok(windows=False, python313=False, template=False), **ran, **left_out}) == []
    # the scope said windows runs, so a skipped windows is not a pass
    assert ci_scope.gate({"scope": ok(windows=True, python313=False, template=False), **ran, **left_out}) == [
        "windows: skipped"
    ]
    # quality is never left out by the scope
    assert ci_scope.gate(
        {"scope": ok(windows=False, python313=False, template=False), "quality": {"result": "skipped"}}
    )
    # a scope that failed leaves nothing out
    assert ci_scope.gate({"scope": {"result": "failure"}, **ran})


def test_the_gate_fails_a_job_that_failed_or_was_cancelled():
    for result in ("failure", "cancelled"):
        needs = {"scope": ok(windows=False, python313=False, template=False), "windows": {"result": result}}
        assert ci_scope.gate(needs) == [f"windows: {result}"]


def test_the_gate_lets_dependabots_commits_job_skip_itself():
    needs = {"scope": ok(windows=True, python313=True, template=True), "commits": {"result": "skipped"}}
    assert ci_scope.gate(needs, actor="dependabot[bot]") == []
    assert ci_scope.gate(needs, actor="someone") == ["commits: skipped"]


def jobs():
    body = WORKFLOW.split("\njobs:\n", 1)[1]
    return re.findall(r"^  ([\w-]+):$", body, flags=re.MULTILINE)


def job(name):
    """The text of one job in checks.yml, up to the next job."""
    names = jobs()
    body = WORKFLOW.split(f"\n  {name}:\n", 1)[1]
    following = names[names.index(name) + 1 :]
    return body.split(f"\n  {following[0]}:\n", 1)[0] if following else body


def test_the_gate_waits_for_every_other_job_and_runs_whatever_they_did():
    gate = job("gate")
    listed = re.search(r"needs: \[([^\]]*)\]", gate).group(1)
    waited = sorted(n for n in jobs() if n != "gate" and n not in ci_scope.NOT_GATED)
    assert sorted(n.strip() for n in listed.split(",")) == waited
    assert "always()" in gate


def test_a_job_the_gate_does_not_wait_for_is_named_with_why():
    assert set(ci_scope.NOT_GATED) <= set(jobs()), "a job left out of the gate must exist"
    assert all(len(why.split()) >= 5 for why in ci_scope.NOT_GATED.values())


def test_the_slower_jobs_read_the_scope_and_still_report_when_left_out():
    assert "needs.scope.outputs.windows != 'false'" in job("windows")
    assert "needs.scope.outputs.template != 'false'" in job("template")
    # the 3.13 leg still runs, so its required check reports; only its steps are left out
    checks = job("checks")
    assert "needs.scope.outputs.python313 != 'false'" in checks
    steps = checks.split("    steps:\n", 1)[1]
    assert steps.count("        if: env.NEEDED == 'true'") == steps.count("      - ") - 1


def test_main_and_a_nightly_run_run_everything():
    assert "  push:\n    branches: [main]" in WORKFLOW
    assert '    - cron: "0 2 * * *"\n      timezone: "Europe/Oslo"' in WORKFLOW

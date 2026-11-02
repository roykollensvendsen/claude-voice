"""The doctor: which dependencies are missing for full use of the bridge, and how to fix each."""

import json

import pytest

from claude_voice import doctor


class Host:
    """A computer with exactly the tools and answers a test gives it."""

    def __init__(self, tools=("git", "gh", "uv", "tailscale"), answers=None, trusted=(), signed_in=True):
        self.tools = set(tools)
        self.answers = answers or {}
        self.trusted_folders = set(trusted)
        self.signed_in = signed_in

    def which(self, name):
        return f"/usr/bin/{name}" if name in self.tools else None

    def run(self, *command):
        return self.answers.get(command[:3], (0, ""))

    def login(self):
        return (True, "Claude Code is signed in") if self.signed_in else (False, "Claude Code is not signed in")

    def trusted(self, folder):
        return str(folder) in self.trusted_folders


SERVE = json.dumps(
    {
        "Web": {
            "box.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8811"}}},
            "box.ts.net:8814": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8811"}}},
        },
        "AllowFunnel": {"box.ts.net:443": True},
    }
)


def healthy(tmp_path):
    clone = tmp_path / "claude-voice"
    (clone / ".git").mkdir(parents=True)
    state = tmp_path / "state"
    state.mkdir()
    (state / "statusline").mkdir()
    (state / "statusline" / "s1.json").write_text("{}")
    host = Host(
        answers={
            ("gh", "auth", "status"): (0, ""),
            ("tailscale", "status", "--json"): (0, json.dumps({"BackendState": "Running"})),
            ("tailscale", "serve", "status"): (0, SERVE),
        },
        trusted={str(clone)},
    )
    return host, clone, state


def by_id(report):
    return {c["id"]: c for c in report["checks"]}


def test_a_complete_computer_passes_every_check(tmp_path):
    host, clone, state = healthy(tmp_path)
    report = doctor.examine(host, {"folder": str(clone)}, state_dir=state, port=8811)
    assert report["ready"] is True
    assert [c["id"] for c in report["checks"] if not c["ok"]] == []
    for c in report["checks"]:
        assert set(c) >= {"id", "title", "ok", "required", "affects", "detail", "fix", "fix_url"}


def test_every_missing_piece_says_what_is_lost_and_how_to_fix_it(tmp_path):
    host = Host(tools=(), signed_in=False)
    report = doctor.examine(host, {"folder": str(tmp_path / "nowhere")}, state_dir=tmp_path, port=8811)
    checks = by_id(report)
    assert report["ready"] is False  # Claude Code not signed in is required
    assert checks["claude_signed_in"]["required"] and "/login" in checks["claude_signed_in"]["fix"]
    assert "gh auth login" in checks["gh"]["fix"] and not checks["gh"]["required"]
    for c in report["checks"]:
        if not c["ok"]:
            assert c["fix"].strip() and c["affects"].strip(), c["id"]


def test_a_signed_out_github_cli_is_named_with_the_command_that_signs_in(tmp_path):
    host, clone, state = healthy(tmp_path)
    host.answers[("gh", "auth", "status")] = (1, "You are not logged into any GitHub hosts.")
    gh = by_id(doctor.examine(host, {"folder": str(clone)}, state_dir=state, port=8811))["gh"]
    assert gh["ok"] is False and gh["fix"] == "gh auth login"


def test_the_tailnet_port_must_be_one_funnel_does_not_serve(tmp_path):
    host, clone, state = healthy(tmp_path)
    only_funnel = json.dumps(
        {
            "Web": {"box.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8811"}}}},
            "AllowFunnel": {"box.ts.net:443": True},
        }
    )
    host.answers[("tailscale", "serve", "status")] = (0, only_funnel)
    serve = by_id(doctor.examine(host, {"folder": str(clone)}, state_dir=state, port=8811))["tailnet_port"]
    assert serve["ok"] is False and "tailscale serve --bg" in serve["fix"]


@pytest.mark.parametrize("missing", ["git", "gh", "uv"])
def test_a_contribute_session_does_not_start_while_a_tool_it_needs_is_missing(tmp_path, missing):
    host, clone, state = healthy(tmp_path)
    host.tools.discard(missing)
    blockers = doctor.before_role(host, {"folder": str(clone), "upstream": "o/r"})
    assert [b["id"] for b in blockers] == [missing]


def test_a_contribute_session_needs_a_clone_claude_code_trusts(tmp_path):
    host, clone, state = healthy(tmp_path)
    host.trusted_folders.clear()
    blockers = doctor.before_role(host, {"folder": str(clone), "upstream": "o/r"})
    assert [b["id"] for b in blockers] == ["role_trust"] and str(clone) in blockers[0]["fix"]
    assert doctor.before_role(host, {"folder": str(tmp_path / "gone"), "upstream": "o/r"})[0]["id"] == "role_clone"


def test_tools_in_the_users_own_folders_are_found_when_the_service_path_leaves_them_out(tmp_path, monkeypatch):
    import os

    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    tool = home / ".local" / "bin" / "uv"
    tool.write_text("#!/bin/sh\necho uv 1.0\n")
    tool.chmod(0o755)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # as a systemd user service gets it
    if os.name == "nt":
        pytest.skip("Windows finds tools by extension; the user folders are a Unix habit")
    host = doctor.RealHost()
    assert host.which("uv") == str(tool)
    assert host.run("uv")[0] == 0

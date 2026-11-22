"""A voice approval compares the plugin's checkout with its branch's tip on GitHub, read from
the bridge's own list of known plugins, at the question and again at the yes
(plugin-config-commands-v1, b3 and f1)."""

import subprocess

from test_plugin_voice import changed_desk, tool_names

from claude_voice import config, plugin_tip

TIP = "a" * 40
GARAGE = config.PluginSpec(name="garage", command="g", args=[], cwd="/home/x/garage-door", tools=["garage_status"])


def test_a_checkout_at_the_tip_on_github_may_be_approved_by_voice():
    assert plugin_tip.refusal(GARAGE, tip_of=lambda repo, branch: TIP, head_of=lambda folder: TIP) is None


def test_a_forged_tracking_ref_is_refused_since_head_is_not_the_tip_on_github():
    """A local origin/main moved to match HEAD passes the cheap check; GitHub's tip does not."""
    why = plugin_tip.refusal(GARAGE, tip_of=lambda repo, branch: TIP, head_of=lambda folder: "b" * 40)
    assert why and "GitHub" in why


def test_the_repository_comes_from_the_bridges_own_list_of_plugins():
    asked = []
    plugin_tip.refusal(GARAGE, tip_of=lambda repo, branch: asked.append((repo, branch)), head_of=lambda f: TIP)
    assert asked == [("roykollensvendsen/garage-door", "main")]
    stranger = config.PluginSpec(**{**GARAGE.__dict__, "name": "stranger"})
    why = plugin_tip.refusal(stranger, tip_of=lambda repo, branch: TIP, head_of=lambda folder: TIP)
    assert why and "not one of the known plugins" in why


def test_a_tip_that_cannot_be_read_means_typed():
    why = plugin_tip.refusal(GARAGE, tip_of=lambda repo, branch: None, head_of=lambda folder: TIP)
    assert why and "could not be read" in why


def test_a_gh_timeout_or_failure_reads_no_tip(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("gh", 10)

    monkeypatch.setattr(plugin_tip.procs, "find", lambda name: "/usr/bin/gh")
    monkeypatch.setattr(plugin_tip.subprocess, "run", timeout)
    assert plugin_tip.github_tip("o/r", "main") is None
    answer = subprocess.CompletedProcess([], 0, stdout="not a sha\n")
    monkeypatch.setattr(plugin_tip.subprocess, "run", lambda *a, **k: answer)
    assert plugin_tip.github_tip("o/r", "main") is None
    answer = subprocess.CompletedProcess([], 0, stdout=f"{TIP}\n")
    assert plugin_tip.github_tip("o/r", "main") == TIP


async def test_a_tip_that_moves_after_the_question_is_not_approved(tmp_path):
    made, server, _ = await changed_desk(tmp_path)
    asked = await made.approve_request("claude_app", "garage")
    assert asked["ok"]
    made.tip = lambda spec: "its checkout is not the tip of main on GitHub"
    out = await made.approve_confirm("claude_app", asked["token"])
    assert not out["ok"] and "GitHub" in out["say"]
    assert "garage_request" not in await tool_names(server)


async def test_a_voice_approval_asks_github_before_the_question(tmp_path):
    made, *_ = await changed_desk(tmp_path)
    made.tip = lambda spec: "its tip on GitHub could not be read; type it"
    out = await made.approve_request("claude_app", "garage")
    assert not out["ok"] and "GitHub" in out["say"]

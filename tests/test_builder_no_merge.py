"""Only the manager merges (batch-merge v1, part 3).

A role on an upstream whose pull requests land in batches runs the guard before each shell
command, and so does any role whose entry says `deny_merges = true`. Unless the owner's own roles
file gives the role `merges = true`, the guard refuses a merge and a write of a commit status or a
check result through gh, however gh's flags are placed and whatever wraps the call. Text rules
refuse the plain spellings too, as a second layer. Roles on other upstreams keep their merges.
"""

import json
import logging

import pytest

from claude_voice import guard, roles

UPSTREAM = "owner/name"


def decide(command, merges=False, merges_only=False):
    return guard.decide(command, UPSTREAM, False, {}.get, lambda: "origin", merges=merges, merges_only=merges_only)


MERGES = [
    "gh pr merge 12 --rebase",
    "gh pr merge --auto --squash 12",
    "gh -R owner/name pr merge 12",
    "gh pr -R owner/name merge 12",
    "gh --repo=owner/name pr merge 12",
    "gh --repo owner/name pr merge 12 --match-head-commit abc",
    "/usr/bin/gh pr merge 12",
    "env GH_REPO=owner/name gh pr merge 12",
    "GH_TOKEN=x gh pr merge 12",
    "timeout 60 gh pr merge 12",
    "bash -c 'gh pr merge 12'",
    'sh -c "gh -R owner/name pr merge 12"',
    "eval gh pr merge 12",
    "true && gh pr merge 12",
    "echo hi; gh pr merge 12",
    "gh pr merge $'1\\x32'",
    "gh alias set mm 'pr merge'",
    "gh alias import aliases.yml",
]

WRITES = [
    "gh api --method POST repos/owner/name/statuses/abc -f state=success -f context=batch",
    "gh api --method=POST repos/owner/name/statuses/abc -f state=success",
    "gh api -X POST repos/owner/name/statuses/abc",
    "gh api -XPOST /repos/owner/name/statuses/abc",
    "gh api repos/owner/name/statuses/abc -f state=success",
    "gh api repos/owner/name/statuses/abc -F state=success",
    "gh api repos/owner/name/statuses/abc --field state=success",
    "gh api repos/owner/name/statuses/abc --raw-field=state=success",
    "gh api repos/owner/name/statuses/abc --input body.json",
    "gh api -X PUT repos/owner/name/pulls/12/merge",
    "gh api --method put repos/{owner}/{repo}/pulls/12/merge",
    "gh -R owner/name api -X PUT repos/owner/name/pulls/12/merge",
    "gh api repos/owner/name/merges -f base=main -f head=feat",
    "gh api repos/owner/name/check-runs -f name=quality -f head_sha=abc",
    "gh api -X PATCH repos/owner/name/check-runs/9 -f conclusion=success",
    "gh api graphql -f query='mutation { mergePullRequest(input: {pullRequestId: \"x\"}) { clientMutationId } }'",
    "gh api graphql -f query='mutation { enablePullRequestAutoMerge(input: {}) { clientMutationId } }'",
    "gh api graphql -f query='mutation { createCommitStatus(input: {}) { clientMutationId } }'",
    "gh api graphql -f query='mutation { createBranchProtectionRule(input: {}) { clientMutationId } }'",
    "gh api graphql -f query='mutation { updateBranchProtectionRule(input: {}) { clientMutationId } }'",
    "gh api graphql -f query='mutation { deleteBranchProtectionRule(input: {}) { clientMutationId } }'",
    "gh api graphql -f query='mutation { createRepositoryRuleset(input: {}) { clientMutationId } }'",
    "gh api graphql -f query='mutation { updateRepositoryRuleset(input: {}) { clientMutationId } }'",
    "gh api graphql -f query='mutation { deleteRepositoryRuleset(input: {}) { clientMutationId } }'",
    "gh api graphql -f query='mutation { updateRepository(input: {}) { clientMutationId } }'",
    "gh api graphql -f query='mutation { archiveRepository(input: {}) { clientMutationId } }'",
    "gh api graphql -f query='mutation { enablePullRequestAgentMerge(input: {}) { clientMutationId } }'",
    "gh api graphql -f query='mutation { enqueuePullRequest(input: {}) { clientMutationId } }'",
    "gh api graphql -f query='mutation { createCommitOnBranch(input: {}) { clientMutationId } }'",
    "gh api graphql -f query='mutation { updateRef(input: {}) { clientMutationId } }'",
    "gh api graphql -f query='mutation { updateRefs(input: {}) { clientMutationId } }'",
    "gh api graphql -f query='mutation { deleteRef(input: {}) { clientMutationId } }'",
    "gh api graphql -f query='mutation { createCheckSuite(input: {}) { clientMutationId } }'",
    "bash -c 'gh api -X POST repos/owner/name/statuses/abc'",
    "gh api -X PATCH repos/owner/name/git/refs/heads/main -f sha=abc",
    "gh api -X DELETE repos/owner/name/git/refs/heads/main",
    "gh api -X PUT REPOS/Owner/Name/Pulls/5/Merge",
]

#: Writes that could take down the protection on main, or the repository's own settings.
ADMIN = [
    "gh api -X DELETE repos/owner/name/branches/main/protection",
    "gh api -X PUT repos/owner/name/branches/main/protection --input rules.json",
    "gh api -X DELETE repos/owner/name/branches/main/protection/enforce_admins",
    "gh api -X DELETE repos/owner/name/branches/feat/x/protection",
    "gh api -X POST repos/owner/name/rulesets --input ruleset.json",
    "gh api -X DELETE repos/owner/name/rulesets/7",
    "gh api repos/owner/name/branches/main/rename -f new_name=old",
    "gh api -X PATCH repos/owner/name -f default_branch=feat",
    "gh api -X PATCH repos/owner/name/ -F allow_merge_commit=true",
    "gh api -X PUT repos/owner/name/actions/permissions -F enabled=false",
    "gh api -X PUT repos/owner/name/actions/permissions/workflow -f default_workflow_permissions=write",
    "gh api repos/owner/name/hooks -f name=web",
    "gh api -X DELETE repos/owner/name/hooks/3",
    "gh api -X DELETE https://api.github.com/repos/Owner/Name/branches/main/protection?x=1",
]

#: The same writes, spelled with a host, a query or a fragment around the path.
SPELLED = [
    "gh api -X PUT repos/owner/name/pulls/5/merge?x=1",
    "gh api -X PUT 'repos/owner/name/pulls/5/merge#top'",
    "gh api repos/owner/name/merges?a=1 -f base=main -f head=feat",
    "gh api -X PUT https://api.github.com/repos/owner/name/pulls/5/merge",
    "gh api -X POST http://api.github.com/repos/owner/name/statuses/abc",
    "gh api -X PUT https://ghe.example/api/v3/repos/owner/name/pulls/5/merge",
    "gh api -X PATCH https://api.github.com/repos/owner/name/git/refs/heads/main?force=1 -F force=true",
    "gh api https://api.github.com/graphql -f query='mutation { mergePullRequest(input: {}) { clientMutationId } }'",
    "gh api /graphql -f query='mutation { mergePullRequest(input: {}) { clientMutationId } }'",
    "gh api https://ghe.example/api/graphql -f query='mutation { createCheckRun(input: {}) { clientMutationId } }'",
]

#: GraphQL calls whose query the guard cannot see: it comes from a file or from stdin.
UNSEEN = [
    "gh api graphql -F query=@mutation.graphql",
    "gh api graphql --field query=@q.graphql",
    "gh api graphql -Fquery=@q.graphql",
    "gh api graphql --field=query=@-",
    "gh api graphql --input body.json",
    "gh api graphql --input -",
    "gh api graphql --input=-",
    "gh api https://api.github.com/graphql --input body.json",
    "gh api https://ghe.example/api/graphql -F query=@q.graphql",
]

#: Ways to get gh's login out, for the same calls made with another program.
TOKENS = ["gh auth token", "gh auth status --show-token", "gh auth status -t", "gh auth token --hostname github.com"]

READS = [
    "gh pr view 12 --json mergeable,mergeStateStatus",
    "gh pr checks 12",
    "gh pr list --search 'is:merged'",
    "gh api repos/owner/name/commits/abc/statuses",
    "gh api repos/owner/name/commits/abc/status",
    "gh api repos/owner/name/statuses/abc",
    "gh api -X GET repos/owner/name/statuses/abc -f per_page=100",
    "gh api repos/owner/name/pulls/12/merge",
    "gh api repos/owner/name/check-runs/9",
    'gh api graphql -f query=\'{ repository(owner: "o", name: "n") { pullRequest(number: 1) { mergeable } } }\'',
    "gh api graphql -f query='{ node(id: \"x\") { ... on CheckRun { conclusion } } }'",
    "echo gh pr merge 12",
    "printf '%s' 'gh pr merge 12'",
    "git merge --ff-only origin/main",
    "gh run list --branch main",
    "gh api repos/owner/name/pulls/5/merge?x=1",
    "gh api https://api.github.com/repos/owner/name/pulls/5/merge",
    "gh api repos/owner/name/git/refs/heads/main",
    "gh api -X PATCH repos/owner/name/git/refs/heads/feat/x -f sha=abc",
    "gh api -X PATCH repos/owner/name/git/refs/heads/main-old -f sha=abc",
    "gh api graphql -f query='mutation { disablePullRequestAutoMerge(input: {}) { clientMutationId } }'",
    "gh api graphql -f query='mutation { createRef(input: {}) { clientMutationId } }'",
    "gh api graphql -f query='mutation { updateRepositoryCustomProperty(input: {}) { clientMutationId } }'",
    "gh api repos/owner/name/branches/main/protection",
    "gh api repos/owner/name/rulesets",
    "gh api repos/owner/name",
    "gh api -X POST repos/owner/name/issues -f title=x",
    "gh api -X POST repos/owner/name/pulls/5/reviews -f event=COMMENT",
    "gh api graphql -F owner=o -f query='{ viewer { login } }'",
    "gh auth status",
    "gh auth login --with-token",
]


@pytest.mark.parametrize("command", MERGES + WRITES + SPELLED + UNSEEN + TOKENS + ADMIN)
def test_a_merge_or_a_status_write_through_gh_is_refused_to_a_role_without_merges(command):
    verdict, reason = decide(command)
    assert verdict == "deny" and "manager" in reason
    assert decide(command, merges=True) is None or "push" in decide(command, merges=True)[1]


@pytest.mark.parametrize("command", MERGES[:7])
def test_the_flags_of_gh_never_hide_a_merge(command):
    assert guard.merge_or_status(command) == "merges a pull request"


@pytest.mark.parametrize("command", WRITES)
def test_a_writing_api_call_to_a_merge_or_a_status_is_refused(command):
    assert guard.merge_or_status(command) == "merges or sets a status through GitHub's API"


@pytest.mark.parametrize("command", SPELLED)
def test_the_path_of_an_api_call_is_read_without_its_host_query_or_fragment(command):
    assert guard.merge_or_status(command) == "merges or sets a status through GitHub's API"


@pytest.mark.parametrize("command", ADMIN)
def test_a_write_to_the_repositorys_protection_or_settings_is_refused(command):
    assert guard.merge_or_status(command) == "changes the repository's protection or settings through GitHub's API"


@pytest.mark.parametrize(
    "command",
    ['gh api graphql -f query="$Q"', 'gh api graphql -f query="$(cat q.graphql)"', "gh api graphql -f query=`cat q`"],
)
def test_a_graphql_query_in_a_variable_or_a_substitution_is_refused(command):
    assert "from a variable or a command" in guard.merge_or_status(command)
    assert decide(command)[0] == "deny"


@pytest.mark.parametrize("command", UNSEEN)
def test_a_graphql_query_from_a_file_or_stdin_is_refused_to_a_role_without_merges(command):
    assert "query the guard cannot read" in guard.merge_or_status(command)


@pytest.mark.parametrize("command", TOKENS)
def test_gh_s_login_is_never_handed_to_a_role_without_merges(command):
    assert "login" in guard.merge_or_status(command)


@pytest.mark.parametrize("command", READS)
def test_a_read_of_a_pull_request_or_its_checks_runs(command):
    assert guard.merge_or_status(command) is None
    assert decide(command, merges_only=True) is None


def test_merges_only_judges_nothing_but_merges():
    assert decide("git push origin :main", merges_only=True) is None
    assert decide("gh pr merge 12", merges_only=True)[0] == "deny"


def test_the_hook_reads_its_merge_flags_from_its_own_command_line(monkeypatch, capsys):
    command = {"tool_name": "Bash", "tool_input": {"command": "gh pr merge 12"}, "cwd": "."}
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps(command)))
    monkeypatch.setattr("sys.argv", ["guard", "--upstream", UPSTREAM, "--push-mode", "merges-only"])
    guard.main()
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps(command)))
    monkeypatch.setattr("sys.argv", ["guard", "--upstream", UPSTREAM, "--push-mode", "merges-only", "--merges"])
    guard.main()
    assert capsys.readouterr().out == ""


def hook_command(settings):
    return json.loads(settings)["hooks"]["PreToolUse"][0]["hooks"][0]["command"]


#: The upstream whose pull requests land in batches, and one that merges its own.
BATCHED = roles.BATCHED[0]
ELSEWHERE = "someone/else"


@pytest.mark.parametrize(
    ("role", "denied"),
    [
        ({"upstream": BATCHED}, True),
        ({"upstream": BATCHED, "merges": True}, False),
        ({"upstream": BATCHED, "merges": "true"}, True),
        ({"upstream": BATCHED, "merges": True, "deny_merges": True}, True),
        ({"upstream": ELSEWHERE}, False),
        ({"upstream": ELSEWHERE, "guard": True}, False),
        ({"upstream": ELSEWHERE, "deny_merges": True}, True),
        ({"upstream": ELSEWHERE, "deny_merges": "yes"}, False),
    ],
)
def test_merges_are_denied_by_default_only_on_an_upstream_that_merges_in_batches(role, denied):
    assert roles.denies_merges(role) is denied


@pytest.mark.parametrize(
    "upstream",
    [
        "RoyKollenSvendsen/Claude-Voice",
        "https://github.com/roykollensvendsen/claude-voice",
        "https://github.com/roykollensvendsen/claude-voice.git",
        "git@github.com:roykollensvendsen/claude-voice.git",
        "ssh://git@github.com/roykollensvendsen/claude-voice/",
        " roykollensvendsen/claude-voice/ ",
    ],
)
def test_an_upstream_is_compared_as_owner_and_name_whatever_its_spelling(upstream):
    assert roles.denies_merges({"upstream": upstream})
    assert not roles.denies_merges({"upstream": upstream.lower().replace("claude-voice", "claude-voice-ui")})


def test_the_batched_upstream_is_the_bridges_own():
    from claude_voice.config import UPSTREAM as OWN

    assert roles.BATCHED == (OWN,)


@pytest.mark.parametrize(
    ("role", "mode"),
    [
        ({"upstream": BATCHED}, "--push-mode merges-only"),
        ({"upstream": ELSEWHERE, "deny_merges": True}, "--push-mode merges-only"),
        ({"upstream": BATCHED, "guard": True}, None),
        ({"upstream": BATCHED, "guard": True, "push_mode": "own-clone"}, "--push-mode own-clone"),
        ({"upstream": BATCHED, "settings": "x.json"}, "--push-mode no-deletes"),
    ],
)
def test_a_role_denied_merges_gets_the_guard_with_only_its_merge_check_when_it_has_neither_guard_nor_settings(
    role, mode
):
    command = hook_command(roles.guard_settings(role))
    assert "-I -m claude_voice.guard" in command and "--merges" not in command
    assert (mode in command) if mode else "--push-mode" not in command


@pytest.mark.parametrize("role", [{"upstream": ELSEWHERE}, {"upstream": BATCHED, "merges": True}])
def test_a_role_that_may_merge_and_has_neither_guard_nor_settings_gets_no_hook(role):
    assert roles.guard_settings(role) is None


def test_a_guarded_role_that_may_merge_keeps_its_merges():
    settings = json.loads(roles.guard_settings({"upstream": ELSEWHERE, "guard": True}))
    assert "--merges" in hook_command(json.dumps(settings)) and "permissions" not in settings


@pytest.mark.parametrize("value", [True, "true", 1, "yes", None])
def test_a_role_denied_merges_carries_the_text_denies_of_a_merge(value):
    role = {"upstream": BATCHED, "guard": True, **({"merges": value} if value is not None else {})}
    settings = json.loads(roles.guard_settings(role))
    if value is True:
        assert "--merges" in hook_command(json.dumps(settings)) and "permissions" not in settings
    else:
        assert "--merges" not in hook_command(json.dumps(settings))
        assert settings["permissions"]["deny"] == list(roles.MERGE_DENIES)


def test_a_role_with_no_upstream_is_denied_merges_on_the_bridges_own(tmp_path, monkeypatch):
    built_in = tmp_path / "roles.toml"
    built_in.write_text('[roles.b]\ntitle = "b"\nupstream = ""\n', encoding="utf-8")
    monkeypatch.setattr(roles, "BUILT_IN", built_in)
    found = roles.load(None)
    assert found["b"]["upstream"] in roles.BATCHED and roles.denies_merges(found["b"])


def test_deny_merges_is_honoured_from_either_roles_file(tmp_path, monkeypatch):
    built_in = tmp_path / "roles.toml"
    built_in.write_text(f'[roles.b]\nupstream = "{ELSEWHERE}"\ndeny_merges = true\n', encoding="utf-8")
    monkeypatch.setattr(roles, "BUILT_IN", built_in)
    found = roles.load(None)
    assert found["b"]["deny_merges"] is True and roles.denies_merges(found["b"])


def test_merges_is_honoured_only_from_the_owners_own_roles_file(tmp_path, monkeypatch, caplog):
    built_in = tmp_path / "roles.toml"
    built_in.write_text('[roles.b]\nmerges = true\n[roles.m]\ntitle = "m"\n', encoding="utf-8")
    owners = tmp_path / "own.toml"
    owners.write_text("[roles.m]\nmerges = true\n", encoding="utf-8")
    monkeypatch.setattr(roles, "BUILT_IN", built_in)
    with caplog.at_level(logging.WARNING):
        found = roles.load(owners)
    assert "merges" not in found["b"] and found["m"]["merges"] is True
    assert "says it merges" in caplog.text


def test_the_merge_denies_are_kept_beside_the_files_own_denies(tmp_path, monkeypatch):
    folder = tmp_path / "roles"
    folder.mkdir()
    own = folder / "b.json"
    own.write_text(json.dumps({"permissions": {"deny": ["Bash(rm *)"], "allow": ["Read"]}}), encoding="utf-8")
    monkeypatch.setattr(roles, "SETTINGS_DIR", folder)
    import hashlib

    role = {"upstream": BATCHED, "settings": str(own), "settings_sha256": hashlib.sha256(own.read_bytes()).hexdigest()}
    merged = json.loads(roles.settings_for(role))
    assert merged["permissions"]["deny"] == ["Bash(rm *)", *roles.MERGE_DENIES]
    assert merged["permissions"]["allow"] == ["Read"]

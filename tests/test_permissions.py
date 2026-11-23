"""The permission rules for the voice repositories, merged into each one's local settings.

A Claude Code session in one of these repositories asks before every command it has
no rule for. `claude-voice permissions` gives each repository the same rules: what a
session may run without asking (looking at git, running the tests), and what it may
never run (deleting history, reading a secret). The owner's own rules are kept.
"""

import json
import os
from pathlib import Path, PurePosixPath

import pytest

from claude_voice import permissions, session_hook
from claude_voice.cli import main

RULES = permissions.Rules(allow=["Bash(git status*)", "Bash(uv run pytest*)"], deny=["Bash(git reset --hard*)"])


def settings_file(folder):
    return folder / ".claude" / "settings.local.json"


def with_settings(folder, content):
    file = settings_file(folder)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(content), encoding="utf-8")
    return file


def read(folder):
    return json.loads(settings_file(folder).read_text(encoding="utf-8"))


def test_rules_already_in_the_settings_are_kept(tmp_path):
    with_settings(
        tmp_path,
        {"model": "opus", "permissions": {"allow": ["Bash(make*)"], "deny": ["Bash(rm -rf*)"], "ask": ["Bash(x)"]}},
    )
    apply(tmp_path, RULES)
    got = read(tmp_path)
    assert got["model"] == "opus"
    assert got["permissions"]["allow"] == ["Bash(make*)", "Bash(git status*)", "Bash(uv run pytest*)"]
    assert got["permissions"]["deny"] == ["Bash(rm -rf*)", "Bash(git reset --hard*)"]
    assert got["permissions"]["ask"] == ["Bash(x)"]


def test_a_rule_the_settings_already_have_is_not_added_again(tmp_path):
    with_settings(tmp_path, {"permissions": {"allow": ["Bash(git status*)"]}})
    outcome = apply(tmp_path, RULES)
    got = read(tmp_path)["permissions"]
    assert got["allow"].count("Bash(git status*)") == 1
    assert outcome.added_allow == 1 and outcome.added_deny == 1


def test_a_second_run_writes_nothing(tmp_path):
    with_settings(tmp_path, {"permissions": {"allow": ["Bash(make*)"]}})
    apply(tmp_path, RULES)
    file = settings_file(tmp_path)
    before = (file.read_bytes(), os.stat(file).st_ino, os.stat(file).st_mtime_ns)
    outcome = apply(tmp_path, RULES)
    assert (file.read_bytes(), os.stat(file).st_ino, os.stat(file).st_mtime_ns) == before
    assert outcome.added_allow == 0 and outcome.added_deny == 0
    assert "already has every rule" in outcome.line()


def backups(tmp_path):
    """Where the tests keep backups: under a state folder, never in the repository."""
    return tmp_path / "state" / "settings-backups"


def apply(folder, rules, backup_root=None, **kwargs):
    return permissions.apply(folder, rules, backup_root=backup_root or backups(folder), **kwargs)


STAMP = 1794478500.0  # 2026-11-12 10:15:00 UTC


def test_the_old_file_is_kept_outside_the_repository_before_every_change(tmp_path):
    """A file left in the repository makes its checkout dirty, and plugins approve then refuses it."""
    repo = tmp_path / "garage-door"
    original = {"permissions": {"allow": ["Bash(make*)"]}}
    with_settings(repo, original)
    outcome = apply(repo, RULES, backups(tmp_path), clock=lambda: STAMP)
    kept = backups(tmp_path) / "garage-door" / "settings.local.json.20261112T101500Z"
    assert outcome.backup == kept and json.loads(kept.read_text(encoding="utf-8")) == original
    assert [p.name for p in (repo / ".claude").iterdir()] == ["settings.local.json"]
    assert f"kept the old file as {kept}" in outcome.line()


def test_an_older_backup_is_never_overwritten(tmp_path):
    original = {"permissions": {"allow": ["Bash(make*)"]}}
    with_settings(tmp_path, original)
    first = apply(tmp_path, RULES, backups(tmp_path), clock=lambda: STAMP)
    second = apply(tmp_path, permissions.Rules(allow=["Bash(ls*)"], deny=[]), backups(tmp_path), clock=lambda: STAMP)
    assert first.backup is not None and second.backup is not None and first.backup != second.backup
    assert json.loads(first.backup.read_text(encoding="utf-8")) == original
    after_first = {"permissions": {"allow": ["Bash(make*)", *RULES.allow], "deny": RULES.deny}}
    assert json.loads(second.backup.read_text(encoding="utf-8")) == after_first


def test_a_dry_run_writes_nothing(tmp_path):
    file = with_settings(tmp_path, {"permissions": {"allow": ["Bash(make*)"]}})
    before = file.read_bytes()
    outcome = apply(tmp_path, RULES, backups(tmp_path), dry_run=True)
    assert file.read_bytes() == before
    assert not backups(tmp_path).exists()
    assert outcome.added_allow == 2 and "would add 2 allow and 1 deny" in outcome.line()


RETIRING = permissions.Rules(allow=RULES.allow, deny=RULES.deny, retired=["Bash(cp * /tmp/*)"])


def test_a_retired_allow_rule_is_removed(tmp_path):
    """The old installer allowed a copy into /tmp, which got round the secret rules."""
    with_settings(tmp_path, {"permissions": {"allow": ["Bash(make*)", "Bash(cp * /tmp/*)"]}})
    outcome = apply(tmp_path, RETIRING)
    assert read(tmp_path)["permissions"]["allow"] == ["Bash(make*)", *RULES.allow]
    assert outcome.removed_allow == 1 and outcome.added_allow == 2
    assert "removed 1 retired allow rule" in outcome.line()


def test_only_the_exact_retired_rule_is_removed(tmp_path):
    near = ["Bash(cp * /tmp/x)", "Bash(cp *)", "Bash(cp * /tmp/*) ", "bash(cp * /tmp/*)"]
    with_settings(tmp_path, {"permissions": {"allow": near, "deny": ["Bash(cp * /tmp/*)"]}})
    outcome = apply(tmp_path, RETIRING)
    got = read(tmp_path)["permissions"]
    assert got["allow"][: len(near)] == near and "Bash(cp * /tmp/*)" in got["deny"]
    assert outcome.removed_allow == 0 and "retired allow" not in outcome.line()


def test_a_dry_run_says_how_many_retired_rules_it_would_remove(tmp_path):
    file = with_settings(tmp_path, {"permissions": {"allow": [*RULES.allow, "Bash(cp * /tmp/*)"], "deny": RULES.deny}})
    before = file.read_bytes()
    outcome = apply(tmp_path, RETIRING, dry_run=True)
    assert file.read_bytes() == before
    assert outcome.line() == f"{tmp_path}: would add 0 allow and 0 deny rules, and remove 1 retired allow rule"


def test_removing_a_retired_rule_keeps_the_backup_already_written(tmp_path):
    original = {"permissions": {"allow": ["Bash(make*)"]}}
    file = with_settings(tmp_path, original)
    first = apply(tmp_path, RULES, backups(tmp_path))
    settings = read(tmp_path)
    settings["permissions"]["allow"].append("Bash(cp * /tmp/*)")
    file.write_text(json.dumps(settings), encoding="utf-8")
    outcome = apply(tmp_path, RETIRING, backups(tmp_path))
    assert outcome.removed_allow == 1 and outcome.backup is not None
    assert first.backup is not None and json.loads(first.backup.read_text(encoding="utf-8")) == original


ASKING = permissions.Rules(
    allow=RULES.allow, deny=RULES.deny, ask=["Bash(rm -r*)", "Bash(gh pr close*)"], retired_ask=["Bash(old ask)"]
)


def test_ask_rules_are_added_after_the_owners_own_and_never_twice(tmp_path):
    with_settings(tmp_path, {"permissions": {"ask": ["Bash(make*)", "Bash(gh pr close*)"]}})
    outcome = apply(tmp_path, ASKING)
    assert read(tmp_path)["permissions"]["ask"] == ["Bash(make*)", "Bash(gh pr close*)", "Bash(rm -r*)"]
    assert outcome.added_ask == 1
    assert "added 2 allow, 1 ask and 1 deny rules" in outcome.line()


def test_a_missing_settings_file_gets_the_ask_rules_too(tmp_path):
    apply(tmp_path, ASKING)
    assert read(tmp_path)["permissions"]["ask"] == ASKING.ask


def test_a_retired_ask_rule_is_removed(tmp_path):
    with_settings(tmp_path, {"permissions": {"ask": ["Bash(old ask)", "Bash(old ask) "], "allow": ["Bash(old ask)"]}})
    outcome = apply(tmp_path, ASKING)
    got = read(tmp_path)["permissions"]
    assert got["ask"] == ["Bash(old ask) ", *ASKING.ask]
    assert got["allow"][0] == "Bash(old ask)"  # retired as an ask, not as an allow
    assert outcome.removed_ask == 1 and outcome.removed_allow == 0
    assert "removed 1 retired ask rule" in outcome.line()


def test_a_dry_run_says_how_many_retired_ask_rules_it_would_remove(tmp_path):
    settings = {"permissions": {"allow": RULES.allow, "deny": RULES.deny, "ask": [*ASKING.ask, "Bash(old ask)"]}}
    with_settings(tmp_path, settings)
    outcome = apply(tmp_path, ASKING, dry_run=True)
    assert outcome.line() == f"{tmp_path}: would add 0 allow and 0 deny rules, and remove 1 retired ask rule"


def test_a_retired_allow_or_ask_rule_is_removed(tmp_path):
    """Each from its own list, and counted apart."""
    rules = permissions.Rules(allow=[], deny=[], retired=["Bash(a)"], retired_ask=["Bash(b)", "Bash(c)"])
    with_settings(tmp_path, {"permissions": {"allow": ["Bash(a)"], "ask": ["Bash(b)", "Bash(c)"]}})
    outcome = apply(tmp_path, rules)
    assert read(tmp_path)["permissions"] == {"allow": [], "ask": []}
    assert "and removed 1 retired allow rule and 2 retired ask rules;" in outcome.line()


def test_a_second_run_with_ask_rules_writes_nothing(tmp_path):
    apply(tmp_path, ASKING)
    outcome = apply(tmp_path, ASKING)
    assert outcome.added_ask == 0 and "already has every rule" in outcome.line()


def test_a_dry_run_does_not_create_a_missing_settings_file(tmp_path):
    apply(tmp_path, RULES, dry_run=True)
    assert not (tmp_path / ".claude").exists()


def test_a_missing_settings_file_is_created_with_the_rules(tmp_path):
    outcome = apply(tmp_path, RULES, backups(tmp_path))
    assert read(tmp_path) == {"permissions": {"allow": RULES.allow, "deny": RULES.deny}}
    assert outcome.created and outcome.backup is None
    assert not backups(tmp_path).exists()


def test_a_settings_file_that_is_not_json_is_left_alone(tmp_path):
    file = settings_file(tmp_path)
    file.parent.mkdir()
    file.write_text("{ not json", encoding="utf-8")
    with pytest.raises(permissions.PermissionsError, match="not JSON"):
        apply(tmp_path, RULES)
    assert file.read_text(encoding="utf-8") == "{ not json"


def test_a_folder_that_does_not_exist_is_refused(tmp_path):
    with pytest.raises(permissions.PermissionsError, match="not a folder"):
        apply(tmp_path / "nowhere", RULES)


# -- the rules this repository ships ---------------------------------------------------


def test_the_shipped_rules_have_no_duplicates_and_nothing_both_allowed_and_denied():
    rules = permissions.load()
    for group in (rules.allow, rules.ask, rules.deny):
        assert len(group) == len(set(group))
    assert not set(rules.allow) & set(rules.deny)
    assert not set(rules.allow) & set(rules.ask) and not set(rules.ask) & set(rules.deny)
    assert "Bash(git status*)" in rules.allow and "Bash(git reset --hard*)" in rules.deny


def test_every_command_allowed_to_read_a_file_is_refused_every_secret_file():
    """A secret is protected only if no allowed reader can reach it by its path."""
    rules = permissions.load()
    table = json.loads(permissions.TABLE.read_text(encoding="utf-8"))
    readers, files = table["secrets"]["readers"], table["secrets"]["files"]
    assert "*.config/claude-voice/env*" in files
    allowed = [reader for reader in readers if f"Bash({reader} *)" in rules.allow or f"Bash({reader}*)" in rules.allow]
    assert {"cat", "head", "tail", "grep"} <= set(allowed)
    for reader in readers:
        for path in files:
            assert f"Bash({reader} {path})" in rules.deny


def test_copying_or_printing_a_secret_file_is_refused():
    """A copy puts the secret where no rule guards it; these print a file's bytes."""
    rules = permissions.load()
    for reader in ("cp", "rsync", "base64", "xxd", "od"):
        for path in ("*.config/claude-voice/env*", "*secrets.yaml*", "*.claude/.credentials.json*"):
            assert f"Bash({reader} {path})" in rules.deny


def test_the_copy_into_tmp_the_old_installer_allowed_is_retired():
    rules = permissions.load()
    assert "Bash(cp * /tmp/*)" in rules.retired
    assert not set(rules.retired) & set(rules.allow)
    assert not set(rules.retired_ask) & set(rules.ask)


def test_one_commit_rule_replaces_the_message_and_file_pair():
    rules = permissions.load()
    assert {"Bash(git commit -m *)", "Bash(git commit -F *)"} <= set(rules.retired)
    assert "Bash(git commit *)" in rules.allow


#: The rules the first hand-run installer refused. The shipped rules may refuse more, never less:
#: dropping one is a permission change the owner decides, not a side effect of moving the rules.
INSTALLED_BEFORE = (
    "Bash(cat *.config/claude-voice/env*)",
    "Bash(cat *.config/dreame*)",
    "Bash(cat *.config/tv-mcp*)",
    "Bash(cat *secrets.yaml*)",
    "Bash(git branch * -D*)",
    "Bash(git branch * -d*)",
    "Bash(git branch --delete*)",
    "Bash(git branch -D*)",
    "Bash(git branch -d*)",
    "Bash(git branch -f*)",
    "Bash(git checkout -- *)",
    "Bash(git clean -f*)",
    "Bash(git reset --hard*)",
    "Bash(git worktree remove* -f*)",
    "Bash(git worktree remove*--force*)",
    "Bash(grep *.config/claude-voice/env*)",
    "Bash(grep *.config/dreame*)",
    "Bash(grep *.config/tv-mcp*)",
    "Bash(grep *secrets.yaml*)",
    "Bash(head *.config/dreame*)",
    "Bash(head *.config/tv-mcp*)",
    "Bash(head *secrets.yaml*)",
    "Bash(sed -n *.config/*)",
    "Bash(sed -n *secrets*)",
    "Bash(tail *.config/dreame*)",
    "Bash(tail *.config/tv-mcp*)",
    "Bash(tail *secrets.yaml*)",
)


def test_everything_refused_before_is_still_refused():
    rules = permissions.load()
    assert [rule for rule in INSTALLED_BEFORE if rule not in rules.deny] == []


def test_the_shipped_rules_name_no_person_host_or_home_folder():
    text = permissions.TABLE.read_text(encoding="utf-8")
    # The rules about removing folders name everyone's home and state folders the same way and
    # no person's, so those exact rules are taken out before looking for anything private.
    raw = json.loads(text)
    owner = raw["what later runs as the owner"]
    generic = raw["what cannot be got back"]["targets"] + owner["home"]
    assert not any(p.startswith(("/", "~")) for p in owner["home"])  # relative to any home folder
    for rule in generic:
        text = text.replace(json.dumps(rule), "")
    for private in ("/home/", "/users/", "~/", "roykollensvendsen", "192.168.", "100.", ".local/"):
        assert private not in text.lower(), private


# -- forced pushes: a session's own branch, never main ---------------------------------
#
# Claude Code does the matching; `permissions.matches` is the one faithful copy of it, used
# here and by the dry run's "what decides" line (ask-waivers v1.1 f2).

_matches = permissions.matches
decision = permissions.decision


def test_the_matcher_reads_rules_the_way_claude_code_does():
    rules = permissions.Rules(allow=["Bash(git status*)", "Bash(ls)"], deny=["Bash(git status --x*)"])
    assert decision(rules, "git status") == "allowed"
    assert decision(rules, "git status --short") == "allowed"
    assert decision(rules, "git status --x y") == "refused"
    assert decision(rules, "ls -la") == "asked"
    asking = permissions.Rules(
        allow=["Bash(git push origin *)"], deny=["Bash(git push* -f*)"], ask=["Bash(git push* main)"]
    )
    assert decision(asking, "git push origin feature") == "allowed"
    assert decision(asking, "git push origin main") == "asked"
    assert decision(asking, "git push origin main -f") == "refused"
    prefixed = permissions.Rules(allow=["Bash(npm test:*)"], deny=["Bash(git push* :*)"])
    assert decision(prefixed, "npm test --watch") == "allowed"
    assert decision(prefixed, "git push origin :feature") == "asked"  # the old form never applied
    quoted = permissions.Rules(allow=["Bash(git push origin *)"], deny=["Bash(git push* :**)"])
    assert decision(quoted, "git push origin ':feature'") == "refused"
    assert decision(quoted, 'git push origin ":feature"') == "refused"


def test_a_rule_ending_in_a_colon_and_a_star_is_refused(tmp_path):
    table = json.loads(permissions.TABLE.read_text(encoding="utf-8"))
    table["ask"]["pushes that delete a branch or could land on main without naming it"].append("Bash(git push* :*)")
    broken = tmp_path / "permissions.json"
    broken.write_text(json.dumps(table), encoding="utf-8")
    with pytest.raises(permissions.PermissionsError, match=r"git push\* :\*"):
        permissions.load(broken)
    permissions.load()  # the shipped table has none


def test_the_ask_rule_that_never_applied_is_taken_out_of_the_settings(tmp_path):
    with_settings(tmp_path, {"permissions": {"ask": ["Bash(git push* :*)"]}})
    apply(tmp_path, permissions.load())
    asked = read(tmp_path)["permissions"]["ask"]
    assert "Bash(git push* :*)" not in asked and "Bash(git push* :**)" in asked


APPROVALS_IN_THE_OWNERS_NAME = (
    "gh pr review 5 --approve",
    "gh pr review --approve 5",
    "gh pr review 5 --approve --body fine",
    "gh pr review 5 -a",
    "gh pr review -a 5",
    "gh api repos/o/r/pulls/5/reviews -f event=APPROVE",
    "gh api repos/o/r/pulls/5/reviews -F event=APPROVE",
    "gh api repos/o/r/pulls/5/reviews --field event=APPROVE",
    "gh api repos/o/r/pulls/5/reviews --raw-field event=APPROVE",
    "gh api -X POST repos/o/r/pulls/5/reviews -f event=APPROVE",
    "gh api graphql -f query='mutation { addPullRequestReview(input: {event: APPROVE}) { clientMutationId } }'",
)


@pytest.mark.parametrize("command", APPROVALS_IN_THE_OWNERS_NAME)
def test_a_session_can_never_approve_a_pull_request(command):
    """Sessions act on GitHub as the owner, so an approval would be public in his name."""
    assert decision(permissions.load(), command) == "refused"


OWN_BRANCH_PUSHES = (
    "git push --force-with-lease origin feature",
    "git push --force-with-lease origin fix/the-pager",
    "git push --force-with-lease -u origin feature",
    "git push --force-with-lease origin maintenance",
    "gh pr update-branch 42 --rebase",
)

PUSHES_TO_A_PROTECTED_BRANCH = tuple(
    form.format(branch=branch)
    for branch in ("main", "master")
    for form in (
        "git push --force-with-lease origin {branch}",
        "git push --force-with-lease -u origin {branch}",
        "git push --force-with-lease origin HEAD:{branch}",
        "git push --force-with-lease origin +{branch}",
        "git push --force-with-lease origin feature:{branch}",
        "git push --force-with-lease origin feature:refs/heads/{branch}",
        "git push --force-with-lease origin feature {branch}",
        "git push --force-with-lease origin {branch} --no-verify",
        "git push --force-with-lease={branch} origin {branch}",
        "git push --force-with-lease={branch}:abc123 origin feature",
    )
)

PUSHES_THAT_CHECK_NOTHING = (
    "git push --force origin feature",
    "git push --force",
    "git push -f origin feature",
    "git push -fu origin feature",
    "git push origin feature --force",
    "git push origin feature -f",
    "git push --force-with-lease origin feature --force",
    "git push --force-with-lease origin",
    "git push --force-with-lease origin HEAD",
    "git push --force-with-lease origin :feature",
    "git push --force-with-lease origin ':feature'",
    'git push --force-with-lease origin ":feature"',
    "git push origin +:feature",
    "git push --force-with-lease origin +:feature",
    "git push --force-with-lease origin --delete feature",
    "git push --force-with-lease origin --all",
    "git push --force-with-lease origin --mirror",
    "git push origin +feature",
    "git push -u origin +feature",
    "git push origin feature -f",
    "git push origin feature -fu",
    "git push -q -u origin feature -uf",
    "git push origin feature -qf",
    "git push origin feature --force",
    "git push -u origin feature --force",
    "git push origin --mirror",
    "git push origin --force-with-lease main",
    "git push origin feature --force-with-lease=main",
    "git push -u origin --force-with-lease HEAD:master",
)


@pytest.mark.parametrize("command", OWN_BRANCH_PUSHES)
def test_a_session_may_force_push_its_own_branch_with_a_lease(command):
    assert decision(permissions.load(), command) == "allowed"


@pytest.mark.parametrize("command", PUSHES_TO_A_PROTECTED_BRANCH)
def test_a_forced_push_can_never_reach_main_or_master(command):
    assert decision(permissions.load(), command) == "refused"


@pytest.mark.parametrize("command", PUSHES_THAT_CHECK_NOTHING)
def test_a_plain_forced_push_or_one_that_could_land_anywhere_is_refused(command):
    assert decision(permissions.load(), command) == "refused"


# -- what a session may do without asking, and what it must ask first -----------------

NEWLY_ALLOWED = (
    "uv run --quiet pytest -q",
    "uv run --quiet ruff check .",
    "uv run --quiet mypy src",
    "npm run typecheck",
    "git commit -q -F message.txt",
    "git commit --amend --no-edit",
    "git switch feature",
    "git switch -c feature",
    "git rebase origin/main",
    "git rebase --continue",
    "git push -u origin feature",
    "git push -q -u origin fix/the-pager",
    "git push origin feature",
    "git push origin maintenance",
    "gh run watch 123 --exit-status",
    "gh pr status",
    "gh issue view 7",
    "gh issue list --state open",
    "gh repo view",
    "gh release view v1.0",
    "gh release list",
)

PLAIN_PUSHES_TO_A_PROTECTED_BRANCH = tuple(
    form.format(branch=branch)
    for branch in ("main", "master")
    for form in (
        "git push origin {branch}",
        "git push -u origin {branch}",
        "git push -q -u origin {branch}",
        "git push origin {branch} --tags",
        "git push origin HEAD:{branch}",
        "git push origin feature:refs/heads/{branch}",
    )
)

NOW_ASKED = (
    "git push origin --delete feature",
    "git push origin -d feature",
    "git push origin :feature",
    "git push origin HEAD",
    "git push -u origin HEAD",
    "git push origin --all",
    "git push origin feature --force-with-lease",
    "gh pr comment 5 --body hi",
    "gh pr close 5",
    "gh pr review 5 --comment --body looks-fine",
    "gh pr review 5 --request-changes --body fix-this",
    "git push -u origin :feature",
    "git push origin ':feature'",
    'git push origin ":feature"',
    "gh api repos/o/r/pulls/5/reviews -f event=COMMENT",
    "gh issue create --title x",
    "gh issue comment 7 --body x",
    "gh release create v2",
    "gh release edit v2",
    "gh repo edit --visibility public",
    "gh secret set TOKEN",
    "gh api graphql -f query=x",
    "gh api repos/o/r/pulls -X POST",
    "gh api repos/o/r/pulls --method PATCH",
    "gh api repos/o/r/issues -f title=x",
    "gh api repos/o/r/issues -F n=1",
    "gh api repos/o/r/issues --field title=x",
    "gh api repos/o/r/issues --raw-field title=x",
    "gh api repos/o/r/issues --input body.json",
    "rm -rf build",
    "rm -r build",
    "rm -f notes.txt",
)


@pytest.mark.parametrize("command", NEWLY_ALLOWED)
def test_a_session_may_run_these_without_asking(command):
    assert decision(permissions.load(), command) == "allowed"


@pytest.mark.parametrize("command", PLAIN_PUSHES_TO_A_PROTECTED_BRANCH)
def test_any_other_push_to_main_or_master_is_refused(command):
    assert decision(permissions.load(), command) == "refused"


FAST_FORWARD_RULES = (
    "Bash(git fetch origin)",
    "Bash(git fetch origin main)",
    "Bash(git pull --ff-only origin main)",
    "Bash(git pull --ff-only)",
    "Bash(git merge --ff-only origin/main)",
)


def test_a_session_may_bring_main_up_to_date_by_fast_forward_only():
    """The owner's decision: local main may follow GitHub, but never be moved or overwritten."""
    rules = permissions.load()
    assert [rule for rule in FAST_FORWARD_RULES if rule not in rules.allow] == []
    for command in ("git pull --ff-only origin main", "git merge --ff-only origin/main", "git fetch origin main"):
        assert decision(rules, command) == "allowed", command
    assert "Bash(git branch -f*)" in rules.deny
    assert "Bash(git reset --hard*)" in rules.deny
    for command in ("git branch -f main origin/main", "git reset --hard origin/main", "git push origin main"):
        assert decision(rules, command) == "refused", command


@pytest.mark.parametrize("command", NOW_ASKED)
def test_a_session_must_ask_before_these(command):
    assert decision(permissions.load(), command) == "asked"


def test_merging_a_sessions_own_green_pull_request_is_not_an_ask():
    """The owner's policy: a session merges its own pull request once every check is green."""
    rules = permissions.load()
    assert not any(_matches(rule, "gh pr merge 5 --rebase") for rule in rules.ask)


@pytest.mark.parametrize(
    "command",
    ["git switch -f main", "git switch --discard-changes main", "git switch -C feature", "git switch main --force"],
)
def test_switching_branch_in_a_way_that_throws_work_away_is_refused(command):
    assert decision(permissions.load(), command) == "refused"


def test_files_in_the_repository_may_be_edited_but_its_claude_settings_ask_first():
    rules = permissions.load()
    assert "Edit(./**)" in rules.allow and "Edit(./.claude/**)" in rules.ask
    assert not any(rule.startswith("Write(") for rule in rules.allow)


#: The tools that move something in the house, by the names the bridge gives its plugins' tools.
DEVICE_ACTIONS = (
    "garage_request",
    "garage_confirm",
    "garage_switch",
    "garage_press_button",
    "tv_power_request",
    "tv_power_confirm",
    "vacuum_start",
    "vacuum_stop",
    "vacuum_pause",
    "vacuum_go_home",
    "vacuum_find",
    "vacuum_clean_rooms",
)


@pytest.mark.parametrize("server", ["claude-voice", "claude_ai_claude-voice"])
def test_a_tool_that_moves_something_in_the_house_asks_first_by_every_name_it_is_seen_under(server):
    """Added with `claude mcp add` the bridge is `claude-voice`; as a claude.ai connector it is the other."""
    asks = permissions.load().ask
    assert [tool for tool in DEVICE_ACTIONS if f"mcp__{server}__{tool}" not in asks] == []


def test_the_garage_doors_own_server_asks_first_too():
    asks = permissions.load().ask
    for tool in ("garage_request", "garage_confirm", "garage_switch", "garage_press_button"):
        assert f"mcp__garage-door__{tool}" in asks


def test_looking_at_a_device_is_not_an_ask():
    asks = permissions.load().ask
    assert [rule for rule in asks if rule.endswith(("_status", "__vacuum_where", "__vacuum_rooms"))] == []


def test_sending_mail_asks_first():
    asks = permissions.load().ask
    for tool in ("send_message", "reply", "forward"):
        assert f"mcp__claude_ai_Gmail__{tool}" in asks


#: The bridge's two names, as a session sees it: added with `claude mcp add`, or as a connector.
BRIDGE_NAMES = ("claude-voice", "claude_ai_claude-voice")


@pytest.mark.parametrize("server", BRIDGE_NAMES)
def test_a_confirm_only_the_voice_may_call_is_refused_to_every_session_by_every_name(server):
    """The phone's confirm is offered as phone-a_sms_send_confirm; no session may call it."""
    rules = permissions.load()
    assert f"mcp__{server}__phone-a_sms_send_confirm" in rules.deny
    assert f"mcp__{server}__phone-a_sms_send_confirm" not in rules.ask + rules.allow


@pytest.mark.parametrize("server", BRIDGE_NAMES)
@pytest.mark.parametrize("tool", ["phone-a_sms_send_request", "phone-a_sms_send_cancel"])
def test_drafting_a_text_or_stopping_it_is_never_denied(server, tool):
    """A draft sends nothing, and a stop can only keep a text from going."""
    assert f"mcp__{server}__{tool}" not in permissions.load().deny


def test_a_second_phone_or_a_renamed_one_has_its_confirm_refused_too():
    """The table lists the plain tool; each machine the bridge knows gets its own deny row."""
    deny = permissions.load(machines=["phone-b"]).deny
    for server in BRIDGE_NAMES:
        assert f"mcp__{server}__phone-b_sms_send_confirm" in deny
        assert f"mcp__{server}__phone-a_sms_send_confirm" in deny, "the table's own machine stays"


def test_every_machine_in_the_settings_has_its_voice_only_confirms_refused(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    file = tmp_path / "config.toml"
    file.write_text("[machines.phone-b]\naddress = '100.64.0.9'\n", encoding="utf-8")
    assert main(["permissions", str(repo)], env={"HOME": str(tmp_path), "CLAUDE_VOICE_CONFIG": str(file)}) == 0
    assert "mcp__claude-voice__phone-b_sms_send_confirm" in read(repo)["permissions"]["deny"]


def test_a_machine_the_table_does_not_name_is_told_to_re_apply_the_permissions(tmp_path, capsys):
    file = tmp_path / "config.toml"
    file.write_text("", encoding="utf-8")
    env = {"HOME": str(tmp_path), "CLAUDE_VOICE_CONFIG": str(file)}
    assert main(["machines", "add", "phone-b", "100.64.0.9"], env=env) == 0
    out = capsys.readouterr().out
    assert "phone-b_sms_send_confirm" in out and " permissions" in out
    assert permissions.reapply_note("phone-a") is None, "a machine the table names needs nothing"


# -- which folders, and the command ----------------------------------------------------


def config_naming(tmp_path, *folders, plugin=None):
    # Literal TOML strings, so a Windows path's backslashes are not read as escapes.
    lines = ["[paths]", f"claude_voice = '{folders[0]}'", f"claude_voice_ui = '{folders[1]}'"]
    if len(folders) > 2:
        lines.append(f"claude_voice_mobile = '{folders[2]}'")
    if plugin:
        lines += ["[plugins.garage]", 'command = "uv"', f"cwd = '{plugin}'", 'tools = ["garage_status"]']
    file = tmp_path / "config.toml"
    file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"HOME": str(tmp_path), "CLAUDE_VOICE_CONFIG": str(file)}


def test_with_no_folders_named_the_bridge_and_plugin_clones_from_the_settings_are_used(tmp_path, capsys):
    voice, ui, garage = tmp_path / "voice", tmp_path / "ui", tmp_path / "garage"
    for folder in (voice, garage):
        folder.mkdir()
    env = config_naming(tmp_path, voice, ui, plugin=garage)
    assert main(["permissions"], env=env) == 0
    assert "Bash(git status*)" in read(voice)["permissions"]["allow"]
    assert "Bash(git status*)" in read(garage)["permissions"]["allow"]
    assert not ui.exists()  # a clone that is not there is skipped, not made
    out = capsys.readouterr().out
    assert str(voice.resolve()) in out and str(garage.resolve()) in out


def test_with_no_folders_named_the_phone_app_clone_next_to_the_bridge_is_used(tmp_path, capsys):
    voice, mobile = tmp_path / "claude-voice", tmp_path / "claude-voice-mobile"
    for folder in (voice, mobile):
        folder.mkdir()
    file = tmp_path / "config.toml"
    file.write_text(f"[paths]\nclaude_voice = '{voice}'\n", encoding="utf-8")
    assert main(["permissions"], env={"HOME": str(tmp_path), "CLAUDE_VOICE_CONFIG": str(file)}) == 0
    assert "Bash(git status*)" in read(mobile)["permissions"]["allow"]
    assert str(mobile.resolve()) in capsys.readouterr().out


def test_the_phone_app_clone_can_be_named_in_the_settings(tmp_path):
    voice, mobile = tmp_path / "voice", tmp_path / "elsewhere" / "phone"
    for folder in (voice, mobile):
        folder.mkdir(parents=True)
    env = config_naming(tmp_path, voice, tmp_path / "ui", mobile)
    assert main(["permissions"], env=env) == 0
    assert "Bash(git status*)" in read(mobile)["permissions"]["allow"]


def test_the_command_keeps_the_old_file_under_the_home_state_folder(tmp_path, capsys):
    repo = tmp_path / "garage-door"
    with_settings(repo, {"permissions": {"allow": ["Bash(make*)"]}})
    assert main(["permissions", str(repo)], env={"HOME": str(tmp_path)}) == 0
    kept = list((tmp_path / ".local" / "state" / "claude-voice" / "settings-backups" / "garage-door").iterdir())
    assert len(kept) == 1 and kept[0].name.startswith("settings.local.json.")
    assert str(kept[0]) in capsys.readouterr().out


def test_the_command_takes_folders_and_a_dry_run(tmp_path, capsys):
    assert main(["permissions", "--dry-run", str(tmp_path)], env={"HOME": str(tmp_path)}) == 0
    assert not (tmp_path / ".claude").exists()
    assert f"{tmp_path}: would add" in capsys.readouterr().out


def test_the_command_says_which_folder_it_could_not_change(tmp_path, capsys):
    missing = tmp_path / "nowhere"
    assert main(["permissions", str(missing), str(tmp_path)], env={"HOME": str(tmp_path)}) == 1
    captured = capsys.readouterr()
    assert f"{missing} is not a folder" in captured.err
    assert f"{tmp_path}: added" in captured.out


def test_with_no_folders_named_and_none_in_the_settings_it_says_how_to_name_them(tmp_path, capsys):
    env = config_naming(tmp_path, tmp_path / "gone", tmp_path / "gone-too")
    assert main(["permissions"], env=env) == 1
    assert "name the folders" in capsys.readouterr().err


# -- removing folders: every recursive remove asks, and what can't be got back is refused --


def for_a_folder():
    """The shipped rules as written for one repository, with its placeholders filled. The paths
    are fixed POSIX ones, so the rows read the same on every system the suite runs on."""
    home = PurePosixPath("/home/u")
    folder = home / "voice-repo"
    return permissions.for_folder(permissions.load(), folder, home=home, uid=1000), home, folder


def test_the_placeholders_are_filled_in_for_each_repository():
    rules, home, folder = for_a_folder()
    every = rules.allow + rules.ask + rules.deny
    assert not [rule for rule in every if any(p in rule for p in ("{uid}", "{home}", "{repo}", "{folder}"))]
    assert f"Bash(rm -r* {home})" in rules.deny and f"Bash(rm -r* {folder})" in rules.deny


def test_removing_what_cannot_be_got_back_is_refused():
    rules, home, folder = for_a_folder()
    for command in (
        "rm -r ~",
        "rm -rf ~/",
        "rm -rf /",
        "rm -rf $HOME",
        f"rm -rf {home}",
        f"rm -rf {folder}",
        f"rm -rf {folder}/",
        "rm -rf .",
        "rm -r .git",
        "rm -rf sub/.git",
        "rm -R ~/.claude/projects",
        f"rm --recursive {home}/.config/claude-voice",
        "rm -fr ~/.local/state/claude-voice",
    ):
        assert decision(rules, command) == "refused", command


def test_every_other_recursive_remove_still_asks_first():
    """No recursive remove runs unasked; the deny rules only make some of them a plain no."""
    rules, home, folder = for_a_folder()
    for command in (
        "rm -rf ~/*",
        "rm -rf ${HOME}",
        f"rm -rf {home}/*",
        "rm -rf /home",
        "rm -rf /*",
        "rm -rf *",
        "rm -rf ./*",
        f"rm -rf {folder}/*",
        "rm -rf ~/.ssh",
        "rm -rf /tmp/claude-1000/x",
        "rm -rf .claude/worktrees/x",
        "rm -rf src /tmp/claude-1000/x",
        "rm -r /var/tmp/x",
    ):
        refused = any(_matches(rule, command) for rule in rules.deny)
        said = session_hook.decision(
            {"tool_name": "Bash", "session_id": "0" * 8, "cwd": str(folder), "tool_input": {"command": command}},
            tmp_root=Path("/tmp/claude-1000"),
            repos=[],
            terms=None,
        )
        assert refused or said == "ask", command


def test_a_remove_or_an_issue_is_no_longer_asked_by_a_rule_since_the_session_hook_asks():
    rules, _, _ = for_a_folder()
    for rule in ("Bash(rm -r*)", "Bash(gh issue create*)"):
        assert rule not in rules.ask and rule in rules.retired_ask, rule


def test_applied_settings_have_the_placeholders_filled(tmp_path):
    folder = tmp_path / "voice-repo"
    folder.mkdir()
    apply(folder, permissions.load())
    written = read(folder)["permissions"]
    every = written["allow"] + written["ask"] + written["deny"]
    assert not [rule for rule in every if "{" in rule and "}" in rule]
    assert f"Bash(rm -r* {folder.resolve()})" in written["deny"]


@pytest.mark.parametrize(
    "command",
    [
        "pkill -f claude",
        "pkill sleep",
        "pkill",
        "killall python3",
        "killall -9 node",
        "pgrep -f claude | xargs kill",
        "pgrep -f x | xargs kill -9",
        "pgrep x | xargs -r kill",
    ],
)
def test_no_session_stops_processes_by_name(command):
    """Claude Code judges each part of a pipe on its own, so the part that kills is refused."""
    rules = permissions.load()
    assert any(decision(rules, part.strip()) == "refused" for part in command.split("|")), command


@pytest.mark.parametrize("command", ["kill 12345", "kill -TERM 12345", "pgrep -f claude"])
def test_stopping_one_process_by_its_number_is_not_refused(command):
    assert decision(permissions.load(), command) != "refused", command


def test_the_by_name_kill_rules_reach_every_repo_without_the_owner_typing():
    """The update writes a rule by itself only when it takes rights away and is not broad (#326)."""
    from claude_voice import shipped

    raw = json.loads(Path(permissions.__file__).with_name("permissions.json").read_text(encoding="utf-8"))
    for rule in raw["deny"]["stopping processes by name, which ends other sessions' work too"]:
        assert not shipped.broad(rule), rule


# -- allow rules narrowed to the forms that are used --------------------------------------

#: Allow rules that matched more than the everyday form, each now taken out of every clone.
NARROWED = (
    "Bash(git -C * status*)",
    "Bash(git -C * diff*)",
    "Bash(git -C * log*)",
    "Bash(git -C * show*)",
    "Bash(git -C * ls-files*)",
    "Bash(git -C * rev-parse*)",
    "Bash(git -C * remote -v)",
    "Bash(git -C * branch --show-current)",
    "Bash(git -C * branch -a*)",
    "Bash(git -C * branch -r*)",
    "Bash(git -C * fetch*)",
    "Bash(git -C * worktree list*)",
    "Bash(git -C * add *)",
    "Bash(git -C * commit *)",
    "Bash(git -C * cherry-pick *)",
    "Bash(git -C * worktree add *)",
    "Bash(git fetch*)",
    "Bash(git worktree add *)",
    "Bash(uv sync*)",
    "Bash(sed -n *)",
)


@pytest.mark.parametrize("rule", NARROWED)
def test_a_narrowed_allow_rule_is_retired(rule):
    rules = permissions.load()
    assert rule not in rules.allow and rule in rules.retired


def test_a_settings_file_loses_the_narrowed_rules_and_keeps_the_owners_own(tmp_path):
    with_settings(tmp_path, {"permissions": {"allow": ["Bash(make)", *NARROWED]}})
    apply(tmp_path, permissions.load())
    allowed = read(tmp_path)["permissions"]["allow"]
    assert "Bash(make)" in allowed
    assert [rule for rule in NARROWED if rule in allowed] == []


EXACT_FORMS = (
    "git fetch",
    "git fetch origin",
    "git fetch origin main",
    "uv sync",
    "uv sync --locked",
    "uv sync --frozen",
    "uv sync --all-extras",
    "uv sync --locked --all-extras",
    "uv sync --frozen --all-extras",
)


@pytest.mark.parametrize("command", EXACT_FORMS)
def test_the_everyday_fetch_and_sync_forms_are_still_allowed(command):
    assert decision(permissions.load(), command) == "allowed"


NO_LONGER_ALLOWED = (
    "git -C ../ui status",
    "git -C ../ui add src/app.ts",
    "git -C ../ui commit -m fix",
    "git fetch --all",
    "git fetch origin --prune",
    "git worktree add ../elsewhere",
    "uv sync --python 3.12",
    "uv sync --frozen --no-dev",
    "sed -n 1p notes.txt",
)


@pytest.mark.parametrize("command", NO_LONGER_ALLOWED)
def test_other_forms_of_these_commands_ask_first(command):
    assert decision(permissions.load(), command) == "asked"


def test_no_git_or_sync_allow_rule_has_a_star_before_its_last_word():
    """A star in the middle lets any option in before the words that follow it."""
    wide = []
    for rule in permissions.load().allow:
        if rule.startswith(("Bash(git ", "Bash(uv sync")):
            words = rule[len("Bash(") : -1].split(" ")
            if any("*" in word for word in words[:-1]):
                wide.append(rule)
    assert wide == []


@pytest.mark.parametrize("command", ["git diff --output=x", "git log --output x", "git show HEAD --output=x"])
def test_git_writing_its_output_to_a_file_is_refused(command):
    rules = permissions.load()
    assert "Bash(git * --output*)" in rules.deny
    assert decision(rules, command) == "refused"


def test_gits_own_files_are_never_edited_anywhere():
    rules = permissions.load()
    assert {"Edit(//**/.git)", "Edit(//**/.git/**)"} <= set(rules.deny)


def test_the_owners_git_settings_are_still_refused_to_every_edit():
    rules = permissions.load()
    for path in (".gitconfig", ".config/git/**"):
        assert f"Edit(~/{path})" in rules.deny, path


def _for_home():
    """Fixed POSIX paths, as in for_a_folder, so the rule reads the same on every system."""
    home = PurePosixPath("/h")
    return permissions.for_folder(permissions.load(), home / "repo", home=home, uid=1000)


@pytest.mark.parametrize(
    "command",
    [
        "git worktree add /h/claude-voice-worktrees/fix-x -b fix/x origin/main",
        "git worktree add /h/claude-voice-worktrees/fix-x fix/x",
    ],
)
def test_a_new_worktree_in_the_shared_worktrees_folder_is_allowed(command):
    assert decision(_for_home(), command) == "allowed"


@pytest.mark.parametrize(
    "command",
    [
        "git worktree add /h/elsewhere -b fix/x",
        "git worktree add /h/claude-voice-worktrees-other/x",
        "git worktree add /h/claude-voice-worktrees/../.claude/x",
        "git worktree add ../claude-voice-worktrees/x",
    ],
)
def test_a_new_worktree_anywhere_else_asks_first(command):
    assert decision(_for_home(), command) == "asked"

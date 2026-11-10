---
name: review-contribution
description: Take a pull request from someone outside the project from first read to a thank-you - read it, let its checks run, run its code only in a sandbox, review it as it is, merge it by rebase, thank the contributor for what was good, and record what it taught us. Use whenever a pull request comes from a fork or an outside author, when its checks wait for approval, when it conflicts with main, or when it has just been merged.
---

# Reviewing a contribution from outside

Someone gave their time to this project. The review decides whether their work
lands, and how they are treated decides whether they come back. Both matter.
The rules are in CONTRIBUTING, "When someone else's change comes in"; this is
the order to do them in.

## 1. Read it before anything runs

- Read the description, then the whole diff: `gh pr view N`, `gh pr diff N`.
- Look hardest at what runs before any test does: `.github/workflows/`,
  `pyproject.toml`, `uv.lock`, `scripts/`, and anything a hook or the build
  calls. A change there can run code on our machines or in our checks.
- Check that it carries nothing private, and no secret.
- Note who else's lines it changes, so they can be mentioned.

## 2. Let the checks run

A first-time contributor's checks wait for approval. Approve them once the diff
is read and nothing in step 1 worried you. The owner of the repository decides
this; ask them if they have not said so already:

<!-- not run: it approves real runs on GitHub -->
```
gh run list --branch BRANCH --status action_required --json databaseId -q '.[].databaseId'
gh api -X POST repos/OWNER/REPO/actions/runs/ID/approve
```

## 3. Run their code only in the sandbox

Never run a contributor's tests, scripts or hooks with your own home folder,
keys or network in reach. Clone into a scratch folder, install outside (this
needs the network, and runs the build only, so read `pyproject.toml` first),
then run everything else inside:

<!-- not run: it clones and runs a stranger's pull request -->
```
git clone -q https://github.com/OWNER/REPO.git /scratch/pr-N && cd /scratch/pr-N
gh pr checkout N
uv sync --frozen --all-extras
.claude/skills/review-contribution/sandboxed.sh . uv run pytest -q
```

`sandboxed.sh` (beside this file) uses bubblewrap: no network, no home folder,
none of your environment, and the clone is the only place it may write.
Continuous integration is the other safe place; often it is enough.

## 4. Review it as it is

- Questions and wishes go in a comment on the pull request. Say what blocks and
  what is only a suggestion, and keep it short.
- Never push your own commits to their branch. Your improvements go in your own
  pull request on top of main, mentioning them.
- A trivial fix (a rebase, a recorded number, a wrapped line) only when they
  have agreed to it, keeping their commits and authorship.
- If they are gone, take their commits into your own pull request with them
  still as author, and close theirs with thanks.
- Do not make them fix conflicts our own merges caused. Offer to do it.

## 5. Merge

Contributions go in before our own pull requests on the same ground. Merge by
rebase, when the checks are green and the owner has said yes.

## 6. Thank them, and say what was good

Comment on the pull request when it lands. Name what made it easy to review or
to trust, and why it helped, so they know what to do again. Add one tip if one
is worth having. Keep it honest: praise that is not specific reads as polite
noise. For example, from #108:

> The rule came with its own enforcement. The test that every first line says
> what the tool is for means the rule cannot quietly rot.

## 7. Learn from it

When it did something better than our own changes do, add it to "What
contributions have taught us" in CONTRIBUTING, in a pull request of our own.
From then on, hold our own pull requests to it, and check the list before
writing a pull request body.

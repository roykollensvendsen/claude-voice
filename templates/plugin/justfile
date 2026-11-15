# The steps continuous integration runs, as plain local commands, so CI anywhere can run them.
# tests/test_docs.py holds every line here to the same line in .github/workflows/.

python := "uv run python"

# Once per clone: install, and turn on the commit-msg hook.
setup:
    uv sync --all-extras
    git config core.hooksPath .githooks

# Everything a pull request must pass, offline.
check: lint types private tests mutation

lint:
    {{python}} -m ruff check .
    {{python}} -m ruff format --check .

types:
    {{python}} -m mypy src

private:
    {{python}} scripts/private_data_scan.py --tracked --history

tests:
    {{python}} -m pytest -q -p no:cacheprovider --strict-markers --cov

# Every rule switched off in turn. Never give it a time limit.
mutation:
    {{python}} scripts/mutate.py

# The commits a branch adds: the convention, the imperative, and credit as the only trailer.
commits range="origin/main..HEAD":
    committed --no-merge-commit {{range}}
    {{python}} scripts/imperative.py {{range}}
    {{python}} scripts/no_session_links.py {{range}}

# A branch's new and changed tests must fail on the code before it.
red-first base="origin/main":
    {{python}} scripts/red_first.py --base {{base}} --head HEAD

# No measure worse than main's, unless the pull request says why. Never give it a time limit.
ratchet base="origin/main":
    {{python}} scripts/ratchet.py check --base {{base}}

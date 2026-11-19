"""A hook that refuses a gh command spelled with `$` or a backtick, and says why.

Claude Code runs it before every shell command of every session (a PreToolUse hook), as
``python -I -m claude_voice.literal_gh`` with the hook input on stdin. The owner's settings
deny these commands anyway; that deny is the floor. This hook only adds the reason, so a
session can retry with literal values at once instead of handing the command on.

It must start fast and alone, so it uses the standard library only. It judges a gh only at
command position, splitting the line as the shell does: a `;` or `|` inside quotes, and the
body of a heredoc, are not commands. Anything it can't decide gives no decision, and the
call goes on to the settings rules.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from typing import Any

REASON = (
    "Refused: use literal values: one gh per command, no $, pipes or loops; read files with the "
    "Read tool. For text that holds a $ (a PR body, a comment), write it to a file in your "
    "scratchpad and pass --body-file. For a GraphQL query, put the values in the query itself "
    "(number:5), or pass the query as -F query=@file.graphql. For --jq, use .field paths, not "
    "$variables."
)

HEREDOC = re.compile(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
SEPARATORS = {";", "&", "|", "&&", "||", "|&", ";;", ";&", "\n", "(", ")"}
# Words after which the next word is still at command position.
LEADERS = {"do", "then", "else", "elif", "if", "while", "until", "!", "{", "time"}
RAW_SEGMENTS = re.compile(r"&&|\|\||;|\||\n")


def _without_heredocs(command: str) -> str:
    """The command with each heredoc's body taken out, up to its closing word."""
    kept: list[str] = []
    waiting: list[tuple[str, bool]] = []
    for line in command.split("\n"):
        # RULE: a heredoc body is not judged as a command
        if waiting:
            word, tabs = waiting[0]
            if (line.lstrip("\t") if tabs else line) == word:
                waiting.pop(0)
            continue
        kept.append(line)
        waiting += [(m.group(3), m.group(1) == "-") for m in HEREDOC.finditer(line)]
    return "\n".join(kept)


def _simple_commands(command: str) -> list[list[str]]:
    """Each simple command's words, split as the shell would; ValueError when it can't."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars="();<>|&\n")
    lexer.whitespace = " \t\r"
    lexer.commenters = ""
    lexer.whitespace_split = True
    found: list[list[str]] = [[]]
    for token in lexer:
        if token in SEPARATORS:
            found.append([])
        elif found[-1] or not (token in LEADERS or ASSIGNMENT.match(token)):
            found[-1].append(token)
    return [words for words in found if words]


def _spelled(words: list[str]) -> bool:
    return any("$" in w or "`" in w for w in words)


def refused(command: str) -> bool:
    """Whether a gh at command position in `command` has a word holding `$` or a backtick."""
    try:
        commands = _simple_commands(_without_heredocs(command))
    except ValueError:
        # RULE: a line the shell cannot parse is still refused when a gh segment holds a dollar
        return any(s.strip().startswith("gh ") and _spelled([s]) for s in RAW_SEGMENTS.split(command))
    # RULE: a gh command whose words hold a dollar or a backtick is refused with the reason
    return any(words[0] == "gh" and _spelled(words[1:]) for words in commands)


def answer(hook_input: dict[str, Any]) -> dict[str, Any]:
    """Claude Code's hook answer: a refusal with the reason, or nothing so the call goes on."""
    if hook_input.get("tool_name") != "Bash":
        return {}
    command = str((hook_input.get("tool_input") or {}).get("command", ""))
    # RULE: a command with no gh is answered at once without parsing
    if "gh" not in command:
        return {}
    if not refused(command):
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": REASON,
        }
    }


def main() -> int:
    try:
        out = json.dumps(answer(json.load(sys.stdin)))
    except Exception:  # noqa: BLE001  # whatever goes wrong, no decision: the settings rules still apply
        return 0
    # The whole answer in one write, only once decided: a hook cut off by its timeout leaves
    # nothing half-written.
    sys.stdout.write(out)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

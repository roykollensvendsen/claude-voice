"""Reading a waiting question off a session's tmux pane, only ever reading."""

from claude_voice import stalled

GHP = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"

BOXED = """\
  some earlier output
╭──────────────────────────────────────────╮
│ Bash command                             │
│                                          │
│   git push origin feat/idle-builders     │
│   Push the branch                        │
│                                          │
│ Do you want to proceed?                  │
│ ❯ 1. Yes                                 │
│   2. Yes, and don't ask again for git    │
│   push commands                          │
│   3. No, and tell Claude what to do      │
│   differently (esc)                      │
╰──────────────────────────────────────────╯
"""

OPEN = """\
⏺ I'll run the tests now.
────────────────────────────────────────────
 Bash command

   uv run pytest -q

 Do you want to proceed?
 ❯ 1. Yes
   2. No
"""


def test_a_boxed_question_and_its_options_are_read_off_the_pane():
    found = stalled.parse_pane(BOXED)
    assert found.question == "Bash command git push origin feat/idle-builders Push the branch Do you want to proceed?"
    assert found.options == [
        "Yes",
        "Yes, and don't ask again for git push commands",
        "No, and tell Claude what to do differently (esc)",
    ]


def test_a_question_without_side_borders_is_read_too():
    found = stalled.parse_pane(OPEN)
    assert found.question == "Bash command uv run pytest -q Do you want to proceed?"
    assert found.options == ["Yes", "No"]


def test_a_pane_with_no_numbered_options_has_no_question():
    assert stalled.parse_pane("just some output\n$ ") is None


def test_a_line_broken_at_the_edge_of_its_box_is_joined_without_a_space():
    """So a token broken across two box lines is hidden whole."""
    pane = (
        "╭────────────────────────────╮\n"
        f"│   git push https://x:{GHP[:12]}│\n"
        f"│   {GHP[12:]}@github.com/a  │\n"
        "│ Do you want to proceed?    │\n"
        "│ ❯ 1. Yes                   │\n"
        "│   2. No                    │\n"
        "╰────────────────────────────╯\n"
    )
    said = stalled.said_question(stalled.parse_pane(pane))
    assert GHP[:12] not in said and GHP[12:] not in said and GHP[16:24] not in said


def test_a_pane_is_read_with_its_wrapped_lines_joined():
    commands = []

    def run(command):
        commands.append(command)
        return BOXED

    assert stalled.read_pane("%3", run) == BOXED
    assert commands == [["tmux", "capture-pane", "-p", "-J", "-t", "%3"]]
    assert not any("send-keys" in part for command in commands for part in command)


def test_pane_text_is_quoted_with_its_quote_marks_neutralised():
    found = stalled.Asked("Say \"yes\" or 'no'?", ["Yes"])
    assert stalled.said_question(found) == "'Say ʼyesʼ or ʼnoʼ?'. Options: 1 Yes."

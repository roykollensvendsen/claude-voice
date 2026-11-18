"""The `plugin` skill: its links lead somewhere, its rules keep their numbers, and they match the template.

The skill is how a session makes, joins, reviews or backfills a voice plugin. Its rules page is the
reference copy that reviews cite by number, so a rule that drifts from the template, a number that
is reused, or a link that leads nowhere would send a reviewer the wrong way without anyone noticing.
"""

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).parent.parent
SKILL = ROOT / ".claude/skills/plugin"
TEMPLATE = ROOT / "templates/plugin"
OLD = ROOT / ".claude/skills/write-plugin"
PARTS = ("SKILL.md", "manager.md", "builder.md", "review.md", "backfill.md", "rules.md", "reference.md")
#: The parts a role reads on its own; each says first what to have read before it.
ROLE_PARTS = ("manager.md", "builder.md", "review.md", "backfill.md")

sys.path.insert(0, str(TEMPLATE / "scripts"))

import private_data_scan  # noqa: E402
import readable  # noqa: E402

#: Built from parts, so this file does not itself hold what it looks for. Lower-case.
PERSONAL = ("roy" + "kollen", "kollen" + "svendsen", "@" + "gmail", "/home/" + "roy")
FIRST_NAME = re.compile(r"\b" + "r" + "oy\\b", re.IGNORECASE)

RULE = re.compile(r"^### (R\d+)\b(.*)$", re.MULTILINE)
CITED = re.compile(r"\bR(\d+)\b")
LINK = re.compile(r"\]\(([^)\s]+)\)")
HAS = re.compile(r"^Template: `([^`]+)` has `([^`]+)`", re.MULTILINE)
NOT_YET = re.compile(r"^Template, not built yet: `([^`]+)` has `([^`]+)`", re.MULTILINE)

#: Quality's f3: the content each of these rules needs from the template, checked here as well
#: as in rules.md, so taking a line out of rules.md cannot quietly drop the check.
F3 = {
    ("tests/bounded.py", "start_new_session=True"),
    ("tests/bounded.py", "os.killpg(child.pid, signal.SIGKILL)"),
    ("tests/test_bounded.py", "assert time.monotonic() - started < 15"),
}
#: f3's third check, on a part of the template not built yet: it must turn into a `Template:` line
#: the day the template gains it.
F3_NOT_YET = {("tests/test_docs.py", "safe-to-run")}

#: v8's numbered points, which old verdicts cite, each of which rules.md's first table maps.
V8_POINTS = (
    [f"cleanup {n}" for n in range(1, 10)] + [f"hang {n}" for n in range(1, 8)] + [f"docs {n}" for n in range(1, 4)]
)


def _text(name: str) -> str:
    return (SKILL / name).read_text(encoding="utf-8")


def _rules() -> dict[str, str]:
    """Each rule's number and the text under its heading, up to the next one."""
    text = _text("rules.md")
    heads = list(RULE.finditer(text))
    return {
        head.group(1): text[head.start() : heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        for i, head in enumerate(heads)
    }


def test_the_skill_has_every_part_its_design_names():
    missing = [part for part in PARTS if not (SKILL / part).is_file()]
    assert missing == [], f"add these to {SKILL.relative_to(ROOT)}: {missing}"


def test_the_skill_names_itself_and_says_when_to_use_it():
    head = _text("SKILL.md").split("---\n")
    assert head[0] == "" and "name: plugin\n" in head[1], "SKILL.md opens with front matter naming it `plugin`"
    assert re.search(r"^description: .{40,}$", head[1], re.MULTILINE), "the description says when to use it"


def test_the_skill_entry_stays_short():
    """It only points at the parts; a long entry is read whole by every role."""
    assert len(_text("SKILL.md").splitlines()) <= 45


def test_each_role_part_says_first_what_to_have_read():
    for part in ROLE_PARTS:
        body = _text(part).split("\n", 2)
        assert body[2].lstrip().startswith("You need:"), f"{part} opens with a 'You need:' line"


def test_every_link_in_the_skill_resolves():
    broken = []
    for part in PARTS:
        for target in LINK.findall(_text(part)):
            if re.match(r"[a-z]+://", target):
                continue
            path, _, anchor = target.partition("#")
            place = (SKILL / path).resolve() if path else SKILL / part
            if not place.exists():
                broken.append(f"{part}: {target}")
            elif anchor and place.name == "rules.md" and anchor.upper() not in _rules():
                broken.append(f"{part}: {target} names no rule")
    assert broken == [], "these links lead nowhere: " + "; ".join(broken)


def test_the_rule_numbers_are_unique_and_none_is_skipped():
    """A removed rule keeps its number as withdrawn, so a verdict citing it can still be read."""
    numbers = RULE.findall(_text("rules.md"))
    assert numbers, "rules.md has no `### R1 ...` headings"
    found = [int(n[1:]) for n, _ in numbers]
    assert found == list(range(1, len(found) + 1)), f"rule numbers run {found}"


def test_every_rule_cited_in_the_skill_exists():
    rules = _rules()
    unknown = sorted({f"{part}: R{n}" for part in PARTS for n in CITED.findall(_text(part)) if f"R{n}" not in rules})
    assert unknown == []


def test_a_withdrawn_rule_says_so_and_holds_nothing_to_check():
    for number, body in _rules().items():
        if "withdrawn" in " ".join(body.splitlines()[:3]).lower():
            assert not HAS.search(body) and not NOT_YET.search(body), number


def test_the_rules_open_with_every_v8_point_and_its_rule():
    """Quality's f2: verdicts written before the move cite v8's points, so each maps to a rule."""
    table = _text("rules.md").split("\n### ", 1)[0]
    rules = _rules()
    rows = dict(re.findall(r"^\| v8 (\w+ \d+) \| (R\d+(?:, R\d+)*) \|", table, re.MULTILINE))
    assert sorted(rows) == sorted(V8_POINTS), f"the v8 table maps {sorted(rows)}"
    for point, cited in rows.items():
        assert all(r in rules for r in cited.split(", ")), point


def test_the_rules_name_the_design_they_came_from():
    assert re.search(r"new-plugin-v8\.md \(sha256\s+[0-9a-f]{64}\)", _text("rules.md"))


def _template_lines(pattern: re.Pattern[str]) -> set[tuple[str, str]]:
    return set(pattern.findall(_text("rules.md")))


def test_each_rule_finds_what_it_names_in_the_template():
    """Quality's f3: a rule is checked against the template's content, not only that a file exists."""
    claims = _template_lines(HAS)
    assert F3 <= claims, f"rules.md lost a check quality asked for: {F3 - claims}"
    absent = [f"{file} has no {needle!r}" for file, needle in sorted(claims) if needle not in _read(file)]
    assert absent == [], "rules.md and templates/plugin/ disagree: " + "; ".join(absent)


def test_a_rule_the_template_does_not_have_yet_is_marked_until_it_does():
    """Once the template gains it, the mark comes off and it becomes a real check."""
    pending = _template_lines(NOT_YET)
    assert F3_NOT_YET <= pending | _template_lines(HAS)
    arrived = [f"{file} now has {needle!r}" for file, needle in sorted(pending) if needle in _read(file)]
    assert arrived == [], "turn these into `Template:` lines in rules.md: " + "; ".join(arrived)


def _read(file: str) -> str:
    path = TEMPLATE / file
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def test_the_skill_holds_nothing_personal():
    """Quality's f1: the skill is public, so it says "the owner" and carries no one's details."""
    found = []
    for part in PARTS:
        text = _text(part)
        found += [f"{part}: {word}" for word in PERSONAL if word in text.lower()]
        found += [f"{part}: a first name" for _ in FIRST_NAME.finditer(text)]
        found += [f"{part}: {said}" for said in private_data_scan.offences(text, set())]
    assert found == []


def test_no_sentence_in_the_skill_is_over_thirty_words():
    long = [f"{part}: {s[:60]}..." for part in PARTS for s in readable.long_sentences(_text(part))]
    assert long == [], "split these: " + "; ".join(long)


def test_the_old_skill_is_a_short_pointer_to_the_new_one():
    """It stays one release, so sessions that still load it land in the right place."""
    text = (OLD / "SKILL.md").read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 10
    assert "`plugin`" in text and "builder.md" in text and "manager.md" in text
    assert sorted(p.name for p in OLD.iterdir()) == ["SKILL.md"]


#: Where the old name may still be read: the pointer itself, the README's row saying it is one,
#: and this file.
MAY_NAME_THE_OLD_SKILL = {".claude/skills/write-plugin/SKILL.md", "README.md", "tests/test_plugin_skill.py"}


def _tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


def test_no_file_links_to_the_old_skill_after_the_move():
    named = []
    for name in _tracked():
        path = ROOT / name
        if name in MAY_NAME_THE_OLD_SKILL or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        named += [f"{name}:{n}" for n, line in enumerate(text.splitlines(), 1) if "write-plugin" in line]
    assert named == [], "point these at the `plugin` skill: " + "; ".join(named)


def test_the_readme_names_the_old_skill_only_as_a_pointer():
    """The skills table lists every shipped skill, the pointer too, so its row says where to go."""
    rows = [line for line in (ROOT / "README.md").read_text(encoding="utf-8").splitlines() if "write-plugin" in line]
    assert len(rows) == 1 and rows[0].startswith("| `write-plugin` |") and "`plugin`" in rows[0], rows

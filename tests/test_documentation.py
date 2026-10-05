"""Wherever a document restates a fact that lives in code, check that it still holds.

Point rather than copy. Where a document has to copy anyway, because a reader
needs the fact in front of them, this is what keeps the copy honest.

The two tests here are the ones every project needs. Add one of your own for
every other fact a document restates: an exit code, a field name, a default,
an index of files.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).parent.parent


def read(name: str) -> str:
    return (ROOT / name).read_text()


def test_every_gate_the_contributing_guide_names_runs_in_the_workflow():
    """A guide naming a gate that runs nowhere is the commonest defect in careful work.

    The tool alone is not enough: `mypy src` and `mypy scripts` are both mypy
    and only one of them is the gate. So where the workflow runs the tool as a
    shell command, the target is compared too. A tool the workflow reaches
    through an action instead cannot be compared that way, and its presence is
    all there is to check.
    """
    guide = read("CONTRIBUTING.md")
    workflow = "\n".join(p.read_text() for p in sorted((ROOT / ".github/workflows").glob("*.yml")))
    block = re.search(r"## The gates.*?```\n(.*?)```", guide, re.DOTALL)
    assert block, "CONTRIBUTING.md no longer lists the gates"
    run_steps = "\n".join(line for line in workflow.splitlines() if "run:" in line)
    for line in (x.strip() for x in block.group(1).splitlines() if x.strip()):
        for command in (c.strip() for c in line.split("&&")):
            words = command.removeprefix("uv run ").split()
            tool = words[0]
            target = next((w for w in words[1:] if not w.startswith("-")), "")
            assert tool in workflow, f"{tool} is a gate in CONTRIBUTING.md and runs nowhere in CI"
            if tool in run_steps and target:
                assert f"{tool} {target}" in run_steps, (
                    f"CONTRIBUTING.md runs `{tool} {target}` and no workflow runs {tool} on that"
                )


def test_the_deferred_list_gives_every_row_a_trigger():
    """A thing left undone with no trigger is a thing forgotten."""
    rows = [
        line
        for line in read("decisions/deferred.md").splitlines()
        if line.startswith("| ") and "---" not in line and not line.startswith("| Not done")
    ]
    assert rows, "the deferred list lost its rows"
    for row in rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert len(cells) == 3, f"a deferred row needs what, the trigger, and why not: {row[:60]}"
        assert cells[1], f"no trigger on: {cells[0]}"
        assert cells[2], f"no reason on: {cells[0]}"


def test_the_readme_lists_exactly_the_tools_the_bridge_offers(tmp_path):
    """The tool table is the only place a reader learns what they can ask for."""
    import asyncio
    import re

    from mcp.client import Client

    from claude_voice.server import build_server
    from claude_voice.sessions import SessionManager
    from claude_voice.store import Store

    async def offered() -> set[str]:
        m = SessionManager(Store(tmp_path / "b.db"), project_root=tmp_path)
        async with Client(build_server(m)) as client:
            return {t.name for t in (await client.list_tools()).tools}

    readme = (ROOT / "README.md").read_text()
    table = readme.split("## What you can ask for", 1)[1].split("\n## ", 1)[0]
    documented = set(
        re.findall(r"`(\w+)`", "\n".join(row.split("|")[1] for row in table.splitlines() if row.startswith("| `")))
    )
    assert documented == asyncio.run(offered())

"""The collector protocol and the one list of collectors.

A collector reads one source and yields raw items; the core does the rest. These
tests hold the parts of that promise that are pure: the shape of a collector,
the list, and what a collector may name as its own words.
"""

import ast
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from claude_voice.workflow import collectors
from claude_voice.workflow.core import event, gate, protocol
from claude_voice.workflow.core.protocol import Context, Raw, Where

ROOT = Path(__file__).resolve().parent.parent
COLLECTOR_FILES = ROOT / "src/claude_voice/workflow/collectors"
NOW = datetime(2026, 11, 16, 9, 12, 0, tzinfo=timezone(timedelta(hours=1)))


@dataclass
class Plain:
    name: str = "plain"
    every_s: int = 60

    def collect(self, ctx: Context) -> Iterable[Raw]:
        yield Raw(key="one", about="fuel", kind="waiting", blocker="review", state="point", what="quality")


@dataclass
class Worded(Plain):
    name: str = "worded"
    vocabulary: frozenset[str] = frozenset({"quality", "other-reviewer"})
    catch_all: str | None = "other-reviewer"


def test_a_collector_needs_only_a_name_a_cadence_and_a_collect():
    assert isinstance(Plain(), protocol.Collector)
    assert protocol.vocabulary_of(Plain()) == frozenset()
    assert protocol.vocabulary_of(Worded()) == {"quality", "other-reviewer"}
    assert protocol.catch_all_of(Plain()) is None
    assert protocol.catch_all_of(Worded()) == "other-reviewer"


def test_a_word_in_a_collectors_vocabulary_passes_and_the_same_word_from_another_is_dropped():
    ctx = Context(state={}, now=NOW, programmes={"fuel": Where("fuel-mcp", "fuel-mcp")}, log_seen=lambda i, s: False)
    known = gate.Known(paths={}, rules=frozenset(), private=frozenset())
    whats = {}
    for collector in (Worded(), Plain()):
        (item,) = collector.collect(ctx)
        setting = gate.Setting(
            collector.name,
            known,
            ctx.programmes,
            ctx.now,
            vocabulary=protocol.vocabulary_of(collector),
            catch_all=protocol.catch_all_of(collector),
        )
        out = gate.gate(item, setting)
        assert isinstance(out, event.Event)
        whats[collector.name] = out.what
    assert whats == {"worded": "quality", "plain": None}


def test_a_collector_name_is_unique():
    protocol.check([Plain(), Worded()])
    with pytest.raises(ValueError, match="twice"):
        protocol.check([Plain(), Plain()])


def test_a_collector_name_is_plain():
    for name in ("Prs", "two words", "", "a" * 33, "prs:x"):
        with pytest.raises(ValueError, match="name"):
            protocol.check([Plain(name=name)])


def test_a_collector_runs_at_most_once_a_second():
    with pytest.raises(ValueError, match="every_s"):
        protocol.check([Plain(every_s=0)])


def test_a_vocabulary_word_is_short_and_plain():
    for word in ("Quality", "two words", "x" * 41, "", "review_bot"):
        with pytest.raises(ValueError, match="word"):
            protocol.check([Worded(vocabulary=frozenset({word, "other-reviewer"}))])
    protocol.check([Worded(vocabulary=frozenset({"x" * 40, "other-reviewer"}))])


def test_a_vocabulary_holds_its_own_catch_all():
    protocol.check([Worded(vocabulary=frozenset({"tool-timeout", "other-tool"}), catch_all="other-tool")])
    with pytest.raises(ValueError, match="catch_all"):
        protocol.check([Worded(vocabulary=frozenset({"quality", "other-reviewer"}), catch_all=None)])
    with pytest.raises(ValueError, match="catch_all"):
        protocol.check([Worded(vocabulary=frozenset({"quality"}), catch_all="other-reviewer")])
    with pytest.raises(ValueError, match="catch_all"):
        protocol.check([Worded(vocabulary=frozenset(), catch_all="other-reviewer")])


def test_a_vocabulary_is_a_frozen_set():
    with pytest.raises(ValueError, match="frozenset"):
        protocol.check([Worded(vocabulary={"quality", "other-reviewer"})])  # type: ignore[arg-type]


def test_the_registered_collectors_pass_the_check():
    protocol.check(collectors.COLLECTORS)


def literal_faults(source: str) -> list[str]:
    """Every `vocabulary` in a collector's source that is not a frozenset of plain strings, by line.

    A vocabulary built at run time could take words from what the collector reads,
    which would make `what` a free text field after all.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        targets = []
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        for target in targets:
            name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)
            if name == "vocabulary" and not _frozenset_literal(value):
                found.append(f"line {node.lineno}")
    return found


def _frozenset_literal(value: ast.expr) -> bool:
    if not (isinstance(value, ast.Call) and getattr(value.func, "id", None) == "frozenset" and not value.keywords):
        return False
    if not value.args:
        return True
    (inner,) = value.args
    return isinstance(inner, ast.Set) and all(
        isinstance(e, ast.Constant) and isinstance(e.value, str) for e in inner.elts
    )


def test_a_collectors_vocabulary_must_be_a_frozenset_literal():
    good = 'class A:\n    vocabulary = frozenset({"quality", "other-reviewer"})\n'
    good_annotated = 'class A:\n    vocabulary: frozenset[str] = frozenset({"other-reviewer"})\n'
    assert literal_faults(good) == literal_faults(good_annotated) == []
    for bad in (
        "class A:\n    vocabulary = frozenset(read_names())\n",
        'class A:\n    vocabulary = {"quality"}\n',
        "class A:\n    def __init__(self, seen):\n        self.vocabulary = frozenset(seen)\n",
        'WORDS = "a"\nclass A:\n    vocabulary = frozenset({WORDS})\n',
    ):
        assert literal_faults(bad), bad
    for path in sorted(COLLECTOR_FILES.glob("*.py")):
        assert literal_faults(path.read_text(encoding="utf-8")) == [], path.name

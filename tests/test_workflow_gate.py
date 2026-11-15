"""The gate: what a collector saw becomes an event only through it.

The gate keeps personal data and secrets out of the friction log by letting
through only fields it can check. Every name, token and place in these tests is
made up; none is anyone's real one.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jsonschema
import pytest

from claude_voice.workflow.core import event, gate
from claude_voice.workflow.core.protocol import Raw, Where

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "docs/workflow-event/v1.json").read_text(encoding="utf-8"))
OSLO = timezone(timedelta(hours=1))
NOW = datetime(2026, 11, 16, 9, 12, 0, tzinfo=OSLO)
STARTED = datetime(2026, 11, 16, 8, 2, 17, 412000, tzinfo=OSLO)
PROGRAMMES = {"fuel": Where(session="fuel-mcp", repo="fuel-mcp")}
KNOWN = gate.Known(
    paths={"fuel-mcp": frozenset({"src/fuel_mcp/server.py"}), "tv-mcp": frozenset({"src/tv_mcp/tv.py"})},
    rules=frozenset({"a price is never older than a day"}),
    private=frozenset({"Plommeby", "Kaffekroken"}),
)


def raw(**changes):
    fields = {"key": "fuel-mcp#14:review", "about": "fuel", "kind": "waiting", "blocker": "review", "state": "open"}
    return Raw(**{"started": STARTED, **fields, **changes})


def let(item, *, vocabulary=frozenset(), catch_all=None, source="prs"):
    """The gate's answer, checked against the published schema when it is an event."""
    setting = gate.Setting(source, KNOWN, PROGRAMMES, NOW, vocabulary=vocabulary, catch_all=catch_all)
    out = gate.gate(item, setting)
    if isinstance(out, event.Event):
        jsonschema.Draft202012Validator(SCHEMA).validate(out.to_dict())
        assert event.read([out.to_line()]).events == [out]
    return out


def line_of(text):
    out = let(raw(line=text))
    assert isinstance(out, event.Event)
    return out.line


def test_an_event_passes_with_the_bridges_own_session_and_repo():
    out = let(raw(state="closed"))
    assert isinstance(out, event.Event)
    assert (out.source, out.session, out.repo, out.at) == ("prs", "fuel-mcp", "fuel-mcp", NOW)
    assert out.duration_s == (NOW - STARTED.replace(microsecond=0)).total_seconds() == 4183


def test_an_event_from_a_place_the_bridge_does_not_know_is_refused():
    out = let(raw(about="fuel-mcp-but-not-really"))
    assert isinstance(out, gate.Refused)
    assert "place" in out.reason
    assert "not-really" not in out.reason


def test_a_raw_item_has_no_field_for_its_session_repo_or_source():
    for field in ("session", "repo", "source"):
        with pytest.raises(TypeError):
            raw(**{field: "someone-else"})


def test_a_refusal_from_the_format_comes_back_as_a_refusal():
    for changes in ({"kind": "annoying"}, {"blocker": "the-weather"}, {"state": "paused"}):
        out = let(raw(**changes))
        assert isinstance(out, gate.Refused), changes
        assert "annoying" not in out.reason and "weather" not in out.reason


def test_an_open_or_closed_event_needs_its_start():
    for state in ("open", "closed"):
        assert isinstance(let(raw(state=state, started=None)), gate.Refused)
    point = let(raw(state="point", started=None))
    assert isinstance(point, event.Event)
    assert point.started == NOW and point.duration_s is None


def test_an_event_id_carries_its_start_to_the_second():
    first = let(raw())
    assert first.id == "prs:fuel-mcp#14:review:2026-11-16T07:02:17Z"
    closed = let(raw(state="closed", started=STARTED.replace(microsecond=0)))
    assert closed.id == first.id
    again = let(raw(started=STARTED + timedelta(minutes=40)))
    assert again.id != first.id


def test_an_event_id_writes_its_start_in_utc():
    here = let(raw())
    elsewhere = let(raw(started=STARTED.astimezone(timezone(timedelta(hours=-5)))))
    assert here.id == elsewhere.id == "prs:fuel-mcp#14:review:2026-11-16T07:02:17Z"
    assert here.started.utcoffset() == timedelta(hours=1)
    assert elsewhere.started.utcoffset() == timedelta(hours=-5)


def test_an_event_key_is_plain():
    for key in ("", "two words", "a\nb", "x" * 121, "ghp_" + "A1b2" * 9):
        out = let(raw(key=key))
        assert isinstance(out, gate.Refused), key
        assert "A1b2" not in out.reason
    assert isinstance(let(raw(key="live:fuel-mcp/main#3+x.y")), event.Event)


def test_an_event_key_holds_no_private_data():
    for key in ("review:someone@example.com", "fuel:plommeby#3", "charger:EH1234AB"):
        out = let(raw(key=key))
        assert isinstance(out, gate.Refused), key
        assert "private" in out.reason
        assert not any(part in out.reason for part in ("someone", "plommeby", "EH1234AB"))


def test_what_is_only_a_known_path_rule_or_word():
    def what(value):
        return let(raw(what=value)).what

    assert what("src/fuel_mcp/server.py") == "src/fuel_mcp/server.py"
    assert what("a price is never older than a day") == "a price is never older than a day"
    assert what("src/tv_mcp/tv.py") is None  # known, but in another repo
    assert what("/home/someone/notes.txt") is None
    assert what("Plommeby") is None
    assert what(None) is None


def test_a_word_in_the_collectors_own_vocabulary_is_a_known_what():
    words = frozenset({"quality", "other-reviewer"})
    assert let(raw(what="quality"), vocabulary=words, catch_all="other-reviewer").what == "quality"
    assert let(raw(what="quality")).what is None


def test_an_unknown_what_from_a_collector_with_a_catch_all_becomes_its_catch_all():
    words = frozenset({"quality", "other-reviewer"})
    assert let(raw(what="a-new-bot"), vocabulary=words, catch_all="other-reviewer").what == "other-reviewer"
    assert let(raw(what="src/fuel_mcp/server.py"), vocabulary=words, catch_all="other-reviewer").what == (
        "src/fuel_mcp/server.py"
    )
    assert let(raw(what=None), vocabulary=words, catch_all="other-reviewer").what is None
    assert let(raw(what="a-new-bot")).what is None


def test_a_what_from_outside_the_collectors_words_is_counted_per_source():
    items = [raw(what="quality"), raw(what="src/fuel_mcp/server.py"), raw(what=None), raw(about="nowhere")]
    words = frozenset({"other-reviewer"})
    batch = gate.gate_all(
        items, gate.Setting("prs", KNOWN, PROGRAMMES, NOW, vocabulary=words, catch_all="other-reviewer")
    )
    assert [e.what for e in batch.events] == ["other-reviewer", "src/fuel_mcp/server.py", None]
    assert batch.unknown_what == {"prs": 1}
    assert sum(batch.refused.values()) == 1
    assert not any("nowhere" in reason for reason in batch.refused)


def test_an_event_line_is_cut_to_the_limit():
    line = line_of("word " * 60)
    assert len(line) == event.LINE_CHARS
    assert line_of("one\nline\tonly") == "one line only"
    assert line_of(None) is None


@pytest.mark.parametrize(
    "token",
    [
        "sk-ant-api03-madeUpMadeUpMadeUp",
        "ghp_madeUpMadeUpMadeUp0123456789",
        "gho_madeUpMadeUp0123",
        "github_pat_11MADEUP0_madeUpMadeUp",
        "Bearer made.up.jwt-value",
        "bWFkZSB1cCBtYWRlIHVwIG1hZGUgdXAgbWFkZSB1cA==",
        "0123456789abcdef0123456789abcdef",
        "https://example.com/callback?code=madeup&state=x",
    ],
)
def test_a_token_shaped_text_is_blanked(token):
    line = line_of(f"the push failed with {token} again")
    assert line == f"the push failed with {gate.BLANK} again"
    for part in ("madeUp", "madeup", "bWFkZ", "0123456789abcdef", "jwt", "code="):
        assert part not in line


def test_a_token_cut_by_the_limit_is_still_blanked():
    line = line_of("x " * 50 + "ghp_" + "madeUp01" * 6)
    assert "madeUp" not in line and "ghp_" not in line


def test_ordinary_words_and_paths_are_not_blanked():
    text = "review waits on quality for src/fuel_mcp/server.py, see https://example.com/pull/14"
    assert line_of(text) == text


@pytest.mark.parametrize(
    "private",
    [
        "fuel near plommeby again",
        "the shop KAFFEKROKEN is open",
        "mail to someone@example.com",
        "at 59.91234, 10.75123 now",
        "charger EH1234AB said no",
        "the box at 192.168.1.20 is down",
        "she lives at Eksempelgata 12",
    ],
)
def test_a_line_with_private_data_is_dropped(private):
    out = let(raw(line=private, what="src/fuel_mcp/server.py"))
    assert out.line is None
    assert (out.kind, out.what) == ("waiting", "src/fuel_mcp/server.py")


def test_a_private_word_shorter_than_four_letters_is_ignored():
    def line_with(words):
        known = gate.Known(paths={}, rules=frozenset(), private=frozenset(words))
        return gate.gate(raw(line="about a tab"), gate.Setting("prs", known, PROGRAMMES, NOW)).line

    assert line_with({"tab", "ab"}) == "about a tab"
    assert line_with({"abou"}) is None

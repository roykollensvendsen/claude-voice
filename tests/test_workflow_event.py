"""The workflow event format, version 1: what one line of the friction log holds.

Every value here is made up. The schema in docs/workflow-event/v1.json is what
other programs read the log by, so every event these tests make is checked
against it as well as against the reader in the bridge.
"""

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import jsonschema
import pytest

from claude_voice.workflow.core import event

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "docs/workflow-event/v1.json").read_text(encoding="utf-8"))
OSLO = timezone(timedelta(hours=1))
STARTED = datetime(2026, 11, 16, 8, 2, 17, tzinfo=OSLO)
AT = STARTED + timedelta(seconds=4200)


def fits(body):
    """The schema's verdict on one event, raising if it does not fit."""
    jsonschema.Draft202012Validator(SCHEMA, format_checker=jsonschema.FormatChecker()).validate(body)


def make(**changes):
    fields = {
        "at": AT,
        "source": "prs",
        "id": "prs:fuel-mcp#14:review:2026-11-16T07:02:17Z",
        "session": "fuel-mcp",
        "repo": "fuel-mcp",
        "kind": "waiting",
        "blocker": "review",
        "state": "closed",
        "started": STARTED,
    }
    return event.Event(**{**fields, **changes})


def test_the_schema_is_a_valid_schema_and_its_own_examples_fit_it():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["examples"]
    for example in SCHEMA["examples"]:
        fits(example)
        assert event.read([json.dumps(example)]).events


def test_the_schema_names_the_same_fixed_lists_as_the_code():
    properties = SCHEMA["properties"]
    assert tuple(properties["kind"]["enum"]) == event.KINDS
    assert tuple(properties["blocker"]["enum"]) == event.BLOCKERS
    assert tuple(properties["state"]["enum"]) == event.STATES
    assert properties["line"]["maxLength"] == event.LINE_CHARS


def test_the_version_is_written_first():
    line = make().to_line()
    assert line.startswith('{"v": 1,')
    assert "\n" not in line


def test_a_closed_event_reads_back_as_it_was_written():
    written = make(what="src/fuel_mcp/server.py")
    fits(written.to_dict())
    assert event.read([written.to_line()]).events == [written]


def test_a_duration_is_set_only_on_a_closed_event():
    assert make().duration_s == 4200
    for state in ("open", "point"):
        body = make(state=state, at=AT).to_dict()
        assert body["duration_s"] is None
        fits(body)


def test_closed_late_is_only_on_a_closed_event():
    late = make(closed_late=True).to_dict()
    assert late["closed_late"] is True
    fits(late)
    assert "closed_late" not in make().to_dict()
    with pytest.raises(event.EventError, match="closed late"):
        make(state="open", closed_late=True)


def test_an_event_kind_outside_the_fixed_list_is_refused():
    with pytest.raises(event.EventError, match="kind"):
        make(kind="annoying")


def test_a_blocker_outside_the_fixed_list_is_refused():
    with pytest.raises(event.EventError, match="blocker"):
        make(blocker="the-weather")


def test_a_state_outside_the_fixed_list_is_refused():
    with pytest.raises(event.EventError, match="state"):
        make(state="paused")


def test_an_event_time_carries_its_offset():
    with pytest.raises(event.EventError, match="offset"):
        make(at=AT.replace(tzinfo=None))
    with pytest.raises(event.EventError, match="offset"):
        make(started=STARTED.replace(tzinfo=None))
    assert make(at=AT.astimezone(UTC)).duration_s == 4200


def test_an_event_cannot_end_before_it_starts():
    with pytest.raises(event.EventError, match="before"):
        make(at=STARTED - timedelta(seconds=1))


def test_an_event_line_is_never_longer_than_the_limit():
    assert make(line="x" * event.LINE_CHARS).line
    with pytest.raises(event.EventError, match="line"):
        make(line="x" * (event.LINE_CHARS + 1))


def test_an_unknown_event_version_is_skipped_and_counted():
    v1 = make().to_dict()
    lines = [json.dumps({**v1, "v": 2}), json.dumps({**v1, "v": 0}), json.dumps({**v1, "v": "1"})]
    reading = event.read([*lines, json.dumps(v1)])
    assert reading.events == [make()]
    assert reading.skipped == {"version": 3}
    with pytest.raises(jsonschema.ValidationError):
        fits({**v1, "v": 2})


def test_a_version_that_only_compares_equal_to_one_is_not_read_as_one():
    v1 = make().to_dict()
    reading = event.read([json.dumps({**v1, "v": True}), json.dumps({**v1, "v": 1.0})])
    assert reading.events == []
    assert reading.skipped == {"version": 2}


@pytest.mark.parametrize(
    "broken",
    [
        "not json",
        "[1, 2]",
        '{"v": 1}',
        "MISSING_FIELD",
        "EXTRA_FIELD",
        "WRONG_DURATION",
        "BAD_KIND",
        "NAIVE_TIME",
        "ID_OF_ANOTHER_SOURCE",
    ],
)
def test_an_event_line_that_does_not_fit_its_version_is_skipped_and_counted(broken):
    v1 = make().to_dict()
    lines = {
        "MISSING_FIELD": json.dumps({k: v for k, v in v1.items() if k != "blocker"}),
        "EXTRA_FIELD": json.dumps({**v1, "screen": "text"}),
        "WRONG_DURATION": json.dumps({**v1, "duration_s": 5}),
        "BAD_KIND": json.dumps({**v1, "kind": "annoying"}),
        "NAIVE_TIME": json.dumps({**v1, "at": "2026-11-16T09:12:17"}),
        "ID_OF_ANOTHER_SOURCE": json.dumps({**v1, "id": "live:fuel-mcp"}),
    }
    reading = event.read([lines.get(broken, broken)])
    assert reading.events == []
    assert reading.skipped == {"unreadable": 1}


def test_blank_lines_are_neither_events_nor_counted():
    assert event.read(["", "  \n"]) == event.Reading(events=[], skipped={})


def test_an_event_waiting_on_the_owner_says_owner_and_fits_the_schema():
    body = make(blocker="owner").to_dict()
    fits(body)
    assert body["blocker"] == "owner"


def test_a_stored_blocker_of_an_old_name_is_read_as_its_new_name():
    """Lines written before the rename keep the old value for as long as they exist."""
    old = make(blocker="owner").to_dict() | {"blocker": "r" + "oy"}
    fits(old)
    assert [e.blocker for e in event.read([json.dumps(old)]).events] == ["owner"]

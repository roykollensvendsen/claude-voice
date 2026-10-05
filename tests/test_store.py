import pytest

from claude_voice.store import SessionNotFound, Store


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return Store(tmp_path / "bridge.db", clock=clock)


def test_created_session_starts_idle_without_claude_session(store):
    s = store.create_session("/src/app", label="app")

    assert s["status"] == "idle"
    assert s["project_path"] == "/src/app"
    assert s["label"] == "app"
    assert s["claude_session_id"] is None
    assert store.get_session(s["id"]) == s


def test_unknown_session_raises(store):
    with pytest.raises(SessionNotFound):
        store.get_session("nope")


def test_update_changes_fields_and_bumps_updated_at(store, clock):
    s = store.create_session("/src/app")
    clock.t = 2000.0

    store.update_session(s["id"], status="running", claude_session_id="abc")

    got = store.get_session(s["id"])
    assert got["status"] == "running"
    assert got["claude_session_id"] == "abc"
    assert got["updated_at"] == 2000.0
    assert got["created_at"] == 1000.0


def test_update_rejects_unknown_columns(store):
    s = store.create_session("/src/app")
    with pytest.raises(ValueError):
        store.update_session(s["id"], **{"status = 'x'; --": "y"})


def test_list_is_newest_activity_first_and_filters(store, clock):
    a = store.create_session("/src/a")
    clock.t += 1
    b = store.create_session("/src/b")
    clock.t += 1
    store.update_session(a["id"], status="running")

    assert [s["id"] for s in store.list_sessions()] == [a["id"], b["id"]]
    assert [s["id"] for s in store.list_sessions(project_path="/src/b")] == [b["id"]]
    assert [s["id"] for s in store.list_sessions(status="running")] == [a["id"]]
    assert len(store.list_sessions(limit=1)) == 1


def test_events_are_returned_in_order_after_a_cursor(store):
    s = store.create_session("/src/app")
    first = store.add_event(s["id"], "prompt", {"text": "hi"})
    store.add_event(s["id"], "text", {"text": "hello"})

    events = store.events(s["id"])
    assert [e["kind"] for e in events] == ["prompt", "text"]
    assert events[0]["payload"] == {"text": "hi"}
    assert [e["kind"] for e in store.events(s["id"], after=first)] == ["text"]


def test_events_limit_keeps_the_oldest_after_cursor(store):
    s = store.create_session("/src/app")
    for i in range(5):
        store.add_event(s["id"], "text", {"i": i})

    assert [e["payload"]["i"] for e in store.events(s["id"], limit=2)] == [0, 1]


def test_tail_returns_latest_events_in_order(store):
    s = store.create_session("/src/app")
    for i in range(5):
        store.add_event(s["id"], "text", {"i": i})

    assert [e["payload"]["i"] for e in store.tail(s["id"], 2)] == [3, 4]


def test_event_on_unknown_session_raises(store):
    with pytest.raises(SessionNotFound):
        store.add_event("nope", "text", {})


def test_recent_events_span_sessions_and_respect_cutoff(store, clock):
    a = store.create_session("/src/a")
    store.add_event(a["id"], "old", {})
    clock.t = 5000.0
    b = store.create_session("/src/b")
    store.add_event(b["id"], "new", {})

    recent = store.recent_events(since=4000.0)
    assert [e["kind"] for e in recent] == ["new"]
    assert recent[-1]["project_path"] == "/src/b"


def test_state_survives_reopening_the_database(tmp_path, clock):
    path = tmp_path / "bridge.db"
    s = Store(path, clock=clock).create_session("/src/app")

    reopened = Store(path, clock=clock)
    assert reopened.get_session(s["id"])["project_path"] == "/src/app"


def test_sessions_left_running_by_a_crash_are_marked_interrupted(tmp_path, clock):
    path = tmp_path / "bridge.db"
    first = Store(path, clock=clock)
    s = first.create_session("/src/app")
    first.update_session(s["id"], status="running")

    reopened = Store(path, clock=clock)
    assert reopened.get_session(s["id"])["status"] == "interrupted"
    assert reopened.events(s["id"])[-1]["kind"] == "interrupted"


def test_recent_events_limit_keeps_the_newest(store):
    s = store.create_session("/src/app")
    for i in range(5):
        store.add_event(s["id"], "text", {"i": i})

    assert [e["payload"]["i"] for e in store.recent_events(since=0, limit=2)] == [3, 4]

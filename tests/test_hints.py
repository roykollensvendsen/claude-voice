"""Hints that sessions in no programme seem to belong together."""

from pathlib import Path

from claude_voice import hints

ROOT = Path("/work")


def row(sid, name, programme=None):
    return {"id": sid, "name": name, "programme": programme}


def found(rows, folders=None, counts=None):
    return hints.hints(rows, folders or {}, counts or {})


def test_sessions_that_message_each_other_are_hinted_with_the_count():
    rows = [row("a", "one"), row("b", "two"), row("c", "three")]
    out = found(rows, counts={("a", "b"): 3})
    expected = {"sessions": ["a", "b"], "reason": "messages", "detail": None, "messages": 3}
    assert out == [{"id": hints.hint_id(["a", "b"]), **expected}]


def test_sessions_linked_through_a_third_by_messages_are_one_hint():
    rows = [row("a", "one"), row("b", "two"), row("c", "three")]
    out = found(rows, counts={("a", "b"): 1, ("b", "c"): 2})
    assert [(h["sessions"], h["messages"]) for h in out] == [(["a", "b", "c"], 3)]


def test_only_sessions_in_no_programme_are_hinted():
    rows = [row("a", "acme-one"), row("b", "acme-two", programme={"id": "acme", "name": "Acme"})]
    assert found(rows, counts={("a", "b"): 5}) == []


def test_a_hint_holds_two_sessions_or_more():
    rows = [row("a", "acme-one"), row("b", "other")]
    assert found(rows, folders={"a": "/work/acme", "b": "/work/beta"}) == []


def test_sessions_sharing_a_folder_are_hinted_with_its_name():
    rows = [row("a", "one"), row("b", "two")]
    out = found(rows, folders={"a": "/work/acme", "b": "/work/acme"})
    assert [(h["sessions"], h["reason"], h["detail"], h["messages"]) for h in out] == [
        (["a", "b"], "folder", "acme", None)
    ]


def test_a_name_prefix_ends_in_a_dash_and_is_at_least_four_characters():
    rows = [row("a", "ab-one"), row("b", "ab-two"), row("c", "acme-one"), row("d", "acme-two"), row("e", "acmeish")]
    out = found(rows)
    assert [(h["sessions"], h["reason"], h["detail"]) for h in out] == [(["c", "d"], "prefix", "acme-")]


def test_the_longest_shared_prefix_groups_first():
    rows = [row("a", "acme-quest-art"), row("b", "acme-quest-levels"), row("c", "acme-shop"), row("d", "acme-docs")]
    out = found(rows)
    assert [(h["sessions"], h["detail"]) for h in out] == [(["a", "b"], "acme-quest-"), (["c", "d"], "acme-")]


def test_a_session_is_in_one_hint_at_most():
    rows = [row("a", "acme-one"), row("b", "acme-two")]
    out = found(rows, folders={"a": "/work/acme", "b": "/work/acme"}, counts={("a", "b"): 1})
    assert [h["reason"] for h in out] == ["messages"]


def test_the_strongest_reason_places_a_session_first():
    rows = [row("a", "one"), row("b", "two"), row("c", "three")]
    out = found(rows, folders={"a": "/work/acme", "c": "/work/acme"}, counts={("a", "b"): 1})
    assert [(h["sessions"], h["reason"]) for h in out] == [(["a", "b"], "messages")]


def test_folder_comes_before_prefix():
    rows = [row("a", "acme-one"), row("b", "acme-two"), row("c", "acme-three")]
    out = found(rows, folders={"b": "/work/beta", "c": "/work/beta"})
    assert [(h["sessions"], h["reason"]) for h in out] == [(["b", "c"], "folder")]


def test_the_same_sessions_always_get_the_same_hint_id():
    assert hints.hint_id(["b", "a"]) == hints.hint_id(["a", "b"])
    assert hints.hint_id(["a", "b"]) != hints.hint_id(["a", "c"])
    assert hints.hint_id(["a", "b"]).startswith("h-")


def test_a_worktree_is_grouped_with_its_repository():
    assert hints.folder_key("/work/acme/.claude/worktrees/agent-1", ROOT) == "/work/acme"
    assert hints.folder_key("/elsewhere/acme/.claude/worktrees/agent-1", ROOT) == "/elsewhere/acme"


def test_a_folder_inside_a_project_belongs_to_the_project():
    assert hints.folder_key("/work/acme/src/deep", ROOT) == "/work/acme"
    assert hints.folder_key("/elsewhere/tools", ROOT) == "/elsewhere/tools"
    assert hints.folder_key(None, ROOT) is None


def test_the_project_root_is_no_shared_folder():
    assert hints.folder_key("/work", ROOT) is None

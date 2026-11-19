"""The phone page sees and answers the sessions on other machines through the hub's
event API: each with its machine, an id with the machine in front, and the machines'
states, all answered from what the hub last heard so the page never waits."""

import json

from test_api import BEARER, bridge, client  # noqa: F401  (fixtures)
from test_live import live, root  # noqa: F401  (fixtures)

from claude_voice import fleet
from claude_voice.config import MachineSpec


class Answer:
    def __init__(self, body, error=False):
        self.structured_content, self.is_error = body, error
        self.content = [type("T", (), {"text": json.dumps(body)})()]


class FakeMachine:
    """A machine whose agent answers from a table, and remembers what it was asked."""

    def __init__(self, name="laptop-a", sessions=None, approvals=None):
        self.machine = MachineSpec(name=name, address="100.64.0.2")
        self.approval, self.last_failure = {"seen_as": "laptop-host"}, None
        self.spec = type("Spec", (), {"tools": ["list_active_sessions"]})()
        self.sessions = sessions or []
        self.approvals = approvals or []
        self.asked: list[tuple[str, dict]] = []
        self.down = False

    async def call(self, tool, arguments):
        self.asked.append((tool, arguments))
        if self.down:
            raise RuntimeError("laptop-a did not answer")
        if tool == "fleet_recap":
            return Answer({"sessions": self.sessions})
        if tool == "list_pending_approvals":
            return Answer({"approvals": self.approvals})
        if tool == "read_session_output":
            return Answer(
                {
                    "session": arguments["session"],
                    "status": "idle",
                    "turns": [],
                    "next_after": -1,
                    "turns_left": 0,
                    "extra": 1,
                }
            )
        if tool in ("approve", "deny"):
            return Answer({"id": arguments["approval_id"]})
        return Answer({})


MOD = {"id": "840ab631", "name": "mod", "kind": "background", "status": "idle", "state": "idle", "number": 1}
OWN = {"id": "s-7", "name": "fix", "kind": "bridge-session", "status": "running", "state": "approval", "number": 2}
ASK = {"id": "3", "session_id": "s-7", "tool": "Bash", "input": "touch a", "reason": None}


async def a_fleet(**kw):
    across = fleet.Fleet()
    across.machines = [FakeMachine(**kw)]
    await across.refresh()
    return across, across.machines[0]


async def test_each_session_says_which_machine_it_is_on(tmp_path):
    across, _ = await a_fleet(sessions=[MOD])
    listed = across.listing({"sessions": [{"id": "here-1", "name": "billing", "number": 1}]})
    here, there = listed["sessions"]
    assert here["machine"] is None and here["id"] == "here-1"
    assert there["machine"] == "laptop-a" and there["id"] == "laptop-a/840ab631" and there["name"] == "mod"
    assert there["answer_in"] == "claude_app" and there["number"] is None
    machine = listed["machines"][0]
    assert machine["name"] == "laptop-a" and machine["state"] == "online" and machine["reachable"] is True


async def test_a_machine_that_does_not_answer_leaves_its_sessions_out():
    across, laptop = await a_fleet(sessions=[MOD])
    laptop.down = True
    across._seen["laptop-a"]["checked_at"] = 0
    await across.refresh()
    listed = across.listing({"sessions": []})
    assert listed["sessions"] == []
    assert listed["machines"][0]["reachable"] is False and listed["machines"][0]["last_seen"] is not None


async def test_a_remote_session_is_read_by_its_id_with_the_machine_in_front():
    across, laptop = await a_fleet(sessions=[MOD])
    out = await across.output("laptop-a/840ab631", 6, None)
    assert out == {"status": "idle", "turns": [], "next_after": -1, "turns_left": 0}
    assert laptop.asked[-1] == ("read_session_output", {"session": "840ab631", "limit": 6})


async def test_only_questions_the_hub_can_answer_are_offered_to_the_page():
    across, _ = await a_fleet(sessions=[MOD, OWN], approvals=[ASK])
    prompts = across.prompts()
    assert prompts == [
        {
            "id": "laptop-a/3",
            "session": "laptop-a/s-7",
            "machine": "laptop-a",
            "kind": "approval",
            "tool": "Bash",
            "input": "touch a",
            "cut": False,
            "asked_at": prompts[0]["asked_at"],
        }
    ]


async def test_an_answer_from_the_page_reaches_the_machine_the_question_is_on():
    across, laptop = await a_fleet(sessions=[OWN], approvals=[ASK])
    laptop.down = True
    assert await across.answer("laptop-a/3", {"decision": "allow"}) == (
        502,
        {"error": "laptop-a did not answer"},
    )
    laptop.down = False
    assert await across.answer("laptop-a/3", {"decision": "deny", "reason": "nei"}) == (200, {"ok": True})
    assert laptop.asked[-1] == ("deny", {"approval_id": "3", "reason": "nei"})
    assert await across.answer("laptop-a/3", {"decision": "allow"}) == (404, {"error": "this prompt is gone"})


async def test_the_page_hears_when_a_machine_or_its_sessions_change():
    events = []
    across, laptop = await a_fleet(sessions=[MOD])
    across.publish = lambda event, data: events.append((event, data))

    async def again():
        across._seen["laptop-a"]["checked_at"] = 0
        await across.refresh()

    laptop.sessions, laptop.approvals = [{**MOD, "state": "working"}, OWN], [ASK]
    await again()
    assert ("status", "laptop-a/840ab631", "laptop-a") in [(e, d.get("session"), d.get("machine")) for e, d in events]
    assert [d["id"] for e, d in events if e == "prompt"] == ["laptop-a/3"]
    laptop.down = True
    await again()
    assert [d["state"] for e, d in events if e == "machine"] == ["slow"]
    assert not [e for e, _ in events if e == "prompt_closed"]  # not known to be answered while away
    laptop.down, laptop.approvals = False, []
    await again()
    assert [d for e, d in events if e == "prompt_closed"] == [{"id": "laptop-a/3", "how": "screen"}]
    assert [d["state"] for e, d in events if e == "machine"] == ["slow", "online"]


async def test_the_event_api_takes_an_id_with_the_machine_in_front(bridge):  # noqa: F811
    server, app, _, _ = bridge
    server.fleet.machines = [FakeMachine(sessions=[MOD, OWN], approvals=[ASK])]
    await server.fleet.refresh()
    async with client(app) as http:
        listed = (await http.get("/api/v1/sessions", headers=BEARER)).json()
        read = await http.get("/api/v1/sessions/laptop-a%2F840ab631/output", headers=BEARER)
        prompts = (await http.get("/api/v1/prompts", headers=BEARER)).json()["prompts"]
        answered = await http.post("/api/v1/prompts/laptop-a%2F3", headers=BEARER, json={"decision": "allow"})
    assert {s["id"] for s in listed["sessions"]} >= {"laptop-a/840ab631", "laptop-a/s-7"}
    assert listed["machines"][0]["name"] == "laptop-a"
    assert read.status_code == 200 and read.json()["status"] == "idle"
    assert [p["id"] for p in prompts] == ["laptop-a/3"]
    assert answered.status_code == 200


async def test_a_session_on_another_machine_can_be_the_one_the_voice_talks_to(bridge):  # noqa: F811
    server, app, _, _ = bridge
    server.fleet.machines = [FakeMachine(sessions=[MOD])]
    await server.fleet.refresh()
    async with client(app) as http:
        chosen = await http.put("/api/v1/active", headers=BEARER, json={"session": "laptop-a/840ab631"})
        gone = await http.put("/api/v1/active", headers=BEARER, json={"session": "laptop-a/nope"})
    assert chosen.status_code == 200, chosen.text
    assert {"id": "laptop-a/840ab631", "name": "laptop-a/mod", "machine": "laptop-a"}.items() <= (chosen.json().items())
    assert gone.status_code == 404


async def test_the_sessions_here_stay_in_the_list_beside_another_machines(bridge):  # noqa: F811
    from mcp.client import Client

    server, _, _, _ = bridge
    server.fleet.machines = [FakeMachine(sessions=[MOD])]
    async with Client(server) as voice:
        listed = (await voice.call_tool("list_active_sessions", {})).structured_content
        recap = (await voice.call_tool("fleet_recap", {})).structured_content
    for answer in (listed, recap):
        names = [s["name"] for s in answer["sessions"]]
        assert "laptop-a/mod" in names and any("/" not in n for n in names), names


async def test_a_session_on_another_machine_has_a_detail_page_of_its_own(bridge):  # noqa: F811
    server, app, _, _ = bridge
    server.fleet.machines = [FakeMachine(sessions=[MOD, OWN])]
    await server.fleet.refresh()
    async with client(app) as http:
        page = await http.get("/api/v1/sessions/laptop-a%2F840ab631/detail", headers=BEARER)
        summary = await http.post("/api/v1/sessions/laptop-a%2F840ab631/summary", headers=BEARER, json={})
    assert page.status_code == 200
    assert page.json() == {
        "session": "laptop-a/840ab631",
        "number": None,
        "name": "mod",
        "machine": "laptop-a",
        "context": None,
        "tool_calls": {"total": 0, "by_tool": {}},
        "subagents": {"running": 0, "total": 0},
        "timeline": [],
        "summary": None,
        "summary_state": "none",
        "summary_error": None,
        "summary_at": None,
    }
    assert summary.status_code == 404 and "this computer" in summary.json()["error"]

"""The agent protocol as written down: docs/agent-protocol.md and its schemas.

The schemas are checked three ways. Each is a valid JSON schema and its own examples
fit it. The parts version 0 already speaks, pairing and refusals, fit what the agent in
this package really sends. And a version, once frozen, has the same bytes it was frozen
with, because a frozen version is never edited.
"""

import hashlib
import json
import re
from pathlib import Path

import httpx
import jsonschema
import pytest
from referencing import Registry, Resource

from claude_voice import agent

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "agent-protocol.md"
SCHEMAS = ROOT / "docs" / "agent-protocol"
FROZEN = SCHEMAS / "frozen.json"
HUB, HUB_NODE, HERE = "100.64.0.1", "nHUBstable", "100.64.0.10"


def versions():
    return sorted(p for p in SCHEMAS.iterdir() if p.is_dir() and re.fullmatch(r"v\d+", p.name))


def schema_files():
    return [f for v in versions() for f in sorted(v.glob("*.json"))]


def load(path):
    return json.loads(path.read_text())


def schema(name, version="v1"):
    return load(SCHEMAS / version / f"{name}.json")


def validator(body):
    """A validator that finds the other schemas a schema points at, by their $id."""
    registry = Registry().with_resources((load(p)["$id"], Resource.from_contents(load(p))) for p in schema_files())
    return jsonschema.Draft202012Validator(body, registry=registry, format_checker=jsonschema.FormatChecker())


def validate(instance, body):
    validator(body).validate(instance)


def test_the_protocol_has_a_version_one_with_schemas():
    assert DOC.exists()
    assert [v.name for v in versions()][:1] == ["v1"]
    assert schema_files()


@pytest.mark.parametrize("path", schema_files(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_schema_is_valid_and_says_its_version(path):
    body = load(path)
    jsonschema.Draft202012Validator.check_schema(body)
    assert body["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert f"/agent-protocol/{path.parent.name}/{path.name}" in body["$id"]
    assert body.get("title") and body.get("description")


@pytest.mark.parametrize("path", schema_files(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_schema_has_examples_that_fit_it(path):
    body = load(path)
    assert body.get("examples"), "an example shows a reader what the words mean"
    for example in body["examples"]:
        validate(example, body)


def test_every_schema_is_described_and_every_described_schema_exists():
    text = DOC.read_text()
    named = set(re.findall(r"agent-protocol/(v\d+/[a-z-]+\.json)", text))
    present = {f"{p.parent.name}/{p.name}" for p in schema_files()}
    assert named == present


def test_the_version_one_schemas_name_version_one():
    info = schema("agent-info")
    assert info["properties"]["protocol"]["minimum"] == 1
    assert "protocol" in info["required"]
    assert "Claude-Voice-Protocol: 1" in DOC.read_text()


def test_a_frozen_version_is_never_edited():
    frozen = load(FROZEN)
    for version, files in frozen.items():
        present = {p.name for p in (SCHEMAS / version).glob("*.json")}
        assert present == set(files), f"{version} is frozen: no file may be added or removed"
        for name, digest in files.items():
            assert hashlib.sha256((SCHEMAS / version / name).read_bytes()).hexdigest() == digest, (
                f"{version}/{name} is frozen; a change is a new version"
            )


def test_an_example_that_breaks_a_schema_is_refused():
    with pytest.raises(jsonschema.ValidationError):
        validate({"code": "short"}, schema("pair-request"))
    with pytest.raises(jsonschema.ValidationError):
        validate({**schema("agent-info")["examples"][0], "protocol": 0}, schema("agent-info"))


# What version 0, the agent in this package today, already sends must fit the schemas
# for the parts version 1 keeps unchanged.


def whois(address):
    return {HUB: (HUB_NODE, "laptop-a")}.get(address)


def gate(tmp_path):
    pairing = agent.Pairing(tmp_path / "agent")
    server = agent.build_agent("phone-a", pairing, address=HERE)
    return agent.build_agent_app(server, pairing, whois=whois, address=HERE), pairing


async def post(app, path, body, caller=HUB):
    transport = httpx.ASGITransport(app=app, client=(caller, 4321))
    async with httpx.AsyncClient(transport=transport, base_url=f"http://{HERE}:8812") as h:
        return await h.post(path, json=body)


async def test_the_agents_pairing_answer_fits_the_schemas(tmp_path):
    app, pairing = gate(tmp_path)
    code = pairing.start()
    validate({"code": code}, schema("pair-request"))
    answer = await post(app, "/pair", {"code": code})
    assert answer.status_code == 200
    validate(answer.json(), schema("pair-response"))


async def test_the_agents_refusals_fit_the_error_schema(tmp_path):
    app, pairing = gate(tmp_path)
    pairing.start()
    wrong = await post(app, "/pair", {"code": "AAAAAAAA"})
    unpaired = await post(app, "/mcp", {})
    stranger = await post(app, "/pair", {"code": "AAAAAAAA"}, caller="192.0.2.10")
    assert (wrong.status_code, unpaired.status_code, stranger.status_code) == (403, 401, 403)
    for answer in (wrong, unpaired, stranger):
        validate(answer.json(), schema("error"))


def test_a_plugin_that_uses_the_microphone_needs_a_microphone_setting():
    info = schema("agent-info")
    machine = {**info["examples"][0], "privacy": {"microphone": "none", "location_log": False}}
    machine["plugins"] = [{"name": "call", "tools": ["call_start"], "uses": ["microphone"]}]
    with pytest.raises(jsonschema.ValidationError):
        validate(machine, info)
    validate({**machine, "privacy": {"microphone": "must-answer", "location_log": False}}, info)


def test_every_machine_says_whether_it_logs_location():
    info = schema("agent-info")
    machine = {**info["examples"][0], "privacy": {"microphone": "none"}}
    with pytest.raises(jsonschema.ValidationError):
        validate(machine, info)


def test_a_plugin_that_reads_which_apps_are_used_says_so():
    data = {"name": "mobiledata", "tools": ["mobiledata_left"], "uses": ["usage"]}
    validate(data, schema("plugin"))
    assert "`usage`" in DOC.read_text()


# A later version may add fields and values. A receiver of an older one ignores what it
# does not know, so a newer phone or hub never breaks an older one.


@pytest.mark.parametrize("path", schema_files(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_message_lets_a_newer_sender_add_a_field(path):
    body = load(path)
    validate({**body["examples"][0], "added_later": 1}, body)


def test_a_newer_machine_may_report_what_an_older_hub_does_not_know():
    info = schema("agent-info")
    machine = {**info["examples"][0], "protocol": 2}
    machine["plugins"] = [{"name": "radio", "tools": ["radio_tune"], "uses": ["radio"]}]
    machine["privacy"] = {"microphone": "on-request", "location_log": False}
    validate(machine, info)


def test_a_refusal_says_what_went_wrong_in_a_code_a_hub_can_read():
    error = schema("error")
    validate({"error": "this agent speaks version 1", "code": "unsupported_version", "versions": [1]}, error)
    with pytest.raises(jsonschema.ValidationError):
        validate({"error": "bad", "code": "Not A Code"}, error)
    text = DOC.read_text()
    assert "Claude-Voice-Protocol: 1,2" in text
    assert "`unsupported_version`" in text


def test_a_call_carries_an_id_so_a_repeat_is_never_run_twice():
    meta = schema("caller-meta")
    validate({"claude-voice/caller": "voice", "claude-voice/call_id": "c1example0call0id0"}, meta)
    with pytest.raises(jsonschema.ValidationError):
        validate({"claude-voice/call_id": "short"}, meta)
    text = DOC.read_text()
    assert "`duplicate_call`" in text
    assert "may have happened" in text


def test_a_machine_that_has_spoken_version_one_is_never_taken_back_to_version_zero():
    text = DOC.read_text()
    assert "never accepts an answer in a lower version from it again" in text

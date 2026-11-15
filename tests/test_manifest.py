"""The manifest a plugin ships, claude-voice-plugin.toml, and the published format it follows.

The bridge checks a manifest in code; the format is published as a JSON schema for each
plugin's own CI. The two must agree, so every case here is put to both.
"""

import copy
import json
import tomllib
from pathlib import Path

import jsonschema
import pytest

from claude_voice import manifest
from claude_voice.manifest import ManifestError

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = json.loads((ROOT / "docs" / "plugin-manifest" / "v1.json").read_text())
DOC = ROOT / "docs" / "plugin-manifest.md"

FUEL = {
    "manifest": 1,
    "name": "fuel",
    "command": "uv",
    "args": ["run", "fuel-mcp", "serve"],
    "tools": ["fuel_price", "fuel_cheapest"],
    "timeout": 30,
    "branch": "main",
    "uses": ["home"],
    "config": {
        "default": "config.default.toml",
        "dir": "fuel-mcp",
        "personal": [
            {"key": "sources.min_age_minutes", "type": "int", "range": [0, 1440], "ask": "How many minutes?"},
            {"key": "contact", "type": "str", "max_len": 80, "ask": "Your e-mail for MET?"},
            {"key": "fuel", "type": "choice", "values": ["petrol", "diesel"], "ask": "Which fuel?"},
            {"key": "margin", "type": "float", "range": [0, 5], "round": 1, "ask": "Margin?"},
        ],
    },
}


def changed(path, value):
    """FUEL with one value set, or removed when `value` is None; `path` is dotted, a
    number picking from a list."""
    body = copy.deepcopy(FUEL)
    *parents, last = path.split(".")
    at = body
    for part in parents:
        at = at[int(part)] if part.isdigit() else at[part]
    if value is None:
        del at[int(last) if last.isdigit() else last]
    elif last.isdigit():
        at[int(last)] = value
    else:
        at[last] = value
    return body


def schema_says(body):
    return jsonschema.Draft202012Validator(SCHEMA).is_valid(body)


def bridge_says(body):
    try:
        manifest.parse(body)
    except ManifestError:
        return False
    return True


VALID = [
    FUEL,
    changed("uses", None),  # no shared value, so no config needed either
    {"manifest": 1, "name": "easee", "command": "easee-mcp", "tools": ["easee_status"], "secrets_step": "x login"},
]
INVALID = [
    changed("colour", "red"),
    changed("config.colour", "red"),
    changed("config.personal.0.colour", "red"),
    changed("manifest", 2),
    changed("name", "Fuel"),
    changed("name", "fuel-mcp"),
    changed("tools", []),
    changed("command", ""),
    changed("uses", ["home", "car"]),
    changed("timeout", 0),
    changed("config", None),  # uses home without a config to write home_file into
    changed("config.dir", "claude-voice"),
    changed("config.dir", "systemd"),
    changed("config.dir", "../evil"),
    changed("config.dir", "a/b"),
    changed("config.default", "../outside.toml"),
    changed("config.default", "/etc/passwd"),
    changed("config.personal.0.type", "secret"),
    changed("config.personal.0.key", "api_key"),
    changed("config.personal.0.key", "easee.password"),
    changed("config.personal.0.key", "home_file"),
    changed("config.personal.0.key", "Bad Key"),
    changed("config.personal.0.ask", ""),
    changed("config.personal.0.round", 2),  # round is for floats only
    changed("config.personal.1.max_len", None),
    changed("config.personal.2.values", []),
    changed("config.personal.3.round", 7),
]

# One case for each constraint the published format makes, breaking only that one: the
# walk below requires it, so a constraint added to the schema needs a case, and the
# agreement test then requires the bridge to refuse it too.
ONE_EACH = [
    [],  # not a table at all
    changed("manifest", None),  # required
    changed("name", 5),
    changed("command", 5),
    changed("args", "run"),
    changed("args", [5]),
    changed("tools", "fuel_price"),
    changed("tools", ["fuel_price", "fuel_price"]),
    changed("tools", [5]),
    changed("tools", ["fuel price"]),
    changed("timeout", "30"),
    changed("branch", 5),
    changed("branch", ""),
    changed("uses", "home"),
    changed("uses", ["home", "home"]),
    changed("secrets_step", 5),
    changed("secrets_step", ""),
    changed("config", "fuel-mcp"),
    changed("config.default", None),
    changed("config.default", 5),
    changed("config.dir", 5),
    changed("config.personal", "x"),
    changed("config.personal.0", "x"),
    changed("config.personal.0.key", None),
    changed("config.personal.0.key", 5),
    changed("config.personal.0.ask", 5),
    changed("config.personal.0.range", "0-1440"),
    changed("config.personal.0.range", ["0", 1440]),
    changed("config.personal.0.range", [0, "1440"]),
    changed("config.personal.0.range", [0]),
    changed("config.personal.0.range", [0, 1, 2]),
    changed("config.personal.0.max_len", 5),
    changed("config.personal.1.range", [0, 5]),
    changed("config.personal.1.max_len", "80"),
    changed("config.personal.1.max_len", 0),
    changed("config.personal.1.max_len", 1001),
    changed("config.personal.2.values", "petrol"),
    changed("config.personal.2.values", ["petrol", "petrol"]),
    changed("config.personal.2.values", [5]),
    changed("config.personal.2.values", [""]),
    changed("config.personal.2.values", None),
    changed("config.personal.2.range", [0, 5]),
    changed("config.personal.3.round", 1.5),
    changed("config.personal.3.round", -1),
    changed("config.personal.3.values", ["a"]),
]
INVALID += ONE_EACH
# What a constraint is: a keyword that can refuse a manifest by itself. The insides of
# `not` and `if` are conditions, not constraints of their own, so the walk does not go there.
CONSTRAINTS = {
    "type",
    "const",
    "enum",
    "pattern",
    "minLength",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "required",
    "additionalProperties",
    "not",
}


def constraints(node, path=()):
    """Every constraint in the schema, as the path jsonschema names when it refuses there."""
    if "$ref" in node:
        node = SCHEMA["$defs"][node["$ref"].removeprefix("#/$defs/")]
    for key, value in node.items():
        if key in CONSTRAINTS:
            yield (*path, key)
        if key == "properties":
            for name, sub in value.items():
                yield from constraints(sub, (*path, key, name))
        elif key in ("items", "then"):
            yield from constraints(value, (*path, key))
        elif key in ("allOf", "prefixItems"):
            for number, sub in enumerate(value):
                yield from constraints(sub, (*path, key, number))


def refused_at(body):
    return {tuple(e.absolute_schema_path) for e in jsonschema.Draft202012Validator(SCHEMA).iter_errors(body)}


def test_every_constraint_in_the_published_format_has_a_case_that_breaks_only_it():
    alone = {next(iter(found)) for found in map(refused_at, INVALID) if len(found) == 1}
    missing = sorted("/".join(map(str, p)) for p in set(constraints(SCHEMA)) - alone)
    assert not missing, f"add a case to ONE_EACH that breaks only: {missing}"


def test_every_manifest_rule_names_the_part_of_the_format_it_mirrors():
    lines = (ROOT / "src/claude_voice/manifest.py").read_text().splitlines()
    marked = [n for n, line in enumerate(lines) if line.strip().startswith("# RULE:")]
    assert marked
    for n in marked:
        said = lines[n + 1].strip()
        assert said.startswith("# schema: "), f"line {n + 2}: say which part of v1.json the rule above mirrors"
        for pointer in said.removeprefix("# schema: ").split(", "):
            at = SCHEMA
            for part in pointer.strip("/").split("/"):
                at = at[int(part)] if isinstance(at, list) else at[part]  # a KeyError names a path that is not there


@pytest.mark.parametrize("body", VALID, ids=lambda b: b["name"])
def test_a_good_manifest_fits_the_published_format_and_the_bridge(body):
    assert schema_says(body) and bridge_says(body)


@pytest.mark.parametrize("body", INVALID, ids=range(len(INVALID)))
def test_a_manifest_the_format_refuses_the_bridge_refuses_too(body):
    assert not schema_says(body) and not bridge_says(body)


def test_the_published_format_is_a_valid_schema_with_examples_that_fit():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["$id"].endswith("/docs/plugin-manifest/v1.json")
    for example in SCHEMA["examples"]:
        assert schema_says(example) and bridge_says(example)


def test_the_page_shows_a_manifest_that_fits():
    text = DOC.read_text()
    block = text.split("```toml\n", 1)[1].split("```", 1)[0]
    body = tomllib.loads(block)
    assert schema_says(body) and bridge_says(body)
    assert "plugin-manifest/v1.json" in text


def test_a_manifest_is_read_into_what_install_needs():
    found = manifest.parse(FUEL)
    assert found.name == "fuel" and found.command == "uv" and found.args == ("run", "fuel-mcp", "serve")
    assert found.tools == ("fuel_price", "fuel_cheapest") and found.timeout == 30 and found.uses == ("home",)
    assert found.config is not None and found.config.dir == "fuel-mcp"
    minutes = found.config.personal[0]
    assert minutes.key == "sources.min_age_minutes" and minutes.field.parse("10") == 10
    bare = manifest.parse(VALID[2])
    assert bare.config is None and bare.uses == () and bare.args == () and bare.branch == "main"
    assert bare.timeout == 30 and bare.secrets_step == "x login"


def test_a_manifest_key_the_format_does_not_know_is_refused():
    with pytest.raises(ManifestError, match="colour"):
        manifest.parse(changed("colour", "red"))
    with pytest.raises(ManifestError, match="colour"):
        manifest.parse(changed("config.personal.0.colour", "red"))


def test_a_manifest_of_another_format_version_is_refused():
    with pytest.raises(ManifestError, match="version"):
        manifest.parse(changed("manifest", 2))


def test_a_manifest_name_outside_the_plugin_name_pattern_is_refused():
    with pytest.raises(ManifestError, match="name"):
        manifest.parse(changed("name", "Fuel"))


def test_a_manifest_without_a_tool_is_refused():
    with pytest.raises(ManifestError, match="tool"):
        manifest.parse(changed("tools", []))


def test_a_shared_value_the_format_does_not_know_is_refused():
    with pytest.raises(ManifestError, match="car"):
        manifest.parse(changed("uses", ["home", "car"]))


def test_a_config_folder_outside_the_pattern_or_used_by_the_system_is_refused():
    for folder in ("claude-voice", "claude-voice-home", "autostart", "../evil", "a/b", "Fuel"):
        with pytest.raises(ManifestError, match="dir"):
            manifest.parse(changed("config.dir", folder))


def test_a_personal_field_that_names_a_secret_is_refused():
    for key in ("api_key", "easee.password", "auth.token", "client_secret"):
        with pytest.raises(ManifestError, match="secret"):
            manifest.parse(changed("config.personal.0.key", key))


def test_a_manifest_is_read_from_the_checkout_and_says_what_is_wrong(tmp_path):
    with pytest.raises(ManifestError, match="does not exist"):
        manifest.read(tmp_path)
    (tmp_path / manifest.FILE).write_text("manifest = [")
    with pytest.raises(ManifestError, match="not valid TOML"):
        manifest.read(tmp_path)
    (tmp_path / manifest.FILE).write_text('manifest = 1\nname = "x"\ncommand = "x"\ntools = ["x_a"]\n')
    assert manifest.read(tmp_path).name == "x"

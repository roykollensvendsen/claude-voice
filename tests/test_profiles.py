"""The profiles: which repositories and plugins a developer gets, with nothing of anyone's own.

A profile is shared as a plain file, so the guard is an allow-list. Every word in it must
be a placeholder, a safe word or the name of one of its own public repositories, and every
number a documented default. A pattern scan for private-looking words runs over the
comments, which a TOML parser would otherwise drop unread.
"""

import pathlib

import pytest

from claude_voice import profiles

ROOT = pathlib.Path(__file__).parent.parent

REPO = """
[[repo]]
name = "weather-mcp"
url = "https://github.com/someone/weather-mcp"
kind = "plugin"
"""

PLUGIN = (
    REPO
    + '''
[[plugin]]
name = "weather"
repo = "weather-mcp"
block = """
[plugins.weather]
command = "{plugins_dir}/weather-mcp/.venv/bin/weather-mcp"
args = ["serve"]
cwd = "{plugins_dir}/weather-mcp"
tools = ["weather_now"]
"""
config."~/.config/weather-mcp/config.toml" = """
contact = "{contact_url}"
[home]
lat = "{home_lat}"
lon = "{home_lon}"
"""
'''
)


def every_profile() -> list[pathlib.Path]:
    return sorted((ROOT / "src/claude_voice").glob("*profile*.toml"))


def test_the_default_profile_is_shipped_with_the_package():
    assert profiles.DEFAULT in every_profile()


@pytest.mark.parametrize("path", every_profile(), ids=lambda p: p.name)
def test_every_shipped_profile_passes_the_guard(path):
    assert profiles.problems(path.read_text(encoding="utf-8")) == []


def test_the_default_profile_names_the_bridge_the_app_and_the_phone_app():
    profile = profiles.load([profiles.DEFAULT])
    assert list(profile.repos) == ["claude-voice", "claude-voice-ui", "claude-voice-mobile"]
    assert profile.repos["claude-voice"].kind == "core"
    assert profile.repos["claude-voice-mobile"].optional
    assert profile.plugins == {}


def test_a_plugin_with_placeholders_only_passes_the_guard():
    assert profiles.problems(PLUGIN) == []


def test_a_profile_repo_is_a_url_on_the_public_host():
    text = REPO.replace("https://github.com/someone/weather-mcp", "http://192.0.2.7/weather-mcp")
    assert any("not a repository on https://github.com" in p for p in profiles.problems(text))


def test_every_word_in_a_profile_is_a_placeholder_or_a_safe_word():
    real = PLUGIN.replace('lat = "{home_lat}"', 'lat = "trondheim"')
    assert any("trondheim" in p for p in profiles.problems(real))


def test_a_device_in_a_plugin_command_is_refused():
    device = PLUGIN.replace('args = ["serve"]', 'args = ["serve", "--host", "garage.local"]')
    assert any("garage" in p for p in profiles.problems(device))


def test_a_table_named_after_a_room_is_refused():
    assert any("stue" in p for p in profiles.problems(PLUGIN.replace("[home]", "[stue]")))


def test_a_plugin_named_after_a_home_device_is_refused():
    named = PLUGIN.replace('name = "weather"', 'name = "carport"').replace("[plugins.weather]", "[plugins.carport]")
    assert any("carport" in p for p in profiles.problems(named))


def test_a_placeholder_named_after_something_private_is_refused():
    assert any("anna" in p for p in profiles.problems(PLUGIN.replace("{contact_url}", "{anna_phone}")))


def test_a_number_in_a_profile_is_a_documented_default():
    assert profiles.problems(PLUGIN.replace('args = ["serve"]', "timeout = 30")) == []
    found = profiles.problems(PLUGIN.replace('lat = "{home_lat}"', "lat = 63.43"))
    assert any("63.43" in p for p in found)


def test_a_template_that_does_not_parse_is_refused():
    found = profiles.problems(PLUGIN.replace('lat = "{home_lat}"', "lat = {home_lat}"))
    assert any("does not parse" in p for p in found)


def test_a_plugin_block_sets_only_its_own_plugin():
    other = PLUGIN.replace("[plugins.weather]", "[plugins.weather_mcp]")
    assert any("only [plugins.weather]" in p for p in profiles.problems(other))


def layer(folder, name, text):
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def test_a_profile_plugin_comes_from_a_repo_the_merged_profile_lists(tmp_path):
    """Asked of the layers together: one layer's plugin may use another's repo, and a later
    layer that removes the repo leaves the plugin with none, which names every layer read."""
    first = layer(tmp_path, "first.toml", REPO)
    mine = layer(tmp_path, "mine.toml", PLUGIN.replace(REPO, ""))
    assert "weather" in profiles.load([first, mine]).plugins
    local = layer(tmp_path, "profile.local.toml", 'remove = ["weather-mcp"]\n')
    with pytest.raises(profiles.ProfileError) as refused:
        profiles.load([first, mine, local])
    said = str(refused.value)
    assert "'weather-mcp'" in said and all(p.name in said for p in (first, mine, local))


def test_a_plugin_may_use_the_name_of_a_repo_an_earlier_layer_lists():
    alone = PLUGIN.replace(REPO, "")
    assert any("weather" in p for p in profiles.problems(alone))
    assert profiles.problems(alone, known=("weather-mcp",)) == []


def test_the_upstream_account_appears_only_in_its_three_repositories():
    for url in profiles.UPSTREAM_URLS:
        assert (
            profiles.problems(
                REPO.replace("https://github.com/someone/weather-mcp", url).replace(
                    '"weather-mcp"', f'"{url.rsplit("/", 1)[1]}"', 1
                )
            )
            == []
        )
    other = REPO.replace("someone", profiles.UPSTREAM_OWNER)
    assert any("upstream account" in p for p in profiles.problems(other))


def test_the_upstream_account_is_not_a_free_word():
    named = PLUGIN.replace('args = ["serve"]', f'args = ["serve", "{profiles.UPSTREAM_OWNER}"]')
    assert any(profiles.UPSTREAM_OWNER in p for p in profiles.problems(named))


def test_the_default_profile_names_exactly_the_three_upstream_repositories():
    urls = [r.url for r in profiles.load([profiles.DEFAULT]).repos.values()]
    assert urls == list(profiles.UPSTREAM_URLS)


def test_a_config_template_goes_under_the_users_config_folder():
    elsewhere = PLUGIN.replace('config."~/.config/weather-mcp/config.toml"', 'config."/etc/weather-mcp/config.toml"')
    assert any("~/.config/" in p for p in profiles.problems(elsewhere))


def test_a_profile_comment_starts_its_line():
    inline = REPO.replace('kind = "plugin"', 'kind = "plugin"  # mine')
    assert any("line 5" in p and "start" in p for p in profiles.problems(inline))


def test_a_private_word_in_a_profile_comment_is_refused():
    for private in ("192.168.1.20", "63.4305, 10.3951", "someone@example.com", "garage.local"):
        found = profiles.problems(f"# at {private}\n" + REPO)
        assert any("line 1" in p and "private" in p for p in found), private


def test_a_plain_comment_passes():
    assert profiles.problems("# The repositories anyone can use.\n" + REPO) == []


def test_a_profile_that_is_not_toml_says_so():
    assert any("does not parse" in p for p in profiles.problems("[[repo]\n"))


def test_an_unknown_table_in_a_profile_is_refused():
    assert any("unknown" in p for p in profiles.problems(REPO + "\n[secrets]\n"))


def test_a_later_profile_layer_replaces_an_entry_by_name():
    first = profiles.parse(REPO, "first")
    second = profiles.parse(REPO.replace("someone", "another"), "second")
    profile = profiles.merge([first, second])
    assert profile.repos["weather-mcp"].url == "https://github.com/another/weather-mcp"


def test_a_profile_layer_can_remove_an_entry_by_name():
    first = profiles.parse(PLUGIN, "first")
    second = profiles.parse('remove = ["weather"]\n', "second")
    profile = profiles.merge([first, second])
    assert "weather" not in profile.plugins and "weather-mcp" in profile.repos


def test_the_layers_are_the_default_then_the_users_then_this_machines(tmp_path):
    home = tmp_path
    mine = home / "mine.toml"
    assert profiles.layers(mine, home) == [
        profiles.DEFAULT,
        mine,
        home / ".config/claude-voice/profile.local.toml",
    ]
    assert profiles.layers(None, home) == [profiles.DEFAULT, home / ".config/claude-voice/profile.local.toml"]


def test_a_layer_that_does_not_exist_is_skipped(tmp_path):
    profile = profiles.load([profiles.DEFAULT, tmp_path / "none.toml"])
    assert "claude-voice" in profile.repos


def test_a_broken_layer_names_its_file_and_the_fix(tmp_path):
    broken = tmp_path / "profile.toml"
    broken.write_text("[[repo]\n", encoding="utf-8")
    with pytest.raises(profiles.ProfileError, match=r"profile\.toml.*Fix it"):
        profiles.load([broken])

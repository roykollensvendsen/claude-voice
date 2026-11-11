"""The org chart: programmes, components and roles, read from each programme's own file."""

import pytest

from claude_voice import org

ACME = """
[programme]
code = "acme"
name = "Acme Robots"
aliases = ["robots"]
order = 2

[components.arm]
name = "Robot arm"
aliases = ["the arm"]
folders = ["."]

[components.vision]
name = "Vision"
folders = ["../acme-vision"]
remotes = ["acme/vision"]

[roles.manager]
name = "Manager"
purpose = "Keeps the overview."
sessions = ["acme-robots"]
limits = "Never merges without the owner."
send_here = ["decisions", "proposals"]

[roles.quality]
component = "arm"
name = "Quality"
purpose = "Watches the tests."
sessions = ["arm-quality"]
limits = "Changes tests only."
send_here = ["quality"]
"""

SHOP = """
[programme]
code = "shop"
name = "Web shop"
order = 1

[components.cart]
name = "Cart"
folders = ["."]

[roles.manager]
name = "Manager"
purpose = "Runs the shop work."
sessions = ["shop"]
"""


@pytest.fixture
def home(tmp_path):
    for name, text in (("acme-robots", ACME), ("shop", SHOP)):
        (tmp_path / name / ".claude").mkdir(parents=True)
        (tmp_path / name / ".claude" / "org.toml").write_text(text)
    (tmp_path / "acme-vision").mkdir()
    (tmp_path / "loose").mkdir()
    return tmp_path


def chart(home, remotes=None):
    folders = [home / "acme-robots", home / "shop", home / "loose", home / "acme-robots"]
    return org.load(folders, remotes=remotes or (lambda folder: ()))


def row(home, folder, name, sid=None, **more):
    return {"sessionId": sid or name, "name": name, "cwd": str(home / folder), **more}


def test_each_programme_comes_from_its_own_file_in_order(home):
    c = chart(home)
    assert [p.code for p in c.programmes] == ["shop", "acme"]
    assert c.listing() == [
        {"id": "shop", "name": "Web shop", "order": 0},
        {"id": "acme", "name": "Acme Robots", "order": 1},
    ]
    assert c.problems == []


def test_a_worker_is_placed_by_its_folder(home):
    place = chart(home).place(str(home / "acme-robots" / ".claude" / "worktrees" / "a1"), ["fix-gripper"])
    assert place.fields() == {
        "programme": {"id": "acme", "name": "Acme Robots"},
        "component": {"id": "arm", "name": "Robot arm"},
        "role": None,
        "programme_wide": False,
        "address": "acme/arm",
    }


def test_the_deepest_matching_folder_places_a_session(home):
    text = ACME.replace('folders = ["../acme-vision"]', 'folders = ["camera"]')
    (home / "acme-robots" / ".claude" / "org.toml").write_text(text)
    (home / "acme-robots" / "camera").mkdir()
    place = chart(home).place(str(home / "acme-robots" / "camera"), ["x"])
    assert place.component.code == "vision"


def test_a_session_in_no_programme_is_left_out(home):
    place = chart(home).place(str(home / "loose"), ["loose"])
    assert place.fields() == {
        "programme": None,
        "component": None,
        "role": None,
        "programme_wide": False,
        "address": None,
    }


def test_a_session_s_role_comes_from_its_name(home):
    place = chart(home).place(str(home / "acme-robots"), ["arm-quality"])
    assert place.role.code == "quality" and place.address == "acme/arm/quality"


def test_a_programme_wide_role_leaves_the_component_out_of_the_address(home):
    place = chart(home).place(str(home / "acme-robots"), ["acme-robots"])
    assert place.fields()["component"] is None
    assert place.fields()["programme_wide"] is True
    assert place.address == "acme/manager"


def test_a_role_places_a_session_whose_folder_is_elsewhere(home):
    place = chart(home).place(str(home / "loose"), ["old-name", "acme-robots"])
    assert place.address == "acme/manager"


def test_a_remote_places_a_session_no_folder_matched(home):
    remotes = {str(home / "loose"): ("git@github.com:acme/vision.git",)}
    place = chart(home, remotes=lambda folder: remotes.get(folder, ())).place(str(home / "loose"), ["x"])
    assert place.address == "acme/vision"


def test_a_folder_in_an_org_file_must_be_relative(home):
    text = ACME.replace('folders = ["../acme-vision"]', 'folders = ["/elsewhere", "~/acme-vision"]')
    (home / "acme-robots" / ".claude" / "org.toml").write_text(text)
    c = chart(home)
    vision = c.programme("acme").component("vision")
    assert vision.folders == ()
    assert any("/elsewhere" in p for p in c.problems) and any("~/acme-vision" in p for p in c.problems)


def test_a_file_that_cannot_be_read_is_a_problem_not_a_crash(home):
    (home / "shop" / ".claude" / "org.toml").write_text("[programme\n")
    c = chart(home)
    assert [p.code for p in c.programmes] == ["acme"]
    assert any("shop" in p for p in c.problems)


def test_a_programme_without_a_code_is_a_problem(home):
    (home / "shop" / ".claude" / "org.toml").write_text('[programme]\nname = "Shop"\n')
    c = chart(home)
    assert [p.code for p in c.programmes] == ["acme"]
    assert any("code" in p for p in c.problems)


def test_the_same_programme_twice_is_read_once(home):
    (home / "copy" / ".claude").mkdir(parents=True)
    (home / "copy" / ".claude" / "org.toml").write_text(SHOP)
    c = org.load([home / "shop", home / "copy"], remotes=lambda f: ())
    assert [p.code for p in c.programmes] == ["shop"]
    assert any("twice" in p for p in c.problems)


# -- finding a session by its address ---------------------------------------------------


def rows_of(home):
    return [
        row(home, "acme-robots", "acme-robots"),
        row(home, "acme-robots", "arm-quality"),
        row(home, "acme-robots", "fix-gripper"),
        row(home, "shop", "shop"),
        row(home, "loose", "loose"),
    ]


def find(home, words):
    c = chart(home)
    return org.find(rows_of(home), words, c.placer())


def test_a_full_address_finds_its_session(home):
    assert find(home, "acme/arm/quality")["name"] == "arm-quality"


def test_the_end_of_an_address_finds_its_session(home):
    assert find(home, "arm/quality")["name"] == "arm-quality"
    assert find(home, "quality")["name"] == "arm-quality"


def test_a_name_or_an_alias_stands_for_a_code(home):
    assert find(home, "Robots/Manager")["name"] == "acme-robots"
    assert find(home, "the arm/quality")["name"] == "arm-quality"


def test_an_address_that_fits_several_sessions_is_answered_with_the_choices(home):
    with pytest.raises(org.Ambiguous) as caught:
        find(home, "manager")
    assert caught.value.choices == ["acme/manager (acme-robots)", "shop/manager (shop)"]
    assert "acme/manager" in str(caught.value)


def test_an_address_that_fits_nothing_finds_nothing(home):
    assert find(home, "acme/vision") is None
    assert find(home, "loose/x") is None
    assert find(home, "") is None


# -- the check --------------------------------------------------------------------------


def test_a_role_naming_a_session_that_is_not_running_is_warned_about(home):
    warnings = org.check(chart(home), running={"acme-robots"})
    assert "acme: role quality names session 'arm-quality', which is not running" in warnings
    assert not any("acme-robots" in w for w in warnings)


def test_a_folder_that_does_not_exist_is_warned_about(home):
    (home / "acme-vision").rmdir()
    warnings = org.check(chart(home), running={"acme-robots", "arm-quality", "shop"})
    assert any("folder ../acme-vision does not exist" in w for w in warnings)


def test_a_role_in_an_unknown_component_or_sending_an_unknown_kind_is_warned_about(home):
    text = ACME.replace('component = "arm"', 'component = "legs"').replace('["quality"]', '["gossip"]')
    (home / "acme-robots" / ".claude" / "org.toml").write_text(text)
    warnings = org.check(chart(home), running={"acme-robots", "arm-quality", "shop"})
    assert any("legs" in w for w in warnings) and any("gossip" in w for w in warnings)


# -- what the voice hears, and the page's org view -------------------------------------


def test_the_overview_says_who_fills_each_role_and_whether_it_runs(home):
    c = chart(home)
    rows = [r for r in rows_of(home) if r["name"] != "arm-quality"]
    lines = org.overview(c, rows, c.placer())
    assert lines[0] == "Web shop (shop): manager, shop, running."
    assert "Acme Robots (acme): manager, acme-robots, running; send decisions, proposals." in lines
    assert "Acme Robots (acme): arm quality, arm-quality, not running; send quality." in lines
    assert lines[-1] == "Outside any programme: loose."


def test_the_org_view_flags_a_role_with_no_running_session(home):
    c = chart(home)
    rows = [r for r in rows_of(home) if r["name"] != "arm-quality"]
    out = org.view(c.programme("acme"), rows, c.placer())
    assert out["id"] == "acme" and out["name"] == "Acme Robots"
    manager, quality = out["roles"]
    assert manager == {
        "id": "manager",
        "name": "Manager",
        "component": None,
        "programme_wide": True,
        "address": "acme/manager",
        "purpose": "Keeps the overview.",
        "limits": "Never merges without the owner.",
        "routing": ["decisions", "proposals"],
        "session_names": ["acme-robots"],
        "sessions": ["acme-robots"],
        "running": True,
    }
    assert quality["component"] == {"id": "arm", "name": "Robot arm"} and quality["running"] is False
    assert out["unfilled"] == ["quality"]
    assert out["components"][0] == {"id": "arm", "name": "Robot arm", "aliases": ["the arm"]}

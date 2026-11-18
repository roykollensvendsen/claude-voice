"""Hiding secrets in text the bridge sends, before it is cut short."""

import pytest

from claude_voice import redact

GHP = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"

SAMPLES = [
    GHP,
    "gho_" + "x" * 36,
    "ghs_" + "y" * 36,
    "ghu_" + "z" * 36,
    "ghr_" + "w" * 36,
    "github_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz",
    "sk-" + "proj-abcdefghijklmnopqrstuvwxyz012345",
    "xoxb-" + "123456789012-abcdefghijkl",
    "AKIA" + "ABCDEFGHIJKLMNOP",
]


@pytest.mark.parametrize("secret", SAMPLES)
def test_every_token_shape_is_hidden(secret):
    out = redact.mask(f"run it with {secret} now")
    assert secret not in out and "[hidden]" in out and out.startswith("run it with ")


def test_a_private_key_block_is_hidden_whole():
    block = "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk\nAAAA\n-----END OPENSSH PRIVATE KEY-----"
    out = redact.mask(f"cat <<EOF\n{block}\nEOF")
    assert "b3BlbnNzaC1rZXk" not in out and "AAAA" not in out and "[hidden]" in out


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ("curl -H 'Authorization: Bearer abc.def.ghi' x", "curl -H 'Authorization: "),
        ("curl -H 'Authorization: Basic dXNlcjpwYXNz' x", "curl -H 'Authorization: "),
        ("Authorization: s3cr3tvalue", "Authorization: "),
        ("token=abc123 rest", "token="),
        ("API_KEY: zzz999", "API_KEY: "),
        ("password=hunter2", "password="),
        ("passwd:hunter2", "passwd:"),
        ("apikey=q1w2e3", "apikey="),
        ("auth=abc", "auth="),
        ("secret = 'deep'", "secret = "),
        ("export GH_TOKEN=abc123", "export GH_TOKEN="),
        ("MY_PASS=abc123 ./run.sh", "MY_PASS="),
    ],
)
def test_the_value_after_a_secret_name_is_hidden(text, kept):
    out = redact.mask(text)
    assert out.startswith(kept) and "[hidden]" in out
    for value in ("abc", "dXNlcjpwYXNz", "s3cr3tvalue", "zzz999", "hunter2", "q1w2e3", "deep"):
        assert value not in out


def test_every_url_query_value_and_its_userinfo_are_hidden():
    out = redact.mask("open https://bob:pw123@example.com/a/b?code=xyz&state=s1 please")
    assert out == "open https://[hidden]@example.com/a/b?code=[hidden]&state=[hidden] please"


def test_ordinary_text_and_a_plain_url_path_are_unchanged():
    text = "Do you want to run git status in https://github.com/acme/arm/pull/12? 1 Yes 2 No"
    assert redact.mask(text) == text


def test_secrets_are_hidden_before_the_text_is_cut():
    text = "x" * 190 + " " + GHP + " and more"
    out = redact.hide(text, 200)
    assert len(out) <= 200 and "ghp_" not in out and "A1b2" not in out
    assert out.endswith("[hidden]") or "[hidden]" in out


def test_a_short_text_is_not_cut():
    assert redact.hide("Run the tests?", 200) == "Run the tests?"

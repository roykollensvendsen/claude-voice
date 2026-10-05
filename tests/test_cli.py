from claude_voice.cli import COMMANDS, main


class Recorder:
    def __init__(self):
        self.configs = []

    def __call__(self, cfg):
        self.configs.append(cfg)


def env(tmp_path, **extra):
    return {"CLAUDE_VOICE_ROOT": str(tmp_path), **extra}


def test_the_command_has_serve_and_check():
    assert COMMANDS == ("serve", "check")


def test_serve_starts_the_bridge_with_the_chosen_transport(tmp_path):
    serve = Recorder()
    code = main(["serve", "--transport", "stdio"], env=env(tmp_path), serve=serve)
    assert code == 0
    assert serve.configs[0].transport == "stdio"


def test_no_verb_means_serve_so_existing_service_files_keep_working(tmp_path):
    serve = Recorder()
    token = {"CLAUDE_VOICE_TOKEN": "x" * 32}
    main(["--transport", "http"], env=env(tmp_path, **token), serve=serve)
    assert serve.configs[0].transport == "http"


def test_check_reports_a_usable_configuration_without_serving(tmp_path, capsys):
    serve = Recorder()
    code = main(["check"], env=env(tmp_path), serve=serve)
    assert code == 0
    assert serve.configs == []
    assert capsys.readouterr().out.strip() == (f"claude-voice: ready to serve over stdio; projects under {tmp_path}")


def test_a_bad_configuration_is_one_line_and_exit_code_one(tmp_path, capsys):
    code = main(["check"], env=env(tmp_path, ANTHROPIC_API_KEY="sk-x"), serve=Recorder())
    assert code == 1
    err = capsys.readouterr().err.strip()
    assert err.startswith("claude-voice: ANTHROPIC_API_KEY is set")
    assert "\n" not in err

"""The command, as a command.

This is the code a first run touches and the code whose failures a person meets with no
interface to explain them, and it had no test file at all. Nothing here starts a server,
opens a browser, reaches a model or reaches a warehouse: what is being pinned down is what
the command does with its arguments, what it prints, and what it exits with.
"""

import sys

import pytest

from vizmith import cli, config

PROFILE = "VIZMITH_DATABRICKS_PROFILE"
KEY = "VIZMITH_MODEL_KEY"


@pytest.fixture(autouse=True)
def elsewhere(tmp_path, monkeypatch):
    """A state directory and a working directory of their own, so a test never reads the
    configuration of whoever is running the suite and never writes over it."""
    monkeypatch.setenv("VIZMITH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    for name, _ in config.SETTINGS:
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def run(monkeypatch, *argv: str) -> int:
    """The command as a shell runs it, down to the exit code."""
    monkeypatch.setattr(sys, "argv", ["vizmith", *argv])
    with pytest.raises(SystemExit) as exited:
        cli.main()
    return exited.value.code


def test_a_flag_writes_the_setting_it_names(monkeypatch, capsys):
    code = run(monkeypatch, "configure", "--databricks-profile", "work", "--model-key", "sekrit")

    assert code == 0
    assert config.read() == {PROFILE: "work", KEY: "sekrit"}
    assert str(config.config_path()) in capsys.readouterr().out


def test_a_flag_run_never_asks(monkeypatch):
    """Flags are what a script has and what a session with no terminal has, so a run that
    was given one must not stop on a prompt nobody is there to answer."""

    def refuse(*args, **kwargs):
        raise AssertionError("the command asked when it was told")

    monkeypatch.setattr("builtins.input", refuse)
    monkeypatch.setattr(cli.getpass, "getpass", refuse)

    assert run(monkeypatch, "configure", "--databricks-catalog", "vizmith") == 0


def test_show_prints_where_each_setting_comes_from_and_never_a_value(monkeypatch, capsys):
    """The key is the reason this rule exists: a value printed to a terminal is a value in a
    scrollback buffer and in whatever is recording it."""
    run(monkeypatch, "configure", "--databricks-profile", "work", "--model-key", "sekrit")
    capsys.readouterr()

    code = run(monkeypatch, "configure", "--show")

    printed = capsys.readouterr().out
    assert code == 0
    assert "sekrit" not in printed
    assert "work" not in printed
    for name, _ in config.SETTINGS:
        assert name in printed
    assert f"{KEY}: set in {config.CONFIG_FILE}" in printed


def test_a_run_with_nothing_to_set_and_no_terminal_says_what_to_do(monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

    code = run(monkeypatch, "configure")

    said = capsys.readouterr().err
    assert code == 2
    assert "--help" in said
    assert not config.config_path().exists(), "a refusal wrote a file"


def test_a_terminal_is_asked_one_question_per_setting(monkeypatch, capsys):
    """The path a person on their own machine takes. An empty answer keeps what is there,
    and the key is read without echoing it."""
    run(monkeypatch, "configure", "--databricks-profile", "work")
    capsys.readouterr()

    asked = []
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda text: asked.append(text) or "")
    monkeypatch.setattr(cli.getpass, "getpass", lambda text: asked.append(text) or "sekrit")

    code = run(monkeypatch, "configure")

    assert code == 0
    assert len(asked) == len(config.SETTINGS)
    assert any(f"[{'work'}]" in question for question in asked), "what is set is shown back"
    assert not any("sekrit" in question for question in asked)
    assert config.read() == {PROFILE: "work", KEY: "sekrit"}, "an empty answer kept what was there"


def test_eval_with_no_question_set_names_the_path_it_looked_at(monkeypatch, capsys, tmp_path):
    missing = tmp_path / "nowhere" / "questions.json"

    code = run(monkeypatch, "eval", "--questions", str(missing))

    said = capsys.readouterr().err
    assert code == 2
    assert str(missing) in said
    assert "--questions" in said


def test_serve_starts_the_application_on_the_host_and_port_it_was_given(monkeypatch):
    started = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: started.update(app=app, **kwargs))
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: pytest.fail("a test opened a browser"))

    monkeypatch.setattr(sys, "argv", ["vizmith", "serve", "--port", "9123", "--no-browser"])
    cli.main()

    assert started == {"app": "vizmith.api:app", "host": "127.0.0.1", "port": 9123}


def test_serve_opens_the_browser_at_the_address_it_is_serving(monkeypatch):
    """A local application that leaves a person to find the URL in the log is one that made
    them read a log. The timer is what waits for the server rather than a sleep in the
    request path, and this asserts what it was told to open rather than opening it."""
    timers = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: None)
    monkeypatch.setattr(
        cli.threading, "Timer", lambda delay, target, args: timers.append((delay, target, args)) or _Idle()
    )

    monkeypatch.setattr(sys, "argv", ["vizmith", "serve", "--host", "0.0.0.0", "--port", "8123"])
    cli.main()

    assert [args for _, _, args in timers] == [("http://0.0.0.0:8123",)]
    assert all(target is cli.webbrowser.open for _, target, _ in timers)


class _Idle:
    """A timer that is started and never fires, because a test that opened a browser would
    be a test that opened a browser."""

    def start(self) -> None:
        pass


def test_a_command_is_required(monkeypatch, capsys):
    """`vizmith` on its own is somebody who does not know what it does yet, and argparse's
    own message lists the commands."""
    assert run(monkeypatch, "") == 2
    assert "invalid choice" in capsys.readouterr().err

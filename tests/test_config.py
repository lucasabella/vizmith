"""Where configuration comes from, and what is allowed to write it.

Every test here is offline and none of them starts a server. What is being pinned down is
an order — environment, then a checkout's `.env`, then the file the command wrote — and the
two properties that order exists to protect: a checkout keeps behaving exactly as it did,
and a key that lands in a file lands in one nobody else can read.
"""

import os
import stat
from pathlib import Path

import pytest

from vizmith import config
from vizmith.api import WEB_DIST, _web

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


def test_what_the_command_writes_is_what_the_server_reads(elsewhere):
    config.write({PROFILE: "work", KEY: "sekrit"})
    config.load()

    assert os.environ[PROFILE] == "work"
    assert os.environ[KEY] == "sekrit"


def test_a_real_environment_variable_wins_over_the_file(monkeypatch):
    config.write({PROFILE: "from the file"})
    monkeypatch.setenv(PROFILE, "from the environment")

    config.load()

    assert os.environ[PROFILE] == "from the environment"


def test_a_checkouts_own_env_wins_over_the_file(elsewhere):
    """The README documented `.env` before any of this existed, and a checkout that has one
    has to keep working the way it always did."""
    config.write({PROFILE: "from the file"})
    (elsewhere / ".env").write_text(f"{PROFILE}=from the checkout\n")

    config.load()

    assert os.environ[PROFILE] == "from the checkout"


def test_the_file_answers_where_nothing_nearer_does(elsewhere):
    (elsewhere / ".env").write_text(f"{KEY}=from the checkout\n")
    config.write({PROFILE: "from the file", KEY: "from the file"})

    config.load()

    assert os.environ[PROFILE] == "from the file"
    assert os.environ[KEY] == "from the checkout"


def test_the_file_holding_a_key_is_readable_by_nobody_else(elsewhere):
    path = config.write({KEY: "sekrit"})

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_writing_one_setting_keeps_the_others(elsewhere):
    config.write({PROFILE: "work", KEY: "sekrit"})
    config.write({PROFILE: "home"})

    assert config.read() == {PROFILE: "home", KEY: "sekrit"}


def test_an_empty_value_takes_a_setting_back_out(elsewhere):
    config.write({PROFILE: "work"})
    config.write({PROFILE: ""})

    assert PROFILE not in config.read()


def test_the_description_says_which_of_the_three_answered(elsewhere, monkeypatch):
    (elsewhere / ".env").write_text("VIZMITH_DATABRICKS_CATALOG=from the checkout\n")
    config.write({PROFILE: "from the file"})
    monkeypatch.setenv("VIZMITH_DATABRICKS_SCHEMA", "from the environment")

    config.load()
    where = dict(config.described())

    assert where[PROFILE] == f"set in {config.CONFIG_FILE}"
    assert where["VIZMITH_DATABRICKS_CATALOG"] == "set in .env, in the working directory"
    assert where["VIZMITH_DATABRICKS_SCHEMA"] == "set in the environment"
    assert where["VIZMITH_DATABRICKS_WAREHOUSE"] == "not set"


def test_a_setting_the_file_holds_and_something_nearer_overrode_says_so(elsewhere, monkeypatch):
    """A person who edited the file and saw nothing change is owed this sentence."""
    config.write({PROFILE: "from the file"})
    monkeypatch.setenv(PROFILE, "from the environment")

    config.load()

    assert dict(config.described())[PROFILE] == (
        f"set in the environment, which wins over {config.CONFIG_FILE}"
    )


def test_no_description_carries_a_value(elsewhere):
    """The key is the reason this rule exists, and the others are dull enough that showing
    them would only be a habit worth not having."""
    config.write({PROFILE: "work", KEY: "sekrit"})
    config.load()

    said = " ".join(where for _, where in config.described())
    assert "sekrit" not in said
    assert "work" not in said


def test_the_configuration_file_sits_in_the_state_directory(elsewhere):
    assert config.config_path().parent == config.state_dir()
    assert config.config_path() == elsewhere / "state" / config.CONFIG_FILE


def test_the_interface_is_served_from_the_package_where_one_was_built_into_it(tmp_path, monkeypatch):
    """A wheel carries the built interface inside the package, because an installed Vizmith
    has no checkout to serve web/dist out of. A checkout has no such directory and falls
    back to the one npm run build writes, which is what this repository runs on."""
    import vizmith.api

    packaged = tmp_path / "vizmith" / "web"
    packaged.mkdir(parents=True)
    monkeypatch.setattr(vizmith.api, "__file__", str(tmp_path / "vizmith" / "api.py"))

    assert _web() == packaged
    assert WEB_DIST.name == "dist"


def test_the_key_is_never_on_disk_at_a_wider_mode_than_the_file_keeps(elsewhere, monkeypatch):
    """The narrowing happens before the content, not after it. Writing the file and then
    restricting it leaves a window, one chmod long, where a key sits at whatever the umask
    gave it, and on a shared machine that window is the whole of the exposure."""
    seen = []
    replace = os.replace

    def watched(beside, target):
        seen.append((stat.S_IMODE(os.stat(beside).st_mode), Path(beside).read_text()))
        replace(beside, target)

    monkeypatch.setattr(os, "replace", watched)
    config.write({KEY: "sekrit"})

    assert len(seen) == 1
    mode, held = seen[0]
    assert "sekrit" in held, "the temporary file is where the key first lands"
    assert mode == 0o600


def test_two_writes_cannot_share_one_temporary_path(elsewhere, monkeypatch):
    """A fixed name means the second `configure` writes into the first one's file. The
    other three state writers use mkstemp for this, so this is the fourth doing what they
    do rather than a fourth way of doing it."""
    beside = []
    replace = os.replace

    def watched(source, target):
        beside.append(str(source))
        replace(source, target)

    monkeypatch.setattr(os, "replace", watched)
    config.write({KEY: "first"})
    config.write({PROFILE: "second"})

    assert beside[0] != beside[1]
    assert list(config.state_dir().iterdir()) == [config.config_path()]


def test_re_writing_keeps_what_was_not_mentioned(elsewhere):
    config.write({PROFILE: "work", KEY: "sekrit"})
    config.write({PROFILE: "other"})

    assert config.read() == {PROFILE: "other", KEY: "sekrit"}
    assert stat.S_IMODE(config.config_path().stat().st_mode) == 0o600


def test_the_state_directory_is_the_owner_s_and_nobody_else_s(tmp_path, monkeypatch):
    """The files inside were always 0600, because mkstemp makes them that way whatever the
    umask is. The directory was not: created without a mode it lands at 0755, and another
    account on a shared machine can then list it and learn that config.env exists, how
    large it is and when it last changed."""
    monkeypatch.setenv("VIZMITH_STATE_DIR", str(tmp_path / "state"))

    config.write({"VIZMITH_MODEL_KEY": "not-a-real-key"})

    assert stat.S_IMODE((tmp_path / "state").stat().st_mode) == 0o700

"""Where Vizmith's own configuration lives, and what may write it.

Vizmith runs on a person's own machine and serves their own browser, and until there was
something to install, configuration was a `.env` beside a checkout. That is fine for a
checkout and wrong for an installed application: a person who ran `uvx vizmith serve` has
no checkout to put a file next to, and telling them to create one in whatever directory
they happen to be in is telling them to be an operator.

So the configuration a packaged Vizmith reads is a file it owns, in the state directory
beside the profiles, the relationship answers and the dashboards, and `vizmith configure`
is what writes it. What does not write it is the browser. The server's surface stays what
it was — specs and answers about them — and the model key keeps having no path into a
page at all, rather than a rule saying it must not come back out of one. That is the
sentence a governance review reads, and it costs one command to keep.

Three places are read, and the first one that has a value wins:

1. A real environment variable, which is how a container or a launcher sets this.
2. `.env`, found from the working directory upwards, which is what a checkout has and
   what the README documented before any of this existed.
3. `config.env` in the state directory, which is what `vizmith configure` writes.

The file holds a model key, so it is written readable by its owner and nobody else, and
`--show` prints what is set rather than what it is set to.
"""

import os
import stat
import tempfile
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from vizmith.sources import DEFAULT, KINDS
from vizmith.state import hold

CONFIG_FILE = "config.env"

# Every name Vizmith reads, with what it is for. The order is the order `configure` asks
# in, so the kind of source comes first, then the settings that make one of that kind, then
# the three that make an endpoint.
#
# Every kind's settings are listed rather than only the chosen one's, because this is also
# what `.env` documents and what `--show` reports, and a person switching from a warehouse
# to a file should be able to see both halves without editing anything. What decides which
# ones have to be filled in is `VIZMITH_SOURCE`, and `configure` asks only for those.
SETTINGS: tuple[tuple[str, str], ...] = (
    ("VIZMITH_SOURCE", f"Which kind of source this is: {' or '.join(sorted(KINDS))}"),
    ("VIZMITH_DATABRICKS_PROFILE", "A profile in ~/.databrickscfg, from `databricks auth login`"),
    ("VIZMITH_DATABRICKS_CATALOG", "The catalog a spec's table names resolve against"),
    ("VIZMITH_DATABRICKS_SCHEMA", "The schema a spec's table names resolve against"),
    ("VIZMITH_DATABRICKS_WAREHOUSE", "The SQL warehouse that runs the query"),
    ("VIZMITH_DUCKDB_PATH", "The .duckdb file to read, opened read only"),
    ("VIZMITH_DUCKDB_DATABASE", "The database inside it a spec's table names resolve against"),
    ("VIZMITH_DUCKDB_SCHEMA", "The schema inside that database"),
    ("VIZMITH_BIGQUERY_PROJECT", "The Google Cloud project a spec's table names resolve against"),
    ("VIZMITH_BIGQUERY_DATASET", "The dataset inside that project"),
    ("VIZMITH_BIGQUERY_LOCATION", "Where its jobs run, e.g. EU or us-central1. Empty for the default"),
    ("VIZMITH_SNOWFLAKE_CONNECTION", "A connection in ~/.snowflake/connections.toml, which holds the credential"),
    ("VIZMITH_SNOWFLAKE_DATABASE", "The database a spec's table names resolve against"),
    ("VIZMITH_SNOWFLAKE_SCHEMA", "The schema a spec's table names resolve against"),
    ("VIZMITH_SNOWFLAKE_WAREHOUSE", "The warehouse that runs the query"),
    ("VIZMITH_MODEL_BASE_URL", "An OpenAI-compatible base URL, before /chat/completions"),
    ("VIZMITH_MODEL_NAME", "The model that writes a spec, or an Azure deployment name"),
    ("VIZMITH_MODEL_KEY", "The key for that endpoint. Written to a file only you can read"),
)

# The name that says which kind, and what a checkout that predates there being a choice
# gets without setting it.
SOURCE = "VIZMITH_SOURCE"


def kind() -> str:
    """Which source is configured. Read from the environment on every call rather than
    held, because `configure` writes it and `serve` reads it in another process, and a
    value cached at import would be the one that was set when this module was first
    touched."""
    return os.environ.get(SOURCE) or DEFAULT


def source_settings() -> tuple[str, ...]:
    """The settings the configured kind needs, or nothing where the kind is not one this
    knows. Empty rather than raising: `/api/health` asks this to say whether a source is
    configured, and a kind nobody recognises is a source that is not."""
    return KINDS.get(kind(), ())


def asked(chosen: str | None = None) -> tuple[tuple[str, str], ...]:
    """What `configure` asks for: the kind, the settings that kind needs, and the model.
    Not the other kinds' settings, because a person configuring a file should not be asked
    for a warehouse id to leave blank.

    `chosen` is a kind answered in this run and not yet written anywhere, which is what
    makes the first question change the rest of them."""
    wanted = {
        SOURCE,
        *KINDS.get(chosen or kind(), ()),
        *(name for name, _ in SETTINGS if name.startswith("VIZMITH_MODEL")),
    }
    return tuple((name, why) for name, why in SETTINGS if name in wanted)


# What is never printed back, whatever asked for it. A key that reaches a terminal reaches
# a scrollback buffer and whatever is recording it.
SECRET = "VIZMITH_MODEL_KEY"


def state_dir() -> Path:
    """Where the server keeps what it has to remember between runs: what a person answered
    about a suggested relationship, the profiles it has already paid for, the dashboards
    they saved, and the configuration `vizmith configure` wrote. Nothing else.
    `VIZMITH_STATE_DIR` moves it."""
    return Path(os.environ.get("VIZMITH_STATE_DIR") or Path.home() / ".vizmith")


# Which of the three places answered for each setting, recorded while they are read,
# because afterwards they all look the same: everything that was loaded is in the
# environment, and a person checking their setup is asking exactly which one won.
_ANSWERED: dict[str, str] = {}


def load() -> None:
    """Read configuration into the environment, nearest source first.

    `load_dotenv` never overrides a variable that is already set, so the order here is the
    precedence: a real environment variable stays, then a checkout's own `.env`, then the
    file `configure` wrote.

    The command calls this, not the application. Importing `vizmith` should never pull in
    whatever `.env` happens to sit in the working directory."""
    _ANSWERED.clear()
    _ANSWERED.update(dict.fromkeys(_set_now(), "set in the environment"))
    # Searched from the working directory upwards rather than from this file upwards,
    # which is what `load_dotenv()` on its own does: inside an installed package that
    # walks site-packages and finds nothing, and the .env a person means is the one where
    # they started the server.
    load_dotenv(find_dotenv(usecwd=True))
    _ANSWERED.update(dict.fromkeys(_set_now() - set(_ANSWERED), "set in .env, in the working directory"))
    load_dotenv(config_path())
    _ANSWERED.update(dict.fromkeys(_set_now() - set(_ANSWERED), f"set in {CONFIG_FILE}"))


def _set_now() -> set[str]:
    return {name for name, _ in SETTINGS if os.environ.get(name)}


def config_path() -> Path:
    return state_dir() / CONFIG_FILE


def write(values: dict[str, str]) -> Path:
    """Store what was given, keeping what was already there for anything not mentioned.

    Written whole and moved into place, and readable by its owner only, because one of
    these values is a key. An empty string clears a setting rather than storing emptiness,
    so `configure` can take one back out.

    `mkstemp` creates the file at owner read and write before anything is written into it,
    so there is no moment where a key is on disk at whatever the umask gave it. It also
    gives a unique name, so two `configure` runs at once cannot write to one temporary
    path and hand each other half a file."""
    path = config_path()
    stored = read()
    for name, value in values.items():
        if value == "":
            stored.pop(name, None)
        else:
            stored[name] = value

    hold(path.parent)
    lines = [
        "# Written by `vizmith configure`. A real environment variable and a .env in the",
        "# working directory both win over this file.",
        *(f"{name}={stored[name]}" for name, _ in SETTINGS if name in stored),
        "",
    ]
    handle, beside = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(handle, "w") as writing:
            writing.write("\n".join(lines))
        # mkstemp already creates at 0600. Said again here because it is the mode the file
        # keeps after the move, and that is the promise the docstring above makes.
        os.chmod(beside, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(beside, path)
    except BaseException:
        Path(beside).unlink(missing_ok=True)
        raise
    return path


def read() -> dict[str, str]:
    """What the file holds, as it holds it. Nothing here reaches the environment: that is
    `load`'s job and it has an order to keep."""
    path = config_path()
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        values[name.strip()] = value.strip()
    return values


def described() -> list[tuple[str, str]]:
    """Every setting and where its value came from, for a person checking their own setup.

    The value itself is not here: the four that name a source are dull, the key is not
    printable, and what somebody needs to know is which of the three places answered. A
    setting the file holds and something nearer overrode is said in full, because a person
    who edited the file and saw nothing change is owed that sentence."""
    stored = read()
    out = []
    for name, _ in SETTINGS:
        where = _ANSWERED.get(name)
        if where is None:
            where = "set in the environment" if os.environ.get(name) else "not set"
        if name in stored and where != f"set in {CONFIG_FILE}" and where != "not set":
            where += f", which wins over {CONFIG_FILE}"
        out.append((name, where))
    return out

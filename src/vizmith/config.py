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
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

CONFIG_FILE = "config.env"

# Every name Vizmith reads, with what it is for. The order is the order `configure` asks
# in, so the four that make a source come before the three that make an endpoint.
SETTINGS: tuple[tuple[str, str], ...] = (
    ("VIZMITH_DATABRICKS_PROFILE", "A profile in ~/.databrickscfg, from `databricks auth login`"),
    ("VIZMITH_DATABRICKS_CATALOG", "The catalog a spec's table names resolve against"),
    ("VIZMITH_DATABRICKS_SCHEMA", "The schema a spec's table names resolve against"),
    ("VIZMITH_DATABRICKS_WAREHOUSE", "The SQL warehouse that runs the query"),
    ("VIZMITH_MODEL_BASE_URL", "An OpenAI-compatible base URL, before /chat/completions"),
    ("VIZMITH_MODEL_NAME", "The model that writes a spec, or an Azure deployment name"),
    ("VIZMITH_MODEL_KEY", "The key for that endpoint. Written to a file only you can read"),
)

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
    so `configure` can take one back out."""
    path = config_path()
    stored = read()
    for name, value in values.items():
        if value == "":
            stored.pop(name, None)
        else:
            stored[name] = value

    path.parent.mkdir(parents=True, exist_ok=True)
    beside = path.with_name(path.name + ".writing")
    lines = [
        "# Written by `vizmith configure`. A real environment variable and a .env in the",
        "# working directory both win over this file.",
        *(f"{name}={stored[name]}" for name, _ in SETTINGS if name in stored),
        "",
    ]
    beside.write_text("\n".join(lines))
    beside.chmod(stat.S_IRUSR | stat.S_IWUSR)
    os.replace(beside, path)
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

"""The files the server owns, and what it does with one it cannot read.

Four things are kept in the state directory: the profile cache, the relationship answers,
the dashboards, and the configuration `vizmith configure` wrote. The cache may throw
itself away, because an unreadable cache costs one more profile. The other two files hold
work a person did, so a file that cannot be read is refused rather than started empty: one
that quietly became an empty file would be answered by the next save overwriting
everything that was in it.

What this module adds is the shape of that refusal. A `JSONDecodeError` travelling out of
a constructor reaches a person as a 500 with a stack trace and no file name in it, which
tells them nothing they can act on. `Damaged` names the path, so the sentence they read is
the one that says which file to move.
"""

import json
import os
import tempfile
from pathlib import Path


class Damaged(Exception):
    """A state file that cannot be read as what it is.

    Carries the path, because moving that file aside is the whole of the remedy and a
    refusal that does not name it leaves a person searching for which of four it was."""

    def __init__(self, path: Path, reason: str):
        super().__init__(
            f"'{path}' cannot be read: {reason}. Nothing was changed. Move that file aside "
            f"to start a new one, and what is in it is still there to look at."
        )
        self.path = path


def stored(path: Path, key: str) -> dict:
    """What the file holds under `key`, or nothing where the file does not exist yet.

    A file that is absent is a first run. A file that is present and is not an object with
    an object under that key is damage, whether it was truncated mid write or edited by
    hand, and it is refused in one place so that both stores refuse it the same way."""
    if not path.is_file():
        return {}
    try:
        written = json.loads(path.read_text())
    except (OSError, ValueError) as failure:
        raise Damaged(path, str(failure)) from failure
    if not isinstance(written, dict) or not isinstance(written.get(key), dict):
        raise Damaged(path, f"it holds no object under '{key}'")
    return written[key]


def write(path: Path, written: str) -> None:
    """Write a state file whole: beside itself, then moved onto the target.

    `os.replace` is atomic, so a reader arriving mid write finds the last whole file rather
    than half of the next one, and a write that is interrupted leaves the file it had. Every
    state file goes through one of these, because the file most worth keeping whole is the
    one holding answers a person typed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, beside = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(handle, "w") as writing:
            writing.write(written)
        os.replace(beside, path)
    except BaseException:
        Path(beside).unlink(missing_ok=True)
        raise

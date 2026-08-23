"""Who may ask, when the server answers to more than one person.

Vizmith's default posture is one person on their own machine: the server refuses any
request that did not arrive addressed to this machine by this machine, and there is
nothing to authenticate because there is nobody else to be. A deployment that is reachable
beyond loopback on purpose needs more than that posture, so shared deployment mode adds
the thing the default deliberately does not have: an authenticated principal on every
request that can reach data or state.

The principal proves itself with a bearer token the operator issued through
`vizmith user add`. Tokens are generated here, shown once, and stored only as a SHA-256
digest — the same discipline the model key is kept to, because a users file readable by
its owner alone is still a file somebody can copy. A digest cannot be turned back into
the token that issued it, so the file answers "is this the token I gave out" and nothing
else.

What authentication is not here is authorisation against the warehouse. Every principal
reads the same configured source through the same rules — the scope, the validator, the
confirmed-join rule and the sample threshold all sit below this module and do not know it
exists. What a principal owns is state: dashboards saved and relationship answers given
land under `<state>/users/<name>/`, where no other principal's request looks. The profile
cache stays shared, because a profile is derived from metadata nobody asked a question of
and caching it twice buys nothing but bills.

The default mode keeps working exactly as it did, with no tokens, no users file and no
identity provider: `VIZMITH_SHARED_MODE` unset means the single local user this project
has always served.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from vizmith.config import state_dir
from vizmith.state import hold, stored, write

# Set to 1, yes or true. Anything else is off, because a typo turning a deployment into
# an unauthenticated one should fail towards the mode that has worked since the start.
SHARED_MODE = "VIZMITH_SHARED_MODE"

USERS_FILE = "users.json"

# Where a principal's own state lands, under the state directory.
USER_STATE = "users"

# A name becomes one path segment under `<state>/users/`, so it may carry nothing that
# addresses anywhere else. Starting it with an alphanumeric also refuses `.` and `..`,
# which is what stops a name from naming the directory above its own.
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def shared_mode() -> bool:
    return os.environ.get(SHARED_MODE, "").strip().lower() in ("1", "yes", "true")


def users_path() -> Path:
    return state_dir() / USERS_FILE


@dataclass(frozen=True)
class Principal:
    """Who a request says it is, once a token has answered for it.

    The name is what state is scoped by and what rations are keyed by, so it is carried
    as given rather than re-derived from anything on the wire."""

    name: str


class Principals:
    """The tokens this deployment has issued, kept beside the rest of the state.

    Read on construction rather than held open, like every store here: the file is small,
    and a copy held between requests is one more thing that can answer with what was true
    before `vizmith user remove` ran. A file that cannot be read raises rather than
    reading as empty, because a principals file that quietly became an empty one would
    answer every token with a refusal and look like an outage rather than damage."""

    def __init__(self, path: Path | None = None):
        self._path = path if path is not None else users_path()
        self._users: dict[str, dict] = stored(self._path, "users")

    def names(self) -> list[str]:
        return sorted(self._users)

    def issue(self, name: str) -> str:
        """Create a principal and answer with the one look anyone gets at its token.

        The token itself never touches disk; the digest is what is stored, which is why
        issuing again replaces rather than restores."""
        _checked(name)
        token = secrets.token_urlsafe(24)
        self._users[name] = {"token": hashlib.sha256(token.encode()).hexdigest()}
        write(self._path, _serialised(self._users))
        return token

    def revoke(self, name: str) -> bool:
        """Forget one principal. Their saved state is left where it is: removing the
        ability to reach work is not the same as destroying it, and the operator who
        wants it gone has a directory to delete and knows it."""
        if name not in self._users:
            return False
        del self._users[name]
        write(self._path, _serialised(self._users))
        return True

    def verify(self, token: str | None) -> Principal | None:
        """The principal a token belongs to, or None where it answers for nobody.

        Compared digest to digest under `compare_digest`, because equality on strings
        returns at the first differing byte and a timing difference is a slower way of
        reading the file. An absent header lands here too, as None."""
        if not token:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        for name, held in self._users.items():
            if isinstance(held, dict) and hmac.compare_digest(held.get("token", ""), digest):
                return Principal(name)
        return None


def scoped(name: str, principal: Principal | None) -> Path:
    """Where one state file lives: beside the rest for the one local user, under the
    principal's own directory for anybody authenticated. The directory is made owner
    only on creation, so one principal's dashboards are not even listable by another
    account on the same machine."""
    base = state_dir()
    if principal is None:
        return base / name
    return hold(base / USER_STATE / principal.name) / name


def _checked(name: str) -> str:
    if not NAME.fullmatch(name):
        raise ValueError(
            f"'{name}' is not a name a principal can hold: letters, digits, dots, "
            "underscores and hyphens, starting with a letter or digit, at most 64 characters."
        )
    return name


def _serialised(users: dict) -> str:
    return json.dumps({"users": users}, indent=2, sort_keys=True)


__all__ = [
    "SHARED_MODE",
    "Principal",
    "Principals",
    "scoped",
    "shared_mode",
    "users_path",
]

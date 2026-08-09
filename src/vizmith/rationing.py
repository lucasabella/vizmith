"""How often the endpoints that spend money may be asked to spend it.

Four endpoints cost something outside this process. `/api/ask` makes up to `ATTEMPTS`
billed model calls per question and then runs the query the answer describes; `/api/critique`
makes a billed model call; `/api/execute` runs a warehouse statement; the two metadata
endpoints run one when the profile cache misses. Nothing bounded how often any of them
could be asked.

The existing caps are all about size, and they are good ones: a statement is waited on for
five minutes and then cancelled, a model call times out at sixty seconds, `limit` is bounded
by the schema, a result past one manifest chunk is refused. None of them is a bound on rate.
So a frontend bug that loops, a dashboard being reopened over and over, or a script pointed
at the server spends real money with nothing in the way, and the person finds out from a
bill rather than from the screen.

Two bounds, because they catch different things.

**A rate.** A token bucket per client per class, refilling steadily. A bucket both caps a
sustained rate and allows a burst up to its size, which matters here because the largest
legitimate burst is a known number: a dashboard is at most `TILE_LIMIT` tiles and opening
one runs every tile at once. A limit that a dashboard trips is a limit somebody turns off,
so the query bucket holds a dashboard and then some.

**A count in flight.** A rate says how many may start per minute and says nothing about how
many may be running at once. Statements are what queue up behind a slow warehouse, and a
hundred waiting statements are a hundred that are each still being paid for, so the number
that may be in flight is capped at the same known burst.

Both refuse rather than queue. Waiting would turn a loop into a slow loop that still spends
everything, and a person watching a screen would see a chart that is taking a long time
rather than a server telling them what is happening. A refusal says which ration ran out
and when to try again.

Nothing here is authentication and it does not pretend to be. `only_this_machine` is what
stands between this API and another tab; this is what stands between it and a mistake,
including one of ours. Keyed per client anyway, because the day there is a second caller is
not the day to discover that one of them can starve the other.

Every ceiling is a number in the environment, and `0` turns that one off — which is what
the eval harness and any batch caller want, since a ration exists to protect a person from
a runaway loop and a harness is a loop somebody meant.
"""

import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from vizmith.dashboards import TILE_LIMIT

# A question is up to three billed calls, so twenty a minute is a person asking as fast as
# they can read the answers, and a loop is not a person.
MODEL_PER_MINUTE = 20

# The largest legitimate burst is a dashboard, and reopening one twice in a minute is a
# thing people do. Five times that, so nothing anybody does by hand comes near it.
QUERY_PER_MINUTE = 5 * TILE_LIMIT

# What may be running at once. A dashboard opens every tile together, so the burst is the
# tile cap; anything past it is a caller that is not waiting for its own answers.
IN_FLIGHT = TILE_LIMIT

MODEL = "model"
QUERY = "query"

# What each class of endpoint is rationed at, and the name that moves it.
CEILINGS = {
    MODEL: ("VIZMITH_MODEL_PER_MINUTE", MODEL_PER_MINUTE),
    QUERY: ("VIZMITH_QUERY_PER_MINUTE", QUERY_PER_MINUTE),
}
FLIGHT = "VIZMITH_IN_FLIGHT"


class Exhausted(Exception):
    """A ration that ran out, with how long until it has not.

    Carries `retry_after` in whole seconds because that is what the header takes, rounded
    up: a header that says zero is one a client reads as "immediately" and comes straight
    back on."""

    def __init__(self, says: str, retry_after: float):
        super().__init__(says)
        self.retry_after = max(1, int(retry_after + 0.999))


def ceiling(name: str, fallback: int) -> int:
    """A ceiling out of the environment. A value that is not a whole number at least zero
    is the fallback rather than a crash: this is a knob on a tool somebody runs on their own
    machine, and a typo in it should not stop the server from starting. Zero is off."""
    written = os.environ.get(name)
    if written is None:
        return fallback
    try:
        asked = int(written)
    except ValueError:
        return fallback
    return asked if asked >= 0 else fallback


@dataclass
class Bucket:
    """Tokens that refill at a steady rate, up to a ceiling.

    `per_minute` is both the sustained rate and the size of the burst, which is one number
    rather than two on purpose: a bucket whose burst is larger than its rate is one that
    lets a loop run at the burst forever by waiting between bursts, and a bucket whose
    burst is smaller than its rate cannot answer a dashboard.
    """

    per_minute: int
    clock: Callable[[], float] = time.monotonic
    tokens: float = 0.0
    filled: float = 0.0

    def __post_init__(self):
        self.tokens = float(self.per_minute)
        self.filled = self.clock()

    def take(self) -> float | None:
        """One token, or how many seconds until there is one."""
        now = self.clock()
        self.tokens = min(float(self.per_minute), self.tokens + (now - self.filled) * self.per_minute / 60)
        self.filled = now
        if self.tokens >= 1:
            self.tokens -= 1
            return None
        return (1 - self.tokens) * 60 / self.per_minute


class Rations:
    """What every client has left, and what is in flight across all of them.

    One lock over both, because the numbers are small and contended for microseconds, and
    because two locks here would be two orders to take them in."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.limits = {name: ceiling(*setting) for name, setting in CEILINGS.items()}
        self.flight = ceiling(FLIGHT, IN_FLIGHT)
        self.buckets: dict[tuple[str, str], Bucket] = {}
        self.running = 0
        self.lock = threading.Lock()

    def spend(self, client: str, what: str) -> None:
        """Take one of this client's tokens for this class, or refuse."""
        limit = self.limits[what]
        if limit == 0:
            return
        with self.lock:
            bucket = self.buckets.setdefault((client, what), Bucket(limit, self.clock))
            wait = bucket.take()
        if wait is None:
            return
        raise Exhausted(
            f"That is more than {limit} {what} requests in a minute, which is more than this "
            f"server will spend. Try again in a moment, or raise "
            f"{CEILINGS[what][0]} if this is deliberate.",
            wait,
        )

    def enter(self) -> None:
        """Claim one of the slots for a request that is about to cost something."""
        if self.flight == 0:
            return
        with self.lock:
            if self.running >= self.flight:
                raise Exhausted(
                    f"{self.running} requests that cost something are already in flight, which "
                    f"is this server's limit. Wait for one to finish, or raise {FLIGHT}.",
                    1,
                )
            self.running += 1

    def leave(self) -> None:
        """Give the slot back. Never below zero, because a counter that can go negative is
        a limiter that quietly stops limiting after one mismatched pair."""
        if self.flight == 0:
            return
        with self.lock:
            self.running = max(0, self.running - 1)

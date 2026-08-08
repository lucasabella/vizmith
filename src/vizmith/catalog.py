"""What a data source says about itself, in words the rest of Vizmith understands.

Nothing above this module knows which source it is talking to. A caller gets qualified
names, a closed set of types and nullability, and rows whose values are the one shape the
result set contract fixes, and nothing else.

The sources themselves are in `sources/`, one module each. What is here is what they have
in common: the closed set of types and the shapes their values arrive in, the records a
description is made of, where a spec may read, and the hold that keeps a burst of requests
from asking a source the same question five times.
"""

import dataclasses
import datetime as dt
import threading
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

STRING = "string"
INTEGER = "integer"
DECLARED = "declared"
SUGGESTED = "suggested"
DECIMAL = "decimal"
BOOLEAN = "boolean"
DATE = "date"
TIMESTAMP = "timestamp"
UNSUPPORTED = "unsupported"

# Deliberately narrower than the source's type system. A type that is not here is
# reported as unsupported, so a caller can see why a column cannot be charted rather
# than being handed a guess.
TYPES = {
    "STRING": STRING,
    "CHAR": STRING,
    "BYTE": INTEGER,
    "SHORT": INTEGER,
    "INT": INTEGER,
    "LONG": INTEGER,
    "DECIMAL": DECIMAL,
    "FLOAT": DECIMAL,
    "DOUBLE": DECIMAL,
    "BOOLEAN": BOOLEAN,
    "DATE": DATE,
    "TIMESTAMP": TIMESTAMP,
    "TIMESTAMP_NTZ": TIMESTAMP,
}

# What a value of each type is once it has left a catalog. The set above says what a
# column is; this says what a row holds, so that a caller reading a result set does not
# have to know which source produced it. A null is None whatever the column's type, and a
# column the set calls unsupported cannot be selected, so neither has an entry here. The
# reasons for these particular shapes, temporal and decimal especially, are in ROADMAP.md.
SHAPES = {
    STRING: str,
    INTEGER: int,
    DECIMAL: float,
    BOOLEAN: bool,
    DATE: dt.date,
    TIMESTAMP: dt.datetime,
}


# How many of a source's metadata reads run at once. Wider than the profiler's pool on
# purpose: what bounds that number is how many concurrent statements a small warehouse will
# run, and a listing or a table description is not a statement. It is a call to the source's
# control plane, which is nearly all waiting and which nothing bills for, so what bounds
# this is the source's own rate limit rather than a cluster's queue.
METADATA_WORKERS = 16


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    nullable: bool


@dataclass(frozen=True, order=True)
class Relationship:
    """One join a source either states or is thought to hold. Declared means the source's
    own foreign key, which nobody has to approve. Suggested is inferred from names and
    types and is not usable until a person confirms it, because a wrong join produces a
    plausible number rather than an error.

    The left side is the table carrying the key and the right side is the one it points
    at. Ordering is by field, so a list of these sorts the same way twice."""

    left_table: str
    left_column: str
    right_table: str
    right_column: str
    kind: str = SUGGESTED

    @property
    def key(self) -> str:
        """What identifies this relationship wherever an answer about it is stored. The
        kind is left out: a suggestion that the source later declares is the same join,
        and a confirmation of it should not be lost to that."""
        return f"{self.left_table}.{self.left_column}>{self.right_table}.{self.right_column}"

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class Table:
    """One table as the source describes it: its columns, and the foreign keys it declares.

    The relationships ride along with the columns because they arrive together. A source
    holds a constraint on the table that declares it, so the response that lists a table's
    columns is the response that carries its keys, and asking for them separately means
    reading every table twice to build one graph. `relationships` is what the source
    states, never what a name suggests: an empty tuple is a table that declares nothing."""

    name: str
    columns: tuple[Column, ...]
    relationships: tuple[Relationship, ...] = ()


@dataclass(frozen=True)
class Scope:
    """Where a spec's table names resolve, and the only place a spec may read.

    Two halves, and they are not the same promise. Filling in is a convenience: a spec may
    name a table with fewer segments than the source uses, and the configured values are
    what the missing ones are. Refusing is a boundary: a spec is hand editable by design,
    so a name spelled out in full used to be taken at its word, which made the settings
    describe where short names resolve rather than where a spec may read. Those are
    different sentences, and the second one is what `api.py` means when it says a client
    cannot name a database. The reach of the credential is not the scope of the tool.

    It lives here, on a record every catalog carries, rather than inside one catalog's
    method, because the rule is not the source's to keep. `query.py` resolves a spec's
    every reference through this before a name reaches the source, so a connector that
    never learned about the rule cannot be pointed outside it by a spec.

    `levels` is what the source calls the segments in front of a table — a catalog and a
    schema here, a project and a dataset on BigQuery, a schema alone on PostgreSQL — and
    it is carried only so that a refusal reads in the source's own words. `values` is what
    is configured, and it is the whole of what a name is checked against."""

    levels: tuple[str, ...]
    values: tuple[str, ...]

    def qualify(self, name: str) -> str:
        """The name as the source spells it, refusing anything outside the configured
        values. Raises `ValueError`, which is the spec's own fault and answers 400."""
        segments = name.split(".")
        spelled = ".".join([*self.levels, "table"])
        if len(segments) > len(self.levels) + 1:
            raise ValueError(f"{name} is not a table name: at most {spelled}")

        qualified = [*self.values[: len(self.levels) + 1 - len(segments)], *segments]
        if tuple(qualified[: len(self.values)]) != self.values:
            raise ValueError(
                f"{name} is outside the configured {self.levels[-1]}. This server reads "
                f"{'.'.join(self.values)}, and where a spec may read is configuration "
                "rather than something a spec chooses."
            )
        return ".".join(qualified)


# How a column is truncated to a unit where a source spells it the way most of them do:
# the unit first and quoted, the column second. It is the default rather than the only
# spelling, which is what `truncate` below is for.
DATE_TRUNC = "date_trunc('{unit}', {column})"


@dataclass(frozen=True)
class Dialect:
    """The parts of a source's SQL that differ between sources, so that everything above
    writes one query and the source names the pieces. Templates rather than function
    names, because the shape differs too: a set of distinct values is `collect_set(c)`
    here and `array_agg(DISTINCT c)` there. `approx_distinct` is None for a source that
    offers no approximate count, and a caller then pays for an exact one. `parameter` is
    how a bound value is referred to in a statement, named rather than positional because
    Unity Catalog's statement execution takes named markers only.

    `truncate` is the newest of them and the one that says the record is the right shape.
    The builder used to write `date_trunc('month', c)` into the statement itself, which is
    two sources' spelling stated as though it were every source's: BigQuery reverses the
    arguments and takes the unit as a bare keyword. A template covers that, and the reason
    a unit is written into the statement rather than bound has not changed — it is one of
    the grammar's own keywords rather than a value, and a source would need it foldable at
    plan time either way."""

    quote: str
    approx_distinct: str | None
    distinct_values: str
    parameter: str
    truncate: str = DATE_TRUNC

    def quoted(self, identifier: str) -> str:
        return f"{self.quote}{identifier.replace(self.quote, self.quote * 2)}{self.quote}"

    def qualified(self, name: str) -> str:
        return ".".join(self.quoted(segment) for segment in name.split("."))


class Catalog(Protocol):
    dialect: Dialect

    # Where a spec's names resolve and the only place it may read. A field rather than a
    # method, because the rule it carries is enforced above this protocol — `query.py`
    # resolves a spec's references through it before a name reaches a source — and a
    # connector that answered the question itself would be a second place for the answer
    # to be wrong. What a connector owes here is the two configured values, not a check.
    scope: Scope

    def tables(self) -> list[str]:
        """Qualified names, one per table."""

    def describe(self, name: str) -> Table:
        """A table by qualified name, with the foreign keys it declares. Fewer segments
        than the source uses are filled in, through `scope`.

        The declared keys belong here rather than only in `relationships` below because a
        caller that has described the schema has already been told them, and asking a
        second time is one more round trip per table for facts in hand."""

    def relationships(self) -> list[Relationship]:
        """The relationships the source declares for itself, and only those. What is
        inferred from names and types is not the source's word and is not reported here.

        The same facts `describe` reports per table, rolled up for the schema and sorted,
        for a caller that has no descriptions to read them off. A caller that does has them
        already and should not ask."""

    def modified(self, name: str) -> str | None:
        """When the source last changed this table, as a token that is only ever compared
        with the last one seen, and None where the source has none to give.

        A token rather than a time, because a source whose honest answer is a commit
        version should not have to dress it up as a clock to be compared with itself. What
        it has to do is move when the table's data moves: something that only tracks the
        definition is not this, and reporting None is the right answer where that is all
        the source has. A caller caching against None must not cache at all, since a
        profile that is never re-read is a wrong answer with no symptom."""

    def run(self, sql: str, parameters: dict | None = None) -> list[tuple]:
        """Rows for a statement built above this layer, with every value bound by name
        rather than written into the statement.

        Every value is in the shape `SHAPES` gives its type, whatever the source's own
        client answers in. That is the half of the result set contract this layer owns: a
        renderer that had to know which source drew a chart is what the interface exists to
        prevent, and a catalog that answered in its client's shapes would put it there.

        Callable from several threads at once, because profiling a schema runs several
        tables in parallel and a warehouse round trip is nearly all waiting. A source whose
        client is not safe to share serialises here rather than making the caller ask."""


# How long the source's freshness answers are held before they are asked for again. A burst
# is a page load and what the person does immediately after it — expand a table, ask a
# question, drag a field into a well — and each of those reads the profiles and pays a
# freshness statement per table to do it. Thirty seconds is about how long that burst is.
#
# What the window covers is the burst rather than the entry, and that is the correction
# measuring it forced. Held per entry, this is a constant governing something proportional
# to the schema: one cold read of 152 tables takes about 25 seconds against a 30 second
# window, so the answers taken at the top of a request were seconds from expiring by the
# time it finished, and the next request re-read the front of the schema while the back of
# it was still warm — 152 billed DESCRIBE DETAIL statements for a schema nobody changed.
# A read taken while a burst is running joins that burst and lives as long as it does, so
# one request cannot expire halfway through the next however large the schema is.
FRESHNESS_HOLD = 30.0

# The most a burst may reach from its first read to its last. A burst runs for as long as
# the gaps between reads stay under the hold, so somebody working steadily would otherwise
# extend one indefinitely and hold an answer for as long as they kept working.
#
# This is where the harm is bounded, and it is the number to argue with. A table rewritten
# under a running server is described as it was for at most this long while the interface is
# in use, and for at most FRESHNESS_HOLD after it goes quiet. Four times the hold, which
# leaves room for a cold read of a schema several times larger than the ones measured and
# still says a stale answer is a matter of minutes rather than of a restart.
FRESHNESS_CEILING = 120.0

# How long a table's shape — its columns and the keys it declares — is held before the
# source is asked to describe it again. Longer than the freshness window above, and for a
# reason rather than for symmetry: what moves a freshness token is a write, and what changes
# a description is somebody altering the table. A schema is written to all day and altered
# on the days a person changes it.
#
# The window is not the only bound. A description is dropped as soon as the source answers
# with a modified token that is not the one it was taken under, and Unity Catalog's token
# moves for a definition change as well as for a write, so every table the profile path
# reads is re-described the moment it changes rather than at the end of five minutes. The
# window is what bounds a table nothing asks the freshness of, which is one nothing profiles.
SHAPE_HOLD = 300.0

# What is recorded against a description taken when no freshness answer was in hand. It
# compares equal to no token, so the first freshness answer that does arrive drops the
# description rather than being read as agreement with it.
_UNKNOWN = object()


class Held:
    """A source whose answers about a table are held for a window: when it last changed,
    and what shape it is.

    **Freshness.** The freshness check is what a warm read costs. A stored profile is served
    only where the source's modified token still matches, so every read of the schema asks
    the source that question once per table, and on Unity Catalog the answer is a `DESCRIBE
    DETAIL` the warehouse runs and bills for rather than a free metastore read. One request
    already asks it once per table; what this removes is the next request asking the same
    thing about the same tables a second later, which is what a person browsing produces.

    The window covers a burst rather than an answer. A burst is a run of reads with no gap
    of `hold` or more between them, and every answer taken in one is held for as long as
    that burst runs — which is what keeps a cold read of a large schema from expiring at the
    front while it is still working on the back. It cannot run longer than `ceiling` from
    its first read to its last, and that is the bound on how stale an answer can be.

    **Shape.** Describing every table is what the relationship graph is made of, and the
    graph is rebuilt from nothing by every join path resolved — which is every drag of a
    column from a table the query does not already read. On a hundred and fifty tables that
    was the whole schema described per gesture, on the one path in the codebase that had no
    cache at all. A description is held per table and the declared relationships with it,
    since a description carries the keys its table declares.

    What it costs is that a table rewritten or altered under a running server is described
    as it was for up to the hold. That is the same trade as caching the profiles at all,
    made smaller: the check still runs, just not several times inside one burst. Holding it
    for the life of the process is what the file cache exists to refuse, and this is not
    that, because the window has a number on it.

    A held description is dropped early where the source says the table moved, which is what
    keeps it from reaching the profile cache. `profile_table` describes the table it
    profiles, so a profile built from a description taken before a column was added would be
    stored under the freshness token taken after it — current, and missing a column, until
    the table changed again. So a modified answer that differs from the one a description
    was taken under drops that description, and a description taken with no token in hand is
    dropped by the first answer that arrives.

    A listing and a statement are passed through untouched. A listing is how a table that
    appeared is noticed, and it is one call for the schema rather than one per table; a
    statement has a different answer every time by construction.

    `None` is held like any other answer. A source object that has no modified time to
    give — a view, which `DESCRIBE DETAIL` will not describe — costs a failed statement to
    find that out, and finding it out repeatedly inside one burst is the same waste.
    """

    def __init__(
        self,
        catalog: "Catalog",
        hold: float = FRESHNESS_HOLD,
        shape: float = SHAPE_HOLD,
        ceiling: float = FRESHNESS_CEILING,
        clock=time.monotonic,
    ):
        self.dialect = catalog.dialect
        self.scope = catalog.scope
        self._catalog = catalog
        self._hold = hold
        self._shape = shape
        self._ceiling = ceiling
        self._clock = clock
        self._lock = threading.Lock()
        # Which burst is running, when it began and when it last read. A freshness answer
        # is stored against a burst rather than against the moment it was taken, so the
        # whole of a burst expires together and none of it expires under the request that
        # is still taking it.
        self._burst = 0
        self._began = 0.0
        self._last = None
        self._held: dict[str, tuple[int, str | None]] = {}
        self._described: dict[str, tuple[float, object, Table]] = {}
        self._relationships: tuple[float, list[Relationship]] | None = None

    def tables(self) -> list[str]:
        return self._catalog.tables()

    def describe(self, name: str) -> "Table":
        """The held description where it is still inside the window and still taken under
        the token the source last gave, and the source's otherwise.

        The token is read before the source is asked and checked again before the answer is
        stored, so a description that crossed a write is not stored as though it preceded
        it. What it costs to lose that race is one more description on the next call."""
        asked = self._clock()
        with self._lock:
            held = self._described.get(name)
            if held is not None and asked - held[0] < self._shape:
                return held[2]
            token = self._token(name, asked)

        described = self._catalog.describe(name)
        with self._lock:
            if self._token(name, self._clock()) == token:
                self._described[name] = (asked, token, described)
        return described

    def relationships(self) -> list["Relationship"]:
        """Every key the schema declares, held whole rather than per table, because that is
        the shape the question is asked in: nothing asks for one table's keys on their own.

        A caller that describes the schema anyway reads the keys off the descriptions
        instead and never arrives here — `relationship_graph` in `api.py` — so what this
        serves is the path that has no descriptions in hand."""
        asked = self._clock()
        with self._lock:
            held = self._relationships
            if held is not None and asked - held[0] < self._shape:
                return list(held[1])

        found = self._catalog.relationships()
        with self._lock:
            self._relationships = (asked, list(found))
        return found

    def run(self, sql: str, parameters: dict | None = None) -> list[tuple]:
        return self._catalog.run(sql, parameters)

    def modified(self, name: str) -> str | None:
        """The held answer where the burst it was taken in is still running, and the
        source's otherwise.

        Two threads asking about the same table at the same moment both ask the source,
        which is what they did before this existed. Collapsing them would mean holding a
        lock across a warehouse round trip, and the profiling that produces those threads
        asks each table once anyway: what arrives at the same moment is several tables,
        not several copies of one.

        The burst is joined when the answer is stored rather than when it was asked for,
        because reading the source is what a burst is made of: a request whose reads run
        back to back keeps its own burst running for as long as it is reading."""
        asked = self._clock()
        with self._lock:
            held = self._held.get(name)
            if held is not None and held[0] == self._burst and self._running(asked):
                return held[1]

        answer = self._catalog.modified(name)
        with self._lock:
            self._held[name] = (self._joined(self._clock()), answer)
            described = self._described.get(name)
            if described is not None and described[1] != answer:
                del self._described[name]
        return answer

    def _running(self, now: float) -> bool:
        """Whether the burst is still running at `now`, which is what says an answer taken
        in it may still be served. Called under the lock."""
        return (
            self._last is not None
            and now - self._last < self._hold
            and now - self._began < self._ceiling
        )

    def _joined(self, now: float) -> int:
        """The burst a read taken at `now` belongs to, beginning one where the last has
        stopped running, and holding the running one open where it has not. Called under
        the lock."""
        if not self._running(now):
            self._burst += 1
            self._began = now
        self._last = now
        return self._burst

    def _token(self, name: str, asked: float):
        """The freshness answer in hand for this table, or `_UNKNOWN` where there is none
        from the burst that is running. Called under the lock."""
        held = self._held.get(name)
        if held is None or held[0] != self._burst or not self._running(asked):
            return _UNKNOWN
        return held[1]


def conform(value):
    """One value in the shape the contract fixes, for a source whose client answers in
    Python objects rather than in text.

    Three things move. A decimal becomes a float, because the closed set folds FLOAT and
    DOUBLE in with DECIMAL and no shape can be exact for all three. A timestamp that
    carries a zone becomes the same instant in UTC without one, because a result set holds
    one shape and a zone is not part of it. And an array's values are conformed too, since
    the profiler's samples arrive inside one.

    Everything else is returned untouched. A value this does not recognise is not
    converted, because a shape nobody planned for is a source's bug, and guessing here
    would hide it rather than report it."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dt.datetime) and value.tzinfo is not None:
        return value.astimezone(dt.UTC).replace(tzinfo=None)
    if isinstance(value, (list, tuple)):
        return [conform(item) for item in value]
    return value

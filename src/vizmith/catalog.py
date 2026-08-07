"""What a data source says about itself, in words the rest of Vizmith understands.

Nothing above this module knows that the source is Databricks. A caller gets
qualified names, a closed set of types and nullability, and rows whose values are the
one shape the result set contract fixes, and nothing else.
"""

import dataclasses
import datetime as dt
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
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


# How long a statement is waited on before it is given up on and cancelled, in seconds. A
# warehouse that has to start answers pending for minutes, so this is generous rather than
# protective; what it rules out is a wait with no end. See ROADMAP.md for the trade.
WAIT_LIMIT = 300

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


@dataclass(frozen=True)
class Dialect:
    """The parts of a source's SQL that differ between sources, so that everything above
    writes one query and the source names the pieces. Templates rather than function
    names, because the shape differs too: a set of distinct values is `collect_set(c)`
    here and `array_agg(DISTINCT c)` there. `approx_distinct` is None for a source that
    offers no approximate count, and a caller then pays for an exact one. `parameter` is
    how a bound value is referred to in a statement, named rather than positional because
    Unity Catalog's statement execution takes named markers only."""

    quote: str
    approx_distinct: str | None
    distinct_values: str
    parameter: str

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


# How long one of the source's freshness answers is held before it is asked for again. A
# burst is a page load and what the person does immediately after it — expand a table, ask
# a question, drag a field into a well — and each of those reads the profiles and pays a
# freshness statement per table to do it. Thirty seconds is about how long that burst is,
# and it is a guess bounded by the harm it can do: a table rewritten while somebody has the
# interface open is described as it was for at most this long, where holding the answer for
# the life of the process would describe it that way until a restart. What would tune this
# rather than reason about it is the measurement in tests/test_profiling_cost.py, which
# needs a warehouse.
FRESHNESS_HOLD = 30.0

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
        clock=time.monotonic,
    ):
        self.dialect = catalog.dialect
        self.scope = catalog.scope
        self._catalog = catalog
        self._hold = hold
        self._shape = shape
        self._clock = clock
        self._lock = threading.Lock()
        self._held: dict[str, tuple[float, str | None]] = {}
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
        """The held answer where it is still inside the window, and the source's otherwise.

        Two threads asking about the same table at the same moment both ask the source,
        which is what they did before this existed. Collapsing them would mean holding a
        lock across a warehouse round trip, and the profiling that produces those threads
        asks each table once anyway: what arrives at the same moment is several tables,
        not several copies of one."""
        asked = self._clock()
        with self._lock:
            held = self._held.get(name)
            if held is not None and asked - held[0] < self._hold:
                return held[1]

        answer = self._catalog.modified(name)
        with self._lock:
            self._held[name] = (asked, answer)
            described = self._described.get(name)
            if described is not None and described[1] != answer:
                del self._described[name]
        return answer

    def _token(self, name: str, asked: float):
        """The freshness answer in hand for this table, or `_UNKNOWN` where there is none
        inside the window. Called under the lock."""
        held = self._held.get(name)
        if held is None or asked - held[0] >= self._hold:
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


class DatabricksCatalog:
    dialect = Dialect(
        quote="`",
        approx_distinct="approx_count_distinct({column})",
        distinct_values="collect_set({column})",
        parameter=":{name}",
    )

    def __init__(self, profile: str, catalog: str, schema: str, warehouse: str):
        self._profile = profile
        self._catalog = catalog
        self._schema = schema
        self._warehouse = warehouse
        self._client = None
        self.scope = Scope(levels=("catalog", "schema"), values=(catalog, schema))

    def tables(self) -> list[str]:
        listed = self._workspace().tables.list(catalog_name=self._catalog, schema_name=self._schema)
        return sorted(table.full_name for table in listed)

    def describe(self, name: str) -> Table:
        return _table(self._workspace().tables.get(self.qualify(name)))

    def relationships(self) -> list[Relationship]:
        """The foreign keys Unity Catalog holds for the configured schema. A lakehouse
        table carries one only where somebody declared it by hand, so this is often
        empty, and what fills the gap is a suggestion a person confirms.

        Every table is read, because a constraint is held on the table that declares it and
        there is nothing to ask for the set of them. That was a loop, so a schema's worth of
        round trips happened one after another for an answer that is nearly all waiting: on
        a hundred and fifty tables it was the larger half of every join path resolved and
        every question asked. They overlap now.

        It is the same read `describe` makes, rather than a second one beside it: a
        description already carries the constraints the response held, so this is those
        descriptions rolled up. That is what makes the read once rather than twice for
        anything holding descriptions in front of this — `Held` above — and it is why a
        graph costs one round trip per table rather than two.

        `tables` runs first and on this thread, which is what builds the client before the
        pool shares it."""
        names = self.tables()
        with ThreadPoolExecutor(max_workers=METADATA_WORKERS) as pool:
            described = pool.map(self.describe, names)
            return sorted(relationship for table in described for relationship in table.relationships)

    def modified(self, name: str) -> str | None:
        """The last time this table's data or definition changed, from `DESCRIBE DETAIL`.

        Unity Catalog's own `updated_at` on a table, which is the same field the
        information schema calls `last_altered`, is not this: Databricks documents that it
        tracks the table's structure and does not move for an insert, an update or a
        delete. It is free to read where this costs a statement, and a cache keyed on it
        would never notice a write, which is the failure this whole key exists to prevent.
        So the more expensive answer is the only correct one.

        A source object `DESCRIBE DETAIL` does not answer for, a view among them, has no
        modified time here rather than a failure: what it means is that the table cannot be
        cached, and a caller is told that by None."""
        detail = f"DESCRIBE DETAIL {self.dialect.qualified(self.qualify(name))}"
        try:
            columns, rows = self._statement(detail)
        except RuntimeError:
            return None
        found = dict(zip(columns, rows[0])) if rows else {}
        modified = found.get("lastModified")
        return None if modified is None else str(modified)

    def run(self, sql: str, parameters: dict | None = None) -> list[tuple]:
        return self._statement(sql, parameters)[1]

    def _statement(self, sql: str, parameters: dict | None = None) -> tuple[list[str], list[tuple]]:
        """`run` plus the names the manifest gave the columns, which `run` drops because
        the result set contract is positional. Only this class reads the names, and only to
        find one field of a `DESCRIBE DETAIL` whose column order has changed between
        runtime versions."""
        from databricks.sdk.service.sql import StatementParameterListItem, StatementState

        execution = self._workspace().statement_execution
        started = time.monotonic()
        response = execution.execute_statement(
            statement=sql,
            warehouse_id=self._warehouse,
            wait_timeout="50s",
            parameters=[
                StatementParameterListItem(name=name, value=_parameter(value), type=_parameter_type(value))
                for name, value in (parameters or {}).items()
            ],
        )
        # A statement that outlives the wait comes back pending rather than finished, and a
        # warehouse that has to start does that every time, so the cap is minutes rather
        # than seconds. Past it the statement is cancelled and this gives up, because a
        # query whose caller has gone is billed for as long as it keeps running. The clock
        # starts before the call rather than at the loop: the call blocks for wait_timeout
        # first, and that is part of what the person waited.
        while response.status.state in (StatementState.PENDING, StatementState.RUNNING):
            waited = time.monotonic() - started
            if waited >= WAIT_LIMIT:
                _cancel(execution, response.statement_id)
                raise RuntimeError(f"statement not finished after {waited:.0f} seconds, cancelled")
            time.sleep(1)
            response = execution.get_statement(response.statement_id)

        # An unsuccessful statement still carries a result of None, and returning no rows
        # for it would read as an empty answer rather than as a failure.
        if response.status.state != StatementState.SUCCEEDED:
            raise RuntimeError(f"statement {response.status.state}: {response.status.error}")
        # Rows past the first chunk are fetched separately, so returning that chunk would
        # answer with a page and call it the result. A spec's row cap keeps a chart query
        # well inside one chunk, and a profile is a single row.
        if response.manifest.truncated or response.manifest.total_chunk_count > 1:
            raise RuntimeError("statement returned more rows than one chunk holds")

        described = response.manifest.schema.columns
        types = [_type(column) for column in described]
        return [column.name for column in described], [
            tuple(_value(v, t) for v, t in zip(row, types)) for row in response.result.data_array or []
        ]

    def qualify(self, name: str) -> str:
        """A name as this source spells it, through the scope every catalog carries.

        The rule used to live here, which made it this connector's to keep and the next
        one's to forget. It is `Scope` now, and this is where the workspace's own calls
        reach it: a `DESCRIBE DETAIL` needs the full name whether the caller wrote one or
        not."""
        return self.scope.qualify(name)

    def _workspace(self):
        if self._client is None:
            from databricks.sdk import WorkspaceClient

            self._client = WorkspaceClient(profile=self._profile)
        return self._client


def _cancel(execution, statement_id: str) -> None:
    """Stop a statement nobody is left to read the answer of.

    Best effort, because the caller is already being told that the statement did not
    finish and a source that will not take the cancellation cannot change that. Raising
    here would replace a message about waiting with one about cancelling, which sends the
    person to the wrong thing."""
    with suppress(Exception):
        execution.cancel_execution(statement_id)


def _parameter(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _parameter_type(value) -> str:
    """A statement takes its values as text plus a declared type, so a number compared
    against a numeric column has to say so or it arrives as a string. A whole number is
    declared as small as it fits, because a row limit is one of these and LIMIT rejects
    anything wider than an INT. A comparison widens the other way by itself."""
    if isinstance(value, bool):
        return "BOOLEAN"
    if isinstance(value, int):
        return "INT" if -(2**31) <= value < 2**31 else "BIGINT"
    if isinstance(value, float):
        return "DOUBLE"
    return "STRING"


def _type(column) -> str:
    """The manifest names a column's type twice and not always in both places: for a
    TIMESTAMP_NTZ the SDK's enum is absent while the text says what it is. The text is
    the fallback rather than the primary, because it carries a decimal's precision and
    scale and the enum does not."""
    return column.type_name.value if column.type_name else column.type_text


def _value(text: str | None, type_name: str):
    """Rows come back as text with the types in the manifest, so the source's own type is
    what turns a total back into a number and a month back into a date before either
    reaches a chart. What it turns them into is `SHAPES`, which is the same answer the
    harness gives for the same column.

    An array arrives as the JSON text of one. It is handled here rather than in `TYPES`
    because that set says which column types can be charted, and an array still cannot be.
    Only the profiler's sample query returns one, and it needs the values rather than a
    string that happens to contain them: counting the characters of that string is how a
    four value column ends up looking like a thirty character one."""
    if text is None:
        return None
    if type_name == "ARRAY":
        return json.loads(text)
    kind = TYPES.get(type_name, UNSUPPORTED)
    if kind == INTEGER:
        return int(text)
    if kind == DECIMAL:
        return float(text)
    if kind == BOOLEAN:
        return text == "true"
    if kind in (DATE, TIMESTAMP):
        return _temporal(text, kind, type_name)
    return text


def _temporal(text: str, kind: str, type_name: str):
    """A date or a timestamp as an object rather than as the text the API sent.

    A TIMESTAMP arrives with a `Z` on the end where a TIMESTAMP_NTZ arrives without one,
    so this is also where the zone goes: the instant is carried to UTC and stripped of it,
    because the contract has one shape for a timestamp and a zone is not part of it.

    Text the manifest called temporal and Python cannot read is the source failing rather
    than the spec, and it raises the error the API attributes to a source. Returning the
    text would be worse than raising: it puts the shape this exists to remove into a
    result set, and only whatever reads the value much later would notice."""
    try:
        moment = dt.date.fromisoformat(text) if kind == DATE else dt.datetime.fromisoformat(text)
    except ValueError as failure:
        raise RuntimeError(f"the source answered {text!r} for a {type_name} column") from failure
    return conform(moment)


def _foreign_key(table: str, constraint) -> list[Relationship]:
    """One constraint as one relationship per column pair. A composite key joins on
    several columns at once, and the pairs are read positionally because that is the
    order the constraint declares them in."""
    foreign_key = getattr(constraint, "foreign_key_constraint", None)
    if foreign_key is None:
        return []
    return [
        Relationship(table, child, foreign_key.parent_table, parent, kind=DECLARED)
        for child, parent in zip(foreign_key.child_columns, foreign_key.parent_columns)
    ]


def _table(info) -> Table:
    """One table's response as a `Table`, columns and declared keys from the same object.

    A table with no constraints answers with the field absent rather than empty, and a
    workspace that has never had one declared on anything answers that way for every
    table, so the field is read defensively."""
    return Table(
        name=info.full_name,
        columns=tuple(
            Column(
                name=column.name,
                type=TYPES.get(column.type_name.value, UNSUPPORTED),
                nullable=bool(column.nullable),
            )
            for column in info.columns or []
        ),
        relationships=tuple(
            relationship
            for constraint in getattr(info, "table_constraints", None) or []
            for relationship in _foreign_key(info.full_name, constraint)
        ),
    )

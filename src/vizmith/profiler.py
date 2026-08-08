"""Column profiles: what a model gets to see of the data, and all it ever gets to see.

Two statements per table and no more. The first computes every column's statistics in
one pass over the table. The second collects distinct values, and only for the columns
the first one showed to be below the sample threshold, because collecting the distinct
values of a free text column means collecting the whole table.

The threshold is the security boundary. A column above it contributes no sample values
at all, since sample values from a high cardinality column are raw data and raw data
never reaches a prompt.

A sample list is exact while the distinct count next to it usually is not, so the two
can disagree by a value or two on the same column. Whatever writes a profile into a
prompt has to say which of the two it is quoting.

`Profiles` is the cache in front of all of that, keyed on the source's own modified time
for the table plus the threshold the profile was built with, because lowering the
threshold has to silence samples that a stored profile still holds.
"""

import dataclasses
import json
import os
import tempfile
import threading
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from vizmith.catalog import DATE, DECIMAL, INTEGER, TIMESTAMP, UNSUPPORTED, Catalog, Column
from vizmith.state import hold

# A column with more distinct values than this gets no samples. Low enough that a list
# of values still reads as a vocabulary rather than as data.
SAMPLE_THRESHOLD = 25

# Only these carry a meaningful minimum and maximum. Free text has one and it says
# nothing, booleans have one and it is already in the type.
ORDERED = {INTEGER, DECIMAL, DATE, TIMESTAMP}

# The shape of the file `Profiles` writes. A stored profile written under a different one
# is dropped rather than read or migrated: this is a cache, and the cheapest way to change
# what it holds is to pay for one more profile.
CACHE_VERSION = 1


@dataclass(frozen=True)
class ColumnProfile:
    name: str
    type: str
    null_rate: float
    distinct_count: int
    distinct_count_exact: bool
    minimum: str | None
    maximum: str | None
    samples: tuple[str, ...]


@dataclass(frozen=True)
class TableProfile:
    table: str
    row_count: int
    columns: tuple[ColumnProfile, ...]

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "TableProfile":
        return TableProfile(
            table=data["table"],
            row_count=data["row_count"],
            columns=tuple(
                ColumnProfile(**{**column, "samples": tuple(column["samples"])}) for column in data["columns"]
            ),
        )


def profile_table(catalog: Catalog, name: str, threshold: int = SAMPLE_THRESHOLD) -> TableProfile:
    """A profile of one table. Columns whose type the catalog reports as unsupported are
    left out: a profile describes what can be charted, and `describe` is where the full
    column list lives."""
    table = catalog.describe(name)
    columns = [column for column in table.columns if column.type != UNSUPPORTED]

    row_count, statistics = _statistics(catalog, table.name, columns)
    low_cardinality = [column for column in columns if statistics[column.name].distinct_count <= threshold]
    samples = _samples(catalog, table.name, low_cardinality, threshold)

    profiles = []
    for column in columns:
        figures = statistics[column.name]
        profiles.append(
            ColumnProfile(
                name=column.name,
                type=column.type,
                null_rate=_null_rate(row_count, figures.non_null),
                distinct_count=figures.distinct_count,
                distinct_count_exact=catalog.dialect.approx_distinct is None,
                minimum=figures.minimum,
                maximum=figures.maximum,
                samples=samples.get(column.name, ()),
            )
        )
    return TableProfile(table=table.name, row_count=row_count, columns=tuple(profiles))


class _File:
    """One cache file, parsed once and held under the stamp of the file it was parsed from.

    The stamp is the modified time and the size, which is what says the file on disk is
    still the file that was read: another process replacing it moves both, and this picks
    the new one up on the next construction rather than at a restart."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.profiles: dict[str, dict] = {}
        self.stamp: tuple[int, int] | None = None

    def refresh(self) -> None:
        """Parse the file where it is not the one already parsed, and nothing otherwise."""
        with self.lock:
            stamp = _stamp(self.path)
            if stamp == self.stamp:
                return
            self.stamp = stamp
            self.profiles = {} if stamp is None else _parse(self.path)

    def wrote(self) -> None:
        """Called under the lock after this process wrote the file, so the write it just
        made is not read back as somebody else's."""
        self.stamp = _stamp(self.path)


_FILES: dict[Path, _File] = {}
_FILES_LOCK = threading.Lock()


def _file(path: Path) -> _File:
    """The held copy of one cache file. Keyed by the absolute path, because two callers
    naming the same file by different paths are talking about one file."""
    resolved = path.absolute()
    with _FILES_LOCK:
        held = _FILES.get(resolved)
        if held is None:
            held = _File(resolved)
            _FILES[resolved] = held
        return held


def _stamp(path: Path) -> tuple[int, int] | None:
    try:
        status = path.stat()
    except OSError:
        return None
    return (status.st_mtime_ns, status.st_size)


def _parse(path: Path) -> dict[str, dict]:
    """What the file holds, or nothing where it holds something this cannot read.

    A file that cannot be read is dropped exactly like one written under another version.
    Refusing to serve because a cache is unreadable would turn the cheapest possible
    failure, one more profile, into a server that answers nothing."""
    with suppress(OSError, ValueError):
        written = json.loads(path.read_text())
        if written.get("version") == CACHE_VERSION:
            return written.get("profiles", {})
    return {}


class Profiles:
    """Profiles kept in a file the server owns, under the source's own modified time.

    A stored profile is read back only where that time and the threshold both still match,
    so a table that was written to is profiled again and a threshold that was lowered
    cannot be answered with samples collected under a higher one.

    A table the source reports no modified time for is profiled every time and never
    stored. Keeping one forever to save two statements trades a bill for a stale profile,
    and a stale profile is the worse of the two: the figures still look like figures and
    the model reads them as current.

    Several tables are profiled at once, so every method that touches the file holds the
    lock, and the file is written whole and moved into place rather than edited. Two of
    these storing at the same moment can still lose one of the two entries, since each
    read the file when it was built. That costs a profile rather than producing a wrong
    one, which is the trade a cache is allowed to make.

    The parsed file is held per path for the life of the process rather than parsed again
    per instance. The API builds one of these per request, and the file holds every table's
    profile including its sample values, so a panel load of N single-table requests parsed
    an N-table file N times: on a 150 table schema that was seconds of server CPU for
    nothing. What makes holding it safe is the key that was already there — a stored profile
    is served only where the source's modified token still matches — so a held copy cannot
    answer with a profile a fresh read would have refused. A file replaced by another
    process is still picked up, because its stamp is checked on every construction."""

    def __init__(self, path: Path):
        self._path = path
        self._file = _file(path)
        self._file.refresh()

    @property
    def _lock(self) -> threading.Lock:
        return self._file.lock

    @property
    def _stored(self) -> dict[str, dict]:
        return self._file.profiles

    def read(self, catalog: Catalog, name: str, threshold: int = SAMPLE_THRESHOLD) -> TableProfile:
        """The table's profile, from the file where it is still current and from the
        source where it is not. Asking the source when the table last changed is the price
        of the answer, and it is a metadata read rather than a pass over the table.

        Where nothing is stored the two happen at once. A table this file has never seen is
        going to be profiled whatever the source says about its freshness — there is nothing
        for the answer to serve — so waiting for that answer before starting the scan put a
        round trip per table in front of a cold read for no decision. Modelled at 152 tables
        that was about a third of the wall clock.

        It is a concurrent start and not a reordering, and the difference is the whole
        correctness of the cache. The token has to be *taken* no later than the scan begins:
        a write landing between the two then leaves a profile that is newer than the token it
        is stored under, so the next freshness answer differs and the table is profiled
        again — late by one read. Taken after the scan instead, that same write would be
        stored as current and missing, and nothing would notice until the table changed a
        second time."""
        with self._lock:
            stored = self._stored.get(name)

        if stored is None:
            asking = _Asking(catalog, name)
            profile = profile_table(catalog, name, threshold)
            modified = asking.answer()
            if modified is not None:
                self._store(name, modified, threshold, profile)
            return profile

        modified = catalog.modified(name)
        if modified is not None and (stored["modified"], stored["threshold"]) == (modified, threshold):
            return TableProfile.from_dict(stored["profile"])

        profile = profile_table(catalog, name, threshold)
        if modified is not None:
            self._store(name, modified, threshold, profile)
        return profile

    def _store(self, name: str, modified: str, threshold: int, profile: TableProfile) -> None:
        with self._lock:
            self._stored[name] = {
                "modified": modified,
                "threshold": threshold,
                "profile": profile.as_dict(),
            }
            written = json.dumps(
                {"version": CACHE_VERSION, "profiles": self._stored}, indent=2, sort_keys=True
            )
            hold(self._path.parent)
            # Written beside the file and moved onto it, because a reader arriving while a
            # table is being stored should find the last whole file rather than the first
            # half of the next one. The name beside it is unique per write, since a second
            # request can be storing at the same moment and must not move the first one's
            # file out from under it.
            handle, beside = tempfile.mkstemp(dir=self._path.parent, prefix=self._path.name + ".")
            with os.fdopen(handle, "w") as writing:
                writing.write(written)
            os.replace(beside, self._path)
            self._file.wrote()


class _Asking:
    """When the source last changed a table, asked on a thread of its own so that the scan
    beside it does not wait for the answer.

    A thread rather than a pool because there is one call and the caller is already inside
    the profiler's pool: a pool per table would be a pool per worker. What it costs is a
    thread that lives for one metadata read.

    Whatever the source raised is raised here instead, on `answer`. Swallowing it would
    quietly turn every table into one that cannot be cached — a profile is never stored
    against no token — and the symptom of that is a warehouse bill rather than an error."""

    def __init__(self, catalog: Catalog, name: str):
        self._answer: str | None = None
        self._failure: BaseException | None = None
        self._thread = threading.Thread(target=self._ask, args=(catalog, name), daemon=True)
        self._thread.start()

    def _ask(self, catalog: Catalog, name: str) -> None:
        try:
            self._answer = catalog.modified(name)
        except BaseException as failure:  # noqa: BLE001 - re-raised on the calling thread
            self._failure = failure

    def answer(self) -> str | None:
        self._thread.join()
        if self._failure is not None:
            raise self._failure
        return self._answer


class _Statistics(NamedTuple):
    non_null: int
    distinct_count: int
    minimum: str | None
    maximum: str | None


def _statistics(catalog: Catalog, table: str, columns: list[Column]):
    """One query over the table: non null count, distinct count and range per column."""
    dialect = catalog.dialect
    distinct = dialect.approx_distinct or "count(DISTINCT {column})"

    select = ["count(*)"]
    for column in columns:
        quoted = dialect.quoted(column.name)
        select.append(f"count({quoted})")
        select.append(distinct.format(column=quoted))
        if column.type in ORDERED:
            select.append(f"min({quoted})")
            select.append(f"max({quoted})")

    values = iter(catalog.run(f"SELECT {', '.join(select)} FROM {dialect.qualified(table)}")[0])
    row_count = int(next(values))
    statistics = {}
    for column in columns:
        non_null = int(next(values))
        distinct_count = int(next(values))
        minimum, maximum = None, None
        if column.type in ORDERED:
            minimum, maximum = _text(next(values)), _text(next(values))
        statistics[column.name] = _Statistics(non_null, distinct_count, minimum, maximum)
    return row_count, statistics


def _samples(catalog: Catalog, table: str, columns: list[Column], threshold: int):
    """One query for every low cardinality column together, or none if there are none."""
    if not columns:
        return {}

    dialect = catalog.dialect
    select = [dialect.distinct_values.format(column=dialect.quoted(column.name)) for column in columns]
    collected = catalog.run(f"SELECT {', '.join(select)} FROM {dialect.qualified(table)}")[0]

    samples = {}
    for column, values in zip(columns, collected):
        # A null is not one of a column's values, and whether the source's own aggregate
        # thinks so differs: Spark's collect_set drops nulls, DuckDB's array_agg keeps one,
        # and BigQuery's raises unless it is told to ignore them. Dropping it here is what
        # makes those three answer the same thing, and it is also what stops a list of
        # values that holds a None from being sorted against its strings, which is a crash
        # rather than a wrong figure. What a column's nulls are is its null rate, which is
        # collected separately and printed beside the values in the prompt.
        held = [value for value in values or [] if value is not None]
        # A distinct count from an approximate function can sit below the threshold while
        # the column sits above it. What came back is then raw data, and it gets dropped
        # rather than trimmed to size, because a trimmed list still leaks values. Counted
        # after the null comes out, so a column of exactly the threshold's worth of values
        # plus a null is not refused for the one that was never a value.
        if len(held) <= threshold:
            samples[column.name] = tuple(sorted(_text(value) for value in held))
    return samples


def _null_rate(row_count: int, non_null: int) -> float:
    """Unrounded, because rounding turns one null in a million rows into a column that
    reports no nulls, and a model reads that rate to decide whether to filter. Rounding
    for display belongs where a profile is written into a prompt."""
    if not row_count:
        return 0.0
    return (row_count - non_null) / row_count


def _text(value) -> str | None:
    """Every figure in a profile is text, because a date, a decimal and a string have to
    survive the same JSON round trip and end up in the same prompt."""
    return None if value is None else str(value)

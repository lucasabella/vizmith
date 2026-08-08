"""The query IR compiled to SQL, and the SQL run against a source.

Every value in a spec is bound as a parameter, including values the validator has already
seen and including the row limits. Validation narrows what a query can ask for; it is not
what keeps a value out of the statement text. Only three things are ever written into the
SQL itself, and none of them comes from a value: identifiers the catalog reported, and the
truncation units and function names of the grammar's own closed enums.

The order of the SELECT is the result set contract. It comes from `output_columns`, the
same list the validator checks references against, so the builder cannot drift from what a
spec was validated as producing.
"""

import datetime as dt

from vizmith.catalog import Catalog
from vizmith.spec import names_table, output_columns, validate_spec

# count is the only one the schema lets through without a column, and it becomes count(*).
AGGREGATES = {
    "sum": "sum({column})",
    "avg": "avg({column})",
    "min": "min({column})",
    "max": "max({column})",
    "count": "count({column})",
    "count_distinct": "count(DISTINCT {column})",
}

# How a measure is re-aggregated when limit_by ranks the outer dimension. The rows being
# ranked are already grouped, so ranking a country by its revenue means combining the
# revenue of its rows, and the only honest way to combine them is the function that
# produced them. An average of averages is not an average, so avg has no entry and asking
# for it raises rather than returning a plausible order that is wrong.
RANKING = {"sum": "sum", "count": "sum", "count_distinct": "sum", "min": "min", "max": "max"}

COMPARISONS = {"=", "!=", "<", "<=", ">", ">="}

# How many months each unit is worth, for the units that are a whole number of them. The
# rest are counted in days or hours, because a week is seven days everywhere and a month is
# not thirty of anything.
MONTHS = {"year": 12, "quarter": 3, "month": 1}


def build(spec: dict, catalog: Catalog, now: dt.datetime | None = None) -> tuple[str, dict]:
    """SQL plus the values to bind to it. Needs the catalog for names only: a spec may
    name a table with fewer segments than the source uses, and the source is the only
    thing that can fill the rest in.

    `now` is what a relative filter value resolves against, and it is the server's clock.
    That is the decision this feature had to make, because a warehouse's `current_date` and
    the server's differ by zone and a spec that means something different depending on where
    it was compiled is not the reproducible artefact this design promises. Resolving here
    means the spec on disk stays relative while the statement is absolute, and the local
    civil day is what "today" means to the person asking — this runs on their machine.
    A caller passes one to make an answer repeatable; nothing in the application does."""
    errors = validate_spec(spec)
    if errors:
        raise ValueError("spec is not valid: " + "; ".join(errors))
    # Naive local time, deliberately. The zone is the machine's and "today" is the civil day
    # of the person asking; an aware UTC clock would answer a different question for most of
    # the world for part of every day.
    return _Builder(spec["query"], catalog, now or dt.datetime.now()).build()  # noqa: DTZ005


def execute(spec: dict, catalog: Catalog, now: dt.datetime | None = None) -> list[dict]:
    """Rows as plain objects keyed by the query's output columns."""
    sql, parameters = build(spec, catalog, now)
    names = output_columns(spec["query"])
    return [dict(zip(names, row)) for row in catalog.run(sql, parameters)]


def resolve(value: dict, now: dt.datetime) -> str:
    """One relative value, as the text a literal date would have arrived as.

    Text rather than a date object on purpose. A date written into a spec by hand is already
    a string — `"2025-01-01"` in the fixtures — and every connector binds a value as text
    with a declared type, so resolving to the same shape means a relative filter travels the
    path a literal one has always travelled and no source needs to learn anything.

    Date grained where the unit is, timestamp grained where it is not. Comparing a DATE
    column against midnight and a TIMESTAMP column against a date both work in every dialect
    here; what would not is inventing a precision the question did not have.
    """
    token = value["relative"]
    if token == "now":
        return now.replace(microsecond=0).isoformat(sep=" ")
    if token == "today":
        return now.date().isoformat()

    unit = value["unit"]
    if token == "start_of":
        return _started(now, unit)
    return _before(now, unit, value["count"])


def _started(now: dt.datetime, unit: str) -> str:
    if unit == "hour":
        return now.replace(minute=0, second=0, microsecond=0).isoformat(sep=" ")
    day = now.date()
    if unit == "day":
        return day.isoformat()
    if unit == "week":
        # Monday, which is what a week starts on everywhere this is likely to be read.
        return (day - dt.timedelta(days=day.weekday())).isoformat()
    if unit == "month":
        return day.replace(day=1).isoformat()
    if unit == "quarter":
        return day.replace(month=3 * ((day.month - 1) // 3) + 1, day=1).isoformat()
    return day.replace(month=1, day=1).isoformat()


def _before(now: dt.datetime, unit: str, count: int) -> str:
    if unit == "hour":
        return (now.replace(microsecond=0) - dt.timedelta(hours=count)).isoformat(sep=" ")
    day = now.date()
    if unit in ("day", "week"):
        return (day - dt.timedelta(days=count * (7 if unit == "week" else 1))).isoformat()

    # Calendar months, counted rather than approximated: thirty days before the 31st of
    # March is not "a month ago" to anybody. The day is clamped where the target month is
    # shorter, which is the only answer that stays inside the month it names.
    months = day.month - 1 - count * MONTHS[unit]
    year = day.year + months // 12
    month = months % 12 + 1
    return day.replace(year=year, month=month, day=min(day.day, _days_in(year, month))).isoformat()


def _days_in(year: int, month: int) -> int:
    return (dt.date(year + month // 12, month % 12 + 1, 1) - dt.timedelta(days=1)).day


class _Builder:
    def __init__(self, query: dict, catalog: Catalog, now: dt.datetime):
        self._query = query
        self._now = now
        self._dialect = catalog.dialect
        # Keyed by the reference as the spec wrote it, because that is what a column
        # qualifier is matched against, and valued with what the source calls the table.
        self._tables = {
            reference: self._describe(catalog, reference).name
            for reference in [query["from"], *(join["table"] for join in query.get("joins", []))]
        }
        self._parameters: dict[str, object] = {}

    @staticmethod
    def _describe(catalog: Catalog, reference: str):
        """The source's word on one table, resolved against the configured scope first and
        with the source's own exception types translated.

        The scope is applied here rather than trusted to the source. Every reference in a
        spec passes through this method, and a spec is the one name in the system that came
        from outside: `api.py` promises that a client cannot name a database, and until now
        that promise was kept by `DatabricksCatalog.describe` happening to call its own
        `qualify` on the way in. It was not on the protocol, no test asked a catalog to
        refuse anything, and the second implementation in the repository — the fixture
        catalog the whole suite runs against — quietly rewrote an out-of-scope name to an
        in-scope one instead. So the refusal is above the source now, where the promise is.

        A source client raises whatever it likes. The SDK's not-found and permission-denied
        are neither `ValueError` nor `RuntimeError`, so a spec naming a table that is gone,
        or one this credential cannot read, came out of here as a bare 500 while every
        other failure came out shaped, with a `spoke` saying which side to look at. The
        request was well formed and something behind the server refused it, which is what
        `RuntimeError` already means everywhere else on this path.

        `ValueError` passes through untouched: it is the spec's own fault and it answers
        400, which is what the scope raises for a name outside what this server reads."""
        name = catalog.scope.qualify(reference)
        try:
            return catalog.describe(name)
        except ValueError:
            raise
        except Exception as failure:
            raise RuntimeError(f"the source could not describe {reference}: {failure}") from failure

    def build(self) -> tuple[str, dict]:
        query = self._query
        limit_by = query.get("limit_by")
        names = output_columns(query)
        columns = ", ".join(self._quoted(name) for name in names)

        select = ", ".join(
            f"{expression} AS {self._quoted(name)}" for name, expression in zip(names, self._expressions())
        )
        body = f"SELECT {select} FROM {self._from()}{self._where()}{self._group_by()}"

        if limit_by:
            ranked = self._ranked(limit_by)
            outer = self._quoted(limit_by["column"])
            body = (
                f"WITH base AS ({body}), ranked AS ({ranked}) "
                f"SELECT {columns} FROM base WHERE {outer} IN (SELECT {outer} FROM ranked)"
            )

        return f"{body}{self._order_by()} LIMIT {self._bind(query['limit'])}", self._parameters

    def _expressions(self) -> list[str]:
        query = self._query
        items = [*query.get("select", []), *query.get("group_by", [])]
        return [self._item(item) for item in items] + [
            self._aggregate(aggregate) for aggregate in query.get("aggregates", [])
        ]

    def _item(self, item: dict) -> str:
        column = self._column(item["column"])
        unit = item.get("truncate")
        # A unit is one of the grammar's own keywords rather than a value, and a source
        # that took it as a parameter would still need it foldable at plan time. Which
        # spelling it goes into is the source's, because they differ: the unit is quoted
        # and first here and a bare keyword and second on BigQuery.
        return self._dialect.truncate.format(unit=unit, column=column) if unit else column

    def _aggregate(self, aggregate: dict) -> str:
        column = self._column(aggregate["column"]) if "column" in aggregate else "*"
        return AGGREGATES[aggregate["fn"]].format(column=column)

    def _ranked(self, limit_by: dict) -> str:
        """The top N values of the outer dimension, ranked over the grouped rows. A plain
        LIMIT on the outer query would cut rows, which on a multi series chart means
        cutting a series in half and drawing it as if that were the data."""
        outer = self._quoted(limit_by["column"])
        measure = limit_by["by"]
        # The validator refuses a 'by' that is not an aggregate alias, but the builder does not
        # take that on trust: what it compiles into SQL is never an assumption nobody checked.
        aggregate = next((a for a in self._query.get("aggregates", []) if a["as"] == measure), None)
        if aggregate is None:
            raise ValueError(
                f"query.limit_by.by: '{measure}' is not one of the query's aggregate aliases, "
                f"and ranking '{limit_by['column']}' needs a measure to rank it by"
            )
        if aggregate["fn"] not in RANKING:
            raise ValueError(
                f"query.limit_by.by: '{measure}' is an {aggregate['fn']}, which cannot be "
                f"re-aggregated to rank '{limit_by['column']}'"
            )
        direction = "DESC" if limit_by.get("direction", "desc") == "desc" else "ASC"
        ranking = f"{RANKING[aggregate['fn']]}({self._quoted(measure)})"
        return (
            f"SELECT {outer} FROM base GROUP BY {outer} "
            f"ORDER BY {ranking} {direction} LIMIT {self._bind(limit_by['limit'])}"
        )

    def _from(self) -> str:
        sql = self._table(self._query["from"])
        for join in self._query.get("joins", []):
            on = " AND ".join(
                f"{self._column(pair['left'])} = {self._column(pair['right'])}" for pair in join["on"]
            )
            kind = join.get("type", "inner").upper()
            sql += f" {kind} JOIN {self._table(join['table'])} ON {on}"
        return sql

    def _where(self) -> str:
        conditions = []
        for filter_ in self._query.get("filters", []):
            column = self._column(filter_["column"])
            op = filter_["op"]
            if op in ("is_null", "is_not_null"):
                conditions.append(f"{column} IS {'NOT ' if op == 'is_not_null' else ''}NULL")
            elif op in COMPARISONS:
                conditions.append(f"{column} {op} {self._bind(self._value(filter_['value']))}")
            else:
                values = ", ".join(self._bind(value) for value in filter_["value"])
                conditions.append(f"{column} {'NOT ' if op == 'not_in' else ''}IN ({values})")
        return (" WHERE " + " AND ".join(conditions)) if conditions else ""

    def _group_by(self) -> str:
        group_by = self._query.get("group_by", [])
        # The expressions again rather than the output names: a source that cannot group
        # by an alias is common enough that relying on it buys nothing.
        return (" GROUP BY " + ", ".join(self._item(item) for item in group_by)) if group_by else ""

    def _order_by(self) -> str:
        order_by = self._query.get("order_by", [])
        if not order_by:
            return ""
        terms = [
            f"{self._quoted(order['column'])} {order.get('direction', 'asc').upper()}" for order in order_by
        ]
        return " ORDER BY " + ", ".join(terms)

    def _table(self, reference: str) -> str:
        return self._dialect.qualified(self._tables[reference])

    def _column(self, reference: str) -> str:
        qualifier, _, column = reference.rpartition(".")
        # A qualifier names exactly one table here and an unqualified reference only
        # appears on a single table query, both established by the validator.
        table = (
            next(table for table in self._tables if names_table(table, qualifier))
            if qualifier
            else self._query["from"]
        )
        return f"{self._table(table)}.{self._dialect.quoted(column)}"

    def _quoted(self, name: str) -> str:
        return self._dialect.quoted(name)

    def _value(self, value):
        """A filter's value, with a relative one resolved. Resolved here and bound like any
        other, so nothing relative ever reaches the statement text: what the source is sent
        is the same parameter a written down date would have produced."""
        return resolve(value, self._now) if isinstance(value, dict) else value

    def _bind(self, value) -> str:
        name = f"p{len(self._parameters)}"
        self._parameters[name] = value
        return self._dialect.parameter.format(name=name)

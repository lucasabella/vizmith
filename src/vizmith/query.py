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


def build(spec: dict, catalog: Catalog) -> tuple[str, dict]:
    """SQL plus the values to bind to it. Needs the catalog for names only: a spec may
    name a table with fewer segments than the source uses, and the source is the only
    thing that can fill the rest in."""
    errors = validate_spec(spec)
    if errors:
        raise ValueError("spec is not valid: " + "; ".join(errors))
    return _Builder(spec["query"], catalog).build()


def execute(spec: dict, catalog: Catalog) -> list[dict]:
    """Rows as plain objects keyed by the query's output columns."""
    sql, parameters = build(spec, catalog)
    names = output_columns(spec["query"])
    return [dict(zip(names, row)) for row in catalog.run(sql, parameters)]


class _Builder:
    def __init__(self, query: dict, catalog: Catalog):
        self._query = query
        self._dialect = catalog.dialect
        # Keyed by the reference as the spec wrote it, because that is what a column
        # qualifier is matched against, and valued with what the source calls the table.
        self._tables = {
            reference: catalog.describe(reference).name
            for reference in [query["from"], *(join["table"] for join in query.get("joins", []))]
        }
        self._parameters: dict[str, object] = {}

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
        # that took it as a parameter would still need it foldable at plan time.
        return f"date_trunc('{unit}', {column})" if unit else column

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
                conditions.append(f"{column} {op} {self._bind(filter_['value'])}")
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

    def _bind(self, value) -> str:
        name = f"p{len(self._parameters)}"
        self._parameters[name] = value
        return self._dialect.parameter.format(name=name)

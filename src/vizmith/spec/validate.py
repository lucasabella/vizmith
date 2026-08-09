"""Validation for the Vizmith visualisation spec.

Two layers. The JSON Schema catches shape and vocabulary. The semantic pass
catches the things a schema cannot express: references that point at nothing,
and query shapes that would not produce the columns the chart asks for.

Errors are returned as strings rather than raised, because they are fed back to
the model on retry. Their wording is part of the interface.
"""

import json
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).parent / "v1" / "spec.schema.json"


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text())
    return Draft202012Validator(schema)


@lru_cache(maxsize=1)
def _filters_validator() -> Draft202012Validator:
    """The `filters` list on its own, judged by the grammar's own definition of one.

    A dashboard holds a list of filters that is not part of any spec: it is applied to each
    tile's query when the tile runs, so there is no query to attach it to and no `from` to
    resolve its columns against. Built out of the same `$defs`, so a filter the grammar
    stops allowing inside a query stops being storable on a dashboard on the same day.
    """
    schema = json.loads(SCHEMA_PATH.read_text())
    return Draft202012Validator(
        {
            "$defs": schema["$defs"],
            "type": "array",
            "maxItems": schema["$defs"]["query"]["properties"]["filters"]["maxItems"],
            "items": {"$ref": "#/$defs/filter"},
        }
    )


def validate_spec(spec: object) -> list[str]:
    """Return a list of human readable errors. Empty means the spec is valid."""
    errors = _schema_errors(spec)
    if errors or not isinstance(spec, dict):
        return errors
    return _semantic_errors(spec)


def validate_filters(filters: object) -> list[str]:
    """Everything wrong with a list of filters held apart from a query, as sentences.

    Two rules on top of the shape. A column here has to name its table, because the only
    thing that can be done with one of these is to match it against a tile's query — an
    unqualified `status` names a table in the tile it lands in and a different one in the
    next tile, and matching nothing would be the quietest of the possible wrongs. And the
    condition checks a spec's filters get are run here too: `is_null` with a value, and a
    relative value carrying a key its token does not read, are the same mistakes wherever
    they are written down.
    """
    errors = _errors_from(_filters_validator(), filters)
    if errors or not isinstance(filters, list):
        return errors
    for condition in conditions({"filters": filters}):
        if "." not in condition["column"]:
            errors.append(
                f"filters: '{condition['column']}' names no table. A filter held by a "
                "dashboard is matched against the tables each tile reads, so it names its "
                "own table rather than borrowing whichever one the tile happens to read"
            )
    return errors + _condition_errors(filters, "filters")


def _schema_errors(spec: object) -> list[str]:
    return _errors_from(_validator(), spec)


def _errors_from(validator: Draft202012Validator, value: object) -> list[str]:
    out = []
    for error in sorted(validator.iter_errors(value), key=lambda e: list(e.path)):
        location = "/".join(str(p) for p in error.path) or "<root>"
        out.append(f"{location}: {error.message}")
    return out


def _semantic_errors(spec: dict) -> list[str]:
    query = spec["query"]
    errors: list[str] = []

    tables = {query["from"]} | {j["table"] for j in query.get("joins", [])}
    qualification_required = len(tables) > 1

    for ref, where in _column_refs(query):
        qualifier = ref.rpartition(".")[0]
        if not qualifier:
            if qualification_required:
                errors.append(f"{where}: '{ref}' must be qualified with a table name when the query joins")
            continue

        named = sorted(t for t in tables if names_table(t, qualifier))
        if not named:
            errors.append(f"{where}: '{ref}' refers to table '{qualifier}', which is not in the query")
        elif len(named) > 1:
            errors.append(
                f"{where}: '{ref}' is ambiguous, '{qualifier}' names "
                f"{' and '.join(repr(t) for t in named)}, so qualify it with more segments"
            )

    errors += _computed_errors(query)

    errors += _condition_errors(query.get("filters", []), "query.filters")

    select = query.get("select", [])
    group_by = query.get("group_by", [])
    aggregates = query.get("aggregates", [])

    aliases = [aggregate["as"] for aggregate in aggregates]
    for condition in query.get("having", []):
        if condition["aggregate"] not in aliases:
            errors.append(
                f"query.having: '{condition['aggregate']}' is not one of this query's "
                f"aggregate aliases {aliases or '[]'}. A condition on a measure names the "
                "measure it is about; a condition on a column is a filter"
            )
    if query.get("having") and not aggregates:
        errors.append(
            "query.having: a query with no aggregates has no measure to put a condition on. "
            "Use 'filters', which apply before the rows are grouped"
        )

    errors += _window_errors(query)

    if select and (group_by or aggregates):
        errors.append(
            "query: 'select' cannot be combined with 'group_by' or 'aggregates'. An "
            "aggregated query puts its dimensions in 'group_by', which are already output "
            "columns, and nothing in 'select'"
        )
    if not select and not group_by and not aggregates:
        errors.append("query: needs 'select', or 'group_by' and 'aggregates'")
    if group_by and not aggregates:
        errors.append("query: 'group_by' without 'aggregates' produces no measure")

    output = output_columns(query)

    duplicates = {name for name in output if output.count(name) > 1}
    for name in sorted(duplicates):
        errors.append(f"query: output column '{name}' is produced more than once")

    known = set(output)
    for order in query.get("order_by", []):
        if order["column"] not in known:
            errors.append(f"query.order_by: '{order['column']}' is not an output column of the query")

    limit_by = query.get("limit_by")
    if limit_by:
        if limit_by["column"] not in known:
            errors.append(
                f"query.limit_by.column: '{limit_by['column']}' is not an output column of the query"
            )
        # A window is worked out over the rows the ranking kept, so it does not exist yet
        # where the ranking runs. Ranking one would compile to a reference to a column the
        # inner query never produced, which is a source's error about SQL nobody wrote.
        elif limit_by["column"] in {window["as"] for window in query.get("windows", [])}:
            errors.append(
                f"query.limit_by.column: '{limit_by['column']}' is a window, which is worked "
                "out over the rows the ranking keeps and so does not exist where the ranking "
                "runs. Rank one of the query's dimensions"
            )
        # 'by' is what the ranking sorts on, so it is a measure and not merely an output
        # column. A dimension there passes 'is an output column' and then leaves the builder
        # with no aggregate to re-aggregate.
        if limit_by["by"] not in {aggregate["as"] for aggregate in aggregates}:
            errors.append(
                f"query.limit_by.by: '{limit_by['by']}' is not one of the query's aggregate "
                f"aliases, and ranking '{limit_by['column']}' needs a measure to rank it by"
            )
        elif limit_by["column"] == limit_by["by"]:
            errors.append("query.limit_by: 'column' and 'by' must differ, ranking needs a measure")

    encoding = spec["chart"]["encoding"]
    for channel, spec_channel in encoding.items():
        field = spec_channel["field"]
        if field not in known:
            errors.append(f"chart.encoding.{channel}: '{field}' is not an output column of the query")

        # A format says how a number reads, so a channel bound to something else has
        # nothing for it to apply to. The schema cannot say this: it would have to make
        # `format` conditional on a sibling property, which is the `if`/`then` shape that
        # costs a readable error message and that the endpoints refuse to constrain on.
        if "format" in spec_channel and spec_channel["type"] != "quantitative":
            errors.append(
                f"chart.encoding.{channel}: 'format' says how a number reads, and "
                f"'{field}' is bound as '{spec_channel['type']}'. Only a quantitative "
                "channel carries one"
            )

    # The value axis is what a chart is read against, so it carries a measure. A nominal 'y'
    # draws categories up the side and produces a picture with no quantity anywhere in it.
    if encoding["y"]["type"] != "quantitative":
        errors.append(
            f"chart.encoding.y: '{encoding['y']['field']}' is bound to the value axis as "
            f"'{encoding['y']['type']}', but the value axis carries a measure, so its type "
            "is 'quantitative'"
        )

    # An absent 'x' is the answer to a question with no dimension, and it draws the measure as
    # one figure. That only holds where the query returns one row, which is a query that
    # aggregates and groups by nothing, and one figure has nothing to colour.
    if "x" not in encoding:
        if select or group_by:
            errors.append(
                "chart.encoding: a chart without 'x' draws one figure, so its query cannot have "
                "'select' or 'group_by', which produce a row each"
            )
        if "color" in encoding:
            errors.append("chart.encoding: 'color' needs an 'x', because one figure has nothing to colour")
    elif encoding["x"]["field"] == encoding["y"]["field"]:
        errors.append(
            f"chart.encoding: '{encoding['x']['field']}' is bound to both 'x' and 'y', which plots a "
            "measure against itself. A query with no dimension omits 'x' and draws one figure"
        )

    # A row limit on a multi series chart cuts series members at an arbitrary point, which renders
    # a chart that looks right and is not. limit_by makes the outer dimension explicit instead.
    if "color" in encoding and len(group_by) > 1 and not limit_by:
        errors.append(
            "query: a multi series chart needs 'limit_by', because 'limit' truncates rows and would "
            "drop part of a series"
        )

    return errors


def _computed_errors(query: dict) -> list[str]:
    """What the schema cannot say about a computed column.

    Three things, and each is a spec that validates against the shape and means something
    nobody asked for. An item carrying both a `column` and an `expression` compiles as one
    of the two and silently drops the other — the schema sees two properties it allows and
    has nothing to say about them together. `truncate` rounds a date to a unit and an
    expression is a number, so a unit on one is a key the builder ignores. And an operation
    over two numbers is a constant: `2 * 3` compiles, runs, and draws a column of sixes,
    which is the quiet kind of wrong this project exists to avoid.
    """
    errors = []
    for where, items in (
        ("query.select", query.get("select", [])),
        ("query.group_by", query.get("group_by", [])),
        ("query.aggregates", query.get("aggregates", [])),
    ):
        for item in items:
            expression = item.get("expression")
            if expression is None:
                continue
            named = item["as"]
            if "column" in item:
                errors.append(
                    f"{where}: '{named}' has both a 'column' and an 'expression', and only "
                    "one of them can be what it reads. Keep whichever it means"
                )
            if "truncate" in item:
                errors.append(
                    f"{where}: '{named}' computes a number, and 'truncate' rounds a date to "
                    "a unit, so there is nothing here for it to round"
                )
            if not any(True for _ in operands(item)):
                errors.append(
                    f"{where}: '{named}' computes {expression['left']} {expression['op']} "
                    f"{expression['right']}, which names no column and is the same number in "
                    "every row"
                )
    return errors


# The window functions that reach back a row rather than accumulating forwards. Which row
# is the one before depends on the walk being unambiguous, which is the whole of why they
# are held to a dimension below.
LAGGING = ("previous", "difference", "change")


def _window_errors(query: dict) -> list[str]:
    """What the schema cannot say about a window, which is everything about what it reads.

    A window names output columns rather than source columns, so nothing about it can be
    judged from the shape alone: whether `of` is a measure, whether `along` and
    `partition_by` are dimensions this query has, and whether the rows a partition holds
    are the rows the function was meant to walk.

    Each window reports at most one thing, because the first thing wrong with it decides
    what the rest of it means. A window over a column that is not a measure has no measure
    to be partitioned or walked, and three sentences about one mistake is a retry loop
    reading three ways to have written the same spec.
    """
    windows = query.get("windows", [])
    if not windows:
        return []

    grouped = dimensions(query)
    measures = [aggregate["as"] for aggregate in query.get("aggregates", [])]
    errors: list[str] = []
    for window in windows:
        named = window["as"]
        if window["of"] not in measures:
            errors.append(
                f"query.windows: '{named}' is taken over '{window['of']}', which is not one of "
                f"this query's aggregate aliases {measures or '[]'}. A window compares a "
                "measure across rows, so it names one"
            )
        elif not grouped:
            errors.append(
                f"query.windows: '{named}' reads a row against the other rows, and a query "
                "that groups by nothing produces one row. Group by something, or ask for the "
                "measure on its own"
            )
        else:
            errors += _read_errors(window, grouped, measures, [w["as"] for w in windows])
    return errors


def _read_errors(
    window: dict, grouped: list[str], measures: list[str], windows: list[str]
) -> list[str]:
    """Whether the rows this window reads are the rows it was written for.

    Three rules, and each of them is a window that compiles, runs and answers a question
    nobody asked. A partition covering every dimension leaves one row per partition, so a
    share is 1 in every row and a rank is 1 in every row — the column of sixes rule, applied
    to a window. A walk that leaves a dimension neither partitioned nor walked steps from
    one value of it to another, so a running total over months and categories accumulates
    across both and means nothing. And a walk along a measure has no defined order where two
    rows hold the same value, which a running total survives — its frame gives every tied
    row the same total — and a lag does not, since which row is the one before is then the
    source's to decide.
    """
    named = window["as"]
    partition = window.get("partition_by", [])
    outside = [column for column in partition if column not in grouped]
    if outside:
        return [
            (
                f"query.windows: '{named}' partitions by {_listed(outside)}, which "
                f"{'are' if len(outside) > 1 else 'is'} not among this query's dimensions "
                f"{grouped}. A window restarts inside a dimension, so it names a column the "
                "query groups by"
            )
        ]

    # Asked before the partition is counted, because it is the same mistake said usefully:
    # a window that partitions by the column it walks is one where both sentences are true
    # and only this one names the pair that caused it.
    along = window.get("along")
    if along is not None and along in partition:
        return [
            (
                f"query.windows: '{named}' walks '{along}' and partitions by it as well, so every "
                "step stays inside one value of it and the window never moves"
            )
        ]

    remaining = [dimension for dimension in grouped if dimension not in partition]
    if not remaining:
        return [
            (
                f"query.windows: '{named}' partitions by every dimension the query has, so each "
                "partition holds one row and there is nothing to read it against. Leave the "
                "dimension it is read across out of 'partition_by'"
            )
        ]

    if along is None:
        return []
    if along in measures:
        if window["fn"] in LAGGING:
            return [
                (
                    f"query.windows: '{named}' walks '{along}', which is a measure. Two rows can "
                    "hold the same measure and which of them comes first is then the source's to "
                    f"decide, so '{window['fn']}' walks a dimension, whose values the grouping "
                    "makes unique"
                )
            ]
        if len(remaining) > 1:
            return [
                (
                    f"query.windows: '{named}' walks the measure '{along}' over rows that are one "
                    f"per combination of {_listed(remaining)}, so each step crosses from one of "
                    "them to another. Partition by all of them but one"
                )
            ]
        return []
    # Named before the sentence below, which would otherwise say a window alias is not an
    # output column when it is one. Every window is worked out in the same pass over the same
    # rows, so a window is not there yet to be read along.
    if along in windows:
        return [
            (
                f"query.windows: '{named}' walks '{along}', which is a window. They are all "
                "worked out in one pass over the same rows, so one cannot be read along "
                "another"
            )
        ]
    if along not in grouped:
        return [f"query.windows: '{named}' walks '{along}', which is not an output column of the query"]

    others = [dimension for dimension in remaining if dimension != along]
    if others:
        return [
            (
                f"query.windows: '{named}' walks '{along}' and the query also groups by "
                f"{_listed(others)}, so one step of the window crosses from one value of "
                f"{_listed(others)} to another. Put {'them' if len(others) > 1 else 'it'} in "
                "'partition_by', so the window restarts inside each"
            )
        ]
    return []


def _listed(names: list[str]) -> str:
    return " and ".join(repr(name) for name in names)


def _condition_errors(filters: list, where: str) -> list[str]:
    """What is wrong with the conditions themselves, wherever they were written down.

    `where` is the path they are reported under, because the same list is a query's
    `filters` and a dashboard's, and a message that named the wrong one would send somebody
    to look in a spec for a filter that is on the dashboard around it."""
    errors: list[str] = []
    for condition in conditions({"filters": filters}):
        if condition["op"] in ("is_null", "is_not_null") and "value" in condition:
            errors.append(
                f"{where}: '{condition['op']}' takes no value, but one was given "
                f"for '{condition['column']}'"
            )
        errors += _relative_errors(condition, where)
    return errors


def _relative_errors(filter_: dict, where: str = "query.filters") -> list[str]:
    """What the schema cannot say about a relative value without answering in the language
    of `if` and `then`.

    The schema holds what each token *needs* — `start_of` a unit, `ago` a unit and a count —
    because a missing key is what a model most often gets wrong. What is here is the other
    half: a key that is present and means nothing. `{"relative": "now", "unit": "month"}`
    validates against a schema that only checks the required side, and a model that wrote it
    believes it asked for the start of the month. Saying so is cheaper than the alternative,
    which is a filter that quietly means this instant.
    """
    value = filter_.get("value")
    if not isinstance(value, dict) or "relative" not in value:
        return []
    token = value["relative"]
    spare = [key for key in ("unit", "count") if key in value and key not in _TAKES[token]]
    if not spare:
        return []
    takes = _TAKES[token]
    wants = f"takes {' and '.join(repr(key) for key in takes)}" if takes else "takes neither"
    named = " and ".join(repr(key) for key in spare)
    return [
        (
            f"{where}: a relative value of '{token}' {wants}, so {named} "
            f"{'do' if len(spare) > 1 else 'does'} nothing here and the filter does not "
            "mean what it says"
        )
    ]


# What each relative token reads. Anything else present alongside it is a key the builder
# ignores, which is the same as a spec that lies about what it asked for.
_TAKES = {"now": (), "today": (), "start_of": ("unit",), "ago": ("unit", "count")}


def output_columns(query: dict) -> list[str]:
    """The names the query produces, in the order the builder emits them: every select or
    group_by item by its alias or the last segment of its column, then every aggregate
    alias. The validator checks references against this list and the builder compiles the
    list into the SELECT, so the result set contract has one definition rather than two
    that can disagree.

    An item that computes has no column to fall back on, which is why the schema makes its
    alias required: `a * b` has no name of its own and something has to be what the chart
    binds a channel to.

    A window's alias goes last, after every measure, because a window is worked out over
    the rows the rest of the query produced: the builder compiles them in a second SELECT
    over the first, and the order here is the order that SELECT is written in."""
    names = dimensions(query)
    names.extend(aggregate["as"] for aggregate in query.get("aggregates", []))
    names.extend(window["as"] for window in query.get("windows", []))
    return names


def dimensions(query: dict) -> list[str]:
    """What the query produces a row per: every `select` or `group_by` item, by its alias or
    the last segment of its column.

    Separate from `output_columns` because a window has to tell the two apart. Partitioning
    or walking a measure is a different question from partitioning or walking a dimension,
    and the answer to one of them is that it cannot be asked."""
    return [
        item.get("as") or item["column"].rsplit(".", 1)[-1]
        for item in [*query.get("select", []), *query.get("group_by", [])]
    ]


def names_table(table: str, qualifier: str) -> bool:
    """Whether a qualifier names a table. Any trailing part of the reference counts, so
    'orders' and 'shop.orders' both name 'warehouse.shop.orders'. A model that had to
    repeat three segments in every column reference would get one of them wrong."""
    segments = table.split(".")
    wanted = qualifier.split(".")
    return len(wanted) <= len(segments) and segments[-len(wanted) :] == wanted


def conditions(query: dict):
    """Every condition in `filters`, with a disjunction replaced by the conditions it holds.

    A filter is either a condition or `{"any": [...]}`, one level deep, so everything that
    reads conditions — the reference check, the null check, the relative check, the eval
    harness counting which columns an answer touched — wants the flat list and none of them
    wants to know which of the two shapes it came from. One nesting level is the whole of
    what the grammar allows, so this is a loop rather than a walk."""
    for filter_ in query.get("filters", []):
        yield from filter_.get("any", [filter_])


def operands(item: dict):
    """The column references a computed item holds, which is one, two, or none: an operand
    is a column or a number, and a number is bound rather than resolved. Nothing here knows
    whether the item computes; an item that does not has no `expression` and yields nothing,
    which is what lets every caller ask the same question of every item."""
    expression = item.get("expression")
    if expression is None:
        return
    for side in ("left", "right"):
        if isinstance(expression[side], str):
            yield expression[side]


def _column_refs(query: dict):
    yield from ((f["column"], "query.filters") for f in conditions(query))
    for where, items in (
        ("query.select", query.get("select", [])),
        ("query.group_by", query.get("group_by", [])),
        ("query.aggregates", query.get("aggregates", [])),
    ):
        for item in items:
            if "column" in item:
                yield item["column"], where
            yield from ((reference, where) for reference in operands(item))
    for join in query.get("joins", []):
        for on in join["on"]:
            yield on["left"], "query.joins.on"
            yield on["right"], "query.joins.on"

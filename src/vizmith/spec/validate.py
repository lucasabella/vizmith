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


def validate_spec(spec: object) -> list[str]:
    """Return a list of human readable errors. Empty means the spec is valid."""
    errors = _schema_errors(spec)
    if errors or not isinstance(spec, dict):
        return errors
    return _semantic_errors(spec)


def _schema_errors(spec: object) -> list[str]:
    out = []
    for error in sorted(_validator().iter_errors(spec), key=lambda e: list(e.path)):
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

    for filter_ in conditions(query):
        if filter_["op"] in ("is_null", "is_not_null") and "value" in filter_:
            errors.append(
                f"query.filters: '{filter_['op']}' takes no value, but one was given "
                f"for '{filter_['column']}'"
            )
        errors += _relative_errors(filter_)

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


def _relative_errors(filter_: dict) -> list[str]:
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
            f"query.filters: a relative value of '{token}' {wants}, so {named} "
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
    binds a channel to."""
    names = [
        item.get("as") or item["column"].rsplit(".", 1)[-1]
        for item in [*query.get("select", []), *query.get("group_by", [])]
    ]
    names.extend(aggregate["as"] for aggregate in query.get("aggregates", []))
    return names


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

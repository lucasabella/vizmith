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

    for filter_ in query.get("filters", []):
        if filter_["op"] in ("is_null", "is_not_null") and "value" in filter_:
            errors.append(
                f"query.filters: '{filter_['op']}' takes no value, but one was given "
                f"for '{filter_['column']}'"
            )

    select = query.get("select", [])
    group_by = query.get("group_by", [])
    aggregates = query.get("aggregates", [])

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


def output_columns(query: dict) -> list[str]:
    """The names the query produces, in the order the builder emits them: every select or
    group_by item by its alias or the last segment of its column, then every aggregate
    alias. The validator checks references against this list and the builder compiles the
    list into the SELECT, so the result set contract has one definition rather than two
    that can disagree."""
    names = [
        item.get("as") or item["column"].rsplit(".", 1)[-1]
        for item in [*query.get("select", []), *query.get("group_by", [])]
    ]
    names.extend(aggregate["as"] for aggregate in query.get("aggregates", []))
    return names


def referenced(query: dict) -> tuple[set[str], set[str]]:
    """Every table and every column the query names, in short names.

    One definition, because two readers need it and they have to agree: the eval harness
    scores an answer on what it references, and a critique refuses a suggestion that
    references something the spec it corrects did not. Two implementations of "what does
    this query read" would be two answers to that question.

    Aliases are not columns and are left out: `order_by` and `limit_by` name output
    columns, which the query itself invented, so counting them would measure a spec
    against its own vocabulary.
    """
    default = short(query["from"])
    tables = {default} | {short(join["table"]) for join in query.get("joins", [])}

    columns = {
        normalised(item["column"], default)
        for item in [*query.get("select", []), *query.get("group_by", []), *query.get("filters", [])]
    }
    columns |= {
        normalised(aggregate["column"], default)
        for aggregate in query.get("aggregates", [])
        if "column" in aggregate
    }
    for join in query.get("joins", []):
        for pair in join["on"]:
            columns |= {normalised(pair["left"], default), normalised(pair["right"], default)}
    return tables, columns


def short(reference: str) -> str:
    """A table under the name a person uses. A spec may name a table with one segment or
    with three, and a model reading a profile writes the qualified one, so the last
    segment is the only name the two share."""
    return reference.rsplit(".", 1)[-1].lower()


def normalised(reference: str, default: str) -> str:
    """A column as table.column, whatever the spec qualified it with."""
    qualifier, _, column = reference.rpartition(".")
    return f"{short(qualifier) if qualifier else default}.{column}".lower()


def names_table(table: str, qualifier: str) -> bool:
    """Whether a qualifier names a table. Any trailing part of the reference counts, so
    'orders' and 'shop.orders' both name 'warehouse.shop.orders'. A model that had to
    repeat three segments in every column reference would get one of them wrong."""
    segments = table.split(".")
    wanted = qualifier.split(".")
    return len(wanted) <= len(segments) and segments[-len(wanted) :] == wanted


def _column_refs(query: dict):
    yield from ((f["column"], "query.filters") for f in query.get("filters", []))
    yield from ((s["column"], "query.select") for s in query.get("select", []))
    yield from ((g["column"], "query.group_by") for g in query.get("group_by", []))
    yield from ((a["column"], "query.aggregates") for a in query.get("aggregates", []) if "column" in a)
    for join in query.get("joins", []):
        for on in join["on"]:
            yield on["left"], "query.joins.on"
            yield on["right"], "query.joins.on"

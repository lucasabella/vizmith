"""A question becomes a validated spec, or it becomes the reasons it could not.

The loop is the whole design. A model answers, the validator judges, and a rejected answer
goes back as the next question with the validator's own words attached. Nothing here ever
repairs a spec: a patched spec is one nobody specified, and it would pass validation while
answering a question the person did not ask.

What reaches a model is a question and a profile. Never rows. `prompt` takes profiles and
a question and has no parameter a result set could arrive through, so putting one in a
prompt means changing this signature rather than changing a line inside it.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field

from vizmith.model import Model
from vizmith.profiler import TableProfile
from vizmith.spec import SCHEMA_PATH, validate_spec

# Enough for the model to read its own mistake and fix it, few enough that a hopeless
# question fails while somebody is still watching. Every attempt is a billed request.
ATTEMPTS = 3

SCHEMA = json.loads(SCHEMA_PATH.read_text())

INSTRUCTIONS = """You write a chart specification for Vizmith. It is JSON: a query that
describes tables, joins, filters, grouping and aggregation, and a chart that binds the
query's output columns to visual channels. A deterministic builder compiles the query to
SQL, so you never write SQL.

Answer with the specification as JSON and nothing else. No explanation, no code fence.

Use only the tables and columns listed below. A column that is not listed does not exist.
Refer to a column as table.column when the query names more than one table.

A query either selects rows or aggregates them, never both. An aggregated query uses
group_by and aggregates, and its group_by items are already output columns, so repeating
them in select produces each of them twice. Use select only for a query that aggregates
nothing.

A question with no dimension is answered by one figure, not by a chart. Its query groups by
nothing and its chart binds y to the measure and omits x. Binding the same column to x and y
plots a measure against itself and is rejected.

Every query needs a row limit. A chart that binds a colour channel also needs limit_by,
whose column is that colour dimension and whose by is an aggregate's alias, so the two are
never the same. It keeps the top N values of one dimension whole instead of cutting rows
off partway through a series."""


@dataclass(frozen=True)
class Answer:
    """A validated spec, or the errors from the last attempt when there is none."""

    spec: dict | None
    errors: list[str] = field(default_factory=list)
    attempts: int = 0


def prompt(question: str, tables: Sequence[TableProfile], errors: Sequence[str] = ()) -> str:
    """The question, what the tables look like, and what went wrong last time."""
    parts = [
        INSTRUCTIONS,
        "The specification must validate against this JSON Schema:",
        json.dumps(SCHEMA, indent=None),
        "Tables:",
        "\n\n".join(_table(table) for table in tables),
        f"Question: {question}",
    ]
    if errors:
        parts.append(
            "Your previous answer was rejected by the validator, which said:\n"
            + "\n".join(f"- {error}" for error in errors)
            + "\nAnswer the same question again, corrected."
        )
    return "\n\n".join(parts)


def ask(
    question: str,
    tables: Sequence[TableProfile],
    model: Model,
    attempts: int = ATTEMPTS,
    constrained: bool = False,
) -> Answer:
    """Ask until the validator is satisfied or the attempts run out.

    `constrained` says whether the endpoint honours a JSON Schema, which the adapter
    reports and this does not check, because checking costs a request of its own. Where it
    is false the loop below is the fallback and it runs more often.
    """
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        text = model.complete(prompt(question, tables, errors), SCHEMA if constrained else None).text
        try:
            spec = json.loads(text)
        except json.JSONDecodeError as failure:
            errors = [f"the answer was not JSON: {failure}"]
            continue
        errors = validate_spec(spec)
        if not errors:
            return Answer(spec=spec, attempts=attempt)
    return Answer(spec=None, errors=errors, attempts=attempts)


def _table(table: TableProfile) -> str:
    lines = [f"{table.table}, {table.row_count} rows"]
    lines += [f"  {_column(column)}" for column in table.columns]
    return "\n".join(lines)


def _column(column) -> str:
    """One column on one line. Every figure says what kind of figure it is, because a
    distinct count is usually an estimate while the samples beside it are exact, and a
    reader that cannot tell them apart will treat a guess as a fact."""
    parts = [f"{column.name} {column.type}"]
    if column.null_rate:
        parts.append(f"{column.null_rate:.1%} null")
    counted = "distinct" if column.distinct_count_exact else "distinct, approximate"
    parts.append(f"{column.distinct_count} {counted}")
    if column.minimum is not None:
        parts.append(f"from {column.minimum} to {column.maximum}")
    if column.samples:
        parts.append("values: " + ", ".join(column.samples))
    return ", ".join(parts)

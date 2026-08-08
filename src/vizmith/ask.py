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

from vizmith.catalog import Relationship
from vizmith.model import Model
from vizmith.profiler import TableProfile
from vizmith.relevance import select
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
plots a measure against itself and is rejected. y is the value axis, so it carries a measure
and its type is quantitative; a dimension belongs on x or on colour.

A condition on a measure is 'having' rather than 'filters'. A filter applies before the
rows are grouped and names a column; a having applies after and names one of the query's own
aggregate aliases. "Countries with revenue over a million" is
having: [{"aggregate": "revenue", "op": ">", "value": 1000000}].

A question about a moving window says so rather than writing a date down. A filter value
may be {"relative": "today"} or {"relative": "now"}, {"relative": "start_of", "unit": U} for
the beginning of the current year, quarter, month, week, day or hour, or {"relative": "ago",
"unit": U, "count": N} for N of those units before now. Use one wherever the question is
about the present — "this month", "the last 30 days", "so far this year" — because a date
written down is the answer to the day it was written and this spec may be saved and run
again. Each token reads only its own keys: "today" and "now" take neither a unit nor a
count, and "start_of" takes no count.

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


def prompt(
    question: str,
    tables: Sequence[TableProfile],
    errors: Sequence[str] = (),
    constrained: bool = False,
    withheld: int = 0,
) -> str:
    """The question, what the tables look like, and what went wrong last time.

    `tables` is what the model is to read, which on a schema larger than a prompt can hold
    is a selection rather than all of it (`relevance.py`). `withheld` is how many were left
    out, and the heading says so: a model handed four tables and told nothing would read
    them as the whole source and answer a question about a fifth by inventing it.

    `constrained` says the schema is going with the request as the response format, in
    which case it is left out of the text: the endpoint is enforcing the schema it was
    handed, and a second copy in the prose buys nothing and costs about 1,350 tokens per
    attempt. Where the endpoint does not honour one, the text is the only place it can go.

    The instructions are sent either way. They say what a schema cannot: that an aggregated
    query puts its dimensions in `group_by` and nothing in `select`, that a question with no
    dimension omits `x`, that a colour channel needs `limit_by`. Those are the semantic
    validator's rules, and they are why the retry loop converges.

    The order is instructions, schema, tables, question, then the validator's errors, and
    the question goes second to last on purpose: everything before it is byte-identical
    between two questions over one schema, which is the prefix a provider's prompt cache
    can hit. A test asserts that prefix so it stays structural rather than accidental."""
    parts = [INSTRUCTIONS]
    if not constrained:
        parts += [
            "The specification must validate against this JSON Schema:",
            json.dumps(SCHEMA, indent=None),
        ]
    # A subset says so. A model told these are the tables would otherwise read a schema of
    # four tables as the whole source and answer a question about a fifth by inventing it.
    heading = (
        "Tables:"
        if not withheld
        else (
            f"Tables, the ones this question appears to be about. The schema holds "
            f"{withheld} more, which are not here. Use only what is below, and if the "
            f"question needs a table that is not, say so rather than inventing one:"
        )
    )
    parts += [
        heading,
        "\n\n".join(block(table) for table in tables),
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
    relationships: Sequence[Relationship] = (),
) -> Answer:
    """Ask until the validator is satisfied or the attempts run out.

    `constrained` says whether the endpoint honours a JSON Schema, which the adapter
    reports and this does not check, because checking costs a request of its own. Where it
    is false the loop below is the fallback and it runs more often.

    The tables the model reads are chosen here rather than by the caller, so that every
    caller asks the same way: the API, the eval harness and a test all send the selection
    `relevance.select` makes and not whatever they happened to have. `relationships` is what
    a join may be resolved through, and it is what keeps a table a correct answer has to
    join through readable even where the question never names it.
    """
    chosen = select(question, tables, relationships)
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        written = prompt(
            question,
            chosen.tables,
            errors,
            constrained=constrained,
            withheld=chosen.withheld,
        )
        text = model.complete(written, SCHEMA if constrained else None).text
        try:
            spec = json.loads(text)
        except json.JSONDecodeError as failure:
            errors = [f"the answer was not JSON: {failure}"]
            continue
        errors = validate_spec(spec)
        if not errors:
            return Answer(spec=spec, attempts=attempt)
    return Answer(spec=None, errors=errors, attempts=attempts)


def block(table: TableProfile) -> str:
    """One table as the model reads it. Public because a critique's prompt writes the same
    block, and two spellings of what a profile looks like is two of them to drift."""
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

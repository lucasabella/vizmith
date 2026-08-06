"""A second opinion on a spec that already exists: what a rule refuses about it, and a
suggestion that answers the refusal.

Two halves, and the split between them is the whole design.

**The finding is a rule.** What may be said about a spec is what is refusable — a mark the
shape of the result contradicts — and nothing else. It is the same rule the eval harness
scores a mark with, so a critique cannot approve of a chart the harness refuses or refuse
one it accepts. A critique that said "this would read better as a line" would be scoring a
preference, and a preference shipped as a suggestion is still shipped: it moves whenever
somebody's taste does and there is no run that can say whether it held.

**The suggestion is the model's.** The rule refuses and does not name a replacement, on
purpose: several marks suit most shapes and naming the best one is exactly the preference
above. So the replacement is asked for rather than computed, it arrives as a proposal, and
a person takes it or leaves it. That is the one judgement in this file that is not a rule,
and it is the reason there is a model call here at all.

What that buys is a refusal a person meets before they pay for it. The harness judges a
mark against the rows it fetched; this judges it against the profiles, so an arc that
cannot be read is named before the query runs rather than after the warehouse has billed
for it. What it costs is that a profile bounds a row count rather than counting it, so the
number in a finding is an upper bound and says where it came from. Where nothing bounds it
below the query's own limit, nothing is claimed: a limit of 500 over a column with four
values is four slices, and a finding built out of the 500 would be wrong.

Two things this does not do. It does not change the query, which is the question the person
asked — an answer that changed it would answer a different one, so a suggestion whose query
is not the one it was given is refused here rather than shown. And it does not apply
anything: what comes back is a spec beside the spec, and the one on screen stays until
somebody asks for the other.

Nothing here reaches a source and nothing here sees a row. It takes profiles and a spec,
the same way `ask` takes profiles and a question.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass

# The same schema and the same attempt limit as a question. This is that loop with a
# different prompt: a model answers, the rules judge, and a rejected answer goes back with
# what refused it attached.
from vizmith.ask import ATTEMPTS, SCHEMA, block
from vizmith.model import Model
from vizmith.profiler import ColumnProfile, TableProfile
from vizmith.spec import names_table, validate_spec

# More slices than this and a reader is matching them to a legend rather than comparing
# them. The same figure as the colour order the renderer refuses to go past, and for the
# same reason: past it the chart is drawn and cannot be read.
ARC_SLICES = 8

INSTRUCTIONS = """You correct a chart specification for Vizmith. It is JSON: a query that
describes tables, joins, filters, grouping and aggregation, and a chart that binds the
query's output columns to visual channels.

A rule has refused the chart below. Answer with the whole specification as JSON and nothing
else. No explanation, no code fence.

Change the chart only. The query is the question the person asked, and an answer that
changes it answers a different question, so send the query back exactly as it is here: the
same tables, joins, filters, grouping, aggregates, order and limits, in the same order.
Everything outside the chart comes back unchanged too, the title included.

Pick the mark the shape of the result supports. The rule that refused this one does not
name a replacement, because several marks suit most shapes, which is what you are being
asked for. A bar compares categories. A line or an area reads a measure over time. A point
reads one measure against another. An arc reads parts of a whole, and only where there are
few enough slices to compare at a glance. Do not answer with a mark the same rule would
refuse again."""


@dataclass(frozen=True)
class Finding:
    """One thing a rule refuses about a spec, in the words of the rule that refused it.

    `bound` is the sentence saying where a number in `says` came from, or empty where the
    rule needed none. It is separate because a figure derived from a profile is an upper
    bound rather than a count, and a reader who cannot tell those apart reads a bound as a
    fact.
    """

    says: str
    bound: str = ""

    def __str__(self) -> str:
        return f"{self.says}. {self.bound}" if self.bound else self.says


@dataclass(frozen=True)
class Critique:
    """What the rules refuse about a spec, and the spec suggested in its place.

    `spec` is None where there was nothing to say, which is the common case and costs no
    request, and also where the model was asked and nothing it answered survived the rules.
    `errors` tells those two apart: empty means nothing was wrong.
    """

    findings: tuple[Finding, ...] = ()
    spec: dict | None = None
    errors: tuple[str, ...] = ()
    attempts: int = 0
    asked: bool = False

    def as_dict(self) -> dict:
        return {
            "findings": [str(finding) for finding in self.findings],
            "spec": self.spec,
            "errors": list(self.errors),
        }


def misreads(chart: dict, slices: int | None = None) -> str:
    """Why this mark does not suit the shape of this result, or an empty string.

    A rule rather than a list per chart, because "defensible" is a property of the result
    and there is one implementation of every rule in this project. It refuses rather than
    ranks: several marks suit most shapes and picking a favourite among them would score a
    preference.

    `slices` is how many slices an arc would draw, where the caller knows: the harness
    counts the rows it fetched, the critique bounds them from the profiles, and None is a
    caller that cannot say, which leaves the arc judged on its axis alone.
    """
    mark = chart["mark"]
    encoding = chart["encoding"]

    if "x" not in encoding:
        # A figure. `mark` says nothing on one, the same way `stack` says nothing on an arc.
        return ""
    if "color" in encoding and mark == "arc":
        return "an arc has one ring of slices and cannot carry a colour channel as a second series"
    if unreadable(chart, slices):
        return f"an arc of {slices} slices cannot be read; {ARC_SLICES} is the most that can"

    kind = encoding["x"]["type"]
    if kind == "temporal" and mark == "arc":
        return "an arc has no order, and a time axis is nothing but order"
    if kind == "quantitative" and mark in ("bar", "arc"):
        drawn = "an arc" if mark == "arc" else "a bar"
        return f"{drawn} treats a quantitative axis as categories, and this one is a measure"
    if kind in ("nominal", "ordinal") and mark in ("line", "area"):
        return f"a {mark} draws a category axis as if the gaps between its values meant something"
    return ""


def unreadable(chart: dict, slices: int | None) -> bool:
    """Whether this is an arc with more slices than can be compared. Its own predicate
    because two callers ask it: the rule above, and whatever wants to say where the number
    came from without matching on the sentence the rule wrote."""
    encoding = chart["encoding"]
    return (
        chart["mark"] == "arc"
        and "x" in encoding
        and "color" not in encoding
        and slices is not None
        and slices > ARC_SLICES
    )


def findings(spec: dict, tables: Sequence[TableProfile] = ()) -> list[Finding]:
    """What the rules refuse about this spec. Empty means there is nothing to suggest.

    The spec is one the validator has already accepted; what is left to say is what a
    schema and a semantic pass cannot, which is whether the mark suits the shape of the
    result. That shape is read off the profiles, so this costs no query and no request.
    """
    chart = spec["chart"]
    bound = _slices(spec, tables)
    said = misreads(chart, bound.count if bound else None)
    if not said:
        return []
    # The bound is only worth quoting where it is what refused the chart.
    quoted = bound.because if bound and unreadable(chart, bound.count) else ""
    return [Finding(says=said, bound=quoted)]


def critique(
    spec: dict,
    tables: Sequence[TableProfile],
    model: Model,
    attempts: int = ATTEMPTS,
    constrained: bool = False,
) -> Critique:
    """What the rules refuse about a spec, and a spec that answers them.

    Nothing is asked where nothing is refused: a spec with no findings gets an empty
    critique and costs no request, because a model asked to improve a chart that is fine
    will improve it, and what comes back is somebody's taste with a bill attached.

    The loop is `ask`'s: the model answers, the rules judge, and a rejected answer goes back
    with what refused it. What judges here is the validator, then the query being the one it
    was given, then the same rule that produced the findings — a suggestion that trades one
    refusal for another is not a suggestion.
    """
    found = findings(spec, tables)
    if not found:
        return Critique()

    read = reads(spec, tables)
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        written = prompt(spec, read, found, errors, constrained=constrained)
        text = model.complete(written, SCHEMA if constrained else None).text
        try:
            suggested = json.loads(text)
        except json.JSONDecodeError as failure:
            errors = [f"the answer was not JSON: {failure}"]
            continue
        errors = refusals(spec, suggested, tables)
        if not errors:
            return Critique(
                findings=tuple(found), spec=suggested, errors=(), attempts=attempt, asked=True
            )
    return Critique(findings=tuple(found), spec=None, errors=tuple(errors), attempts=attempts, asked=True)


def prompt(
    spec: dict,
    tables: Sequence[TableProfile],
    found: Sequence[Finding],
    errors: Sequence[str] = (),
    constrained: bool = False,
) -> str:
    """The instructions, the tables the spec reads, the spec, and what the rule said.

    `tables` is what the model is to read, which is the profile of every table the query
    names and nothing else: a critique is about a spec that already chose its tables, so
    there is no selection to make and no schema to send. It reads profiles here for the
    same reason `ask` does — the shape of a column is what says whether a mark suits it —
    and it has no parameter a row could arrive through.

    `constrained` says the schema is going with the request as the response format, in which
    case it is left out of the text, exactly as a question's prompt leaves it out. There is
    no cacheable prefix to protect here: the spec is what changes between two of these, and
    it is most of what they carry.
    """
    parts = [INSTRUCTIONS]
    if not constrained:
        parts += [
            "The specification must validate against this JSON Schema:",
            json.dumps(SCHEMA, indent=None),
        ]
    parts += [
        "Tables the query reads:",
        "\n\n".join(block(table) for table in tables),
        "The specification:",
        json.dumps(spec, indent=2, sort_keys=True),
        "What the rule said about its chart:\n"
        + "\n".join(f"- {finding}" for finding in found),
    ]
    if errors:
        parts.append(
            "Your previous answer was rejected, which said:\n"
            + "\n".join(f"- {error}" for error in errors)
            + "\nAnswer again, corrected."
        )
    return "\n\n".join(parts)


def refusals(before: dict, after: object, tables: Sequence[TableProfile]) -> list[str]:
    """Why this suggestion is not one, or an empty list.

    Three rules, in the order of what they cost to check. It has to validate, like every
    other answer a model gives. It has to be the same question: everything outside `chart`
    comes back as it was, so the rows the person is looking at are the rows the suggestion
    draws, and a critique cannot quietly rewrite a filter. And it has to survive the rule
    that produced the finding, since a suggestion that swaps one refusal for another has
    answered nothing.
    """
    errors = validate_spec(after)
    if errors or not isinstance(after, dict):
        return errors

    changed = sorted(
        key
        for key in set(before) | set(after)
        if key != "chart" and before.get(key) != after.get(key)
    )
    if changed:
        return [
            "the query is the question, and a critique does not change it: "
            + ", ".join(changed)
            + " came back different"
        ]
    if after["chart"] == before["chart"]:
        return ["the chart came back unchanged, which is not a suggestion"]
    return [f"the same rule refuses the suggestion: {finding}" for finding in findings(after, tables)]


def reads(spec: dict, tables: Sequence[TableProfile]) -> tuple[TableProfile, ...]:
    """The profiles of the tables the query names, in the order the profiles arrived in."""
    named = _named(spec["query"])
    return tuple(table for table in tables if any(names_table(table.table, name) for name in named))


@dataclass(frozen=True)
class Slices:
    """How many slices an arc would draw at most, and what says so."""

    count: int
    because: str


def _slices(spec: dict, tables: Sequence[TableProfile]) -> Slices | None:
    """What bounds the rows this chart draws, or None where nothing here does.

    A grouped query returns one row per combination of its dimensions, so the profiles bound
    it: the product of the distinct counts, or the query's own limit where that is smaller.

    None in three cases. A query that selects rows rather than grouping them, where the
    profiles say nothing about how many come back. A dimension the profiles cannot resolve
    to a column. And a truncated date, whose buckets are fewer than the column's values by
    an amount nothing here knows — an arc of one is refused for its axis rather than for its
    count anyway, and the axis is the better sentence.

    The limit alone is not a bound worth acting on: a limit of 500 over a column with four
    values is four slices, and a finding made out of the 500 would name a problem that is
    not there.
    """
    query = spec["query"]
    dimensions = query.get("group_by", [])
    if not dimensions or not query.get("aggregates"):
        return None

    combinations = 1
    approximate = False
    counted = []
    for dimension in dimensions:
        column = None if "truncate" in dimension else _column(dimension["column"], query, tables)
        if column is None:
            return None
        combinations *= max(column.distinct_count, 1)
        approximate = approximate or not column.distinct_count_exact
        counted.append(f"{dimension['column']} has {column.distinct_count}")

    limit = query["limit"]
    count = min(limit, combinations)
    values = " and ".join(counted) + (" distinct values" if len(counted) == 1 else " combined")
    return Slices(
        count=count,
        because=(
            f"the query keeps at most {limit} rows and {values}"
            + (", approximately" if approximate else "")
        ),
    )


def _column(reference: str, query: dict, tables: Sequence[TableProfile]) -> ColumnProfile | None:
    """The profile of the column a reference points at, or None where the profiles do not
    hold one. A reference is qualified with any trailing part of a table's name, or with
    nothing at all on a query that reads one table, which is what the validator accepts and
    therefore what this has to resolve."""
    qualifier, _, name = reference.rpartition(".")
    named = [query["from"], *(join["table"] for join in query.get("joins", []))]
    if qualifier:
        matching = [table for table in named if names_table(table, qualifier)]
        if len(matching) != 1:
            return None
        named = matching
    else:
        named = named[:1]

    profile = next((table for table in tables if names_table(table.table, named[0])), None)
    if profile is None:
        return None
    return next((column for column in profile.columns if column.name == name), None)


def _named(query: dict) -> tuple[str, ...]:
    return (query["from"], *(join["table"] for join in query.get("joins", [])))

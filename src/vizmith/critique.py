"""What a spec gets wrong, and a corrected spec for each of it.

A spec that passes the validator can still be wrong about the result it produces: an arc
of forty slices, a limit with nothing ordering it, a total of identifiers. The validator
cannot say so, because none of those are illegal — they are legal specs that answer the
question badly, and what makes them visible is the profiles and the shape of the result
rather than the grammar.

**Where the line sits.** A critique here may only name a fault, and a fault is something
there is a rule for in this file. It may not suggest a different question, a nicer mark or
a better colour. That is the same argument the mark rule already makes: `indefensible`
refuses rather than ranks, because a layer that named the best mark would ship somebody's
taste as a rule and score it forever. A critique that ranked would be that preference with
a button on it. So the rules are written here, in code, deterministic and readable, and the
model is never asked what is wrong — only to write the repair for a fault this file found.
What it may say is bounded by this module rather than by a sentence in a prompt asking it
to be conservative.

**What the model is for.** Finding a fault is cheap and mechanical; fixing one is not. A
limit with no order needs a measure to order by, and which measure that is depends on what
the query aggregates; a crowded axis needs a `limit_by` naming the right dimension and the
right aggregate alias. Those are exactly the shapes the retry loop already teaches, so the
repair is asked for in the same words `ask` uses and judged by the same validator. A
suggestion that does not validate is not shown: the person is offered a spec that runs.

**What a suggestion may not do.** It may not read a table or a column the original did not.
That is enforced here rather than asked for, because "improve this" and "answer a different
question" are one prompt away from each other, and a suggestion that quietly joined another
table would be a new question wearing the old one's clothes. A repair narrows or re-shapes
what is already referenced; anything wider is refused with our own words and asked again.

**Rows.** The rules read the result set, because "forty slices" is a fact about a result and
not about a schema. The model does not: `revision` takes a spec, profiles and a fault
sentence, and has no parameter a row could arrive through, the same way `prompt` has none.
What crosses into the prompt is the fault's sentence, which carries a count of rows where
the rule is about a count. A count is not a row, and it is the same figure the interface
already puts under the chart.

Nothing here applies anything. A suggestion is a spec the person can take or leave, and
taking one is one control in the interface and one control back — a spec that changed itself
is the failure mode the whole design is built against.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field

from vizmith.ask import ATTEMPTS, INSTRUCTIONS, SCHEMA, block
from vizmith.model import Model
from vizmith.profiler import ColumnProfile, TableProfile
from vizmith.spec import names_table, referenced, validate_spec

# More slices than this and a reader is matching them to a legend rather than comparing
# them. The same figure as the colour order the renderer refuses to go past, and for the
# same reason: past it the chart is drawn and cannot be read.
ARC_SLICES = 8

# How many categories fit on an axis before the labels stop being readable. Generous on
# purpose: this is a suggestion, not a refusal, and a rule that fires on twelve bars would
# fire on most charts anybody draws.
READABLE = 40

# A share of nulls worth saying something about. Below this, grouping by the column puts a
# handful of rows under an empty label, which is a footnote rather than a fault.
NULLS = 0.1

# What a key column is called. `_id` is the suffix relationship inference already reads, and
# a column called `id` outright is the other half of the same convention. Nothing wider:
# "paid" ends in those two letters and is a measure.
KEYS = ("_id", "_key")

# How many faults are put to the model in one critique. Each is a billed request and each
# is a spec the person has to read, and a list of six suggestions is one nobody reads past
# the second. The rules are ordered, so what is dropped is the least serious.
SUGGESTIONS = 3

CORRECTION = """You are given a chart specification that is already valid, and one thing
it gets wrong about the result it produces. Answer with the corrected specification as
JSON and nothing else. No explanation, no code fence.

Correct what is named and nothing else. Keep the same tables, the same columns and the
same measures: a specification that reads something the original did not answers a
different question, and it will be rejected. What you may change is the chart, the
ordering, the limits and how the query is shaped around what it already reads."""


@dataclass(frozen=True)
class Fault:
    """One thing a spec gets wrong, in the words a person reads.

    `rule` is the name of the rule that found it, which is what a record is diffed on: a
    sentence carries a row count and moves between runs, and a rule name does not.
    """

    rule: str
    says: str

    def as_dict(self) -> dict:
        return {"rule": self.rule, "says": self.says}


@dataclass(frozen=True)
class Suggestion:
    """A fault, and the corrected spec that was written for it.

    `spec` is None where the model never produced one that validated, and `errors` is why,
    in the validator's own words. A suggestion with no spec is not offered to anybody: it
    is here so that a run record can say the critique was asked and came back empty rather
    than saying nothing at all.
    """

    fault: Fault
    spec: dict | None
    errors: list[str] = field(default_factory=list)
    attempts: int = 0

    @property
    def usable(self) -> bool:
        return self.spec is not None

    def as_dict(self) -> dict:
        return {
            **self.fault.as_dict(),
            "spec": self.spec,
            "errors": self.errors,
            "attempts": self.attempts,
        }


@dataclass(frozen=True)
class Critique:
    """Every fault found, and what came back for the ones that were asked about."""

    suggestions: tuple[Suggestion, ...] = ()
    unasked: tuple[Fault, ...] = ()

    @property
    def usable(self) -> tuple[Suggestion, ...]:
        return tuple(suggestion for suggestion in self.suggestions if suggestion.usable)

    def as_dict(self) -> dict:
        return {
            "suggestions": [suggestion.as_dict() for suggestion in self.usable],
            "faults": [
                fault.as_dict()
                for fault in [*(s.fault for s in self.suggestions), *self.unasked]
            ],
        }


def critique(
    spec: dict,
    rows: list[dict],
    tables: Sequence[TableProfile],
    model: Model,
    attempts: int = ATTEMPTS,
    constrained: bool = False,
    limit: int = SUGGESTIONS,
) -> Critique:
    """Every fault this spec has, with a corrected spec for the first few.

    The faults are found here and the repairs are asked for one at a time, because a
    person takes one suggestion or another and a single answer correcting three things at
    once cannot be half taken. That is a billed request per fault, which is what `limit`
    bounds.
    """
    found = faults(spec, rows, tables)
    asked = found[:limit]
    return Critique(
        suggestions=tuple(
            improve(spec, fault, tables, model, attempts=attempts, constrained=constrained)
            for fault in asked
        ),
        unasked=tuple(found[limit:]),
    )


def faults(spec: dict, rows: list[dict], tables: Sequence[TableProfile]) -> tuple[Fault, ...]:
    """What is wrong with this spec, in the order of how wrong it is.

    Every rule reads the spec, the profiles, or the shape of the result, and every one of
    them is refusable in the same terms a validator message is: it names something the spec
    gets wrong, not something another person would have done differently. A rule that
    cannot tell — a column it could not find a profile for, a figure it has no count for —
    stays quiet, because a suggestion made out of a guess is worse than no suggestion.
    """
    query = spec["query"]
    found = [
        _mark(spec, rows),
        _arbitrary(query, rows),
        _crowded(spec, rows),
        _identifiers(query, tables),
        _nulls(query, tables),
    ]
    return tuple(fault for fault in found if fault is not None)


def improve(
    spec: dict,
    fault: Fault,
    tables: Sequence[TableProfile],
    model: Model,
    attempts: int = ATTEMPTS,
    constrained: bool = False,
) -> Suggestion:
    """Ask for one fault to be corrected, until the answer validates or the attempts run out.

    The same loop `ask` runs, for the same reason: a rejected answer goes back with the
    words that rejected it, and nothing here repairs a spec by hand. The widening check is
    part of that loop rather than a filter after it, so a model that joined another table
    is told why and gets to answer again.
    """
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        written = revision(spec, fault, tables, errors, constrained=constrained)
        text = model.complete(written, SCHEMA if constrained else None).text
        try:
            suggested = json.loads(text)
        except json.JSONDecodeError as failure:
            errors = [f"the answer was not JSON: {failure}"]
            continue
        errors = validate_spec(suggested)
        if not errors:
            errors = widened(spec, suggested)
        if not errors:
            return Suggestion(fault=fault, spec=suggested, attempts=attempt)
    return Suggestion(fault=fault, spec=None, errors=errors, attempts=attempts)


def revision(
    spec: dict,
    fault: Fault,
    tables: Sequence[TableProfile],
    errors: Sequence[str] = (),
    constrained: bool = False,
) -> str:
    """The specification, what it gets wrong, and what the tables it reads look like.

    There is no parameter a result set could arrive through, which is the same rule
    `ask.prompt` keeps and for the same reason: what reaches a model is profiles and a
    spec. The fault's sentence is text this module wrote.

    Only the tables the spec already reads are sent. The rest of the schema is not a thing
    a correction may reach for — a suggestion that reads another table is refused below —
    so sending it would pay for tokens whose only use would be to break that rule.

    The order is the same as a question's: instructions, schema where the endpoint does not
    enforce one, tables, then what varies. Everything before the specification is identical
    between two critiques over one schema, which is the prefix a provider's cache can hit.
    """
    parts = [INSTRUCTIONS, CORRECTION]
    if not constrained:
        parts += [
            "The specification must validate against this JSON Schema:",
            json.dumps(SCHEMA, indent=None),
        ]
    read = _reading(spec, tables)
    if read:
        parts += ["Tables:", "\n\n".join(block(table) for table in read)]
    parts += [
        "Specification:",
        json.dumps(spec, indent=2),
        f"What it gets wrong: {fault.says}",
    ]
    if errors:
        parts.append(
            "Your previous answer was rejected, because:\n"
            + "\n".join(f"- {error}" for error in errors)
            + "\nCorrect the same specification again."
        )
    return "\n\n".join(parts)


def widened(spec: dict, suggested: dict) -> list[str]:
    """Whether the suggestion reads something the original did not.

    A repair re-shapes what the spec already reads. Reaching for another table or another
    column is answering a different question, and a person who pressed a button called
    "use this one" did not ask a second question. Refused in the shape every other refusal
    arrives in, because it goes back to the model on the next attempt exactly as the
    validator's words do.
    """
    tables, columns = referenced(spec["query"])
    reads, uses = referenced(suggested["query"])
    lines = []
    for name in sorted(reads - tables):
        lines.append(
            f"query: the suggestion reads '{name}', which the specification does not. A "
            "correction may only re-shape what the specification already reads"
        )
    for name in sorted(uses - columns):
        lines.append(
            f"query: the suggestion names the column '{name}', which the specification does "
            "not. A correction may only re-shape what the specification already reads"
        )
    return lines


def indefensible(spec: dict, rows: list[dict]) -> str:
    """Why this mark does not suit the shape of this result, or an empty string.

    A rule rather than a list per question, because "defensible" is a property of the
    result and there is one implementation of every rule in this project. It refuses
    rather than ranks: several marks suit most shapes and picking a favourite among them
    would score a preference.
    """
    chart = spec["chart"]
    mark = chart["mark"]
    encoding = chart["encoding"]
    x = encoding.get("x")

    if x is None:
        # A figure. `mark` says nothing on one, the same way `stack` says nothing on an arc.
        return ""
    if "color" in encoding and mark == "arc":
        return "an arc has one ring of slices and cannot carry a colour channel as a second series"
    if mark == "arc" and len(rows) > ARC_SLICES:
        return f"an arc of {len(rows)} slices cannot be read; {ARC_SLICES} is the most that can"

    kind = x["type"]
    if kind == "temporal" and mark == "arc":
        return "an arc has no order, and a time axis is nothing but order"
    if kind == "quantitative" and mark in ("bar", "arc"):
        return f"a {mark} treats a quantitative axis as categories, and this one is a measure"
    if kind in ("nominal", "ordinal") and mark in ("line", "area"):
        return f"a {mark} draws a category axis as if the gaps between its values meant something"
    return ""


def _mark(spec: dict, rows: list[dict]) -> Fault | None:
    refused = indefensible(spec, rows)
    return Fault("mark", refused) if refused else None


def _arbitrary(query: dict, rows: list[dict]) -> Fault | None:
    """A limit that cut the result, with nothing saying which rows to keep.

    The rows that came back are then whatever the source happened to produce first, which
    is a chart that looks like a top ten and is not one. It only fires where the limit was
    actually reached: a limit of a thousand on nine rows cut nothing.
    """
    limit = query.get("limit")
    if limit is None or len(rows) < limit:
        return None
    if query.get("order_by") or query.get("limit_by"):
        return None
    return Fault(
        "arbitrary",
        f"the query returned {len(rows)} rows, which is its limit, and nothing orders them, "
        "so which rows these are is the source's choice rather than the question's",
    )


def _crowded(spec: dict, rows: list[dict]) -> Fault | None:
    """More categories on the axis than anybody can read them off."""
    x = spec["chart"]["encoding"].get("x")
    if x is None or x["type"] not in ("nominal", "ordinal") or len(rows) <= READABLE:
        return None
    return Fault(
        "crowded",
        f"the chart draws {len(rows)} categories along '{x['field']}', which is more labels "
        f"than an axis can be read at; past about {READABLE} the chart is drawn and cannot "
        "be read",
    )


def _identifiers(query: dict, tables: Sequence[TableProfile]) -> Fault | None:
    """A total or an average of a key column.

    Adding identifiers together produces a number that has a value and no meaning, and it
    is one of the few mistakes that never announces itself: the chart draws, the axis has
    figures on it and every one of them is nonsense.
    """
    for aggregate in query.get("aggregates", []):
        column = aggregate.get("column")
        if column is None or aggregate["fn"] not in ("sum", "avg"):
            continue
        name = column.rsplit(".", 1)[-1].lower()
        if not name.endswith(KEYS) and name != "id":
            continue
        profile = _profile(column, query, tables)
        if profile is not None and profile.type not in ("bigint", "int", "long", "integer"):
            continue
        return Fault(
            "identifiers",
            f"'{aggregate['fn']}' over '{column}' adds identifiers together, which produces a "
            "number with no meaning. Counting them is what a key column can answer",
        )
    return None


def _nulls(query: dict, tables: Sequence[TableProfile]) -> Fault | None:
    """A dimension a large share of the rows have no value for.

    Those rows are still in the result, under an empty label, and a reader takes that
    category for a real one. Saying so is the fault; what to do about it — filter them out
    or leave them and say so — is the person's, and the suggestion is one of the two.
    """
    for item in query.get("group_by", []):
        profile = _profile(item["column"], query, tables)
        if profile is None or profile.null_rate < NULLS:
            continue
        return Fault(
            "nulls",
            f"'{item['column']}' is {profile.null_rate:.0%} null, and grouping by it draws "
            "those rows as a category of their own with no name on it",
        )
    return None


def _reading(spec: dict, tables: Sequence[TableProfile]) -> list[TableProfile]:
    """The profiles of the tables this spec reads, in the schema's own order."""
    named = _tables(spec["query"])
    return [table for table in tables if any(names_table(table.table, one) for one in named)]


def _tables(query: dict) -> set[str]:
    return {query["from"]} | {join["table"] for join in query.get("joins", [])}


def _profile(
    reference: str, query: dict, tables: Sequence[TableProfile]
) -> ColumnProfile | None:
    """The profile of one column a spec names, or None where nothing here can say.

    A reference is qualified with any trailing part of a table name, or with nothing at all
    where the query reads one table, so the qualifier is matched the way the validator
    matches it rather than by string equality.
    """
    qualifier, _, column = reference.rpartition(".")
    named = _tables(query)
    if qualifier:
        named = {table for table in named if names_table(table, qualifier)}
    elif len(named) > 1:
        return None
    for table in tables:
        if not any(names_table(table.table, one) for one in named):
            continue
        found = next((held for held in table.columns if held.name == column), None)
        if found is not None:
            return found
    return None

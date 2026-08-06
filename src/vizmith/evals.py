"""Score a fixed question set, so a prompt change is measurable rather than anecdotal.

A question goes through the same path a person's question goes through: `ask` writes the
prompt, the model answers, the validator judges. What this adds is the scoring, in four
layers, cheapest first, and a run record that can be diffed against the last one.

The layers are ordered by what they cost. Layer 1 costs nothing beyond the answer that was
already paid for. Layer 2 reads the spec. Layer 3 runs two queries against the source, the
model's and the reference's, which is the first layer that spends anything on a warehouse.
Layer 4 reads the rows layer 3 already fetched. A question that fails a layer stops there,
because the layers below it answer a question that no longer means anything: whether the
right rows came back from a spec that names the wrong table is not information.

Nothing here reaches a model except through `ask`, and nothing here shows the model a row.
The result sets it compares are read after the answer is written and never go back.
"""

import dataclasses
import datetime as dt
import hashlib
import json
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from vizmith import query
from vizmith.ask import ATTEMPTS, ask, prompt
from vizmith.catalog import Catalog
from vizmith.model import Model, ModelError
from vizmith.profiler import TableProfile
from vizmith.relevance import select
from vizmith.spec import output_columns

VALIDATES = "validates"
REFERENCES = "references"
RESULT = "result"
MARK = "mark"

# In the order they run, which is the order of what they cost.
LAYERS = (VALIDATES, REFERENCES, RESULT, MARK)

# The shape of the cache file. A file written under another one is dropped rather than
# migrated: what it holds is answers that can be bought again.
CACHE_VERSION = 1

# More slices than this and a reader is matching them to a legend rather than comparing
# them. The same figure as the colour order the renderer refuses to go past, and for the
# same reason: past it the chart is drawn and cannot be read.
ARC_SLICES = 8


@dataclass(frozen=True)
class Question:
    """One entry of the question set: what to ask, and what a correct answer looks like.

    `tables` and `columns` are what the answer has to reference, in the source's own short
    names. `reference` is a spec that answers the question correctly, and running it is
    what produces the expected result set, so nothing here stores rows.
    """

    name: str
    question: str
    tables: tuple[str, ...]
    columns: tuple[str, ...]
    reference: dict
    notes: str = ""


@dataclass(frozen=True)
class Score:
    """What one question scored, and where it stopped.

    `passed` holds the layers that ran and passed, in order. `failed` is the layer that
    stopped it, or None where every layer passed. `reason` is in the words of whatever
    refused, because a score of three out of four says nothing a prompt can be changed on.
    """

    name: str
    passed: tuple[str, ...]
    failed: str | None
    reason: str = ""
    attempts: int = 0
    asked: bool = True
    note: str = ""

    @property
    def complete(self) -> bool:
        return self.failed is None

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class Run:
    """One run of the harness: what produced it, and what it scored.

    `model` and `endpoint` are on it because a score without them is not comparable to
    anything, and `at` is the only field that moves on its own, which is what keeps two
    runs of the same question set diffable.
    """

    at: str
    model: str
    endpoint: str
    scores: tuple[Score, ...] = ()

    @property
    def totals(self) -> dict[str, int]:
        """How many questions reached each layer's pass, plus the two headline figures.
        Counted rather than averaged: a mean over four layers of different worth reads
        like a grade and hides which layer moved."""
        totals = {layer: sum(layer in score.passed for score in self.scores) for layer in LAYERS}
        return {
            "questions": len(self.scores),
            **totals,
            "complete": sum(score.complete for score in self.scores),
            "asked": sum(score.asked for score in self.scores),
        }

    def as_dict(self) -> dict:
        return {
            "at": self.at,
            "model": self.model,
            "endpoint": self.endpoint,
            "totals": self.totals,
            "scores": [score.as_dict() for score in sorted(self.scores, key=lambda s: s.name)],
        }


class Cache:
    """Answers kept against the prompt that produced them.

    Keyed on the prompt rather than on the question, because the prompt is what this
    harness exists to measure: an instruction that changed has to be paid for again, and a
    cache that answered from the old wording would report the new one's score without ever
    having asked it. The model and the endpoint are in the key for the same reason.

    Only the first prompt of a question is the key. The retry loop's later prompts carry
    the validator's words about an answer that is not in the cache, so keying on them would
    be keying on something the next run cannot reproduce.

    An answer that never validated is stored like any other. A model is not deterministic,
    so asking again could produce a spec where the last attempt produced none, and a cache
    that quietly re-rolled the failures would report a score that moved on its own. Asking
    again is what `--no-cache` is for, and it is a decision rather than a side effect.
    """

    def __init__(self, path: Path):
        self._path = path
        self._answers: dict[str, dict] = {}
        try:
            written = json.loads(path.read_text())
        except (OSError, ValueError):
            return
        if written.get("version") == CACHE_VERSION:
            self._answers = written.get("answers", {})

    @staticmethod
    def key(written: str, model: str, endpoint: str) -> str:
        return hashlib.sha256(f"{model}\n{endpoint}\n{written}".encode()).hexdigest()

    def read(self, key: str) -> dict | None:
        return self._answers.get(key)

    def write(self, key: str, answer: dict) -> None:
        self._answers[key] = answer
        written = json.dumps(
            {"version": CACHE_VERSION, "answers": self._answers}, indent=2, sort_keys=True
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Written beside the file and moved onto it, so a run interrupted halfway leaves the
        # last whole file rather than the first half of the next one.
        handle, beside = tempfile.mkstemp(dir=self._path.parent, prefix=self._path.name + ".")
        with os.fdopen(handle, "w") as writing:
            writing.write(written)
        os.replace(beside, self._path)


def questions(path: Path) -> tuple[Question, ...]:
    """The question set, with every reference spec loaded from beside it.

    A reference is a path relative to the question set, which is what keeps the expected
    answer a spec in the fixtures rather than a copy of one inside this file.
    """
    entries = json.loads(Path(path).read_text())["questions"]
    return tuple(
        Question(
            name=entry["name"],
            question=entry["question"],
            tables=tuple(entry["tables"]),
            columns=tuple(entry["columns"]),
            reference=json.loads((Path(path).parent / entry["reference"]).read_text()),
            notes=entry.get("notes", ""),
        )
        for entry in entries
    )


def run(
    asked: Sequence[Question],
    tables: Sequence[TableProfile],
    model: Model,
    catalog: Catalog,
    cache: Cache | None = None,
    only: Sequence[str] = (),
    constrained: bool = False,
    attempts: int = ATTEMPTS,
) -> Run:
    """Every question, or the named subset, scored.

    `only` is names rather than an index, because a run costs money per question and the
    thing a person re-runs is the question that failed, which they know by name. A name the
    set does not hold raises rather than scoring nothing, since a typo that quietly runs an
    empty harness reports a perfect run.
    """
    chosen = _chosen(asked, only)
    scores = [_score(question, tables, model, catalog, cache, constrained, attempts) for question in chosen]
    name, endpoint = model.described
    return Run(at=_now(), model=name, endpoint=endpoint, scores=tuple(scores))


def write(record: Run, directory: Path) -> Path:
    """The run, in a file named for when it ran. Sorted keys and one question per entry,
    because the value of a run record is the diff against the last one."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{record.at.replace(':', '').replace('-', '')}.json"
    path.write_text(json.dumps(record.as_dict(), indent=2, sort_keys=True) + "\n")
    return path


def _chosen(asked: Sequence[Question], only: Sequence[str]) -> list[Question]:
    if not only:
        return list(asked)
    known = {question.name for question in asked}
    missing = [name for name in only if name not in known]
    if missing:
        raise ValueError("no question named " + ", ".join(sorted(missing)))
    return [question for question in asked if question.name in set(only)]


def _score(
    question: Question,
    tables: Sequence[TableProfile],
    model: Model,
    catalog: Catalog,
    cache: Cache | None,
    constrained: bool,
    attempts: int,
) -> Score:
    # Keyed on the prompt that is actually sent. That depends on whether the endpoint takes
    # the schema as a response format, and on which tables the question selected: an answer
    # to a prompt carrying six tables is not an answer to one carrying three.
    relationships = catalog.relationships()
    chosen = select(question.question, tables, relationships)
    written = prompt(
        question.question,
        chosen.tables,
        constrained=constrained,
        withheld=chosen.withheld,
    )
    name, endpoint = model.described
    key = Cache.key(written, name, endpoint)
    stored = cache.read(key) if cache else None

    if stored is not None:
        spec, errors, taken, asked = stored["spec"], stored["errors"], stored["attempts"], False
    else:
        asked = True
        try:
            answer = ask(
                question.question,
                tables,
                model,
                attempts=attempts,
                constrained=constrained,
                relationships=relationships,
            )
        except ModelError as failure:
            return Score(question.name, (), VALIDATES, str(failure), attempts=0, asked=True)
        spec, errors, taken = answer.spec, answer.errors, answer.attempts
        if cache:
            cache.write(key, {"spec": spec, "errors": errors, "attempts": taken})

    if spec is None:
        return Score(question.name, (), VALIDATES, "; ".join(errors), attempts=taken, asked=asked)

    passed = [VALIDATES]

    missing, extra = _difference(question, spec)
    if missing:
        return Score(question.name, tuple(passed), REFERENCES, missing, attempts=taken, asked=asked)
    passed.append(REFERENCES)

    try:
        rows = query.execute(spec, catalog)
        expected = query.execute(question.reference, catalog)
    except (ValueError, RuntimeError) as failure:
        return Score(question.name, tuple(passed), RESULT, str(failure), attempts=taken, asked=asked, note=extra)
    if _comparable(rows, spec) != _comparable(expected, question.reference):
        reason = f"{len(rows)} rows, expected {len(expected)}"
        if len(rows) == len(expected):
            reason = f"{len(rows)} rows that are not the expected ones"
        return Score(question.name, tuple(passed), RESULT, reason, attempts=taken, asked=asked, note=extra)
    passed.append(RESULT)

    refused = indefensible(spec, rows)
    if refused:
        return Score(question.name, tuple(passed), MARK, refused, attempts=taken, asked=asked, note=extra)
    passed.append(MARK)

    return Score(question.name, tuple(passed), None, attempts=taken, asked=asked, note=extra)


def _difference(question: Question, spec: dict) -> tuple[str, str]:
    """What the answer failed to reference, and what it referenced beyond what was asked
    for. Only the first is a failure: a spec that joins a table it did not need produces
    the wrong rows or the right ones, and layer 3 is what says which. Recording the extras
    keeps that visible in the record rather than invisible in the score."""
    tables, columns = _referenced(spec)
    missing = [name for name in question.tables if name not in tables]
    missing += [name for name in question.columns if _normalised(name, "") not in columns]
    extra = sorted(tables - set(question.tables))
    return (
        ", ".join(f"does not reference {name}" for name in missing),
        ("also reads " + ", ".join(extra)) if extra else "",
    )


def _referenced(spec: dict) -> tuple[set[str], set[str]]:
    """Every table and column the query names, in short names.

    Aliases are not columns and are left out: `order_by` and `limit_by` name output
    columns, which the spec itself invented, so counting them would score the answer
    against its own vocabulary.
    """
    asked = spec["query"]
    default = _short(asked["from"])
    tables = {default} | {_short(join["table"]) for join in asked.get("joins", [])}

    columns = {
        _normalised(item["column"], default)
        for item in [*asked.get("select", []), *asked.get("group_by", []), *asked.get("filters", [])]
    }
    columns |= {
        _normalised(aggregate["column"], default)
        for aggregate in asked.get("aggregates", [])
        if "column" in aggregate
    }
    for join in asked.get("joins", []):
        for pair in join["on"]:
            columns |= {_normalised(pair["left"], default), _normalised(pair["right"], default)}
    return tables, columns


def _short(reference: str) -> str:
    """A table under the name a person uses. A spec may name a table with one segment or
    with three, and a model reading a profile writes the qualified one, so the last
    segment is the only name the two share."""
    return reference.rsplit(".", 1)[-1].lower()


def _normalised(reference: str, default: str) -> str:
    """A column as table.column, whatever the spec qualified it with."""
    qualifier, _, column = reference.rpartition(".")
    return f"{_short(qualifier) if qualifier else default}.{column}".lower()


def _comparable(rows: list[dict], spec: dict) -> list[tuple[str, ...]]:
    """A result set in a shape two of them can be compared in.

    Values as text and rows sorted, so an answer that named its columns differently or
    ordered them differently still matches: an alias is the model's to choose and the
    order of the rows is the chart's business. The output columns keep their order,
    because that order is the result set contract and a chart reads it by position.
    """
    names = output_columns(spec["query"])
    return sorted(tuple("" if row[name] is None else str(row[name]) for name in names) for row in rows)


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


def _now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

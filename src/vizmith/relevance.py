"""Which tables a question is about, and how many of them fit.

A prompt used to carry every table in the schema. That is fine on eight tables and is not
what a lakehouse schema is: measured by building the prompt at several sizes, a hundred
tables of twenty-five columns is about 65,000 tokens per attempt and three times that for a
question that takes three tries. The bill grows with the source, the wait before the first
token grows with it, and past some size the request stops fitting in a context window at
all — which arrives as whatever the endpoint says about an oversized request rather than as
a sentence anybody can act on. It also spends the budget on the wrong thing: a question
about revenue per country needs `orders` and `customers`, and the other ninety-eight tables
are tokens spent making the answer harder to find.

So the model sees the tables the question is about. Two rules decide that, and they are
deliberately the cheap ones:

**Words.** A table is scored on how much of the question its own name and its columns'
names account for. Deterministic, explainable, free, and it asks the model nothing. What it
cannot do is bridge a vocabulary gap — a question about "turnover" scores nothing against a
table called `orders`, and a first call asking the model which tables it needs is the thing
that would. That is a round trip per question and is left as the next step if this is not
enough; the eval harness is what says whether it is.

**Reach.** A join is only legal through a confirmed relationship, so a question that needs
two tables often needs a third to get between them: `order_items` sits between `orders` and
`products` and shares a word with neither. Anything one hop from a chosen table is a
candidate, ranked below the tables that were chosen outright.

**A budget, whatever the words say.** The chosen tables are added in rank order until the
next one would not fit. That is the guarantee the failure at the far end of the table above
needs: a prompt is bounded by a number in this file rather than by the size of somebody's
schema. At least one table is always sent, since a prompt with no tables in it cannot be
answered at all.

What the prompt loses is a prefix that is identical between questions. Instructions and
schema still are, but the table blocks now depend on the question, so an endpoint that
caches prompt prefixes hits a shorter one. That is a real cost of selecting, and it is
smaller than sending a schema nobody asked about.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from vizmith.catalog import Relationship
from vizmith.profiler import TableProfile

# What a table block may cost, in characters of prompt. Four characters to a token is the
# usual rule of thumb, so this is around 6,000 tokens of tables however large the schema is,
# on top of instructions that are about 350. A question that needs more tables than fit is
# a question this cannot answer, and answering it badly out of a truncated schema is worse
# than answering it out of the tables it ranked highest.
BUDGET = 24_000

# How many tables are sent when the question's words match nothing. A schema whose names
# share no vocabulary with the question is exactly where a model needs something to read,
# and the largest tables are the likeliest subjects.
FLOOR = 4

# Words that match everything and therefore mean nothing here. A column called `total` is
# on half a schema, and every question has "the" in it.
STOP = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "count", "each", "for", "from",
        "group", "have", "how", "in", "into", "is", "it", "many", "most", "of", "on", "or",
        "per", "show", "sum", "table", "the", "their", "there", "these", "this", "to", "top",
        "total", "value", "values", "what", "when", "where", "which", "who", "with",
    }
)


@dataclass(frozen=True)
class Selection:
    """The tables that go in the prompt, and what was left out of it."""

    tables: tuple[TableProfile, ...]
    withheld: int

    @property
    def whole_schema(self) -> bool:
        return self.withheld == 0


def select(
    question: str,
    tables: Sequence[TableProfile],
    relationships: Sequence[Relationship] = (),
    budget: int = BUDGET,
    floor: int = FLOOR,
) -> Selection:
    """The tables to put in front of the model for this question.

    Ordered by how much of the question each accounts for, then by row count, so a tie is
    broken by the table more of the data is in rather than by whatever order the schema
    listed. Nothing is truncated: a table is in the prompt whole or not at all, because
    half a column list is a column list a model will read as complete."""
    if not tables:
        return Selection(tables=(), withheld=0)

    words = _words(question)
    scored = {table.table: _score(table, words) for table in tables}
    reachable = _one_hop({name for name, score in scored.items() if score > 0}, relationships)

    def rank(table: TableProfile) -> tuple[int, int, int]:
        # Chosen outright, then reachable from something chosen, then the rest. Row count
        # breaks a tie, and the name keeps it stable where even that ties.
        return (scored[table.table], 1 if table.table in reachable else 0, table.row_count)

    ordered = sorted(tables, key=rank, reverse=True)
    wanted = [table for table in ordered if scored[table.table] > 0 or table.table in reachable]
    if len(wanted) < floor:
        wanted = ordered[:floor]

    kept: list[TableProfile] = []
    spent = 0
    for table in wanted:
        cost = _cost(table)
        if kept and spent + cost > budget:
            continue
        kept.append(table)
        spent += cost

    # In the schema's own order, not the ranking's: the ranking is this file's reasoning and
    # a prompt that reordered the schema per question would make two prompts differ by more
    # than the tables they hold.
    chosen = tuple(table for table in tables if table in kept)
    return Selection(tables=chosen, withheld=len(tables) - len(chosen))


def _words(question: str) -> set[str]:
    """The question as the words a name could match. Split on anything that is not a letter
    or a digit, so `revenue_by_country` and "revenue by country" produce the same set, and a
    trailing s is dropped so that `orders` matches "order"."""
    found = {word for word in re.split(r"[^a-z0-9]+", question.lower()) if len(word) > 2}
    return {_stem(word) for word in found - STOP}


def _stem(word: str) -> str:
    return word[:-1] if word.endswith("s") and len(word) > 3 else word


def _score(table: TableProfile, words: set[str]) -> int:
    """How much of the question this table accounts for. Its own name counts for more than
    a column's, because a question that names a table is a question about that table, and a
    column called `status` is on half the tables in a schema."""
    name = {_stem(part) for part in re.split(r"[^a-z0-9]+", table.table.lower()) if part}
    columns = {
        _stem(part)
        for column in table.columns
        for part in re.split(r"[^a-z0-9]+", column.name.lower())
        if len(part) > 2
    }
    return 3 * len(name & words) + len(columns & words)


def _one_hop(chosen: set[str], relationships: Iterable[Relationship]) -> set[str]:
    """Everything a chosen table can be joined to directly. A spec may only join through
    what the graph confirms, so the tables that make a join possible have to be readable
    even where the question never says their name."""
    reached: set[str] = set()
    for relationship in relationships:
        if relationship.left_table in chosen:
            reached.add(relationship.right_table)
        if relationship.right_table in chosen:
            reached.add(relationship.left_table)
    return reached - chosen


def _cost(table: TableProfile) -> int:
    """What this table is worth in the prompt, near enough. The block `ask` writes is a
    line per column plus a heading, and what varies between columns is small beside the
    count of them, so a per column estimate is what the budget is spent in."""
    return 60 + sum(60 + len(column.name) + len(str(column.samples)) for column in table.columns)


def named(question: str, tables: Sequence[TableProfile]) -> tuple[str, ...]:
    """The tables the question names outright, for a caller that wants to check its own
    work. `select` keeps these by construction: a name match scores three."""
    words = _words(question)
    return tuple(
        table.table
        for table in tables
        if {_stem(part) for part in re.split(r"[^a-z0-9]+", table.table.lower()) if part} & words
    )


def sizes(tables: Sequence[TableProfile]) -> Mapping[str, int]:
    """What each table costs the prompt, which is what a person reading a bill wants."""
    return {table.table: _cost(table) for table in tables}

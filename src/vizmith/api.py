"""The HTTP surface: a spec goes in, its errors or its rows come back.

What a client can also ask for is metadata: the tables in the configured schema and the
profile of one of them, which is the figures the model was given and never a row out of a
table. The profiler's sample threshold is the boundary that keeps that true, and nothing
here widens it.

A request carries a spec and nothing else. The data source is server configuration, so a
client cannot name a database. The artefact a client holds is the spec, which is the point
of the whole design. A source's own error message is passed on even where it quotes the
statement that failed, because the person reading it asked for that query and withholding
the only clue protects nothing.

Two things here write, and both write specs or answers about them rather than configuration:
a person's answer about a suggested relationship, and a dashboard, which is several specs
saved under a name. Neither can point the server at data, which is the sentence the read
only surface existed to keep.

Validator messages are returned word for word. They are written to be fed back to a model
on retry, so rewording them here would break that loop before it is written.
"""

import os
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from vizmith import __version__, query
from vizmith.ask import SCHEMA, ask
from vizmith.catalog import DECLARED, Catalog, DatabricksCatalog, Relationship
from vizmith.config import state_dir
from vizmith.dashboards import Dashboards, Refused
from vizmith.model import Endpoint, Model, ModelError
from vizmith.profiler import Profiles, TableProfile
from vizmith.relationships import Confirmations, graph, resolve, suggest
from vizmith.spec import validate_spec
from vizmith.state import Damaged


def _web() -> Path:
    """The built interface. Inside a wheel it sits in the package, put there at build time;
    in a checkout it is `web/dist`, where `npm run build` leaves it. The packaged copy wins,
    because an installed Vizmith run from a checkout's directory should serve what it
    shipped rather than whatever happens to be built there."""
    packaged = Path(__file__).resolve().parent / "web"
    return packaged if packaged.is_dir() else Path(__file__).resolve().parents[2] / "web" / "dist"


WEB_DIST = _web()

CONFIGURATION = (
    "VIZMITH_DATABRICKS_PROFILE",
    "VIZMITH_DATABRICKS_CATALOG",
    "VIZMITH_DATABRICKS_SCHEMA",
    "VIZMITH_DATABRICKS_WAREHOUSE",
)

MODEL_CONFIGURATION = (
    "VIZMITH_MODEL_BASE_URL",
    "VIZMITH_MODEL_NAME",
    "VIZMITH_MODEL_KEY",
)

app = FastAPI(title="Vizmith")


@app.exception_handler(Damaged)
def damaged(request: Request, failure: Damaged) -> JSONResponse:
    """A state file the server cannot read, in the shape every other refusal arrives in.

    It is registered here rather than caught in each endpoint because the stores are built
    as dependencies, so the failure happens before any endpoint body runs. 503 rather than
    500: the request was well formed, nothing is wrong with what was asked, and the endpoint
    can answer again as soon as the file named in the message is moved aside."""
    return JSONResponse(status_code=503, content={"errors": [str(failure)]})


@lru_cache
def source() -> Catalog:
    """The configured source, built once and on first use rather than at import, so that
    health answers on a server that has none and a test can put its own in its place."""
    profile, catalog, schema, warehouse = (os.environ[name] for name in CONFIGURATION)
    return DatabricksCatalog(profile=profile, catalog=catalog, schema=schema, warehouse=warehouse)


@lru_cache
def model() -> Model:
    base_url, name, key = (os.environ[variable] for variable in MODEL_CONFIGURATION)
    return Model(Endpoint(base_url=base_url, model=name, api_key=key))


@lru_cache(maxsize=1)
def constrains(writer: Model) -> bool:
    """Whether this endpoint honours the schema a question will be asked with, asked once
    and kept for the life of the process. The probe carries that schema rather than a
    simpler one, because an endpoint can take a simple schema and refuse this one. The
    probe is a billed request, so asking per question would pay for the same answer every
    time. A probe that never got an answer raises rather than reporting no, and nothing is
    remembered, so the next question asks again."""
    return writer.constrains_output(SCHEMA)


# Profiling one table is two statements that mostly wait, so the schema is profiled several
# at a time. Wide enough that the waiting overlaps, narrow enough that the warehouse is not
# handed more concurrent statements than a small one will run, which only moves the queue.
PROFILE_WORKERS = 8


def profiles(catalog: Catalog) -> tuple[TableProfile, ...]:
    """Every table in the configured schema, read through the profile cache.

    The cache is what keeps this affordable: profiling a table is two passes over it, and
    a table that has not changed since the last one is answered out of the file instead.
    What is paid on every call is one metadata read per table asking the source when the
    table last changed, which is what lets a schema that moved under a running server be
    noticed without restarting it.

    `tables` runs first and on this thread, which is also what builds the source's client
    before anything shares it."""
    names = catalog.tables()
    kept = Profiles(state_dir() / "profiles.json")
    with ThreadPoolExecutor(max_workers=PROFILE_WORKERS) as pool:
        return tuple(pool.map(lambda name: kept.read(catalog, name), names))


def profile(catalog: Catalog, name: str) -> TableProfile:
    """One table, through the same cache the whole schema is read through, which is what
    makes reading one table on its own honest: a profile is a profile whether it was asked
    for by name or as part of a schema, and both find the same stored one. Reading the
    whole schema to answer for one table would pay for fifteen freshness checks to serve
    one, which is what a panel drawing a tree of tables does per table."""
    return Profiles(state_dir() / "profiles.json").read(catalog, name)


def saved() -> Dashboards:
    """The dashboards a person saved, read from disk on every request for the same reason
    the relationship answers are: the file is small, and a copy held between requests is
    one more thing that can answer with what was true a moment ago."""
    return Dashboards(state_dir() / "dashboards.json")


def answers() -> Confirmations:
    """What a person has said about the suggested relationships, read from disk on every
    request rather than cached, because the file is small and a cache would be one more
    thing that can hold a stale answer."""
    return Confirmations(state_dir() / "relationships.json")


def relationship_graph(catalog: Catalog) -> list[Relationship]:
    """Everything known about how the tables relate: what the source declares, plus what
    the profiles suggest. The suggestions are inferred from the profiles rather than from
    a second read of the schema, so the columns this reasons about are the ones the panel
    shows and the model was given."""
    columns = {
        table.table: {column.name: column.type for column in table.columns}
        for table in profiles(catalog)
    }
    return graph(catalog.relationships(), suggest(columns))


class SpecRequest(BaseModel):
    # Deliberately untyped: the validator answers for every shape a spec can arrive in,
    # including the ones that are not objects at all, and a model that rejected those
    # first would replace its message with one of pydantic's.
    spec: object


class QuestionRequest(BaseModel):
    question: str


class DashboardRequest(BaseModel):
    """The tiles of one dashboard. The name is in the path, because it is what addresses
    the dashboard, and a name in the body as well would be two of them to disagree.

    The tiles are untyped for the same reason a spec is: the validator answers for every
    shape a spec can arrive in, and a model that rejected the wrong ones first would
    replace its message with one of pydantic's."""

    tiles: list[object]


class AnswerRequest(BaseModel):
    """A person's answer about one suggested relationship. It names the relationship by
    the columns it joins rather than by an index into a list, because the list is derived
    and would renumber under a re-profile."""

    left_table: str
    left_column: str
    right_table: str
    right_column: str
    answer: str


@app.get("/api/health")
def health() -> dict[str, str | bool]:
    """Answerable without a source, because it is what a deployment check runs. It says
    what is configured so that the interface can name the missing piece rather than
    letting it arrive as a failed query."""
    return {
        "status": "ok",
        "version": __version__,
        "source": all(os.environ.get(name) for name in CONFIGURATION),
        "model": all(os.environ.get(name) for name in MODEL_CONFIGURATION),
    }


@app.get("/api/tables")
def tables(catalog: Annotated[Catalog, Depends(source)]):
    """The qualified name of every table in the configured schema.

    The names come out of the profiles rather than out of a second listing, so a name here
    is one the endpoint below can answer for. Profiling a schema nobody has profiled is the
    wait the first question pays, and the two read the same cache, so it is paid once
    however it is reached and not again until a table changes."""
    try:
        return {"tables": [table.table for table in profiles(catalog)]}
    except RuntimeError as failure:
        return refused("source", failure)


@app.get("/api/tables/{name}")
def table(name: str, catalog: Annotated[Catalog, Depends(source)]):
    """One table's profile: the figures the prompt path was given for that table.

    Read from the same profiles the model sees rather than profiled a second time here. A
    second path could produce figures that disagree with the prompt's, and a panel whose
    claim is that this is what the model saw is worse than no panel when it is wrong.

    `as_dict` is the serialisation, because it already turns every figure into text, so a
    date, a decimal and a string survive the same JSON round trip. A column above the
    profiler's sample threshold carries no sample values, here as everywhere: this answers
    with a profile and never with a row.

    A name the schema does not hold is refused at 404 naming it, rather than raising. The
    name is checked against the source's own listing rather than against the profiles,
    because profiling a schema to find out that one of its tables is missing is a bill for
    an answer the listing already had. The panel takes its names from the list above, so a
    miss means the table went away between the two requests."""
    try:
        if name not in catalog.tables():
            return JSONResponse(
                status_code=404,
                content={"errors": [f"no table named {name} in the configured schema"]},
            )
        return profile(catalog, name).as_dict()
    except RuntimeError as failure:
        return refused("source", failure)


@app.get("/api/relationships")
def relationships(
    catalog: Annotated[Catalog, Depends(source)],
    confirmations: Annotated[Confirmations, Depends(answers)],
):
    """What the source declares and what the profiles suggest, each with what a person
    has said about it. A suggestion they turned down is not in the list, because a
    rejected suggestion that came back would be the same question asked every time.

    Nothing here is a row. A suggestion is made from column names and types, so naming
    the two columns is the whole of the evidence for it."""
    try:
        known = relationship_graph(catalog)
    except RuntimeError as failure:
        return refused("source", failure)
    return {
        "relationships": [
            {**relationship.as_dict(), "state": confirmations.state(relationship)}
            for relationship in confirmations.offered(known)
        ]
    }


@app.post("/api/relationships")
def answer(
    request: AnswerRequest,
    catalog: Annotated[Catalog, Depends(source)],
    confirmations: Annotated[Confirmations, Depends(answers)],
):
    """Confirm a suggestion, mark it as not a match, or take a confirmation back. The
    relationship has to be one the graph holds: an answer about a pair nothing suggested
    would be a person naming a join by hand, which is a different feature and is not this
    one."""
    try:
        known = relationship_graph(catalog)
    except RuntimeError as failure:
        return refused("source", failure)

    named = Relationship(
        request.left_table, request.left_column, request.right_table, request.right_column
    )
    match = next((r for r in known if r.key == named.key), None)
    if match is None:
        left = f"{named.left_table}.{named.left_column}"
        right = f"{named.right_table}.{named.right_column}"
        return JSONResponse(
            status_code=404,
            content={"errors": [f"nothing relates '{left}' to '{right}'"]},
        )
    if match.kind == DECLARED:
        return JSONResponse(
            status_code=400,
            content={
                "errors": [
                    f"'{match.key}' is declared by the source, which is not a person's to approve"
                ]
            },
        )
    try:
        confirmations.record(match, request.answer)
    except ValueError as failure:
        return JSONResponse(status_code=400, content={"errors": [str(failure)]})
    return {"relationship": match.as_dict(), "state": confirmations.state(match)}


@app.get("/api/join-path")
def join_path(
    left: str,
    right: str,
    catalog: Annotated[Catalog, Depends(source)],
    confirmations: Annotated[Confirmations, Depends(answers)],
):
    """How to get from one table to another, as the joins a spec would carry.

    Resolution walks confirmed relationships only, so an ambiguity and an absence are
    both refusals rather than a guess. They answer 400 with the resolver's own words,
    because that message is what a person who dragged a field reads."""
    try:
        known = relationship_graph(catalog)
    except RuntimeError as failure:
        return refused("source", failure)
    try:
        path = resolve(confirmations.usable(known), left, right)
    except ValueError as failure:
        return JSONResponse(status_code=400, content={"errors": [str(failure)]})
    return {"joins": _joins(left, path)}


def _joins(left: str, path: list[Relationship]) -> list[dict]:
    """A resolved path in the spec grammar's own shape. Each step names the table being
    joined, which is whichever end of the relationship the walk had not reached yet, so
    the direction of the foreign key does not decide the direction of the walk."""
    joins = []
    reached = {left}
    for relationship in path:
        table = (
            relationship.right_table
            if relationship.left_table in reached
            else relationship.left_table
        )
        joins.append(
            {
                "table": table,
                "on": [
                    {
                        "left": f"{relationship.left_table}.{relationship.left_column}",
                        "right": f"{relationship.right_table}.{relationship.right_column}",
                    }
                ],
            }
        )
        reached.add(table)
    return joins

# Neither endpoint below declares a response model. One would re-serialise the rows on
# their way out, and pydantic and the encoder disagree about a decimal, so a total would
# reach the chart as text. What a value looks like belongs to the source, and the API
# hands on the result set it was given.
@app.post("/api/validate")
def validate(request: SpecRequest):
    """The validator's errors. An empty list means valid. Reaches no source."""
    return {"errors": validate_spec(request.spec)}


@app.get("/api/dashboards")
def dashboards(store: Annotated[Dashboards, Depends(saved)]):
    """Every saved dashboard, as its name and how many tiles are under it.

    The tiles themselves are not here. A list of names is what a person picks from, and
    answering it with every spec of every dashboard would send the whole store to draw a
    menu. Reaches no source: a dashboard is specs, and running one is what runs a query."""
    return {
        "dashboards": [
            {"name": name, "tiles": len(store.read(name).tiles)} for name in store.names()
        ]
    }


@app.get("/api/dashboards/{name}")
def dashboard(name: str, store: Annotated[Dashboards, Depends(saved)]):
    """One dashboard: its tiles, in the order they are drawn in, each with its width. The
    rows are not here either. Every tile goes back through `/api/execute`, so a tile and a
    single chart are the same spec run the same way against the same source."""
    found = store.read(name)
    if found is None:
        return JSONResponse(
            status_code=404, content={"errors": [f"nothing is saved under the name '{name}'"]}
        )
    return found.as_dict()


@app.put("/api/dashboards/{name}")
def save_dashboard(
    name: str, request: DashboardRequest, store: Annotated[Dashboards, Depends(saved)]
):
    """Save a dashboard under a name, replacing whatever that name held.

    Every tile is validated before anything is written, and a rejection comes back in the
    same `errors` list the validator's own refusal uses, naming which tile by its position
    on the grid. Nothing partial is stored: a save that refused one tile leaves what was
    saved before exactly as it was, because half a dashboard is not a thing a person asked
    for."""
    try:
        return store.save(name, request.tiles).as_dict()
    except Refused as failure:
        return JSONResponse(status_code=400, content={"errors": failure.errors})


@app.delete("/api/dashboards/{name}")
def delete_dashboard(name: str, store: Annotated[Dashboards, Depends(saved)]):
    """Forget one. A name nothing is saved under is a 404 rather than a quiet success,
    since a delete that always succeeds cannot tell a person they deleted the other one."""
    if not store.delete(name):
        return JSONResponse(
            status_code=404, content={"errors": [f"nothing is saved under the name '{name}'"]}
        )
    return {"name": name}


def refused(spoke: str, failure: Exception, status: int = 502) -> JSONResponse:
    """A failure after validation, in the shape the validator's refusal already uses, so the
    interface keeps one way to show a refusal rather than growing a second. 502 by default,
    rather than 500 or 400: the request was well formed and the spec was valid, and what
    failed sits behind the server. `spoke` names which part failed, because a question
    passes through the source, the model and the source again, and a caller cannot tell
    from a message which of them produced it."""
    return JSONResponse(status_code=status, content={"errors": [str(failure)], "spoke": spoke})


def execute_spec(spec: dict, catalog: Catalog):
    """The rows, or what refused to produce them.

    `RuntimeError` is the source: a statement it did not finish, or a result too large for
    one chunk. `ValueError` is the spec, and it answers 400 rather than 502 because nothing
    behind the server was reached and the spec is what has to change. Both callers validate
    before they get here, so the only rule left to fail is the builder's re-aggregation
    check, which needs the compiled query and cannot run inside the validator."""
    try:
        rows = query.execute(spec, catalog)
    except ValueError as failure:
        return refused("spec", failure, status=400)
    except RuntimeError as failure:
        return refused("source", failure)
    return {"spec": spec, "rows": rows}


@app.post("/api/execute")
def execute(request: SpecRequest, catalog: Annotated[Catalog, Depends(source)]):
    """The rows a valid spec produces, with the spec that produced them. An invalid spec is
    refused with its errors and never reaches the source."""
    errors = validate_spec(request.spec)
    if errors:
        return JSONResponse(status_code=400, content={"errors": errors})
    return execute_spec(request.spec, catalog)


@app.post("/api/ask")
def question(
    request: QuestionRequest,
    catalog: Annotated[Catalog, Depends(source)],
    writer: Annotated[Model, Depends(model)],
):
    """A question, answered as the spec it produced and the rows that spec returned. The
    model sees the profiles and the question. The rows it caused to be fetched go to the
    caller, never back to it.

    Profiling reaches the source before the model is asked anything, so the first thing a
    question can fail on is the source rather than the model."""
    try:
        tables = profiles(catalog)
    except RuntimeError as failure:
        return refused("source", failure)
    try:
        answer = ask(request.question, tables, writer, constrained=constrains(writer))
    except ModelError as failure:
        return refused("model", failure)
    if answer.spec is None:
        return JSONResponse(status_code=400, content={"errors": answer.errors})
    return execute_spec(answer.spec, catalog)


if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")

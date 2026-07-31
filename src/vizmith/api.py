"""The HTTP surface: a spec goes in, its errors or its rows come back.

A request carries a spec and nothing else. The data source is server configuration, so a
client cannot name a database, and no response carries SQL, so a client cannot learn one
either. The artefact a client holds is the spec, which is the point of the whole design.

Validator messages are returned word for word. They are written to be fed back to a model
on retry, so rewording them here would break that loop before it is written.
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from vizmith import __version__, query
from vizmith.ask import ask
from vizmith.catalog import Catalog, DatabricksCatalog
from vizmith.model import Endpoint, Model
from vizmith.profiler import profile_table
from vizmith.spec import validate_spec

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"

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
def profiles(catalog: Catalog) -> tuple:
    """Every table in the configured schema, profiled once and kept for the life of the
    process. Profiling a table is two warehouse queries, so doing it per question would
    make asking one the slowest thing here, every time. A schema that changes under a
    running server is not noticed until it restarts."""
    return tuple(profile_table(catalog, name) for name in catalog.tables())


class SpecRequest(BaseModel):
    # Deliberately untyped: the validator answers for every shape a spec can arrive in,
    # including the ones that are not objects at all, and a model that rejected those
    # first would replace its message with one of pydantic's.
    spec: object


class QuestionRequest(BaseModel):
    question: str


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


# Neither endpoint below declares a response model. One would re-serialise the rows on
# their way out, and pydantic and the encoder disagree about a decimal, so a total would
# reach the chart as text. What a value looks like belongs to the source, and the API
# hands on the result set it was given.
@app.post("/api/validate")
def validate(request: SpecRequest):
    """The validator's errors. An empty list means valid. Reaches no source."""
    return {"errors": validate_spec(request.spec)}


@app.post("/api/execute")
def execute(request: SpecRequest, catalog: Annotated[Catalog, Depends(source)]):
    """The rows a valid spec produces, with the spec that produced them. An invalid spec is
    refused with its errors and never reaches the source."""
    errors = validate_spec(request.spec)
    if errors:
        return JSONResponse(status_code=400, content={"errors": errors})
    return {"spec": request.spec, "rows": query.execute(request.spec, catalog)}


@app.post("/api/ask")
def question(
    request: QuestionRequest,
    catalog: Annotated[Catalog, Depends(source)],
    writer: Annotated[Model, Depends(model)],
):
    """A question, answered as the spec it produced and the rows that spec returned. The
    model sees the profiles and the question. The rows it caused to be fetched go to the
    caller, never back to it."""
    answer = ask(request.question, profiles(catalog), writer)
    if answer.spec is None:
        return JSONResponse(status_code=400, content={"errors": answer.errors})
    return {"spec": answer.spec, "rows": query.execute(answer.spec, catalog)}


if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")

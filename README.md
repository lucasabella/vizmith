# Vizmith

Ask a question in plain language, get a chart back.

Vizmith connects to a database or lakehouse, reads its **metadata** (schemas, column types, cardinality, null rates, value ranges), and uses an LLM to turn a natural language question into a validated visualisation spec. A deterministic renderer draws the chart. The LLM never renders anything and never sees your raw rows.

**Status: early development. The shell runs and the backend answers. Nothing behind it works yet.**

## Why metadata and not the data

The LLM receives a profile of your tables, not their contents. That keeps token cost bounded, keeps results reproducible, and means the tool can pass a data governance review: no customer records leave your infrastructure through the model.

## Design

```
question -> catalog + profile -> query -> result set -> chart spec -> renderer
```

The model does not write SQL. It returns a query IR: a JSON description of tables, joins, filters, grouping and aggregation, which a deterministic builder compiles into a query. Nothing the model produces reaches the database unvalidated.

The spec is versioned JSON, validated against a schema, diffable in git. Every feature produces the same output type:

- Type a question, get a spec.
- Adjust the spec by hand if you want more control.
- Save a set of specs as a dashboard.

Nothing generated is executed as code.

## Bring your own model

Vizmith targets the OpenAI-compatible `base_url` convention, so one adapter covers hosted providers, Azure OpenAI, Ollama, vLLM and LM Studio. You supply the key and the endpoint. Vizmith ships with no model access of its own.

## Stack

Python (FastAPI) backend, React frontend, ECharts for rendering, Databricks Unity Catalog as the connector. DuckDB is the test harness, not a source you point Vizmith at.

Vizmith runs on your machine and serves a browser. The frontend talks to the backend over HTTP and nothing else, which keeps a desktop build possible later without touching application code.

## Running it

```
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/vizmith serve
```

That starts the API on port 8000 and opens a browser. Running a spec needs a source, which is server configuration rather than something a request carries. Copy `.env.example` to `.env` and fill in the four values; `vizmith serve` reads it, and a real environment variable wins over it.

For frontend work, run Vite alongside it:

```
cd web
npm install
npm run dev
```

Vite proxies `/api` to port 8000. To run everything from one process instead, build the frontend once with `npm run build`; the backend serves `web/dist` when it exists.

Tests and lint:

```
.venv/bin/pytest
.venv/bin/ruff check .
```

The suite runs offline against DuckDB. With a profile and a warehouse in `.env` it also compiles and runs every fixture spec against the workspace, through the query builder and through the HTTP API. Without one those tests skip.

## Licence

Apache-2.0. See [LICENSE](LICENSE).

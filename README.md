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

A compatible base URL does not mean a compatible feature set. Vizmith asks the model for a chart spec constrained to a JSON Schema, and support for that differs. It is also not one capability: an endpoint accepts a schema or refuses it, and OpenAI does both depending on which schema it is, because its structured output covers a subset of JSON Schema. Vizmith's spec schema is outside that subset, so it is refused there and the question is asked in prose with the schema in the prompt instead. Checked against each vendor's documentation in July 2026, not against a running instance except where stated:

| Endpoint | JSON Schema response format | Notes |
|---|---|---|
| OpenAI | Not for this schema | `response_format` with `strict: true` is accepted, and covers a subset of JSON Schema that Vizmith's spec schema sits outside of: a live endpoint, `gpt-5.6-luna`, answered `400 'if' is not permitted` on 31 July 2026. Questions there are asked in prose. |
| Azure OpenAI | Not for this schema | Only through the `/openai/v1/` base URL, which takes a plain bearer key. `model` is the deployment name, not the model name. Same structured output implementation as OpenAI, so the same subset applies. Not measured. |
| vLLM | Yes | 0.8.5 and later. The older `guided_json` parameter is deprecated in favour of `response_format`. |
| LM Studio | Yes | `json_schema` only, no `json_object` mode. |
| Ollama | No | Its OpenAI-compatible route takes a `format` parameter instead, so the schema is refused here. |

None of these were verified against a live endpoint in this repository, because the test suite makes no model calls. The adapter therefore asks rather than assumes: `Model.constrains_output(schema)` sends one request carrying the schema the caller is about to use and reports what the endpoint did with it. It takes the schema rather than owning one, because an answer about a simpler schema than the one being sent is an answer to a different question. The server asks it once, on the first question, so nothing here is configuration and an endpoint that changes needs a restart to be noticed. Smoke check a real endpoint before trusting the table:

```
.venv/bin/python -c "
from vizmith.model import Endpoint, Model
from vizmith.ask import SCHEMA
m = Model(Endpoint(base_url='https://api.example.com/v1', model='a-model', api_key='YOUR-KEY'))
print('schema constrained:', m.constrains_output(SCHEMA))
print(m.complete('Answer with the word ready.').text)
"
```

## Stack

Python (FastAPI, httpx) backend, React frontend, ECharts for rendering, Databricks Unity Catalog as the connector. DuckDB is the test harness, not a source you point Vizmith at.

Vizmith runs on your machine and serves a browser. The frontend talks to the backend over HTTP and nothing else, which keeps a desktop build possible later without touching application code.

## Running it

```
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/vizmith serve
```

That starts the API on port 8000 and opens a browser. Running a spec needs a source, which is server configuration rather than something a request carries. Copy `.env.example` to `.env` and fill it in; `vizmith serve` reads it, and a real environment variable wins over it. The four `VIZMITH_DATABRICKS_` values are the source. The three `VIZMITH_MODEL_` values are the endpoint that writes a spec, and without them the question field stays disabled while a spec pasted by hand still runs.

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

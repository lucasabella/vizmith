# Model endpoint compatibility

Vizmith targets the OpenAI-compatible `base_url` convention, so one adapter covers hosted
providers, Azure OpenAI, Ollama, vLLM and LM Studio.

A compatible base URL does not mean a compatible feature set. Vizmith asks the model for a
chart spec constrained to a JSON Schema, and support for that differs. It is also not one
capability: an endpoint accepts a schema or refuses it, and OpenAI does both depending on
which schema it is, because its structured output covers a subset of JSON Schema. Vizmith's
spec schema is outside that subset, so it is refused there and the question is asked in
prose with the schema in the prompt instead.

This table lives here rather than in the README because it rots on a schedule nobody sets.
Every row carries the date it was last checked and how, so a stale row is visibly stale
rather than quietly wrong. Nothing in CI re-verifies any of it: the test suite makes no
model calls, by design.

| Endpoint | JSON Schema response format | Last verified | How |
|---|---|---|---|
| OpenAI | Not for this schema | 31 July 2026 | Live endpoint. `gpt-5.6-luna` answered `400 'if' is not permitted`. `response_format` with `strict: true` is accepted, and covers a subset of JSON Schema that Vizmith's spec schema sits outside of, so questions there are asked in prose. |
| Azure OpenAI | Not for this schema | July 2026 | Vendor documentation only. Reachable through the `/openai/v1/` base URL, which takes a plain bearer key; `model` is the deployment name, not the model name. Same structured output implementation as OpenAI, so the same subset applies. |
| vLLM | Yes | July 2026 | Vendor documentation only. 0.8.5 and later. The older `guided_json` parameter is deprecated in favour of `response_format`. |
| LM Studio | Yes | July 2026 | Vendor documentation only. `json_schema` only, no `json_object` mode. |
| Ollama | No | July 2026 | Vendor documentation only. Its OpenAI-compatible route takes a `format` parameter instead, so the schema is refused here. |

## Do not trust the table

One row was measured and four were read. The adapter therefore asks rather than assumes.
`Model.constrains_output(schema)` sends one request carrying the schema the caller is about
to use and reports what the endpoint did with it. It takes the schema rather than owning
one, because an answer about a simpler schema than the one being sent is an answer to a
different question.

The server asks once, on the first question, so nothing here is configuration and an
endpoint that changes needs a restart to be noticed.

Smoke check a real endpoint before trusting any of the above:

```
.venv/bin/python -c "
from vizmith.model import Endpoint, Model
from vizmith.ask import SCHEMA
m = Model(Endpoint(base_url='https://api.example.com/v1', model='a-model', api_key='YOUR-KEY'))
print('schema constrained:', m.constrains_output(SCHEMA))
print(m.complete('Answer with the word ready.').text)
"
```

If you check a row and it has moved, please update it here with the date and how you
checked, rather than only mentioning it in an issue. A table nobody maintains is worse than
no table, because it is believed.

# Source compatibility

Which sources Vizmith can be pointed at, what each one answers for the two contracts that
have a `None` in them, and how that was checked. Same house rule as the table above: a row
carries the date and the method, so a row that was read rather than measured says so.

| Source | Namespace | Approximate distinct | Freshness token | Declared keys | Last verified | How |
|---|---|---|---|---|---|---|
| Databricks (Unity Catalog) | `catalog.schema.table` | `approx_count_distinct` | `DESCRIBE DETAIL`'s `lastModified`, one billed statement per table | Read where somebody declared one by hand; never enforced | July 2026 | Live workspace. `tests/fixtures/catalog/tables.json` is the recording, and the live tests check it is still true. |
| DuckDB | `database.schema.table` | `approx_count_distinct` | **None.** The profile cache is off for this source | Read from `duckdb_constraints()`, and enforced by the engine | August 2026 | A real file, in CI. `tests/test_duckdb.py` compiles and runs every spec the repository ships against it. |
| BigQuery | `project.dataset.table` | `APPROX_COUNT_DISTINCT` | **None**, pending a measurement. `__TABLES__.last_modified_time` is the candidate and the streaming buffer is why it is not trusted yet | Read from `INFORMATION_SCHEMA`, declared and unenforced | August 2026 | **Never run against a project.** Vendor documentation, plus deterministic tests against a fake client using the real client library's own parameter types. The live half of `tests/test_bigquery.py` is written and skips without `VIZMITH_BIGQUERY_PROJECT`. |

Two rows say `None` for a freshness token and they do not mean the same thing. DuckDB has
nothing to offer and that is settled. BigQuery has a candidate that nobody has measured, and
until somebody does, the connector reports `None` rather than a token that mostly moves —
`tests/test_bigquery.py::test_whether_the_modified_time_moves_when_the_data_changes` is the
measurement, and it needs a project.

`None` for a freshness token is not a gap in the connector. The protocol says a source with
no token to give must report one, and a caller must then not cache, which `Profiles` does:
a profile is rebuilt per read rather than stored under something that would not move when
the data does. On a local file that costs two statements nobody bills for.

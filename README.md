# Vizmith

Ask a question in plain language, get a chart back.

Vizmith connects to a database or lakehouse, reads its **metadata** (schemas, column types, cardinality, null rates, value ranges), and uses an LLM to turn a natural language question into a validated visualisation spec. A deterministic renderer draws the chart. The LLM never renders anything and never sees your raw rows.

**Status: early development. Nothing works yet.**

## Why metadata and not the data

The LLM receives a profile of your tables, not their contents. That keeps token cost bounded, keeps results reproducible, and means the tool can pass a data governance review: no customer records leave your infrastructure through the model.

## Design

```
question -> catalog + profile -> query -> result set -> chart spec -> renderer
```

The chart spec is versioned JSON, validated against a schema, diffable in git. Every feature produces the same output type:

- Type a question, get a spec.
- Adjust the spec by hand if you want more control.
- Save a set of specs as a dashboard.

Nothing generated is executed as code.

## Bring your own model

Vizmith targets the OpenAI-compatible `base_url` convention, so one adapter covers hosted providers, Azure OpenAI, Ollama, vLLM and LM Studio. You supply the key and the endpoint. Vizmith ships with no model access of its own.

## Stack

Python (FastAPI) backend, React frontend, ECharts for rendering, DuckDB and Databricks as the first two connectors.

## Licence

Apache-2.0. See [LICENSE](LICENSE).

# Security

## Reporting a vulnerability

Report privately through GitHub, at
[Security → Report a vulnerability](https://github.com/lucasabella/vizmith/security/advisories/new).
That opens an advisory only you and the maintainer can read.

Please do not open a public issue for anything that would let somebody reach data they
should not. People run Vizmith against their own warehouses, and a public issue tells
everyone at once, including them last.

There is one maintainer and no schedule to promise. What you can expect is an
acknowledgement within a week, and to be told plainly if the answer is that it will not be
fixed soon, rather than left waiting for one that is not coming.

## What is in scope

Vizmith is a local tool. It runs on your machine, serves a browser on loopback, holds
credentials for a warehouse and a model endpoint in a file, and compiles a JSON spec into
SQL that runs against your data. Interesting failures generally look like:

- A spec, a question or a request that reads data outside the configured catalog and
  schema.
- Anything that reaches the API from a page the person did not open deliberately. The
  server checks the `Host` and `Origin` headers because it has no authentication; a way
  around those checks is a finding.
- A path that gets the model API key or the warehouse credentials into a response, a log,
  an error message, or an eval run record.
- SQL built from a spec that escapes the query builder's quoting or parameter binding.
- Anything that writes outside the state directory.

Out of scope: findings that need an attacker who already has a shell on the machine or
write access to the warehouse, since both of those already reach the data directly.

## What the model is sent

Worth stating here because it is the question most often asked as a security question. The
model receives column names, types, null rates, distinct counts, the extremes of ordered
columns, and the full value set of any column with no more distinct values than
`SAMPLE_THRESHOLD` in `profiler.py`, currently 25. It is never sent a row, and query
results go to the renderer rather than back into a prompt. That boundary is described in
the README and is a design decision rather than a vulnerability. A path that sends more
than that is a vulnerability.

## Versions

Vizmith is early and unreleased. Fixes land on `main`; there is no supported older version
to back-port to yet.

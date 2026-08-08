# Contributing

This project has a definite house style. Most of it is legible from the code and none of it
was written down, so a first contributor either inferred it or found out in review. This is
that, written down.

## Getting the suite running

```
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
cd web && npm ci && npm run build
```

Then, from the root:

```
.venv/bin/ruff check .
.venv/bin/pytest --cov
```

and in `web/`:

```
npm run lint
npm test
npm run build
```

Those five are the gate. CI runs exactly them and adds nothing, because a check that only
exists on a runner is one nobody can answer before pushing.

The browser suite is separate:

```
.venv/bin/pytest tests/test_interface.py
```

**It needs a built frontend.** The server serves `web/dist`, so the suite skips entirely
when there is none, and — the part that will cost you an afternoon — it passes happily
against a *stale* one. If you changed anything under `web/src` and the browser suite still
agrees with you, run `npm run build` and try again. Where Playwright's own browser is not
the one installed, `VIZMITH_CHROMIUM` names the executable to drive.

`uv.lock` records the dependency set CI installs. `pip install -e ".[dev]"` resolves the
floors in `pyproject.toml` instead, which is usually what you want while working; use
`uv sync --extra dev` when you need to reproduce a result rather than approximate it. A
change that moves a dependency has to move the lock with it, and CI refuses the two when
they disagree.

### What skips, and why that is not neglect

About seventy tests skip on a clean checkout. They are not incomplete: they need a
Databricks warehouse, a model endpoint, or a browser, and none of those belongs in a public
repository's CI — the reasoning is in `.github/workflows/ci.yml` and in `ROADMAP.md`. The
offline suite reaches 98% of the package without any of them. If your change touches the
live half, say so in the pull request; nobody can check it for you.

## Where a decision goes

**`ROADMAP.md` holds decisions, argued rather than asserted.** If your change makes a
choice somebody could reasonably have made differently, the entry says what the alternative
was and what it would have cost. Entries are written in the past tense about a thing that is
now true, and they are allowed to be long. An entry that only says what the code does is one
the code already said.

**A module docstring says what was rejected.** The reason `catalog.py` explains why a
freshness answer is held per burst rather than per entry is that the next person to read it
would otherwise reasonably change it back. Comments here explain why, on the assumption the
reader can see what.

**A number in a comment should be one somebody measured.** This repository argues from
measurement — bundle sizes, statement counts, wall clock against a modelled schema — and
where a number is reasoned rather than measured it says so in those words. Both are
acceptable; a guess presented as a measurement is not.

## The rules that do not move

These are load-bearing. A change that crosses one needs to argue for it in the pull request
rather than do it quietly.

- **No row reaches a prompt.** The model gets profiles. `SAMPLE_THRESHOLD` in `profiler.py`
  is the boundary — a column with more distinct values than that contributes none of them —
  and nothing widens it.
- **The validator is the only judge of a spec.** The interface holds no second opinion,
  because an opinion in the browser is one that can disagree with the one that counts.
  `draftIn` in `spec.ts` is the narrow exception and its docstring says why: whether a value
  is *shaped* like a spec is a question the browser has to answer before it can render, and
  it is not the question of whether the spec is legal.
- **Nothing a spec carries reaches the SQL text.** Identifiers come from the catalog and
  everything else is bound as a parameter, including values the validator has already seen.
- **A spec may only read where the server is configured to read.** `Scope` in `catalog.py`,
  enforced above the connector so a source that never learned the rule cannot be pointed
  outside it.
- **A join needs a confirmed relationship.** A wrong join produces a plausible number rather
  than an error, which is the failure the whole design exists to prevent.

## Tests

Name a test for the property it holds, as a sentence:
`test_a_burst_that_is_still_reading_keeps_what_it_read_at_the_start_of_it`. The docstring
says why the property matters, which is usually a bug that happened.

**Check that your test fails without your change.** Revert the production line in a scratch
copy and run it. This is not ceremony — it has caught tests here that passed for reasons
unrelated to what they claimed, and one that could not have failed at all. If a test cannot
be made to fail, say so in its docstring rather than leaving it looking like a guard.

Prefer holding a thing still to timing it. A test that asserts an ordering by withholding an
answer until the test lets it go is deterministic; one that asserts the same thing with a
clock fails on whatever else the machine was doing.

## Commits and pull requests

A commit subject is a sentence about behaviour, not a component and a verb. *"An unanswered
request is sent again, and a burst holds what it read"* rather than *"fix retry logic"*. The
body says what was wrong, what changed, and what it cost — the diff already says how.

The pull request template asks for what it asks for on purpose. **"Anything worth arguing
about"** is the section that matters most: a trade-off you made that somebody could
reasonably have made differently, said here rather than left to be found in review. A pull
request with that section empty is usually one where the author has not noticed the
trade-off yet.

If your change closes an issue, say `Closes #123`. If measuring changed what you built — it
happens here more than you would expect — say what you measured and what it redirected.

## Reporting things

Security holes go through
[Security → Report a vulnerability](https://github.com/lucasabella/vizmith/security/advisories/new),
not a public issue. See `SECURITY.md`, which also states the threat model this project
thinks it has.

Everything else is an issue. The templates ask for a claim rather than a component, and for
the measurement where there is one. An issue that says *"the freshness hold is shorter than
the request it protects"* is one somebody can act on; *"caching is broken"* is not.

# Releasing

How a commit on `main` becomes a published Vizmith, what the release workflow proves
before anything is published, and what to do when something between the two goes wrong.
The workflow this documents is `.github/workflows/release.yml`; this file is where the
judgement calls it cannot make are written down instead of improvised at the moment of
an incident.

## The one-time setup

PyPI publishes through [Trusted Publishing](https://docs.pypi.org/trusted-publishers/):
the release workflow mints a short-lived OIDC token per run, and PyPI accepts it because
it names exactly one publisher configuration. There is no API token in this repository
and there must never be one — a long-lived secret in a settings page is the thing this
arrangement exists to retire, because it outlives every rotation policy somebody meant
to keep.

Before the first real tag, the project owner adds that publisher on PyPI, under
*Account settings → Publishing*, with:

| field | value |
| --- | --- |
| Owner | `lucasabella` |
| Repository | `vizmith` |
| Workflow | `release.yml` |
| Environment | `pypi` |

The workflow declares the same `environment: pypi`, which is what ties the two halves
together. Until that configuration exists, a pushed tag produces everything except the
PyPI half, and the publish job fails naming it — which is the setup step being refused,
not an incident.

## Cutting a release

1. Bump `version` in `pyproject.toml`, through a pull request like any other change.
2. Merge it. The tag must point at protected history, because the workflow trusts the
   history it is given and branch protection is what makes that trust worth granting.
3. Tag and push:

   ```
   git tag -a v0.1.0b1 -m "Vizmith 0.1.0b1"
   git push origin v0.1.0b1
   ```

4. The workflow then proves, in order: the tag names the version the tree carries;
   both distributions build from that exact commit; the wheel installs somewhere clean
   and answers `vizmith --version`, serves its bundled interface and answers
   `/api/health`; the source distribution installs somewhere clean too; every artefact
   is hashed into `SHA256SUMS`; and the build's provenance is attested. Only after all
   of that do the GitHub release and the PyPI publish run, carrying those same files.

## Verifying a release yourself

Checksums and attestations travel with the release. From the directory holding them:

```
sha256sum -c SHA256SUMS
gh attestation verify vizmith-*.whl -R lucasabella/vizmith
```

An artefact whose hash disagrees with the file, or whose provenance will not verify, is
one to treat as compromised rather than broken: follow the withdrawal path below and
stop before reinstalling anything.

## When something goes wrong

**A job failed and nothing was published.** Rerun the failed jobs from the Actions run.
Everything rebuilds from the same commit and the same tag, so the rerun either passes
now or names the problem more precisely. There is nothing to withdraw: publication is
the last thing that happens, deliberately, so a failure before it costs nothing but
time.

**The GitHub release exists but PyPI refused.** Fix the cause, delete the draft state if
any, and rerun the publish job alone. The GitHub half is idempotent enough to redo; the
files it carries are already the ones that were proved.

**Something wrong reached PyPI.** Published versions cannot be deleted, only **yanked**:
on pypi.org, *Manage → Options → Yank*, with a reason. A yanked version stops being
resolved by `pip install vizmith` and `pip install vizmith==X` keeps working for anybody
who pinned it deliberately — which is the correct behaviour for a release that builds
but should not have been chosen. Then delete the GitHub release, fix the actual problem,
and cut a new tag with a bumped version.

**Never move or re-push a tag that has published anything.** A tag names a release that
already exists in other people's lockfiles; repointing it makes two different artefacts
that claim the same identity, which is the one outcome worse than a bad release. The
remedy for a bad release is always a new version number.

**Selecting a known good.** Every earlier release stays on its GitHub release page with
its checksums, so rolling back is `pip install vizmith==<previous>` or building from the
previous tag. Record which version is known good, and why the newer one is not, in the
newer release's notes — the next person diagnosing a regression reads that before they
read anything else.

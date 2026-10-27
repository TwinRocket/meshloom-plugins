# AGENTS.md — meshloom-plugins

The reviewed plugin catalogue for Meshloom. Public repository: every pull request may come
from a stranger and carries untrusted code (the plugin archive it points at).

## Gate (run before saying a change is ready)

```sh
uv sync --frozen
uv run --frozen ruff check
uv run --frozen ruff format --check
uv run --frozen pytest -q
```

Locally, tests that need the pinned meshloom checkout or `bwrap` are skipped. To run them,
check out meshloom at `MESHLOOM_REF`, run `uv sync --frozen --no-dev` in it, and set
`MESHLOOM_DIR=<that checkout>`; `bwrap` exists on Linux only. The CI sets
`CATALOGUE_STRICT=1`, which turns those skips into failures.

## Layout

- `plugins/<id>.yaml`: catalogue entries (`schema/entry.schema.json`, owned here).
- `schema/manifest.schema.json`, `schema/vectors/manifest.json`: byte copies of meshloom
  `docs/dev/plugins/` at the pin; `tests/test_schema_contract.py` checks the equality.
- `scripts/validate_catalogue.py`: the validator the CI runs on the changed entries.
- `tests/fixtures/sample_plugin/`: copy of meshloom `tests/plugin_dev/echo` at the pin.
- `keys/`: public index keys, empty until T17 (signed index and `publish.yml`).

## The meshloom pin

`MESHLOOM_REF` in `.github/workflows/validate.yml` is the only place the meshloom commit is
written; the tests read it from there. It is a full commit SHA on `preversion-5` until
5.0.0 ships, then the commit of the 5.0.0 tag. To move it: change `MESHLOOM_REF`, copy
`docs/dev/plugins/manifest.schema.json`, `docs/dev/plugins/vectors/manifest.json` and
`tests/plugin_dev/echo/*` again from that commit, and run the gate with `MESHLOOM_DIR`.

## Safety rules (asserted by tests/test_workflow_policy.py)

- One workflow, triggered by `pull_request` only (never its target-context variant, never
  `workflow_run`); no repository secret is referenced anywhere under `.github/`, templates
  included; a read-only token; checkouts keep no credentials.
- GitHub-hosted runners only, failing closed: `vars.RUNNER` may name another GitHub-hosted
  Ubuntu label, a `preflight` job refuses anything else, and every job's first step
  refuses a runner whose `RUNNER_ENVIRONMENT` is not `github-hosted`.
- Actions limited to `actions/checkout`, `actions/setup-python` and `astral-sh/setup-uv`,
  pinned by commit SHA; jobs time out within 15 minutes; one run per pull request.
- The validator and its schemas come from the base commit, so a pull request cannot change
  the rules it is judged by (the bootstrap pull request, whose base has none, is the only
  exception). A pull request that changes entries and anything else is refused.
- Archives: `Content-Length` checked on `HEAD` before any download, bounded download,
  SHA-256 checked, contents inspected before extraction, extraction into fresh temp
  directories outside the repository. Plugin code runs only under bubblewrap (no network,
  no environment, read-only filesystem); no `bwrap` means a failure, never an unsandboxed
  run.

## Repository settings (owner actions, not enforced by any file here)

- Settings > Actions > "Require approval for first-time contributors" (or stricter).
- A ruleset on `main`: pull request required, review by a code owner, no direct push, the
  `result` check required. Signed commits are **not** required (agent pull requests could
  not satisfy it).
- Keep `vars.RUNNER` empty or a GitHub-hosted label.

## Working rules

- Docs in sync on every change: any change to code, CI, schema or settings comes with a
  check of the docs that describe it (this file, README, `docs/`, templates, headers),
  fixed in the same pull request; the pull request body lists the docs checked.
- Author-facing text (`docs/authoring*.md`, pull request and issue templates) exists in
  English and French; never drop a language.
- Code, comments and dev docs in English.
- Agents never merge, never change repository settings, never add a plugin entry on their
  own initiative.

# Publishing a plugin in the Meshloom catalogue

French summary: [authoring.fr.md](authoring.fr.md).

This catalogue lists reviewed Meshloom plugins. You keep your plugin in your own GitHub
repository; the catalogue only records which release to trust, by its SHA-256.

## 1. Build and check the plugin

Follow the plugin developer guide in the Meshloom repository (`docs/dev/plugins/`). Before
submitting, run the same check the CI runs, from a Meshloom checkout:

```sh
python -m meshloom_plugin.dev check path/to/your-plugin --origin third_party
```

The manifest must satisfy the contract. In particular:

- `id` is `<publisher>.<name>`, where `<publisher>` is the GitHub owner of your repository,
  lowercased (`acme/meshloom-weather` publishes `acme.*`). `meshloom.*` belongs to the
  Meshloom project and `local.*` to local plugins.
- `license` is one of: Apache-2.0, BSD-2-Clause, BSD-3-Clause, GPL-3.0-only, ISC,
  LGPL-3.0-only, MIT, MPL-2.0. Bundled code under `vendor/` must declare an accepted
  licence as well (dist-info `License-Expression`, `License` or classifier).
- The name must not imitate Meshloom or another publisher; links must use `https:`.
- Text shown to users (name, summary, permission reasons) is given in English and French.

## 2. Publish a release

Create a GitHub release whose tag points at the version and attach one asset named
`plugin.zip`, with `manifest.json` at the root of the archive. Limits:

- archive at most 5 MiB, served with a `Content-Length` header (GitHub does this);
- at most 20 MiB uncompressed and 2000 entries;
- regular files only: no symbolic links, no executable bit, no encryption, no hidden files,
  no `__pycache__` or `.pyc`, no native libraries (`.so`, `.pyd`, `.dll`, `.dylib`), no
  names that differ only by case or Unicode normalisation.

Then compute the digest: `sha256sum plugin.zip` (or `shasum -a 256 plugin.zip`).

A release asset must never be replaced: the catalogue checks the digest again when it
publishes, and a changed archive is refused.

## 3. Open the pull request

Fork this repository and add or update `plugins/<id>.yaml`:

```yaml
id: acme.weather
repo: acme/meshloom-weather
tier: reviewed
releases:
  - version: "1.0.0"
    tag: v1.0.0
    sha256: "<64 lowercase hex characters>"
```

- Quote `version` and `sha256`: unquoted, YAML may read them as numbers.
- An update appends one release at the end; existing releases are never edited or removed,
  and `id` never changes.
- If the new release adds or widens a permission compared with the previous one, add
  `permissions_changed: true` to it. The CI fails when a permission changes without it.
- Change only files under `plugins/` in that pull request (at most 10 entries, 5 new
  releases each).

Tick every box of the submission declaration in the pull request template.

## 4. What the CI checks

The `validate` workflow, on Python 3.11, 3.13 and 3.14:

1. the entry format, the file name, the publisher and the release history;
2. the announced size (`HEAD`), then the download and the SHA-256;
3. the archive contents, before extraction;
4. the manifest against the contract, `id` and `version` against the entry, the licence,
   the name, the links and vendored licences;
5. a dry run of the plugin inside a sandbox with no network (`meshloom_plugin.dev check`).

Its job summary shows the permission changes, the code diff from the previous release and
its size. A plugin that reads direct messages or every channel **and** uses `http` is
flagged as sensitive and gets a closer review.

The first workflow run of a first-time contributor waits for a maintainer's approval. A
green CI does not mean acceptance: a maintainer reviews every pull request.

## 5. Withdrawal and reports

- To withdraw your plugin or a version, open a pull request or an issue ("Request
  takedown"). The catalogue operator lists the version under `revoked:`; a revocation is
  never lifted by a pull request.
- The catalogue operator (the publisher named on the Meshloom legal pages) may revoke a
  plugin first and investigate afterwards when users are at risk.
- To report a plugin (security, privacy, licence, misleading content), open a "Report a
  plugin" issue; for anything sensitive, do not put details in a public issue and use the
  contact on the Meshloom legal pages instead.

# Meshloom plugin template

A working [Meshloom](https://github.com/TwinRocket/meshloom) plugin (`!hello` answers a
greeting), its tests, and the workflow that publishes a release the Store can install.
French version: [README.fr.md](README.fr.md).

## Start

This template lives in the `template/` folder of the
[meshloom-plugins](https://github.com/TwinRocket/meshloom-plugins) repository. Copy it into a
new repository of yours (the repository name is free; the plugin id is `<your GitHub owner,
lowercase>.<name>`):

```sh
git clone --depth 1 https://github.com/TwinRocket/meshloom-plugins.git
cp -R meshloom-plugins/template my-plugin && rm -rf meshloom-plugins
cd my-plugin && git init -b main
```

If the project owner has published this folder as a GitHub template repository, use it
instead: `gh repo create my-plugin --public --template <owner>/<template-repository>`.

Then:

1. Edit `plugin/manifest.json`: `id`, `name`, `summary`, `authors`, `license`, `categories`,
   `permissions` (declare only what you use), `privacy`. English is required, French is
   welcome. Rules and fields: the plugin developer guide, `docs/dev/plugins.md` in the Meshloom
   repository.
2. Replace `plugin/plugin.py`, `plugin/config.schema.json` (delete the example secret if you
   have none), `plugin/i18n/*.json`, `plugin/README.md` and `plugin/README.fr.md`. Put your
   name in `LICENSE` and `plugin/LICENSE` (MIT by default; you may pick another licence from
   the accepted list: Apache-2.0, BSD-2-Clause, BSD-3-Clause, GPL-3.0-only, ISC,
   LGPL-3.0-only, MIT, MPL-2.0, and set `license` in the manifest to match).
3. Adapt `tests/replay.json` and `tests/test_plugin.py`.

The archive is built from `plugin/` only (tests, workflows and this README stay out of it).

## Develop

The tooling is part of Meshloom and imports its server code, so you need a checkout of it, at
the commit `MESHLOOM_REF` in `.github/workflows/release.yml`:

```sh
git clone https://github.com/TwinRocket/meshloom .meshloom
git -C .meshloom checkout <MESHLOOM_REF>
(cd .meshloom && uv sync --frozen --no-dev)

.meshloom/.venv/bin/python -m meshloom_plugin.dev --help   # from inside .meshloom
```

Run from inside `.meshloom`, with absolute paths to your plugin:

| Command | What it does |
|---|---|
| `check <dir>/plugin` | The server's own validation, a static scan and a dry run |
| `run <dir>/plugin --replay <dir>/tests/replay.json` | Replays events against a fake host: permissions, quotas and send guards are the server's; nothing is transmitted |
| `pack <dir>/plugin --out <dir>/dist` | `check`, then `plugin.zip` and `plugin.zip.sha256` |

`MESHLOOM_DIR=.meshloom uv run --no-project --with pytest pytest` runs the tests. The replay does
not simulate the 2 s spacing between sends, the `http.fetch` host rules and egress guard, or the
storage quota across runs.

## Try it on your Meshloom

1. Push to GitHub, then tag the version of `plugin/manifest.json`: `git tag v1.0.0 && git push
   origin v1.0.0`. The `release` workflow tests, packs and creates the release (assets:
   `plugin.zip`, `plugin.zip.sha256`, `manifest.json`). Run it from the Actions tab first for a
   dry run: it does everything except the release.
2. In Meshloom, Settings > Extensions > Custom repositories (the Store must be online), add
   your repository as `owner/name` and install it. Custom repositories are unsigned: Meshloom
   shows a warning and checks the SHA-256 only.

## Get it into the catalogue

Once it works, open a pull request on
[meshloom-plugins](https://github.com/TwinRocket/meshloom-plugins) as described in
[docs/authoring.md](https://github.com/TwinRocket/meshloom-plugins/blob/main/docs/authoring.md):
the entry points at the release tag and the SHA-256 in `dist/plugin.zip.sha256`. Never replace
a release asset; publish a new version instead.

## Licence

The template files are MIT. Your plugin keeps the licence you choose.

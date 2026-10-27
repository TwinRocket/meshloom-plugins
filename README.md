# meshloom-plugins

The reviewed plugin catalogue for [Meshloom](https://github.com/TwinRocket/meshloom).

Each file `plugins/<id>.yaml` lists the releases of one plugin: the GitHub repository
that publishes them, the release tag and the SHA-256 of its `plugin.zip`. A submission or
an update is a pull request that adds a release line. The `validate` workflow downloads
the archive, checks it against the Meshloom plugin contract and runs it once under a
sandbox; a maintainer then reviews it.

- Publishing a plugin: [docs/authoring.md](docs/authoring.md) (French summary:
  [docs/authoring.fr.md](docs/authoring.fr.md)).
- Reporting a plugin or requesting a takedown: open an issue with the matching template.
- Maintaining this repository: [AGENTS.md](AGENTS.md).

The signed index built from these entries, its keys (`keys/`) and the publishing workflow
come later; until then this repository holds the entries and their validation only.

## Layout

| Path | Content |
|---|---|
| `plugins/<id>.yaml` | One catalogue entry per plugin |
| `schema/entry.schema.json` | The entry format (owned here) |
| `schema/manifest.schema.json`, `schema/vectors/` | Copies of the Meshloom plugin contract at the pinned commit |
| `scripts/validate_catalogue.py` | The validator run by the CI |
| `keys/` | Public index signing keys (empty for now) |
| `docs/` | Author documentation |

## Licence

The repository's own files are under the MIT licence (`LICENSE`). Each plugin keeps its
own licence, which must be on the accepted list.

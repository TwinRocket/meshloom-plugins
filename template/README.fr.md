# Modèle de plugin Meshloom

Un plugin [Meshloom](https://github.com/TwinRocket/meshloom) qui fonctionne (`!hello` répond
une salutation), ses tests, et le workflow qui publie une release que le Store sait installer.
Version anglaise : [README.md](README.md).

## Démarrer

Ce modèle est le dossier `template/` du dépôt
[meshloom-plugins](https://github.com/TwinRocket/meshloom-plugins). Copiez-le dans un nouveau
dépôt à vous (le nom du dépôt est libre ; l'id du plugin est `<votre propriétaire GitHub, en
minuscules>.<nom>`) :

```sh
git clone --depth 1 https://github.com/TwinRocket/meshloom-plugins.git
cp -R meshloom-plugins/template my-plugin && rm -rf meshloom-plugins
cd my-plugin && git init -b main
```

Si le propriétaire du projet a publié ce dossier comme dépôt modèle GitHub, utilisez-le :
`gh repo create my-plugin --public --template <propriétaire>/<dépôt-modèle>`.

Ensuite :

1. Modifiez `plugin/manifest.json` : `id`, `name`, `summary`, `authors`, `license`,
   `categories`, `permissions` (ne déclarez que ce que vous utilisez), `privacy`. L'anglais est
   obligatoire, le français bienvenu. Règles et champs : le guide du développeur de plugins,
   `docs/dev/plugins.md` dans le dépôt Meshloom.
2. Remplacez `plugin/plugin.py`, `plugin/config.schema.json` (supprimez l'exemple de secret si
   vous n'en avez pas), `plugin/i18n/*.json`, `plugin/README.md` et `plugin/README.fr.md`.
   Mettez votre nom dans `LICENSE` et `plugin/LICENSE` (MIT par défaut ; une autre licence de la
   liste acceptée convient : Apache-2.0, BSD-2-Clause, BSD-3-Clause, GPL-3.0-only, ISC,
   LGPL-3.0-only, MIT, MPL-2.0, avec `license` du manifeste à l'identique).
3. Adaptez `tests/replay.json` et `tests/test_plugin.py`.

L'archive est construite à partir de `plugin/` seul (les tests, les workflows et ce README n'y
entrent pas).

## Développer

L'outillage fait partie de Meshloom et importe son code serveur : il faut un clone de Meshloom,
au commit `MESHLOOM_REF` de `.github/workflows/release.yml` :

```sh
git clone https://github.com/TwinRocket/meshloom .meshloom
git -C .meshloom checkout <MESHLOOM_REF>
(cd .meshloom && uv sync --frozen --no-dev)
```

À lancer depuis `.meshloom`, avec des chemins absolus vers votre plugin :

| Commande | Rôle |
|---|---|
| `.venv/bin/python -m meshloom_plugin.dev check <dossier>/plugin` | La validation du serveur lui-même, une analyse statique et un essai à blanc |
| `... run <dossier>/plugin --replay <dossier>/tests/replay.json` | Rejoue des événements sur un hôte factice : permissions, quotas et garde-fous d'envoi sont ceux du serveur ; rien n'est transmis |
| `... pack <dossier>/plugin --out <dossier>/dist` | `check`, puis `plugin.zip` et `plugin.zip.sha256` |

`MESHLOOM_DIR=.meshloom uv run --no-project --with pytest pytest` lance les tests. Le rejeu ne
simule pas l'espacement de 2 s entre envois, les règles d'hôtes et la garde de sortie de
`http.fetch`, ni le quota de stockage d'une exécution à l'autre.

## L'essayer sur votre Meshloom

1. Poussez sur GitHub, puis posez le tag de la version de `plugin/manifest.json` :
   `git tag v1.0.0 && git push origin v1.0.0`. Le workflow `release` teste, empaquette et crée la
   release (assets : `plugin.zip`, `plugin.zip.sha256`, `manifest.json`). Lancez-le d'abord à la
   main depuis l'onglet Actions pour un essai : il fait tout sauf la release.
2. Dans Meshloom, Réglages > Extensions > Dépôts personnalisés (le Store doit être en ligne),
   ajoutez votre dépôt sous la forme `propriétaire/nom` et installez-le. Les dépôts
   personnalisés ne sont pas signés : Meshloom affiche un avertissement et ne vérifie que le
   SHA-256.

## Entrer au catalogue

Quand cela fonctionne, ouvrez une pull request sur
[meshloom-plugins](https://github.com/TwinRocket/meshloom-plugins) comme décrit dans
[docs/authoring.fr.md](https://github.com/TwinRocket/meshloom-plugins/blob/main/docs/authoring.fr.md) :
l'entrée désigne le tag de la release et le SHA-256 de `dist/plugin.zip.sha256`. Ne remplacez
jamais l'asset d'une release ; publiez une nouvelle version.

## Licence

Les fichiers du modèle sont sous licence MIT. Votre plugin garde la licence que vous choisissez.

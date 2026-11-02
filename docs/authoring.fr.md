# Publier un plugin dans le catalogue Meshloom (résumé)

Version complète, en anglais : [authoring.md](authoring.md).

Le catalogue liste des plugins relus. Votre plugin reste dans votre dépôt GitHub ; le
catalogue enregistre seulement la release à laquelle se fier, par son SHA-256.

0. **Partir du modèle** : [`template/`](../template/) est un plugin qui fonctionne, avec ses
   tests et un workflow de release ([README](../template/README.fr.md), version anglaise
   [README.md](../template/README.md)). Copiez-le dans un dépôt à vous ; le workflow construit
   `plugin.zip`, son SHA-256 et `manifest.json` et les attache à une release GitHub quand vous
   poussez un tag `v<version>`. Les mêmes assets permettent d'installer votre plugin depuis
   Meshloom comme dépôt personnalisé (Réglages > Extensions > Dépôts personnalisés), sans
   attendre le catalogue.
1. **Vérifier le plugin** avec `python -m meshloom_plugin.dev check <dossier> --origin
   third_party` depuis un clone de Meshloom. Le même outil rejoue des événements sur un hôte factice (`run <dossier> --replay fixture.json`) et construit l'archive de l'étape 2 (`pack <dossier> --out dist` écrit `plugin.zip` et `plugin.zip.sha256`). L'`id` est `<éditeur>.<nom>`, où l'éditeur est
   le propriétaire GitHub du dépôt en minuscules ; `meshloom.*` et `local.*` sont réservés.
   Licence acceptée : Apache-2.0, BSD-2-Clause, BSD-3-Clause, GPL-3.0-only, ISC,
   LGPL-3.0-only, MIT ou MPL-2.0 (y compris pour `vendor/`). Nom non trompeur, liens en
   `https:`, textes en anglais (la traduction française est bienvenue : la CI la signale sans la refuser).
2. **Publier une release GitHub** avec un asset `plugin.zip` (manifeste à la racine, 5 Mio
   au plus, 20 Mio décompressés, 2000 entrées, fichiers ordinaires seulement : ni lien
   symbolique, ni bit exécutable, ni bibliothèque native, ni fichier caché, ni bytecode).
   Ne remplacez jamais l'asset d'une release.
3. **Ouvrir une pull request** qui ajoute ou complète `plugins/<id>.yaml` (exemple dans la
   version anglaise). Mettez `version` et `sha256` entre guillemets. Une mise à jour ajoute
   une release à la fin, sans modifier les précédentes ; `id` et `repo` ne changent jamais. Si une permission est ajoutée ou
   élargie, indiquez `permissions_changed: true`. Cochez la déclaration de soumission.
4. **La CI vérifie** le format, la taille annoncée puis l'empreinte, le contenu de
   l'archive, le manifeste, la licence, le nom, les liens, puis exécute le plugin à blanc
   dans un bac à sable sans réseau, sous Python 3.11, 3.13 et 3.14. Le résumé montre le
   diff des permissions et du code. Un plugin qui lit les messages privés ou tous les
   canaux **et** utilise `http` est signalé comme sensible. Une CI verte ne vaut pas
   acceptation : chaque pull request est relue.
5. **Retrait et signalement** : issue « Request takedown » ou pull request pour retirer
   une version ; issue « Report a plugin » pour signaler un problème (sans détail sensible
   en public : utilisez le contact des pages légales de Meshloom). L'éditeur du service
   (nommé dans les pages légales de Meshloom) peut révoquer d'abord et instruire ensuite.

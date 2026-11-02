# Bonjour

Salue celui qui envoie `!hello` dans un canal public. C'est le modèle de plugin Meshloom :
remplacez ce texte par ce que fait votre plugin. Meshloom affiche ce README dans le Store et
à l'installation, donc restez bref et honnête.

- **Lit** les messages des canaux publics (ni les messages directs, ni les vôtres).
- **Envoie** uniquement une réponse, dans la conversation du message auquel il répond.
- **Ne conserve** rien et n'a aucun accès au réseau.

## Réglages

- **Salutation** : remplace la salutation par défaut ; `{name}` désigne le nom de l'expéditeur.
- **Clé d'API** : un exemple de champ secret. Ce plugin ne l'utilise pas ; supprimez le champ
  de `config.schema.json` si le vôtre n'a pas de secret.

# Hello

Greets whoever sends `!hello` in a public channel. This is the Meshloom plugin template:
replace this text with what your plugin does. Meshloom shows this README in the Store and
at install time, so keep it short and honest.

- **Reads** the messages of public channels (not direct messages, not your own).
- **Sends** only a reply, in the conversation of the message it answers.
- **Stores** nothing and has no network access.

## Settings

- **Greeting**: replaces the default greeting; `{name}` stands for the sender's name.
- **API key**: an example of a secret field. This plugin does not use it; delete the field
  from `config.schema.json` if yours has no secret.

"""Hello: the plugin template. Answers `!hello` in a public channel with a greeting.

Start from this file. The class named in `manifest.json` (`entry`) subclasses `Plugin`;
each hook listed in `hooks` has an `on_<hook>` method. Everything the plugin does to the
outside world goes through the host (`self.reply`, `self.storage`, `self.http`...), and
the host refuses what `permissions` does not declare. Developer guide:
docs/dev/plugins.md in the Meshloom repository ("SDK").
"""

from meshloom_plugin import HostError, Message, Plugin
from meshloom_plugin.sdk import error_code

COMMAND = "!hello"
# Runtime texts by language. They mirror i18n/<lang>.json, which the template's test keeps
# equal: the plugin sandbox allows no file or path module, so the code cannot read them.
TEXTS = {
    "en": {"greeting": "Hello {name}!", "status": "Greets !hello"},
    "fr": {"greeting": "Bonjour {name} !", "status": "Salue !hello"},
}


def texts_for(lang: str) -> dict[str, str]:
    """English, with the user's language on top (`fr`, or the `fr` of `fr-CA`)."""
    return {**TEXTS["en"], **TEXTS.get(lang.split("-")[0].lower(), {})}


class Hello(Plugin):
    def __init__(self) -> None:
        super().__init__()
        self.texts: dict[str, str] = {}

    async def on_start(self) -> None:
        # `self.config` and `self.lang` are filled by the host's `plugin.init`.
        self.texts = texts_for(self.lang)
        # A secret reaches the plugin in clear, here only. Never log or send it.
        configured = bool(self.config.get("api_key"))
        self.log("info", f"started, api key {'set' if configured else 'not set'}")
        await self.set_status(self.texts["status"])

    async def on_message(self, msg: Message) -> None:
        if msg.is_outgoing or msg.text.strip().lower() != COMMAND:
            return
        template = self.config.get("greeting") or self.texts["greeting"]
        text = template.replace("{name}", msg.sender_name or "")
        try:
            await self.reply(msg, self.fit(text))
        except HostError as exc:
            # The host refuses a send past its quotas or limits; that is not a crash.
            self.log("info", f"reply refused: {error_code(exc) or 'host error'}")

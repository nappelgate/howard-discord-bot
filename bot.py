"""Discord Chaos Bot — entry point.

Two flavors of mischief:
  1. Randomly raids occupied voice channels and plays a random sound.
  2. Randomly replies to messages with a condescending / cryptic one-liner.

Run with:  python bot.py   (after filling in .env)
"""

import logging
import os

import discord
from discord.ext import commands

from config import settings

# libopus is not in the system library path (installed without Homebrew).
# Load it explicitly from the venv so voice encoding works.
_opus_path = os.path.join(os.path.dirname(__file__), ".venv", "lib", "libopus.0.dylib")
if os.path.exists(_opus_path) and not discord.opus.is_loaded():
    discord.opus.load_opus(_opus_path)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("chaosbot")

# Intents: message_content and members are privileged intents — both must also
# be enabled in the Discord Developer Portal (Bot tab -> Privileged Gateway Intents).
intents = discord.Intents.default()
intents.message_content = True


class ChaosBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix=settings.command_prefix,
            intents=intents,
            help_command=None,
        )
        self._synced = False

    async def setup_hook(self) -> None:
        await self.load_extension("cogs.voice_chaos")
        await self.load_extension("cogs.message_chaos")
        await self.load_extension("cogs.music")
        log.info("Cogs loaded.")

    async def _sync_commands(self) -> None:
        """Push slash commands to each connected guild (instant), or fall back
        to a global sync (can take up to an hour to appear)."""
        guilds = list(self.guilds)
        if settings.guild_allowlist:
            guilds = [g for g in guilds if g.id in settings.guild_allowlist]
        if guilds:
            for guild in guilds:
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log.info("Synced %d slash command(s) to '%s'.", len(synced), guild.name)
        else:
            synced = await self.tree.sync()
            log.info("Synced %d global slash command(s) (may take up to 1h).", len(synced))

    async def on_ready(self) -> None:
        if not self._synced:
            try:
                await self._sync_commands()
            except Exception:
                log.exception("Slash command sync failed.")
            self._synced = True
        log.info("Logged in as %s (id=%s)", self.user, getattr(self.user, "id", "?"))
        log.info("Watching %d guild(s).", len(self.guilds))
        log.info(
            "Voice raids: %s | Random replies: %s",
            "ON" if settings.enable_voice else "off",
            "ON" if settings.enable_replies else "off",
        )


def main() -> None:
    if not settings.token:
        raise SystemExit(
            "DISCORD_TOKEN is not set.\n"
            "Copy .env.example to .env and paste your bot token in."
        )
    ChaosBot().run(settings.token, log_handler=None)


if __name__ == "__main__":
    main()

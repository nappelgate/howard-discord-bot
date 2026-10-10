"""One-shot script: clears all globally registered slash commands from this application.
Run once to remove any commands that were registered globally (e.g. by OpenClaw).
Howard re-registers only its own commands on next startup via guild-specific sync."""

import asyncio
import sys
sys.path.insert(0, ".")

import discord
from config import settings


async def main() -> None:
    client = discord.Client(intents=discord.Intents.default())
    await client.login(settings.token)
    app = await client.application_info()
    await client.http.bulk_upsert_global_commands(app.id, [])
    print(f"Cleared all global commands for application {app.name} ({app.id}).")
    await client.close()


asyncio.run(main())

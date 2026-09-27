"""Message chaos: randomly reply to user messages with a condescending or
cryptic one-liner, with a per-channel cooldown so it stays funny, not spammy."""

import json
import logging
import random
import time
from pathlib import Path

import discord
from discord.ext import commands

from config import settings
from responses import random_response
from cogs.voice_chaos import _pick_council_caption

log = logging.getLogger("chaosbot.message")

_COUNCIL_PATH = Path(__file__).parent.parent / "data" / "council.json"


def _load_council() -> list[dict]:
    try:
        return json.loads(_COUNCIL_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


class MessageChaos(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._last_reply: dict[int, float] = {}  # channel_id -> monotonic timestamp

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not settings.enable_replies:
            return
        if message.author.bot:
            return
        if message.guild is None:  # ignore DMs
            return
        if message.author.id == self.bot.user.id:
            return
        if settings.guild_allowlist and message.guild.id not in settings.guild_allowlist:
            return
        # Don't pounce on command invocations.
        if message.content.startswith(settings.command_prefix):
            return

        # Per-channel cooldown: only after an *actual* reply.
        now = time.monotonic()
        last = self._last_reply.get(message.channel.id, 0.0)
        if now - last < settings.reply_cooldown:
            return

        if random.random() >= settings.reply_chance:
            return

        self._last_reply[message.channel.id] = now
        try:
            council = _load_council()
            valid = [
                e for e in council
                if e.get("video_url")
                or (e.get("reply_text") and e.get("captions"))
                or (e.get("path") and Path(e["path"]).exists())
            ]

            # Build weighted pool.
            # - Keyword match in message text:          weight 20
            # - Targeted to this author (no kw match):  weight 5
            # - Untargeted (no kw match):               weight 1
            # - Targeted to someone else:               weight 0 (excluded)
            author_id = str(message.author.id)
            author_role_ids = {str(r.id) for r in getattr(message.author, "roles", [])}
            msg_lower = message.content.lower()

            def _weight(entry: dict) -> int:
                tu = entry.get("target_users", [])
                tr = entry.get("target_roles", [])
                is_targeted = bool(tu or tr)
                if is_targeted and author_id not in tu and not (author_role_ids & set(tr)):
                    return 0
                kws = entry.get("keywords", [])
                if kws and any(kw in msg_lower for kw in kws):
                    return 20
                return 5 if is_targeted else 1

            # Weighted pool: council entries × their weight, vs 20 slots for text.
            weighted_council = [e for e in valid for _ in range(_weight(e))]
            if weighted_council and random.random() < len(weighted_council) / (len(weighted_council) + 20):
                entry = random.choice(weighted_council)
                caption = _pick_council_caption(entry)
                if entry.get("video_url"):
                    content = f"{caption}\n{entry['video_url']}" if caption else entry["video_url"]
                    await message.reply(content=content, mention_author=False)
                    log.info(
                        "Council video reply in #%s: %s (by %s)",
                        getattr(message.channel, "name", "?"),
                        entry["video_url"],
                        entry.get("submitted_by", "?"),
                    )
                elif entry.get("reply_text"):
                    if caption:
                        await message.reply(content=caption, mention_author=False)
                        log.info(
                            "Council text reply in #%s: '%.60s' (by %s)",
                            getattr(message.channel, "name", "?"),
                            caption,
                            entry.get("submitted_by", "?"),
                        )
                else:
                    img_path = Path(entry["path"])
                    files = [discord.File(img_path)]
                    paired = entry.get("paired_path")
                    if paired and Path(paired).exists():
                        files.append(discord.File(Path(paired)))
                    await message.reply(content=caption, files=files, mention_author=False)
                    log.info(
                        "Council reply in #%s: %s (by %s)",
                        getattr(message.channel, "name", "?"),
                        img_path.name,
                        entry.get("submitted_by", "?"),
                    )
            else:
                line = random_response(message.author.display_name)
                await message.reply(line, mention_author=False)
                log.info("Replied in #%s: %s", getattr(message.channel, "name", "?"), line)
        except discord.HTTPException:
            log.exception("Failed to send reply.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MessageChaos(bot))

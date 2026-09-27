"""Voice chaos: at random intervals, join an occupied voice channel and play
a random sound, then leave. Also: gaslight users in text chat."""

import asyncio
import io
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from config import settings

log = logging.getLogger("chaosbot.voice")

SOUND_EXTS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".opus", ".webm"}
# Phase 2 scaffolding: video clip pool for the future video-raid feature.
# Nothing currently plays these — see _list_videos() below.
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi"}

# Allowlist of supported URL patterns.
_SUPPORTED_URL_RE = re.compile(
    r"^https?://(?:www\.|m\.|music\.)?(?:"
    r"youtube\.com/|youtu\.be/"           # YouTube
    r"|tiktok\.com/"                      # TikTok
    r"|vm\.tiktok\.com/"                  # TikTok short links
    r"|instagram\.com/"                   # Instagram Reels/posts
    r"|twitter\.com/|x\.com/"            # Twitter/X
    r"|reddit\.com/|v\.redd\.it/"        # Reddit videos
    r"|soundcloud\.com/"                  # SoundCloud
    r"|open\.spotify\.com/"              # Spotify
    r")",
    re.IGNORECASE,
)
_SPOTIFY_URL_RE = re.compile(r"^https?://open\.spotify\.com/", re.IGNORECASE)
_YTDLP_BIN = shutil.which("yt-dlp") or str(Path(__file__).parent.parent / ".venv" / "bin" / "yt-dlp")
_NODE_BIN = shutil.which("node") or "node"
# A timestamp, accurate down to the millisecond: "90", "90.5", "1:30",
# "1:30.250", or "01:02:03.4". The optional ".\d+" is the fractional second.
_TIME_RE = re.compile(r"^\d+(?::\d{1,2}){0,2}(?:\.\d+)?$")
# Don't let a single download balloon the sounds folder.
MAX_DOWNLOAD_SIZE = "75M"
DOWNLOAD_TIMEOUT = 300  # seconds

# EBU R128 loudness target — every clip gets normalized to this so none is
# wildly louder than another. -14 LUFS integrated, -1.5 dBTP true peak.
LOUDNORM = "I=-20:TP=-1.5:LRA=11"
# Per-format re-encode settings for the normalized output (keeps each file's
# original container/codec).
_CODEC_ARGS = {
    ".mp3": ["-c:a", "libmp3lame", "-q:a", "4"],
    ".wav": ["-c:a", "pcm_s16le"],
    ".flac": ["-c:a", "flac"],
    ".ogg": ["-c:a", "libvorbis", "-q:a", "5"],
    ".opus": ["-c:a", "libopus", "-b:a", "128k"],
    ".webm": ["-c:a", "libopus", "-b:a", "128k"],
    ".m4a": ["-c:a", "aac", "-b:a", "192k"],
}

# Persistent store for truthwarrior assignments and user submissions.
_TRUTH_PATH = Path(__file__).parent.parent / "data" / "truthwarrior.json"

# Built-in truth templates. {name} = display name, {pct} = seeded percentage.
_BUILTIN_TRUTHS: list[str] = [
    "{name} is {pct}% likely to be 8 billion ants in a trench coat",
    "{name} is {pct}% powered by unresolved childhood memories",
    "{name} is {pct}% legally considered a cryptid in 3 states",
    "{name} is {pct}% running a shadow economy based on Pokémon cards",
    "{name} is {pct}% statistically indistinguishable from a bowl of soup",
    "{name} is {pct}% haunted by a very specific type of goose",
    "{name} is {pct}% vibrating at a frequency that unsettles small animals",
    "{name} is {pct}% definitely not a time traveler (too suspicious)",
    "{name} is {pct}% biologically gravel at this point",
    "{name} is {pct}% fueled entirely by spite and ambient WiFi",
    "{name} is {pct}% a known quantity in the worst possible way",
    "{name} is {pct}% believed by experts to be load-bearing",
    "{name} is {pct}% just a little guy honestly",
    "{name} is {pct}% more ideas than neurons to house them",
    "{name} is {pct}% operating at theoretical maximum",
]

# Per-sound metadata: who added it and when.
_SOUNDS_META_PATH = Path(__file__).parent.parent / "data" / "sounds_meta.json"

# Council submissions: user-uploaded images (+ optional caption) used in random replies.
_COUNCIL_PATH = Path(__file__).parent.parent / "data" / "council.json"
_COUNCIL_DIR = Path(__file__).parent.parent / "data" / "council"
_COUNCIL_GUILD = "obama means family"

# Join-sound pool: one is picked at random whenever someone joins the target channel.
# trim_last_secs: download only the last N seconds; None = full clip.
_JOIN_SOUND_GUILD = "obama means family"
_JOIN_SOUND_CHANNEL = "chinesemaxxing"
# Sounds from the library played on join for a second channel.
_JOIN_SOUND_CHANNEL_2 = "retardmaxxing"
_JOIN_SOUND_CHANNEL_2_STEMS = {"darius closet stim", "darius communicating w mothership", "open na noor"}
_JOIN_SOUND_DIR = Path(__file__).parent.parent / "data" / "join_sounds"
# trim_start_secs / trim_end_secs: absolute timestamps (seconds). trim_last_secs: relative to end.
_JOIN_SOUNDS = [
    {"name": "js_0",   "url": "https://www.youtube.com/watch?v=u7Djn-G6uSk", "trim_last_secs": 3,    "trim_start_secs": None, "trim_end_secs": None},
    {"name": "js_1",   "url": "https://www.youtube.com/shorts/ox1Hybyy50Y",   "trim_last_secs": None, "trim_start_secs": None, "trim_end_secs": None},
    {"name": "js_2",   "url": "https://www.youtube.com/watch?v=ObmnHY7ThWs",  "trim_last_secs": None, "trim_start_secs": 1,    "trim_end_secs": None},
    {"name": "js_ric", "url": "https://www.youtube.com/watch?v=q-Rqdgna3Yw",  "trim_last_secs": None, "trim_start_secs": 4,    "trim_end_secs": 11},
]
# Personal join sounds: play for a specific user regardless of which channel they join.
# Keys are lowercase Discord username OR display name / nickname.
_PERSONAL_JOIN_SOUNDS: dict[str, str] = {
    "nickillzone8": "js_ric",
    "ric":          "js_ric",
}


def _pick_council_caption(entry: dict) -> str | None:
    """Return a random caption from an entry, handling both old and new formats."""
    captions = entry.get("captions")
    if captions:
        return random.choice(captions) or None
    # backwards compat: old entries stored a single "caption" string
    legacy = entry.get("caption", "")
    return legacy or None


def _council_load() -> list[dict]:
    try:
        return json.loads(_COUNCIL_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _council_save(data: list[dict]) -> None:
    _COUNCIL_PATH.parent.mkdir(parents=True, exist_ok=True)
    _COUNCIL_PATH.write_text(json.dumps(data, indent=2))


# Gaslight feature: fake conversations sent via webhook, impersonating VC users.
_GASLIGHT_PATH = Path(__file__).parent.parent / "data" / "gaslight.json"
_VOICES_DIR = Path(__file__).parent.parent / "data" / "voices"
_RVC_DIR = Path(__file__).parent.parent / "data" / "voices" / "rvc"
_TTS_VENV_PYTHON = Path(__file__).parent.parent / ".venv-tts" / "bin" / "python"
_TTS_SCRIPT = Path(__file__).parent.parent / "generate_tts.py"
_GASLIGHT_MIN_INTERVAL = 1200   # 20 minutes
_GASLIGHT_MAX_INTERVAL = 3600   # 60 minutes

# Each script is a list of {"s": "A"|"B"|..., "m": "message"}.
# Speaker letters map round-robin to actual users currently in VC.
_BUILTIN_SCRIPTS: list[list[dict]] = [
    [  # paranoia → anticlimactic
        {"s": "A", "m": "does anyone else hear that sound"},
        {"s": "B", "m": "what sound"},
        {"s": "A", "m": "like a low hum. its been going on for days"},
        {"s": "B", "m": "i dont hear anything"},
        {"s": "A", "m": "its there. its definitely there"},
        {"s": "A", "m": "wait nevermind i had my phone on vibrate"},
    ],
    [  # the confession
        {"s": "A", "m": "ok i need to tell someone this"},
        {"s": "B", "m": "what happened"},
        {"s": "A", "m": "so ive been putting my empty cereal boxes back in the cabinet"},
        {"s": "A", "m": "for like 6 months"},
        {"s": "A", "m": "there are 14 empty boxes in there right now"},
        {"s": "B", "m": "why"},
        {"s": "A", "m": "i dont know i just kept doing it"},
        {"s": "B", "m": "what do you want me to say"},
        {"s": "A", "m": "i dont know i just needed someone to know"},
    ],
    [  # hypochondria
        {"s": "A", "m": "guys i think im dying"},
        {"s": "B", "m": "what"},
        {"s": "A", "m": "my left eye has been twitching for 3 weeks straight"},
        {"s": "B", "m": "thats just stress"},
        {"s": "A", "m": "or its a brain tumor"},
        {"s": "B", "m": "its not a brain tumor"},
        {"s": "A", "m": "you dont know that"},
        {"s": "A", "m": "ok it stopped"},
        {"s": "A", "m": "nevermind"},
    ],
    [  # wrong chat
        {"s": "A", "m": "babe i miss you so much"},
        {"s": "A", "m": "i was thinking about you all day"},
        {"s": "A", "m": "can we talk tonight"},
        {"s": "A", "m": "wait"},
        {"s": "A", "m": "wrong chat"},
        {"s": "A", "m": "everyone pretend you didnt see that"},
        {"s": "B", "m": "lmaooo"},
    ],
    [  # bird conspiracy
        {"s": "A", "m": "wait have you guys ever noticed"},
        {"s": "B", "m": "noticed what"},
        {"s": "A", "m": "that birds are really quiet in the morning sometimes"},
        {"s": "B", "m": "yeah thats normal"},
        {"s": "A", "m": "but WHY"},
        {"s": "A", "m": "like who authorized that"},
        {"s": "B", "m": "what do you mean who authorized that"},
        {"s": "A", "m": "birds report to someone. i just know it"},
    ],
    [  # npc philosophy
        {"s": "A", "m": "what if we're all just npcs"},
        {"s": "B", "m": "we're not npcs"},
        {"s": "A", "m": "but how do you KNOW"},
        {"s": "B", "m": "because i have free will"},
        {"s": "A", "m": "thats exactly what an npc would say"},
        {"s": "A", "m": "do you ever make decisions or do you just kind of happen"},
        {"s": "B", "m": "what"},
        {"s": "A", "m": "like does anything feel chosen"},
        {"s": "B", "m": "good night"},
    ],
    [  # watered the fake plant
        {"s": "A", "m": "i have to tell you something"},
        {"s": "B", "m": "oh no"},
        {"s": "A", "m": "its not that bad"},
        {"s": "A", "m": "well its a little bad"},
        {"s": "B", "m": "ok what"},
        {"s": "A", "m": "you know the plant i have on my desk"},
        {"s": "B", "m": "yeah"},
        {"s": "A", "m": "its fake"},
        {"s": "A", "m": "its been fake the entire time and i just found out"},
        {"s": "A", "m": "i watered it for 8 months"},
        {"s": "B", "m": "WHAT"},
        {"s": "A", "m": "i cried a little"},
    ],
    [  # beige carpet
        {"s": "A", "m": "random question"},
        {"s": "B", "m": "yeah"},
        {"s": "A", "m": "if i was an item in a room what would i be"},
        {"s": "B", "m": "hmm like a couch maybe"},
        {"s": "A", "m": "no i think beige carpet"},
        {"s": "B", "m": "why"},
        {"s": "A", "m": "nonthreatening, everyone forgets im there, technically functional"},
        {"s": "B", "m": "thats the saddest thing ive ever heard"},
        {"s": "A", "m": "its actually very peaceful"},
        {"s": "B", "m": "are you ok"},
        {"s": "A", "m": "yeah why"},
    ],
    [  # dry chicken apology
        {"s": "A", "m": "hey i need to apologize about something"},
        {"s": "B", "m": "about what"},
        {"s": "A", "m": "i told your mom her roast chicken was amazing last christmas"},
        {"s": "B", "m": "yeah she still talks about that"},
        {"s": "A", "m": "it was not amazing"},
        {"s": "A", "m": "it was extremely dry"},
        {"s": "A", "m": "i dont know why i kept saying it was good"},
        {"s": "A", "m": "i just couldnt stop"},
        {"s": "B", "m": "why are you telling me this now"},
        {"s": "A", "m": "it haunts me"},
        {"s": "B", "m": "im going to kill you"},
    ],
    [  # manifestation
        {"s": "A", "m": "guys ive been manifesting a sports car for 6 months"},
        {"s": "B", "m": "oh yeah has it worked"},
        {"s": "A", "m": "kind of"},
        {"s": "A", "m": "i got a toy sports car in a happy meal"},
        {"s": "A", "m": "im choosing to see it as a sign"},
        {"s": "B", "m": "a sign of what"},
        {"s": "A", "m": "that i need to be more specific"},
    ],
    [  # sleep paralysis
        {"s": "A", "m": "ok so last night was terrifying"},
        {"s": "B", "m": "what happened"},
        {"s": "A", "m": "sleep paralysis again"},
        {"s": "A", "m": "there was a figure in the corner of my room"},
        {"s": "B", "m": "oh god"},
        {"s": "A", "m": "just standing there staring at me"},
        {"s": "A", "m": "for like 45 minutes"},
        {"s": "B", "m": "that sounds awful are you ok"},
        {"s": "A", "m": "yeah"},
        {"s": "A", "m": "it was my jacket on the chair"},
        {"s": "A", "m": "but it looked SO evil"},
    ],
    [  # 2am fish philosophy
        {"s": "A", "m": "what if fish dont notice water"},
        {"s": "B", "m": "like philosophically"},
        {"s": "A", "m": "yeah like is water just nothing to them"},
        {"s": "B", "m": "probably nothing i guess"},
        {"s": "A", "m": "thats so sad"},
        {"s": "A", "m": "do you think people notice when theyre happy or only after"},
        {"s": "B", "m": "bro its 2am"},
        {"s": "A", "m": "answer the question"},
        {"s": "B", "m": "only after i think"},
        {"s": "A", "m": "yeah"},
        {"s": "A", "m": "me too"},
    ],
]

# Guilds where all commands are available.
_FULL_ACCESS_GUILDS: frozenset[int] = frozenset({
    1174570163115405392,  # obama means family
    1493360064340426893,  # Arch Environmental
})

_LIMITED_ACCESS_GUILDS: frozenset[int] = frozenset({
    1250044273127194646,  # Hokazu's Stream
})


def _full_only(interaction: discord.Interaction) -> bool:
    """app_commands check: pass in full-access guilds, block elsewhere."""
    if interaction.guild_id in _FULL_ACCESS_GUILDS:
        return True
    raise app_commands.CheckFailure(
        "That command isn't available in this server."
    )


def _omf_only(interaction: discord.Interaction) -> bool:
    """app_commands check: obama means family only."""
    if interaction.guild_id == 1174570163115405392:
        return True
    raise app_commands.CheckFailure(
        "That command isn't available in this server."
    )


class GaslightModal(discord.ui.Modal, title="Submit Gaslight Script"):
    """Modal for submitting a multi-line gaslight conversation."""

    script = discord.ui.TextInput(
        label="Script (A: line, B: line, C: line…)",
        style=discord.TextStyle.paragraph,
        placeholder=(
            "A: hey can i tell you something\n"
            "B: what\n"
            "A: i've been a cryptid this whole time\n"
            "B: what does that even mean\n"
            "A: i cant explain it"
        ),
        max_length=2000,
    )

    def __init__(self, cog: "VoiceChaos") -> None:
        super().__init__()
        self._cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        lines = self._parse(self.script.value)
        if lines is None:
            await interaction.response.send_message(
                "Couldn't parse that. Each line needs a speaker letter and colon:\n"
                "`A: message` `B: message` etc. Need at least 2 lines.",
                ephemeral=True,
            )
            return
        data = self._cog._load_gaslight()
        data.setdefault("scripts", []).append(lines)
        self._cog._save_gaslight(data)
        await interaction.response.send_message(
            f"✅ Script added ({len(lines)} lines). Howard will deploy it.",
            ephemeral=True,
        )

    @staticmethod
    def _parse(text: str) -> list[dict] | None:
        lines: list[dict] = []
        for raw in text.strip().splitlines():
            raw = raw.strip()
            if not raw:
                continue
            m = re.match(r"^([A-Za-z])\s*:\s*(.+)$", raw)
            if not m:
                return None
            lines.append({"s": m.group(1).upper(), "m": m.group(2).strip()})
        return lines if len(lines) >= 2 else None


class AmendmentModal(discord.ui.Modal, title="Amend Council Entry"):
    """Modal for adding a caption to an existing council image."""

    caption = discord.ui.TextInput(
        label="New Caption",
        style=discord.TextStyle.paragraph,
        placeholder="Enter a caption to add to this image...",
        max_length=500,
    )

    def __init__(self, cog: "VoiceChaos", entry_id: str) -> None:
        super().__init__()
        self._cog = cog
        self._entry_id = entry_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        new_caption = self.caption.value.strip()
        if not new_caption:
            await interaction.response.send_message("Caption can't be empty.", ephemeral=True)
            return
        data = _council_load()
        for entry in data:
            if entry["id"] == self._entry_id:
                # migrate old single-caption format if needed
                captions = entry.get("captions") or (
                    [entry["caption"]] if entry.get("caption") else []
                )
                captions.append(new_caption)
                entry["captions"] = captions
                entry.pop("caption", None)
                total = len(captions)
                break
        else:
            await interaction.response.send_message(
                "Council entry not found — it may have been removed.", ephemeral=True
            )
            return
        _council_save(data)
        await interaction.response.send_message(
            f"✅ Caption added. This image now has **{total}** caption(s) on file.",
            ephemeral=True,
        )


class CouncilGroup(app_commands.Group):
    """Subgroup managing the council — Howard's random-reply content bank."""

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(name="council", description="Submit content for Howard to deploy as random replies.")
        self.bot = bot

    @staticmethod
    def _is_valid(e: dict) -> bool:
        return bool(
            e.get("video_url")
            or (e.get("reply_text") and e.get("captions"))
            or (e.get("path") and Path(e["path"]).exists())
        )

    @staticmethod
    def _entry_label(i: int, e: dict) -> str:
        kind = "[img]" if e.get("path") else ("[vid]" if e.get("video_url") else "[txt]")
        first = (e.get("captions") or [None])[0] or ""
        label = f"#{i} {kind} {e.get('submitted_by', '?')}"
        if first:
            label += f" — {first[:40]}"
        return label[:100]

    @staticmethod
    def _guild_check(interaction: discord.Interaction) -> bool:
        return bool(interaction.guild and interaction.guild.name == _COUNCIL_GUILD)

    # ── /howard council image ─────────────────────────────────────────────────

    @app_commands.command(name="image", description="Submit an image for Howard to deploy as a random reply.")
    @app_commands.describe(
        image="Primary image to submit.",
        image2="Optional paired image — always posted directly below the primary.",
        caption="Caption 1.",
        caption2="Caption 2.",
        caption3="Caption 3.",
        caption4="Caption 4.",
        caption5="Caption 5.",
        keywords="Comma-separated keywords. Prioritized when Howard replies to a matching message.",
    )
    async def image_cmd(
        self, interaction: discord.Interaction,
        image: discord.Attachment,
        image2: discord.Attachment | None = None,
        caption: str = "", caption2: str = "", caption3: str = "",
        caption4: str = "", caption5: str = "",
        keywords: str = "",
    ) -> None:
        if not self._guild_check(interaction):
            await interaction.response.send_message("Council is not available in this server.", ephemeral=True)
            return
        await self._save_image(
            interaction, image, image2=image2,
            captions=[caption, caption2, caption3, caption4, caption5],
            keywords=keywords,
        )

    async def _save_image(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment,
        image2: discord.Attachment | None,
        captions: list[str],
        keywords: str,
        target_user_ids: list[str] | None = None,
        target_role_ids: list[str] | None = None,
    ) -> None:
        if not image.content_type or not image.content_type.startswith("image/"):
            await interaction.response.send_message(
                "That doesn't look like an image. Please attach a PNG, JPG, GIF, or WEBP.", ephemeral=True,
            )
            return
        if image2 and (not image2.content_type or not image2.content_type.startswith("image/")):
            await interaction.response.send_message("The paired image doesn't look like an image.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        entry_id = f"{int(time.time())}_{interaction.user.id}"
        ext = Path(image.filename).suffix.lower() or ".png"
        dest = _COUNCIL_DIR / f"{entry_id}{ext}"
        _COUNCIL_DIR.mkdir(parents=True, exist_ok=True)

        try:
            await image.save(dest)
        except Exception as exc:
            log.exception("Failed to save council image.")
            await interaction.followup.send(f"❌ Failed to save image: {exc}", ephemeral=True)
            return

        paired_path: str | None = None
        if image2:
            ext2 = Path(image2.filename).suffix.lower() or ".png"
            dest2 = _COUNCIL_DIR / f"{entry_id}_pair{ext2}"
            try:
                await image2.save(dest2)
                paired_path = str(dest2)
            except Exception as exc:
                log.exception("Failed to save paired council image.")
                await interaction.followup.send(f"❌ Failed to save paired image: {exc}", ephemeral=True)
                return

        clean_captions = [c.strip() for c in captions if c.strip()]
        clean_keywords = [k.strip().lower() for k in keywords.split(",") if k.strip()]
        data = _council_load()
        entry: dict = {
            "id": entry_id,
            "path": str(dest),
            "captions": clean_captions,
            "keywords": clean_keywords,
            "target_users": target_user_ids or [],
            "target_roles": target_role_ids or [],
            "submitted_by": interaction.user.display_name,
            "submitted_at": int(time.time()),
        }
        if paired_path:
            entry["paired_path"] = paired_path
        data.append(entry)
        _council_save(data)
        cap_note = f" with {len(clean_captions)} caption(s)" if clean_captions else ""
        kw_note = f" and {len(clean_keywords)} keyword(s)" if clean_keywords else ""
        pair_note = " + paired image" if paired_path else ""
        log.info("Council image #%d from %s%s%s%s.", len(data), interaction.user.display_name, cap_note, kw_note, pair_note)
        await interaction.followup.send(
            f"✅ Image submitted{cap_note}{kw_note}{pair_note}. Council has **{len(data)}** submission(s).",
            ephemeral=True,
        )

    # ── /howard council video ─────────────────────────────────────────────────

    @app_commands.command(name="video", description="Submit a YouTube link for Howard to deploy as a random reply.")
    @app_commands.describe(
        url="YouTube URL to submit.",
        caption="Caption 1.",
        caption2="Caption 2.",
        caption3="Caption 3.",
        caption4="Caption 4.",
        caption5="Caption 5.",
        keywords="Comma-separated keywords. Prioritized when Howard replies to a matching message.",
    )
    async def video_cmd(
        self, interaction: discord.Interaction,
        url: str,
        caption: str = "", caption2: str = "", caption3: str = "",
        caption4: str = "", caption5: str = "",
        keywords: str = "",
    ) -> None:
        if not self._guild_check(interaction):
            await interaction.response.send_message("Council is not available in this server.", ephemeral=True)
            return
        if not re.match(r"^https?://(?:www\.|m\.)?(?:youtube\.com/|youtu\.be/)", url, re.IGNORECASE):
            await interaction.response.send_message("Please provide a valid YouTube URL.", ephemeral=True)
            return
        clean_captions = [c.strip() for c in [caption, caption2, caption3, caption4, caption5] if c.strip()]
        clean_keywords = [k.strip().lower() for k in keywords.split(",") if k.strip()]
        data = _council_load()
        entry_id = f"{int(time.time())}_{interaction.user.id}"
        entry: dict = {
            "id": entry_id,
            "video_url": url.strip(),
            "captions": clean_captions,
            "keywords": clean_keywords,
            "target_users": [],
            "target_roles": [],
            "submitted_by": interaction.user.display_name,
            "submitted_at": int(time.time()),
        }
        data.append(entry)
        _council_save(data)
        cap_note = f" with {len(clean_captions)} caption(s)" if clean_captions else ""
        kw_note = f" and {len(clean_keywords)} keyword(s)" if clean_keywords else ""
        log.info("Council video #%d from %s%s%s.", len(data), interaction.user.display_name, cap_note, kw_note)
        await interaction.response.send_message(
            f"✅ Video submitted{cap_note}{kw_note}. Council has **{len(data)}** submission(s).",
            ephemeral=True,
        )

    # ── /howard council text ──────────────────────────────────────────────────

    @app_commands.command(name="text", description="Submit a text reply for Howard to use at random.")
    @app_commands.describe(
        text="The reply Howard will send. Add variants for random selection.",
        text2="Alternate version 2.",
        text3="Alternate version 3.",
        text4="Alternate version 4.",
        text5="Alternate version 5.",
        keywords="Comma-separated keywords. Prioritized when Howard replies to a matching message.",
    )
    async def text_cmd(
        self, interaction: discord.Interaction,
        text: str,
        text2: str = "", text3: str = "", text4: str = "", text5: str = "",
        keywords: str = "",
    ) -> None:
        if not self._guild_check(interaction):
            await interaction.response.send_message("Council is not available in this server.", ephemeral=True)
            return
        variants = [t.strip() for t in [text, text2, text3, text4, text5] if t.strip()]
        clean_keywords = [k.strip().lower() for k in keywords.split(",") if k.strip()]
        data = _council_load()
        entry_id = f"{int(time.time())}_{interaction.user.id}"
        entry: dict = {
            "id": entry_id,
            "reply_text": True,
            "captions": variants,
            "keywords": clean_keywords,
            "target_users": [],
            "target_roles": [],
            "submitted_by": interaction.user.display_name,
            "submitted_at": int(time.time()),
        }
        data.append(entry)
        _council_save(data)
        var_note = f" ({len(variants)} variant(s))" if len(variants) > 1 else ""
        kw_note = f" with {len(clean_keywords)} keyword(s)" if clean_keywords else ""
        log.info("Council text #%d from %s%s.", len(data), interaction.user.display_name, kw_note)
        await interaction.response.send_message(
            f"✅ Text submitted{var_note}{kw_note}. Council has **{len(data)}** submission(s).",
            ephemeral=True,
        )

    # ── /howard council target ────────────────────────────────────────────────

    @app_commands.command(name="target", description="Submit council content with user/role targeting.")
    @app_commands.default_permissions()
    @app_commands.describe(
        image="Image to submit (provide one of: image, url, or text).",
        image2="Optional paired image.",
        url="YouTube URL to submit.",
        text="Text reply to submit.",
        caption="Caption 1.",
        caption2="Caption 2.",
        caption3="Caption 3.",
        caption4="Caption 4.",
        caption5="Caption 5.",
        keywords="Comma-separated keywords.",
        target_user="Deploy only to this user.",
        target_user2="Deploy only to this user.",
        target_user3="Deploy only to this user.",
        target_role="Deploy only to members of this role.",
        target_role2="Deploy only to members of this role.",
    )
    async def target_cmd(
        self, interaction: discord.Interaction,
        image: discord.Attachment | None = None,
        image2: discord.Attachment | None = None,
        url: str = "",
        text: str = "",
        caption: str = "", caption2: str = "", caption3: str = "",
        caption4: str = "", caption5: str = "",
        keywords: str = "",
        target_user: discord.Member | None = None,
        target_user2: discord.Member | None = None,
        target_user3: discord.Member | None = None,
        target_role: discord.Role | None = None,
        target_role2: discord.Role | None = None,
    ) -> None:
        if settings.owner_id is None or interaction.user.id != settings.owner_id:
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        if not self._guild_check(interaction):
            await interaction.response.send_message("Council is not available in this server.", ephemeral=True)
            return
        target_user_ids = [str(u.id) for u in [target_user, target_user2, target_user3] if u]
        target_role_ids = [str(r.id) for r in [target_role, target_role2] if r]

        if image:
            await self._save_image(
                interaction, image, image2=image2,
                captions=[caption, caption2, caption3, caption4, caption5],
                keywords=keywords,
                target_user_ids=target_user_ids,
                target_role_ids=target_role_ids,
            )
        elif url.strip():
            if not re.match(r"^https?://(?:www\.|m\.)?(?:youtube\.com/|youtu\.be/)", url, re.IGNORECASE):
                await interaction.response.send_message("Please provide a valid YouTube URL.", ephemeral=True)
                return
            clean_captions = [c.strip() for c in [caption, caption2, caption3, caption4, caption5] if c.strip()]
            clean_keywords = [k.strip().lower() for k in keywords.split(",") if k.strip()]
            data = _council_load()
            entry_id = f"{int(time.time())}_{interaction.user.id}"
            entry: dict = {
                "id": entry_id,
                "video_url": url.strip(),
                "captions": clean_captions,
                "keywords": clean_keywords,
                "target_users": target_user_ids,
                "target_roles": target_role_ids,
                "submitted_by": interaction.user.display_name,
                "submitted_at": int(time.time()),
            }
            data.append(entry)
            _council_save(data)
            cap_note = f" with {len(clean_captions)} caption(s)" if clean_captions else ""
            await interaction.response.send_message(
                f"✅ Targeted video submitted{cap_note}. Council has **{len(data)}** submission(s).",
                ephemeral=True,
            )
        elif text.strip():
            clean_keywords = [k.strip().lower() for k in keywords.split(",") if k.strip()]
            data = _council_load()
            entry_id = f"{int(time.time())}_{interaction.user.id}"
            entry: dict = {
                "id": entry_id,
                "reply_text": True,
                "captions": [text.strip()],
                "keywords": clean_keywords,
                "target_users": target_user_ids,
                "target_roles": target_role_ids,
                "submitted_by": interaction.user.display_name,
                "submitted_at": int(time.time()),
            }
            data.append(entry)
            _council_save(data)
            await interaction.response.send_message(
                f"✅ Targeted text submitted. Council has **{len(data)}** submission(s).",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "Please provide an image, a YouTube URL, or text.", ephemeral=True,
            )

    # ── /howard council invoke ────────────────────────────────────────────────

    @app_commands.command(name="invoke", description="Summon a submission from the council. Leave entry blank for random.")
    @app_commands.describe(entry="Specific submission to post (leave blank for random).")
    async def invoke_cmd(self, interaction: discord.Interaction, entry: str = "") -> None:
        is_owner = settings.owner_id is not None and interaction.user.id == settings.owner_id
        if entry and not is_owner:
            entry = ""
        if not self._guild_check(interaction):
            await interaction.response.send_message("The council is not available in this server.", ephemeral=True)
            return
        council = _council_load()
        valid = [e for e in council if self._is_valid(e)]
        if not valid:
            await interaction.response.send_message("The council has no submissions yet.", ephemeral=True)
            return
        if entry:
            chosen = next((e for e in valid if e["id"] == entry), None)
            if chosen is None:
                await interaction.response.send_message("Entry not found.", ephemeral=True)
                return
        else:
            chosen = random.choice(valid)
        caption = _pick_council_caption(chosen)
        if chosen.get("video_url"):
            content = f"{caption}\n{chosen['video_url']}" if caption else chosen["video_url"]
            await interaction.response.send_message(content=content)
        elif chosen.get("reply_text"):
            if not caption:
                await interaction.response.send_message("That entry has no text.", ephemeral=True)
                return
            await interaction.response.send_message(content=caption)
        else:
            files = [discord.File(Path(chosen["path"]))]
            paired = chosen.get("paired_path")
            if paired and Path(paired).exists():
                files.append(discord.File(Path(paired)))
            await interaction.response.send_message(content=caption, files=files)

    @invoke_cmd.autocomplete("entry")
    async def _invoke_entry_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if settings.owner_id is None or interaction.user.id != settings.owner_id:
            return []
        council = _council_load()
        choices = []
        for i, e in enumerate(council, 1):
            if not self._is_valid(e):
                continue
            label = self._entry_label(i, e)
            if current.lower() not in label.lower():
                continue
            choices.append(app_commands.Choice(name=label, value=e["id"]))
            if len(choices) >= 25:
                break
        return choices


class VoiceChaos(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._task: asyncio.Task | None = None
        self._gaslight_task: asyncio.Task | None = None
        self._start_time: float = time.monotonic()
        self._next_raid_at: float | None = None
        self._next_gaslight_at: float | None = None
        self._chill_until: float | None = None
        self._webhook_cache: dict[int, discord.Webhook] = {}
        self._amend_ctx = app_commands.ContextMenu(
            name="Amend Council Entry",
            callback=self._amend_council_ctx,
        )
        self.bot.tree.add_command(self._amend_ctx)

    async def _amend_council_ctx(
        self, interaction: discord.Interaction, message: discord.Message
    ) -> None:
        if message.author.id != self.bot.user.id:
            await interaction.response.send_message(
                "That's not one of Howard's messages.", ephemeral=True
            )
            return
        if not message.attachments:
            await interaction.response.send_message(
                "That message doesn't have an image.", ephemeral=True
            )
            return
        attachment_name = message.attachments[0].filename
        council = _council_load()
        entry = next(
            (e for e in council if Path(e["path"]).name == attachment_name),
            None,
        )
        if entry is None:
            await interaction.response.send_message(
                "Couldn't find this image in the council archive.", ephemeral=True
            )
            return
        await interaction.response.send_modal(AmendmentModal(self, entry["id"]))

    async def cog_load(self) -> None:
        if settings.enable_voice:
            self._task = asyncio.create_task(self._loop(), name="voice-chaos-loop")
        if settings.enable_gaslight:
            self._gaslight_task = asyncio.create_task(self._gaslight_loop(), name="gaslight-loop")
        else:
            log.info("Gaslight loop disabled (ENABLE_GASLIGHT=false).")
        asyncio.create_task(self._ensure_join_sounds(), name="join-sound-download")

    async def _ensure_join_sounds(self) -> None:
        """Download and cache any join-sound clips not yet on disk."""
        _JOIN_SOUND_DIR.mkdir(parents=True, exist_ok=True)
        loop = asyncio.get_event_loop()
        ffmpeg_dir = str(Path(settings.ffmpeg_path).parent)

        for entry in _JOIN_SOUNDS:
            dest = _JOIN_SOUND_DIR / f"{entry['name']}.mp3"
            if dest.exists():
                continue
            url = entry["url"]
            trim = entry["trim_last_secs"]
            log.info("Downloading join sound '%s' from %s …", entry["name"], url)

            trim_start = entry.get("trim_start_secs")
            trim_end_abs = entry.get("trim_end_secs")
            section_start: float = trim_start if trim_start is not None else 0.0
            section_end: str = f"{float(trim_end_abs):.3f}" if trim_end_abs is not None else "inf"

            if trim is not None:
                def _info(u=url):
                    result = subprocess.run(
                        [_YTDLP_BIN,
                         "--cookies-from-browser", "chrome",
                         "--js-runtimes", f"node:{_NODE_BIN}",
                         "-j", "--quiet", "--no-warnings", u],
                        capture_output=True, text=True, timeout=60,
                    )
                    return json.loads(result.stdout) if result.stdout.strip() else {}
                try:
                    info = await loop.run_in_executor(None, _info)
                except Exception:
                    log.exception("Could not fetch info for join sound '%s'.", entry["name"])
                    continue
                duration = info.get("duration") or 0
                section_start = max(section_start, duration - trim)

            if section_start > 0 or section_end != "inf" or trim is not None:
                section_args = [
                    "--download-sections", f"*{section_start:.3f}-{section_end}",
                    "--force-keyframes-at-cuts",
                ]
            else:
                section_args = []

            with tempfile.TemporaryDirectory() as tmp:
                args = [
                    _YTDLP_BIN,
                    "--cookies-from-browser", "chrome",
                    "--js-runtimes", f"node:{_NODE_BIN}",
                    "--no-playlist", "--no-progress",
                    "--ffmpeg-location", ffmpeg_dir,
                    "-x", "--audio-format", "mp3", "--audio-quality", "5",
                    *section_args,
                    "-o", str(Path(tmp) / "clip.%(ext)s"),
                    url,
                ]
                rc, output = await self._run(args, timeout=120)
                if rc != 0:
                    log.error("Join sound '%s' download failed: %s", entry["name"], output[-300:])
                    continue
                produced = sorted(Path(tmp).glob("*.mp3"))
                if not produced:
                    log.error("Join sound '%s': no mp3 produced.", entry["name"])
                    continue
                shutil.move(str(produced[0]), str(dest))

            await self._normalize_audio(dest)
            log.info("Join sound '%s' cached at %s.", entry["name"], dest)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self._amend_ctx.name, type=self._amend_ctx.type)
        if self._task:
            self._task.cancel()
        if self._gaslight_task:
            self._gaslight_task.cancel()

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        # Only fire on a join (not a move within the server or a leave).
        if member.bot:
            return
        if after.channel is None or after.channel == before.channel:
            return
        if member.guild.name != _JOIN_SOUND_GUILD:
            return
        if member.guild.voice_client is not None:
            return  # Don't interrupt an active raid or music stream.

        # Personal sound: check username and display name / nickname.
        member_names = {member.name.lower(), member.display_name.lower()}
        personal_key = next((k for k in _PERSONAL_JOIN_SOUNDS if k in member_names), None)
        if personal_key:
            sound_name = _PERSONAL_JOIN_SOUNDS[personal_key]
            personal_path = _JOIN_SOUND_DIR / f"{sound_name}.mp3"
            if personal_path.exists():
                asyncio.create_task(
                    self._play_clip(after.channel, personal_path),
                    name="join-sound-personal",
                )
            else:
                log.warning("Personal join sound '%s' not yet cached.", sound_name)
            return

        if after.channel.name == _JOIN_SOUND_CHANNEL:
            available = [
                _JOIN_SOUND_DIR / f"{e['name']}.mp3"
                for e in _JOIN_SOUNDS
                if (_JOIN_SOUND_DIR / f"{e['name']}.mp3").exists()
            ]
            if not available:
                log.warning("No join sounds cached yet; skipping.")
                return
            asyncio.create_task(
                self._play_clip(after.channel, random.choice(available)),
                name="join-sound-play",
            )

        elif after.channel.name == _JOIN_SOUND_CHANNEL_2:
            available2 = [
                p for p in settings.sounds_dir.glob("*.mp3")
                if p.stem.lower() in _JOIN_SOUND_CHANNEL_2_STEMS
            ]
            if not available2:
                log.warning("No library sounds found for %s join.", _JOIN_SOUND_CHANNEL_2)
                return
            asyncio.create_task(
                self._play_clip(after.channel, random.choice(available2)),
                name="join-sound-play-2",
            )

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.CheckFailure):
            msg = str(error) or "That command isn't available in this server."
            try:
                await interaction.response.send_message(msg, ephemeral=True)
            except discord.InteractionResponded:
                await interaction.followup.send(msg, ephemeral=True)
            return
        # Stale/duplicate interactions are expected when the bot restarts mid-session;
        # log them and swallow so one bad interaction can't crash the process.
        inner = getattr(error, "original", error)
        if isinstance(inner, discord.NotFound) and inner.code == 10062:
            log.warning("Stale interaction ignored (%s).", getattr(error, 'command', {}) and error.command.name)
            return
        raise error

    # --- helpers ---------------------------------------------------------

    def _list_sounds(self) -> list[Path]:
        d = settings.sounds_dir
        if not d.exists():
            return []
        return [
            p for p in d.iterdir()
            if p.is_file() and p.suffix.lower() in SOUND_EXTS
        ]

    def _list_videos(self) -> list[Path]:
        """Phase 2 scaffolding: list available video clips. Not wired into any
        raid flow yet — playback depends on the Phase 3 decision (unofficial
        Go-Live streaming), which is still pending explicit sign-off."""
        d = settings.video_clips_dir
        if not d.exists():
            return []
        return [
            p for p in d.iterdir()
            if p.is_file() and p.suffix.lower() in VIDEO_EXTS
        ]

    def _occupied_channels(self) -> list[discord.VoiceChannel]:
        found: list[discord.VoiceChannel] = []
        for guild in self.bot.guilds:
            if settings.guild_allowlist and guild.id not in settings.guild_allowlist:
                continue
            for ch in guild.voice_channels:
                if not any(not m.bot for m in ch.members):
                    continue
                perms = ch.permissions_for(guild.me)
                if perms.connect and perms.speak:
                    found.append(ch)
        return found

    # --- background loops ------------------------------------------------

    async def _loop(self) -> None:
        await self.bot.wait_until_ready()
        log.info(
            "Voice loop armed (every %.0f-%.0fs).",
            settings.voice_min_interval,
            settings.voice_max_interval,
        )
        while not self.bot.is_closed():
            delay = random.uniform(settings.voice_min_interval, settings.voice_max_interval)
            self._next_raid_at = time.monotonic() + delay
            log.info("Next raid in %.0fs.", delay)
            try:
                await asyncio.sleep(delay)
                self._next_raid_at = None
                if self._chill_until and time.monotonic() < self._chill_until:
                    remaining = self._chill_until - time.monotonic()
                    log.info("Chilling — skipping raid (%.0fs remaining).", remaining)
                    continue
                await self._raid()
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("Raid failed.")

    async def _gaslight_loop(self) -> None:
        await self.bot.wait_until_ready()
        log.info(
            "Gaslight loop armed (every %.0f-%.0fs).",
            _GASLIGHT_MIN_INTERVAL,
            _GASLIGHT_MAX_INTERVAL,
        )
        while not self.bot.is_closed():
            delay = random.uniform(_GASLIGHT_MIN_INTERVAL, _GASLIGHT_MAX_INTERVAL)
            self._next_gaslight_at = time.monotonic() + delay
            log.info("Next gaslight in %.0fs.", delay)
            try:
                await asyncio.sleep(delay)
                self._next_gaslight_at = None
                eligible = [g for g in self.bot.guilds if self._guild_has_humans(g)]
                if eligible:
                    await self._do_gaslight(random.choice(eligible))
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("Gaslight failed.")

    async def _raid(self) -> None:
        sounds = self._list_sounds()
        if not sounds:
            log.warning("No sounds in %s.", settings.sounds_dir)
            return
        channels = self._occupied_channels()
        if not channels:
            log.info("Nobody's in a voice channel. The chaos waits.")
            return
        channel = random.choice(channels)
        sound = random.choice(sounds)
        log.info("Raiding '%s' (%s) with '%s'.", channel.name, channel.guild.name, sound.name)
        await self._play_clip(channel, sound)

    async def _play_clip(self, channel: discord.VoiceChannel, sound: Path) -> bool:
        if channel.guild.voice_client is not None:
            log.info("Already busy in %s; skipping.", channel.guild.name)
            return False
        try:
            vc = await channel.connect()
        except Exception:
            log.exception("Could not connect to '%s'.", channel.name)
            return False

        try:
            audio = discord.FFmpegPCMAudio(str(sound), executable=settings.ffmpeg_path)
            source = discord.PCMVolumeTransformer(audio, volume=settings.voice_volume)
            done = asyncio.Event()

            def _after(err: Exception | None) -> None:
                if err:
                    log.error("Playback error: %s", err)
                self.bot.loop.call_soon_threadsafe(done.set)

            vc.play(source, after=_after)
            try:
                await asyncio.wait_for(done.wait(), timeout=120)
            except asyncio.TimeoutError:
                log.warning("Playback ran long; cutting it off.")
                vc.stop()
        finally:
            await vc.disconnect()
            log.info("Vanished from '%s'.", channel.name)
        return True

    def _resolve_sound(self, query: str) -> Path | None:
        query = query.strip()
        if not query:
            return None
        sounds = self._list_sounds()
        for p in sounds:
            if p.name == query:
                return p
        subs = [p for p in sounds if query.lower() in p.name.lower()]
        if not subs:
            return None
        exact_stem = [p for p in subs if p.stem.lower() == query.lower()]
        return exact_stem[0] if exact_stem else subs[0]

    # --- gaslight helpers ------------------------------------------------

    @staticmethod
    def _guild_has_humans(guild: discord.Guild) -> bool:
        return any(
            any(not m.bot for m in vc.members)
            for vc in guild.voice_channels
        )

    @staticmethod
    def _find_text_channel(guild: discord.Guild) -> discord.TextChannel | None:
        me = guild.me
        if guild.system_channel and guild.system_channel.permissions_for(me).send_messages:
            return guild.system_channel
        for ch in guild.text_channels:
            if "general" in ch.name.lower() and ch.permissions_for(me).send_messages:
                return ch
        for ch in guild.text_channels:
            if ch.permissions_for(me).send_messages:
                return ch
        return None

    async def _get_webhook(self, channel: discord.TextChannel) -> discord.Webhook | None:
        cached = self._webhook_cache.get(channel.id)
        if cached:
            return cached
        if not channel.permissions_for(channel.guild.me).manage_webhooks:
            log.info("No manage_webhooks in #%s — will use text fallback.", channel.name)
            return None
        try:
            hooks = await channel.webhooks()
            hook = next((h for h in hooks if h.name == "Howard"), None)
            if hook is None:
                hook = await channel.create_webhook(name="Howard")
            self._webhook_cache[channel.id] = hook
            return hook
        except discord.HTTPException as exc:
            log.warning("Webhook setup failed in #%s: %s", channel.name, exc)
            return None

    async def _do_gaslight(self, guild: discord.Guild) -> bool:
        humans: list[discord.Member] = []
        for vc in guild.voice_channels:
            members = [m for m in vc.members if not m.bot]
            if members:
                humans = members
                break
        if not humans:
            log.info("Gaslight skipped for %s: no humans in VC.", guild.name)
            return False

        channel = self._find_text_channel(guild)
        if channel is None:
            log.warning("No writable text channel in %s.", guild.name)
            return False

        hook = await self._get_webhook(channel)  # None = fall back to plain text

        data = self._load_gaslight()
        scripts = _BUILTIN_SCRIPTS + data.get("scripts", [])
        script = random.choice(scripts)

        letters: list[str] = []
        for line in script:
            if line["s"] not in letters:
                letters.append(line["s"])
        speaker_map = {letter: humans[i % len(humans)] for i, letter in enumerate(letters)}

        log.info(
            "Gaslighting #%s in %s (%d lines, %d speakers, webhook=%s).",
            channel.name, guild.name, len(script), len(letters), hook is not None,
        )
        for line in script:
            await asyncio.sleep(random.uniform(15, 120))
            member = speaker_map[line["s"]]
            if hook is not None:
                try:
                    await hook.send(
                        content=line["m"],
                        username=member.display_name,
                        avatar_url=member.display_avatar.url,
                    )
                    continue
                except discord.NotFound:
                    self._webhook_cache.pop(channel.id, None)
                    hook = None
                except discord.HTTPException as exc:
                    log.warning("Gaslight webhook send failed: %s", exc)
                    continue
            # Text fallback: bold name prefix so it reads like the user typed it
            try:
                await channel.send(f"**{member.display_name}:** {line['m']}")
            except discord.HTTPException as exc:
                log.warning("Gaslight text send failed: %s", exc)
        return True

    @staticmethod
    def _load_gaslight() -> dict:
        try:
            return json.loads(_GASLIGHT_PATH.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {"scripts": []}

    @staticmethod
    def _save_gaslight(data: dict) -> None:
        _GASLIGHT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _GASLIGHT_PATH.write_text(json.dumps(data, indent=2))

    # --- sound metadata --------------------------------------------------

    @staticmethod
    def _load_sounds_meta() -> dict:
        try:
            return json.loads(_SOUNDS_META_PATH.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _save_sounds_meta(data: dict) -> None:
        _SOUNDS_META_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SOUNDS_META_PATH.write_text(json.dumps(data, indent=2))

    def _record_sound_meta(self, path: Path, added_by: str) -> None:
        data = self._load_sounds_meta()
        data[path.name] = {"added_by": added_by, "added_at": int(time.time())}
        self._save_sounds_meta(data)

    # --- YouTube → sound download ----------------------------------------

    @staticmethod
    def _norm_time(value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        return value if _TIME_RE.match(value) else "INVALID"

    @staticmethod
    def _safe_name(name: str) -> str:
        name = re.sub(r"\.(mp3|wav|ogg|m4a|flac|opus|webm)$", "", name.strip(), flags=re.I)
        name = re.sub(r'[/\\\x00-\x1f<>:"|?*]', "", name)
        name = name.strip(". ")
        return name[:80]

    def _unique_dest(self, stem: str) -> Path:
        dest = settings.sounds_dir / f"{stem}.mp3"
        i = 2
        while dest.exists():
            dest = settings.sounds_dir / f"{stem} ({i}).mp3"
            i += 1
        return dest

    async def _run(self, args: list[str], timeout: float) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        return proc.returncode or 0, out.decode("utf-8", "replace")

    @staticmethod
    def _friendly_error(log_text: str) -> str:
        errs = [ln for ln in log_text.splitlines() if "ERROR" in ln]
        msg = (errs[-1] if errs else log_text.strip().splitlines()[-1:] or ["unknown error"])
        msg = msg if isinstance(msg, str) else msg[0]
        msg = msg.replace("ERROR:", "").strip()
        return msg[:300] or "the download failed"

    async def _download_sound(
        self, url: str, start: str | None, end: str | None, name: str | None
    ) -> Path:
        settings.sounds_dir.mkdir(parents=True, exist_ok=True)
        ffmpeg_dir = str(Path(settings.ffmpeg_path).parent)

        with tempfile.TemporaryDirectory() as tmp:
            out_tmpl = str(Path(tmp) / "%(title).80s.%(ext)s")
            args = [
                _YTDLP_BIN,
                "--cookies-from-browser", "chrome",
                "--js-runtimes", f"node:{_NODE_BIN}",
                "--no-playlist", "--no-progress",
                "--max-filesize", MAX_DOWNLOAD_SIZE,
                "--ffmpeg-location", ffmpeg_dir,
                "-x", "--audio-format", "mp3", "--audio-quality", "5",
                "-o", out_tmpl,
            ]
            if start or end:
                args += [
                    "--download-sections", f"*{start or '0'}-{end or 'inf'}",
                    "--force-keyframes-at-cuts",
                ]
            args.append(url)

            rc, output = await self._run(args, timeout=DOWNLOAD_TIMEOUT)
            if rc != 0:
                raise RuntimeError(self._friendly_error(output))

            produced = sorted(p for p in Path(tmp).iterdir() if p.suffix.lower() == ".mp3")
            if not produced:
                raise RuntimeError("download finished but no audio file came out")
            src = produced[0]

            stem = self._safe_name(name) if name else self._safe_name(src.stem)
            dest = self._unique_dest(stem or "sound")
            shutil.move(str(src), str(dest))
            log.info("Added sound '%s' from %s.", dest.name, url)

        await self._normalize_audio(dest)
        return dest

    async def _download_spotify(
        self, url: str, start: str | None, end: str | None, name: str | None
    ) -> Path:
        settings.sounds_dir.mkdir(parents=True, exist_ok=True)
        spotdl_bin = Path(sys.executable).parent / "spotdl"

        with tempfile.TemporaryDirectory() as tmp:
            args = [
                str(spotdl_bin), "download", url,
                "--output", str(Path(tmp) / "{title}"),
                "--format", "mp3",
                "--bitrate", "128k",
                "--ffmpeg", settings.ffmpeg_path,
            ]
            rc, output = await self._run(args, timeout=DOWNLOAD_TIMEOUT)
            if rc != 0:
                raise RuntimeError(self._friendly_error(output))

            produced = sorted(p for p in Path(tmp).iterdir() if p.suffix.lower() == ".mp3")
            if not produced:
                raise RuntimeError("spotdl finished but no audio file came out")
            src = produced[0]

            # Optional trim via ffmpeg.
            if start or end:
                trimmed = Path(tmp) / f"trimmed_{src.name}"
                trim_args = [settings.ffmpeg_path, "-y", "-i", str(src)]
                if start:
                    trim_args += ["-ss", start]
                if end:
                    trim_args += ["-to", end]
                trim_args += ["-acodec", "copy", str(trimmed)]
                rc2, out2 = await self._run(trim_args, timeout=60)
                if rc2 != 0:
                    raise RuntimeError(f"Trim failed: {out2[-200:]}")
                src = trimmed

            stem = self._safe_name(name) if name else self._safe_name(src.stem)
            dest = self._unique_dest(stem or "sound")
            shutil.move(str(src), str(dest))
            log.info("Added Spotify sound '%s' from %s.", dest.name, url)

        await self._normalize_audio(dest)
        return dest

    # --- loudness normalization ------------------------------------------

    @staticmethod
    def _parse_loudnorm_json(text: str) -> dict | None:
        start = text.rfind("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
        needed = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
        return data if all(k in data for k in needed) else None

    async def _normalize_audio(self, path: Path) -> bool:
        ff = settings.ffmpeg_path
        rc, out = await self._run(
            [ff, "-hide_banner", "-nostdin", "-i", str(path),
             "-af", f"loudnorm={LOUDNORM}:print_format=json", "-f", "null", "-"],
            timeout=180,
        )
        stats = self._parse_loudnorm_json(out) if rc == 0 else None
        if not stats:
            log.warning("Skipping normalize for '%s' (couldn't measure).", path.name)
            return False

        measured = (
            f"loudnorm={LOUDNORM}"
            f":measured_I={stats['input_i']}:measured_TP={stats['input_tp']}"
            f":measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}"
            f":offset={stats['target_offset']}:linear=true"
        )
        codec = _CODEC_ARGS.get(path.suffix.lower(), [])
        tmp = path.with_name(f"{path.stem}.norm{path.suffix}")
        rc, out = await self._run(
            [ff, "-hide_banner", "-nostdin", "-y", "-i", str(path),
             "-af", measured, "-ar", "48000", *codec, str(tmp)],
            timeout=180,
        )
        if rc != 0 or not tmp.exists():
            log.warning("Normalize pass 2 failed for '%s': %s", path.name, out[-200:])
            tmp.unlink(missing_ok=True)
            return False
        shutil.move(str(tmp), str(path))
        return True

    async def _trim_audio(self, path: Path, start: str | None, end: str | None) -> None:
        ff = settings.ffmpeg_path
        codec = _CODEC_ARGS.get(path.suffix.lower(), ["-c:a", "libmp3lame", "-q:a", "4"])
        tmp = path.with_name(f"{path.stem}.trim{path.suffix}")
        args = [ff, "-hide_banner", "-nostdin", "-y"]
        if start:
            args += ["-ss", start]
        if end:
            args += ["-to", end]
        args += ["-i", str(path), "-ar", "48000", *codec, str(tmp)]
        rc, out = await self._run(args, timeout=120)
        if rc != 0 or not tmp.exists():
            tmp.unlink(missing_ok=True)
            raise RuntimeError(out[-300:] or "ffmpeg trim failed")
        shutil.move(str(tmp), str(path))

    @staticmethod
    def _write_env(key: str, value: str) -> None:
        env_path = Path(__file__).parent.parent / ".env"
        text = env_path.read_text()
        new_line = f"{key}={value}"
        if re.search(rf"^{re.escape(key)}=", text, re.MULTILINE):
            text = re.sub(rf"^{re.escape(key)}=.*$", new_line, text, flags=re.MULTILINE)
        else:
            text = text.rstrip("\n") + f"\n{new_line}\n"
        env_path.write_text(text)

    @staticmethod
    def _load_truths() -> dict:
        try:
            return json.loads(_TRUTH_PATH.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {"assignments": {}, "submissions": []}

    @staticmethod
    def _save_truths(data: dict) -> None:
        _TRUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TRUTH_PATH.write_text(json.dumps(data, indent=2))

    def _get_truth(self, user_id: int, display_name: str) -> str:
        data = self._load_truths()
        uid = str(user_id)
        assignments = data.setdefault("assignments", {})
        if uid not in assignments:
            pool = _BUILTIN_TRUTHS + data.get("submissions", [])
            rng = random.Random(user_id)
            template = rng.choice(pool)
            pct = rng.randint(1, 99)
            assignments[uid] = {"t": template, "p": pct}
            self._save_truths(data)
        a = assignments[uid]
        return a["t"].format(name=display_name, pct=a["p"])

    # --- slash commands (/howard ...) ------------------------------------

    chaos = app_commands.Group(
        name="howard",
        description="Howard bot controls.",
        guild_only=True,
    )

    @chaos.command(name="play", description="Play a specific sound in your current voice channel.")
    @app_commands.describe(sound="Start typing to search your sound library.")
    async def play_cmd(self, interaction: discord.Interaction, sound: str) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member) or member.voice is None or member.voice.channel is None:
            await interaction.response.send_message(
                "Join a voice channel first, then run `/howard play`.", ephemeral=True
            )
            return

        match = self._resolve_sound(sound)
        if match is None:
            await interaction.response.send_message(
                f"Nothing matches `{sound}`. Try `/howard sounds`.", ephemeral=True
            )
            return

        channel = member.voice.channel
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.connect and perms.speak):
            await interaction.response.send_message(
                "I can't connect or speak in that channel.", ephemeral=True
            )
            return
        if interaction.guild.voice_client is not None:
            await interaction.response.send_message(
                "I'm already in a channel — give me a sec.", ephemeral=True
            )
            return

        await interaction.response.send_message(f"🔊 Playing **{match.name}**", ephemeral=True)
        log.info("On-demand: '%s' in '%s' (by %s).", match.name, channel.name, member)
        await self._play_clip(channel, match)

    @play_cmd.autocomplete("sound")
    async def _play_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        cur = current.lower()
        choices: list[app_commands.Choice[str]] = []
        for p in self._list_sounds():
            if cur in p.name.lower():
                choices.append(app_commands.Choice(name=p.name[:100], value=p.name[:100]))
            if len(choices) >= 25:
                break
        return choices

    @chaos.command(name="discography", description="List every sound in the library.")
    async def sounds_cmd(self, interaction: discord.Interaction) -> None:
        sounds = self._list_sounds()
        if not sounds:
            await interaction.response.send_message(
                "No sounds found. Feed me audio files.", ephemeral=True
            )
            return
        meta = self._load_sounds_meta()
        # Sort by added_at descending (newest first); unknowns go to the end.
        sounds.sort(key=lambda p: meta.get(p.name, {}).get("added_at", 0), reverse=True)
        col = max(len(p.name) for p in sounds) + 2
        header = f"{'SOUND':<{col}}{'ADDED BY':<20}TIMESTAMP (UTC)"
        divider = "-" * (col + 40)
        rows = [header, divider]
        for p in sounds:
            m = meta.get(p.name, {})
            added_by = m.get("added_by", "unknown")
            ts = m.get("added_at")
            timestamp = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if ts else "unknown"
            rows.append(f"{p.name:<{col}}{added_by:<20}{timestamp}")
        buf = io.BytesIO("\n".join(rows).encode())
        await interaction.response.send_message(
            f"**{len(sounds)} sound(s):**",
            file=discord.File(buf, filename="discography.txt"),
            ephemeral=True,
        )

    @chaos.command(
        name="raid",
        description="Summon howard via blood sacrifice (admin only).",
    )
    @app_commands.check(_full_only)
    async def raid_cmd(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Admins only, sorry.", ephemeral=True)
            return
        await interaction.response.send_message("👁️ Initiating unscheduled chaos…", ephemeral=True)
        await self._raid()

    @chaos.command(
        name="addsound",
        description="Force howard to commit piracy. YOU wouldn't download a car.",
    )
    @app_commands.describe(
        url="A YouTube, SoundCloud, Spotify, TikTok, or other supported link.",
        start="Optional start: seconds or M:SS, ms OK (e.g. 90.5 or 1:30.250). Omit = from the start.",
        end="Optional end: seconds or M:SS, ms OK (e.g. 120.75 or 2:00.5). Omit = to the end.",
        name="Optional name for the sound (defaults to the video title).",
    )
    @app_commands.check(_full_only)
    async def addsound_cmd(
        self,
        interaction: discord.Interaction,
        url: str,
        start: str | None = None,
        end: str | None = None,
        name: str | None = None,
    ) -> None:
        if not _SUPPORTED_URL_RE.match(url.strip()):
            await interaction.response.send_message(
                "Your link doesn't fucking work, dumbass.", ephemeral=True
            )
            return

        start_n = self._norm_time(start)
        end_n = self._norm_time(end)
        if start_n == "INVALID" or end_n == "INVALID":
            await interaction.response.send_message(
                "Couldn't read that time. Use seconds or `M:SS` — milliseconds OK, "
                "e.g. `90.5` or `1:30.250`.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            clean_url = url.strip()
            if _SPOTIFY_URL_RE.match(clean_url):
                dest = await self._download_spotify(clean_url, start_n, end_n, name)
            else:
                dest = await self._download_sound(clean_url, start_n, end_n, name)
        except asyncio.TimeoutError:
            await interaction.followup.send(
                "⏱️ That took too long and timed out. Try a shorter clip with `start`/`end`."
            )
            return
        except Exception as exc:
            log.exception("addsound failed for %s", url)
            reason = str(exc) or "something went wrong"
            await interaction.followup.send(f"❌ Couldn't add that sound: {reason}")
            return

        self._record_sound_meta(dest, interaction.user.display_name)
        await interaction.followup.send(
            f"✅ Added **{dest.name}** to the library. Play it with `/howard play`."
        )

    @chaos.command(
        name="normalize",
        description="ear rape conformism (admin only).",
    )
    @app_commands.check(_full_only)
    async def normalize_cmd(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("who the FUCK do you think you are?", ephemeral=True)
            return
        sounds = self._list_sounds()
        if not sounds:
            await interaction.response.send_message("No sounds to normalize.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        done = 0
        for p in sounds:
            try:
                if await self._normalize_audio(p):
                    done += 1
            except Exception:
                log.exception("Normalize failed for '%s'", p.name)
        await interaction.followup.send(
            f"🔉 Normalized **{done}/{len(sounds)}** sound(s) to {LOUDNORM.split(':')[0]}."
        )

    @chaos.command(name="removesound", description="Remove a sound from the library.")
    @app_commands.describe(sound="Sound to remove.")
    @app_commands.check(_full_only)
    async def removesound_cmd(self, interaction: discord.Interaction, sound: str) -> None:
        match = self._resolve_sound(sound)
        if match is None:
            await interaction.response.send_message(
                f"No sound matching `{sound}`.", ephemeral=True
            )
            return
        name = match.name
        match.unlink()
        log.info("Removed sound '%s' (by %s).", name, interaction.user)
        await interaction.response.send_message(f"🗑️ Removed **{name}**.", ephemeral=True)

    @removesound_cmd.autocomplete("sound")
    async def _removesound_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        cur = current.lower()
        choices: list[app_commands.Choice[str]] = []
        for p in self._list_sounds():
            if cur in p.name.lower():
                choices.append(app_commands.Choice(name=p.name[:100], value=p.name[:100]))
            if len(choices) >= 25:
                break
        return choices

    @chaos.command(
        name="editsound",
        description="Rename a sound and/or trim it to new start/end times.",
    )
    @app_commands.describe(
        sound="Sound to edit.",
        newname="New name (no extension). Leave blank to keep current.",
        start="New start time, ms OK (e.g. 0.5 or 1:30.250). Omit to keep from the beginning.",
        end="New end time, ms OK (e.g. 3.0 or 2:00.5). Omit to keep to the end.",
    )
    @app_commands.check(_full_only)
    async def editsound_cmd(
        self,
        interaction: discord.Interaction,
        sound: str,
        newname: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> None:
        match = self._resolve_sound(sound)
        if match is None:
            await interaction.response.send_message(
                f"No sound matching `{sound}`.", ephemeral=True
            )
            return

        start_n = self._norm_time(start)
        end_n = self._norm_time(end)
        if start_n == "INVALID" or end_n == "INVALID":
            await interaction.response.send_message(
                "Bad time format. Use `SS`, `M:SS`, or `H:MM:SS`, ms OK (e.g. `1:30.250`).",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        old_name = match.name
        needs_trim = bool(start_n or end_n)

        if needs_trim:
            try:
                await self._trim_audio(match, start_n, end_n)
                await self._normalize_audio(match)
            except Exception as exc:
                log.exception("editsound trim failed for '%s'", old_name)
                await interaction.followup.send(f"❌ Trim failed: {exc}")
                return

        if newname:
            safe = self._safe_name(newname)
            if safe:
                new_path = match.parent / f"{safe}{match.suffix}"
                if new_path.exists() and new_path != match:
                    new_path = self._unique_dest(safe).with_suffix(match.suffix)
                match = match.rename(new_path)

        parts = []
        if needs_trim:
            parts.append("trimmed")
        if match.name != old_name:
            parts.append(f"renamed to **{match.name}**")
        summary = " and ".join(parts) or "no changes made"
        await interaction.followup.send(f"✅ **{old_name}** — {summary}.")

    @editsound_cmd.autocomplete("sound")
    async def _editsound_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]] :
        cur = current.lower()
        choices: list[app_commands.Choice[str]] = []
        for p in self._list_sounds():
            if cur in p.name.lower():
                choices.append(app_commands.Choice(name=p.name[:100], value=p.name[:100]))
            if len(choices) >= 25:
                break
        return choices

    @chaos.command(
        name="chill",
        description="Pause Howard from joining voice channels for a set time.",
    )
    @app_commands.describe(duration="How long to pause raids (e.g. 30m, 2h, 1h30m). Omit to cancel an active chill.")
    @app_commands.check(_full_only)
    async def chill_cmd(self, interaction: discord.Interaction, duration: str = "") -> None:
        if not duration:
            self._chill_until = None
            await interaction.response.send_message("Chill cancelled — raids back on.", ephemeral=True)
            return

        pattern = re.compile(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$", re.IGNORECASE)
        if duration.strip().isdigit():
            seconds = int(duration.strip()) * 60
        else:
            match = pattern.match(duration.strip())
            if not match or not any(match.groups()):
                await interaction.response.send_message(
                    "Couldn't parse that time. Use formats like `30m`, `2h`, `1h30m`, or `90`.",
                    ephemeral=True,
                )
                return
            h, mi, s = (int(g or 0) for g in match.groups())
            seconds = h * 3600 + mi * 60 + s

        if seconds <= 0:
            await interaction.response.send_message("Time must be greater than zero.", ephemeral=True)
            return

        self._chill_until = time.monotonic() + seconds
        hh, rem = divmod(seconds, 3600)
        mm, ss = divmod(rem, 60)
        label = (f"{hh}h {mm}m {ss}s" if hh else (f"{mm}m {ss}s" if mm else f"{ss}s")).strip()
        log.info("Chill activated for %s.", label)
        await interaction.response.send_message(
            f"Howard is chilling for **{label}** — no voice raids until then.",
            ephemeral=True,
        )

    @chaos.command(
        name="vibecheck",
        description="Show uptime, sound count, howards upcoming guest appearance, and current settings.",
    )
    @app_commands.check(_full_only)
    async def vibecheck_cmd(self, interaction: discord.Interaction) -> None:
        elapsed = time.monotonic() - self._start_time
        h, rem = divmod(int(elapsed), 3600)
        m, s = divmod(rem, 60)
        uptime = f"{h}h {m}m {s}s"

        if self._chill_until and time.monotonic() < self._chill_until:
            chill_rem = self._chill_until - time.monotonic()
            ch, cr = divmod(int(chill_rem), 3600)
            cm, cs = divmod(cr, 60)
            chill_str = (f"{ch}h {cm}m {cs}s" if ch else f"{cm}m {cs}s").strip()
            raid_eta = f"chilling ({chill_str} left) 😴"
        elif self._next_raid_at is not None:
            eta = max(0.0, self._next_raid_at - time.monotonic())
            em, es = divmod(int(eta), 60)
            raid_eta = f"{em}m {es}s"
        else:
            raid_eta = "raiding right now 👁️"

        if self._next_gaslight_at is not None:
            geta = max(0.0, self._next_gaslight_at - time.monotonic())
            gm, gs = divmod(int(geta), 60)
            gaslight_eta = f"{gm}m {gs}s"
        else:
            gaslight_eta = "gaslighting right now 🪄"

        data = self._load_gaslight()
        custom_scripts = len(data.get("scripts", []))
        sounds = self._list_sounds()
        lines = [
            f"**⏱️ Uptime:** {uptime}",
            f"**🎵 Sounds:** {len(sounds)}",
            f"**💣 Next raid:** {raid_eta}",
            f"**🪄 Next gaslight:** {gaslight_eta}",
            f"**📜 Gaslight scripts:** {len(_BUILTIN_SCRIPTS)} built-in + {custom_scripts} custom",
            f"**🔊 Volume:** {settings.voice_volume:.2f}",
            f"**🎲 Reply chance:** {settings.reply_chance * 100:.0f}%",
            f"**🔁 Raid interval:** {settings.voice_min_interval:.0f}–{settings.voice_max_interval:.0f}s",
            f"**🎤 Voice raids:** {'ON' if settings.enable_voice else 'off'}",
            f"**💬 Random replies:** {'ON' if settings.enable_replies else 'off'}",
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @chaos.command(name="fuckoff", description="Send howard to the fucking shadow realm.")
    async def fuckoff_cmd(self, interaction: discord.Interaction) -> None:
        vc = interaction.guild.voice_client
        if vc is None:
            await interaction.response.send_message(
                "You hate me that much, huh", ephemeral=True
            )
            return
        vc.stop()
        await vc.disconnect(force=True)
        log.info("Ejected from voice by %s.", interaction.user)
        await interaction.response.send_message("Fine.", ephemeral=True)

    @chaos.command(
        name="volume",
        description="Set playback volume. 0.0 = silent, 1.0 = full, 2.0 = double. Admin only.",
    )
    @app_commands.describe(level="Volume between 0.0 and 2.0 (current default is 0.8).")
    @app_commands.check(_full_only)
    async def volume_cmd(self, interaction: discord.Interaction, level: float) -> None:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Admins only, sorry.", ephemeral=True)
            return
        if not 0.0 <= level <= 2.0:
            await interaction.response.send_message(
                "Level must be between 0.0 and 2.0.", ephemeral=True
            )
            return
        settings.voice_volume = level
        self._write_env("VOICE_VOLUME", str(level))
        await interaction.response.send_message(
            f"🔊 Volume set to **{level:.2f}** — takes effect on the next clip.",
            ephemeral=True,
        )

    @chaos.command(
        name="truthwarrior",
        description="Receive your permanent, scientifically determined truth.",
    )
    async def truthwarrior_cmd(self, interaction: discord.Interaction) -> None:
        truth = self._get_truth(interaction.user.id, interaction.user.display_name)
        await interaction.response.send_message(
            f"📊 **Truth Warrior Report**\n> {truth}"
        )

    @chaos.command(
        name="addtruth",
        description="Submit a community truth to the pool. Thank god you did your own research, you free-thinker, you.",
    )
    @app_commands.describe(
        truth="Use {name} and {pct} as placeholders. E.g.: {name} is {pct}% a sentient dishwasher",
    )
    @app_commands.check(_full_only)
    async def addtruth_cmd(self, interaction: discord.Interaction, truth: str) -> None:
        if "{name}" not in truth or "{pct}" not in truth:
            await interaction.response.send_message(
                "Your truth must contain `{name}` and `{pct}` — "
                "e.g. `{name} is {pct}% a sentient dishwasher`.",
                ephemeral=True,
            )
            return
        data = self._load_truths()
        data.setdefault("submissions", []).append(truth.strip())
        self._save_truths(data)
        rng = random.Random(interaction.user.id ^ 0xC0FFEE)
        preview = truth.format(name=interaction.user.display_name, pct=rng.randint(1, 99))
        await interaction.response.send_message(
            f"✅ The Trump administration thanks you for your bulletproof research and will base our next policy on your contribution! Preview:\n> {preview}", ephemeral=True
        )

    @chaos.command(
        name="gaslight",
        description="Manually trigger a gaslight event in this server's general channel.",
    )
    @app_commands.check(_full_only)
    async def gaslight_cmd(self, interaction: discord.Interaction) -> None:
        if not settings.enable_gaslight:
            await interaction.response.send_message("Gaslight is currently disabled.", ephemeral=True)
            return
        await interaction.response.send_message("🪄 deploying narrative…", ephemeral=True)
        ok = await self._do_gaslight(interaction.guild)
        if not ok:
            await interaction.followup.send(
                "Couldn't gaslight: no humans in VC, no writable channel, or missing webhook perms.",
                ephemeral=True,
            )

    @chaos.command(
        name="submitgas",
        description="Submit a custom gaslight script for Howard to deploy.",
    )
    @app_commands.check(_full_only)
    async def submitgas_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(GaslightModal(self))

    @chaos.command(
        name="say",
        description="Make Howard say something in your voice channel.",
    )
    @app_commands.describe(text="What Howard should say.")
    @app_commands.check(_omf_only)
    async def say_cmd(self, interaction: discord.Interaction, text: str) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member) or member.voice is None or member.voice.channel is None:
            await interaction.response.send_message(
                "Join a voice channel first.", ephemeral=True
            )
            return
        if interaction.guild.voice_client is not None:
            await interaction.response.send_message(
                "I'm already in a channel — give me a sec.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                tmp_path = Path(f.name)

            rvc_models = sorted(_RVC_DIR.glob("*.pth")) if _RVC_DIR.exists() else []
            xtts_voices = sorted(_VOICES_DIR.glob("*.wav")) if _VOICES_DIR.exists() else []
            use_tts_venv = _TTS_VENV_PYTHON.exists()

            if use_tts_venv and rvc_models:
                # Preferred: edge-tts words → RVC voice conversion
                cmd = [str(_TTS_VENV_PYTHON), str(_TTS_SCRIPT),
                       "--text", text,
                       "--rvc-model", rvc_models[0].stem,
                       "--out", str(tmp_path)]
            elif use_tts_venv and xtts_voices:
                # Fallback: XTTS voice cloning
                cmd = [str(_TTS_VENV_PYTHON), str(_TTS_SCRIPT),
                       "--text", text,
                       "--out", str(tmp_path)]
            else:
                cmd = None

            if cmd:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env={**os.environ, "COQUI_TOS_AGREED": "1"},
                )
                _, stderr = await proc.communicate()
                if proc.returncode != 0:
                    raise RuntimeError(stderr.decode()[-500:])
            else:
                import edge_tts
                communicate = edge_tts.Communicate(text, voice="en-US-ChristopherNeural")
                await communicate.save(str(tmp_path))

            await self._play_clip(member.voice.channel, tmp_path)
            tmp_path.unlink(missing_ok=True)
        except Exception as exc:
            log.exception("TTS failed for text: %s", text[:80])
            await interaction.followup.send(f"❌ TTS failed: {exc}", ephemeral=True)
            return

        await interaction.followup.send("✅ Said.", ephemeral=True)

    async def _autocomplete_voices(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        choices = []
        if _RVC_DIR.exists():
            choices += [app_commands.Choice(name=f"rvc:{p.stem}", value=f"rvc:{p.stem}")
                        for p in sorted(_RVC_DIR.glob("*.pth"))]
        if _VOICES_DIR.exists():
            choices += [app_commands.Choice(name=p.stem, value=p.stem)
                        for p in sorted(_VOICES_DIR.glob("*.wav"))]
        return [c for c in choices if current.lower() in c.name.lower()][:25]

    @chaos.command(
        name="ttsgen",
        description="Generate a TTS clip and add it to the sound library.",
    )
    @app_commands.describe(
        text="Text to synthesize.",
        filename="Output filename (no extension) in the sounds/ library.",
        voice="Voice to use — prefix rvc: for RVC models, plain name for XTTS profiles.",
    )
    @app_commands.autocomplete(voice=_autocomplete_voices)
    @app_commands.check(_full_only)
    async def ttsgen_cmd(
        self,
        interaction: discord.Interaction,
        text: str,
        filename: str,
        voice: str = "",
    ) -> None:
        if not _TTS_VENV_PYTHON.exists():
            await interaction.response.send_message(
                "❌ TTS venv not found (`discord-chaos-bot/.venv-tts`).", ephemeral=True
            )
            return

        rvc_models = sorted(_RVC_DIR.glob("*.pth")) if _RVC_DIR.exists() else []
        xtts_voices = sorted(_VOICES_DIR.glob("*.wav")) if _VOICES_DIR.exists() else []
        if not rvc_models and not xtts_voices:
            await interaction.response.send_message(
                "❌ No voice profiles found in `data/voices/`.", ephemeral=True
            )
            return

        safe = re.sub(r"[^\w\-]", "_", filename).strip("_") or "tts_clip"
        dest = settings.sounds_dir / f"{safe}.wav"
        if dest.exists():
            await interaction.response.send_message(
                f"❌ `{safe}.wav` already exists. Choose a different filename.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            cmd = [str(_TTS_VENV_PYTHON), str(_TTS_SCRIPT), "--text", text, "--out", str(dest)]
            if voice.startswith("rvc:"):
                cmd += ["--rvc-model", voice[4:]]
            elif voice:
                cmd += ["--voice", voice]
            elif rvc_models:
                cmd += ["--rvc-model", rvc_models[0].stem]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "COQUI_TOS_AGREED": "1"},
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(stderr.decode()[-500:])
        except Exception as exc:
            log.exception("ttsgen failed: %s", text[:80])
            await interaction.followup.send(f"❌ Generation failed: {exc}", ephemeral=True)
            return

        self._record_sound_meta(dest, interaction.user.display_name)
        voice_label = voice or (f"rvc:{rvc_models[0].stem}" if rvc_models else xtts_voices[0].stem)
        await interaction.followup.send(
            f"✅ Generated `{safe}.wav` using voice `{voice_label}` — now in the library.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    cog = VoiceChaos(bot)
    await bot.add_cog(cog)
    cog.chaos.add_command(CouncilGroup(bot))

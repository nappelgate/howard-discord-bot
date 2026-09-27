"""Music streaming under /howard music — attaches to the VoiceChaos howard group."""

import asyncio
import json
import logging
import re
import shutil
import subprocess
import time
import urllib.request
from collections import deque
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("chaosbot.music")

_SAVED_PATH = Path(__file__).parent.parent / "data" / "music_saved.json"

_SPOTIFY_RE = re.compile(r"https?://open\.spotify\.com/", re.IGNORECASE)
# Path to yt-dlp binary (venv or system)
_YTDLP_BIN = (
    shutil.which("yt-dlp")
    or str(Path(__file__).parent.parent / ".venv" / "bin" / "yt-dlp")
)
_NODE_BIN = shutil.which("node") or "node"


class Track:
    def __init__(self, stream_url: str, title: str, webpage_url: str,
                 requester: str, duration: int | None = None) -> None:
        self.stream_url = stream_url
        self.title = title
        self.webpage_url = webpage_url
        self.requester = requester
        self.duration = duration

    def duration_str(self) -> str:
        if not self.duration:
            return "?"
        m, s = divmod(int(self.duration), 60)
        h, m = divmod(m, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _spotify_query(url: str) -> str:
    """Resolve a Spotify URL to a search string via the public oEmbed API (no credentials)."""
    try:
        req = urllib.request.Request(
            f"https://open.spotify.com/oembed?url={url}",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()).get("title", "")
    except Exception as exc:
        log.warning("Spotify oEmbed failed: %s", exc)
        return ""


class MusicGroup(app_commands.Group):
    """Stateful subgroup that owns the per-guild queue and player logic."""

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(name="music", description="Stream music in voice.")
        self.bot = bot
        self._queues: dict[int, deque[Track]] = {}
        self._now_playing: dict[int, Track] = {}

    # ── saved URL library ────────────────────────────────────────────────────

    def _load_saved(self) -> list[dict]:
        try:
            return json.loads(_SAVED_PATH.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save_saved(self, data: list[dict]) -> None:
        _SAVED_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SAVED_PATH.write_text(json.dumps(data, indent=2))

    # ── helpers ──────────────────────────────────────────────────────────────

    def _queue(self, guild_id: int) -> deque[Track]:
        if guild_id not in self._queues:
            self._queues[guild_id] = deque()
        return self._queues[guild_id]

    async def _resolve(self, url: str) -> Track:
        loop = asyncio.get_event_loop()

        def _extract_meta(search_url: str) -> dict:
            result = subprocess.run(
                [_YTDLP_BIN,
                 "--cookies-from-browser", "chrome",
                 "--js-runtimes", f"node:{_NODE_BIN}",
                 "-J", "--quiet", "--no-warnings", search_url],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode not in (0, 1) or not result.stdout.strip():
                raise RuntimeError(result.stderr.strip() or "yt-dlp returned no metadata.")
            info = json.loads(result.stdout)
            if "entries" in info:
                info = info["entries"][0]
            return info

        if _SPOTIFY_RE.match(url):
            query = await loop.run_in_executor(None, _spotify_query, url)
            if not query:
                raise RuntimeError("Couldn't read that Spotify track — make sure it's a public URL.")
            search_url = f"ytsearch1:{query}"
        else:
            search_url = url

        info = await loop.run_in_executor(None, _extract_meta, search_url)
        return Track(
            stream_url=info.get("url", ""),
            title=info.get("title", "Unknown"),
            webpage_url=info.get("webpage_url", url),
            requester="",
            duration=info.get("duration"),
        )

    def _advance(self, guild_id: int, error: Exception | None = None) -> None:
        if error:
            log.error("Playback error in guild %d: %s", guild_id, error)
        asyncio.run_coroutine_threadsafe(self._start_next(guild_id), self.bot.loop)

    async def _start_next(self, guild_id: int) -> None:
        guild = self.bot.get_guild(guild_id)
        if not guild or not guild.voice_client:
            self._now_playing.pop(guild_id, None)
            return
        q = self._queue(guild_id)
        if not q:
            self._now_playing.pop(guild_id, None)
            await guild.voice_client.disconnect()
            return
        track = q.popleft()
        self._now_playing[guild_id] = track

        # Re-fetch stream URL + headers fresh (avoids stale pre-signed URLs).
        # Use the CLI with browser cookies so YouTube hands back a web-client URL
        # that ffmpeg can actually open — no pipe, no seekability issues.
        loop = asyncio.get_event_loop()
        def _fresh_stream() -> tuple[str, dict]:
            result = subprocess.run(
                [_YTDLP_BIN,
                 "--cookies-from-browser", "chrome",
                 "--js-runtimes", f"node:{_NODE_BIN}",
                 "-j", "--quiet", "--no-warnings", track.webpage_url],
                capture_output=True, text=True, timeout=60,
            )
            if not result.stdout.strip():
                raise RuntimeError(result.stderr.strip() or "yt-dlp returned no stream info")
            info = json.loads(result.stdout)
            if "entries" in info:
                info = info["entries"][0]
            return info["url"], info.get("http_headers", {})

        try:
            stream_url, headers = await loop.run_in_executor(None, _fresh_stream)
        except Exception as exc:
            log.error("Failed to fetch stream for '%s': %s — skipping.", track.title, exc)
            await self._start_next(guild_id)
            return

        header_str = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
        before_opts = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
        if header_str:
            before_opts = f"-headers '{header_str}' {before_opts}"

        source = discord.PCMVolumeTransformer(
            discord.FFmpegPCMAudio(stream_url, before_options=before_opts, options="-vn"),
            volume=0.5,
        )
        if not guild.voice_client:
            return
        guild.voice_client.play(source, after=lambda e: self._advance(guild_id, e))
        log.info("Now playing in guild %d: %s", guild_id, track.title)

    # ── commands ─────────────────────────────────────────────────────────────

    @app_commands.command(name="play", description="Stream a song from YouTube, SoundCloud, or Spotify.")
    @app_commands.describe(url="URL to play, or pick a saved link from the list.")
    async def play_cmd(self, interaction: discord.Interaction, url: str) -> None:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("Join a voice channel first.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            track = await self._resolve(url.strip())
        except Exception as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return
        track.requester = interaction.user.display_name

        vc_channel = interaction.user.voice.channel
        guild = interaction.guild
        vc = guild.voice_client

        if vc and vc.channel != vc_channel:
            await vc.move_to(vc_channel)
        elif not vc:
            vc = await vc_channel.connect()

        q = self._queue(guild.id)
        if vc.is_playing() or vc.is_paused():
            q.append(track)
            await interaction.followup.send(
                f"Queued: **{track.title}** `{track.duration_str()}` — #{len(q)} in queue",
                ephemeral=True,
            )
        else:
            q.appendleft(track)
            asyncio.ensure_future(self._start_next(guild.id))
            await interaction.followup.send(
                f"Now playing: **{track.title}** `{track.duration_str()}`",
                ephemeral=True,
            )

    @play_cmd.autocomplete("url")
    async def _play_url_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        saved = self._load_saved()
        choices = []
        for entry in saved:
            if current.lower() in entry["name"].lower() or current.lower() in entry["url"].lower():
                choices.append(app_commands.Choice(name=entry["name"], value=entry["url"]))
                if len(choices) >= 25:
                    break
        return choices

    @app_commands.command(name="skip", description="Skip the current track.")
    async def skip_cmd(self, interaction: discord.Interaction) -> None:
        vc = interaction.guild.voice_client if interaction.guild else None
        if not vc or not vc.is_playing():
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        vc.stop()
        await interaction.response.send_message("Skipped.", ephemeral=True)

    @app_commands.command(name="stop", description="Stop music and leave the voice channel.")
    async def stop_cmd(self, interaction: discord.Interaction) -> None:
        vc = interaction.guild.voice_client if interaction.guild else None
        if not vc:
            await interaction.response.send_message("Not in a voice channel.", ephemeral=True)
            return
        guild_id = interaction.guild.id
        self._queues.pop(guild_id, None)
        self._now_playing.pop(guild_id, None)
        await vc.disconnect()
        await interaction.response.send_message("Stopped.", ephemeral=True)

    @app_commands.command(name="queue", description="Show the current queue.")
    async def queue_cmd(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild.id if interaction.guild else 0
        now = self._now_playing.get(guild_id)
        q = list(self._queue(guild_id))
        lines: list[str] = []
        if now:
            lines.append(f"**Now playing:** {now.title} `{now.duration_str()}` — {now.requester}")
        for i, t in enumerate(q, 1):
            lines.append(f"{i}. {t.title} `{t.duration_str()}` — {t.requester}")
        if not lines:
            await interaction.response.send_message("Queue is empty.", ephemeral=True)
            return
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="volume", description="Adjust music volume (1–100).")
    @app_commands.describe(level="Volume level from 1 to 100.")
    async def volume_cmd(self, interaction: discord.Interaction, level: int) -> None:
        if not 1 <= level <= 100:
            await interaction.response.send_message("Volume must be between 1 and 100.", ephemeral=True)
            return
        vc = interaction.guild.voice_client if interaction.guild else None
        if not vc or not vc.is_playing():
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        if isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = level / 100
        await interaction.response.send_message(f"Volume set to **{level}%**.", ephemeral=True)

    @app_commands.command(name="save", description="Save a URL to the music library with a name.")
    @app_commands.describe(name="Name for this link.", url="URL to save.")
    async def save_cmd(self, interaction: discord.Interaction, name: str, url: str) -> None:
        saved = self._load_saved()
        existing = next((e for e in saved if e["name"].lower() == name.lower()), None)
        if existing:
            existing["url"] = url
            existing["updated_by"] = interaction.user.display_name
            existing["updated_at"] = int(time.time())
            self._save_saved(saved)
            await interaction.response.send_message(f"Updated **{name}**.", ephemeral=True)
        else:
            saved.append({
                "name": name,
                "url": url,
                "added_by": interaction.user.display_name,
                "added_at": int(time.time()),
            })
            self._save_saved(saved)
            await interaction.response.send_message(f"Saved **{name}**. Use `/howard music play` and type the name to find it.", ephemeral=True)

    @app_commands.command(name="saved", description="List all saved music links.")
    async def saved_cmd(self, interaction: discord.Interaction) -> None:
        saved = self._load_saved()
        if not saved:
            await interaction.response.send_message("No saved links yet. Use `/howard music save` to add one.", ephemeral=True)
            return
        lines = [f"{i}. **{e['name']}** — <{e['url']}> (saved by {e['added_by']})"
                 for i, e in enumerate(saved, 1)]
        text = "\n".join(lines)
        if len(text) > 1900:
            text = text[:1900] + "\n…"
        await interaction.response.send_message(text, ephemeral=True)

    @app_commands.command(name="remove", description="Remove a saved music link.")
    @app_commands.describe(name="Name of the saved link to remove.")
    async def remove_cmd(self, interaction: discord.Interaction, name: str) -> None:
        saved = self._load_saved()
        new = [e for e in saved if e["name"].lower() != name.lower()]
        if len(new) == len(saved):
            await interaction.response.send_message(f"No saved link named **{name}**.", ephemeral=True)
            return
        self._save_saved(new)
        await interaction.response.send_message(f"Removed **{name}**.", ephemeral=True)

    @remove_cmd.autocomplete("name")
    async def _remove_name_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=e["name"], value=e["name"])
            for e in self._load_saved()
            if current.lower() in e["name"].lower()
        ][:25]

    @app_commands.command(name="nowplaying", description="Show what's currently playing.")
    async def nowplaying_cmd(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild.id if interaction.guild else 0
        track = self._now_playing.get(guild_id)
        if not track:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"🎵 **{track.title}** `{track.duration_str()}` — requested by {track.requester}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    # Attach to the 'howard' group owned by VoiceChaos.
    # VoiceChaos must be loaded first (bot.py load order).
    voice_cog = bot.cogs.get("VoiceChaos")
    if not voice_cog:
        raise RuntimeError("VoiceChaos must be loaded before music")
    voice_cog.chaos.add_command(MusicGroup(bot))
    log.info("Music subgroup attached to /howard.")

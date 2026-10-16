"""Configuration loaded from environment / .env file."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
# ENV_FILE lets a second bot account run this same codebase with its own settings.
ENV_FILE = Path(os.getenv("ENV_FILE") or BASE_DIR / ".env")
load_dotenv(ENV_FILE)


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, ""))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on", "y"}


def _id_set(name: str) -> set[int]:
    raw = os.getenv(name, "")
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


class Settings:
    def __init__(self) -> None:
        self.token = os.getenv("DISCORD_TOKEN", "").strip()
        # discord.py skips whitespace after the prefix, so "!chaos raid" works.
        self.command_prefix = os.getenv("COMMAND_PREFIX", "!chaos").strip() or "!chaos"

        # Where sound files live (relative paths resolve against the project dir).
        sounds = os.getenv("SOUNDS_DIR", "sounds")
        self.sounds_dir = (BASE_DIR / sounds).resolve()

        # Where video clips live for the (future) video-raid feature.
        # Phase 2 scaffolding only: folder + picker exist, nothing plays them yet.
        video_clips = os.getenv("VIDEO_CLIPS_DIR", "video_clips")
        self.video_clips_dir = (BASE_DIR / video_clips).resolve()

        # ffmpeg binary. "ffmpeg" works if it's on PATH; otherwise an absolute path.
        self.ffmpeg_path = (os.getenv("FFMPEG_PATH", "ffmpeg").strip() or "ffmpeg")

        # Feature toggles.
        self.enable_voice = _bool("ENABLE_VOICE", True)
        self.enable_replies = _bool("ENABLE_REPLIES", True)
        self.enable_gaslight = _bool("ENABLE_GASLIGHT", True)

        # Voice raid timing (seconds). A random delay between each raid.
        self.voice_min_interval = _float("VOICE_MIN_INTERVAL", 300.0)
        self.voice_max_interval = _float("VOICE_MAX_INTERVAL", 1800.0)
        self.voice_volume = _float("VOICE_VOLUME", 0.8)

        # Message replies: chance per eligible message + per-channel cooldown.
        self.reply_chance = _float("REPLY_CHANCE", 0.05)
        self.reply_cooldown = _float("REPLY_COOLDOWN", 60.0)

        # Optional: restrict the bot to specific guild IDs (comma-separated).
        self.guild_allowlist = _id_set("GUILD_ALLOWLIST")

        # Top-level slash command group name (/howard by default).
        self.command_group = os.getenv("COMMAND_GROUP", "howard").strip() or "howard"

        # Background jobs that should only run on one bot when two share this codebase.
        self.enable_join_sounds = _bool("ENABLE_JOIN_SOUNDS", True)
        self.enable_tiktok_sync = _bool("ENABLE_TIKTOK_SYNC", True)
        self.enable_library_seed = _bool("ENABLE_LIBRARY_SEED", True)

        # message_content + members are privileged; a bot without them enabled
        # in the Developer Portal must set this false or login fails.
        self.privileged_intents = _bool("PRIVILEGED_INTENTS", True)

        # Tagalong: when the bot with FOLLOW_BOT_ID joins a voice channel,
        # this bot joins and raids it too with probability TAGALONG_CHANCE (0.0–1.0).
        _follow_raw = os.getenv("FOLLOW_BOT_ID", "").strip()
        self.follow_bot_id: int | None = int(_follow_raw) if _follow_raw.isdigit() else None
        self.tagalong_chance = _float("TAGALONG_CHANCE", 0.05)

        # Owner's Discord user ID — gates privileged commands.
        _owner_raw = os.getenv("OWNER_ID", "").strip()
        self.owner_id: int | None = int(_owner_raw) if _owner_raw.isdigit() else None

        # Riot Games API (ARAM Mayhem damage outliers). Disabled if no key is set.
        self.riot_api_key = os.getenv("RIOT_API_KEY", "").strip()
        self.enable_riot = _bool("ENABLE_RIOT", True) and bool(self.riot_api_key)
        self.riot_channel_name = os.getenv("RIOT_CHANNEL_NAME", "damage-charts").strip() or "damage-charts"
        self.riot_poll_seconds = max(60.0, _float("RIOT_POLL_SECONDS", 240.0))
        self.riot_queue_id = int(_float("RIOT_QUEUE_ID", 2400))  # ARAM: Mayhem
        self.riot_min_game_seconds = _float("RIOT_MIN_GAME_SECONDS", 300.0)
        self.riot_high_lobby = _float("RIOT_HIGH_LOBBY_RATIO", 1.75)
        self.riot_high_team = _float("RIOT_HIGH_TEAM_RATIO", 2.0)
        self.riot_low_lobby = _float("RIOT_LOW_LOBBY_RATIO", 0.35)
        self.riot_low_team = _float("RIOT_LOW_TEAM_RATIO", 0.30)


        # Sanity: keep min <= max.
        if self.voice_min_interval > self.voice_max_interval:
            self.voice_min_interval, self.voice_max_interval = (
                self.voice_max_interval,
                self.voice_min_interval,
            )


settings = Settings()

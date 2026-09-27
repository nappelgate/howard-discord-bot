"""Configuration loaded from environment / .env file."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


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

        # Owner's Discord user ID — gates privileged commands.
        _owner_raw = os.getenv("OWNER_ID", "").strip()
        self.owner_id: int | None = int(_owner_raw) if _owner_raw.isdigit() else None


        # Sanity: keep min <= max.
        if self.voice_min_interval > self.voice_max_interval:
            self.voice_min_interval, self.voice_max_interval = (
                self.voice_max_interval,
                self.voice_min_interval,
            )


settings = Settings()

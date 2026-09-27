# Discord Chaos Bot

A server gremlin that does two things:

1. **Voice raids** — at random intervals it slips into an *occupied* voice channel, plays a random clip from your sound library, and leaves.
2. **Random replies** — every so often it answers a message with a one-liner that's either smugly condescending or unhinged-oracle cryptic.

Built with [discord.py](https://discordpy.readthedocs.io/). Replies are pulled from a canned list (`responses.py`) — no LLM or internet required.

---

## 1. One-time setup

The setup script already ran and created a `.venv` with everything installed, plus a local `ffmpeg`. If you ever need to redo it from scratch:

```powershell
winget install Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 2. Create the bot + get a token

1. Go to <https://discord.com/developers/applications> → **New Application**.
2. **Bot** tab → **Reset Token** → copy it.
3. On that same Bot tab, scroll to **Privileged Gateway Intents** and turn ON:
   - **Message Content Intent**  ← required for the random replies
   - *(Server Members Intent is not required.)*
4. Copy `.env.example` to `.env` and paste the token into `DISCORD_TOKEN=`.

```powershell
Copy-Item .env.example .env
```

## 3. Invite it to your server

**OAuth2 → URL Generator**:
- Scopes: `bot` **and** `applications.commands`  ← the second one is required for `/` slash commands
- Bot permissions: **View Channels**, **Send Messages**, **Read Message History**, **Connect**, **Speak**

Open the generated URL, pick your server, authorize.

## 4. Add sounds

Drop short audio clips into the `sounds/` folder (`.mp3`, `.wav`, `.ogg`, `.m4a`, `.flac`, `.opus`, `.webm`). Filenames don't matter — one is chosen at random per raid.

## 5. Run it

```powershell
.\.venv\Scripts\python.exe bot.py
```

You should see `Logged in as ...`. Leave the terminal open; the bot runs until you close it (Ctrl+C to stop).

---

## Slash commands (in Discord)

The bot uses Discord slash commands. Type `/chaos` and the options pop up.

| Command | Who | What |
|---|---|---|
| `/chaos play <sound>` | anyone | Play a specific sound in **your** voice channel. The `sound` field autocompletes from your library. |
| `/chaos sounds` | anyone | List the audio files the bot can see |
| `/chaos raid` | admins | Trigger a random voice raid immediately (great for testing) |

To test voice instantly: join a voice channel yourself, then run `/chaos play` and pick a sound.

> **Commands not showing up?** The bot needs the `applications.commands` scope. Re-open the
> invite URL (it includes `scope=bot applications.commands`) to re-authorize — this won't kick
> the bot, it just grants the scope. Then fully reload your Discord client (Ctrl+R) and the
> commands sync within a few seconds. Commands are synced to each server on startup.

---

## Tuning (`.env`)

| Setting | Default | Meaning |
|---|---|---|
| `VOICE_MIN_INTERVAL` / `VOICE_MAX_INTERVAL` | 300 / 1800 | Random seconds between raids (5–30 min) |
| `VOICE_VOLUME` | 0.8 | Playback volume (0.0–2.0) |
| `REPLY_CHANCE` | 0.05 | Chance to reply to any given message (5%) |
| `REPLY_COOLDOWN` | 60 | Min seconds between replies in the same channel |
| `ENABLE_VOICE` / `ENABLE_REPLIES` | true | Turn either feature off |
| `GUILD_ALLOWLIST` | *(empty)* | Comma-separated server IDs to restrict the bot to |

Edit the actual one-liners in **`responses.py`** (two pools: `CONDESCENDING` and `CRYPTIC`).

---

## Project layout

```
discord-chaos-bot/
├─ bot.py                 # entry point, loads cogs
├─ config.py              # reads .env
├─ responses.py           # the canned one-liners
├─ cogs/
│  ├─ voice_chaos.py      # random voice-channel raids
│  └─ message_chaos.py    # random message replies
├─ sounds/                # your audio clips go here
├─ requirements.txt
└─ .env                   # your token + settings (not committed)
```

## Notes

- **Be a good neighbor.** Only run this on servers where everyone's in on the joke — randomly blasting audio and heckling people is funny among friends and obnoxious everywhere else.
- The bot ignores its own messages, other bots, and DMs.
- If voice playback fails with an ffmpeg error, double-check `FFMPEG_PATH` in `.env` points at a real `ffmpeg.exe`.
```

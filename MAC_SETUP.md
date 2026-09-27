# Runbook: set up the Discord Chaos Bot on this Mac

**This file is written for a Claude Code agent running on the Mac mini.** A human will
point you at it. Follow it top to bottom. It sets the bot up to run 24/7 natively via
`launchd` (no Docker).

## Goal
Get `bot.py` running as a background `launchd` service that auto-starts on login and
restarts on crash, then confirm it connected to Discord.

## Before you start — verify
1. You are inside the `discord-chaos-bot` folder. Confirm with `ls` — you should see
   `bot.py`, `cogs/`, `requirements.txt`, `setup_mac.sh`, and `.env`.
2. `.env` contains a real `DISCORD_TOKEN=...` value (not blank). If it's blank, **stop**
   and ask the human for the token before continuing.

## Do this
Run the setup script. It is idempotent (safe to re-run) and does everything:
installs Homebrew if missing, installs Python + ffmpeg, builds the venv, installs
dependencies, fixes the ffmpeg path in `.env`, and installs + starts the launchd service.

```bash
bash setup_mac.sh
```

- If you hit `bad interpreter` or a `\r` error, the file picked up Windows line endings in
  transit. Fix and retry:
  ```bash
  sed -i '' 's/\r$//' setup_mac.sh && bash setup_mac.sh
  ```
- Homebrew's installer (only if Homebrew isn't already present) will prompt for the Mac
  password and a Return keypress. Tell the human to type their password in the terminal
  when prompted — you can't supply it.

## Verify it worked
The script tails the log at the end. Confirm success yourself:
```bash
grep "Logged in as" chaosbot.log
launchctl list | grep com.chaosbot.discord
```
You want to see a `Logged in as howard#...` line and the service listed. If not, show the
human the last 30 lines of `chaosbot.log` and diagnose.

## Then tell the human these two things
1. **Enable Automatic Login** so the bot comes back after a power cut/reboot without anyone
   signing in: **System Settings → Users & Groups → Automatically log in as → (their user).**
   (This one toggle can't be safely scripted — they must click it.)
2. **Stop the old instance on the Windows PC.** Two copies of the same bot token will both
   connect and double every action. The Mac is now the home for it.

## Managing it later (for the human's reference)
| Task | Command (run in this folder) |
|---|---|
| View live logs | `tail -f chaosbot.log` |
| Restart the bot | `launchctl kickstart -k gui/$(id -u)/com.chaosbot.discord` |
| Stop the bot | `launchctl bootout gui/$(id -u)/com.chaosbot.discord` |
| Start it again | `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.chaosbot.discord.plist` |
| Add/replace sounds | Drop files into `./sounds` — picked up live, no restart |
| Update bot code | Copy new files over, then `launchctl kickstart -k gui/$(id -u)/com.chaosbot.discord` |

# Self-hosting the Chaos Bot (spare Linux PC or Mac)

Run it 24/7 on a spare machine using Docker. It bundles ffmpeg + Python, auto-restarts
on crash, and comes back after a reboot. The steps are the same on Linux and macOS — only
**installing Docker** and **starting it on boot** differ.

---

## 1. Install Docker

**Linux (Debian / Ubuntu / Raspberry Pi OS / most distros):**
```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER          # run docker without sudo (log out/in after)
sudo systemctl enable --now docker     # start Docker now AND on every boot
```

**Mac:** Install [Docker Desktop](https://www.docker.com/products/docker-desktop/). Open it
once, then **Settings → General → "Start Docker Desktop when you sign in."**

---

## 2. Copy the project to the server

Move the whole `discord-chaos-bot` folder over — **including `.env` (your token) and
`sounds/`**. Pick whatever's easiest:

- **USB drive** — just copy the folder.
- **scp from your Windows PC** (run in PowerShell):
  ```powershell
  scp -r "C:\Users\nappe\Desktop\claude\discord-chaos-bot" user@SERVER_IP:~/
  ```
- **git** — push to a *private* repo and `git clone` on the server. `.env` is gitignored,
  so copy that one file across separately.

The Windows `FFMPEG_PATH` sitting in your `.env` is fine — `docker-compose.yml` overrides it.

---

## 3. Build and start it

From inside the folder on the server:
```bash
docker compose up -d --build
```
`-d` = background. `restart: unless-stopped` = auto-restart on crash and after reboot
(as long as Docker starts on boot — step 1).

## 4. Verify
```bash
docker compose logs -f
```
Look for `Logged in as howard#9946` and `Synced 1 slash command(s)`. Press `Ctrl+C` to stop
*watching* the logs — the bot keeps running.

---

## Day-to-day

| Task | Command (run inside the folder) |
|---|---|
| View logs | `docker compose logs -f` |
| Add / replace sounds | Drop files in `./sounds` — picked up live, no restart |
| Restart the bot | `docker compose restart` |
| Update the code | copy new files over, then `docker compose up -d --build` |
| Stop it | `docker compose down` |

---

## ⚠️ Run only ONE copy

Discord lets two instances of the same bot connect at once, and they'll **both** respond —
doubled messages and fighting over voice. Once the server copy is confirmed working,
**stop the bot on your Windows PC** (close its terminal / don't run `bot.py` there anymore).

## Notes

- The bot makes only outbound connections — **no port forwarding or firewall rules needed.**
- Voice uses UDP; Docker's default networking handles that out of the box.
- **Unattended Mac:** also enable auto-login (System Settings → Users & Groups → Automatically
  log in) so Docker Desktop restarts after a power cut without someone logging in. A Linux box
  with Docker Engine doesn't need this.

---

## Alternative: native systemd (Linux, no Docker)

If you'd rather skip Docker on a Linux box:
```bash
sudo apt update && sudo apt install -y python3 python3-venv ffmpeg
cd ~/discord-chaos-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```
Edit `.env` and set `FFMPEG_PATH=ffmpeg` (remove the Windows path). Then create
`/etc/systemd/system/chaosbot.service` (replace `USER` with your username):
```ini
[Unit]
Description=Discord Chaos Bot
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/home/USER/discord-chaos-bot
ExecStart=/home/USER/discord-chaos-bot/.venv/bin/python bot.py
Restart=always
RestartSec=5
User=USER

[Install]
WantedBy=multi-user.target
```
Enable and watch it:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now chaosbot
journalctl -u chaosbot -f
```

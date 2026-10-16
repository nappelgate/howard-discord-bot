"""Riot Games integration: ARAM Mayhem damage outliers, tallies, and /rito ritoflex.

Polls linked players' recent ARAM Mayhem games. When a linked player is the
dramatic highest or lowest damage dealer in a game, posts a damage chart to the
configured channel and adds to their running tally.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from urllib.parse import quote

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from config import BASE_DIR, settings
from riot_logic import Outlier, Player, Thresholds, find_outliers

log = logging.getLogger("chaosbot.riot")

REGION_HOST = "https://americas.api.riotgames.com"  # NA routes through 'americas'
LINKS_FILE = BASE_DIR / "data" / "riot_links.json"
STATE_FILE = BASE_DIR / "data" / "riot_state.json"

BACKFILL_COUNT = 15  # Mayhem games imported silently when someone links
RECENT_IDS_COUNT = 5  # games checked per player per poll
STALE_POST_SECONDS = 3 * 3600  # never announce games older than this
MAX_RETRIES = 3

RITO_GROUP = "rito"
HIGH_LABEL = "carry"
LOW_LABEL = "glibdif"


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


class RiotError(Exception):
    """Raised with a user-presentable message."""


class RiotClient:
    def __init__(self, api_key: str) -> None:
        self._headers = {"X-Riot-Token": api_key, "User-Agent": "howard-bot/1.0"}
        self._session: aiohttp.ClientSession | None = None
        self._gate = asyncio.Semaphore(4)
        self._match_cache: dict[str, dict] = {}

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get(self, path: str, params: dict | None = None):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self._headers, timeout=aiohttp.ClientTimeout(total=15)
            )
        url = f"{REGION_HOST}{path}"
        for attempt in range(MAX_RETRIES):
            async with self._gate:
                try:
                    async with self._session.get(url, params=params) as resp:
                        if resp.status == 200:
                            return await resp.json()
                        if resp.status == 429:
                            wait = float(resp.headers.get("Retry-After", "5"))
                            log.warning("Riot rate limited; sleeping %.0fs", wait)
                            await asyncio.sleep(wait + 0.5)
                            continue
                        if resp.status == 404:
                            raise RiotError("Not found.")
                        if resp.status in (401, 403):
                            raise RiotError("Riot rejected the API key.")
                        if resp.status >= 500:
                            await asyncio.sleep(2 * (attempt + 1))
                            continue
                        raise RiotError(f"Riot API error {resp.status}.")
                except aiohttp.ClientError as exc:
                    log.warning("Riot request failed: %s", exc)
                    await asyncio.sleep(2 * (attempt + 1))
        raise RiotError("Riot API is unavailable right now.")

    async def puuid_for(self, name: str, tag: str) -> tuple[str, str, str]:
        try:
            data = await self._get(
                f"/riot/account/v1/accounts/by-riot-id/{quote(name, safe='')}/{quote(tag, safe='')}"
            )
        except RiotError as exc:
            if str(exc) == "Not found.":
                raise RiotError(f"No Riot account found for **{name}#{tag}**.") from exc
            raise
        return data["puuid"], data.get("gameName", name), data.get("tagLine", tag)

    async def match_ids(self, puuid: str, count: int) -> list[str]:
        return await self._get(
            f"/lol/match/v5/matches/by-puuid/{puuid}/ids",
            {"queue": settings.riot_queue_id, "start": 0, "count": count},
        )

    async def match(self, match_id: str) -> dict:
        cached = self._match_cache.get(match_id)
        if cached:
            return cached
        data = await self._get(f"/lol/match/v5/matches/{match_id}")
        if len(self._match_cache) > 200:
            self._match_cache.pop(next(iter(self._match_cache)))
        self._match_cache[match_id] = data
        return data


def _players(info: dict) -> list[Player]:
    out = []
    for p in info.get("participants", []):
        name = p.get("riotIdGameName") or p.get("summonerName") or "?"
        out.append(
            Player(
                puuid=p["puuid"],
                name=name,
                champion=p.get("championName", "?"),
                team=p.get("teamId", 0),
                damage=int(p.get("totalDamageDealtToChampions", 0)),
                kills=int(p.get("kills", 0)),
                deaths=int(p.get("deaths", 0)),
                assists=int(p.get("assists", 0)),
                win=bool(p.get("win", False)),
            )
        )
    return out


def _fmt_dmg(n: int) -> str:
    return f"{n / 1000:.1f}k"


class Riot(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.client = RiotClient(settings.riot_api_key)
        self.lock = asyncio.Lock()
        self.thresholds = Thresholds(
            high_lobby=settings.riot_high_lobby,
            high_team=settings.riot_high_team,
            low_lobby=settings.riot_low_lobby,
            low_team=settings.riot_low_team,
        )
        # links: {discord_id: {"puuid", "name", "tag"}}
        self.links: dict[str, dict] = _read_json(LINKS_FILE, {})
        # state: {"players": {puuid: {"seen": [...], "games", "damage", "kills", "deaths",
        #                              "assists", "wins", "high", "low"}}, "posted": [...]}
        self.state: dict = _read_json(STATE_FILE, {"players": {}, "posted": []})
        self.state.setdefault("players", {})
        self.state.setdefault("posted", [])

    # --- lifecycle ---------------------------------------------------------

    async def cog_load(self) -> None:
        self.poll.change_interval(seconds=settings.riot_poll_seconds)
        self.poll.start()

    async def cog_unload(self) -> None:
        self.poll.cancel()
        await self.client.close()

    def _save(self) -> None:
        _write_json(LINKS_FILE, self.links)
        _write_json(STATE_FILE, self.state)

    def _pstate(self, puuid: str) -> dict:
        return self.state["players"].setdefault(
            puuid,
            {"seen": [], "games": 0, "damage": 0, "kills": 0, "deaths": 0,
             "assists": 0, "wins": 0, "high": 0, "low": 0},
        )

    def _discord_id_for(self, puuid: str) -> str | None:
        for did, link in self.links.items():
            if link["puuid"] == puuid:
                return did
        return None

    # --- match processing --------------------------------------------------

    async def _process_match(self, match_id: str, *, announce: bool) -> None:
        """Apply one match to every linked player in it who hasn't seen it yet."""
        data = await self.client.match(match_id)
        info = data["info"]
        if info.get("queueId") != settings.riot_queue_id:
            log.info("Skipping %s: queueId %s", match_id, info.get("queueId"))
            for link in self.links.values():
                self._pstate(link["puuid"])["seen"].append(match_id)
            return
        if info.get("gameDuration", 0) < settings.riot_min_game_seconds:
            for link in self.links.values():
                self._pstate(link["puuid"])["seen"].append(match_id)
            return

        players = _players(info)
        linked = {p.puuid for p in players if self._discord_id_for(p.puuid)}
        fresh = {pu for pu in linked if match_id not in self._pstate(pu)["seen"]}
        if not fresh:
            return

        outliers = find_outliers(players, self.thresholds)
        by_puuid = {p.puuid: p for p in players}
        for pu in fresh:
            ps, p = self._pstate(pu), by_puuid[pu]
            ps["seen"] = (ps["seen"] + [match_id])[-300:]
            ps["games"] += 1
            ps["damage"] += p.damage
            ps["kills"] += p.kills
            ps["deaths"] += p.deaths
            ps["assists"] += p.assists
            ps["wins"] += int(p.win)

        counted = [o for o in outliers if o.puuid in fresh]
        for o in counted:
            self._pstate(o.puuid)["high" if o.kind == "high" else "low"] += 1

        ended_ms = info.get("gameEndTimestamp") or info.get("gameStartTimestamp", 0)
        recent = (time.time() - ended_ms / 1000) < STALE_POST_SECONDS
        if announce and counted and recent and match_id not in self.state["posted"]:
            self.state["posted"] = (self.state["posted"] + [match_id])[-500:]
            await self._post_chart(info, players, outliers)

    async def _post_chart(self, info: dict, players: list[Player], outliers: list[Outlier]) -> None:
        channel = self._find_channel()
        if channel is None:
            log.warning("No #%s channel found; skipping post.", settings.riot_channel_name)
            return

        flagged = {o.puuid: o for o in outliers}
        ranked = sorted(players, key=lambda p: p.damage, reverse=True)
        top = max(ranked[0].damage, 1)
        lines = []
        for p in ranked:
            bar = "█" * max(1, round(p.damage / top * 14))
            tag = ""
            if p.puuid in flagged:
                tag = "  ◄ CARRY" if flagged[p.puuid].kind == "high" else "  ◄ GLIBDIF"
            side = "B" if p.team == 100 else "R"
            lines.append(
                f"{side} {p.name[:12]:<12} {p.champion[:10]:<10} {_fmt_dmg(p.damage):>7} {bar}{tag}"
            )

        headline = []
        for o in outliers:
            p = next(pl for pl in players if pl.puuid == o.puuid)
            did = self._discord_id_for(o.puuid)
            who = f"<@{did}>" if did else f"**{p.name}**"
            if o.kind == "high":
                headline.append(
                    f"🔥 {who} ({p.champion}) dropped **{_fmt_dmg(o.damage)}**, "
                    f"{o.lobby_ratio:.1f}× the lobby average."
                )
            else:
                headline.append(
                    f"🧊 {who} ({p.champion}) managed **{_fmt_dmg(o.damage)}**, "
                    f"just {o.lobby_ratio:.0%} of the lobby average. Glibdif."
                )

        embed = discord.Embed(
            title="ARAM Mayhem damage chart",
            description="\n".join(headline) + "\n```\n" + "\n".join(lines) + "\n```",
            colour=discord.Colour.red() if any(o.kind == "low" for o in outliers) else discord.Colour.gold(),
        )
        mins = info.get("gameDuration", 0) // 60
        embed.set_footer(text=f"B = blue side, R = red side · {mins} min game")
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions(users=True))

    def _find_channel(self) -> discord.TextChannel | None:
        allow = settings.guild_allowlist
        for guild in self.bot.guilds:
            if allow and guild.id not in allow:
                continue
            ch = discord.utils.get(guild.text_channels, name=settings.riot_channel_name)
            if ch:
                return ch
        return None

    # --- polling -----------------------------------------------------------

    @tasks.loop(seconds=240)
    async def poll(self) -> None:
        async with self.lock:
            puuids = {link["puuid"] for link in self.links.values()}
            for puuid in puuids:
                try:
                    ids = await self.client.match_ids(puuid, RECENT_IDS_COUNT)
                except RiotError as exc:
                    log.warning("Match list failed: %s", exc)
                    continue
                seen = set(self._pstate(puuid)["seen"])
                for match_id in reversed(ids):  # oldest first
                    if match_id in seen:
                        continue
                    try:
                        await self._process_match(match_id, announce=True)
                    except RiotError as exc:
                        log.warning("Match %s failed: %s", match_id, exc)
                    except Exception:
                        log.exception("Processing %s crashed", match_id)
                self._save()

    @poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()

    @poll.error
    async def _poll_error(self, exc: BaseException) -> None:
        log.error("Riot poll loop died: %r; restarting.", exc)
        await asyncio.sleep(30)
        if not self.poll.is_running():
            self.poll.restart()

    # --- helpers for commands ----------------------------------------------

    async def link_account(self, discord_id: int, name: str, tag: str) -> tuple[str, str, int]:
        puuid, game_name, tag_line = await self.client.puuid_for(name, tag)
        async with self.lock:
            taken = self._discord_id_for(puuid)
            if taken and taken != str(discord_id):
                raise RiotError("That Riot account is already linked to someone else.")
            self.links[str(discord_id)] = {"puuid": puuid, "name": game_name, "tag": tag_line}
            self._pstate(puuid)
            imported = 0
            try:
                ids = await self.client.match_ids(puuid, BACKFILL_COUNT)
                for match_id in reversed(ids):
                    if match_id not in self._pstate(puuid)["seen"]:
                        await self._process_match(match_id, announce=False)
                        imported += 1
            except RiotError as exc:
                log.warning("Backfill failed for %s: %s", game_name, exc)
            self._save()
        return game_name, tag_line, imported

    def stats_for(self, discord_id: int) -> tuple[dict, dict] | None:
        link = self.links.get(str(discord_id))
        if not link:
            return None
        return link, self._pstate(link["puuid"])


# --- slash commands (/rito link, unlink, ritoflex, ritoboard) -----------

def _cog(interaction: discord.Interaction) -> Riot:
    return interaction.client.get_cog("Riot")  # type: ignore[return-value]


def _kda(ps: dict) -> str:
    if not ps["games"]:
        return "—"
    deaths = max(ps["deaths"], 1)
    return f"{(ps['kills'] + ps['assists']) / deaths:.2f}"


@app_commands.command(name="link", description="Link your Riot ID so Howard tracks your ARAM Mayhem games.")
@app_commands.describe(riot_id="Your Riot ID, like Name#NA1")
async def link_cmd(interaction: discord.Interaction, riot_id: str) -> None:
    if "#" not in riot_id:
        await interaction.response.send_message("Use the format `Name#TAG`.", ephemeral=True)
        return
    name, _, tag = riot_id.strip().rpartition("#")
    await interaction.response.defer(ephemeral=True)
    try:
        game_name, tag_line, imported = await _cog(interaction).link_account(
            interaction.user.id, name.strip(), tag.strip()
        )
    except RiotError as exc:
        await interaction.followup.send(str(exc), ephemeral=True)
        return
    await interaction.followup.send(
        f"Linked to **{game_name}#{tag_line}**. Imported {imported} recent ARAM Mayhem game(s).",
        ephemeral=True,
    )


@app_commands.command(name="unlink", description="Stop tracking your Riot account.")
async def unlink_cmd(interaction: discord.Interaction) -> None:
    cog = _cog(interaction)
    async with cog.lock:
        removed = cog.links.pop(str(interaction.user.id), None)
        cog._save()
    msg = "Unlinked. Your tally is kept if you link again." if removed else "You aren't linked."
    await interaction.response.send_message(msg, ephemeral=True)


@app_commands.command(name="ritoflex", description="Compare your ARAM Mayhem stats with someone else's.")
@app_commands.describe(user="Who to flex on (defaults to showing just your own stats).")
async def ritoflex_cmd(interaction: discord.Interaction, user: discord.Member | None = None) -> None:
    cog = _cog(interaction)
    mine = cog.stats_for(interaction.user.id)
    if not mine:
        await interaction.response.send_message("Link your account first with `/rito link`.", ephemeral=True)
        return
    theirs = cog.stats_for(user.id) if user else None
    if user and not theirs:
        await interaction.response.send_message(f"{user.display_name} hasn't linked a Riot account.", ephemeral=True)
        return

    def col(link: dict, ps: dict) -> dict[str, str]:
        g = ps["games"]
        return {
            "Riot ID": f"{link['name']}#{link['tag']}",
            "Games tracked": str(g),
            "Win rate": f"{ps['wins'] / g:.0%}" if g else "—",
            "Avg damage": _fmt_dmg(ps["damage"] // g) if g else "—",
            "KDA": _kda(ps),
            f"{HIGH_LABEL.title()} games 🔥": str(ps["high"]),
            f"{LOW_LABEL.title()} games 🧊": str(ps["low"]),
        }

    a = col(*mine)
    embed = discord.Embed(title="RitoFlex", colour=discord.Colour.blurple())
    if not theirs:
        embed.description = "\n".join(f"**{k}:** {v}" for k, v in a.items())
    else:
        b = col(*theirs)
        embed.add_field(name=interaction.user.display_name, value="\n".join(f"{k}: **{v}**" for k, v in a.items()))
        embed.add_field(name=user.display_name, value="\n".join(f"{k}: **{v}**" for k, v in b.items()))
        ma, mb = mine[1], theirs[1]
        verdict = []
        if ma["games"] and mb["games"]:
            da, db = ma["damage"] / ma["games"], mb["damage"] / mb["games"]
            lead = interaction.user if da >= db else user
            verdict.append(f"{lead.display_name} out-damages by {abs(da - db) / 1000:.1f}k per game.")
        if ma["low"] != mb["low"]:
            worse = interaction.user if ma["low"] > mb["low"] else user
            verdict.append(f"{worse.display_name} has more {LOW_LABEL}s.")
        if verdict:
            embed.set_footer(text=" ".join(verdict))
    await interaction.response.send_message(embed=embed)


@app_commands.command(name="ritoboard", description="Leaderboard of carry and glibdif counts.")
async def ritoboard_cmd(interaction: discord.Interaction) -> None:
    cog = _cog(interaction)
    rows = []
    for did, link in cog.links.items():
        ps = cog._pstate(link["puuid"])
        rows.append((did, ps))
    if not rows:
        await interaction.response.send_message("Nobody has linked yet.", ephemeral=True)
        return
    carries = sorted(rows, key=lambda r: r[1]["high"], reverse=True)[:10]
    glibs = sorted(rows, key=lambda r: r[1]["low"], reverse=True)[:10]

    def fmt(entries, key):
        return "\n".join(f"{i}. <@{d}> — **{ps[key]}**" for i, (d, ps) in enumerate(entries, 1) if ps[key]) or "None yet."

    embed = discord.Embed(title="RitoBoard", colour=discord.Colour.gold())
    embed.add_field(name=f"🔥 {HIGH_LABEL.title()}s", value=fmt(carries, "high"))
    embed.add_field(name=f"🧊 {LOW_LABEL.title()}s", value=fmt(glibs, "low"))
    await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())


_COMMANDS = (link_cmd, unlink_cmd, ritoflex_cmd, ritoboard_cmd)


async def setup(bot: commands.Bot) -> None:
    # /howard is already at Discord's 25-subcommand cap, so Riot gets its own group.
    group = app_commands.Group(
        name=RITO_GROUP, description="ARAM Mayhem damage stats.", guild_only=True
    )
    for cmd in _COMMANDS:
        group.add_command(cmd)
    await bot.add_cog(Riot(bot))
    bot.tree.add_command(group)
    log.info("Riot commands registered under /%s.", RITO_GROUP)


async def teardown(bot: commands.Bot) -> None:
    bot.tree.remove_command(RITO_GROUP)

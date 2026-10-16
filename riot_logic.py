"""Pure logic for spotting damage outliers in an ARAM Mayhem lobby.

No Discord or network code lives here so it can be unit tested on its own.

An outlier is the single highest- or lowest-damage player in a game whose
damage is dramatically far from the rest, judged against the other players in
the lobby (mean excluding themselves) and/or their own teammates.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Player:
    puuid: str
    name: str
    champion: str
    team: int
    damage: int  # totalDamageDealtToChampions
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    win: bool = False


@dataclass(frozen=True)
class Outlier:
    puuid: str
    kind: str  # "high" or "low"
    damage: int
    lobby_ratio: float  # damage / mean damage of the other players
    team_ratio: float  # damage / mean damage of their teammates


@dataclass(frozen=True)
class Thresholds:
    high_lobby: float = 1.75
    high_team: float = 2.0
    low_lobby: float = 0.35
    low_team: float = 0.30


def _mean(values: list[int]) -> float:
    return sum(values) / len(values) if values else 0.0


def ratios(player: Player, players: list[Player]) -> tuple[float, float]:
    """(lobby_ratio, team_ratio) for one player; 0.0 where the mean is zero."""
    others = [p.damage for p in players if p.puuid != player.puuid]
    mates = [p.damage for p in players if p.puuid != player.puuid and p.team == player.team]
    lobby_mean, team_mean = _mean(others), _mean(mates)
    lobby = player.damage / lobby_mean if lobby_mean else 0.0
    team = player.damage / team_mean if team_mean else 0.0
    return lobby, team


def find_outliers(players: list[Player], t: Thresholds = Thresholds()) -> list[Outlier]:
    """Return the high and/or low outlier of a game (at most one of each).

    A tie for highest or lowest damage is ambiguous, so it yields no outlier
    on that side.
    """
    if len(players) < 4:
        return []

    by_damage = sorted(players, key=lambda p: p.damage)
    result: list[Outlier] = []

    top, runner_up = by_damage[-1], by_damage[-2]
    if top.damage > runner_up.damage:
        lobby, team = ratios(top, players)
        if lobby >= t.high_lobby or team >= t.high_team:
            result.append(Outlier(top.puuid, "high", top.damage, lobby, team))

    bottom, next_up = by_damage[0], by_damage[1]
    if bottom.damage < next_up.damage:
        lobby, team = ratios(bottom, players)
        mates_exist = any(p.team == bottom.team and p.puuid != bottom.puuid for p in players)
        low_vs_lobby = lobby <= t.low_lobby
        low_vs_team = mates_exist and team <= t.low_team
        if low_vs_lobby or low_vs_team:
            result.append(Outlier(bottom.puuid, "low", bottom.damage, lobby, team))

    return result

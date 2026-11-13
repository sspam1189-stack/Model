# core/player_minutes.py
# Typical minutes for the lineup (injury) adjustment: the median of a player's
# last WINDOW games instead of his season average. One short night -- an early
# injury exit, a blowout, foul trouble -- pulls an average down and can drop a
# starter below the 28-minute tier (2026-11-12: a 6-minute game put a 29-minute
# starter at 26.3). The median ignores it.
#
# Minutes come from the per-game logs the props pipeline saves every day
# (data/player_cache/nba/basic_<season>_*.json). Only games before the run date
# are used, so a replay sees exactly what a live run on that date would.
import json
import os
import statistics

WINDOW = 10
MIN_GAMES = 5
_HERE = os.path.dirname(os.path.abspath(__file__))


def _season(date):
    y, m = int(date[:4]), int(date[4:6])
    start = y if m >= 10 else y - 1
    return f"{start}-{str((start + 1) % 100).zfill(2)}"


def _cache_dirs():
    roots = [os.environ.get("PYFULL_BACKFILL_REPO"), os.path.normpath(os.path.join(_HERE, ".."))]
    return [os.path.join(r, "data", "player_cache", "nba") for r in roots if r]


def median_minutes(date, window=WINDOW, min_games=MIN_GAMES):
    """{player name: median minutes over his last `window` games before `date`}
    for players with at least `min_games` games. Empty when no logs are found."""
    season, cutoff = _season(date), f"{date[:4]}-{date[4:6]}-{date[6:]}"
    rows = []
    for d in _cache_dirs():
        found = False
        for kind in ("Regular_Season", "PlayIn", "Playoffs"):
            p = os.path.join(d, f"basic_{season}_{kind}.json")
            if not os.path.exists(p):
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    rows += json.load(f) or []
                found = True
            except Exception:
                pass
        if found:
            break
    games = {}
    for r in rows:
        try:
            m, gd = float(r.get("min") or 0), str(r.get("game_date") or "")
        except (TypeError, ValueError):
            continue
        if m > 0 and gd and gd < cutoff and r.get("player_name"):
            games.setdefault(r["player_name"], []).append((gd, m))
    out = {}
    for name, g in games.items():
        if len(g) >= min_games:
            g.sort()
            out[name] = round(statistics.median(m for _, m in g[-window:]), 1)
    return out


def apply_median_minutes(player_adv, date):
    """Copy of player_adv with each player's "min" replaced by his median
    minutes where known. Returns (player_adv, number changed)."""
    if not player_adv:
        return player_adv, 0
    med = median_minutes(date)
    if not med:
        return player_adv, 0
    out, n = {}, 0
    for name, p in player_adv.items():
        m = med.get(name)
        if m is not None and isinstance(p, dict):
            if abs(m - float(p.get("min") or 0)) >= 0.05:
                n += 1
            p = {**p, "min": m}
        out[name] = p
    return out, n

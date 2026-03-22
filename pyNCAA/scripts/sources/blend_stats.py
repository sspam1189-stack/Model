# scripts/sources/blend_stats.py
# ────────────────────────────────────────────────────────────────────────────
# Blends multiple stat windows into one stats object per game.
#
# Why: season averages miss recent form (streaks, injuries, trades).
#      Home/away splits miss location-specific performance.
#      Blending captures what matters RIGHT NOW for each team in each game.
#
# Pipeline:
#   1. blend_base(season, last10, recent_weight)  -> base blended stats (team-level)
#      This goes through lineup_adjust and b2b_adjust as before.
#
#   2. blend_for_game(base_stats, home_splits, away_splits, home_team, away_team, loc_weight)
#      -> game-specific stats with home/away adjustments applied per-team.
#      Called in the game loop, just before analyze_game.
# ────────────────────────────────────────────────────────────────────────────

import math

STAT_KEYS = ["OFF", "DEF", "TS", "TO", "ORR", "PACE"]


def _blend_team(base, overlay, weight):
    if not overlay:
        return base
    out = dict(base)
    for k in STAT_KEYS:
        base_val = base.get(k)
        overlay_val = overlay.get(k)
        if isinstance(base_val, (int, float)) and math.isfinite(base_val) and \
           isinstance(overlay_val, (int, float)) and math.isfinite(overlay_val):
            out[k] = base_val * (1 - weight) + overlay_val * weight
    return out


# -- Step 1: Season + Last 10 blend --
# Creates a single stats object with recent form blended in.
# recent_weight = 0 -> pure season. recent_weight = 1 -> pure last 10.
# Default 0.35: research-backed starting point for NBA recent form.
# Min GP for last10: if a team played < 8 of their last 10, trust season more.

def blend_base(season, last10, recent_weight=0.35):
    if not last10 or not len(last10):
        print("  [blend] No last-10 data -- using season only")
        return dict(season)

    blended = {}
    blend_count = 0

    for team, s in season.items():
        r = last10.get(team)
        if not r or r.get("GP", 0) < 5:
            # Not enough recent games -- use season only
            blended[team] = dict(s)
            continue

        # Scale weight by how many of last 10 were actually played
        # If GP=10, use full weight. If GP=6, reduce proportionally.
        gp_factor = min(r["GP"] / 10, 1)
        effective_weight = recent_weight * gp_factor

        blended[team] = _blend_team(s, r, effective_weight)
        blended[team]["GP"] = s.get("GP")  # keep season GP for min-games check
        blend_count += 1

    print(f"  [blend] Blended {blend_count}/{len(season)} teams (recentWeight={recent_weight})")
    return blended


# -- Step 2: Per-game home/away adjustment --
# Takes the base-blended stats and adjusts the home team toward their home
# performance and the away team toward their road performance.
# Returns a shallow copy with only the two game teams modified.

def blend_for_game(base_stats, home_splits, away_splits, home_team, away_team, loc_weight=0.25):
    if (not home_splits and not away_splits) or loc_weight <= 0:
        return base_stats

    game_stats = dict(base_stats)

    # Home team: blend toward their home splits
    if home_splits and home_splits.get(home_team) and (home_splits[home_team].get("GP", 0) >= 10):
        game_stats[home_team] = _blend_team(
            base_stats[home_team],
            home_splits[home_team],
            loc_weight
        )
        game_stats[home_team]["GP"] = (base_stats.get(home_team) or {}).get("GP")

    # Away team: blend toward their road splits
    if away_splits and away_splits.get(away_team) and (away_splits[away_team].get("GP", 0) >= 10):
        game_stats[away_team] = _blend_team(
            base_stats[away_team],
            away_splits[away_team],
            loc_weight
        )
        game_stats[away_team]["GP"] = (base_stats.get(away_team) or {}).get("GP")

    return game_stats

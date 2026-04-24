# pyNFL/scripts/team_environment.py
# Team-level game environment for NFL player prop projections.
#
# Takes Vegas lines (total, spread) and team pace data to project how many
# plays each team will run and how they split between pass and rush.
#
# Usage:
#   from pyNFL.scripts.team_environment import (
#       compute_team_pace, compute_team_pass_rate, compute_team_rush_attempts,
#       project_game_environment,
#   )
#   pace = compute_team_pace(pbp)
#   pass_rate = compute_team_pass_rate(pbp)
#   rush_att = compute_team_rush_attempts(pbp)
#   env = project_game_environment("KC", "BUF", pace, pass_rate,
#                                   vegas_total=48.5, vegas_spread=-3.0)

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# League-wide defaults
# ---------------------------------------------------------------------------
LEAGUE_AVG_TOTAL = 44.0        # Average NFL game total (points)
LEAGUE_AVG_PLAYS = 63.0        # Average plays per team per game
LEAGUE_AVG_PASS_RATE = 0.58    # Average pass play %

# Game-script coefficient: each point of spread shifts pass% by ~0.5%
SPREAD_PASS_COEFF = 0.005


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _filter_real_plays(pbp_df):
    """
    Filter PBP to real offensive plays (pass or run).

    Uses play_type in ('pass', 'run') as the primary filter, with the binary
    pass/rush flags as a fallback for rows where play_type might be null.
    """
    mask = pd.Series(False, index=pbp_df.index)

    if "play_type" in pbp_df.columns:
        mask |= pbp_df["play_type"].isin(["pass", "run"])

    # Fallback: binary flags (1 = real pass/rush play)
    if "pass" in pbp_df.columns and "rush" in pbp_df.columns:
        mask |= (pbp_df["pass"] == 1) | (pbp_df["rush"] == 1)

    return pbp_df.loc[mask].copy()


def _exponential_weights(weeks, decay=0.88):
    """
    Build exponential-decay weights where the most recent week gets weight 1.0
    and each earlier week is multiplied by `decay`.

    Parameters
    ----------
    weeks : array-like of int
        Week numbers for each play.
    decay : float
        Decay factor per week (0 < decay <= 1).

    Returns
    -------
    np.ndarray
        Per-row weights aligned to the input.
    """
    weeks = np.asarray(weeks, dtype=float)
    max_week = weeks.max()
    return decay ** (max_week - weeks)


# ---------------------------------------------------------------------------
# Team pace: plays per game
# ---------------------------------------------------------------------------

def compute_team_pace(pbp_df, decay=0.88):
    """
    Compute each team's exponentially-weighted plays per game.

    Parameters
    ----------
    pbp_df : pd.DataFrame
        Play-by-play data with columns: posteam, week, game_id, play_type,
        pass, rush.
    decay : float
        Recency decay factor per week.

    Returns
    -------
    dict
        {team_abbr: plays_per_game}
    """
    plays = _filter_real_plays(pbp_df)
    if plays.empty:
        return {}

    # Count plays per team per game
    game_counts = (
        plays.groupby(["posteam", "game_id", "week"])
        .size()
        .reset_index(name="play_count")
    )

    result = {}
    for team, grp in game_counts.groupby("posteam"):
        weights = _exponential_weights(grp["week"].values, decay)
        result[team] = np.average(grp["play_count"].values, weights=weights)

    return result


# ---------------------------------------------------------------------------
# Team pass rate: dropbacks / total plays
# ---------------------------------------------------------------------------

def compute_team_pass_rate(pbp_df, decay=0.88):
    """
    Compute each team's exponentially-weighted pass play percentage
    (dropbacks / total plays) per game.

    Parameters
    ----------
    pbp_df : pd.DataFrame
        Play-by-play data.
    decay : float
        Recency decay factor per week.

    Returns
    -------
    dict
        {team_abbr: pass_rate}  (0.0 to 1.0)
    """
    plays = _filter_real_plays(pbp_df)
    if plays.empty:
        return {}

    # Count pass ATTEMPTS (excluding sacks) and total plays per team per game.
    # Sacks are coded as pass==1 in nflfastR but don't produce passing stats.
    # The betting market "passing yards" excludes sack yards, so we need
    # pass_attempts (not dropbacks) to match.
    has_sack = "sack" in plays.columns
    def _agg(g):
        if has_sack:
            pass_att = int(((g["pass"] == 1) & (g["sack"] != 1)).sum())
        else:
            pass_att = int(g["pass"].sum()) if "pass" in g.columns else 0
        return pd.Series({
            "total": len(g),
            "passes": pass_att,
            "week": g["week"].iloc[0],
        })

    game_stats = plays.groupby(["posteam", "game_id"]).apply(_agg).reset_index()
    game_stats["pass_rate"] = game_stats["passes"] / game_stats["total"].clip(lower=1)

    result = {}
    for team, grp in game_stats.groupby("posteam"):
        weights = _exponential_weights(grp["week"].values, decay)
        result[team] = np.average(grp["pass_rate"].values, weights=weights)

    return result


# ---------------------------------------------------------------------------
# Team rush attempts per game
# ---------------------------------------------------------------------------

def compute_team_rush_attempts(pbp_df, decay=0.88):
    """
    Compute each team's exponentially-weighted rush attempts per game.
    Useful for computing individual player rush share.

    Parameters
    ----------
    pbp_df : pd.DataFrame
        Play-by-play data.
    decay : float
        Recency decay factor per week.

    Returns
    -------
    dict
        {team_abbr: rush_att_per_game}
    """
    plays = _filter_real_plays(pbp_df)
    if plays.empty:
        return {}

    # Count rush plays per team per game
    if "rush" in plays.columns:
        rush_plays = plays[plays["rush"] == 1]
    elif "play_type" in plays.columns:
        rush_plays = plays[plays["play_type"] == "run"]
    else:
        return {}

    game_counts = (
        rush_plays.groupby(["posteam", "game_id", "week"])
        .size()
        .reset_index(name="rush_count")
    )

    result = {}
    for team, grp in game_counts.groupby("posteam"):
        weights = _exponential_weights(grp["week"].values, decay)
        result[team] = np.average(grp["rush_count"].values, weights=weights)

    return result


# ---------------------------------------------------------------------------
# Game environment projection
# ---------------------------------------------------------------------------

def project_game_environment(
    team,
    opp,
    team_pace_dict,
    team_pass_rate_dict,
    vegas_total,
    vegas_spread,
    league_avg_total=LEAGUE_AVG_TOTAL,
    is_home=True,
):
    """
    Project a team's game environment: expected plays, dropbacks, rush attempts.

    Parameters
    ----------
    team : str
        Team abbreviation (e.g. "KC").
    opp : str
        Opponent abbreviation (e.g. "BUF").
    team_pace_dict : dict
        {team_abbr: plays_per_game} from compute_team_pace().
    team_pass_rate_dict : dict
        {team_abbr: pass_rate} from compute_team_pass_rate().
    vegas_total : float
        Game total (over/under line).
    vegas_spread : float
        Home team spread. Negative means home is favored.
        E.g. -3.0 means home favored by 3.
    league_avg_total : float
        League average game total for scaling.
    is_home : bool
        True if `team` is the home team. Determines implied total direction.

    Returns
    -------
    dict
        {
            "implied_total": float,     # Team's implied point total
            "expected_plays": float,    # Projected total offensive plays
            "pass_pct": float,          # Adjusted pass play percentage
            "dropbacks": float,         # Projected dropbacks
            "rush_attempts": float,     # Projected rush attempts
        }
    """
    # -- Implied team total --
    # Convention: vegas_spread is the HOME team spread.
    # home_implied = (total - spread) / 2   (spread negative = favored)
    # away_implied = (total + spread) / 2
    if is_home:
        implied_total = (vegas_total - vegas_spread) / 2
        team_spread = vegas_spread          # Home team's spread
    else:
        implied_total = (vegas_total + vegas_spread) / 2
        team_spread = -vegas_spread         # Flip for away perspective

    # -- Expected plays --
    # Average of both teams' pace. NFL plays-per-game is driven by pace
    # (successful plays per drive, first-down conversion, clock strategy)
    # NOT by game total — a 48-point game and a 38-point game both average
    # ~63 plays per team. Totals reflect EFFICIENCY per play, not volume.
    #
    # Previous version scaled by total_scale = vegas_total / league_avg_total,
    # which inflated projected volume by ~9% in a 48-pt game. Backtest
    # diagnosis: rush_att was over-projected by +24%, largely from this
    # volume inflation getting split across rushers. Removed.
    team_pace = team_pace_dict.get(team, LEAGUE_AVG_PLAYS)
    opp_pace = team_pace_dict.get(opp, LEAGUE_AVG_PLAYS)
    expected_plays = (team_pace + opp_pace) / 2

    # -- Game-script adjusted pass rate --
    # Underdogs pass more, favorites run more.
    # team_spread: negative = favored (run more), positive = underdog (pass more)
    base_pass_rate = team_pass_rate_dict.get(team, LEAGUE_AVG_PASS_RATE)
    pass_rate_adj = base_pass_rate + team_spread * SPREAD_PASS_COEFF

    # Clamp pass rate to reasonable bounds
    pass_rate_adj = max(0.35, min(0.75, pass_rate_adj))

    # -- Split plays --
    dropbacks = expected_plays * pass_rate_adj
    rush_attempts = expected_plays * (1 - pass_rate_adj)

    return {
        "implied_total": round(implied_total, 2),
        "expected_plays": round(expected_plays, 1),
        "pass_pct": round(pass_rate_adj, 4),
        "dropbacks": round(dropbacks, 1),
        "rush_attempts": round(rush_attempts, 1),
    }

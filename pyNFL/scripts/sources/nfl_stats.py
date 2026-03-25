# pyNFL/scripts/sources/nfl_stats.py
# Aggregates nflfastR play-by-play data into team-level and player-level stats.
#
# Usage:
#   from pyNFL.scripts.sources.nflfastr import fetch_pbp
#   from pyNFL.scripts.sources.nfl_stats import compute_team_stats, compute_player_stats
#
#   pbp = fetch_pbp(2025)
#   teams = compute_team_stats(pbp, decay=0.85)
#   players = compute_player_stats(pbp)
#
# Exponential decay: weight recent weeks more heavily. decay=0.85 means each
# week back is 85% of the previous week's weight. A Week 12 team profile is
# dominated by the last 4-5 weeks, not the full season average.

import math
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Success rate thresholds by down (per Football Outsiders convention):
#   1st down: gain >= 50% of yards to go
#   2nd down: gain >= 70% of yards to go
#   3rd/4th down: gain >= 100% of yards to go (first down or TD)
_SUCCESS_THRESHOLDS = {1: 0.50, 2: 0.70, 3: 1.00, 4: 1.00}

# Minimum plays for a stat to be considered reliable
_MIN_PLAYS_TEAM = 50
_MIN_DROPBACKS_PLAYER = 10
_MIN_SNAPS_PLAYER = 20


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _compute_decay_weights(weeks_series, decay):
    """
    Compute exponential decay weights for each play based on its week number.

    The most recent week gets weight 1.0, the second-most-recent gets
    `decay`, the third gets `decay^2`, etc.

    Parameters
    ----------
    weeks_series : pd.Series
        Week numbers for each play.
    decay : float
        Decay factor (0 < decay <= 1). 1.0 = no decay (equal weighting).

    Returns
    -------
    np.ndarray
        Weight for each play.
    """
    if decay >= 1.0:
        return np.ones(len(weeks_series))

    max_week = weeks_series.max()
    weeks_back = max_week - weeks_series
    return np.power(decay, weeks_back.values)


def _weighted_mean(values, weights):
    """Weighted mean, handling NaN values."""
    mask = np.isfinite(values)
    if mask.sum() == 0:
        return float("nan")
    return np.average(values[mask], weights=weights[mask])


def _weighted_rate(flags, weights):
    """Weighted rate (proportion), handling NaN values."""
    mask = np.isfinite(flags)
    if mask.sum() == 0:
        return float("nan")
    return np.average(flags[mask], weights=weights[mask])


def _safe_divide(num, denom, default=0.0):
    """Safe division returning default when denominator is zero/NaN."""
    if denom is None or denom == 0 or (isinstance(denom, float) and math.isnan(denom)):
        return default
    return num / denom


# ---------------------------------------------------------------------------
# Compute success (per play) using down-specific thresholds
# ---------------------------------------------------------------------------

def _compute_success(pbp):
    """
    Add a 'success_calc' column using Football Outsiders thresholds.

    Falls back to the built-in 'success' column from nflfastR if
    yards_gained or down data is missing.
    """
    if "success" in pbp.columns and "down" not in pbp.columns:
        return pbp["success"]

    def _is_success(row):
        down = row.get("down")
        ydstogo = row.get("ydstogo", 10)
        gained = row.get("yards_gained", 0)

        if pd.isna(down) or pd.isna(gained):
            # Fall back to nflfastR success flag
            return row.get("success", 0)

        threshold = _SUCCESS_THRESHOLDS.get(int(down), 1.0)
        needed = ydstogo * threshold
        return 1 if gained >= needed else 0

    return pbp.apply(_is_success, axis=1)


# ---------------------------------------------------------------------------
# Team-level stats
# ---------------------------------------------------------------------------

def compute_team_stats(pbp_df, decay=0.85):
    """
    Aggregate play-by-play into team-level stats with exponential decay weighting.

    Parameters
    ----------
    pbp_df : pd.DataFrame
        Play-by-play data from fetch_pbp(). Must contain columns: posteam,
        defteam, week, epa, play_type, pass, rush, down, ydstogo, etc.
    decay : float
        Exponential decay factor per week (0.85 = each week is 85% of the
        previous week's weight). Set to 1.0 for equal weighting.

    Returns
    -------
    dict[str, dict]
        Mapping of team abbreviation to stat dict. Each team dict contains:

        Tier 1 - Efficiency:
            off_pass_epa       : Offensive EPA per pass play
            off_rush_epa       : Offensive EPA per rush play
            def_pass_epa       : Defensive EPA per pass play allowed
            def_rush_epa       : Defensive EPA per rush play allowed
            success_rate_off   : Offensive success rate
            success_rate_def   : Defensive success rate

        Tier 2 - Passing game:
            cpoe               : Completion % Over Expected (offense)
            adot               : Average Depth of Target (air yards per attempt)
            pressure_rate_allowed : % of dropbacks where QB was pressured

        Tier 3 - Defensive detail:
            pressure_rate_generated : % of opponent dropbacks where defense pressured QB
            rz_td_pct_def     : Red zone TD% allowed on defense

        Tier 4 - Situational:
            third_down_off     : 3rd down conversion rate (offense)
            third_down_def     : 3rd down conversion rate allowed (defense)
            rz_td_pct_off      : Red zone TD% (offense)
            pace               : Plays per game
            turnover_adj_efficiency : EPA after stripping turnover luck

        Meta:
            games_played       : Number of games in the sample
            total_plays        : Total offensive plays
    """
    if pbp_df.empty:
        print("  [nfl_stats] Warning: empty PBP dataframe")
        return {}

    # Work with pass/rush plays only for EPA stats
    df = pbp_df.copy()

    # Compute success using our thresholds
    df["success_calc"] = _compute_success(df)

    # Separate pass and rush plays
    pass_plays = df[df["play_type"] == "pass"].copy()
    rush_plays = df[df["play_type"] == "run"].copy()
    # All scrimmage plays (pass + rush)
    scrimmage = df[df["play_type"].isin(["pass", "run"])].copy()

    if scrimmage.empty:
        print("  [nfl_stats] Warning: no pass/run plays found")
        return {}

    # Compute decay weights for all scrimmage plays
    scrimmage["w"] = _compute_decay_weights(scrimmage["week"], decay)
    pass_plays["w"] = _compute_decay_weights(pass_plays["week"], decay)
    rush_plays["w"] = _compute_decay_weights(rush_plays["week"], decay)

    # Get all teams
    teams = set()
    if "posteam" in scrimmage.columns:
        teams.update(scrimmage["posteam"].dropna().unique())
    if "defteam" in scrimmage.columns:
        teams.update(scrimmage["defteam"].dropna().unique())

    # Count games per team
    games_by_team = {}
    if "game_id" in df.columns and "posteam" in df.columns:
        for team in teams:
            team_games = df[
                (df["posteam"] == team) | (df["defteam"] == team)
            ]["game_id"].nunique()
            games_by_team[team] = team_games

    stats = {}
    for team in sorted(teams):
        if not team or (isinstance(team, float) and math.isnan(team)):
            continue

        # --- Offensive stats (team is posteam) ---
        off_pass = pass_plays[pass_plays["posteam"] == team]
        off_rush = rush_plays[rush_plays["posteam"] == team]
        off_all = scrimmage[scrimmage["posteam"] == team]

        # --- Defensive stats (team is defteam) ---
        def_pass = pass_plays[pass_plays["defteam"] == team]
        def_rush = rush_plays[rush_plays["defteam"] == team]
        def_all = scrimmage[scrimmage["defteam"] == team]

        gp = games_by_team.get(team, 0)
        total_off_plays = len(off_all)

        if total_off_plays < _MIN_PLAYS_TEAM:
            continue

        # --- Tier 1: EPA efficiency ---
        off_pass_epa = (
            _weighted_mean(off_pass["epa"].values, off_pass["w"].values)
            if len(off_pass) > 0 else 0.0
        )
        off_rush_epa = (
            _weighted_mean(off_rush["epa"].values, off_rush["w"].values)
            if len(off_rush) > 0 else 0.0
        )
        def_pass_epa = (
            _weighted_mean(def_pass["epa"].values, def_pass["w"].values)
            if len(def_pass) > 0 else 0.0
        )
        def_rush_epa = (
            _weighted_mean(def_rush["epa"].values, def_rush["w"].values)
            if len(def_rush) > 0 else 0.0
        )

        # Success rate
        success_rate_off = (
            _weighted_rate(off_all["success_calc"].values, off_all["w"].values)
            if len(off_all) > 0 else 0.0
        )
        success_rate_def = (
            _weighted_rate(def_all["success_calc"].values, def_all["w"].values)
            if len(def_all) > 0 else 0.0
        )

        # --- Tier 2: Passing game ---
        cpoe = float("nan")
        if "cpoe" in off_pass.columns and len(off_pass) > 0:
            cpoe_valid = off_pass.dropna(subset=["cpoe"])
            if len(cpoe_valid) > 0:
                cpoe = _weighted_mean(cpoe_valid["cpoe"].values, cpoe_valid["w"].values)

        adot = float("nan")
        if "air_yards" in off_pass.columns and len(off_pass) > 0:
            ay_valid = off_pass.dropna(subset=["air_yards"])
            if len(ay_valid) > 0:
                adot = _weighted_mean(ay_valid["air_yards"].values, ay_valid["w"].values)

        # Pressure rate allowed (offense): % of dropbacks with pressure
        pressure_rate_allowed = float("nan")
        if "was_pressure" in off_pass.columns:
            pra_valid = off_pass.dropna(subset=["was_pressure"])
            if len(pra_valid) > 20:
                pressure_rate_allowed = _weighted_rate(
                    pra_valid["was_pressure"].values, pra_valid["w"].values
                )

        # --- Tier 3: Defensive detail ---
        # Pressure rate generated (defense): % of opponent dropbacks pressured
        pressure_rate_generated = float("nan")
        if "was_pressure" in def_pass.columns:
            prg_valid = def_pass.dropna(subset=["was_pressure"])
            if len(prg_valid) > 20:
                pressure_rate_generated = _weighted_rate(
                    prg_valid["was_pressure"].values, prg_valid["w"].values
                )

        # Red zone defense: TD% allowed when opponent inside the 20
        rz_td_pct_def = float("nan")
        if "yardline_100" in def_all.columns and "touchdown" in def_all.columns:
            rz_def = def_all[def_all["yardline_100"] <= 20]
            if len(rz_def) >= 10:
                rz_td_pct_def = _weighted_rate(
                    rz_def["touchdown"].values, rz_def["w"].values
                )

        # --- Tier 4: Situational ---
        # Third down conversion (offense)
        third_down_off = float("nan")
        if "down" in off_all.columns and "first_down" in off_all.columns:
            third_off = off_all[off_all["down"] == 3]
            if len(third_off) >= 10:
                # A 3rd down "conversion" = first down or touchdown
                converted = ((third_off["first_down"] == 1) | (third_off["touchdown"] == 1)).astype(float)
                third_down_off = _weighted_rate(converted.values, third_off["w"].values)

        # Third down conversion allowed (defense)
        third_down_def = float("nan")
        if "down" in def_all.columns and "first_down" in def_all.columns:
            third_def = def_all[def_all["down"] == 3]
            if len(third_def) >= 10:
                converted = ((third_def["first_down"] == 1) | (third_def["touchdown"] == 1)).astype(float)
                third_down_def = _weighted_rate(converted.values, third_def["w"].values)

        # Red zone TD% (offense)
        rz_td_pct_off = float("nan")
        if "yardline_100" in off_all.columns and "touchdown" in off_all.columns:
            rz_off = off_all[off_all["yardline_100"] <= 20]
            if len(rz_off) >= 10:
                rz_td_pct_off = _weighted_rate(
                    rz_off["touchdown"].values, rz_off["w"].values
                )

        # Pace: plays per game
        pace = _safe_divide(total_off_plays, gp) if gp > 0 else float("nan")

        # Turnover-adjusted efficiency: total EPA minus turnover-play EPA,
        # then divided by non-turnover plays. Strips fumble/INT luck.
        turnover_adj_efficiency = float("nan")
        if "interception" in off_all.columns and "fumble_lost" in off_all.columns:
            turnover_mask = (off_all["interception"] == 1) | (off_all["fumble_lost"] == 1)
            non_to = off_all[~turnover_mask]
            if len(non_to) > _MIN_PLAYS_TEAM:
                turnover_adj_efficiency = _weighted_mean(
                    non_to["epa"].values, non_to["w"].values
                )

        stats[team] = {
            # Tier 1
            "off_pass_epa": _round(off_pass_epa),
            "off_rush_epa": _round(off_rush_epa),
            "def_pass_epa": _round(def_pass_epa),
            "def_rush_epa": _round(def_rush_epa),
            "success_rate_off": _round(success_rate_off, 4),
            "success_rate_def": _round(success_rate_def, 4),
            # Tier 2
            "cpoe": _round(cpoe),
            "adot": _round(adot, 2),
            "pressure_rate_allowed": _round(pressure_rate_allowed, 4),
            # Tier 3
            "pressure_rate_generated": _round(pressure_rate_generated, 4),
            "rz_td_pct_def": _round(rz_td_pct_def, 4),
            # Tier 4
            "third_down_off": _round(third_down_off, 4),
            "third_down_def": _round(third_down_def, 4),
            "rz_td_pct_off": _round(rz_td_pct_off, 4),
            "pace": _round(pace, 1),
            "turnover_adj_efficiency": _round(turnover_adj_efficiency),
            # Meta
            "games_played": gp,
            "total_plays": total_off_plays,
        }

    print(f"  [nfl_stats] Computed team stats for {len(stats)} teams (decay={decay})")
    return stats


def _round(val, decimals=4):
    """Round a value, returning NaN-safe."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    return round(val, decimals)


# ---------------------------------------------------------------------------
# Player-level stats
# ---------------------------------------------------------------------------

def compute_player_stats(pbp_df):
    """
    Compute per-player EPA snapshots from play-by-play data.

    Used by the injury layer to estimate starter-vs-backup quality deltas.

    Parameters
    ----------
    pbp_df : pd.DataFrame
        Play-by-play data from fetch_pbp().

    Returns
    -------
    dict[str, dict]
        Mapping of player_id to stat dict. Stats depend on position role
        inferred from play participation:

        Passers:
            epa_per_dropback   : EPA per pass attempt/sack
            cpoe               : Completion % over expected
            total_dropbacks    : Total pass attempts + sacks
            team               : Most recent team

        Receivers:
            epa_per_target     : EPA per target
            target_share       : Share of team targets
            total_targets      : Total targets
            team               : Most recent team

        Rushers:
            epa_per_carry      : EPA per rush attempt
            total_carries      : Total rush attempts
            team               : Most recent team

        All players also get:
            snap_count         : Estimated play involvement count
            pressure_rate_contribution : (passers only) how often pressured
    """
    if pbp_df.empty:
        print("  [nfl_stats] Warning: empty PBP for player stats")
        return {}

    df = pbp_df.copy()
    players = {}

    # --- Passers ---
    if "passer_player_id" in df.columns:
        pass_plays = df[
            (df["play_type"] == "pass") & df["passer_player_id"].notna()
        ].copy()

        # Include sacks as dropbacks (they have passer_player_id in nflfastR)
        sack_plays = df[
            (df["sack"] == 1) & df["passer_player_id"].notna()
        ]
        # Merge -- sacks on pass plays are already included; add any standalone sacks
        dropbacks = pd.concat([pass_plays, sack_plays]).drop_duplicates(
            subset=["game_id", "play_id"], keep="first"
        )

        for pid, group in dropbacks.groupby("passer_player_id"):
            if pd.isna(pid) or len(group) < _MIN_DROPBACKS_PLAYER:
                continue

            epa_per_db = group["epa"].mean() if "epa" in group.columns else float("nan")

            cpoe_val = float("nan")
            if "cpoe" in group.columns:
                cpoe_valid = group["cpoe"].dropna()
                if len(cpoe_valid) > 0:
                    cpoe_val = cpoe_valid.mean()

            pressure_rate = float("nan")
            if "was_pressure" in group.columns:
                pr_valid = group["was_pressure"].dropna()
                if len(pr_valid) > 10:
                    pressure_rate = pr_valid.mean()

            team = group["posteam"].mode().iloc[0] if len(group["posteam"].mode()) > 0 else None
            name = (
                group["passer_player_name"].mode().iloc[0]
                if "passer_player_name" in group.columns
                and len(group["passer_player_name"].mode()) > 0
                else None
            )

            players[pid] = {
                "role": "passer",
                "player_name": name,
                "team": team,
                "epa_per_play": _round(epa_per_db),
                "epa_per_dropback": _round(epa_per_db),
                "cpoe": _round(cpoe_val),
                "snap_count": len(group),
                "total_dropbacks": len(group),
                "pressure_rate_contribution": _round(pressure_rate, 4),
                "target_share": None,
            }

    # --- Receivers ---
    if "receiver_player_id" in df.columns:
        targets = df[
            (df["play_type"] == "pass") & df["receiver_player_id"].notna()
        ].copy()

        # Compute team-level target counts for target share
        team_target_counts = targets.groupby("posteam").size().to_dict()

        for pid, group in targets.groupby("receiver_player_id"):
            if pd.isna(pid) or len(group) < _MIN_SNAPS_PLAYER:
                continue

            # Skip if already recorded as a passer (some gadget plays)
            if pid in players and players[pid]["role"] == "passer":
                continue

            epa_per_target = group["epa"].mean() if "epa" in group.columns else float("nan")

            team = group["posteam"].mode().iloc[0] if len(group["posteam"].mode()) > 0 else None
            name = (
                group["receiver_player_name"].mode().iloc[0]
                if "receiver_player_name" in group.columns
                and len(group["receiver_player_name"].mode()) > 0
                else None
            )

            team_targets = team_target_counts.get(team, 0)
            target_share = _safe_divide(len(group), team_targets) if team_targets > 0 else 0.0

            players[pid] = {
                "role": "receiver",
                "player_name": name,
                "team": team,
                "epa_per_play": _round(epa_per_target),
                "epa_per_dropback": None,
                "cpoe": None,
                "snap_count": len(group),
                "total_targets": len(group),
                "target_share": _round(target_share, 4),
                "pressure_rate_contribution": None,
            }

    # --- Rushers ---
    if "rusher_player_id" in df.columns:
        carries = df[
            (df["play_type"] == "run") & df["rusher_player_id"].notna()
        ].copy()

        for pid, group in carries.groupby("rusher_player_id"):
            if pd.isna(pid) or len(group) < _MIN_SNAPS_PLAYER:
                continue

            # Skip if already recorded as passer or receiver
            if pid in players:
                continue

            epa_per_carry = group["epa"].mean() if "epa" in group.columns else float("nan")

            team = group["posteam"].mode().iloc[0] if len(group["posteam"].mode()) > 0 else None
            name = (
                group["rusher_player_name"].mode().iloc[0]
                if "rusher_player_name" in group.columns
                and len(group["rusher_player_name"].mode()) > 0
                else None
            )

            players[pid] = {
                "role": "rusher",
                "player_name": name,
                "team": team,
                "epa_per_play": _round(epa_per_carry),
                "epa_per_dropback": None,
                "cpoe": None,
                "snap_count": len(group),
                "total_carries": len(group),
                "target_share": None,
                "pressure_rate_contribution": None,
            }

    print(f"  [nfl_stats] Computed player stats for {len(players)} players")
    _print_role_summary(players)
    return players


def _print_role_summary(players):
    """Print a breakdown of player roles."""
    roles = {}
    for p in players.values():
        r = p.get("role", "unknown")
        roles[r] = roles.get(r, 0) + 1
    parts = [f"{count} {role}s" for role, count in sorted(roles.items())]
    if parts:
        print(f"  [nfl_stats]   Roles: {', '.join(parts)}")


# ---------------------------------------------------------------------------
# Convenience: team stats for a specific week range
# ---------------------------------------------------------------------------

def compute_team_stats_through_week(pbp_df, through_week, decay=0.85):
    """
    Compute team stats using only plays through a given week.

    Useful for backtesting -- avoids look-ahead by filtering data.

    For regular season weeks (1-18), only REG games are included.
    For playoff weeks (19+), both REG and POST games through that week
    are included so playoff performance feeds into the model.

    Parameters
    ----------
    pbp_df : pd.DataFrame
        Full-season play-by-play.
    through_week : int
        Include plays from weeks 1 through this week (inclusive).
    decay : float
        Exponential decay factor.

    Returns
    -------
    dict[str, dict]
        Same format as compute_team_stats().
    """
    week_mask = pbp_df["week"] <= through_week

    # If through_week is within regular season, exclude any playoff games
    # that might share overlapping week numbers in some data sources.
    if through_week <= 18 and "season_type" in pbp_df.columns:
        season_type_mask = pbp_df["season_type"].isin(["REG", "reg"])
        filtered = pbp_df[week_mask & season_type_mask].copy()
    else:
        filtered = pbp_df[week_mask].copy()

    if filtered.empty:
        print(f"  [nfl_stats] No plays through week {through_week}")
        return {}

    return compute_team_stats(filtered, decay=decay)


def compute_player_stats_through_week(pbp_df, through_week):
    """
    Compute player stats using only plays through a given week.

    Parameters
    ----------
    pbp_df : pd.DataFrame
        Full-season play-by-play.
    through_week : int
        Include plays from weeks 1 through this week (inclusive).

    Returns
    -------
    dict[str, dict]
        Same format as compute_player_stats().
    """
    week_mask = pbp_df["week"] <= through_week

    # Same season_type guard as compute_team_stats_through_week
    if through_week <= 18 and "season_type" in pbp_df.columns:
        season_type_mask = pbp_df["season_type"].isin(["REG", "reg"])
        filtered = pbp_df[week_mask & season_type_mask].copy()
    else:
        filtered = pbp_df[week_mask].copy()

    if filtered.empty:
        return {}

    return compute_player_stats(filtered)


# ---------------------------------------------------------------------------
# Stat keys export (for downstream consumers like model_engine, self_tune)
# ---------------------------------------------------------------------------

TEAM_STAT_KEYS = [
    "off_pass_epa", "off_rush_epa", "def_pass_epa", "def_rush_epa",
    "success_rate_off", "success_rate_def",
    "cpoe", "adot", "pressure_rate_allowed",
    "pressure_rate_generated", "rz_td_pct_def",
    "third_down_off", "third_down_def", "rz_td_pct_off",
    "pace", "turnover_adj_efficiency",
]

PLAYER_STAT_KEYS = [
    "epa_per_play", "epa_per_dropback", "cpoe", "snap_count",
    "target_share", "pressure_rate_contribution",
]

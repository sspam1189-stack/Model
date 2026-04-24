# pyNFL/scripts/player_volume.py
# Per-player volume share computation from NFL play-by-play data.
#
# Computes what fraction of team volume (dropbacks, rush attempts, targets)
# each player commands, using exponentially weighted averages across games.

import math

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_DECAY = 0.88  # Match props_engine.py decay factor


# ---------------------------------------------------------------------------
# Core: compute shares directly from PBP
# ---------------------------------------------------------------------------

def compute_shares_from_pbp(pbp_df, decay=DEFAULT_DECAY):
    """
    Compute per-player volume shares from play-by-play data.

    For each game, calculates team-level totals (dropbacks, rush attempts)
    then derives each player's share of that volume.

    Parameters
    ----------
    pbp_df : pd.DataFrame
        Play-by-play data with nflfastr columns: passer_player_id,
        passer_player_name, rusher_player_id, rusher_player_name,
        receiver_player_id, receiver_player_name, pass, rush,
        posteam, defteam, game_id, week.
    decay : float
        Exponential decay factor for weighted averages.

    Returns
    -------
    dict
        {player_id: [share_dict, ...]} sorted by week ascending.
        Each share_dict contains:
            game_id, week, team, name, role,
            target_share, rush_share,
            targets, rush_att, team_dropbacks, team_rush_att
    """
    import pandas as pd

    # ------------------------------------------------------------------
    # 1. Team totals per game
    # ------------------------------------------------------------------
    pass_plays = pbp_df[pbp_df["pass"] == 1]
    rush_plays = pbp_df[pbp_df["rush"] == 1]

    # Team pass ATTEMPTS per game (excluding sacks) — used as denominator
    # for target_share. Must match team_environment.compute_team_pass_rate,
    # which also excludes sacks. Mismatched conventions caused receptions
    # and rec_yds to be systematically under-projected (target_share
    # computed on a larger denominator than the dropbacks value used at
    # projection time).
    if "sack" in pass_plays.columns:
        pass_att_plays = pass_plays[pass_plays["sack"] != 1]
    else:
        pass_att_plays = pass_plays
    team_dropbacks = (
        pass_att_plays.groupby(["game_id", "posteam"])["pass"]
        .sum()
        .rename("team_dropbacks")
        .reset_index()
    )

    # Team rush attempts per game
    team_rush_att = (
        rush_plays.groupby(["game_id", "posteam"])["rush"]
        .sum()
        .rename("team_rush_att")
        .reset_index()
    )

    # Merge into a single team totals frame
    team_totals = pd.merge(
        team_dropbacks, team_rush_att,
        on=["game_id", "posteam"], how="outer"
    ).fillna(0)

    # Week lookup: game_id -> week (take first non-null)
    week_lookup = (
        pbp_df[["game_id", "week"]]
        .drop_duplicates("game_id")
        .set_index("game_id")["week"]
        .to_dict()
    )

    # DefTeam lookup: (game_id, posteam) -> defteam
    def_lookup = (
        pbp_df[["game_id", "posteam", "defteam"]]
        .drop_duplicates(["game_id", "posteam"])
        .set_index(["game_id", "posteam"])["defteam"]
        .to_dict()
    )

    # Index team totals for fast lookup: (game_id, posteam) -> row
    tt_idx = team_totals.set_index(["game_id", "posteam"])

    # ------------------------------------------------------------------
    # 2. Player-level counts per game
    # ------------------------------------------------------------------
    shares = {}  # player_id -> list of share dicts

    # --- Passers (QBs get all dropbacks — share is always 1.0) ---
    if not pass_plays.empty:
        passer_counts = (
            pass_plays.groupby(
                ["passer_player_id", "passer_player_name", "game_id", "posteam"]
            )["pass"]
            .sum()
            .rename("pass_att")
            .reset_index()
        )
        for _, row in passer_counts.iterrows():
            pid = row["passer_player_id"]
            if pid is None or (isinstance(pid, float) and math.isnan(pid)):
                continue
            gid = row["game_id"]
            team = row["posteam"]
            t_db = _safe_lookup(tt_idx, gid, team, "team_dropbacks")
            t_ra = _safe_lookup(tt_idx, gid, team, "team_rush_att")

            entry = {
                "game_id": gid,
                "week": int(week_lookup.get(gid, 0)),
                "team": team,
                "opp": def_lookup.get((gid, team), ""),
                "name": row["passer_player_name"],
                "role": "passer",
                "target_share": 1.0,  # QB commands the passing game
                "rush_share": 0.0,
                "targets": 0,
                "pass_att": int(row["pass_att"]),
                "rush_att": 0,
                "team_dropbacks": int(t_db),
                "team_rush_att": int(t_ra),
            }
            shares.setdefault(pid, []).append(entry)

    # --- Receivers (WR/TE/RB targets) ---
    rec_plays = pass_plays[pass_plays["receiver_player_id"].notna()]
    if not rec_plays.empty:
        rec_counts = (
            rec_plays.groupby(
                ["receiver_player_id", "receiver_player_name", "game_id", "posteam"]
            )["pass"]
            .sum()
            .rename("targets")
            .reset_index()
        )
        for _, row in rec_counts.iterrows():
            pid = row["receiver_player_id"]
            if pid is None or (isinstance(pid, float) and math.isnan(pid)):
                continue
            gid = row["game_id"]
            team = row["posteam"]
            t_db = _safe_lookup(tt_idx, gid, team, "team_dropbacks")
            t_ra = _safe_lookup(tt_idx, gid, team, "team_rush_att")
            tgt = int(row["targets"])
            tgt_share = tgt / t_db if t_db > 0 else 0.0

            # Check if this player already has an entry for this game
            existing = _find_game_entry(shares.get(pid, []), gid)
            if existing is not None:
                existing["targets"] = tgt
                existing["target_share"] = tgt_share
            else:
                entry = {
                    "game_id": gid,
                    "week": int(week_lookup.get(gid, 0)),
                    "team": team,
                    "opp": def_lookup.get((gid, team), ""),
                    "name": row["receiver_player_name"],
                    "role": "receiver",
                    "target_share": tgt_share,
                    "rush_share": 0.0,
                    "targets": tgt,
                    "pass_att": 0,
                    "rush_att": 0,
                    "team_dropbacks": int(t_db),
                    "team_rush_att": int(t_ra),
                }
                shares.setdefault(pid, []).append(entry)

    # --- Rushers ---
    if not rush_plays.empty:
        rusher_counts = (
            rush_plays.groupby(
                ["rusher_player_id", "rusher_player_name", "game_id", "posteam"]
            )["rush"]
            .sum()
            .rename("rush_att")
            .reset_index()
        )
        for _, row in rusher_counts.iterrows():
            pid = row["rusher_player_id"]
            if pid is None or (isinstance(pid, float) and math.isnan(pid)):
                continue
            gid = row["game_id"]
            team = row["posteam"]
            t_db = _safe_lookup(tt_idx, gid, team, "team_dropbacks")
            t_ra = _safe_lookup(tt_idx, gid, team, "team_rush_att")
            ra = int(row["rush_att"])
            rush_share = ra / t_ra if t_ra > 0 else 0.0

            existing = _find_game_entry(shares.get(pid, []), gid)
            if existing is not None:
                existing["rush_att"] = ra
                existing["rush_share"] = rush_share
            else:
                entry = {
                    "game_id": gid,
                    "week": int(week_lookup.get(gid, 0)),
                    "team": team,
                    "opp": def_lookup.get((gid, team), ""),
                    "name": row["rusher_player_name"],
                    "role": "rusher",
                    "target_share": 0.0,
                    "rush_share": rush_share,
                    "targets": 0,
                    "pass_att": 0,
                    "rush_att": ra,
                    "team_dropbacks": int(t_db),
                    "team_rush_att": int(t_ra),
                }
                shares.setdefault(pid, []).append(entry)

    # ------------------------------------------------------------------
    # 3. Sort each player's games by week
    # ------------------------------------------------------------------
    for pid in shares:
        shares[pid].sort(key=lambda g: g.get("week", 0))

    return shares


# ---------------------------------------------------------------------------
# Weighted share averages
# ---------------------------------------------------------------------------

def compute_weighted_shares(player_share_logs, decay=DEFAULT_DECAY):
    """
    Compute exponentially weighted average shares for a single player.

    Parameters
    ----------
    player_share_logs : list[dict]
        Per-game share dicts for one player (from compute_shares_from_pbp).
        Must be sorted by week ascending (oldest first).
    decay : float
        Exponential decay per game step.

    Returns
    -------
    dict
        {"target_share": float, "rush_share": float}
    """
    if not player_share_logs:
        return {"target_share": 0.0, "rush_share": 0.0}

    tgt_shares = [g["target_share"] for g in player_share_logs]
    rush_shares = [g["rush_share"] for g in player_share_logs]

    return {
        "target_share": round(_weighted_avg(tgt_shares, decay), 4),
        "rush_share": round(_weighted_avg(rush_shares, decay), 4),
    }


def compute_all_weighted_shares(all_shares, decay=DEFAULT_DECAY, min_games=3):
    """
    Compute weighted shares for all players at once.

    Parameters
    ----------
    all_shares : dict
        {player_id: [share_dict, ...]} from compute_shares_from_pbp.
    decay : float
        Exponential decay factor.
    min_games : int
        Minimum games required. Players below this are excluded.

    Returns
    -------
    dict
        {player_id: {"name", "team", "role", "target_share", "rush_share",
                      "games_played"}}
    """
    result = {}
    for pid, logs in all_shares.items():
        if len(logs) < min_games:
            continue
        weighted = compute_weighted_shares(logs, decay)
        latest = logs[-1]
        result[pid] = {
            "name": latest["name"],
            "team": latest["team"],
            "role": latest.get("role", "unknown"),
            "target_share": weighted["target_share"],
            "rush_share": weighted["rush_share"],
            "games_played": len(logs),
        }
    return result


# ---------------------------------------------------------------------------
# Legacy adapter: compute shares from pre-built player game logs
# ---------------------------------------------------------------------------

def compute_player_shares(player_game_logs):
    """
    Compute volume shares from the player_game_logs dict produced by
    build_player_game_logs() in props_engine.py.

    This is a convenience wrapper for when you already have aggregated logs
    but not the raw PBP. It reconstructs approximate team totals from the
    player logs themselves (summing all players on the same team in the
    same game).

    Parameters
    ----------
    player_game_logs : dict
        {player_id: [{"game_id", "week", "team", "role", "pass_att",
                       "rush_att", "targets", ...}, ...]}

    Returns
    -------
    dict
        {player_id: [{"game_id", "week", "team", "target_share",
                       "rush_share", ...}, ...]}
    """
    # Step 1: Reconstruct team totals per game
    # team_totals[(game_id, team)] = {"dropbacks": int, "rush_att": int}
    team_totals = {}
    for pid, games in player_game_logs.items():
        for g in games:
            key = (g["game_id"], g["team"])
            if key not in team_totals:
                team_totals[key] = {"dropbacks": 0, "rush_att": 0}
            role = g.get("role", "")
            if role == "passer":
                # Passer's pass_att = team dropbacks (approximately)
                team_totals[key]["dropbacks"] = max(
                    team_totals[key]["dropbacks"], g.get("pass_att", 0)
                )
            if role == "rusher" or g.get("rush_att", 0) > 0:
                team_totals[key]["rush_att"] += g.get("rush_att", 0)

    # Step 2: Compute per-player shares
    shares = {}
    for pid, games in player_game_logs.items():
        player_shares = []
        for g in sorted(games, key=lambda x: x.get("week", 0)):
            key = (g["game_id"], g["team"])
            tt = team_totals.get(key, {"dropbacks": 0, "rush_att": 0})
            t_db = tt["dropbacks"]
            t_ra = tt["rush_att"]

            targets = g.get("targets", 0)
            rush_att = g.get("rush_att", 0)
            role = g.get("role", "")

            tgt_share = 0.0
            if role == "passer":
                tgt_share = 1.0
            elif targets > 0 and t_db > 0:
                tgt_share = targets / t_db

            rush_share = rush_att / t_ra if rush_att > 0 and t_ra > 0 else 0.0

            player_shares.append({
                "game_id": g["game_id"],
                "week": g.get("week", 0),
                "team": g["team"],
                "opp": g.get("opp", ""),
                "name": g.get("name", ""),
                "role": role,
                "target_share": round(tgt_share, 4),
                "rush_share": round(rush_share, 4),
                "targets": targets,
                "rush_att": rush_att,
                "team_dropbacks": t_db,
                "team_rush_att": t_ra,
            })
        shares[pid] = player_shares

    return shares


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _weighted_avg(values, decay=DEFAULT_DECAY):
    """Exponentially weighted average (most recent = highest weight)."""
    if not values:
        return 0.0
    n = len(values)
    # values[0] = oldest, values[-1] = newest
    weights = [decay ** (n - 1 - i) for i in range(n)]
    return sum(v * w for v, w in zip(values, weights)) / sum(weights)


def _safe_lookup(tt_idx, game_id, team, col):
    """Safely look up a team total value from the indexed DataFrame."""
    try:
        return float(tt_idx.loc[(game_id, team), col])
    except (KeyError, TypeError):
        return 0.0


def _find_game_entry(entries, game_id):
    """Find an existing game entry in a player's share list."""
    for e in entries:
        if e["game_id"] == game_id:
            return e
    return None

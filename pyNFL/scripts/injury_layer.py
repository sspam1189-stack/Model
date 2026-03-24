# pyNFL/scripts/injury_layer.py
# Injury Adjustment Layer — the pipeline's primary edge.
#
# Computes injury-conditional EPA deltas per team by comparing starter EPA
# to backup EPA at each position, weighted by positional importance and
# snap share.  Most public EPA models ignore injuries entirely; this layer
# gives the projection a structural advantage.
#
# Usage:
#   from pyNFL.scripts.injury_layer import compute_injury_deltas
#   deltas = compute_injury_deltas(injury_data, player_stats, team_stats)
#   # deltas = {"KC": -0.043, "BUF": 0.0, ...}

import math
import os
import json
from collections import defaultdict

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Positional weights — calibrated via backfill variance decomposition.
# Directional ordering: QB >>> Edge/WR1 > CB1 > OL
POSITIONAL_WEIGHTS = {
    "QB":    0.90,
    "WR":    0.20,
    "EDGE":  0.28,
    "CB":    0.15,
    "OL":    0.12,
    "OTHER": 0.05,
}

# League-average replacement-level EPA by position group.
# Used as Bayesian prior when backup sample size is insufficient.
REPLACEMENT_LEVEL_EPA = {
    "QB":    -0.10,   # EPA/dropback — backup QBs are significantly worse
    "WR":    -0.02,   # EPA/target
    "EDGE":  -0.01,   # EPA/rush (pressure generation proxy)
    "CB":    +0.02,   # EPA/coverage snap — positive = bad (allows more EPA)
    "OL":    -0.01,   # EPA/pass block snap
    "OTHER": -0.005,
}

# Minimum sample sizes before trusting individual EPA estimates.
# Below this, we blend with league-average replacement level.
MIN_SAMPLE = {
    "QB":    30,   # dropbacks
    "WR":    100,  # snaps
    "EDGE":  100,
    "CB":    100,
    "OL":    100,
    "OTHER": 100,
}

# Prior weight for Bayesian blending (acts as pseudo-count).
PRIOR_WEIGHT = {
    "QB":    30,
    "WR":    100,
    "EDGE":  100,
    "CB":    100,
    "OL":    100,
    "OTHER": 100,
}

# Multi-injury compounding multiplier — applied when 2+ starters on the
# same unit (offense or defense) are out simultaneously.
MULTI_INJURY_MULTIPLIER = 1.15

# Questionable players: probability-weighted delta (50% of full impact).
QUESTIONABLE_WEIGHT = 0.50

# Position group -> unit mapping (for multi-injury compounding).
POSITION_UNIT = {
    "QB":    "offense",
    "WR":    "offense",
    "OL":    "offense",
    "EDGE":  "defense",
    "CB":    "defense",
    "OTHER": None,  # no compounding for generic positions
}

# ---------------------------------------------------------------------------
# Position classification
# ---------------------------------------------------------------------------

# Maps NFL position abbreviations to our position groups.
_POS_MAP = {
    # Quarterbacks
    "QB": "QB",
    # Wide receivers / pass catchers
    "WR": "WR", "TE": "WR",
    # Edge rushers
    "DE": "EDGE", "OLB": "EDGE", "EDGE": "EDGE",
    # Cornerbacks / secondary
    "CB": "CB", "S": "CB", "FS": "CB", "SS": "CB", "DB": "CB",
    # Offensive line
    "OT": "OL", "OG": "OL", "C": "OL", "OL": "OL",
    "LT": "OL", "RT": "OL", "LG": "OL", "RG": "OL", "T": "OL", "G": "OL",
    # Defensive interior (not edge — lower positional weight)
    "DT": "OTHER", "NT": "OTHER", "ILB": "OTHER", "MLB": "OTHER", "LB": "OTHER",
    # Specialists
    "K": "OTHER", "P": "OTHER", "LS": "OTHER",
    # Running backs
    "RB": "OTHER", "FB": "OTHER", "HB": "OTHER",
}


def classify_position_group(position):
    """Classify an NFL position string into one of our position groups.

    Parameters
    ----------
    position : str
        Raw position string (e.g. "QB", "WR", "OLB", "CB").

    Returns
    -------
    str
        One of "QB", "WR", "EDGE", "CB", "OL", or "OTHER".
    """
    if not position:
        return "OTHER"
    pos = str(position).upper().strip()

    # Direct lookup
    if pos in _POS_MAP:
        return _POS_MAP[pos]

    # Slash-separated positions (e.g. "DE/OLB"): take the first recognized
    for part in pos.replace("/", " ").replace("-", " ").split():
        if part in _POS_MAP:
            return _POS_MAP[part]

    return "OTHER"


# ---------------------------------------------------------------------------
# EPA blending with replacement-level prior
# ---------------------------------------------------------------------------

def blend_epa_with_prior(player_epa, sample_size, position_group):
    """Blend an individual player's EPA with the league-average replacement
    level prior, using a Bayesian shrinkage approach.

    When sample size is small, the estimate is pulled toward the positional
    replacement-level average.  As sample grows, the individual estimate
    dominates.

    Parameters
    ----------
    player_epa : float
        Observed EPA per relevant action (dropback, snap, target, etc.).
    sample_size : int or float
        Number of relevant actions (dropbacks for QB, snaps for others).
    position_group : str
        One of "QB", "WR", "EDGE", "CB", "OL", "OTHER".

    Returns
    -------
    float
        Blended EPA estimate.
    """
    league_avg = REPLACEMENT_LEVEL_EPA.get(position_group, -0.005)
    prior_wt = PRIOR_WEIGHT.get(position_group, 100)

    if sample_size <= 0:
        return league_avg

    blended = (player_epa * sample_size + league_avg * prior_wt) / (sample_size + prior_wt)
    return blended


# ---------------------------------------------------------------------------
# Starter / backup identification
# ---------------------------------------------------------------------------

def get_starter_backup_pairs(team, position_group, player_stats):
    """Identify the starter and primary backup at a position group for a team.

    Parameters
    ----------
    team : str
        Team abbreviation (e.g. "KC", "BUF").
    position_group : str
        One of "QB", "WR", "EDGE", "CB", "OL", "OTHER".
    player_stats : dict
        Player-level stats keyed by player_id or player_name, each containing:
            - team: str
            - position: str
            - epa_per_action: float (EPA per dropback/snap/target)
            - sample_size: int (dropbacks for QB, snaps for others)
            - snap_share: float (0-1, fraction of team snaps)
            - is_starter: bool

    Returns
    -------
    tuple or None
        (starter_epa, backup_epa, snap_share) if a starter can be identified,
        or None if insufficient data.
    """
    # Collect all players on this team at this position group
    candidates = []
    for pid, pdata in player_stats.items():
        if pdata.get("team") != team:
            continue
        if classify_position_group(pdata.get("position", "")) != position_group:
            continue
        candidates.append(pdata)

    if not candidates:
        return None

    # Sort by snap share descending to identify starter vs backup
    candidates.sort(key=lambda p: p.get("snap_share", 0), reverse=True)

    starter = candidates[0]
    backup = candidates[1] if len(candidates) > 1 else None

    starter_epa = starter.get("epa_per_action", 0.0)
    starter_sample = starter.get("sample_size", 0)
    starter_snap_share = starter.get("snap_share", 0.0)

    # Blend starter EPA (usually sufficient sample, but safety check)
    starter_epa_blended = blend_epa_with_prior(starter_epa, starter_sample, position_group)

    if backup:
        backup_epa = backup.get("epa_per_action", 0.0)
        backup_sample = backup.get("sample_size", 0)
        backup_epa_blended = blend_epa_with_prior(backup_epa, backup_sample, position_group)
    else:
        # No known backup — use pure replacement level
        backup_epa_blended = REPLACEMENT_LEVEL_EPA.get(position_group, -0.005)

    return (starter_epa_blended, backup_epa_blended, starter_snap_share)


# ---------------------------------------------------------------------------
# Core: compute injury deltas for all teams
# ---------------------------------------------------------------------------

def compute_injury_deltas(injury_data, player_stats, team_stats=None):
    """Compute injury-conditional EPA deltas per team.

    This is the main entry point for the injury layer.  For each injured
    starter, we compute how much the team's effective EPA changes when the
    backup steps in, weighted by positional importance and snap share.

    Parameters
    ----------
    injury_data : dict
        Injury report from sources/injuries.py.  Structure:
        {
            "report": {
                team_name: [
                    {"player": str, "status": "out"|"doubtful"|"questionable"|...,
                     "position": str, "player_id": str (optional)},
                    ...
                ]
            }
        }
        The report may also have entries without position — those are skipped.

    player_stats : dict
        Player-level stats keyed by player_id or player_name.  Each entry:
        {
            "team": str,
            "position": str,
            "epa_per_action": float,
            "sample_size": int,
            "snap_share": float (0-1),
            "is_starter": bool,
        }

    team_stats : dict or None
        Optional team-level stats (not currently used but reserved for
        future interaction terms).

    Returns
    -------
    dict
        Mapping of team identifier -> total injury EPA delta (float).
        Negative values mean the team is weakened.  Teams with no injuries
        or no data will have delta = 0.0.
    """
    if not injury_data or not player_stats:
        return {}

    report = injury_data if isinstance(injury_data, dict) and "report" not in injury_data else injury_data.get("report", injury_data)

    # Collect all teams mentioned in the report
    all_teams = set()
    for team_name, injuries in report.items():
        all_teams.add(team_name)

    deltas = {}

    for team_name, injuries in report.items():
        if not injuries:
            deltas[team_name] = 0.0
            continue

        # Track per-unit injury counts for compounding
        unit_injury_counts = defaultdict(int)  # "offense" | "defense" -> count
        individual_deltas = []

        for inj in injuries:
            status = str(inj.get("status", "")).lower()

            # Only process players who are out, doubtful, or questionable
            if status not in ("out", "doubtful", "questionable"):
                continue

            player_name = inj.get("player", "")
            position = inj.get("position", "")
            player_id = inj.get("player_id", "")

            # Try to find this player in player_stats
            pdata = None
            if player_id and player_id in player_stats:
                pdata = player_stats[player_id]
            elif player_name and player_name in player_stats:
                pdata = player_stats[player_name]
            else:
                # Fuzzy match: last name + team
                for pid, ps in player_stats.items():
                    if ps.get("team") != team_name:
                        continue
                    if player_name and player_name.split()[-1].lower() == str(pid).split()[-1].lower():
                        pdata = ps
                        break

            if not pdata:
                # Cannot compute delta without player stats
                continue

            # Determine position group
            pos_group = classify_position_group(
                position or pdata.get("position", "")
            )

            # Only compute deltas for starters (or high snap-share players)
            snap_share = pdata.get("snap_share", 0.0)
            is_starter = pdata.get("is_starter", False) or snap_share >= 0.50

            if not is_starter:
                continue

            # Get starter/backup pair for this position group on this team
            pair = get_starter_backup_pairs(team_name, pos_group, player_stats)
            if pair is None:
                # Fallback: use the player's own EPA vs replacement level
                starter_epa = blend_epa_with_prior(
                    pdata.get("epa_per_action", 0.0),
                    pdata.get("sample_size", 0),
                    pos_group,
                )
                backup_epa = REPLACEMENT_LEVEL_EPA.get(pos_group, -0.005)
                snap_share_factor = snap_share if snap_share > 0 else 0.5
            else:
                starter_epa, backup_epa, snap_share_factor = pair
                if snap_share_factor <= 0:
                    snap_share_factor = 0.5

            # Core delta formula
            positional_weight = POSITIONAL_WEIGHTS.get(pos_group, 0.05)
            raw_delta = (backup_epa - starter_epa) * positional_weight * snap_share_factor

            # Apply questionable discount
            if status == "questionable":
                raw_delta *= QUESTIONABLE_WEIGHT
            elif status == "doubtful":
                raw_delta *= 0.80  # doubtful = ~80% of full impact

            # Track unit for compounding
            if status in ("out", "doubtful"):
                unit = POSITION_UNIT.get(pos_group)
                if unit:
                    unit_injury_counts[unit] += 1

            individual_deltas.append({
                "player": player_name,
                "position": pos_group,
                "delta": raw_delta,
                "unit": POSITION_UNIT.get(pos_group),
                "status": status,
            })

        # Sum individual deltas
        total_delta = sum(d["delta"] for d in individual_deltas)

        # Apply multi-injury compounding
        # When 2+ starters on the same unit are out, the interaction effect
        # makes the total worse than the sum of parts.
        compounding_applied = False
        for unit, count in unit_injury_counts.items():
            if count >= 2:
                # Only compound the deltas from this unit
                unit_delta = sum(
                    d["delta"] for d in individual_deltas
                    if d["unit"] == unit and d["status"] in ("out", "doubtful")
                )
                extra = unit_delta * (MULTI_INJURY_MULTIPLIER - 1.0)
                total_delta += extra
                compounding_applied = True

        deltas[team_name] = round(total_delta, 6)

    return deltas


# ---------------------------------------------------------------------------
# Build player stats from nflfastR play-by-play data
# ---------------------------------------------------------------------------

def build_player_epa_from_pbp(pbp_df, roster_df=None):
    """Build per-player EPA stats from nflfastR play-by-play DataFrame.

    This function aggregates play-by-play data into player-level EPA metrics
    suitable for the injury delta computation.

    Parameters
    ----------
    pbp_df : pandas.DataFrame
        Play-by-play data from nflfastr.fetch_pbp().  Must contain columns:
        passer_player_name, receiver_player_name, rusher_player_name,
        posteam, epa, play_type, etc.
    roster_df : pandas.DataFrame or None
        Optional roster data from nflfastr.fetch_roster() for position info.

    Returns
    -------
    dict
        Player stats keyed by player name, each entry:
        {
            "team": str,
            "position": str,
            "epa_per_action": float,
            "sample_size": int,
            "snap_share": float,
            "is_starter": bool,
        }
    """
    try:
        import pandas as pd
    except ImportError:
        print("  [injury_layer] pandas not available, cannot build player EPA")
        return {}

    if pbp_df is None or len(pbp_df) == 0:
        return {}

    players = {}

    # --- Passers (QB EPA per dropback) ---
    pass_plays = pbp_df[
        (pbp_df["play_type"] == "pass") &
        (pbp_df["passer_player_name"].notna())
    ].copy()

    if len(pass_plays) > 0:
        passer_stats = pass_plays.groupby(["passer_player_name", "posteam"]).agg(
            epa_sum=("epa", "sum"),
            n_plays=("epa", "count"),
        ).reset_index()

        # Compute team total pass plays for snap share
        team_pass_totals = pass_plays.groupby("posteam")["epa"].count().to_dict()

        for _, row in passer_stats.iterrows():
            name = row["passer_player_name"]
            team = row["posteam"]
            n = int(row["n_plays"])
            epa_per = row["epa_sum"] / n if n > 0 else 0.0
            team_total = team_pass_totals.get(team, 1)
            snap_share = n / team_total if team_total > 0 else 0.0

            players[name] = {
                "team": team,
                "position": "QB",
                "epa_per_action": round(epa_per, 4),
                "sample_size": n,
                "snap_share": round(snap_share, 3),
                "is_starter": snap_share >= 0.50,
            }

    # --- Receivers (WR/TE EPA per target) ---
    target_plays = pbp_df[
        (pbp_df["play_type"] == "pass") &
        (pbp_df["receiver_player_name"].notna())
    ].copy()

    if len(target_plays) > 0:
        receiver_stats = target_plays.groupby(["receiver_player_name", "posteam"]).agg(
            epa_sum=("epa", "sum"),
            n_targets=("epa", "count"),
        ).reset_index()

        team_target_totals = target_plays.groupby("posteam")["epa"].count().to_dict()

        for _, row in receiver_stats.iterrows():
            name = row["receiver_player_name"]
            team = row["posteam"]
            n = int(row["n_targets"])
            epa_per = row["epa_sum"] / n if n > 0 else 0.0
            team_total = team_target_totals.get(team, 1)
            target_share = n / team_total if team_total > 0 else 0.0

            # Only add if not already present as a passer (dual-threat QB edge case)
            if name not in players:
                players[name] = {
                    "team": team,
                    "position": "WR",  # will be refined by roster if available
                    "epa_per_action": round(epa_per, 4),
                    "sample_size": n,
                    "snap_share": round(target_share, 3),
                    "is_starter": target_share >= 0.15,  # ~15% target share = WR1/WR2
                }

    # --- Rushers (RB EPA per carry) ---
    rush_plays = pbp_df[
        (pbp_df["play_type"] == "run") &
        (pbp_df["rusher_player_name"].notna())
    ].copy()

    if len(rush_plays) > 0:
        rusher_stats = rush_plays.groupby(["rusher_player_name", "posteam"]).agg(
            epa_sum=("epa", "sum"),
            n_carries=("epa", "count"),
        ).reset_index()

        team_rush_totals = rush_plays.groupby("posteam")["epa"].count().to_dict()

        for _, row in rusher_stats.iterrows():
            name = row["rusher_player_name"]
            team = row["posteam"]
            n = int(row["n_carries"])
            epa_per = row["epa_sum"] / n if n > 0 else 0.0
            team_total = team_rush_totals.get(team, 1)
            carry_share = n / team_total if team_total > 0 else 0.0

            # Only add if not already present
            if name not in players:
                players[name] = {
                    "team": team,
                    "position": "RB",
                    "epa_per_action": round(epa_per, 4),
                    "sample_size": n,
                    "snap_share": round(carry_share, 3),
                    "is_starter": carry_share >= 0.40,
                }

    # --- Enrich with roster position data if available ---
    if roster_df is not None and len(roster_df) > 0:
        try:
            roster_positions = {}
            for _, row in roster_df.iterrows():
                name = row.get("player_name") or row.get("full_name", "")
                pos = row.get("position", "")
                if name and pos:
                    roster_positions[name] = pos

            for pname, pdata in players.items():
                if pname in roster_positions:
                    real_pos = roster_positions[pname]
                    # Only override if the roster gives a more specific position
                    if real_pos and real_pos != pdata["position"]:
                        pdata["position"] = real_pos
        except Exception:
            pass  # roster enrichment is best-effort

    return players


# ---------------------------------------------------------------------------
# Diagnostic: human-readable injury adjustment notes
# ---------------------------------------------------------------------------

def get_injury_notes(injury_data, player_stats, team_stats=None):
    """Build human-readable notes about injury adjustments per team.

    Parameters
    ----------
    injury_data : dict
        Same as compute_injury_deltas.
    player_stats : dict
        Same as compute_injury_deltas.
    team_stats : dict or None
        Optional.

    Returns
    -------
    dict
        Mapping of team -> list of note strings.
    """
    deltas = compute_injury_deltas(injury_data, player_stats, team_stats)
    report = injury_data.get("report", injury_data) if isinstance(injury_data, dict) else {}

    notes = {}
    for team_name, injuries in report.items():
        if not injuries:
            continue

        team_notes = []
        total_delta = deltas.get(team_name, 0.0)

        for inj in injuries:
            status = str(inj.get("status", "")).lower()
            if status not in ("out", "doubtful", "questionable"):
                continue
            player = inj.get("player", "Unknown")
            pos = inj.get("position", "")
            pos_group = classify_position_group(pos)
            status_label = status.upper()
            team_notes.append(f"{player} ({pos_group}) — {status_label}")

        if team_notes and abs(total_delta) > 0.001:
            team_notes.append(f"Total EPA delta: {total_delta:+.3f}")
            notes[team_name] = team_notes

    return notes


# ---------------------------------------------------------------------------
# Persistence: save/load player EPA snapshots
# ---------------------------------------------------------------------------

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "data"))
_PLAYER_EPA_DIR = os.path.join(_DATA_DIR, "player_epa")


def save_player_epa(player_stats, season, week=None):
    """Save player EPA snapshot to disk.

    Parameters
    ----------
    player_stats : dict
        Output of build_player_epa_from_pbp().
    season : int
        NFL season year.
    week : int or None
        Week number (None = full season aggregate).
    """
    os.makedirs(_PLAYER_EPA_DIR, exist_ok=True)
    suffix = f"_wk{week}" if week else ""
    path = os.path.join(_PLAYER_EPA_DIR, f"player_epa_{season}{suffix}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(player_stats, f, indent=2)
    print(f"  [injury_layer] Saved {len(player_stats)} player EPA records to {path}")


def load_player_epa(season, week=None):
    """Load player EPA snapshot from disk.

    Parameters
    ----------
    season : int
        NFL season year.
    week : int or None
        Week number (None = full season aggregate).

    Returns
    -------
    dict or None
        Player stats dict, or None if file not found.
    """
    suffix = f"_wk{week}" if week else ""
    path = os.path.join(_PLAYER_EPA_DIR, f"player_epa_{season}{suffix}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  [injury_layer] Failed to load {path}: {e}")
        return None

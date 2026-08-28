# pyNFL/scripts/sources/qb_starts.py
# Backup-QB start detection from nflverse schedules data.
#
# Empirical basis (2023-2025, closing consensus totals): games where a team
# starts a QB who is neither its previous-game starter nor its modal starter
# of the last 4 games go OVER the total by +2.8 pts on average (t=2.05,
# n=104 excl. W18; blind-over 60-43, +12.7u, positive all three seasons).
# Books over-discount backup QBs on totals. Starting QBs are announced
# pregame, so the flag is walk-forward legitimate.

import os

import pandas as pd

_CACHE_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "data", "stats_cache", "nfl"))

# nflverse team codes that differ from this repo's canonical abbreviations
_ABBR_FIX = {"LA": "LAR", "WSH": "WAS", "JAC": "JAX"}


def norm_abbr(abbr):
    a = str(abbr or "").upper()
    return _ABBR_FIX.get(a, a)


def fetch_schedules(season):
    """Season schedule with starting QBs, cached to a local parquet."""
    os.makedirs(_CACHE_DIR, exist_ok=True)
    cache = os.path.join(_CACHE_DIR, f"schedules_{season}.parquet")
    if os.path.exists(cache):
        return pd.read_parquet(cache)
    import nfl_data_py as nfl
    sched = nfl.import_schedules([season])
    keep = [c for c in (
        "game_id", "season", "week", "gameday", "home_team", "away_team",
        "home_qb_name", "away_qb_name", "home_score", "away_score",
        "spread_line", "total_line", "roof", "temp", "wind",
        # gametime/weekday drive the primetime split, which several
        # situational systems depend on. Leaving them out silently made every
        # game look like a day game (kickoff hour unparseable -> not primetime).
        "gametime", "weekday",
    ) if c in sched.columns]
    sched = sched[keep]
    sched.to_parquet(cache)
    return sched


def build_backup_qb_flags(sched, strict=True):
    """
    Walk-forward backup-QB flags from a season schedule.

    strict=True (the TOTALS system's definition, validated at 62-54):
        flags only when the listed QB differs BOTH from the previous-game
        starter and from the modal starter of the last 4 games — i.e. the
        *transition* into a backup.

    strict=False (the MARGIN penalty's definition): flags whenever the
        listed QB differs from the modal starter of the last 4 games. This
        also catches weeks 2+ of a multi-week absence, where the team's
        EPA rating is still built mostly on the injured starter and is
        therefore still stale. Once the backup has started enough games to
        become the modal starter, the rating has absorbed him and the flag
        correctly stops firing.

    Either way, a minimum of 2 prior starts is required so early-season QB
    competitions don't flag.

    Returns
    -------
    dict
        {(week, team_abbr): True} for flagged team-weeks.
    """
    from collections import defaultdict, Counter

    flags = {}
    starts = defaultdict(list)   # team -> [(week, qb_name)]
    for _, r in sched.sort_values("week").iterrows():
        for team_col, qb_col in (("home_team", "home_qb_name"),
                                 ("away_team", "away_qb_name")):
            team = norm_abbr(r[team_col])
            qb = r[qb_col]
            if not isinstance(qb, str) or not qb:
                continue
            hist = [q for w, q in starts[team] if w < r["week"]]
            if len(hist) >= 2:
                modal, _n = Counter(hist[-4:]).most_common(1)[0]
                differs = (qb != modal) if not strict else (
                    qb != hist[-1] and qb != modal)
                if differs:
                    flags[(int(r["week"]), team)] = True
            starts[team].append((int(r["week"]), qb))
    return flags


def detect_backup_qb_live(injury_report, player_stats, name_key_fn):
    """
    Live backup-QB detection: a team's primary QB (most dropbacks this
    season) is listed OUT or Doubtful on the injury report.

    Parameters
    ----------
    injury_report : dict
        {team_display_name: [{player, status, position, ...}]} from
        sources.injuries (status lowercase: out/doubtful/questionable).
    player_stats : dict
        {player_id: {...}} from compute_player_stats (role/team/dropbacks).
    name_key_fn : callable
        Name normalizer, e.g. props_engine._name_key.

    Returns
    -------
    dict
        {team_abbr: primary_qb_name} for teams whose primary QB is out.
    """
    # Primary QB per team by dropback volume
    primary = {}
    for _pid, st in (player_stats or {}).items():
        if st.get("role") != "passer":
            continue
        team = norm_abbr(st.get("team"))
        db = st.get("total_dropbacks") or 0
        if team and db > (primary.get(team) or (None, 0))[1]:
            primary[team] = (st.get("player_name") or "", db)

    out_keys = set()
    for _team, entries in (injury_report or {}).items():
        for e in entries:
            if str(e.get("status", "")).lower() in ("out", "doubtful") and \
                    str(e.get("position", "")).upper() == "QB":
                out_keys.add(name_key_fn(str(e.get("player", ""))))

    flagged = {}
    for team, (qb_name, _db) in primary.items():
        if qb_name and name_key_fn(qb_name) in out_keys:
            flagged[team] = qb_name
    return flagged

# pyNFL/scripts/props_weekly.py
# Live weekly NFL player-props pipeline: project + grade.
#
# The projection path is an exact replica of props_backfill.py's walk-forward
# backtest (the configuration that validated 478W-316L, 60.2%, +130u across
# 2023-2025): _project_volume_output with a per-player Kalman warmed up over
# the season-to-date, MARKET_THRESHOLDS, DISABLED_MARKETS, t-dist df=4.
# No bias adjust / EMPIRICAL_STD floor here — those live in props_engine's
# unvalidated path and are intentionally NOT part of the live pipeline.
#
# On top of the validated path, two live-only gates:
#   * Injury gate — players listed OUT/Doubtful are suppressed entirely;
#     Questionable picks carry an "injury" flag but still fire.
#   * EV gate — pCover must clear the implied breakeven of the actual price
#     plus EV_MARGIN. At standard -110 this is a no-op (thresholds are far
#     higher); it only kills picks on heavily juiced lines.
#
# Entry points (called from run_weekly.py):
#   project_week_props(season, week, odds_list=None, injury_report=None)
#   grade_week_props(season, week)

import json
import math
import os
from datetime import datetime, timedelta, timezone

from scipy.stats import t as t_dist

from sources.nflfastr import fetch_pbp
from sources.odds_theoddsapi import fetch_nfl_odds, fetch_nfl_player_props
from team_environment import (
    compute_team_pace, compute_team_pass_rate, project_game_environment,
)
from player_volume import compute_shares_from_pbp
from player_kalman_nfl import (
    new_player_kalman_state, batch_update_from_game_logs, apply_drift,
)
from props_engine import (
    build_player_game_logs, _name_key,
    MARKET_THRESHOLDS, DISABLED_MARKETS, PROP_T_DF,
    # EV gate lives in props_engine so the backfill applies the identical one
    EV_MARGIN, DEFAULT_PRICE, implied_breakeven, pick_units,
)
from props_backfill import (
    _project_volume_output, _build_kalman_logs,
    compute_player_rates_from_logs, _resolve_abbr,
    MARKETS,
)

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROPS_JSON_PATHS = [
    os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "data", "nfl-props.json")),
    os.path.normpath(os.path.join(
        _SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "nfl-props.json")),
]
_CALIB_PATH = os.path.normpath(
    os.path.join(_SCRIPT_DIR, "..", "data", "prop_calibration.json"))

# Picks only fire on markets that survived the 2023-2025 backtest.
ACTIVE_MARKETS = [m for m in MARKETS if m not in DISABLED_MARKETS]

GAME_WINDOW_DAYS = 7      # only project games commencing within this window


def _parse_iso(ts):
    if not ts:
        return None
    try:
        s = str(ts).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Injury gate
# ---------------------------------------------------------------------------

def build_injury_lookup(injury_report):
    """
    Map (first_initial, last_name) -> normalized status for players who are
    out / doubtful / questionable.  injury_report is the "report" dict from
    sources.injuries.fetch_injury_data().
    """
    lookup = {}
    for _team, entries in (injury_report or {}).items():
        for e in entries:
            status = str(e.get("status", "")).lower()
            if status not in ("out", "doubtful", "questionable"):
                continue
            nk = _name_key(str(e.get("player", "")))
            if nk == ("", ""):
                continue
            # OUT/doubtful beats questionable if a player appears twice
            prev = lookup.get(nk)
            if prev in ("out", "doubtful"):
                continue
            lookup[nk] = status
    return lookup


# ---------------------------------------------------------------------------
# Kalman warm-up (replays the season to date — deterministic, no state file)
# ---------------------------------------------------------------------------

def warm_up_player_kalman(pbp, through_week):
    """Fresh per-player Kalman state, updated sequentially with weeks
    1..through_week — identical to the backtest's warm-up."""
    state = new_player_kalman_state()
    weeks = sorted(w for w in pbp["week"].dropna().unique().astype(int)
                   if w <= through_week)
    for wk in weeks:
        wk_pbp = pbp[pbp["week"] == wk]
        if wk_pbp.empty:
            continue
        wk_logs = build_player_game_logs(wk_pbp)
        wk_shares = compute_shares_from_pbp(wk_pbp)
        records = _build_kalman_logs(wk_logs, wk_shares)
        batch_update_from_game_logs(state, records)
        apply_drift(state, games_elapsed=1)
    return state


# ---------------------------------------------------------------------------
# JSON store helpers
# ---------------------------------------------------------------------------

def _load_props_json():
    path = _PROPS_JSON_PATHS[0]
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"props": []}


def _write_props_json(data):
    for path in _PROPS_JSON_PATHS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"  [props] Wrote {len(data.get('props', []))} picks to {path}")


def _summarize(props):
    """Compute record summaries over graded picks."""
    def _tally(picks):
        w = sum(1 for p in picks if p.get("result") == "WIN")
        l = sum(1 for p in picks if p.get("result") == "LOSS")
        u = sum(p.get("units", pick_units(p.get("result"), p.get("odds")))
                for p in picks if p.get("result") in ("WIN", "LOSS"))
        pct = f"{w / (w + l) * 100:.1f}%" if (w + l) else "n/a"
        return w, l, u, pct

    graded = [p for p in props if p.get("result") in ("WIN", "LOSS")]
    live = [p for p in graded if p.get("live")]
    w, l, u, pct = _tally(graded)
    summary = f"{w}W-{l}L ({pct}) {'+' if u >= 0 else ''}{u:.1f}u all graded picks"
    if live:
        lw, ll, lu, lpct = _tally(live)
        summary = (f"LIVE {lw}W-{ll}L ({lpct}) {'+' if lu >= 0 else ''}{lu:.1f}u"
                   f" | {summary}")
    return summary


# ---------------------------------------------------------------------------
# PROJECT — generate this week's picks
# ---------------------------------------------------------------------------

def project_week_props(season, week, odds_list=None, injury_report=None):
    """
    Project player props for (season, week) and merge picks into
    nfl-props.json.  Re-running refreshes picks for games that have not yet
    commenced; picks whose games already started are locked and preserved.
    """
    print(f"\n== PROPS PROJECT — {season} Week {week} ==\n")

    pbp = fetch_pbp(season)
    if pbp is None or pbp.empty:
        print("  [props] No PBP data — cannot project")
        return
    prior_pbp = pbp[pbp["week"] < week].copy()
    if prior_pbp.empty:
        print(f"  [props] No prior-week PBP for week {week} — "
              "picks start once players have 3+ games")
        return

    # --- Game odds (for team environments + commence times) ---
    if odds_list is None:
        try:
            odds_list = fetch_nfl_odds(season=season, week=week)
        except Exception as e:
            print(f"  [props] Odds fetch failed: {e}")
            odds_list = []

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=GAME_WINDOW_DAYS)

    def _in_window(entry):
        dt = _parse_iso(entry.get("commenceTimeIso") or entry.get("commence_time"))
        return dt is None or dt <= horizon

    odds_list = [g for g in (odds_list or []) if _in_window(g)]

    # --- Team environments ---
    team_pace = compute_team_pace(prior_pbp)
    team_pass_rate = compute_team_pass_rate(prior_pbp)
    team_envs = {}
    commence_by_abbr = {}
    allowed_matchups = set()
    for entry in odds_list:
        home_abbr = _resolve_abbr(entry.get("home", ""))
        away_abbr = _resolve_abbr(entry.get("away", ""))
        total, line = entry.get("total"), entry.get("line")
        if not home_abbr or not away_abbr or total is None or line is None:
            continue
        team_envs[home_abbr] = project_game_environment(
            home_abbr, away_abbr, team_pace, team_pass_rate,
            vegas_total=total, vegas_spread=line, is_home=True)
        team_envs[away_abbr] = project_game_environment(
            away_abbr, home_abbr, team_pace, team_pass_rate,
            vegas_total=total, vegas_spread=line, is_home=False)
        commence = entry.get("commenceTimeIso") or entry.get("commence_time")
        commence_by_abbr[home_abbr] = commence
        commence_by_abbr[away_abbr] = commence
        allowed_matchups.add((entry.get("home", ""), entry.get("away", "")))

    if not team_envs:
        print("  [props] No usable game odds — cannot project")
        return

    # --- Prop lines (real lines only; no simulated fallback in live mode) ---
    try:
        prop_lines = fetch_nfl_player_props(season=season, week=week)
    except Exception as e:
        print(f"  [props] Prop lines fetch failed: {e}")
        prop_lines = []
    if allowed_matchups and prop_lines:
        prop_lines = [
            pl for pl in prop_lines
            if (pl.get("event_home", ""), pl.get("event_away", "")) in allowed_matchups
            or not pl.get("event_home")
        ]
    # Team-aware line index: name keys like "B.Robinson" collide across
    # players (Bijan vs Brian Robinson), so each line keeps its event's team
    # abbrs and is only matched to a projection from one of those teams.
    line_lookup = {}
    for pl in prop_lines or []:
        nk = _name_key(pl.get("player", ""))
        entry = dict(pl)
        entry["_teams"] = {
            _resolve_abbr(pl.get("event_home", "")),
            _resolve_abbr(pl.get("event_away", "")),
        } - {""}
        line_lookup.setdefault((nk[0], nk[1], pl.get("market", "")), []).append(entry)
    if not line_lookup:
        print("  [props] No prop lines available — nothing to pick")
        return

    def _find_line(nk, market, team):
        candidates = line_lookup.get((nk[0], nk[1], market), [])
        for c in candidates:
            if c["_teams"] and team in c["_teams"]:
                return c
        # Fall back only when unambiguous and the event teams are unknown
        if len(candidates) == 1 and not candidates[0]["_teams"]:
            return candidates[0]
        return None

    # --- Injury gate ---
    if injury_report is None:
        try:
            from sources.injuries import fetch_injury_data
            injury_report = fetch_injury_data().get("report", {})
        except Exception as e:
            print(f"  [props] Injury fetch failed: {e} — no injury gate")
            injury_report = {}
    injury_lookup = build_injury_lookup(injury_report)
    if injury_lookup:
        n_out = sum(1 for s in injury_lookup.values() if s in ("out", "doubtful"))
        print(f"  [props] Injury gate armed: {n_out} players OUT/Doubtful, "
              f"{len(injury_lookup) - n_out} Questionable")

    # --- Player inputs + Kalman warm-up (backtest-identical) ---
    prior_logs = build_player_game_logs(prior_pbp)
    prior_shares = compute_shares_from_pbp(prior_pbp)
    prior_rates = compute_player_rates_from_logs(prior_logs)
    kalman_state = warm_up_player_kalman(pbp, through_week=week - 1)

    # --- Project + generate picks ---
    picks = []
    n_projected = 0
    n_inj_blocked = 0
    n_ev_blocked = 0

    for pid, games in prior_logs.items():
        if not games:
            continue
        latest = games[-1]
        team = latest.get("team", "")
        env = team_envs.get(team)
        if env is None:
            continue  # team not playing in this window

        proj = _project_volume_output(
            pid, games,
            prior_shares.get(pid, []),
            prior_rates.get(pid, []),
            env, kalman_state, latest.get("opp", ""),
        )
        if proj is None:
            continue

        name = proj["name"]
        nk = _name_key(name)
        inj_status = injury_lookup.get(nk)

        for market, mdata in proj["markets"].items():
            if market not in ACTIVE_MARKETS:
                continue
            n_projected += 1

            line_data = _find_line(nk, market, team)
            if not line_data or line_data.get("line") is None:
                continue
            line = float(line_data["line"])
            proj_val, std = mdata["proj"], mdata["std"]
            if not std or std <= 0:
                continue

            z = (proj_val - line) / std
            p_over = float(t_dist.cdf(z, df=PROP_T_DF))
            p_under = 1.0 - p_over
            best_p = max(p_over, p_under)
            thresh = MARKET_THRESHOLDS.get(market, 0.80)
            if best_p < thresh:
                continue

            direction = "OVER" if p_over > p_under else "UNDER"
            price = (line_data.get("over_price") if direction == "OVER"
                     else line_data.get("under_price"))

            # Injury gate: OUT/Doubtful never fires
            if inj_status in ("out", "doubtful"):
                n_inj_blocked += 1
                continue

            # EV gate: must clear the actual price's breakeven + margin
            breakeven = implied_breakeven(price if price is not None else DEFAULT_PRICE)
            if best_p < breakeven + EV_MARGIN:
                n_ev_blocked += 1
                continue

            pick = {
                "player": name,
                "team": team,
                "opp": proj.get("opp", ""),
                "market": market,
                "proj": round(proj_val, 1),
                "std": round(std, 1),
                "line": line,
                "pick": direction,
                "edge": round(proj_val - line, 1),
                "pCover": round(best_p, 3),
                "conf": "high",
                "odds": price if price is not None else DEFAULT_PRICE,
                "breakeven": round(breakeven, 3),
                "season": int(season),
                "week": int(week),
                "commence": commence_by_abbr.get(team),
                "live": True,
                "result": None,
                "generatedAt": datetime.now().isoformat(timespec="seconds"),
            }
            if inj_status == "questionable":
                pick["injury"] = "questionable"
            picks.append(pick)

    picks.sort(key=lambda p: -(p["pCover"] or 0))
    print(f"\n  [props] {n_projected} projections -> {len(picks)} picks "
          f"({n_inj_blocked} injury-blocked, {n_ev_blocked} EV-blocked)")
    for p in picks:
        flag = " [Q]" if p.get("injury") else ""
        print(f"    {p['player']:24s} {p['market']:11s} {p['pick']:5s} "
              f"{p['line']:>6.1f}  proj {p['proj']:>6.1f}  p={p['pCover']:.3f} "
              f"({p['odds']}){flag}")

    # --- Merge into nfl-props.json ---
    data = _load_props_json()
    existing = data.get("props", [])

    def _is_current(p):
        return p.get("season") == int(season) and p.get("week") == int(week)

    # Lock rule: keep current-week picks whose game already commenced;
    # everything else current-week is replaced by this refresh.
    kept_other = [p for p in existing if not _is_current(p)]
    locked = []
    for p in existing:
        if not _is_current(p):
            continue
        commence = _parse_iso(p.get("commence"))
        if (commence is not None and commence <= now) or p.get("result"):
            locked.append(p)
    locked_keys = {(p.get("player"), p.get("market")) for p in locked}
    fresh = [p for p in picks if (p["player"], p["market"]) not in locked_keys]
    if locked:
        print(f"  [props] {len(locked)} current-week picks locked "
              "(game started or already graded)")

    merged = kept_other + locked + fresh
    merged.sort(key=lambda p: (
        p.get("season") or 0,
        p.get("week") if isinstance(p.get("week"), int) else 0,
        -(p.get("pCover") or 0),
    ))

    data.update({
        "season": int(season),
        "week": int(week),
        "generated": datetime.now().isoformat(timespec="seconds"),
        "totalProjections": n_projected,
        "totalPicks": len(merged),
        "props": merged,
        "summary": _summarize(merged),
    })
    _write_props_json(data)


# ---------------------------------------------------------------------------
# GRADE — settle a completed week's picks
# ---------------------------------------------------------------------------

_MARKET_STAT_KEY = {
    "pass_yds": "pass_yds",
    "pass_tds": "pass_td",
    "rush_yds": "rush_yds",
    "rush_att": "rush_att",
    "rec_yds": "rec_yds",
    "receptions": "receptions",
}


def _find_actual_for_pick(player_name, team, market, actual_logs):
    """Team-aware actual lookup (avoids B.Robinson-style name collisions)."""
    stat_key = _MARKET_STAT_KEY.get(market)
    if not stat_key:
        return None
    fallback = None
    for _pid, games in actual_logs.items():
        for g in games:
            if g.get("name") != player_name:
                continue
            val = g.get(stat_key)
            if val is None:
                continue
            if g.get("team") == team:
                return float(val)
            if fallback is None:
                fallback = float(val)
    # Same name, different team: only trust it when the team is unknown
    return fallback if not team else None

def grade_week_props(season, week):
    """
    Grade pending props picks for (season, week) against actual PBP stats.
    Players with no stat line in a completed game grade VOID (0u) — the
    same convention the backtest used (no grade), so the record stays
    comparable.  Games with no PBP yet stay PENDING for the next run.
    """
    if week < 1:
        return
    print(f"\n== PROPS GRADE — {season} Week {week} ==\n")

    data = _load_props_json()
    props = data.get("props", [])
    pending = [
        p for p in props
        if p.get("season") == int(season) and p.get("week") == int(week)
        and not p.get("result")
    ]
    if not pending:
        print("  [props] Nothing to grade")
        return

    try:
        pbp = fetch_pbp(season)
    except Exception as e:
        print(f"  [props] PBP fetch failed: {e}")
        return
    week_pbp = pbp[pbp["week"] == week]
    if week_pbp.empty:
        print(f"  [props] No PBP for week {week} yet — picks stay pending")
        return
    actual_logs = build_player_game_logs(week_pbp)
    teams_played = set(week_pbp["posteam"].dropna().unique()) | \
        set(week_pbp["defteam"].dropna().unique())

    graded = voided = 0
    for p in pending:
        actual = _find_actual_for_pick(
            p["player"], p.get("team"), p["market"], actual_logs)
        if actual is None:
            if p.get("team") in teams_played:
                # Game completed but no stat line — treat as void (DNP)
                p["result"] = "VOID"
                p["units"] = 0.0
                voided += 1
            continue  # game not played yet — stays pending
        p["actual"] = actual
        line = p.get("line")
        if actual == line:
            p["result"] = "PUSH"
        elif (p["pick"] == "OVER") == (actual > line):
            p["result"] = "WIN"
        else:
            p["result"] = "LOSS"
        p["units"] = round(pick_units(p["result"], p.get("odds")), 2)
        graded += 1

    week_graded = [
        p for p in pending if p.get("result") in ("WIN", "LOSS", "PUSH")
    ]
    w = sum(1 for p in week_graded if p["result"] == "WIN")
    l = sum(1 for p in week_graded if p["result"] == "LOSS")
    u = sum(p.get("units", 0.0) for p in week_graded)
    print(f"  [props] Week {week}: graded {graded} ({voided} void) — "
          f"{w}W-{l}L {'+' if u >= 0 else ''}{u:.2f}u")

    data["summary"] = _summarize(props)
    _write_props_json(data)

    # Feed residuals into the bias-calibration tracker (telemetry only —
    # the live projection path does not consume the bias).
    if graded:
        try:
            from calibrate_props import update_from_graded
            print("  [props] Updating prop_calibration.json from graded picks...")
            update_from_graded(_PROPS_JSON_PATHS[0], _CALIB_PATH)
        except Exception as e:
            print(f"  [props] WARNING: calibration update failed: {e}")


# ---------------------------------------------------------------------------
# CLI (manual runs / smoke tests)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Live NFL props pipeline")
    parser.add_argument("--stage", choices=["project", "grade"], required=True)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--week", type=int, required=True)
    args = parser.parse_args()

    if args.stage == "project":
        project_week_props(args.season, args.week)
    else:
        grade_week_props(args.season, args.week)

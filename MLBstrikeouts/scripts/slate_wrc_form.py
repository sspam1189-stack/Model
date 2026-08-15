"""
slate_wrc_form.py — Slate scouting report: team wRC+ platoon splits x starter recent form.

Joins three existing repo artifacts for a given slate date:

  1. ``MLBstrikeouts/data/mlb-all-ml.json``   -> today's games, odds, totals,
     probable starters and their throwing hand.
  2. ``MLBstrikeouts/data/mlb-team-wrc.json`` -> FanGraphs true wRC+ by team,
     split by opposing-starter handedness (vs LHP / vs RHP). Season snapshot,
     manually captured — see ``docs/superpowers/specs/2026-07-28-mlb-fade-ml-wrc-table-design.md``.
  3. ``data/pitcher_cache/mlb/game_logs_2026.json`` -> per-game pitching lines,
     from which season / last-5-start / last-3-start form is derived.

Output is a per-game scouting table, not a betting model: nothing here feeds a
pick, gate, size or grade. The wRC+ file is explicitly view-only (see the design
doc), so this report inherits that status.

Usage
-----
    python MLBstrikeouts/scripts/slate_wrc_form.py                 # today's slate
    python MLBstrikeouts/scripts/slate_wrc_form.py --date 2026-08-15
    python MLBstrikeouts/scripts/slate_wrc_form.py --json          # machine-readable
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ALL_ML = REPO / "MLBstrikeouts" / "data" / "mlb-all-ml.json"
TEAM_WRC = REPO / "MLBstrikeouts" / "data" / "mlb-team-wrc.json"
GAME_LOGS = REPO / "data" / "pitcher_cache" / "mlb" / "game_logs_2026.json"

# Recent-form windows, in starts.
RECENT_N = 5
HOT_COLD_N = 3

# Below this many innings per start the arm is being used as an opener, and its
# rate stats describe one time through the order rather than a starter's night.
OPENER_IP_PER_GS = 3.5

# Fewer than this many starts and the rates are noise; the report says so
# rather than presenting them at face value.
THIN_SAMPLE_GS = 5

# League-average reference points for the 2026 season, used only to label a
# starter's rate stats. Kept local so the report never reaches the network.
LG_K_PCT = 22.0
LG_BB_PCT = 8.3


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _load(path):
    with open(path) as fh:
        return json.load(fh)


def load_slate(slate_date):
    """Return the list of games on ``slate_date`` from the all-ML payload."""
    games = _load(ALL_ML).get("today", [])
    return [g for g in games if g.get("date") == slate_date]


def load_wrc():
    """Return ``(teams_dict, as_of_str)`` from the team wRC+ snapshot."""
    blob = _load(TEAM_WRC)
    return blob.get("teams", {}), blob.get("asOf")


def organize_starts(logs):
    """Group starting-pitcher game lines by pitcher name, oldest first."""
    by_name = {}
    for row in logs:
        if not row.get("is_start"):
            continue
        by_name.setdefault(row.get("pitcher_name"), []).append(row)
    for rows in by_name.values():
        rows.sort(key=lambda r: r.get("game_date") or "")
    return by_name


# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------

def aggregate(starts):
    """Collapse a list of start lines into rate stats. ``None`` if empty."""
    if not starts:
        return None
    outs = sum(int(s.get("outs") or 0) for s in starts)
    bf = sum(int(s.get("bf") or 0) for s in starts)
    k = sum(int(s.get("k") or 0) for s in starts)
    bb = sum(int(s.get("bb") or 0) for s in starts)
    h = sum(int(s.get("h") or 0) for s in starts)
    hr = sum(int(s.get("hr") or 0) for s in starts)
    er = sum(int(s.get("er") or 0) for s in starts)
    ip = outs / 3.0
    return {
        "gs": len(starts),
        "ip": round(ip, 1),
        "ip_per_gs": round(ip / len(starts), 2),
        "era": round(er * 9.0 / ip, 2) if ip else None,
        "whip": round((h + bb) / ip, 2) if ip else None,
        "k_pct": round(100.0 * k / bf, 1) if bf else None,
        "bb_pct": round(100.0 * bb / bf, 1) if bf else None,
        "k_bb_pct": round(100.0 * (k - bb) / bf, 1) if bf else None,
        "hr9": round(hr * 9.0 / ip, 2) if ip else None,
        "k": k,
        "bb": bb,
        "hr": hr,
        "er": er,
    }


def form_for(starts, before_date):
    """Season / last-N / last-3 aggregates for starts strictly before a date."""
    prior = [s for s in starts if (s.get("game_date") or "") < before_date]
    return {
        "season": aggregate(prior),
        "recent": aggregate(prior[-RECENT_N:]),
        "hot": aggregate(prior[-HOT_COLD_N:]),
        "last_starts": [
            {
                "date": s.get("game_date"),
                "opp": s.get("opp"),
                "ip": round(int(s.get("outs") or 0) / 3.0, 1),
                "k": int(s.get("k") or 0),
                "bb": int(s.get("bb") or 0),
                "h": int(s.get("h") or 0),
                "hr": int(s.get("hr") or 0),
                "er": int(s.get("er") or 0),
            }
            for s in prior[-RECENT_N:]
        ],
    }


def trend(form):
    """Recent-minus-season deltas on the rates that matter most."""
    season, recent = form.get("season"), form.get("recent")
    if not season or not recent:
        return {}
    out = {}
    for key in ("era", "whip", "k_pct", "bb_pct", "ip_per_gs", "hr9"):
        a, b = recent.get(key), season.get(key)
        if a is not None and b is not None:
            out[key] = round(a - b, 2)
    return out


# ---------------------------------------------------------------------------
# Matchup scoring
# ---------------------------------------------------------------------------

def role_flags(form):
    """Label usage patterns that make the rate stats read differently."""
    season = form.get("season") or {}
    recent = form.get("recent") or season
    flags = []
    ip_per_gs = (recent or {}).get("ip_per_gs")
    if ip_per_gs is not None and ip_per_gs < OPENER_IP_PER_GS:
        flags.append("opener")
    if season.get("gs", 0) < THIN_SAMPLE_GS:
        flags.append("thin-sample")
    return flags


def matchup_wrc(wrc, offense_team, pitcher_hand):
    """Opposing offense's wRC+ against this starter's throwing hand."""
    row = wrc.get(offense_team)
    if not row or not pitcher_hand:
        return None
    return row.get("vsLHP" if pitcher_hand.upper().startswith("L") else "vsRHP")


def platoon_gap(wrc, offense_team):
    """vsLHP minus vsRHP for an offense — how lopsided its platoon profile is."""
    row = wrc.get(offense_team)
    if not row:
        return None
    lhp, rhp = row.get("vsLHP"), row.get("vsRHP")
    if lhp is None or rhp is None:
        return None
    return lhp - rhp


def pressure(form, opp_wrc):
    """
    Composite matchup-pressure score for one starter, roughly in wRC+ units.

    Positive = the offense he faces is better than the arm he brings, i.e. the
    matchup leans toward the bats. Built from three additive pieces:

      * opponent wRC+ vs his hand, relative to a league-average 100
      * his recent (last-5-start) ERA relative to a 4.20 league baseline
      * his recent K-BB% relative to league average, inverted

    The weights are deliberately blunt — this is a scouting sort key, not a
    calibrated projection, and nothing downstream consumes it.
    """
    recent = form.get("recent") or form.get("season")
    if not recent or opp_wrc is None:
        return None
    score = float(opp_wrc - 100)
    if recent.get("era") is not None:
        score += (recent["era"] - 4.20) * 8.0
    if recent.get("k_bb_pct") is not None:
        score -= (recent["k_bb_pct"] - (LG_K_PCT - LG_BB_PCT)) * 1.2
    return round(score, 1)


def build_report(slate_date):
    games = load_slate(slate_date)
    wrc, wrc_as_of = load_wrc()
    starts_by_name = organize_starts(_load(GAME_LOGS))

    rows = []
    for game in games:
        entry = {
            "matchup": f"{game['away']} @ {game['home']}",
            "away": game["away"],
            "home": game["home"],
            "commence": game.get("commence"),
            "away_ml": game.get("away_ml"),
            "home_ml": game.get("home_ml"),
            "total": game.get("total_line"),
            "over_ml": game.get("over_ml"),
            "under_ml": game.get("under_ml"),
            "sides": {},
        }
        for side, offense in (("away", game["home"]), ("home", game["away"])):
            name = game.get(f"{side}_pitcher")
            hand = game.get(f"{side}_hand")
            form = form_for(starts_by_name.get(name, []), slate_date) if name else {}
            opp_wrc = matchup_wrc(wrc, offense, hand)
            entry["sides"][side] = {
                "pitcher": name,
                "hand": hand,
                "team": game[side],
                "opponent_offense": offense,
                "opp_wrc_vs_hand": opp_wrc,
                "opp_platoon_gap": platoon_gap(wrc, offense),
                "form": form,
                "trend": trend(form),
                "flags": role_flags(form),
                "pressure": pressure(form, opp_wrc),
            }
        rows.append(entry)

    rows.sort(key=lambda r: r["commence"] or "")

    # Slate-wide ranking of individual starter matchups, worst spot first.
    ranked = []
    for game in rows:
        for side in ("away", "home"):
            s = game["sides"][side]
            if s["pressure"] is None:
                continue
            ranked.append({
                "pitcher": s["pitcher"],
                "hand": s["hand"],
                "team": s["team"],
                "opponent_offense": s["opponent_offense"],
                "opp_wrc_vs_hand": s["opp_wrc_vs_hand"],
                "pressure": s["pressure"],
                "flags": s["flags"],
            })
    ranked.sort(key=lambda r: -r["pressure"])

    return {
        "date": slate_date,
        "games": len(rows),
        "wrc_as_of": wrc_as_of,
        "recent_window_starts": RECENT_N,
        "slate": rows,
        "ranked_pressure": ranked,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _fmt(value, spec="{:.2f}", dash="--"):
    return dash if value is None else spec.format(value)


def _signed(value, spec="{:+.2f}", dash=""):
    return dash if value is None else spec.format(value)


def render(report):
    lines = []
    lines.append(f"MLB slate scouting — {report['date']}  ({report['games']} games)")
    lines.append(f"wRC+ snapshot as of {report['wrc_as_of']} | "
                 f"form window = last {report['recent_window_starts']} starts")
    lines.append("")

    for game in report["slate"]:
        lines.append(f"{game['matchup']}   "
                     f"ML {game['away_ml']:+d}/{game['home_ml']:+d}   "
                     f"total {game['total']}")
        for side in ("away", "home"):
            s = game["sides"][side]
            if not s["pitcher"]:
                lines.append(f"   {side:<5} TBD")
                continue
            season = s["form"].get("season") or {}
            recent = s["form"].get("recent") or {}
            t = s["trend"]
            flags = f"  [{', '.join(s['flags'])}]" if s["flags"] else ""
            lines.append(
                f"   {s['pitcher']} ({s['hand']}) vs {s['opponent_offense']} "
                f"wRC+ {_fmt(s['opp_wrc_vs_hand'], '{:.0f}')} "
                f"(gap {_signed(s['opp_platoon_gap'], '{:+.0f}')}){flags}"
            )
            lines.append(
                f"      season {season.get('gs', 0):>2} GS  "
                f"ERA {_fmt(season.get('era'))}  K% {_fmt(season.get('k_pct'), '{:.1f}')}  "
                f"BB% {_fmt(season.get('bb_pct'), '{:.1f}')}  IP/GS {_fmt(season.get('ip_per_gs'))}"
            )
            lines.append(
                f"      last{RECENT_N}  {recent.get('gs', 0):>2} GS  "
                f"ERA {_fmt(recent.get('era'))} {_signed(t.get('era'))}  "
                f"K% {_fmt(recent.get('k_pct'), '{:.1f}')} {_signed(t.get('k_pct'), '{:+.1f}')}  "
                f"BB% {_fmt(recent.get('bb_pct'), '{:.1f}')} {_signed(t.get('bb_pct'), '{:+.1f}')}  "
                f"IP/GS {_fmt(recent.get('ip_per_gs'))}"
            )
            lines.append(f"      pressure {_fmt(s['pressure'], '{:+.1f}')}")
        lines.append("")

    lines.append("Matchup pressure, worst spot first "
                 "(+ = offense outclasses the arm):")
    for r in report["ranked_pressure"]:
        flags = f"  [{', '.join(r['flags'])}]" if r["flags"] else ""
        lines.append(
            f"   {r['pressure']:+6.1f}  {r['pitcher']} ({r['hand']}, {r['team']}) "
            f"vs {r['opponent_offense']} "
            f"wRC+ {_fmt(r['opp_wrc_vs_hand'], '{:.0f}')}{flags}"
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=date.today().isoformat(),
                        help="slate date, YYYY-MM-DD (default: today)")
    parser.add_argument("--json", action="store_true",
                        help="emit the report as JSON instead of a table")
    args = parser.parse_args()

    report = build_report(args.date)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render(report))


if __name__ == "__main__":
    main()

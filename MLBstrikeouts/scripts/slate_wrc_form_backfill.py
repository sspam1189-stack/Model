"""
slate_wrc_form_backfill.py — Walk-forward grading of the slate scouting report.

Answers "what would this have told us, and would it have been right", for a
range of past slates. Everything is reconstructed as of the morning of each
slate; nothing downstream of first pitch is allowed to leak in.

Point-in-time reconstruction
----------------------------
The published ``mlb-team-woba-splits.json`` is a CURRENT snapshot — its
"season" window runs through the latest game played — so replaying it against
an 08-09 slate would leak a week of results. Instead this rebuilds wRC+ for
each slate date from the PA-level rows in ``team_pa_splits_2026.json``,
keeping only ``game_date < slate_date`` and reapplying the same formula as
``build_team_woba_splits.py``: league baselines per (hand, role), wOBA on the
shared linear weights, and the PA-weighted park adjustment. Starter form is
already date-filtered by ``slate_wrc_form.form_for``.

Play rules
----------
The rules are mechanical, so this grades the method rather than my judgement:

  * TOTALS — back the Over on the ``--top`` games with the highest combined
    matchup pressure, and the Under on the ``--top`` lowest.
  * SIDES  — fade the single worst individual starter spot on the slate: back
    the opposing moneyline.

``--clean-only`` additionally skips any game whose starter carries a data flag
(opener, thin-sample, layoff, stale-window), which is the discipline the
report's flags exist to enforce.

Staking is flat 1u, matching the live dashboard (see MLBstrikeouts/CLAUDE.md).

Usage
-----
    python MLBstrikeouts/scripts/slate_wrc_form_backfill.py --start 2026-08-08 --end 2026-08-15
    python MLBstrikeouts/scripts/slate_wrc_form_backfill.py --start 2026-08-08 --clean-only
    python MLBstrikeouts/scripts/slate_wrc_form_backfill.py --start 2026-08-01 --picks
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import slate_wrc_form as S
from sources.park_factors import PRIOR_PARK_FACTORS

PA_SPLITS = S.REPO / "data" / "pitcher_cache" / "mlb" / "team_pa_splits_2026.json"

# Same constants build_team_woba_splits.py uses, so the reconstruction matches
# the published table rather than inventing a second metric.
W = {"bb": 0.69, "hbp": 0.72, "s": 0.88, "d": 1.24, "t": 1.57, "hr": 2.00}
WOBA_SCALE = 1.24
LG_R_PA = 0.117
PARK_PF = {tm: (f.get("tb") or 1.0) for tm, f in PRIOR_PARK_FACTORS.items()}
ACC = ["pa", "ab", "h", "doubles", "triples", "hr", "bb", "hbp"]

FLAT_STAKE = 1.0


def _blank():
    return {k: 0 for k in ACC}


def _woba(d):
    singles = d["h"] - d["doubles"] - d["triples"] - d["hr"]
    num = (W["bb"] * d["bb"] + W["hbp"] * d["hbp"] + W["s"] * singles
           + W["d"] * d["doubles"] + W["t"] * d["triples"] + W["hr"] * d["hr"])
    den = d["ab"] + d["bb"] + d["hbp"]
    return num / den if den else 0.0


def wrc_as_of(pa_rows, cutoff):
    """
    Rebuild all-role team wRC+ by hand using only games before ``cutoff``.

    Mirrors build_team_woba_splits.py: aggregate by (team, hand, role), take
    league baselines per (hand, role), combine roles for the "all" node, and
    subtract the PA-weighted park inflation.
    """
    agg = defaultdict(_blank)
    park = defaultdict(lambda: [0.0, 0])
    lg = defaultdict(_blank)

    for r in pa_rows:
        if not (r.get("game_date") and r["game_date"] < cutoff):
            continue
        hand, team = r.get("opp_hand"), r.get("team")
        if hand not in ("L", "R") or not team:
            continue
        role = r.get("role") or "SP"
        a, lgb = agg[(team, hand, role)], lg[(hand, role)]
        for k in ACC:
            v = r.get(k) or 0
            a[k] += v
            lgb[k] += v
        venue_park = team if r.get("is_home") else r.get("opp")
        pa = r.get("pa") or 0
        acc = park[(team, hand)]
        acc[0] += pa * PARK_PF.get(venue_park, 1.0)
        acc[1] += pa

    # League baseline across both roles, matching the "ALL" node.
    lg_all = {}
    for hand in ("L", "R"):
        comb = _blank()
        for role in ("SP", "RP"):
            for k in ACC:
                comb[k] += lg[(hand, role)][k]
        lg_all[hand] = _woba(comb)

    teams = defaultdict(dict)
    for (team, hand, _role) in list(agg):
        if hand in teams[team]:
            continue
        combined = _blank()
        for role in ("SP", "RP"):
            for k in ACC:
                combined[k] += agg[(team, hand, role)][k]
        if not (combined["ab"] + combined["bb"] + combined["hbp"]):
            continue
        neutral = 100 * ((_woba(combined) - lg_all[hand]) / WOBA_SCALE
                         + LG_R_PA) / LG_R_PA
        num, den = park[(team, hand)]
        avg_pf = (num / den) if den else 1.0
        teams[team][hand] = {
            "wrcplus": round(neutral - 100 * (avg_pf - 1.0)),
            "pa": combined["pa"],
        }

    # Shaped like the published windows dict so wrc_cell can read it unchanged.
    return {
        "season": {
            "teams": {
                t: {"vsLHP": v.get("L"), "vsRHP": v.get("R")}
                for t, v in teams.items()
            }
        }
    }


def american_profit(odds, stake=FLAT_STAKE):
    return stake * (odds / 100.0 if odds > 0 else 100.0 / abs(odds))


def grade_total(runs, line, side):
    if runs == line:
        return "push"
    over = runs > line
    return "win" if (over == (side == "over")) else "loss"


def build_day(slate_date, games, pa_rows, starts_by_name):
    """Score every game on one slate using only pre-slate information."""
    wrc = wrc_as_of(pa_rows, slate_date)
    rows = []
    for game in games:
        sides = {}
        for side, offense in (("away", game["home"]), ("home", game["away"])):
            listed = game.get(f"{side}_pitcher")
            hand = game.get(f"{side}_hand")
            name = S.resolve_pitcher(listed, starts_by_name, game[side])
            form = S.form_for(starts_by_name.get(name, []), slate_date) if name else {}
            cell = S.wrc_cell(wrc, "season", offense, hand, role="all")
            opp_wrc = cell["wrcplus"] if cell else None
            sides[side] = {
                "pitcher": name or listed,
                "hand": hand,
                "team": game[side],
                "offense": offense,
                "opp_wrc": opp_wrc,
                "flags": S.role_flags(form, slate_date) if form else ["no-form"],
                "pressure": S.pressure(form, opp_wrc) if form else None,
            }
        ps = [s["pressure"] for s in sides.values() if s["pressure"] is not None]
        rows.append({
            "date": slate_date,
            "matchup": f"{game['away']} @ {game['home']}",
            "game": game,
            "sides": sides,
            "combined": (sum(ps) / len(ps)) if ps else None,
            "runs": (game.get("home_score") or 0) + (game.get("away_score") or 0),
            "flags": sorted({f for s in sides.values() for f in s["flags"]}),
        })
    return rows


def pick_day(rows, top, clean_only):
    """Apply the mechanical play rules to one graded slate."""
    usable = [r for r in rows if r["combined"] is not None
              and r["game"].get("total_line") is not None]
    if clean_only:
        usable = [r for r in usable if not r["flags"]]
    ranked = sorted(usable, key=lambda r: -r["combined"])
    picks = []

    for r in ranked[:top]:
        picks.append({"kind": "total", "side": "over", "row": r,
                      "odds": r["game"].get("over_ml")})
    for r in ranked[-top:] if len(ranked) > top else []:
        picks.append({"kind": "total", "side": "under", "row": r,
                      "odds": r["game"].get("under_ml")})

    # Fade the worst single spot: back the offense that faces him.
    worst = None
    for r in usable:
        for side, s in r["sides"].items():
            if s["pressure"] is None or (clean_only and s["flags"]):
                continue
            if worst is None or s["pressure"] > worst[1]["pressure"]:
                worst = (r, s, side)
    if worst:
        r, s, side = worst
        back = "home" if side == "away" else "away"
        picks.append({"kind": "side", "side": back, "row": r,
                      "odds": r["game"].get(f"{back}_ml"), "faded": s["pitcher"]})
    return picks


def grade(picks):
    out = []
    for p in picks:
        r, g = p["row"], p["row"]["game"]
        if p["kind"] == "total":
            res = grade_total(r["runs"], g["total_line"], p["side"])
            label = f"{p['side'].title()} {g['total_line']}"
        else:
            won = (g.get("home_win") is True) if p["side"] == "home" else (g.get("home_win") is False)
            res = "win" if won else "loss"
            label = f"{g[p['side']]} ML (fade {p.get('faded')})"
        odds = p["odds"]
        units = 0.0 if res == "push" else (
            american_profit(odds) if res == "win" else -FLAT_STAKE)
        out.append({**p, "result": res, "label": label, "units": units})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True, help="first slate date, YYYY-MM-DD")
    ap.add_argument("--end", help="last slate date (default: latest available)")
    ap.add_argument("--top", type=int, default=2,
                    help="how many games per side of the pressure ranking to back")
    ap.add_argument("--clean-only", action="store_true",
                    help="skip games whose starters carry a data flag")
    ap.add_argument("--picks", action="store_true", help="print every graded pick")
    args = ap.parse_args()

    allml = S._load(S.ALL_ML)
    pa_rows = S._load(PA_SPLITS)
    starts_by_name = S.organize_starts(S._load(S.GAME_LOGS))

    by_date = defaultdict(list)
    seen = set()
    for g in allml.get("games", []):
        if g.get("home_score") is None or g.get("away_score") is None:
            continue
        key = (g.get("date"), g.get("away"), g.get("home"), g.get("commence"))
        if key in seen:
            continue
        seen.add(key)
        by_date[g["date"]].append(g)

    dates = sorted(d for d in by_date
                   if d >= args.start and (not args.end or d <= args.end))
    if not dates:
        print("no completed slates in range")
        return

    ledger, per_day = [], []
    for d in dates:
        rows = build_day(d, by_date[d], pa_rows, starts_by_name)
        graded = grade(pick_day(rows, args.top, args.clean_only))
        ledger += graded
        per_day.append((d, len(by_date[d]), graded))

    print(f"Walk-forward backfill — {dates[0]} to {dates[-1]}  "
          f"({len(dates)} slates, top-{args.top}"
          f"{', clean-only' if args.clean_only else ''})")
    print("Point-in-time wRC+ rebuilt per slate; flat 1u.\n")

    for d, n, graded in per_day:
        u = sum(g["units"] for g in graded)
        rec = "-".join(str(sum(1 for g in graded if g["result"] == k))
                       for k in ("win", "loss", "push"))
        print(f"  {d}  ({n:>2} games)  {rec:<8} {u:+6.2f}u")
        if args.picks:
            for g in graded:
                print(f"        {g['result']:<5} {g['row']['matchup']:<12} "
                      f"{g['label']:<28} runs {g['row']['runs']:<3} "
                      f"{g['odds']:+5d}  {g['units']:+.2f}u")

    for name, sel in (("TOTALS", lambda g: g["kind"] == "total"),
                      ("  overs", lambda g: g["kind"] == "total" and g["side"] == "over"),
                      ("  unders", lambda g: g["kind"] == "total" and g["side"] == "under"),
                      ("SIDES", lambda g: g["kind"] == "side"),
                      ("ALL", lambda g: True)):
        sub = [g for g in ledger if sel(g)]
        if not sub:
            continue
        w = sum(1 for g in sub if g["result"] == "win")
        l = sum(1 for g in sub if g["result"] == "loss")
        p = sum(1 for g in sub if g["result"] == "push")
        u = sum(g["units"] for g in sub)
        decided = w + l
        print(f"\n{name:<10} {w}-{l}-{p}  "
              f"({(w / decided * 100) if decided else 0:.1f}%)  "
              f"{u:+.2f}u  ROI {(u / len(sub) * 100):+.1f}%")


if __name__ == "__main__":
    main()

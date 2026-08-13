#!/usr/bin/env python3
"""
Scouting read on a slate using team wRC+ platoon splits + starter form.

VIEW-ONLY. This is the same posture as the Fade ML wRC+ table
(docs/superpowers/specs/2026-07-28-mlb-fade-ml-wrc-table-design.md): nothing
here feeds a pick, a gate, a size, or a grade. It is a "where does each side
stand" overlay you can hold next to a posted board.

Inputs (all in-repo, no network):
  data/mlb-team-woba-splits.json  self-computed park-adjusted wRC+ by window,
                                  by actual pitcher hand, SP-only / RP-only,
                                  home/road. Regenerated daily.
  data/mlb-props.json             per-start actuals (outs, K, BF, pitches) ->
                                  starter form.
  data/mlb-all-ml.json            season game log w/ starters + finals ->
                                  team runs allowed in each starter's starts,
                                  and the league run environment.
  data/kalman_state.json          the K-model's recency-weighted per-start
                                  K / outs / H / BB means.

Usage:
  python scripts/analyze_slate_wrc_form.py                    # today's unplayed games
  python scripts/analyze_slate_wrc_form.py --games LAD MIN LAA
  python scripts/analyze_slate_wrc_form.py --board board.json # posted prices to compare
  python scripts/analyze_slate_wrc_form.py --window last30      # headline window
  python scripts/analyze_slate_wrc_form.py --price-window last30 # what prices it
  python scripts/analyze_slate_wrc_form.py --json             # machine-readable

--board takes {"<home_abbr>": {"home_ml": -139, "away_ml": 126,
                              "total": 8, "over_ml": -115, "under_ml": -105,
                              "book": "..."}}
and overrides the FanDuel prices carried in mlb-all-ml.json.
"""

import argparse
import json
import math
from datetime import date
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

# --- scouting constants (documented so the read is auditable) ---------------
# Two different jobs, two different windows.
#
# DISPLAY is what the Fade ML tab shows by default (DEFAULT_WRC_WIN in
# PythonDashboard/js/mlb-fade-ml.js), so the headline here reconciles with the
# number on screen.
DISPLAY_WINDOW = "last20"
# PRICING is what the run math actually uses. --calibrate 45 (2026-08-13) swept
# every window as the recent leg; season-only was the best calibrated and the
# dashboard's last20 was the worst:
#   season  41.5/44.5  48.8/49.7  55.3/57.7  63.6/61.3   (pred/actual by bucket)
#   last30  40.5/38.6  48.7/51.9  55.7/59.8  64.8/60.8
#   last20  39.8/38.1  48.4/57.1  55.5/58.9  65.0/54.4
# Short windows are 150-300 PA -- fine as a scouting chip, too noisy to price.
PRICING_WINDOW = "season"
# Weight on the recent leg when pricing off a non-season window; season anchors
# the remainder.
WINDOW_BLEND_RECENT = 0.50
# A window is dropped (and the blend renormalized) below this many PA.
MIN_PA = 60
# Team runs-allowed in a starter's starts is a noisy, bullpen-contaminated
# proxy for his run prevention, so regress it halfway to league average.
RA_REGRESSION = 0.50
# Starter K% blend (season carries it, last 5 tilts it).
KPCT_BLEND = [("season", 0.60), ("last5", 0.40)]
# Runs suppressed per point of K% above league average, as a multiplier on the
# opposing offense. ~5 pts of K% above average -> ~9% fewer runs.
K_RUNS_PER_PCT = 0.018
# Runs -> win probability. Logistic scale calibrated so a +1.0 run edge sits
# near -170, which is where MLB run-diff-to-price lands empirically.
RUNS_TO_WP_SCALE = 1.90
# Home-field edge in runs, on top of the park factor already in the wRC+.
HFA_RUNS = 0.15
# Our run-diff estimate is noisier than a true talent run diff, so it overshoots
# at the extremes. --calibrate 30 (2026-08-13) had predicted win prob ranging
# 36.6%-68.0% where the games actually landed 42.0%-60.7%, i.e. roughly 0.6 of
# the spread we were claiming. Shrink the diff before pricing it. Re-fit this
# constant with --calibrate whenever the run environment moves.
DIFF_SHRINK = 0.60


# --- odds helpers ----------------------------------------------------------
def implied(american):
    """American odds -> implied probability (with vig)."""
    if american is None:
        return None
    a = float(american)
    return -a / (-a + 100.0) if a < 0 else 100.0 / (a + 100.0)


def no_vig(a, b):
    """Two-way American prices -> vig-free probabilities."""
    pa, pb = implied(a), implied(b)
    if pa is None or pb is None:
        return None, None
    tot = pa + pb
    return pa / tot, pb / tot


def to_american(p):
    """Probability -> fair American price."""
    if not p or p <= 0 or p >= 1:
        return None
    return -round(100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def fmt_odds(o):
    if o is None:
        return "n/a"
    return f"+{o}" if o > 0 else str(o)


# --- data loading ----------------------------------------------------------
def load(name):
    with open(DATA / name, encoding="utf-8") as fh:
        return json.load(fh)


def league_run_env(games):
    """League runs per team per game, from every completed game on file."""
    runs = n = 0
    for g in games:
        if g.get("home_score") is None or g.get("away_score") is None:
            continue
        runs += g["home_score"] + g["away_score"]
        n += 2
    return runs / n if n else 4.5


def league_starter_baseline(props, as_of):
    """League-average starter K% and IP/start, from graded prop rows."""
    k = bf = outs = starts = 0
    for r in props:
        if (r.get("market") != "strikeouts" or r.get("actual_outs") is None
                or r.get("date", "") >= as_of):
            continue
        k += r["actual"]
        bf += r["actual_bf"]
        outs += r["actual_outs"]
        starts += 1
    return {
        "k_pct": 100 * k / bf if bf else 22.0,
        "ip_per_start": outs / 3 / starts if starts else 5.2,
        "starts": starts,
    }


# --- offense: wRC+ vs the hand they're facing -------------------------------
def _sp_node(splits, wname, team, roster="team"):
    """
    The SP-only node for a team in one window. `roster='lineup'` selects the
    dashboard's confirmed-nine rows where that team has posted.

    Caveat that keeps the lineup rows OUT of the run math: a starting nine
    excludes bench and pinch-hit PAs, but the league baseline the wRC+ is
    computed against does not. Measured on 2026-08-13 the confirmed-nine rows
    ran +8.0 wRC+ above the whole-team rows on the season (n=28 team-hand
    pairs). Great as a scouting chip, a level bias in a runs estimate.
    """
    t = splits["windows"].get(wname, {}).get("teams", {}).get(team, {})
    base = t.get("lineup") if (roster == "lineup" and t.get("lineup")) else t
    return base.get("sp", {}), bool(roster == "lineup" and t.get("lineup"))


def _cell(node, key):
    """wRC+ / wOBA / PA for one hand out of an SP node."""
    cell = node.get(key) or {}
    # PA lives on the home/road rows; sum them for the split's total.
    pa = sum((node.get(s, {}).get(key, {}) or {}).get("pa", 0)
             for s in ("home", "road"))
    return {"wrcplus": cell.get("wrcplus"), "woba": cell.get("woba"), "pa": pa}


def offense_vs_hand(splits, team, hand, side, display_window,
                    pricing_window, roster):
    """
    wRC+ for `team` against `hand` starters: the dashboard's selected window as
    the headline, blended with the season anchor for the run math, plus the
    home-or-road version of the same split.

    Uses the SP-only rows: we are pricing the starter's share of the game, and
    the RP-only rows are a different (and later) matchup.
    """
    key = "vsLHP" if hand == "L" else "vsRHP"
    detail, num, den = {}, 0.0, 0.0

    blend = [(pricing_window, WINDOW_BLEND_RECENT),
             ("season", 1 - WINDOW_BLEND_RECENT)]
    if pricing_window == "season":
        blend = [("season", 1.0)]

    # Whole-team rows only — see _sp_node on why the confirmed nine is context.
    for wname, weight in blend:
        node, _ = _sp_node(splits, wname, team)
        d = _cell(node, key)
        d["used"] = d["wrcplus"] is not None and d["pa"] >= MIN_PA
        detail[wname] = d
        if d["used"]:
            num += d["wrcplus"] * weight
            den += weight

    # Context windows — shown, never blended.
    for wname in (display_window, "last30", "last15", "2026-08"):
        if wname not in detail:
            node, _ = _sp_node(splits, wname, team)
            d = _cell(node, key)
            d["used"] = False
            detail[wname] = d

    season_node, _ = _sp_node(splits, "season", team)
    venue = (season_node.get(side, {}) or {}).get(key, {}) or {}

    # Confirmed-nine chip, matching the dashboard's roster selector.
    lineup = None
    if roster == "lineup":
        ln, posted = _sp_node(splits, display_window, team, "lineup")
        if posted:
            lineup = _cell(ln, key)
            lineup["delta_vs_team"] = (
                lineup["wrcplus"] - detail[display_window]["wrcplus"]
                if lineup["wrcplus"] is not None
                and detail.get(display_window, {}).get("wrcplus") is not None
                else None)

    return {
        "team": team,
        "vs_hand": hand,
        "side": side,
        "window": display_window,
        "pricing_window": pricing_window,
        "lineup": lineup,
        "headline_wrcplus": detail.get(display_window, {}).get("wrcplus"),
        "blended_wrcplus": round(num / den, 1) if den else None,
        "blend_weight_used": round(den, 2),
        "windows": detail,
        "venue_split": {"wrcplus": venue.get("wrcplus"), "pa": venue.get("pa")},
        "park_factor": (season_node.get(key) or {}).get("parkFactor"),
    }


# --- starter: recent form ---------------------------------------------------
def starter_form(props, allml, kalman, name, as_of):
    """Season and last-N form for one starter, from graded rows only."""
    rows = sorted(
        (r for r in props
         if r.get("player") == name
         and r.get("market") == "strikeouts"
         and r.get("actual_outs") is not None
         and r.get("date", "") < as_of),
        key=lambda r: r["date"],
    )

    def agg(rs):
        if not rs:
            return None
        outs = sum(r["actual_outs"] for r in rs)
        bf = sum(r["actual_bf"] for r in rs)
        k = sum(r["actual"] for r in rs)
        return {
            "starts": len(rs),
            "ip_per_start": round(outs / 3 / len(rs), 2),
            "k_pct": round(100 * k / bf, 1) if bf else None,
            "pitches_per_start": round(sum(r["actual_pitches"] for r in rs) / len(rs), 1),
        }

    # Team runs allowed in his starts — includes the bullpen, so it is a team
    # run-prevention line for his starts, not his ERA. Labeled as such.
    ra = []
    for g in allml:
        if g.get("home_score") is None or g.get("date", "") >= as_of:
            continue
        if g.get("home_pitcher") == name:
            ra.append(g["away_score"])
        elif g.get("away_pitcher") == name:
            ra.append(g["home_score"])

    kal = next((v for v in kalman.get("pitchers", {}).values()
                if v.get("name") == name), None)

    season, last5, last3 = agg(rows), agg(rows[-5:]), agg(rows[-3:])
    return {
        "player": name,
        "season": season,
        "last5": last5,
        "last3": last3,
        "k_pct_delta_l5": (round(last5["k_pct"] - season["k_pct"], 1)
                           if season and last5 and season["k_pct"] and last5["k_pct"]
                           else None),
        "ip_delta_l5": (round(last5["ip_per_start"] - season["ip_per_start"], 2)
                        if season and last5 else None),
        "team_ra_per_start": round(sum(ra) / len(ra), 2) if ra else None,
        "team_ra_per_start_l5": round(sum(ra[-5:]) / len(ra[-5:]), 2) if ra else None,
        "team_ra_log_l5": ra[-5:],
        "kalman": ({k: round(v["mean"], 2) for k, v in kal["stats"].items()}
                   if kal else None),
        "kalman_starts": kal.get("gamesProcessed") if kal else None,
        "last_start_date": rows[-1]["date"] if rows else None,
        "days_rest": ((date.fromisoformat(as_of) - date.fromisoformat(rows[-1]["date"])).days
                      if rows else None),
    }


# --- the read ---------------------------------------------------------------
def suppression(form, lg_rpg, lg_sp):
    """
    How much the opposing side's run prevention scales an offense, as a
    multiplier centered on 1.0. Split by who is actually pitching:

      starter's innings  -> his own K% vs league (a pitcher-level signal, so an
                            ace is not washed out by the team around him)
      the rest of the game -> team runs allowed in his starts, regressed
                            halfway to league (bullpen + defense + noise)
    """
    k_parts = [(form.get(w) or {}).get("k_pct") for w, _ in KPCT_BLEND]
    weights = [wt for (_, wt), v in zip(KPCT_BLEND, k_parts) if v is not None]
    vals = [v for v in k_parts if v is not None]
    k_pct = sum(v * w for v, w in zip(vals, weights)) / sum(weights) if vals else None

    starter_supp = (1.0 - K_RUNS_PER_PCT * (k_pct - lg_sp["k_pct"])
                    if k_pct is not None else 1.0)

    ra = form.get("team_ra_per_start")
    team_supp = (1.0 + (ra / lg_rpg - 1.0) * (1 - RA_REGRESSION)) if ra else 1.0

    ip = ((form.get("last5") or form.get("season") or {}).get("ip_per_start")
          or lg_sp["ip_per_start"])
    share = max(0.0, min(1.0, ip / 9.0))

    return {
        "total": share * starter_supp + (1 - share) * team_supp,
        "starter_k_supp": starter_supp,
        "team_ra_supp": team_supp,
        "innings_share": share,
        "k_pct_blend": round(k_pct, 1) if k_pct is not None else None,
    }


def expected_runs(offense, opp_form, lg_rpg, lg_sp):
    """
    Indicative runs for one offense: league R/G scaled by its blended wRC+
    against the hand it faces, then scaled by the opposing side's suppression.
    """
    wrc = offense["blended_wrcplus"]
    if wrc is None:
        return None, None
    supp = suppression(opp_form, lg_rpg, lg_sp)
    base = lg_rpg * wrc / 100.0
    return round(base * supp["total"], 2), {k: (round(v, 3) if isinstance(v, float) else v)
                                            for k, v in supp.items()}


def read_game(game, splits, props, allml, kalman, as_of, lg_rpg, lg_sp, board,
              display_window=DISPLAY_WINDOW, pricing_window=PRICING_WINDOW,
              roster="lineup"):
    home, away = game["home"], game["away"]
    hp, ap = game.get("home_pitcher"), game.get("away_pitcher")
    hhand, ahand = game.get("home_hand"), game.get("away_hand")

    # Home bats against the away starter's hand, and vice versa.
    off_home = offense_vs_hand(splits, home, ahand, "home", display_window,
                               pricing_window, roster)
    off_away = offense_vs_hand(splits, away, hhand, "road", display_window,
                               pricing_window, roster)
    form_home = starter_form(props, allml, kalman, hp, as_of)
    form_away = starter_form(props, allml, kalman, ap, as_of)

    rh, sup_h = expected_runs(off_home, form_away, lg_rpg, lg_sp)
    ra_, sup_a = expected_runs(off_away, form_home, lg_rpg, lg_sp)

    wp_home = None
    diff = None
    if rh is not None and ra_ is not None:
        diff = round(rh + HFA_RUNS - ra_, 2)
        wp_home = 1 / (1 + math.exp(-(diff * DIFF_SHRINK) / RUNS_TO_WP_SCALE))

    prices = board.get(home, {})
    home_ml = prices.get("home_ml", game.get("home_ml"))
    away_ml = prices.get("away_ml", game.get("away_ml"))
    total = prices.get("total", game.get("total_line"))
    nv_home, nv_away = no_vig(home_ml, away_ml)

    return {
        "matchup": f"{away} @ {home}",
        "commence": game.get("commence"),
        "market": {
            "book": prices.get("book", "fanduel (payload)"),
            "home_ml": home_ml, "away_ml": away_ml, "total": total,
            "over_ml": prices.get("over_ml", game.get("over_ml")),
            "under_ml": prices.get("under_ml", game.get("under_ml")),
            "no_vig_home": round(nv_home, 4) if nv_home else None,
            "no_vig_away": round(nv_away, 4) if nv_away else None,
        },
        "home": {
            "team": home, "starter": hp, "hand": hhand,
            "form": form_home, "offense_vs_opp_starter": off_home,
            "exp_runs": rh, "opp_suppression": sup_h,
        },
        "away": {
            "team": away, "starter": ap, "hand": ahand,
            "form": form_away, "offense_vs_opp_starter": off_away,
            "exp_runs": ra_, "opp_suppression": sup_a,
        },
        "read": {
            "run_diff_home": diff,
            "exp_total": round(rh + ra_, 2) if rh is not None and ra_ is not None else None,
            "wp_home": round(wp_home, 4) if wp_home else None,
            "fair_home_ml": to_american(wp_home) if wp_home else None,
            "fair_away_ml": to_american(1 - wp_home) if wp_home else None,
            "edge_home_pts": (round(100 * (wp_home - nv_home), 1)
                              if wp_home and nv_home else None),
        },
    }


# --- rendering --------------------------------------------------------------
def render(r):
    m, h, a, rd = r["market"], r["home"], r["away"], r["read"]
    out = []
    out.append("=" * 78)
    out.append(f"{r['matchup']}   {r['commence']}")
    out.append(f"  board ({m['book']}): {a['team']} {fmt_odds(m['away_ml'])} / "
               f"{h['team']} {fmt_odds(m['home_ml'])}   total {m['total']} "
               f"(O {fmt_odds(m['over_ml'])} / U {fmt_odds(m['under_ml'])})")
    if m["no_vig_home"]:
        out.append(f"  no-vig: {h['team']} {m['no_vig_home']*100:.1f}%  "
                   f"{a['team']} {m['no_vig_away']*100:.1f}%")
    out.append("")

    for side, opp, label in ((a, h, "AWAY"), (h, a, "HOME")):
        f, o = side["form"], side["offense_vs_opp_starter"]
        out.append(f"  {label}  {side['team']}  —  {side['starter']} ({side['hand']}HP)")
        if f["season"] and f["last5"]:
            out.append(f"    form   season {f['season']['starts']} GS: "
                       f"{f['season']['ip_per_start']} IP/GS, K% {f['season']['k_pct']}, "
                       f"{f['season']['pitches_per_start']} pit")
            out.append(f"           last 5 GS: {f['last5']['ip_per_start']} IP/GS "
                       f"({f['ip_delta_l5']:+}), K% {f['last5']['k_pct']} "
                       f"({f['k_pct_delta_l5']:+}), {f['last5']['pitches_per_start']} pit")
            out.append(f"           last 3 GS: {f['last3']['ip_per_start']} IP/GS, "
                       f"K% {f['last3']['k_pct']}   "
                       f"(last start {f['last_start_date']}, {f['days_rest']}d rest)")
        if f["team_ra_per_start"] is not None:
            out.append(f"           team R allowed in his GS: {f['team_ra_per_start']}/GS "
                       f"season, {f['team_ra_per_start_l5']}/GS last 5 {f['team_ra_log_l5']}")
        if f["kalman"]:
            k = f["kalman"]
            out.append(f"           kalman ({f['kalman_starts']} GS): "
                       f"{k['k']} K, {k['outs']} outs, {k['h']} H, {k['bb']} BB")
        out.append(f"    offense vs {o['vs_hand']}HP starters "
                   f"(self-computed park-adj wRC+, whole team):")
        w = o["windows"]
        order = [o["window"]] + [x for x in ("season", "last30", "last15", "2026-08")
                                 if x != o["window"]]
        parts = []
        for wname in order:
            d = w.get(wname)
            if not d or d.get("wrcplus") is None:
                continue
            flag = "" if d.get("used") else "*"
            parts.append(f"{wname} {d['wrcplus']}{flag} ({d['pa']} PA)")
        out.append("           " + "  |  ".join(parts))
        out.append(f"           dashboard window ({o['window']}) "
                   f"{o['headline_wrcplus']}  |  priced off "
                   f"{o['pricing_window']} -> {o['blended_wrcplus']}  |  "
                   f"{o['side']}-only season {o['venue_split']['wrcplus']} "
                   f"({o['venue_split']['pa']} PA)  |  park {o['park_factor']}")
        if o.get("lineup") and o["lineup"].get("wrcplus") is not None:
            ln = o["lineup"]
            out.append(f"           confirmed 9 ({o['window']}): {ln['wrcplus']} "
                       f"({ln['pa']} PA, {ln['delta_vs_team']:+} vs whole team) "
                       f"— context only, runs a level above the league baseline")
        s = side["opp_suppression"] or {}
        out.append(f"    -> {side['team']} indicative runs {side['exp_runs']}  "
                   f"[suppressed x{s.get('total')} by {opp['team']}: "
                   f"{s.get('innings_share')} of the game vs {opp['starter']} "
                   f"at K% {s.get('k_pct_blend')} (x{s.get('starter_k_supp')}), "
                   f"rest vs bullpen/defense at team RA "
                   f"(x{s.get('team_ra_supp')})]")
        out.append("")

    if rd["wp_home"]:
        out.append(f"  READ  {h['team']} run diff {rd['run_diff_home']:+} "
                   f"(incl. {HFA_RUNS} HFA), indicative total {rd['exp_total']}")
        out.append(f"        fair: {h['team']} {fmt_odds(rd['fair_home_ml'])} / "
                   f"{a['team']} {fmt_odds(rd['fair_away_ml'])}   "
                   f"vs no-vig board -> {h['team']} {rd['edge_home_pts']:+} pts")
    out.append("  (* below the PA floor / context only — not in the blend)")
    return "\n".join(out)


def calibrate(games, splits, props, kalman, lg_rpg, lg_sp, days, as_of,
              pricing_window=PRICING_WINDOW, roster='team'):
    """
    Rough bias check on the two scale constants (run level and RUNS_TO_WP_SCALE)
    against recently completed games.

    CAVEAT, and it is not a small one: the wRC+ splits file is a *current*
    snapshot, so every replayed game sees offense data that postdates it. This
    catches a systematic run-level or win-prob bias; it is not a backtest and
    must not be read as one.
    """
    cutoff = date.fromisoformat(as_of).toordinal() - days
    rows = [g for g in games
            if g.get("home_score") is not None
            and g.get("home_pitcher") and g.get("away_pitcher")
            and date.fromisoformat(g["date"]).toordinal() >= cutoff
            and g["date"] < as_of]

    tot_err, wp_rows, n = [], [], 0
    for g in rows:
        try:
            r = read_game(g, splits, props, games, kalman, g["date"], lg_rpg,
                          lg_sp, {}, DISPLAY_WINDOW, pricing_window, roster)
        except (KeyError, TypeError):
            continue
        if r["read"]["exp_total"] is None:
            continue
        n += 1
        tot_err.append(r["read"]["exp_total"] - (g["home_score"] + g["away_score"]))
        wp_rows.append((r["read"]["wp_home"], 1 if g.get("home_win") else 0))

    print(f"calibration check — {n} completed games in the last {days} days")
    if not n:
        return
    print(f"  indicative total bias: {sum(tot_err)/n:+.2f} runs "
          f"(mean |err| {sum(abs(e) for e in tot_err)/n:.2f})")
    for lo, hi in ((0.0, 0.45), (0.45, 0.52), (0.52, 0.60), (0.60, 1.0)):
        b = [(p, w) for p, w in wp_rows if p is not None and lo <= p < hi]
        if b:
            print(f"  wp_home {lo:.2f}-{hi:.2f}: predicted "
                  f"{sum(p for p, _ in b)/len(b)*100:5.1f}%  actual "
                  f"{sum(w for _, w in b)/len(b)*100:5.1f}%  (n={len(b)})")
    print("  NOTE: wRC+ inputs are a current snapshot — this is a bias probe, "
          "not a backtest.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--games", nargs="*", default=None,
                    help="filter by team abbr (either side)")
    ap.add_argument("--board", default=None,
                    help="JSON file of posted prices keyed by home abbr")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--window", default=DISPLAY_WINDOW,
                    help=f"wRC+ window shown as the headline (default "
                         f"{DISPLAY_WINDOW}, matching the Fade ML tab)")
    ap.add_argument("--price-window", dest="price_window",
                    default=PRICING_WINDOW,
                    help=f"wRC+ window the run math prices off (default "
                         f"{PRICING_WINDOW}, best calibrated -- see --calibrate)")
    ap.add_argument("--no-lineup", action="store_true",
                    help="hide the confirmed-nine context chip")
    ap.add_argument("--calibrate", type=int, metavar="DAYS", default=None,
                    help="bias probe against completed games (see caveat)")
    args = ap.parse_args()

    splits = load("mlb-team-woba-splits.json")
    props = load("mlb-props.json")["props"]
    allml = load("mlb-all-ml.json")
    kalman = load("kalman_state.json")
    board = json.loads(Path(args.board).read_text()) if args.board else {}

    lg_rpg = league_run_env(allml["games"])
    lg_sp = league_starter_baseline(props, args.date)

    if args.calibrate:
        calibrate(allml["games"], splits, props, kalman, lg_rpg, lg_sp,
                  args.calibrate, args.date, args.price_window, "team")
        return

    today = [g for g in allml.get("today", []) if g.get("date") == args.date]
    if args.games:
        want = {t.upper() for t in args.games}
        today = [g for g in today if g["home"] in want or g["away"] in want]

    reads = [read_game(g, splits, props, allml["games"], kalman, args.date,
                       lg_rpg, lg_sp, board, args.window, args.price_window,
                       "team" if args.no_lineup else "lineup")
             for g in today]

    if args.json:
        print(json.dumps({
            "date": args.date,
            "league_runs_per_team_game": round(lg_rpg, 3),
            "league_starter_baseline": {k: round(v, 2) for k, v in lg_sp.items()},
            "wrc_source": splits["metric"],
            "wrc_through": splits["throughDate"],
            "wrc_display_window": args.window,
            "wrc_pricing_window": args.price_window,
            "games": reads,
        }, indent=2))
        return

    print(f"MLB slate read — {args.date}")
    print(f"wRC+ source: {splits['metric']}, through {splits['throughDate']} "
          f"(display {args.window}, pricing {args.price_window}, "
          f"whole-team rows)")
    print(f"league run environment: {lg_rpg:.2f} R/team/game; "
          f"league starter {lg_sp['k_pct']:.1f} K%, "
          f"{lg_sp['ip_per_start']:.2f} IP/GS ({lg_sp['starts']} GS)")
    print("VIEW-ONLY scouting overlay — does not feed any pick or grade.")
    for r in reads:
        print(render(r))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
MLBstrikeouts/scripts/build_team_woba_splits.py
Self-computed team offense splits vs LHP / RHP, for ANY time window -- the
calculable alternative to the manual FanGraphs wRC+ snapshot (build_team_wrc.py)
so we can look at June / July / recent instead of only full-season.

Reads the PA-level splits (build_pbp_team_pa.py: team_pa_splits_2026.json),
where each plate appearance is already tallied under the ACTUAL pitcher's hand
(starters AND relievers, from play-by-play) with the batting team's home/away
side and the park. Aggregates by team x hand over each window and computes:
  * wOBA   -- standard linear-weight wOBA (denominator AB+BB+HBP).
  * wRC+   -- PARK-NEUTRAL approximation: 100 * ((wOBA-lgwOBA)/scale + lgR/PA)
              / (lgR/PA), league baseline computed from the SAME window+hand.

NOTE: this is an approximation, not FanGraphs' exact wRC+ (which uses their
proprietary park factors and exact yearly weights). It's park-neutral and uses
fixed weights, so treat it as a relative gauge, not a to-the-point FG match.

Windows: 'season', each calendar month present, and 'last30' / 'last15'
(rolling from the latest game date). Writes mlb-team-woba-splits.json.

Usage:
    cd MLBstrikeouts
    python -m scripts.build_team_woba_splits
"""
import os
import sys
import json
import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "sources"))
from sources.park_factors import PRIOR_PARK_FACTORS

# Single offense park factor per team (its home park), 1.0 = neutral. We use the
# total-bases factor as the run-environment proxy. Because we know each game's
# park (the home team's), we PA-weight the ACTUAL parks a team hit in over the
# window -- more precise than FanGraphs' single regressed home-park factor.
PARK_PF = {tm: (f.get("tb") or 1.0) for tm, f in PRIOR_PARK_FACTORS.items()}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DATA = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "data"))
# TRUE all-pitcher PA-level splits (build_pbp_team_pa.py): each row is one
# (game, batting team, ACTUAL pitcher hand) with wOBA components + venue + park.
PA_SPLITS = os.path.join(ROOT_DATA, "pitcher_cache", "mlb", "team_pa_splits_2026.json")
# Per-batter SEASON splits (batter, hand, venue, role) -> for the 'Confirmed
# Lineup' window (aggregate tonight's confirmed 9 hitters).
BATTER_SPLITS = os.path.join(ROOT_DATA, "pitcher_cache", "mlb", "batter_pa_splits_2026.json")
LINEUP_CACHE_DIR = os.path.join(ROOT_DATA, "pitcher_cache", "mlb")
OUTPUT_PATHS = [
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "mlb-team-woba-splits.json")),
    os.path.normpath(os.path.join(
        SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "mlb-team-woba-splits.json")),
]

# Standard-ish wOBA linear weights + scale (2023-24 vintage). lgR/PA is the
# league runs-per-PA baseline used to convert wRAA into wRC+.
W = {"bb": 0.69, "hbp": 0.72, "s": 0.88, "d": 1.24, "t": 1.57, "hr": 2.00}
WOBA_SCALE = 1.24
LG_R_PA = 0.117
ASB_DATE = "2026-07-13"   # first game of the 2026 second half (post All-Star break)
_ACC = ["pa", "ab", "h", "doubles", "triples", "hr", "bb", "hbp"]


def _woba(d):
    singles = d["h"] - d["doubles"] - d["triples"] - d["hr"]
    num = (W["bb"] * d["bb"] + W["hbp"] * d["hbp"] + W["s"] * singles
           + W["d"] * d["doubles"] + W["t"] * d["triples"] + W["hr"] * d["hr"])
    den = d["ab"] + d["bb"] + d["hbp"]
    return num / den if den else 0.0


def _blank():
    return {k: 0 for k in _ACC}


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _load_confirmed_lineups():
    """{team: [batter_ids]} for the current slate's CONFIRMED lineups (source
    'lineup' / confirmed flag -- the same gate the hand-tails fade uses). Empty
    if today's lineup file is missing or nothing is posted yet."""
    today = datetime.date.today().strftime("%Y%m%d")
    path = os.path.join(LINEUP_CACHE_DIR, f"lineups_{today}.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    out = {}
    for team, v in (data or {}).items():
        if not isinstance(v, dict):
            continue
        confirmed = bool(v.get("confirmed")) or v.get("source") == "lineup"
        ids = v.get("player_ids")
        if confirmed and ids:
            out[team] = list(ids)
    return out


def build():
    # PA-level rows: each already carries the ACTUAL pitcher's hand (starters +
    # relievers), the batting team's home/away side, and the park's home team.
    rows = json.load(open(PA_SPLITS, encoding="utf-8"))
    recs = []
    for r in rows:
        h = r.get("opp_hand")
        if h in ("L", "R") and r.get("team") and r.get("game_date"):
            recs.append((r["game_date"], r["team"], h, r))
    dates = sorted({d for d, *_ in recs})
    if not dates:
        raise SystemExit("no PA-split rows -- run build_pbp_team_pa first")
    last = datetime.date.fromisoformat(dates[-1])
    months = sorted({d[:7] for d in dates})

    def window_pred(name):
        if name == "season":
            return lambda d: True
        if name == "asb":                        # since the All-Star break
            return lambda d: d >= ASB_DATE
        if name.startswith("last"):
            k = int(name[4:])
            cutoff = (last - datetime.timedelta(days=k - 1)).isoformat()
            return lambda d: d >= cutoff
        return lambda d: d.startswith(name)      # a month "YYYY-MM"

    windows = ["season", "asb"] + months + ["last15", "last20", "last30", "last45", "last60"]
    ROLES = ("SP", "RP")
    VENUE_GROUPS = [(("home", "road"), None), (("home",), "home"), (("road",), "road")]
    out = {}
    for wname in windows:
        keep = window_pred(wname)
        # Keyed by (team, hand, venue, role): venue in {'home','road'}, role in
        # {'SP','RP'}. Output aggregates 'all' venue (home+road) and 'all' role
        # (SP+RP), plus per-role branches. League baselines are per (hand, role)
        # so a starters-only wRC+ is measured against league-average-vs-starters
        # (relievers are league-wide tougher; a shared baseline would make every
        # team look worse vs RP rather than isolating team skill).
        agg = defaultdict(_blank)
        pfw = defaultdict(lambda: [0.0, 0])  # (team,hand,venue,role) -> [sum(pa*PF), sum(pa)]
        lg = defaultdict(_blank)             # (hand, role) -> league totals
        for d, tm, h, r in recs:
            if not keep(d):
                continue
            role = r.get("role") or "SP"
            venue = "home" if r.get("is_home") else "road"
            a = agg[(tm, h, venue, role)]
            lgb = lg[(h, role)]
            for k in _ACC:
                a[k] += r.get(k) or 0
                lgb[k] += r.get(k) or 0
            # Park the PAs happened in = the home team's park.
            park = tm if r.get("is_home") else r.get("opp")
            pa = r.get("pa") or 0
            w = pfw[(tm, h, venue, role)]
            w[0] += pa * PARK_PF.get(park, 1.0)
            w[1] += pa
        # League wOBA per (hand, role) and combined all-role ('ALL').
        lgwoba = {}
        for h in ("L", "R"):
            comb = _blank()
            for role in ROLES:
                lgwoba[(h, role)] = _woba(lg[(h, role)])
                for k in _ACC:
                    comb[k] += lg[(h, role)][k]
            lgwoba[(h, "ALL")] = _woba(comb)

        def wrcplus(d, h, rolekey, avg_pf):
            if not (d["ab"] + d["bb"] + d["hbp"]):
                return None, None
            base = lgwoba.get((h, rolekey)) or 0.0
            neutral = 100 * ((_woba(d) - base) / WOBA_SCALE + LG_R_PA) / LG_R_PA
            # Park term: subtract the park's inflation (avg_pf-1) in wRC+ points.
            park_adj = neutral - 100 * (avg_pf - 1.0)
            return round(park_adj), round(neutral)

        def _cell(d, h, rolekey, pf_num, pf_den):
            avg_pf = (pf_num / pf_den) if pf_den else 1.0
            wp, wn = wrcplus(d, h, rolekey, avg_pf)
            return {"woba": round(_woba(d), 3), "wrcplus": wp, "wrcplusNeutral": wn,
                    "parkFactor": round(avg_pf, 3), "pa": d["pa"]}

        def _combine(tm, h, roles, venues):
            d = _blank()
            pn = pd = 0.0
            for role in roles:
                for venue in venues:
                    a = agg.get((tm, h, venue, role))
                    if a:
                        for k in _ACC:
                            d[k] += a[k]
                        pn += pfw[(tm, h, venue, role)][0]
                        pd += pfw[(tm, h, venue, role)][1]
            return d, pn, pd

        teams = {}
        for tm in {t for (t, h, v, r) in agg}:
            node = {}
            for h in ("L", "R"):
                hkey = "vsLHP" if h == "L" else "vsRHP"
                # all-role cells at the node top level (backward compatible),
                # plus starters-only ('sp') and relievers-only ('rp') branches.
                for roles, rolekey, dest in (
                        (ROLES, "ALL", node),
                        (("SP",), "SP", node.setdefault("sp", {})),
                        (("RP",), "RP", node.setdefault("rp", {}))):
                    for venues, vlabel in VENUE_GROUPS:
                        d, pn, pd = _combine(tm, h, roles, venues)
                        if not d["pa"]:
                            continue
                        target = dest if vlabel is None else dest.setdefault(vlabel, {})
                        target[hkey] = _cell(d, h, rolekey, pn, pd)
            teams[tm] = node
        out[wname] = {
            "lgWobaVsLHP": round(lgwoba[("L", "ALL")], 3),
            "lgWobaVsRHP": round(lgwoba[("R", "ALL")], 3),
            "teams": teams,
        }
        if wname == "season":
            season_lg = dict(lgwoba)  # (hand, role) baselines for the lineup window

    # ---- 'Confirmed Lineup' window --------------------------------------
    # Tonight's confirmed 9 per team, using each hitter's SEASON split. Same
    # node structure as the other windows (vsLHP/vsRHP + home/road + sp/rp) so
    # the dashboard's Venue/Pitcher filters apply unchanged. Park-NEUTRAL (the
    # hitters' season components already blend the parks they played in).
    lineups = _load_confirmed_lineups()
    if lineups:
        bat = {}  # (batter_id, hand, venue, role) -> components
        for r in (_read_json(BATTER_SPLITS) or []):
            venue = "home" if r.get("is_home") else "road"
            bat[(r["batter_id"], r["opp_hand"], venue, r["role"])] = r

        def _lineup_cell(d, h, rolekey):
            if not (d["ab"] + d["bb"] + d["hbp"]):
                return None
            base = season_lg.get((h, rolekey)) or 0.0
            wp = round(100 * ((_woba(d) - base) / WOBA_SCALE + LG_R_PA) / LG_R_PA)
            return {"woba": round(_woba(d), 3), "wrcplus": wp,
                    "wrcplusNeutral": wp, "parkFactor": 1.0, "pa": d["pa"]}

        lu_teams = {}
        for tm, bids in lineups.items():
            node = {}
            for h in ("L", "R"):
                hkey = "vsLHP" if h == "L" else "vsRHP"
                for roles, rolekey, dest in (
                        (ROLES, "ALL", node),
                        (("SP",), "SP", node.setdefault("sp", {})),
                        (("RP",), "RP", node.setdefault("rp", {}))):
                    for venues, vlabel in VENUE_GROUPS:
                        d = _blank()
                        for bid in bids:
                            for role in roles:
                                for venue in venues:
                                    c = bat.get((bid, h, venue, role))
                                    if c:
                                        for k in _ACC:
                                            d[k] += c.get(k) or 0
                        cell = _lineup_cell(d, h, rolekey)
                        if cell:
                            target = dest if vlabel is None else dest.setdefault(vlabel, {})
                            target[hkey] = cell
            lu_teams[tm] = node
        if lu_teams:
            out["lineup"] = {
                "lgWobaVsLHP": round(season_lg[("L", "ALL")], 3),
                "lgWobaVsRHP": round(season_lg[("R", "ALL")], 3),
                "teams": lu_teams,
            }

    payload = {
        "sport": "MLB", "type": "team-woba-splits",
        "generated": datetime.datetime.now(datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "throughDate": dates[-1],
        "metric": "wOBA + park-adjusted wRC+ (self-computed)",
        "weights": W, "wobaScale": WOBA_SCALE, "lgRperPA": LG_R_PA,
        "note": "Self-computed team offense vs LHP/RHP by the ACTUAL pitcher's "
                "hand each plate appearance (starters + relievers, from "
                "play-by-play), per window, home/away, and pitcher role (all / "
                "starters-only 'sp' / relievers-only 'rp', each vs its own league "
                "baseline). wrcplus is PARK-ADJUSTED (PA-weighted "
                "by the actual parks the team hit in over the window, using the "
                "total-bases park factor); wrcplusNeutral is the un-adjusted "
                "version; parkFactor is the PA-weighted park factor. Standard wOBA "
                "weights, league baseline from the same window -- close to but not "
                "identical to FanGraphs' exact wRC+.",
        "windows": out,
    }
    for path in OUTPUT_PATHS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)
    return payload, windows


if __name__ == "__main__":
    pay, windows = build()
    print(f"[team-woba-splits] through {pay['throughDate']} | windows: {', '.join(windows)}")
    for w in ("season", "2026-07", "last30"):
        if w not in pay["windows"]:
            continue
        t = pay["windows"][w]["teams"]
        top = sorted(((abbr, v.get("vsRHP", {}).get("wrcplus")) for abbr, v in t.items()
                      if v.get("vsRHP", {}).get("wrcplus") is not None),
                     key=lambda x: -x[1])[:3]
        print(f"  {w}: top vs RHP -> " + ", ".join(f"{a} {n}" for a, n in top))

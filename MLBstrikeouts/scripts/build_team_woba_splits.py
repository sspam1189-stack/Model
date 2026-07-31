#!/usr/bin/env python3
"""
MLBstrikeouts/scripts/build_team_woba_splits.py
Self-computed team offense splits vs LHP / RHP, for ANY time window -- the
calculable alternative to the manual FanGraphs wRC+ snapshot (build_team_wrc.py)
so we can look at June / July / recent instead of only full-season.

Reads batter game logs (per hitter, per game: PA/AB/H/2B/3B/HR/BB/HBP + the
opposing STARTER's id) and the pitcher game logs (id -> name) + mlb-all-ml.json
(name -> throwing hand) to attribute each hitter-game to the opposing starter's
hand. Aggregates by team x hand over each window and computes:
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
from fade_list import _norm
from sources.park_factors import PRIOR_PARK_FACTORS

# Single offense park factor per team (its home park), 1.0 = neutral. We use the
# total-bases factor as the run-environment proxy. Because we know each game's
# park (the home team's), we PA-weight the ACTUAL parks a team hit in over the
# window -- more precise than FanGraphs' single regressed home-park factor.
PARK_PF = {tm: (f.get("tb") or 1.0) for tm, f in PRIOR_PARK_FACTORS.items()}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DATA = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "data"))
PITCHER_LOGS = os.path.join(ROOT_DATA, "pitcher_cache", "mlb", "game_logs_2026.json")
BATTER_LOGS = os.path.join(ROOT_DATA, "pitcher_cache", "mlb", "batter_game_logs_2026.json")
ALLML = os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "mlb-all-ml.json"))
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
_ACC = ["pa", "ab", "h", "doubles", "triples", "hr", "bb", "hbp"]


def _hand_resolver():
    """opp_pitcher_id -> 'L'/'R', via id->name (pitcher logs) + name->hand (all-ml)."""
    id2name = {}
    for r in json.load(open(PITCHER_LOGS, encoding="utf-8")):
        if r.get("pitcher_id") and r.get("pitcher_name"):
            id2name[r["pitcher_id"]] = r["pitcher_name"]
    name2hand = {}
    for g in json.load(open(ALLML, encoding="utf-8")).get("games", []):
        for side in ("home", "away"):
            p, h = g.get(side + "_pitcher"), g.get(side + "_hand")
            if p and h:
                name2hand[_norm(p)] = h
    def hand_of(pid):
        nm = id2name.get(pid)
        return name2hand.get(_norm(nm)) if nm else None
    return hand_of


def _woba(d):
    singles = d["h"] - d["doubles"] - d["triples"] - d["hr"]
    num = (W["bb"] * d["bb"] + W["hbp"] * d["hbp"] + W["s"] * singles
           + W["d"] * d["doubles"] + W["t"] * d["triples"] + W["hr"] * d["hr"])
    den = d["ab"] + d["bb"] + d["hbp"]
    return num / den if den else 0.0


def _blank():
    return {k: 0 for k in _ACC}


def build():
    hand_of = _hand_resolver()
    blogs = json.load(open(BATTER_LOGS, encoding="utf-8"))
    rows = [r for v in blogs.values() for r in (v if isinstance(v, list) else [v])]
    # Attach resolved hand + keep only rows we can place.
    recs = []
    for r in rows:
        h = hand_of(r.get("opp_pitcher_id"))
        if h in ("L", "R") and r.get("team") and r.get("game_date"):
            recs.append((r["game_date"], r["team"], h, r))
    dates = sorted({d for d, *_ in recs})
    if not dates:
        raise SystemExit("no resolvable batter-game rows")
    last = datetime.date.fromisoformat(dates[-1])
    months = sorted({d[:7] for d in dates})

    def window_pred(name):
        if name == "season":
            return lambda d: True
        if name.startswith("last"):
            k = int(name[4:])
            cutoff = (last - datetime.timedelta(days=k - 1)).isoformat()
            return lambda d: d >= cutoff
        return lambda d: d.startswith(name)      # a month "YYYY-MM"

    windows = ["season"] + months + ["last15", "last20", "last30", "last45", "last60"]
    out = {}
    for wname in windows:
        keep = window_pred(wname)
        # Keyed by (team, hand, venue) with venue in {'home','road'}; 'all' is
        # home+road combined at output time.
        agg = defaultdict(_blank)
        pfw = defaultdict(lambda: [0.0, 0])      # (team,hand,venue) -> [sum(pa*PF), sum(pa)]
        lg = {"L": _blank(), "R": _blank()}
        for d, tm, h, r in recs:
            if not keep(d):
                continue
            venue = "home" if r.get("is_home") else "road"
            a = agg[(tm, h, venue)]
            for k in _ACC:
                a[k] += r.get(k) or 0
            for k in _ACC:
                lg[h][k] += r.get(k) or 0
            # Park the PAs happened in = the home team's park.
            park = tm if r.get("is_home") else r.get("opp")
            pa = r.get("pa") or 0
            w = pfw[(tm, h, venue)]
            w[0] += pa * PARK_PF.get(park, 1.0)
            w[1] += pa
        lgwoba = {h: _woba(lg[h]) for h in ("L", "R")}

        def wrcplus(d, h, avg_pf):
            if not (d["ab"] + d["bb"] + d["hbp"]):
                return None, None
            neutral = 100 * ((_woba(d) - lgwoba[h]) / WOBA_SCALE + LG_R_PA) / LG_R_PA
            # Park term: subtract the park's inflation (avg_pf-1) in wRC+ points.
            park_adj = neutral - 100 * (avg_pf - 1.0)
            return round(park_adj), round(neutral)

        def _cell(d, h, pf_num, pf_den):
            avg_pf = (pf_num / pf_den) if pf_den else 1.0
            wp, wn = wrcplus(d, h, avg_pf)
            return {"woba": round(_woba(d), 3), "wrcplus": wp, "wrcplusNeutral": wn,
                    "parkFactor": round(avg_pf, 3), "pa": d["pa"]}

        teams = {}
        for (tm, h) in {(t, hh) for (t, hh, v) in agg}:
            hkey = "vsLHP" if h == "L" else "vsRHP"
            node = teams.setdefault(tm, {})
            # per-venue cells
            for venue in ("home", "road"):
                if (tm, h, venue) in agg:
                    pn, pd = pfw[(tm, h, venue)]
                    node.setdefault(venue, {})[hkey] = _cell(agg[(tm, h, venue)], h, pn, pd)
            # 'all' cell = home + road combined
            comb = _blank()
            pn = pd = 0.0
            for venue in ("home", "road"):
                if (tm, h, venue) in agg:
                    for k in _ACC:
                        comb[k] += agg[(tm, h, venue)][k]
                    pn += pfw[(tm, h, venue)][0]
                    pd += pfw[(tm, h, venue)][1]
            node[hkey] = _cell(comb, h, pn, pd)
        out[wname] = {
            "lgWobaVsLHP": round(lgwoba["L"], 3), "lgWobaVsRHP": round(lgwoba["R"], 3),
            "teams": teams,
        }

    payload = {
        "sport": "MLB", "type": "team-woba-splits",
        "generated": datetime.datetime.now(datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "throughDate": dates[-1],
        "metric": "wOBA + park-adjusted wRC+ (self-computed)",
        "weights": W, "wobaScale": WOBA_SCALE, "lgRperPA": LG_R_PA,
        "note": "Self-computed team offense vs LHP/RHP by opposing STARTER hand, "
                "per window. wrcplus is PARK-ADJUSTED (PA-weighted by the actual "
                "parks the team hit in over the window, using the total-bases park "
                "factor); wrcplusNeutral is the un-adjusted version; parkFactor is "
                "the PA-weighted park factor. Standard wOBA weights, league "
                "baseline from the same window -- close to but not identical to "
                "FanGraphs' exact wRC+.",
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

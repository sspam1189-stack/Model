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
from fade_list import _norm

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

    windows = ["season"] + months + ["last30", "last15"]
    out = {}
    for wname in windows:
        keep = window_pred(wname)
        agg = defaultdict(_blank)                # (team,hand) -> counts
        lg = {"L": _blank(), "R": _blank()}
        for d, tm, h, r in recs:
            if not keep(d):
                continue
            a = agg[(tm, h)]
            for k in _ACC:
                a[k] += r.get(k) or 0
            for k in _ACC:
                lg[h][k] += r.get(k) or 0
        lgwoba = {h: _woba(lg[h]) for h in ("L", "R")}

        def wrcplus(d, h):
            if not (d["ab"] + d["bb"] + d["hbp"]):
                return None
            return round(100 * ((_woba(d) - lgwoba[h]) / WOBA_SCALE + LG_R_PA) / LG_R_PA)

        teams = {}
        for (tm, h), d in agg.items():
            teams.setdefault(tm, {})[("vsLHP" if h == "L" else "vsRHP")] = {
                "woba": round(_woba(d), 3), "wrcplus": wrcplus(d, h), "pa": d["pa"],
            }
        out[wname] = {
            "lgWobaVsLHP": round(lgwoba["L"], 3), "lgWobaVsRHP": round(lgwoba["R"], 3),
            "teams": teams,
        }

    payload = {
        "sport": "MLB", "type": "team-woba-splits",
        "generated": datetime.datetime.now(datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "throughDate": dates[-1],
        "metric": "wOBA + park-neutral wRC+ approximation (self-computed)",
        "weights": W, "wobaScale": WOBA_SCALE, "lgRperPA": LG_R_PA,
        "note": "Self-computed team offense vs LHP/RHP by opposing STARTER hand, "
                "per window. wRC+ is a park-neutral approximation (standard wOBA "
                "weights, league baseline from the same window) -- a relative "
                "gauge, not FanGraphs' exact park-adjusted wRC+.",
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

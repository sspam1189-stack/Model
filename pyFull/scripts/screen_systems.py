"""
Situational-systems screener for the NBA full-game model.

Reads pyFull/data/history.json (every game of the season with the closing
DraftKings spread/total and the final score), derives schedule/situational
features that need nothing but lines + results, and screens a registry of
candidate situations against three markets:

    spread  -- team ATS at -110
    ML      -- team moneyline, priced from the spread (no ML prices in the
               data): fair p = Phi(spread / sigma) with sigma fit from the
               season's margin-vs-line residual, then a 4.5% hold split
               evenly across both sides
    total   -- OVER / UNDER at -110

Survivors must clear ROI >= 10% on n >= 25.  Every survivor is also shown
with a binomial p-value and a first-half / second-half split so the reader
can see which ones are one-window artefacts.  With ~150 candidates x 2-3
sides screened on ~1,300 games, ~5% would clear a 10% ROI bar by luck, so
the split and p-value matter more than the headline ROI.

Usage:  python pyFull/scripts/screen_systems.py [--min-n 25] [--min-roi 0.10]
"""
import argparse
import json
import math
import os
import sys
from collections import defaultdict
from datetime import date, datetime
from itertools import combinations

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")

DIV = {
    "Atlantic": ["Boston Celtics", "Brooklyn Nets", "New York Knicks", "Philadelphia 76ers", "Toronto Raptors"],
    "Central": ["Chicago Bulls", "Cleveland Cavaliers", "Detroit Pistons", "Indiana Pacers", "Milwaukee Bucks"],
    "Southeast": ["Atlanta Hawks", "Charlotte Hornets", "Miami Heat", "Orlando Magic", "Washington Wizards"],
    "Northwest": ["Denver Nuggets", "Minnesota Timberwolves", "Oklahoma City Thunder", "Portland Trail Blazers", "Utah Jazz"],
    "Pacific": ["Golden State Warriors", "Los Angeles Clippers", "Los Angeles Lakers", "Phoenix Suns", "Sacramento Kings"],
    "Southwest": ["Dallas Mavericks", "Houston Rockets", "Memphis Grizzlies", "New Orleans Pelicans", "San Antonio Spurs"],
}
TEAM_DIV = {t: d for d, ts in DIV.items() for t in ts}
EAST = {"Atlantic", "Central", "Southeast"}
TEAM_CONF = {t: ("E" if d in EAST else "W") for t, d in TEAM_DIV.items()}

JUICE = 1.0 / 1.1  # -110 payout on a win
ML_HOLD = 0.045


def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def binom_p_two_sided(w, n, p=0.5):
    """Exact two-sided binomial p-value (sum of tails at least as extreme)."""
    if n == 0:
        return 1.0
    from math import lgamma, exp, log
    lp, lq = log(p), log(1 - p)
    pm = [exp(lgamma(n + 1) - lgamma(k + 1) - lgamma(n - k + 1) + k * lp + (n - k) * lq) for k in range(n + 1)]
    obs = pm[w]
    return min(1.0, sum(v for v in pm if v <= obs + 1e-12))


# ---------------------------------------------------------------------------
# Load + feature build
# ---------------------------------------------------------------------------
def load_games():
    h = json.load(open(os.path.join(DATA, "history.json")))
    games = []
    seen = set()
    for r in h["runs"]:
        for g in r["games"]:
            if g.get("line") is None or g.get("total") is None:
                continue
            if g.get("homeScore") is None or g.get("awayScore") is None:
                continue
            key = (r["date"], g["away"], g["home"])
            if key in seen:
                continue
            seen.add(key)
            games.append({
                "date": r["date"],
                "d": datetime.strptime(r["date"], "%Y%m%d").date(),
                "home": g["home"], "away": g["away"],
                "line": float(g["line"]),       # home spread (neg = home fav)
                "total": float(g["total"]),
                "hs": float(g["homeScore"]), "as": float(g["awayScore"]),
                # model fields (kept for the market-vs-model checks)
                "pHomeCover": g.get("pHomeCover"), "pOver": g.get("pOver"),
                "sDiff": g.get("sDiff"), "tDiff": g.get("tDiff"),
            })
    games.sort(key=lambda g: (g["d"], g["home"]))
    return games


STATS_DIRS = [
    os.path.join(HERE, "..", "..", "data", "stats_cache", "nba"),   # shared cache (full season)
    os.path.join(DATA, "stats_cache"),                              # pyFull copy (through 2026-03-27)
]


def load_stats():
    out = {}
    for d in STATS_DIRS:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".json") and fn[:-5] not in out:
                out[fn[:-5]] = json.load(open(os.path.join(d, fn)))
    return out


def stats_for(stats, d8):
    """Latest cache strictly BEFORE d8.  The cache written on date D was
    fetched after D's games (GP = games through D), so using cache[D] for
    D's games is look-ahead -- it was worth ~+65u on the season-net-rating
    systems before this was caught.  After 2026-03-27 there is no cache, so
    late-season / playoff rows carry stale (frozen) season stats if only
    the pyFull copy of the cache is present."""
    keys = [k for k in stats if k < d8]
    return stats[max(keys)] if keys else None


def build(games, stats):
    """Attach team-perspective rows and game-level situational fields."""
    # --- schedule: per-team ordered game list -----------------------------
    by_team = defaultdict(list)
    for i, g in enumerate(games):
        by_team[g["home"]].append(i)
        by_team[g["away"]].append(i)

    # regular season = first 82 games for each team
    reg_cut = {}
    for t, idxs in by_team.items():
        if len(idxs) > 82:
            reg_cut[t] = games[idxs[82]]["d"]
    # median: the two NBA Cup finalists play an 83rd regular-season game
    cuts = sorted(reg_cut.values())
    playoff_start = cuts[len(cuts) // 2] if cuts else date(2099, 1, 1)
    # play-in = the first 4 days of post-season
    playin_end = date.fromordinal(playoff_start.toordinal() + 3)

    # --- rolling per-team state ------------------------------------------
    state = {t: {
        "last_d": None, "last_home": None, "streak_loc": 0,
        "su": [], "ats": [], "ou": [], "margins": [], "ats_margins": [],
        "was_fav": [], "dates": [], "vs": {},   # vs[opp] = list of SU results
        "last_opp": None,
    } for t in by_team}

    rows = []
    for gi, g in enumerate(games):
        g["playoffs"] = g["d"] >= playoff_start
        g["playin"] = playoff_start <= g["d"] <= playin_end
        g["month"] = g["d"].month
        g["dow"] = g["d"].weekday()  # 0=Mon
        g["division"] = TEAM_DIV.get(g["home"]) == TEAM_DIV.get(g["away"])
        g["conference"] = TEAM_CONF.get(g["home"]) == TEAM_CONF.get(g["away"])
        g["margin_home"] = g["hs"] - g["as"]
        g["pts"] = g["hs"] + g["as"]
        g["ou_result"] = "O" if g["pts"] > g["total"] else ("U" if g["pts"] < g["total"] else "P")
        st = stats_for(stats, g["date"])

        for side in ("home", "away"):
            team = g[side]
            opp = g["away"] if side == "home" else g["home"]
            s = state[team]
            so = state[opp]
            spread = g["line"] if side == "home" else -g["line"]
            margin = g["margin_home"] if side == "home" else -g["margin_home"]
            adj = margin + spread
            rest = (g["d"] - s["last_d"]).days if s["last_d"] else None
            orest = (g["d"] - so["last_d"]).days if so["last_d"] else None
            recent3 = [d for d in s["dates"] if (g["d"] - d).days <= 3]
            orecent3 = [d for d in so["dates"] if (g["d"] - d).days <= 3]

            def streak(seq, val):
                n = 0
                for x in reversed(seq):
                    if x == val:
                        n += 1
                    else:
                        break
                return n

            n_prior = len(s["su"])
            su_pct = sum(s["su"]) / n_prior if n_prior else None
            ats_pct = (sum(1 for x in s["ats"] if x == 1) / max(1, sum(1 for x in s["ats"] if x != 0))) if n_prior else None
            ou_over_pct = (sum(1 for x in s["ou"] if x == "O") / max(1, sum(1 for x in s["ou"] if x != "P"))) if n_prior else None

            tstat = st["season"].get(team) if st else None
            ostat = st["season"].get(opp) if st else None
            tl10 = st["last10"].get(team) if st else None
            ol10 = st["last10"].get(opp) if st else None

            row = {
                "gi": gi, "d": g["d"], "date": g["date"], "team": team, "opp": opp,
                "is_home": side == "home", "spread": spread, "total": g["total"],
                "margin": margin, "adj": adj,
                "cover": 1 if adj > 0 else (0 if adj < 0 else None),
                "win": 1 if margin > 0 else 0,
                "fav": spread < 0, "dog": spread > 0, "pk": spread == 0,
                "rest": rest, "orest": orest,
                "b2b": rest == 1, "ob2b": orest == 1,
                "three_in_four": len(recent3) >= 2, "o_three_in_four": len(orecent3) >= 2,
                "rest_adv": (rest - orest) if (rest is not None and orest is not None) else None,
                "n_prior": n_prior,
                "prev_su": s["su"][-1] if s["su"] else None,
                "prev_ats": s["ats"][-1] if s["ats"] else None,
                "prev_ou": s["ou"][-1] if s["ou"] else None,
                "prev_margin": s["margins"][-1] if s["margins"] else None,
                "prev_ats_margin": s["ats_margins"][-1] if s["ats_margins"] else None,
                "prev_was_fav": s["was_fav"][-1] if s["was_fav"] else None,
                "su_wstreak": streak(s["su"], 1), "su_lstreak": streak(s["su"], 0),
                "ats_wstreak": streak(s["ats"], 1), "ats_lstreak": streak(s["ats"], -1),
                "ou_ostreak": streak(s["ou"], "O"), "ou_ustreak": streak(s["ou"], "U"),
                "su_pct": su_pct, "ats_pct": ats_pct, "ou_over_pct": ou_over_pct,
                "opp_su_pct": (sum(so["su"]) / len(so["su"])) if so["su"] else None,
                "loc_streak": (s["streak_loc"] + 1) if s["last_home"] == (side == "home") else 1,
                "lost_last_meeting": (s["vs"].get(opp, [None])[-1] == 0) if s["vs"].get(opp) else None,
                "won_last_meeting": (s["vs"].get(opp, [None])[-1] == 1) if s["vs"].get(opp) else None,
                "rematch": s["last_opp"] == opp,
                "playoffs": g["playoffs"], "playin": g["playin"], "month": g["month"],
                "dow": g["dow"], "division": g["division"], "conference": g["conference"],
                "net": (tstat["OFF"] - tstat["DEF"]) if tstat else None,
                "onet": (ostat["OFF"] - ostat["DEF"]) if ostat else None,
                "net10": (tl10["OFF"] - tl10["DEF"]) if tl10 else None,
                "onet10": (ol10["OFF"] - ol10["DEF"]) if ol10 else None,
                "pace": tstat["PACE"] if tstat else None, "opace": ostat["PACE"] if ostat else None,
                "gp": tstat["GP"] if tstat else 0,
                "pHomeCover": g["pHomeCover"], "sDiff": g["sDiff"],
            }
            if row["net"] is not None and row["onet"] is not None and row["gp"] >= 10:
                hca = 2.0 if row["is_home"] else -2.0
                row["stat_margin"] = row["net"] - row["onet"] + hca   # expected margin for team
                row["stat_edge"] = row["stat_margin"] + spread        # >0 => stats like this side
            else:
                row["stat_margin"] = row["stat_edge"] = None
            rows.append(row)
            g.setdefault("rows", {})[side] = row

        # update state after both rows are built
        for side in ("home", "away"):
            team, opp = (g["home"], g["away"]) if side == "home" else (g["away"], g["home"])
            r = g["rows"][side]
            s = state[team]
            s["su"].append(r["win"])
            s["ats"].append(1 if r["cover"] == 1 else (-1 if r["cover"] == 0 else 0))
            s["ou"].append(g["ou_result"])
            s["margins"].append(r["margin"])
            s["ats_margins"].append(r["adj"])
            s["was_fav"].append(r["fav"])
            s["dates"].append(g["d"])
            s["vs"].setdefault(opp, []).append(r["win"])
            s["last_opp"] = opp
            s["streak_loc"] = r["loc_streak"]
            s["last_home"] = (side == "home")
            s["last_d"] = g["d"]
    return rows, playoff_start


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def fit_sigma(games):
    res = [g["margin_home"] + g["line"] for g in games if not g["playoffs"]]
    mu = sum(res) / len(res)
    return math.sqrt(sum((x - mu) ** 2 for x in res) / (len(res) - 1)), mu


ML_FIT = [0.0, 0.0]   # logistic intercept, slope on -spread (set by fit_ml)


def fit_ml(rows, iters=300, lr=0.05):
    """Logistic fit P(win) = sigmoid(a + b * -spread) on the season's rows.
    Pricing ML off this instead of a fixed sigma removes bucket-level bias
    (e.g. small favourites won only ~51% this season) so ML systems show
    within-bucket deviation rather than a curve-shape artefact."""
    a, b = 0.0, 0.15
    xs = [(-r["spread"], r["win"]) for r in rows]
    n = len(xs)
    for _ in range(iters):
        ga = gb = 0.0
        for x, y in xs:
            pr = 1.0 / (1.0 + math.exp(-(a + b * x)))
            ga += (pr - y); gb += (pr - y) * x
        a -= lr * ga / n; b -= lr * gb / n * 0.05
    ML_FIT[0], ML_FIT[1] = a, b
    return a, b


def ml_fair(spread):
    return 1.0 / (1.0 + math.exp(-(ML_FIT[0] + ML_FIT[1] * -spread)))


def ml_payout(spread, sigma=None):
    """Net units won per 1u risked when the team at `spread` wins its ML."""
    p_book = min(0.995, max(0.005, ml_fair(spread) + ML_HOLD / 2))
    return (1.0 / p_book) - 1.0


def evaluate(rows, market, side, cond, sigma, split_date):
    """market: 'spread' | 'ml' | 'total'. side for total: 'OVER'|'UNDER'.
    Returns dict or None."""
    w = l = p = 0
    units = 0.0
    halves = [[0, 0, 0.0], [0, 0, 0.0]]
    used = set()
    for r in rows:
        if market == "total":
            if r["gi"] in used:
                continue
            used.add(r["gi"])
        if not cond(r):
            continue
        h = 0 if r["d"] < split_date else 1
        if market == "spread":
            if r["cover"] is None:
                p += 1
                continue
            if r["cover"]:
                w += 1; units += JUICE; halves[h][0] += 1; halves[h][2] += JUICE
            else:
                l += 1; units -= 1; halves[h][1] += 1; halves[h][2] -= 1
        elif market == "ml":
            pay = ml_payout(r["spread"], sigma)
            if r["win"]:
                w += 1; units += pay; halves[h][0] += 1; halves[h][2] += pay
            else:
                l += 1; units -= 1; halves[h][1] += 1; halves[h][2] -= 1
        else:
            g_pts = r["_pts"]
            if g_pts == r["total"]:
                p += 1
                continue
            hit = (g_pts > r["total"]) if side == "OVER" else (g_pts < r["total"])
            if hit:
                w += 1; units += JUICE; halves[h][0] += 1; halves[h][2] += JUICE
            else:
                l += 1; units -= 1; halves[h][1] += 1; halves[h][2] -= 1
    n = w + l
    if n == 0:
        return None
    if market == "ml":
        # p-value vs the implied (fair) win prob: use average fair prob
        pv = None
    else:
        pv = binom_p_two_sided(w, n)
    return {
        "w": w, "l": l, "p": p, "n": n, "units": units, "roi": units / n,
        "pct": w / n, "pval": pv,
        "h1": halves[0], "h2": halves[1],
    }


# ---------------------------------------------------------------------------
# Candidate registry
# ---------------------------------------------------------------------------
def team_conditions():
    C = {}
    C["home"] = lambda r: r["is_home"]
    C["away"] = lambda r: not r["is_home"]
    C["fav"] = lambda r: r["fav"]
    C["dog"] = lambda r: r["dog"]
    C["home fav"] = lambda r: r["is_home"] and r["fav"]
    C["home dog"] = lambda r: r["is_home"] and r["dog"]
    C["road fav"] = lambda r: (not r["is_home"]) and r["fav"]
    C["road dog"] = lambda r: (not r["is_home"]) and r["dog"]
    for lo, hi in [(0.5, 3), (3.5, 6), (6.5, 9), (9.5, 12), (12.5, 99)]:
        C[f"dog +{lo}..+{hi}"] = (lambda lo, hi: lambda r: lo <= r["spread"] <= hi)(lo, hi)
        C[f"fav -{lo}..-{hi}"] = (lambda lo, hi: lambda r: -hi <= r["spread"] <= -lo)(lo, hi)
    C["home dog +1..+5"] = lambda r: r["is_home"] and 0.5 <= r["spread"] <= 5
    C["home dog +5.5..+10"] = lambda r: r["is_home"] and 5.5 <= r["spread"] <= 10
    C["home dog +10.5+"] = lambda r: r["is_home"] and r["spread"] >= 10.5
    C["road dog +1..+5"] = lambda r: (not r["is_home"]) and 0.5 <= r["spread"] <= 5
    C["road dog +5.5..+10"] = lambda r: (not r["is_home"]) and 5.5 <= r["spread"] <= 10
    C["road dog +10.5+"] = lambda r: (not r["is_home"]) and r["spread"] >= 10.5
    C["home fav -1..-5"] = lambda r: r["is_home"] and -5 <= r["spread"] <= -0.5
    C["home fav -5.5..-10"] = lambda r: r["is_home"] and -10 <= r["spread"] <= -5.5
    C["home fav -10.5+"] = lambda r: r["is_home"] and r["spread"] <= -10.5
    C["road fav -1..-5"] = lambda r: (not r["is_home"]) and -5 <= r["spread"] <= -0.5
    C["road fav -5.5+"] = lambda r: (not r["is_home"]) and r["spread"] <= -5.5
    # rest
    C["on b2b"] = lambda r: r["b2b"]
    C["opp on b2b"] = lambda r: r["ob2b"]
    C["on b2b, opp rested"] = lambda r: r["b2b"] and not r["ob2b"]
    C["rested, opp on b2b"] = lambda r: (not r["b2b"]) and r["ob2b"]
    C["both on b2b"] = lambda r: r["b2b"] and r["ob2b"]
    C["rest adv >= 2"] = lambda r: r["rest_adv"] is not None and r["rest_adv"] >= 2
    C["rest disadv <= -2"] = lambda r: r["rest_adv"] is not None and r["rest_adv"] <= -2
    C["3+ days rest"] = lambda r: r["rest"] is not None and r["rest"] >= 3
    C["3 in 4 nights"] = lambda r: r["three_in_four"]
    C["home on b2b"] = lambda r: r["is_home"] and r["b2b"]
    C["road on b2b"] = lambda r: (not r["is_home"]) and r["b2b"]
    C["home, opp on b2b"] = lambda r: r["is_home"] and r["ob2b"]
    C["road, opp on b2b"] = lambda r: (not r["is_home"]) and r["ob2b"]
    C["fav on b2b"] = lambda r: r["fav"] and r["b2b"]
    C["dog on b2b"] = lambda r: r["dog"] and r["b2b"]
    C["fav, opp on b2b"] = lambda r: r["fav"] and r["ob2b"]
    C["dog, opp on b2b"] = lambda r: r["dog"] and r["ob2b"]
    C["dog on b2b, opp rested"] = lambda r: r["dog"] and r["b2b"] and not r["ob2b"]
    C["fav on b2b, opp rested"] = lambda r: r["fav"] and r["b2b"] and not r["ob2b"]
    # previous game
    C["after SU win"] = lambda r: r["prev_su"] == 1
    C["after SU loss"] = lambda r: r["prev_su"] == 0
    C["after ATS cover"] = lambda r: r["prev_ats"] == 1
    C["after ATS loss"] = lambda r: r["prev_ats"] == -1
    C["after blowout loss (15+)"] = lambda r: r["prev_margin"] is not None and r["prev_margin"] <= -15
    C["after blowout win (15+)"] = lambda r: r["prev_margin"] is not None and r["prev_margin"] >= 15
    C["after 25+ pt loss"] = lambda r: r["prev_margin"] is not None and r["prev_margin"] <= -25
    C["after loss as fav"] = lambda r: r["prev_su"] == 0 and r["prev_was_fav"]
    C["after win as dog"] = lambda r: r["prev_su"] == 1 and r["prev_was_fav"] is False
    C["after ATS miss by 10+"] = lambda r: r["prev_ats_margin"] is not None and r["prev_ats_margin"] <= -10
    C["after ATS cover by 10+"] = lambda r: r["prev_ats_margin"] is not None and r["prev_ats_margin"] >= 10
    C["SU win streak 3+"] = lambda r: r["su_wstreak"] >= 3
    C["SU win streak 5+"] = lambda r: r["su_wstreak"] >= 5
    C["SU loss streak 3+"] = lambda r: r["su_lstreak"] >= 3
    C["SU loss streak 5+"] = lambda r: r["su_lstreak"] >= 5
    C["ATS win streak 3+"] = lambda r: r["ats_wstreak"] >= 3
    C["ATS loss streak 3+"] = lambda r: r["ats_lstreak"] >= 3
    C["ATS loss streak 4+"] = lambda r: r["ats_lstreak"] >= 4
    C["fav after SU loss"] = lambda r: r["fav"] and r["prev_su"] == 0
    C["dog after SU loss"] = lambda r: r["dog"] and r["prev_su"] == 0
    C["fav after SU win"] = lambda r: r["fav"] and r["prev_su"] == 1
    C["dog after SU win"] = lambda r: r["dog"] and r["prev_su"] == 1
    C["home after SU loss"] = lambda r: r["is_home"] and r["prev_su"] == 0
    C["home after SU win"] = lambda r: r["is_home"] and r["prev_su"] == 1
    C["road after SU loss"] = lambda r: (not r["is_home"]) and r["prev_su"] == 0
    C["road after SU win"] = lambda r: (not r["is_home"]) and r["prev_su"] == 1
    C["home dog after SU loss"] = lambda r: r["is_home"] and r["dog"] and r["prev_su"] == 0
    C["road dog after SU loss"] = lambda r: (not r["is_home"]) and r["dog"] and r["prev_su"] == 0
    C["home fav after SU loss"] = lambda r: r["is_home"] and r["fav"] and r["prev_su"] == 0
    C["road fav after SU loss"] = lambda r: (not r["is_home"]) and r["fav"] and r["prev_su"] == 0
    C["home after blowout loss"] = lambda r: r["is_home"] and r["prev_margin"] is not None and r["prev_margin"] <= -15
    C["road after blowout loss"] = lambda r: (not r["is_home"]) and r["prev_margin"] is not None and r["prev_margin"] <= -15
    C["home after blowout win"] = lambda r: r["is_home"] and r["prev_margin"] is not None and r["prev_margin"] >= 15
    C["road after blowout win"] = lambda r: (not r["is_home"]) and r["prev_margin"] is not None and r["prev_margin"] >= 15
    # season-to-date form (10+ games)
    C["ATS% >= .60 (10+ g)"] = lambda r: r["n_prior"] >= 10 and r["ats_pct"] >= 0.60
    C["ATS% <= .40 (10+ g)"] = lambda r: r["n_prior"] >= 10 and r["ats_pct"] <= 0.40
    C["SU% >= .650 (10+ g)"] = lambda r: r["n_prior"] >= 10 and r["su_pct"] >= 0.65
    C["SU% <= .350 (10+ g)"] = lambda r: r["n_prior"] >= 10 and r["su_pct"] <= 0.35
    C["good team (.600+) as dog"] = lambda r: r["n_prior"] >= 10 and r["su_pct"] >= 0.60 and r["dog"]
    C["bad team (.400-) as fav"] = lambda r: r["n_prior"] >= 10 and r["su_pct"] <= 0.40 and r["fav"]
    C["bad team (.350-) as dog"] = lambda r: r["n_prior"] >= 10 and r["su_pct"] <= 0.35 and r["dog"]
    C["good team (.650+) as fav"] = lambda r: r["n_prior"] >= 10 and r["su_pct"] >= 0.65 and r["fav"]
    C["bad (.400-) home dog"] = lambda r: r["n_prior"] >= 10 and r["su_pct"] <= 0.40 and r["dog"] and r["is_home"]
    C["bad (.400-) road dog"] = lambda r: r["n_prior"] >= 10 and r["su_pct"] <= 0.40 and r["dog"] and not r["is_home"]
    C["good (.600+) road fav"] = lambda r: r["n_prior"] >= 10 and r["su_pct"] >= 0.60 and r["fav"] and not r["is_home"]
    C["good (.600+) home fav"] = lambda r: r["n_prior"] >= 10 and r["su_pct"] >= 0.60 and r["fav"] and r["is_home"]
    C["vs bad team (.400-)"] = lambda r: r["opp_su_pct"] is not None and r["n_prior"] >= 10 and r["opp_su_pct"] <= 0.40
    C["vs good team (.600+)"] = lambda r: r["opp_su_pct"] is not None and r["n_prior"] >= 10 and r["opp_su_pct"] >= 0.60
    C["hot (L10 net - season net >= +5)"] = lambda r: r["net10"] is not None and r["net"] is not None and r["gp"] >= 15 and r["net10"] - r["net"] >= 5
    C["cold (L10 net - season net <= -5)"] = lambda r: r["net10"] is not None and r["net"] is not None and r["gp"] >= 15 and r["net10"] - r["net"] <= -5
    C["hot as dog"] = lambda r: r["net10"] is not None and r["net"] is not None and r["gp"] >= 15 and r["net10"] - r["net"] >= 5 and r["dog"]
    C["cold as fav"] = lambda r: r["net10"] is not None and r["net"] is not None and r["gp"] >= 15 and r["net10"] - r["net"] <= -5 and r["fav"]
    C["opp hot (+5)"] = lambda r: r["onet10"] is not None and r["onet"] is not None and r["gp"] >= 15 and r["onet10"] - r["onet"] >= 5
    C["opp cold (-5)"] = lambda r: r["onet10"] is not None and r["onet"] is not None and r["gp"] >= 15 and r["onet10"] - r["onet"] <= -5
    # market vs season net-rating
    C["stats say +4 vs line"] = lambda r: r["stat_edge"] is not None and r["stat_edge"] >= 4
    C["stats say +6 vs line"] = lambda r: r["stat_edge"] is not None and r["stat_edge"] >= 6
    C["stats say -4 vs line (fade)"] = lambda r: r["stat_edge"] is not None and r["stat_edge"] <= -4
    C["stats +4, dog"] = lambda r: r["stat_edge"] is not None and r["stat_edge"] >= 4 and r["dog"]
    C["stats +4, fav"] = lambda r: r["stat_edge"] is not None and r["stat_edge"] >= 4 and r["fav"]
    # location streaks / travel
    C["home stand game 3+"] = lambda r: r["is_home"] and r["loc_streak"] >= 3
    C["home stand game 5+"] = lambda r: r["is_home"] and r["loc_streak"] >= 5
    C["road trip game 3+"] = lambda r: (not r["is_home"]) and r["loc_streak"] >= 3
    C["road trip game 4+"] = lambda r: (not r["is_home"]) and r["loc_streak"] >= 4
    C["first game of road trip"] = lambda r: (not r["is_home"]) and r["loc_streak"] == 1
    C["first home game after road trip 3+"] = lambda r: r["is_home"] and r["loc_streak"] == 1 and r["_prev_loc_streak"] >= 3
    # opponent history
    C["lost last meeting (revenge)"] = lambda r: r["lost_last_meeting"] is True
    C["won last meeting"] = lambda r: r["won_last_meeting"] is True
    C["revenge at home"] = lambda r: r["lost_last_meeting"] is True and r["is_home"]
    C["revenge on road"] = lambda r: r["lost_last_meeting"] is True and not r["is_home"]
    C["revenge as fav"] = lambda r: r["lost_last_meeting"] is True and r["fav"]
    C["revenge as dog"] = lambda r: r["lost_last_meeting"] is True and r["dog"]
    C["rematch (same opp as last game)"] = lambda r: r["rematch"]
    C["rematch, lost game 1"] = lambda r: r["rematch"] and r["prev_su"] == 0
    C["rematch, won game 1"] = lambda r: r["rematch"] and r["prev_su"] == 1
    C["division game"] = lambda r: r["division"]
    C["division home"] = lambda r: r["division"] and r["is_home"]
    C["division dog"] = lambda r: r["division"] and r["dog"]
    C["division home dog"] = lambda r: r["division"] and r["dog"] and r["is_home"]
    C["interconference"] = lambda r: not r["conference"]
    C["interconference home"] = lambda r: (not r["conference"]) and r["is_home"]
    C["interconference dog"] = lambda r: (not r["conference"]) and r["dog"]
    C["East vs West (East team)"] = lambda r: (not r["conference"]) and TEAM_CONF.get(r["team"]) == "E"
    C["East vs West (West team)"] = lambda r: (not r["conference"]) and TEAM_CONF.get(r["team"]) == "W"
    # calendar
    for m, nm in [(10, "Oct"), (11, "Nov"), (12, "Dec"), (1, "Jan"), (2, "Feb"), (3, "Mar"), (4, "Apr")]:
        C[f"{nm} home"] = (lambda m: lambda r: r["month"] == m and r["is_home"] and not r["playoffs"])(m)
        C[f"{nm} dog"] = (lambda m: lambda r: r["month"] == m and r["dog"] and not r["playoffs"])(m)
        C[f"{nm} fav"] = (lambda m: lambda r: r["month"] == m and r["fav"] and not r["playoffs"])(m)
    C["playoffs home"] = lambda r: r["playoffs"] and r["is_home"] and not r["playin"]
    C["playoffs dog"] = lambda r: r["playoffs"] and r["dog"] and not r["playin"]
    C["playoffs fav"] = lambda r: r["playoffs"] and r["fav"] and not r["playin"]
    C["playoffs road dog"] = lambda r: r["playoffs"] and r["dog"] and not r["is_home"] and not r["playin"]
    C["playoffs home fav"] = lambda r: r["playoffs"] and r["fav"] and r["is_home"] and not r["playin"]
    C["playoffs, lost game before"] = lambda r: r["playoffs"] and not r["playin"] and r["prev_su"] == 0 and r["rematch"]
    C["playoffs, won game before"] = lambda r: r["playoffs"] and not r["playin"] and r["prev_su"] == 1 and r["rematch"]
    C["playoffs dog, lost game before"] = lambda r: r["playoffs"] and not r["playin"] and r["dog"] and r["prev_su"] == 0 and r["rematch"]
    C["weekend home (Fri-Sun)"] = lambda r: r["dow"] >= 4 and r["is_home"]
    C["weekend dog"] = lambda r: r["dow"] >= 4 and r["dog"]
    C["weekday dog"] = lambda r: r["dow"] < 4 and r["dog"]
    C["Sunday home"] = lambda r: r["dow"] == 6 and r["is_home"]
    C["Monday home"] = lambda r: r["dow"] == 0 and r["is_home"]
    # totals context on sides
    C["high total (>=235) dog"] = lambda r: r["total"] >= 235 and r["dog"]
    C["high total (>=235) fav"] = lambda r: r["total"] >= 235 and r["fav"]
    C["low total (<=222) dog"] = lambda r: r["total"] <= 222 and r["dog"]
    C["low total (<=222) fav"] = lambda r: r["total"] <= 222 and r["fav"]
    C["low total (<=222) home"] = lambda r: r["total"] <= 222 and r["is_home"]
    C["high total (>=235) home"] = lambda r: r["total"] >= 235 and r["is_home"]
    return C


def total_conditions():
    C = {}
    C["all games"] = lambda r: True
    C["home on b2b"] = lambda r: r["b2b"]
    C["away on b2b"] = lambda r: r["ob2b"]
    C["either on b2b"] = lambda r: r["b2b"] or r["ob2b"]
    C["both on b2b"] = lambda r: r["b2b"] and r["ob2b"]
    C["neither on b2b"] = lambda r: (not r["b2b"]) and (not r["ob2b"])
    C["both 2+ days rest"] = lambda r: (r["rest"] or 0) >= 2 and (r["orest"] or 0) >= 2
    C["both 3+ days rest"] = lambda r: (r["rest"] or 0) >= 3 and (r["orest"] or 0) >= 3
    C["either 3 in 4"] = lambda r: r["three_in_four"] or r["o_three_in_four"]
    for lo, hi, nm in [(0, 214.5, "<=214.5"), (215, 220.5, "215-220.5"), (221, 226.5, "221-226.5"), (227, 232.5, "227-232.5"), (233, 238.5, "233-238.5"), (239, 999, ">=239")]:
        C[f"total {nm}"] = (lambda lo, hi: lambda r: lo <= r["total"] <= hi)(lo, hi)
    C["|spread| <= 2"] = lambda r: abs(r["spread"]) <= 2
    C["|spread| 2.5-6"] = lambda r: 2.5 <= abs(r["spread"]) <= 6
    C["|spread| 6.5-10"] = lambda r: 6.5 <= abs(r["spread"]) <= 10
    C["|spread| >= 10.5"] = lambda r: abs(r["spread"]) >= 10.5
    C["|spread| >= 13"] = lambda r: abs(r["spread"]) >= 13
    C["home fav"] = lambda r: r["fav"]
    C["home dog"] = lambda r: r["dog"]
    C["home dog 5+"] = lambda r: r["spread"] >= 5
    C["home fav 10+"] = lambda r: r["spread"] <= -10
    C["home prev game OVER"] = lambda r: r["prev_ou"] == "O"
    C["home prev game UNDER"] = lambda r: r["prev_ou"] == "U"
    C["both teams prev OVER"] = lambda r: r["prev_ou"] == "O" and r["_o_prev_ou"] == "O"
    C["both teams prev UNDER"] = lambda r: r["prev_ou"] == "U" and r["_o_prev_ou"] == "U"
    C["home 3+ straight OVERs"] = lambda r: r["ou_ostreak"] >= 3
    C["home 3+ straight UNDERs"] = lambda r: r["ou_ustreak"] >= 3
    C["away 3+ straight OVERs"] = lambda r: r["_o_ou_ostreak"] >= 3
    C["away 3+ straight UNDERs"] = lambda r: r["_o_ou_ustreak"] >= 3
    C["either 3+ straight OVERs"] = lambda r: r["ou_ostreak"] >= 3 or r["_o_ou_ostreak"] >= 3
    C["either 3+ straight UNDERs"] = lambda r: r["ou_ustreak"] >= 3 or r["_o_ou_ustreak"] >= 3
    C["home after blowout loss"] = lambda r: r["prev_margin"] is not None and r["prev_margin"] <= -15
    C["away after blowout loss"] = lambda r: r["_o_prev_margin"] is not None and r["_o_prev_margin"] <= -15
    C["either after blowout loss"] = lambda r: (r["prev_margin"] is not None and r["prev_margin"] <= -15) or (r["_o_prev_margin"] is not None and r["_o_prev_margin"] <= -15)
    C["either after blowout win"] = lambda r: (r["prev_margin"] is not None and r["prev_margin"] >= 15) or (r["_o_prev_margin"] is not None and r["_o_prev_margin"] >= 15)
    C["both after SU loss"] = lambda r: r["prev_su"] == 0 and r["_o_prev_su"] == 0
    C["both after SU win"] = lambda r: r["prev_su"] == 1 and r["_o_prev_su"] == 1
    C["home team OVER% >= .60 (10+)"] = lambda r: r["n_prior"] >= 10 and r["ou_over_pct"] >= 0.60
    C["home team OVER% <= .40 (10+)"] = lambda r: r["n_prior"] >= 10 and r["ou_over_pct"] <= 0.40
    C["both OVER% >= .55"] = lambda r: r["n_prior"] >= 10 and r["ou_over_pct"] >= 0.55 and r["_o_ou_over_pct"] is not None and r["_o_ou_over_pct"] >= 0.55
    C["both OVER% <= .45"] = lambda r: r["n_prior"] >= 10 and r["ou_over_pct"] <= 0.45 and r["_o_ou_over_pct"] is not None and r["_o_ou_over_pct"] <= 0.45
    C["both fast (pace >= 101.5)"] = lambda r: r["pace"] and r["opace"] and r["gp"] >= 10 and r["pace"] >= 101.5 and r["opace"] >= 101.5
    C["both slow (pace <= 98.5)"] = lambda r: r["pace"] and r["opace"] and r["gp"] >= 10 and r["pace"] <= 98.5 and r["opace"] <= 98.5
    C["pace mismatch (>=4)"] = lambda r: r["pace"] and r["opace"] and r["gp"] >= 10 and abs(r["pace"] - r["opace"]) >= 4
    C["two good teams (.600+)"] = lambda r: r["n_prior"] >= 10 and r["su_pct"] >= 0.60 and r["opp_su_pct"] is not None and r["opp_su_pct"] >= 0.60
    C["two bad teams (.400-)"] = lambda r: r["n_prior"] >= 10 and r["su_pct"] <= 0.40 and r["opp_su_pct"] is not None and r["opp_su_pct"] <= 0.40
    C["good vs bad"] = lambda r: r["n_prior"] >= 10 and r["opp_su_pct"] is not None and ((r["su_pct"] >= 0.60 and r["opp_su_pct"] <= 0.40) or (r["su_pct"] <= 0.40 and r["opp_su_pct"] >= 0.60))
    C["division game"] = lambda r: r["division"]
    C["interconference"] = lambda r: not r["conference"]
    C["rematch (same teams as last game)"] = lambda r: r["rematch"]
    C["revenge (home lost last meeting)"] = lambda r: r["lost_last_meeting"] is True
    for m, nm in [(10, "Oct"), (11, "Nov"), (12, "Dec"), (1, "Jan"), (2, "Feb"), (3, "Mar"), (4, "Apr")]:
        C[f"{nm} (reg season)"] = (lambda m: lambda r: r["month"] == m and not r["playoffs"])(m)
    C["playoffs"] = lambda r: r["playoffs"] and not r["playin"]
    C["play-in"] = lambda r: r["playin"]
    C["playoffs game 1 of series"] = lambda r: r["playoffs"] and not r["playin"] and not r["rematch"]
    C["playoffs games 2+"] = lambda r: r["playoffs"] and not r["playin"] and r["rematch"]
    C["playoffs, |spread| >= 7"] = lambda r: r["playoffs"] and not r["playin"] and abs(r["spread"]) >= 7
    for d, nm in [(0, "Mon"), (1, "Tue"), (2, "Wed"), (3, "Thu"), (4, "Fri"), (5, "Sat"), (6, "Sun")]:
        C[f"{nm} games"] = (lambda d: lambda r: r["dow"] == d)(d)
    C["weekend (Fri-Sun)"] = lambda r: r["dow"] >= 4
    C["high total + either b2b"] = lambda r: r["total"] >= 233 and (r["b2b"] or r["ob2b"])
    C["low total + both rested"] = lambda r: r["total"] <= 222 and (r["rest"] or 0) >= 2 and (r["orest"] or 0) >= 2
    C["big spread + b2b dog"] = lambda r: abs(r["spread"]) >= 8 and ((r["dog"] and r["b2b"]) or (r["fav"] and r["ob2b"]))
    C["high total + big spread"] = lambda r: r["total"] >= 233 and abs(r["spread"]) >= 8
    C["low total + big spread"] = lambda r: r["total"] <= 224 and abs(r["spread"]) >= 8
    C["hot home (L10 net +5)"] = lambda r: r["net10"] is not None and r["net"] is not None and r["gp"] >= 15 and r["net10"] - r["net"] >= 5
    C["either hot (+5)"] = lambda r: r["net10"] is not None and r["net"] is not None and r["gp"] >= 15 and (r["net10"] - r["net"] >= 5 or r["onet10"] - r["onet"] >= 5)
    C["either cold (-5)"] = lambda r: r["net10"] is not None and r["net"] is not None and r["gp"] >= 15 and (r["net10"] - r["net"] <= -5 or r["onet10"] - r["onet"] <= -5)
    C["model pOver >= .60"] = lambda r: r["_pOver"] is not None and r["_pOver"] >= 0.60
    C["model pOver <= .40"] = lambda r: r["_pOver"] is not None and r["_pOver"] <= 0.40
    return C


def enrich(rows, games):
    """Cross-fields the registry wants (opponent's previous game, game pts)."""
    by_gi = defaultdict(dict)
    for r in rows:
        by_gi[r["gi"]][r["is_home"]] = r
    last_streak = defaultdict(int)
    for r in rows:  # rows are in game order; both rows of a game are adjacent
        o = by_gi[r["gi"]][not r["is_home"]]
        r["_o_prev_ou"] = o["prev_ou"]; r["_o_prev_margin"] = o["prev_margin"]; r["_o_prev_su"] = o["prev_su"]
        r["_o_ou_ostreak"] = o["ou_ostreak"]; r["_o_ou_ustreak"] = o["ou_ustreak"]; r["_o_ou_over_pct"] = o["ou_over_pct"]
        g = games[r["gi"]]
        r["_pts"] = g["pts"]; r["_pOver"] = g["pOver"]
        # length of the home stand / road trip the team just came off
        r["_prev_loc_streak"] = last_streak[r["team"]] if r["loc_streak"] == 1 else 0
        last_streak[r["team"]] = r["loc_streak"]
    return by_gi


# ---------------------------------------------------------------------------
def fmt(res, market):
    h1 = res["h1"]; h2 = res["h2"]
    def hs(h):
        n = h[0] + h[1]
        return f"{h[0]}-{h[1]} ({h[2]:+.1f}u)" if n else "-"
    pv = f"p={res['pval']:.3f}" if res["pval"] is not None else "p=n/a"
    return (f"{res['w']}-{res['l']}" + (f"-{res['p']}" if res['p'] else "") +
            f"  {res['pct']*100:.1f}%  {res['units']:+.1f}u  ROI {res['roi']*100:+.1f}%  {pv}"
            f"  | H1 {hs(h1)}  H2 {hs(h2)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-n", type=int, default=25)
    ap.add_argument("--min-roi", type=float, default=0.10)
    ap.add_argument("--combos", action="store_true", help="also screen pairwise combos (stricter bar)")
    ap.add_argument("--json", default=None, help="write survivors to this path")
    args = ap.parse_args()

    games = load_games()
    stats = load_stats()
    rows, playoff_start = build(games, stats)
    sigma, mu = fit_sigma(games)

    by_gi = enrich(rows, games)
    a, b = fit_ml(rows)

    home_rows = [r for r in rows if r["is_home"]]
    dates = sorted({r["d"] for r in rows})
    split_date = dates[len(dates) // 2]

    print(f"Games: {len(games)}  ({sum(1 for g in games if not g['playoffs'])} regular season, "
          f"{sum(1 for g in games if g['playoffs'])} post-season from {playoff_start})")
    print(f"Margin-vs-line residual: sigma={sigma:.2f}, mean={mu:+.2f} (home covers {sum(1 for g in games if g['margin_home']+g['line']>0)/len(games)*100:.1f}%)")
    print(f"ML priced from spread: logistic fit p=sigmoid({a:.3f} + {b:.4f}*-spread) + {ML_HOLD/2*100:.2f}% hold per side")
    print(f"Split-half date: {split_date}   Bar: n>={args.min_n}, ROI>={args.min_roi*100:.0f}%\n")

    survivors = []
    TC = team_conditions()
    n_screened = 0
    for name, cond in TC.items():
        for market in ("spread", "ml"):
            res = evaluate(rows, market, None, cond, sigma, split_date)
            n_screened += 1
            if res and res["n"] >= args.min_n and res["roi"] >= args.min_roi:
                survivors.append((market, name, res))
    OC = total_conditions()
    for name, cond in OC.items():
        for side in ("OVER", "UNDER"):
            res = evaluate(home_rows, "total", side, cond, sigma, split_date)
            n_screened += 1
            if res and res["n"] >= args.min_n and res["roi"] >= args.min_roi:
                survivors.append((f"total {side}", name, res))

    print(f"Screened {n_screened} condition x market combos "
          f"({len(TC)} team situations x spread/ML, {len(OC)} game situations x over/under)\n")

    def both_halves_positive(res):
        return res["h1"][2] > 0 and res["h2"][2] > 0

    for market in ("spread", "ml", "total OVER", "total UNDER"):
        sub = sorted([s for s in survivors if s[0] == market], key=lambda s: -s[2]["units"])
        print(f"=== {market.upper()}  ({len(sub)} survivors) ===")
        for mk, name, res in sub:
            flag = "  [both halves +]" if both_halves_positive(res) else ""
            print(f"  {name:38s} {fmt(res, mk)}{flag}")
        print()

    # ------------------------------------------------------------------
    if args.combos:
        print("=== PAIRWISE COMBOS (n>=40, ROI>=15%, both halves positive) -- treat skeptically ===")
        base_team = ["home", "away", "fav", "dog", "on b2b", "opp on b2b", "rest adv >= 2", "3+ days rest",
                     "after SU win", "after SU loss", "after ATS loss", "after ATS cover",
                     "after blowout loss (15+)", "after blowout win (15+)", "SU loss streak 3+", "SU win streak 3+",
                     "ATS loss streak 3+", "ATS win streak 3+", "lost last meeting (revenge)", "rematch (same opp as last game)",
                     "division game", "interconference", "playoffs home", "weekend dog",
                     "good team (.600+) as dog", "bad team (.400-) as fav", "vs bad team (.400-)", "vs good team (.600+)",
                     "hot (L10 net - season net >= +5)", "cold (L10 net - season net <= -5)",
                     "high total (>=235) dog", "low total (<=222) dog", "road trip game 3+", "home stand game 3+",
                     "stats say +4 vs line", "dog +0.5..+3", "dog +3.5..+6", "dog +6.5..+9", "dog +9.5..+12",
                     "fav -0.5..-3", "fav -3.5..-6", "fav -6.5..-9", "fav -9.5..-12"]
        base_team = [b for b in base_team if b in TC]
        combo_hits = []
        for a, b in combinations(base_team, 2):
            cond = (lambda ca, cb: lambda r: ca(r) and cb(r))(TC[a], TC[b])
            for market in ("spread", "ml"):
                res = evaluate(rows, market, None, cond, sigma, split_date)
                if res and res["n"] >= 40 and res["roi"] >= 0.15 and both_halves_positive(res):
                    combo_hits.append((market, f"{a} & {b}", res))
        base_tot = ["home on b2b", "away on b2b", "either on b2b", "neither on b2b", "both 2+ days rest",
                    "|spread| <= 2", "|spread| 2.5-6", "|spread| 6.5-10", "|spread| >= 10.5",
                    "total <=214.5", "total 215-220.5", "total 221-226.5", "total 227-232.5", "total 233-238.5", "total >=239",
                    "home fav", "home dog", "home prev game OVER", "home prev game UNDER", "both teams prev OVER", "both teams prev UNDER",
                    "either after blowout loss", "either after blowout win", "both after SU loss", "both after SU win",
                    "division game", "interconference", "rematch (same teams as last game)", "weekend (Fri-Sun)",
                    "two good teams (.600+)", "two bad teams (.400-)", "good vs bad", "either hot (+5)", "either cold (-5)",
                    "both fast (pace >= 101.5)", "both slow (pace <= 98.5)", "pace mismatch (>=4)"]
        base_tot = [b for b in base_tot if b in OC]
        for a, b in combinations(base_tot, 2):
            cond = (lambda ca, cb: lambda r: ca(r) and cb(r))(OC[a], OC[b])
            for side in ("OVER", "UNDER"):
                res = evaluate(home_rows, "total", side, cond, sigma, split_date)
                if res and res["n"] >= 40 and res["roi"] >= 0.15 and both_halves_positive(res):
                    combo_hits.append((f"total {side}", f"{a} & {b}", res))
        combo_hits.sort(key=lambda s: -s[2]["units"])
        for mk, name, res in combo_hits[:60]:
            print(f"  {mk:12s} {name:60s} {fmt(res, mk)}")
        print(f"  ({len(combo_hits)} combos cleared; {len(base_team)*(len(base_team)-1)//2*2 + len(base_tot)*(len(base_tot)-1)//2*2} screened)\n")
        survivors += combo_hits

    # ------------------------------------------------------------------
    print("=== PER-TEAM (n~41 per split; 10% ROI here is within noise for almost any team) ===")
    teams = sorted({r["team"] for r in rows})
    team_hits = []
    for t in teams:
        for label, cond in [("all", lambda r, t=t: r["team"] == t),
                            ("home", lambda r, t=t: r["team"] == t and r["is_home"]),
                            ("away", lambda r, t=t: r["team"] == t and not r["is_home"]),
                            ("as dog", lambda r, t=t: r["team"] == t and r["dog"]),
                            ("as fav", lambda r, t=t: r["team"] == t and r["fav"])]:
            for market in ("spread", "ml"):
                res = evaluate(rows, market, None, cond, sigma, split_date)
                if res and res["n"] >= args.min_n and res["roi"] >= args.min_roi:
                    team_hits.append((market, f"{t} {label}", res))
            if label in ("all", "home", "away"):
                tcond = (lambda c: lambda r: c(r) or c(by_gi[r["gi"]][False]))(cond)
                for side in ("OVER", "UNDER"):
                    res = evaluate(home_rows, "total", side, tcond, sigma, split_date)
                    if res and res["n"] >= args.min_n and res["roi"] >= args.min_roi:
                        team_hits.append((f"total {side}", f"{t} {label}", res))
    team_hits.sort(key=lambda s: -s[2]["units"])
    for mk, name, res in team_hits:
        flag = "  [both halves +]" if both_halves_positive(res) else ""
        print(f"  {mk:12s} {name:36s} {fmt(res, mk)}{flag}")

    if args.json:
        out = [{"market": mk, "system": nm, **{k: v for k, v in res.items()}} for mk, nm, res in survivors + team_hits]
        json.dump(out, open(args.json, "w"), indent=1, default=str)
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()

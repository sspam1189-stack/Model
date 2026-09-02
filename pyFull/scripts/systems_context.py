"""
systems_context.py -- situational context for NBA games, from lines + history.

THE ONE BUILDER.  The backtest (screen_systems.py / validate_systems.py) and
the live daily run (build_nba_systems.py) both import this module, so a system
cannot mean one thing in the backtest and another on tonight's slate.  The NFL
props pipeline works for exactly this reason and the MLB backfill keeps
breaking for want of it.

Everything here is derived from closing lines, final scores and the schedule --
no box scores, no injuries, no projection.  A team-row is one team's view of
one game and carries only what was knowable BEFORE tip: rest, prior-game
results, streaks, season-to-date rates, trip length, opponent history.

Pending games (tonight's slate, no scores yet) are first-class: they get full
context built from every prior game and simply contribute nothing back to the
running state.  That is what lets the live run evaluate the same conditions the
backtest scored.

TWO DATA TRAPS, both encoded here rather than in a comment somewhere else:

1. stats_cache/<D>.json ALREADY CONTAINS D's GAMES.  The backfill fetches it
   with DateTo=D inclusive, so GP counts games through D.  Reading cache[D] for
   D's games is look-ahead -- it was worth ~+65u of imaginary edge on the
   season-net-rating systems before it was caught.  stats_for() takes the
   latest cache strictly BEFORE the date.
2. THE NBA CUP FINALISTS PLAY 83 REGULAR-SEASON GAMES.  Taking the earliest
   83rd-game date as the playoff cut moved the boundary six days early.  The
   cut is the median.
"""
import json
import os
from collections import defaultdict
from datetime import date, datetime

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

JUICE = 1.0 / 1.1          # net units won on a -110 winner, flat 1u risked

STATS_DIRS = [
    os.path.join(HERE, "..", "..", "data", "stats_cache", "nba"),   # shared, full season
    os.path.join(DATA, "stats_cache"),                              # pyFull copy
]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_stats():
    out = {}
    for d in STATS_DIRS:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".json") and fn[:-5] not in out:
                try:
                    out[fn[:-5]] = json.load(open(os.path.join(d, fn), encoding="utf-8"))
                except Exception:
                    pass
    return out


def stats_for(stats, d8):
    """Latest cache strictly BEFORE d8 -- see trap 1 in the module docstring."""
    keys = [k for k in stats if k < d8]
    return stats[max(keys)] if keys else None


#: never a real game -- excluded outright. SKIPPED is deliberately NOT here:
#: those are real games with real lines and finals where only the projection
#: failed, and the backtest counted all 215 of them.
DEAD_STATUS = {"POSTPONED", "CANCELED", "CANCELLED"}


def _row_from_history(date8, g):
    """One history game -> the flat shape build() wants, or None if unusable."""
    if g.get("status") in DEAD_STATUS:
        return None
    if g.get("line") is None or g.get("total") is None:
        return None
    hs, as_ = g.get("homeScore"), g.get("awayScore")
    settled = isinstance(hs, (int, float)) and isinstance(as_, (int, float))
    return {
        "date": date8,
        "d": datetime.strptime(date8, "%Y%m%d").date(),
        "home": g["home"], "away": g["away"],
        "line": float(g["line"]), "total": float(g["total"]),
        "hs": float(hs) if settled else None,
        "as": float(as_) if settled else None,
        "settled": settled,
        # real prices when the feed carried them (h2h added 2026-09); None on
        # every historical row, which is why ML systems have no backtest ROI
        "home_ml": g.get("home_ml"), "away_ml": g.get("away_ml"),
        "over_ml": g.get("over_ml"), "under_ml": g.get("under_ml"),
        "spread_price_home": g.get("spread_price_home"),
        "spread_price_away": g.get("spread_price_away"),
        "startTimeUTC": g.get("startTimeUTC"),
        "pOver": g.get("pOver"), "pHomeCover": g.get("pHomeCover"),
    }


def load_games(store=None, history_path=None, pending=None, pending_date=None):
    """Every usable game, oldest first.

    store / history_path : the pyFull history (either the loaded dict or a path).
    pending              : tonight's odds rows (away/home/line/total, no scores),
                           appended as unsettled games dated `pending_date`.
    """
    if store is None:
        history_path = history_path or os.path.join(DATA, "history.json")
        store = json.load(open(history_path, encoding="utf-8"))

    games, seen = [], set()
    for r in store.get("runs", []):
        d8 = r.get("date")
        if not d8:
            continue
        for g in r.get("games", []):
            key = (d8, g.get("away"), g.get("home"))
            if key in seen:
                continue
            row = _row_from_history(d8, g)
            if row is None:
                continue
            seen.add(key)
            games.append(row)

    for g in (pending or []):
        d8 = pending_date or g.get("date")
        if not d8 or g.get("line") is None or g.get("total") is None:
            continue
        key = (d8, g.get("away"), g.get("home"))
        if key in seen:          # already carded earlier today -- keep the first
            continue
        seen.add(key)
        row = _row_from_history(d8, {**g, "homeScore": None, "awayScore": None})
        if row:
            games.append(row)

    games.sort(key=lambda g: (g["d"], g["home"]))
    return games


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------
def _streak(seq, val):
    n = 0
    for x in reversed(seq):
        if x == val:
            n += 1
        else:
            break
    return n


def build(games, stats=None):
    """Attach two team-rows per game. Returns (rows, playoff_start)."""
    stats = stats if stats is not None else {}

    by_team = defaultdict(list)
    for i, g in enumerate(games):
        by_team[g["home"]].append(i)
        by_team[g["away"]].append(i)

    # Regular season = a team's first 82 games. The two NBA Cup finalists play
    # 83, so the cut is the MEDIAN 83rd-game date, not the earliest (trap 2).
    cuts = sorted(games[idxs[82]]["d"] for idxs in by_team.values() if len(idxs) > 82)
    playoff_start = cuts[len(cuts) // 2] if cuts else date(2099, 1, 1)
    playin_end = date.fromordinal(playoff_start.toordinal() + 3)

    state = {t: {"last_d": None, "last_home": None, "streak_loc": 0,
                 "su": [], "ats": [], "ou": [], "margins": [], "ats_margins": [],
                 "was_fav": [], "dates": [], "vs": {}, "last_opp": None}
             for t in by_team}

    rows = []
    for gi, g in enumerate(games):
        g["playoffs"] = g["d"] >= playoff_start
        g["playin"] = playoff_start <= g["d"] <= playin_end
        g["month"] = g["d"].month
        g["dow"] = g["d"].weekday()          # 0 = Monday
        g["division"] = TEAM_DIV.get(g["home"]) == TEAM_DIV.get(g["away"])
        g["conference"] = TEAM_CONF.get(g["home"]) == TEAM_CONF.get(g["away"])
        g["margin_home"] = (g["hs"] - g["as"]) if g["settled"] else None
        g["pts"] = (g["hs"] + g["as"]) if g["settled"] else None
        g["ou_result"] = (None if not g["settled"] else
                          "O" if g["pts"] > g["total"] else
                          "U" if g["pts"] < g["total"] else "P")
        st = stats_for(stats, g["date"])

        for side in ("home", "away"):
            team = g[side]
            opp = g["away"] if side == "home" else g["home"]
            s, so = state.setdefault(team, dict(state[g["home"]])), state.get(opp)
            s = state[team]
            so = state.get(opp) or {}
            is_home = side == "home"
            spread = g["line"] if is_home else -g["line"]
            margin = (g["margin_home"] if is_home else -g["margin_home"]) \
                if g["settled"] else None
            adj = (margin + spread) if margin is not None else None

            rest = (g["d"] - s["last_d"]).days if s["last_d"] else None
            orest = (g["d"] - so["last_d"]).days if so.get("last_d") else None
            recent3 = [d for d in s["dates"] if (g["d"] - d).days <= 3]
            orecent3 = [d for d in so.get("dates", []) if (g["d"] - d).days <= 3]

            n_prior = len(s["su"])
            ats_dec = sum(1 for x in s["ats"] if x != 0)
            ou_dec = sum(1 for x in s["ou"] if x != "P")
            tstat = (st or {}).get("season", {}).get(team)
            ostat = (st or {}).get("season", {}).get(opp)
            tl10 = (st or {}).get("last10", {}).get(team)
            ol10 = (st or {}).get("last10", {}).get(opp)

            row = {
                "gi": gi, "d": g["d"], "date": g["date"], "team": team, "opp": opp,
                "is_home": is_home, "spread": spread, "total": g["total"],
                "settled": g["settled"],
                "margin": margin, "adj": adj,
                "cover": None if adj is None else (1 if adj > 0 else (0 if adj < 0 else None)),
                "win": None if margin is None else (1 if margin > 0 else 0),
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
                "su_wstreak": _streak(s["su"], 1), "su_lstreak": _streak(s["su"], 0),
                "ats_wstreak": _streak(s["ats"], 1), "ats_lstreak": _streak(s["ats"], -1),
                "ou_ostreak": _streak(s["ou"], "O"), "ou_ustreak": _streak(s["ou"], "U"),
                "su_pct": (sum(s["su"]) / n_prior) if n_prior else None,
                "ats_pct": (sum(1 for x in s["ats"] if x == 1) / ats_dec) if ats_dec else None,
                "ou_over_pct": (sum(1 for x in s["ou"] if x == "O") / ou_dec) if ou_dec else None,
                "opp_su_pct": (sum(so["su"]) / len(so["su"])) if so.get("su") else None,
                "loc_streak": (s["streak_loc"] + 1) if s["last_home"] == is_home else 1,
                "lost_last_meeting": (s["vs"].get(opp, [None])[-1] == 0) if s["vs"].get(opp) else None,
                "won_last_meeting": (s["vs"].get(opp, [None])[-1] == 1) if s["vs"].get(opp) else None,
                "rematch": s["last_opp"] == opp,
                "playoffs": g["playoffs"], "playin": g["playin"], "month": g["month"],
                "dow": g["dow"], "division": g["division"], "conference": g["conference"],
                "net": (tstat["OFF"] - tstat["DEF"]) if tstat else None,
                "onet": (ostat["OFF"] - ostat["DEF"]) if ostat else None,
                "net10": (tl10["OFF"] - tl10["DEF"]) if tl10 else None,
                "onet10": (ol10["OFF"] - ol10["DEF"]) if ol10 else None,
                "pace": tstat["PACE"] if tstat else None,
                "opace": ostat["PACE"] if ostat else None,
                "gp": tstat["GP"] if tstat else 0,
                # prices, for the ledger. None on every pre-2026-09 row.
                "ml_price": g["home_ml"] if is_home else g["away_ml"],
                "spread_price": g["spread_price_home"] if is_home else g["spread_price_away"],
                "over_ml": g["over_ml"], "under_ml": g["under_ml"],
                "startTimeUTC": g.get("startTimeUTC"),
                "matchup": f"{g['away']} @ {g['home']}",
            }
            if row["net"] is not None and row["onet"] is not None and row["gp"] >= 10:
                row["stat_margin"] = row["net"] - row["onet"] + (2.0 if is_home else -2.0)
                row["stat_edge"] = row["stat_margin"] + spread
            else:
                row["stat_margin"] = row["stat_edge"] = None
            rows.append(row)
            g.setdefault("rows", {})[side] = row

        # State advances only on SETTLED games -- a pending game contributes
        # nothing, so tonight's slate cannot pollute its own context.
        if g["settled"]:
            for side in ("home", "away"):
                team, opp = ((g["home"], g["away"]) if side == "home"
                             else (g["away"], g["home"]))
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


def enrich(rows, games):
    """Cross-fields the registry needs: the opponent's previous game, game pts."""
    by_gi = defaultdict(dict)
    for r in rows:
        by_gi[r["gi"]][r["is_home"]] = r
    last_streak = defaultdict(int)
    for r in rows:                       # both rows of a game are adjacent
        o = by_gi[r["gi"]][not r["is_home"]]
        r["_o_prev_ou"] = o["prev_ou"]
        r["_o_prev_margin"] = o["prev_margin"]
        r["_o_prev_su"] = o["prev_su"]
        r["_o_ou_ostreak"] = o["ou_ostreak"]
        r["_o_ou_ustreak"] = o["ou_ustreak"]
        r["_o_ou_over_pct"] = o["ou_over_pct"]
        g = games[r["gi"]]
        r["_pts"] = g["pts"]
        r["_pOver"] = g.get("pOver")
        r["_prev_loc_streak"] = last_streak[r["team"]] if r["loc_streak"] == 1 else 0
        last_streak[r["team"]] = r["loc_streak"]
    return by_gi

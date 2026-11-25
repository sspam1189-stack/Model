"""
systems_extra.py -- extra context for the shadow "watch" systems added
2026-11-15. Two kinds of information the original screen never had:

  THREE-POINT LUCK (from the saved per-game player logs). For a team's last
  game: threes made above what its usual rate gives on the shots it took,
  minus the same for its opponent, in points. Rates are season-to-date and
  pulled toward the league rate (300 attempts) so early games do not swing
  them. Needs 5 prior games.
      _3own    points from the team's own made threes above expectation
      _3luck   that, minus the opponent's (negative = unlucky)
      _o_3own  the opponent row's _3own

  INTRADAY LINE MOVES (from the live runs' recorded inputs,
  data/run_inputs/nba/<date>/). The first line saved that day against the
  line on the row.
      _spread_move   this team's spread now minus its first spread of the day
                     (positive = the market moved AGAINST this team)
      _total_move    the total now minus the first total of the day

Fields are None when the data is missing, and every watch system treats None
as "does not fire". Nothing here touches the carded systems.
"""
import collections
import glob
import gzip
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

SHRINK_ATT = 300.0
MIN_PRIOR = 5


def _abbr_map():
    try:
        from core.ratings_model import ABBR
        return dict(ABBR)
    except Exception:
        return {}


def _season(d8):
    y, m = int(d8[:4]), int(d8[4:6])
    s = y if m >= 10 else y - 1
    return f"{s}-{str((s + 1) % 100).zfill(2)}"


def _team_games(season):
    p = os.path.join(REPO, "data", "player_cache", "nba", f"basic_{season}_Regular_Season.json")
    if not os.path.exists(p):
        return []
    try:
        with open(p, encoding="utf-8") as f:
            rows = json.load(f) or []
    except Exception:
        return []
    fix = lambda t: "LAC" if str(t or "").strip() in ("LA", "LAC") else str(t or "").strip()
    tg = collections.defaultdict(lambda: {"m": 0, "a": 0})
    meta = {}
    for r in rows:
        k = (r.get("game_id"), fix(r.get("team")))
        tg[k]["m"] += r.get("fg3m") or 0
        tg[k]["a"] += r.get("fg3a") or 0
        meta[k] = (str(r.get("game_date") or "").replace("-", ""), fix(r.get("opp")))
    out = []
    for (gid, t), v in tg.items():
        d, opp = meta[(gid, t)]
        if d and (gid, opp) in tg:
            o = tg[(gid, opp)]
            out.append({"d": d, "gid": gid, "t": t, "m": v["m"], "a": v["a"], "om": o["m"], "oa": o["a"]})
    out.sort(key=lambda x: (x["d"], str(x["gid"])))
    return out


def _three_point(rows):
    """Attach _3own / _3luck to each team-row (None when not available)."""
    ab = _abbr_map()
    by_season = {}
    for r in rows:
        r["_3own"] = r["_3luck"] = None
    for r in rows:
        season = _season(r["date"])
        if season not in by_season:
            tg = _team_games(season)
            per = collections.defaultdict(list)
            for x in tg:
                per[x["t"]].append(x)
            by_season[season] = (tg, per)
        tg, per = by_season[season]
        t = ab.get(r["team"])
        if not t or not tg:
            continue
        prior = [x for x in per.get(t, []) if x["d"] < r["date"]]
        if len(prior) < MIN_PRIOR:
            continue
        lm = sum(x["m"] for x in tg if x["d"] < r["date"])
        la = sum(x["a"] for x in tg if x["d"] < r["date"])
        lg = (lm / la) if la else 0.36
        p = (sum(x["m"] for x in prior) + lg * SHRINK_ATT) / (sum(x["a"] for x in prior) + SHRINK_ATT)
        op = (sum(x["om"] for x in prior) + lg * SHRINK_ATT) / (sum(x["oa"] for x in prior) + SHRINK_ATT)
        last = prior[-1]
        own = 3.0 * (last["m"] - p * last["a"])
        r["_3own"] = own
        r["_3luck"] = own - 3.0 * (last["om"] - op * last["oa"])


def first_lines(d8):
    """{(away, home): (home line, total)} from the earliest recorded run of the
    day that priced the game before its tip."""
    out = {}
    snaps = []
    for p in sorted(glob.glob(os.path.join(REPO, "data", "run_inputs", "nba", d8, "*.json.gz"))):
        try:
            with gzip.open(p, "rt", encoding="utf-8") as f:
                snaps.append(json.load(f))
        except Exception:
            pass
    snaps.sort(key=lambda s: s.get("startedAt") or "")
    for s in snaps:
        t = str(s.get("startedAt") or "")
        for g in ((s.get("inputs") or {}).get("odds") or {}).get("v") or []:
            k = (g.get("away"), g.get("home"))
            if k in out or not isinstance(g.get("line"), (int, float)) or not isinstance(g.get("total"), (int, float)):
                continue
            tip = str(g.get("startTimeUTC") or "")
            if tip and t and _iso(t) >= _iso(tip):
                continue
            out[k] = (float(g["line"]), float(g["total"]))
    return out


def _iso(s):
    import datetime as dt
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(dt.timezone.utc).isoformat()
    except Exception:
        return str(s)


def _line_moves(rows):
    cache = {}
    for r in rows:
        r["_spread_move"] = r["_total_move"] = None
        d8 = r["date"]
        if d8 not in cache:
            cache[d8] = first_lines(d8)
        away, home = (r["opp"], r["team"]) if r["is_home"] else (r["team"], r["opp"])
        f = cache[d8].get((away, home))
        if not f or r.get("spread") is None or r.get("total") is None:
            continue
        first_spread = f[0] if r["is_home"] else -f[0]
        r["_spread_move"] = r["spread"] - first_spread
        r["_total_move"] = r["total"] - f[1]


def attach(rows):
    """Add the extra fields to every team-row, then the opponent's _3own."""
    try:
        _three_point(rows)
    except Exception as e:
        print(f"  [systems] three-point context failed (non-fatal): {e}")
    try:
        _line_moves(rows)
    except Exception as e:
        print(f"  [systems] line-move context failed (non-fatal): {e}")
    by_gi = collections.defaultdict(dict)
    for r in rows:
        by_gi[r["gi"]][r["is_home"]] = r
    for r in rows:
        o = by_gi[r["gi"]].get(not r["is_home"])
        r["_o_3own"] = o.get("_3own") if o else None
        r["_o_spread_price"] = o.get("spread_price") if o else None   # juiced_side_ats
    return rows

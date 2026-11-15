# core/ratings_model.py
#
# Team ratings for NBA Full Ratings (pyFullRatings), built from game results
# and box scores instead of raw season averages. Three parts:
#
#   1. OPPONENT-ADJUSTED RATINGS. One ridge regression over every game played
#      so far solves each team's offence, defence and pace together, given who
#      the opponent was and who was at home. Offence and defence count the
#      same, and a team with few games is pulled toward its prior.
#   2. TONIGHT'S LINEUP. Each player has a value (box-score production per
#      minute above a bench-level replacement). A team's rating reflects the
#      lineups it has fielded; tonight's expected lineup (the roster minus
#      players the injury report lists out or doubtful) is compared with that,
#      and the gap moves the team's offence.
#   3. LAST SEASON AS THE STARTING POINT. Each team's prior is last season's
#      final rating, regressed toward average, plus the change in roster value
#      (this season's roster, valued with last season's player numbers, against
#      the lineups the team fielded last season). The prior counts as
#      PRIOR_GAMES games and fades as this season's games accumulate.
#
# The result is a normal team-stats table (OFF / DEF / PACE per team), shaped so
# the shared engine's formula -- (OFF + wDEF x (oppDEF - league DEF)) x pace --
# reproduces the ratings projection when wDEF is 1.
#
# Inputs: the per-game player logs the props pipeline saves every day
# (data/player_cache/nba/basic_<season>_Regular_Season.json) for this season and
# last, and tonight's injury report. Only games before the run date are used, so
# a replay sees exactly what a live run on that date would.
#
# Checked 2026-11-15 (walk-forward, 2026-27 through Nov 13, 167 games): average
# margin miss 11.34, against 12.84 for KalmanOff and 10.90 for the closing line.
import collections
import datetime as dt
import json
import os
import statistics

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))

SHRINK_GAMES = 6        # ridge strength when there is no prior season
PRIOR_GAMES = 12        # how many games the last-season prior counts as
PRIOR_REGRESS = 0.75    # last season's rating is pulled this far toward average
VALUE_POINTS = 0.5      # points of margin per unit of lineup value
PLAYER_PRIOR_MIN = 300  # minutes a player's prior rate counts as
HCA_PRIOR, HCA_PRIOR_W = 2.6, 2.0

ABBR = {
    "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN", "Charlotte Hornets": "CHA",
    "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE", "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN",
    "Detroit Pistons": "DET", "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
    "LA Clippers": "LAC", "Los Angeles Clippers": "LAC", "Los Angeles Lakers": "LAL", "Memphis Grizzlies": "MEM",
    "Miami Heat": "MIA", "Milwaukee Bucks": "MIL", "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP",
    "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL", "Philadelphia 76ers": "PHI",
    "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR", "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS",
    "Toronto Raptors": "TOR", "Utah Jazz": "UTA", "Washington Wizards": "WAS",
}


def _season(date):
    y, m = int(date[:4]), int(date[4:6])
    start = y if m >= 10 else y - 1
    return f"{start}-{str((start + 1) % 100).zfill(2)}"


def _prev_season(season):
    y = int(season[:4]) - 1
    return f"{y}-{str((y + 1) % 100).zfill(2)}"


def _fix(team):
    t = str(team or "").strip()
    return "LAC" if t in ("LA", "LAC") else t


def _game_score(r):
    return (r["pts"] + 0.4 * r["fgm"] - 0.7 * r["fga"] - 0.4 * (r["fta"] - r["ftm"]) + 0.7 * r["oreb"]
            + 0.3 * r["dreb"] + r["stl"] + 0.7 * r["ast"] + 0.7 * r["blk"] - 0.4 * r["pf"] - r["tov"])


_LOG_CACHE = {}


def _load_logs(season):
    """Raw per-game player rows for a season's regular season ([] if not saved)."""
    if season in _LOG_CACHE:
        return _LOG_CACHE[season]
    rows = []
    roots = [os.environ.get("PYFULL_BACKFILL_REPO"), os.path.normpath(os.path.join(_HERE, ".."))]
    for root in [r for r in roots if r]:
        p = os.path.join(root, "data", "player_cache", "nba", f"basic_{season}_Regular_Season.json")
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    rows = json.load(f) or []
            except Exception:
                rows = []
            if rows:
                break
    _LOG_CACHE[season] = rows
    return rows


def _games(rows, before=None):
    """Game-level results from the player rows: date, home, away, points,
    possessions (FGA - OREB + TOV + 0.44 FTA, averaged over the two teams)."""
    tg = collections.defaultdict(lambda: dict(pts=0, fga=0, fta=0, oreb=0, tov=0))
    meta = {}
    for r in rows:
        d = str(r.get("game_date") or "").replace("-", "")
        if not d or (before and d >= before):
            continue
        k = (r["game_id"], _fix(r["team"]))
        a = tg[k]
        for f in ("pts", "fga", "fta", "oreb", "tov"):
            a[f] += r.get(f) or 0
        meta[k] = (d, bool(r.get("is_home")), _fix(r.get("opp")))
    games = {}
    for (gid, t), a in tg.items():
        d, home, opp = meta[(gid, t)]
        if (gid, opp) not in tg:
            continue
        g = games.setdefault(gid, {"d": d, "id": gid})
        s = "h" if home else "a"
        g[s] = t
        g[s + "p"] = a["pts"]
        g[s + "poss"] = a["fga"] - a["oreb"] + a["tov"] + 0.44 * a["fta"]
    out = [g for g in games.values() if all(k in g for k in ("h", "a", "hp", "ap", "hposs", "aposs"))]
    for g in out:
        g["poss"] = (g["hposs"] + g["aposs"]) / 2
    out.sort(key=lambda g: (g["d"], g["id"]))
    return [g for g in out if g["poss"] > 0]


def _player_rows(rows, before=None):
    """(date, game id, team, player, minutes, game score), played minutes only."""
    out = []
    for r in rows:
        d = str(r.get("game_date") or "").replace("-", "")
        if not d or (before and d >= before):
            continue
        try:
            m = float(r.get("min") or 0)
        except (TypeError, ValueError):
            continue
        if m > 0 and r.get("player_name"):
            out.append((d, r["game_id"], _fix(r["team"]), r["player_name"], m, _game_score(r)))
    out.sort()
    return out


def _ridge(games, lam, prior_net, teams):
    """eff(team vs opp) = mu + off[team] - dfn[opp] +/- hca/2  (points per 100)
    poss(game)        = mup + pace[home] + pace[away]
    off/dfn shrink toward prior_net[team] / 2 with strength lam, pace toward 0."""
    ix = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    X = np.zeros((2 * len(games), 2 * n + 2))
    y = np.zeros(2 * len(games))
    Xp = np.zeros((len(games), n + 1))
    yp = np.zeros(len(games))
    for i, g in enumerate(games):
        for j, (me, op, pts, s) in enumerate(((g["h"], g["a"], g["hp"], 0.5), (g["a"], g["h"], g["ap"], -0.5))):
            r = 2 * i + j
            X[r, ix[me]] = 1
            X[r, n + ix[op]] = -1
            X[r, 2 * n] = s
            X[r, 2 * n + 1] = 1
            y[r] = 100 * pts / g["poss"]
        Xp[i, ix[g["h"]]] = 1
        Xp[i, ix[g["a"]]] = 1
        Xp[i, n] = 1
        yp[i] = g["poss"]
    P = np.eye(2 * n + 2) * lam
    P[2 * n, 2 * n] = HCA_PRIOR_W
    P[2 * n + 1, 2 * n + 1] = 1e-6
    pr = np.zeros(2 * n + 2)
    pr[2 * n] = HCA_PRIOR
    pr[2 * n + 1] = y.mean()
    for t in teams:
        v = (prior_net or {}).get(t, 0.0) / 2
        pr[ix[t]] = v
        pr[n + ix[t]] = v
    b = np.linalg.solve(X.T @ X + P, X.T @ y + P @ pr)
    Pp = np.eye(n + 1) * lam
    Pp[n, n] = 1e-6
    prp = np.zeros(n + 1)
    prp[n] = yp.mean()
    bp = np.linalg.solve(Xp.T @ Xp + Pp, Xp.T @ yp + Pp @ prp)
    return {"off": {t: float(b[ix[t]]) for t in teams}, "dfn": {t: float(b[n + ix[t]]) for t in teams},
            "hca": float(b[2 * n]), "mu": float(b[2 * n + 1]),
            "pace": {t: float(bp[ix[t]]) for t in teams}, "mup": float(bp[n])}


_PRIOR_CACHE = {}


def _prior_season(season):
    """Last season's final ratings and player numbers (None if its logs are not saved)."""
    if season in _PRIOR_CACHE:
        return _PRIOR_CACHE[season]
    rows = _load_logs(season)
    out = None
    if rows:
        games = _games(rows)
        pl = _player_rows(rows)
        teams = sorted({g["h"] for g in games} | {g["a"] for g in games})
        fit = _ridge(games, SHRINK_GAMES, None, teams)
        tot = collections.defaultdict(lambda: [0.0, 0.0])      # player -> [minutes, game score]
        mins = collections.defaultdict(list)
        team_of = {}
        by_game = collections.defaultdict(lambda: collections.defaultdict(list))
        for d, gid, t, n, m, gs in pl:
            tot[n][0] += m
            tot[n][1] += gs
            mins[n].append(m)
            team_of[n] = t
            by_game[gid][t].append((n, m))
        lg = sum(v[1] for v in tot.values()) / max(1.0, sum(v[0] for v in tot.values()))
        rm = rg = 0.0                                           # replacement = players averaging 8-16 minutes
        for n, ms in mins.items():
            if len(ms) >= 15 and 8 <= sum(ms) / len(ms) < 16:
                rm += tot[n][0]
                rg += tot[n][1]
        repl = (rg / rm) if rm > 0 else lg * 0.8

        def rate(n):
            v = tot.get(n)
            return ((v[1] / v[0]) if v and v[0] >= PLAYER_PRIOR_MIN else lg * 0.9) - repl
        tg = collections.defaultdict(list)
        for gid, tt in by_game.items():
            for t in tt:
                tg[t].append(gid)
        field = {t: float(np.mean([sum(m * rate(n) for n, m in by_game[g][t]) for g in gs])) for t, gs in tg.items()}
        out = {"net": {t: fit["off"][t] + fit["dfn"][t] for t in teams}, "field": field, "tot": dict(tot),
               "mpg": {n: sum(ms) / len(ms) for n, ms in mins.items()}, "team_of": team_of, "lg": lg, "repl": repl}
    _PRIOR_CACHE[season] = out
    return out


def build_team_stats(date, team_names, injury_report=None):
    """Team-stats table for `date` (games before it only).

    Returns (stats, info): stats = {team name: {OFF, DEF, PACE, GP}} for every
    name in team_names that maps to a team; info = {"hca": home edge in points,
    "notes": {team name: text}, "games": n}. Returns (None, info) when there are
    too few games to solve (fewer than 8 and no prior season)."""
    season = _season(date)
    rows = _load_logs(season)
    games = _games(rows, before=date)
    prior = _prior_season(_prev_season(season))
    info = {"hca": None, "notes": {}, "games": len(games)}
    if len(games) < 8:
        return None, info
    names = {}                                  # abbreviation -> the name this run uses
    for nm in team_names:
        ab = ABBR.get(nm)
        if ab and ab not in names:
            names[ab] = nm
    teams = sorted(set(names) | {g["h"] for g in games} | {g["a"] for g in games} | set((prior or {}).get("net", {})))

    # ---- player numbers this season (before `date`)
    pl = _player_rows(rows, before=date)
    acc = collections.defaultdict(lambda: [0.0, 0.0])
    mins = collections.defaultdict(list)
    team_games = collections.defaultdict(list)
    last_seen = collections.defaultdict(dict)
    by_game = collections.defaultdict(lambda: collections.defaultdict(list))
    for d, gid, t, n, m, gs in pl:
        acc[n][0] += m
        acc[n][1] += gs
        mins[n].append(m)
        last_seen[t][n] = d
        by_game[gid][t].append((n, m))
        if not team_games[t] or team_games[t][-1] != gid:
            team_games[t].append(gid)
    if prior:
        lg, repl = prior["lg"], prior["repl"]
    else:
        lg = sum(v[1] for v in acc.values()) / max(1.0, sum(v[0] for v in acc.values()))
        repl = lg * 0.8

    def val(n):                                 # value per minute above replacement, shrunk to last season's
        a = acc.get(n) or (0.0, 0.0)
        p = (prior or {}).get("tot", {}).get(n)
        pr = (p[1] / p[0]) if p and p[0] >= PLAYER_PRIOR_MIN else lg * 0.9
        return (a[1] + PLAYER_PRIOR_MIN * pr) / (a[0] + PLAYER_PRIOR_MIN) - repl

    def typ(n):                                 # typical minutes: median of the last 10 games, else last season's
        if mins.get(n):
            return statistics.median(mins[n][-10:])
        return float((prior or {}).get("mpg", {}).get(n, 0.0))

    def lineup_value(players):
        ps = [(n, typ(n)) for n in players if typ(n) > 0]
        tot = sum(m for _, m in ps)
        if tot <= 0:
            return None
        sc = 240 / tot if tot > 240 else min(240 / tot, 1.15)   # others absorb some minutes; the rest is replacement
        return sum(m * sc * val(n) for n, m in ps)

    # ---- rosters and tonight's absences
    out_by_team = collections.defaultdict(set)
    for nm, lst in (injury_report or {}).items():
        ab = ABBR.get(nm)
        for i in (lst or []) if ab else []:
            if i.get("status") in ("out", "doubtful") and i.get("player"):
                out_by_team[ab].add(i["player"])
    cut = (dt.datetime.strptime(date, "%Y%m%d") - dt.timedelta(days=12)).strftime("%Y%m%d")
    seen_anywhere = {n for t in last_seen for n in last_seen[t]}
    roster, full = {}, {}
    for t in teams:
        ros = {n for g in team_games[t][-4:] for n, _ in by_game[g][t]} | {n for n, d in last_seen[t].items() if d >= cut}
        if not ros and prior:                   # no games yet: last season's roster, minus players now elsewhere
            ros = {n for n, tt in prior["team_of"].items() if tt == t and n not in seen_anywhere}
        outs = out_by_team.get(t, set())
        ros |= {n for n in outs if typ(n) > 0}
        roster[t] = ros
        full[t] = lineup_value(ros) or 0.0

    # ---- step 3: the prior
    prior_net, lam = None, SHRINK_GAMES
    if prior:
        lam = PRIOR_GAMES
        lg_full = float(np.mean([full[t] for t in teams]))
        lg_field = float(np.mean([prior["field"].get(t, 0.0) for t in teams]))
        prior_net = {t: PRIOR_REGRESS * prior["net"].get(t, 0.0)
                        + VALUE_POINTS * ((full[t] - lg_full) - (prior["field"].get(t, lg_field) - lg_field))
                     for t in teams}

    # ---- step 1: opponent-adjusted ratings
    fit = _ridge(games, lam, prior_net, teams)
    poss100 = fit["mup"] / 100.0

    # ---- step 2: tonight's lineup against the lineups the rating reflects
    stats = {}
    for t in teams:
        nm = names.get(t)
        if not nm:
            continue
        gp = len(team_games[t])
        outs = out_by_team.get(t, set())
        tv = lineup_value([n for n in roster[t] if n not in outs])
        fielded = float(np.mean([sum(m * val(n) for n, m in by_game[g][t]) for g in team_games[t]])) if gp else None
        delta = 0.0
        if tv is not None:
            share = (lam / (gp + lam)) if prior else 0.0        # how much of the rating is the prior (full roster)
            base = share * full[t] + (1 - share) * (fielded if fielded is not None else full[t])
            delta = tv - base
        pts = VALUE_POINTS * delta                               # points of margin
        stats[nm] = {
            "OFF": round(fit["mu"] + fit["off"][t] + pts / poss100, 2),
            "DEF": round(fit["mu"] - fit["dfn"][t], 2),
            "PACE": round(fit["mup"] + 2 * fit["pace"][t], 2),   # the engine averages the two teams' PACE
            "GP": gp,
        }
        missing = sorted(((typ(n) * val(n), n) for n in outs if n in roster[t] and typ(n) > 0), reverse=True)
        if abs(pts) >= 0.3 or missing:
            who = ", ".join(n for _, n in missing[:4])
            info["notes"][nm] = f"lineup {pts:+.1f} pts" + (f" (out: {who})" if who else "")
    info["hca"] = round(fit["hca"] * poss100, 2)
    return stats, info

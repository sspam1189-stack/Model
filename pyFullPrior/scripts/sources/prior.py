# sources/prior.py -- NBA Full Prior only.
#
# Two things the other full-season models do not do:
#
# 1. LAST SEASON AS THE STARTING POINT (blend_team_prior). Early ratings rest
#    on a handful of games, so each team's stats are blended with last season's
#    final regular-season numbers, the prior counting as PRIOR_GAMES games and
#    regressed toward the league average by PRIOR_REGRESS:
#        stat = league_avg_now + (GP*(cur - league_avg_now) + K*r*(prior - prior_league_avg)) / (GP + K)
#    The prior fades as games are played (10 GP -> 50/50 with K = 10).
#    Check on 2026-27 through Nov 11 (154 games, KalmanOff's inputs): average
#    margin miss 12.82 -> ~11.2 (Vegas 10.85); first week 14.8 -> ~10.6.
#
# 2. INJURIES FROM GAME 1 (adjust_team_stats_prior). The stock lineup adjustment
#    needs this season's player ratings, which only exist once a player has 10
#    games. Here a team's roster and each player's ratings fall back to last
#    season's until this season's are available, and each absence is scaled by
#    how much of the team's (blended) rating actually includes that player:
#      - out tonight, played for this team last season: full deduction
#      - out tonight, new to the team: only the this-season share GP/(GP+K)
#      - out for a long stretch (4+ of the last 10 reports), with the team last
#        season: only the prior share K/(GP+K) -- this season's stats already
#        reflect the absence, last season's do not
#      - out for a long stretch, new to the team: nothing
#
# Both use only the run's recorded inputs plus the static last-season files in
# data/priors/nba, so a replay reproduces them exactly.
import json
import math
import os

from sources.lineup_adjust import _resolve_team_name, _real_last_name

_HERE = os.path.dirname(os.path.abspath(__file__))
PRIOR_DIR = os.path.normpath(os.path.join(_HERE, "..", "..", "..", "data", "priors", "nba"))
STAT_KEYS = ["OFF", "DEF", "TS", "TO", "ORR", "PACE"]


def prior_season(season):
    """'2026-27' -> '2025-26'."""
    y = int(str(season)[:4]) - 1
    return f"{y}-{str((y + 1) % 100).zfill(2)}"


def _load(name):
    try:
        with open(os.path.join(PRIOR_DIR, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"  [prior] {name} not available ({e})")
        return None


def load_team_prior(season):
    d = _load(f"team_{prior_season(season)}.json")
    return (d or {}).get("teams") or {}


def load_player_prior(season):
    d = _load(f"players_{prior_season(season)}.json")
    return (d or {}).get("players") or {}


def _avg(table, key):
    vals = [v[key] for v in table.values() if isinstance(v.get(key), (int, float)) and math.isfinite(v[key])]
    return sum(vals) / len(vals) if vals else None


def blend_team_prior(stats, prior, k_games=10.0, regress=0.8):
    """Blend each team's stats with last season's. Returns (blended, shares)
    where shares[team] is the prior's weight K/(GP+K) (0 when no prior)."""
    if not stats or not prior or k_games <= 0:
        return stats, {t: 0.0 for t in (stats or {})}
    keys = list(prior.keys())
    lg_now = {k: _avg(stats, k) for k in STAT_KEYS}
    lg_pri = {k: _avg(prior, k) for k in STAT_KEYS}
    out, shares, n = {}, {}, 0
    for team, s in stats.items():
        pk = _resolve_team_name(team, keys)
        p = prior.get(pk) if pk else None
        gp = float(s.get("GP") or 0)
        if not p:
            out[team], shares[team] = dict(s), 0.0
            continue
        share = k_games / (gp + k_games)
        b = dict(s)
        for k in STAT_KEYS:
            cur, pv = s.get(k), p.get(k)
            if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in (cur, pv)) \
                    or lg_now[k] is None or lg_pri[k] is None:
                continue
            b[k] = lg_now[k] + ((1 - share) * (cur - lg_now[k]) + share * regress * (pv - lg_pri[k]))
        out[team], shares[team] = b, share
        n += 1
    print(f"  [prior] Blended {n}/{len(stats)} teams with last season "
          f"(prior = {k_games:g} games, regress {regress:g})")
    return out, shares


# ---------------------------------------------------------------------------
# Injuries
# ---------------------------------------------------------------------------
def _find(name, table):
    """Exact name, then unique last-name match, in a {name: ...} table."""
    if name in table:
        return name
    last = _real_last_name(name)
    hits = [n for n in table if _real_last_name(n) == last]
    return hits[0] if len(hits) == 1 else None


def _wavg(players, key, mins):
    tot = w = 0.0
    for p, m in zip(players, mins):
        v = p.get(key)
        if isinstance(v, (int, float)) and math.isfinite(v) and m > 0:
            tot += m * v
            w += m
    return tot / w if w > 0 else None


def _dampen(out_players):
    """Same impact tiers as lineup_adjust.adjust_team_stats."""
    best = 0.70
    for p in out_players:
        net = p.get("netRtg")
        if net is None:
            o, d = p.get("offRtg"), p.get("defRtg")
            net = (o - d) if o is not None and d is not None else 0
        m = p["min"]
        d = 1.20 if (m >= 28 and net > 5) else 1.00 if m >= 28 else 0.85 if m >= 18 else 0.70
        best = max(best, d)
    return best


def build_rosters(team_keys, player_adv, player_mpg, player_prior, k_games=10.0):
    """{team: {name: player}} for tonight. A player's team comes from this
    season's data when it exists (ratings table, then the minutes feed), else
    last season's. Ratings blend this season's (weight gp/(gp+K)) with last
    season's; minutes prefer this season's."""
    cur_team = {}
    for name, p in (player_adv or {}).items():
        t = _resolve_team_name(p.get("team"), team_keys)
        if t:
            cur_team[name] = t
    for name, p in (player_mpg or {}).items():
        if name not in cur_team:
            t = _resolve_team_name((p or {}).get("team"), team_keys)
            if t:
                cur_team[name] = t
    rosters = {t: {} for t in team_keys}
    names = set(cur_team) | set(player_prior or {})
    for name in names:
        cur = (player_adv or {}).get(name)
        pri = (player_prior or {}).get(name)
        mpg = (player_mpg or {}).get(name) or {}
        team = cur_team.get(name) or _resolve_team_name((pri or {}).get("team"), team_keys)
        if not team or not (cur or pri):
            continue
        pri_team = _resolve_team_name((pri or {}).get("team"), team_keys) if pri else None
        p = {"name": name, "with_team_last_season": bool(pri) and pri_team == team,
             "known_this_season": name in cur_team}
        gp = float((cur or {}).get("gp") or mpg.get("gp") or 0)
        w = gp / (gp + k_games) if cur else 0.0
        for k in ("offRtg", "defRtg", "netRtg", "tsPct", "tovPct", "orbPct"):
            a, b = (cur or {}).get(k), (pri or {}).get(k)
            ok_a = isinstance(a, (int, float)) and math.isfinite(a)
            ok_b = isinstance(b, (int, float)) and math.isfinite(b)
            p[k] = (w * a + (1 - w) * b) if (ok_a and ok_b) else (a if ok_a else (b if ok_b else None))
        m = (cur or {}).get("min") or mpg.get("mpg") or (pri or {}).get("min") or 0
        p["min"] = float(m)
        if p["min"] >= 5 and p.get("offRtg") is not None and p.get("defRtg") is not None:
            rosters[team][name] = p
    # A team whose current players are known (8+ from this season's data) drops
    # the last-season-only names that are not on tonight's report: they have
    # most likely left. The report adds them back below when they are listed.
    for t, r in rosters.items():
        known = [n for n, p in r.items() if p["known_this_season"]]
        if len(known) >= 8:
            for n in [n for n, p in r.items() if not p["known_this_season"]]:
                r[n]["_maybe_gone"] = True
    return rosters


def adjust_team_stats_prior(team_stats, injury_report, player_mpg, player_adv, todays_games,
                            player_prior=None, prior_share=None, recent_injury_dates=None,
                            k_games=10.0):
    """Lineup-adjusted team stats, usable from game 1. Returns (adjusted, notes)."""
    adjusted = dict(team_stats)
    notes = {}
    team_keys = list(team_stats.keys())
    if not (player_adv or player_prior):
        return adjusted, notes
    tonight = set()
    for g in todays_games or []:
        for side in ("away", "home"):
            t = _resolve_team_name(g.get(side), team_keys)
            if t:
                tonight.add(t)
    rosters = build_rosters(team_keys, player_adv, player_mpg, player_prior, k_games)
    n_adj = 0
    for team in team_keys:
        if team not in tonight:
            continue
        inj_key = _resolve_team_name(team, list((injury_report or {}).keys()))
        injuries = (injury_report or {}).get(inj_key, []) if inj_key else []
        out_names = [i["player"] for i in injuries if i.get("status") in ("out", "doubtful") and i.get("player")]
        if not out_names:
            continue
        roster = dict(rosters.get(team) or {})
        share = float((prior_share or {}).get(team, 0.0))       # prior's weight in this team's stats
        factors = {}
        for out_name in out_names:
            rn = _find(out_name, roster)
            if not rn:
                # on the report for this team but filed under another team (or
                # nowhere) in the tables: use last season's line if there is one
                pn = _find(out_name, player_prior or {})
                if not pn:
                    continue
                pp = dict((player_prior or {})[pn])
                pri_team = _resolve_team_name(pp.get("team"), team_keys)
                roster[pn] = {"name": pn, **pp, "min": float(pp.get("min") or 0),
                              "with_team_last_season": pri_team == team, "known_this_season": False}
                rn = pn
            p = roster[rn]
            p.pop("_maybe_gone", None)
            # long-term: out/doubtful on 4+ of the last 10 saved reports
            dates_out = 0
            last = _real_last_name(out_name)
            for rep in (recent_injury_dates or {}).values():
                tk = _resolve_team_name(team, list(rep.keys()))
                for inj in (rep.get(tk) or []) if tk else []:
                    if inj.get("status") in ("out", "doubtful") and (
                            inj.get("player") == out_name or _real_last_name(inj.get("player", "")) == last):
                        dates_out += 1
                        break
            long_term = bool(recent_injury_dates) and len(recent_injury_dates) >= 5 and dates_out >= 4
            was_here = p.get("with_team_last_season")
            if long_term:
                f = share if was_here else 0.0
            else:
                f = 1.0 if was_here else (1.0 - share)
            if f > 0.01:
                factors[rn] = f
        # last-season-only names not on the report, on a team whose current
        # players are known: assume they left
        players = [p for p in roster.values() if not p.get("_maybe_gone")]
        if not factors or len(players) < 6:
            continue
        full_min = [p["min"] for p in players]
        avail_min = [p["min"] * (1.0 - factors.get(p["name"], 0.0)) for p in players]
        if sum(1 for m in avail_min if m > 0) < 5 or sum(full_min) <= 0:
            continue
        out_list = [p for p in players if p["name"] in factors]
        damp = _dampen(out_list)
        orig = adjusted.get(team, team_stats[team])
        adj = dict(orig)
        changed = False
        # TO is left alone: the player turnover figure is on a different scale
        # from the team's (the stock adjustment moves team TO by whole points),
        # and last season's file has none. The projection does not read TO.
        for stat, key, nd in (("OFF", "offRtg", 2), ("DEF", "defRtg", 2), ("TS", "tsPct", 4),
                              ("ORR", "orbPct", 4)):
            full, avail = _wavg(players, key, full_min), _wavg(players, key, avail_min)
            if full is None or avail is None or orig.get(stat) is None:
                continue
            delta = (avail - full) * damp
            # player TS/ORB are fractions like the team's; ratings are per 100
            adj[stat] = round(orig[stat] + delta, nd)
            if stat in ("OFF", "DEF") and abs(delta) > 0.005:
                changed = True
        if changed:
            adjusted[team] = adj
            n_adj += 1
            notes[team] = [{"player": p["name"], "min": round(p["min"], 1),
                            "share": round(factors[p["name"]], 2)} for p in out_list]
    print(f"  [prior-lineup] Adjusted {n_adj} team(s) for absences (rosters from this season + last)")
    return adjusted, notes

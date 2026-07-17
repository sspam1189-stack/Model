# MLBstrikeouts/scripts/fade_ml_common.py
# Shared logic for the fade-list betting model. Turns fade starts + odds +
# results into graded bets, and writes the dashboard JSON.
#
# Bet types (per fade game = a game with >=1 fade-list starter):
#   ml      - single fade arm: bet the OPPONENT's moneyline.
#   ml_dog  - MUTUAL fade (both starters fade): bet per MUTUAL_FADE_RULE.
#             'underdog' (default) bets the underdog ML; 'favorite' the fav;
#             'skip' places no ML bet.
#   total   - OVER on the game total (a fade arm -> worse pitcher -> more runs).
#
# One serializer builds the record for both the live and backfill paths, so
# no field drifts between them.

import os
import json
import datetime

from fade_list import is_fade, matched_entry, _norm, FADE_LIST

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

OUTPUT_PATHS = [
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "mlb-fade-ml.json")),
    os.path.normpath(os.path.join(
        SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "mlb-fade-ml.json")),
]

# --- config ---------------------------------------------------------------
# skip | underdog | favorite. 'underdog' bets the underdog's ML on mutual
# fades (both starters fade). Weak edge / tiny sample -- tracked separately.
MUTUAL_FADE_RULE = os.environ.get("MUTUAL_FADE_RULE", "underdog")
# Compute OVER-on-total results for every fade game. These are SHADOW stats
# only (not actual bets): they are excluded from the record / units / ROI and
# shown separately so you can see how the total angle would have done.
BET_TOTALS = os.environ.get("FADE_BET_TOTALS", "1") != "0"

# Bet types that count toward the real record. Totals are shadow-only.
REAL_BET_TYPES = ("ml", "ml_dog")


# --- props loading + fade-game extraction ---------------------------------

def load_props_index(props_path=None):
    """Return {date_str: [rows]} from mlb-props.json props[] (fade trigger)."""
    if props_path is None:
        props_path = os.path.normpath(
            os.path.join(SCRIPT_DIR, "..", "data", "mlb-props.json"))
    with open(props_path, "r") as f:
        data = json.load(f)
    index = {}
    for r in data.get("props", []):
        index.setdefault(r.get("date"), []).append(r)
    return index


def starts_from_rows(rows):
    """From prop/projection rows -> list of {pitcher, team, opp} starts."""
    seen, out = set(), []
    for r in rows:
        pitcher, team, opp = r.get("player"), r.get("team"), r.get("opp")
        if not (pitcher and team and opp):
            continue
        if (r.get("market") or "strikeouts") != "strikeouts":
            continue
        k = (pitcher, team, opp)
        if k in seen:
            continue
        seen.add(k)
        out.append({"pitcher": pitcher, "team": team, "opp": opp})
    return out


def fade_games(starts):
    """Return one entry per game that has >=1 fade-list starter.

    Each entry: {teams(frozenset), mutual(bool), pitchers[list], fadeTeam,
    betTeam, oppPitcher}. For mutual games fadeTeam/betTeam are None (the ML
    side is decided later from the odds).
    """
    starter = {s["team"]: s["pitcher"] for s in starts}
    seen, out = set(), []
    for s in starts:
        if not is_fade(s["pitcher"]):
            continue
        teams = frozenset((s["team"], s["opp"]))
        if teams in seen:
            continue
        seen.add(teams)
        opp_pitcher = starter.get(s["opp"])
        mutual = bool(opp_pitcher and is_fade(opp_pitcher))
        if mutual:
            out.append({"teams": teams, "mutual": True,
                        "pitchers": [s["pitcher"], opp_pitcher],
                        "fadeTeam": None, "betTeam": None,
                        "oppPitcher": None})
        else:
            out.append({"teams": teams, "mutual": False,
                        "pitchers": [s["pitcher"]],
                        "fadeTeam": s["team"], "betTeam": s["opp"],
                        "oppPitcher": opp_pitcher})
    return out


def _name_match(a, b):
    ta, tb = set(_norm(a).split()), set(_norm(b).split())
    return bool(ta and tb and (ta.issubset(tb) or tb.issubset(ta)))


def match_game(games, teams, pitcher=None):
    """Pick the schedule game for a matchup; disambiguate doubleheaders."""
    cands = [g for g in games if {g.get("home"), g.get("away")} == set(teams)]
    if not cands:
        return None
    if len(cands) == 1 or not pitcher:
        return cands[0]
    for g in cands:
        for side in ("home_pitcher", "away_pitcher"):
            if g.get(side) and _name_match(pitcher, g[side]):
                return g
    return cands[0]


def odds_row_for(odds_rows, g):
    """Return the cached odds row for schedule game g, or None."""
    for r in odds_rows or []:
        if r.get("home") == g.get("home") and r.get("away") == g.get("away"):
            return r
    return None


# --- staking + settlement -------------------------------------------------

def stake_for(odds):
    """Neg odds -> risk-to-win-1u; pos odds -> risk-1u."""
    if odds is None:
        return None
    return abs(odds) / 100.0 if odds < 0 else 1.0


def profit_for(odds, won):
    if won:
        return 1.0 if odds < 0 else odds / 100.0
    return -stake_for(odds)


def _settle_ml(g, team):
    if g is None or g.get("void"):
        return ("VOID", "postponed")
    if g.get("home_win") is None:
        return ("pending", None)
    won = g["home_win"] if team == g["home"] else not g["home_win"]
    return (("WIN" if won else "LOSS"), None)


def _settle_total(g, line, side="OVER"):
    if g is None or g.get("void"):
        return ("VOID", "postponed")
    hs, as_ = g.get("home_score"), g.get("away_score")
    if not g.get("final") or hs is None or as_ is None:
        return ("pending", None)
    total = hs + as_
    if total == line:
        return ("VOID", "push")
    over = total > line
    won = over if side == "OVER" else not over
    return (("WIN" if won else "LOSS"), None)


# --- bet construction (single serializer) ---------------------------------

def _bet(date, commence, bet_type, fg, market, selection, line, odds,
         result, reason, book="fanduel", source=None):
    settled = result in ("WIN", "LOSS")
    stake = stake_for(odds)
    profit = profit_for(odds, result == "WIN") if settled else 0.0
    return {
        "date": date, "commence": commence, "betType": bet_type,
        "shadow": bet_type not in REAL_BET_TYPES,  # totals = shadow stat only
        "pitchers": fg.get("pitchers", []),
        "fadeTeam": fg.get("fadeTeam"),
        "market": market, "selection": selection, "line": line,
        "odds": odds, "stake": round(stake, 4) if stake is not None else None,
        "result": result, "profit": round(profit, 4),
        "book": book, "source": source, "reason": reason,
    }


def build_bets_for_game(date, fg, g, odds_row):
    """Return the list of bet records for one fade game (ML + total)."""
    commence = g.get("commence") if g else None
    source = (odds_row or {}).get("source")
    bets = []

    # ----- moneyline leg -----
    if fg["mutual"]:
        if MUTUAL_FADE_RULE == "skip":
            bets.append(_bet(date, commence, "ml_dog", fg, "h2h", None, None,
                             None, "SKIP", "mutual_skip", source=source))
        elif odds_row and odds_row.get("home_ml") is not None:
            home, away = g["home"], g["away"]
            mls = {home: odds_row["home_ml"], away: odds_row["away_ml"]}
            if MUTUAL_FADE_RULE == "favorite":
                team = min(mls, key=lambda t: mls[t])
            else:  # underdog (default)
                team = max(mls, key=lambda t: mls[t])
            result, reason = _settle_ml(g, team)
            bets.append(_bet(date, commence, "ml_dog", fg, "h2h", team, None,
                             mls[team], result, reason, source=source))
        else:
            bets.append(_bet(date, commence, "ml_dog", fg, "h2h", None, None,
                             None, "VOID", "no_price", source=source))
    else:
        team = fg["betTeam"]
        odds = None
        if odds_row:
            odds = odds_row["home_ml"] if team == g.get("home") else odds_row["away_ml"]
        if odds is None:
            bets.append(_bet(date, commence, "ml", fg, "h2h", team, None,
                             None, "VOID", "no_price", source=source))
        else:
            result, reason = _settle_ml(g, team)
            bets.append(_bet(date, commence, "ml", fg, "h2h", team, None,
                             odds, result, reason, source=source))

    # ----- total (OVER) leg -----
    if BET_TOTALS:
        line = (odds_row or {}).get("total_line")
        over = (odds_row or {}).get("over_ml")
        if line is None or over is None:
            bets.append(_bet(date, commence, "total", fg, "totals", "OVER",
                             line, over, "VOID", "no_price", source=source))
        else:
            result, reason = _settle_total(g, line, "OVER")
            bets.append(_bet(date, commence, "total", fg, "totals", "OVER",
                             line, over, result, reason, source=source))
    return bets


# --- summary + output -----------------------------------------------------

def _record(bets):
    wins = sum(1 for b in bets if b["result"] == "WIN")
    losses = sum(1 for b in bets if b["result"] == "LOSS")
    voids = sum(1 for b in bets if b["result"] == "VOID")
    skips = sum(1 for b in bets if b["result"] == "SKIP")
    staked = sum(b["stake"] for b in bets
                 if b["result"] in ("WIN", "LOSS") and b.get("stake"))
    units = sum(b["profit"] for b in bets if b["result"] in ("WIN", "LOSS"))
    roi = (units / staked) if staked else 0.0
    return {"wins": wins, "losses": losses, "voids": voids, "skips": skips,
            "staked": round(staked, 3), "units": round(units, 3),
            "roi": round(roi, 4)}


def compute_summary(bets):
    graded = [b for b in bets if b["result"] in ("WIN", "LOSS", "VOID", "SKIP")]
    # Overall record counts REAL bets only (totals are shadow stats).
    real = [b for b in graded if b["betType"] in REAL_BET_TYPES]
    summary = _record(real)
    summary["byType"] = {
        t: _record([b for b in graded if b["betType"] == t])
        for t in ("ml", "ml_dog", "total")
    }
    return summary


def build_payload(bets, today, generated=None):
    bets = sorted(bets, key=lambda b: (b["date"], b.get("commence") or "",
                                       b.get("betType") or ""))
    return {
        "sport": "MLB", "type": "fade-ml",
        "generated": generated or datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fadeList": list(FADE_LIST),
        "mutualRule": MUTUAL_FADE_RULE,
        "betTotals": BET_TOTALS,
        "summary": compute_summary(bets),
        "today": today,
        "bets": bets,
    }


def write_outputs(payload):
    for path in OUTPUT_PATHS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)
    return OUTPUT_PATHS


def load_existing():
    for path in OUTPUT_PATHS:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return json.load(f)
            except Exception:
                continue
    return None

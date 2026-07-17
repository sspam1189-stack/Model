# MLBstrikeouts/scripts/fade_ml_common.py
# Shared logic for the fade-list moneyline model: turn fade starts + odds +
# results into graded bets, and write the dashboard JSON.
#
# Used by both run_daily_ml.py (live) and ml_backfill.py (history) so the
# bet record is built by ONE serializer -- no field-whitelist drift between
# the two paths.

import os
import json
import datetime

from fade_list import is_fade, matched_entry, _norm

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

OUTPUT_PATHS = [
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "mlb-fade-ml.json")),
    os.path.normpath(os.path.join(
        SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "mlb-fade-ml.json")),
]


# --- fade-start extraction ------------------------------------------------

def starts_from_rows(rows):
    """From prop/projection rows -> list of {pitcher, team, opp} starts.

    Deduped on (pitcher, team, opp). ``rows`` may be props[], todayProjections,
    or todayProbables entries (all expose player/team/opp).
    """
    seen = set()
    out = []
    for r in rows:
        pitcher = r.get("player")
        team = r.get("team")
        opp = r.get("opp")
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


def fade_plays(starts):
    """Given all of a date's starts, return the fade plays to bet.

    A play = {pitcher, fade_team, bet_team, skipped, skip_reason}. When both
    starters of a game are fade-listed, the play is marked skipped (confirmed
    mutual fade). Only confirmed opposing fade arms trigger a skip; an unknown
    opposing starter still yields a bet.
    """
    # Index opposing starter by (team, opp) for mutual-fade lookup.
    by_matchup = {}
    for s in starts:
        by_matchup[(s["team"], s["opp"])] = s["pitcher"]

    plays = []
    for s in starts:
        if not is_fade(s["pitcher"]):
            continue
        opp_pitcher = by_matchup.get((s["opp"], s["team"]))
        if opp_pitcher and is_fade(opp_pitcher):
            plays.append({
                "pitcher": s["pitcher"], "fade_team": s["team"],
                "bet_team": s["opp"], "skipped": True,
                "skip_reason": "mutual_fade", "opp_pitcher": opp_pitcher,
            })
            continue
        plays.append({
            "pitcher": s["pitcher"], "fade_team": s["team"],
            "bet_team": s["opp"], "skipped": False, "skip_reason": None,
            "opp_pitcher": opp_pitcher,
        })
    return plays


# --- props loading + game matching ----------------------------------------

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


def _name_match(a, b):
    """True if one player name's tokens are a subset of the other's."""
    ta, tb = set(_norm(a).split()), set(_norm(b).split())
    if not ta or not tb:
        return False
    return ta.issubset(tb) or tb.issubset(ta)


def match_game(games, fade_team, bet_team, pitcher=None):
    """Pick the schedule game for a fade play; disambiguate doubleheaders.

    Matches on the {home, away} pair. When two games share the matchup
    (doubleheader), prefer the one whose fade-side probable pitcher matches
    ``pitcher``; else return the first.
    """
    cands = [g for g in games
             if {g.get("home"), g.get("away")} == {fade_team, bet_team}]
    if not cands:
        return None
    if len(cands) == 1 or not pitcher:
        return cands[0]
    for g in cands:
        fade_side = g["home_pitcher"] if g.get("home") == fade_team else g["away_pitcher"]
        if fade_side and _name_match(pitcher, fade_side):
            return g
    return cands[0]


def odds_for_bet(odds_rows, game, bet_team):
    """From cached odds rows, return (odds, source, book) for the bet team."""
    for r in odds_rows or []:
        if r.get("home") == game.get("home") and r.get("away") == game.get("away"):
            odds = r.get("home_ml") if bet_team == r.get("home") else r.get("away_ml")
            if odds is not None:
                return odds, r.get("source"), r.get("book", "fanduel")
    return None, None, "fanduel"


# --- staking + grading ----------------------------------------------------

def stake_for(odds):
    """House convention: neg odds -> risk-to-win-1u; pos odds -> risk-1u."""
    if odds is None:
        return None
    return abs(odds) / 100.0 if odds < 0 else 1.0


def profit_for(odds, won):
    """Signed profit in units for a settled bet at American ``odds``.

    won=True -> win; won=False -> loss.
    """
    stake = stake_for(odds)
    if won:
        return 1.0 if odds < 0 else odds / 100.0
    return -stake


# --- serialization --------------------------------------------------------

def serialize_bet(date, play, odds, result, commence=None, book="fanduel",
                  source=None):
    """Build one bet record. Single source of truth for the schema.

    result: "WIN" | "LOSS" | "VOID" | "pending". reason set for VOIDs.
    """
    won = result == "WIN"
    settled = result in ("WIN", "LOSS")
    stake = stake_for(odds)
    profit = profit_for(odds, won) if settled else 0.0
    return {
        "date": date,
        "commence": commence,
        "pitcher": play["pitcher"],
        "fadeEntry": matched_entry(play["pitcher"]),
        "fadeTeam": play["fade_team"],
        "betTeam": play["bet_team"],
        "oppPitcher": play.get("opp_pitcher"),
        "odds": odds,
        "stake": round(stake, 4) if stake is not None else None,
        "result": result,
        "profit": round(profit, 4),
        "book": book,
        "source": source,
        "reason": play.get("skip_reason") if not settled else None,
    }


def compute_summary(bets):
    wins = sum(1 for b in bets if b["result"] == "WIN")
    losses = sum(1 for b in bets if b["result"] == "LOSS")
    voids = sum(1 for b in bets if b["result"] == "VOID")
    staked = sum(b["stake"] for b in bets
                 if b["result"] in ("WIN", "LOSS") and b.get("stake"))
    units = sum(b["profit"] for b in bets if b["result"] in ("WIN", "LOSS"))
    roi = (units / staked) if staked else 0.0
    return {
        "wins": wins, "losses": losses, "voids": voids,
        "staked": round(staked, 3), "units": round(units, 3),
        "roi": round(roi, 4),
    }


# --- output ---------------------------------------------------------------

from fade_list import FADE_LIST


def build_payload(bets, today, generated=None):
    bets = sorted(bets, key=lambda b: (b["date"], b.get("commence") or ""))
    return {
        "sport": "MLB",
        "type": "fade-ml",
        "generated": generated or datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fadeList": list(FADE_LIST),
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

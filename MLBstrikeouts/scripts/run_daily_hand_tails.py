#!/usr/bin/env python3
"""
MLBstrikeouts/scripts/run_daily_hand_tails.py
LIVE handedness-conditional fade/take tracker (own ledger).

For every date, finds hand-tails arms (see hand_tails.py) starting against a
lineup with >= HAND_MIN opposite-hand bats, and records a fade (bet opponent)
or take (bet own team) moneyline bet. Grades from the FanDuel ML cache +
results, writes mlb-hand-tails.json (model + dashboard copies).

Reads the ML odds cache only -- run after run_daily_ml so today's prices are
cached. Lineup handedness comes from the K-model's batting_orders + bat_sides
caches (populated by run_daily earlier in the workflow).

Usage:
    cd MLBstrikeouts
    python -m scripts.run_daily_hand_tails
"""
import sys
import os
import json
import datetime

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "sources"))

from fade_ml_common import (stake_for, profit_for, _settle_ml,
                            odds_row_for, load_props_index, SCRIPT_DIR)
from hand_tails import tail_entry, opp_lineup_state, qualifies, HAND_MIN, HAND_TAILS
from sources.mlb_schedule import fetch_schedule
from sources.odds_ml_theoddsapi import load_ml_cache

OUTPUT_PATHS = [
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "mlb-hand-tails.json")),
    os.path.normpath(os.path.join(
        SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "mlb-hand-tails.json")),
]


def _bet_for(date_iso, g, side, entry, hand, action, od, lefty, righty, confirmed):
    """Build one hand-tails bet record for a qualifying start, or None.

    A not-yet-played game whose opponent lineup is only a projected/default card
    (confirmed=False) is emitted as an UNCONFIRMED candidate (result
    "unconfirmed", odds withheld) rather than a placed bet -- projected counts
    can shift, so no pick is made until the real lineup posts. Completed games
    (settled) are always graded normally; their lineups are the boxscore truth.
    """
    home, away = g.get("home"), g.get("away")
    arm_team = home if side == "home" else away
    opp_team = away if side == "home" else home
    if action == "fade":
        team = opp_team
    else:  # take
        team = arm_team
    odds = od["home_ml"] if team == home else od["away_ml"]
    if odds is None:
        return None
    result, reason = _settle_ml(g, team)
    settled = result in ("WIN", "LOSS")
    # Confirmation gate: withhold the pick (and its odds) until the lineup posts.
    if result == "pending" and not confirmed:
        result = "unconfirmed"
        odds = None
    return {
        "date": date_iso, "commence": g.get("commence"),
        "betType": "hand_" + action, "action": action,
        "pitcher": (g.get(side + "_pitcher")), "entry": entry, "hand": hand,
        "arm_team": arm_team, "opp_team": opp_team,
        "selection": team, "home": home, "away": away,
        "oppLefty": lefty, "oppRighty": righty,
        "lineupConfirmed": confirmed,
        "odds": odds,
        "stake": round(stake_for(odds), 4) if odds is not None else None,
        "result": result, "reason": reason,
        "profit": round(profit_for(odds, result == "WIN"), 4) if settled else 0.0,
        "book": "fanduel", "source": (od or {}).get("source"),
    }


def grade_date(date_key, date_iso):
    games = fetch_schedule(date_key)
    odds_rows = load_ml_cache(date_key) or []
    out = []
    for g in games:
        for side in ("home", "away"):
            p = g.get(side + "_pitcher")
            if not p:
                continue
            entry, hand, action = tail_entry(p)
            if not entry:
                continue
            opp = g.get("away") if side == "home" else g.get("home")
            lefty, righty, confirmed = opp_lineup_state(opp, date_iso)
            if not qualifies(hand, lefty, righty):
                continue
            od = odds_row_for(odds_rows, g)
            if not od or od.get("home_ml") is None:
                continue
            b = _bet_for(date_iso, g, side, entry, hand, action, od,
                         lefty, righty, confirmed)
            if b:
                out.append(b)
    return out


def _record(bets):
    graded = [b for b in bets if b["result"] in ("WIN", "LOSS")]
    w = sum(1 for b in graded if b["result"] == "WIN")
    u = sum(b["profit"] for b in graded)
    sk = sum(b["stake"] for b in graded if b.get("stake"))
    return {"wins": w, "losses": len(graded) - w,
            "units": round(u, 3), "staked": round(sk, 3),
            "roi": round(u / sk, 4) if sk else 0.0}


def main():
    props_index = load_props_index()
    dates = sorted(d for d in props_index if d)
    today_iso = datetime.date.today().isoformat()

    bets, today = [], []
    for d in dates:
        for b in grade_date(d.replace("-", ""), d):
            # "pending" (confirmed) and "unconfirmed" both belong to today's
            # display; only settled WIN/LOSS rows enter the graded ledger.
            (today if b["result"] in ("pending", "unconfirmed") else bets).append(b)

    summary = _record(bets)
    summary["byAction"] = {a: _record([b for b in bets if b["action"] == a])
                           for a in ("fade", "take")}
    summary["byHand"] = {h: _record([b for b in bets if b["hand"] == h])
                         for h in ("R", "L")}

    payload = {
        "sport": "MLB", "type": "hand-tails",
        "generated": datetime.datetime.now(datetime.timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "handMin": HAND_MIN,
        "roster": {e: {"hand": v[0], "action": v[1]} for e, v in HAND_TAILS.items()},
        "note": "In-sample selection; live paper-forward test. Not part of the "
                "fade-list ledger. On Nola/Lopez overlap games, hand-tails wins.",
        "summary": summary,
        "today": sorted(today, key=lambda b: b.get("commence") or ""),
        "bets": sorted(bets, key=lambda b: (b["date"], b.get("commence") or "")),
    }
    for path in OUTPUT_PATHS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)
    s = summary
    n_conf = sum(1 for b in today if b["result"] == "pending")
    n_unconf = sum(1 for b in today if b["result"] == "unconfirmed")
    print(f"[hand-tails] {s['wins']}-{s['losses']} {s['units']:+.2f}u "
          f"ROI {s['roi']*100:+.1f}% | fade {s['byAction']['fade']['wins']}-"
          f"{s['byAction']['fade']['losses']} take {s['byAction']['take']['wins']}-"
          f"{s['byAction']['take']['losses']} | {n_conf} confirmed pending, "
          f"{n_unconf} unconfirmed")


if __name__ == "__main__":
    main()

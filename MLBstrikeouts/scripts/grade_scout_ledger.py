#!/usr/bin/env python3
"""
grade_scout_ledger.py — settle pending scout-ledger entries from the finals.

The daily run logs qualifiers (log_daily_qualifiers.py) but nothing settled
them: grading stayed a hand command, which is why the 8/30 card sat open for
two days until somebody asked what the record was. A ledger that logs itself
and never grades itself is worse than one that does neither -- it looks
complete while every rule's W-L is stuck at the last day someone remembered.

Reads finals from mlb-all-ml.json and grades every `pending` entry whose game
has one. Profit uses the entry's OWN stored price at flat 1u, so a line that
moved after the play was logged cannot regrade it.

Rules it applies, all of them boring on purpose:

  totals   under wins when the game total is below the line, over when above;
           exactly on the line is a PUSH (profit 0). The line used is the
           entry's `line`, never the file's current total_line -- those differ
           whenever the number moved after the play was carded.
  h2h      the entry names its pick as the first token of `play` ("SF ML ...",
           "CHC ML (fav, ...)"); it wins when that team won.

Entries marked `not_bet` still grade -- they stay in their rule's W-L and are
held out of the units by the report, which is the whole point of the flag.

Idempotent: only `pending` rows are touched, so re-running does nothing.

Usage:  cd MLBstrikeouts && python -m scripts.grade_scout_ledger [--dry-run]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import scout_card_log as LEDGER

HERE = os.path.dirname(os.path.abspath(__file__))
ALLML = os.path.normpath(os.path.join(HERE, "..", "data", "mlb-all-ml.json"))


def finals_by_id():
    """gamePk -> settled game. The exact join, used whenever an entry has one."""
    out = {}
    for g in json.load(open(ALLML, encoding="utf-8")).get("games", []):
        if g.get("gamePk") is not None and g.get("away_score") is not None \
                and g.get("home_score") is not None:
            out[g["gamePk"]] = g
    return out


def finals():
    """(date, away, home) -> LIST of settled games. LEGACY fallback.

    A list, not a single game, because doubleheaders share all three key
    parts: 2026-08-29 ARI @ SF was ARI 7-1 and then SF 7-2. Keying on one
    game silently graded a mismatch-ML entry against the wrong half of it
    (a real LOSS came back WIN), which is exactly the kind of quiet error an
    auto-grader must not make. When a key holds more than one game the caller
    disambiguates by price, and leaves the entry pending if it cannot.
    """
    out = {}
    for g in json.load(open(ALLML, encoding="utf-8")).get("games", []):
        if g.get("away_score") is None or g.get("home_score") is None:
            continue
        out.setdefault((g["date"], g["away"], g["home"]), []).append(g)
    return out


def _pick_game(games, entry):
    """The one game this entry belongs to, or None if it stays ambiguous."""
    if len(games) == 1:
        return games[0]
    price, line = entry.get("price"), entry.get("line")
    if entry.get("market") == "totals":
        hits = [g for g in games
                if g.get("total_line") == line
                and price in (g.get("over_ml"), g.get("under_ml"))]
    else:
        hits = [g for g in games
                if price in (g.get("away_ml"), g.get("home_ml"))]
    return hits[0] if len(hits) == 1 else None


def _teams(entry):
    """(away, home) from the entry's `game` field, in either convention."""
    game = (entry.get("game") or "").replace("/", " @ ")
    parts = [p.strip() for p in game.split("@")]
    return (parts[0], parts[1]) if len(parts) == 2 else (None, None)


def grade_entry(entry, scores, by_id=None):
    """(result, profit) or None when it cannot be graded yet.

    Prefers the gamePk join -- exact, and immune to the doubleheader problem
    that team names have. Rows logged before gamePk was stamped fall back to
    (date, away, home) plus the price disambiguation below.
    """
    game = None
    gid = entry.get("gamePk")
    if gid is not None and by_id:
        game = by_id.get(gid)
        if game is None:
            return None        # known id, no final yet
    if game is None:
        away, home = _teams(entry)
        if not away or not home:
            return None
        games = scores.get((entry.get("date"), away, home))
        if not games:
            return None
        game = _pick_game(games, entry)
        if game is None:
            return None        # doubleheader we cannot pin down; stays pending
    # Teams come from the matched GAME, not the entry text, so the h2h check
    # below is right even when the ledger's `game` field is missing or odd.
    away, home = game["away"], game["home"]
    a, h = game["away_score"], game["home_score"]
    price = entry.get("price")
    stake = entry.get("stake", 1.0)

    if entry.get("market") == "totals":
        line = entry.get("line")
        if line is None or price is None:
            return None
        total = a + h
        if total == line:
            return "PUSH", 0.0
        play = (entry.get("play") or "").lower()
        # "SD/CIN O9" / "CWS/HOU Under 8.5" -- both conventions appear.
        is_under = (" u" in f" {play}") or ("under" in play)
        won = (total < line) if is_under else (total > line)
        return ("WIN" if won else "LOSS",
                LEDGER.profit_for(price, stake, "WIN" if won else "LOSS"))

    if entry.get("market") == "h2h":
        pick = (entry.get("play") or "").split()[0]
        if pick not in (away, home) or price is None:
            return None
        won = (h > a) if pick == home else (a > h)
        return ("WIN" if won else "LOSS",
                LEDGER.profit_for(price, stake, "WIN" if won else "LOSS"))
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    scores, by_id = finals(), finals_by_id()
    blob = LEDGER._load()
    pending = [e for e in blob["entries"] if e.get("result") == "pending"]
    done, skipped = [], 0
    for e in pending:
        got = grade_entry(e, scores, by_id)
        if got is None:
            skipped += 1
            continue
        result, profit = got
        if not args.dry_run:
            e["result"] = result
            e["profit"] = round(profit, 2)
        done.append((e, result, profit))

    for e, result, profit in done:
        tag = "nb" if e.get("not_bet") else ("sh" if e.get("shadow") else "  ")
        print(f"  {tag} {e['date']} {e.get('rule',''):16} "
              f"{(e.get('play') or '')[:38]:38} -> {result:5} {profit:+.2f}u")
    print(f"graded {len(done)} of {len(pending)} pending "
          f"({skipped} awaiting finals)")
    if done and not args.dry_run:
        LEDGER._save(blob)
    elif args.dry_run:
        print("(dry run -- nothing written)")


if __name__ == "__main__":
    main()

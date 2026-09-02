#!/usr/bin/env python3
"""
backfill_allml_systems.py — write the non-scout systems' season into the ledger.

The systems were carded 2026-09-01, so the ledger holds them only from that
date. Their season record existed but lived in allml-systems-table.json as a
REPLAY -- a calculation, not logged rows -- which is why the tab's date filter
showed "no plays" for every earlier day while the season table underneath it
said 628.

This writes those 628 as ledger rows so both surfaces answer from the same
place. Every row is marked ``backfilled`` and carries no units, exactly as the
99 mismatch-ML rows written the same way do: the ledger report keeps
backfilled plays out of the record entirely, because counting profit on a bet
nobody placed reports money that was never risked. They are hindsight, and the
ledger says so on every row.

Idempotent on the logger's own structural key (date, rule, market, gamePk,
line-or-pick), so re-running adds nothing and never rewrites a price. It will
not touch a row that already exists, which means today's live entries are
safe: the walk stops before the current slate.

Usage:  cd MLBstrikeouts && python -m scripts.backfill_allml_systems [--dry-run]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import allml_systems as SYS
import scout_card_log as LEDGER
from log_daily_qualifiers import _short, _num, sig_of


def rows_for_season():
    """Every historical play the carded systems would have made, graded."""
    blob = SYS.load()
    asof = SYS.AsOf()
    out = []
    for g in SYS._settled(blob):
        matchup = f"{g['away']} @ {g['home']}"
        for play in SYS.plays_for(g, asof.features(g)):
            total = g["total_line"]
            name = SYS.SYSTEMS[play["rule"]][0]
            entry = {
                "date": g["date"],
                "market": play["market"],
                "game": matchup,
                "price": int(play["price"]),
                "stake": 1.0,
                "rule": play["rule"],
                "gamePk": g.get("gamePk"),
                "commence": g.get("commence"),
                "basis": f"{name}: {play.get('why', '')}".strip(),
                "auto": True,
                "backfilled": True,
                "non_scout": True,
            }
            if play["market"] == "totals":
                entry["line"] = total
                entry["play"] = (f"{_short(matchup)} "
                                 f"{'U' if play['pick'] == 'under' else 'O'}"
                                 f"{_num(total)}")
            else:
                entry["play"] = (f"{play['pick']} ML "
                                 f"({SYS.short_tag(play['rule'])})")
            got = SYS._graded(play, g)
            if got is None:                       # total landed on the line
                entry["result"], entry["profit"] = "PUSH", 0.0
            else:
                won, profit = got
                entry["result"] = "WIN" if won else "LOSS"
                entry["profit"] = round(profit, 2)
            out.append(entry)
        asof.absorb(g)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    blob = LEDGER._load()
    have = {sig_of(e) for e in blob["entries"]}
    added, skipped = [], 0
    for e in rows_for_season():
        if sig_of(e) in have:
            skipped += 1
            continue
        have.add(sig_of(e))
        added.append(e)

    by_rule = {}
    for e in added:
        r = by_rule.setdefault(e["rule"], {"w": 0, "l": 0, "p": 0, "u": 0.0})
        r["w" if e["result"] == "WIN" else
          "l" if e["result"] == "LOSS" else "p"] += 1
        r["u"] += e["profit"]
    for rule, r in sorted(by_rule.items()):
        print(f"  {rule:18} {r['w']:>3}-{r['l']:<3}"
              + (f"-{r['p']}" if r["p"] else "   ")
              + f" {r['u']:+8.2f}u  ({r['w'] + r['l'] + r['p']} rows)")
    print(f"backfill: {len(added)} new rows, {skipped} already present "
          f"(ledger {len(blob['entries'])} -> {len(blob['entries']) + len(added)})")

    if args.dry_run or not added:
        if args.dry_run:
            print("(dry run -- nothing written)")
        return
    blob["entries"].extend(added)
    blob["entries"].sort(key=lambda e: (e.get("date", ""), e.get("rule", "")))
    LEDGER._save(blob)
    print(f"wrote {len(added)} backfilled rows")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
mismatch_shadow.py -- shadow-log the mismatch moneyline rule.

THE RULE (user, 2026-08-29), read off the live scout payload's L20 mismatch:

    m <= -45   the arm outclasses the offense  -> TAIL him  (back his team)
    m >= +55   the offense outclasses the arm  -> FADE him  (back the opponent)

REVIVED AS SHADOW 2026-09-01 (user), which is what the gate asked for in the
first place. History: carded 2026-08-29 without the 15-20 shadow plays
MLBstrikeouts/CLAUDE.md requires, pulled 2026-08-30 after one day at 1-3.
`--log` now writes SHADOW entries (rule `shadow-mismatch-ml`, "shadow": true)
that are tracked and never bet; it cannot write card plays.

The retirement is NOT evidence the rule is dead: four plays cannot overturn
a permutation test at p=0.022, any more than four wins would have proved it.
What it settles is the process question -- the gate exists because a rule
that has not shadow-traded has no live record to size against, and this one
went straight to full stake and lost three of four. If it is ever revived,
it shadow-trades 15-20 plays first, at the +9.4% expectation below rather
than the +17.2% season figure.

WHAT THE BACKTEST ACTUALLY SAID (scripts/backtest_mismatch.py --days 20,
1,727 games, walk-forward split 2026-06-10). Recorded here so the review has
the priors and not just the results:

  tail  m <= -45   39-20  +14.3% ROI   but the edge is ALL in the -45..-50
                                       band (23-6, +40.8%); -50..-60 is
                                       -1.7% and beyond -60 is -33.5%
  fade  m >= +55   24-12  +20.2% ROI   but +55..+65 is only +1.4%; the edge
                                       is in +65 and up (12-3, +46.5%)

  walk-forward: the tail half DEGRADES (+32.4% -> +0.7%), the fade half
  IMPROVES (-5.7% -> +22.8%). No confidence interval excludes zero.

  These thresholds are also not equally rare. The ERA term is floored at
  -33.6 (a 0.00 ERA) but uncapped above (+62.4 at a 12.00 ERA), so the
  positive tail runs further: |m|>=55 is 0.6% of sides negative vs 1.3%
  positive, and |m|>=65 is 0.1% vs 0.5%. -45 sits near the 2.5th percentile
  while +55 is nearer the 1st, so the fade side is the rarer event of the
  two. A percentile-based rule would make them comparable; the raw cut is
  what was asked for and is what is tracked.

  PERMUTATION TEST (1,000 shuffles of the mismatch values across games,
  same rule re-run each time): real +17.2% vs shuffled mean -3.5%, and the
  shuffles beat it 22/1000 -> p = 0.022. This is the evidence that the
  column is doing real work rather than the thresholds cutting noise.

  Positive in every month: Apr +29.1%, May +33.8%, Jun +14.6%, Jul +13.1%,
  Aug +9.4% (66-33 overall, +17.2%, CI +0% to +34%).

  THE ARGUMENT AGAINST: that month series decays monotonically, and every
  split point confirms it (drift -20.1% at a June cut, -6.0% at a late-July
  cut). Treat +9.4% as the live expectation, not +17.2%.

Usage:
    python3 scripts/mismatch_shadow.py                 # today's qualifiers
    python3 scripts/mismatch_shadow.py --log           # record as card plays
    python3 scripts/mismatch_shadow.py --log --shadow  # record as shadow
    python3 scripts/mismatch_shadow.py backfill --log  # historical record
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scout_card_log as LEDGER  # noqa: E402

SCOUT = os.path.normpath(os.path.join(HERE, "..", "data", "mlb-slate-scout.json"))
TAIL_AT = -45.0
FADE_AT = 55.0
RULE = "shadow-mismatch-ml"


def qualifiers(payload):
    """One entry per qualifying SIDE, widest |mismatch| first."""
    out = []
    for g in payload.get("slate", []):
        for side in ("away", "home"):
            s = g["sides"].get(side) or {}
            m = s.get("mismatch")
            if m is None:
                continue
            arm_team = g[side]
            opp_team = g["home"] if side == "away" else g["away"]
            if m <= TAIL_AT:
                action, pick = "TAIL", arm_team
            elif m >= FADE_AT:
                action, pick = "FADE", opp_team
            else:
                continue
            price = g.get("home_ml") if pick == g["home"] else g.get("away_ml")
            out.append({
                "action": action, "m": m, "pitcher": s.get("pitcher"),
                "arm_team": arm_team, "pick": pick, "price": price,
                "matchup": g["matchup"],
                "opp_wrc": s.get("opp_wrc_vs_hand"),
                "l5_era": ((s.get("form") or {}).get("recent") or {}).get("era"),
            })
    out.sort(key=lambda r: -abs(r["m"]))
    return out


def basis(q):
    verb = ("arm outclasses the offense -- tail him"
            if q["action"] == "TAIL" else
            "offense outclasses the arm -- fade him")
    return (f"L20 mismatch {q['m']:+.1f} ({verb}). {q['pitcher']} L5 ERA "
            f"{q['l5_era']}, opponent wRC+ vs his hand {q['opp_wrc']}. "
            f"Carded 2026-08-29 on a permutation test (p=0.022) and five "
            f"positive months; expectation is August's +9.4%, not the "
            f"+17.2% season figure (see scripts/mismatch_shadow.py header).")


def backfill(log=False):
    """Rebuild every historical play the rule would have made, as-of date.

    Written with "backfilled": true so the ledger can report them apart from
    live bets. These were NEVER WAGERED -- folding them into the card record
    would report profit that was never risked, which is the one thing a
    ledger must not do.
    """
    sys.path.insert(0, HERE)
    import backtest_mismatch as BT

    rows = BT.build_rows(BT.MIN_WINDOW_PA, 20)
    out = []
    for r in rows:
        g = r["game"]
        for side in ("away", "home"):
            m = r["sides"][side]["m"]
            if m is None:
                continue
            if m <= TAIL_AT:
                pick = g["away"] if side == "away" else g["home"]
            elif m >= FADE_AT:
                pick = g["home"] if side == "away" else g["away"]
            else:
                continue
            ml = g["home_ml"] if pick == g["home"] else g["away_ml"]
            if not ml:
                continue
            won = ((g["home_score"] > g["away_score"]) if pick == g["home"]
                   else (g["away_score"] > g["home_score"]))
            out.append({
                "date": g["date"],
                "play": f"{pick} ML (mismatch {m:+.1f})",
                "market": "h2h", "game": f"{g['away']} @ {g['home']}",
                "price": int(ml), "stake": 1.0, "rule": RULE,
                "basis": f"Backfilled {'tail' if m < 0 else 'fade'} at L20 "
                         f"mismatch {m:+.1f}. Not wagered.",
                "result": "WIN" if won else "LOSS",
                "profit": round(LEDGER.profit_for(int(ml), 1.0,
                                                  "WIN" if won else "LOSS"), 2),
                "backfilled": True,
            })
    out.sort(key=lambda e: e["date"])
    w = sum(1 for e in out if e["result"] == "WIN")
    u = sum(e["profit"] for e in out)
    print(f"backfill: {len(out)} plays  {w}-{len(out)-w} ({w/len(out):.1%})  "
          f"{u:+.2f}u  ROI {u/len(out):+.1%}")
    if not log:
        print("(dry run -- pass --log to write them)")
        return
    blob = LEDGER._load()
    blob["entries"] = [e for e in blob["entries"] if not e.get("backfilled")]
    blob["entries"].extend(out)
    blob["entries"].sort(key=lambda e: e.get("date", ""))
    LEDGER._save(blob)
    print(f"wrote {len(out)} backfilled entries (replacing any previous backfill)")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=None,
                    help="defaults to the payload's own slate date")
    ap.add_argument("--log", action="store_true",
                    help="write today's qualifiers as SHADOW entries "
                         "(tracked, never bet)")
    ap.add_argument("mode", nargs="?", choices=("today", "backfill"),
                    default="today")
    args = ap.parse_args()

    if args.mode == "backfill":
        return backfill(args.log)

    with open(SCOUT) as fh:
        payload = json.load(fh)
    date = args.date or payload.get("date")
    if args.date and payload.get("date") != args.date:
        raise SystemExit(f"payload is for {payload.get('date')}, not {args.date}")

    qs = qualifiers(payload)
    print(f"{date}  window={payload.get('wrc_primary_window')}  "
          f"tail <= {TAIL_AT:+.0f} / fade >= {FADE_AT:+.0f}")
    if not qs:
        print("  no qualifiers")
    for q in qs:
        price = f"{q['price']:+d}" if q["price"] is not None else "no price"
        print(f"  {q['action']:4} {q['m']:+7.1f}  {q['pitcher']:20} "
              f"{q['matchup']:12} -> back {q['pick']:4} {price}")

    if not args.log:
        print("\n(dry run -- pass --log to record these as shadow entries)")
        return

    # Shadow only. This rule does not get to write a card play again until it
    # has 15-20 tracked plays, at August's +9.4% expectation rather than the
    # +17.2% season figure (see the header and CLAUDE.md).
    blob = LEDGER._load()
    have = {(e.get("date"), e.get("play")) for e in blob["entries"]}
    added = 0
    for q in qs:
        if q["price"] is None:
            continue
        entry = {
            "date": date,
            "play": f"{q['pick']} ML (mismatch {q['m']:+.1f})",
            "market": "h2h", "game": q["matchup"],
            "price": int(q["price"]), "stake": 1.0,
            "rule": RULE, "shadow": True,
            "basis": (f"{q['action']} at L20 mismatch {q['m']:+.1f} "
                      f"({q['pitcher']}). SHADOW revival 2026-09-01 after the "
                      f"8/29-8/30 card-and-pull; expectation is August's "
                      f"+9.4%, not the +17.2% season figure. Not bet."),
            "result": "pending", "profit": 0.0,
        }
        if (entry["date"], entry["play"]) in have:
            continue
        blob["entries"].append(entry)
        added += 1
    if added:
        blob["entries"].sort(key=lambda e: e.get("date", ""))
        LEDGER._save(blob)
    print(f"\nlogged {added} SHADOW entries (rule {RULE}, never bet)")
    return

    blob = LEDGER._load()
    have = {(e.get("date"), e.get("play")) for e in blob["entries"]}
    added = 0
    for q in qs:
        if q["price"] is None:
            print(f"  SKIP {q['pitcher']}: no moneyline in the payload")
            continue
        play = f"{q['pick']} ML (mismatch {q['m']:+.1f})"
        if (date, play) in have:
            print(f"  SKIP {play}: already logged")
            continue
        blob["entries"].append({
            "date": date, "play": play, "market": "h2h",
            "game": q["matchup"], "price": int(q["price"]), "stake": 1.0,
            "rule": RULE, "basis": basis(q),
            "result": "pending", "profit": 0.0,
            **({"shadow": True} if args.shadow else {}),
        })
        added += 1
        print(f"  logged {'SHADOW' if args.shadow else 'CARD'}: "
              f"{play} @ {q['price']:+d}")
    if added:
        LEDGER._save(blob)
    print(f"\n{added} entries added"
          f"{' (shadow)' if args.shadow else ''}")


if __name__ == "__main__":
    main()

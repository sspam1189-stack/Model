#!/usr/bin/env python3
"""
mismatch_shadow.py -- shadow-log the mismatch moneyline rule.

THE RULE (user, 2026-08-29), read off the live scout payload's L20 mismatch:

    m <= -45   the arm outclasses the offense  -> TAIL him  (back his team)
    m >= +55   the offense outclasses the arm  -> FADE him  (back the opponent)

Shadow only. Nothing here is a bet: entries land in scout-card-log.json with
"shadow": true and never count toward the card record. Fifteen to twenty
graded plays with closing-line value, then the rule is reviewed -- the gate in
MLBstrikeouts/CLAUDE.md ("Changing a scout rule").

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

Usage:
    python3 scripts/mismatch_shadow.py            # show today's qualifiers
    python3 scripts/mismatch_shadow.py --log      # append them as shadow
    python3 scripts/mismatch_shadow.py --date 2026-08-29 --log
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
            f"SHADOW: rule has no confidence interval excluding zero and the "
            f"band beneath this threshold carries most of the backtest edge "
            f"(see scripts/mismatch_shadow.py header).")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=None,
                    help="defaults to the payload's own slate date")
    ap.add_argument("--log", action="store_true",
                    help="append qualifiers to scout-card-log.json as shadow")
    args = ap.parse_args()

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
            "result": "pending", "profit": 0.0, "shadow": True,
        })
        added += 1
        print(f"  logged SHADOW: {play} @ {q['price']:+d}")
    if added:
        LEDGER._save(blob)
    print(f"\n{added} shadow entries added")


if __name__ == "__main__":
    main()

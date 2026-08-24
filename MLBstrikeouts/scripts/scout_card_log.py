#!/usr/bin/env python3
"""
scout_card_log.py — append to and report on the scout card ledger.

The scout card (tiers 2/3 of the daily policy in MLBstrikeouts/CLAUDE.md)
had no persistent record: calls lived in chat sessions and evaporated with
them, so "what is the scout record this week" was archaeology while fade-ml
could answer the same question from JSON going back to March. This ledger
fixes that. Model plays are NOT logged here — they keep their own files.

No-play days are first-class entries: an empty card is evidence about the
rules, and skipping them would make the record look busier than it is.
Weeks run MONDAY-SUNDAY (user convention, 2026-08-24). Prices are the
card-time quote, not the close.

Usage:
    python3 scripts/scout_card_log.py report
    python3 scripts/scout_card_log.py add --date 2026-08-24 --play "SD ML" \
        --market h2h --game "MIN @ SD" --price -150 --rule scout-ml-both-halves-aligned \
        --basis "..."
    python3 scripts/scout_card_log.py noplay --date 2026-08-24 --note "..."
    python3 scripts/scout_card_log.py grade --date 2026-08-24 --play "SD ML" \
        --result WIN
Grading computes profit from the stored American price at flat stake unless
--profit overrides it.
"""
import argparse
import datetime
import json
from pathlib import Path

LOG = Path(__file__).resolve().parents[1] / "data" / "scout-card-log.json"


def _load():
    with open(LOG) as fh:
        return json.load(fh)


def _save(blob):
    blob["generated"] = (datetime.datetime.now(datetime.timezone.utc)
                         .isoformat(timespec="seconds").replace("+00:00", "Z"))
    with open(LOG, "w") as fh:
        json.dump(blob, fh, indent=2)


def week_start(date_iso):
    """Monday of the week containing this date (Monday-Sunday convention)."""
    d = datetime.date.fromisoformat(date_iso)
    return (d - datetime.timedelta(days=d.weekday())).isoformat()


def profit_for(price, stake, result):
    if result == "LOSS":
        return -stake
    if result != "WIN":
        return 0.0
    return round(stake * (price / 100.0 if price > 0 else 100.0 / -price), 2)


def cmd_add(args, blob):
    blob["entries"].append({
        "date": args.date, "play": args.play, "market": args.market,
        "game": args.game, "price": args.price, "stake": args.stake,
        "rule": args.rule, "basis": args.basis,
        "result": "pending", "profit": 0.0,
    })
    _save(blob)
    print(f"logged: {args.date} {args.play} @ {args.price:+d}")


def cmd_noplay(args, blob):
    blob["entries"].append({"date": args.date, "no_play": True,
                            "note": args.note or ""})
    _save(blob)
    print(f"logged: {args.date} no qualifying plays")


def cmd_grade(args, blob):
    for e in blob["entries"]:
        if e.get("date") == args.date and e.get("play") == args.play:
            e["result"] = args.result
            e["profit"] = (args.profit if args.profit is not None
                           else profit_for(e["price"], e.get("stake", 1.0),
                                           args.result))
            _save(blob)
            print(f"graded: {args.date} {args.play} -> {args.result} "
                  f"({e['profit']:+.2f}u)")
            return
    raise SystemExit(f"no entry matches {args.date} / {args.play!r}")


def cmd_report(args, blob):
    weeks = {}
    for e in blob["entries"]:
        weeks.setdefault(week_start(e["date"]), []).append(e)
    print(f"Scout card record (weeks run Monday-Sunday)")
    for h in blob.get("pre_log_history", []):
        print(f"  week of {h['week_start']}  {h['wins']}-{h['losses']} "
              f"(pre-log aggregate: {h['note']})")
    for wk in sorted(weeks):
        plays = [e for e in weeks[wk] if not e.get("no_play")]
        w = sum(1 for e in plays if e.get("result") == "WIN")
        l = sum(1 for e in plays if e.get("result") == "LOSS")
        p = sum(1 for e in plays if e.get("result") == "pending")
        u = sum(e.get("profit") or 0 for e in plays)
        quiet = sum(1 for e in weeks[wk] if e.get("no_play"))
        print(f"  week of {wk}  {w}-{l}"
              + (f" ({p} pending)" if p else "")
              + f"  {u:+.2f}u  [{len(plays)} plays, {quiet} no-play days]")
        if args.verbose:
            for e in weeks[wk]:
                if e.get("no_play"):
                    print(f"      {e['date']}  — no play. {e.get('note', '')}")
                else:
                    print(f"      {e['date']}  {e['play']:22} "
                          f"{e['price']:+d}  {e.get('result', '?'):7} "
                          f"{(e.get('profit') or 0):+.2f}u")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("add", help="log a scout play (result starts pending)")
    p.add_argument("--date", required=True)
    p.add_argument("--play", required=True)
    p.add_argument("--market", required=True, choices=("h2h", "totals", "team_total"))
    p.add_argument("--game", required=True)
    p.add_argument("--price", required=True, type=int)
    p.add_argument("--stake", type=float, default=1.0)
    p.add_argument("--rule", required=True)
    p.add_argument("--basis", default="")

    p = sub.add_parser("noplay", help="log a day with no qualifying plays")
    p.add_argument("--date", required=True)
    p.add_argument("--note", default="")

    p = sub.add_parser("grade", help="set a logged play's result")
    p.add_argument("--date", required=True)
    p.add_argument("--play", required=True)
    p.add_argument("--result", required=True, choices=("WIN", "LOSS", "PUSH", "pending"))
    p.add_argument("--profit", type=float, default=None)

    p = sub.add_parser("report", help="Monday-Sunday weekly rollup")
    p.add_argument("--verbose", "-v", action="store_true")

    args = ap.parse_args()
    blob = _load()
    {"add": cmd_add, "noplay": cmd_noplay,
     "grade": cmd_grade, "report": cmd_report}[args.cmd](args, blob)


if __name__ == "__main__":
    main()

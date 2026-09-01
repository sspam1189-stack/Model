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


# Rules pulled from the card whose history no longer counts toward the week
# record (user, 2026-08-31). Entries stay in the ledger and report on their
# own line so money actually risked on them stays visible. The hold-out
# applies only from RETIRED_FROM (the week the mismatch ML was carded and
# pulled) -- earlier weeks keep their graded record as bet at the time; the
# 8/17 week's 15-7-1 stands.
RETIRED_FROM = "2026-08-24"
RETIRED_RULES = {
    # mismatch-ml is NOT here any more: revived as shadow 2026-09-01, so the
    # `shadow` flag already keeps it out of the card record while its own
    # line accumulates the 15-20 tracked plays.
    "better-arm-ml-fav",               # favorite half, out of scope after the dogs-only narrowing
    "scout-ml-both-halves-aligned",    # pre-card experiment
    "card-grade-total-both-aligned",   # pre-card experiment
    "card-grade-total-aligned-plus-flag",
}


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
    entry = {
        "date": args.date, "play": args.play, "market": args.market,
        "game": args.game, "price": args.price, "stake": args.stake,
        "rule": args.rule, "basis": args.basis,
        "result": "pending", "profit": 0.0,
    }
    if args.shadow:
        # Shadow plays are tracked but never counted in the card record --
        # a rule earns its way onto the card with 15-20 of these first
        # (see "Changing a scout rule" in MLBstrikeouts/CLAUDE.md).
        entry["shadow"] = True
    if args.line is not None:
        entry["line"] = args.line
    _save(blob)
    blob["entries"].append(entry)
    _save(blob)
    print(f"logged{' SHADOW' if args.shadow else ''}: "
          f"{args.date} {args.play} @ {args.price:+d}")


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
            if args.close_line is not None:
                # Closing-line value: did the number move toward the read?
                # A play can beat the close and still lose (both 8/25 losses
                # did) -- that is variance on a sound process, and W/L alone
                # cannot distinguish it from a broken read.
                e["close_line"] = args.close_line
                opened = e.get("line")
                if opened is not None:
                    move = round(args.close_line - opened, 1)
                    play = (e.get("play") or "").lower()
                    if "under" in play:
                        e["clv"] = "beat" if move < 0 else ("lost" if move > 0 else "flat")
                    elif "over" in play:
                        e["clv"] = "beat" if move > 0 else ("lost" if move < 0 else "flat")
                    e["line_move"] = move
            _save(blob)
            clv = f", CLV {e['clv']}" if e.get("clv") else ""
            print(f"graded: {args.date} {args.play} -> {args.result} "
                  f"({e['profit']:+.2f}u{clv})")
            return
    raise SystemExit(f"no entry matches {args.date} / {args.play!r}")


def cmd_report(args, blob):
    # One rollup per Monday-Sunday week, merging the per-play entries with
    # the pre-log daily aggregates (days before 2026-08-21, where only
    # day-level W-L-P and units survive).
    weeks = {}
    for e in blob["entries"]:
        weeks.setdefault(week_start(e["date"]), {"logged": [], "pre": []})[
            "logged"].append(e)
    for h in blob.get("pre_log_history", []):
        weeks.setdefault(week_start(h["date"]), {"logged": [], "pre": []})[
            "pre"].append(h)

    print("Scout card record (weeks run Monday-Sunday)")
    for wk in sorted(weeks):
        logged, pre = weeks[wk]["logged"], weeks[wk]["pre"]
        # Shadow plays are tracked separately and never inflate the card
        # record -- they are a rule auditioning, not a bet. Backfilled plays
        # are held out for a harder reason: they were never wagered at all,
        # so counting their profit would report money that was never risked.
        shadow = [e for e in logged if e.get("shadow") and not e.get("no_play")]
        back = [e for e in logged if e.get("backfilled")]
        # Retired rules (2026-08-31, user): the mismatch ML and the pre-card
        # experiments no longer count toward the week record -- the going
        # concern is the flagged unders (+ whatever graduates from shadow).
        # Their entries stay in the ledger and get their own line below, with
        # units shown, so real money lost on them is visible, just not mixed
        # into the record of the system that is actually running.
        retired = [e for e in logged
                   if e.get("rule") in RETIRED_RULES and not e.get("no_play")
                   and e["date"] >= RETIRED_FROM]
        logged = [e for e in logged
                  if not e.get("shadow") and not e.get("backfilled")
                  and not (e.get("rule") in RETIRED_RULES
                           and e["date"] >= RETIRED_FROM)]
        plays = [e for e in logged if not e.get("no_play")]
        # not_bet: the rule fired and still grades, but the wager was never
        # placed (missed first pitch, price gone, sizing call). Kept in the
        # rule's record; held out of the money line so units reflect what was
        # actually risked.
        missed = [e for e in plays if e.get("not_bet")]
        w = sum(h["wins"] for h in pre) + sum(
            1 for e in plays if e.get("result") == "WIN")
        l = sum(h["losses"] for h in pre) + sum(
            1 for e in plays if e.get("result") == "LOSS")
        t = sum(h.get("pushes", 0) for h in pre) + sum(
            1 for e in plays if e.get("result") == "PUSH")
        p = sum(1 for e in plays if e.get("result") == "pending")
        u = sum(h.get("units") or 0 for h in pre) + sum(
            e.get("profit") or 0 for e in plays if not e.get("not_bet"))
        quiet = sum(1 for e in logged if e.get("no_play"))
        rec = f"{w}-{l}" + (f"-{t}" if t else "")
        print(f"  week of {wk}  {rec}"
              + (f" ({p} pending)" if p else "")
              + f"  {u:+.2f}u"
              + (f"  [{len(plays)} logged plays, {quiet} no-play days"
                 + (f", {len(pre)} pre-log days" if pre else "")
                 + (f", {len(shadow)} shadow" if shadow else "") + "]"))
        clv = [e for e in plays if e.get("clv")]
        if clv:
            beat = sum(1 for e in clv if e["clv"] == "beat")
            print(f"{'':16}CLV: beat the close {beat}/{len(clv)}"
                  " (process check, independent of W/L)")
        if missed:
            mw = sum(1 for e in missed if e.get("result") == "WIN")
            ml_ = sum(1 for e in missed if e.get("result") == "LOSS")
            mu = sum(e.get("profit") or 0 for e in missed)
            print(f"{'':16}not bet (rule fired, no wager): {mw}-{ml_} "
                  f"{mu:+.2f}u — counted in W-L, excluded from units")
        if back:
            bw = sum(1 for e in back if e.get("result") == "WIN")
            bl = sum(1 for e in back if e.get("result") == "LOSS")
            bu = sum(e.get("profit") or 0 for e in back)
            print(f"{'':16}backfilled (never wagered): {bw}-{bl} {bu:+.2f}u"
                  f"  ROI {bu/max(1, bw+bl):+.1%}")
        if retired:
            rw = sum(1 for e in retired if e.get("result") == "WIN")
            rl = sum(1 for e in retired if e.get("result") == "LOSS")
            ru = sum(e.get("profit") or 0 for e in retired
                     if not e.get("not_bet"))
            print(f"{'':16}retired rules (excluded from record): {rw}-{rl} "
                  f"{ru:+.2f}u risked")
        if shadow:
            sw = sum(1 for e in shadow if e.get("result") == "WIN")
            sl = sum(1 for e in shadow if e.get("result") == "LOSS")
            su = sum(e.get("profit") or 0 for e in shadow)
            need = max(0, 15 - (sw + sl))
            print(f"{'':16}shadow: {sw}-{sl} {su:+.2f}u"
                  + (f" ({need} more before card-eligible)" if need else
                     " (eligible for review)"))
        if args.verbose:
            for h in sorted(pre, key=lambda x: x["date"]):
                rec = f"{h['wins']}-{h['losses']}" + (
                    f"-{h['pushes']}" if h.get("pushes") else "")
                print(f"      {h['date']}  pre-log day: {rec}  "
                      f"{(h.get('units') or 0):+.2f}u")
            for e in sorted(logged, key=lambda x: x["date"]):
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
    p.add_argument("--line", type=float, default=None,
                   help="the total/spread at card time, for CLV grading")
    p.add_argument("--shadow", action="store_true",
                   help="rule auditioning: tracked, never counted as a bet")

    p = sub.add_parser("noplay", help="log a day with no qualifying plays")
    p.add_argument("--date", required=True)
    p.add_argument("--note", default="")

    p = sub.add_parser("grade", help="set a logged play's result")
    p.add_argument("--date", required=True)
    p.add_argument("--play", required=True)
    p.add_argument("--result", required=True, choices=("WIN", "LOSS", "PUSH", "pending"))
    p.add_argument("--profit", type=float, default=None)
    p.add_argument("--close-line", type=float, default=None,
                   help="closing total/spread, to score CLV against --line")

    p = sub.add_parser("report", help="Monday-Sunday weekly rollup")
    p.add_argument("--verbose", "-v", action="store_true")

    args = ap.parse_args()
    blob = _load()
    {"add": cmd_add, "noplay": cmd_noplay,
     "grade": cmd_grade, "report": cmd_report}[args.cmd](args, blob)


if __name__ == "__main__":
    main()

"""
systems_log.py -- the NBA systems bet ledger: append, grade, report.

A ledger that logs itself and never grades itself is worse than one that does
neither: it looks complete while every system's record is stuck at the last day
somebody remembered. So grading runs inside the daily run, not by hand.

Three rules that make the record honest:

  PRICE AT CARD TIME.  Profit uses the price stored on the entry, never the
  file's current number. A line that moved after the play was logged cannot
  regrade it.
  SHADOW IS GRADED, NOT COUNTED.  Everything here is shadow at open. Shadow
  entries settle exactly like card entries and report on their own line; they
  are simply held out of the card units. That is what makes a promotion later
  an evidence-based call instead of a guess.
  IDEMPOTENT.  Only `pending` rows are ever touched, and an entry key is
  (date, system, away, home), so re-running a day changes nothing.

MONEYLINE ENTRIES CARRY TWO PRICES. `price` is the frozen spread-to-ML
conversion (situational_systems.ml_american) and is what grades the entry, so
the forward record stays comparable with the backtest it is testing.
`book_price` is the real h2h number the feed saw, recorded from 2026-09
onward. `book_profit` grades the same play at the real price. The gap between
profit and book_profit is the measurement the backtest could never make.

Usage:
    python -m systems_log report
    python -m systems_log report --system elite_dog_ml
"""
import argparse
import datetime
import json
import os

import situational_systems as R

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.normpath(os.path.join(HERE, "..", "data", "nba-systems-log.json"))

CARD, SHADOW = "card", "shadow"


def _now():
    return (datetime.datetime.now(datetime.timezone.utc)
            .isoformat(timespec="seconds").replace("+00:00", "Z"))


def empty_blob():
    return {
        "sport": "NBA",
        "type": "systems-log",
        "description": (
            "Forward record for the pre-registered NBA situational systems. "
            "Every system is SHADOW at open -- graded and priced, no money on "
            "it -- until it clears a promotion bar on games the registry never "
            "saw. The 2025-26 backtest lives in backtest_history and is held "
            "out of the live record on purpose: it is the claim being tested, "
            "not evidence for it."),
        "registry_frozen": "2026-09-02",
        "promotion_min_plays": R.PROMOTION_MIN_PLAYS,
        "generated": _now(),
        "backtest_history": {s["id"]: dict(s["backtest"]) for s in R.SYSTEMS},
        "entries": [],
    }


def load():
    if not os.path.exists(LOG):
        return empty_blob()
    with open(LOG, encoding="utf-8") as fh:
        blob = json.load(fh)
    blob.setdefault("entries", [])
    # keep the backtest block in step with the registry without touching entries
    blob["backtest_history"] = {s["id"]: dict(s["backtest"]) for s in R.SYSTEMS}
    return blob


def save(blob):
    blob["generated"] = _now()
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "w", encoding="utf-8") as fh:
        json.dump(blob, fh, indent=1)


def entry_key(e):
    """Unique per play. The TEAM is part of the key, not just the game: both
    sides of one game can fire the same team-based system (both coming off a
    10+ cover, say), and keying on the matchup alone silently dropped the
    second one -- 539 logged plays where the backtest scored 600."""
    return "{0}|{1}|{2}".format(e["date"], e["system"], e.get("team"))


def profit_for(price, stake, result):
    """Net units at an American price, flat `stake` risked (the scout ledger's
    convention: -110 pays 0.909 on 1u, not 1.0 on 1.1u)."""
    if result == "LOSS":
        return -stake
    if result != "WIN":
        return 0.0
    if price is None:
        return 0.0
    return round(stake * (price / 100.0 if price > 0 else 100.0 / -price), 4)


# ---------------------------------------------------------------------------
def add(blob, *, date, system, row, status=None, stake=1.0):
    """Append one play. Returns the entry, or None if it is already logged.

    Status defaults to the REGISTRY's, never to shadow: a caller that forgets
    to pass one must not silently log a carded play as untracked."""
    s = R.BY_ID[system]
    status = status or s.get("status", SHADOW)
    market = s["market"]
    away, home = _sides(row)

    if market == "total":
        play = f"{s['side']} {_fmt(row['total'])}"
        line, price = row["total"], _price_or_default(
            row.get("over_ml") if s["side"] == "OVER" else row.get("under_ml"))
        book_price = row.get("over_ml") if s["side"] == "OVER" else row.get("under_ml")
    elif market == "spread":
        play = f"{row['team']} {_fmt(row['spread'], signed=True)}"
        line, price = row["spread"], _price_or_default(row.get("spread_price"))
        book_price = row.get("spread_price")
    else:
        play = f"{row['team']} ML"
        line = row["spread"]
        price = R.ml_american(row["spread"])     # frozen conversion -- grades the entry
        book_price = row.get("ml_price")         # real h2h, from 2026-09 on

    e = {
        "date": date, "system": system, "tier": s["tier"],
        "market": market, "side": s["side"],
        "play": play, "team": row["team"], "game": f"{away} @ {home}",
        "away": away, "home": home,
        "line": line, "price": price, "book_price": book_price,
        "stake": stake, "status": status,
        "result": "pending", "profit": 0.0, "book_profit": 0.0,
        "startTimeUTC": row.get("startTimeUTC"),
    }
    keys = {entry_key(x) for x in blob["entries"]}
    if entry_key(e) in keys:
        return None
    blob["entries"].append(e)
    return e


def add_no_play(blob, date, note=""):
    """A slate on which nothing fired. First-class: an empty card is evidence,
    and skipping it makes the record look busier than it was."""
    for x in blob["entries"]:
        if x.get("date") == date and x.get("no_play"):
            return None
    e = {"date": date, "system": None, "tier": None, "market": None,
         "play": "NO PLAY", "game": None, "away": None, "home": None,
         "no_play": True, "note": note, "status": SHADOW,
         "result": "no_play", "profit": 0.0, "book_profit": 0.0}
    blob["entries"].append(e)
    return e


def _sides(row):
    return (row["opp"], row["team"]) if row["is_home"] else (row["team"], row["opp"])


def _fmt(v, signed=False):
    if v is None:
        return "?"
    s = f"{v:+.1f}" if signed else f"{v:.1f}"
    return s.replace(".0", "") if abs(v - round(v)) < 1e-9 else s


def _price_or_default(p):
    """Spreads and totals grade at the real price when the feed carried one,
    and at -110 otherwise -- which is what the backtest assumed."""
    return p if isinstance(p, (int, float)) else -110


# ---------------------------------------------------------------------------
def grade(blob, finals, dry_run=False):
    """Settle every pending entry whose game has a final. `finals` maps
    (date, away, home) -> (away_score, home_score). Returns entries graded."""
    graded = []
    for e in blob["entries"]:
        if e.get("result") != "pending" or e.get("no_play"):
            continue
        key = (e["date"], e["away"], e["home"])
        fin = finals.get(key)
        if not fin:
            continue
        a_s, h_s = fin
        res = _settle(e, a_s, h_s)
        if res is None:
            continue
        e["result"] = res
        e["profit"] = profit_for(e.get("price"), e.get("stake", 1.0), res)
        e["book_profit"] = (profit_for(e["book_price"], e.get("stake", 1.0), res)
                            if e.get("book_price") is not None else None)
        graded.append(e)
    if graded and not dry_run:
        save(blob)
    return graded


def _settle(e, away_score, home_score):
    """WIN / LOSS / PUSH for one entry, against its OWN stored line."""
    market = e["market"]
    if market == "total":
        if e["line"] is None:
            return None
        pts = away_score + home_score
        if pts == e["line"]:
            return "PUSH"
        over = pts > e["line"]
        return "WIN" if (over == (e["side"] == "OVER")) else "LOSS"
    is_home = e["team"] == e["home"]
    margin = (home_score - away_score) if is_home else (away_score - home_score)
    if market == "spread":
        if e["line"] is None:
            return None
        adj = margin + e["line"]
        return "PUSH" if adj == 0 else ("WIN" if adj > 0 else "LOSS")
    if margin == 0:
        return None                      # basketball has no ties; guard anyway
    return "WIN" if margin > 0 else "LOSS"


# ---------------------------------------------------------------------------
def tally(entries, system=None, status=None, backfilled=False):
    """W-L-P, units and ROI over a slice of the ledger.

    Seeded 2025-26 plays are EXCLUDED by default. They are in the ledger so the
    season log has something to filter, but they are the in-sample claim under
    test -- counting them as live record would fold the backtest back into its
    own out-of-sample result and defeat the whole point. Pass backfilled=True
    to report on them deliberately.
    """
    t = {"w": 0, "l": 0, "p": 0, "pending": 0, "units": 0.0,
         "book_units": 0.0, "book_n": 0}
    for e in entries:
        if e.get("no_play"):
            continue
        if bool(e.get("backfilled")) != bool(backfilled):
            continue
        if system and e["system"] != system:
            continue
        if status and e.get("status") != status:
            continue
        r = e.get("result")
        if r == "WIN":
            t["w"] += 1
        elif r == "LOSS":
            t["l"] += 1
        elif r == "PUSH":
            t["p"] += 1
        else:
            t["pending"] += 1
            continue
        t["units"] += e.get("profit") or 0.0
        if e.get("book_profit") is not None:
            t["book_units"] += e["book_profit"]
            t["book_n"] += 1
    n = t["w"] + t["l"]
    t["n"] = n
    t["pct"] = (t["w"] / n) if n else None
    t["roi"] = (t["units"] / (n + t["p"])) if (n + t["p"]) else None
    t["units"] = round(t["units"], 3)
    t["book_units"] = round(t["book_units"], 3)
    return t


def report(blob, system=None):
    entries = blob["entries"]
    live = [e for e in entries if not e.get("no_play")]
    ncard = sum(1 for x in R.SYSTEMS if x.get("status") == CARD)
    print("NBA systems ledger -- {0} plays, registry frozen {1}, {2} carded / "
          "{3} shadow".format(len(live), blob.get("registry_frozen"), ncard,
                              len(R.SYSTEMS) - ncard))
    if not live:
        print("  no live plays yet. The registry is pre-registered and idle "
              "until the season opens.")
    print("\n  {0:18s} {1:7s} {2:10s} {3:>12s} {4:>8s} {5:>8s} | {6:>18s} {7:>8s}"
          .format("system", "status", "tier", "live", "units", "ROI",
                  "2025-26 backtest", "ROI"))
    print("  " + "-" * 92)
    for s in R.SYSTEMS:
        if system and s["id"] != system:
            continue
        t = tally(entries, system=s["id"])
        b = blob.get("backtest_history", {}).get(s["id"], {})
        rec = "{0}-{1}".format(t["w"], t["l"]) + ("-{0}".format(t["p"]) if t["p"] else "")
        roi = "{0:+.1f}%".format(t["roi"] * 100) if t["roi"] is not None else "-"
        broi = "{0:+.1f}%".format((b.get("roi") or 0) * 100) if b.get("roi") else "-"
        brec = "{0}-{1}".format(b.get("w", "?"), b.get("l", "?"))
        pend = "  (+{0} pend)".format(t["pending"]) if t["pending"] else ""
        print("  {0:18s} {1:7s} {2:10s} {3:>12s} {4:+7.1f}u {5:>8s} | {6:>18s} {7:>8s}{8}"
              .format(s["id"], s.get("status", SHADOW).upper(), s["tier"], rec,
                      t["units"], roi, brec, broi, pend))
    tot = tally(entries)
    print("  " + "-" * 92)
    trec = "{0}-{1}".format(tot["w"], tot["l"])
    troi = "{0:+.1f}%".format(tot["roi"] * 100) if tot["roi"] is not None else "-"
    print("  {0:18s} {1:7s} {2:10s} {3:>12s} {4:+7.1f}u {5:>8s}"
          .format("ALL", "", "", trec, tot["units"], troi))
    for st in (CARD, SHADOW):
        t = tally(entries, status=st)
        if t["n"] or t["pending"]:
            print("  {0:18s} {1:7s} {2:10s} {3:>12s} {4:+7.1f}u"
                  .format("  " + st, "", "",
                          "{0}-{1}".format(t["w"], t["l"]), t["units"]))
    if tot["book_n"]:
        print(f"  moneyline at REAL book prices where available: "
              f"{tot['book_units']:+.1f}u over {tot['book_n']} graded plays "
              f"(converted price gave {tot['units']:+.1f}u overall)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["report"])
    ap.add_argument("--system", default=None)
    args = ap.parse_args()
    report(load(), args.system)


if __name__ == "__main__":
    main()

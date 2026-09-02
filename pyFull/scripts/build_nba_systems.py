"""
build_nba_systems.py -- evaluate the frozen registry on tonight's slate, log
the plays, grade what has finished, and write the dashboard feed.

Called from run_daily.py after the games are analyzed, and safe to call by
hand. Off-season safe: a slate with no games writes a valid empty feed and a
NO PLAY ledger row rather than crashing, so this can ship now and sit idle
until October.

Order matters. Grading runs BEFORE tonight's plays are appended, so a re-run
during a slate cannot grade a game that has not finished, and the ledger's
pending count always means "waiting on a final" rather than "waiting on a
run".

Usage:
    python build_nba_systems.py                 # today, from history.json
    python build_nba_systems.py --date 20260115 # rebuild one day
    python build_nba_systems.py --seed          # replay 2025-26 into the ledger
"""
import argparse
import datetime
import json
import os
import shutil

import situational_systems as R
import systems_context as C
import systems_log as LOG

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.normpath(os.path.join(HERE, "..", "data"))
OUT = os.path.join(DATA, "nba-systems.json")
DASH = os.path.normpath(os.path.join(HERE, "..", "..", "PythonDashboard",
                                     "data", "nba-systems.json"))

NOTES = [
    "PRE-REGISTERED EXPERIMENT, NOT A BETTING PRODUCT. Every backtest number "
    "on this page is in-sample, from a single season screened over ~3,600 "
    "conditions. A permutation test says that screen returns 13 winners where "
    "noise returns 7.1, and that its best system (+30.9u) is what noise "
    "produces one time in five.",
    "A walk-forward test says systems picked this way do not hold: 22 selected "
    "on the first half of 2025-26 went -76.2u, -4.2% ROI on the second half, "
    "with 5 of 22 staying positive where chance predicts 11.",
    "THE CONTROLS WERE DROPPED 2026-09-02. Five known-junk conditions that "
    "cleared the same ROI > 10% bar used to run alongside these as a null. "
    "Without them the forward record can only be read against break-even, not "
    "against what this screen returns on nothing. bigdog_ml in particular was "
    "the canary for the frozen moneyline conversion, which still prices three "
    "of the seven carded systems.",
    "MONEYLINE PLAYS CARRY TWO PRICES. `price` is the frozen conversion and "
    "grades the entry so the forward record stays comparable with the "
    "backtest; `book_price` is the real h2h number. Their difference measures "
    "how wrong the conversion is -- something the backtest could never do.",
    "CARDED 2026-09-02: all seven candidates are bet. That was decided at "
    "registry freeze, on the in-sample evidence above, without waiting for the "
    "25-play out-of-sample gate.",
]


def finals_from_store(store):
    """(date, away, home) -> (away_score, home_score) for every settled game."""
    out = {}
    for r in store.get("runs", []):
        for g in r.get("games", []):
            a, h = g.get("awayScore"), g.get("homeScore")
            if isinstance(a, (int, float)) and isinstance(h, (int, float)):
                out[(r.get("date"), g.get("away"), g.get("home"))] = (float(a), float(h))
    return out


def _play_row(row, s, date):
    away, home = (row["opp"], row["team"]) if row["is_home"] else (row["team"], row["opp"])
    if s["market"] == "total":
        play = "{0} {1}".format(s["side"], LOG._fmt(row["total"]))
        price = LOG._price_or_default(
            row.get("over_ml") if s["side"] == "OVER" else row.get("under_ml"))
    elif s["market"] == "spread":
        play = "{0} {1}".format(row["team"], LOG._fmt(row["spread"], signed=True))
        price = LOG._price_or_default(row.get("spread_price"))
    else:
        play = "{0} ML".format(row["team"])
        price = R.ml_american(row["spread"])
    return {
        "date": date, "system": s["id"], "tier": s["tier"], "market": s["market"],
        "side": s["side"], "label": s["label"], "play": play, "team": row["team"],
        "game": "{0} @ {1}".format(away, home), "away": away, "home": home,
        "line": row["total"] if s["market"] == "total" else row["spread"],
        "spread": row["spread"], "total": row["total"],
        "price": price, "book_price": row.get("ml_price") if s["market"] == "h2h" else None,
        "status": s.get("status", LOG.SHADOW),
        "startTimeUTC": row.get("startTimeUTC"),
    }


def build(date=None, store=None, pending=None, write_dashboard=True):
    """Evaluate `date`'s slate. `pending` is tonight's odds rows when called
    live; without it the date is read from history."""
    if store is None:
        with open(os.path.join(DATA, "history.json"), encoding="utf-8") as fh:
            store = json.load(fh)
    date = date or datetime.datetime.now().strftime("%Y%m%d")

    stats = C.load_stats()
    games = C.load_games(store=store, pending=pending, pending_date=date)
    rows, playoff_start = C.build(games, stats)
    C.enrich(rows, games)

    blob = LOG.load()

    # 1. grade first -- see the module docstring
    graded = LOG.grade(blob, finals_from_store(store))

    # 2. tonight's plays
    todays = [r for r in rows if r["date"] == date]
    plays = []
    for row in todays:
        for s in R.evaluate(row):
            plays.append(_play_row(row, s, date))
    # card first, then shadow, first tip inside each: the card is what gets
    # acted on and must not be interleaved with rows kept only for the record
    plays.sort(key=lambda p: (0 if p["status"] == LOG.CARD else 1,
                              R.TIERS.index(p["tier"]) if p["tier"] in R.TIERS else 9,
                              str(p.get("startTimeUTC") or ""), p["system"]))

    # 3. log them (idempotent on date|system|away|home)
    added = 0
    for row in todays:
        for s in R.evaluate(row):
            if LOG.add(blob, date=date, system=s["id"], row=row) is not None:
                added += 1
    if todays and not plays:
        LOG.add_no_play(blob, date, "slate had games, no system fired")
    LOG.save(blob)

    # 4. records: live ledger vs the in-sample backtest, never merged
    records = {}
    for s in R.SYSTEMS:
        records[s["id"]] = {
            "live": LOG.tally(blob["entries"], system=s["id"]),
            "shadow": LOG.tally(blob["entries"], system=s["id"], status=LOG.SHADOW),
            "card": LOG.tally(blob["entries"], system=s["id"], status=LOG.CARD),
            # replayed 2025-26, held out of live -- the claim, not the evidence
            "seeded": LOG.tally(blob["entries"], system=s["id"], backfilled=True),
            "backtest": dict(s["backtest"]),
        }

    feed = {
        "sport": "NBA", "type": "systems",
        "generated": LOG._now(),
        "date": date,
        "dateDisplay": "{0}-{1}-{2}".format(date[:4], date[4:6], date[6:]),
        "registry_frozen": blob.get("registry_frozen"),
        "promotion_min_plays": R.PROMOTION_MIN_PLAYS,
        "bar_roi": R.BAR_ROI,
        "games_today": len({r["gi"] for r in todays}),
        "ml_conversion": {"a": R.ML_FIT_A, "b": R.ML_FIT_B, "hold": R.ML_HOLD,
                          "note": "frozen at the 2025-26 fit; never refit"},
        "systems": [{
            "id": s["id"], "tier": s["tier"], "market": s["market"],
            "side": s["side"], "label": s["label"], "mechanism": s["mechanism"],
            "backtest": dict(s["backtest"]),
            "status": s.get("status", LOG.SHADOW),
        } for s in R.SYSTEMS],
        "today": plays,
        "records": records,
        "log": blob["entries"],
        "totals": {
            "live": LOG.tally(blob["entries"]),
            "card": LOG.tally(blob["entries"], status=LOG.CARD),
            "shadow": LOG.tally(blob["entries"], status=LOG.SHADOW),
            "candidate": LOG.tally([e for e in blob["entries"]
                                    if e.get("tier") == "candidate"]),

        },
        "notes": NOTES,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(feed, fh, indent=1)
    if write_dashboard:
        try:
            os.makedirs(os.path.dirname(DASH), exist_ok=True)
            shutil.copy2(OUT, DASH)
        except Exception as e:
            print("  [systems] dashboard copy failed: {0}".format(e))

    print("  [systems] {0}: {1} game(s), {2} play(s) ({3} new), {4} graded"
          .format(date, feed["games_today"], len(plays), added, len(graded)))
    return feed


def seed():
    """Replay every settled 2025-26 game through the registry so the ledger
    opens with a full, gradeable history. Marked `backfilled` so it stays out
    of the live record -- it is the claim under test, not evidence for it."""
    with open(os.path.join(DATA, "history.json"), encoding="utf-8") as fh:
        store = json.load(fh)
    stats = C.load_stats()
    games = C.load_games(store=store)
    rows, _ = C.build(games, stats)
    C.enrich(rows, games)

    blob = LOG.load()
    blob["entries"] = [e for e in blob["entries"] if not e.get("backfilled")]
    n = 0
    for row in rows:
        if not row["settled"]:
            continue
        for s in R.evaluate(row):
            e = LOG.add(blob, date=row["date"], system=s["id"], row=row)
            if e is not None:
                e["backfilled"] = True
                n += 1
    graded = LOG.grade(blob, finals_from_store(store), dry_run=True)
    LOG.save(blob)
    print("  [seed] {0} backfilled plays, {1} graded".format(n, len(graded)))
    return blob


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--no-dashboard", action="store_true")
    args = ap.parse_args()
    if args.seed:
        seed()
    build(date=args.date, write_dashboard=not args.no_dashboard)


if __name__ == "__main__":
    main()

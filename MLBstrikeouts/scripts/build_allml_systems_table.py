#!/usr/bin/env python3
"""
build_allml_systems_table.py — the non-scout systems' season record, daily.

Same job build_flag_combo_table.py does for the flag combos and
build_msum_ml_table.py does for the better-arm rule: replay every system over
the full settled season as-of each game date, write the record the dashboard
renders, and rebuild it every run so the table on the tab is never a number
somebody typed once.

Definitions live in scripts/allml_systems.py, which the daily logger imports
too, so the table, the ledger and the tab cannot disagree about what a rule is.

Output: MLBstrikeouts/data/allml-systems-table.json (+ the dashboard copy)

Usage:  cd MLBstrikeouts && python -m scripts.build_allml_systems_table
"""
import collections
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import allml_systems as SYS
from rule_status import RULE_STATUS

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATHS = [
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data",
                                  "allml-systems-table.json")),
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "..", "PythonDashboard",
                                  "data", "allml-systems-table.json")),
]


# Which blind baseline a system is measured against. Side bets go against
# backing every side at market price; totals against blind over / blind under.
BASELINE_KEY = {"h2h": "side", "totals": None}


def blind_baselines(settled):
    """The four do-nothing benchmarks, from the same games the systems see."""
    side, over, under = [], [], []
    for g in settled:
        hw = g["home_score"] > g["away_score"]
        side.append(SYS._profit(g["home_ml"], hw))
        side.append(SYS._profit(g["away_ml"], not hw))
        tot = g["away_score"] + g["home_score"]
        if tot == g["total_line"]:
            continue
        over.append(SYS._profit(g["over_ml"], tot > g["total_line"]))
        under.append(SYS._profit(g["under_ml"], tot < g["total_line"]))
    pct = lambda v: round(sum(v) / len(v) * 100, 1) if v else None
    return {"side": pct(side), "over": pct(over), "under": pct(under),
            "parlay": 0.0}


def split(rows, at):
    return [r for r in rows if r["date"] < at], [r for r in rows if r["date"] >= at]


LEDGER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "data", "scout-card-log.json")


def _with_stale(today, blob):
    """Add back plays that fired earlier today but no longer qualify.

    A rule is evaluated against the CURRENT market, so a line move can drop
    a play out of `today` while the bet is still live in the ledger. On
    2026-09-02 DET/MIN went from a -112 favourite to -116, one tick past
    pickem-under's threshold, and a logged card play vanished from the panel
    with money on it.

    The ledger is the bet; the panel should not quietly disagree. Any
    non-scout row logged for today that the engine no longer produces is
    appended with `stale`, so the tab can show it as still-on rather than
    drop it. Matched on (rule, gamePk) -- the price and line are expected to
    have moved, that is the whole point.
    """
    date = (blob.get("today") or [{}])[0].get("date")
    if not date or not os.path.exists(LEDGER_PATH):
        return today
    with open(LEDGER_PATH, encoding="utf-8") as fh:
        entries = json.load(fh).get("entries") or []
    live = {(p["rule"], p.get("gamePk")) for p in today}
    games = {g.get("gamePk"): g for g in (blob.get("today") or [])}
    finals = {k for k, g in games.items() if g.get("final")}
    out = list(today)
    for e in entries:
        key = (e.get("rule"), e.get("gamePk"))
        if (e.get("date") != date or e.get("rule") not in SYS.SYSTEMS
                or key in live or e.get("gamePk") in finals):
            continue
        live.add(key)
        out.append({
            "rule": e["rule"], "market": e.get("market"),
            "pick": e.get("pick") or _pick_from(e),
            "price": e.get("price"), "line": e.get("line"),
            "ml_price": e.get("ml_price"), "under_price": e.get("under_price"),
            "payout": e.get("payout"),
            "gamePk": e.get("gamePk"), "date": e.get("date"),
            "commence": e.get("commence"),
            "matchup": e.get("game"), "total": e.get("line"),
            "why": (e.get("basis") or "").split(": ", 1)[-1],
            "stale": True,
            "stale_now": _moved(e, games.get(e.get("gamePk"))),
        })
    out.sort(key=lambda p: (str(p.get("commence") or "~"), p["rule"]))
    return out


def _moved(entry, game):
    """What the market looks like NOW, for a row that stopped qualifying.

    A bare "line moved" tag makes the reader go and check. Saying which
    number moved and to what answers the question on the row.
    """
    if not game:
        return "no longer on the slate"
    bits = []
    fav = None
    if game.get("away_ml") is not None and game.get("home_ml") is not None:
        fav = min(game["away_ml"], game["home_ml"])
        bits.append(f"favourite now {fav:+d}")
    tot = game.get("total_line")
    if tot is not None and entry.get("line") is not None and tot != entry["line"]:
        bits.append(f"total now {tot:g} (was {entry['line']:g})")
    elif tot is not None:
        bits.append(f"total still {tot:g}")
    return ", ".join(bits) or "market moved"


def _mark_conflicts(today):
    """Flag BOTH sides of a game two carded rules disagree on.

    The engine has no notion of conflicts -- each rule answers only for
    itself -- so starter-over-run still fires on a game the parlay is
    already taking the under in. The plays stay on the board, because a
    row that vanishes is how a live bet gets lost, and carry the flag so
    the tab can say why they are not being taken. A parlay's second leg
    counts as an under for detection.

    SYMMETRIC SINCE 2026-09-03 (user). It used to flag the over only, on
    the policy of keeping the under and passing the over. Both sides are
    passed now. What that costs, stated because it is a cost and not a
    saving: the over side was already being passed, so the change gives up
    the UNDER side's 17-12 +12.6% (+3.66u over 29 settled plays). The card
    tier's ROI rises +27.6% -> +28.0% only because +12.6% sits below the
    tier average -- the units go down.

    The case for it is the disagreement, not the return. Across carded and
    shadow rules together a one-over-vs-one-under game returns -4.1% to the
    under and -3.8% to the over over 150 plays: two rules cancelling to the
    vig, with no side to be on. The carded-only under cell that pays for
    the old policy is 29 plays with a negative last third, and it is really
    the single statement that starter-over-run is wrong when a carded under
    contradicts it (+23.4% unopposed, -9.7% opposed).

    The parlay stands down with them, on every date (user, 2026-09-03). It
    briefly carried a CONFLICT_PARLAY_FROM start date so the ledger would
    not be restated; the user asked for the whole season instead, and
    scripts/backfill_conflict_skips.py marked the history to match. One
    policy, one record, no date seam in the middle of it.

    It costs 6-14 +32.1% (+6.42u over 20 plays) and is defensible because a
    parlay on a conflicted game measures worse than an unopposed one
    (+32.1% against +46.6%, n=234) -- the +27.7% that justified the original
    exemption was the under leg graded STRAIGHT, against a +9.9% unopposed
    baseline, which is a different bet.
    """
    # Card rules only. A shadow rule has no money on it, so it can neither
    # create a conflict nor be told to stand down for one.
    by_game = collections.defaultdict(list)
    for p in today:
        if (p.get("market") in ("totals", "parlay")
                and RULE_STATUS.get(p["rule"]) == "card"):
            by_game[p.get("gamePk")].append(p)
    for rows in by_game.values():
        sides = {("under" if r.get("market") == "parlay" else r.get("pick"))
                 for r in rows}
        if "over" in sides and "under" in sides:
            for r in rows:
                r["conflict_skip"] = True


def _pick_from(entry):
    """Side for a ledger row that predates the engine carrying `pick`."""
    play = entry.get("play") or ""
    if entry.get("market") == "totals":
        return "over" if " O" in play else "under"
    return play.split(" ML")[0].strip()


def main():
    blob = SYS.load()
    rows = SYS.replay(blob)
    settled = SYS._settled(blob)
    dates = [g["date"] for g in settled]
    mid = dates[len(dates) // 2] if dates else None

    base = blind_baselines(settled)
    systems = []
    for key in SYS.ALL_ORDER:
        name, market, rule, case = SYS.SYSTEMS[key]
        rs = rows.get(key, [])
        by = collections.defaultdict(list)
        for r in rs:
            by[r["date"][:7]].append(r["p"])
        a, b = split(rs, mid) if mid else ([], [])
        if market == "parlay":
            # No blind baseline exists for a two-leg parlay; the honest
            # benchmark is break-even, so it is measured against zero.
            bkey = "parlay"
        elif market == "h2h":
            bkey = "side"
        else:
            bkey = "over" if any(r["pick"] == "over" for r in rs) else "under"
        systems.append({
            "baseline_key": bkey,
            "baseline": base.get(bkey),
            "key": key,
            "name": name,
            "market": market,
            "rule": rule,
            "plain": SYS.PLAIN.get(key, ""),
            "case": case,
            "status": RULE_STATUS.get(key, "shadow"),
            "ladder_fails": case.startswith("LADDER FAILS"),
            "record": SYS.summarize(rs),
            "halves": [SYS.summarize(a)["roi"], SYS.summarize(b)["roi"]],
            "monthly": [{"month": m, **SYS.summarize(
                [r for r in rs if r["date"][:7] == m])}
                for m in sorted(by)],
            "per_day": round(len(rs) / len(set(dates)), 2) if dates else 0,
        })

    today = SYS.today_plays(blob)
    today = _with_stale(today, blob)
    _mark_conflicts(today)
    blob_out = {
        "sport": "MLB",
        "type": "allml-systems-table",
        "generated": datetime.datetime.now(datetime.timezone.utc)
                             .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "span": {"from": dates[0], "to": dates[-1]} if dates else {},
        "games": len(settled),
        "note": ("Systems derived from mlb-all-ml.json alone -- moneylines, "
                 "totals, probables, scores -- with no input from the mismatch "
                 "model. Replayed as-of each game date: a game's own result is "
                 "never in the features that select it. Each system carries the "
                 "blind baseline it is measured against: backing every side "
                 "at market price for moneylines, blind over or blind under "
                 "for totals."),
        "baselines": base,
        "systems": systems,
        "today": today,
        "today_date": (blob.get("today") or [{}])[0].get("date"),
    }

    for path in OUTPUT_PATHS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(blob_out, fh, indent=1)
    tot = sum(s["record"]["units"] for s in systems)
    print(f"allml systems table: {len(systems)} systems, "
          f"{sum(s['record']['n'] for s in systems)} graded plays, "
          f"{tot:+.1f}u season, {len(today)} tonight -> "
          f"{len(OUTPUT_PATHS)} paths")


if __name__ == "__main__":
    main()

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
    finals = {g.get("gamePk") for g in (blob.get("today") or []) if g.get("final")}
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
        })
    out.sort(key=lambda p: (str(p.get("commence") or "~"), p["rule"]))
    return out


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

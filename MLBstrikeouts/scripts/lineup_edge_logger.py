"""
lineup_edge_logger.py — Prospective ledger for lineup-delta signals.

The rule this was built to track is REFUTED; see ``slate_lineup_edge.py``. Its
backtest read the batting order from the boxscore, which is what was played
rather than what was posted, and against genuinely pre-game lineups the edge is
50.0% on 100 bets — nothing. Do not stake anything off this ledger.

Writing this logger is what exposed the problem, and that is why it is kept.
It reads ``lineups_YYYYMMDD.json`` — the pre-game confirmed source — so the
deltas it records disagreed immediately with the backtest's, which is the
discrepancy that unravelled the finding. Any future lineup hypothesis should be
logged prospectively through this path from the start, rather than backtested
against post-game data and validated afterwards.

The price-drift half of ``--report`` remains independently useful: it measures
whether the total stored in the historical record is one that was actually
available at lineup time, which is a question any future totals work will need
answered.

Two modes:

``--capture``  Snapshot the slate. For every game with both lineups posted it
               records the total and prices available NOW, the lineup delta,
               and which rules qualify. Safe to run on every pass of the daily
               pipeline — rows are keyed by (date, game, captured_at), so
               repeated runs build a price timeline rather than overwriting.

``--report``   Grade the ledger. Reports (1) PRICE DRIFT: captured total vs the
               total that later landed in the historical record, which is the
               Phase 1 question, and (2) the live record of each rule once
               finals exist.

The ledger is JSON Lines at ``MLBstrikeouts/data/lineup-edge-ledger.jsonl`` so
appends are atomic-ish and a partial write costs one row.

Nothing here bets, sizes, or feeds another model. It records.

Usage
-----
    python MLBstrikeouts/scripts/lineup_edge_logger.py --capture
    python MLBstrikeouts/scripts/lineup_edge_logger.py --report
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import slate_wrc_form as S
import slate_wrc_form_backfill as B
import slate_lineup_edge as L

LEDGER = S.REPO / "MLBstrikeouts" / "data" / "lineup-edge-ledger.jsonl"
LINEUP_DIR = S.REPO / "data" / "pitcher_cache" / "mlb"

# Rules carried in the ledger. All three are logged from one capture so the
# looser rule supplies statistical power while the tighter one is evaluated;
# which to act on is a decision for the report, not the logger.
RULES = {
    "avg<=0": lambda avg, both: avg <= 0,
    "both<=0": lambda avg, both: both <= 0,
    "both<=-2": lambda avg, both: both <= -2,
}


def load_lineups(date_iso):
    """{team: [batter_ids]} for confirmed lineups, matching build_team_woba_splits."""
    path = LINEUP_DIR / f"lineups_{date_iso.replace('-', '')}.json"
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    out = {}
    for team, v in (data or {}).items():
        if not isinstance(v, dict):
            continue
        if (bool(v.get("confirmed")) or v.get("source") == "lineup") and v.get("player_ids"):
            out[team] = list(v["player_ids"])
    return out


def capture(now_iso=None):
    """Append a snapshot row for every game whose lineups are both posted."""
    allml = S._load(S.ALL_ML)
    today = allml.get("today", [])
    if not today:
        print("no games in today[]")
        return
    date_iso = today[0].get("date")
    lineups = load_lineups(date_iso)
    if not lineups:
        print(f"{date_iso}: no confirmed lineups posted yet — nothing captured")
        return

    bsplits = S._load(L.BATTER_SPLITS)
    index = L.index_batters(bsplits)
    lg = L.league_woba_by_hand(bsplits, date_iso)
    team_wrc = B.wrc_as_of(S._load(B.PA_SPLITS), date_iso,
                           park_adjust=False)["season"]["teams"]

    captured_at = now_iso or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    rows, skipped = [], 0
    for g in today:
        if g.get("total_line") is None:
            skipped += 1
            continue
        deltas = {}
        for bat_team, pitch_side in ((g["home"], "away"), (g["away"], "home")):
            hand = g.get(f"{pitch_side}_hand")
            ids = lineups.get(bat_team)
            if hand not in ("L", "R") or not ids:
                break
            pooled = {k: 0 for k in L.ACC}
            for bid in ids:
                c = L.before(index, bid, hand, date_iso)
                if c:
                    for k in L.ACC:
                        pooled[k] += c[k]
            lu = L.wrcplus(pooled, lg[hand])
            tm = (team_wrc.get(bat_team) or {}).get(
                "vsLHP" if hand == "L" else "vsRHP")
            if lu is None or not tm:
                break
            deltas[bat_team] = round(lu - tm["wrcplus"], 1)
        if len(deltas) != 2:
            skipped += 1
            continue

        vals = list(deltas.values())
        avg, both = sum(vals) / 2, max(vals)
        rows.append({
            "date": date_iso,
            "matchup": f"{g['away']} @ {g['home']}",
            "captured_at": captured_at,
            "commence": g.get("commence"),
            "total": g["total_line"],
            "over_ml": g.get("over_ml"),
            "under_ml": g.get("under_ml"),
            "delta_avg": round(avg, 1),
            "delta_both": round(both, 1),
            "deltas": deltas,
            "qualifies": [name for name, fn in RULES.items() if fn(avg, both)],
        })

    with open(LEDGER, "a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")

    qual = sum(1 for r in rows if r["qualifies"])
    print(f"{date_iso} @ {captured_at}: captured {len(rows)} games "
          f"({skipped} skipped), {qual} qualifying")
    for r in rows:
        if r["qualifies"]:
            print(f"   {r['matchup']:<12} O{r['total']:<5} {r['over_ml']:+5d}  "
                  f"avg {r['delta_avg']:+6.1f}  both {r['delta_both']:+6.1f}  "
                  f"{','.join(r['qualifies'])}")


def _profit(odds):
    return odds / 100.0 if odds > 0 else 100.0 / abs(odds)


def report():
    """Grade price drift first, then the live record of each rule."""
    if not LEDGER.exists():
        print("ledger is empty — run --capture first")
        return
    rows = [json.loads(l) for l in open(LEDGER, encoding="utf-8") if l.strip()]
    if not rows:
        print("ledger is empty")
        return

    # Keep the FIRST capture per game: the earliest post-lineup price, which is
    # the one actually available when the signal became known.
    first = {}
    for r in sorted(rows, key=lambda r: r["captured_at"]):
        first.setdefault((r["date"], r["matchup"]), r)

    allml = S._load(S.ALL_ML)
    final = {}
    for g in allml.get("games", []):
        if g.get("home_score") is None:
            continue
        final[(g.get("date"), f"{g['away']} @ {g['home']}")] = {
            "runs": (g.get("home_score") or 0) + (g.get("away_score") or 0),
            "total": g.get("total_line"),
            "over_ml": g.get("over_ml"),
        }

    print(f"ledger: {len(rows)} snapshots, {len(first)} unique games, "
          f"{len({r['date'] for r in rows})} slates\n")

    # --- Phase 1: is the captured price the price the backtest graded? -------
    drift = [(k, v) for k, v in first.items()
             if k in final and final[k]["total"] is not None]
    print("PHASE 1 — price drift (captured at lineup time vs stored line)")
    if not drift:
        print("   no games have finals yet\n")
    else:
        moves = [final[k]["total"] - v["total"] for k, v in drift]
        same = sum(1 for m in moves if m == 0)
        print(f"   games compared      : {len(drift)}")
        print(f"   identical total     : {same} ({same / len(drift) * 100:.0f}%)")
        print(f"   mean signed move    : {st.mean(moves):+.3f} runs")
        print(f"   mean absolute move  : {st.mean(abs(m) for m in moves):.3f} runs")
        if len(moves) > 1:
            print(f"   sd of move          : {st.pstdev(moves):.3f}")
        print("   -> near zero means the backtest graded an obtainable price\n")

    # --- Phase 2: live record per rule --------------------------------------
    print("PHASE 2 — live record (graded at the CAPTURED price)")
    print(f"{'rule':<12}{'bets':>6}{'W-L-P':>10}{'win%':>8}{'units':>9}{'ROI':>8}")
    for name in RULES:
        picks = [v for k, v in first.items()
                 if name in v["qualifies"] and k in final
                 and v.get("over_ml") is not None]
        w = l = p = 0
        units = 0.0
        for v in picks:
            runs = final[(v["date"], v["matchup"])]["runs"]
            if runs == v["total"]:
                p += 1
            elif runs > v["total"]:
                w += 1
                units += _profit(v["over_ml"])
            else:
                l += 1
                units -= 1.0
        n = w + l
        wr = (w / n * 100) if n else 0.0
        roi = (units / len(picks) * 100) if picks else 0.0
        print(f"{name:<12}{len(picks):>6}{f'{w}-{l}-{p}':>10}{wr:>7.1f}%"
              f"{units:>+9.2f}{roi:>+7.1f}%")

    pending = sum(1 for k in first if k not in final)
    print(f"\npending (no final yet): {pending}")
    print("\nPre-committed decision points (see the Phase 2 plan):")
    print("   ~30 bets  — kill if under 45%")
    print("   ~100 bets — adopt if 58%+, kill if under 52%, else keep logging")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capture", action="store_true",
                    help="append a snapshot of today's slate to the ledger")
    ap.add_argument("--report", action="store_true",
                    help="grade price drift and the live record")
    args = ap.parse_args()
    if args.capture:
        capture()
    elif args.report:
        report()
    else:
        ap.error("pass --capture or --report")


if __name__ == "__main__":
    main()

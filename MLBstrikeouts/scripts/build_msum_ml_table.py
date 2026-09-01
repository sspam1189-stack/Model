#!/usr/bin/env python3
"""
build_msum_ml_table.py — the better-arm ML rule's season record, rebuilt daily.

The rule (found 2026-09-01 while asking what ELSE lives in the m_sum >= +40
pool that the dead form-over rule was sitting on):

    In games where both starters are outclassed by the bats they face
    (m_sum = away mismatch + home mismatch >= +40), back the team whose
    starter has the LOWER individual mismatch -- the better arm.

Why it is not just "back the favorite": inside the same pool, backing the
favorite LOSES (-1.9%). And the identical rule applied to every game outside
the pool is flat (-0.2%), so the m_sum filter is doing the selecting, not the
arm comparison alone. The reading: when both totals are inflated by bad-
pitching narratives, the market overprices the GAME and underprices which arm
is actually better.

The DOG subset is carried separately because that is where the edge
concentrates -- the better arm priced as an underdog.

Status: SHADOW. It is p=0.043 out of a session that scanned a great many
cells, and April ran -31%. A rule with better statistics than this one died
this morning when its sample got real (see build_flag_combo_table.py header).
15-20 live plays before it can bet.

Output: MLBstrikeouts/data/msum-ml-table.json (+ the dashboard copy)

Usage:  cd MLBstrikeouts && python -m scripts.build_msum_ml_table
"""
import json
import os
import sys
import datetime
import collections

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "sources"))

import backtest_mismatch as bm

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATHS = [
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "msum-ml-table.json")),
    os.path.normpath(os.path.join(
        SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "msum-ml-table.json")),
]

MSUM_AT = 40.0     # pool threshold: both arms outclassed by this much, summed
STATUS = "shadow"  # "shadow" (tracked, not bet) or "card"
SHADOW_TARGET = 20
# Scope narrowed to the DOG half (user, 2026-09-01): the rule only produces a
# play when the better arm is priced at plus money. Full pool 82-56 +9.3%,
# dogs 19-15 +26.0% (n=34), favorites 63-41 +3.8% (n=104) -- the favorite half
# is positive but thin edge, and the dog half is where the market disagreement
# actually is. Noted for the record: this is a split taken on 34 games, so the
# favorite half keeps being measured (splits.favorite) rather than discarded,
# and can be folded back in if the dog half does not hold up.
REQUIRE_DOG = True


def profit(ml, won):
    return (ml / 100.0 if ml > 0 else 100.0 / -ml) if won else -1.0


def build_rows():
    """Every gradeable game with both mismatches computed as-of its own date."""
    logs = bm._load(bm.GAME_LOGS)
    games = [g for g in bm._load(bm.ALL_ML)["games"]
             if g.get("home_score") is not None and g.get("away_score") is not None
             and g.get("away_pitcher") and g.get("home_pitcher")
             and g.get("home_ml") and g.get("away_ml")]
    games.sort(key=lambda g: g["date"])
    batter_rows = bm.load_batter_rows()
    asof = bm.AsOf(games, logs, batter_rows)

    lg_cache, out = {}, []
    for g in games:
        d = g["date"]
        if d not in lg_cache:
            lg_cache[d] = bm.league_woba_by_hand(batter_rows, d, bm.OFFENSE_DAYS)
        lg = lg_cache[d]
        ms, ok = {}, True
        for side in ("away", "home"):
            hand = g.get(f"{side}_hand")
            opp = g["home"] if side == "away" else g["away"]
            form = asof.starter_form(g[f"{side}_pitcher"], d)
            idx = (asof.offense_index(opp, d, hand, bm.OFFENSE_DAYS,
                                      bm.MIN_WINDOW_PA, lg.get(hand))
                   if hand else None)
            if form is None or idx is None:
                ok = False
                break
            ms[side] = ((idx - 100.0) + (form["era"] - 4.20) * 8.0
                        - (form["kbb"] - 13.7) * 1.2)
        if not ok:
            continue
        better = "away" if ms["away"] < ms["home"] else "home"
        pick = g[better]
        pick_ml = g[f"{better}_ml"]
        won = (g["home_score"] > g["away_score"]) == (pick == g["home"])
        out.append({
            "date": d,
            "msum": ms["away"] + ms["home"],
            "pick": pick,
            "ml": pick_ml,
            "is_dog": pick_ml > 0,
            "won": won,
            "p": profit(pick_ml, won),
            # control: the same game bet on the market favorite
            "p_fav": profit(min(g["home_ml"], g["away_ml"]),
                            (g["home_score"] > g["away_score"])
                            == (g["home_ml"] < g["away_ml"])),
        })
    return out


def summarize(rows, key="p"):
    if not rows:
        return None
    w = sum(1 for r in rows if (r["won"] if key == "p" else r[key] > 0))
    u = sum(r[key] for r in rows)
    return {"w": w, "l": len(rows) - w, "n": len(rows),
            "units": round(u, 2), "roi": round(u / len(rows) * 100, 1)}


def main():
    rows = build_rows()
    pool_all = [r for r in rows if r["msum"] >= MSUM_AT]
    pool = [r for r in pool_all if r["is_dog"]] if REQUIRE_DOG else pool_all
    outside = [r for r in rows if r["msum"] < MSUM_AT]
    dogs = [r for r in pool_all if r["is_dog"]]
    favs = [r for r in pool_all if not r["is_dog"]]

    by_month = {}
    for r in pool:
        by_month.setdefault(r["date"][:7], []).append(r)

    blob = {
        "sport": "MLB",
        "type": "msum-ml-table",
        "generated": datetime.datetime.now(datetime.timezone.utc)
                             .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "span": {"from": rows[0]["date"], "to": rows[-1]["date"]} if rows else {},
        "threshold": MSUM_AT,
        "status": STATUS,
        "require_dog": REQUIRE_DOG,
        "shadow_target": SHADOW_TARGET,
        "rule": ("m_sum >= +%g: back the team whose starter has the LOWER "
                 "mismatch (the better arm)%s." % (MSUM_AT,
                 ", and only when that team is a plus-money dog"
                 if REQUIRE_DOG else "")),
        "note": ("Mismatches computed as-of each game date from the pitcher "
                 "logs and the batter PA logs, graded at the game's own "
                 "moneyline. SHADOW: p=0.043 out of a heavily scanned session, "
                 "April ran -31%, and a better-looking rule died the same "
                 "morning when its sample got real."),
        "splits": {
            "rule": summarize(pool),
            "dog": summarize(dogs),
            "favorite": summarize(favs),
            "pool_all": summarize(pool_all),
            "control_back_favorite": summarize(pool_all, "p_fav"),
            "outside_pool": summarize(outside),
        },
        "monthly": [{"month": m, **(summarize(v) or {})}
                    for m, v in sorted(by_month.items())],
    }

    for path in OUTPUT_PATHS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(blob, fh, indent=1)
    s = blob["splits"]
    print(f"msum-ml table: pool {s['rule']['n']} games "
          f"({s['rule']['w']}-{s['rule']['l']} {s['rule']['roi']:+.1f}%), "
          f"dogs {s['dog']['n']} ({s['dog']['roi']:+.1f}%), "
          f"favorite control {s['control_back_favorite']['roi']:+.1f}% "
          f"-> {len(OUTPUT_PATHS)} paths")


if __name__ == "__main__":
    main()

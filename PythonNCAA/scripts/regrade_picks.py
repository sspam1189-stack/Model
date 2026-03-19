#!/usr/bin/env python3
# scripts/regrade_picks.py
# Re-applies current spread/total pick filters to all historical games
# without hitting any APIs. Uses existing projections (margin, pHomeCover, etc.)
# and re-grades against actual scores.

import re
import math
from store import load_store, save_store


def parse_spread_pick(pick):
    if not pick or pick == "PASS":
        return None
    m = re.match(r"(.+?)\s+([+-])(\d+(?:\.\d+)?)", pick)
    return {"team": m.group(1).strip(), "sign": m.group(2), "pts": float(m.group(3))} if m else None


def grade_spread(g):
    p = parse_spread_pick(g.get("sPick"))
    if not p:
        return None
    chosen_is_home = p["team"] == g.get("home")
    margin = (g["homeScore"] - g["awayScore"]) if chosen_is_home else (g["awayScore"] - g["homeScore"])
    val = margin + p["pts"] if p["sign"] == "+" else margin - p["pts"]
    if val == 0:
        return "PUSH"
    return "WIN" if val > 0 else "LOSS"


def grade_total(g):
    if not g.get("oPick") or g["oPick"] == "PASS":
        return None
    actual = g["homeScore"] + g["awayScore"]
    if actual == g.get("total"):
        return "PUSH"
    if g["oPick"] == "OVER":
        return "WIN" if actual > g["total"] else "LOSS"
    return "WIN" if actual < g["total"] else "LOSS"


SDIFF_MIN = 3
SDIFF_CAP = 12
P_COVER_THRESH = 0.65
FAV_LINE_CAP = 7


def re_pick_spread(g):
    p_home_cover = g.get("pHomeCover")
    p_away_cover = g.get("pAwayCover")
    if p_home_cover is None or p_away_cover is None:
        return

    s_diff = g.get("sDiff", 0)
    best_p = max(p_home_cover, p_away_cover)
    side = "home" if p_home_cover >= p_away_cover else "away"

    abs_line = abs(g.get("line", 0))
    home_fav = g.get("line", 0) > 0

    # Dog/fav detection
    is_dog = (side == "home" and g.get("line", 0) < 0) or (side == "away" and g.get("line", 0) > 0)
    line_ok = True if is_dog else abs_line <= FAV_LINE_CAP

    if best_p >= P_COVER_THRESH and SDIFF_MIN <= s_diff <= SDIFF_CAP and line_ok and abs_line > 0:
        if side == "home":
            g["sPick"] = f"{g['home']} -{abs_line}" if home_fav else f"{g['home']} +{abs_line}"
        else:
            g["sPick"] = f"{g['away']} +{abs_line}" if home_fav else f"{g['away']} -{abs_line}"
        g["sConf"] = "elite"
        g["pCover"] = best_p
    else:
        g["sPick"] = "PASS"
        g["sConf"] = "low"
        g["pCover"] = None


def re_pick_total(g):
    p_over = g.get("pOver")
    p_under = g.get("pUnder")
    if p_over is None or p_under is None:
        return
    # Total picks disabled - edge not holding
    g["oPick"] = "PASS"
    g["oConf"] = "low"
    g["pOU"] = None


# -- Main ----------------------------------------------------------------------

def main():
    store = load_store()
    runs = store.get("runs", {})

    total_games = 0
    s_wins, s_losses, s_pushes, s_picks = 0, 0, 0, 0
    o_wins, o_losses, o_picks = 0, 0, 0
    old_s_picks, old_s_wins, old_s_losses = 0, 0, 0

    for key, run in (runs.items() if isinstance(runs, dict) else enumerate(runs)):
        r = run if isinstance(runs, dict) else run
        if not r.get("games"):
            continue
        for g in r["games"]:
            # Count old record
            if g.get("sResult") == "WIN":
                old_s_wins += 1
                old_s_picks += 1
            elif g.get("sResult") == "LOSS":
                old_s_losses += 1
                old_s_picks += 1

            if g.get("homeScore") is None or g.get("awayScore") is None:
                continue
            if g.get("line") is None and g.get("total") is None:
                continue

            total_games += 1

            re_pick_spread(g)
            re_pick_total(g)

            if g.get("sPick") and g["sPick"] != "PASS":
                g["sResult"] = grade_spread(g)
                s_picks += 1
                if g["sResult"] == "WIN":
                    s_wins += 1
                elif g["sResult"] == "LOSS":
                    s_losses += 1
                elif g["sResult"] == "PUSH":
                    s_pushes += 1
            else:
                g["sResult"] = None

            if g.get("oPick") and g["oPick"] != "PASS":
                g["oResult"] = grade_total(g)
                o_picks += 1
                if g["oResult"] == "WIN":
                    o_wins += 1
                elif g["oResult"] == "LOSS":
                    o_losses += 1
            else:
                g["oResult"] = None

    save_store(store)

    s_pct = f"{(s_wins / (s_wins + s_losses) * 100):.1f}" if s_picks > 0 and (s_wins + s_losses) > 0 else "N/A"
    o_pct = f"{(o_wins / (o_wins + o_losses) * 100):.1f}" if o_picks > 0 and (o_wins + o_losses) > 0 else "N/A"
    old_pct = f"{(old_s_wins / (old_s_wins + old_s_losses) * 100):.1f}" if old_s_picks > 0 and (old_s_wins + old_s_losses) > 0 else "N/A"

    print(f"\n{'=' * 58}")
    print(f"  REGRADE COMPLETE (dogs only, pCover >= {P_COVER_THRESH}, sDiff {SDIFF_MIN}-{SDIFF_CAP})")
    print(f"{'=' * 58}")
    print(f"  Games processed: {total_games}")
    print(f"{'-' * 58}")
    print(f"  OLD SPREAD RECORD:  {old_s_wins}-{old_s_losses} ({old_pct}%)  n={old_s_picks}")
    print(f"  NEW SPREAD RECORD:  {s_wins}-{s_losses}-{s_pushes} ({s_pct}%)  n={s_picks}")
    print(f"  TOTAL RECORD:       {o_wins}-{o_losses} ({o_pct}%)  n={o_picks}")
    print(f"{'=' * 58}\n")


if __name__ == "__main__":
    main()

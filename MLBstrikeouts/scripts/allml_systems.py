#!/usr/bin/env python3
"""
allml_systems.py — the non-scout systems, computed from mlb-all-ml.json alone.

Everything else in the scout tier reads the mismatch model: pitcher form, the
batter PA logs, the defect flags. These rules read none of it. They come
out of a full scan of the 2,066 settled games in mlb-all-ml.json (2026-09-01)
using only what that file carries -- moneylines, totals, probables, scores,
dates -- plus what can be derived as-of from its own history: a starter's
trailing over-rate, his team's trailing ROI in his starts, a team's losing
streak, who it played last.

That independence is the point of grouping them separately on the tab and in
the ledger. When Flag Plays and Form under agree it is partly because they read
the same inputs; when one of these agrees with them it is a second opinion.

CARDED 2026-09-01 (user), without a shadow period. Eight were carded; Low
line over and Under juice were removed the same day, before either settled a
play (see RETIRED below).

HOW THEY WERE FOUND, stated plainly because it bears on how much to trust them.
The scan tested every single and pairwise cell over ~30 derived features,
roughly 4,000 cells per market. About 2,000 beat their baseline per market and
~100 cleared p<0.05 on chance alone; none survived Benjamini-Hochberg (best
q ~= 0.18). So p-values here are screening statistics, not proof. What each
rule below had to also do: beat the blind baseline for its market, hold up in
both walk-forward halves, ladder sensibly rather than spike in one bucket, and
have a reason to exist. Two of the six fail the ladder test and are carded
anyway on the user's call -- they are marked LADDER FAILS and carry the
numbers against them, so the record can settle it.

Baselines for reference: back every favorite -3.1%, back every dog -3.4%,
blind under -3.5%, blind over -6.1%.

Usage:  cd MLBstrikeouts && python -m scripts.allml_systems      (self-test)
"""
import collections
import datetime
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ALLML = os.path.normpath(os.path.join(HERE, "..", "data", "mlb-all-ml.json"))

MIN_STARTS = 5          # prior starts before a pitcher's trailing form is used
PIT_WINDOW = 8          # trailing starts in the over-rate and ROI windows

# key -> (display name, market, one-line rule, the season case)
SYSTEMS = {
    "pickem-under": (
        "Pickem under", "totals",
        "Favorite at -115 or shorter and total 8.5 or higher: under.",
        "98-69 +11.5% (n=167, p=0.017), halves +7/+14, all thirds positive. "
        "Ladders in favorite size -- pickem +11.5%, -116..-149 -6.7%, "
        "-150..-179 -12.8%, -180+ -13.7% -- and in total, +11.5% at 8.5+ and "
        "+17.2% at 9+. The read: an evenly-priced game with a big number is "
        "the market pricing two offenses it cannot separate."),
    "starter-over-run": (
        "Starter over run", "totals",
        "Either starter has gone over in 75% or more of his last 8 starts "
        "and the total is 8.5 or higher: over.",
        "120-87 +10.8% (n=207, p=0.004), halves +13/+10, all thirds positive. "
        "The L10 window agrees (+8.3%). Same story as the flag rules -- the "
        "totals market is slow to move on a starter's run. Weak point: "
        "monthly alternates (+27/-8/+20/-5/+9). The total>=8.5 half is what "
        "carries it; under 8 the same signal is -3.6%."),
    "cold-arms-under": (
        "Cold arms under", "totals",
        "Both starters have gone over in 35% or less of their last 8: under.",
        "27-14 +23.5% (n=41, p=0.038), halves +28/+20, every month positive. "
        "The smallest sample of the eight and the one most likely to be "
        "noise on size alone; kept because it is the exact mirror of Starter "
        "over run and agrees with it."),
    "away-dog-ml": (
        "Away dog ML", "h2h",
        "Away team priced as a dog in a game with a total of 9.5 or higher.",
        "LADDER FAILS. 87-76 +23.4% (n=163, p<0.001) at plus money; scored "
        "as not-the-favorite, 102-85 +23.4% on 187 plays. Halves +24/+23, five "
        "of six months positive, and it survives every robustness test I ran: "
        "removing the three biggest wins leaves +26.6%, and excluding "
        "Coors/GABP/Fenway leaves +16.6% (p=0.007), so it is not a park cell. "
        "What is against it: the whole effect is the 9.5 bucket. By line, "
        "away dogs run -5.2% at 7.5, -11.7% at 8.5, -15.4% at 9.0 (n=168, "
        "MORE games than the winning cell), +31.4% at 9.5, +6.1% at 10+. "
        "There is no story where a market is calibrated at 9.0, badly wrong "
        "at 9.5, and calibrated again at 10. Under the search that found it "
        "the false-discovery rate is about 47%. The smooth version of the "
        "same idea, away dog at total >= 9, is +5.1% (n=355, p=0.052)."),
    "home-slide-ml": (
        "Home slide ML", "h2h",
        "Home team on a 4-game losing streak or longer, facing a different "
        "opponent than its last game.",
        "LADDER FAILS. 33-17 +25.0% (n=50, p=0.027), halves +25/+25, all "
        "thirds and all five months positive (+19/+28/+31/+35/+14). The "
        "new-opponent condition is what makes it behave -- on a new opponent "
        "the streak ladder runs L2+ -4.7%, L3+ +13.0%, L4+ +25.0%. What is "
        "against it: the parent cell is entirely 'exactly L4' (+32.2%, n=66) "
        "with L3 at -9.4% (n=118) and L5 at -6.4% (n=29). The market prices "
        "L2, L3 and L5 teams within a few points of their true win rate and "
        "misses L4 teams by 15.9 points; the favorite half of that cell is "
        "27-6, an 81.8% win rate against a 58.6% price. Series length does "
        "make L4 structurally different (98% of L4 streaks span two or more "
        "opponents against 67% at L3), but that cannot be the cause: L5 "
        "streaks span multiple opponents 100% of the time and lose money, "
        "and splitting L4 by the same variable leaves both halves winning."),
}
CARD_ORDER = ("away-dog-ml", "home-slide-ml",
              "pickem-under", "starter-over-run", "cold-arms-under")

# REMOVED 2026-09-01 (user), the same day they were carded, before either had
# a settled play. Kept here rather than deleted so the numbers are not
# rediscovered later and mistaken for something new. Their branches in
# plays_for() are inert: add() drops any rule not in SYSTEMS.
#
#   low-line-over   total <= 7 -> over. 72-52 +8.9% (n=124, p=0.033), halves
#                   +10/+7, all thirds positive. Posted lines of 7 returned a
#                   mean actual total of 8.66. Its neighbours are the problem:
#                   7.5 runs -9.4% over 457 games, so this was the 7-and-under
#                   cell rather than a low-total trend.
#   under-juice     under priced <= -120 -> under. 234-171 +5.1% (n=405,
#                   p=0.020), halves +5/+6, monotone through the juice ladder
#                   (-112 -0.8%, -115 -1.2%, -118 +2.6%, -120 +5.1%). The
#                   biggest sample of the eight and the smallest edge per
#                   play, at 2.6 plays a day -- the most volume for the least
#                   conviction, and it is close to just following the book.
#   hot-arm-dog-ml  back a plus-money side whose starter's team is +40% or
#                   better over his last 8 starts. 91-90 +12.7% (n=181,
#                   p=0.010), halves +17/+11, and the threshold ladder was
#                   monotone. Removed 2026-09-01 (user) before it settled a
#                   play: at 91-90 the win rate is a coin flip and the entire
#                   return is the plus-money prices holding up, which 181
#                   plays at an even record is thin evidence for. A dog system
#                   has to beat its price, and this one only matches it.
RETIRED = ("low-line-over", "under-juice", "hot-arm-dog-ml")

HOT_ARM_ROI = 40.0       # trailing team ROI% in the starter's last 8
PICKEM_ML = -115         # favorite no shorter than this
PICKEM_TOTAL = 8.5
OVER_RUN_RATE = 0.75
OVER_RUN_TOTAL = 8.5
LOW_LINE = 7.0
COLD_ARMS_RATE = 0.35
UNDER_JUICE_ML = -120
AWAY_DOG_TOTAL = 9.5
HOME_SLIDE_LOSSES = 4


def short_tag(key):
    """Ledger tag for a moneyline play: "Hot arm dog ML" -> "hot arm dog"."""
    name = SYSTEMS[key][0]
    return (name[:-3].strip() if name.endswith(" ML") else name).lower()


def _profit(ml, won):
    return (ml / 100.0 if ml > 0 else 100.0 / -ml) if won else -1.0


def _date(s):
    return datetime.date.fromisoformat(s)


def load(path=None):
    with open(path or ALLML, encoding="utf-8") as fh:
        return json.load(fh)


def _settled(blob):
    games = [g for g in blob.get("games", [])
             if g.get("home_score") is not None and g.get("away_score") is not None
             and g.get("home_ml") and g.get("away_ml")
             and g.get("total_line") is not None]
    games.sort(key=lambda g: (g["date"], g.get("commence") or ""))
    return games


class AsOf:
    """Trailing form for every team and starter, replayed in date order.

    Nothing here reads a game's own result before it is used: `features(g)` is
    called first, then `absorb(g)` files it. That ordering is the whole
    correctness argument for the season records these systems report.
    """

    def __init__(self):
        self.team = collections.defaultdict(list)
        self.pit = collections.defaultdict(list)

    def features(self, g):
        out = {}
        for side in ("away", "home"):
            other = "home" if side == "away" else "away"
            f = {}
            hist = self.team[g[side]]
            if hist:
                last = hist[-1]
                k = 0
                for x in reversed(hist):
                    if x["won"] == last["won"]:
                        k += 1
                    else:
                        break
                f["streak"] = k if last["won"] else -k
                f["last_opp"] = last["opp"]
            plog = self.pit[g.get(f"{side}_pitcher") or ""]
            if len(plog) >= MIN_STARTS:
                w = plog[-PIT_WINDOW:]
                f["pit_roi"] = sum(x["p"] for x in w) / len(w) * 100.0
                ov = [x["over"] for x in w if x["over"] is not None]
                f["pit_over"] = (sum(ov) / len(ov)) if ov else None
            out[side] = f
        return out

    def absorb(self, g):
        total = g["away_score"] + g["home_score"]
        line = g["total_line"]
        over = None if total == line else (1 if total > line else 0)
        home_won = g["home_score"] > g["away_score"]
        for side in ("away", "home"):
            other = "home" if side == "away" else "away"
            won = home_won if side == "home" else not home_won
            rec = {"date": g["date"], "won": won, "opp": g[other], "over": over,
                   "p": _profit(g[f"{side}_ml"], won)}
            self.team[g[side]].append(rec)
            if g.get(f"{side}_pitcher"):
                self.pit[g[f"{side}_pitcher"]].append(rec)


def plays_for(g, feat):
    """Every system firing on one game: list of dicts, at most one per rule.

    `g` needs the market fields and probables; `feat` is AsOf.features(g).
    Works identically on a settled game and on tonight's unplayed one, which
    is why the season record and the daily card cannot drift apart.
    """
    out = []
    a, h = feat.get("away") or {}, feat.get("home") or {}
    total = g.get("total_line")
    over_ml, under_ml = g.get("over_ml"), g.get("under_ml")
    away_ml, home_ml = g.get("away_ml"), g.get("home_ml")

    def add(rule, market, pick, price, side=None, why=""):
        if price is None or rule not in SYSTEMS:
            return
        out.append({"rule": rule, "market": market, "pick": pick,
                    "price": int(price), "side": side, "why": why})

    # ---- moneyline ----------------------------------------------------
    if away_ml and home_ml:
        fav_ml = min(away_ml, home_ml)
        for side in ("away", "home"):
            f = a if side == "away" else h
            ml = away_ml if side == "away" else home_ml
            roi = f.get("pit_roi")
            if roi is not None and roi >= HOT_ARM_ROI and ml > 0:
                add("hot-arm-dog-ml", "h2h", g[side], ml,
                    why=f"{g.get(side + '_pitcher')} — team {roi:+.0f}% "
                        f"over his last {PIT_WINDOW} starts, priced +{ml}")
        if total is not None and total >= AWAY_DOG_TOTAL and away_ml > 0:
            add("away-dog-ml", "h2h", g["away"], away_ml,
                why=f"away dog at a {total:g} total")
        st = h.get("streak")
        if st is not None and st <= -HOME_SLIDE_LOSSES \
                and h.get("last_opp") and h["last_opp"] != g["away"]:
            add("home-slide-ml", "h2h", g["home"], home_ml,
                why=f"home on L{-st}, new opponent (last faced "
                    f"{h['last_opp']})")

    # ---- totals -------------------------------------------------------
    if total is None:
        return out
    if away_ml and home_ml and min(away_ml, home_ml) >= PICKEM_ML \
            and total >= PICKEM_TOTAL:
        add("pickem-under", "totals", "under", under_ml, side="U",
            why=f"favorite only {min(away_ml, home_ml):+d} at a {total:g} total")
    hot = [(s, (a if s == "away" else h).get("pit_over")) for s in ("away", "home")]
    hot = [(s, r) for s, r in hot if r is not None]
    if hot and total >= OVER_RUN_TOTAL and max(r for _, r in hot) >= OVER_RUN_RATE:
        who = ", ".join(f"{g.get(s + '_pitcher')} {r:.0%}"
                        for s, r in hot if r >= OVER_RUN_RATE)
        add("starter-over-run", "totals", "over", over_ml, side="O",
            why=f"over in {who} of last {PIT_WINDOW}, total {total:g}")
    if total <= LOW_LINE:
        add("low-line-over", "totals", "over", over_ml, side="O",
            why=f"posted total {total:g}; lines of 7 and under average 8.66 runs")
    if len(hot) == 2 and max(r for _, r in hot) <= COLD_ARMS_RATE:
        add("cold-arms-under", "totals", "under", under_ml, side="U",
            why="both starters "
                + ", ".join(f"{g.get(s + '_pitcher')} {r:.0%}" for s, r in hot)
                + f" over in last {PIT_WINDOW}")
    if under_ml is not None and under_ml <= UNDER_JUICE_ML:
        add("under-juice", "totals", "under", under_ml, side="U",
            why=f"book laying {under_ml} on the under")
    return out


def _graded(play, g):
    """(won, profit) for a settled game, or None on a push."""
    if play["market"] == "h2h":
        won = (g["home_score"] > g["away_score"]) == (play["pick"] == g["home"])
    else:
        total = g["away_score"] + g["home_score"]
        if total == g["total_line"]:
            return None
        won = (total > g["total_line"]) if play["pick"] == "over" \
            else (total < g["total_line"])
    return won, _profit(play["price"], won)


def replay(blob=None):
    """Full-season as-of replay: rule -> list of graded rows."""
    blob = blob or load()
    asof = AsOf()
    rows = collections.defaultdict(list)
    for g in _settled(blob):
        for play in plays_for(g, asof.features(g)):
            got = _graded(play, g)
            if got is not None:
                rows[play["rule"]].append({"date": g["date"], "won": got[0],
                                           "p": got[1], "pick": play["pick"],
                                           "price": play["price"]})
        asof.absorb(g)
    return rows


def today_plays(blob=None):
    """Tonight's qualifiers, with the same feature state the replay ends on."""
    blob = blob or load()
    asof = AsOf()
    for g in _settled(blob):
        asof.absorb(g)
    out = []
    for g in (blob.get("today") or []):
        if g.get("final"):
            continue
        for play in plays_for(g, asof.features(g)):
            play.update({"gamePk": g.get("gamePk"), "date": g.get("date"),
                         "commence": g.get("commence"),
                         "matchup": f"{g.get('away')} @ {g.get('home')}",
                         "away": g.get("away"), "home": g.get("home"),
                         "total": g.get("total_line")})
            out.append(play)
    out.sort(key=lambda p: (CARD_ORDER.index(p["rule"])
                            if p["rule"] in CARD_ORDER else 99,
                            str(p.get("commence") or "")))
    return out


def summarize(rows):
    if not rows:
        return {"w": 0, "l": 0, "n": 0, "units": 0.0, "roi": 0.0}
    u = sum(r["p"] for r in rows)
    w = sum(1 for r in rows if r["won"])
    return {"w": w, "l": len(rows) - w, "n": len(rows),
            "units": round(u, 2), "roi": round(u / len(rows) * 100, 1)}


def main():
    rows = replay()
    print(f"{'rule':18} {'record':>10} {'ROI':>8} {'units':>8}   monthly")
    for key in CARD_ORDER:
        s = summarize(rows.get(key, []))
        by = collections.defaultdict(list)
        for r in rows.get(key, []):
            by[r["date"][5:7]].append(r["p"])
        months = " ".join(f"{m}:{sum(v) / len(v) * 100:+.0f}"
                          for m, v in sorted(by.items()))
        print(f"{key:18} {s['w']:>4}-{s['l']:<5} {s['roi']:>+7.1f}% "
              f"{s['units']:>+7.2f}u   {months}")
    tp = today_plays()
    print(f"\ntonight: {len(tp)} plays")
    for p in tp:
        print(f"  {p['rule']:18} {p['matchup']:12} "
              f"{p['pick'] if p['market'] == 'h2h' else p['pick'].upper()} "
              f"{p['price']:>+5}  {p['why']}")


if __name__ == "__main__":
    main()

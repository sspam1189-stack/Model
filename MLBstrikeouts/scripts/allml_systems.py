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

# Division membership, for the division-home-dog rule. The only thing in this
# module that is not derivable from mlb-all-ml.json itself.
DIVISIONS = {
    "AL East": ("NYY", "BOS", "TB", "TOR", "BAL"),
    "AL Central": ("CLE", "MIN", "DET", "CWS", "KC"),
    "AL West": ("HOU", "SEA", "TEX", "LAA", "OAK"),
    "NL East": ("ATL", "PHI", "NYM", "MIA", "WSH"),
    "NL Central": ("MIL", "CHC", "STL", "CIN", "PIT"),
    "NL West": ("LAD", "SD", "SF", "ARI", "COL"),
}
DIVISION_OF = {t: d for d, ts in DIVISIONS.items() for t in ts}

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
    "division-home-dog": (
        "Division home dog", "h2h",
        "Home team hosting a division rival and priced +115 to +149: back the "
        "home side.",
        "LADDER FAILS. 46-27 +41.0% (n=73), halves positive, and it survives "
        "leave-one-division-out on all six divisions and leave-one-team-out "
        "with COL/LAA/WSH removed. Controls are the right shape: the same "
        "price band in NON-division games is +11.5% at +115-129 and -9.4% at "
        "+130-149, backing every home team is -3.5%, division home FAVOURITES "
        "are -0.3%, and the mirror (away side in division games) is -10.6%. A "
        "true 2/3-1/3 holdout came back 21-8 on the test period.\n\n"
        "        WHAT IS AGAINST IT, recorded because it is serious. The "
        "effect is a hump in price, not a trend: +100-114 runs -5.8% and "
        "+150-179 runs -11.9%, so the market is roughly right on both sides "
        "of a 73-game island -- structurally the same shape as the away-dog- "
        "at-9.5 cell. And the calibration gap is not physically plausible: at "
        "+115-129 these teams won 66.7% against a 45.4% price, a 21.3-point "
        "miss. When the L4 losing-streak cell was rejected, the stated reason "
        "was that its 15.9-point gap was larger than any liquid market "
        "leaves open; this is bigger. Against a market that is exactly right, "
        "46-27 has probability 0.0009 -- which sounds decisive until you "
        "count roughly 310 hand-built cells tested across six scanning "
        "passes on this file, where one result at p~0.001 is about what "
        "chance produces. Carded on the user's call 2026-09-02 at the "
        "+115..+149 band; the record settles it."),
    "home-dog-getaway": (
        "Home dog getaway", "h2h",
        "Day game, the visitors played a night game yesterday, and the home "
        "team is a plus-money dog: back the home side.",
        "56-50 +20.2% (n=106) at plus money, halves +21.9/+18.5 and all "
        "three thirds positive (+28.8/+18.2/+13.1).\n\n"
        "        WINDOW WIDENED 2026-09-02 (user). The night-before test was "
        "`hour >= 23` on a UTC hour, which reads as 7 pm ET or later but "
        "silently dropped every start from 8 pm ET on -- those are hour 0 in "
        "UTC and 0 >= 23 is false -- excluding a 23-22 +22.0% bucket of 45 "
        "games for no reason but arithmetic. The window is now minute-based "
        "and wraps: 22:30Z to 00:59Z, i.e. 6:30 pm to 8:59 pm ET. The old "
        "cell was 26-19 +26.0% on 45; the fix alone (7 pm+, wrap correct) "
        "gives 49-41 +24.0% on 90, and the user chose the 6:30 start. "
        "Dropping the lower bound the rest of the way to 6:00 pm ET adds 2 "
        "games and 0.20u, and extending past 00:59Z pulls in 9 pm ET starts "
        "at -7.2% over 68 games, which is where the edge dies.\n\n"
        "        Monthly at plus money on the original 7 pm+ cell ran "
        "+10/+19/+22/-14/+53, so July was negative -- the 'every month "
        "positive' reading belongs to the wider not-the-favourite cut, not "
        "to the version carded here. "
        "The mechanism is the cleanest of the six: after a night game both "
        "clubs are short on sleep, but one sleeps at home and the other packs "
        "for the airport, and the market prices team quality rather than that. "
        "It shows up where the home side is the weaker team and vanishes where "
        "it is the favourite (-0.2%).\n\n"
        "        Controls behave. The same day games WITHOUT the night-before "
        "condition return -4.9%, home dogs in day games not after a night "
        "game -1.4%, and leave-one-team-out holds at every one of the five "
        "most frequent hosts (+22% to +29%), with 15 distinct home teams over "
        "53 games and no team supplying more than 8. A 2/3-1/3 holdout "
        "trained +19.0% and tested +34.0%. The calibration gap is +11.6 "
        "points, and against an exactly-right market the record has "
        "probability 0.060 -- unremarkable, which is the point: the return "
        "comes from dog prices rather than from an implausible win rate.\n\n"
        "        Against it: the price ladder inside the cell is not clean "
        "(-120..-101 +22.8% on 8, pickem -5.1% on 17, +110..+139 +47.5% on "
        "24) and it is a three-way slice. Carded on the user's call "
        "2026-09-02."),
    "home-dog-under-parlay": (
        "Home dog + under", "parlay",
        "Home dog priced +115 to +149: parlay the home moneyline with the "
        "under, both legs at book price.",
        "83-165 +43.6% (n=248) at book odds on both legs, 1.58 plays a day. "
        "Excluding the 5 games where the total pushed and the parlay reduced "
        "to its moneyline leg, 82-161 +45.6% on 243 -- the figure the scan "
        "reported before push handling existed. "
        "Average payout 4.35x, so it needs a 23.0% hit rate and gets 33.7%. "
        "Halves +45.9/+45.3, every month positive, and leave-one-team-out "
        "holds at all six most frequent hosts across 26 distinct home teams.\n\n"
        "        THE MECHANISM IS REAL AND ITS MIRROR FAILS CORRECTLY, which "
        "is what separates this from the rest of the board. A home win means "
        "the bottom of the ninth is never played, so home wins skew under: "
        "the league total averages 8.77 when the home side wins against 9.15 "
        "when it loses. An away win carries no such property, and the away "
        "mirror is duly dead -- away dog +115..+149 parlayed with the under "
        "is -11.1% over 517 games, joint 0.205 against 0.213 independent. It "
        "is the first mirror in nine scanning passes to fail for the right "
        "reason rather than by construction.\n\n"
        "        The edge is NOT the moneyline leg repeating another system. "
        "In non-division games that leg alone is 77-95 +1.8%, flat, and the "
        "parlay still returns +40.5%: the return comes from the correlation, "
        "with these home dogs going under 72.7% of the time when they win "
        "against 51.6% league-wide for any home win.\n\n"
        "        AGAINST IT. The no-bottom-ninth effect is worth about two "
        "points league-wide and here it is twenty, and only inside one price "
        "band: the under rate when home dogs win runs 0.486 at +100..+114, "
        "0.717-0.742 at +115..+149, 0.500 at +150+. The under leg alone "
        "follows the same hump. The mechanism explains the direction and the "
        "mirror; it does not explain why +125 differs from +110. Variance is "
        "severe -- 13 straight losses and a -13.0u drawdown inside a "
        "recent 8-week stretch that finished +40u -- and plays cluster by "
        "series, so a bad matchup costs three units rather than one. Carded "
        "on the user's call 2026-09-02. NOTE: assumes the book prices a "
        "same-game ML+total parlay at multiplied odds; a correlation-priced "
        "parlay removes exactly the edge measured here."),
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
# Plain-English gloss for the tab: what the system bets, and why it might
# work. Where there is no mechanism, it says so -- three of these are carded
# on a measured cell with no story behind it, and the panel should not pretend
# otherwise.
PLAIN = {
    "away-dog-ml":
        "Backs the visiting underdog whenever the posted total is 9.5 or "
        "higher. No mechanism: it is a price-and-total cell that measured "
        "well, and 9.0 totals measure badly, so treat the record as the only "
        "evidence.",
    "home-slide-ml":
        "Backs a home team that has lost four or more in a row and is facing "
        "a different opponent than its last game. The idea is that the market "
        "over-adjusts to a visible losing streak. Weak support: the effect is "
        "entirely at exactly four losses, and three or five measure negative.",
    "division-home-dog":
        "Backs the home side when it hosts a division rival and is priced "
        "between +115 and +149. Familiar opponents in a narrow price window. "
        "The same price band outside the division is roughly flat, which is "
        "the whole case; below +115 and above +150 the market is right.",
    "home-dog-getaway":
        "Backs a home underdog in a day game when the visitors played a night "
        "game the day before. Both clubs are short on sleep, but one slept at "
        "home and the other packs for the airport. It only shows up when the "
        "home side is the weaker team, which is what makes it credible.",
    "home-dog-under-parlay":
        "Parlays a +115 to +149 home underdog with the under. When the home "
        "team wins it does not bat in the ninth, so home wins skew under -- "
        "these dogs go under 73% of the time when they win, against 52% "
        "league-wide. The away-side version is correctly dead, which is the "
        "strongest structural check on the board.",
    "pickem-under":
        "Takes the under when the game is priced close to even (favourite "
        "-115 or shorter) but the total is 8.5 or higher. Reads as the market "
        "unable to separate two offences and settling on a big number.",
    "starter-over-run":
        "Takes the over when either starter has gone over in 75% or more of "
        "his last eight starts and the total is 8.5 or higher. The totals "
        "market is slow to move on a starter's run -- the same lag the flag "
        "rules exploit.",
    "cold-arms-under":
        "Takes the under when BOTH starters have gone over in 35% or less of "
        "their last eight. The exact mirror of Starter over run, and it "
        "agrees with it, which is why a 41-game cell is on the board at all.",
}

CARD_ORDER = ("away-dog-ml", "home-slide-ml", "division-home-dog",
              "home-dog-getaway", "home-dog-under-parlay", "pickem-under",
              "starter-over-run", "cold-arms-under")

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
DIV_DOG_LO, DIV_DOG_HI = 115, 149   # the band the effect lives in
PARLAY_DOG_LO, PARLAY_DOG_HI = 115, 149
GETAWAY_DAY_HOUR = 20     # today's first pitch before 20Z is a day game
# Yesterday's night window, as minutes past midnight UTC, WRAPPING past
# midnight: 22:30Z (6:30 pm ET) through 00:59Z (8:59 pm ET).
#
# It has to wrap, and that is the whole reason this is not an hour compare.
# The test used to be `hour >= 23`, which reads as "7 pm ET or later" but
# silently dropped every start from 8 pm ET on -- those are hour 0 or 1 in
# UTC, and 0 >= 23 is false. That excluded a 23-22 +22.0% bucket of 45 games
# for no reason but arithmetic. The upper end stops before 01:00Z because
# 9 pm ET starts (mostly West-coast) measure -7.2% over 68 games and undo
# the edge.
GETAWAY_NIGHT_FROM = 22 * 60 + 30   # 22:30Z
GETAWAY_NIGHT_TO = 59               # 00:59Z, the morning side of the wrap
HOME_SLIDE_LOSSES = 4


def short_tag(key):
    """Ledger tag for a moneyline play: "Hot arm dog ML" -> "hot arm dog"."""
    name = SYSTEMS[key][0]
    return (name[:-3].strip() if name.endswith(" ML") else name).lower()


def to_american(dec):
    """Decimal payout -> American odds, so a parlay prices like any other row."""
    return int(round((dec - 1) * 100)) if dec >= 2 else int(round(-100 / (dec - 1)))


def _dec(ml):
    return (1 + ml / 100.0) if ml > 0 else (1 + 100.0 / -ml)


def _profit(ml, won):
    return (ml / 100.0 if ml > 0 else 100.0 / -ml) if won else -1.0


def _date(s):
    return datetime.date.fromisoformat(s)


def _hour(g):
    """First pitch hour in UTC, or None."""
    c = g.get("commence") or ""
    try:
        return int(c[11:13])
    except (ValueError, IndexError):
        return None


def _clock(g):
    """First pitch as minutes past midnight UTC, or None.

    Hour resolution is not enough for the getaway night window: the cut sits
    at 6:30 pm ET, which is 22:30Z, in the middle of an hour.
    """
    c = g.get("commence") or ""
    try:
        return int(c[11:13]) * 60 + int(c[14:16])
    except (ValueError, IndexError):
        return None


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
                f["last_date"] = last["date"]
                f["last_hour"] = last.get("hour")
                f["last_clock"] = last.get("clock")
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
                   "hour": _hour(g), "clock": _clock(g),
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

    def add(rule, market, pick, price, side=None, why="", **extra):
        if price is None or rule not in SYSTEMS:
            return
        out.append({"rule": rule, "market": market, "pick": pick,
                    "price": int(price), "side": side, "why": why, **extra})

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
        if (DIVISION_OF.get(g["away"]) is not None
                and DIVISION_OF.get(g["away"]) == DIVISION_OF.get(g["home"])
                and DIV_DOG_LO <= home_ml <= DIV_DOG_HI):
            add("division-home-dog", "h2h", g["home"], home_ml,
                why=f"home to a {DIVISION_OF[g['home']]} rival at +{home_ml}")
        if (PARLAY_DOG_LO <= home_ml <= PARLAY_DOG_HI
                and total is not None and under_ml is not None):
            payout = _dec(home_ml) * _dec(under_ml)
            add("home-dog-under-parlay", "parlay", g["home"],
                to_american(payout), line=total, ml_price=home_ml,
                under_price=under_ml, payout=round(payout, 3),
                why=f"{g['home']} ML +{home_ml} with U{total:g} "
                    f"{under_ml:+d}, pays {payout:.2f}x")
        today_hour = _hour(g)
        last_clock = a.get("last_clock")
        night_before = last_clock is not None and (
            last_clock >= GETAWAY_NIGHT_FROM or last_clock <= GETAWAY_NIGHT_TO)
        if (today_hour is not None and today_hour < GETAWAY_DAY_HOUR
                and home_ml > 0
                and night_before
                and a.get("last_date")
                and (_date(g["date"]) - _date(a["last_date"])).days == 1):
            add("home-dog-getaway", "h2h", g["home"], home_ml,
                why=f"{g['away']} played a night game yesterday, day game "
                    f"today, home dog at +{home_ml}")
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
    if play["market"] == "parlay":
        total = g["away_score"] + g["home_score"]
        ml_won = (g["home_score"] > g["away_score"]) == (play["pick"] == g["home"])
        if total == g["total_line"]:
            # Standard rule: a pushed leg drops out and the parlay reduces to
            # the surviving one. The backtest excluded pushes, so this path is
            # live-only and is why the recorded n is smaller than the pool.
            return ml_won, _profit(play["ml_price"], ml_won)
        won = ml_won and total < g["total_line"]
        return won, _profit(play["price"], won)
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
    # First pitch leads: the card reads top-to-bottom as the night unfolds,
    # so a game about to start is never buried under a later one. Games with
    # no commence time sink to the bottom; system order only breaks ties.
    out.sort(key=lambda p: (str(p.get("commence") or "~"),
                            CARD_ORDER.index(p["rule"])
                            if p["rule"] in CARD_ORDER else 99))
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

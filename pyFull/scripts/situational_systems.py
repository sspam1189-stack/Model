"""
situational_systems.py -- the NBA systems registry. Pre-registered, frozen.

READ THIS BEFORE CHANGING A NUMBER.

Every record below is IN-SAMPLE. These systems were found by screening ~3,600
conditions against a single season (2025-26, 1,320 games), and two tests say a
screen that size cannot separate signal from noise (validate_systems.py):

  PERMUTATION (300 shuffles; results reassigned between games, lines and
  situations held fixed): the screen returns 13 survivors where noise returns
  7.1 (P=0.050), and its best system at +30.9u is what noise produces one time
  in five (P=0.197). The only condition to beat the null on a price-free
  z-score was 'after ATS cover by 10+' at z=3.94 -- and it then collapsed out
  of sample. The candidates here sit at z=2.66 and below, against a noise
  distribution whose MAXIMUM averages 2.52.

  WALK-FORWARD (select on games before 2026-02-06, score after): 22 systems
  that cleared the bar on the first half went 915-908, -76.2u, -4.2% ROI on
  the second. Five of 22 stayed positive where chance predicts eleven.

The registry still freezes before a ball is tipped and every system grades
forward, so next season's out-of-sample record remains the thing that decides
whether any of this is real.

WHAT IS BET (2026-09-02): the seven candidates are CARDED. That decision was
taken at freeze, on the in-sample evidence above, without waiting for the
PROMOTION_MIN_PLAYS gate.

THE CONTROLS WERE DROPPED THE SAME DAY (see the note in SYSTEMS below). Five
known-junk conditions that cleared the identical ROI > 10% bar were carried so
the season could show whether the carded systems were distinguishable from
noise; they are gone. Nothing now separates "this edge is real" from "this
screen returns +14% on anything" except the forward record itself, read against
break-even rather than against a null.

TWO TIERS

  candidate  a stated mechanism, cleared ROI > 10% on 2025-26. All seven are
             carded and bet.
  reference  the model's own totals probability. Not a system and never bet
             from this tab -- the benchmark a situational system must beat to
             justify existing. It was among the few things in the screen that
             held up out of sample, which makes it the last comparison left.

MONEYLINE PRICING -- THE ONE PLACE THIS FILE PRINTS A NUMBER IT DID NOT OBSERVE

The odds feed carried no h2h market before 2026-09, so the ML systems have no
observed historical price. Their backtest units come from converting the spread
to a fair win probability and adding a hold, and the coefficients below are
FROZEN AT THE 2025-26 FIT so pricing cannot drift between seasons.

That conversion is known to be wrong in a specific, measurable way. Fit
in-sample, it should return about -2.25% in every spread bucket; instead it
returns -13.1% on small favourites and +7.3% on small dogs, a swing of twenty
points. Any ML system concentrated in one bucket inherits that error as fake
edge. bigdog_ml was the control that would have exposed exactly that and has
been dropped, so THREE carded systems (elite_dog_ml, rematch_dog_ml,
blowout_dog_ml) now settle on a price this file knows to be miscalibrated with
nothing left to measure the miscalibration against.

From 2026-09 the feed carries real h2h. Every live ML play therefore records
BOTH prices: `price` (converted, so the forward record stays comparable with
the backtest) and `book_price` (what was actually available). The gap between
them is the measurement this registry could never make before.
"""
import math

CARD, SHADOW = "card", "shadow"

BAR_ROI = 0.10             # shipping bar, applied to the tier's own pricing
PROMOTION_MIN_PLAYS = 25   # live plays before a shadow system may be promoted

# Status is per system and lives HERE -- the single source of truth. The ledger
# reads it when it logs a play; the dashboard reads it out of the feed. Edit
# this file, never either surface.
#
# 2026-09-02, user decision: the seven candidates were promoted to CARD at
# registry freeze, BEFORE any out-of-sample game existed. The 25-play gate
# below was written for exactly that moment and was not used. The controls stay
# SHADOW, so the candidate-versus-control comparison this registry was built
# around still runs -- it is now a comparison between money and no money rather
# than between two tracked groups.

# --- frozen spread -> moneyline conversion (2025-26 logistic fit) -----------
# P(win) = sigmoid(ML_FIT_A + ML_FIT_B * -spread), then half the hold per side.
# Do not refit. Re-fitting each season would silently reprice the entire
# forward record and make it incomparable with the backtest it is testing.
ML_FIT_A = 0.0
ML_FIT_B = 0.12983578423978293
ML_HOLD = 0.045


def ml_fair(spread):
    """Fair win probability for a team laid `spread` (negative = favourite)."""
    return 1.0 / (1.0 + math.exp(-(ML_FIT_A + ML_FIT_B * -spread)))


def ml_book_prob(spread):
    return min(0.995, max(0.005, ml_fair(spread) + ML_HOLD / 2))


def ml_american(spread):
    """The converted price as an American number, for display and logging."""
    p = ml_book_prob(spread)
    return round(-100.0 * p / (1.0 - p)) if p >= 0.5 else round(100.0 * (1.0 - p) / p)


def ml_payout(spread):
    """Net units won per 1u risked when this team wins its moneyline."""
    return (1.0 / ml_book_prob(spread)) - 1.0


# ---------------------------------------------------------------------------
def _elite_dog(r):
    """A .650+ SU team getting points. Needs 10 prior games, so it cannot fire
    before roughly day 21 of the season."""
    return (r["n_prior"] >= 10 and r["su_pct"] is not None
            and r["su_pct"] >= 0.65 and r["dog"])


def _blowout_dog(r):
    """A dog whose last game was a 15+ point win."""
    return r["dog"] and r["prev_margin"] is not None and r["prev_margin"] >= 15


def _rematch_dog(r):
    """A dog facing the same opponent it just played."""
    return r["dog"] and r["rematch"]


SYSTEMS = [
    # ---------------------------------------------------------------- CANDIDATES
    {
        "id": "elite_dog_ml", "tier": "candidate", "status": CARD, "market": "h2h", "side": "TEAM",
        "label": ".650+ SU team as a dog -> ML",
        "name": (
            "Elite dog ML"),
        "rule": (
            "Team SU win% >= .650 over 10+ prior games AND getting points "
            "(spread > 0) -> back that team on the moneyline."),
        "why": (
            "SU .650+ team getting points -> its ML. 54-52 +38.6% (n=106) at "
            "the CONVERTED price; price-free the wins beat a spread-matched "
            "baseline 54 vs 40.9 expected (z=+2.66), so the win rate is real "
            "even though the ROI is not observed. Cannot fire before ~day 21. "
            "Carded 2026-09-02."),
        "backtest": {"w": 54, "l": 52, "units": 41.0, "roi": 0.386, "z": 2.66,
                     "priced": "converted"},
        "mechanism": (
            "The market prices a good team's off night off its season number "
            "rather than off why it is a dog tonight. Price-free, these teams "
            "won 54 of 106 where teams laid the same number won 40.9 "
            "(z=+2.66), so the win rate is real even though the +38.6% is not "
            "observed. A .650-calibre team taking dog money is also exactly "
            "what public money shades, the one direction that would eat it. "
            "Fires from ~day 21."),
        "test": _elite_dog,
    },
    {
        "id": "rematch_dog_ml", "tier": "candidate", "status": CARD, "market": "h2h", "side": "TEAM",
        "label": "Dog facing the same opponent as last game -> ML",
        "name": (
            "Rematch dog ML"),
        "rule": (
            "Getting points (spread > 0) AND the opponent is the same team this "
            "side played in its previous game -> back the dog on the moneyline."),
        "why": (
            "Dog facing the team it just played -> its ML. 50-69 +30.5% (n=119) "
            "at the CONVERTED price. WEAKEST IN THE REGISTRY: price-free z is "
            "only +1.88 against a noise maximum averaging 2.52, and the ATS "
            "twin of the same trigger is -0.5%, so the number lives in the "
            "conversion rather than in the wins. First to drop. Carded "
            "2026-09-02."),
        "backtest": {"w": 50, "l": 69, "units": 36.3, "roi": 0.305, "z": 1.88,
                     "priced": "converted"},
        "mechanism": (
            "Back-to-back meetings, mostly playoff series and scheduling "
            "quirks: the market carries the first result forward harder than "
            "it should. WEAKEST CANDIDATE IN THE FILE -- price-free z is only "
            "+1.88 against a noise maximum averaging 2.52, and the ATS twin of "
            "this same trigger is -0.5%, which means the whole number lives in "
            "the conversion rather than in the wins. First to drop."),
        "test": _rematch_dog,
    },
    {
        "id": "blowout_dog_ml", "tier": "candidate", "status": CARD, "market": "h2h", "side": "TEAM",
        "label": "Dog off a 15+ point win -> ML",
        "name": (
            "Blowout dog ML"),
        "rule": (
            "Getting points (spread > 0) AND this side won its previous game by "
            "15 or more -> back it on the moneyline."),
        "why": (
            "Dog off a 15+ point win -> its ML. 80-100 +21.1% (n=180) at the "
            "CONVERTED price; price-free 80 wins against 63.6 expected "
            "(z=+2.63). Gradient by the size of the prior win rather than a "
            "threshold: after 20+ largest, 10-14 smaller, 1-9 inverts, after a "
            "loss firmly negative. Carded 2026-09-02."),
        "backtest": {"w": 80, "l": 100, "units": 38.0, "roi": 0.211, "z": 2.63,
                     "priced": "converted"},
        "mechanism": (
            "A gradient by size of the previous win rather than a threshold: "
            "after a 20+ win the price-free edge is largest, after a 10-14 win "
            "smaller, after a 1-9 win it inverts, after a loss it is firmly "
            "negative. A market that moves too little on a blowout makes "
            "exactly that shape."),
        "test": _blowout_dog,
    },
    {
        "id": "elite_dog_ats", "tier": "candidate", "status": CARD, "market": "spread", "side": "TEAM",
        "label": ".650+ SU team as a dog -> ATS",
        "name": (
            "Elite dog ATS"),
        "rule": (
            "Same trigger as Elite dog ML -- SU .650+ over 10+ games, getting "
            "points -- but take the points instead of the moneyline."),
        "why": (
            "SU .650+ team getting points -> ATS. 63-43 +13.5% (n=106) at a "
            "REAL -110, both walk-forward halves positive (+8.2u / +6.1u). The "
            "trustworthy half of the elite-dog pair: identical trigger, price "
            "that actually existed. Carded 2026-09-02."),
        "backtest": {"w": 63, "l": 43, "units": 14.3, "roi": 0.135, "z": 2.66,
                     "h1": 8.2, "h2": 6.1, "priced": "real -110"},
        "mechanism": (
            "The spread twin of elite_dog_ml, and the more trustworthy half: "
            "same trigger, but settled at a price that actually existed. When "
            "the two disagree next season, the difference is the conversion."),
        "test": _elite_dog,
    },
    {
        "id": "pickem_under", "tier": "candidate", "status": CARD, "market": "total", "side": "UNDER",
        "label": "Pick'em (|spread| <= 2) -> UNDER",
        "name": (
            "Pick'em under"),
        "rule": (
            "Game spread of 2 points or less either way (|spread| <= 2) -> "
            "UNDER."),
        "why": (
            "|spread| <= 2 -> under. 87-62 +11.5% (n=149, p=0.049), both halves "
            "positive. Over-rate is 41.6% here against 49-53% in every other "
            "bucket, averaging -1.85 points versus the number. The edge sits in "
            "the 1.5-2 slice (85-59) and decays to 73-61 at 2.5-3, so the "
            "threshold is doing real work. One of only 5 of 22 systems to "
            "survive the walk-forward test. Carded 2026-09-02."),
        "backtest": {"w": 87, "l": 62, "units": 17.1, "roi": 0.115, "p": 0.049,
                     "h1": 11.0, "h2": 6.1, "priced": "real -110"},
        "mechanism": (
            "Evenly-matched games grind: 41.6% over-rate at |spread| <= 2 "
            "against 49-53% in every other bucket, averaging -1.85 points "
            "versus the number. The edge sits in the 1.5-2 slice (85-59) and "
            "decays to 73-61 at 2.5-3, so the threshold is doing real work "
            "rather than being a cut point chosen after the fact. One of only "
            "five systems out of 22 to survive the walk-forward test."),
        "test": lambda r: r["is_home"] and abs(r["spread"]) <= 2,
    },
    {
        "id": "blowout_dog_ats", "tier": "candidate", "status": CARD, "market": "spread", "side": "TEAM",
        "label": "Dog off a 15+ point win -> ATS",
        "name": (
            "Blowout dog ATS"),
        "rule": (
            "Same trigger as Blowout dog ML -- a dog whose previous game was a "
            "15+ point win -- but take the points instead of the moneyline."),
        "why": (
            "Dog off a 15+ point win -> ATS. 105-75 +11.4% (n=180) at a REAL "
            "-110, both halves positive (+11.9u / +8.5u). The observed-price "
            "half of the blowout pair; if this pays and the ML twin does not, "
            "the price was the answer. Carded 2026-09-02."),
        "backtest": {"w": 105, "l": 75, "units": 20.5, "roi": 0.114, "z": 2.63,
                     "h1": 11.9, "h2": 8.5, "priced": "real -110"},
        "mechanism": (
            "The spread twin of blowout_dog_ml, settled at an observed price. "
            "If this pays next season and the ML twin does not, the price was "
            "the answer."),
        "test": _blowout_dog,
    },
    {
        "id": "home_dog_5_over", "tier": "candidate", "status": CARD, "market": "total", "side": "OVER",
        "label": "Home dog of +5 or more -> OVER",
        "name": (
            "Home dog over"),
        "rule": (
            "Home team getting 5 or more points (home spread >= +5) -> OVER."),
        "why": (
            "Home dog of +5 or more -> over. 166-120 +10.8% (n=286, p=0.008), "
            "both halves positive. The book lifts the total only 0.7 over "
            "average for these games and they land 3.7 over it, with the "
            "scoring coming from the ROAD FAVOURITE (121.7 points against a "
            "114.2 league average) rather than the home dog collapsing. Mirror "
            "test: home favourites of 5+ lean under at 250-262. CAUTION -- at "
            "+30.9u this is the screen's best system and the permutation null "
            "clears that mark one time in five. Carded 2026-09-02."),
        "backtest": {"w": 166, "l": 120, "units": 30.9, "roi": 0.108, "p": 0.008,
                     "h1": 13.9, "h2": 17.0, "priced": "real -110"},
        "mechanism": (
            "The book lifts the total only 0.7 over average for these games; "
            "they land 3.7 over it. The scoring comes from the ROAD FAVOURITE "
            "(121.7 points against a 114.2 league average), not from the home "
            "dog collapsing. Stronger at +8 or more (97-65). The mirror is the "
            "test: home favourites of 5+ lean UNDER at 250-262. Same shape as "
            "the day-mismatch OVER already registered in pyNFL -- an "
            "independent replication in another sport. CAUTION: at +30.9u this "
            "is the screen's best system, and the permutation null clears that "
            "mark 20% of the time."),
        "test": lambda r: r["is_home"] and r["spread"] >= 5,
    },

    # ---------------------------------------------------------- CONTROLS (DROPPED)
    # Removed 2026-09-02 by user decision, the same day the candidates were
    # carded. They were: dec_dog_ml (+20.3%), bigdog_ml (+13.1%),
    # sunday_home_ats (+13.0%), jan_under (+13.1%), cover10_ml (+11.2%) --
    # five known-junk conditions that cleared the identical ROI > 10% bar on
    # the identical data, carried so the season could answer whether the
    # carded systems were distinguishable from noise.
    #
    # WHAT THAT COSTS, recorded so nobody has to rediscover it: there is now
    # no null to compare the carded record against. A +14% in-sample edge and
    # a +14% in-sample artifact looked identical here, and the controls were
    # the only thing that could have separated them going forward.
    #
    # bigdog_ml is the specific loss. Its price-free z was +0.89 -- no edge in
    # the wins at all -- and it cleared the bar purely on the frozen
    # spread-to-moneyline conversion mispricing big dogs. It was the canary
    # for that conversion, which prices THREE of the seven carded systems
    # (elite_dog_ml, rematch_dog_ml, blowout_dog_ml). Without it, a profit on
    # those three cannot be told apart from the conversion being wrong.
    #
    # The measurements survive in validate_systems.py and can be re-run; the
    # controls can be restored from git history if the comparison is wanted.

    # ----------------------------------------------------------------- REFERENCE
    {
        "id": "model_over_60", "tier": "reference", "status": SHADOW, "market": "total", "side": "OVER",
        "label": "REF: model pOver >= .60 -> OVER",
        "name": (
            "Model over (benchmark)"),
        "rule": (
            "pyFull's own projected P(over) >= .60 -> OVER. Not situational."),
        "why": (
            "NOT A SYSTEM and never bet from this tab: the model's own totals "
            "probability, carried as the benchmark a situational system has to "
            "beat to justify existing. 67-46 +13.2% (n=113), and among the few "
            "things in the whole screen that held up out of sample (+4.5u where "
            "selected, +10.4u after). Shadow."),
        "backtest": {"w": 67, "l": 46, "units": 14.9, "roi": 0.132,
                     "h1": 4.5, "h2": 10.4, "priced": "real -110"},
        "mechanism": (
            "pyFull's own totals probability, not a situational system. Here "
            "as the benchmark: it was among the few things in the whole screen "
            "that held up out of sample (+4.5u selected, +10.4u after). A "
            "situational system that cannot beat this has no reason to be bet. "
            "Never carded from this tab -- the model has its own."),
        "test": lambda r: (r["is_home"] and r.get("_pOver") is not None
                           and r["_pOver"] >= 0.60),
    },
]

BY_ID = {s["id"]: s for s in SYSTEMS}
#: "control" is retained in the ordering only so ledger rows written before
#: the controls were dropped still sort and render.
TIERS = ("candidate", "control", "reference")
CONVERTED_PRICE_MARKETS = ("h2h",)


def evaluate(row):
    """Every system this team-row fires, in registry order."""
    out = []
    for s in SYSTEMS:
        try:
            if s["test"](row):
                out.append(s)
        except Exception:
            continue
    return out

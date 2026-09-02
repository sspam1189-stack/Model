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

So this file is not a betting product. It is a PRE-REGISTERED EXPERIMENT: the
registry freezes before a ball is tipped, every system logs and grades forward,
and next season's out-of-sample record -- not the backtest -- decides whether
any of it is real. Nothing is CARDED at open; everything is SHADOW until it
clears a promotion bar on games this registry never saw, by hand.

THREE TIERS, AND THE CONTROLS ARE THE POINT

  candidate  a stated mechanism, cleared ROI > 10% on 2025-26.
  control    KNOWN JUNK that also cleared ROI > 10%. If the candidates and the
             controls look alike next season, that is the answer and it needs
             no interpretation. Each control is chosen to be maximally
             embarrassing: sunday_home_ats has no mechanism and its Saturday
             mirror runs 68-101 the other way; jan_under earned all +30.5u in
             the first half and exactly 0.0u after; cover10_ml carries the
             highest price-free z in the entire screen (3.94) and still died
             out of sample; bigdog_ml has NO price-free edge at all (z=+0.89)
             and clears the bar purely on the conversion's own miscalibration.
  reference  the model's own totals probability. Not a system and never bet
             from this tab -- the benchmark a situational system must beat to
             justify existing. It was among the few things in the screen that
             held up out of sample.

MONEYLINE PRICING -- THE ONE PLACE THIS FILE PRINTS A NUMBER IT DID NOT OBSERVE

The odds feed carried no h2h market before 2026-09, so the ML systems have no
observed historical price. Their backtest units come from converting the spread
to a fair win probability and adding a hold, and the coefficients below are
FROZEN AT THE 2025-26 FIT so pricing cannot drift between seasons.

That conversion is known to be wrong in a specific, measurable way. Fit
in-sample, it should return about -2.25% in every spread bucket; instead it
returns -13.1% on small favourites and +7.3% on small dogs, a swing of twenty
points. Any ML system concentrated in one bucket inherits that error as fake
edge -- which is precisely what bigdog_ml is in the registry to expose.

From 2026-09 the feed carries real h2h. Every live ML play therefore records
BOTH prices: `price` (converted, so the forward record stays comparable with
the backtest) and `book_price` (what was actually available). The gap between
them is the measurement this registry could never make before.
"""
import math

BAR_ROI = 0.10             # shipping bar, applied to the tier's own pricing
PROMOTION_MIN_PLAYS = 25   # live plays before a shadow system may be promoted

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
        "id": "elite_dog_ml", "tier": "candidate", "market": "h2h", "side": "TEAM",
        "label": ".650+ SU team as a dog -> ML",
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
        "id": "rematch_dog_ml", "tier": "candidate", "market": "h2h", "side": "TEAM",
        "label": "Dog facing the same opponent as last game -> ML",
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
        "id": "blowout_dog_ml", "tier": "candidate", "market": "h2h", "side": "TEAM",
        "label": "Dog off a 15+ point win -> ML",
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
        "id": "elite_dog_ats", "tier": "candidate", "market": "spread", "side": "TEAM",
        "label": ".650+ SU team as a dog -> ATS",
        "backtest": {"w": 63, "l": 43, "units": 14.3, "roi": 0.135, "z": 2.66,
                     "h1": 8.2, "h2": 6.1, "priced": "real -110"},
        "mechanism": (
            "The spread twin of elite_dog_ml, and the more trustworthy half: "
            "same trigger, but settled at a price that actually existed. When "
            "the two disagree next season, the difference is the conversion."),
        "test": _elite_dog,
    },
    {
        "id": "pickem_under", "tier": "candidate", "market": "total", "side": "UNDER",
        "label": "Pick'em (|spread| <= 2) -> UNDER",
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
        "id": "blowout_dog_ats", "tier": "candidate", "market": "spread", "side": "TEAM",
        "label": "Dog off a 15+ point win -> ATS",
        "backtest": {"w": 105, "l": 75, "units": 20.5, "roi": 0.114, "z": 2.63,
                     "h1": 11.9, "h2": 8.5, "priced": "real -110"},
        "mechanism": (
            "The spread twin of blowout_dog_ml, settled at an observed price. "
            "If this pays next season and the ML twin does not, the price was "
            "the answer."),
        "test": _blowout_dog,
    },
    {
        "id": "home_dog_5_over", "tier": "candidate", "market": "total", "side": "OVER",
        "label": "Home dog of +5 or more -> OVER",
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

    # ------------------------------------------------------------------ CONTROLS
    {
        "id": "dec_dog_ml", "tier": "control", "market": "h2h", "side": "TEAM",
        "label": "CONTROL: December dog -> ML",
        "backtest": {"w": 74, "l": 124, "units": 40.2, "roi": 0.203, "z": 1.58,
                     "priced": "converted"},
        "mechanism": (
            "NONE. A calendar artifact: November dogs are -44.8u and March "
            "dogs -67.1u on the identical rule."),
        "test": lambda r: r["month"] == 12 and r["dog"],
    },
    {
        "id": "bigdog_ml", "tier": "control", "market": "h2h", "side": "TEAM",
        "label": "CONTROL: dog of +9.5 to +12 -> ML",
        "backtest": {"w": 47, "l": 134, "units": 23.7, "roi": 0.131, "z": 0.89,
                     "priced": "converted"},
        "mechanism": (
            "NONE, and this is the most useful control in the file. Its "
            "price-free z is +0.89 -- no edge in the wins at all -- yet it "
            "clears the bar at +13.1% purely because the frozen conversion "
            "misprices big dogs. If THIS system wins next season, the "
            "conversion is broken rather than the systems, and every "
            "converted-price line in the registry has to be re-read."),
        "test": lambda r: 9.5 <= r["spread"] <= 12,
    },
    {
        "id": "sunday_home_ats", "tier": "control", "market": "spread", "side": "TEAM",
        "label": "CONTROL: Sunday home team -> ATS",
        # one push, counted in the ROI denominator as a graded 0u play
        "backtest": {"w": 125, "l": 86, "push": 1, "units": 27.6, "roi": 0.130,
                     "p": 0.009, "priced": "real -110"},
        "mechanism": (
            "NONE, deliberately. Day-of-week is what a 30-cell search returns "
            "by chance, and Saturday home runs 68-101 the other way."),
        "test": lambda r: r["is_home"] and r["dow"] == 6,
    },
    {
        "id": "jan_under", "tier": "control", "market": "total", "side": "UNDER",
        "label": "CONTROL: January regular season -> UNDER",
        "backtest": {"w": 138, "l": 95, "units": 30.5, "roi": 0.131, "p": 0.006,
                     "h1": 30.5, "h2": 0.0, "priced": "real -110"},
        "mechanism": (
            "NONE. December is -10.9u and February -28.5u on the same rule. "
            "Every unit came from the first half of the season and precisely "
            "zero from the second -- the clearest example in the file of a "
            "number that means nothing."),
        "test": lambda r: r["is_home"] and r["month"] == 1 and not r["playoffs"],
    },
    {
        "id": "cover10_ml", "tier": "control", "market": "h2h", "side": "TEAM",
        "label": "CONTROL: after an ATS cover by 10+ -> ML",
        "backtest": {"w": 348, "l": 252, "units": 67.0, "roi": 0.112, "z": 3.94,
                     "h1": 69.7, "h2": -2.7, "priced": "converted"},
        "mechanism": (
            "NONE. Carries the highest price-free z in the entire screen "
            "(3.94, the only condition to beat the permutation null) and still "
            "died out of sample: +69.7u in the first half, -2.7u in the "
            "second. The registry's reminder that a big z on one season "
            "predicts nothing. Highest volume here at ~600 plays a season."),
        "test": lambda r: r["prev_ats_margin"] is not None and r["prev_ats_margin"] >= 10,
    },

    # ----------------------------------------------------------------- REFERENCE
    {
        "id": "model_over_60", "tier": "reference", "market": "total", "side": "OVER",
        "label": "REF: model pOver >= .60 -> OVER",
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

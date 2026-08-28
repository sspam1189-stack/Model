# pyNFL/scripts/situational_systems.py
# Registry of validated NFL situational betting systems.
#
# These are PRICING BIASES, not predictions. Every edge found in this repo
# came from the market mispricing a recognisable situation, never from a
# better projection -- see the engine_v2 header for how that search went.
#
# Screened ~88 candidate situations against CLOSING lines over 2023-2025.
# Shipping bar: raw hit rate >= 60% AND every individual season >= 55% AND
# an identifiable mechanism. Systems are DEDUPLICATED -- several of the
# survivors were nested subsets of each other (|spread|>=10 is a subset of
# >=7), so only the parent condition is registered and a game produces at
# most one pick per market.
#
# THE PROBABILITIES BELOW ARE NOT THE BACKTEST HIT RATES. Each is:
#   shrunk toward breakeven with a Beta prior of 100 pseudo-games (these
#   survived an 88-candidate screen, so raw rates are selection-inflated)
#   MINUS 3.5pp for line quality (our single book vs the closing consensus
#   these were measured against -- measured on the backup-QB system).
# Raw 61-67% becomes an honest 52-55%. Do not "fix" these upward.

BREAKEVEN = 0.524          # -110

# ---------------------------------------------------------------------------
# Mechanisms (why these aren't curve-fits)
# ---------------------------------------------------------------------------
# DAY-GAME MISMATCH OVER: the book sets essentially the same total (~44)
#   regardless of spread size, but mismatches outscore it -- residual by
#   spread bucket runs +0.08 / +1.01 / +2.28 / +2.33, a clean gradient
#   (garbage time, the dog throwing to catch up). It only works on DAY
#   games: identical situation in primetime is 45.5% because the market
#   prices its national-TV games sharply (primetime residual -0.25 vs day
#   +2.95). It's a market-attention effect, not a football effect.
# BACKUP-QB OVER: the book cuts the total 2.34 pts for a backup but scoring
#   RISES 1.31 -- a 3.64 pt over-cut. The backup's own team doesn't score
#   much less; the OPPONENT scores more (3-and-outs, short fields, tired
#   defence). Dies after week 13, where the book cuts 4.31 and scoring
#   really does drop 4.90 -- the market corrects its own error late season.
# LATE-SEASON COLD OVER: the market shades December totals down expecting
#   winter football; scoring doesn't drop (outdoors 43.8->42.8 line but
#   44.4->44.4 actual). Holds in domes too, so it isn't weather forecasting.
# HOME DOG 7-10 ATS: the most season-stable split found (64/67/65).
# DIVISIONAL REMATCH, HOME LOST MEETING 1: the market carries the first
#   meeting forward and underrates the loser now playing at home.

SYSTEMS = [
    {
        "id": "day_mismatch_over",
        "market": "total", "side": "OVER", "prob": 0.549,
        "desc": "day game, |spread| >= 7 -> OVER",
        # raw 61.8% (n=173, p=0.002, seasons 62/58/65) -- the anchor system:
        # most volume (~58/yr) and the only one at p<0.01. Subsumes the
        # >=10 (65.2%) and wk14-18 (66.7%) variants, which are the same
        # edge on fewer games.
        "test": lambda c: (not c.get("primetime")) and abs(c.get("spread", 0)) >= 7,
    },
    {
        "id": "backup_qb_over",
        "market": "total", "side": "OVER", "prob": 0.541,
        "desc": "day game, backup QB starting, weeks 1-13 -> OVER",
        # raw 65.6% (n=64, p=0.017, seasons 57/70/70)
        "test": lambda c: (not c.get("primetime")) and c.get("backup_qb")
                          and 1 <= c.get("week", 0) <= 13,
    },
    {
        "id": "late_cold_over",
        "market": "total", "side": "OVER", "prob": 0.522,
        "desc": "weeks 14-18, outdoors, <=35F -> OVER",
        # raw 64.1% (n=39, seasons 60/56/72). Smallest sample here and the
        # most likely of the set to be chance -- lowest confidence.
        "test": lambda c: (14 <= c.get("week", 0) <= 18) and (not c.get("dome"))
                          and c.get("temp") is not None and c["temp"] <= 35,
    },
    {
        "id": "home_dog_7_10",
        "market": "spread", "side": "HOME", "prob": 0.529,
        "desc": "home underdog of 7-10 -> HOME ATS",
        # raw 65.2% (n=46, seasons 64/67/65) -- the tightest season spread
        # of anything screened, but only ~15 games a year.
        "test": lambda c: 7 <= c.get("home_dog_pts", 0) < 10,
    },
    {
        "id": "rematch_home_lost_m1",
        "market": "spread", "side": "HOME", "prob": 0.527,
        "desc": "divisional rematch, home team lost meeting 1 -> HOME ATS",
        # raw 61.0% (n=77, seasons 58/61/63)
        "test": lambda c: bool(c.get("rematch")) and bool(c.get("home_lost_meeting1")),
    },
]


def evaluate(ctx):
    """
    Run every system against one game's context.

    ctx keys (all optional; a missing key simply fails that system's test):
        primetime            bool   TNF / SNF (Sun >= 20:00) / MNF
        spread               float  market home spread, NEGATIVE = home favoured
        home_dog_pts         float  points the home team is getting (0 if favoured)
        week                 int
        backup_qb            bool   either side starting a non-primary QB
        dome                 bool
        temp                 float  Fahrenheit, None if unknown
        rematch              bool   2nd meeting of these teams this season
        home_lost_meeting1   bool

    OVERLAP is fine and expected -- several systems can fire on one game and
    ALL of them are reported. What can't happen is two bets on the same
    market, so one system is designated the pick per market.

    CONFLICT (two systems on the same market pointing OPPOSITE ways, e.g. one
    OVER and one UNDER) is different from overlap: the systems disagree about
    direction, so neither is trustworthy on that game. Policy is STAND DOWN --
    no pick for that market, flagged in "conflicts". Agreement was measured
    and does NOT improve the hit rate (62.9% when 2+ fire vs 61.8% for the
    anchor system alone), so there is no case for sizing up on confluence
    either -- which is also why the winner is simply the highest-probability
    system rather than some blend.

    Returns
    -------
    dict
        {
          "total":     pick system or None,
          "spread":    pick system or None,
          "all":       [every id that fired],
          "by_market": {"total": [systems...], "spread": [systems...]},
          "conflicts": ["total", ...]   markets where systems disagreed
        }
    """
    fired = []
    for s in SYSTEMS:
        try:
            if s["test"](ctx):
                fired.append(s)
        except (TypeError, ValueError):
            continue
    out = {"total": None, "spread": None, "all": [s["id"] for s in fired],
           "by_market": {"total": [], "spread": []}, "conflicts": []}
    for market in ("total", "spread"):
        cands = [s for s in fired if s["market"] == market]
        out["by_market"][market] = cands
        if not cands:
            continue
        if len({s["side"] for s in cands}) > 1:
            out["conflicts"].append(market)     # disagree -> no pick
            continue
        out[market] = max(cands, key=lambda s: s["prob"])
    return out


def build_context(spread=None, week=None, primetime=None, backup_qb=False,
                  dome=False, temp=None, rematch=False, home_lost_meeting1=False):
    """Assemble a context dict, deriving home_dog_pts from the spread."""
    ctx = dict(spread=spread, week=week, primetime=bool(primetime),
               backup_qb=bool(backup_qb), dome=bool(dome), temp=temp,
               rematch=bool(rematch), home_lost_meeting1=bool(home_lost_meeting1))
    # spread convention here matches the engine: negative = home favoured,
    # so a positive spread means the home team is getting points.
    ctx["home_dog_pts"] = spread if (spread is not None and spread > 0) else 0.0
    return ctx

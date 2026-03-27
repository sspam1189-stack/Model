# scripts/defaults.py
# Starting weights for NCAA model. Self-tune adjusts ALL of these over time.

DEFAULT_STATS = {}

DEFAULT_W = {
    # Projection weights (means)
    "wTS": 1,           # true shooting / eFG% delta weight
    "wTO": 1,           # turnover rate delta weight
    "wORR": 1,          # offensive rebound rate delta weight
    "wNET": 1,          # net rating delta weight
    "constant": 0,      # additive constant
    "paceAdj": 1,       # pace multiplier (1.0 = neutral)
    "hca": 4.0,         # home court advantage (points) — higher in college

    # Legacy thresholds (fallback)
    "sprHigh": 4,
    "ouHigh": 5,
    "sprEliteBump": 3,
    "ouEliteBump": 3,

    # Bayesian probability thresholds
    "probHigh": 0.65,       # P(cover) threshold for spread picks
    "probHighFav": 0.65,    # (legacy, unused — single threshold now)
    "probElite": 0.67,
    "probOUHigh": 0.58,
    "probOUElite": 0.64,

    # Stats blending weights
    "recentWeight": 0.30,     # less weight on recent form (fewer games, noisier)
    "locationWeight": 0.20,   # less weight on home/away splits (smaller sample)
}

DEFAULT_W_VAR = {
    "wTS": 4.0,
    "wTO": 4.0,
    "wORR": 4.0,
    "wNET": 4.0,
    "hca": 2.0,
    "constant": 4.0,
}

BAYES_HYPER = {
    "marginNoise": 196,    # NCAA margins ~14 pt std dev -> 196 variance
    "totalNoise": 210,     # totals noisier in college
    "minWeightVar": 0.05,
    "maxWeightVar": 10,
    "residualVar": 130,    # higher irreducible noise in college
}

# --- Core engine config (used by core/model_engine.py) -------------------------
ENGINE_CONFIG = {
    "TEAM_NAME_ALIASES": {
        "ohio state": "Ohio St.",
        "ohio state buckeyes": "Ohio St.",
        "ohio bobcats": "Ohio",
        "oregon state": "Oregon St.",
        "oregon state beavers": "Oregon St.",
        "oregon ducks": "Oregon",
        "miami hurricanes": "Miami FL",
        "miami fl": "Miami FL",
        "miami florida": "Miami FL",
        "miami oh": "Miami OH",
        "miami ohio": "Miami OH",
        "miami redhawks": "Miami OH",
        "miami oh redhawks": "Miami OH",
        "penn state": "Penn St.",
        "penn state nittany lions": "Penn St.",
        "penn quakers": "Penn",
        "mississippi state": "Mississippi St.",
        "mississippi rebels": "Mississippi",
        "ole miss rebels": "Mississippi",
        "michigan state": "Michigan St.",
        "michigan state spartans": "Michigan St.",
        "michigan wolverines": "Michigan",
        "washington state": "Washington St.",
        "uconn huskies": "Connecticut",
        "washington huskies": "Washington",
        "iowa state": "Iowa St.",
        "iowa state cyclones": "Iowa St.",
        "iowa hawkeyes": "Iowa",
        "kansas state": "Kansas St.",
        "kansas state wildcards": "Kansas St.",
        "kansas jayhawks": "Kansas",
        "oklahoma state": "Oklahoma St.",
        "oklahoma sooners": "Oklahoma",
        "nc state": "N.C. State",
        "north carolina state": "N.C. State",
        "north carolina tar heels": "North Carolina",
        "colorado state": "Colorado St.",
        "fresno state": "Fresno St.",
        "san diego state": "San Diego St.",
        "boise state": "Boise St.",
        "arizona state": "Arizona St.",
        "arizona wildcats": "Arizona",
        "utah state": "Utah St.",
        "utah utes": "Utah",
        "florida state": "Florida St.",
        "florida gators": "Florida",
        "wichita state": "Wichita St.",
    },
    "MIN_GP": 5,                   # NCAA: fewer games needed (lower sample threshold)
    "USE_SAFE_FUZZY": True,        # NCAA: safe fuzzy matching (prevents "arkansas"/"kansas")
    "USE_COLLAPSE_ABBR": True,     # NCAA: collapse abbreviation dots ("N.C. State" -> "NC State")
}

# --- Self-tune HCA clamps (used by core/self_tune.py) -------------------------
HCA_CLAMP_LO  = 2.0              # NCAA HCA range is higher and more variable than NBA
HCA_CLAMP_HI  = 6.0
HCA_VAR_FLOOR = 0.3

# --- Kalman filter defaults (used by core/kalman_state.py) ---------------------
KALMAN_DEFAULTS = {
    "initialVar":  20,             # higher: 362 teams, more uncertain per team
    "gameNoise":   196,            # higher: ~14 pt std dev in college margins
    "dailyDrift":  0.15,
    "minVar":      2.0,
    "maxVar":      40,             # higher: teams can go weeks between games
}

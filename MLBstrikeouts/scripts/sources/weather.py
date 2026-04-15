"""
MLB weather data fetcher and adjustment engine.

Fetches game-day weather from MLB Stats API and computes
hit-rate multipliers based on temperature and park-specific wind effects.

Research basis (Alan Nathan, FanGraphs, Hardball Times):
  - Temperature: every 10°F changes fly ball distance ~3 feet.
    Below 55°F batted balls are significantly less likely to fall for hits.
    HR/FB rate drops ~20% in cold weather (4.0% at 75°F+ vs 3.2% cold).
    Strikeouts slightly increase in cold (weaker contact, less carry).
  - Wind: league-wide, wind direction is NOT statistically significant
    for hit rates. Only a handful of wind-sensitive parks show real effects
    (Wrigley, Guaranteed Rate, Kauffman). At Wrigley, wind out → 23.2%
    HR/FB vs wind in → 13.7% (p<.01). At most parks, effect is noise.
  - Humidity: negligible real effect (humid air is less dense, not more).
    Not worth modeling.
  - Threshold: 10 mph+ is when wind direction becomes meaningful at
    wind-sensitive parks. Below that, no adjustment.
"""

import json
import re
import time
import requests
from pathlib import Path
from sources.mlb_stats import _load_cache, _save_cache, CACHE_DIR, MLB_TEAM_ID_TO_ABBR

# Teams that play in domed / retractable-roof stadiums — weather irrelevant
DOMED_TEAMS = {"TB", "TEX", "MIA", "MIL", "HOU", "SEA", "ARI", "TOR"}

# Parks where wind direction has meaningful effects on hits/HR.
# Sources: Statcast Weather Applied Metrics (2023-24), Hardball Times,
#          MLB.com wind impact study, Alan Nathan physics research.
#
# Tier 1 — Statistically significant (p<.01) or dominant wind share:
#   CHC (Wrigley): 40%+ of all 25ft+ wind-affected balls league-wide.
#                  HR/FB: 13.7% wind in → 23.2% wind out.
#   CWS (Guaranteed Rate): HR/FB 15.5% wind in → 24.5% wind out (p<.01).
#   KC  (Kauffman): 67 prevented HRs (most in MLB), 69 total created/prevented.
#
# Tier 2 — Meaningful wind effects documented by Statcast:
#   NYM (Citi Field): 28 wind-created HRs (#1 in MLB for wind-aided HRs).
#   PHI (Citizens Bank): wind prevents HRs, open design exposes to wind.
#   MIN (Target Field): wind demonstrations documented, open design.
#   BOS (Fenway): documented wind effects on fly balls, unique wall angles.
#
# Tier 3 — Not wind-sensitive enough to model:
#   All other outdoor parks: wind effects are noise, not signal.

WIND_PARK_FACTOR = {
    # Tier 1 — heavy wind effect
    "CHC": 1.00,   # Wrigley — by far #1 wind-sensitive park in MLB
    "CWS": 0.85,   # Guaranteed Rate — significant (p<.01)
    "KC":  0.75,    # Kauffman — most wind-prevented HRs in MLB

    # Tier 2 — moderate wind effect
    "NYM": 0.55,   # Citi Field — most wind-created HRs in MLB
    "PHI": 0.45,   # Citizens Bank — open design, wind-prevented HRs
    "MIN": 0.40,   # Target Field — open design, documented wind effects
    "BOS": 0.35,   # Fenway — unique wall angles amplify wind effect
}

WIND_SENSITIVE_PARKS = set(WIND_PARK_FACTOR.keys())

# Altitude adjustment — thin air increases ball carry independent of weather.
# Coors Field: ball carries ~7.5% further than avg (~30 extra ft on HR).
# Statcast wOBA park factor: 112 (12% above league avg).
# This is a constant multiplier, not weather-dependent.
ALTITUDE_MULTIPLIER = {
    "COL": 1.04,   # Coors Field — +4% on hits (conservative; park factor is 112)
}


# ---------------------------------------------------------------------------
# Wind parser
# ---------------------------------------------------------------------------

def parse_mlb_wind(wind_str):
    """
    Parse MLB weather wind string into (speed, direction).

    Parameters
    ----------
    wind_str : str
        Examples: "7 mph, Out To LF", "12 mph, In From CF",
                  "5 mph, L To R", "Calm", "Dome"

    Returns
    -------
    tuple[int, str]
        (speed_mph, direction) where direction is one of
        "out", "in", "cross", "calm".
    """
    if not wind_str or not isinstance(wind_str, str):
        return (0, "calm")

    wind_str = wind_str.strip()
    lower = wind_str.lower()

    if lower in ("calm", "dome", ""):
        return (0, "calm")

    # Extract speed: first integer in the string
    speed_match = re.search(r"(\d+)", wind_str)
    speed = int(speed_match.group(1)) if speed_match else 0

    # Determine direction
    if "out to" in lower:
        direction = "out"
    elif "in from" in lower:
        direction = "in"
    elif "l to r" in lower or "r to l" in lower:
        direction = "cross"
    else:
        direction = "calm"

    return (speed, direction)


# ---------------------------------------------------------------------------
# Weather multiplier
# ---------------------------------------------------------------------------

def compute_weather_multiplier(weather_data, home_team):
    """
    Compute a hits multiplier based on weather conditions.

    Based on research:
      - Temperature is the universal factor (affects all outdoor parks).
        Every 10°F ≈ 3 feet of fly ball distance (Alan Nathan).
        Below 55°F: hits suppressed. Above 85°F: hits boosted.
      - Wind direction only matters at specific wind-sensitive parks
        (Wrigley, Guaranteed Rate, Kauffman). At all other parks,
        wind effects are not statistically significant.
      - Humidity: not modeled (negligible real effect).

    Parameters
    ----------
    weather_data : dict
        {"temp": str, "wind": str, "condition": str, "home_team": str}
    home_team : str
        Home team abbreviation (used for dome/wind-sensitive checks).

    Returns
    -------
    float
        Multiplier in [0.92, 1.08].  1.0 = no adjustment.
    """
    if not weather_data:
        return 1.0

    # Dome check — no weather effect (altitude doesn't apply to domes either)
    if home_team in DOMED_TEAMS:
        return 1.0

    adj = 0.0

    # --- Altitude adjustment (constant, not weather-dependent) ---
    # Coors Field: thin air → ball carries ~7.5% further than average.
    # We apply a conservative +4% since park factors already partially
    # captured in season stats, but day-of projection should reflect it.
    alt_mult = ALTITUDE_MULTIPLIER.get(home_team, 0.0)
    if alt_mult > 1.0:
        adj += (alt_mult - 1.0)

    # --- Temperature adjustment (universal, all outdoor parks) ---
    # Research (Alan Nathan): 10°F ≈ 3 feet of fly ball carry.
    # Below 55°F: batted balls significantly less likely to fall for hits.
    # HR/FB rate drops ~20% in cold (4.0% at 75°F+ vs 3.2% cold).
    temp_str = weather_data.get("temp", "")
    temp_match = re.search(r"(\d+)", str(temp_str))
    if temp_match:
        temp_f = int(temp_match.group(1))
        if temp_f <= 50:
            adj -= 0.03  # -3% very cold
        elif temp_f <= 55:
            adj -= 0.02  # -2% cold (research threshold)
        elif temp_f <= 62:
            adj -= 0.01  # slight suppression
        elif temp_f >= 90:
            adj += 0.03  # +3% very hot
        elif temp_f >= 85:
            adj += 0.02  # +2% hot
        # 63-84°F: neutral, no adjustment

    # --- Wind adjustment (park-specific ONLY) ---
    # Research: league-wide wind effects are NOT statistically significant.
    # Statcast 2023-24 data shows meaningful effects only at specific parks:
    #   Tier 1: CHC (40% of all 25ft+ wind balls), CWS (p<.01), KC (69 HRs affected)
    #   Tier 2: NYM (28 wind-created HRs), PHI, MIN, BOS
    # Wind threshold: 10 mph+ (research cutoff for meaningful effect)
    if home_team in WIND_SENSITIVE_PARKS:
        speed, direction = parse_mlb_wind(weather_data.get("wind", ""))
        park_factor = WIND_PARK_FACTOR.get(home_team, 0.0)

        if speed >= 10 and direction == "out":
            # At Wrigley: HR/FB goes from 13.7% (in) to 23.2% (out).
            # 5 mph of tailwind adds ~19 feet of carry.
            # Scale: 10mph = base effect, 20mph+ = max effect
            speed_scale = min((speed - 10) / 10.0, 1.0)
            adj += (0.03 + 0.04 * speed_scale) * park_factor

        elif speed >= 10 and direction == "in":
            # Wind in suppresses fly balls — KC had 67 prevented HRs (#1 MLB)
            speed_scale = min((speed - 10) / 10.0, 1.0)
            adj -= (0.02 + 0.03 * speed_scale) * park_factor

        # crosswind / calm / speed < 10: no wind adjustment

    # Cap total at ±10% (wider cap to accommodate altitude + weather combo)
    return max(0.90, min(1.10, 1.0 + adj))


# ---------------------------------------------------------------------------
# Fetch weather from MLB Stats API
# ---------------------------------------------------------------------------

def fetch_game_weather(date_str):
    """
    Fetch weather for all MLB games on a given date.

    Uses the MLB Stats API schedule endpoint with weather hydration.
    Caches results (never expires for past dates, 1hr for today).

    Parameters
    ----------
    date_str : str
        ISO date, e.g. "2026-04-14".

    Returns
    -------
    dict
        {game_id (int or str): {"temp": str, "wind": str,
         "condition": str, "home_team": str}}
    """
    cache_key = date_str.replace("-", "")
    cache_path = CACHE_DIR / f"weather_{cache_key}.json"

    # Match NBA cache pattern: past dates never expire, today = 1hr freshness
    from datetime import date as dt_date
    today_str = dt_date.today().strftime("%Y-%m-%d")
    max_age = 1 if date_str >= today_str else None  # None = never expire
    cached = _load_cache(cache_path, max_age_hours=max_age)
    if cached is not None:
        return cached

    url = (
        f"https://statsapi.mlb.com/api/v1/schedule"
        f"?sportId=1&date={date_str}&hydrate=weather,venue"
    )

    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  [weather] Failed to fetch weather: {e}")
        return {}

    time.sleep(0.5)

    result = {}
    for date_entry in data.get("dates", []):
        for game in date_entry.get("games", []):
            game_id = game.get("gamePk")
            if game_id is None:
                continue

            weather = game.get("weather", {})
            home_id = (game.get("teams", {}).get("home", {})
                       .get("team", {}).get("id"))
            home_abbr = MLB_TEAM_ID_TO_ABBR.get(home_id, "")

            result[game_id] = {
                "temp": weather.get("temp", ""),
                "wind": weather.get("wind", ""),
                "condition": weather.get("condition", ""),
                "home_team": home_abbr,
            }

    _save_cache(cache_path, result)
    return result

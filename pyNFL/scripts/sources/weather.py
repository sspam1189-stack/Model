# pyNFL/scripts/sources/weather.py
# NFL stadium reference + weather impact estimation.
#
# For live games: fetches forecast from Open-Meteo (free, no API key).
# For backfill: estimates from nflfastr game-level weather when available,
# or uses historical climate averages for the stadium location + month.
#
# Primary output: weather_adjustment dict with total_adj and spread_adj.

import os
import json
import math
import requests
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# NFL Stadium Reference
# ---------------------------------------------------------------------------
# roof: "dome" | "retractable" | "open"
# lat/lon for weather lookups
# elevation_ft for altitude adjustment (Denver)

STADIUMS = {
    "ARI": {"name": "State Farm Stadium", "roof": "retractable", "lat": 33.53, "lon": -112.26, "elev_ft": 1070},
    "ATL": {"name": "Mercedes-Benz Stadium", "roof": "retractable", "lat": 33.76, "lon": -84.40, "elev_ft": 1050},
    "BAL": {"name": "M&T Bank Stadium", "roof": "open", "lat": 39.28, "lon": -76.62, "elev_ft": 33},
    "BUF": {"name": "Highmark Stadium", "roof": "open", "lat": 42.77, "lon": -78.79, "elev_ft": 600},
    "CAR": {"name": "Bank of America Stadium", "roof": "open", "lat": 35.23, "lon": -80.85, "elev_ft": 751},
    "CHI": {"name": "Soldier Field", "roof": "open", "lat": 41.86, "lon": -87.62, "elev_ft": 595},
    "CIN": {"name": "Paycor Stadium", "roof": "open", "lat": 39.10, "lon": -84.52, "elev_ft": 490},
    "CLE": {"name": "Cleveland Browns Stadium", "roof": "open", "lat": 41.51, "lon": -81.70, "elev_ft": 583},
    "DAL": {"name": "AT&T Stadium", "roof": "retractable", "lat": 32.75, "lon": -97.09, "elev_ft": 545},
    "DEN": {"name": "Empower Field", "roof": "open", "lat": 39.74, "lon": -105.02, "elev_ft": 5280},
    "DET": {"name": "Ford Field", "roof": "dome", "lat": 42.34, "lon": -83.05, "elev_ft": 600},
    "GB":  {"name": "Lambeau Field", "roof": "open", "lat": 44.50, "lon": -88.06, "elev_ft": 640},
    "HOU": {"name": "NRG Stadium", "roof": "retractable", "lat": 29.68, "lon": -95.41, "elev_ft": 43},
    "IND": {"name": "Lucas Oil Stadium", "roof": "retractable", "lat": 39.76, "lon": -86.16, "elev_ft": 715},
    "JAX": {"name": "EverBank Stadium", "roof": "open", "lat": 30.32, "lon": -81.64, "elev_ft": 16},
    "KC":  {"name": "Arrowhead Stadium", "roof": "open", "lat": 39.05, "lon": -94.48, "elev_ft": 820},
    "LA":  {"name": "SoFi Stadium", "roof": "dome", "lat": 33.95, "lon": -118.34, "elev_ft": 131},
    "LAC": {"name": "SoFi Stadium", "roof": "dome", "lat": 33.95, "lon": -118.34, "elev_ft": 131},
    "LV":  {"name": "Allegiant Stadium", "roof": "dome", "lat": 36.09, "lon": -115.18, "elev_ft": 2001},
    "MIA": {"name": "Hard Rock Stadium", "roof": "open", "lat": 25.96, "lon": -80.24, "elev_ft": 7},
    "MIN": {"name": "U.S. Bank Stadium", "roof": "dome", "lat": 44.97, "lon": -93.26, "elev_ft": 830},
    "NE":  {"name": "Gillette Stadium", "roof": "open", "lat": 42.09, "lon": -71.26, "elev_ft": 256},
    "NO":  {"name": "Caesars Superdome", "roof": "dome", "lat": 29.95, "lon": -90.08, "elev_ft": 3},
    "NYG": {"name": "MetLife Stadium", "roof": "open", "lat": 40.81, "lon": -74.07, "elev_ft": 3},
    "NYJ": {"name": "MetLife Stadium", "roof": "open", "lat": 40.81, "lon": -74.07, "elev_ft": 3},
    "PHI": {"name": "Lincoln Financial Field", "roof": "open", "lat": 39.90, "lon": -75.17, "elev_ft": 30},
    "PIT": {"name": "Acrisure Stadium", "roof": "open", "lat": 40.45, "lon": -80.02, "elev_ft": 730},
    "SEA": {"name": "Lumen Field", "roof": "open", "lat": 47.60, "lon": -122.33, "elev_ft": 12},
    "SF":  {"name": "Levi's Stadium", "roof": "open", "lat": 37.40, "lon": -121.97, "elev_ft": 43},
    "TB":  {"name": "Raymond James Stadium", "roof": "open", "lat": 27.98, "lon": -82.50, "elev_ft": 36},
    "TEN": {"name": "Nissan Stadium", "roof": "open", "lat": 36.17, "lon": -86.77, "elev_ft": 446},
    "WAS": {"name": "Northwest Stadium", "roof": "open", "lat": 38.91, "lon": -76.86, "elev_ft": 66},
}

# Dome teams — weather doesn't affect these
DOME_TEAMS = {k for k, v in STADIUMS.items() if v["roof"] in ("dome", "retractable")}


def get_stadium_for_home(home_abbr):
    """Return stadium info for a home team abbreviation."""
    return STADIUMS.get(home_abbr)


def is_dome_game(home_abbr):
    """Return True if the home stadium is a dome or retractable roof."""
    return home_abbr in DOME_TEAMS


# ---------------------------------------------------------------------------
# Open-Meteo forecast (free, no API key needed)
# ---------------------------------------------------------------------------

def fetch_forecast(lat, lon, game_datetime_utc):
    """
    Fetch hourly weather forecast from Open-Meteo for a specific time.

    Returns dict: {"wind_mph": float, "precip_mm": float, "temp_f": float}
    or None on failure.
    """
    try:
        dt = game_datetime_utc if isinstance(game_datetime_utc, datetime) else datetime.fromisoformat(game_datetime_utc.replace("Z", "+00:00"))
        date_str = dt.strftime("%Y-%m-%d")
        hour = dt.hour

        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}"
            f"&hourly=wind_speed_10m,precipitation,temperature_2m"
            f"&start_date={date_str}&end_date={date_str}"
            f"&wind_speed_unit=mph&temperature_unit=fahrenheit"
            f"&precipitation_unit=mm&timezone=UTC"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        hourly = data.get("hourly", {})
        winds = hourly.get("wind_speed_10m", [])
        precips = hourly.get("precipitation", [])
        temps = hourly.get("temperature_2m", [])

        # Get the 3-hour window around game time (kickoff + ~3hrs)
        start_h = max(0, hour)
        end_h = min(len(winds), hour + 3)

        avg_wind = sum(winds[start_h:end_h]) / max(1, end_h - start_h) if winds else 0
        max_precip = max(precips[start_h:end_h]) if precips else 0
        avg_temp = sum(temps[start_h:end_h]) / max(1, end_h - start_h) if temps else 60

        return {
            "wind_mph": round(avg_wind, 1),
            "precip_mm": round(max_precip, 1),
            "temp_f": round(avg_temp, 1),
        }
    except Exception as e:
        print(f"  [weather] forecast fetch failed: {e}")
        return None


# ---------------------------------------------------------------------------
# Weather impact computation
# ---------------------------------------------------------------------------

# Wind thresholds (mph) — backed by NFL research
WIND_LIGHT = 10      # No meaningful impact
WIND_MODERATE = 15   # Reduces deep passing ~5-10%
WIND_HEAVY = 20      # Significant passing impact
WIND_EXTREME = 25    # Game-changing conditions

# Precip thresholds (mm/hr)
PRECIP_LIGHT = 0.5   # Drizzle — minimal
PRECIP_MODERATE = 2.0  # Steady rain — impacts footing, ball handling
PRECIP_HEAVY = 5.0   # Heavy rain — major impact

# Temperature
COLD_THRESHOLD = 25  # Fahrenheit — cold impacts ball handling, passing


def compute_weather_adjustment(weather, home_abbr=None):
    """
    Compute weather-based adjustments to total and spread projections.

    Parameters
    ----------
    weather : dict or None
        {"wind_mph": float, "precip_mm": float, "temp_f": float}
    home_abbr : str or None
        Home team abbreviation (for dome check).

    Returns
    -------
    dict
        {
            "total_adj": float (points to add to projected total, usually negative),
            "spread_adj": float (points to adjust spread — usually 0),
            "is_dome": bool,
            "weather_note": str,
        }
    """
    result = {"total_adj": 0.0, "spread_adj": 0.0, "is_dome": False, "weather_note": ""}

    if home_abbr and is_dome_game(home_abbr):
        result["is_dome"] = True
        result["weather_note"] = "dome"
        return result

    if weather is None:
        return result

    wind = weather.get("wind_mph", 0)
    precip = weather.get("precip_mm", 0)
    temp = weather.get("temp_f", 60)

    notes = []
    total_adj = 0.0

    # Wind impact on totals (primarily passing game)
    if wind >= WIND_EXTREME:
        total_adj -= 5.0
        notes.append(f"extreme wind {wind}mph")
    elif wind >= WIND_HEAVY:
        total_adj -= 3.0
        notes.append(f"heavy wind {wind}mph")
    elif wind >= WIND_MODERATE:
        total_adj -= 1.5
        notes.append(f"moderate wind {wind}mph")
    elif wind >= WIND_LIGHT:
        total_adj -= 0.5
        notes.append(f"light wind {wind}mph")

    # Precipitation impact
    if precip >= PRECIP_HEAVY:
        total_adj -= 4.0
        notes.append(f"heavy rain/snow {precip}mm")
    elif precip >= PRECIP_MODERATE:
        total_adj -= 2.0
        notes.append(f"rain {precip}mm")
    elif precip >= PRECIP_LIGHT:
        total_adj -= 0.5
        notes.append(f"light rain {precip}mm")

    # Cold weather impact
    if temp <= COLD_THRESHOLD:
        total_adj -= 1.5
        notes.append(f"cold {temp}°F")

    result["total_adj"] = round(total_adj, 1)
    result["weather_note"] = ", ".join(notes) if notes else "clear"

    return result

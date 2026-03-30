# pyMLB/scripts/sources/weather.py
# Fetch game-time weather from Open-Meteo (free, no API key needed).
# Used for temperature and wind adjustments to run totals.

import requests
import datetime
import math


def fetch_forecast(lat, lon, game_time_iso):
    """
    Fetch hourly forecast from Open-Meteo for the given location and time.
    Returns: {"temperature_f": float, "wind_speed_mph": float, "wind_direction_deg": int}
    """
    try:
        # Parse game time
        if isinstance(game_time_iso, str):
            gt = datetime.datetime.fromisoformat(game_time_iso.replace("Z", "+00:00"))
        else:
            gt = game_time_iso

        date_str = gt.strftime("%Y-%m-%d")

        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={lat}&longitude={lon}"
            f"&hourly=temperature_2m,windspeed_10m,winddirection_10m"
            f"&temperature_unit=fahrenheit"
            f"&windspeed_unit=mph"
            f"&start_date={date_str}&end_date={date_str}"
            f"&timezone=auto"
        )

        res = requests.get(url, timeout=10)
        if res.status_code != 200:
            print(f"  [weather] Open-Meteo returned {res.status_code}")
            return _default_weather()

        data = res.json()
        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        winds = hourly.get("windspeed_10m", [])
        wind_dirs = hourly.get("winddirection_10m", [])

        if not times:
            return _default_weather()

        # Find closest hour to game time
        game_hour = gt.hour
        best_idx = 0
        best_diff = 999
        for i, t in enumerate(times):
            try:
                h = int(t.split("T")[1].split(":")[0])
                diff = abs(h - game_hour)
                if diff < best_diff:
                    best_diff = diff
                    best_idx = i
            except (IndexError, ValueError):
                continue

        return {
            "temperature_f": temps[best_idx] if best_idx < len(temps) else 72.0,
            "wind_speed_mph": winds[best_idx] if best_idx < len(winds) else 5.0,
            "wind_direction_deg": int(wind_dirs[best_idx]) if best_idx < len(wind_dirs) else 0,
        }

    except Exception as e:
        print(f"  [weather] Forecast fetch error: {e}")
        return _default_weather()


def _default_weather():
    """Return neutral weather defaults."""
    return {"temperature_f": 72.0, "wind_speed_mph": 5.0, "wind_direction_deg": 0}


def compute_weather_features(weather_data, is_dome=False):
    """
    Compute weather-based feature adjustments for the model.
    Returns: {"temperature_adj": float, "wind_adj": float}
    """
    if is_dome or not weather_data:
        return {"temperature_adj": 0.0, "wind_adj": 0.0}

    temp_f = weather_data.get("temperature_f", 72.0)
    wind_mph = weather_data.get("wind_speed_mph", 5.0)
    wind_dir = weather_data.get("wind_direction_deg", 0)

    # Temperature adjustment: runs increase ~0.05/degree above 70F
    temp_adj = max(0.0, (temp_f - 70.0)) * 0.05

    # Wind adjustment: positive = blowing out (more runs), negative = blowing in
    # Simple model: wind speed * cos(direction factor)
    # 0/360 deg = North. Most parks have CF roughly to the north/northeast.
    # Crude approximation: wind from south (180 deg) blows out to CF
    wind_rad = math.radians(wind_dir)
    # cos(dir - 180) peaks when wind is from the south (blowing out)
    directional = math.cos(wind_rad - math.pi)
    wind_adj = wind_mph * directional * 0.02  # Scale factor

    return {
        "temperature_adj": round(temp_adj, 4),
        "wind_adj": round(wind_adj, 4),
    }

# pyMLB/scripts/sources/park_factors.py
# Static MLB park factor table and stadium metadata.
# Park factors from FanGraphs (runs factor, 1.0 = neutral).

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import defaults

# {team_canonical_name: {"runs_factor": float, "lat": float, "lon": float, "stadium": str}}
PARK_DATA = {
    "Arizona Diamondbacks":     {"runs_factor": 1.05, "lat": 33.4455, "lon": -112.0667, "stadium": "Chase Field"},
    "Atlanta Braves":           {"runs_factor": 0.98, "lat": 33.8907, "lon": -84.4677, "stadium": "Truist Park"},
    "Baltimore Orioles":        {"runs_factor": 1.02, "lat": 39.2839, "lon": -76.6217, "stadium": "Camden Yards"},
    "Boston Red Sox":           {"runs_factor": 1.08, "lat": 42.3467, "lon": -71.0972, "stadium": "Fenway Park"},
    "Chicago Cubs":             {"runs_factor": 1.05, "lat": 41.9484, "lon": -87.6553, "stadium": "Wrigley Field"},
    "Chicago White Sox":        {"runs_factor": 1.02, "lat": 41.8299, "lon": -87.6338, "stadium": "Guaranteed Rate Field"},
    "Cincinnati Reds":          {"runs_factor": 1.08, "lat": 39.0974, "lon": -84.5065, "stadium": "Great American Ball Park"},
    "Cleveland Guardians":      {"runs_factor": 0.95, "lat": 41.4962, "lon": -81.6852, "stadium": "Progressive Field"},
    "Colorado Rockies":         {"runs_factor": 1.30, "lat": 39.7561, "lon": -104.9942, "stadium": "Coors Field"},
    "Detroit Tigers":           {"runs_factor": 0.95, "lat": 42.3390, "lon": -83.0485, "stadium": "Comerica Park"},
    "Houston Astros":           {"runs_factor": 1.00, "lat": 29.7573, "lon": -95.3555, "stadium": "Minute Maid Park"},
    "Kansas City Royals":       {"runs_factor": 1.02, "lat": 39.0517, "lon": -94.4803, "stadium": "Kauffman Stadium"},
    "Los Angeles Angels":       {"runs_factor": 0.98, "lat": 33.8003, "lon": -117.8827, "stadium": "Angel Stadium"},
    "Los Angeles Dodgers":      {"runs_factor": 0.93, "lat": 34.0739, "lon": -118.2400, "stadium": "Dodger Stadium"},
    "Miami Marlins":            {"runs_factor": 0.90, "lat": 25.7781, "lon": -80.2196, "stadium": "loanDepot Park"},
    "Milwaukee Brewers":        {"runs_factor": 1.02, "lat": 43.0280, "lon": -87.9712, "stadium": "American Family Field"},
    "Minnesota Twins":          {"runs_factor": 1.00, "lat": 44.9818, "lon": -93.2775, "stadium": "Target Field"},
    "New York Mets":            {"runs_factor": 0.95, "lat": 40.7571, "lon": -73.8458, "stadium": "Citi Field"},
    "New York Yankees":         {"runs_factor": 1.10, "lat": 40.8296, "lon": -73.9262, "stadium": "Yankee Stadium"},
    "Oakland Athletics":        {"runs_factor": 0.92, "lat": 37.7516, "lon": -122.2005, "stadium": "Oakland Coliseum"},
    "Philadelphia Phillies":    {"runs_factor": 1.03, "lat": 39.9061, "lon": -75.1665, "stadium": "Citizens Bank Park"},
    "Pittsburgh Pirates":       {"runs_factor": 0.93, "lat": 40.4469, "lon": -80.0058, "stadium": "PNC Park"},
    "San Diego Padres":         {"runs_factor": 0.92, "lat": 32.7076, "lon": -117.1570, "stadium": "Petco Park"},
    "San Francisco Giants":     {"runs_factor": 0.85, "lat": 37.7786, "lon": -122.3893, "stadium": "Oracle Park"},
    "Seattle Mariners":         {"runs_factor": 0.93, "lat": 47.5914, "lon": -122.3325, "stadium": "T-Mobile Park"},
    "St. Louis Cardinals":      {"runs_factor": 0.98, "lat": 38.6226, "lon": -90.1928, "stadium": "Busch Stadium"},
    "Tampa Bay Rays":           {"runs_factor": 0.90, "lat": 27.7682, "lon": -82.6534, "stadium": "Tropicana Field"},
    "Texas Rangers":            {"runs_factor": 1.00, "lat": 32.7512, "lon": -97.0832, "stadium": "Globe Life Field"},
    "Toronto Blue Jays":        {"runs_factor": 1.00, "lat": 43.6414, "lon": -79.3894, "stadium": "Rogers Centre"},
    "Washington Nationals":     {"runs_factor": 0.98, "lat": 38.8730, "lon": -77.0074, "stadium": "Nationals Park"},
}


def get_park_factor(home_team):
    """Return the runs park factor for the home team. 1.0 = neutral."""
    data = PARK_DATA.get(home_team)
    if data:
        return data["runs_factor"]
    return 1.0


def get_stadium_coords(home_team):
    """Return (lat, lon) for the home team's stadium."""
    data = PARK_DATA.get(home_team)
    if data:
        return (data["lat"], data["lon"])
    return None


def is_dome(home_team):
    """Check if the home team plays in a domed/retractable roof stadium."""
    data = PARK_DATA.get(home_team)
    if data:
        return data["stadium"] in defaults.DOMED_STADIUMS
    return False

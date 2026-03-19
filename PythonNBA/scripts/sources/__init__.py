# scripts/sources/__init__.py
# Package init -- imports all source modules for convenient access.

from .http import fetch_text, html, fetch_json
from .nba_stats import fetch_nba_stats, fetch_nba_stats_enhanced
from .nba_recent_stats import fetch_blended_stats
from .espn_scoreboard import fetch_scoreboard, extract_final_scores
from .odds_theoddsapi import fetch_todays_odds
from .odds_theoddsapi_historical import fetch_closing_odds_for_game
from .blend_stats import blend_base, blend_for_game
from .injuries import fetch_injury_data, get_key_injuries, game_uncertainty_score, fetch_player_mpg
from .lineup_adjust import fetch_player_advanced, adjust_team_stats, get_adjustment_notes
from .rest_detect import detect_b2b, apply_b2b_adjustment
from .season_type import get_season_type, get_espn_season_type, is_playoffs
from .teamrankings_trends import fetch_ou_trends, fetch_ats_trends

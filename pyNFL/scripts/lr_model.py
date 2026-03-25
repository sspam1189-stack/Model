# pyNFL/scripts/lr_model.py
# NFL Logistic Regression confirmation layer.
#
# Mirrors pyNBA/scripts/lr_model.py architecture: a thin wrapper around
# core/lr_model.py that configures NFL-specific feature names, thresholds,
# and an NFL-specific extra feature function for injury deltas.
#
# The LR model acts as a confirm/veto gate on top of the main projection.
# Picks agreed on by both the Bayesian model and LR historically hit at
# a higher rate.
#
# Usage:
#   from pyNFL.scripts.lr_model import load_or_train_lr, predict_lr
#   model = load_or_train_lr(store, data_dir)
#   result = predict_lr(model, features)

import sys
import os
import math
import json
import time
from collections import defaultdict
from datetime import datetime

# Ensure core/ is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    import numpy as np
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

try:
    import joblib
    HAS_JOBLIB = True
except ImportError:
    try:
        from sklearn.externals import joblib
        HAS_JOBLIB = True
    except ImportError:
        HAS_JOBLIB = False
        joblib = None


# ---------------------------------------------------------------------------
# NFL-specific configuration
# ---------------------------------------------------------------------------

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA_DIR = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "data"))
MODEL_DIR = os.path.join(_DATA_DIR, "lr_models")

# Thresholds for confirm/veto gate (calibrated via backtest)
LR_CONFIRM_THRESH = 0.55
LR_VETO_THRESH = 0.45

# Retraining schedule
RETRAIN_INTERVAL = 20    # retrain after this many new graded games
MIN_TRAINING_GAMES = 50  # NFL has fewer games per season; 50 ~= 3 weeks x 16 games

# NFL feature vector — tailored to football analytics.
# These are independent features (not from the Bayesian model), used to
# build a confirmation/veto signal.
LR_FEATURE_NAMES = [
    # EPA differentials (4)
    "epa_diff",               # team EPA differential (home - away)
    "pass_epa_diff",          # passing EPA differential
    "rush_epa_diff",          # rushing EPA differential
    "def_epa_diff",           # defensive EPA differential
    # Injury impact (2)
    "home_injury_delta",      # home team injury EPA delta
    "away_injury_delta",      # away team injury EPA delta
    # Market context (3)
    "market_line",            # market spread (+ = home favored)
    "sdiff",                  # model spread - market spread
    "abs_line",               # absolute spread size
    # Pace & efficiency (3)
    "pace_diff",              # plays/game differential
    "success_rate_diff",      # offensive success rate differential
    "turnover_diff",          # turnover rate differential
    # ATS momentum (4)
    "home_ats_pct_5",         # home ATS hit rate last 5
    "away_ats_pct_5",         # away ATS hit rate last 5
    "home_ats_margin_5",      # home avg ATS margin last 5
    "away_ats_margin_5",      # away avg ATS margin last 5
    # Recent form (4)
    "home_win_pct_5",         # home win% last 5
    "away_win_pct_5",         # away win% last 5
    "home_avg_margin_5",      # home avg margin last 5
    "away_avg_margin_5",      # away avg margin last 5
    # Situational (2)
    "home_flag",              # 1 if home team is the picked side
    "rest_advantage",         # bye week / rest day advantage
]

_FEATURE_LABELS = {
    "epa_diff":           "EPA Differential",
    "pass_epa_diff":      "Pass EPA Diff",
    "rush_epa_diff":      "Rush EPA Diff",
    "def_epa_diff":       "Def EPA Diff",
    "home_injury_delta":  "Home Injury Delta",
    "away_injury_delta":  "Away Injury Delta",
    "market_line":        "Market Line",
    "sdiff":              "sDiff (model - market)",
    "abs_line":           "Spread Size",
    "pace_diff":          "Pace Diff",
    "success_rate_diff":  "Success Rate Diff",
    "turnover_diff":      "Turnover Diff",
    "home_ats_pct_5":     "Home ATS% L5",
    "away_ats_pct_5":     "Away ATS% L5",
    "home_ats_margin_5":  "Home ATS Margin L5",
    "away_ats_margin_5":  "Away ATS Margin L5",
    "home_win_pct_5":     "Home Win% L5",
    "away_win_pct_5":     "Away Win% L5",
    "home_avg_margin_5":  "Home Avg Margin L5",
    "away_avg_margin_5":  "Away Avg Margin L5",
    "home_flag":          "Home Flag",
    "rest_advantage":     "Rest Advantage",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(v, default=0.0):
    """Convert a value to float safely, returning default on failure."""
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _safe_pct(wins, total):
    """Compute win percentage, returning 0.5 with no data."""
    if total <= 0:
        return 0.5
    return wins / total


def _safe_mean(values):
    """Mean of a list, returning 0.0 if empty."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def _parse_date(d):
    """Parse a YYYYMMDD or YYYY-MM-DD date string to datetime."""
    try:
        s = str(d).replace("-", "")[:8]
        return datetime.strptime(s, "%Y%m%d")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# build_team_histories
# ---------------------------------------------------------------------------

def build_team_histories(store):
    """Walk every graded game in the store, building per-team chronological
    histories suitable for LR feature extraction.

    Parameters
    ----------
    store : dict
        The NFL store (from pyNFL/data/nfl.json or equivalent).
        Expected shape: {"runs": [{"date": ..., "games": [...]}]}

    Returns
    -------
    dict
        team_name -> list of game dicts, sorted by date ascending.
    """
    histories = defaultdict(list)

    for run in store.get("runs", []):
        run_date = run.get("date", "")
        if not run_date:
            continue

        for g in run.get("games", []):
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue
            if not isinstance(g.get("homeScore"), (int, float)):
                continue
            if not isinstance(g.get("awayScore"), (int, float)):
                continue
            if not isinstance(g.get("line"), (int, float)):
                continue

            home = g["home"]
            away = g["away"]
            home_score = g["homeScore"]
            away_score = g["awayScore"]
            line = g["line"]
            total = g.get("total", home_score + away_score)
            actual_total = home_score + away_score
            home_margin = home_score - away_score
            home_spread = -line
            home_covered = (home_margin + home_spread) > 0
            game_over = actual_total > total

            base = {
                "date": run_date,
                "home": home,
                "away": away,
                "line": line,
                "total": total,
                "actual_total": actual_total,
            }

            histories[home].append({
                **base,
                "is_home": True,
                "score": home_score,
                "opp_score": away_score,
                "margin": home_margin,
                "covered": home_covered,
                "over": game_over,
                "line_for_team": home_spread,
                "abs_line": abs(line),
                "won": home_score > away_score,
            })

            histories[away].append({
                **base,
                "is_home": False,
                "score": away_score,
                "opp_score": home_score,
                "margin": -home_margin,
                "covered": not home_covered,
                "over": game_over,
                "line_for_team": -home_spread,
                "abs_line": abs(line),
                "won": away_score > home_score,
            })

    # Sort each team's history by date
    for team in histories:
        histories[team].sort(key=lambda x: x["date"])

    # Compute rest_days (NFL has 7+ day gaps typically, with byes = 14)
    for team in histories:
        games = histories[team]
        for i, g in enumerate(games):
            if i == 0:
                g["rest_days"] = 7  # assume normal week for first game
            else:
                prev_date = _parse_date(games[i - 1]["date"])
                cur_date = _parse_date(g["date"])
                if prev_date and cur_date:
                    g["rest_days"] = max(0, min((cur_date - prev_date).days - 1, 21))
                else:
                    g["rest_days"] = 7

    return dict(histories)


# ---------------------------------------------------------------------------
# _team_rolling_features
# ---------------------------------------------------------------------------

def _team_rolling_features(hist, n5=5):
    """Compute rolling stats for one team given history before current game.

    Parameters
    ----------
    hist : list
        Team's game history (chronological, before the current game).
    n5 : int
        Window for recent performance features.

    Returns
    -------
    dict
        Rolling feature components for this team.
    """
    if not hist:
        return {
            "ats_pct_5": 0.5, "ats_margin_5": 0.0,
            "win_pct_5": 0.5, "avg_margin_5": 0.0,
            "rest_days": 7,
        }

    last5 = hist[-n5:]

    ats_pct_5 = _safe_pct(sum(1 for g in last5 if g["covered"]), len(last5))
    ats_margin_5 = _safe_mean([g["margin"] + g["line_for_team"] for g in last5])
    win_pct_5 = _safe_pct(sum(1 for g in last5 if g["won"]), len(last5))
    avg_margin_5 = _safe_mean([g["margin"] for g in last5])
    rest_days = hist[-1].get("rest_days", 7) if hist else 7

    return {
        "ats_pct_5": ats_pct_5,
        "ats_margin_5": ats_margin_5,
        "win_pct_5": win_pct_5,
        "avg_margin_5": avg_margin_5,
        "rest_days": rest_days,
    }


# ---------------------------------------------------------------------------
# extract_lr_features
# ---------------------------------------------------------------------------

def extract_lr_features(game_analysis):
    """Build the feature vector for a single game from an analysis dict.

    This is the primary feature extraction function.  It takes a game
    analysis dict (as produced by the model engine or orchestrator) and
    returns a feature vector matching LR_FEATURE_NAMES.

    Parameters
    ----------
    game_analysis : dict
        Must contain some subset of:
        - epa_diff, pass_epa_diff, rush_epa_diff, def_epa_diff: float
        - home_injury_delta, away_injury_delta: float
        - market_line: float (market spread, + = home favored)
        - sdiff: float (model spread - market spread)
        - pace_diff: float
        - success_rate_diff: float
        - turnover_diff: float
        - home_ats_pct_5, away_ats_pct_5: float
        - home_ats_margin_5, away_ats_margin_5: float
        - home_win_pct_5, away_win_pct_5: float
        - home_avg_margin_5, away_avg_margin_5: float
        - home_flag: float (1 or 0)
        - rest_advantage: float

    Returns
    -------
    list[float] or None
        Feature vector matching LR_FEATURE_NAMES, or None on error.
    """
    if not game_analysis:
        return None

    try:
        features = [
            _safe_float(game_analysis.get("epa_diff")),
            _safe_float(game_analysis.get("pass_epa_diff")),
            _safe_float(game_analysis.get("rush_epa_diff")),
            _safe_float(game_analysis.get("def_epa_diff")),
            _safe_float(game_analysis.get("home_injury_delta")),
            _safe_float(game_analysis.get("away_injury_delta")),
            _safe_float(game_analysis.get("market_line")),
            _safe_float(game_analysis.get("sdiff")),
            abs(_safe_float(game_analysis.get("market_line"))),
            _safe_float(game_analysis.get("pace_diff")),
            _safe_float(game_analysis.get("success_rate_diff")),
            _safe_float(game_analysis.get("turnover_diff")),
            _safe_float(game_analysis.get("home_ats_pct_5"), 0.5),
            _safe_float(game_analysis.get("away_ats_pct_5"), 0.5),
            _safe_float(game_analysis.get("home_ats_margin_5")),
            _safe_float(game_analysis.get("away_ats_margin_5")),
            _safe_float(game_analysis.get("home_win_pct_5"), 0.5),
            _safe_float(game_analysis.get("away_win_pct_5"), 0.5),
            _safe_float(game_analysis.get("home_avg_margin_5")),
            _safe_float(game_analysis.get("away_avg_margin_5")),
            _safe_float(game_analysis.get("home_flag")),
            _safe_float(game_analysis.get("rest_advantage")),
        ]

        # Validate -- replace NaN/Inf with 0
        for i, v in enumerate(features):
            if v is None or (isinstance(v, float) and not math.isfinite(v)):
                features[i] = 0.0

        return features

    except Exception:
        return None


def build_lr_features_for_game(game, team_histories, team_stats=None,
                               injury_deltas=None, run_date=None):
    """Build LR features for a single upcoming game by combining team
    histories, team stats, and injury deltas.

    This is a convenience function that assembles the game_analysis dict
    from components and then calls extract_lr_features.

    Parameters
    ----------
    game : dict
        Must have 'home', 'away', 'line'.  May have 'projSpread', 'sdiff'.
    team_histories : dict
        Output of build_team_histories().
    team_stats : dict or None
        Team-level aggregated stats (EPA, pace, success rate).
    injury_deltas : dict or None
        Output of injury_layer.compute_injury_deltas().
    run_date : str or None
        YYYYMMDD for the current run.

    Returns
    -------
    list[float] or None
        Feature vector, or None if insufficient data.
    """
    home = game.get("home", "")
    away = game.get("away", "")
    line = _safe_float(game.get("line"))

    home_hist = team_histories.get(home, [])
    away_hist = team_histories.get(away, [])

    # Need at least 3 games for each team
    if len(home_hist) < 3 or len(away_hist) < 3:
        return None

    hf = _team_rolling_features(home_hist)
    af = _team_rolling_features(away_hist)

    # EPA differentials from team_stats (if available)
    hs = (team_stats or {}).get(home, {})
    aws = (team_stats or {}).get(away, {})

    epa_diff = _safe_float(hs.get("epa_per_play")) - _safe_float(aws.get("epa_per_play"))
    pass_epa_diff = _safe_float(hs.get("pass_epa")) - _safe_float(aws.get("pass_epa"))
    rush_epa_diff = _safe_float(hs.get("rush_epa")) - _safe_float(aws.get("rush_epa"))
    def_epa_diff = _safe_float(aws.get("def_epa")) - _safe_float(hs.get("def_epa"))
    pace_diff = _safe_float(hs.get("pace")) - _safe_float(aws.get("pace"))
    sr_diff = _safe_float(hs.get("success_rate")) - _safe_float(aws.get("success_rate"))
    to_diff = _safe_float(aws.get("turnover_rate")) - _safe_float(hs.get("turnover_rate"))

    # Injury deltas
    inj_deltas = injury_deltas or {}
    home_inj = _safe_float(inj_deltas.get(home))
    away_inj = _safe_float(inj_deltas.get(away))

    # sDiff
    sdiff = _safe_float(game.get("sdiff", game.get("sDiff")))

    # Home flag — is the home team favored?
    home_flag = 1.0 if line > 0 else 0.0

    # Rest advantage
    rest_adv = hf["rest_days"] - af["rest_days"]

    analysis = {
        "epa_diff": epa_diff,
        "pass_epa_diff": pass_epa_diff,
        "rush_epa_diff": rush_epa_diff,
        "def_epa_diff": def_epa_diff,
        "home_injury_delta": home_inj,
        "away_injury_delta": away_inj,
        "market_line": line,
        "sdiff": sdiff,
        "pace_diff": pace_diff,
        "success_rate_diff": sr_diff,
        "turnover_diff": to_diff,
        "home_ats_pct_5": hf["ats_pct_5"],
        "away_ats_pct_5": af["ats_pct_5"],
        "home_ats_margin_5": hf["ats_margin_5"],
        "away_ats_margin_5": af["ats_margin_5"],
        "home_win_pct_5": hf["win_pct_5"],
        "away_win_pct_5": af["win_pct_5"],
        "home_avg_margin_5": hf["avg_margin_5"],
        "away_avg_margin_5": af["avg_margin_5"],
        "home_flag": home_flag,
        "rest_advantage": rest_adv,
    }

    return extract_lr_features(analysis)


# ---------------------------------------------------------------------------
# _explain_lr — top contributing features
# ---------------------------------------------------------------------------

def _explain_lr(model_bundle, features, top_n=3, game=None):
    """Return top contributing features pushing the LR prediction.

    Parameters
    ----------
    model_bundle : dict
        Trained model bundle with "model", "scaler", "feature_names".
    features : list[float]
        Feature vector.
    top_n : int
        Number of top contributions to return.
    game : dict or None
        Game dict with 'home'/'away' keys for team-name labels.

    Returns
    -------
    list[str]
        Human-readable reason strings.
    """
    if model_bundle is None or features is None or not HAS_SKLEARN:
        return []

    home_name = (game or {}).get("home", "Home")
    away_name = (game or {}).get("away", "Away")

    try:
        model = model_bundle["model"]
        scaler = model_bundle["scaler"]
        names = model_bundle.get("feature_names", LR_FEATURE_NAMES)

        X_scaled = scaler.transform(np.array([features], dtype=np.float64))[0]
        coefs = model.coef_[0]

        contributions = []
        for i, (name, coef, scaled_val, raw_val) in enumerate(
            zip(names, coefs, X_scaled, features)
        ):
            contrib = coef * scaled_val
            contributions.append((contrib, name, raw_val))

        contributions.sort(key=lambda x: x[0], reverse=(not picked_home))

        reasons = []
        for contrib, name, raw_val in contributions[:top_n]:
            label = _FEATURE_LABELS.get(name, name)
            label = label.replace("Home", home_name).replace("Away", away_name)
            if "pct" in name:
                val_str = f"{raw_val * 100:.0f}%"
            elif "margin" in name or "diff" in name or "delta" in name:
                val_str = f"{raw_val:+.2f}"
            elif "line" in name or "sdiff" in name.lower():
                val_str = f"{raw_val:+.1f}"
            elif name in ("home_flag",):
                val_str = "yes" if raw_val > 0.5 else "no"
            elif "rest" in name:
                val_str = f"{raw_val:+.0f}d"
            else:
                val_str = f"{raw_val:.3f}"
            reasons.append(f"{label}: {val_str}")

        return reasons
    except Exception:
        return []


# ---------------------------------------------------------------------------
# predict_lr
# ---------------------------------------------------------------------------

def predict_lr(model_bundle, features, game=None):
    """Run a single LR prediction.

    Parameters
    ----------
    model_bundle : dict or None
        Trained model bundle from load_or_train_lr().
    features : list[float] or None
        Feature vector from extract_lr_features().
    game : dict or None
        Game dict with 'home'/'away' keys for team-name labels in reasons.

    Returns
    -------
    dict
        {
            "lr_prob": float or None (probability of home cover, 0-1),
            "lr_verdict": "CONFIRM" | "VETO" | "NEUTRAL",
            "lr_reasons": list[str] (populated on VETO),
        }
    """
    if model_bundle is None or features is None:
        return {"lr_prob": None, "lr_verdict": "NEUTRAL", "lr_reasons": []}
    if not HAS_SKLEARN:
        return {"lr_prob": None, "lr_verdict": "NEUTRAL", "lr_reasons": []}

    try:
        model = model_bundle["model"]
        scaler = model_bundle["scaler"]

        X = np.array([features], dtype=np.float64)
        X_scaled = scaler.transform(X)

        proba = model.predict_proba(X_scaled)[0]
        prob = float(proba[1]) if len(proba) > 1 else float(proba[0])

        if prob >= LR_CONFIRM_THRESH:
            verdict = "CONFIRM"
        elif prob <= LR_VETO_THRESH:
            verdict = "VETO"
        else:
            verdict = "NEUTRAL"

        reasons = _explain_lr(model_bundle, features, game=game) if verdict == "VETO" else []

        return {"lr_prob": round(prob, 3), "lr_verdict": verdict, "lr_reasons": reasons}

    except Exception as e:
        print(f"  [lr_model] predict error: {e}")
        return {"lr_prob": None, "lr_verdict": "NEUTRAL", "lr_reasons": []}


def predict_lr_for_pick(model_bundle, features, picked_home, game=None):
    """Predict and return a verdict relative to the picked side.

    Parameters
    ----------
    model_bundle : dict or None
        Trained model bundle.
    features : list[float] or None
        Feature vector.
    picked_home : bool
        True if the Bayesian model picked the home team.

    Returns
    -------
    dict
        {
            "lr_prob": float (P(home covers)),
            "lr_pick_prob": float (P(picked side covers)),
            "lr_verdict": "confirm" | "veto" | "neutral",
        }
    """
    raw = predict_lr(model_bundle, features, game=game)
    if raw["lr_prob"] is None:
        return {"lr_prob": None, "lr_pick_prob": None, "lr_verdict": "neutral", "lr_reasons": []}

    p_home = raw["lr_prob"]
    p_pick = p_home if picked_home else (1.0 - p_home)

    if p_pick >= LR_CONFIRM_THRESH:
        verdict = "confirm"
    elif p_pick <= LR_VETO_THRESH:
        verdict = "veto"
    else:
        verdict = "neutral"

    reasons = []
    if verdict == "veto":
        reasons = _explain_lr(model_bundle, features, game=game, picked_home=picked_home)

    return {
        "lr_prob": round(p_home, 4),
        "lr_pick_prob": round(p_pick, 4),
        "lr_verdict": verdict,
        "lr_reasons": reasons,
    }


# ---------------------------------------------------------------------------
# _append_to_running — helper for walk-forward training
# ---------------------------------------------------------------------------

def _append_to_running(running, g, run_date):
    """Append a graded game to per-team running histories (training helper).

    Parameters
    ----------
    running : dict
        defaultdict(list) of team -> game history.
    g : dict
        Graded game dict from the store.
    run_date : str
        Date string for this run.
    """
    home = g["home"]
    away = g["away"]
    home_score = g["homeScore"]
    away_score = g["awayScore"]
    line = g["line"]
    total = g.get("total", home_score + away_score)
    actual_total = home_score + away_score
    home_margin = home_score - away_score
    home_spread = -line
    home_covered = (home_margin + home_spread) > 0
    game_over = actual_total > total

    base = {
        "date": run_date, "home": home, "away": away,
        "line": line, "total": total, "actual_total": actual_total,
    }

    def _rest(team_hist):
        if team_hist:
            prev = _parse_date(team_hist[-1]["date"])
            cur = _parse_date(run_date)
            if prev and cur:
                return max(0, min((cur - prev).days - 1, 21))
        return 7

    home_entry = {
        **base, "is_home": True,
        "score": home_score, "opp_score": away_score,
        "margin": home_margin, "covered": home_covered,
        "over": game_over, "line_for_team": home_spread,
        "abs_line": abs(line), "won": home_score > away_score,
        "rest_days": _rest(running[home]),
    }
    running[home].append(home_entry)

    away_entry = {
        **base, "is_home": False,
        "score": away_score, "opp_score": home_score,
        "margin": -home_margin, "covered": not home_covered,
        "over": game_over, "line_for_team": -home_spread,
        "abs_line": abs(line), "won": away_score > home_score,
        "rest_days": _rest(running[away]),
    }
    running[away].append(away_entry)


# ---------------------------------------------------------------------------
# train_lr_model
# ---------------------------------------------------------------------------

def train_lr_model(store, min_games=None):
    """Train a LogisticRegression on graded games from the NFL store.

    Target: home team covers the spread (binary).
    Uses walk-forward feature extraction from rolling team histories.

    Parameters
    ----------
    store : dict
        NFL store with "runs" key.
    min_games : int or None
        Minimum games required.  Defaults to MIN_TRAINING_GAMES.

    Returns
    -------
    dict or None
        Model bundle with "model", "scaler", "n_train", "accuracy",
        "trained_at", "feature_names".  None if insufficient data.
    """
    if not HAS_SKLEARN:
        print("  [lr_model] scikit-learn not installed, cannot train")
        return None

    if min_games is None:
        min_games = MIN_TRAINING_GAMES

    print("  [lr_model] Building training data from graded NFL games...")
    running = defaultdict(list)

    X_rows = []
    y_rows = []

    for run in store.get("runs", []):
        run_date = run.get("date", "")
        if not run_date:
            continue

        for g in run.get("games", []):
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue
            if not isinstance(g.get("homeScore"), (int, float)):
                continue
            if not isinstance(g.get("awayScore"), (int, float)):
                continue
            if not isinstance(g.get("line"), (int, float)):
                continue

            home = g["home"]
            away = g["away"]

            # Build features from history *before* this game
            home_hist = list(running.get(home, []))
            away_hist = list(running.get(away, []))

            if len(home_hist) >= 3 and len(away_hist) >= 3:
                hf = _team_rolling_features(home_hist)
                af = _team_rolling_features(away_hist)

                line = g["line"]
                sdiff = _safe_float(g.get("sdiff", g.get("sDiff")))

                analysis = {
                    "epa_diff": _safe_float(g.get("epa_diff")),
                    "pass_epa_diff": _safe_float(g.get("pass_epa_diff")),
                    "rush_epa_diff": _safe_float(g.get("rush_epa_diff")),
                    "def_epa_diff": _safe_float(g.get("def_epa_diff")),
                    "home_injury_delta": _safe_float(g.get("home_injury_delta")),
                    "away_injury_delta": _safe_float(g.get("away_injury_delta")),
                    "market_line": line,
                    "sdiff": sdiff,
                    "pace_diff": _safe_float(g.get("pace_diff")),
                    "success_rate_diff": _safe_float(g.get("success_rate_diff")),
                    "turnover_diff": _safe_float(g.get("turnover_diff")),
                    "home_ats_pct_5": hf["ats_pct_5"],
                    "away_ats_pct_5": af["ats_pct_5"],
                    "home_ats_margin_5": hf["ats_margin_5"],
                    "away_ats_margin_5": af["ats_margin_5"],
                    "home_win_pct_5": hf["win_pct_5"],
                    "away_win_pct_5": af["win_pct_5"],
                    "home_avg_margin_5": hf["avg_margin_5"],
                    "away_avg_margin_5": af["avg_margin_5"],
                    "home_flag": 1.0 if line > 0 else 0.0,
                    "rest_advantage": hf["rest_days"] - af["rest_days"],
                }

                features = extract_lr_features(analysis)

                if features is not None:
                    home_margin = g["homeScore"] - g["awayScore"]
                    ats_margin = home_margin - line
                    # Skip pushes
                    if ats_margin != 0:
                        home_covered = 1 if ats_margin > 0 else 0
                        X_rows.append(features)
                        y_rows.append(home_covered)

            # Record into running histories
            _append_to_running(running, g, run_date)

    if len(X_rows) < min_games:
        print(f"  [lr_model] Only {len(X_rows)} training samples (need {min_games}), skipping")
        return None

    X = np.array(X_rows, dtype=np.float64)
    y = np.array(y_rows, dtype=np.int32)

    # Replace NaN/Inf
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    print(f"  [lr_model] Training on {len(X)} games "
          f"(home covers: {y.sum()}, away covers: {len(y) - y.sum()})...")

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    model = LogisticRegression(
        C=1.0,
        class_weight="balanced",
        max_iter=1000,
        solver="lbfgs",
        random_state=42,
    )
    model.fit(X_scaled, y)

    preds = model.predict(X_scaled)
    accuracy = float(np.mean(preds == y))
    print(f"  [lr_model] In-sample accuracy: {accuracy:.3f}")

    bundle = {
        "model": model,
        "scaler": scaler,
        "n_train": len(X),
        "n_trained": len(X),
        "accuracy": accuracy,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "feature_names": list(LR_FEATURE_NAMES),
    }

    _save_model(bundle)
    return bundle


# ---------------------------------------------------------------------------
# Model persistence
# ---------------------------------------------------------------------------

def _save_model(bundle):
    """Persist model + scaler to disk using joblib."""
    if not HAS_JOBLIB or not joblib:
        print("  [lr_model] joblib not available, model not saved to disk")
        return

    os.makedirs(MODEL_DIR, exist_ok=True)

    path = os.path.join(MODEL_DIR, "lr_latest.joblib")
    save_data = {
        "model": bundle["model"],
        "scaler": bundle["scaler"],
        "n_train": bundle.get("n_train", 0),
        "accuracy": bundle.get("accuracy", 0),
        "trained_at": bundle.get("trained_at", "unknown"),
        "feature_names": bundle.get("feature_names", list(LR_FEATURE_NAMES)),
    }
    joblib.dump(save_data, path)
    print(f"  [lr_model] Saved to {path}")

    # Also save human-readable metadata as JSON
    meta_path = os.path.join(MODEL_DIR, "lr_meta.json")
    meta = {
        "n_train": save_data["n_train"],
        "accuracy": round(save_data["accuracy"], 4) if save_data["accuracy"] else 0,
        "trained_at": save_data["trained_at"],
        "features": save_data["feature_names"],
        "confirm_threshold": LR_CONFIRM_THRESH,
        "veto_threshold": LR_VETO_THRESH,
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)


def _load_model():
    """Load persisted model from disk.

    Returns
    -------
    dict or None
        Model bundle, or None if not found / corrupt.
    """
    if not HAS_JOBLIB or not joblib:
        return None

    path = os.path.join(MODEL_DIR, "lr_latest.joblib")
    if not os.path.exists(path):
        return None

    try:
        data = joblib.load(path)
        return {
            "model": data["model"],
            "scaler": data["scaler"],
            "n_train": data.get("n_train", 0),
            "n_trained": data.get("n_train", 0),
            "accuracy": data.get("accuracy", 0),
            "trained_at": data.get("trained_at", "unknown"),
            "feature_names": data.get("feature_names", list(LR_FEATURE_NAMES)),
        }
    except Exception as e:
        print(f"  [lr_model] Failed to load from disk: {e}")
        return None


# ---------------------------------------------------------------------------
# load_or_train_lr — main entry point
# ---------------------------------------------------------------------------

def load_or_train_lr(store, data_dir=None):
    """Load the LR model from disk, retraining if stale or missing.

    Parameters
    ----------
    store : dict
        NFL store with graded games.
    data_dir : str or None
        Override for MODEL_DIR.  If None, uses default.

    Returns
    -------
    dict or None
        Model bundle, or None if scikit-learn unavailable or insufficient data.
    """
    global MODEL_DIR

    if not HAS_SKLEARN:
        print("  [lr_model] scikit-learn not installed")
        return None

    if data_dir:
        MODEL_DIR = os.path.join(data_dir, "lr_models")

    # Count current graded games
    n_graded = 0
    for run in store.get("runs", []):
        for g in run.get("games", []):
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue
            if isinstance(g.get("homeScore"), (int, float)) and isinstance(g.get("line"), (int, float)):
                n_graded += 1

    # Try loading from disk
    bundle = _load_model()
    if bundle is not None:
        n_train = bundle.get("n_train", 0)
        if n_graded - n_train < RETRAIN_INTERVAL:
            print(f"  [lr_model] Loaded (trained on {n_train}, current graded: {n_graded})")
            return bundle
        else:
            print(f"  [lr_model] Stale (trained on {n_train}, current graded: {n_graded}), retraining...")

    return train_lr_model(store, min_games=MIN_TRAINING_GAMES)

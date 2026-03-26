# core/lr_model.py
# Unified Logistic Regression confirmation model.
#
# Provides shared infrastructure for LR-based spread confirmation:
#   - predict_lr / _explain_lr
#   - Model persistence (_save_model / _load_model)
#   - load_or_train_lr (staleness check + retrain)
#   - train_lr_model (walk-forward training scaffold)
#   - build_team_histories (per-team rolling game buffers)
#   - extract_lr_features (feature vector extraction)
#
# Config variables (set by sport wrapper via _configure):
#   MODEL_DIR, LR_CONFIRM_THRESH, LR_VETO_THRESH,
#   LR_FEATURE_NAMES, _FEATURE_LABELS,
#   RETRAIN_INTERVAL, MIN_TRAINING_GAMES,
#   EXTRA_FEATURE_FN (optional, for sport-specific extra features)

import json
import math
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta

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

# -- Configurable (set by sport wrapper) ------------------------------------

MODEL_DIR = None            # Must be set by wrapper
LR_CONFIRM_THRESH = 0.57
LR_VETO_THRESH = 0.43
RETRAIN_INTERVAL = 20
MIN_TRAINING_GAMES = 80

# Feature configuration -- wrappers can override these
LR_FEATURE_NAMES = [
    # O/U Trend (3) — strongest signals
    "picked_ou_pm_10", "opp_over_pct_10", "picked_over_pct_10",
    # ATS Momentum (1)
    "picked_ats_pm_5",
    # Performance (3)
    "picked_avg_margin_5", "opp_win_pct_5", "picked_win_pct_5",
]

_FEATURE_LABELS = {
    "picked_ou_pm_10":     "Picked O/U margin L10",
    "opp_over_pct_10":     "Opp O/U trend L10",
    "picked_over_pct_10":  "Picked O/U trend L10",
    "picked_ats_pm_5":     "Picked ATS margin L5",
    "picked_avg_margin_5": "Picked avg margin L5",
    "opp_win_pct_5":       "Opp win% L5",
    "picked_win_pct_5":    "Picked win% L5",
    "is_tournament":       "Tournament game",
    "is_neutral":          "Neutral site",
}

# Optional: sport-specific extra feature function
# Signature: extra_fn(game) -> list[float]
# Set by wrapper for NCAA tournament features, etc.
EXTRA_FEATURE_FN = None


def _configure(defaults_module):
    """Configure LR model from a sport-specific defaults module."""
    global MODEL_DIR, LR_CONFIRM_THRESH, LR_VETO_THRESH
    global LR_FEATURE_NAMES, _FEATURE_LABELS
    global RETRAIN_INTERVAL, MIN_TRAINING_GAMES, EXTRA_FEATURE_FN

    LR_CONFIRM_THRESH = getattr(defaults_module, "LR_CONFIRM_THRESH", LR_CONFIRM_THRESH)
    LR_VETO_THRESH = getattr(defaults_module, "LR_VETO_THRESH", LR_VETO_THRESH)
    RETRAIN_INTERVAL = getattr(defaults_module, "LR_RETRAIN_INTERVAL", RETRAIN_INTERVAL)
    MIN_TRAINING_GAMES = getattr(defaults_module, "LR_MIN_TRAINING_GAMES", MIN_TRAINING_GAMES)

    if hasattr(defaults_module, "LR_FEATURE_NAMES"):
        LR_FEATURE_NAMES = defaults_module.LR_FEATURE_NAMES
    if hasattr(defaults_module, "LR_FEATURE_LABELS"):
        _FEATURE_LABELS = defaults_module.LR_FEATURE_LABELS
    if hasattr(defaults_module, "LR_EXTRA_FEATURE_FN"):
        EXTRA_FEATURE_FN = defaults_module.LR_EXTRA_FEATURE_FN


def _get_model_dir():
    if MODEL_DIR:
        return MODEL_DIR
    raise RuntimeError("core.lr_model.MODEL_DIR not configured.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_pct(wins, total):
    if total <= 0:
        return 0.5
    return wins / total


def _safe_mean(values):
    if not values:
        return 0.0
    return sum(values) / len(values)


def _safe_float(v, default=0.0):
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _parse_date(d):
    try:
        return datetime.strptime(str(d).replace("-", "")[:8], "%Y%m%d")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# build_team_histories
# ---------------------------------------------------------------------------

def build_team_histories(store):
    """Walk every graded game in store, build per-team chronological histories.

    Returns dict[team_name -> list[dict]], sorted by date ascending.
    Each entry has fields needed for LR feature extraction.
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
            if not isinstance(g.get("total"), (int, float)):
                continue

            home = g["home"]
            away = g["away"]
            home_score = g["homeScore"]
            away_score = g["awayScore"]
            line = g["line"]
            total = g["total"]
            actual_total = home_score + away_score
            home_margin = home_score - away_score
            # Line convention (sportsbook): -X means HOME favored by X, +X means AWAY favored by X.
            # line IS the home spread already.
            home_spread = line
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

    # Compute rest_days
    for team in histories:
        games = histories[team]
        for i, g in enumerate(games):
            if i == 0:
                g["rest_days"] = 3
            else:
                prev_date = _parse_date(games[i - 1]["date"])
                cur_date = _parse_date(g["date"])
                if prev_date and cur_date:
                    g["rest_days"] = max(0, min((cur_date - prev_date).days - 1, 14))
                else:
                    g["rest_days"] = 1

    return dict(histories)


# ---------------------------------------------------------------------------
# _team_features -- compute rolling stats for one team
# ---------------------------------------------------------------------------

def _team_features(hist, n5=5, n10=10):
    """Compute feature components for one team given history before current game."""
    if not hist:
        return {
            "ats_pct_5": 0.5, "ats_pct_10": 0.5, "ats_pm_5": 0.0,
            "over_pct_10": 0.5, "ou_pm_10": 0.0,
            "win_pct_5": 0.5, "avg_margin_5": 0.0,
            "avg_line": 0.0, "rest_days": 1,
        }

    last5 = hist[-n5:]
    last10 = hist[-n10:]

    ats_pct_5 = _safe_pct(sum(1 for g in last5 if g["covered"]), len(last5))
    ats_pct_10 = _safe_pct(sum(1 for g in last10 if g["covered"]), len(last10))
    ats_pm_5 = _safe_mean([g["margin"] + g["line_for_team"] for g in last5])

    over_pct_10 = _safe_pct(sum(1 for g in last10 if g["over"]), len(last10))
    ou_pm_10 = _safe_mean([g["actual_total"] - g["total"] for g in last10])

    win_pct_5 = _safe_pct(sum(1 for g in last5 if g["margin"] > 0), len(last5))
    avg_margin_5 = _safe_mean([g["margin"] for g in last5])

    avg_line = _safe_mean([g["line_for_team"] for g in hist])
    rest_days = hist[-1].get("rest_days", 1) if hist else 1

    return {
        "ats_pct_5": ats_pct_5, "ats_pct_10": ats_pct_10, "ats_pm_5": ats_pm_5,
        "over_pct_10": over_pct_10, "ou_pm_10": ou_pm_10,
        "win_pct_5": win_pct_5, "avg_margin_5": avg_margin_5,
        "avg_line": avg_line, "rest_days": rest_days,
    }


# ---------------------------------------------------------------------------
# extract_lr_features
# ---------------------------------------------------------------------------

def extract_lr_features(home_hist, away_hist, game, home_lines=None, away_lines=None, picked_home=True):
    """Build the feature vector for a single game, oriented to the picked side.

    Parameters
    ----------
    home_hist : list  -- home team's history *before* this game
    away_hist : list  -- away team's history *before* this game
    game      : dict  -- must have 'line', 'total'
    home_lines : list -- optional, all lines seen by home (for avg)
    away_lines : list -- optional, all lines seen by away (for avg)
    picked_home : bool -- True if the model picked the home team

    Returns list[float] matching LR_FEATURE_NAMES, or None on error.
    """
    if len(home_hist) < 5 or len(away_hist) < 5:
        return None

    try:
        # Orient features to picked/opponent (not home/away)
        if picked_home:
            picked_hist, opp_hist = home_hist, away_hist
            picked_lines, opp_lines = home_lines, away_lines
        else:
            picked_hist, opp_hist = away_hist, home_hist
            picked_lines, opp_lines = away_lines, home_lines

        pf = _team_features(picked_hist)
        of = _team_features(opp_hist)

        line = game.get("line", 0) or 0
        abs_line = abs(line)

        # Line vs picked team's average line
        if picked_lines:
            if isinstance(picked_lines[0], dict):
                avg_picked_line = _safe_mean([g.get("line_for_team", g.get("line", 0)) for g in picked_lines])
            else:
                avg_picked_line = _safe_mean(picked_lines)
        else:
            avg_picked_line = pf["avg_line"]

        # picked team's spread from their perspective
        picked_spread = line if picked_home else -line
        line_vs_team_avg = picked_spread - avg_picked_line

        # Picked is fav/dog
        # Sportsbook: line < 0 = home favored, line > 0 = away favored
        if picked_home:
            picked_is_fav = 1.0 if line < 0 else 0.0
        else:
            picked_is_fav = 1.0 if line > 0 else 0.0
        picked_is_dog = 1.0 - picked_is_fav

        # Rest days
        run_date = game.get("_run_date") or game.get("date")
        cur = _parse_date(run_date) if run_date else None

        if cur and picked_hist:
            last_picked = _parse_date(picked_hist[-1].get("date"))
            picked_rest = max(0, min((cur - last_picked).days - 1, 14)) if last_picked else pf["rest_days"]
        else:
            picked_rest = pf["rest_days"]

        if cur and opp_hist:
            last_opp = _parse_date(opp_hist[-1].get("date"))
            opp_rest = max(0, min((cur - last_opp).days - 1, 14)) if last_opp else of["rest_days"]
        else:
            opp_rest = of["rest_days"]

        rest_advantage = picked_rest - opp_rest

        # Build features — top 7 by importance, all from picked team's perspective
        features = [
            pf["ou_pm_10"], of["over_pct_10"], pf["over_pct_10"],
            pf["ats_pm_5"],
            pf["avg_margin_5"], of["win_pct_5"], pf["win_pct_5"],
        ]

        # Add sport-specific extra features if configured
        if EXTRA_FEATURE_FN:
            extras = EXTRA_FEATURE_FN(game)
            if extras:
                features.extend(extras)

        # Validate -- no NaN/Inf
        for i, v in enumerate(features):
            if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
                features[i] = 0.0

        return features

    except Exception:
        return None


# ---------------------------------------------------------------------------
# build_lr_features_for_game -- convenience for live predictions
# ---------------------------------------------------------------------------

def build_lr_features_for_game(game, team_histories, run_date=None, picked_home=True):
    """Build LR features for a single upcoming game.

    Parameters
    ----------
    game : dict -- must have 'home', 'away', 'line', 'total'
    team_histories : dict -- output of build_team_histories(store)
    run_date : str or None -- YYYYMMDD for rest-day calculation
    picked_home : bool -- True if the model picked the home team

    Returns list or None.
    """
    home = game.get("home", "")
    away = game.get("away", "")

    home_hist = team_histories.get(home, [])
    away_hist = team_histories.get(away, [])

    game_with_date = {**game}
    if run_date:
        game_with_date["_run_date"] = run_date

    return extract_lr_features(
        home_hist, away_hist, game_with_date,
        home_hist, away_hist,
        picked_home=picked_home,
    )


# ---------------------------------------------------------------------------
# _explain_lr
# ---------------------------------------------------------------------------

def _explain_lr(model_bundle, features, top_n=3, game=None, picked_home=True):
    """Return the top contributing features pushing AGAINST the picked side.

    LR predicts P(picked team covers). Most negative contributions
    hurt the pick (they push probability down).
    """
    if model_bundle is None or features is None:
        return []
    if not HAS_SKLEARN:
        return []

    model = model_bundle["model"]
    scaler = model_bundle["scaler"]
    names = model_bundle.get("feature_names", LR_FEATURE_NAMES)

    # Build team-name substitutions for labels
    home_name = (game or {}).get("home", "Picked")
    away_name = (game or {}).get("away", "Opp")
    picked_name = home_name if picked_home else away_name
    opp_name = away_name if picked_home else home_name

    try:
        X_scaled = scaler.transform(np.array([features], dtype=np.float64))[0]
        coefs = model.coef_[0]
    except Exception:
        return []

    contributions = []
    for i, (name, coef, scaled_val, raw_val) in enumerate(
        zip(names, coefs, X_scaled, features)
    ):
        contrib = coef * scaled_val
        contributions.append((contrib, name, raw_val))

    # Most negative = hurts pick (features already oriented to picked side)
    contributions.sort(key=lambda x: x[0])

    reasons = []
    for contrib, name, raw_val in contributions[:top_n]:
        label = _FEATURE_LABELS.get(name, name)
        # Replace generic Picked/Opp with actual team names
        label = label.replace("Picked", picked_name).replace("Opp", opp_name)
        if "pct" in name or "win_pct" in name:
            val_str = f"{raw_val * 100:.0f}%"
        elif "rest" in name:
            val_str = f"{raw_val:.0f}d"
        elif "margin" in name or "pm" in name:
            val_str = f"{raw_val:+.1f}"
        elif name == "abs_line":
            val_str = f"{raw_val:.1f}"
        elif name in ("home_is_fav", "is_tournament", "is_neutral"):
            val_str = "yes" if raw_val > 0.5 else "no"
        elif "line_vs" in name:
            val_str = f"{raw_val:+.1f}"
        else:
            val_str = f"{raw_val:.2f}"
        reasons.append(f"{label}: {val_str}")

    return reasons


# ---------------------------------------------------------------------------
# predict_lr
# ---------------------------------------------------------------------------

def predict_lr(model_bundle, features, game=None):
    """Run a single prediction.

    Returns dict with lr_prob (float), lr_verdict ("CONFIRM"/"VETO"/"NEUTRAL"),
    and lr_reasons (list of strings, only populated on VETO).
    game : optional dict with 'home'/'away' keys for team-name labels in reasons.
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
        print(f"lr_model: predict error: {e}")
        return {"lr_prob": None, "lr_verdict": "NEUTRAL", "lr_reasons": []}


def predict_lr_for_pick(model_bundle, features, picked_home, game=None):
    """Predict and return a verdict for the picked side.

    Features are already oriented to the picked team's perspective,
    so lr_prob IS P(picked team covers) — no flip needed.

    Returns dict with lr_prob (P(picked covers)), lr_pick_prob (same),
    lr_verdict ('confirm', 'veto', or 'neutral'), and lr_reasons on veto.
    """
    raw = predict_lr(model_bundle, features, game=game)
    if raw["lr_prob"] is None:
        return {"lr_prob": None, "lr_pick_prob": None, "lr_verdict": "neutral", "lr_reasons": []}

    p_pick = raw["lr_prob"]  # Already P(picked covers), no flip needed

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
        "lr_prob": round(p_pick, 4),
        "lr_pick_prob": round(p_pick, 4),
        "lr_verdict": verdict,
        "lr_reasons": reasons,
    }


# ---------------------------------------------------------------------------
# train_lr_model
# ---------------------------------------------------------------------------

def train_lr_model(store, min_games=None):
    """Train a LogisticRegression on graded games from the store.

    Target: home team covers the spread (binary).
    Only games where both teams have >= 5 prior graded games are used.
    """
    if not HAS_SKLEARN:
        print("lr_model: scikit-learn not installed, cannot train")
        return None

    if min_games is None:
        min_games = MIN_TRAINING_GAMES

    print("lr_model: Building team histories...")
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
            if not isinstance(g.get("total"), (int, float)):
                continue

            home = g["home"]
            away = g["away"]

            # Determine which side the model picked
            pick = g.get("sPick", "")
            if not pick or pick == "PASS":
                # No pick — still record history but skip training
                _append_to_running(running, g, run_date)
                continue
            picked_home = home in pick

            # Extract features oriented to picked side
            game_with_date = {**g, "_run_date": run_date, "date": run_date}
            features = extract_lr_features(
                running[home], running[away], game_with_date,
                home_lines=[x["line_for_team"] for x in running[home]] if running[home] else None,
                away_lines=[x["line_for_team"] for x in running[away]] if running[away] else None,
                picked_home=picked_home,
            )

            if features is not None:
                home_margin = g["homeScore"] - g["awayScore"]
                line = g["line"]
                ats_margin = home_margin + line

                # Label: did the PICKED team cover?
                if ats_margin != 0:
                    if picked_home:
                        picked_covered = 1 if ats_margin > 0 else 0
                    else:
                        picked_covered = 1 if ats_margin < 0 else 0
                    X_rows.append(features)
                    y_rows.append(picked_covered)

            # Record into running histories
            _append_to_running(running, g, run_date)

    if len(X_rows) < min_games:
        print(f"lr_model: Only {len(X_rows)} training samples (need {min_games}), skipping")
        return None

    X = np.array(X_rows, dtype=np.float64)
    y = np.array(y_rows, dtype=np.int32)

    print(f"lr_model: Training on {len(X)} picks (picked covers: {y.sum()}, picked loses: {len(y) - y.sum()})...")

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    model = LogisticRegression(
        l1_ratio=0,
        C=1.0,
        class_weight="balanced",
        max_iter=1000,
        solver="lbfgs",
        random_state=42,
    )
    model.fit(X_scaled, y)

    preds = model.predict(X_scaled)
    accuracy = float(np.mean(preds == y))
    print(f"lr_model: In-sample accuracy: {accuracy:.3f}")

    bundle = {
        "model": model,
        "scaler": scaler,
        "n_train": len(X),
        "n_trained": len(X),  # alias for compatibility
        "accuracy": accuracy,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "feature_names": list(LR_FEATURE_NAMES),
    }

    _save_model(bundle)
    return bundle


def _append_to_running(running, g, run_date):
    """Append a graded game to the running per-team histories."""
    home = g["home"]
    away = g["away"]
    home_score = g["homeScore"]
    away_score = g["awayScore"]
    line = g["line"]
    total = g["total"]
    actual_total = home_score + away_score
    home_margin = home_score - away_score
    # Line convention (sportsbook): -X means HOME favored by X, +X means AWAY favored by X.
    # line IS the home spread already.
    home_spread = line
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

    home_entry = {
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
    }
    if running[home]:
        prev_date = _parse_date(running[home][-1]["date"])
        cur_date = _parse_date(run_date)
        if prev_date and cur_date:
            home_entry["rest_days"] = max(0, min((cur_date - prev_date).days - 1, 14))
        else:
            home_entry["rest_days"] = 1
    else:
        home_entry["rest_days"] = 3
    running[home].append(home_entry)

    away_entry = {
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
    }
    if running[away]:
        prev_date = _parse_date(running[away][-1]["date"])
        cur_date = _parse_date(run_date)
        if prev_date and cur_date:
            away_entry["rest_days"] = max(0, min((cur_date - prev_date).days - 1, 14))
        else:
            away_entry["rest_days"] = 1
    else:
        away_entry["rest_days"] = 3
    running[away].append(away_entry)


# ---------------------------------------------------------------------------
# Model persistence
# ---------------------------------------------------------------------------

def _save_model(bundle):
    """Persist model + scaler to disk using joblib."""
    if not HAS_JOBLIB or not joblib:
        print("lr_model: joblib not available, model not saved to disk")
        return

    model_dir = _get_model_dir()
    os.makedirs(model_dir, exist_ok=True)

    path = os.path.join(model_dir, "lr_latest.joblib")
    save_data = {
        "model": bundle["model"],
        "scaler": bundle["scaler"],
        "n_train": bundle.get("n_train", bundle.get("n_trained", 0)),
        "accuracy": bundle.get("accuracy", 0),
        "trained_at": bundle.get("trained_at", "unknown"),
        "feature_names": bundle.get("feature_names", list(LR_FEATURE_NAMES)),
    }
    joblib.dump(save_data, path)
    print(f"lr_model: Saved to {path}")

    # Also save metadata as JSON for inspection
    meta_path = os.path.join(model_dir, "lr_meta.json")
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
    """Load persisted model from disk. Returns bundle dict or None."""
    if not HAS_JOBLIB or not joblib:
        return None

    model_dir = _get_model_dir()
    path = os.path.join(model_dir, "lr_latest.joblib")
    if not os.path.exists(path):
        return None

    try:
        data = joblib.load(path)
        return {
            "model": data["model"],
            "scaler": data["scaler"],
            "n_train": data.get("n_train", data.get("n_trained", 0)),
            "n_trained": data.get("n_train", data.get("n_trained", 0)),
            "accuracy": data.get("accuracy", 0),
            "trained_at": data.get("trained_at", "unknown"),
            "feature_names": data.get("feature_names", list(LR_FEATURE_NAMES)),
        }
    except Exception as e:
        print(f"lr_model: Failed to load from disk: {e}")
        return None


# ---------------------------------------------------------------------------
# load_or_train_lr -- main entry point
# ---------------------------------------------------------------------------

def load_or_train_lr(store):
    """Load the LR model from disk, retraining if stale or missing."""
    if not HAS_SKLEARN:
        print("lr_model: scikit-learn not installed")
        return None

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
            print(f"lr_model: Loaded (trained on {n_train}, current graded: {n_graded})")
            return bundle
        else:
            print(f"lr_model: Stale (trained on {n_train}, current graded: {n_graded}), retraining...")

    return train_lr_model(store, min_games=MIN_TRAINING_GAMES)

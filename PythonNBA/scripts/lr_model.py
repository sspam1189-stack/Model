# scripts/lr_model.py
# --------------------------------------------------------------------------
# Logistic Regression confirmation model for NBA spread picks.
#
# This model uses INDEPENDENT features from the Bayesian model -- no stat
# deltas. All features are computed from rolling game history (ATS momentum,
# O/U trends, line context, rest days, performance momentum).
#
# The LR acts as a second opinion: confirm if it agrees with the Bayesian
# pick, veto if it strongly disagrees, neutral otherwise.
# --------------------------------------------------------------------------

import os
import math
import re
from datetime import datetime, timedelta
from collections import defaultdict

try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

try:
    import joblib
    HAS_JOBLIB = True
except ImportError:
    HAS_JOBLIB = False

from defaults import LR_CONFIRM_THRESH, LR_VETO_THRESH

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_DIR = os.path.dirname(os.path.abspath(__file__))
_MODEL_DIR = os.path.join(_DIR, "..", "data", "lr_models")
_MODEL_PATH = os.path.join(_MODEL_DIR, "lr_spread.pkl")
_SCALER_PATH = os.path.join(_MODEL_DIR, "lr_scaler.pkl")
_META_PATH = os.path.join(_MODEL_DIR, "lr_meta.pkl")

# ---------------------------------------------------------------------------
# Feature names (22 features, all independent from Bayesian stat deltas)
# ---------------------------------------------------------------------------

LR_FEATURE_NAMES = [
    # ATS momentum
    "away_ats_pct_5",
    "home_ats_pct_5",
    "away_ats_pct_10",
    "home_ats_pct_10",
    "away_ats_pm_5",
    "home_ats_pm_5",
    # O/U trend
    "away_over_pct_10",
    "home_over_pct_10",
    "away_ou_pm_10",
    "home_ou_pm_10",
    # Line context
    "abs_line",
    "line_vs_home_avg",
    "line_vs_away_avg",
    "home_is_fav",
    # Rest
    "away_rest_days",
    "home_rest_days",
    "rest_advantage",
    # Performance momentum
    "away_win_pct_5",
    "home_win_pct_5",
    "away_avg_margin_5",
    "home_avg_margin_5",
]


# ---------------------------------------------------------------------------
# Team-name normalisation (lightweight, mirrors model_engine._norm_key)
# ---------------------------------------------------------------------------

def _norm_team(name):
    """Lowercase, strip non-alphanumeric, collapse whitespace."""
    s = str(name or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# build_team_histories
# ---------------------------------------------------------------------------

def build_team_histories(store):
    """Walk history chronologically, build per-team rolling game buffers.

    Each team entry is a list of dicts, one per graded game the team played,
    in chronological order.  Each dict contains the fields needed for LR
    feature extraction:
        date, line, total, homeScore, awayScore, is_home, opp,
        sPick (parsed side), sResult
    """
    histories = defaultdict(list)

    runs = store.get("runs") or []
    for run in sorted(runs, key=lambda r: r.get("date", "")):
        run_date = run.get("date", "")
        for g in run.get("games", []):
            # Need scores to grade
            if not isinstance(g.get("homeScore"), (int, float)):
                continue
            if not isinstance(g.get("awayScore"), (int, float)):
                continue

            home = g.get("home", "")
            away = g.get("away", "")
            line = g.get("line")
            total = g.get("total")
            if line is None or total is None:
                continue

            home_score = g["homeScore"]
            away_score = g["awayScore"]
            actual_total = home_score + away_score
            margin_vs_line = home_score - away_score - line  # >0 means home covered

            # Determine which side the model picked (if any)
            s_pick = g.get("sPick", "PASS")
            s_result = g.get("sResult")

            # Build record for home team
            home_rec = {
                "date": run_date,
                "line": line,
                "total": total,
                "homeScore": home_score,
                "awayScore": away_score,
                "is_home": True,
                "opp": away,
                "margin_vs_line": margin_vs_line,
                "actual_total": actual_total,
                "ou_margin": actual_total - total,
                "game_margin": home_score - away_score,
                "won": home_score > away_score,
                "covered": margin_vs_line > 0,
                "went_over": actual_total > total,
                "sPick": s_pick,
                "sResult": s_result,
            }

            # Build record for away team (flip perspective)
            away_rec = {
                "date": run_date,
                "line": line,
                "total": total,
                "homeScore": home_score,
                "awayScore": away_score,
                "is_home": False,
                "opp": home,
                "margin_vs_line": margin_vs_line,
                "actual_total": actual_total,
                "ou_margin": actual_total - total,
                "game_margin": away_score - home_score,
                "won": away_score > home_score,
                # Away covers when margin_vs_line < 0 (home did NOT cover)
                "covered": margin_vs_line < 0,
                "went_over": actual_total > total,
                "sPick": s_pick,
                "sResult": s_result,
            }

            histories[home].append(home_rec)
            histories[away].append(away_rec)

    return dict(histories)


# ---------------------------------------------------------------------------
# Helper: compute rolling stats from a team buffer
# ---------------------------------------------------------------------------

def _ats_pct(buf, n):
    """ATS cover rate over last n games.  Returns None if < n games."""
    if len(buf) < n:
        return None
    window = buf[-n:]
    covers = sum(1 for g in window if g["covered"])
    return covers / n


def _ats_pm(buf, n):
    """Average margin vs spread over last n games."""
    if len(buf) < n:
        return None
    window = buf[-n:]
    # margin_vs_line is from home perspective; for away team, flip sign
    total = 0.0
    for g in window:
        if g["is_home"]:
            total += g["margin_vs_line"]
        else:
            total -= g["margin_vs_line"]
    return total / n


def _over_pct(buf, n):
    """Over hit rate over last n games."""
    if len(buf) < n:
        return None
    window = buf[-n:]
    overs = sum(1 for g in window if g["went_over"])
    return overs / n


def _ou_pm(buf, n):
    """Average actual total minus O/U line over last n games."""
    if len(buf) < n:
        return None
    window = buf[-n:]
    return sum(g["ou_margin"] for g in window) / n


def _win_pct(buf, n):
    """Straight-up win rate over last n games."""
    if len(buf) < n:
        return None
    window = buf[-n:]
    wins = sum(1 for g in window if g["won"])
    return wins / n


def _avg_margin(buf, n):
    """Average margin of victory (from that team's perspective) over last n."""
    if len(buf) < n:
        return None
    window = buf[-n:]
    return sum(g["game_margin"] for g in window) / n


def _season_avg_line(buf):
    """Average absolute spread the team has seen this season (as home/away).

    Returns the mean of abs(line) across all games in the buffer.
    Accepts either a list of game dicts (with "line" key) or a flat list of floats.
    """
    if not buf:
        return 0.0
    if isinstance(buf[0], (int, float)):
        return sum(abs(v) for v in buf) / len(buf)
    return sum(abs(g["line"]) for g in buf) / len(buf)


def _rest_days(buf, current_date_str):
    """Days since last game for this team. Returns 7 if no prior game or parse error."""
    if not buf:
        return 7
    last_date_str = buf[-1].get("date", "")
    try:
        last_dt = datetime.strptime(last_date_str, "%Y%m%d")
        curr_dt = datetime.strptime(current_date_str, "%Y%m%d")
        delta = (curr_dt - last_dt).days
        # Clamp to reasonable range
        return max(0, min(delta, 14))
    except (ValueError, TypeError):
        return 7


# ---------------------------------------------------------------------------
# extract_lr_features
# ---------------------------------------------------------------------------

def extract_lr_features(home_history, away_history, game,
                        home_season_lines, away_season_lines):
    """Extract LR feature vector from rolling team histories.

    Parameters
    ----------
    home_history : list
        Rolling game buffer for the home team (up to but NOT including this game).
    away_history : list
        Rolling game buffer for the away team.
    game : dict
        The current game dict (must have line, total, home, away, date or parent run date).
    home_season_lines : list
        All lines the home team has seen this season (for line_vs_team_avg).
    away_season_lines : list
        All lines the away team has seen this season.

    Returns
    -------
    list or None
        Feature vector of length len(LR_FEATURE_NAMES), or None if either
        team has fewer than 5 games in their history.
    """
    if len(home_history) < 5 or len(away_history) < 5:
        return None

    line = game.get("line", 0)
    run_date = game.get("_run_date", game.get("date", ""))

    # ATS momentum
    away_ats_pct_5 = _ats_pct(away_history, 5)
    home_ats_pct_5 = _ats_pct(home_history, 5)
    away_ats_pct_10 = _ats_pct(away_history, 10) if len(away_history) >= 10 else _ats_pct(away_history, len(away_history))
    home_ats_pct_10 = _ats_pct(home_history, 10) if len(home_history) >= 10 else _ats_pct(home_history, len(home_history))
    away_ats_pm_5 = _ats_pm(away_history, 5)
    home_ats_pm_5 = _ats_pm(home_history, 5)

    # O/U trend
    away_over_pct_10 = _over_pct(away_history, 10) if len(away_history) >= 10 else _over_pct(away_history, len(away_history))
    home_over_pct_10 = _over_pct(home_history, 10) if len(home_history) >= 10 else _over_pct(home_history, len(home_history))
    away_ou_pm_10 = _ou_pm(away_history, 10) if len(away_history) >= 10 else _ou_pm(away_history, len(away_history))
    home_ou_pm_10 = _ou_pm(home_history, 10) if len(home_history) >= 10 else _ou_pm(home_history, len(home_history))

    # Line context
    abs_line = abs(line)
    home_avg_line = _season_avg_line(home_season_lines) if home_season_lines else abs_line
    away_avg_line = _season_avg_line(away_season_lines) if away_season_lines else abs_line
    line_vs_home_avg = abs_line - home_avg_line
    line_vs_away_avg = abs_line - away_avg_line
    home_is_fav = 1.0 if line > 0 else 0.0  # positive line = away is underdog = home is fav

    # Rest days
    away_rest = _rest_days(away_history, run_date)
    home_rest = _rest_days(home_history, run_date)
    rest_adv = home_rest - away_rest

    # Performance momentum
    away_win_pct_5 = _win_pct(away_history, 5)
    home_win_pct_5 = _win_pct(home_history, 5)
    away_avg_margin_5 = _avg_margin(away_history, 5)
    home_avg_margin_5 = _avg_margin(home_history, 5)

    # Any None means we can't build a valid vector
    feats = [
        away_ats_pct_5, home_ats_pct_5,
        away_ats_pct_10, home_ats_pct_10,
        away_ats_pm_5, home_ats_pm_5,
        away_over_pct_10, home_over_pct_10,
        away_ou_pm_10, home_ou_pm_10,
        abs_line, line_vs_home_avg, line_vs_away_avg, home_is_fav,
        away_rest, home_rest, rest_adv,
        away_win_pct_5, home_win_pct_5,
        away_avg_margin_5, home_avg_margin_5,
    ]

    if any(f is None for f in feats):
        return None

    return feats


# ---------------------------------------------------------------------------
# train_lr_model
# ---------------------------------------------------------------------------

def train_lr_model(store, min_games=80):
    """Walk-forward training of Logistic Regression on historical games.

    Walks through history chronologically, maintaining per-team rolling
    buffers.  For each graded game with a spread pick, extracts features
    and labels (did the home team cover?).

    Parameters
    ----------
    store : dict
        The history store (must contain 'runs').
    min_games : int
        Minimum number of labelled samples before fitting the model.

    Returns
    -------
    dict or None
        {model, scaler, n_trained, feature_names} or None if not enough data.
    """
    if not HAS_SKLEARN:
        print("lr_model: sklearn not available -- cannot train")
        return None

    # Build rolling histories game by game
    team_bufs = defaultdict(list)       # team -> list of game records
    team_season_lines = defaultdict(list)  # team -> list of game records (for avg line)

    X = []
    y = []

    runs = store.get("runs") or []
    for run in sorted(runs, key=lambda r: r.get("date", "")):
        run_date = run.get("date", "")
        for g in run.get("games", []):
            # Need scores to compute label
            if not isinstance(g.get("homeScore"), (int, float)):
                continue
            if not isinstance(g.get("awayScore"), (int, float)):
                continue

            home = g.get("home", "")
            away = g.get("away", "")
            line = g.get("line")
            total = g.get("total")
            if line is None or total is None:
                continue

            home_score = g["homeScore"]
            away_score = g["awayScore"]
            margin_vs_line = home_score - away_score - line

            # Extract features BEFORE adding this game to histories
            game_with_date = {**g, "_run_date": run_date}
            feats = extract_lr_features(
                list(team_bufs[home]),
                list(team_bufs[away]),
                game_with_date,
                list(team_season_lines[home]),
                list(team_season_lines[away]),
            )

            if feats is not None:
                # Label: did home cover?
                if margin_vs_line == 0:
                    # Push -- skip (ambiguous label)
                    pass
                else:
                    label = 1 if margin_vs_line > 0 else 0
                    X.append(feats)
                    y.append(label)

            # Now add game to team histories
            actual_total = home_score + away_score
            base_rec = {
                "date": run_date,
                "line": line,
                "total": total,
                "homeScore": home_score,
                "awayScore": away_score,
                "actual_total": actual_total,
                "margin_vs_line": margin_vs_line,
                "ou_margin": actual_total - total,
                "sPick": g.get("sPick", "PASS"),
                "sResult": g.get("sResult"),
            }

            team_bufs[home].append({
                **base_rec,
                "is_home": True,
                "opp": away,
                "game_margin": home_score - away_score,
                "won": home_score > away_score,
                "covered": margin_vs_line > 0,
                "went_over": actual_total > total,
            })
            team_bufs[away].append({
                **base_rec,
                "is_home": False,
                "opp": home,
                "game_margin": away_score - home_score,
                "won": away_score > home_score,
                "covered": margin_vs_line < 0,
                "went_over": actual_total > total,
            })
            team_season_lines[home].append(base_rec)
            team_season_lines[away].append(base_rec)

    if len(X) < min_games:
        print(f"lr_model: only {len(X)} samples (need {min_games}) -- skipping training")
        return None

    print(f"lr_model: training on {len(X)} samples ({sum(y)} home covers, "
          f"{len(y) - sum(y)} away covers)")

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # l1_ratio=0 is equivalent to penalty='l2' but avoids deprecation warning
    # in sklearn >= 1.8.  Fall back to penalty='l2' for older versions.
    try:
        model = LogisticRegression(
            C=1.0,
            l1_ratio=0,
            class_weight="balanced",
            max_iter=1000,
            solver="lbfgs",
            random_state=42,
        )
    except TypeError:
        model = LogisticRegression(
            penalty="l2",
            C=1.0,
            class_weight="balanced",
            max_iter=1000,
            solver="lbfgs",
            random_state=42,
        )
    model.fit(X_scaled, y)

    # Quick in-sample accuracy (informational only)
    preds = model.predict(X_scaled)
    acc = sum(1 for p, t in zip(preds, y) if p == t) / len(y)
    print(f"lr_model: in-sample accuracy = {acc:.3f}")

    bundle = {
        "model": model,
        "scaler": scaler,
        "n_trained": len(X),
        "feature_names": LR_FEATURE_NAMES,
    }

    # Save to disk
    _save_model(bundle)

    return bundle


# ---------------------------------------------------------------------------
# predict_lr
# ---------------------------------------------------------------------------

def predict_lr(model_bundle, features):
    """Predict using the trained LR model.

    Parameters
    ----------
    model_bundle : dict
        Output of train_lr_model (model, scaler, etc.).
    features : list
        Feature vector of length len(LR_FEATURE_NAMES).

    Returns
    -------
    dict
        {lr_prob: float, lr_verdict: str}
        lr_prob is P(home covers).
        lr_verdict is 'confirm', 'veto', or 'neutral'.
    """
    if model_bundle is None or features is None:
        return {"lr_prob": None, "lr_verdict": "neutral"}

    model = model_bundle["model"]
    scaler = model_bundle["scaler"]

    try:
        X = scaler.transform([features])
        proba = model.predict_proba(X)[0]
        # proba[1] = P(home covers), proba[0] = P(away covers)
        p_home = float(proba[1]) if len(proba) > 1 else 0.5
    except Exception as e:
        print(f"lr_model: predict error -- {e}")
        return {"lr_prob": None, "lr_verdict": "neutral"}

    # Verdict relative to the pick side is determined by the caller.
    # Here we return raw P(home cover) and a generic verdict.
    if p_home >= LR_CONFIRM_THRESH:
        verdict = "confirm_home"
    elif p_home <= LR_VETO_THRESH:
        verdict = "confirm_away"
    else:
        verdict = "neutral"

    return {"lr_prob": round(p_home, 4), "lr_verdict": verdict}


def predict_lr_for_pick(model_bundle, features, picked_home):
    """Predict and return a verdict relative to the picked side.

    Parameters
    ----------
    model_bundle : dict
        Output of train_lr_model.
    features : list
        Feature vector.
    picked_home : bool
        True if the Bayesian model picked the home side to cover.

    Returns
    -------
    dict
        {lr_prob: float, lr_pick_prob: float, lr_verdict: str}
        lr_prob is P(home covers).
        lr_pick_prob is P(picked side covers).
        lr_verdict is 'confirm', 'veto', or 'neutral'.
    """
    raw = predict_lr(model_bundle, features)
    if raw["lr_prob"] is None:
        return {"lr_prob": None, "lr_pick_prob": None, "lr_verdict": "neutral"}

    p_home = raw["lr_prob"]
    p_pick = p_home if picked_home else (1.0 - p_home)

    if p_pick >= LR_CONFIRM_THRESH:
        verdict = "confirm"
    elif p_pick <= LR_VETO_THRESH:
        verdict = "veto"
    else:
        verdict = "neutral"

    return {
        "lr_prob": round(p_home, 4),
        "lr_pick_prob": round(p_pick, 4),
        "lr_verdict": verdict,
    }


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _save_model(bundle):
    """Save model, scaler, and metadata to disk."""
    if not HAS_JOBLIB:
        print("lr_model: joblib not available -- model not saved to disk")
        return

    os.makedirs(_MODEL_DIR, exist_ok=True)
    try:
        joblib.dump(bundle["model"], _MODEL_PATH)
        joblib.dump(bundle["scaler"], _SCALER_PATH)
        meta = {
            "n_trained": bundle["n_trained"],
            "feature_names": bundle["feature_names"],
        }
        joblib.dump(meta, _META_PATH)
        print(f"lr_model: saved to {_MODEL_DIR} (n={bundle['n_trained']})")
    except Exception as e:
        print(f"lr_model: save error -- {e}")


def _load_model():
    """Load model, scaler, and metadata from disk.

    Returns
    -------
    dict or None
        Same structure as train_lr_model output, or None if files missing.
    """
    if not HAS_SKLEARN or not HAS_JOBLIB:
        return None

    if not (os.path.exists(_MODEL_PATH) and os.path.exists(_SCALER_PATH)
            and os.path.exists(_META_PATH)):
        return None

    try:
        model = joblib.load(_MODEL_PATH)
        scaler = joblib.load(_SCALER_PATH)
        meta = joblib.load(_META_PATH)
        return {
            "model": model,
            "scaler": scaler,
            "n_trained": meta.get("n_trained", 0),
            "feature_names": meta.get("feature_names", LR_FEATURE_NAMES),
        }
    except Exception as e:
        print(f"lr_model: load error -- {e}")
        return None


# ---------------------------------------------------------------------------
# load_or_train_lr
# ---------------------------------------------------------------------------

def load_or_train_lr(store):
    """Load an existing LR model from disk, or retrain if stale / missing.

    Retrains if:
      - No saved model on disk
      - The number of graded games in the store exceeds the saved model's
        n_trained by at least LR_RETRAIN_INTERVAL (20)

    Parameters
    ----------
    store : dict
        The history store.

    Returns
    -------
    dict or None
        Model bundle, or None if not enough data / sklearn unavailable.
    """
    from defaults import LR_MIN_TRAINING_GAMES as MIN_GAMES

    # Try to import retrain interval; fall back to 20
    try:
        from defaults import LR_RETRAIN_INTERVAL as RETRAIN_INTERVAL
    except ImportError:
        RETRAIN_INTERVAL = 20

    # Count total graded games in store
    n_graded = 0
    for run in (store.get("runs") or []):
        for g in run.get("games", []):
            if isinstance(g.get("homeScore"), (int, float)):
                n_graded += 1

    # Try loading from disk
    bundle = _load_model()
    if bundle is not None:
        n_trained = bundle.get("n_trained", 0)
        if n_graded - n_trained < RETRAIN_INTERVAL:
            print(f"lr_model: loaded from disk (n={n_trained}, "
                  f"{n_graded - n_trained} new games, next retrain at +{RETRAIN_INTERVAL})")
            return bundle
        else:
            print(f"lr_model: {n_graded - n_trained} new games since last train -- retraining")

    # Train fresh
    return train_lr_model(store, min_games=MIN_GAMES)


# ---------------------------------------------------------------------------
# Convenience: build features for a live game
# ---------------------------------------------------------------------------

def build_lr_features_for_game(game, team_histories, run_date=None):
    """Build LR features for a single upcoming game.

    Parameters
    ----------
    game : dict
        Must have 'home', 'away', 'line', 'total'.
    team_histories : dict
        Output of build_team_histories(store).
    run_date : str or None
        Current date as YYYYMMDD for rest-day calculation.
        If None, rest features use fallback value.

    Returns
    -------
    list or None
        Feature vector, or None if insufficient history.
    """
    home = game.get("home", "")
    away = game.get("away", "")

    home_hist = team_histories.get(home, [])
    away_hist = team_histories.get(away, [])

    game_with_date = {**game}
    if run_date:
        game_with_date["_run_date"] = run_date

    return extract_lr_features(
        home_hist,
        away_hist,
        game_with_date,
        home_hist,  # season lines = full history for that team
        away_hist,
    )

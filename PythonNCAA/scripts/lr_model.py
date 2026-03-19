# scripts/lr_model.py
# --------------------------------------------------------------------------
# Logistic Regression confirmation model for NCAA basketball.
#
# Uses INDEPENDENT features from the Bayesian model — no stat deltas like
# dTS, dTO, dORR, dNET.  Instead, features are computed from rolling
# per-team game history: ATS momentum, O/U trends, line context, rest,
# performance momentum, and tournament flags.
#
# The LR model predicts P(Bayesian pick covers) and is used to confirm
# or veto the Bayesian model's chosen side.
# --------------------------------------------------------------------------

import math
import os
import re
from datetime import datetime, timedelta

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

# ---------------------------------------------------------------------------
# Thresholds — imported from defaults.py when available, else hard-coded.
# ---------------------------------------------------------------------------

try:
    from defaults import LR_CONFIRM_THRESH, LR_VETO_THRESH
except ImportError:
    LR_CONFIRM_THRESH = 0.55
    LR_VETO_THRESH = 0.45

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_DIR = os.path.dirname(os.path.abspath(__file__))
_MODEL_DIR = os.path.join(_DIR, "..", "data", "lr_models")
_MODEL_PATH = os.path.join(_MODEL_DIR, "lr_ncaa.joblib")
_SCALER_PATH = os.path.join(_MODEL_DIR, "lr_ncaa_scaler.joblib")
_META_PATH = os.path.join(_MODEL_DIR, "lr_ncaa_meta.joblib")

# ---------------------------------------------------------------------------
# Feature names (must match extract_lr_features order)
# ---------------------------------------------------------------------------

LR_FEATURE_NAMES = [
    # ATS Momentum
    "away_ats_pct_5",
    "home_ats_pct_5",
    "away_ats_pct_10",
    "home_ats_pct_10",
    "away_ats_pm_5",
    "home_ats_pm_5",
    # O/U Trend
    "away_over_pct_10",
    "home_over_pct_10",
    "away_ou_pm_10",
    "home_ou_pm_10",
    # Line Context
    "abs_line",
    "line_vs_away_avg",
    "line_vs_home_avg",
    "home_is_fav",
    # Rest
    "away_rest_days",
    "home_rest_days",
    "rest_advantage",
    # Performance Momentum
    "away_win_pct_5",
    "home_win_pct_5",
    "away_avg_margin_5",
    "home_avg_margin_5",
    # Tournament
    "is_tournament",
    "is_neutral",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(d):
    """Parse YYYYMMDD string to datetime. Returns None on failure."""
    try:
        return datetime.strptime(str(d).replace("-", "")[:8], "%Y%m%d")
    except Exception:
        return None


def _safe_float(v, default=0.0):
    """Coerce value to float, returning *default* on failure."""
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _ats_pct(games, n):
    """ATS cover rate over last *n* games.  Returns 0.5 if fewer than *n*."""
    recent = games[-n:]
    if len(recent) < n:
        return 0.5
    covers = sum(1 for g in recent if g["covered_home"] == g["is_home"])
    return covers / n


def _ats_pm(games, n):
    """Average ATS margin over last *n* games.  Returns 0.0 if fewer."""
    recent = games[-n:]
    if len(recent) < n:
        return 0.0
    return sum(g["ats_margin"] for g in recent) / n


def _over_pct(games, n):
    """Over hit rate over last *n* games.  Returns 0.5 if fewer."""
    recent = games[-n:]
    if len(recent) < n:
        return 0.5
    overs = sum(1 for g in recent if g["actual_total"] > g["total_line"])
    return overs / n


def _ou_pm(games, n):
    """Average total vs O/U line over last *n* games.  Returns 0.0 if fewer."""
    recent = games[-n:]
    if len(recent) < n:
        return 0.0
    return sum(g["actual_total"] - g["total_line"] for g in recent) / n


def _win_pct(games, n):
    """Straight-up win rate over last *n* games.  Returns 0.5 if fewer."""
    recent = games[-n:]
    if len(recent) < n:
        return 0.5
    return sum(1 for g in recent if g["won"]) / n


def _avg_margin(games, n):
    """Average margin of victory over last *n* games.  Returns 0.0 if fewer."""
    recent = games[-n:]
    if len(recent) < n:
        return 0.0
    return sum(g["margin"] for g in recent) / n


def _avg_abs_line(games):
    """Season average absolute line for a team (all games in buffer)."""
    if not games:
        return 0.0
    return sum(g["abs_line"] for g in games) / len(games)


# ---------------------------------------------------------------------------
# build_team_histories
# ---------------------------------------------------------------------------

def build_team_histories(store):
    """Walk history.json runs chronologically, build per-team rolling game buffers.

    Returns ``{team_name: [game_record, ...]}`` where each game_record is::

        {
            "date":          str,      # YYYYMMDD
            "is_home":       bool,
            "covered_home":  bool,     # True if home side covered the spread
            "ats_margin":    float,    # homeScore - awayScore - line (positive = home covered)
            "actual_total":  float,    # homeScore + awayScore
            "total_line":    float,    # O/U line
            "won":           bool,     # straight-up win for this team
            "margin":        float,    # margin from this team's perspective (positive = win)
            "abs_line":      float,    # absolute value of the spread
        }
    """
    histories = {}  # team -> list of game records
    runs = store.get("runs") or []

    for run in runs:
        run_date = run.get("date", "")
        games = run.get("games") or []

        for g in games:
            home = g.get("home")
            away = g.get("away")
            h_score = g.get("homeScore")
            a_score = g.get("awayScore")
            line = g.get("line")
            total_line = g.get("total")

            # Need scores and line to compute features
            if h_score is None or a_score is None or line is None:
                continue

            h_score = _safe_float(h_score)
            a_score = _safe_float(a_score)
            line = _safe_float(line)
            total_line = _safe_float(total_line, 140.0)

            ats_margin = h_score - a_score - line   # positive = home covered
            actual_total = h_score + a_score
            covered_home = ats_margin > 0

            abs_line = abs(line)

            # Home team record
            if home:
                histories.setdefault(home, []).append({
                    "date": run_date,
                    "is_home": True,
                    "covered_home": covered_home,
                    "ats_margin": ats_margin,
                    "actual_total": actual_total,
                    "total_line": total_line,
                    "won": h_score > a_score,
                    "margin": h_score - a_score,
                    "abs_line": abs_line,
                })

            # Away team record
            if away:
                histories.setdefault(away, []).append({
                    "date": run_date,
                    "is_home": False,
                    "covered_home": covered_home,
                    "ats_margin": ats_margin,
                    "actual_total": actual_total,
                    "total_line": total_line,
                    "won": a_score > h_score,
                    "margin": a_score - h_score,
                    "abs_line": abs_line,
                })

    return histories


# ---------------------------------------------------------------------------
# extract_lr_features
# ---------------------------------------------------------------------------

def extract_lr_features(home_history, away_history, game,
                        home_season_lines, away_season_lines):
    """Extract LR feature vector from team rolling histories.

    Parameters
    ----------
    home_history : list
        Rolling game buffer for the home team (up to but NOT including this game).
    away_history : list
        Rolling game buffer for the away team (up to but NOT including this game).
    game : dict
        The current game dict from history.json.
    home_season_lines : list
        All absolute lines the home team has seen this season (for avg comparison).
    away_season_lines : list
        All absolute lines the away team has seen this season.

    Returns
    -------
    list[float] or None
        Feature vector matching ``LR_FEATURE_NAMES`` order, or ``None`` if
        either team has fewer than 5 games in history.
    """
    if len(home_history) < 5 or len(away_history) < 5:
        return None

    line = _safe_float(game.get("line", 0))
    abs_line = abs(line)

    # ATS Momentum
    away_ats_pct_5 = _ats_pct(away_history, 5)
    home_ats_pct_5 = _ats_pct(home_history, 5)
    away_ats_pct_10 = _ats_pct(away_history, 10)
    home_ats_pct_10 = _ats_pct(home_history, 10)
    away_ats_pm_5 = _ats_pm(away_history, 5)
    home_ats_pm_5 = _ats_pm(home_history, 5)

    # O/U Trend
    away_over_pct_10 = _over_pct(away_history, 10)
    home_over_pct_10 = _over_pct(home_history, 10)
    away_ou_pm_10 = _ou_pm(away_history, 10)
    home_ou_pm_10 = _ou_pm(home_history, 10)

    # Line Context
    away_avg_line = _avg_abs_line(away_season_lines) if away_season_lines else abs_line
    home_avg_line = _avg_abs_line(home_season_lines) if home_season_lines else abs_line
    line_vs_away_avg = abs_line - away_avg_line
    line_vs_home_avg = abs_line - home_avg_line
    home_is_fav = 1.0 if line > 0 else 0.0  # positive line = home is favorite

    # Rest days
    away_rest = _compute_rest_days(away_history, game)
    home_rest = _compute_rest_days(home_history, game)
    rest_advantage = home_rest - away_rest

    # Performance Momentum
    away_win_pct_5 = _win_pct(away_history, 5)
    home_win_pct_5 = _win_pct(home_history, 5)
    away_avg_margin_5 = _avg_margin(away_history, 5)
    home_avg_margin_5 = _avg_margin(home_history, 5)

    # Tournament flags
    is_tournament = 1.0 if game.get("is_tournament") else 0.0
    is_neutral = 1.0 if game.get("is_neutral") else 0.0

    return [
        away_ats_pct_5,
        home_ats_pct_5,
        away_ats_pct_10,
        home_ats_pct_10,
        away_ats_pm_5,
        home_ats_pm_5,
        away_over_pct_10,
        home_over_pct_10,
        away_ou_pm_10,
        home_ou_pm_10,
        abs_line,
        line_vs_away_avg,
        line_vs_home_avg,
        home_is_fav,
        away_rest,
        home_rest,
        rest_advantage,
        away_win_pct_5,
        home_win_pct_5,
        away_avg_margin_5,
        home_avg_margin_5,
        is_tournament,
        is_neutral,
    ]


def _compute_rest_days(history, game):
    """Compute days since last game from the team's history buffer.

    Falls back to 3.0 (typical mid-week rest) if date parsing fails.
    """
    if not history:
        return 3.0

    last_game = history[-1]
    last_date = _parse_date(last_game.get("date"))
    # The current game's date comes from the run, passed via game dict
    # or inferred from the run context.  We look for 'date' or '_runDate'.
    cur_date = _parse_date(game.get("date") or game.get("_runDate"))

    if not last_date or not cur_date:
        return 3.0

    delta = (cur_date - last_date).days
    # Clamp to [0, 14] — anything beyond 14 days is likely a break
    return float(max(0, min(delta, 14)))


# ---------------------------------------------------------------------------
# _avg_abs_line helper for season lines list
# ---------------------------------------------------------------------------

def _avg_abs_line(lines_list):
    """Average absolute line from a list of floats or game records."""
    if not lines_list:
        return 0.0
    # Support both raw floats and dicts with "abs_line" key
    if isinstance(lines_list[0], (int, float)):
        return sum(lines_list) / len(lines_list)
    return sum(g["abs_line"] for g in lines_list) / len(lines_list)


# ---------------------------------------------------------------------------
# _determine_bayes_picked_home
# ---------------------------------------------------------------------------

def _determine_bayes_picked_home(game):
    """Determine whether the Bayesian model picked the home side.

    Returns True if Bayesian picked home, False if away, None if PASS.
    """
    pick = game.get("sPick_bayes") or game.get("sPick")
    if not pick or pick == "PASS":
        return None

    home = game.get("home", "")
    away = game.get("away", "")

    # Parse "TeamName +/-N.N" format
    m = re.match(r"(.+?)\s+[+-]\d", pick)
    if m:
        picked_team = m.group(1).strip()
        # Check if the picked team matches home or away
        if picked_team == home:
            return True
        if picked_team == away:
            return False
        # Fuzzy: check if picked team is a substring
        picked_lower = picked_team.lower()
        if picked_lower in home.lower():
            return True
        if picked_lower in away.lower():
            return False
    return None


# ---------------------------------------------------------------------------
# train_lr_model
# ---------------------------------------------------------------------------

def train_lr_model(store, min_games=80):
    """Walk-forward train on all graded games in history.

    Parameters
    ----------
    store : dict
        The history store (loaded from history.json).
    min_games : int
        Minimum number of labelled games before training begins.

    Returns
    -------
    dict or None
        ``{model: LogisticRegression, scaler: StandardScaler, n_trained: int}``
        or ``None`` if sklearn is unavailable or insufficient data.
    """
    if not HAS_SKLEARN:
        print("[lr_model] sklearn not available — skipping LR training")
        return None

    runs = store.get("runs") or []

    # ── Collect all labelled samples ──────────────────────────────────────
    team_histories = {}  # team -> list of game records (built incrementally)
    X_all = []
    y_all = []

    for run in runs:
        run_date = run.get("date", "")
        games = run.get("games") or []

        for g in games:
            home = g.get("home")
            away = g.get("away")
            h_score = g.get("homeScore")
            a_score = g.get("awayScore")
            line = g.get("line")
            total_line = g.get("total")

            if h_score is None or a_score is None or line is None:
                continue

            h_score_f = _safe_float(h_score)
            a_score_f = _safe_float(a_score)
            line_f = _safe_float(line)
            total_f = _safe_float(total_line, 140.0)

            # ── Determine if Bayesian model made a pick on this game ─────
            bayes_home = _determine_bayes_picked_home(g)
            if bayes_home is None:
                # No Bayesian pick — still update team histories, skip training sample
                _append_team_history(team_histories, home, away, run_date,
                                     h_score_f, a_score_f, line_f, total_f)
                continue

            # ── Extract features BEFORE updating histories ───────────────
            home_hist = team_histories.get(home, [])
            away_hist = team_histories.get(away, [])

            # Inject run date for rest-day calculation
            game_with_date = {**g, "date": run_date, "_runDate": run_date}

            features = extract_lr_features(
                home_hist, away_hist, game_with_date,
                home_hist, away_hist,  # season lines = full history so far
            )

            if features is not None:
                # ── Label: did the Bayesian pick cover? ──────────────────
                margin_vs_line = h_score_f - a_score_f - line_f
                if bayes_home:
                    covered = 1 if margin_vs_line > 0 else 0
                else:
                    covered = 1 if margin_vs_line < 0 else 0

                X_all.append(features)
                y_all.append(covered)

            # ── Update team histories ────────────────────────────────────
            _append_team_history(team_histories, home, away, run_date,
                                 h_score_f, a_score_f, line_f, total_f)

    # ── Train ─────────────────────────────────────────────────────────────
    n = len(X_all)
    if n < min_games:
        print(f"[lr_model] Only {n} labelled games (need {min_games}) — skipping")
        return None

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X_all)

    model = LogisticRegression(
        l1_ratio=0,
        C=1.0,
        class_weight="balanced",
        max_iter=1000,
        solver="lbfgs",
    )
    model.fit(X_scaled, y_all)

    print(f"[lr_model] Trained on {n} games")
    return {"model": model, "scaler": scaler, "n_trained": n}


def _append_team_history(histories, home, away, run_date,
                         h_score, a_score, line, total_line):
    """Append a game record to both teams' rolling histories."""
    ats_margin = h_score - a_score - line
    actual_total = h_score + a_score
    covered_home = ats_margin > 0
    abs_line = abs(line)

    if home:
        histories.setdefault(home, []).append({
            "date": run_date,
            "is_home": True,
            "covered_home": covered_home,
            "ats_margin": ats_margin,
            "actual_total": actual_total,
            "total_line": total_line,
            "won": h_score > a_score,
            "margin": h_score - a_score,
            "abs_line": abs_line,
        })

    if away:
        histories.setdefault(away, []).append({
            "date": run_date,
            "is_home": False,
            "covered_home": covered_home,
            "ats_margin": ats_margin,
            "actual_total": actual_total,
            "total_line": total_line,
            "won": a_score > h_score,
            "margin": a_score - h_score,
            "abs_line": abs_line,
        })


# ---------------------------------------------------------------------------
# predict_lr
# ---------------------------------------------------------------------------

def predict_lr(model_bundle, features):
    """Run the LR model on a feature vector.

    Parameters
    ----------
    model_bundle : dict
        Output of ``train_lr_model`` or ``load_or_train_lr``.
    features : list[float]
        Feature vector from ``extract_lr_features``.

    Returns
    -------
    dict
        ``{lr_prob: float, lr_verdict: str}`` where verdict is one of
        ``"CONFIRM"``, ``"VETO"``, or ``"NEUTRAL"``.
    """
    if model_bundle is None or features is None:
        return {"lr_prob": None, "lr_verdict": "NEUTRAL"}

    model = model_bundle["model"]
    scaler = model_bundle["scaler"]

    try:
        X = scaler.transform([features])
        prob = float(model.predict_proba(X)[0, 1])  # P(covered)
    except Exception as e:
        print(f"[lr_model] Prediction error: {e}")
        return {"lr_prob": None, "lr_verdict": "NEUTRAL"}

    confirm_thresh = LR_CONFIRM_THRESH
    veto_thresh = LR_VETO_THRESH

    if prob >= confirm_thresh:
        verdict = "CONFIRM"
    elif prob <= veto_thresh:
        verdict = "VETO"
    else:
        verdict = "NEUTRAL"

    return {"lr_prob": round(prob, 3), "lr_verdict": verdict}


# ---------------------------------------------------------------------------
# load_or_train_lr
# ---------------------------------------------------------------------------

def load_or_train_lr(store):
    """Load a previously saved LR model from disk, or retrain from scratch.

    Saves the trained model, scaler, and metadata via joblib to
    ``data/lr_models/``.

    Parameters
    ----------
    store : dict
        The history store (loaded from history.json).

    Returns
    -------
    dict or None
        ``{model, scaler, n_trained}`` or ``None``.
    """
    if not HAS_SKLEARN:
        print("[lr_model] sklearn not installed — LR model disabled")
        return None

    # Try loading from disk first
    if HAS_JOBLIB and os.path.exists(_MODEL_PATH) and os.path.exists(_SCALER_PATH):
        try:
            model = joblib.load(_MODEL_PATH)
            scaler = joblib.load(_SCALER_PATH)
            meta = joblib.load(_META_PATH) if os.path.exists(_META_PATH) else {}
            n_trained = meta.get("n_trained", 0)
            print(f"[lr_model] Loaded saved model ({n_trained} games)")

            # Check if we should retrain (every 20 new games)
            total_graded = _count_graded_picks(store)
            if total_graded - n_trained >= 20:
                print(f"[lr_model] {total_graded - n_trained} new games since last train — retraining")
                bundle = train_lr_model(store)
                if bundle:
                    _save_model(bundle)
                    return bundle

            return {"model": model, "scaler": scaler, "n_trained": n_trained}
        except Exception as e:
            print(f"[lr_model] Failed to load saved model: {e} — retraining")

    # Train from scratch
    bundle = train_lr_model(store)
    if bundle:
        _save_model(bundle)
    return bundle


def _count_graded_picks(store):
    """Count total graded spread picks (non-PASS with scores) in history."""
    count = 0
    for run in store.get("runs") or []:
        for g in run.get("games") or []:
            pick = g.get("sPick_bayes") or g.get("sPick")
            if pick and pick != "PASS" and g.get("homeScore") is not None:
                count += 1
    return count


def _save_model(bundle):
    """Persist model, scaler, and metadata to disk via joblib."""
    if not HAS_JOBLIB:
        print("[lr_model] joblib not installed — cannot save model")
        return

    os.makedirs(_MODEL_DIR, exist_ok=True)
    try:
        joblib.dump(bundle["model"], _MODEL_PATH)
        joblib.dump(bundle["scaler"], _SCALER_PATH)
        joblib.dump({"n_trained": bundle["n_trained"]}, _META_PATH)
        print(f"[lr_model] Saved model to {_MODEL_DIR}")
    except Exception as e:
        print(f"[lr_model] Failed to save model: {e}")


# ---------------------------------------------------------------------------
# CLI entry point (for standalone testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    from store import load_store

    store = load_store()
    bundle = load_or_train_lr(store)
    if bundle:
        print(f"\n[lr_model] Model ready — trained on {bundle['n_trained']} games")
        print(f"[lr_model] Features: {len(LR_FEATURE_NAMES)}")
        print(f"[lr_model] Feature names: {LR_FEATURE_NAMES}")
        if hasattr(bundle["model"], "coef_"):
            coefs = bundle["model"].coef_[0]
            print("\n[lr_model] Feature importance (|coef|):")
            ranked = sorted(zip(LR_FEATURE_NAMES, coefs),
                            key=lambda x: abs(x[1]), reverse=True)
            for name, coef in ranked:
                print(f"  {name:>25s}  {coef:+.4f}")
    else:
        print("[lr_model] No model produced")

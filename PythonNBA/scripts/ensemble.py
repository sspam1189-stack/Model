"""
ensemble.py -- XGBoost + Ridge + Bayesian ensemble for daily pipeline.
Trains on graded history, predicts for today's games, blends with Bayesian.
Falls back to Bayesian-only if xgboost/sklearn unavailable.
"""
from __future__ import annotations
import math
import re
from typing import Dict, List, Optional, Tuple

try:
    import numpy as np
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler
    import xgboost as xgb
    ENSEMBLE_AVAILABLE = True
except ImportError:
    ENSEMBLE_AVAILABLE = False


# ---------------------------------------------------------------------------
# Feature helpers (identical to ml_independent_backtest.py)
# ---------------------------------------------------------------------------

def normal_cdf(x: float) -> float:
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = -1 if x < 0 else 1
    z = abs(x) / math.sqrt(2.0)
    t = 1.0 / (1.0 + p * z)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-z * z)
    return 0.5 * (1.0 + sign * y)


def _safe_num(v, default=0.0) -> float:
    return float(v) if isinstance(v, (int, float)) else float(default)


def _trend_val(trends: dict, side: str, key: str) -> float:
    if not isinstance(trends, dict):
        return 0.0
    node = trends.get(side) or {}
    if not isinstance(node, dict):
        return 0.0
    return _safe_num(node.get(key), 0.0)


def _count_injuries(injury_note, side_label: str) -> int:
    if not injury_note or not isinstance(injury_note, str):
        return 0
    m = re.search(rf"{side_label}:\s*([^|]+)", injury_note)
    if not m:
        return 0
    part = m.group(1).strip()
    return len([x for x in (p.strip() for p in part.split(",")) if x]) if part else 0


def _b2b_flags(b2b_note) -> Tuple[int, int]:
    if not b2b_note:
        return (0, 0)
    s = str(b2b_note).lower()
    return int("home" in s and "b2b" in s), int("away" in s and "b2b" in s)


def _delta_features(adj, side: str, stat: str) -> float:
    if not isinstance(adj, dict):
        return 0.0
    node = adj.get(side) or {}
    return _safe_num(node.get(stat), 0.0)


def extract_features(g: dict) -> List[float]:
    """Extract 45 independent features from an analyzed game dict."""
    f = g.get("_features") or {}
    mf = g.get("_marginFeatures") or {}
    trends = g.get("trends") or {}
    line = _safe_num(g.get("line"), 0.0)
    total = _safe_num(g.get("total"), 220.0)
    abs_line = abs(line)
    implied_home = (total + line) / 2.0
    implied_away = (total - line) / 2.0
    home_b2b, away_b2b = _b2b_flags(g.get("b2bNote"))
    home_inj = _count_injuries(g.get("injuryNote"), "Home")
    away_inj = _count_injuries(g.get("injuryNote"), "Away")
    h_ats = _trend_val(trends, "home", "atsPct")
    a_ats = _trend_val(trends, "away", "atsPct")
    h_ou = _trend_val(trends, "home", "overPct")
    a_ou = _trend_val(trends, "away", "overPct")
    h_tot_pm = _trend_val(trends, "home", "totalPlusMinus")
    a_tot_pm = _trend_val(trends, "away", "totalPlusMinus")
    h_ats_pm = _trend_val(trends, "home", "atsPlusMinus")
    a_ats_pm = _trend_val(trends, "away", "atsPlusMinus")
    home_off_d = _delta_features(g.get("_adjDeltas"), "home", "OFF")
    away_off_d = _delta_features(g.get("_adjDeltas"), "away", "OFF")
    home_def_d = _delta_features(g.get("_adjDeltas"), "home", "DEF")
    away_def_d = _delta_features(g.get("_adjDeltas"), "away", "DEF")
    home_pace_d = _delta_features(g.get("_adjDeltas"), "home", "PACE")
    away_pace_d = _delta_features(g.get("_adjDeltas"), "away", "PACE")
    dNET = _safe_num(f.get("dNET"))
    avgPace = _safe_num(f.get("avgPace"), 98.0)
    return [
        _safe_num(f.get("dTS")), _safe_num(f.get("dTO")), _safe_num(f.get("dORR")),
        dNET, avgPace,
        _safe_num(mf.get("dTS")), _safe_num(mf.get("dTO")), _safe_num(mf.get("dORR")),
        _safe_num(mf.get("dNET")), _safe_num(mf.get("_baseline")), _safe_num(mf.get("_pace"), 1.0),
        line, total, abs_line, implied_home, implied_away, implied_home - implied_away,
        dNET * line, dNET * abs_line, avgPace * line,
        h_ats, a_ats, h_ats - a_ats, h_ou, a_ou, h_ou - a_ou,
        h_ats_pm, a_ats_pm, h_ats_pm - a_ats_pm,
        h_tot_pm, a_tot_pm, h_tot_pm - a_tot_pm,
        float(home_b2b), float(away_b2b), float(home_b2b - away_b2b),
        float(home_inj), float(away_inj), float(home_inj - away_inj),
        home_off_d, away_off_d, home_off_d - away_off_d,
        home_def_d, away_def_d, home_def_d - away_def_d,
        home_pace_d, away_pace_d, home_pace_d - away_pace_d,
    ]


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def _extract_graded_rows(store: dict):
    """Extract training rows from graded history."""
    rows_x, rows_y, rows_bayes, rows_actual, rows_push = [], [], [], [], []
    for run in store.get("runs", []):
        if run.get("burnIn"):
            continue
        for g in run.get("games", []):
            hs = g.get("homeScore")
            aws = g.get("awayScore")
            line = g.get("line")
            if not isinstance(hs, (int, float)) or not isinstance(aws, (int, float)):
                continue
            if not isinstance(line, (int, float)):
                continue
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue
            if not g.get("_features") or not g.get("_marginFeatures"):
                continue
            y = float((hs - aws) - line)
            rows_x.append(extract_features(g))
            rows_y.append(y)
            rows_bayes.append(_safe_num(g.get("pHomeCover"), 0.5))
            rows_actual.append(y > 0)
            rows_push.append(y == 0)
    return rows_x, rows_y, rows_bayes, rows_actual, rows_push


def train_ensemble(store: dict, min_train: int = 50, lookback: int = 120, max_xgb_weight: float = 1.0) -> Optional[dict]:
    """Train XGBoost + Ridge on graded history. Returns models dict or None."""
    if not ENSEMBLE_AVAILABLE:
        return None
    rows_x, rows_y, rows_bayes, rows_actual, rows_push = _extract_graded_rows(store)
    n = len(rows_x)
    if n < min_train:
        return None

    X = np.array(rows_x, dtype=float)
    y = np.array(rows_y, dtype=float)

    # Train XGBoost on raw features
    xgb_model = xgb.XGBRegressor(
        n_estimators=120, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8,
        reg_alpha=1.0, reg_lambda=2.0, min_child_weight=5,
        random_state=42, verbosity=0,
    )
    xgb_model.fit(X, y)
    xgb_std = max(float(np.std(y - xgb_model.predict(X))), 1.0)

    # Train Ridge on scaled features
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)
    ridge_model = Ridge(alpha=1.0)
    ridge_model.fit(Xs, y)
    ridge_std = max(float(np.std(y - ridge_model.predict(Xs))), 1.0)

    # Compute adaptive weights from trailing performance
    tail = min(lookback, n)
    tail_start = n - tail
    b_wins = b_total = x_wins = x_total = r_wins = r_total = 0
    for i in range(tail_start, n):
        if rows_push[i]:
            continue
        a = rows_actual[i]
        bp = rows_bayes[i]
        xp = normal_cdf(float(xgb_model.predict(np.array([rows_x[i]], dtype=float))[0]) / xgb_std)
        rp = normal_cdf(float(ridge_model.predict(scaler.transform(np.array([rows_x[i]], dtype=float)))[0]) / ridge_std)
        b_wins += int((bp >= 0.5) == a)
        x_wins += int((xp >= 0.5) == a)
        r_wins += int((rp >= 0.5) == a)
        b_total += 1
        x_total += 1
        r_total += 1

    floor = 0.10
    raw_b = max(floor, (b_wins / b_total) - 0.40) if b_total >= 30 else floor
    raw_x = max(floor, (x_wins / x_total) - 0.40) if x_total >= 30 else floor
    raw_r = max(floor, (r_wins / r_total) - 0.40) if r_total >= 30 else floor
    s = raw_b + raw_x + raw_r
    weights = (raw_b / s, raw_x / s, raw_r / s) if s > 0 else (1.0, 0.0, 0.0)
    # Cap XGB weight and redistribute excess to Bayesian
    if max_xgb_weight < 1.0 and weights[1] > max_xgb_weight:
        excess = weights[1] - max_xgb_weight
        weights = (weights[0] + excess, max_xgb_weight, weights[2])

    return {
        "xgb_model": xgb_model,
        "ridge_model": ridge_model,
        "scaler": scaler,
        "xgb_std": xgb_std,
        "ridge_std": ridge_std,
        "weights": weights,
        "n_trained": n,
        "trailing_acc": {
            "bayes": f"{b_wins}/{b_total}",
            "xgb": f"{x_wins}/{x_total}",
            "ridge": f"{r_wins}/{r_total}",
        },
    }


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------

def predict_ensemble(models: dict, game_dict: dict, bayes_p_home: float) -> dict:
    """Predict ensemble P(home cover) for a single game."""
    x = np.array([extract_features(game_dict)], dtype=float)
    xgb_margin = float(models["xgb_model"].predict(x)[0])
    xgb_p = normal_cdf(xgb_margin / models["xgb_std"])
    ridge_margin = float(models["ridge_model"].predict(models["scaler"].transform(x))[0])
    ridge_p = normal_cdf(ridge_margin / models["ridge_std"])
    wb, wx, wr = models["weights"]
    p_home = wb * bayes_p_home + wx * xgb_p + wr * ridge_p
    p_home = max(0.001, min(0.999, p_home))
    return {
        "pHomeCover": p_home,
        "pAwayCover": 1.0 - p_home,
        "weights": (round(wb, 3), round(wx, 3), round(wr, 3)),
        "xgb_raw": xgb_p,
        "ridge_raw": ridge_p,
    }

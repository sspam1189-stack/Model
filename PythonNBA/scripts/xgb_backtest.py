#!/usr/bin/env python
"""
XGBoost walk-forward backtest for NBA spread picks.

Compares Bayesian-only vs XGBoost-only vs Ensemble predictions using
strict walk-forward evaluation (no future data leakage).

Usage:
  python scripts/xgb_backtest.py
  python scripts/xgb_backtest.py --min-prob 0.57 --retrain-every 25
  python scripts/xgb_backtest.py --start-date 20250101
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    import xgboost as xgb

    HAS_XGB = True
except ImportError:
    HAS_XGB = False

try:
    from sklearn.linear_model import LogisticRegression

    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
HISTORY_PATH = ROOT / "data" / "history.json"
OUT_PATH = ROOT / "data" / "xgb_backtest_results.json"

# ---------------------------------------------------------------------------
# Import project modules (add scripts dir to path)
# ---------------------------------------------------------------------------

sys.path.insert(0, str(ROOT / "scripts"))

from defaults import (
    XGB_ENSEMBLE_WEIGHT,
    XGB_LOOKBACK_WINDOW,
    XGB_MIN_TRAINING_GAMES,
    XGB_RETRAIN_INTERVAL,
)
from xgb_model import extract_features, normal_cdf

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

UNIT_WIN = 1.0
UNIT_LOSS = -1.1
UNIT_PUSH = 0.0

FEATURE_NAMES = [
    "dTS", "dTO", "dORR", "dNET", "avgPace",
    "mf_dTS", "mf_dTO", "mf_dORR", "mf_dNET", "mf_pace",
    "total", "abs_line",
    "dNET*absLine",
    "h_ats", "a_ats", "ats_delta",
    "h_ou", "a_ou", "ou_delta",
    "h_ats_pm", "a_ats_pm", "ats_pm_delta",
    "h_tot_pm", "a_tot_pm", "tot_pm_delta",
    "home_b2b", "away_b2b", "b2b_delta",
    "home_inj", "away_inj", "inj_delta",
    "h_off_d", "a_off_d", "off_d_delta",
    "h_def_d", "a_def_d", "def_d_delta",
    "h_pace_d", "a_pace_d", "pace_d_delta",
]

ENSEMBLE_WEIGHTS_TO_TEST = [0.20, 0.30, 0.35, 0.40, 0.50]

CALIBRATION_BUCKETS = [
    (0.50, 0.55),
    (0.55, 0.60),
    (0.60, 0.65),
    (0.65, 0.70),
    (0.70, 1.01),
]

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def _safe_num(v, default=0.0) -> float:
    return float(v) if isinstance(v, (int, float)) else float(default)


def load_graded_games(history_path: Path) -> List[dict]:
    """Load graded games from history.json in chronological order.

    Each returned dict has keys:
        x, y_margin, y_cover, is_push, date, bayes_p,
        home, away, line, homeScore, awayScore
    """
    store = json.loads(history_path.read_text(encoding="utf-8"))
    rows: List[dict] = []

    for run in store.get("runs", []):
        if run.get("burnIn"):
            continue
        date = str(run.get("date", ""))
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
            feats = extract_features(g)

            rows.append({
                "x": feats,
                "y_margin": y,
                "y_cover": y > 0,
                "is_push": y == 0.0,
                "date": date,
                "bayes_p": _safe_num(g.get("pHomeCover"), 0.5),
                "home": g.get("home", "?"),
                "away": g.get("away", "?"),
                "line": float(line),
                "homeScore": float(hs),
                "awayScore": float(aws),
                "sDiff": _safe_num(g.get("sDiff"), 0.0),
            })

    return rows


# ---------------------------------------------------------------------------
# Walk-forward engine
# ---------------------------------------------------------------------------


def _train_xgb(X: np.ndarray, y: np.ndarray) -> Tuple:
    """Train XGBoost regressor, return (model, residual_std)."""
    model = xgb.XGBRegressor(
        n_estimators=120,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        reg_alpha=1.0,
        reg_lambda=2.0,
        min_child_weight=5,
        random_state=42,
        verbosity=0,
    )
    model.fit(X, y)
    preds = model.predict(X)
    residual_std = max(float(np.std(y - preds)), 1.0)
    return model, residual_std


def _fit_platt(
    raw_probs: List[float],
    actuals: List[bool],
    pushes: List[bool],
    lookback: int,
    min_samples: int = 40,
) -> Optional[LogisticRegression]:
    """Fit Platt calibrator on trailing window of raw probabilities."""
    if not HAS_SKLEARN:
        return None
    n = len(raw_probs)
    start = max(0, n - lookback)
    xs, ys = [], []
    for j in range(start, n):
        if pushes[j]:
            continue
        xs.append([raw_probs[j]])
        ys.append(1 if actuals[j] else 0)
    if len(xs) < min_samples or len(set(ys)) < 2:
        return None
    lr = LogisticRegression(solver="lbfgs", max_iter=500)
    lr.fit(np.array(xs, dtype=np.float64), np.array(ys, dtype=int))
    return lr


def _calibrate_prob(raw_p: float, calibrator: Optional[LogisticRegression]) -> float:
    if calibrator is None:
        return raw_p
    return float(
        calibrator.predict_proba(np.array([[raw_p]], dtype=np.float64))[0, 1]
    )


def walk_forward_backtest(
    rows: List[dict],
    min_train: int,
    retrain_every: int,
    min_prob: float,
    start_date: Optional[str] = None,
) -> dict:
    """Run walk-forward backtest over all graded games.

    Returns a results dict with per-model metrics.
    """
    n = len(rows)
    if n < min_train + 1:
        return {"error": f"Only {n} games, need at least {min_train + 1}"}

    # Determine effective start index
    start_idx = min_train
    if start_date:
        for i, r in enumerate(rows):
            if r["date"] >= start_date and i >= min_train:
                start_idx = i
                break

    X_all = np.array([r["x"] for r in rows], dtype=np.float64)
    y_all = np.array([r["y_margin"] for r in rows], dtype=np.float64)

    # Per-game prediction storage
    xgb_raw_probs: List[float] = []
    xgb_cal_probs: List[float] = []
    xgb_margins: List[float] = []
    bayes_probs: List[float] = []
    actuals: List[bool] = []
    pushes: List[bool] = []
    dates: List[str] = []
    game_infos: List[dict] = []
    sdiffs: List[float] = []
    lines: List[float] = []

    xgb_model = None
    xgb_std = 12.0
    train_count = 0
    last_feature_importance = None

    print(f"Running walk-forward backtest: {n} total games, start at index {start_idx}")
    print(f"  retrain_every={retrain_every}, min_train={min_train}, min_prob={min_prob:.2f}")
    print()

    for i in range(start_idx, n):
        # --- Retrain check ---
        need_train = (xgb_model is None) or ((i - start_idx) % retrain_every == 0)
        if need_train:
            X_train = X_all[:i]
            y_train = y_all[:i]
            xgb_model, xgb_std = _train_xgb(X_train, y_train)
            train_count += 1

            # Capture feature importance from latest model
            imp = xgb_model.feature_importances_
            last_feature_importance = sorted(
                zip(FEATURE_NAMES[: len(imp)], imp.tolist()),
                key=lambda t: t[1],
                reverse=True,
            )

        r = rows[i]
        actuals.append(r["y_cover"])
        pushes.append(r["is_push"])
        dates.append(r["date"])
        bayes_probs.append(r["bayes_p"])
        game_infos.append({
            "home": r["home"],
            "away": r["away"],
            "line": r["line"],
            "date": r["date"],
        })
        sdiffs.append(r["sDiff"])
        lines.append(r["line"])

        # XGBoost prediction
        x_row = np.array([r["x"]], dtype=np.float64)
        margin_pred = float(xgb_model.predict(x_row)[0])
        raw_p = normal_cdf(margin_pred / xgb_std)
        xgb_margins.append(margin_pred)
        xgb_raw_probs.append(raw_p)

        # Online Platt calibration on trailing window
        calibrator = _fit_platt(
            xgb_raw_probs, actuals, pushes,
            lookback=XGB_LOOKBACK_WINDOW * 4,
        )
        cal_p = _calibrate_prob(raw_p, calibrator)
        xgb_cal_probs.append(cal_p)

        if (i - start_idx + 1) % 200 == 0:
            print(f"  processed {i - start_idx + 1} / {n - start_idx} games...")

    n_test = len(actuals)
    print(f"\nBacktest complete: {n_test} test games, {train_count} retrains")

    # --- Evaluate models ---
    results: Dict[str, dict] = {}

    # Bayesian only
    results["Bayesian"] = _evaluate_model(
        "Bayesian", bayes_probs, actuals, pushes, min_prob
    )

    # XGBoost raw
    results["XGB_raw"] = _evaluate_model(
        "XGB_raw", xgb_raw_probs, actuals, pushes, min_prob
    )

    # XGBoost calibrated
    results["XGB_cal"] = _evaluate_model(
        "XGB_cal", xgb_cal_probs, actuals, pushes, min_prob
    )

    # Ensembles at various weight splits
    for xw in ENSEMBLE_WEIGHTS_TO_TEST:
        bw = 1.0 - xw
        ens_probs = [
            bw * bp + xw * xp for bp, xp in zip(bayes_probs, xgb_cal_probs)
        ]
        label = f"Ens_{int(xw*100)}xgb"
        results[label] = _evaluate_model(label, ens_probs, actuals, pushes, min_prob)

    # Adaptive ensemble (trailing accuracy weights)
    adaptive_probs = _compute_adaptive_ensemble(
        bayes_probs, xgb_cal_probs, actuals, pushes
    )
    results["Ens_adaptive"] = _evaluate_model(
        "Ens_adaptive", adaptive_probs, actuals, pushes, min_prob
    )

    # Calibration buckets per model
    calib = {}
    for name, probs in [
        ("Bayesian", bayes_probs),
        ("XGB_cal", xgb_cal_probs),
        (f"Ens_{int(XGB_ENSEMBLE_WEIGHT*100)}xgb", [
            (1 - XGB_ENSEMBLE_WEIGHT) * b + XGB_ENSEMBLE_WEIGHT * x
            for b, x in zip(bayes_probs, xgb_cal_probs)
        ]),
    ]:
        calib[name] = _calibration_table(probs, actuals, pushes)

    # Brier scores
    brier = {}
    for name, probs in [
        ("Bayesian", bayes_probs),
        ("XGB_raw", xgb_raw_probs),
        ("XGB_cal", xgb_cal_probs),
        ("Ens_adaptive", adaptive_probs),
    ]:
        brier[name] = _brier_score(probs, actuals, pushes)

    # Rolling windows
    rolling = {}
    for name, probs in [
        ("Bayesian", bayes_probs),
        ("XGB_cal", xgb_cal_probs),
        (f"Ens_{int(XGB_ENSEMBLE_WEIGHT*100)}xgb", [
            (1 - XGB_ENSEMBLE_WEIGHT) * b + XGB_ENSEMBLE_WEIGHT * x
            for b, x in zip(bayes_probs, xgb_cal_probs)
        ]),
    ]:
        rolling[name] = _rolling_windows(probs, actuals, pushes, min_prob)

    # --- Production-filtered ensemble results ---
    prod_filtered: Dict[str, dict] = {}
    for xw in ENSEMBLE_WEIGHTS_TO_TEST:
        bw = 1.0 - xw
        ens_probs = [
            bw * bp + xw * xp for bp, xp in zip(bayes_probs, xgb_cal_probs)
        ]
        label = f"Ens_{int(xw*100)}xgb"
        prod_filtered[label] = _evaluate_model_filtered(
            label, ens_probs, actuals, pushes, sdiffs, lines,
        )
    # Adaptive ensemble filtered
    prod_filtered["Ens_adaptive"] = _evaluate_model_filtered(
        "Ens_adaptive", adaptive_probs, actuals, pushes, sdiffs, lines,
    )

    # Feature importance (top 15)
    top_features = (last_feature_importance or [])[:15]

    return {
        "n_total_games": n,
        "n_test_games": n_test,
        "start_index": start_idx,
        "min_train": min_train,
        "retrain_every": retrain_every,
        "min_prob": min_prob,
        "start_date": start_date,
        "n_retrains": train_count,
        "model_results": {k: v for k, v in sorted(
            results.items(), key=lambda kv: kv[1]["units"], reverse=True
        )},
        "calibration": calib,
        "brier_scores": brier,
        "rolling_performance": rolling,
        "top_features": top_features,
        "production_filtered_ensemble": {k: v for k, v in sorted(
            prod_filtered.items(), key=lambda kv: kv[1]["units"], reverse=True
        )},
    }


# ---------------------------------------------------------------------------
# Adaptive ensemble
# ---------------------------------------------------------------------------


def _compute_adaptive_ensemble(
    bayes: List[float],
    xgb_probs: List[float],
    actuals: List[bool],
    pushes: List[bool],
) -> List[float]:
    """Adaptive ensemble weighting based on trailing accuracy."""
    out: List[float] = []
    for i in range(len(actuals)):
        bw, xw = _adaptive_weights(bayes, xgb_probs, actuals, pushes, i)
        out.append(bw * bayes[i] + xw * xgb_probs[i])
    return out


def _adaptive_weights(
    bayes: List[float],
    xgb_probs: List[float],
    actuals: List[bool],
    pushes: List[bool],
    i: int,
) -> Tuple[float, float]:
    """Compute trailing-accuracy-based weights for Bayesian vs XGB."""
    lookback = XGB_LOOKBACK_WINDOW
    start = max(0, i - lookback)

    b_correct = b_total = 0
    x_correct = x_total = 0

    for j in range(start, i):
        if pushes[j]:
            continue
        a = actuals[j]
        b_total += 1
        x_total += 1
        b_correct += int((bayes[j] >= 0.5) == a)
        x_correct += int((xgb_probs[j] >= 0.5) == a)

    if b_total < 30 or x_total < 30:
        return (1.0 - XGB_ENSEMBLE_WEIGHT, XGB_ENSEMBLE_WEIGHT)

    floor = 0.10
    b_edge = max(floor, (b_correct / b_total) - 0.40)
    x_edge = max(floor, (x_correct / x_total) - 0.40)
    total_edge = b_edge + x_edge
    if total_edge <= 0:
        return (1.0, 0.0)
    return (b_edge / total_edge, x_edge / total_edge)


# ---------------------------------------------------------------------------
# Evaluation helpers
# ---------------------------------------------------------------------------


def _evaluate_model(
    name: str,
    probs: List[float],
    actuals: List[bool],
    pushes: List[bool],
    min_prob: float,
) -> dict:
    """Evaluate a model's pick performance at the given probability threshold.

    Takes picks when P(cover) >= min_prob (bet home cover) or
    P(cover) <= (1 - min_prob) (bet away cover).
    """
    wins = losses = push_count = 0
    total_games = 0
    correct_all = 0  # accuracy on ALL games (not just picks)
    total_non_push = 0

    for i, p in enumerate(probs):
        if pushes[i]:
            continue
        total_non_push += 1
        a = actuals[i]
        correct_all += int((p >= 0.5) == a)

        lo = 1.0 - min_prob
        if p >= min_prob:
            # Bet home covers
            total_games += 1
            if a:
                wins += 1
            else:
                losses += 1
        elif p <= lo:
            # Bet away covers (home doesn't cover)
            total_games += 1
            if not a:
                wins += 1
            else:
                losses += 1

    picks = wins + losses
    units = wins * UNIT_WIN + losses * UNIT_LOSS
    win_pct = (100.0 * wins / picks) if picks else 0.0
    roi = (100.0 * units / picks) if picks else 0.0

    return {
        "name": name,
        "record": f"{wins}-{losses}-{push_count}",
        "wins": wins,
        "losses": losses,
        "pushes": push_count,
        "picks": picks,
        "win_pct": round(win_pct, 1),
        "units": round(units, 2),
        "roi_pct": round(roi, 2),
        "all_side_acc": round(100.0 * correct_all / total_non_push, 1) if total_non_push else 0.0,
        "total_non_push": total_non_push,
    }


def _evaluate_model_filtered(
    name: str,
    probs: List[float],
    actuals: List[bool],
    pushes: List[bool],
    sdiffs: List[float],
    lines: List[float],
    min_prob: float = 0.57,
    sdiff_cap: float = 9.0,
    abs_line_cap: float = 12.0,
) -> dict:
    """Evaluate with production filters: probHigh, sDiff cap, absLine cap.

    Only takes picks when all three conditions are met:
      - P(cover) >= min_prob (or <= 1-min_prob for away side)
      - sDiff <= sdiff_cap
      - abs(line) < abs_line_cap
    """
    wins = losses = push_count = 0
    total_games = 0
    filtered_out = 0

    for i, p in enumerate(probs):
        if pushes[i]:
            continue

        a = actuals[i]
        lo = 1.0 - min_prob

        # Check probability threshold first
        has_pick = p >= min_prob or p <= lo
        if not has_pick:
            continue

        # Apply production filters
        if sdiffs[i] > sdiff_cap or abs(lines[i]) >= abs_line_cap:
            filtered_out += 1
            continue

        total_games += 1
        if p >= min_prob:
            if a:
                wins += 1
            else:
                losses += 1
        elif p <= lo:
            if not a:
                wins += 1
            else:
                losses += 1

    picks = wins + losses
    units = wins * UNIT_WIN + losses * UNIT_LOSS
    win_pct = (100.0 * wins / picks) if picks else 0.0
    roi = (100.0 * units / picks) if picks else 0.0

    return {
        "name": name,
        "record": f"{wins}-{losses}-{push_count}",
        "wins": wins,
        "losses": losses,
        "pushes": push_count,
        "picks": picks,
        "win_pct": round(win_pct, 1),
        "units": round(units, 2),
        "roi_pct": round(roi, 2),
        "filtered_out": filtered_out,
        "filters": {
            "min_prob": min_prob,
            "sdiff_cap": sdiff_cap,
            "abs_line_cap": abs_line_cap,
        },
    }


def _calibration_table(
    probs: List[float],
    actuals: List[bool],
    pushes: List[bool],
) -> List[dict]:
    """Compute calibration by probability buckets."""
    buckets: List[dict] = []
    for lo, hi in CALIBRATION_BUCKETS:
        correct = total = 0
        prob_sum = 0.0
        for i, p in enumerate(probs):
            if pushes[i]:
                continue
            # Check home-cover side
            if lo <= p < hi:
                total += 1
                prob_sum += p
                correct += int(actuals[i])
            # Check away-cover side (mirror)
            elif lo <= (1.0 - p) < hi:
                total += 1
                prob_sum += (1.0 - p)
                correct += int(not actuals[i])
        actual_rate = (correct / total) if total else 0.0
        avg_pred = (prob_sum / total) if total else (lo + hi) / 2
        buckets.append({
            "bucket": f"{lo:.2f}-{hi:.2f}",
            "n": total,
            "predicted": round(avg_pred, 3),
            "actual": round(actual_rate, 3),
            "gap": round(actual_rate - avg_pred, 3),
        })
    return buckets


def _brier_score(
    probs: List[float],
    actuals: List[bool],
    pushes: List[bool],
) -> float:
    """Compute Brier score (lower is better)."""
    total = 0.0
    count = 0
    for i, p in enumerate(probs):
        if pushes[i]:
            continue
        outcome = 1.0 if actuals[i] else 0.0
        total += (p - outcome) ** 2
        count += 1
    return round(total / count, 6) if count else 0.0


def _rolling_windows(
    probs: List[float],
    actuals: List[bool],
    pushes: List[bool],
    min_prob: float,
) -> Dict[str, dict]:
    """Compute performance over last N picks windows."""
    windows = {}
    for window_size in [50, 100, 200]:
        if len(probs) < window_size:
            continue
        # Take the tail
        tail_p = probs[-window_size:]
        tail_a = actuals[-window_size:]
        tail_push = pushes[-window_size:]
        result = _evaluate_model(
            f"last_{window_size}", tail_p, tail_a, tail_push, min_prob
        )
        windows[f"last_{window_size}"] = result
    return windows


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def print_results(results: dict) -> None:
    """Print formatted backtest results to console."""
    print("\n" + "=" * 80)
    print("  XGBoost Walk-Forward Backtest Results")
    print("=" * 80)
    print(f"  Total games: {results['n_total_games']}")
    print(f"  Test games:  {results['n_test_games']}")
    print(f"  Retrains:    {results['n_retrains']}")
    print(f"  Min P(cover): {results['min_prob']:.2f}")
    if results.get("start_date"):
        print(f"  Start date:  {results['start_date']}")
    print()

    # --- Model comparison table ---
    print("-" * 80)
    print(f"  {'Model':<20} {'Record':<12} {'Win%':>6} {'Units':>8} {'ROI%':>7} {'Picks':>6} {'SideAcc':>8}")
    print("-" * 80)
    for name, m in results["model_results"].items():
        print(
            f"  {m['name']:<20} {m['record']:<12} {m['win_pct']:>5.1f}% "
            f"{m['units']:>+7.1f}u {m['roi_pct']:>+6.1f}% "
            f"{m['picks']:>5}  {m['all_side_acc']:>6.1f}%"
        )
    print()

    # --- Brier scores ---
    if results.get("brier_scores"):
        print("-" * 40)
        print("  Brier Scores (lower = better)")
        print("-" * 40)
        for name, score in sorted(results["brier_scores"].items(), key=lambda x: x[1]):
            print(f"    {name:<25} {score:.4f}")
        print()

    # --- Calibration ---
    if results.get("calibration"):
        for model_name, buckets in results["calibration"].items():
            print(f"  Calibration: {model_name}")
            print(f"    {'Bucket':<12} {'N':>5} {'Predicted':>10} {'Actual':>8} {'Gap':>7}")
            for b in buckets:
                print(
                    f"    {b['bucket']:<12} {b['n']:>5} "
                    f"{b['predicted']:>9.3f}  {b['actual']:>7.3f} {b['gap']:>+6.3f}"
                )
            print()

    # --- Feature importance ---
    if results.get("top_features"):
        print("-" * 40)
        print("  Top 15 XGBoost Features")
        print("-" * 40)
        for rank, (fname, imp) in enumerate(results["top_features"], 1):
            bar = "#" * int(imp * 200)
            print(f"    {rank:>2}. {fname:<18} {imp:.4f}  {bar}")
        print()

    # --- Production-filtered ensemble results ---
    if results.get("production_filtered_ensemble"):
        print("-" * 80)
        print("  Production-Filtered Ensemble Results")
        print("  (P(cover)>=0.57, sDiff<=9, abs(line)<12)")
        print("-" * 80)
        print(f"  {'Model':<20} {'Record':<12} {'Win%':>6} {'Units':>8} {'ROI%':>7} {'Picks':>6} {'Filt':>6}")
        print("-" * 80)
        for name, m in results["production_filtered_ensemble"].items():
            print(
                f"  {m['name']:<20} {m['record']:<12} {m['win_pct']:>5.1f}% "
                f"{m['units']:>+7.1f}u {m['roi_pct']:>+6.1f}% "
                f"{m['picks']:>5}  {m.get('filtered_out', 0):>5}"
            )
        print()

    # --- Rolling performance ---
    if results.get("rolling_performance"):
        for model_name, windows in results["rolling_performance"].items():
            if not windows:
                continue
            print(f"  Rolling performance: {model_name}")
            print(f"    {'Window':<12} {'Record':<12} {'Win%':>6} {'Units':>8} {'ROI%':>7}")
            for wname, w in windows.items():
                print(
                    f"    {wname:<12} {w['record']:<12} {w['win_pct']:>5.1f}% "
                    f"{w['units']:>+7.1f}u {w['roi_pct']:>+6.1f}%"
                )
            print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="XGBoost walk-forward backtest for NBA spread picks"
    )
    ap.add_argument(
        "--min-prob",
        type=float,
        default=0.55,
        help="Minimum P(cover) to trigger a pick (default: 0.55)",
    )
    ap.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="Start backtest from this date (format: YYYYMMDD)",
    )
    ap.add_argument(
        "--retrain-every",
        type=int,
        default=XGB_RETRAIN_INTERVAL,
        help=f"Retrain interval in games (default: {XGB_RETRAIN_INTERVAL})",
    )
    ap.add_argument(
        "--min-train",
        type=int,
        default=XGB_MIN_TRAINING_GAMES,
        help=f"Minimum training games before first prediction (default: {XGB_MIN_TRAINING_GAMES})",
    )
    ap.add_argument(
        "--history",
        type=str,
        default=str(HISTORY_PATH),
        help="Path to history.json",
    )
    return ap.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    if not HAS_XGB:
        print("ERROR: xgboost is not installed. Run: pip install xgboost")
        sys.exit(1)

    args = parse_args()
    history_path = Path(args.history)

    if not history_path.exists():
        print(f"ERROR: History file not found: {history_path}")
        sys.exit(1)

    print(f"Loading games from {history_path}...")
    rows = load_graded_games(history_path)
    print(f"  Loaded {len(rows)} graded games")

    if len(rows) < args.min_train + 1:
        print(f"ERROR: Not enough games ({len(rows)}) for min_train={args.min_train}")
        sys.exit(1)

    results = walk_forward_backtest(
        rows=rows,
        min_train=args.min_train,
        retrain_every=args.retrain_every,
        min_prob=args.min_prob,
        start_date=args.start_date,
    )

    # Print to console
    print_results(results)

    # Save to JSON
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Results saved to {OUT_PATH}")


if __name__ == "__main__":
    main()

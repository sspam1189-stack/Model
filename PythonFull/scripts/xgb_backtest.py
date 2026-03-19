#!/usr/bin/env python3
"""
xgb_backtest.py -- Walk-forward XGBoost backtest.

Loads history.json, runs a strict walk-forward evaluation comparing
Bayesian-only, XGBoost-only, and multiple ensemble weight splits.

Usage:
  python scripts/xgb_backtest.py
  python scripts/xgb_backtest.py --min-prob 0.55 --retrain-every 30
  python scripts/xgb_backtest.py --start-date 2025-01-01
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

try:
    import xgboost as xgb
except ImportError:
    print("ERROR: xgboost is required.  pip install xgboost")
    sys.exit(1)

from xgb_model import (
    extract_features,
    XGB_PARAMS,
    FEATURE_NAMES,
    _normal_cdf,
    _safe_num,
)
from defaults import (
    XGB_RETRAIN_INTERVAL,
    XGB_MIN_TRAINING_GAMES,
    XGB_ENSEMBLE_WEIGHT,
)

ROOT = Path(__file__).resolve().parent.parent
HISTORY_PATH = ROOT / "data" / "history.json"
OUT_PATH = ROOT / "data" / "xgb_backtest_results.json"

UNIT_WIN = 1.0
UNIT_LOSS = -1.1
UNIT_PUSH = 0.0

ENSEMBLE_WEIGHTS = [0.20, 0.30, 0.35, 0.40, 0.50]
CALIBRATION_BUCKETS = [(0.50, 0.55), (0.55, 0.60), (0.60, 0.65),
                       (0.65, 0.70), (0.70, 0.75), (0.75, 1.01)]
ROLLING_WINDOWS = [50, 100, 200]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_graded_games(history_path: Path) -> List[dict]:
    """Load all graded games from history.json as flat list with date attached."""
    store = json.loads(history_path.read_text(encoding="utf-8"))
    games: List[dict] = []
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

            margin_vs_line = float((hs - aws) - line)
            try:
                feats = extract_features(g)
            except Exception:
                continue

            games.append({
                "date": date,
                "home": g.get("home", "?"),
                "away": g.get("away", "?"),
                "line": float(line),
                "homeScore": float(hs),
                "awayScore": float(aws),
                "margin_vs_line": margin_vs_line,
                "home_covered": margin_vs_line > 0,
                "is_push": margin_vs_line == 0,
                "features": feats,
                "bayes_pHomeCover": _safe_num(g.get("pHomeCover"), 0.5),
                "bayes_pCover": _safe_num(g.get("pCover"), 0.5),
                "sDiff": _safe_num(g.get("sDiff"), 0.0),
            })
    return games


# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------

class ATSTracker:
    """Tracks ATS record, units P&L, Brier score, calibration."""

    def __init__(self, name: str):
        self.name = name
        self.wins = 0
        self.losses = 0
        self.pushes = 0
        self.units = 0.0
        self.brier_sum = 0.0
        self.brier_n = 0
        self.bucket_correct: Dict[str, int] = defaultdict(int)
        self.bucket_total: Dict[str, int] = defaultdict(int)
        self.recent_results: List[float] = []  # +1/−1.1/0 per pick

    def record(self, p_cover: float, actual_cover: bool, is_push: bool,
               min_prob: float):
        """Record one prediction.  Only counts as a pick if p >= min_prob."""
        # Brier score on all non-push games (regardless of min_prob filter)
        if not is_push:
            self.brier_sum += (p_cover - (1.0 if actual_cover else 0.0)) ** 2
            self.brier_n += 1

        # Calibration bucket (all non-push)
        if not is_push:
            for lo, hi in CALIBRATION_BUCKETS:
                if lo <= p_cover < hi:
                    bk = f"{lo:.2f}-{hi:.2f}"
                    self.bucket_total[bk] += 1
                    if actual_cover:
                        self.bucket_correct[bk] += 1
                    break

        # ATS record only for picks above threshold
        if p_cover < min_prob:
            return

        if is_push:
            self.pushes += 1
            self.units += UNIT_PUSH
            self.recent_results.append(UNIT_PUSH)
        elif actual_cover:
            self.wins += 1
            self.units += UNIT_WIN
            self.recent_results.append(UNIT_WIN)
        else:
            self.losses += 1
            self.units += UNIT_LOSS
            self.recent_results.append(UNIT_LOSS)

    @property
    def total_picks(self) -> int:
        return self.wins + self.losses + self.pushes

    @property
    def win_pct(self) -> float:
        denom = self.wins + self.losses
        return (self.wins / denom * 100) if denom > 0 else 0.0

    @property
    def roi_pct(self) -> float:
        return (self.units / self.total_picks * 100) if self.total_picks > 0 else 0.0

    @property
    def brier(self) -> float:
        return (self.brier_sum / self.brier_n) if self.brier_n > 0 else 0.0

    def rolling_units(self, window: int) -> float:
        if not self.recent_results:
            return 0.0
        sl = self.recent_results[-window:]
        return sum(sl)

    def rolling_record(self, window: int) -> Tuple[int, int, int]:
        sl = self.recent_results[-window:]
        w = sum(1 for r in sl if r == UNIT_WIN)
        l = sum(1 for r in sl if r == UNIT_LOSS)
        p = sum(1 for r in sl if r == UNIT_PUSH)
        return w, l, p

    def summary_dict(self) -> dict:
        cal = {}
        for bk in sorted(self.bucket_total.keys()):
            t = self.bucket_total[bk]
            c = self.bucket_correct[bk]
            cal[bk] = {"correct": c, "total": t,
                        "pct": round(c / t * 100, 1) if t else 0.0}
        rolling = {}
        for w in ROLLING_WINDOWS:
            rw, rl, rp = self.rolling_record(w)
            rolling[f"last_{w}"] = {
                "record": f"{rw}-{rl}-{rp}",
                "units": round(self.rolling_units(w), 2),
            }
        return {
            "name": self.name,
            "record": f"{self.wins}-{self.losses}-{self.pushes}",
            "win_pct": round(self.win_pct, 1),
            "units": round(self.units, 2),
            "roi_pct": round(self.roi_pct, 2),
            "brier": round(self.brier, 4),
            "total_picks": self.total_picks,
            "calibration": cal,
            "rolling": rolling,
        }


# ---------------------------------------------------------------------------
# Walk-forward engine
# ---------------------------------------------------------------------------

def run_backtest(games: List[dict], min_train: int, retrain_every: int,
                 min_prob: float, start_date: Optional[str]) -> dict:
    """
    Walk-forward backtest.
    For each game after the burn-in window:
      - Train XGBoost on all prior games (retrain every N games)
      - Predict margin -> P(home cover) via Normal CDF
      - Record Bayesian-only, XGB-only, and ensemble predictions
    """
    n = len(games)
    if n < min_train:
        print(f"Only {n} games, need {min_train} for burn-in.  Aborting.")
        sys.exit(1)

    # Trackers
    bayes_tracker = ATSTracker("Bayesian-only")
    xgb_tracker = ATSTracker("XGBoost-only")
    ensemble_trackers = {
        w: ATSTracker(f"Ensemble(xgb={w:.0%})")
        for w in ENSEMBLE_WEIGHTS
    }

    model = None
    residual_std = 12.0
    last_train_idx = -1
    feature_importance_acc = None
    n_trains = 0
    test_count = 0

    # Storage for production-filtered ensemble evaluation
    pf_ens_p_homes: Dict[float, List[float]] = {w: [] for w in ENSEMBLE_WEIGHTS}
    pf_actuals: List[bool] = []
    pf_pushes: List[bool] = []
    pf_sdiffs: List[float] = []
    pf_lines: List[float] = []

    for i in range(min_train, n):
        g = games[i]

        # Optional date filter
        if start_date and g["date"] < start_date:
            continue

        # (Re)train if needed
        games_since_train = i - last_train_idx if last_train_idx >= 0 else retrain_every + 1
        if model is None or games_since_train >= retrain_every:
            X_train = np.array([games[j]["features"] for j in range(i)],
                               dtype=np.float64)
            y_train = np.array([games[j]["margin_vs_line"] for j in range(i)],
                               dtype=np.float64)
            model = xgb.XGBRegressor(**XGB_PARAMS)
            model.fit(X_train, y_train)
            residuals = y_train - model.predict(X_train)
            residual_std = max(float(np.std(residuals)), 1.0)
            last_train_idx = i

            # Accumulate feature importance
            imp = model.feature_importances_
            if feature_importance_acc is None:
                feature_importance_acc = np.zeros_like(imp)
            feature_importance_acc += imp
            n_trains += 1

        # Predict
        x = np.array([g["features"]], dtype=np.float64)
        pred_margin = float(model.predict(x)[0])
        xgb_p_home_cover = _normal_cdf(pred_margin / residual_std)

        bayes_p_home = g["bayes_pHomeCover"]
        actual_cover = g["home_covered"]
        is_push = g["is_push"]

        # Determine pick side for each model:
        # We evaluate P(cover) for the side each model likes.
        # XGB side
        if xgb_p_home_cover >= 0.5:
            xgb_p = xgb_p_home_cover
            xgb_actual = actual_cover
        else:
            xgb_p = 1.0 - xgb_p_home_cover
            xgb_actual = not actual_cover

        # Bayesian side
        if bayes_p_home >= 0.5:
            bayes_p = bayes_p_home
            bayes_actual = actual_cover
        else:
            bayes_p = 1.0 - bayes_p_home
            bayes_actual = not actual_cover

        bayes_tracker.record(bayes_p, bayes_actual, is_push, min_prob)
        xgb_tracker.record(xgb_p, xgb_actual, is_push, min_prob)

        # Ensembles at various weight splits
        for w_xgb, tracker in ensemble_trackers.items():
            ens_p_home = (1.0 - w_xgb) * bayes_p_home + w_xgb * xgb_p_home_cover
            if ens_p_home >= 0.5:
                ens_p = ens_p_home
                ens_actual = actual_cover
            else:
                ens_p = 1.0 - ens_p_home
                ens_actual = not actual_cover
            tracker.record(ens_p, ens_actual, is_push, min_prob)
            pf_ens_p_homes[w_xgb].append(ens_p_home)

        pf_actuals.append(actual_cover)
        pf_pushes.append(is_push)
        pf_sdiffs.append(g["sDiff"])
        pf_lines.append(g["line"])

        test_count += 1

    # Feature importance (averaged across retrains)
    feat_imp = {}
    if feature_importance_acc is not None and n_trains > 0:
        avg_imp = feature_importance_acc / n_trains
        names = FEATURE_NAMES[:len(avg_imp)]
        if len(names) < len(avg_imp):
            names += [f"f{i}" for i in range(len(names), len(avg_imp))]
        pairs = sorted(zip(names, avg_imp), key=lambda p: p[1], reverse=True)
        feat_imp = {name: round(float(v), 5) for name, v in pairs[:15]}

    # --- Production-filtered ensemble evaluation ---
    PROD_MIN_PROB = 0.57
    PROD_SDIFF_CAP = 9.0
    PROD_ABS_LINE_CAP = 12.0

    prod_filtered = {}
    for w_xgb in ENSEMBLE_WEIGHTS:
        label = f"Ensemble(xgb={w_xgb:.0%})"
        p_homes = pf_ens_p_homes[w_xgb]
        wins = losses = filtered_out = 0
        for j in range(len(pf_actuals)):
            if pf_pushes[j]:
                continue
            p_home = p_homes[j]
            if p_home >= 0.5:
                p_cover = p_home
                actual = pf_actuals[j]
            else:
                p_cover = 1.0 - p_home
                actual = not pf_actuals[j]
            if p_cover < PROD_MIN_PROB:
                continue
            if pf_sdiffs[j] > PROD_SDIFF_CAP or abs(pf_lines[j]) >= PROD_ABS_LINE_CAP:
                filtered_out += 1
                continue
            if actual:
                wins += 1
            else:
                losses += 1
        picks = wins + losses
        units = wins * UNIT_WIN + losses * UNIT_LOSS
        win_pct = (100.0 * wins / picks) if picks else 0.0
        roi = (100.0 * units / picks) if picks else 0.0
        prod_filtered[f"ensemble_xgb_{int(w_xgb*100)}pct"] = {
            "name": label,
            "record": f"{wins}-{losses}-0",
            "win_pct": round(win_pct, 1),
            "units": round(units, 2),
            "roi_pct": round(roi, 2),
            "total_picks": picks,
            "filtered_out": filtered_out,
        }

    return {
        "test_games": test_count,
        "total_games": n,
        "min_train": min_train,
        "retrain_every": retrain_every,
        "min_prob": min_prob,
        "start_date": start_date or "(all)",
        "n_retrains": n_trains,
        "models": {
            "bayesian": bayes_tracker.summary_dict(),
            "xgboost": xgb_tracker.summary_dict(),
            **{
                f"ensemble_xgb_{int(w*100)}pct": tracker.summary_dict()
                for w, tracker in ensemble_trackers.items()
            },
        },
        "feature_importance_top15": feat_imp,
        "production_filtered_ensemble": prod_filtered,
    }


# ---------------------------------------------------------------------------
# Console output
# ---------------------------------------------------------------------------

def print_summary(results: dict) -> None:
    print("\n" + "=" * 80)
    print("  XGBoost Walk-Forward Backtest Results")
    print("=" * 80)
    print(f"  Total games: {results['total_games']}  |  "
          f"Test games: {results['test_games']}  |  "
          f"Burn-in: {results['min_train']}  |  "
          f"Retrain every: {results['retrain_every']}")
    print(f"  Min P(cover): {results['min_prob']}  |  "
          f"Start date: {results['start_date']}  |  "
          f"Retrains: {results['n_retrains']}")

    # Main comparison table
    print("\n" + "-" * 80)
    header = f"  {'Model':<28} {'Record':<14} {'Win%':>6} {'Units':>8} {'ROI%':>7} {'Brier':>7}"
    print(header)
    print("  " + "-" * 76)

    models = results["models"]
    # Sort by units descending for display
    items = sorted(models.items(), key=lambda kv: kv[1]["units"], reverse=True)
    for key, m in items:
        print(f"  {m['name']:<28} {m['record']:<14} "
              f"{m['win_pct']:>5.1f}% {m['units']:>+7.1f}u "
              f"{m['roi_pct']:>+6.1f}% {m['brier']:>7.4f}")

    # Production-filtered ensemble results
    pf = results.get("production_filtered_ensemble")
    if pf:
        print("\n" + "-" * 80)
        print("  Production-Filtered Ensemble Results")
        print("  (P(cover)>=0.57, sDiff<=9, abs(line)<12)")
        print("  " + "-" * 76)
        pf_header = f"  {'Model':<28} {'Record':<14} {'Win%':>6} {'Units':>8} {'ROI%':>7} {'Picks':>6} {'Filt':>6}"
        print(pf_header)
        print("  " + "-" * 76)
        pf_items = sorted(pf.items(), key=lambda kv: kv[1]["units"], reverse=True)
        for key, m in pf_items:
            print(f"  {m['name']:<28} {m['record']:<14} "
                  f"{m['win_pct']:>5.1f}% {m['units']:>+7.1f}u "
                  f"{m['roi_pct']:>+6.1f}% {m['total_picks']:>5}  "
                  f"{m.get('filtered_out', 0):>5}")

    # Rolling performance
    print("\n" + "-" * 80)
    print("  Rolling Performance:")
    print(f"  {'Model':<28}", end="")
    for w in ROLLING_WINDOWS:
        print(f" {'L' + str(w) + ' Rec':<14} {'Units':>6}", end="")
    print()
    print("  " + "-" * 76)
    for key, m in items:
        print(f"  {m['name']:<28}", end="")
        for w in ROLLING_WINDOWS:
            rk = f"last_{w}"
            r = m.get("rolling", {}).get(rk, {})
            rec = r.get("record", "0-0-0")
            u = r.get("units", 0.0)
            print(f" {rec:<14} {u:>+5.1f}u", end="")
        print()

    # Calibration
    print("\n" + "-" * 80)
    print("  Calibration (XGBoost-only):")
    xgb_cal = models.get("xgboost", {}).get("calibration", {})
    if xgb_cal:
        print(f"  {'Bucket':<14} {'Correct':>8} {'Total':>8} {'Actual%':>8} {'Expected':>9}")
        print("  " + "-" * 50)
        for bk in sorted(xgb_cal.keys()):
            c = xgb_cal[bk]
            lo_s = bk.split("-")[0]
            hi_s = bk.split("-")[1]
            expected_mid = (float(lo_s) + float(hi_s)) / 2.0 * 100
            print(f"  {bk:<14} {c['correct']:>8} {c['total']:>8} "
                  f"{c['pct']:>7.1f}% {expected_mid:>8.1f}%")

    # Feature importance
    print("\n" + "-" * 80)
    print("  Feature Importance (Top 15, averaged across retrains):")
    feat_imp = results.get("feature_importance_top15", {})
    if feat_imp:
        max_val = max(feat_imp.values()) if feat_imp else 1.0
        for name, val in feat_imp.items():
            bar_len = int(val / max_val * 30) if max_val > 0 else 0
            bar = "#" * bar_len
            print(f"  {name:<20} {val:.5f}  {bar}")

    print("\n" + "=" * 80)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="XGBoost walk-forward backtest")
    ap.add_argument("--history", default=str(HISTORY_PATH),
                    help="Path to history.json")
    ap.add_argument("--min-prob", type=float, default=0.57,
                    help="Minimum P(cover) to count as a pick (default 0.57)")
    ap.add_argument("--start-date", default=None,
                    help="Only evaluate games on or after this date (YYYY-MM-DD)")
    ap.add_argument("--retrain-every", type=int, default=XGB_RETRAIN_INTERVAL,
                    help=f"Retrain interval in games (default {XGB_RETRAIN_INTERVAL})")
    ap.add_argument("--min-train", type=int, default=XGB_MIN_TRAINING_GAMES,
                    help=f"Burn-in games before first prediction (default {XGB_MIN_TRAINING_GAMES})")
    ap.add_argument("--quiet", action="store_true",
                    help="Suppress console output (still saves JSON)")
    return ap.parse_args()


def main() -> None:
    args = parse_args()

    history_path = Path(args.history)
    if not history_path.exists():
        print(f"ERROR: {history_path} not found")
        sys.exit(1)

    print(f"Loading games from {history_path} ...")
    games = load_graded_games(history_path)
    print(f"  {len(games)} graded games loaded")

    results = run_backtest(
        games,
        min_train=args.min_train,
        retrain_every=args.retrain_every,
        min_prob=args.min_prob,
        start_date=args.start_date,
    )

    # Save JSON
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nResults saved to {OUT_PATH}")

    if not args.quiet:
        print_summary(results)


if __name__ == "__main__":
    main()

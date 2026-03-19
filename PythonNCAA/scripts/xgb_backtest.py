#!/usr/bin/env python
"""
XGBoost walk-forward backtest for NCAA basketball predictions.

Loads history.json, replays every graded game in chronological order,
and compares Bayesian-only vs XGBoost-only vs several ensemble weight
splits.  Tracks ATS record, units P&L, calibration, Brier score, ROI,
feature importance, and NCAA-specific splits (tournament, seed diff,
conference).

Usage:
  python scripts/xgb_backtest.py
  python scripts/xgb_backtest.py --min-prob 0.55 --retrain-every 30
  python scripts/xgb_backtest.py --tournament-only --start-date 2024-03-01
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    import xgboost as xgb
except ImportError:
    sys.exit("[xgb_backtest] xgboost is required -- pip install xgboost")

try:
    from sklearn.linear_model import LogisticRegression
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

from defaults import (
    XGB_MIN_TRAINING_GAMES,
    XGB_RETRAIN_INTERVAL,
    XGB_ENSEMBLE_WEIGHT,
)
from xgb_model import (
    extract_features,
    normal_cdf,
    FEATURE_NAMES,
    _safe_num,
)

ROOT = Path(__file__).resolve().parent.parent
HISTORY_PATH = ROOT / "data" / "history.json"
OUT_PATH = ROOT / "data" / "xgb_backtest_results.json"

UNIT_WIN = 1.0
UNIT_LOSS = -1.1
UNIT_PUSH = 0.0

ENSEMBLE_WEIGHTS = [0.20, 0.30, 0.35, 0.40, 0.50]

CALIBRATION_BUCKETS = [
    (0.50, 0.55),
    (0.55, 0.60),
    (0.60, 0.65),
    (0.65, 0.70),
    (0.70, 0.75),
    (0.75, 1.01),
]

SEED_DIFF_BUCKETS = [
    ("0-3", 0, 3),
    ("4-7", 4, 7),
    ("8-12", 8, 12),
    ("13+", 13, 99),
]

# ── Data structures ──────────────────────────────────────────────────────

class GameRow:
    __slots__ = (
        "date", "x", "y", "cover", "push", "bayes_p",
        "is_tournament", "is_neutral", "seed_diff",
        "is_conference", "home", "away", "line", "sDiff",
    )

    def __init__(self, *, date, x, y, cover, push, bayes_p,
                 is_tournament, is_neutral, seed_diff,
                 is_conference, home, away, line, sDiff):
        self.date = date
        self.x = x
        self.y = y
        self.cover = cover
        self.push = push
        self.bayes_p = bayes_p
        self.is_tournament = is_tournament
        self.is_neutral = is_neutral
        self.seed_diff = seed_diff
        self.is_conference = is_conference
        self.home = home
        self.away = away
        self.line = line
        self.sDiff = sDiff


def load_rows(path: Path, start_date: str = "") -> List[GameRow]:
    """Load graded games from history.json into GameRow list."""
    store = json.loads(path.read_text(encoding="utf-8"))
    rows: List[GameRow] = []
    for run in store.get("runs", []):
        if run.get("burnIn"):
            continue
        date = str(run.get("date", ""))
        if start_date and date < start_date:
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
            if not g.get("_features"):
                continue

            margin_vs_line = float((hs - aws) - line)
            is_tourney = bool(g.get("is_tournament"))
            is_neutral = bool(g.get("is_neutral"))
            seed_diff_val = _safe_num(g.get("seed_diff"), 0.0)
            is_conf = bool(g.get("is_conference"))

            rows.append(GameRow(
                date=date,
                x=extract_features(g),
                y=margin_vs_line,
                cover=margin_vs_line > 0,
                push=margin_vs_line == 0,
                bayes_p=_safe_num(g.get("pHomeCover"), 0.5),
                is_tournament=is_tourney,
                is_neutral=is_neutral,
                seed_diff=seed_diff_val,
                is_conference=is_conf,
                home=g.get("home", ""),
                away=g.get("away", ""),
                line=float(line),
                sDiff=_safe_num(g.get("sDiff"), 0.0),
            ))
    return rows


# ── XGB helpers ──────────────────────────────────────────────────────────

def _build_xgb() -> xgb.XGBRegressor:
    return xgb.XGBRegressor(
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


def _platt_calibrate(
    raw_probs: np.ndarray,
    labels: np.ndarray,
    pushes: np.ndarray,
) -> Optional[LogisticRegression]:
    """Train Platt calibrator on non-push games."""
    if not HAS_SKLEARN:
        return None
    mask = ~pushes
    if mask.sum() < 40 or len(set(labels[mask].tolist())) < 2:
        return None
    cal = LogisticRegression(solver="lbfgs", max_iter=500)
    cal.fit(raw_probs[mask].reshape(-1, 1), labels[mask])
    return cal


def _apply_calibrator(cal: Optional[LogisticRegression], raw_p: float) -> float:
    if cal is None:
        return raw_p
    try:
        return float(cal.predict_proba(np.array([[raw_p]]))[0, 1])
    except Exception:
        return raw_p


# ── Tracking helpers ─────────────────────────────────────────────────────

class PickTracker:
    """Accumulates ATS record, units, and Brier score for a model."""

    def __init__(self, name: str):
        self.name = name
        self.wins = 0
        self.losses = 0
        self.pushes = 0
        self.units = 0.0
        self.brier_sum = 0.0
        self.brier_n = 0
        self.probs: List[float] = []
        self.actuals: List[bool] = []
        self.is_push: List[bool] = []
        # for rolling
        self._results: List[float] = []  # +1 / -1.1 / 0

    def add(self, p_cover: float, covered: bool, push: bool):
        self.probs.append(p_cover)
        self.actuals.append(covered)
        self.is_push.append(push)
        if push:
            self.pushes += 1
            self._results.append(UNIT_PUSH)
            return
        # Brier score
        label = 1.0 if covered else 0.0
        self.brier_sum += (p_cover - label) ** 2
        self.brier_n += 1
        # ATS
        predicted_cover = p_cover >= 0.5
        if predicted_cover == covered:
            self.wins += 1
            self.units += UNIT_WIN
            self._results.append(UNIT_WIN)
        else:
            self.losses += 1
            self.units += UNIT_LOSS
            self._results.append(UNIT_LOSS)

    @property
    def picks(self) -> int:
        return self.wins + self.losses

    @property
    def win_pct(self) -> float:
        return 100.0 * self.wins / self.picks if self.picks else 0.0

    @property
    def roi(self) -> float:
        return 100.0 * self.units / self.picks if self.picks else 0.0

    @property
    def brier(self) -> float:
        return self.brier_sum / self.brier_n if self.brier_n else 0.0

    def record_str(self) -> str:
        return f"{self.wins}-{self.losses}-{self.pushes}"

    def rolling_units(self, window: int) -> float:
        if len(self._results) < window:
            return sum(self._results)
        return sum(self._results[-window:])

    def rolling_record(self, window: int) -> str:
        tail = self._results[-window:] if len(self._results) >= window else self._results
        w = sum(1 for r in tail if r == UNIT_WIN)
        l = sum(1 for r in tail if r == UNIT_LOSS)
        p = sum(1 for r in tail if r == UNIT_PUSH)
        return f"{w}-{l}-{p}"

    def calibration_table(self) -> List[dict]:
        """Group picks by P(cover) bucket and report actual cover rate."""
        table = []
        for lo, hi in CALIBRATION_BUCKETS:
            # also check the mirror bucket for away-side picks
            bucket_n = 0
            bucket_correct = 0
            for i, p in enumerate(self.probs):
                if self.is_push[i]:
                    continue
                # home-side pick
                if lo <= p < hi:
                    bucket_n += 1
                    bucket_correct += int(self.actuals[i])
                # away-side pick (mirror)
                elif lo <= (1.0 - p) < hi:
                    bucket_n += 1
                    bucket_correct += int(not self.actuals[i])
            pct = 100.0 * bucket_correct / bucket_n if bucket_n else 0.0
            table.append({
                "bucket": f"{lo:.2f}-{hi:.2f}",
                "n": bucket_n,
                "correct": bucket_correct,
                "win_pct": round(pct, 1),
            })
        return table

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "record": self.record_str(),
            "picks": self.picks,
            "win_pct": round(self.win_pct, 1),
            "units": round(self.units, 1),
            "roi_pct": round(self.roi, 2),
            "brier": round(self.brier, 4),
        }


class SplitTracker:
    """Tracks W-L-P and units for a named subset of games."""

    def __init__(self):
        self.w = 0
        self.l = 0
        self.p = 0
        self.units = 0.0

    def add(self, won: bool, push: bool):
        if push:
            self.p += 1
            return
        if won:
            self.w += 1
            self.units += UNIT_WIN
        else:
            self.l += 1
            self.units += UNIT_LOSS

    @property
    def picks(self):
        return self.w + self.l

    @property
    def win_pct(self):
        return 100.0 * self.w / self.picks if self.picks else 0.0

    def to_dict(self) -> dict:
        return {
            "record": f"{self.w}-{self.l}-{self.p}",
            "picks": self.picks,
            "win_pct": round(self.win_pct, 1),
            "units": round(self.units, 1),
        }


# ── Walk-forward backtest ────────────────────────────────────────────────

def run_backtest(
    rows: List[GameRow],
    min_train: int,
    retrain_every: int,
    min_prob: float,
    tournament_only: bool,
) -> dict:
    n = len(rows)
    if n < min_train + 1:
        sys.exit(f"[xgb_backtest] Only {n} rows, need at least {min_train + 1}")

    # ── Trackers ─────────────────────────────────────────────────────
    bayes_tracker = PickTracker("Bayesian-only")
    xgb_tracker = PickTracker("XGBoost-only")
    ens_trackers = {w: PickTracker(f"Ensemble(xgb={w:.0%})") for w in ENSEMBLE_WEIGHTS}

    # NCAA splits (for the ensemble at default weight)
    default_ew = XGB_ENSEMBLE_WEIGHT
    split_regular = SplitTracker()
    split_tournament = SplitTracker()
    split_conf = SplitTracker()
    split_nonconf = SplitTracker()
    seed_buckets: Dict[str, SplitTracker] = {b[0]: SplitTracker() for b in SEED_DIFF_BUCKETS}

    # Storage for production-filtered ensemble evaluation
    pf_ens_probs: Dict[float, List[float]] = {w: [] for w in ENSEMBLE_WEIGHTS}
    pf_actuals: List[bool] = []
    pf_pushes: List[bool] = []
    pf_sdiffs: List[float] = []
    pf_lines: List[float] = []

    # Feature importance accumulators
    fi_sums = np.zeros(len(FEATURE_NAMES), dtype=np.float64)
    fi_count = 0

    # XGB state
    model: Optional[xgb.XGBRegressor] = None
    residual_std = 14.0
    calibrator: Optional[LogisticRegression] = None
    last_train_idx = -1
    games_since_train = 0

    x_all = np.array([r.x for r in rows], dtype=np.float64)
    x_all = np.nan_to_num(x_all, nan=0.0, posinf=0.0, neginf=0.0)
    y_all = np.array([r.y for r in rows], dtype=np.float64)

    tested = 0

    for i in range(min_train, n):
        r = rows[i]

        # Skip non-tournament if --tournament-only
        if tournament_only and not r.is_tournament:
            continue

        # ── Retrain check ────────────────────────────────────────────
        need_train = model is None or games_since_train >= retrain_every
        if need_train:
            xt = x_all[:i]
            yt = y_all[:i]
            model = _build_xgb()
            model.fit(xt, yt)
            preds_train = model.predict(xt)
            residual_std = max(float(np.std(yt - preds_train)), 1.0)

            # Platt calibrator
            raw_p_train = np.array([normal_cdf(p / residual_std) for p in preds_train])
            labels_train = (yt > 0).astype(int)
            pushes_train = (yt == 0)
            calibrator = _platt_calibrate(raw_p_train, labels_train, pushes_train)

            # Feature importance
            fi = model.feature_importances_
            fi_sums += fi
            fi_count += 1

            last_train_idx = i
            games_since_train = 0

        games_since_train += 1

        # ── Predict ──────────────────────────────────────────────────
        x_row = x_all[i:i+1]
        pred_margin = float(model.predict(x_row)[0])
        raw_p = normal_cdf(pred_margin / residual_std)
        cal_p = _apply_calibrator(calibrator, raw_p)

        bayes_p = r.bayes_p
        covered = r.cover
        push = r.push

        # ── Record results ───────────────────────────────────────────
        bayes_tracker.add(bayes_p, covered, push)
        xgb_tracker.add(cal_p, covered, push)

        for ew, tracker in ens_trackers.items():
            ens_p = (1.0 - ew) * bayes_p + ew * cal_p
            tracker.add(ens_p, covered, push)
            pf_ens_probs[ew].append(ens_p)

        pf_actuals.append(covered)
        pf_pushes.append(push)
        pf_sdiffs.append(r.sDiff)
        pf_lines.append(r.line)

        # NCAA splits (using default ensemble weight)
        ens_default_p = (1.0 - default_ew) * bayes_p + default_ew * cal_p
        ens_predicted_cover = ens_default_p >= 0.5
        ens_won = (ens_predicted_cover == covered) if not push else False

        if r.is_tournament:
            split_tournament.add(ens_won, push)
        else:
            split_regular.add(ens_won, push)

        if r.is_conference:
            split_conf.add(ens_won, push)
        else:
            split_nonconf.add(ens_won, push)

        sd = abs(r.seed_diff)
        for label, lo, hi in SEED_DIFF_BUCKETS:
            if lo <= sd <= hi:
                seed_buckets[label].add(ens_won, push)
                break

        tested += 1

    # ── Feature importance ───────────────────────────────────────────
    fi_avg = (fi_sums / fi_count) if fi_count > 0 else fi_sums
    fi_pairs = sorted(zip(FEATURE_NAMES, fi_avg.tolist()), key=lambda t: -t[1])
    top_features = [{"feature": f, "importance": round(v, 4)} for f, v in fi_pairs[:15]]

    # ── Production-filtered ensemble evaluation ────────────────────
    PROD_MIN_PROB = 0.60
    PROD_FAV_LINE_CAP = 8.0  # fav picks capped at 8, dogs uncapped

    prod_filtered_models = []
    for ew in ENSEMBLE_WEIGHTS:
        label = f"Ensemble(xgb={ew:.0%})"
        probs_list = pf_ens_probs[ew]
        wins = losses = filtered_out = 0
        for j in range(len(pf_actuals)):
            if pf_pushes[j]:
                continue
            p_home = probs_list[j]
            line = pf_lines[j]
            # Determine pick side
            if p_home >= 0.5:
                p_cover = p_home
                actual = pf_actuals[j]
                picked_side_is_dog = line < 0  # picking home, line negative = home is dog
            else:
                p_cover = 1.0 - p_home
                actual = not pf_actuals[j]
                picked_side_is_dog = line > 0  # picking away, line positive = away is dog
            if p_cover < PROD_MIN_PROB:
                continue
            # Fav line cap: dogs uncapped, favs capped at 8
            if not picked_side_is_dog and abs(line) > PROD_FAV_LINE_CAP:
                filtered_out += 1
                continue
            if abs(line) == 0:
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
        prod_filtered_models.append({
            "name": label,
            "record": f"{wins}-{losses}-0",
            "picks": picks,
            "win_pct": round(win_pct, 1),
            "units": round(units, 1),
            "roi_pct": round(roi, 2),
            "filtered_out": filtered_out,
        })

    # ── Assemble output ──────────────────────────────────────────────
    all_trackers = [bayes_tracker, xgb_tracker] + [ens_trackers[w] for w in ENSEMBLE_WEIGHTS]

    results = {
        "n_total_rows": n,
        "n_tested": tested,
        "min_train": min_train,
        "retrain_every": retrain_every,
        "min_prob": min_prob,
        "tournament_only": tournament_only,
        "models": [t.to_dict() for t in all_trackers],
        "calibration": {t.name: t.calibration_table() for t in all_trackers},
        "rolling": {},
        "ncaa_splits": {
            "regular_season": split_regular.to_dict(),
            "tournament": split_tournament.to_dict(),
            "conference": split_conf.to_dict(),
            "non_conference": split_nonconf.to_dict(),
            "seed_diff": {k: v.to_dict() for k, v in seed_buckets.items()},
        },
        "feature_importance_top15": top_features,
        "production_filtered_ensemble": prod_filtered_models,
    }

    for window in (50, 100, 200):
        results["rolling"][f"last_{window}"] = {
            t.name: {
                "record": t.rolling_record(window),
                "units": round(t.rolling_units(window), 1),
            }
            for t in all_trackers
        }

    return results, all_trackers


# ── Console output ───────────────────────────────────────────────────────

def print_results(results: dict, trackers: List[PickTracker]):
    sep = "=" * 90

    print(f"\n{sep}")
    print("  NCAA XGBoost Walk-Forward Backtest")
    print(sep)
    print(f"  Total rows: {results['n_total_rows']}   Tested: {results['n_tested']}")
    print(f"  Burn-in: {results['min_train']}   Retrain every: {results['retrain_every']} games")
    if results["tournament_only"]:
        print("  ** Tournament games only **")
    print()

    # ── Model comparison table ───────────────────────────────────────
    header = f"  {'Model':<28s} {'Record':<14s} {'Win%':>6s} {'Units':>8s} {'ROI%':>7s} {'Brier':>7s}"
    print(header)
    print("  " + "-" * 78)
    for m in results["models"]:
        print(
            f"  {m['name']:<28s} {m['record']:<14s} "
            f"{m['win_pct']:5.1f}% {m['units']:+7.1f} "
            f"{m['roi_pct']:6.2f}% {m['brier']:.4f}"
        )

    # ── Production-filtered ensemble results ────────────────────────
    pf_models = results.get("production_filtered_ensemble", [])
    if pf_models:
        print(f"\n{sep}")
        print("  Production-Filtered Ensemble Results")
        print("  (P(cover)>=0.60, favLine<=8, dogs uncapped)")
        print(sep)
        print(f"  {'Model':<28s} {'Record':<14s} {'Win%':>6s} {'Units':>8s} {'ROI%':>7s} {'Picks':>6s} {'Filt':>6s}")
        print("  " + "-" * 78)
        pf_sorted = sorted(pf_models, key=lambda m: m["units"], reverse=True)
        for m in pf_sorted:
            print(
                f"  {m['name']:<28s} {m['record']:<14s} "
                f"{m['win_pct']:5.1f}% {m['units']:+7.1f} "
                f"{m['roi_pct']:6.2f}% {m['picks']:5d}  "
                f"{m.get('filtered_out', 0):5d}"
            )

    # ── Calibration ──────────────────────────────────────────────────
    print(f"\n{sep}")
    print("  Calibration by P(cover) bucket")
    print(sep)
    # Show for the top 3 models
    for t in trackers[:3]:
        print(f"\n  {t.name}:")
        print(f"    {'Bucket':<12s} {'N':>5s} {'Correct':>8s} {'Win%':>7s}")
        for row in t.calibration_table():
            print(f"    {row['bucket']:<12s} {row['n']:5d} {row['correct']:8d} {row['win_pct']:6.1f}%")

    # ── NCAA-specific splits ─────────────────────────────────────────
    print(f"\n{sep}")
    print(f"  NCAA Splits (Ensemble xgb={XGB_ENSEMBLE_WEIGHT:.0%})")
    print(sep)

    splits = results["ncaa_splits"]
    print(f"\n  {'Split':<20s} {'Record':<14s} {'Picks':>6s} {'Win%':>6s} {'Units':>8s}")
    print("  " + "-" * 60)
    for label, data in [
        ("Regular Season", splits["regular_season"]),
        ("Tournament", splits["tournament"]),
        ("Conference", splits["conference"]),
        ("Non-Conference", splits["non_conference"]),
    ]:
        print(
            f"  {label:<20s} {data['record']:<14s} {data['picks']:6d} "
            f"{data['win_pct']:5.1f}% {data['units']:+7.1f}"
        )

    print(f"\n  Seed Differential Buckets (tournament games):")
    print(f"  {'Bucket':<12s} {'Record':<14s} {'Picks':>6s} {'Win%':>6s} {'Units':>8s}")
    print("  " + "-" * 52)
    for label, data in splits["seed_diff"].items():
        if data["picks"] > 0:
            print(
                f"  {label:<12s} {data['record']:<14s} {data['picks']:6d} "
                f"{data['win_pct']:5.1f}% {data['units']:+7.1f}"
            )

    # ── Feature importance ───────────────────────────────────────────
    print(f"\n{sep}")
    print("  Top 15 Feature Importance (avg across retrains)")
    print(sep)
    print(f"  {'Rank':>4s}  {'Feature':<22s} {'Importance':>10s}")
    print("  " + "-" * 40)
    for rank, fi in enumerate(results["feature_importance_top15"], 1):
        print(f"  {rank:4d}  {fi['feature']:<22s} {fi['importance']:10.4f}")

    # ── Rolling performance ──────────────────────────────────────────
    print(f"\n{sep}")
    print("  Rolling Performance")
    print(sep)
    for window_key, window_data in results["rolling"].items():
        w = window_key.replace("last_", "")
        print(f"\n  Last {w} picks:")
        print(f"    {'Model':<28s} {'Record':<14s} {'Units':>8s}")
        print("    " + "-" * 52)
        for model_name, vals in window_data.items():
            print(f"    {model_name:<28s} {vals['record']:<14s} {vals['units']:+7.1f}")

    print(f"\n{sep}\n")


# ── CLI ──────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="NCAA XGBoost walk-forward backtest")
    ap.add_argument(
        "--history", default=str(HISTORY_PATH),
        help="Path to history.json (default: data/history.json)",
    )
    ap.add_argument(
        "--min-prob", type=float, default=0.50,
        help="Minimum P(cover) to count as a pick (default: 0.50)",
    )
    ap.add_argument(
        "--start-date", default="",
        help="Only include games on or after this date (YYYY-MM-DD)",
    )
    ap.add_argument(
        "--retrain-every", type=int, default=XGB_RETRAIN_INTERVAL,
        help=f"Retrain XGBoost every N games (default: {XGB_RETRAIN_INTERVAL})",
    )
    ap.add_argument(
        "--min-train", type=int, default=XGB_MIN_TRAINING_GAMES,
        help=f"Minimum training games before predicting (default: {XGB_MIN_TRAINING_GAMES})",
    )
    ap.add_argument(
        "--tournament-only", action="store_true",
        help="Only evaluate on NCAA tournament games",
    )
    return ap.parse_args()


def main() -> None:
    args = parse_args()

    print(f"[xgb_backtest] Loading history from {args.history} ...")
    rows = load_rows(Path(args.history), start_date=args.start_date)
    print(f"[xgb_backtest] Loaded {len(rows)} graded games")

    if not rows:
        sys.exit("[xgb_backtest] No games found in history")

    results, trackers = run_backtest(
        rows,
        min_train=args.min_train,
        retrain_every=args.retrain_every,
        min_prob=args.min_prob,
        tournament_only=args.tournament_only,
    )

    # Console output
    print_results(results, trackers)

    # Save JSON
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"[xgb_backtest] Results saved to {OUT_PATH}")


if __name__ == "__main__":
    main()

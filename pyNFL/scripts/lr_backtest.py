#!/usr/bin/env python
"""
NFL LR Confirmation Model Walk-Forward Backtest.

Tests the Logistic Regression confirmation model across multiple
confirm/veto threshold pairs using walk-forward validation.  Trains on
weeks 1-N, predicts week N+1, slides forward.

Mirrors pyNBA/scripts/lr_backtest.py architecture, adapted for NFL
weekly cadence and NFL-specific LR features.

Usage:
  cd pyNFL && python scripts/lr_backtest.py
  cd pyNFL && python scripts/lr_backtest.py --store data/nfl.json
"""

import json
import math
import re
import sys
import warnings
from collections import defaultdict
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning, module="sklearn")

try:
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import (
        accuracy_score,
        precision_score,
        recall_score,
        roc_auc_score,
    )
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from lr_model import (
    extract_lr_features,
    _team_rolling_features,
    _safe_float,
    _append_to_running,
    LR_FEATURE_NAMES,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HISTORY = ROOT / "data" / "nfl.json"

# Threshold pairs to evaluate: (confirm_threshold, veto_threshold)
THRESHOLDS = [
    (0.50, 0.50),
    (0.52, 0.48),
    (0.55, 0.45),
    (0.58, 0.42),
    (0.60, 0.40),
    (0.62, 0.38),
]

MIN_TRAIN = 50       # minimum training samples before prediction starts
RETRAIN_EVERY = 16   # retrain every ~1 NFL week (16 games)
JUICE = 1.10         # standard -110 vig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_pick(pick_str):
    """Parse a pick string like 'KC +3.5' into structured components.

    Parameters
    ----------
    pick_str : str or None
        Pick string from the store.

    Returns
    -------
    dict or None
        {"team": str, "sign": str, "pts": float} or None if unparseable.
    """
    if not pick_str or pick_str == "PASS":
        return None
    m = re.match(r"(.+?)\s+([+-])(\d+(?:\.\d+)?)", str(pick_str))
    if not m:
        return None
    return {"team": m.group(1).strip(), "sign": m.group(2), "pts": float(m.group(3))}


def grade_pick(g, pick):
    """Grade a pick against the game result.

    Parameters
    ----------
    g : dict
        Game dict with homeScore, awayScore, home, away.
    pick : dict
        Parsed pick from parse_pick().

    Returns
    -------
    bool or None
        True if pick won, False if lost, None if push.
    """
    chosen_is_home = pick["team"] == g.get("home")
    margin = (
        (g["homeScore"] - g["awayScore"])
        if chosen_is_home
        else (g["awayScore"] - g["homeScore"])
    )
    val = margin + pick["pts"] if pick["sign"] == "+" else margin - pick["pts"]
    if val == 0:
        return None  # push
    return val > 0


# ---------------------------------------------------------------------------
# Walk-forward backtest
# ---------------------------------------------------------------------------

def run_lr_backtest(store, data_dir=None):
    """Run walk-forward LR backtest on NFL store data.

    Trains on games 0..N, predicts game N+1, slides forward.
    Evaluates multiple threshold pairs for confirm/veto.

    Parameters
    ----------
    store : dict
        NFL store with "runs" key.
    data_dir : str or None
        Not used directly (for API compatibility).

    Returns
    -------
    dict
        {
            "accuracy": float,   # overall accuracy of LR probability > 0.5
            "precision": float,
            "recall": float,
            "auc": float,
            "n_predictions": int,
            "threshold_results": dict,  # per threshold pair
            "by_week": dict,            # per week results
        }
    """
    if not HAS_SKLEARN:
        print("  [lr_backtest] scikit-learn not installed")
        return {"error": "scikit-learn not installed"}

    running = defaultdict(list)
    rows = []

    # Walk through all graded games, building features incrementally
    for run in store.get("runs", []):
        if run.get("burnIn"):
            continue
        run_date = str(run.get("date", ""))
        week_label = run.get("week", run_date)

        for g in run.get("games", []):
            hs = g.get("homeScore")
            aws = g.get("awayScore")
            line = g.get("line")
            if not isinstance(hs, (int, float)):
                continue
            if not isinstance(aws, (int, float)):
                continue
            if not isinstance(line, (int, float)):
                continue
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue

            home = g.get("home", "")
            away = g.get("away", "")

            # Extract features using history *before* this game
            home_hist = list(running.get(home, []))
            away_hist = list(running.get(away, []))

            features = None
            if len(home_hist) >= 3 and len(away_hist) >= 3:
                hf = _team_rolling_features(home_hist)
                af = _team_rolling_features(away_hist)

                analysis = {
                    "epa_diff": _safe_float(g.get("epa_diff")),
                    "pass_epa_diff": _safe_float(g.get("pass_epa_diff")),
                    "rush_epa_diff": _safe_float(g.get("rush_epa_diff")),
                    "def_epa_diff": _safe_float(g.get("def_epa_diff")),
                    "home_injury_delta": _safe_float(g.get("home_injury_delta")),
                    "away_injury_delta": _safe_float(g.get("away_injury_delta")),
                    "market_line": line,
                    "sdiff": _safe_float(g.get("sdiff", g.get("sDiff"))),
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
                    "home_flag": 1.0 if line < 0 else 0.0,
                    "rest_advantage": hf["rest_days"] - af["rest_days"],
                }

                features = extract_lr_features(analysis)

            # Grade the Bayesian pick (if one was made)
            pick = parse_pick(g.get("sPick"))
            label = None
            if pick:
                label = grade_pick(g, pick)

            if features is not None and label is not None:
                rows.append({
                    "features": features,
                    "label": 1 if label else 0,
                    "date": run_date,
                    "week": week_label,
                    "home": home,
                    "away": away,
                })

            # Append to running histories
            g_for_running = {
                **g,
                "homeScore": hs,
                "awayScore": aws,
                "line": line,
                "total": g.get("total", hs + aws),
            }
            _append_to_running(running, g_for_running, run_date)

    print(f"  [lr_backtest] Total rows with features + graded picks: {len(rows)}")

    if len(rows) < MIN_TRAIN + 1:
        print(f"  [lr_backtest] Only {len(rows)} rows, need {MIN_TRAIN + 1}")
        return {
            "error": f"insufficient data ({len(rows)} rows, need {MIN_TRAIN + 1})",
            "n_predictions": 0,
        }

    X = np.array([r["features"] for r in rows], dtype=np.float64)
    y = np.array([r["label"] for r in rows], dtype=np.int32)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    # Walk-forward evaluation
    results = {
        t: {"w": 0, "l": 0, "vetoed_w": 0, "vetoed_l": 0,
            "confirmed": 0, "vetoed": 0, "neutral": 0}
        for t in THRESHOLDS
    }
    bayes_only = {"w": 0, "l": 0}
    all_probs = []
    all_labels = []
    by_week = defaultdict(lambda: {"w": 0, "l": 0, "n": 0})

    model = None
    scaler = None
    last_train = -1

    for i in range(MIN_TRAIN, len(rows)):
        # Retrain periodically
        if model is None or (i - last_train) >= RETRAIN_EVERY:
            scaler = StandardScaler()
            X_train = scaler.fit_transform(X[:i])
            model = LogisticRegression(
                C=1.0,
                class_weight="balanced",
                max_iter=1000,
                solver="lbfgs",
                random_state=42,
            )
            model.fit(X_train, y[:i])
            last_train = i

        x_test = scaler.transform(X[i:i + 1])
        lr_prob = float(model.predict_proba(x_test)[0, 1])
        actual = y[i]
        week_key = rows[i].get("week", rows[i]["date"])

        all_probs.append(lr_prob)
        all_labels.append(actual)

        if actual == 1:
            bayes_only["w"] += 1
        else:
            bayes_only["l"] += 1

        by_week[week_key]["n"] += 1
        if actual == 1:
            by_week[week_key]["w"] += 1
        else:
            by_week[week_key]["l"] += 1

        for (confirm_t, veto_t) in THRESHOLDS:
            r = results[(confirm_t, veto_t)]
            if lr_prob >= confirm_t:
                r["confirmed"] += 1
                if actual == 1:
                    r["w"] += 1
                else:
                    r["l"] += 1
            elif lr_prob < veto_t:
                r["vetoed"] += 1
                if actual == 1:
                    r["vetoed_w"] += 1
                else:
                    r["vetoed_l"] += 1
            else:
                r["neutral"] += 1
                if actual == 1:
                    r["w"] += 1
                else:
                    r["l"] += 1

    # Compute overall metrics
    all_probs_arr = np.array(all_probs)
    all_labels_arr = np.array(all_labels)
    preds_binary = (all_probs_arr >= 0.5).astype(int)

    accuracy = float(accuracy_score(all_labels_arr, preds_binary))
    try:
        precision = float(precision_score(all_labels_arr, preds_binary, zero_division=0))
    except Exception:
        precision = 0.0
    try:
        recall = float(recall_score(all_labels_arr, preds_binary, zero_division=0))
    except Exception:
        recall = 0.0
    try:
        auc = float(roc_auc_score(all_labels_arr, all_probs_arr))
    except Exception:
        auc = 0.5

    return {
        "accuracy": round(accuracy, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "auc": round(auc, 4),
        "n_predictions": len(all_probs),
        "threshold_results": {
            f"{ct:.2f}/{vt:.2f}": dict(r)
            for (ct, vt), r in results.items()
        },
        "by_week": dict(by_week),
        "bayes_only": bayes_only,
    }


# ---------------------------------------------------------------------------
# Print summary
# ---------------------------------------------------------------------------

def print_backtest_summary(result):
    """Print a formatted summary table of backtest results.

    Parameters
    ----------
    result : dict
        Output of run_lr_backtest().
    """
    if "error" in result:
        print(f"\n  [lr_backtest] ERROR: {result['error']}")
        return

    print(f"\n{'=' * 85}")
    print("NFL LR Confirmation Model — Walk-Forward Backtest")
    print(f"{'=' * 85}")
    print(f"Train min: {MIN_TRAIN} | Retrain every: {RETRAIN_EVERY}")
    print(f"Features ({len(LR_FEATURE_NAMES)}): {', '.join(LR_FEATURE_NAMES[:8])}...")
    print(f"\nOverall Metrics:")
    print(f"  Accuracy:   {result['accuracy']:.3f}")
    print(f"  Precision:  {result['precision']:.3f}")
    print(f"  Recall:     {result['recall']:.3f}")
    print(f"  AUC:        {result['auc']:.3f}")
    print(f"  Predictions: {result['n_predictions']}")

    # Bayesian-only baseline
    bayes = result.get("bayes_only", {})
    bw, bl = bayes.get("w", 0), bayes.get("l", 0)
    bt = bw + bl
    b_roi = ((bw - bl * JUICE) / bt * 100) if bt else 0
    print(f"\nBayesian Only: {bw}W-{bl}L ({bw / bt * 100:.1f}%)  "
          f"ROI: {b_roi:+.1f}%  [{bt} picks]")

    # Threshold results table
    header = (
        f"{'Confirm/Veto':<14} {'Record':<12} {'Win%':>6} {'ROI':>8} "
        f"{'Picks':>6} {'Vetoed':>7} {'Vetoed W-L':>11} {'Veto Acc':>9}"
    )
    print(f"\n{header}")
    print("-" * len(header))

    threshold_results = result.get("threshold_results", {})
    for key, r in sorted(threshold_results.items()):
        w, l = r["w"], r["l"]
        total = w + l
        pct = w / total * 100 if total else 0
        roi = ((w - l * JUICE) / total * 100) if total else 0
        vetoed = r["vetoed"]
        vw, vl = r["vetoed_w"], r["vetoed_l"]
        vt_total = vw + vl
        veto_acc = vl / vt_total * 100 if vt_total else 0

        tag = " <-- baseline" if key == "0.50/0.50" else ""
        print(
            f"{key:<14} {w}W-{l}L    "
            f"{pct:5.1f}% {roi:+7.1f}%  {total:5d}  {vetoed:6d}  "
            f"{vw}W-{vl}L     {veto_acc:6.1f}%{tag}"
        )

    # By-week breakdown
    by_week = result.get("by_week", {})
    if by_week:
        print(f"\n{'=' * 50}")
        print("Results by Week")
        print(f"{'=' * 50}")
        print(f"{'Week':<15} {'W':>4} {'L':>4} {'Total':>6} {'Win%':>7}")
        print("-" * 40)
        for week_key in sorted(by_week.keys()):
            wk = by_week[week_key]
            w, l, n = wk["w"], wk["l"], wk["n"]
            pct = w / n * 100 if n > 0 else 0
            print(f"{str(week_key):<15} {w:>4} {l:>4} {n:>6} {pct:>6.1f}%")

    # Confidence tier breakdown
    print(f"\n{'=' * 50}")
    print("Results by Confidence Tier (using 0.58/0.42 thresholds)")
    print(f"{'=' * 50}")
    r58 = threshold_results.get("0.58/0.42", {})
    if r58:
        confirmed = r58.get("confirmed", 0)
        neutral = r58.get("neutral", 0)
        vetoed = r58.get("vetoed", 0)
        print(f"  CONFIRMED picks:  {confirmed}")
        print(f"  NEUTRAL picks:    {neutral}")
        print(f"  VETOED picks:     {vetoed}")

    print(f"\nROI uses -110 juice (wins +1.0u, losses -{JUICE}u).")
    print(f"Veto Accuracy = % of vetoed picks that would have LOST (higher = better).")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    """CLI entry point for running the NFL LR backtest."""
    import argparse

    parser = argparse.ArgumentParser(description="NFL LR Confirmation Model Backtest")
    parser.add_argument("--store", type=str, default=str(HISTORY),
                        help="Path to NFL store JSON")
    args = parser.parse_args()

    store_path = Path(args.store)
    if not store_path.exists():
        print(f"  [lr_backtest] Store not found: {store_path}")
        print("  [lr_backtest] Run the pipeline to generate graded data first.")
        sys.exit(1)

    store = json.loads(store_path.read_text(encoding="utf-8"))
    result = run_lr_backtest(store)
    print_backtest_summary(result)

    return result


if __name__ == "__main__":
    main()

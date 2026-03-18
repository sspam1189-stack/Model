#!/usr/bin/env python
"""
Leakage-free ML pipeline and walk-forward backtest.

Implements:
1) Separate feature pipeline from Bayesian/Kalman outputs
2) Removal of Bayesian-derived leakage features
3) Labels rebuilt from raw outcomes
4) Strict time-based walk-forward training/evaluation
6) Online probability calibration (Platt scaling)
7) Additional independent features from pregame context

Usage:
  python scripts/ml_independent_backtest.py
  python scripts/ml_independent_backtest.py --min-train 80 --lookback 120
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import numpy as np
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.preprocessing import StandardScaler
import xgboost as xgb

ROOT = Path(__file__).resolve().parent.parent
HISTORY_PATH = ROOT / "data" / "history.json"
OUT_PATH = ROOT / "data" / "independent_ml_backtest.json"

UNIT_LOSS = -1.1


def normal_cdf(x: float) -> float:
    a1 = 0.254829592
    a2 = -0.284496736
    a3 = 1.421413741
    a4 = -1.453152027
    a5 = 1.061405429
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


def _count_injuries(injury_note: str | None, side_label: str) -> int:
    if not injury_note or not isinstance(injury_note, str):
        return 0
    m = re.search(rf"{side_label}:\s*([^|]+)", injury_note)
    if not m:
        return 0
    part = m.group(1).strip()
    if not part:
        return 0
    return len([x for x in (p.strip() for p in part.split(",")) if x])


def _b2b_flags(b2b_note: str | None) -> Tuple[int, int]:
    if not b2b_note:
        return (0, 0)
    s = b2b_note.lower()
    home = int("home" in s and "b2b" in s)
    away = int("away" in s and "b2b" in s)
    return home, away


def _delta_features(adj: dict | None, side: str, stat: str) -> float:
    if not isinstance(adj, dict):
        return 0.0
    node = adj.get(side) or {}
    return _safe_num(node.get(stat), 0.0)


def extract_independent_features(g: dict) -> List[float]:
    f = g.get("_features") or {}
    mf = g.get("_marginFeatures") or {}
    trends = g.get("trends") or {}

    line = _safe_num(g.get("line"), 0.0)
    total = _safe_num(g.get("total"), 220.0)
    abs_line = abs(line)

    # Market implied scoring from spread/total (pregame only)
    implied_home = (total + line) / 2.0
    implied_away = (total - line) / 2.0
    implied_gap = implied_home - implied_away

    # Team trend deltas (pregame external context)
    h_ats = _trend_val(trends, "home", "atsPct")
    a_ats = _trend_val(trends, "away", "atsPct")
    h_ou = _trend_val(trends, "home", "overPct")
    a_ou = _trend_val(trends, "away", "overPct")
    h_tot_pm = _trend_val(trends, "home", "totalPlusMinus")
    a_tot_pm = _trend_val(trends, "away", "totalPlusMinus")
    h_ats_pm = _trend_val(trends, "home", "atsPlusMinus")
    a_ats_pm = _trend_val(trends, "away", "atsPlusMinus")

    home_b2b, away_b2b = _b2b_flags(g.get("b2bNote"))
    home_inj = _count_injuries(g.get("injuryNote"), "Home")
    away_inj = _count_injuries(g.get("injuryNote"), "Away")

    # Pregame adjustment deltas captured at run time
    home_off_d = _delta_features(g.get("_adjDeltas"), "home", "OFF")
    away_off_d = _delta_features(g.get("_adjDeltas"), "away", "OFF")
    home_def_d = _delta_features(g.get("_adjDeltas"), "home", "DEF")
    away_def_d = _delta_features(g.get("_adjDeltas"), "away", "DEF")
    home_pace_d = _delta_features(g.get("_adjDeltas"), "home", "PACE")
    away_pace_d = _delta_features(g.get("_adjDeltas"), "away", "PACE")

    return [
        # Raw matchup/stat features
        _safe_num(f.get("dTS")),
        _safe_num(f.get("dTO")),
        _safe_num(f.get("dORR")),
        _safe_num(f.get("dNET")),
        _safe_num(f.get("avgPace"), 98.0),
        _safe_num(mf.get("dTS")),
        _safe_num(mf.get("dTO")),
        _safe_num(mf.get("dORR")),
        _safe_num(mf.get("dNET")),
        _safe_num(mf.get("_baseline")),
        _safe_num(mf.get("_pace"), 1.0),
        # Market context
        line,
        total,
        abs_line,
        implied_home,
        implied_away,
        implied_gap,
        # Interactions
        _safe_num(f.get("dNET")) * line,
        _safe_num(f.get("dNET")) * abs_line,
        _safe_num(f.get("avgPace"), 98.0) * line,
        # Trends
        h_ats,
        a_ats,
        h_ats - a_ats,
        h_ou,
        a_ou,
        h_ou - a_ou,
        h_ats_pm,
        a_ats_pm,
        h_ats_pm - a_ats_pm,
        h_tot_pm,
        a_tot_pm,
        h_tot_pm - a_tot_pm,
        # Schedule/injury
        float(home_b2b),
        float(away_b2b),
        float(home_b2b - away_b2b),
        float(home_inj),
        float(away_inj),
        float(home_inj - away_inj),
        # Lineup adjustment deltas
        home_off_d,
        away_off_d,
        home_off_d - away_off_d,
        home_def_d,
        away_def_d,
        home_def_d - away_def_d,
        home_pace_d,
        away_pace_d,
        home_pace_d - away_pace_d,
    ]


@dataclass
class GameRow:
    date: str
    x: List[float]
    y_margin_vs_line: float
    y_home_cover: bool
    is_push: bool
    bayes_p: float


def load_clean_rows(history_path: Path) -> List[GameRow]:
    store = json.loads(history_path.read_text(encoding="utf-8"))
    rows: List[GameRow] = []
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

            # Rebuild label from raw outcomes only
            y = float((hs - aws) - line)
            rows.append(
                GameRow(
                    date=date,
                    x=extract_independent_features(g),
                    y_margin_vs_line=y,
                    y_home_cover=bool(y > 0),
                    is_push=bool(y == 0),
                    bayes_p=_safe_num(g.get("pHomeCover"), 0.5),
                )
            )
    return rows


def online_calibrate(
    raw_preds: Sequence[float],
    actual: Sequence[bool],
    pushes: Sequence[bool],
    lookback: int,
    min_samples: int = 60,
) -> List[float]:
    out: List[float] = []
    for i, p in enumerate(raw_preds):
        start = max(0, i - lookback)
        xs: List[List[float]] = []
        ys: List[int] = []
        for j in range(start, i):
            if pushes[j]:
                continue
            xs.append([raw_preds[j]])
            ys.append(1 if actual[j] else 0)
        if len(xs) >= min_samples and len(set(ys)) == 2:
            lr = LogisticRegression(solver="lbfgs", max_iter=500)
            lr.fit(np.array(xs, dtype=float), np.array(ys, dtype=int))
            out.append(float(lr.predict_proba(np.array([[p]], dtype=float))[0, 1]))
        else:
            out.append(float(p))
    return out


def tune_threshold(
    preds: Sequence[float],
    actual: Sequence[bool],
    pushes: Sequence[bool],
    i: int,
    lookback: int,
    default_t: float = 0.57,
) -> float:
    if i < 40:
        return default_t
    start = max(0, i - lookback)
    best_t = default_t
    best_units = -10**9
    for t in np.arange(0.52, 0.631, 0.01):
        lo = 1.0 - t
        w = l = 0
        for j in range(start, i):
            if pushes[j]:
                continue
            p = preds[j]
            a = actual[j]
            if p >= t:
                w += int(a)
                l += int(not a)
            elif p <= lo:
                w += int(not a)
                l += int(a)
        u = w + l * UNIT_LOSS
        if u > best_units:
            best_units = u
            best_t = float(t)
    return best_t


def adaptive_weights(
    bayes: Sequence[float],
    xgb_probs: Sequence[float],
    ridge_probs: Sequence[float],
    actual: Sequence[bool],
    pushes: Sequence[bool],
    i: int,
    lookback: int,
) -> Tuple[float, float, float]:
    start = max(0, i - lookback)
    records = {"b": [0, 0], "x": [0, 0], "r": [0, 0]}
    for j in range(start, i):
        if pushes[j]:
            continue
        a = actual[j]
        for k in ("b", "x", "r"):
            records[k][1] += 1
        records["b"][0] += int((bayes[j] >= 0.5) == a)
        records["x"][0] += int((xgb_probs[j] >= 0.5) == a)
        records["r"][0] += int((ridge_probs[j] >= 0.5) == a)

    floor = 0.10
    raw = {}
    for k in ("b", "x", "r"):
        h, t = records[k]
        if t == 0:
            raw[k] = 0.0
        elif t < 30:
            raw[k] = floor
        else:
            raw[k] = max(floor, (h / t) - 0.40)
    s = raw["b"] + raw["x"] + raw["r"]
    if s <= 0:
        return (1.0, 0.0, 0.0)
    return (raw["b"] / s, raw["x"] / s, raw["r"] / s)


def evaluate_dynamic(
    name: str,
    preds: Sequence[float],
    actual: Sequence[bool],
    pushes: Sequence[bool],
    lookback: int,
) -> Dict[str, float]:
    w = l = p = correct = total = 0
    ts: List[float] = []
    for i, pr in enumerate(preds):
        if pushes[i]:
            p += 1
            continue
        total += 1
        a = actual[i]
        correct += int((pr >= 0.5) == a)
        t = tune_threshold(preds, actual, pushes, i, lookback)
        ts.append(t)
        lo = 1.0 - t
        if pr >= t:
            w += int(a)
            l += int(not a)
        elif pr <= lo:
            w += int(not a)
            l += int(a)
    picks = w + l
    return {
        "name": name,
        "wins": w,
        "losses": l,
        "pushes": p,
        "picks": picks,
        "win_pct": (100.0 * w / picks) if picks else 0.0,
        "units": w + l * UNIT_LOSS,
        "all_side_acc": (100.0 * correct / total) if total else 0.0,
        "avg_t": float(np.mean(ts)) if ts else 0.57,
    }


def walk_forward(rows: Sequence[GameRow], min_train: int, lookback: int) -> Dict[str, dict]:
    x_all = np.array([r.x for r in rows], dtype=float)
    y_all = np.array([r.y_margin_vs_line for r in rows], dtype=float)
    n = len(rows)
    retrain_every = max(20, n // 20)

    bayes_raw: List[float] = []
    xgb_raw: List[float] = []
    ridge_raw: List[float] = []
    actual: List[bool] = []
    pushes: List[bool] = []

    xgb_model = None
    ridge = None
    scaler = None
    xgb_std = 12.0
    ridge_std = 12.0

    for i in range(min_train, n):
        if (i - min_train) % retrain_every == 0:
            xt = x_all[:i]
            yt = y_all[:i]
            scaler = StandardScaler()
            xts = scaler.fit_transform(xt)

            xgb_model = xgb.XGBRegressor(
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
            xgb_model.fit(xt, yt)
            xgb_std = max(float(np.std(yt - xgb_model.predict(xt))), 1.0)

            ridge = Ridge(alpha=1.0)
            ridge.fit(xts, yt)
            ridge_std = max(float(np.std(yt - ridge.predict(xts))), 1.0)

        r = rows[i]
        actual.append(r.y_home_cover)
        pushes.append(r.is_push)
        bayes_raw.append(r.bayes_p)

        x_row = np.array([r.x], dtype=float)
        xm = float(xgb_model.predict(x_row)[0])
        xgb_raw.append(normal_cdf(xm / xgb_std))

        x_row_s = scaler.transform(x_row)
        rm = float(ridge.predict(x_row_s)[0])
        ridge_raw.append(normal_cdf(rm / ridge_std))

    # Calibration (online, trailing only)
    bayes_cal = online_calibrate(bayes_raw, actual, pushes, lookback)
    xgb_cal = online_calibrate(xgb_raw, actual, pushes, lookback)
    ridge_cal = online_calibrate(ridge_raw, actual, pushes, lookback)

    # Adaptive ensemble with trailing performance weights only
    ens_ad: List[float] = []
    for i in range(len(actual)):
        wb, wx, wr = adaptive_weights(bayes_cal, xgb_cal, ridge_cal, actual, pushes, i, lookback)
        ens_ad.append(wb * bayes_cal[i] + wx * xgb_cal[i] + wr * ridge_cal[i])

    # Equal ensemble (no tuned weights)
    ens_eq = [(b + x + r) / 3.0 for b, x, r in zip(bayes_cal, xgb_cal, ridge_cal)]

    models = {
        "Bayesian(cal)": bayes_cal,
        "XGBoost(cal,independent)": xgb_cal,
        "Ridge(cal,independent)": ridge_cal,
        "EnsembleEqual(cal)": ens_eq,
        "EnsembleAdaptive(cal)": ens_ad,
    }
    results = [evaluate_dynamic(name, preds, actual, pushes, lookback) for name, preds in models.items()]
    results.sort(key=lambda x: x["units"], reverse=True)

    return {
        "n_rows": n,
        "n_test": len(actual),
        "lookback": lookback,
        "min_train": min_train,
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", default=str(HISTORY_PATH))
    ap.add_argument("--min-train", type=int, default=50)
    ap.add_argument("--lookback", type=int, default=120)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    rows = load_clean_rows(Path(args.history))
    out = walk_forward(rows, min_train=args.min_train, lookback=args.lookback)

    OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")

    print(
        f"rows={out['n_rows']} test={out['n_test']} min_train={out['min_train']} lookback={out['lookback']}"
    )
    print("name|record|win%|units|picks|all_side_acc|avg_t")
    for r in out["results"]:
        print(
            f"{r['name']}|{r['wins']}-{r['losses']}-{r['pushes']}|{r['win_pct']:.1f}%|{r['units']:+.1f}u|"
            f"{r['picks']}|{r['all_side_acc']:.1f}%|{r['avg_t']:.3f}"
        )
    print(f"saved={OUT_PATH}")


if __name__ == "__main__":
    main()

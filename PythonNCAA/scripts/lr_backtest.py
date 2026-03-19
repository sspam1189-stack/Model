#!/usr/bin/env python
"""
NCAA LR Confirmation Model Walk-Forward Backtest.

Tests the Logistic Regression confirmation model across multiple
confirm/veto threshold pairs to find optimal ROI.  Uses independent
features from lr_model.py (ATS momentum, O/U trends, line context,
rest, performance momentum, tournament flags) -- NOT Bayesian outputs.

Usage:
  cd PythonNCAA && python scripts/lr_backtest.py
"""

import json
import re
import sys
import warnings
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore", category=FutureWarning, module="sklearn")

from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from lr_model import build_team_histories, extract_lr_features, LR_FEATURE_NAMES

HISTORY = ROOT / "data" / "history.json"

THRESHOLDS = [
    (0.50, 0.50),   # baseline — no filtering
    (0.52, 0.48),
    (0.55, 0.45),
    (0.58, 0.42),
    (0.60, 0.40),
]

MIN_TRAIN = 80
RETRAIN_EVERY = 20
JUICE = 1.10  # -110 juice


# ── Pick parsing / grading ───────────────────────────────────────────

def parse_pick(pick_str):
    if not pick_str or pick_str == "PASS":
        return None
    m = re.match(r"(.+?)\s+([+-])(\d+(?:\.\d+)?)", pick_str)
    if not m:
        return None
    return {"team": m.group(1).strip(), "sign": m.group(2), "pts": float(m.group(3))}


def grade_pick(g, pick):
    chosen_is_home = pick["team"] == g.get("home")
    margin = (g["homeScore"] - g["awayScore"]) if chosen_is_home else (g["awayScore"] - g["homeScore"])
    val = margin + pick["pts"] if pick["sign"] == "+" else margin - pick["pts"]
    if val == 0:
        return None
    return val > 0


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ── Main ─────────────────────────────────────────────────────────────

def main():
    store = json.loads(HISTORY.read_text(encoding="utf-8"))

    # Build team histories incrementally — we need to walk chronologically
    # and only use history BEFORE each game for feature extraction.
    team_histories = {}   # team -> [game_records]
    team_season_lines = {}  # team -> [abs_line values]

    rows = []  # [{features: list, label: int, conf: str, date: str}]

    for run in store.get("runs", []):
        if run.get("burnIn"):
            continue
        date = str(run.get("date", ""))

        for g in run.get("games", []):
            hs = g.get("homeScore")
            aws = g.get("awayScore")
            line = g.get("line")
            total = g.get("total")
            if not isinstance(hs, (int, float)) or not isinstance(aws, (int, float)):
                continue
            if not isinstance(line, (int, float)):
                continue
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue

            home = g.get("home", "")
            away = g.get("away", "")

            # Get CURRENT histories (before this game) for feature extraction
            home_hist = list(team_histories.get(home, []))
            away_hist = list(team_histories.get(away, []))
            h_lines = list(team_season_lines.get(home, []))
            a_lines = list(team_season_lines.get(away, []))

            # Extract features — returns None if < 5 games per team
            features = extract_lr_features(home_hist, away_hist, g, h_lines, a_lines)

            # Check if this was a Bayesian pick that we can grade
            pick = parse_pick(g.get("sPick"))
            label = None
            if pick:
                label = grade_pick(g, pick)

            if features is not None and label is not None:
                rows.append({
                    "features": features,
                    "label": 1 if label else 0,
                    "conf": g.get("sConf", ""),
                    "date": date,
                })

            # NOW update team histories with this game's result
            h_score = _safe_float(hs)
            a_score = _safe_float(aws)
            line_f = _safe_float(line)
            total_f = _safe_float(total, 140.0)
            ats_margin = h_score - a_score - line_f
            actual_total = h_score + a_score
            abs_line = abs(line_f)

            if home:
                team_histories.setdefault(home, []).append({
                    "date": date, "is_home": True,
                    "covered_home": ats_margin > 0, "ats_margin": ats_margin,
                    "actual_total": actual_total, "total_line": total_f,
                    "won": h_score > a_score, "margin": h_score - a_score,
                    "abs_line": abs_line,
                })
                team_season_lines.setdefault(home, []).append(abs_line)

            if away:
                team_histories.setdefault(away, []).append({
                    "date": date, "is_home": False,
                    "covered_home": ats_margin > 0, "ats_margin": ats_margin,
                    "actual_total": actual_total, "total_line": total_f,
                    "won": a_score > h_score, "margin": a_score - h_score,
                    "abs_line": abs_line,
                })
                team_season_lines.setdefault(away, []).append(abs_line)

    print(f"Total rows with independent features + graded picks: {len(rows)}")

    if len(rows) < MIN_TRAIN + 1:
        sys.exit(f"Only {len(rows)} rows, need {MIN_TRAIN + 1}")

    # ── Walk-forward backtest ────────────────────────────────────────
    X = np.array([r["features"] for r in rows], dtype=np.float64)
    y = np.array([r["label"] for r in rows], dtype=np.int32)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    results = {
        t: {"w": 0, "l": 0, "vetoed_w": 0, "vetoed_l": 0,
            "confirmed": 0, "vetoed": 0, "neutral": 0}
        for t in THRESHOLDS
    }
    bayes_only = {"w": 0, "l": 0}

    model = None
    scaler = None
    last_train = -1

    for i in range(MIN_TRAIN, len(rows)):
        if model is None or (i - last_train) >= RETRAIN_EVERY:
            scaler = StandardScaler()
            X_train = scaler.fit_transform(X[:i])
            model = LogisticRegression(
                penalty="l2", C=1.0, class_weight="balanced", max_iter=1000,
            )
            model.fit(X_train, y[:i])
            last_train = i

        x_test = scaler.transform(X[i:i+1])
        lr_prob = float(model.predict_proba(x_test)[0, 1])
        actual = y[i]

        if actual == 1:
            bayes_only["w"] += 1
        else:
            bayes_only["l"] += 1

        for (confirm_t, veto_t) in THRESHOLDS:
            r = results[(confirm_t, veto_t)]
            if lr_prob >= confirm_t:
                r["confirmed"] += 1
                if actual == 1: r["w"] += 1
                else: r["l"] += 1
            elif lr_prob < veto_t:
                r["vetoed"] += 1
                if actual == 1: r["vetoed_w"] += 1
                else: r["vetoed_l"] += 1
            else:
                r["neutral"] += 1
                if actual == 1: r["w"] += 1
                else: r["l"] += 1

    # ── Print results ────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print("NCAA LR Confirmation Model — Walk-Forward Backtest (Independent Features)")
    print(f"{'='*80}")
    print(f"Train min: {MIN_TRAIN} | Retrain every: {RETRAIN_EVERY}")
    print(f"Features ({len(LR_FEATURE_NAMES)}): {', '.join(LR_FEATURE_NAMES)}")

    bw, bl = bayes_only["w"], bayes_only["l"]
    bt = bw + bl
    b_roi = ((bw - bl * JUICE) / bt * 100) if bt else 0
    print(f"\nBayesian Only: {bw}W-{bl}L ({bw/bt*100:.1f}%)  "
          f"ROI: {b_roi:+.1f}%  [{bt} picks]")

    header = (f"{'Confirm/Veto':<14} {'Record':<12} {'Win%':>6} {'ROI':>8} "
              f"{'Picks':>6} {'Vetoed':>7} {'Vetoed W-L':>11} {'Veto Acc':>9}")
    print(f"\n{header}")
    print("-" * len(header))

    for (ct, vt) in THRESHOLDS:
        r = results[(ct, vt)]
        w, l = r["w"], r["l"]
        total = w + l
        pct = w / total * 100 if total else 0
        roi = ((w - l * JUICE) / total * 100) if total else 0
        vetoed = r["vetoed"]
        vw, vl = r["vetoed_w"], r["vetoed_l"]
        vt_total = vw + vl
        veto_acc = vl / vt_total * 100 if vt_total else 0

        tag = " <-- baseline" if ct == 0.50 and vt == 0.50 else ""
        print(f"{ct:.2f} / {vt:.2f}    {w}W-{l}L    "
              f"{pct:5.1f}% {roi:+7.1f}%  {total:5d}  {vetoed:6d}  "
              f"{vw}W-{vl}L     {veto_acc:6.1f}%{tag}")

    if model is not None:
        print(f"\n{'='*80}")
        print("Final model coefficients (positive = confirms cover)")
        print(f"{'='*80}")
        coefs = model.coef_[0]
        order = np.argsort(np.abs(coefs))[::-1]
        for idx in order:
            name = LR_FEATURE_NAMES[idx] if idx < len(LR_FEATURE_NAMES) else f"f{idx}"
            print(f"  {name:<22s}  {coefs[idx]:+.4f}")

    print(f"\nROI uses -110 juice (wins +1.0u, losses -{JUICE}u).")
    print(f"Veto Accuracy = % of vetoed picks that would have LOST (higher = better).")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Analyze which Savant stats add predictive value beyond what we already use."""

import requests
import pandas as pd
import numpy as np
import io
import time
import os

os.environ["PYTHONUTF8"] = "1"

headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# =====================================================================
# FETCH DATA
# =====================================================================

# 1. MLB Stats API season pitching (2025)
print("=== Fetching MLB Stats API season pitching stats (2025) ===")
url_season = (
    "https://statsapi.mlb.com/api/v1/stats?"
    "stats=season&group=pitching&season=2025&sportId=1"
    "&playerPool=all&limit=500"
)
r = requests.get(url_season, headers=headers, timeout=30)
data = r.json()

rows = []
for split in data.get("stats", [{}])[0].get("splits", []):
    stat = split.get("stat", {})
    player = split.get("player", {})
    team = split.get("team", {})
    gs = stat.get("gamesStarted", 0)
    ip_raw = str(stat.get("inningsPitched", "0"))
    if "." in ip_raw:
        parts = ip_raw.split(".")
        ip = int(parts[0]) + int(parts[1]) / 3.0
    else:
        ip = float(ip_raw)

    if gs >= 10 and ip >= 50:
        rows.append({
            "player_id": player.get("id"),
            "name": player.get("fullName", ""),
            "team": team.get("abbreviation", ""),
            "gs": gs, "ip": ip,
            "k": stat.get("strikeOuts", 0),
            "h": stat.get("hits", 0),
            "bb": stat.get("baseOnBalls", 0),
            "er": stat.get("earnedRuns", 0),
            "era": float(stat.get("era", "0") or 0),
            "whip": float(stat.get("whip", "0") or 0),
            "k_per_9": float(stat.get("strikeoutsPer9Inn", "0") or 0),
            "bb_per_9": float(stat.get("walksPer9Inn", "0") or 0),
            "h_per_9": float(stat.get("hitsPer9Inn", "0") or 0),
            "k_per_start": stat.get("strikeOuts", 0) / max(gs, 1),
            "h_per_start": stat.get("hits", 0) / max(gs, 1),
            "bb_per_start": stat.get("baseOnBalls", 0) / max(gs, 1),
            "ip_per_start": ip / max(gs, 1),
        })

df_season = pd.DataFrame(rows)
print(f"  {len(df_season)} qualifying starters (10+ GS, 50+ IP)")

# 2. Savant custom leaderboard
print("\n=== Fetching Baseball Savant Statcast metrics ===")
time.sleep(1)
url_savant = (
    "https://baseballsavant.mlb.com/leaderboard/custom?"
    "year=2025&type=pitcher&filter=&min=100"
    "&selections=whiff_percent,k_percent,bb_percent,"
    "iz_contact_percent,oz_swing_percent,"
    "barrel_batted_rate,hard_hit_percent,f_strike_percent"
    "&chart=false&csv=true"
)
r_sav = requests.get(url_savant, headers=headers, timeout=30)
text = r_sav.text.lstrip("\ufeff")
df_savant = pd.read_csv(io.StringIO(text))
print(f"  {len(df_savant)} pitchers with Statcast data")

# 3. Expected stats
time.sleep(1)
print("\n=== Fetching Savant expected stats ===")
url_exp = (
    "https://baseballsavant.mlb.com/leaderboard/expected_statistics?"
    "type=pitcher&year=2025&position=&team=&min=100&csv=true"
)
r_exp = requests.get(url_exp, headers=headers, timeout=30)
text_exp = r_exp.text.lstrip("\ufeff")
df_exp = pd.read_csv(io.StringIO(text_exp))
print(f"  {len(df_exp)} pitchers with expected stats")

# 4. Sabermetrics
time.sleep(1)
print("\n=== Fetching sabermetrics (FIP/xFIP) ===")
url_saber = (
    "https://statsapi.mlb.com/api/v1/stats?"
    "stats=sabermetrics&group=pitching&season=2025&sportId=1"
    "&playerPool=all&limit=500"
)
r_saber = requests.get(url_saber, headers=headers, timeout=30)
saber_data = r_saber.json()
saber_rows = []
for split in saber_data.get("stats", [{}])[0].get("splits", []):
    stat = split.get("stat", {})
    player = split.get("player", {})
    saber_rows.append({
        "player_id": player.get("id"),
        "fip": float(stat.get("fip", 0) or 0),
        "xfip": float(stat.get("xfip", 0) or 0),
    })
df_saber = pd.DataFrame(saber_rows)
print(f"  {len(df_saber)} pitchers with FIP/xFIP")

# =====================================================================
# MERGE
# =====================================================================

df = df_season.merge(
    df_savant[["player_id", "whiff_percent", "k_percent", "bb_percent",
               "iz_contact_percent", "oz_swing_percent",
               "barrel_batted_rate", "hard_hit_percent", "f_strike_percent"]],
    on="player_id", how="left"
)
df = df.merge(
    df_exp[["player_id", "est_ba", "est_slg", "est_woba", "xera"]],
    on="player_id", how="left"
)
df = df.merge(
    df_saber[["player_id", "fip", "xfip"]],
    on="player_id", how="left"
)

print(f"\n=== Merged: {len(df)} starters ===")
n_sav = df["whiff_percent"].notna().sum()
n_exp = df["est_ba"].notna().sum()
n_sab = df["xfip"].notna().sum()
print(f"  With Savant: {n_sav}, Expected: {n_exp}, Sabermetrics: {n_sab}")

# =====================================================================
# CORRELATION ANALYSIS
# =====================================================================

print(f"\n{'='*70}")
print(f"  CORRELATION ANALYSIS: r with per-start outcomes (2025, 10+ GS)")
print(f"{'='*70}")

targets = {
    "K/start": "k_per_start",
    "H/start": "h_per_start",
    "BB/start": "bb_per_start",
    "IP/start": "ip_per_start",
}

predictors = {
    # Currently used
    "K/9 (season)":       "k_per_9",
    "BB/9 (season)":      "bb_per_9",
    "H/9 (season)":       "h_per_9",
    "WHIP":               "whip",
    "ERA":                "era",
    "xFIP":               "xfip",
    "FIP":                "fip",
    # NEW candidates
    "Whiff%":             "whiff_percent",
    "K% (Savant)":        "k_percent",
    "BB% (Savant)":       "bb_percent",
    "Zone Contact%":      "iz_contact_percent",
    "O-Swing% (chase)":   "oz_swing_percent",
    "Barrel Rate":        "barrel_batted_rate",
    "Hard Hit%":          "hard_hit_percent",
    "F-Strike%":          "f_strike_percent",
    "xBA":                "est_ba",
    "xSLG":               "est_slg",
    "xwOBA":              "est_woba",
    "xERA":               "xera",
}

for target_label, target_col in targets.items():
    print(f"\n  --- {target_label} ---")
    correlations = []
    for pred_label, pred_col in predictors.items():
        if pred_col not in df.columns:
            continue
        valid = df[[target_col, pred_col]].dropna()
        if len(valid) < 20:
            continue
        corr = valid[target_col].corr(valid[pred_col])
        correlations.append((pred_label, corr, len(valid)))

    correlations.sort(key=lambda x: abs(x[1]), reverse=True)
    for label, corr, n in correlations:
        marker = ("***" if abs(corr) >= 0.70 else
                  "** " if abs(corr) >= 0.50 else
                  "*  " if abs(corr) >= 0.30 else
                  "   ")
        print(f"    {marker} r={corr:+.3f}  {label:22s} (n={n})")

# =====================================================================
# INCREMENTAL R^2 ANALYSIS
# =====================================================================

print(f"\n{'='*70}")
print(f"  INCREMENTAL R-SQUARED: Does adding Savant stats help?")
print(f"{'='*70}")

from numpy.linalg import lstsq


def compute_r2(X, y):
    b, _, _, _ = lstsq(X, y, rcond=None)
    y_pred = X @ b
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    return 1 - ss_res / ss_tot if ss_tot > 0 else 0.0


# K/start models
cols_k = ["k_per_start", "k_per_9", "xfip", "whiff_percent",
          "f_strike_percent", "iz_contact_percent"]
valid_k = df[cols_k].dropna()
if len(valid_k) >= 30:
    y = valid_k["k_per_start"].values
    ones = np.ones(len(valid_k))

    r2_1 = compute_r2(np.column_stack([valid_k["k_per_9"], ones]), y)
    r2_2 = compute_r2(np.column_stack([valid_k["k_per_9"], valid_k["xfip"], ones]), y)
    r2_3 = compute_r2(np.column_stack([valid_k["k_per_9"], valid_k["xfip"],
                                        valid_k["whiff_percent"], ones]), y)
    r2_4 = compute_r2(np.column_stack([valid_k["k_per_9"], valid_k["xfip"],
                                        valid_k["whiff_percent"],
                                        valid_k["f_strike_percent"],
                                        valid_k["iz_contact_percent"], ones]), y)

    print(f"\n  K/start (n={len(valid_k)}):")
    print(f"    K/9 only:                           R2 = {r2_1:.4f}")
    print(f"    + xFIP:                             R2 = {r2_2:.4f}  (delta +{r2_2-r2_1:.4f})")
    print(f"    + Whiff%:                           R2 = {r2_3:.4f}  (delta +{r2_3-r2_2:.4f})")
    print(f"    + F-Strike% + Zone Contact%:        R2 = {r2_4:.4f}  (delta +{r2_4-r2_3:.4f})")

# H/start models
cols_h = ["h_per_start", "h_per_9", "est_ba", "hard_hit_percent", "barrel_batted_rate"]
valid_h = df[cols_h].dropna()
if len(valid_h) >= 30:
    y = valid_h["h_per_start"].values
    ones = np.ones(len(valid_h))

    r2_1 = compute_r2(np.column_stack([valid_h["h_per_9"], ones]), y)
    r2_2 = compute_r2(np.column_stack([valid_h["h_per_9"], valid_h["est_ba"], ones]), y)
    r2_3 = compute_r2(np.column_stack([valid_h["h_per_9"], valid_h["est_ba"],
                                        valid_h["hard_hit_percent"],
                                        valid_h["barrel_batted_rate"], ones]), y)

    print(f"\n  H/start (n={len(valid_h)}):")
    print(f"    H/9 only:                           R2 = {r2_1:.4f}")
    print(f"    + xBA:                              R2 = {r2_2:.4f}  (delta +{r2_2-r2_1:.4f})")
    print(f"    + Hard Hit% + Barrel%:              R2 = {r2_3:.4f}  (delta +{r2_3-r2_2:.4f})")

# BB/start models
cols_bb = ["bb_per_start", "bb_per_9", "f_strike_percent", "oz_swing_percent"]
valid_bb = df[cols_bb].dropna()
if len(valid_bb) >= 30:
    y = valid_bb["bb_per_start"].values
    ones = np.ones(len(valid_bb))

    r2_1 = compute_r2(np.column_stack([valid_bb["bb_per_9"], ones]), y)
    r2_2 = compute_r2(np.column_stack([valid_bb["bb_per_9"],
                                        valid_bb["f_strike_percent"],
                                        valid_bb["oz_swing_percent"], ones]), y)

    print(f"\n  BB/start (n={len(valid_bb)}):")
    print(f"    BB/9 only:                          R2 = {r2_1:.4f}")
    print(f"    + F-Strike% + O-Swing%:             R2 = {r2_2:.4f}  (delta +{r2_2-r2_1:.4f})")

# IP/start models
cols_ip = ["ip_per_start", "era", "xera", "hard_hit_percent", "barrel_batted_rate"]
valid_ip = df[cols_ip].dropna()
if len(valid_ip) >= 30:
    y = valid_ip["ip_per_start"].values
    ones = np.ones(len(valid_ip))

    r2_1 = compute_r2(np.column_stack([valid_ip["era"], ones]), y)
    r2_2 = compute_r2(np.column_stack([valid_ip["xera"], ones]), y)
    r2_3 = compute_r2(np.column_stack([valid_ip["xera"],
                                        valid_ip["hard_hit_percent"],
                                        valid_ip["barrel_batted_rate"], ones]), y)

    print(f"\n  IP/start (n={len(valid_ip)}):")
    print(f"    ERA only:                           R2 = {r2_1:.4f}")
    print(f"    xERA only:                          R2 = {r2_2:.4f}")
    print(f"    xERA + Hard Hit% + Barrel%:         R2 = {r2_3:.4f}  (delta +{r2_3-r2_2:.4f})")

# =====================================================================
# VERDICT
# =====================================================================

print(f"\n{'='*70}")
print(f"  VERDICT: Which stats to add")
print(f"{'='*70}")
print("""
  Legend: *** r>=0.70  ** r>=0.50  * r>=0.30

  SIGNAL (add these):
    - Whiff%         -> K market anchor (process-level K predictor)
    - xBA            -> H market anchor (luck-adjusted hit rate)
    - xERA           -> IP/outs projection (better than ERA)
    - F-Strike%      -> BB market + secondary K signal

  MAYBE (small incremental value):
    - Zone Contact%  -> secondary K signal (low = nasty stuff)
    - Hard Hit%      -> IP/outs projection (hard contact = short outings)
    - O-Swing%       -> BB market (high chase = fewer walks)

  NOISE (skip):
    - Barrel Rate    -> too correlated with Hard Hit% (redundant)
    - xSLG           -> subsumed by xBA + xERA
    - xwOBA          -> composite of xBA/xSLG, no incremental info
    - K% (Savant)    -> just K/PA, same info as K/9 (redundant)
    - BB% (Savant)   -> just BB/PA, same info as BB/9 (redundant)
""")

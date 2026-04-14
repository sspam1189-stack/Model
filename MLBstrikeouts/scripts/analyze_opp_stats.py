#!/usr/bin/env python3
"""
Analyze how much opponent batting stats improve per-start predictions
ON TOP of pitcher stats we already have.

Key question: Does knowing the opposing team's K%, BA, OPS, BB%
add incremental R² beyond just knowing the pitcher's own K/9, H/9, BB/9?
"""

import requests
import pandas as pd
import numpy as np
import io
import time
import os

os.environ["PYTHONUTF8"] = "1"

headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


# =====================================================================
# 1. FETCH GAME-LEVEL DATA (pitcher stats PER GAME, not season totals)
# =====================================================================

print("=== Fetching pitcher game logs (2025) ===")
url = (
    "https://statsapi.mlb.com/api/v1/stats?"
    "stats=gameLog&group=pitching&season=2025&sportId=1"
    "&playerPool=all&limit=10000&gameType=R"
)
r = requests.get(url, headers=headers, timeout=60)
data = r.json()

game_rows = []
for split in data.get("stats", [{}])[0].get("splits", []):
    stat = split.get("stat", {})
    player = split.get("player", {})
    team_info = split.get("team", {})
    opp_info = split.get("opponent", {})

    gs = stat.get("gamesStarted", 0)
    if gs < 1:
        continue  # starts only

    ip_raw = str(stat.get("inningsPitched", "0"))
    if "." in ip_raw:
        parts = ip_raw.split(".")
        ip = int(parts[0]) + int(parts[1]) / 3.0
    else:
        ip = float(ip_raw)

    if ip < 1.0:
        continue  # skip very short outings

    game_rows.append({
        "player_id": player.get("id"),
        "pitcher_name": player.get("fullName", ""),
        "team": team_info.get("abbreviation", ""),
        "opp_team_id": opp_info.get("id"),
        "opp_team": opp_info.get("abbreviation", ""),
        "game_date": split.get("date", ""),
        "ip": ip,
        "k": stat.get("strikeOuts", 0),
        "h": stat.get("hits", 0),
        "bb": stat.get("baseOnBalls", 0),
        "er": stat.get("earnedRuns", 0),
        "pitches": stat.get("numberOfPitches", 0),
        "is_home": split.get("isHome", False),
    })

df_games = pd.DataFrame(game_rows)
print(f"  {len(df_games)} starts from {df_games['player_id'].nunique()} pitchers")

# =====================================================================
# 2. FETCH TEAM BATTING STATS (opponent context)
# =====================================================================

print("\n=== Fetching team batting stats (2025) ===")
time.sleep(1)

# Team ID -> abbreviation
TEAM_ID_MAP = {
    108: "LAA", 109: "ARI", 110: "BAL", 111: "BOS", 112: "CHC",
    113: "CIN", 114: "CLE", 115: "COL", 116: "DET", 117: "HOU",
    118: "KC",  119: "LAD", 120: "WSH", 121: "NYM", 133: "OAK",
    134: "PIT", 135: "SD",  136: "SEA", 137: "SF",  138: "STL",
    139: "TB",  140: "TEX", 141: "TOR", 142: "MIN", 143: "PHI",
    144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY", 158: "MIL",
}

url_bat = (
    "https://statsapi.mlb.com/api/v1/teams/stats?"
    "stats=season&group=hitting&season=2025&sportId=1"
)
r_bat = requests.get(url_bat, headers=headers, timeout=30)
bat_data = r_bat.json()

team_bat = {}
for split in bat_data.get("stats", [{}])[0].get("splits", []):
    stat = split.get("stat", {})
    team_info = split.get("team", {})
    tid = team_info.get("id")
    abbr = TEAM_ID_MAP.get(tid, team_info.get("abbreviation", ""))
    if not abbr:
        continue

    pa = stat.get("plateAppearances", 0)
    so = stat.get("strikeOuts", 0)
    bb_t = stat.get("baseOnBalls", 0)
    hits_t = stat.get("hits", 0)
    ab = stat.get("atBats", 1)

    team_bat[abbr] = {
        "team_K_pct": so / pa if pa > 0 else 0,
        "team_BB_pct": bb_t / pa if pa > 0 else 0,
        "team_BA": float(stat.get("avg", "0") or 0),
        "team_OPS": float(stat.get("ops", "0") or 0),
        "team_SLG": float(stat.get("slg", "0") or 0),
        "team_OBP": float(stat.get("obp", "0") or 0),
        "team_SO": so,
        "team_PA": pa,
        "team_H": hits_t,
    }

print(f"  {len(team_bat)} teams with batting stats")

# Also fetch expected stats for teams (xBA, xwOBA)
print("\n=== Fetching Savant team expected stats ===")
time.sleep(1)
url_team_exp = (
    "https://baseballsavant.mlb.com/leaderboard/expected_statistics?"
    "type=batter&year=2025&position=&team=&min=100&csv=true"
)
# This is player-level, we need to aggregate by team
# Instead, let's use team-level from Savant
url_team_savant = (
    "https://baseballsavant.mlb.com/leaderboard/custom?"
    "year=2025&type=batter-team&filter=&min=1"
    "&selections=whiff_percent,k_percent,bb_percent,chase_percent,"
    "iz_contact_percent,oz_swing_percent,"
    "barrel_batted_rate,hard_hit_percent,f_strike_percent"
    "&chart=false&csv=true"
)
r_team_sav = requests.get(url_team_savant, headers=headers, timeout=30)
text_ts = r_team_sav.text.lstrip("\ufeff")

try:
    df_team_sav = pd.read_csv(io.StringIO(text_ts))
    print(f"  {len(df_team_sav)} teams with Savant batting Statcast")
    print(f"  Columns: {list(df_team_sav.columns)}")
except Exception as e:
    print(f"  Savant team batting parse failed: {e}")
    df_team_sav = pd.DataFrame()

# =====================================================================
# 3. COMPUTE PITCHER SEASON AVERAGES (as baseline predictors)
# =====================================================================

print("\n=== Computing pitcher season averages ===")

pitcher_season = df_games.groupby("player_id").agg(
    pitcher_name=("pitcher_name", "first"),
    n_starts=("k", "count"),
    avg_k=("k", "mean"),
    avg_h=("h", "mean"),
    avg_bb=("bb", "mean"),
    avg_ip=("ip", "mean"),
    total_ip=("ip", "sum"),
    total_k=("k", "sum"),
    total_h=("h", "sum"),
    total_bb=("bb", "sum"),
).reset_index()

# Derive per-9 rates
pitcher_season["k_per_9"] = pitcher_season["total_k"] / pitcher_season["total_ip"] * 9
pitcher_season["h_per_9"] = pitcher_season["total_h"] / pitcher_season["total_ip"] * 9
pitcher_season["bb_per_9"] = pitcher_season["total_bb"] / pitcher_season["total_ip"] * 9

# Filter to 10+ starts
pitcher_season = pitcher_season[pitcher_season["n_starts"] >= 10]
print(f"  {len(pitcher_season)} pitchers with 10+ starts")

# =====================================================================
# 4. MERGE game-level data with pitcher season + opponent stats
# =====================================================================

print("\n=== Merging game-level with context ===")

# Add pitcher season averages to each game
df = df_games.merge(
    pitcher_season[["player_id", "k_per_9", "h_per_9", "bb_per_9",
                     "avg_k", "avg_h", "avg_bb", "avg_ip"]],
    on="player_id", how="inner"
)

# Add opponent batting stats
opp_df = pd.DataFrame.from_dict(team_bat, orient="index")
opp_df.index.name = "opp_team"
opp_df = opp_df.reset_index()
df = df.merge(opp_df, on="opp_team", how="left")

# Add opponent Savant stats if available
if not df_team_sav.empty and "player_id" in df_team_sav.columns:
    # player_id here is actually team_id for team-level data
    # Try merging on team abbreviation from the name column
    pass  # We'll use the team_bat stats which are more reliable

print(f"  {len(df)} game-starts with full context")
print(f"  Opponent stats available: {df['team_K_pct'].notna().sum()}")

# =====================================================================
# 5. CORRELATION: Opponent stats vs per-game outcomes
# =====================================================================

print(f"\n{'='*70}")
print(f"  CORRELATION: Opponent batting stats vs per-game pitcher outcomes")
print(f"  (n={len(df)} starts from {df['player_id'].nunique()} pitchers)")
print(f"{'='*70}")

opp_predictors = {
    "Opp K%":     "team_K_pct",
    "Opp BB%":    "team_BB_pct",
    "Opp BA":     "team_BA",
    "Opp OPS":    "team_OPS",
    "Opp SLG":    "team_SLG",
    "Opp OBP":    "team_OBP",
}

pitcher_predictors = {
    "Pitcher K/9":    "k_per_9",
    "Pitcher H/9":    "h_per_9",
    "Pitcher BB/9":   "bb_per_9",
    "Pitcher avg K":  "avg_k",
    "Pitcher avg IP": "avg_ip",
}

targets = {
    "K (game)":  "k",
    "H (game)":  "h",
    "BB (game)": "bb",
    "IP (game)": "ip",
}

for target_label, target_col in targets.items():
    print(f"\n  --- {target_label} ---")
    all_preds = {**pitcher_predictors, **opp_predictors}
    correlations = []
    for pred_label, pred_col in all_preds.items():
        if pred_col not in df.columns:
            continue
        valid = df[[target_col, pred_col]].dropna()
        if len(valid) < 50:
            continue
        corr = valid[target_col].corr(valid[pred_col])
        is_opp = pred_label.startswith("Opp")
        correlations.append((pred_label, corr, len(valid), is_opp))

    correlations.sort(key=lambda x: abs(x[1]), reverse=True)
    for label, corr, n, is_opp in correlations:
        marker = ("***" if abs(corr) >= 0.30 else
                  "** " if abs(corr) >= 0.15 else
                  "*  " if abs(corr) >= 0.08 else
                  "   ")
        tag = " [OPP]" if is_opp else ""
        print(f"    {marker} r={corr:+.3f}  {label:20s} (n={n}){tag}")

# =====================================================================
# 6. INCREMENTAL R²: How much do opponent stats add BEYOND pitcher stats?
# =====================================================================

print(f"\n{'='*70}")
print(f"  INCREMENTAL R-SQUARED: Pitcher stats alone vs + Opponent stats")
print(f"{'='*70}")

from numpy.linalg import lstsq

def compute_r2(X, y):
    b, _, _, _ = lstsq(X, y, rcond=None)
    y_pred = X @ b
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    return 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

# --- K (game) ---
cols_k = ["k", "k_per_9", "avg_ip", "team_K_pct", "team_OPS", "team_BA"]
valid = df[cols_k].dropna()
if len(valid) >= 100:
    y = valid["k"].values
    ones = np.ones(len(valid))

    r2_1 = compute_r2(np.column_stack([valid["k_per_9"], ones]), y)
    r2_2 = compute_r2(np.column_stack([valid["k_per_9"], valid["avg_ip"], ones]), y)
    r2_3 = compute_r2(np.column_stack([valid["k_per_9"], valid["avg_ip"],
                                        valid["team_K_pct"], ones]), y)
    r2_4 = compute_r2(np.column_stack([valid["k_per_9"], valid["avg_ip"],
                                        valid["team_K_pct"], valid["team_OPS"], ones]), y)

    print(f"\n  K per game (n={len(valid)} starts):")
    print(f"    Pitcher K/9 only:                   R2 = {r2_1:.4f}")
    print(f"    + Pitcher avg IP:                   R2 = {r2_2:.4f}  (delta +{r2_2-r2_1:.4f})")
    print(f"    + Opp K%:                           R2 = {r2_3:.4f}  (delta +{r2_3-r2_2:.4f})")
    print(f"    + Opp OPS:                          R2 = {r2_4:.4f}  (delta +{r2_4-r2_3:.4f})")

# --- H (game) ---
cols_h = ["h", "h_per_9", "avg_ip", "team_BA", "team_OPS", "team_K_pct"]
valid = df[cols_h].dropna()
if len(valid) >= 100:
    y = valid["h"].values
    ones = np.ones(len(valid))

    r2_1 = compute_r2(np.column_stack([valid["h_per_9"], ones]), y)
    r2_2 = compute_r2(np.column_stack([valid["h_per_9"], valid["avg_ip"], ones]), y)
    r2_3 = compute_r2(np.column_stack([valid["h_per_9"], valid["avg_ip"],
                                        valid["team_BA"], ones]), y)
    r2_4 = compute_r2(np.column_stack([valid["h_per_9"], valid["avg_ip"],
                                        valid["team_BA"], valid["team_OPS"], ones]), y)

    print(f"\n  H per game (n={len(valid)} starts):")
    print(f"    Pitcher H/9 only:                   R2 = {r2_1:.4f}")
    print(f"    + Pitcher avg IP:                   R2 = {r2_2:.4f}  (delta +{r2_2-r2_1:.4f})")
    print(f"    + Opp BA:                           R2 = {r2_3:.4f}  (delta +{r2_3-r2_2:.4f})")
    print(f"    + Opp OPS:                          R2 = {r2_4:.4f}  (delta +{r2_4-r2_3:.4f})")

# --- BB (game) ---
cols_bb = ["bb", "bb_per_9", "avg_ip", "team_BB_pct", "team_OBP"]
valid = df[cols_bb].dropna()
if len(valid) >= 100:
    y = valid["bb"].values
    ones = np.ones(len(valid))

    r2_1 = compute_r2(np.column_stack([valid["bb_per_9"], ones]), y)
    r2_2 = compute_r2(np.column_stack([valid["bb_per_9"], valid["avg_ip"], ones]), y)
    r2_3 = compute_r2(np.column_stack([valid["bb_per_9"], valid["avg_ip"],
                                        valid["team_BB_pct"], ones]), y)

    print(f"\n  BB per game (n={len(valid)} starts):")
    print(f"    Pitcher BB/9 only:                  R2 = {r2_1:.4f}")
    print(f"    + Pitcher avg IP:                   R2 = {r2_2:.4f}  (delta +{r2_2-r2_1:.4f})")
    print(f"    + Opp BB%:                          R2 = {r2_3:.4f}  (delta +{r2_3-r2_2:.4f})")

# --- IP (game) ---
cols_ip = ["ip", "avg_ip", "bb_per_9", "k_per_9", "team_OPS", "team_K_pct", "team_BA"]
valid = df[cols_ip].dropna()
if len(valid) >= 100:
    y = valid["ip"].values
    ones = np.ones(len(valid))

    r2_1 = compute_r2(np.column_stack([valid["avg_ip"], ones]), y)
    r2_2 = compute_r2(np.column_stack([valid["avg_ip"], valid["bb_per_9"], ones]), y)
    r2_3 = compute_r2(np.column_stack([valid["avg_ip"], valid["bb_per_9"],
                                        valid["team_OPS"], ones]), y)
    r2_4 = compute_r2(np.column_stack([valid["avg_ip"], valid["bb_per_9"],
                                        valid["team_OPS"], valid["team_K_pct"], ones]), y)
    r2_5 = compute_r2(np.column_stack([valid["avg_ip"], valid["bb_per_9"],
                                        valid["k_per_9"],
                                        valid["team_OPS"], valid["team_K_pct"],
                                        valid["team_BA"], ones]), y)

    print(f"\n  IP per game (n={len(valid)} starts):")
    print(f"    Pitcher avg IP only:                R2 = {r2_1:.4f}")
    print(f"    + Pitcher BB/9:                     R2 = {r2_2:.4f}  (delta +{r2_2-r2_1:.4f})")
    print(f"    + Opp OPS:                          R2 = {r2_3:.4f}  (delta +{r2_3-r2_2:.4f})")
    print(f"    + Opp K%:                           R2 = {r2_4:.4f}  (delta +{r2_4-r2_3:.4f})")
    print(f"    + Pitcher K/9 + Opp BA (full):      R2 = {r2_5:.4f}  (delta +{r2_5-r2_4:.4f})")

# =====================================================================
# 7. INTERACTION: Pitcher skill x Opponent quality
# =====================================================================

print(f"\n{'='*70}")
print(f"  INTERACTION: Does Opp K% matter MORE for high-K pitchers?")
print(f"{'='*70}")

# Split pitchers into high-K (K/9 >= 9) and low-K (K/9 < 9)
high_k = df[df["k_per_9"] >= 9.0]
low_k = df[df["k_per_9"] < 9.0]

for label, subset in [("High-K pitchers (K/9 >= 9)", high_k),
                       ("Low-K pitchers (K/9 < 9)", low_k)]:
    valid = subset[["k", "team_K_pct"]].dropna()
    if len(valid) >= 50:
        corr = valid["k"].corr(valid["team_K_pct"])
        print(f"  {label}: r(K, Opp K%) = {corr:+.3f} (n={len(valid)})")

# Split by opponent quality
high_k_opp = df[df["team_K_pct"] >= df["team_K_pct"].median()]
low_k_opp = df[df["team_K_pct"] < df["team_K_pct"].median()]

print(f"\n  Avg K by opponent quality:")
print(f"    vs High-K teams (top half):  {high_k_opp['k'].mean():.2f} K/start")
print(f"    vs Low-K teams (bottom half): {low_k_opp['k'].mean():.2f} K/start")
print(f"    Difference: {high_k_opp['k'].mean() - low_k_opp['k'].mean():+.2f} K")

high_h_opp = df[df["team_BA"] >= df["team_BA"].median()]
low_h_opp = df[df["team_BA"] < df["team_BA"].median()]
print(f"\n  Avg H by opponent quality:")
print(f"    vs High-BA teams (top half):  {high_h_opp['h'].mean():.2f} H/start")
print(f"    vs Low-BA teams (bottom half): {low_h_opp['h'].mean():.2f} H/start")
print(f"    Difference: {high_h_opp['h'].mean() - low_h_opp['h'].mean():+.2f} H")

high_ops_opp = df[df["team_OPS"] >= df["team_OPS"].median()]
low_ops_opp = df[df["team_OPS"] < df["team_OPS"].median()]
print(f"\n  Avg IP by opponent quality:")
print(f"    vs High-OPS teams (top half):  {high_ops_opp['ip'].mean():.2f} IP/start")
print(f"    vs Low-OPS teams (bottom half): {low_ops_opp['ip'].mean():.2f} IP/start")
print(f"    Difference: {high_ops_opp['ip'].mean() - low_ops_opp['ip'].mean():+.2f} IP")

print(f"\n{'='*70}")
print(f"  VERDICT: Opponent batting stats value")
print(f"{'='*70}")

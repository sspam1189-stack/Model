#!/usr/bin/env python3
"""Analyze opponent batting stats impact on per-game pitcher outcomes (2024 full season)."""

import requests, json, time, os
import pandas as pd
import numpy as np
from numpy.linalg import lstsq

os.environ["PYTHONUTF8"] = "1"
headers = {"User-Agent": "Mozilla/5.0"}

TEAM_ID_MAP = {
    108: "LAA", 109: "ARI", 110: "BAL", 111: "BOS", 112: "CHC",
    113: "CIN", 114: "CLE", 115: "COL", 116: "DET", 117: "HOU",
    118: "KC",  119: "LAD", 120: "WSH", 121: "NYM", 133: "OAK",
    134: "PIT", 135: "SD",  136: "SEA", 137: "SF",  138: "STL",
    139: "TB",  140: "TEX", 141: "TOR", 142: "MIN", 143: "PHI",
    144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY", 158: "MIL",
}


def r2(X, y):
    b, _, _, _ = lstsq(X, y, rcond=None)
    return 1 - np.sum((y - X @ b) ** 2) / np.sum((y - y.mean()) ** 2)


# =====================================================================
# 1. Get qualifying starters
# =====================================================================
print("Fetching 2024 season pitching stats...")
r = requests.get(
    "https://statsapi.mlb.com/api/v1/stats?stats=season&group=pitching"
    "&season=2024&sportId=1&playerPool=all&limit=500", timeout=30)
starters = []
for split in r.json().get("stats", [{}])[0].get("splits", []):
    stat = split.get("stat", {})
    player = split.get("player", {})
    gs = stat.get("gamesStarted", 0)
    if gs >= 15:
        starters.append({"pid": player["id"], "name": player.get("fullName", ""), "gs": gs})
starters.sort(key=lambda x: -x["gs"])
starters = starters[:80]
print(f"  {len(starters)} starters (top 80 by GS)")

# =====================================================================
# 2. Fetch game logs for each pitcher
# =====================================================================
print("Fetching game logs (this takes ~30s)...")
all_games = []
for i, s in enumerate(starters):
    try:
        r = requests.get(
            f"https://statsapi.mlb.com/api/v1/people/{s['pid']}/stats?"
            f"stats=gameLog&group=pitching&season=2024&gameType=R", timeout=15)
        stats_list = r.json().get("stats", [])
        splits = stats_list[0].get("splits", []) if stats_list else []
    except Exception:
        splits = []
    for sp in splits:
        stat = sp.get("stat", {})
        opp = sp.get("opponent", {})
        if stat.get("gamesStarted", 0) < 1:
            continue
        ip_raw = str(stat.get("inningsPitched", "0"))
        if "." in ip_raw:
            parts = ip_raw.split(".")
            ip = int(parts[0]) + int(parts[1]) / 3.0
        else:
            ip = float(ip_raw)
        if ip < 1.0:
            continue
        all_games.append({
            "pid": s["pid"], "name": s["name"],
            "opp": TEAM_ID_MAP.get(opp.get("id"), ""),
            "k": stat.get("strikeOuts", 0),
            "h": stat.get("hits", 0),
            "bb": stat.get("baseOnBalls", 0),
            "ip": ip,
            "date": sp.get("date", ""),
        })
    if (i + 1) % 20 == 0:
        print(f"  {i+1}/{len(starters)} pitchers ({len(all_games)} games)")
    time.sleep(0.3)

print(f"  Total: {len(all_games)} starts")

# =====================================================================
# 3. Team batting stats
# =====================================================================
print("Fetching 2024 team batting stats...")
r_bat = requests.get(
    "https://statsapi.mlb.com/api/v1/teams/stats?"
    "stats=season&group=hitting&season=2024&sportId=1", timeout=30)
team_bat = {}
for split in r_bat.json().get("stats", [{}])[0].get("splits", []):
    stat = split.get("stat", {})
    tid = split.get("team", {}).get("id")
    abbr = TEAM_ID_MAP.get(tid, "")
    if not abbr:
        continue
    pa = max(stat.get("plateAppearances", 1), 1)
    team_bat[abbr] = {
        "team_K_pct": stat.get("strikeOuts", 0) / pa,
        "team_BB_pct": stat.get("baseOnBalls", 0) / pa,
        "team_BA": float(stat.get("avg", "0") or 0),
        "team_OPS": float(stat.get("ops", "0") or 0),
        "team_SLG": float(stat.get("slg", "0") or 0),
    }
print(f"  {len(team_bat)} teams")

# =====================================================================
# 4. Compute pitcher season avgs & merge
# =====================================================================
df = pd.DataFrame(all_games)
pavg = df.groupby("pid").agg(
    avg_k=("k", "mean"), avg_h=("h", "mean"), avg_bb=("bb", "mean"),
    avg_ip=("ip", "mean"), total_ip=("ip", "sum"), total_k=("k", "sum"),
    total_h=("h", "sum"), total_bb=("bb", "sum"), n=("k", "count"),
).reset_index()
pavg["k_per_9"] = pavg["total_k"] / pavg["total_ip"] * 9
pavg["h_per_9"] = pavg["total_h"] / pavg["total_ip"] * 9
pavg["bb_per_9"] = pavg["total_bb"] / pavg["total_ip"] * 9

df = df.merge(pavg[["pid", "k_per_9", "h_per_9", "bb_per_9", "avg_ip", "avg_k"]], on="pid")
opp_df = pd.DataFrame.from_dict(team_bat, orient="index").reset_index().rename(columns={"index": "opp"})
df = df.merge(opp_df, on="opp", how="left")

print(f"\n{len(df)} starts with full pitcher + opponent context")

# =====================================================================
# CORRELATIONS
# =====================================================================
print(f"\n{'='*70}")
print(f"  CORRELATIONS: Game-level outcomes (2024, n={len(df)} starts)")
print(f"{'='*70}")

targets = {"K": "k", "H": "h", "BB": "bb", "IP": "ip"}
preds = {
    "Pitcher K/9": "k_per_9", "Pitcher H/9": "h_per_9",
    "Pitcher BB/9": "bb_per_9", "Pitcher avg IP": "avg_ip",
    "Opp K%": "team_K_pct", "Opp BB%": "team_BB_pct",
    "Opp BA": "team_BA", "Opp OPS": "team_OPS", "Opp SLG": "team_SLG",
}

for tl, tc in targets.items():
    print(f"\n  --- {tl} per game ---")
    corrs = []
    for pl, pc in preds.items():
        if pc not in df.columns:
            continue
        v = df[[tc, pc]].dropna()
        if len(v) < 50:
            continue
        corrs.append((pl, v[tc].corr(v[pc]), pl.startswith("Opp")))
    corrs.sort(key=lambda x: abs(x[1]), reverse=True)
    for label, corr, is_opp in corrs:
        m = "***" if abs(corr) >= 0.30 else "** " if abs(corr) >= 0.15 else "*  " if abs(corr) >= 0.08 else "   "
        tag = " [OPP]" if is_opp else ""
        print(f"    {m} r={corr:+.3f}  {label}{tag}")

# =====================================================================
# INCREMENTAL R^2
# =====================================================================
print(f"\n{'='*70}")
print(f"  INCREMENTAL R-SQUARED: Pitcher alone vs + Opponent batting")
print(f"{'='*70}")

# K per game
v = df[["k", "k_per_9", "avg_ip", "team_K_pct", "team_OPS"]].dropna()
y = v["k"].values
o = np.ones(len(v))
r2_1 = r2(np.c_[v["k_per_9"], o], y)
r2_2 = r2(np.c_[v["k_per_9"], v["avg_ip"], o], y)
r2_3 = r2(np.c_[v["k_per_9"], v["avg_ip"], v["team_K_pct"], o], y)
r2_4 = r2(np.c_[v["k_per_9"], v["avg_ip"], v["team_K_pct"], v["team_OPS"], o], y)
print(f"\n  K per game (n={len(v)}):")
print(f"    Pitcher K/9:           R2={r2_1:.4f}")
print(f"    + avg IP:              R2={r2_2:.4f}  (+{r2_2-r2_1:.4f})")
print(f"    + Opp K%:              R2={r2_3:.4f}  (+{r2_3-r2_2:.4f})")
print(f"    + Opp OPS:             R2={r2_4:.4f}  (+{r2_4-r2_3:.4f})")

# H per game
v = df[["h", "h_per_9", "avg_ip", "team_BA", "team_OPS"]].dropna()
y = v["h"].values
o = np.ones(len(v))
r2_1 = r2(np.c_[v["h_per_9"], o], y)
r2_2 = r2(np.c_[v["h_per_9"], v["avg_ip"], o], y)
r2_3 = r2(np.c_[v["h_per_9"], v["avg_ip"], v["team_BA"], o], y)
r2_4 = r2(np.c_[v["h_per_9"], v["avg_ip"], v["team_BA"], v["team_OPS"], o], y)
print(f"\n  H per game (n={len(v)}):")
print(f"    Pitcher H/9:           R2={r2_1:.4f}")
print(f"    + avg IP:              R2={r2_2:.4f}  (+{r2_2-r2_1:.4f})")
print(f"    + Opp BA:              R2={r2_3:.4f}  (+{r2_3-r2_2:.4f})")
print(f"    + Opp OPS:             R2={r2_4:.4f}  (+{r2_4-r2_3:.4f})")

# BB per game
v = df[["bb", "bb_per_9", "avg_ip", "team_BB_pct"]].dropna()
y = v["bb"].values
o = np.ones(len(v))
r2_1 = r2(np.c_[v["bb_per_9"], o], y)
r2_2 = r2(np.c_[v["bb_per_9"], v["avg_ip"], o], y)
r2_3 = r2(np.c_[v["bb_per_9"], v["avg_ip"], v["team_BB_pct"], o], y)
print(f"\n  BB per game (n={len(v)}):")
print(f"    Pitcher BB/9:          R2={r2_1:.4f}")
print(f"    + avg IP:              R2={r2_2:.4f}  (+{r2_2-r2_1:.4f})")
print(f"    + Opp BB%:             R2={r2_3:.4f}  (+{r2_3-r2_2:.4f})")

# IP per game
v = df[["ip", "avg_ip", "bb_per_9", "k_per_9", "team_OPS", "team_K_pct", "team_BA"]].dropna()
y = v["ip"].values
o = np.ones(len(v))
r2_1 = r2(np.c_[v["avg_ip"], o], y)
r2_2 = r2(np.c_[v["avg_ip"], v["bb_per_9"], o], y)
r2_3 = r2(np.c_[v["avg_ip"], v["bb_per_9"], v["team_OPS"], o], y)
r2_4 = r2(np.c_[v["avg_ip"], v["bb_per_9"], v["team_OPS"], v["team_K_pct"], o], y)
r2_5 = r2(np.c_[v["avg_ip"], v["bb_per_9"], v["k_per_9"], v["team_OPS"], v["team_K_pct"], v["team_BA"], o], y)
print(f"\n  IP per game (n={len(v)}):")
print(f"    Pitcher avg IP:        R2={r2_1:.4f}")
print(f"    + BB/9:                R2={r2_2:.4f}  (+{r2_2-r2_1:.4f})")
print(f"    + Opp OPS:             R2={r2_3:.4f}  (+{r2_3-r2_2:.4f})")
print(f"    + Opp K%:              R2={r2_4:.4f}  (+{r2_4-r2_3:.4f})")
print(f"    + K/9 + Opp BA (full): R2={r2_5:.4f}  (+{r2_5-r2_4:.4f})")

# =====================================================================
# PRACTICAL SPLITS
# =====================================================================
print(f"\n{'='*70}")
print(f"  PRACTICAL IMPACT: per-game splits by opponent quality")
print(f"{'='*70}")

for label, stat, col in [
    ("K", "k", "team_K_pct"), ("H", "h", "team_BA"),
    ("BB", "bb", "team_BB_pct"), ("IP", "ip", "team_OPS"),
]:
    med = df[col].median()
    hi = df[df[col] >= med][stat].mean()
    lo = df[df[col] < med][stat].mean()
    n_hi = len(df[df[col] >= med])
    n_lo = len(df[df[col] < med])
    print(f"\n  {label}/start by {col} (median={med:.3f}):")
    print(f"    vs tough (above med): {hi:.2f} (n={n_hi})")
    print(f"    vs easy (below med):  {lo:.2f} (n={n_lo})")
    print(f"    Delta:                {hi-lo:+.2f}")

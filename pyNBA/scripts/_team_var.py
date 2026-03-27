# scripts/_team_var.py
import json
import math

def main():
    with open("data/history.json", "r") as f:
        h = json.load(f)

    # Compute prediction error variance per team
    team_errors = {}

    for r in h.get("runs", []):
        if r.get("burnIn"):
            continue
        for g in r.get("games", []):
            home_score = g.get("homeScore")
            away_score = g.get("awayScore")
            margin = g.get("margin")
            line = g.get("line")
            if not all(isinstance(v, (int, float)) for v in [home_score, away_score, margin, line]):
                continue

            actual_edge = (home_score - away_score) + line
            proj_edge = margin
            err = actual_edge - proj_edge

            home = g.get("home", "")
            away = g.get("away", "")
            team_errors.setdefault(home, []).append(err)
            team_errors.setdefault(away, []).append(err)

    print("\n=== Per-Team Prediction Error Variance ===\n")
    print(f"{'Team':<30}{'Games':>6}  {'Var':>8}  {'Std':>6}  {'Win%':>7}")
    print("-" * 60)

    # Also compute win rate per team
    team_wl = {}
    for r in h.get("runs", []):
        if r.get("burnIn"):
            continue
        for g in r.get("games", []):
            if g.get("sResult") not in ("WIN", "LOSS"):
                continue
            pick = g.get("sPick", "")
            for team in [g.get("home", ""), g.get("away", "")]:
                if team in pick:
                    if team not in team_wl:
                        team_wl[team] = {"w": 0, "l": 0}
                    if g.get("sResult") == "WIN":
                        team_wl[team]["w"] += 1
                    else:
                        team_wl[team]["l"] += 1

    sorted_teams = []
    for team, errs in team_errors.items():
        if len(errs) < 5:
            continue
        mean = sum(errs) / len(errs)
        variance = sum((x - mean) ** 2 for x in errs) / len(errs)
        wl = team_wl.get(team, {"w": 0, "l": 0})
        total = wl["w"] + wl["l"]
        pct = f"{wl['w'] / total * 100:.0f}" if total > 0 else "n/a"
        sorted_teams.append({
            "team": team, "n": len(errs), "variance": variance,
            "std": math.sqrt(variance), "win_pct": pct, "wl": wl
        })

    sorted_teams.sort(key=lambda x: x["variance"], reverse=True)

    for t in sorted_teams:
        wl = t["wl"]
        wl_str = f"{wl['w']}-{wl['l']}" if wl["w"] + wl["l"] > 0 else ""
        print(
            f"{t['team']:<30}{t['n']:>6}{t['variance']:>8.1f}{t['std']:>6.1f}"
            f"  {t['win_pct']}% {wl_str}"
        )

    if sorted_teams:
        avg_var = sum(t["variance"] for t in sorted_teams) / len(sorted_teams)
        ratio = sorted_teams[0]["variance"] / sorted_teams[-1]["variance"]
        print(f"\nOverall avg var: {avg_var:.1f}")
        print(f"Most volatile vs least volatile ratio: {ratio:.1f}x")

if __name__ == "__main__":
    main()

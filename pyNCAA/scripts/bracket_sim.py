# scripts/bracket_sim.py
# March Madness 2026 bracket simulation using full model stats + Kalman adjustments

import json
import math
import os
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPTS_DIR, "..", "data")

stats_data = json.loads(open(os.path.join(SCRIPTS_DIR, "..", "..", "data", "stats_cache", "ncaab", "20250317.json"), "r").read())
H = stats_data["season"]
ks = json.loads(open(os.path.join(DATA_DIR, "kalman_state.json"), "r").read())


# -- Team name resolution ---------------------------------------------------

def norm_key(s):
    import re
    s = str(s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def resolve_team(name, data):
    if name in data:
        return name
    teams = list(data.keys())
    wanted = norm_key(name)
    for k in teams:
        if norm_key(k) == wanted:
            return k
    # Longest prefix match
    best_prefix = None
    best_len = 0
    for k in teams:
        nk = norm_key(k)
        if nk == wanted or wanted == nk:
            return k
        if (wanted.startswith(nk + " ") or nk.startswith(wanted + " ")) and len(nk) >= 3:
            l = min(len(nk), len(wanted))
            if l > best_len:
                best_len = l
                best_prefix = k
    if best_prefix:
        return best_prefix
    # Fuzzy
    for k in teams:
        nk = norm_key(k)
        if nk in wanted or wanted in nk:
            return k
    return None


# -- Normal CDF -------------------------------------------------------------

def normal_cdf(x):
    a1 = 0.254829592
    a2 = -0.284496736
    a3 = 1.421413741
    a4 = -1.453152027
    a5 = 1.061405429
    p = 0.3275911
    sign = -1 if x < 0 else 1
    z = abs(x) / math.sqrt(2)
    t = 1.0 / (1.0 + p * z)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-z * z)
    return 0.5 * (1.0 + sign * y)


# -- Game simulation --------------------------------------------------------

def sim_game(team_a, team_b):
    a_key = resolve_team(team_a, H)
    b_key = resolve_team(team_b, H)

    if not a_key or not b_key:
        if not a_key and not b_key:
            return {"winner": team_a, "loser": team_b, "prob": 0.5, "margin": 0}
        if not a_key:
            return {"winner": team_b, "loser": team_a, "prob": 0.75, "margin": 5}
        return {"winner": team_a, "loser": team_b, "prob": 0.75, "margin": 5}

    a = H[a_key]
    b = H[b_key]

    a_expected = (a["OFF"] + b["DEF"]) / 2
    b_expected = (b["OFF"] + a["DEF"]) / 2
    avg_pace = (a["PACE"] + b["PACE"]) / 2 / 100

    margin = (a_expected - b_expected) * avg_pace

    # Add Kalman adjustment
    a_kalman = resolve_team(team_a, ks.get("teams", {}))
    b_kalman = resolve_team(team_b, ks.get("teams", {}))
    if a_kalman and b_kalman:
        margin += (ks["teams"][a_kalman]["adj_mean"] - ks["teams"][b_kalman]["adj_mean"])

    game_sd = 11
    p_a_wins = normal_cdf(margin / game_sd)

    if p_a_wins >= 0.5:
        return {"winner": team_a, "loser": team_b, "prob": p_a_wins, "margin": margin}
    else:
        return {"winner": team_b, "loser": team_a, "prob": 1 - p_a_wins, "margin": -margin}


# -- Bracket definition -----------------------------------------------------

def main():
    print("\u2554" + "\u2550" * 66 + "\u2557")
    print("\u2551         2026 NCAA MARCH MADNESS BRACKET PREDICTIONS            \u2551")
    print("\u2551         Bayesian Model + Kalman Filter Adjustments             \u2551")
    print("\u255a" + "\u2550" * 66 + "\u255d\n")

    print("\u2501" * 18 + " FIRST FOUR " + "\u2501" * 18)
    ff1 = sim_game("Texas", "N.C. State")
    ff2 = sim_game("UMBC", "Howard")
    ff3 = sim_game("Miami OH", "SMU")
    ff4 = sim_game("Prairie View A&M", "Lehigh")

    for g in [ff1, ff2, ff3, ff4]:
        print(f"  {g['winner']:<22} def. {g['loser']:<22} ({g['prob']*100:.0f}% | proj margin: {abs(g['margin']):.1f})")

    # -- Region definitions --
    regions = {
        "EAST": [
            ["Duke", "Siena"],
            ["Ohio St.", "TCU"],
            ["St. John's", "Northern Iowa"],
            ["Kansas", "Cal Baptist"],
            ["Louisville", "South Florida"],
            ["Michigan St.", "North Dakota St."],
            ["UCLA", "UCF"],
            ["Connecticut", "Furman"],
        ],
        "WEST": [
            ["Arizona", "LIU"],
            ["Villanova", "Utah St."],
            ["Wisconsin", "High Point"],
            ["Arkansas", "Hawaii"],
            ["BYU", ff1["winner"]],
            ["Gonzaga", "Kennesaw St."],
            ["Miami FL", "Missouri"],
            ["Purdue", "Queens"],
        ],
        "MIDWEST": [
            ["Michigan", ff2["winner"]],
            ["Georgia", "Saint Louis"],
            ["Texas Tech", "Akron"],
            ["Alabama", "Hofstra"],
            ["Tennessee", ff3["winner"]],
            ["Virginia", "Wright St."],
            ["Kentucky", "Santa Clara"],
            ["Iowa St.", "Tennessee St."],
        ],
        "SOUTH": [
            ["Florida", ff4["winner"]],
            ["Clemson", "Iowa"],
            ["Vanderbilt", "McNeese St."],
            ["Nebraska", "Troy"],
            ["North Carolina", "VCU"],
            ["Illinois", "Penn"],
            ["Saint Mary's", "Texas A&M"],
            ["Houston", "Idaho"],
        ],
    }

    seeds = {
        "EAST": {"Duke": 1, "Siena": 16, "Ohio St.": 8, "TCU": 9, "St. John's": 5, "Northern Iowa": 12, "Kansas": 4, "Cal Baptist": 13, "Louisville": 6, "South Florida": 11, "Michigan St.": 3, "North Dakota St.": 14, "UCLA": 7, "UCF": 10, "Connecticut": 2, "Furman": 15},
        "WEST": {"Arizona": 1, "LIU": 16, "Villanova": 8, "Utah St.": 9, "Wisconsin": 5, "High Point": 12, "Arkansas": 4, "Hawaii": 13, "BYU": 6, ff1["winner"]: 11, "Gonzaga": 3, "Kennesaw St.": 14, "Miami FL": 7, "Missouri": 10, "Purdue": 2, "Queens": 15},
        "MIDWEST": {"Michigan": 1, ff2["winner"]: 16, "Georgia": 8, "Saint Louis": 9, "Texas Tech": 5, "Akron": 12, "Alabama": 4, "Hofstra": 13, "Tennessee": 6, ff3["winner"]: 11, "Virginia": 3, "Wright St.": 14, "Kentucky": 7, "Santa Clara": 10, "Iowa St.": 2, "Tennessee St.": 15},
        "SOUTH": {"Florida": 1, ff4["winner"]: 16, "Clemson": 8, "Iowa": 9, "Vanderbilt": 5, "McNeese St.": 12, "Nebraska": 4, "Troy": 13, "North Carolina": 6, "VCU": 11, "Illinois": 3, "Penn": 14, "Saint Mary's": 7, "Texas A&M": 10, "Houston": 2, "Idaho": 15},
    }

    def get_seed(region, team):
        if region in seeds and team in seeds[region]:
            return seeds[region][team]
        for r in seeds.values():
            if team in r:
                return r[team]
        return "?"

    def sim_round(matchups, region, round_name):
        results = []
        print(f"\n  {round_name}:")
        for a, b in matchups:
            g = sim_game(a, b)
            s_w = get_seed(region, g["winner"])
            s_l = get_seed(region, g["loser"])
            print(f"    ({str(s_w):>2}) {g['winner']:<20} def. ({str(s_l):>2}) {g['loser']:<20} {g['prob']*100:.0f}%  margin: {abs(g['margin']):.1f}")
            results.append(g["winner"])
        return results

    final_four_teams = []

    for region_name, matchups in regions.items():
        print(f"\n{'\u2501' * 22} {region_name} REGION {'\u2501' * 22}")

        r64 = sim_round(matchups, region_name, "Round of 64")

        r32_matchups = [[r64[i], r64[i + 1]] for i in range(0, len(r64), 2)]
        r32 = sim_round(r32_matchups, region_name, "Round of 32")

        s16_matchups = [[r32[i], r32[i + 1]] for i in range(0, len(r32), 2)]
        s16 = sim_round(s16_matchups, region_name, "Sweet 16")

        e8 = sim_round([[s16[0], s16[1]]], region_name, "Elite 8")

        print(f"\n  >>> {region_name} CHAMPION: ({get_seed(region_name, e8[0])}) {e8[0]}")
        final_four_teams.append(e8[0])

    # -- Final Four --
    print(f"\n{'\u2501' * 22} FINAL FOUR {'\u2501' * 22}")
    print("  Lucas Oil Stadium, Indianapolis -- April 4, 2026\n")

    semi1 = sim_game(final_four_teams[0], final_four_teams[3])  # East vs South
    semi2 = sim_game(final_four_teams[2], final_four_teams[1])  # Midwest vs West

    print("  Semifinal 1 (East vs South):")
    print(f"    {semi1['winner']:<20} def. {semi1['loser']:<20} ({semi1['prob']*100:.0f}% | margin: {abs(semi1['margin']):.1f})")
    print("  Semifinal 2 (Midwest vs West):")
    print(f"    {semi2['winner']:<20} def. {semi2['loser']:<20} ({semi2['prob']*100:.0f}% | margin: {abs(semi2['margin']):.1f})")

    # -- Championship --
    print(f"\n{'\u2501' * 22} NATIONAL CHAMPIONSHIP {'\u2501' * 22}")
    print("  April 6, 2026 -- Lucas Oil Stadium, Indianapolis\n")

    champ = sim_game(semi1["winner"], semi2["winner"])
    print(f"    {champ['winner']:<20} def. {champ['loser']:<20} ({champ['prob']*100:.0f}% | margin: {abs(champ['margin']):.1f})")

    print(f"\n{'=' * 60}")
    print(f"  NATIONAL CHAMPION: {champ['winner']}")
    print(f"{'=' * 60}")

    # -- Summary --
    print("\n BRACKET SUMMARY")
    print("\u2500" * 40)
    region_names = list(regions.keys())
    print("  Final Four:")
    for i, team in enumerate(final_four_teams):
        print(f"    {region_names[i]:<8}: ({get_seed(region_names[i], team)}) {team}")
    print(f"  Champion:  {champ['winner']}")
    print(f"  Runner-up: {champ['loser']}\n")

    # -- Upset alerts --
    print("  NOTABLE UPSETS TO WATCH:")
    print("  (Teams where a lower seed wins by 3+ seed lines)")
    print("\u2500" * 40)


if __name__ == "__main__":
    main()

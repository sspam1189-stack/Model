# scripts/print_report.py
import math
from store import load_store


def grade_total(g):
    if g.get("oPick") == "PASS":
        return None
    home_score = g.get("homeScore")
    away_score = g.get("awayScore")
    if not isinstance(home_score, (int, float)) or not math.isfinite(home_score):
        return None
    if not isinstance(away_score, (int, float)) or not math.isfinite(away_score):
        return None
    actual_total = home_score + away_score
    total = g.get("total")
    if not isinstance(total, (int, float)) or not math.isfinite(total) or total == 0:
        return None
    if actual_total == total:
        return "push"
    if g.get("oPick") == "OVER":
        return "win" if actual_total > total else "loss"
    return "win" if actual_total < total else "loss"


def grade_spread(g):
    if g.get("sPick") == "PASS":
        return None
    home_score = g.get("homeScore")
    away_score = g.get("awayScore")
    if not isinstance(home_score, (int, float)) or not math.isfinite(home_score):
        return None
    if not isinstance(away_score, (int, float)) or not math.isfinite(away_score):
        return None

    import re
    m = re.match(r"(.+?)\s+([+-])(\d+(?:\.\d+)?)", g.get("sPick", ""))
    if not m:
        return None
    team = m.group(1).strip()
    sign = m.group(2)
    pts = float(m.group(3))
    if not math.isfinite(pts):
        return None

    chosen_is_home = team == g.get("home")
    chosen_score = home_score if chosen_is_home else away_score
    opp_score = away_score if chosen_is_home else home_score
    chosen_margin = chosen_score - opp_score

    if sign == "+":
        cover = chosen_margin + pts > 0
        push = chosen_margin + pts == 0
    else:
        cover = chosen_margin - pts > 0
        push = chosen_margin - pts == 0
    return "push" if push else ("win" if cover else "loss")


def tally(items, pick_type, conf=None):
    w = l = p = 0
    for g in items:
        c = g.get("sConf") if pick_type == "spread" else g.get("oConf")
        if conf and c != conf:
            continue
        res = grade_spread(g) if pick_type == "spread" else grade_total(g)
        if not res:
            continue
        if res == "win":
            w += 1
        elif res == "loss":
            l += 1
        else:
            p += 1
    denom = w + l
    pct = f"{w / denom * 100:.1f}" if denom else "n/a"
    return {"w": w, "l": l, "p": p, "pct": pct}


def main():
    store = load_store()
    games = []
    for r in store.get("runs", []):
        for g in r.get("games", []):
            game = dict(g)
            game["date"] = r.get("dateDisplay")
            games.append(game)

    s_all = tally(games, "spread")
    s_high = tally(games, "spread", "high")
    s_med = tally(games, "spread", "medium")

    t_all = tally(games, "total")
    t_high = tally(games, "total", "high")
    t_med = tally(games, "total", "medium")

    print("Spread all:", s_all, "high:", s_high, "med:", s_med)
    print("Total  all:", t_all, "high:", t_high, "med:", t_med)


if __name__ == "__main__":
    main()

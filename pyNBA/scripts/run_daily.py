# scripts/run_daily.py
# Daily NBA picks pipeline:
#   1. Grade yesterday's picks against final scores
#   2. Fetch today's stats, odds, trends, injuries
#   3. Analyze each game via model engine
#   4. Build HTML + text email with records, trends, rolling windows
#   5. Send via email

import os
import sys
import re
import json
import math
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
load_dotenv()

from sources.nba_stats import fetch_nba_stats, fetch_nba_stats_enhanced
from sources.blend_stats import blend_base, blend_for_game
from sources.odds_theoddsapi import fetch_todays_odds
from sources.espn_scoreboard import fetch_scoreboard, extract_final_scores
from sources.teamrankings_trends import fetch_ats_trends, fetch_ou_trends
from sources.injuries import fetch_injury_data, get_key_injuries
from sources.lineup_adjust import fetch_player_advanced, adjust_team_stats
from sources.rest_detect import detect_b2b, apply_b2b_adjustment
from sources.season_type import get_season_type, get_espn_season_type
from model_engine import load_defaults, get_avgs, analyze_game
from store import load_store, save_store, upsert_run
from self_tune import tune_weights, compute_residual_var
from kalman_state import (
    load_kalman_state, save_kalman_state, initialize_kalman,
    apply_daily_drift, batch_update, kalman_summary, prune_processed_games,
)
from calibration import build_calibration_table, build_calibration_html
from email_report import send_email
from lr_model import load_or_train_lr, build_team_histories, extract_lr_features, predict_lr

# --- Constants ---

TIMEZONE = "America/Chicago"
CONF_ACTIONABLE = ["high", "elite"]
UNIT_LOSS = -1.1  # standard -110 juice
TOTAL_HALF_UNIT_START = "2026-03-07"  # totals picks bet at 0.5u from this date forward
TOTAL_PICKS_END_DATE = "2026-03-09"   # stop taking totals picks from this date onward

# --- Utility Helpers ---

def esc(s):
    return (str(s) if s is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;")


def fmt_num(x, digits=1):
    if isinstance(x, (int, float)) and math.isfinite(x):
        return f"{x:.{digits}f}"
    return "n/a"


def calc_units(w, l):
    return w + l * UNIT_LOSS


def total_pick_unit(date, result):
    half_unit = date >= TOTAL_HALF_UNIT_START
    win = 0.5 if half_unit else 1.0
    loss = -0.55 if half_unit else UNIT_LOSS
    if result == "WIN":
        return win
    if result == "LOSS":
        return loss
    return 0  # PUSH


def fmt_units(u):
    if not isinstance(u, (int, float)) or not math.isfinite(u):
        return "\u2014"
    return f"{'+' if u >= 0 else ''}{u:.1f}u"


def win_pct(w, l):
    total = w + l
    return f"{w / total * 100:.1f}" if total > 0 else "n/a"


def is_actionable(conf):
    return str(conf or "").lower() in CONF_ACTIONABLE


# --- Team Name Resolution ---

def norm_key(s):
    s = str(s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


MASCOTS = {
    "lakers", "clippers", "warriors", "thunder", "pelicans", "knicks", "spurs",
    "76ers", "sixers", "timberwolves", "grizzlies", "hornets", "pacers",
    "wizards", "magic", "heat", "hawks", "bulls", "pistons", "cavaliers",
    "raptors", "nets", "celtics", "bucks", "nuggets", "jazz", "suns",
    "kings", "mavericks", "rockets", "trail blazers",
}

TEAM_ALIASES = {
    "la lakers": "los angeles lakers", "l a lakers": "los angeles lakers",
    "la clippers": "los angeles clippers", "l a clippers": "los angeles clippers",
    "okc thunder": "oklahoma city thunder", "okc": "oklahoma city thunder",
    "oklahoma city": "oklahoma city thunder", "okla city": "oklahoma city thunder",
    "gs warriors": "golden state warriors", "gsw": "golden state warriors",
    "golden state": "golden state warriors",
    "ny knicks": "new york knicks", "nyk": "new york knicks",
    "new york": "new york knicks",
    "sa spurs": "san antonio spurs", "san antonio": "san antonio spurs",
    "no pelicans": "new orleans pelicans", "new orleans": "new orleans pelicans",
    "phx suns": "phoenix suns", "philly 76ers": "philadelphia 76ers",
    "mn timberwolves": "minnesota timberwolves", "por trail blazers": "portland trail blazers",
}


def extract_mascot(normalized):
    words = normalized.split(" ")
    last = words[-1]
    if last in MASCOTS:
        return last
    if len(words) >= 2:
        last_two = " ".join(words[-2:])
        if last_two in MASCOTS:
            return last_two
    return None


def resolve_key(obj, team_name):
    if not obj or not team_name:
        return None
    if team_name in obj:
        return team_name

    wanted = norm_key(team_name)
    aliased = TEAM_ALIASES.get(wanted, wanted)

    for k in obj:
        nk = norm_key(k)
        if nk == wanted or nk == aliased:
            return k
        if nk.startswith(wanted) or wanted in nk or nk in wanted:
            return k
        if aliased != wanted and (aliased in nk or nk in aliased):
            return k

    # Reverse alias
    for alias_key, alias_val in TEAM_ALIASES.items():
        if norm_key(alias_val) == wanted or norm_key(alias_val) == aliased:
            for k in obj:
                if norm_key(k) == alias_key:
                    return k

    # Fallback: mascot match
    wanted_mascot = extract_mascot(wanted) or extract_mascot(aliased)
    if wanted_mascot:
        for k in obj:
            km = extract_mascot(norm_key(k))
            if km and km == wanted_mascot:
                return k

    return None


def match_team(a, b):
    if a == b:
        return True
    na, nb = norm_key(a), norm_key(b)
    if na == nb:
        return True
    if na in nb or nb in na:
        return True
    ma, mb = extract_mascot(na), extract_mascot(nb)
    if ma and mb and ma == mb:
        return True
    return False


# --- Date Helpers ---

def central_date_parts(dt=None):
    """Get year, month, day in Central Time."""
    import pytz
    tz = pytz.timezone(TIMEZONE)
    if dt is None:
        dt = datetime.now(tz)
    elif dt.tzinfo is None:
        dt = tz.localize(dt)
    else:
        dt = dt.astimezone(tz)
    return {"y": f"{dt.year:04d}", "m": f"{dt.month:02d}", "d": f"{dt.day:02d}"}


def today_central_yyyymmdd():
    p = central_date_parts()
    return f"{p['y']}{p['m']}{p['d']}"


def yyyymmdd_from_central_offset(days_back=0):
    p = central_date_parts()
    from datetime import date as ddate
    base = ddate(int(p["y"]), int(p["m"]), int(p["d"]))
    base = base - timedelta(days=days_back)
    return f"{base.year:04d}{base.month:02d}{base.day:02d}"


def to_display_date(yyyymmdd):
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


# --- Trend Formatting ---

def join_trends(team, ats_obj, ou_obj):
    parts = []
    if ats_obj:
        if ats_obj.get("atsPct") is not None:
            parts.append(f"ATS {ats_obj['atsPct']}%")
        if ats_obj.get("atsPlusMinus") is not None:
            parts.append(f"ATS +/- {ats_obj['atsPlusMinus']}")
    if ou_obj:
        if ou_obj.get("overPct") is not None:
            parts.append(f"Over {ou_obj['overPct']}%")
        if ou_obj.get("underPct") is not None:
            parts.append(f"Under {ou_obj['underPct']}%")
        if ou_obj.get("totalPlusMinus") is not None:
            parts.append(f"O/U +/- {ou_obj['totalPlusMinus']}")
    return " \u00b7 ".join(parts)


# --- Grading ---

def parse_spread_pick(pick):
    if not pick or pick == "PASS":
        return None
    m = re.match(r"(.+?)\s+([+-])(\d+(?:\.\d+)?)", pick)
    if not m:
        return None
    return {"team": m.group(1).strip(), "sign": m.group(2), "pts": float(m.group(3))}


def grade_spread_pick(g):
    parsed = parse_spread_pick(g.get("sPick"))
    if not parsed:
        return None
    team, sign, pts = parsed["team"], parsed["sign"], parsed["pts"]
    chosen_is_home = team == g.get("home")
    margin = (g["homeScore"] - g["awayScore"]) if chosen_is_home else (g["awayScore"] - g["homeScore"])
    val = margin + pts if sign == "+" else margin - pts
    if val == 0:
        return "PUSH"
    return "WIN" if val > 0 else "LOSS"


def grade_total_pick(g):
    o_pick = g.get("oPick")
    if not o_pick or o_pick == "PASS":
        return None
    actual = g["homeScore"] + g["awayScore"]
    if actual == g.get("total"):
        return "PUSH"
    if o_pick == "OVER":
        return "WIN" if actual > g["total"] else "LOSS"
    return "WIN" if actual < g["total"] else "LOSS"


def grade_result(g, pick_type):
    if pick_type == "spread":
        return g.get("sResult") or grade_spread_pick(g)
    return g.get("oResult") or grade_total_pick(g)


# --- Store: Grade a Past Date ---

def grade_date_in_store(store, yyyymmdd):
    run = None
    for r in store.get("runs", []):
        if r.get("date") == yyyymmdd:
            run = r
            break
    if not run:
        return

    needs_grading = any(
        g.get("status") not in ("MISSING_ODDS", "SKIPPED") and not isinstance(g.get("homeScore"), (int, float))
        for g in run.get("games", [])
    )
    if not needs_grading:
        return

    try:
        sb = fetch_scoreboard(yyyymmdd)
    except Exception:
        return
    if not sb:
        return

    finals = extract_final_scores(sb)
    if not finals:
        return

    graded = 0
    for g in run.get("games", []):
        if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
            continue
        if isinstance(g.get("homeScore"), (int, float)):
            continue

        f = None
        for x in finals:
            if match_team(x["away"], g["away"]) and match_team(x["home"], g["home"]):
                f = x
                break
        if not f:
            continue

        g["awayScore"] = f["awayScore"]
        g["homeScore"] = f["homeScore"]
        if g.get("sPick") and g["sPick"] != "PASS":
            g["sResult"] = grade_spread_pick(g)
        if g.get("oPick") and g["oPick"] != "PASS":
            g["oResult"] = grade_total_pick(g)
        graded += 1

    if graded > 0:
        save_store(store)


# --- Team Record ---

def compute_team_records(store):
    teams = {}
    for r in store.get("runs", []):
        for g in r.get("games", []):
            pick = g.get("sPick")
            conf = g.get("sConf")
            if not pick or pick == "PASS" or not is_actionable(conf):
                continue
            home_score = g.get("homeScore")
            away_score = g.get("awayScore")
            if not isinstance(home_score, (int, float)) or not isinstance(away_score, (int, float)):
                continue

            result = g.get("sResult") or grade_spread_pick(g)
            if not result:
                continue

            m = re.match(r"(.+?)\s+([+-])(\d+(?:\.\d+)?)", pick)
            if not m:
                continue
            team = m.group(1).strip()
            is_fav = m.group(2) == "-"

            if team not in teams:
                teams[team] = {"w": 0, "l": 0, "p": 0, "fav": 0, "dog": 0, "picks": 0}
            teams[team]["picks"] += 1
            if is_fav:
                teams[team]["fav"] += 1
            else:
                teams[team]["dog"] += 1
            if result == "WIN":
                teams[team]["w"] += 1
            elif result == "LOSS":
                teams[team]["l"] += 1
            else:
                teams[team]["p"] += 1
    return teams


def build_team_record_table(team_records):
    sorted_teams = sorted(
        [(name, t) for name, t in team_records.items() if t["w"] + t["l"] >= 3],
        key=lambda x: x[1]["w"] - x[1]["l"] * 1.1,
        reverse=True
    )

    if not sorted_teams:
        return ""

    rows = []
    for name, t in sorted_teams:
        total = t["w"] + t["l"]
        pct = round(100 * t["w"] / total) if total > 0 else 0
        flat_units = round((t["w"] - t["l"] * 1.1) * 10) / 10
        flat_str = f"{'+' if flat_units >= 0 else ''}{flat_units:.1f}u"
        flat_color = "#10b981" if flat_units >= 0 else "#ef4444"
        rows.append(
            f'<tr><td>{esc(name)}</td><td class="">{t["picks"]}</td>'
            f'<td class="">{t["w"]}-{t["l"]}-{t["p"]}</td>'
            f'<td class="">{pct}%</td>'
            f'<td class="" style="color:{flat_color}">{flat_str}</td>'
            f'<td class="">{t["fav"]}</td><td class="">{t["dog"]}</td></tr>'
        )

    return f'''
    <div class="card card-records">
      <div class="summaryTitle">\U0001F3C0 Team Spread Record (ATS)</div>
      <table class="data">
        <thead><tr>
          <th>Team</th><th class="">Picks</th><th class="">W-L-P</th>
          <th class="">Win%</th><th class="">Flat</th>
          <th class="">Fav</th><th class="">Dog</th>
        </tr></thead>
        <tbody>{"".join(rows)}</tbody>
      </table>
      <div class="tiny">Only graded spread picks. Units at -110 juice.</div>
    </div>'''


# --- Record Computation ---

def get_graded_picks(store):
    picks = []
    for r in store.get("runs", []):
        if r.get("burnIn"):
            continue
        for g in r.get("games", []):
            home_score = g.get("homeScore")
            away_score = g.get("awayScore")
            if not isinstance(home_score, (int, float)) or not isinstance(away_score, (int, float)):
                continue
            if not math.isfinite(home_score) or not math.isfinite(away_score):
                continue
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue
            pick = dict(g)
            pick["date"] = r["date"]
            picks.append(pick)
    return picks


def tally_picks(lst, type="spread", conf=None, side=None):
    w = l = p = 0
    total_units = 0

    for g in lst:
        pick = g.get("sPick") if type == "spread" else g.get("oPick")
        pick_conf = g.get("sConf") if type == "spread" else g.get("oConf")
        if not pick or pick == "PASS":
            continue
        if conf and pick_conf != conf:
            continue

        # Filter by side
        if side:
            if type == "spread":
                if not is_actionable(pick_conf):
                    continue
                parsed = parse_spread_pick(pick)
                if not parsed:
                    continue
                pick_side = "fav" if parsed["sign"] == "-" else "dog"
                if pick_side != side:
                    continue
            else:
                if not is_actionable(pick_conf):
                    continue
                pick_side = "over" if pick == "OVER" else "under"
                if pick_side != side:
                    continue

        result = grade_result(g, type)
        if not result:
            continue

        if result == "WIN":
            w += 1
        elif result == "LOSS":
            l += 1
        else:
            p += 1

        if type == "total":
            total_units += total_pick_unit(g.get("date", ""), result)

    pct = win_pct(w, l)
    units = total_units if type == "total" else calc_units(w, l)
    return {"w": w, "l": l, "p": p, "pct": pct, "played": w + l + p, "units": units}


def compute_summary_obj(store):
    picks = get_graded_picks(store)
    def tally(opts):
        return tally_picks(picks, **opts)

    return {
        "spread": {
            "all": tally({"type": "spread"}),
            "elite": tally({"type": "spread", "conf": "elite"}),
        },
        "favdog": {
            "fav": tally({"type": "spread", "side": "fav"}),
            "dog": tally({"type": "spread", "side": "dog"}),
        },
        "total": {
            "all": tally({"type": "total"}),
            "elite": tally({"type": "total", "conf": "elite"}),
        },
        "ouside": {
            "over": tally({"type": "total", "side": "over"}),
            "under": tally({"type": "total", "side": "under"}),
        },
    }


def compute_summary_text(store):
    s = compute_summary_obj(store)
    def row(label, b):
        return f"  {label:<12} {b['w']}-{b['l']}-{b['p']}   ({b['pct']}%)   [graded {b['played']}]"

    lines = [
        "RECORD (graded picks only)", "",
        "SPREAD (ATS)",
        row("All:", s["spread"]["all"]),
        row("Elite:", s["spread"]["elite"]), "",
        "FAV/DOG (Spread)",
        row("Favorites:", s["favdog"]["fav"]),
        row("Underdogs:", s["favdog"]["dog"]), "",
        "TOTAL (O/U)",
        row("All:", s["total"]["all"]),
        row("Elite:", s["total"]["elite"]), "",
        "OVER/UNDER",
        row("Over:", s["ouside"]["over"]),
        row("Under:", s["ouside"]["under"]),
    ]
    return "\n".join(lines)


# --- Last 10 / Rolling / Weekly ---

def get_actionable_picks(store, pick_type="spread"):
    results = []
    for r in store.get("runs", []):
        if r.get("burnIn"):
            continue
        for g in r.get("games", []):
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue
            pick = g.get("sPick") if pick_type == "spread" else g.get("oPick")
            conf = g.get("sConf") if pick_type == "spread" else g.get("oConf")
            if not pick or pick == "PASS" or not is_actionable(conf):
                continue
            home_score = g.get("homeScore")
            away_score = g.get("awayScore")
            if not isinstance(home_score, (int, float)) or not isinstance(away_score, (int, float)):
                continue
            if not math.isfinite(home_score) or not math.isfinite(away_score):
                continue

            result = grade_result(g, pick_type)
            final = f"{g['awayScore']}-{g['homeScore']}"
            results.append({
                "date": r["date"],
                "matchup": f"{g['away']} @ {g['home']}",
                "pick": pick,
                "conf": conf,
                "result": result or "PENDING",
                "final": final,
                "total": g.get("total"),
                "home": g.get("home"),
            })
    return results


def compute_last10(store, pick_type="spread"):
    return get_actionable_picks(store, pick_type)[-10:]


def last10_text(store, pick_type="spread"):
    label = "SPREAD" if pick_type == "spread" else "TOTAL"
    picks = compute_last10(store, pick_type)
    lines = [f"LAST 10 {label} PICKS (run order)"]
    if not picks:
        return "\n".join(lines + ["  No picks yet."])
    for p in picks:
        extra = f" {p.get('total', '')}" if pick_type == "total" else ""
        lines.append(f"  {to_display_date(p['date'])} | {p['matchup']} | {p['pick']}{extra} | {p['conf'].upper()} | {p['result']} | {p['final']}")
    return "\n".join(lines)


def compute_rolling_windows(store, pick_type="spread"):
    picks = [p for p in get_actionable_picks(store, pick_type) if p.get("result") and p["result"] != "PENDING"]
    window = 10 if len(picks) < 100 else 20

    rows = []
    for i in range(0, len(picks) - window + 1, window):
        s = picks[i:i + window]
        w = sum(1 for x in s if x["result"] == "WIN")
        l = sum(1 for x in s if x["result"] == "LOSS")
        p = sum(1 for x in s if x["result"] == "PUSH")
        if pick_type == "total":
            units = sum(total_pick_unit(x.get("date", ""), x["result"]) for x in s)
        else:
            units = calc_units(w, l)

        rows.append({
            "label": f"#{i + 1}\u2013{i + window}",
            "w": w, "l": l, "p": p,
            "pct": win_pct(w, l),
            "units": units,
            "startDate": to_display_date(s[0]["date"]),
            "endDate": to_display_date(s[-1]["date"]),
        })
    return rows


def get_monday_of_week(dt):
    day = dt.weekday()  # 0=Monday
    monday = dt - timedelta(days=day)
    return f"{monday.year:04d}-{monday.month:02d}-{monday.day:02d}"


def compute_weekly_breakdown(store, pick_type="spread"):
    picks = [p for p in get_actionable_picks(store, pick_type) if p.get("result") and p["result"] != "PENDING"]
    by_week = {}

    for p in picks:
        dd = to_display_date(p["date"])
        dt = datetime.strptime(dd, "%Y-%m-%d")
        key = get_monday_of_week(dt)
        by_week.setdefault(key, []).append({"result": p["result"], "date": p["date"]})

    rows = []
    for week in sorted(by_week.keys()):
        entries = by_week[week]
        w = sum(1 for x in entries if x["result"] == "WIN")
        l = sum(1 for x in entries if x["result"] == "LOSS")
        p = sum(1 for x in entries if x["result"] == "PUSH")
        if pick_type == "total":
            units = sum(total_pick_unit(x.get("date", ""), x["result"]) for x in entries)
        else:
            units = calc_units(w, l)
        rows.append({"week": week, "w": w, "l": l, "p": p, "pct": win_pct(w, l), "units": units})
    return rows[-12:]


# --- Yesterday's Recap ---

def compute_yesterday_recap(store, yesterday_date):
    yesterday_run = None
    for r in store.get("runs", []):
        if r.get("date") == yesterday_date:
            yesterday_run = r
            break
    if not yesterday_run:
        return None

    picks = []
    units = 0

    for g in yesterday_run.get("games", []):
        if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
            continue
        home_score = g.get("homeScore")
        away_score = g.get("awayScore")
        if not isinstance(home_score, (int, float)) or not isinstance(away_score, (int, float)):
            continue

        if g.get("sPick") and g["sPick"] != "PASS" and is_actionable(g.get("sConf")):
            result = g.get("sResult") or grade_spread_pick(g)
            if result:
                picks.append({
                    "matchup": f"{g['away']} @ {g['home']}",
                    "pick": g["sPick"],
                    "conf": g["sConf"],
                    "result": result,
                    "final": f"{g['awayScore']}-{g['homeScore']}",
                    "sDiff": g.get("sDiff"),
                })
                if result == "WIN":
                    units += 1
                elif result == "LOSS":
                    units += UNIT_LOSS

    if not picks:
        return None

    w = sum(1 for p in picks if p["result"] == "WIN")
    l = sum(1 for p in picks if p["result"] == "LOSS")
    p = sum(1 for p in picks if p["result"] == "PUSH")

    return {
        "date": yesterday_date,
        "dateDisplay": to_display_date(yesterday_date),
        "picks": picks,
        "tally": {"w": w, "l": l, "p": p},
        "units": round(units * 100) / 100,
    }


def build_recap_html(recap):
    if not recap or not recap.get("picks"):
        return ""

    def result_badge(result):
        if result == "WIN":
            return '<span style="color:#10b981; font-weight:700">\u2713 WIN</span>'
        if result == "LOSS":
            return '<span style="color:#ef4444; font-weight:700">\u2717 LOSS</span>'
        return '<span style="color:#6b7280; font-weight:700">\u229c PUSH</span>'

    rows = ""
    for p in recap["picks"]:
        s_diff_str = f"{p['sDiff']:.1f}" if p.get("sDiff") is not None else "\u2014"
        rows += f'''<tr>
      <td>\U0001F3C0 {esc(p["matchup"])}</td>
      <td><b>{esc(p["pick"])}</b> <span class="trend-label">sDiff {s_diff_str}</span></td>
      <td style="text-align:center">{result_badge(p["result"])}</td>
      <td style="text-align:center">{esc(p["final"])}</td>
    </tr>'''

    t = recap["tally"]
    p_val = t["p"]
    summary_str = f'<b>{t["w"]}-{t["l"]}{f"-{p_val}" if p_val else ""}</b>'
    units_color = "#10b981" if recap["units"] >= 0 else "#ef4444"
    units_str = f'<span style="color:{units_color}; font-weight:700">{recap["units"]:+.2f}u</span>'

    return f'''
    <div class="card" style="border-left:4px solid #f59e0b; margin-bottom:10px;">
      <div class="summaryTitle">\U0001F4CB Yesterday\'s Recap ({esc(recap["dateDisplay"])})</div>
      <table class="data">
        <thead><tr>
          <th>Game</th><th>Pick</th>
          <th style="text-align:center">Result</th>
          <th style="text-align:center">Final</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
      <div class="tiny" style="margin-top:6px">Spread: {summary_str} \u00b7 {units_str}</div>
    </div>'''


# Injury Helpers
def build_game_injury_adj(away_team, home_team, injury_report, player_mpg=None):
    away_injuries = get_key_injuries(injury_report, away_team, player_mpg)
    home_injuries = get_key_injuries(injury_report, home_team, player_mpg)
    return {"awayInjuries": away_injuries, "homeInjuries": home_injuries}


# --- Email HTML Builder ---

EMAIL_STYLES = '''
<style>
  body { font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif; margin:0; padding:0; background:#f3f4f6; }
  .wrap { max-width: 920px; margin: 0 auto; padding: 16px; }
  .h1 { font-size:20px; font-weight:800; color:#111827; }
  .sub { font-size:12px; color:#6b7280; }
  .card { background:#fff; border:1px solid #e5e7eb; border-radius:14px; padding:12px; box-shadow: 0 1px 3px rgba(0,0,0,.05); }
  .card .summaryTitle { font-size:12px; font-weight:800; color:#111827; letter-spacing:.02em; margin-bottom:8px; }
  .tiny { font-size:11px; color:#6b7280; line-height:1.35; }
  table.data { width:100%; border-collapse:collapse; }
  table.data th, table.data td { font-size:12px; padding:7px 6px; border-bottom:1px solid #eef2f7; }
  table.data th { text-align:left; color:#6b7280; font-weight:700; }
  .right { text-align:right; }
  .badge { display:inline-block; font-size:10px; font-weight:800; padding:3px 8px; border-radius:999px; border:1px solid #e5e7eb; white-space:nowrap; }
  .b-high { background:#ecfdf5; color:#047857; border-color:#a7f3d0; }
  .b-elite { background:#111827; color:#ffffff; border-color:#111827; }
  .b-low { background:#fef3c7; color:#92400e; border-color:#fde68a; }
  .pick-team { font-weight:800; color:#111827; }
  .pick-line { font-size:11px; color:#6b7280; }
  .no-picks { color:#6b7280; font-size:12px; padding:6px 0; }
  .trend-label { font-size:11px; color:#6b7280; }
  .l10-tally { margin-top:10px; font-size:13px; font-weight:700; color:#111827; }
  .win-text { color:#047857; }
  .loss-text { color:#b91c1c; }
  .section-label { font-size:10px; font-weight:800; text-transform:uppercase; letter-spacing:.1em; padding:10px 0 4px; opacity:.55; }
  .card-picks { border-left: 4px solid #6366f1; }
  .card-records { border-left: 4px solid #0ea5e9; }
  .card-games { border-left: 4px solid #f59e0b; }
  .card-trends { border-left: 4px solid #10b981; }
</style>'''


def win_rate_class(pct):
    try:
        n = float(pct)
    except (ValueError, TypeError):
        return ""
    return "win-text" if n >= 55 else ("loss-text" if n <= 50 else "")


def units_class(u):
    if not isinstance(u, (int, float)) or not math.isfinite(u):
        return ""
    return "win-text" if u > 0 else ("loss-text" if u < 0 else "")


def conf_badge(conf):
    c = str(conf or "").lower()
    cls = "b-elite" if c == "elite" else ("b-high" if c == "high" else "b-low")
    return f'<span class="badge {cls}">{esc(c.upper() if c else "N/A")}</span>'


def build_table(rows, cols):
    if not rows:
        return '<div class="no-picks">Not enough picks yet.</div>'
    header = "".join(f"<th>{esc(c['label'])}</th>" for c in cols)
    body = "".join(
        "<tr>" + "".join(f"<td>{c['render'](r)}</td>" for c in cols) + "</tr>"
        for r in rows
    )
    return f'<table class="data"><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table>'


def wlp_html(r):
    return f'<span class="win-text">{r["w"]}W</span>\u2013<span class="loss-text">{r["l"]}L</span>\u2013{r["p"]}P'


def pct_html(pct):
    return f'<span class="{win_rate_class(pct)}">{pct}%</span>'


def units_html(units):
    return f'<span class="{units_class(units)}">{fmt_units(units)}</span>'


def tally_l10(rows, pick_type="spread"):
    w = sum(1 for x in rows if x.get("result") == "WIN")
    l = sum(1 for x in rows if x.get("result") == "LOSS")
    p = sum(1 for x in rows if x.get("result") == "PUSH")
    if pick_type == "total":
        units = sum(total_pick_unit(x.get("date", ""), x.get("result", "")) for x in rows)
    else:
        units = calc_units(w, l)
    return {"w": w, "l": l, "p": p, "pct": win_pct(w, l), "units": units}


def build_record_table(title, bucket, card_class="card-records"):
    def row(label, b):
        return (f'<tr><td>{label}</td><td class="">{b["w"]}-{b["l"]}-{b["p"]}</td>'
                f'<td class="">{esc(b["pct"])}%</td><td class="">{esc(fmt_units(b["units"]))}</td>'
                f'<td class="">{b["played"]}</td></tr>')
    return f'''
    <div class="card {card_class}">
      <div class="summaryTitle">{esc(title)}</div>
      <table class="data">
        <thead><tr><th>Bucket</th><th class="">W-L-P</th><th class="">Win%</th><th class="">Units</th><th class="">Graded</th></tr></thead>
        <tbody>
          {row("All", bucket["all"])}
          {row("Elite", bucket["elite"]) if bucket.get("elite") else ""}
        </tbody>
      </table>
      <div class="tiny">Only graded picks (games with final scores + a non-PASS pick).</div>
    </div>'''


def build_split_table(title, subtitle, rows, card_class="card-records"):
    body = "".join(
        f'<tr><td>{r["emoji"]} {r["label"]}</td><td class="">{r["data"]["w"]}-{r["data"]["l"]}-{r["data"]["p"]}</td>'
        f'<td class="">{esc(r["data"]["pct"])}%</td><td class="">{esc(fmt_units(r["data"]["units"]))}</td>'
        f'<td class="">{r["data"]["played"]}</td></tr>'
        for r in rows
    )
    return f'''
    <div class="card {card_class}">
      <div class="summaryTitle">{esc(title)}</div>
      <table class="data">
        <thead><tr><th>Bucket</th><th class="">W-L-P</th><th class="">Win%</th><th class="">Units</th><th class="">Graded</th></tr></thead>
        <tbody>{body}</tbody>
      </table>
      <div class="tiny">{esc(subtitle)}</div>
    </div>'''


def build_combined_record_table(title, elite_bucket, split_rows, card_class="card-records"):
    def row(label, b):
        return (f'<tr><td>{label}</td><td class="">{b["w"]}-{b["l"]}-{b["p"]}</td>'
                f'<td class="">{esc(b["pct"])}%</td><td class="">{esc(fmt_units(b["units"]))}</td>'
                f'<td class="">{b["played"]}</td></tr>')

    split_body = "".join(row(f'{r["emoji"]} {r["label"]}', r["data"]) for r in split_rows)

    return f'''
    <div class="card {card_class}">
      <div class="summaryTitle">{esc(title)}</div>
      <table class="data">
        <thead><tr><th>Bucket</th><th class="">W-L-P</th><th class="">Win%</th><th class="">Flat</th><th class="">Graded</th></tr></thead>
        <tbody>
          {row("\U0001F512 Elite", elite_bucket)}
          {split_body}
        </tbody>
      </table>
      <div class="tiny">Only graded picks (games with final scores + a non-PASS pick).</div>
    </div>'''


def build_game_prob_table(games):
    filtered = [g for g in (games or []) if g.get("status") not in ("MISSING_ODDS", "SKIPPED")]
    if not filtered:
        return ""

    rows = ""
    for g in filtered:
        s_pick_display = esc(g["sPick"]) if g.get("sPick") and g["sPick"] != "PASS" else '<span style="color:#9ca3af">PASS</span>'
        o_pick_display = esc(g["oPick"]) if g.get("oPick") and g["oPick"] != "PASS" else '<span style="color:#9ca3af">PASS</span>'

        # Show vetoed pick with strikethrough and reason
        veto_html = ""
        reason_html = ""
        if g.get("lrVetoed"):
            reasons = g.get("lrReasons", [])
            reason_str = " \u00b7 ".join(reasons) if reasons else ""
            veto_html = (
                f'<div class="tiny" style="margin-top:2px;color:#dc2626">'
                f'\u274C <s>{esc(g["lrVetoed"])}</s> vetoed'
                f'</div>'
            )
            reason_html = (
                f'<div class="tiny" style="color:#dc2626">'
                f'{esc(reason_str) if reason_str else ""}'
                f'</div>'
            )

        p_cover_str = f'<b>{g["pCover"] * 100:.0f}%</b>' if g.get("pCover") is not None else '<span style="color:#9ca3af">\u2014</span>'
        p_ou_str = f'<b>{g["pOU"] * 100:.0f}%</b>' if g.get("pOU") is not None else '<span style="color:#9ca3af">\u2014</span>'

        p_home = f'{g["pHomeCover"] * 100:.0f}%' if g.get("pHomeCover") is not None else "\u2014"
        p_away = f'{g["pAwayCover"] * 100:.0f}%' if g.get("pAwayCover") is not None else "\u2014"
        p_over = f'{g["pOver"] * 100:.0f}%' if g.get("pOver") is not None else "\u2014"
        p_under = f'{g["pUnder"] * 100:.0f}%' if g.get("pUnder") is not None else "\u2014"

        s_conf_badge = f" {conf_badge(g.get('sConf'))}" if g.get("sPick") and g["sPick"] != "PASS" else ""
        o_conf_badge = f" {conf_badge(g.get('oConf'))}" if g.get("oPick") and g["oPick"] != "PASS" else ""

        margin = (("+" if g["margin"] >= 0 else "") + fmt_num(g["margin"], 1)) if isinstance(g.get("margin"), (int, float)) and math.isfinite(g["margin"]) else "\u2014"
        t_diff = (("+" if g["tDiff"] >= 0 else "") + fmt_num(g["tDiff"], 1)) if isinstance(g.get("tDiff"), (int, float)) and math.isfinite(g["tDiff"]) else "\u2014"

        rows += f'''<tr>
        <td style="font-weight:700">{esc(g["away"])} @ {esc(g["home"])}</td>
        <td>{s_pick_display}{s_conf_badge}<div class="tiny" style="margin-top:2px">Line {fmt_num(g.get("line"), 1)} \u00b7 proj {margin} \u00b7 sDiff {fmt_num(g.get("sDiff"), 1)}</div>{veto_html}</td>
        <td style="text-align:center">{p_cover_str}<div class="tiny">{p_away} away / {p_home} home</div></td>
        <td>{o_pick_display}{o_conf_badge}<div class="tiny" style="margin-top:2px">O/U {fmt_num(g.get("total"), 1)} \u00b7 diff {t_diff}</div></td>
        <td style="text-align:center">{p_ou_str}<div class="tiny">{p_over} over / {p_under} under</div></td>
        <td style="text-align:center">{reason_html}</td>
      </tr>'''

    return f'''<div class="card" style="border-left:4px solid #8b5cf6; margin-bottom:10px;">
    <div class="summaryTitle">\U0001F3AF Cover Probabilities \u2014 All Games</div>
    <table class="data">
      <thead><tr>
        <th>Game</th><th>Spread Pick</th><th style="text-align:center">P(Cover)</th>
        <th>Total Pick</th><th style="text-align:center">P(Hit)</th><th style="text-align:center">LR Reason</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
    <div class="tiny" style="margin-top:6px">P(Cover) / P(Hit) = probability the picked side wins. Directional % shown below.</div>
  </div>'''


def build_email_html(run, summary_obj, last10, last10_totals, weekly_spread, weekly_total, rolling_spread, rolling_total, team_records, calib_rows, yesterday_recap):
    s = summary_obj or compute_summary_obj({"runs": []})
    calib_html = build_calibration_html(calib_rows) if calib_rows else ""

    # Today's picks
    def build_picks_list(games, pick_type):
        is_spread = pick_type == "spread"
        if not is_spread and run.get("date", "") >= TOTAL_PICKS_END_DATE:
            return '<div class="no-picks">Totals picks discontinued.</div>'
        filtered = [g for g in games
                    if g.get("status") not in ("MISSING_ODDS", "SKIPPED")
                    and ((g.get("sPick") if is_spread else g.get("oPick")) or "") not in ("", "PASS")
                    and is_actionable(g.get("sConf") if is_spread else g.get("oConf"))]
        filtered.sort(key=lambda a: (a.get("pCover", 0) if is_spread else a.get("pOU", 0)) or 0, reverse=True)

        if not filtered:
            return f'<div class="no-picks">No actionable {pick_type} picks today.</div>'

        parts = []
        for g in filtered:
            if is_spread:
                p_str = f" \u00b7 P={g['pCover'] * 100:.0f}%" if g.get("pCover") else ""
                proj_margin = round((g["hS"] - g["aS"]) * 10) / 10
                fav_team = g["home"] if proj_margin >= 0 else g["away"]

                parts.append(
                    f'<div style="padding:6px 0; border-bottom:1px dashed #eef2f7;">\U0001F3C0 <span class="pick-team">{esc(g["sPick"])}</span>'
                    f' <span class="trend-label">proj {esc(fav_team)} by {fmt_num(abs(proj_margin), 1)} \u00b7 sDiff {fmt_num(g.get("sDiff"), 1)}{p_str}</span>'
                    f' {conf_badge(g.get("sConf"))}</div>'
                )
            else:
                arrow = "\u2B06\uFE0F" if g.get("oPick") == "OVER" else "\u2B07\uFE0F"
                p_str = f" \u00b7 P={g['pOU'] * 100:.0f}%" if g.get("pOU") else ""
                clean_total = g["total"] + g["tDiff"] if isinstance(g.get("tDiff"), (int, float)) and isinstance(g.get("total"), (int, float)) else g.get("pT")
                parts.append(
                    f'<div style="padding:6px 0; border-bottom:1px dashed #eef2f7;">{arrow}'
                    f' <span class="pick-team">{esc(g["oPick"])} {fmt_num(g.get("total"), 1)}</span>'
                    f' <span class="pick-line">{esc(g["away"])} @ {esc(g["home"])}</span>'
                    f' <span class="trend-label">proj {fmt_num(clean_total, 1)} \u00b7 edge {fmt_num(abs(g.get("tDiff", 0)), 1)}{p_str}</span>'
                    f' {conf_badge(g.get("oConf"))}</div>'
                )
        return "".join(parts)

    # Last 10 cards
    def build_last10_card(title, picks, pick_type):
        rows_data = [dict(p, dateDisp=to_display_date(p["date"])) for p in picks]
        t = tally_l10(rows_data, pick_type)
        pick_col = (
            {"label": "Pick", "render": lambda r: f'<span class="pick-team">{esc(r["pick"])} {esc(str(r.get("total", "")))}</span>'}
            if pick_type == "total" else
            {"label": "Pick", "render": lambda r: f'<span class="pick-team">{esc(r["pick"])}</span>'}
        )
        return (
            f'<div class="card card-trends"><div class="summaryTitle">{title}</div>' +
            build_table(rows_data, [
                {"label": "Date", "render": lambda r: esc(r["dateDisp"])},
                {"label": "Matchup", "render": lambda r: esc(r["matchup"])},
                pick_col,
                {"label": "Conf", "render": lambda r: conf_badge(r.get("conf"))},
                {"label": "Result", "render": lambda r: esc(r.get("result", ""))},
                {"label": "Final", "render": lambda r: esc(r.get("final", ""))},
            ]) +
            f'<div class="l10-tally">Last 10: {wlp_html(t)} \u00b7 {pct_html(t["pct"])} \u00b7 {units_html(t["units"])}</div></div>'
        )

    # Weekly + Rolling cards
    def build_weekly_card(title, rows_data):
        return (
            f'<div class="card card-trends"><div class="summaryTitle">{title}</div>' +
            build_table(rows_data, [
                {"label": "Week of", "render": lambda r: esc(r["week"])},
                {"label": "W-L-P", "render": wlp_html},
                {"label": "Win%", "render": lambda r: pct_html(r["pct"])},
                {"label": "Flat", "render": lambda r: units_html(r["units"])},
            ]) + '</div>'
        )

    def build_rolling_card(title, rows_data):
        window_size = re.search(r"#\d+\u2013(\d+)", rows_data[0]["label"]).group(1) if rows_data else "?"
        full_title = title.replace("{n}", str(window_size))
        return (
            f'<div class="card card-trends"><div class="summaryTitle">{full_title}</div>'
            f'<div class="tiny" style="margin-bottom:6px">Green = above break-even (52.4%) \u00b7 Red = below \u00b7 Units at -110</div>' +
            build_table(rows_data, [
                {"label": "Window", "render": lambda r: esc(r["label"])},
                {"label": "W-L-P", "render": wlp_html},
                {"label": "Win%", "render": lambda r: pct_html(r["pct"])},
                {"label": "Flat", "render": lambda r: units_html(r["units"])},
                {"label": "Dates", "render": lambda r: f'<span class="trend-label">{esc(r["startDate"])} \u2192 {esc(r["endDate"])}</span>'},
            ]) + '</div>'
        )

    # Layout helpers
    def row2col(left, right):
        return f'''<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:10px;">
      <tr>
        <td width="50%" valign="top" style="padding-right:5px;">{left}</td>
        <td width="50%" valign="top" style="padding-left:5px;">{right}</td>
      </tr>
    </table>'''

    def row_full(content):
        return f'<div style="margin-bottom:10px;">{content}</div>'

    def section_label(text):
        return f'<div class="section-label">{esc(text)}</div>'

    # Helper for pCover/pOU in f-strings
    def _p_cover_str(g):
        pc = g.get("pCover")
        return f" . P={pc*100:.0f}%" if pc else ""

    def _p_ou_str(g):
        po = g.get("pOU")
        return f" . P={po*100:.0f}%" if po else ""

    # Build game cards
    game_cards = []
    for g in run.get("games", []):
        is_skipped = g.get("status") in ("MISSING_ODDS", "SKIPPED")

        if not is_skipped and g.get("sPick") and g["sPick"] != "PASS":
            proj_margin = round((g["hS"] - g["aS"]) * 10) / 10
            fav_team = g["home"] if proj_margin >= 0 else g["away"]

            gc_prob_str = _p_cover_str(g)

            spread_pick = (
                f'<div><span class="pick-team">{esc(g["sPick"])}</span> {conf_badge(g.get("sConf"))}'
                f' <span class="trend-label">proj {esc(fav_team)} by {fmt_num(abs(proj_margin), 1)} \u00b7 sDiff {fmt_num(g.get("sDiff"), 1)}'
                f'{gc_prob_str}</span></div>'
            )
        else:
            spread_pick = f'<div class="tiny">Spread: <span class="trend-label">{esc(g.get("status", "PASS") if is_skipped else "PASS")}</span></div>'

        if not is_skipped and g.get("oPick") and g["oPick"] != "PASS":
            clean_total = g["total"] + g["tDiff"] if isinstance(g.get("tDiff"), (int, float)) and isinstance(g.get("total"), (int, float)) else g.get("pT")
            total_pick = (
                f'<div class="tiny">Total: <span class="pick-team">{esc(g["oPick"])} {fmt_num(g.get("total"), 1)}</span>'
                f' {conf_badge(g.get("oConf"))}'
                f' <span class="trend-label">proj {fmt_num(clean_total, 1)} \u00b7 edge {fmt_num(abs(g.get("tDiff", 0)), 1)}'
                f'{_p_ou_str(g)}</span></div>'
            )
        else:
            total_pick = f'<div class="tiny">Total: <span class="trend-label">{esc(g.get("status", "PASS") if is_skipped else "PASS")}</span></div>'

        proj_line = ""
        if not is_skipped and isinstance(g.get("aS"), (int, float)) and isinstance(g.get("hS"), (int, float)):
            clean_total = g["total"] + g["tDiff"] if isinstance(g.get("tDiff"), (int, float)) and isinstance(g.get("total"), (int, float)) else g.get("pT")
            proj_line = (
                f'<div class="tiny" style="margin-top:4px">\U0001F4D0 Proj: <b>{esc(g["away"])} {fmt_num(g["aS"], 1)}</b> \u2013 <b>{esc(g["home"])} {fmt_num(g["hS"], 1)}</b></div>'
                f'<div class="tiny">\U0001F4D0 Total: <b>{fmt_num(clean_total, 1)}</b> <span class="trend-label">(line {fmt_num(g.get("total"), 1)})</span></div>'
            )

        trends_html = ""
        if g.get("trends"):
            trends_html = (
                f'<div class="tiny" style="margin-top:4px">'
                f'<div><b>{esc(g["away"])}:</b> {esc(g["trends"].get("away", "\u2014"))}</div>'
                f'<div><b>{esc(g["home"])}:</b> {esc(g["trends"].get("home", "\u2014"))}</div></div>'
            )

        injury_html = ""
        if g.get("injuryNote"):
            sides = g["injuryNote"].split(" | ")
            injury_html = '<div class="tiny" style="margin-top:4px">' + "".join(f'<div style="margin-left:0">\U0001F3E5 {esc(s)}</div>' for s in sides) + '</div>'

        b2b_html = f'<div class="tiny" style="margin-top:4px">\U0001F504 {esc(g["b2bNote"])}</div>' if g.get("b2bNote") else ""

        score_line = ""
        if isinstance(g.get("awayScore"), (int, float)) and isinstance(g.get("homeScore"), (int, float)):
            score_line = f'<div class="tiny" style="margin-top:4px">Final: <b>{esc(str(g["awayScore"]))}-{esc(str(g["homeScore"]))}</b></div>'

        game_cards.append(
            f'<div class="card card-games">'
            f'<div class="summaryTitle">{esc(g.get("away", ""))} @ {esc(g.get("home", ""))} <span class="tiny">Line {fmt_num(g.get("line"), 1)} \u00b7 Total {fmt_num(g.get("total"), 1)}</span></div>'
            f'{spread_pick}{total_pick}{proj_line}{injury_html}{b2b_html}{score_line}{trends_html}'
            f'</div>'
        )

    # Pair game cards into 2-col rows
    games_html = ""
    for i in range(0, len(game_cards), 2):
        if i + 1 < len(game_cards):
            games_html += row2col(game_cards[i], game_cards[i + 1])
        else:
            games_html += row_full(game_cards[i])

    # Assemble full HTML
    import pytz
    timestamp = datetime.now(pytz.timezone(TIMEZONE)).strftime("%m/%d/%Y, %I:%M:%S %p")
    recap_html = build_recap_html(yesterday_recap) if yesterday_recap else ""

    return f'''<html><head>{EMAIL_STYLES}</head><body>
    <div class="wrap">
      <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:10px;">
        <tr>
          <td>
            <div class="h1">NBA Picks \u2014 {esc(run.get("dateDisplay", ""))}</div>
            <div class="sub">High + Elite only (no medium). Units at -110. Times in {TIMEZONE}.</div>
          </td>
          <td style="text-align:right;" valign="top"><div class="sub">{esc(timestamp)}</div></td>
        </tr>
      </table>

      {row_full(recap_html) if recap_html else ""}

      {row_full(f'<div class="card card-picks"><div class="summaryTitle">\U0001F525 Today\'s Spread Picks (Actionable)</div>{build_picks_list(run.get("games", []), "spread")}</div>')}
      {row_full(build_combined_record_table("\U0001F4CA Spread (ATS)", s["spread"]["elite"], [
          {"label": "Favorites", "emoji": "\u2B50", "data": s["favdog"]["fav"]},
          {"label": "Underdogs", "emoji": "\U0001F436", "data": s["favdog"]["dog"]},
        ]))}

      {section_label("Games")}
      {games_html}

      {section_label("Cover Probabilities")}
      {row_full(build_game_prob_table(run.get("games", [])))}

      {section_label("Spread Trends")}
      {row_full(build_last10_card("\U0001F9FE Last 10 Spread", last10 or [], "spread"))}
      {row_full(build_weekly_card("\U0001F4C5 Weekly Spread", weekly_spread or []))}
      {row_full(build_rolling_card("\U0001F4CA Rolling {{n}}-Pick Groups (Spread)", rolling_spread or []))}

      {section_label("Team Records")}
      {row_full(build_team_record_table(team_records or {}))}

      {f'{section_label("Model Calibration")}{row_full(f"""<div class="card"><div class="summaryTitle">\\U0001F4CA P(cover) Calibration</div><div class="tiny" style="margin-bottom:6px">Expected vs actual win rate by probability bucket. D shows calibration error.</div>{calib_html}</div>""")}' if calib_html else ""}
    </div>
  </body></html>'''


# --- Text Email Builder ---

def build_text_email(run, store):
    lines = [f"NBA PICKS \u2014 {run.get('dateDisplay', '')}", "", "TODAY (Actionable only)", ""]

    for g in run.get("games", []):
        if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
            lines.append(f"{g['away']} @ {g['home']} | {g['status']} | {g.get('note', '')}")
            lines.append("")
            continue

        lines.append(f"{g['away']} @ {g['home']} | Line: {g['home']} {'+'if g.get('line',0) > 0 else ''}{fmt_num(g.get('line'), 1)} | Total: {fmt_num(g.get('total'), 1)}")

        if g.get("sPick") and g["sPick"] != "PASS":
            proj_margin = round((g["hS"] - g["aS"]) * 10) / 10
            fav_team = g["home"] if proj_margin >= 0 else g["away"]
            p_str = f" | P={fmt_num(g['pCover'] * 100, 0)}%" if g.get("pCover") else ""
            lines.append(f"  Spread: {g['sPick']} ({str(g.get('sConf', '')).upper()}) | proj {fav_team} by {fmt_num(abs(proj_margin), 1)} | edge {fmt_num(g.get('sDiff'), 1)}{p_str}")
        else:
            lines.append("  Spread: PASS")

        if g.get("oPick") and g["oPick"] != "PASS":
            clean_total = g["total"] + g["tDiff"] if isinstance(g.get("tDiff"), (int, float)) and isinstance(g.get("total"), (int, float)) else g.get("pT")
            lines.append(f"  Total:  {g['oPick']} {fmt_num(g.get('total'), 1)} ({str(g.get('oConf', '')).upper()}) | proj {fmt_num(clean_total, 1)} | edge {fmt_num(abs(g.get('tDiff', 0)), 1)}")
        else:
            lines.append("  Total:  PASS")

        if g.get("injuryNote"):
            lines.append(f"  Injury: {g['injuryNote']}")
        if g.get("trends"):
            lines.append(f"  Trends Away: {g['trends'].get('away', '\u2014')}")
            lines.append(f"  Trends Home: {g['trends'].get('home', '\u2014')}")
        lines.append("")

    lines.extend(["\u2014", compute_summary_text(store), "", last10_text(store, "spread"), "", last10_text(store, "total")])
    return "\n".join(lines)


# --- Main Pipeline ---

def main(subject_label="[PY]"):
    date = today_central_yyyymmdd()
    date_display = to_display_date(date)
    store = load_store()
    defaults = load_defaults()

    print(f"\n\u2550\u2550 NBA Picks Pipeline \u2014 {date_display} \u2550\u2550\n")

    # 1. Grade recent days
    days_to_grade = set()
    for d in range(1, 6):
        days_to_grade.add(yyyymmdd_from_central_offset(d))
    for r in store.get("runs", []):
        has_ungraded = any(
            g.get("status") not in ("MISSING_ODDS", "SKIPPED") and
            not isinstance(g.get("homeScore"), (int, float)) and
            ((g.get("sPick") and g["sPick"] != "PASS") or (g.get("oPick") and g["oPick"] != "PASS"))
            for g in r.get("games", [])
        )
        if has_ungraded:
            days_to_grade.add(r["date"])

    yesterday = yyyymmdd_from_central_offset(1)
    for d in days_to_grade:
        grade_date_in_store(store, d)

    # 2. Fetch today's data
    season_type = get_season_type(date)
    espn_type = get_espn_season_type(date)
    print(f"[1/7] Fetching stats, odds, trends, injuries, player data... [{season_type}]")

    # Try JS model's stats cache first to avoid duplicate NBA.com API calls
    enhanced_stats = None
    js_cache = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "jsNBA", "data", "stats_cache", f"{date}.json")
    if os.path.exists(js_cache):
        try:
            with open(js_cache, "r", encoding="utf-8") as f:
                enhanced_stats = json.load(f)
            if enhanced_stats.get("season") and len(enhanced_stats["season"]) >= 20:
                print(f"  [nba_stats] Using JS model cache ({len(enhanced_stats['season'])} teams)")
            else:
                enhanced_stats = None
        except Exception:
            enhanced_stats = None
    if not enhanced_stats:
        enhanced_stats = fetch_nba_stats_enhanced(date, season_type=season_type)
    odds = fetch_todays_odds()

    # Detect started/finished games via ESPN scoreboard
    try:
        _espn_sb = fetch_scoreboard(date)
    except Exception:
        _espn_sb = None
    _espn_statuses = {}  # key: (norm_away, norm_home) -> status_name
    for _ev in (_espn_sb or {}).get("events", []):
        _comp = (_ev.get("competitions") or [None])[0]
        if not _comp:
            continue
        _comps = _comp.get("competitors", [])
        _ac = next((c for c in _comps if c.get("homeAway") == "away"), None)
        _hc = next((c for c in _comps if c.get("homeAway") == "home"), None)
        if _ac and _hc:
            _a = (_ac.get("team") or {}).get("displayName", "")
            _h = (_hc.get("team") or {}).get("displayName", "")
            _st = ((_comp.get("status") or {}).get("type") or {}).get("name", "")
            _espn_statuses[(_a, _h)] = _st

    def _game_started_or_finished(away, home):
        for (ea, eh), st in _espn_statuses.items():
            if match_team(away, ea) and match_team(home, eh):
                if st not in ("STATUS_SCHEDULED", "STATUS_DELAYED", "STATUS_POSTPONED", "STATUS_CANCELED", ""):
                    return True
        return False

    # Load previous run for today (if any) to preserve started/finished games
    _prev_run = next((r for r in store.get("runs", []) if r.get("date") == date), None)
    _prev_games = {(g.get("away", ""), g.get("home", "")): g for g in (_prev_run or {}).get("games", [])} if _prev_run else {}

    def _find_prev_game(away, home):
        for (pa, ph), pg in _prev_games.items():
            if match_team(away, pa) and match_team(home, ph):
                return pg
        return None

    ats = fetch_ats_trends()
    ou = fetch_ou_trends()
    # Try JS model's injury cache first
    injury_data = None
    player_advanced = None
    _scripts = os.path.dirname(os.path.abspath(__file__))
    _inj_cache = os.path.join(_scripts, "..", "..", "jsNBA", "data", "injury_cache", f"{date}.json")
    if os.path.exists(_inj_cache):
        try:
            with open(_inj_cache, "r", encoding="utf-8") as _f:
                _cached = json.load(_f)
            injury_data = _cached.get("injuryData", {"report": {}, "playerMPG": {}})
            player_advanced = _cached.get("playerAdvanced", {})
            print(f"  [cache] Using JS injury cache for {date}")
        except Exception:
            injury_data = None
    if injury_data is None:
        import time as _time
        _time.sleep(5)
        try:
            injury_data = fetch_injury_data(None, season_type=season_type, espn_type=espn_type)
        except Exception as e:
            print(f"  Warning: Injury fetch failed: {e}")
            injury_data = {"report": {}, "playerMPG": {}}
    if player_advanced is None:
        import time as _time
        _time.sleep(5)
        try:
            player_advanced = fetch_player_advanced(season_type=season_type)
        except Exception as e:
            print(f"  Warning: Player advanced fetch failed: {e}")
            player_advanced = {}
    try:
        b2b_teams = detect_b2b()
    except Exception:
        b2b_teams = set()

    base_w = store.get("weights") or defaults["DEFAULT_W"]
    base_w_var = store.get("weightsVar") or defaults["DEFAULT_W_VAR"]

    # Blend season + last 10 games for recent form
    stats = blend_base(enhanced_stats["season"], enhanced_stats.get("last10"), base_w.get("recentWeight", 0.35))

    # Cache stats to disk for recalculate.py
    try:
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        cache_dir = os.path.join(scripts_dir, "..", "data", "stats_cache")
        os.makedirs(cache_dir, exist_ok=True)
        with open(os.path.join(cache_dir, date + ".json"), "w") as f:
            json.dump(enhanced_stats, f)
    except Exception:
        pass

    # 3. Lineup-adjusted stats + B2B rest + Kalman state
    print("[2/7] Applying lineup adjustments + B2B rest + Kalman filter...")

    lineup_stats = adjust_team_stats(stats, injury_data.get("report", {}), injury_data.get("playerMPG", {}), player_advanced, odds)
    result = apply_b2b_adjustment(lineup_stats, b2b_teams, odds)
    adjusted_stats = result["adjusted"]
    b2b_notes = result.get("b2bNotes", {})
    a = get_avgs(adjusted_stats)

    # Cache per-team deltas
    STAT_KEYS = ["OFF", "DEF", "TS", "TO", "ORR", "PACE"]
    def compute_team_delta(team):
        base = stats.get(team)
        adj = adjusted_stats.get(team)
        if not base or not adj or base is adj:
            return None
        d = {}
        any_diff = False
        for k in STAT_KEYS:
            diff = (adj.get(k, 0) or 0) - (base.get(k, 0) or 0)
            if abs(diff) > 0.0001:
                d[k] = round(diff * 10000) / 10000
                any_diff = True
        return d if any_diff else None

    kalman_state = load_kalman_state()
    if not kalman_state.get("teams"):
        kalman_state = initialize_kalman(stats)

    # Feed newly graded games into Kalman
    newly_graded = []
    for r in (store.get("runs") or [])[-14:]:
        if r.get("date") == date:
            continue
        for g in r.get("games", []):
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue
            if not isinstance(g.get("homeScore"), (int, float)) or not isinstance(g.get("awayScore"), (int, float)):
                continue
            if not isinstance(g.get("hS"), (int, float)) or not isinstance(g.get("aS"), (int, float)):
                continue
            g["_kalmanDate"] = r["date"]
            newly_graded.append(g)
    batch_update(kalman_state, newly_graded)

    # Apply drift AFTER learning
    apply_daily_drift(kalman_state, date)

    # 3b. Self-tune weights (with recency weighting, matching backfill logic)
    def _recency_weight(days_ago):
        if days_ago <= 15: return 1.0
        if days_ago <= 30: return 0.75
        if days_ago <= 45: return 0.5
        return 0.25

    TUNE_WINDOW = 60
    recent_graded = []
    for r in store.get("runs", []):
        rd = r.get("date", "")
        if not rd or rd >= date:
            continue
        try:
            days_ago = (datetime.strptime(date, "%Y%m%d") - datetime.strptime(rd, "%Y%m%d")).days
        except Exception:
            continue
        if days_ago < 1 or days_ago > TUNE_WINDOW:
            continue
        for g in r.get("games", []):
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue
            if not isinstance(g.get("homeScore"), (int, float)) or not isinstance(g.get("awayScore"), (int, float)):
                continue
            if not g.get("_features"):
                continue
            g["_recencyWeight"] = _recency_weight(days_ago)
            recent_graded.append(g)
    if recent_graded and store.get("lastTuneDate") != date:
        tuned = tune_weights(base_w, base_w_var, recent_graded)
        base_w = tuned["W"]
        base_w_var = tuned["W_var"]
        store["weights"] = tuned["W"]
        store["weightsVar"] = tuned["W_var"]
        store["lastTuneDate"] = date
        print(f"[3b] Weights tuned on {len(recent_graded)} graded games from last {TUNE_WINDOW} days")
    elif store.get("lastTuneDate") == date:
        print("[3b] Weights already tuned today -- skipping")

    # 3c. Compute dynamic residualVar
    dynamic_residual_var = compute_residual_var(store.get("runs", []))
    store["residualVar"] = dynamic_residual_var

    # 3d. Load / train LR confirmation model
    print("[3d] Loading LR confirmation model...")
    lr_bundle = load_or_train_lr(store)
    lr_histories = build_team_histories(store)
    if lr_bundle:
        print(f"  LR model ready ({lr_bundle.get('n_train', '?')} training games)")
    else:
        print("  LR model not ready (insufficient training data)")

    # 4. Analyze each game
    _skipped_live = 0
    print(f"[3/7] Analyzing {len(odds)} games...")
    games = []
    for g in odds:
        # Preserve previous picks for games already started or finished
        if _game_started_or_finished(g.get("away", ""), g.get("home", "")):
            prev = _find_prev_game(g.get("away", ""), g.get("home", ""))
            if prev:
                games.append(prev)
            else:
                games.append(dict(g, status="STARTED"))
            _skipped_live += 1
            continue

        if not isinstance(g.get("line"), (int, float)) or not isinstance(g.get("total"), (int, float)):
            games.append(dict(g, status="MISSING_ODDS"))
            continue

        try:
            injury_adj = build_game_injury_adj(g["away"], g["home"], injury_data.get("report", {}), injury_data.get("playerMPG"))
        except Exception:
            injury_adj = None

        # Per-game: blend home team toward their home splits, away toward road splits
        game_stats = blend_for_game(
            adjusted_stats, enhanced_stats.get("home"), enhanced_stats.get("away"),
            g["home"], g["away"], base_w.get("locationWeight", 0.25)
        )
        game_avgs = get_avgs(game_stats)

        r = analyze_game(g, game_stats, game_avgs, base_w, injury_adj, kalman_state, base_w_var, dynamic_residual_var)

        if not r:
            games.append(dict(g, status="SKIPPED"))
            continue

        # 4b. LR confirmation / veto (only when there's an actual pick)
        if r.get("sPick") and r["sPick"] != "PASS":
            home_hist = lr_histories.get(r.get("home"), [])
            away_hist = lr_histories.get(r.get("away"), [])
            lr_features = extract_lr_features(home_hist, away_hist, g, home_hist, away_hist)
            lr_result = predict_lr(lr_bundle, lr_features)
            r["lrProb"] = lr_result["lr_prob"]
            r["lrVerdict"] = lr_result["lr_verdict"]

            if lr_result["lr_verdict"] == "VETO":
                r["lrVetoed"] = r["sPick"]
                r["lrReasons"] = lr_result.get("lr_reasons", [])
                r["sPick"] = "PASS"
                r["sConf"] = "vetoed"

        # Cache lineup/B2B deltas
        away_delta = compute_team_delta(g["away"])
        home_delta = compute_team_delta(g["home"])
        if away_delta or home_delta:
            r["_adjDeltas"] = {}
            if away_delta:
                r["_adjDeltas"]["away"] = away_delta
            if home_delta:
                r["_adjDeltas"]["home"] = home_delta

        games.append(r)

    # Merge in previous-run games that are no longer in the odds feed
    if _prev_games:
        _seen = set()
        for g in games:
            _seen.add((g.get("away", ""), g.get("home", "")))
        for (pa, ph), pg in _prev_games.items():
            already = any(match_team(pa, sa) and match_team(ph, sh) for sa, sh in _seen)
            if not already:
                games.append(pg)
                _skipped_live += 1

    if _skipped_live:
        print(f"  [{_skipped_live} game(s) already started/finished -- preserved from previous run]")

    # 5. Attach trends
    for g in games:
        if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
            continue
        ats_key_a = resolve_key(ats, g["away"])
        ats_key_h = resolve_key(ats, g["home"])
        ou_key_a = resolve_key(ou, g["away"])
        ou_key_h = resolve_key(ou, g["home"])
        g["trends"] = {
            "away": join_trends(g["away"], ats.get(ats_key_a) if ats_key_a else None, ou.get(ou_key_a) if ou_key_a else None),
            "home": join_trends(g["home"], ats.get(ats_key_h) if ats_key_h else None, ou.get(ou_key_h) if ou_key_h else None),
        }

        away_b2b = b2b_notes.get(g["away"])
        home_b2b = b2b_notes.get(g["home"])
        if away_b2b or home_b2b:
            parts = []
            if away_b2b:
                parts.append(f"{g['away']}: {away_b2b}")
            if home_b2b:
                parts.append(f"{g['home']}: {home_b2b}")
            g["b2bNote"] = " | ".join(parts)

    # 6. Build run record
    print("[4/7] Saving...")
    run = {"date": date, "dateDisplay": date_display, "burnIn": False, "weightsUsed": base_w, "weightsNext": base_w, "games": games, "summaryText": ""}

    # 7. Save state
    print("[5/7] Saving...")
    run["summaryText"] = compute_summary_text(store)
    upsert_run(store, run)
    save_store(store)

    prune_processed_games(kalman_state, 30)
    save_kalman_state(kalman_state)

    # 8. Compute trend data
    summary_obj = compute_summary_obj(store)
    l10 = compute_last10(store, "spread")
    l10t = compute_last10(store, "total")
    weekly_spread = compute_weekly_breakdown(store, "spread")
    weekly_total = compute_weekly_breakdown(store, "total")
    rolling_spread = compute_rolling_windows(store, "spread")
    rolling_total = compute_rolling_windows(store, "total")
    team_records_data = compute_team_records(store)
    calib_rows = build_calibration_table(store)
    yesterday_recap = compute_yesterday_recap(store, yesterday)

    # 9. Send
    print("[6/7] Sending email...")
    html = build_email_html(run, summary_obj, l10, l10t, weekly_spread, weekly_total, rolling_spread, rolling_total, team_records_data, calib_rows, yesterday_recap)
    text = build_text_email(run, store)
    subject = f"{subject_label} NBA Picks {run['dateDisplay']} (Actionable)"

    send_email(subject, text, html)

    # 10. Summary
    print(f"\nDone: {subject}")


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        import traceback
        traceback.print_exc()
        sys.exit(1)

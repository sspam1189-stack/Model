#!/usr/bin/env python3
# scripts/run_daily.py
# Daily NBA picks pipeline:
#   1. Grade yesterday's picks against final scores
#   2. Fetch today's stats, odds, trends, injuries
#   3. Analyze each game via model engine
#   4. Save store and output results

import json
import math
import os
import re
import sys
from datetime import datetime, timedelta

from dotenv import load_dotenv
load_dotenv()

from sources.nba_stats import fetch_nba_stats, fetch_nba_stats_enhanced
from sources.blend_stats import blend_base, blend_for_game
from sources.odds_fanduel import fetch_fanduel_nba_odds
from sources.odds_theoddsapi import fetch_todays_odds
from sources.espn_scoreboard import fetch_scoreboard, extract_final_scores
from sources.teamrankings_trends import fetch_ats_trends, fetch_ou_trends
from sources.injuries import fetch_injury_data, get_key_injuries, fetch_out_for_season
from sources.lineup_adjust import fetch_player_advanced, adjust_team_stats
from sources.rest_detect import detect_b2b, apply_b2b_adjustment
from sources.h2h_matchup import fetch_h2h_matchups
from sources.season_type import get_season_type, get_espn_season_type, is_playoffs, PLAYOFF_START
from model_engine import load_defaults, get_avgs, analyze_game
from store import load_store, save_store, upsert_run
from self_tune import tune_weights, compute_residual_var
from kalman_state import (
    load_kalman_state, save_kalman_state, initialize_kalman,
    apply_daily_drift, batch_update, kalman_summary, prune_processed_games,
)
from calibration import build_calibration_table, build_calibration_html


# ---- Constants ----
TIMEZONE = "America/Chicago"
CONF_ACTIONABLE = ["high", "elite"]
UNIT_LOSS = -1.1
TOTAL_HALF_UNIT_START = "2026-03-07"
TOTAL_PICKS_END_DATE = "2026-03-09"

# ---- Utility Helpers ----

def esc(s):
    return (str(s if s is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))

def fmt_num(x, digits=1):
    if isinstance(x, (int, float)) and math.isfinite(x):
        return f"{x:.{digits}f}"
    return "n/a"

def calc_units(w, l):
    return w + l * UNIT_LOSS

def compute_empirical_playoff_hca(history_path=None):
    """Compute avg home margin from playoff games in history.json.
    Returns float or None if <10 games available."""
    try:
        if history_path is None:
            history_path = os.path.join(os.path.dirname(__file__), "..", "data", "history.json")
        with open(history_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        runs = data.get("runs", []) if isinstance(data, dict) else []
        total, n = 0.0, 0
        for r in runs:
            if (r.get("date") or "") < PLAYOFF_START:
                continue
            for g in r.get("games", []):
                hs, as_ = g.get("homeScore"), g.get("awayScore")
                if (isinstance(hs, (int, float)) and isinstance(as_, (int, float))
                        and math.isfinite(hs) and math.isfinite(as_)):
                    total += hs - as_
                    n += 1
        if n < 10:
            return None
        return total / n
    except Exception:
        return None

def total_pick_unit(date, result):
    half_unit = date >= TOTAL_HALF_UNIT_START
    win = 0.5 if half_unit else 1.0
    loss = -0.55 if half_unit else UNIT_LOSS
    if result == "WIN": return win
    if result == "LOSS": return loss
    return 0

def fmt_units(u):
    if not isinstance(u, (int, float)) or not math.isfinite(u):
        return "\u2014"
    return f"+{u:.1f}u" if u >= 0 else f"{u:.1f}u"

def win_pct(w, l):
    total = w + l
    return f"{(w / total * 100):.1f}" if total > 0 else "n/a"

def is_actionable(conf):
    return str(conf).lower() in CONF_ACTIONABLE

# ---- Team Name Resolution ----

def norm_key(s):
    s = str(s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()

MASCOTS = {"lakers","clippers","warriors","thunder","pelicans","knicks","spurs",
    "76ers","sixers","timberwolves","grizzlies","hornets","pacers",
    "wizards","magic","heat","hawks","bulls","pistons","cavaliers",
    "raptors","nets","celtics","bucks","nuggets","jazz","suns",
    "kings","mavericks","rockets","trail blazers"}

TEAM_ALIASES = {
    "la lakers":"los angeles lakers","l a lakers":"los angeles lakers",
    "la clippers":"los angeles clippers","l a clippers":"los angeles clippers",
    "okc thunder":"oklahoma city thunder","okc":"oklahoma city thunder",
    "oklahoma city":"oklahoma city thunder","okla city":"oklahoma city thunder",
    "gs warriors":"golden state warriors","gsw":"golden state warriors",
    "golden state":"golden state warriors",
    "ny knicks":"new york knicks","nyk":"new york knicks","new york":"new york knicks",
    "sa spurs":"san antonio spurs","san antonio":"san antonio spurs",
    "no pelicans":"new orleans pelicans","new orleans":"new orleans pelicans",
    "phx suns":"phoenix suns","philly 76ers":"philadelphia 76ers",
    "mn timberwolves":"minnesota timberwolves","por trail blazers":"portland trail blazers",
}

def extract_mascot(normalized):
    words = normalized.split(" ")
    if words[-1] in MASCOTS: return words[-1]
    if len(words) >= 2:
        last_two = " ".join(words[-2:])
        if last_two in MASCOTS: return last_two
    return None

def resolve_key(obj, team_name):
    if not obj or not team_name: return None
    if team_name in obj: return team_name
    wanted = norm_key(team_name)
    aliased = TEAM_ALIASES.get(wanted, wanted)
    for k in obj:
        nk = norm_key(k)
        if nk == wanted or nk == aliased: return k
        if nk.startswith(wanted) or wanted in nk or nk in wanted: return k
        if aliased != wanted and (aliased in nk or nk in aliased): return k
    for alias_key, alias_val in TEAM_ALIASES.items():
        if norm_key(alias_val) == wanted or norm_key(alias_val) == aliased:
            for k in obj:
                if norm_key(k) == alias_key: return k
    wanted_mascot = extract_mascot(wanted) or extract_mascot(aliased)
    if wanted_mascot:
        for k in obj:
            km = extract_mascot(norm_key(k))
            if km and km == wanted_mascot: return k
    return None

def match_team(a, b):
    if a == b: return True
    na, nb = norm_key(a), norm_key(b)
    if na == nb: return True
    if na in nb or nb in na: return True
    ma, mb = extract_mascot(na), extract_mascot(nb)
    if ma and mb and ma == mb: return True
    return False

# ---- Date Helpers ----

def _get_tz():
    try:
        import zoneinfo
        return zoneinfo.ZoneInfo(TIMEZONE)
    except ImportError:
        import pytz
        return pytz.timezone(TIMEZONE)

def today_central_yyyymmdd():
    tz = _get_tz()
    return datetime.now(tz).strftime("%Y%m%d")

def yyyymmdd_from_central_offset(days_back=0):
    tz = _get_tz()
    now = datetime.now(tz)
    target = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_back)
    return target.strftime("%Y%m%d")

def to_display_date(yyyymmdd):
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"

# ---- Trend Formatting ----

def join_trends(team, ats_obj, ou_obj):
    parts = []
    if ats_obj and ats_obj.get("atsPct") is not None: parts.append(f"ATS {ats_obj['atsPct']}%")
    if ats_obj and ats_obj.get("atsPlusMinus") is not None: parts.append(f"ATS +/- {ats_obj['atsPlusMinus']}")
    if ou_obj and ou_obj.get("overPct") is not None: parts.append(f"Over {ou_obj['overPct']}%")
    if ou_obj and ou_obj.get("underPct") is not None: parts.append(f"Under {ou_obj['underPct']}%")
    if ou_obj and ou_obj.get("totalPlusMinus") is not None: parts.append(f"O/U +/- {ou_obj['totalPlusMinus']}")
    return " \u00b7 ".join(parts)

# ---- Grading ----

def parse_spread_pick(pick):
    if not pick or pick == "PASS": return None
    m = re.match(r"(.+?)\s+([+-])(\d+(?:\.\d+)?)", pick)
    if not m: return None
    return {"team": m.group(1).strip(), "sign": m.group(2), "pts": float(m.group(3))}

def grade_spread_pick(g):
    parsed = parse_spread_pick(g.get("sPick"))
    if not parsed: return None
    chosen_is_home = parsed["team"] == g.get("home")
    margin = (g["homeScore"] - g["awayScore"]) if chosen_is_home else (g["awayScore"] - g["homeScore"])
    val = margin + parsed["pts"] if parsed["sign"] == "+" else margin - parsed["pts"]
    if val == 0: return "PUSH"
    return "WIN" if val > 0 else "LOSS"

def grade_total_pick(g):
    o_pick = g.get("oPick")
    if not o_pick or o_pick == "PASS": return None
    actual = g["homeScore"] + g["awayScore"]
    if actual == g.get("total"): return "PUSH"
    if o_pick == "OVER": return "WIN" if actual > g["total"] else "LOSS"
    return "WIN" if actual < g["total"] else "LOSS"

def grade_result(g, type_):
    if type_ == "spread": return g.get("sResult") or grade_spread_pick(g)
    return g.get("oResult") or grade_total_pick(g)

# ---- Store: Grade a Past Date ----

def grade_date_in_store(store, yyyymmdd):
    run = next((r for r in store.get("runs", []) if r["date"] == yyyymmdd), None)
    if not run: return
    needs_grading = any(
        g.get("status") not in ("MISSING_ODDS", "SKIPPED") and not isinstance(g.get("homeScore"), (int, float))
        for g in run.get("games", []))
    if not needs_grading: return
    try: sb = fetch_scoreboard(yyyymmdd)
    except Exception: return
    if not sb: return
    finals = extract_final_scores(sb)
    if not finals: return
    graded = 0
    for g in run.get("games", []):
        if g.get("status") in ("MISSING_ODDS", "SKIPPED"): continue
        if isinstance(g.get("homeScore"), (int, float)): continue
        f = next((x for x in finals if match_team(x["away"], g["away"]) and match_team(x["home"], g["home"])), None)
        if not f: continue
        g["awayScore"] = f["awayScore"]
        g["homeScore"] = f["homeScore"]
        if g.get("sPick") and g["sPick"] != "PASS": g["sResult"] = grade_spread_pick(g)
        if g.get("oPick") and g["oPick"] != "PASS": g["oResult"] = grade_total_pick(g)
        graded += 1
    if graded > 0: save_store(store)

# ---- Record Computation ----

def get_graded_picks(store):
    picks = []
    for r in store.get("runs", []):
        if r.get("burnIn"): continue
        for g in r.get("games", []):
            if not isinstance(g.get("homeScore"), (int, float)) or not isinstance(g.get("awayScore"), (int, float)): continue
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"): continue
            picks.append({"date": r["date"], **g})
    return picks

def tally_picks(pick_list, type_="spread", conf=None, side=None):
    w = l = p = 0
    total_units = 0
    for g in pick_list:
        pick = g.get("sPick") if type_ == "spread" else g.get("oPick")
        pick_conf = g.get("sConf") if type_ == "spread" else g.get("oConf")
        if not pick or pick == "PASS": continue
        if conf and pick_conf != conf: continue
        if side:
            if type_ == "spread":
                if not is_actionable(pick_conf): continue
                parsed = parse_spread_pick(pick)
                if not parsed: continue
                if ("fav" if parsed["sign"] == "-" else "dog") != side: continue
            else:
                if not is_actionable(pick_conf): continue
                if ("over" if pick == "OVER" else "under") != side: continue
        result = grade_result(g, type_)
        if not result: continue
        if result == "WIN": w += 1
        elif result == "LOSS": l += 1
        else: p += 1
        if type_ == "total": total_units += total_pick_unit(g.get("date", ""), result)
    return {"w": w, "l": l, "p": p, "pct": win_pct(w, l), "played": w + l + p,
            "units": total_units if type_ == "total" else calc_units(w, l)}

def compute_summary_obj(store):
    picks = get_graded_picks(store)
    t = lambda **kw: tally_picks(picks, **kw)
    return {
        "spread": {"all": t(), "elite": t(conf="elite")},
        "favdog": {"fav": t(side="fav"), "dog": t(side="dog")},
        "total": {"all": t(type_="total"), "elite": t(type_="total", conf="elite")},
        "ouside": {"over": t(type_="total", side="over"), "under": t(type_="total", side="under")},
    }

def compute_summary_text(store):
    s = compute_summary_obj(store)
    def row(label, b):
        return f"  {label:<12} {b['w']}-{b['l']}-{b['p']}   ({b['pct']}%)   [graded {b['played']}]"
    return "\n".join([
        "RECORD (graded picks only)", "",
        "SPREAD (ATS)", row("All:", s["spread"]["all"]), row("Elite:", s["spread"]["elite"]), "",
        "FAV/DOG (Spread)", row("Favorites:", s["favdog"]["fav"]), row("Underdogs:", s["favdog"]["dog"]), "",
        "TOTAL (O/U)", row("All:", s["total"]["all"]), row("Elite:", s["total"]["elite"]), "",
        "OVER/UNDER", row("Over:", s["ouside"]["over"]), row("Under:", s["ouside"]["under"]),
    ])

# ---- Last 10 / Rolling / Weekly ----

def get_actionable_picks(store, type_="spread"):
    results = []
    for r in store.get("runs", []):
        if r.get("burnIn"): continue
        for g in r.get("games", []):
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"): continue
            pick = g.get("sPick") if type_ == "spread" else g.get("oPick")
            conf = g.get("sConf") if type_ == "spread" else g.get("oConf")
            if not pick or pick == "PASS" or not is_actionable(conf): continue
            if not isinstance(g.get("homeScore"), (int, float)) or not isinstance(g.get("awayScore"), (int, float)): continue
            result = grade_result(g, type_)
            results.append({
                "date": r["date"], "matchup": f"{g['away']} @ {g['home']}", "pick": pick, "conf": conf,
                "result": result or "PENDING", "final": f"{g['awayScore']}-{g['homeScore']}",
                "total": g.get("total"), "home": g.get("home"),
            })
    return results

def compute_last10(store, type_="spread"):
    return get_actionable_picks(store, type_)[-10:]

def last10_text(store, type_="spread"):
    label = "SPREAD" if type_ == "spread" else "TOTAL"
    picks = compute_last10(store, type_)
    lines = [f"LAST 10 {label} PICKS (run order)"]
    if not picks: return "\n".join(lines + ["  No picks yet."])
    for p in picks:
        extra = f" {p.get('total', '')}" if type_ == "total" else ""
        lines.append(f"  {to_display_date(p['date'])} | {p['matchup']} | {p['pick']}{extra} | {p['conf'].upper()} | {p['result']} | {p['final']}")
    return "\n".join(lines)

def compute_rolling_windows(store, type_="spread"):
    picks = [p for p in get_actionable_picks(store, type_) if p.get("result") and p["result"] != "PENDING"]
    window = 10 if len(picks) < 100 else 20
    rows = []
    for i in range(0, len(picks) - window + 1, window):
        sl = picks[i:i + window]
        w = sum(1 for x in sl if x["result"] == "WIN")
        l = sum(1 for x in sl if x["result"] == "LOSS")
        p = sum(1 for x in sl if x["result"] == "PUSH")
        units = sum(total_pick_unit(x["date"], x["result"]) for x in sl) if type_ == "total" else calc_units(w, l)
        rows.append({"label": f"#{i+1}\u2013{i+window}", "w": w, "l": l, "p": p,
                      "pct": win_pct(w, l), "units": units,
                      "startDate": to_display_date(sl[0]["date"]), "endDate": to_display_date(sl[-1]["date"])})
    return rows

def compute_weekly_breakdown(store, type_="spread"):
    picks = [p for p in get_actionable_picks(store, type_) if p.get("result") and p["result"] != "PENDING"]
    by_week = {}
    for p in picks:
        dd = to_display_date(p["date"])
        d = datetime.fromisoformat(f"{dd}T12:00:00-06:00")
        day = d.weekday()
        monday = d - timedelta(days=day)
        key = monday.strftime("%Y-%m-%d")
        by_week.setdefault(key, []).append({"result": p["result"], "date": p["date"]})
    rows = []
    for week in sorted(by_week):
        entries = by_week[week]
        w = sum(1 for x in entries if x["result"] == "WIN")
        l = sum(1 for x in entries if x["result"] == "LOSS")
        p = sum(1 for x in entries if x["result"] == "PUSH")
        units = sum(total_pick_unit(x["date"], x["result"]) for x in entries) if type_ == "total" else calc_units(w, l)
        rows.append({"week": week, "w": w, "l": l, "p": p, "pct": win_pct(w, l), "units": units})
    return rows[-12:]

# ---- Yesterday's Recap ----

def compute_yesterday_recap(store, yesterday_date):
    yesterday_run = next((r for r in store.get("runs", []) if r["date"] == yesterday_date), None)
    if not yesterday_run: return None
    picks = []
    units = 0
    for g in yesterday_run.get("games", []):
        if g.get("status") in ("MISSING_ODDS", "SKIPPED"): continue
        if not isinstance(g.get("homeScore"), (int, float)) or not isinstance(g.get("awayScore"), (int, float)): continue
        if g.get("sPick") and g["sPick"] != "PASS" and is_actionable(g.get("sConf")):
            result = g.get("sResult") or grade_spread_pick(g)
            if result:
                picks.append({"matchup": f"{g['away']} @ {g['home']}", "pick": g["sPick"],
                              "conf": g["sConf"], "result": result, "final": f"{g['awayScore']}-{g['homeScore']}",
                              "sDiff": g.get("sDiff")})
                if result == "WIN": units += 1
                elif result == "LOSS": units += UNIT_LOSS
    if not picks: return None
    w = sum(1 for p in picks if p["result"] == "WIN")
    l = sum(1 for p in picks if p["result"] == "LOSS")
    p_count = sum(1 for p in picks if p["result"] == "PUSH")
    return {"date": yesterday_date, "dateDisplay": to_display_date(yesterday_date),
            "picks": picks, "tally": {"w": w, "l": l, "p": p_count}, "units": round(units * 100) / 100}

def compute_team_records(store):
    teams = {}
    for r in store.get("runs", []):
        for g in r.get("games", []):
            pick, conf = g.get("sPick"), g.get("sConf")
            if not pick or pick == "PASS" or not is_actionable(conf): continue
            if not isinstance(g.get("homeScore"), (int, float)) or not isinstance(g.get("awayScore"), (int, float)): continue
            result = g.get("sResult") or grade_spread_pick(g)
            if not result: continue
            m = re.match(r"(.+?)\s+([+-])(\d+(?:\.\d+)?)", pick)
            if not m: continue
            team, is_fav = m.group(1).strip(), m.group(2) == "-"
            if team not in teams: teams[team] = {"w":0,"l":0,"p":0,"fav":0,"dog":0,"picks":0}
            teams[team]["picks"] += 1
            teams[team]["fav" if is_fav else "dog"] += 1
            if result == "WIN": teams[team]["w"] += 1
            elif result == "LOSS": teams[team]["l"] += 1
            else: teams[team]["p"] += 1
    return teams

def build_game_injury_adj(away_team, home_team, injury_report, player_mpg=None, recent_injury_dates=None, ofs_players=None):
    return {"awayInjuries": get_key_injuries(injury_report, away_team, player_mpg, recent_injury_dates, ofs_players),
            "homeInjuries": get_key_injuries(injury_report, home_team, player_mpg, recent_injury_dates, ofs_players)}

def conf_badge(conf):
    c = str(conf or "").lower()
    cls = "b-elite" if c == "elite" else ("b-high" if c == "high" else "b-low")
    return f'<span class="badge {cls}">{esc(c.upper() if c else "N/A")}</span>'

def build_game_prob_table(games):
    filtered = [g for g in (games or []) if g.get("status") not in ("MISSING_ODDS", "SKIPPED")]
    if not filtered:
        return ""

    rows = ""
    for g in filtered:
        s_pick_display = esc(g["sPick"]) if g.get("sPick") and g["sPick"] != "PASS" else '<span style="color:#9ca3af">PASS</span>'
        s_conf_badge = f" {conf_badge(g.get('sConf'))}" if g.get("sPick") and g["sPick"] != "PASS" else ""


        p_cover_str = f'<b>{g["pCover"] * 100:.0f}%</b>' if g.get("pCover") is not None else '<span style="color:#9ca3af">\u2014</span>'
        p_home = f'{g["pHomeCover"] * 100:.0f}%' if g.get("pHomeCover") is not None else "\u2014"
        p_away = f'{g["pAwayCover"] * 100:.0f}%' if g.get("pAwayCover") is not None else "\u2014"
        margin = (("+" if g["margin"] >= 0 else "") + fmt_num(g["margin"], 1)) if isinstance(g.get("margin"), (int, float)) and math.isfinite(g["margin"]) else "\u2014"

        rows += f'''<tr>
        <td style="font-weight:700">{esc(g["away"])} @ {esc(g["home"])}</td>
        <td>{s_pick_display}{s_conf_badge}<div class="tiny" style="margin-top:2px">Line {fmt_num(g.get("line"), 1)} \u00b7 proj {margin} \u00b7 sDiff {fmt_num(g.get("sDiff"), 1)}</div></td>
        <td style="text-align:center">{p_cover_str}<div class="tiny">{p_away} away / {p_home} home</div></td>
        <td style="text-align:center"></td>
      </tr>'''

    return f'''<div class="card" style="border-left:4px solid #8b5cf6; margin-bottom:10px;">
    <div class="summaryTitle">\U0001F3AF Cover Probabilities \u2014 All Games</div>
    <table class="data">
      <thead><tr>
        <th>Game</th><th>Spread Pick</th><th style="text-align:center">P(Cover)</th><th style="text-align:center">LR Reason</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
    <div class="tiny" style="margin-top:6px">P(Cover) = probability the picked side wins. Directional % shown below.</div>
  </div>'''


# ---- Main Pipeline ----

def main(subject_label="[PY]"):
    date = today_central_yyyymmdd()
    date_display = to_display_date(date)
    store = load_store()
    defaults = load_defaults()

    print(f"\n== NBA Picks (Full Season) Pipeline - {date_display} ==\n")

    # 1. Grade recent days
    days_to_grade = set()
    for d in range(1, 6): days_to_grade.add(yyyymmdd_from_central_offset(d))
    for r in store.get("runs", []):
        if any(g.get("status") not in ("MISSING_ODDS","SKIPPED") and not isinstance(g.get("homeScore"),(int,float))
               and ((g.get("sPick") and g["sPick"]!="PASS") or (g.get("oPick") and g["oPick"]!="PASS"))
               for g in r.get("games",[])):
            days_to_grade.add(r["date"])
    yesterday = yyyymmdd_from_central_offset(1)
    for d in days_to_grade: grade_date_in_store(store, d)

    # 2. Fetch today's data
    season_type = get_season_type(date)
    espn_type = get_espn_season_type(date)
    print(f"[1/7] Fetching stats, odds, trends, injuries, player data... [{season_type}]")
    # Try shared stats cache first, then fetch via nba_api
    enhanced_stats = None
    _scripts_dir = os.path.dirname(os.path.abspath(__file__))
    _shared_cache_dir = os.path.join(_scripts_dir, "..", "..", "data", "stats_cache", "nba")
    _cache_path = os.path.join(_shared_cache_dir, f"{date}.json")
    if os.path.exists(_cache_path):
        try:
            with open(_cache_path, "r", encoding="utf-8") as f:
                enhanced_stats = json.load(f)
            if enhanced_stats.get("season") and len(enhanced_stats["season"]) >= 20:
                print(f"  [nba_stats] Using cached stats ({len(enhanced_stats['season'])} teams)")
            else:
                enhanced_stats = None
        except Exception:
            enhanced_stats = None
    if not enhanced_stats:
        enhanced_stats = fetch_nba_stats_enhanced(date, season_type=season_type)
        os.makedirs(_shared_cache_dir, exist_ok=True)
        try:
            with open(_cache_path, "w", encoding="utf-8") as f:
                json.dump(enhanced_stats, f)
        except Exception:
            pass
    # FanDuel primary (no API key needed), The Odds API fallback
    odds = fetch_fanduel_nba_odds()
    if not odds:
        try:
            odds = fetch_todays_odds()
        except Exception as e:
            print(f"  [odds] Odds API fallback failed: {e}")
            odds = []

    # Detect started/finished games via ESPN scoreboard
    try:
        _espn_sb = fetch_scoreboard(date)
    except Exception:
        _espn_sb = None
    _espn_statuses = {}
    _espn_start_times = {}
    for _ev in (_espn_sb or {}).get("events", []):
        _comp = (_ev.get("competitions") or [None])[0]
        if not _comp: continue
        _comps = _comp.get("competitors", [])
        _ac = next((c for c in _comps if c.get("homeAway") == "away"), None)
        _hc = next((c for c in _comps if c.get("homeAway") == "home"), None)
        if _ac and _hc:
            _a = (_ac.get("team") or {}).get("displayName", "")
            _h = (_hc.get("team") or {}).get("displayName", "")
            _st = ((_comp.get("status") or {}).get("type") or {}).get("name", "")
            _espn_statuses[(_a, _h)] = _st
            _start = _comp.get("date") or _ev.get("date") or ""
            if _start:
                _espn_start_times[(_a, _h)] = _start

    # Backfill startTimeUTC on odds from ESPN scoreboard
    for g in odds:
        if g.get("startTimeUTC"):
            continue
        for (ea, eh), st in _espn_start_times.items():
            if match_team(g.get("away", ""), ea) and match_team(g.get("home", ""), eh):
                g["startTimeUTC"] = st
                break

    def _game_started_or_finished(away, home):
        for (ea, eh), st in _espn_statuses.items():
            if match_team(away, ea) and match_team(home, eh):
                if st not in ("STATUS_SCHEDULED", "STATUS_DELAYED", "STATUS_POSTPONED", "STATUS_CANCELED", ""):
                    return True
        return False

    # Merge + write injury cache: preserve report entries for started/finished teams
    try:
        _locked_teams = set()
        for (_ea, _eh), _st in _espn_statuses.items():
            if _st not in ("STATUS_SCHEDULED", "STATUS_DELAYED", "STATUS_POSTPONED", "STATUS_CANCELED", ""):
                _locked_teams.add(_ea); _locked_teams.add(_eh)
        _merged_report = dict(injury_data.get("report", {}))
        if _locked_teams and _old_inj_report:
            for _team, _entries in _old_inj_report.items():
                if any(match_team(_team, lt) for lt in _locked_teams):
                    _merged_report[_team] = _entries
        _inj_to_write = {"injuryData": {**injury_data, "report": _merged_report}, "playerAdvanced": player_advanced, "h2hMatchups": h2h_matchups}
        os.makedirs(_inj_cache_dir, exist_ok=True)
        with open(_inj_cache, "w", encoding="utf-8") as _f:
            json.dump(_inj_to_write, _f)
    except Exception:
        pass

    _prev_run = next((r for r in store.get("runs", []) if r.get("date") == date), None)
    _prev_games = {(g.get("away", ""), g.get("home", "")): g for g in (_prev_run or {}).get("games", [])} if _prev_run else {}

    def _find_prev_game(away, home):
        for (pa, ph), pg in _prev_games.items():
            if match_team(away, pa) and match_team(home, ph):
                return pg
        return None

    ats = fetch_ats_trends()
    ou = fetch_ou_trends()
    h2h_matchups = None
    # Always fetch fresh injuries, merge with cache for started/finished games
    injury_data = None
    player_advanced = None
    _scripts = os.path.dirname(os.path.abspath(__file__))
    _inj_cache_dir = os.path.join(_scripts, "..", "..", "data", "injury_cache", "nba")
    _inj_cache = os.path.join(_inj_cache_dir, f"{date}.json")
    _old_inj_report = {}
    if os.path.exists(_inj_cache):
        try:
            with open(_inj_cache, "r", encoding="utf-8") as _f:
                _old = json.load(_f)
            _old_inj_report = (_old.get("injuryData") or {}).get("report", {})
        except Exception:
            pass
    import time as _time
    _time.sleep(5)
    try: injury_data = fetch_injury_data(None, season_type=season_type, espn_type=espn_type)
    except Exception as e: print(f"  Warning: Injury fetch failed: {e}"); injury_data = {"report":{},"playerMPG":{}}
    if player_advanced is None:
        import time as _time
        _time.sleep(5)
        try: player_advanced = fetch_player_advanced(season_type=season_type)
        except Exception as e: print(f"  Warning: Player advanced fetch failed: {e}"); player_advanced = {}
    try: b2b_teams = detect_b2b()
    except Exception: b2b_teams = set()
    # H2H disabled (h2hWeight=0) — skip fetch entirely
    h2h_matchups = None

    base_w = store.get("weights") or defaults["DEFAULT_W"]
    base_w_var = store.get("weightsVar") or defaults["DEFAULT_W_VAR"]

    # TEMP OVERRIDE: Orlando L10 — drop 3/29 TOR (87-139) + 4/1 ATL (101-130) blowouts
    # Fetches game log at runtime, removes those games, recomputes L10 from remaining.
    # Auto-disables after 2026-04-20 (both games will have dropped out of L10 by then).
    _orl_deadline = "20260420"
    if date <= _orl_deadline and enhanced_stats.get("last10", {}).get("Orlando Magic"):
        try:
            from nba_api.stats.endpoints import teamgamelog as _tgl
            from nba_api.stats.endpoints import boxscoreadvancedv3 as _bsa
            import time as _time

            _logs = _tgl.TeamGameLog(team_id=1610612753, season="2025-26", season_type_all_star="Regular Season", timeout=30)
            _df = _logs.get_data_frames()[0]
            _exclude = {"0022501086", "0022501107"}  # TOR 3/29, ATL 4/1
            _clean = _df[~_df["Game_ID"].isin(_exclude)].head(10)

            if len(_clean) == 10:
                _adv = []
                for _, _r in _clean.iterrows():
                    _time.sleep(0.6)
                    try:
                        _box = _bsa.BoxScoreAdvancedV3(game_id=_r["Game_ID"], timeout=30)
                        _tdf = _box.get_data_frames()[1]
                        _orl = _tdf[_tdf["teamName"] == "Magic"].iloc[0]
                        _adv.append({
                            "OFF": float(_orl["offensiveRating"]),
                            "DEF": float(_orl["defensiveRating"]),
                            "TS": float(_orl["trueShootingPercentage"]),
                            "TO": float(_orl["turnoverRatio"]) / 100,
                            "ORR": float(_orl["offensiveReboundPercentage"]),
                            "PACE": float(_orl["pace"]),
                        })
                    except Exception:
                        pass

                if len(_adv) >= 8:
                    _avg = lambda k: round(sum(g[k] for g in _adv) / len(_adv), 4)
                    _patch = {k: _avg(k) for k in ["OFF", "DEF", "TS", "TO", "ORR", "PACE"]}
                    enhanced_stats["last10"]["Orlando Magic"].update(_patch)
                    print(f"  [override] Patched Orlando Magic L10 from {len(_adv)} clean games: OFF={_patch['OFF']} DEF={_patch['DEF']}")
                else:
                    print(f"  [override] Only got {len(_adv)} box scores — skipping Orlando patch")
        except Exception as _e:
            print(f"  [override] Orlando L10 patch failed (non-fatal): {_e}")

    stats = blend_base(enhanced_stats["season"], enhanced_stats.get("last10"), base_w.get("recentWeight", 0.35))

    # Stats already cached in the read-or-fetch block above

    # 3. Lineup-adjusted stats + B2B rest + Kalman state
    print("[2/7] Applying lineup adjustments + B2B rest + Kalman filter...")
    # Load recent injury caches for returning-star detection
    recent_injury_dates = {}
    try:
        inj_cache_dir = os.path.join(os.path.dirname(__file__), "..", "..", "data", "injury_cache", "nba")
        if os.path.isdir(inj_cache_dir):
            cache_files = sorted(
                [f for f in os.listdir(inj_cache_dir) if f.endswith(".json") and f < date + ".json"],
                reverse=True
            )[:10]
            for f in cache_files:
                with open(os.path.join(inj_cache_dir, f)) as fh:
                    cached = json.load(fh)
                report = cached.get("report") or (cached.get("injuryData") or {}).get("report") or {}
                if report:
                    recent_injury_dates[f.replace(".json", "")] = report
            print(f"  [lineup] Loaded {len(recent_injury_dates)} recent injury caches for returning-star detection")
    except Exception:
        pass

    # Fetch out-for-season players from ESPN league-wide injuries page
    ofs_players = set()
    try:
        ofs_players = fetch_out_for_season(injury_data.get("report", {}))
    except Exception:
        pass

    # In playoffs, inflate star/starter minutes (mirrors jsFull behavior).
    # season_type.is_playoffs() is currently hardcoded to False — flip the
    # season_type.py override (or pass playoff_mode=True manually) to turn on.
    from sources.season_type import is_playoffs as _is_playoffs
    _playoff_mode = _is_playoffs(date)
    lineup_stats = adjust_team_stats(
        stats, injury_data.get("report",{}), injury_data.get("playerMPG",{}),
        player_advanced, odds,
        recent_injury_dates=recent_injury_dates, ofs_players=ofs_players,
        playoff_mode=_playoff_mode,
    )
    result = apply_b2b_adjustment(lineup_stats, b2b_teams, odds)
    adjusted_stats, b2b_notes = result["adjusted"], result["b2bNotes"]
    a = get_avgs(adjusted_stats)

    STAT_KEYS = ["OFF","DEF","TS","TO","ORR","PACE"]
    def compute_team_delta(team):
        base = stats.get(team); adj = adjusted_stats.get(team)
        if not base or not adj or base is adj: return None
        d = {}; any_d = False
        for k in STAT_KEYS:
            diff = (adj.get(k,0) or 0) - (base.get(k,0) or 0)
            if abs(diff) > 0.0001: d[k] = round(diff*10000)/10000; any_d = True
        return d if any_d else None

    kalman_state = load_kalman_state()
    if not (kalman_state.get("teams") and len(kalman_state["teams"]) > 0):
        kalman_state = initialize_kalman(stats)

    newly_graded = []
    for r in (store.get("runs") or [])[-14:]:
        if r["date"] == date: continue
        for g in r.get("games",[]):
            if g.get("status") in ("MISSING_ODDS","SKIPPED"): continue
            if not isinstance(g.get("homeScore"),(int,float)) or not isinstance(g.get("awayScore"),(int,float)): continue
            if not isinstance(g.get("hS"),(int,float)) or not isinstance(g.get("aS"),(int,float)): continue
            g["_kalmanDate"] = r["date"]; newly_graded.append(g)
    batch_update(kalman_state, newly_graded)

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
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"): continue
            if not isinstance(g.get("homeScore"), (int, float)) or not isinstance(g.get("awayScore"), (int, float)): continue
            if not g.get("_features"): continue
            g["_recencyWeight"] = _recency_weight(days_ago)
            recent_graded.append(g)
    if recent_graded and store.get("lastTuneDate") != date:
        tuned = tune_weights(base_w, base_w_var, recent_graded)
        base_w, base_w_var = tuned["W"], tuned["W_var"]
        store["weights"], store["weightsVar"], store["lastTuneDate"] = tuned["W"], tuned["W_var"], date
        print(f"[3b] Weights tuned on {len(recent_graded)} graded games from last {TUNE_WINDOW} days")
    elif store.get("lastTuneDate") == date:
        print("[3b] Weights already tuned today - skipping")

    # --- Playoff overrides (HCA from empirical history, probHigh threshold floor) ---
    if is_playoffs(date):
        emp_hca = compute_empirical_playoff_hca()
        if emp_hca is not None:
            new_hca = max(emp_hca, 2.5)
            old_hca = base_w.get("hca")
            base_w["hca"] = new_hca
            try:
                with open(os.path.join(os.path.dirname(__file__), "..", "data", "history.json"), "r", encoding="utf-8") as _f:
                    _hist = json.load(_f)
                _n_games = sum(
                    1 for r in _hist.get("runs", [])
                    if (r.get("date") or "") >= PLAYOFF_START
                    for g in r.get("games", [])
                    if isinstance(g.get("homeScore"), (int, float))
                    and isinstance(g.get("awayScore"), (int, float))
                    and math.isfinite(g.get("homeScore"))
                    and math.isfinite(g.get("awayScore"))
                )
            except Exception:
                _n_games = 0
            print(f"[playoff] HCA override: {old_hca} -> {new_hca} (from {_n_games} playoff games)")

        _old_ph = base_w.get("probHigh", 0.58)
        _new_ph = max(_old_ph, 0.65)
        if _new_ph != _old_ph:
            base_w["probHigh"] = _new_ph
            print(f"[playoff] probHigh raised to {_new_ph} (was {_old_ph}) - filters out 0.60-0.65 picks")

    # --- Per-team HCA in playoffs only ---
    # Some teams (CLE, BOS) have near-zero personal home advantage; others (NYK)
    # have well above league average. League-average HCA (~3.0 in playoffs)
    # mis-prices both. Compute per-team HCA from regular-season home/away splits
    # and pass to analyze_game ONLY in playoffs — regular-season behavior
    # unchanged because team_hca stays None there.
    team_hca = None
    if is_playoffs(date):
        from core.model_engine import compute_team_hca
        team_hca = compute_team_hca(
            enhanced_stats.get("home"),
            enhanced_stats.get("away"),
            league_hca=base_w.get("hca", 1.8),
        )
        if team_hca:
            print(f"[playoff] Per-team HCA computed for {len(team_hca)} teams "
                  f"(league_hca={base_w.get('hca', 1.8):.2f})")

    apply_daily_drift(kalman_state, date)
    dynamic_residual_var = compute_residual_var(store.get("runs", []))
    store["residualVar"] = dynamic_residual_var

    # 4. Analyze each game — filter to only games on the run date
    def _game_on_run_date(g):
        st = g.get("startTimeUTC")
        if not st:
            return True
        try:
            from datetime import datetime, timezone, timedelta
            dt = datetime.fromisoformat(st.replace("Z", "+00:00"))
            chicago = dt.astimezone(timezone(timedelta(hours=-5)))  # CDT approx
            game_date = chicago.strftime("%Y%m%d")
            if game_date != date:
                print(f"  [filter] Skipping {g.get('away','')} @ {g.get('home','')} — game date {game_date} != run date {date}")
                return False
        except Exception:
            pass
        return True

    odds = [g for g in odds if _game_on_run_date(g)]
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

        if not isinstance(g.get("line"),(int,float)) or not isinstance(g.get("total"),(int,float)):
            games.append({**g, "status": "MISSING_ODDS"}); continue
        try: injury_adj = build_game_injury_adj(g["away"], g["home"], injury_data.get("report",{}), injury_data.get("playerMPG"), recent_injury_dates, ofs_players)
        except Exception: injury_adj = None
        game_stats = blend_for_game(adjusted_stats, enhanced_stats.get("home"), enhanced_stats.get("away"), g["home"], g["away"], base_w.get("locationWeight", 0.25))
        game_avgs = get_avgs(game_stats)
        r = analyze_game(g, game_stats, game_avgs, base_w, injury_adj, kalman_state, base_w_var, dynamic_residual_var, h2h_matchups, team_hca=team_hca)
        if not r: games.append({**g, "status": "SKIPPED"}); continue

        ad = compute_team_delta(g["away"]); hd = compute_team_delta(g["home"])
        if ad or hd:
            r["_adjDeltas"] = {}
            if ad: r["_adjDeltas"]["away"] = ad
            if hd: r["_adjDeltas"]["home"] = hd
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
        if g.get("status") in ("MISSING_ODDS","SKIPPED"): continue
        ats_ka, ats_kh = resolve_key(ats, g.get("away")), resolve_key(ats, g.get("home"))
        ou_ka, ou_kh = resolve_key(ou, g.get("away")), resolve_key(ou, g.get("home"))
        g["trends"] = {"away": join_trends(g.get("away"), ats.get(ats_ka), ou.get(ou_ka)),
                       "home": join_trends(g.get("home"), ats.get(ats_kh), ou.get(ou_kh))}
        ab, hb = b2b_notes.get(g.get("away")), b2b_notes.get(g.get("home"))
        if ab or hb:
            g["b2bNote"] = " | ".join(p for p in [f"{g['away']}: {ab}" if ab else None, f"{g['home']}: {hb}" if hb else None] if p)

    # Sort games by start time
    games.sort(key=lambda g: g.get("startTimeUTC") or "")

    # 6-7. Save
    print("[4/7] Saving...")
    run = {"date":date,"dateDisplay":date_display,"burnIn":False,"weightsUsed":base_w,"weightsNext":base_w,"games":games,"summaryText":""}
    print("[5/7] Saving...")
    run["summaryText"] = compute_summary_text(store)
    upsert_run(store, run); save_store(store)
    prune_processed_games(kalman_state, 30); save_kalman_state(kalman_state)

    # Sync to PythonDashboard
    try:
        import shutil
        _here = os.path.dirname(os.path.abspath(__file__))
        shutil.copy2(os.path.join(_here, "..", "data", "history.json"),
                     os.path.join(_here, "..", "..", "PythonDashboard", "data", "fullseason.json"))
        print("[7/7] Dashboard synced.")
    except Exception as e:
        print(f"  [dashboard] sync failed: {e}")

    print(f"\nDone: {subject_label} NBA Picks (Full Season) {run['dateDisplay']}")

if __name__ == "__main__":
    try: main()
    except Exception as err:
        import traceback; traceback.print_exc(); sys.exit(1)

# scripts/run_daily.py
# Daily NCAA picks pipeline:
#   1. Grade yesterday's picks against final scores
#   2. Fetch today's stats, odds, trends
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

from sources.ncaa_stats import fetch_ncaa_stats_enhanced
from sources.blend_stats import blend_base, blend_for_game
from sources.odds_theoddsapi import fetch_todays_odds
from sources.espn_scoreboard import fetch_scoreboard, extract_final_scores, fetch_tournament_teams
from sources.teamrankings_trends import fetch_ats_trends, fetch_ou_trends
from sources.rest_detect import detect_b2b, apply_b2b_adjustment
from model_engine import load_defaults, get_avgs, analyze_game
from store import load_store, save_store, upsert_run
from self_tune import tune_weights, compute_residual_var
from kalman_state import (
    load_kalman_state, save_kalman_state, initialize_kalman,
    apply_daily_drift, batch_update, kalman_summary, prune_processed_games,
)
from calibration import build_calibration_table, build_calibration_html
from email_report import send_email
try:
    from ensemble import ENSEMBLE_AVAILABLE, train_ensemble, predict_ensemble
except ImportError:
    ENSEMBLE_AVAILABLE = False

# --- Constants ----------------------------------------------------------------

TIMEZONE = "America/Chicago"
CONF_ACTIONABLE = ["high", "elite"]
UNIT_LOSS = -1.1  # standard -110 juice

# --- Utility Helpers ----------------------------------------------------------

def esc(s):
    s = str(s if s is not None else "")
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;")
             .replace("'", "&#39;"))


def fmt_num(x, digits=1):
    if isinstance(x, (int, float)) and math.isfinite(x):
        return f"{float(x):.{digits}f}"
    return "n/a"


def calc_units(w, l):
    return w + l * UNIT_LOSS


def fmt_units(u):
    if not isinstance(u, (int, float)) or not math.isfinite(u):
        return "\u2014"
    return ("+" if u >= 0 else "") + f"{u:.1f}u"


def win_pct(w, l):
    total = w + l
    return f"{(w / total * 100):.1f}" if total > 0 else "n/a"


def is_actionable(conf):
    return str(conf).lower() in CONF_ACTIONABLE

# --- Team Name Resolution ----------------------------------------------------

def norm_key(s):
    s = str(s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\bstate\b", "st", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def safe_fuzzy(a, b):
    if not a or not b:
        return False
    if a == b:
        return True
    shorter = a if len(a) < len(b) else b
    longer = b if len(a) < len(b) else a
    if len(shorter) / len(longer) < 0.85:
        return False
    return shorter in longer


TEAM_ALIASES = {
    "ohio state buckeyes": "Ohio St.",
    "ohio state": "Ohio St.",
    "ohio st": "Ohio St.",
}

def resolve_key(obj, team_name):
    if not obj or not team_name:
        return None
    if team_name in obj:
        return team_name
    wanted = norm_key(team_name)
    alias = TEAM_ALIASES.get(wanted)
    if alias and alias in obj:
        return alias

    for k in obj:
        if norm_key(k) == wanted:
            return k

    for k in obj:
        if safe_fuzzy(norm_key(k), wanted):
            return k

    prefix_match = None
    for k in obj:
        nk = norm_key(k)
        if len(nk) < 3:
            continue
        if wanted.startswith(nk + " ") or wanted == nk:
            if not prefix_match or len(nk) > len(norm_key(prefix_match)):
                prefix_match = k
    if prefix_match:
        return prefix_match

    first_word = wanted.split(" ")[0]
    if len(first_word) >= 3:
        for k in obj:
            nk = norm_key(k)
            if nk == first_word or (len(nk) >= 3 and first_word.startswith(nk)):
                return k

    wanted_words = wanted.split(" ")
    wanted_last = wanted_words[-1]
    if wanted_last and len(wanted_last) >= 4:
        for k in obj:
            k_words = norm_key(k).split(" ")
            if k_words[-1] == wanted_last:
                return k

    return None


def match_team(a, b):
    if a == b:
        return True
    na, nb = norm_key(a), norm_key(b)
    if na == nb:
        return True
    if safe_fuzzy(na, nb):
        return True
    a_last = na.split(" ")[-1]
    b_last = nb.split(" ")[-1]
    if a_last and b_last and len(a_last) >= 4 and a_last == b_last:
        return True
    return False

# --- Date Helpers -------------------------------------------------------------

def _central_now():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(TIMEZONE))


def today_central_yyyymmdd():
    now = _central_now()
    return now.strftime("%Y%m%d")


def yyyymmdd_from_central_offset(days_back=0):
    now = _central_now()
    d = now - timedelta(days=days_back)
    return d.strftime("%Y%m%d")


def to_display_date(yyyymmdd):
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"

# --- Trend Formatting ---------------------------------------------------------

def join_trends(team, ats_obj, ou_obj):
    parts = []
    if ats_obj and ats_obj.get("atsPct") is not None:
        parts.append(f"ATS {ats_obj['atsPct']}%")
    if ats_obj and ats_obj.get("atsPlusMinus") is not None:
        parts.append(f"ATS +/- {ats_obj['atsPlusMinus']}")
    if ou_obj and ou_obj.get("overPct") is not None:
        parts.append(f"Over {ou_obj['overPct']}%")
    if ou_obj and ou_obj.get("underPct") is not None:
        parts.append(f"Under {ou_obj['underPct']}%")
    if ou_obj and ou_obj.get("totalPlusMinus") is not None:
        parts.append(f"O/U +/- {ou_obj['totalPlusMinus']}")
    return " \u00b7 ".join(parts)

# --- Grading ------------------------------------------------------------------

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
    if not g.get("oPick") or g["oPick"] == "PASS":
        return None
    actual = g["homeScore"] + g["awayScore"]
    if actual == g.get("total"):
        return "PUSH"
    if g["oPick"] == "OVER":
        return "WIN" if actual > g["total"] else "LOSS"
    return "WIN" if actual < g["total"] else "LOSS"


def grade_result(g, result_type):
    if result_type == "spread":
        return g.get("sResult") or grade_spread_pick(g)
    return g.get("oResult") or grade_total_pick(g)

# --- Store: Grade a Past Date ------------------------------------------------

def grade_date_in_store(store, yyyymmdd):
    run = next((r for r in (store.get("runs") or []) if r.get("date") == yyyymmdd), None)
    if not run:
        return
    needs_grading = any(
        g.get("status") not in ("MISSING_ODDS", "SKIPPED") and not isinstance(g.get("homeScore"), (int, float))
        for g in (run.get("games") or [])
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
    for g in (run.get("games") or []):
        if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
            continue
        if isinstance(g.get("homeScore"), (int, float)):
            continue
        f = next((x for x in finals if match_team(x["away"], g["away"]) and match_team(x["home"], g["home"])), None)
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

# --- Team Record --------------------------------------------------------------

def compute_team_records(store):
    teams = {}
    for r in (store.get("runs") or []):
        for g in (r.get("games") or []):
            pick = g.get("sPick")
            conf = g.get("sConf")
            if not pick or pick == "PASS" or not is_actionable(conf):
                continue
            if not isinstance(g.get("homeScore"), (int, float)) or not isinstance(g.get("awayScore"), (int, float)):
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
        reverse=True,
    )
    if not sorted_teams:
        return ""
    rows = ""
    for name, t in sorted_teams:
        total = t["w"] + t["l"]
        pct = round(100 * t["w"] / total) if total > 0 else 0
        flat_units = round((t["w"] - t["l"] * 1.1) * 10) / 10
        flat_str = ("+" if flat_units >= 0 else "") + f"{flat_units:.1f}u"
        flat_color = "#10b981" if flat_units >= 0 else "#ef4444"
        rows += (f'<tr><td>{esc(name)}</td><td class="">{t["picks"]}</td>'
                 f'<td class="">{t["w"]}-{t["l"]}-{t["p"]}</td>'
                 f'<td class="">{pct}%</td>'
                 f'<td class="" style="color:{flat_color}">{flat_str}</td>'
                 f'<td class="">{t["fav"]}</td><td class="">{t["dog"]}</td></tr>')
    return f'''
    <div class="card card-records">
      <div class="summaryTitle">\U0001F3C0 Team Spread Record (ATS)</div>
      <table class="data">
        <thead><tr>
          <th>Team</th><th class="">Picks</th><th class="">W-L-P</th>
          <th class="">Win%</th><th class="">Flat</th>
          <th class="">Fav</th><th class="">Dog</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
      <div class="tiny">Only graded spread picks. Units at -110 juice.</div>
    </div>'''

# --- Record Computation -------------------------------------------------------

def get_graded_picks(store):
    picks = []
    for r in (store.get("runs") or []):
        if r.get("burnIn"):
            continue
        for g in (r.get("games") or []):
            if not isinstance(g.get("homeScore"), (int, float)) or not isinstance(g.get("awayScore"), (int, float)):
                continue
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue
            picks.append({"date": r.get("date"), **g})
    return picks


def tally_picks(pick_list, type_="spread", conf=None, side=None):
    w, l, p = 0, 0, 0
    for g in pick_list:
        pick = g.get("sPick") if type_ == "spread" else g.get("oPick")
        pick_conf = g.get("sConf") if type_ == "spread" else g.get("oConf")
        if not pick or pick == "PASS":
            continue
        if conf and pick_conf != conf:
            continue
        if side:
            if type_ == "spread":
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
        result = grade_result(g, type_)
        if not result:
            continue
        if result == "WIN":
            w += 1
        elif result == "LOSS":
            l += 1
        else:
            p += 1
    pct = win_pct(w, l)
    units = calc_units(w, l)
    return {"w": w, "l": l, "p": p, "pct": pct, "played": w + l + p, "units": units}


def compute_summary_obj(store):
    picks = get_graded_picks(store)
    def tally(type_="spread", conf=None, side=None):
        return tally_picks(picks, type_=type_, conf=conf, side=side)
    return {
        "spread": {"all": tally(), "elite": tally(conf="elite")},
        "favdog": {"fav": tally(side="fav"), "dog": tally(side="dog")},
        "total": {"all": tally(type_="total"), "high": tally(type_="total", conf="high"), "elite": tally(type_="total", conf="elite")},
        "ouside": {"over": tally(type_="total", side="over"), "under": tally(type_="total", side="under")},
    }


def compute_summary_text(store):
    s = compute_summary_obj(store)
    def row(label, b):
        return f"  {label:<12} {b['w']}-{b['l']}-{b['p']}   ({b['pct']}%)   [graded {b['played']}]"
    return "\n".join([
        "RECORD (graded picks only)", "",
        "SPREAD (ATS)", row("All:", s["spread"]["all"]), row("Elite:", s["spread"]["elite"]), "",
        "FAV/DOG (Spread)", row("Favorites:", s["favdog"]["fav"]), row("Underdogs:", s["favdog"]["dog"]), "",
    ])

# --- Last 10 / Rolling / Weekly -----------------------------------------------

def get_actionable_picks(store, type_="spread"):
    results = []
    for r in (store.get("runs") or []):
        if r.get("burnIn"):
            continue
        for g in (r.get("games") or []):
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue
            pick = g.get("sPick") if type_ == "spread" else g.get("oPick")
            conf = g.get("sConf") if type_ == "spread" else g.get("oConf")
            if not pick or pick == "PASS" or not is_actionable(conf):
                continue
            if not isinstance(g.get("homeScore"), (int, float)) or not isinstance(g.get("awayScore"), (int, float)):
                continue
            result = grade_result(g, type_)
            final = f"{g['awayScore']}-{g['homeScore']}"
            results.append({
                "date": r.get("date"), "matchup": f"{g['away']} @ {g['home']}",
                "pick": pick, "conf": conf, "result": result or "PENDING", "final": final,
                "total": g.get("total"), "home": g.get("home"),
            })
    return results


def compute_last10(store, type_="spread"):
    return get_actionable_picks(store, type_)[-10:]


def last10_text(store, type_="spread"):
    label = "SPREAD" if type_ == "spread" else "TOTAL"
    picks = compute_last10(store, type_)
    lines = [f"LAST 10 {label} PICKS (run order)"]
    if not picks:
        return "\n".join(lines + ["  No picks yet."])
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
        units = calc_units(w, l)
        rows.append({
            "label": f"#{i + 1}\u2013{i + window}", "w": w, "l": l, "p": p,
            "pct": win_pct(w, l), "units": units,
            "startDate": to_display_date(sl[0]["date"]),
            "endDate": to_display_date(sl[-1]["date"]),
        })
    return rows


def get_monday_of_week(date_obj):
    day = date_obj.weekday()  # Mon=0
    monday = date_obj - timedelta(days=day)
    return monday.strftime("%Y-%m-%d")


def compute_weekly_breakdown(store, type_="spread"):
    picks = [p for p in get_actionable_picks(store, type_) if p.get("result") and p["result"] != "PENDING"]
    by_week = {}
    for p in picks:
        d = datetime.strptime(to_display_date(p["date"]), "%Y-%m-%d")
        key = get_monday_of_week(d)
        by_week.setdefault(key, []).append({"result": p["result"], "date": p["date"]})
    rows = []
    for week in sorted(by_week.keys()):
        entries = by_week[week]
        w = sum(1 for x in entries if x["result"] == "WIN")
        l = sum(1 for x in entries if x["result"] == "LOSS")
        p = sum(1 for x in entries if x["result"] == "PUSH")
        units = calc_units(w, l)
        rows.append({"week": week, "w": w, "l": l, "p": p, "pct": win_pct(w, l), "units": units})
    return rows[-12:]

# --- Yesterday's Recap --------------------------------------------------------

def compute_yesterday_recap(store, yesterday_date):
    yesterday_run = next((r for r in (store.get("runs") or []) if r.get("date") == yesterday_date), None)
    if not yesterday_run:
        return None
    picks = []
    units = 0
    for g in (yesterday_run.get("games") or []):
        if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
            continue
        if not isinstance(g.get("homeScore"), (int, float)) or not isinstance(g.get("awayScore"), (int, float)):
            continue
        if g.get("sPick") and g["sPick"] != "PASS" and is_actionable(g.get("sConf")):
            result = g.get("sResult") or grade_spread_pick(g)
            if result:
                picks.append({
                    "matchup": f"{g['away']} @ {g['home']}", "pick": g["sPick"],
                    "conf": g["sConf"], "result": result,
                    "final": f"{g['awayScore']}-{g['homeScore']}", "sDiff": g.get("sDiff"),
                })
                if result == "WIN":
                    units += 1
                elif result == "LOSS":
                    units -= 1.1
    if not picks:
        return None
    w = sum(1 for p in picks if p["result"] == "WIN")
    l = sum(1 for p in picks if p["result"] == "LOSS")
    p = sum(1 for p in picks if p["result"] == "PUSH")
    return {
        "date": yesterday_date, "dateDisplay": to_display_date(yesterday_date),
        "picks": picks, "tally": {"w": w, "l": l, "p": p},
        "units": round(units * 100) / 100,
    }


def build_recap_html(recap):
    if not recap or not recap["picks"]:
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
    summary_str = f'<b>{t["w"]}-{t["l"]}{f"-{t[chr(112)]}" if t["p"] else ""}</b>'
    units_color = "#10b981" if recap["units"] >= 0 else "#ef4444"
    units_str = f'<span style="color:{units_color}; font-weight:700">{"+" if recap["units"] >= 0 else ""}{recap["units"]:.2f}u</span>'
    return f'''
    <div class="card" style="border-left:4px solid #f59e0b; margin-bottom:10px;">
      <div class="summaryTitle">\U0001F4CB Yesterday\'s Recap ({esc(recap["dateDisplay"])})</div>
      <table class="data">
        <thead><tr><th>Game</th><th>Pick</th><th style="text-align:center">Result</th><th style="text-align:center">Final</th></tr></thead>
        <tbody>{rows}</tbody>
      </table>
      <div class="tiny" style="margin-top:6px">Spread: {summary_str} \u00b7 {units_str}</div>
    </div>'''

# --- Email HTML Builder -------------------------------------------------------

EMAIL_STYLES = '''<style>
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
    except (TypeError, ValueError):
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


def tally_l10(rows, type_="spread"):
    w = sum(1 for x in rows if x.get("result") == "WIN")
    l = sum(1 for x in rows if x.get("result") == "LOSS")
    p = sum(1 for x in rows if x.get("result") == "PUSH")
    units = calc_units(w, l)
    return {"w": w, "l": l, "p": p, "pct": win_pct(w, l), "units": units}


def build_combined_record_table(title, elite_bucket, split_rows, card_class="card-records"):
    def row(label, b):
        return (f'<tr><td>{label}</td><td class="">{b["w"]}-{b["l"]}-{b["p"]}</td>'
                f'<td class="">{esc(b["pct"])}%</td><td class="">{esc(fmt_units(b["units"]))}</td>'
                f'<td class="">{b["played"]}</td></tr>')
    split_body = "".join(row(f'{sr["emoji"]} {sr["label"]}', sr["data"]) for sr in split_rows)
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
    rows = ""
    for g in (games or []):
        if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
            continue
        s_pick_display = esc(g["sPick"]) if g.get("sPick") and g["sPick"] != "PASS" else '<span style="color:#9ca3af">PASS</span>'
        p_cover_str = f'<b>{g["pCover"]*100:.0f}%</b>' if g.get("pCover") is not None else '<span style="color:#9ca3af">\u2014</span>'
        p_home = f'{g["pHomeCover"]*100:.0f}%' if g.get("pHomeCover") is not None else "\u2014"
        p_away = f'{g["pAwayCover"]*100:.0f}%' if g.get("pAwayCover") is not None else "\u2014"
        s_conf_badge = f' {conf_badge(g.get("sConf"))}' if g.get("sPick") and g["sPick"] != "PASS" else ""
        margin = ("+" if g.get("margin", 0) >= 0 else "") + fmt_num(g.get("margin"), 1) if isinstance(g.get("margin"), (int, float)) and math.isfinite(g["margin"]) else "\u2014"
        t_diff = ("+" if g.get("tDiff", 0) >= 0 else "") + fmt_num(g.get("tDiff"), 1) if isinstance(g.get("tDiff"), (int, float)) and math.isfinite(g["tDiff"]) else "\u2014"
        rows += f'''<tr>
        <td style="font-weight:700">{esc(g["away"])} @ {esc(g["home"])}</td>
        <td>{s_pick_display}{s_conf_badge}<div class="tiny" style="margin-top:2px">Line {fmt_num(g.get("line"), 1)} \u00b7 proj {margin} \u00b7 sDiff {fmt_num(g.get("sDiff"), 1)}</div></td>
        <td style="text-align:center">{p_cover_str}<div class="tiny">{p_away} away / {p_home} home</div></td>
        <td style="text-align:center"><div class="tiny">O/U {fmt_num(g.get("total"), 1)} \u00b7 diff {t_diff}</div></td>
      </tr>'''
    if not rows:
        return ""
    return f'''<div class="card" style="border-left:4px solid #8b5cf6; margin-bottom:10px;">
    <div class="summaryTitle">\U0001F3AF Cover Probabilities \u2014 All Games</div>
    <table class="data">
      <thead><tr><th>Game</th><th>Spread Pick</th><th style="text-align:center">P(Cover)</th><th style="text-align:center">O/U Info</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
    <div class="tiny" style="margin-top:6px">P(Cover) / P(Hit) = probability the picked side wins. Directional % shown below.</div>
  </div>'''


def build_email_html(run, summary_obj, last10, last10_totals, weekly_spread, weekly_total, rolling_spread, rolling_total, team_records, calib_rows, yesterday_recap):
    s = summary_obj or compute_summary_obj({"runs": []})
    calib_html = build_calibration_html(calib_rows) if calib_rows else ""

    def build_picks_list(games, type_):
        is_spread = type_ == "spread"
        filtered = [g for g in games
                     if g.get("status") not in ("MISSING_ODDS", "SKIPPED")
                     and (g.get("sPick") if is_spread else g.get("oPick"))
                     and (g.get("sPick") if is_spread else g.get("oPick")) != "PASS"
                     and is_actionable(g.get("sConf") if is_spread else g.get("oConf"))]
        filtered.sort(key=lambda a: (a.get("pCover") or 0) if is_spread else (a.get("pOU") or 0), reverse=True)
        if not filtered:
            return f'<div class="no-picks">No actionable {type_} picks today.</div>'
        conf_key = "sConf" if is_spread else "oConf"
        elites = [g for g in filtered if g.get(conf_key) == "elite"]
        highs = [g for g in filtered if g.get(conf_key) == "high"]
        html = ""
        if elites:
            html += '<div style="padding:4px 0 2px; font-weight:700; font-size:13px; color:#111827;">\U0001F512 Elite</div>'
            for g in elites:
                if is_spread:
                    p_str = f' \u00b7 P={g["pCover"]*100:.0f}%' if g.get("pCover") else ""
                    proj_margin = round((g.get("hS", 0) - g.get("aS", 0)) * 10) / 10
                    fav_team = g["home"] if proj_margin >= 0 else g["away"]
                    html += f'<div style="padding:6px 0; border-bottom:1px dashed #eef2f7;">\U0001F3C0 <span class="pick-team">{esc(g["sPick"])}</span> <span class="trend-label">proj {esc(fav_team)} by {fmt_num(abs(proj_margin), 1)} \u00b7 sDiff {fmt_num(g.get("sDiff"), 1)}{p_str}</span> {conf_badge(g["sConf"])}</div>'
        if highs:
            pad = "10" if elites else "4"
            html += f'<div style="padding:{pad}px 0 2px; font-weight:700; font-size:13px; color:#047857;">\U0001F7E2 High</div>'
            for g in highs:
                if is_spread:
                    p_str = f' \u00b7 P={g["pCover"]*100:.0f}%' if g.get("pCover") else ""
                    proj_margin = round((g.get("hS", 0) - g.get("aS", 0)) * 10) / 10
                    fav_team = g["home"] if proj_margin >= 0 else g["away"]
                    html += f'<div style="padding:6px 0; border-bottom:1px dashed #eef2f7;">\U0001F3C0 <span class="pick-team">{esc(g["sPick"])}</span> <span class="trend-label">proj {esc(fav_team)} by {fmt_num(abs(proj_margin), 1)} \u00b7 sDiff {fmt_num(g.get("sDiff"), 1)}{p_str}</span> {conf_badge(g["sConf"])}</div>'
        return html

    def build_last10_card(title, picks, type_):
        rows_data = [{**p, "dateDisp": to_display_date(p["date"])} for p in picks]
        t = tally_l10(rows_data, type_)
        cols = [
            {"label": "Date", "render": lambda r: esc(r["dateDisp"])},
            {"label": "Matchup", "render": lambda r: esc(r["matchup"])},
            {"label": "Pick", "render": lambda r: f'<span class="pick-team">{esc(r["pick"])}</span>'},
            {"label": "Conf", "render": lambda r: conf_badge(r["conf"])},
            {"label": "Result", "render": lambda r: esc(r["result"])},
            {"label": "Final", "render": lambda r: esc(r["final"])},
        ]
        return (f'<div class="card card-trends"><div class="summaryTitle">{title}</div>'
                + build_table(rows_data, cols)
                + f'<div class="l10-tally">Last 10: {wlp_html(t)} \u00b7 {pct_html(t["pct"])} \u00b7 {units_html(t["units"])}</div></div>')

    def build_weekly_card(title, rows):
        cols = [
            {"label": "Week of", "render": lambda r: esc(r["week"])},
            {"label": "W-L-P", "render": wlp_html},
            {"label": "Win%", "render": lambda r: pct_html(r["pct"])},
            {"label": "Flat", "render": lambda r: units_html(r["units"])},
        ]
        return f'<div class="card card-trends"><div class="summaryTitle">{title}</div>' + build_table(rows, cols) + '</div>'

    def build_rolling_card(title, rows):
        m = re.search(r"#\d+\u2013(\d+)", rows[0]["label"]) if rows else None
        window_size = m.group(1) if m else "?"
        full_title = title.replace("{n}", str(window_size))
        cols = [
            {"label": "Window", "render": lambda r: esc(r["label"])},
            {"label": "W-L-P", "render": wlp_html},
            {"label": "Win%", "render": lambda r: pct_html(r["pct"])},
            {"label": "Flat", "render": lambda r: units_html(r["units"])},
            {"label": "Dates", "render": lambda r: f'<span class="trend-label">{esc(r["startDate"])} \u2192 {esc(r["endDate"])}</span>'},
        ]
        return (f'<div class="card card-trends"><div class="summaryTitle">{full_title}</div>'
                + '<div class="tiny" style="margin-bottom:6px">Green = above break-even (52.4%) \u00b7 Red = below \u00b7 Units at -110</div>'
                + build_table(rows, cols) + '</div>')

    def row2col(left, right):
        return f'''<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:10px;">
      <tr><td width="50%" valign="top" style="padding-right:5px;">{left}</td><td width="50%" valign="top" style="padding-left:5px;">{right}</td></tr></table>'''
    def row_full(content):
        return f'<div style="margin-bottom:10px;">{content}</div>'
    def section_label(text):
        return f'<div class="section-label">{esc(text)}</div>'

    # Build game cards
    game_cards = []
    for g in (run.get("games") or []):
        is_skipped = g.get("status") in ("MISSING_ODDS", "SKIPPED")
        if not is_skipped and g.get("sPick") and g["sPick"] != "PASS":
            proj_margin = round((g.get("hS", 0) - g.get("aS", 0)) * 10) / 10
            fav_team = g["home"] if proj_margin >= 0 else g["away"]
            p_str = f' \u00b7 P={g["pCover"]*100:.0f}%' if g.get("pCover") else ""
            spread_pick = f'<div><span class="pick-team">{esc(g["sPick"])}</span> {conf_badge(g["sConf"])} <span class="trend-label">proj {esc(fav_team)} by {fmt_num(abs(proj_margin), 1)} \u00b7 sDiff {fmt_num(g.get("sDiff"), 1)}{p_str}</span></div>'
        else:
            spread_pick = f'<div class="tiny">Spread: <span class="trend-label">{esc(g.get("status", "PASS") if is_skipped else "PASS")}</span></div>'
        if not is_skipped and g.get("oPick") and g["oPick"] != "PASS":
            clean_total = g["total"] + g["tDiff"] if isinstance(g.get("tDiff"), (int, float)) and isinstance(g.get("total"), (int, float)) else g.get("pT")
            p_str = f' \u00b7 P={g["pOU"]*100:.0f}%' if g.get("pOU") else ""
            total_pick = f'<div class="tiny">Total: <span class="pick-team">{esc(g["oPick"])} {fmt_num(g.get("total"), 1)}</span> {conf_badge(g["oConf"])} <span class="trend-label">proj {fmt_num(clean_total, 1)} \u00b7 edge {fmt_num(abs(g.get("tDiff", 0)), 1)}{p_str}</span></div>'
        else:
            total_pick = f'<div class="tiny">Total: <span class="trend-label">{esc(g.get("status", "PASS") if is_skipped else "PASS")}</span></div>'
        proj_line = ""
        if not is_skipped and isinstance(g.get("aS"), (int, float)) and isinstance(g.get("hS"), (int, float)):
            clean_total = g["total"] + g["tDiff"] if isinstance(g.get("tDiff"), (int, float)) and isinstance(g.get("total"), (int, float)) else g.get("pT")
            proj_line = f'<div class="tiny" style="margin-top:4px">\U0001F4D0 Proj: <b>{esc(g["away"])} {fmt_num(g["aS"], 1)}</b> \u2013 <b>{esc(g["home"])} {fmt_num(g["hS"], 1)}</b></div><div class="tiny">\U0001F4D0 Total: <b>{fmt_num(clean_total, 1)}</b> <span class="trend-label">(line {fmt_num(g.get("total"), 1)})</span></div>'
        trends = ""
        if g.get("trends"):
            trends = f'<div class="tiny" style="margin-top:4px"><div><b>{esc(g["away"])}:</b> {esc(g["trends"].get("away", "\u2014"))}</div><div><b>{esc(g["home"])}:</b> {esc(g["trends"].get("home", "\u2014"))}</div></div>'
        b2b = f'<div class="tiny" style="margin-top:4px">\U0001F504 {esc(g["b2bNote"])}</div>' if g.get("b2bNote") else ""
        score_line = ""
        if isinstance(g.get("awayScore"), (int, float)) and isinstance(g.get("homeScore"), (int, float)):
            score_line = f'<div class="tiny" style="margin-top:4px">Final: <b>{esc(str(g["awayScore"]))}-{esc(str(g["homeScore"]))}</b></div>'
        game_cards.append(f'''<div class="card card-games">
      <div class="summaryTitle">{esc(g["away"])} @ {esc(g["home"])} <span class="tiny">Line {fmt_num(g.get("line"), 1)} \u00b7 Total {fmt_num(g.get("total"), 1)}</span></div>
      {spread_pick}{total_pick}{proj_line}{b2b}{score_line}{trends}
    </div>''')

    games_html = ""
    for i in range(0, len(game_cards), 2):
        if i + 1 < len(game_cards):
            games_html += row2col(game_cards[i], game_cards[i + 1])
        else:
            games_html += row_full(game_cards[i])

    from zoneinfo import ZoneInfo
    timestamp = datetime.now(ZoneInfo(TIMEZONE)).strftime("%m/%d/%Y, %I:%M:%S %p")
    recap_html = build_recap_html(yesterday_recap) if yesterday_recap else ""

    return f'''<html><head>{EMAIL_STYLES}</head><body>
    <div class="wrap">
      <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:10px;">
        <tr><td><div class="h1">NCAAB Picks \u2014 {esc(run.get("dateDisplay", ""))}</div><div class="sub">High + Elite only (no medium). Units at -110. Times in {TIMEZONE}.</div></td>
          <td style="text-align:right;" valign="top"><div class="sub">{esc(timestamp)}</div></td></tr></table>
      <div style="text-align:center;color:#888;font-size:11px;margin:4px 0 12px">Filters: P(cover) \u2265 0.60 | Fav line \u2264 8 | Dogs uncapped &mdash; Ensemble: 65% Bayesian / 35% XGBoost (adaptive) &mdash; Tournament: HCA = 0 (neutral sites)</div>
      {row_full(recap_html) if recap_html else ""}
      {row_full(f'<div class="card card-picks"><div class="summaryTitle">\U0001F525 Today\'s Spread Picks (Actionable)</div>{build_picks_list(run.get("games") or [], "spread")}</div>')}
      {row_full(build_combined_record_table("\U0001F4CA Spread (ATS)", s["spread"]["elite"], [
          {"label": "Favorites", "emoji": "\u2B50", "data": s["favdog"]["fav"]},
          {"label": "Underdogs", "emoji": "\U0001F436", "data": s["favdog"]["dog"]},
      ]))}
      {section_label("Games")}
      {games_html}
      {section_label("Cover Probabilities")}
      {row_full(build_game_prob_table(run.get("games") or []))}
      {section_label("Spread Trends")}
      {row_full(build_last10_card("\U0001F9FE Last 10 Spread", last10 or [], "spread"))}
      {row_full(build_weekly_card("\U0001F4C5 Weekly Spread", weekly_spread or []))}
      {row_full(build_rolling_card("\U0001F4CA Rolling {{n}}-Pick Groups (Spread)", rolling_spread or []))}
      {section_label("Team Records")}
      {row_full(build_team_record_table(team_records or {}))}
      {(section_label("Model Calibration") + row_full(f'<div class="card"><div class="summaryTitle">\U0001F4CA P(cover) Calibration</div><div class="tiny" style="margin-bottom:6px">Expected vs actual win rate by probability bucket. Delta shows calibration error.</div>{calib_html}</div>')) if calib_html else ""}
    </div></body></html>'''

# --- Text Email Builder -------------------------------------------------------

def build_text_email(run, store):
    lines = [f"NCAAB PICKS \u2014 {run['dateDisplay']}", "", "TODAY (Actionable only)", ""]
    for g in (run.get("games") or []):
        if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
            lines.extend([f"{g['away']} @ {g['home']} | {g['status']} | {g.get('note', '')}", ""])
            continue
        lines.append(f"{g['away']} @ {g['home']} | Line: {g['home']} {'+' if g.get('line', 0) > 0 else ''}{fmt_num(g.get('line'), 1)} | Total: {fmt_num(g.get('total'), 1)}")
        if g.get("sPick") and g["sPick"] != "PASS":
            proj_margin = round((g.get("hS", 0) - g.get("aS", 0)) * 10) / 10
            fav_team = g["home"] if proj_margin >= 0 else g["away"]
            lines.append(f"  Spread: {g['sPick']} ({str(g.get('sConf', '')).upper()}) | proj {fav_team} by {fmt_num(abs(proj_margin), 1)} | edge {fmt_num(g.get('sDiff'), 1)}")
        else:
            lines.append("  Spread: PASS")
        if g.get("oPick") and g["oPick"] != "PASS":
            clean_total = g["total"] + g["tDiff"] if isinstance(g.get("tDiff"), (int, float)) and isinstance(g.get("total"), (int, float)) else g.get("pT")
            lines.append(f"  Total:  {g['oPick']} {fmt_num(g.get('total'), 1)} ({str(g.get('oConf', '')).upper()}) | proj {fmt_num(clean_total, 1)} | edge {fmt_num(abs(g.get('tDiff', 0)), 1)}")
        else:
            lines.append("  Total:  PASS")
        if g.get("trends"):
            lines.append(f"  Trends Away: {g['trends'].get('away', '\u2014')}")
            lines.append(f"  Trends Home: {g['trends'].get('home', '\u2014')}")
        lines.append("")
    lines.extend(["\u2014", compute_summary_text(store), "", last10_text(store, "spread"), "", last10_text(store, "total")])
    return "\n".join(lines)

# --- Main Pipeline ------------------------------------------------------------

def main(subject_label=None):
    if subject_label is None:
        subject_label = "[PY] NCAA Daily"
    date = today_central_yyyymmdd()
    date_display = to_display_date(date)
    store = load_store()
    defaults = load_defaults()

    print(f"\n== NCAAB Picks Pipeline -- {date_display} ==\n")

    # 1. Grade recent days
    days_to_grade = set()
    for d in range(1, 6):
        days_to_grade.add(yyyymmdd_from_central_offset(d))
    for r in (store.get("runs") or []):
        has_ungraded = any(
            g.get("status") not in ("MISSING_ODDS", "SKIPPED")
            and not isinstance(g.get("homeScore"), (int, float))
            and ((g.get("sPick") and g["sPick"] != "PASS") or (g.get("oPick") and g["oPick"] != "PASS"))
            for g in (r.get("games") or [])
        )
        if has_ungraded:
            days_to_grade.add(r.get("date"))

    yesterday = yyyymmdd_from_central_offset(1)
    for d in days_to_grade:
        grade_date_in_store(store, d)

    # 2. Fetch today's data
    print("[1/7] Fetching stats, odds, trends...")
    enhanced_stats = fetch_ncaa_stats_enhanced(date)
    odds = fetch_todays_odds()

    # Tournament team validation (March Madness / NIT)
    tourney_teams = {}
    try:
        tourney_teams = fetch_tournament_teams(date, stats_keys=enhanced_stats.get("season", {}).keys())
        if tourney_teams:
            print(f"  [tourney] {len(tourney_teams)} tournament team names loaded")
    except Exception as e:
        print(f"  [tourney] WARNING: Could not fetch tournament teams: {e}")

    if tourney_teams:
        def _norm_t(s):
            s = str(s or "").lower()
            s = re.sub(r"[^a-z0-9\s]", " ", s)
            s = re.sub(r"\bstate\b", "st", s)
            s = re.sub(r"\s+", " ", s).strip()
            return s

        def _fuzzy_tourney(name_norm):
            """Try prefix/substring match against tourney teams when exact match fails."""
            if not name_norm:
                return None
            best, best_len = None, 0
            for tk, tv in tourney_teams.items():
                if tk.startswith(name_norm) or name_norm.startswith(tk):
                    if len(tk) > best_len:
                        best, best_len = tv, len(tk)
            return best

        filtered = []
        for g in odds:
            away_n = _norm_t(g.get("away", ""))
            home_n = _norm_t(g.get("home", ""))
            away_match = tourney_teams.get(away_n) or _fuzzy_tourney(away_n)
            home_match = tourney_teams.get(home_n) or _fuzzy_tourney(home_n)
            if away_match or home_match:
                # Correct team names using ESPN's authoritative names
                if away_match and away_match != g["away"]:
                    old = g["away"]
                    g["away"] = away_match
                    print(f"  [tourney] Corrected: {old} -> {g['away']}")
                if home_match and home_match != g["home"]:
                    old = g["home"]
                    g["home"] = home_match
                    print(f"  [tourney] Corrected: {old} -> {g['home']}")
                filtered.append(g)
            else:
                print(f"  [tourney] Skipping non-tournament game: {g.get('away')} @ {g.get('home')}")
        odds = filtered

    ats = fetch_ats_trends()
    ou = fetch_ou_trends()
    try:
        b2b_teams = detect_b2b()
    except Exception:
        b2b_teams = set()

    base_w = store.get("weights") or defaults["DEFAULT_W"]
    base_w_var = store.get("weightsVar") or defaults["DEFAULT_W_VAR"]

    # Blend season + last 10 games for recent form
    stats = blend_base(enhanced_stats["season"], enhanced_stats.get("last10"), base_w.get("recentWeight", 0.35))

    # Cache stats to disk
    try:
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        cache_dir = os.path.join(scripts_dir, "..", "data", "stats_cache")
        os.makedirs(cache_dir, exist_ok=True)
        with open(os.path.join(cache_dir, f"{date}.json"), "w") as f:
            json.dump(enhanced_stats, f)
    except Exception:
        pass

    # 3. B2B rest + Kalman state
    print("[2/7] Applying B2B rest + Kalman filter...")
    adj_result = apply_b2b_adjustment(stats, b2b_teams, odds)
    adjusted_stats = adj_result["adjusted"]
    b2b_notes = adj_result["b2bNotes"]
    a = get_avgs(adjusted_stats)

    kalman_state = load_kalman_state()
    if not (kalman_state.get("teams") or {}):
        kalman_state = initialize_kalman(stats)

    # Feed newly graded games into Kalman
    newly_graded = []
    for r in (store.get("runs") or [])[-14:]:
        if r.get("date") == date:
            continue
        for g in (r.get("games") or []):
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue
            if not isinstance(g.get("homeScore"), (int, float)) or not isinstance(g.get("awayScore"), (int, float)):
                continue
            if not isinstance(g.get("hS"), (int, float)) or not isinstance(g.get("aS"), (int, float)):
                continue
            g["_kalmanDate"] = r.get("date")
            newly_graded.append(g)
    batch_update(kalman_state, newly_graded)
    apply_daily_drift(kalman_state, date)

    # 3b. Self-tune weights
    yesterday_run = next((r for r in (store.get("runs") or []) if r.get("date") == yesterday), None)
    recent_graded = []
    if yesterday_run:
        for g in (yesterday_run.get("games") or []):
            if g.get("status") in ("MISSING_ODDS", "SKIPPED"):
                continue
            if not isinstance(g.get("homeScore"), (int, float)) or not isinstance(g.get("awayScore"), (int, float)):
                continue
            if not g.get("_features"):
                continue
            recent_graded.append(g)
    if recent_graded and store.get("lastTuneDate") != date:
        tuned = tune_weights(base_w, base_w_var, recent_graded)
        base_w = tuned["W"]
        base_w_var = tuned["W_var"]
        store["weights"] = base_w
        store["weightsVar"] = base_w_var
        store["lastTuneDate"] = date
        print(f"[3b] Weights tuned on {len(recent_graded)} graded games from {yesterday}")
    elif store.get("lastTuneDate") == date:
        print("[3b] Weights already tuned today -- skipping")

    # 3c. Compute dynamic residualVar
    dynamic_residual_var = compute_residual_var(store.get("runs") or [])
    store["residualVar"] = dynamic_residual_var

    # 4. Analyze each game
    print(f"[3/7] Analyzing {len(odds)} games...")
    games = []
    for g in odds:
        if not isinstance(g.get("line"), (int, float)) or not isinstance(g.get("total"), (int, float)):
            games.append({**g, "status": "MISSING_ODDS"})
            continue
        game_stats = blend_for_game(
            adjusted_stats, enhanced_stats.get("home"), enhanced_stats.get("away"),
            g["home"], g["away"], base_w.get("locationWeight", 0.25)
        )
        game_avgs = get_avgs(game_stats)
        injury_adj = None
        r = analyze_game(g, game_stats, game_avgs, base_w, injury_adj, kalman_state, base_w_var, dynamic_residual_var)
        if not r:
            games.append({**g, "status": "SKIPPED"})
            continue
        games.append(r)

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
            parts = [f'{g["away"]}: {away_b2b}' if away_b2b else None, f'{g["home"]}: {home_b2b}' if home_b2b else None]
            g["b2bNote"] = " | ".join(p for p in parts if p)

    # -- Ensemble overlay (XGBoost + Ridge + Bayesian blend) --
    if ENSEMBLE_AVAILABLE:
        ens_models = train_ensemble(store, min_train=50, max_xgb_weight=0.35)
        if ens_models:
            wb, wx = ens_models["weights"]
            print(f"[ENS] Trained on {ens_models['n_trained']} games | weights: Bayes={wb:.2f} XGB={wx:.2f}")
            for g in games:
                if g.get("sPick") == "PASS" and g.get("oPick") == "PASS":
                    continue
                bayes_p = g.get("pHomeCover", 0.5)
                ens = predict_ensemble(ens_models, g, bayes_p)
                g["ens_pHomeCover"] = round(ens["pHomeCover"], 3)
                g["ens_pAwayCover"] = round(ens["pAwayCover"], 3)
                g["xgb_pHomeCover"] = round(ens.get("xgb_raw", 0.5), 3)
                g["xgb_pAwayCover"] = round(1.0 - ens.get("xgb_raw", 0.5), 3)
                g["pHomeCover"] = round(ens["pHomeCover"], 3)
                g["pAwayCover"] = round(ens["pAwayCover"], 3)
                if g.get("sPick") and g["sPick"] != "PASS":
                    g["pCover"] = round(max(ens["pHomeCover"], ens["pAwayCover"]), 3)
                g["_ensWeights"] = ens["weights"]
        else:
            print("[ENS] Not enough history to train, using Bayesian-only")
    else:
        print("[ENS] xgboost/sklearn not installed, using Bayesian-only")

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
    team_recs = compute_team_records(store)
    calib_rows = build_calibration_table(store)
    yesterday_recap = compute_yesterday_recap(store, yesterday)

    # 9. Send
    print("[6/7] Sending email...")
    html = build_email_html(run, summary_obj, l10, l10t, weekly_spread, weekly_total, rolling_spread, rolling_total, team_recs, calib_rows, yesterday_recap)
    text = build_text_email(run, store)
    subject = f"{subject_label} Picks \u2014 {run['dateDisplay']}"
    send_email(subject, text, html)

    # 10. Summary
    print(f"\nDone: {subject}")


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        print(err, file=sys.stderr)
        sys.exit(1)

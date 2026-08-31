"""
slate_wrc_form.py — Slate scouting report: team wRC+ platoon splits x starter recent form.

Joins three existing repo artifacts for a given slate date:

  1. ``MLBstrikeouts/data/mlb-all-ml.json``   -> today's games, odds, totals,
     probable starters and their throwing hand.
  2. ``MLBstrikeouts/data/mlb-team-woba-splits.json`` -> self-computed
     park-adjusted wRC+ by team, split by pitcher hand, pitcher role and
     window (``build_team_woba_splits.py``).
  3. ``data/pitcher_cache/mlb/game_logs_2026.json`` -> per-game pitching lines,
     from which season / last-5-start / last-3-start form is derived, plus the
     relief outings threaded through that window (see ``form_for``). The same
     file's non-start lines also supply each team's bullpen windows
     (``pen_table``) — no separate bullpen source exists or is needed.

Why the self-computed splits and not the FanGraphs snapshot in
``mlb-team-wrc.json``: that file is a manual browser capture frozen at
2026-07-28 with no refresh path (FanGraphs is Cloudflare-walled), and the
dashboard itself moved off it — ``mlb-fade-ml.js`` reads the wOBA splits. Over
the season window the two agree to a mean absolute 4.0 (vs LHP) / 3.6 (vs RHP),
so this is the same metric definition kept current rather than a new one.

The metric matched is FanGraphs' — offense against ALL pitchers of the
starter's hand (``--role all``, the default). The table also carries a
starters-only split, which is available via ``--role sp`` and is a cleaner
description of the listed arm in isolation, but it is not the default: see the
note on ``PITCHER_ROLE`` below.

The self-computed metric is an approximation — fixed linear weights, total-bases
park factor as the run-environment proxy — so treat it as a relative gauge, not
a figure to quote as a team's published wRC+.

Output is a per-game scouting table, not a betting model: nothing here feeds a
pick, gate, size or grade. The per-game "[context] full-game offense" line is
explicitly not a driver — see ``full_game_wrc``.

Usage
-----
    python MLBstrikeouts/scripts/slate_wrc_form.py                 # today's slate
    python MLBstrikeouts/scripts/slate_wrc_form.py --date 2026-08-15
    python MLBstrikeouts/scripts/slate_wrc_form.py --json          # machine-readable

The all-ML payload only carries the current day, so a future slate has to be
supplied by hand from the board::

    python MLBstrikeouts/scripts/slate_wrc_form.py --slate-file tomorrow.json

The file is a list of game objects in the same shape ``mlb-all-ml.json`` uses
(``away``/``home``, ``away_pitcher``/``home_pitcher``, ``away_hand``/``home_hand``,
``away_ml``/``home_ml``, ``total_line``, ``over_ml``/``under_ml``, ``commence``).
Pitcher names are resolved loosely, so a board abbreviation like ``M SOROKA``
matches the ``Michael Soroka`` in the game logs.
"""

from __future__ import annotations

import argparse
import json
import unicodedata
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ALL_ML = REPO / "MLBstrikeouts" / "data" / "mlb-all-ml.json"
TEAM_WRC = REPO / "MLBstrikeouts" / "data" / "mlb-team-woba-splits.json"
GAME_LOGS = REPO / "data" / "pitcher_cache" / "mlb" / "game_logs_2026.json"

PA_SPLITS = REPO / "data" / "pitcher_cache" / "mlb" / "team_pa_splits_2026.json"

# Offense is read against ALL pitchers of the starter's hand. This is the
# definition the report shipped with and it is the one that has graded well:
# on 2026-08-15 it went 4-0 on confirmed results.
#
# A starters-only ("sp") read is available via --role and is defensible in
# theory — it isolates the arm actually being scouted, and it does surface real
# structure the blended figure hides (PHI is 75 vs LH starters but 121 vs LH
# relievers, both rolling up to 95). It is NOT the default, because every
# recommendation it moved on 2026-08-15 moved the wrong way. Change it only
# with results behind the change.
PITCHER_ROLE = "all"

# 2026-08-18 (user): primary moved off "season" — five months of April-July
# lineups describe rosters that no longer exist, and it kept reading as the
# headline number. "last30" is the largest current window (600-900 PA cells
# vs RHP, ~300+ vs LHP, all clearing MIN_WINDOW_PA); the secondary stays
# scoped to the trade deadline so August reads respect roster changes. Both
# are selectable with --window / --secondary-window; "last7" is the shortest
# the builder emits and is thin enough that MIN_WINDOW_PA will suppress most
# of its cells. Advisory-only change: nothing downstream consumes the scout,
# so no backtest gate applies (the offense window has never been swept —
# only the pitcher-form window was, see slate_wrc_form_sweep.py).
# 2026-08-29 (user): last30 -> last20. Measured first -- swapping the offense
# window changes nothing descriptively: correlation between the mismatch and
# runs the offense actually scores is +0.1241 (L20) vs +0.1258 (L30) vs
# +0.1260 (L45) over ~3,000 pitcher-sides, i.e. flat across the whole range.
# The trade is recency for coverage: L20 median PA ~403 vs L30 ~630, and 209
# fewer sides clear MIN_WINDOW_PA, so a few more cells render as a bare PA
# count. Taken for recency -- L20 respects roster and lineup changes sooner --
# not because it predicts better. Nothing downstream bets on this column
# (see build_slate_scout.py header); the mismatch has no market edge at any
# window, so this is a scouting-view change and no backtest gate applies.
PRIMARY_WINDOW = "last20"
SECONDARY_WINDOW = "deadline"

# Plate appearances below which a window's wRC+ is too thin to lean on.
#
# 2026-08-26: raised 75 -> 150 on backtest evidence (scripts/backtest_scout.py).
# Team wRC+ needs several hundred PA before it describes anything but schedule
# and BABIP luck, and the short windows are nowhere near that: median PA runs
# L30 630 / L20 403 / L15 301 / L7 **125**. At the old threshold a 125-PA week
# rendered as a hard number and got read as signal -- a 182 in the L7 was called
# "the loudest window on the board" more than once. It was noise. The measured
# consequence: across 10 slates, offenses whose ladder was cold in all four
# windows scored 4.64 runs and hot-aligned ones scored 4.50, i.e. no separation
# at all, while betting the ladder cold-side under went 17-15 (-0.4 points
# against a bet-every-under baseline).
#
# 150 suppresses the worst cells (most L7s, thin L15s) to a bare PA count while
# leaving L30/L20 readable. It is a display-honesty fix, not a signal fix: the
# ladder is CONTEXT now (see MLBstrikeouts/CLAUDE.md), never a play trigger.
MIN_WINDOW_PA = 150

# scout-ml-both-halves-aligned only (user, 2026-08-31): the tier-4 aligned-ML
# rule is evaluated at its own 75-PA floor -- the floor the rule lived under
# when it went 3-1 (8/21-8/24) before the 8/26 raise to 150 stopped it from
# ever qualifying (most L7 cells run ~125 PA and render null at 150).
# STRICTLY scoped: every displayed cell and every other rule stays on
# MIN_WINDOW_PA. Emitted as `aligned_ml` on the game entry and logged as
# SHADOW -- n=4 lifetime and the ladder measured inert for runs, so it bets
# nothing until it clears the 25-play gate.
ALIGNED_ML_MIN_PA = 75

# Rolling offense windows carried on every side of the payload (2026-08-18,
# user): one window alone reverses reads — DET and WSH both flipped between
# L30 and L15 in a single week — so the tab shows the ladder and the reader
# sees the trend, not one snapshot. Cells under MIN_WINDOW_PA still render
# as sample-size only.
OFFENSE_WINDOWS = ("last30", "last20", "last15", "last7")

# Team bullpen rolling windows, in calendar DAYS strictly before the slate
# date (2026-08-22, user): totals and team totals lean on 3-4 relief innings
# a night, and the report carried nothing about the arms throwing them. The
# labels match the offense ladder so the two read side by side, and the
# innings come from the same game-log cache — every non-start line is
# bullpen work, so no new data source is involved.
PEN_WINDOW_DAYS = {"last30": 30, "last20": 20, "last15": 15, "last7": 7}

# Below this many relief innings a window's rates are one blown save wide,
# so the cell keeps its workload (g, ip) and withholds the rates — the same
# value-suppression MIN_WINDOW_PA applies to thin offense cells.
MIN_PEN_IP = 9.0

# Recent-form windows, in starts.
RECENT_N = 5
HOT_COLD_N = 3

# Below this many innings per start the arm is being used as an opener, and its
# rate stats describe one time through the order rather than a starter's night.
OPENER_IP_PER_GS = 3.5

# Fewer than this many starts and the rates are noise; the report says so
# rather than presenting them at face value.
THIN_SAMPLE_GS = 5

# Days since the last start beyond which the arm has been away (IL, demotion,
# call-up) and "recent form" no longer describes the pitcher taking the ball.
LAYOFF_DAYS = 12

# A healthy five-start window spans about five turns of a rotation. Much wider
# than this and the window is reaching back across an absence, so the rates
# blend two different stretches of season.
STALE_WINDOW_DAYS = 45

# Relief outings inside the recent-start window past which the arm is a
# swingman, not a starter who went missing. The start-only line then describes
# a fraction of his season: Mlodzinski's "last 5 starts" on 2026-08-17 read
# 6.3% K over 96 days, while the 15 relief outings threaded through that window
# ran 17.4% K — the collapse was a role change, not a decline.
SWINGMAN_MIN_G = 2

# League-average reference points for the 2026 season, used only to label a
# starter's rate stats. Kept local so the report never reaches the network.
LG_K_PCT = 22.0
LG_BB_PCT = 8.3


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _load(path):
    with open(path) as fh:
        return json.load(fh)


def load_slate(slate_date, slate_file=None):
    """
    Return the list of games on ``slate_date``.

    Reads the all-ML payload by default. That payload only ever holds the
    current day, so ``slate_file`` lets a future slate be supplied by hand.
    """
    if slate_file:
        games = _load(slate_file)
        if isinstance(games, dict):
            games = games.get("today") or games.get("games") or []
        return [g for g in games if g.get("date") in (None, slate_date)]
    games = _load(ALL_ML).get("today", [])
    return [g for g in games if g.get("date") == slate_date]


def load_wrc():
    """Return ``(windows_dict, through_date)`` from the self-computed splits."""
    blob = _load(TEAM_WRC)
    return blob.get("windows", {}), blob.get("throughDate")


def wrc_cell(windows, window, team, hand, role=PITCHER_ROLE):
    """
    Pull one ``{wrcplus, pa}`` cell out of the splits table.

    Path is window -> team -> role -> vsLHP|vsRHP. Falls back to the role-less
    ("all") node when a team has no plate appearances against that role inside
    the window, which happens in the short deadline window.
    """
    if not hand:
        return None
    side = "vsLHP" if hand.upper().startswith("L") else "vsRHP"
    node = ((windows.get(window) or {}).get("teams") or {}).get(team) or {}
    cell = node.get(side) if role == "all" else (
        (node.get(role) or {}).get(side) or node.get(side))
    if not cell or cell.get("wrcplus") is None:
        return None
    return {
        "wrcplus": cell["wrcplus"],
        "pa": cell.get("pa"),
        "role": role if (node.get(role) or {}).get(side) else "all",
    }


def organize_starts(logs):
    """Group starting-pitcher game lines by pitcher name, oldest first."""
    by_name = {}
    for row in logs:
        if not row.get("is_start"):
            continue
        by_name.setdefault(row.get("pitcher_name"), []).append(row)
    for rows in by_name.values():
        rows.sort(key=lambda r: r.get("game_date") or "")
    return by_name


def organize_appearances(logs):
    """
    Group EVERY pitching line by name, oldest first — relief included.

    Kept separate from ``organize_starts`` rather than replacing it, because
    ``resolve_pitcher`` disambiguates duplicate surnames by start count, and
    relief appearances would hand that tie-break to the wrong Rogers.
    """
    by_name = {}
    for row in logs:
        by_name.setdefault(row.get("pitcher_name"), []).append(row)
    for rows in by_name.values():
        rows.sort(key=lambda r: r.get("game_date") or "")
    return by_name


def _deaccent(text):
    return "".join(c for c in unicodedata.normalize("NFD", text)
                   if unicodedata.category(c) != "Mn")


def resolve_pitcher(name, by_name, team=None):
    """
    Map a board-style pitcher name onto a key in the game-log index.

    Sportsbook boards abbreviate to "M SOROKA"; the logs carry "Michael
    Soroka", sometimes accented ("Eury Pérez"). Match on surname plus first
    initial, then disambiguate duplicate surnames by team and start count —
    Trevor/Taylor/Tyler Rogers all coexist, and only one of them starts.
    """
    if not name:
        return None
    if name in by_name:
        return name

    key = _deaccent(name).upper().replace(".", "").split()
    if not key:
        return None
    surname, initial = key[-1], key[0][0]

    matches = [
        n for n in by_name
        if _deaccent(n).upper().split()[-1] == surname
        and _deaccent(n).upper()[0] == initial
    ]
    if not matches:
        return None
    if len(matches) > 1 and team:
        on_team = [n for n in matches
                   if any(r.get("team") == team for r in by_name[n])]
        if on_team:
            matches = on_team
    # Prefer the arm that actually starts.
    return max(matches, key=lambda n: len(by_name[n]))


# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------

def aggregate(starts, unit="gs"):
    """
    Collapse a list of game lines into rate stats. ``None`` if empty.

    ``unit`` names the appearance count: ``gs`` for start-only windows,
    ``g`` for windows that mix starts and relief outings, where "innings per
    start" would be a lie.
    """
    if not starts:
        return None
    outs = sum(int(s.get("outs") or 0) for s in starts)
    bf = sum(int(s.get("bf") or 0) for s in starts)
    k = sum(int(s.get("k") or 0) for s in starts)
    bb = sum(int(s.get("bb") or 0) for s in starts)
    h = sum(int(s.get("h") or 0) for s in starts)
    hr = sum(int(s.get("hr") or 0) for s in starts)
    er = sum(int(s.get("er") or 0) for s in starts)
    ip = outs / 3.0
    return {
        unit: len(starts),
        "ip": round(ip, 1),
        f"ip_per_{unit}": round(ip / len(starts), 2),
        "era": round(er * 9.0 / ip, 2) if ip else None,
        "whip": round((h + bb) / ip, 2) if ip else None,
        "k_pct": round(100.0 * k / bf, 1) if bf else None,
        "bb_pct": round(100.0 * bb / bf, 1) if bf else None,
        "k_bb_pct": round(100.0 * (k - bb) / bf, 1) if bf else None,
        "hr9": round(hr * 9.0 / ip, 2) if ip else None,
        "k": k,
        "bb": bb,
        "hr": hr,
        "er": er,
    }


def form_for(starts, appearances, before_date):
    """
    Season / last-N / last-3 aggregates for starts strictly before a date.

    The start-only windows come first because tonight's job is starting, and a
    two-inning relief line does not describe that. But they are not the whole
    story for a swingman, so two appearance-based reads sit alongside them:

      ``recent_all``  last ``RECENT_N`` outings of any kind, with ``gs`` saying
                      how many were starts.
      ``relief``      relief work from the start of the recent-start window
                      onward — the innings the start-only line drops on the
                      floor, and usually the reason that line looks broken.
    """
    prior = [s for s in starts if (s.get("game_date") or "") < before_date]
    apps = [a for a in appearances if (a.get("game_date") or "") < before_date]

    window = prior[-RECENT_N:]
    recent_apps = apps[-RECENT_N:]
    # Anchored at the window's first start rather than a fixed day count, so
    # the relief line covers exactly the span the start-only rates claim to.
    relief = []
    if window:
        opened = window[0].get("game_date") or ""
        relief = [a for a in apps
                  if not a.get("is_start") and (a.get("game_date") or "") >= opened]

    recent_all = aggregate(recent_apps, unit="g")
    if recent_all:
        recent_all["gs"] = sum(1 for a in recent_apps if a.get("is_start"))
    relief_form = aggregate(relief, unit="g")
    if relief_form:
        relief_form["from"] = relief[0].get("game_date")
        relief_form["to"] = relief[-1].get("game_date")

    return {
        "season": aggregate(prior),
        "recent": aggregate(window),
        "hot": aggregate(prior[-HOT_COLD_N:]),
        "recent_all": recent_all,
        "relief": relief_form,
        "last_starts": [
            {
                "date": s.get("game_date"),
                "opp": s.get("opp"),
                "ip": round(int(s.get("outs") or 0) / 3.0, 1),
                "k": int(s.get("k") or 0),
                "bb": int(s.get("bb") or 0),
                "h": int(s.get("h") or 0),
                "hr": int(s.get("hr") or 0),
                "er": int(s.get("er") or 0),
            }
            for s in prior[-RECENT_N:]
        ],
    }


def trend(form):
    """Recent-minus-season deltas on the rates that matter most."""
    season, recent = form.get("season"), form.get("recent")
    if not season or not recent:
        return {}
    out = {}
    for key in ("era", "whip", "k_pct", "bb_pct", "ip_per_gs", "hr9"):
        a, b = recent.get(key), season.get(key)
        if a is not None and b is not None:
            out[key] = round(a - b, 2)
    return out


# ---------------------------------------------------------------------------
# Bullpens
# ---------------------------------------------------------------------------

def pen_table(logs, before_date):
    """
    Per-team bullpen form over rolling day windows, with a league rank.

    Each window aggregates a team's relief lines (every non-start row in the
    game logs) inside the last N calendar days strictly before the slate
    date — the same "through yesterday" cut the offense ladder uses. Cells
    are ``aggregate`` outputs in ``g`` units plus ``rank``: the team's ERA
    position among clubs with a readable window (1 = stingiest), so a 3.80
    carries league context on its face. Windows under MIN_PEN_IP keep their
    workload and withhold the rates, mirroring the thin-cell handling on the
    offense side.
    """
    relief_by_team = {}
    for row in logs:
        if row.get("is_start"):
            continue
        team = row.get("team")
        if team and (row.get("game_date") or "") < before_date:
            relief_by_team.setdefault(team, []).append(row)

    table = {}
    for label, days in PEN_WINDOW_DAYS.items():
        start = (date.fromisoformat(before_date)
                 - timedelta(days=days)).isoformat()
        cells = {}
        for team, rows in relief_by_team.items():
            cell = aggregate(
                [r for r in rows if (r.get("game_date") or "") >= start],
                unit="g")
            if cell and (cell["ip"] or 0) < MIN_PEN_IP:
                cell = {k: (v if k in ("g", "ip") else None)
                        for k, v in cell.items()}
            cells[team] = cell
        by_era = sorted((t for t, c in cells.items()
                         if c and c.get("era") is not None),
                        key=lambda t: cells[t]["era"])
        for i, team in enumerate(by_era, 1):
            cells[team]["rank"] = i
        for team, cell in cells.items():
            if cell is not None:
                cell.setdefault("rank", None)
            table.setdefault(team, {})[label] = cell
    return table


# ---------------------------------------------------------------------------
# Matchup scoring
# ---------------------------------------------------------------------------

def _days_between(start, end):
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def role_flags(form, slate_date):
    """
    Label usage patterns that make the rate stats read differently.

    The layoff and stale-window flags matter most: a starter back from two
    months out still has a full "last 5 starts" line, but it describes a
    different pitcher in a different month. ``swingman`` is the companion the
    other two need — it says the gap those flags found was filled with relief
    work rather than absence, so the arm is current even when the start line
    is not.
    """
    season = form.get("season") or {}
    recent = form.get("recent") or season
    starts = form.get("last_starts") or []
    flags = []

    ip_per_gs = (recent or {}).get("ip_per_gs")
    if ip_per_gs is not None and ip_per_gs < OPENER_IP_PER_GS:
        flags.append("opener")
    if season.get("gs", 0) < THIN_SAMPLE_GS:
        flags.append("thin-sample")

    if starts:
        idle = _days_between(starts[-1]["date"], slate_date)
        if idle > LAYOFF_DAYS:
            flags.append(f"layoff-{idle}d")
        span = _days_between(starts[0]["date"], starts[-1]["date"])
        if len(starts) > 1 and span > STALE_WINDOW_DAYS:
            flags.append(f"stale-window-{span}d")

    relief = form.get("relief") or {}
    if relief.get("g", 0) >= SWINGMAN_MIN_G:
        flags.append(f"swingman-{relief['g']}g")
    return flags


def matchup_wrc(windows, offense_team, pitcher_hand, window=PRIMARY_WINDOW,
                role=PITCHER_ROLE):
    """Opposing offense's wRC+ against pitchers of this starter's hand."""
    cell = wrc_cell(windows, window, offense_team, pitcher_hand, role=role)
    return cell["wrcplus"] if cell else None


def staff_profiles(pa_rows):
    """
    Per pitching staff: starter share of PA and bullpen handedness mix.

    Each row in the PA splits is one (game, batting team, opposing pitcher
    hand, role) tally, so grouping by ``opp`` describes the staff that did the
    pitching rather than the offense that batted.
    """
    staff = {}
    for row in pa_rows:
        team = row.get("opp")
        if not team:
            continue
        acc = staff.setdefault(team, {"sp": 0, "rp": 0, "rp_L": 0, "rp_R": 0})
        role = "sp" if row.get("role") == "SP" else "rp"
        pa = int(row.get("pa") or 0)
        acc[role] += pa
        if role == "rp":
            acc["rp_" + ("L" if row.get("opp_hand") == "L" else "R")] += pa

    out = {}
    for team, acc in staff.items():
        total = acc["sp"] + acc["rp"]
        bullpen = acc["rp"] or 1
        out[team] = {
            "sp_share": acc["sp"] / total if total else 0.57,
            "bp_lhp": acc["rp_L"] / bullpen,
            "bp_rhp": acc["rp_R"] / bullpen,
        }
    return out


def full_game_wrc(windows, offense_team, opp_team, starter_hand, staff,
                  window=PRIMARY_WINDOW):
    """
    Expected offensive output across a whole game, in wRC+ units. CONTEXT ONLY.

    Blends the offense against the starter's hand with the offense against the
    opposing bullpen, split by that bullpen's actual handedness mix and
    weighted by that staff's starter share of plate appearances.

    Do not drive plays off this. Bullpens are far more alike across teams than
    starters are, so blending them in compresses the spread — on 2026-08-15 the
    starter score ranged 104 points across the slate and this figure ranged 18.
    Small wiggles inside that compressed range read as edges and are not: it
    ranked BOS @ PIT the top run environment on the slate and the game produced
    one run. Every recommendation it changed that day was wrong (0-for-4). It is
    printed as a sanity check on the bullpen side of a game, nothing more.
    """
    sp_cell = wrc_cell(windows, window, offense_team, starter_hand, role="sp")
    if not sp_cell:
        return None
    prof = staff.get(opp_team) or {"sp_share": 0.57, "bp_lhp": 0.30, "bp_rhp": 0.70}

    rp_l = wrc_cell(windows, window, offense_team, "L", role="rp")
    rp_r = wrc_cell(windows, window, offense_team, "R", role="rp")
    weights, values = [], []
    if rp_l:
        weights.append(prof["bp_lhp"])
        values.append(rp_l["wrcplus"])
    if rp_r:
        weights.append(prof["bp_rhp"])
        values.append(rp_r["wrcplus"])
    if not weights or sum(weights) == 0:
        return sp_cell["wrcplus"]

    bullpen = sum(w * v for w, v in zip(weights, values)) / sum(weights)
    share = prof["sp_share"]
    return round(share * sp_cell["wrcplus"] + (1 - share) * bullpen)


def platoon_gap(windows, offense_team, window=PRIMARY_WINDOW, role=PITCHER_ROLE):
    """vsLHP minus vsRHP for an offense — how lopsided its platoon profile is."""
    lhp = matchup_wrc(windows, offense_team, "L", window, role=role)
    rhp = matchup_wrc(windows, offense_team, "R", window, role=role)
    if lhp is None or rhp is None:
        return None
    return lhp - rhp


def mismatch(form, opp_wrc):
    """
    Composite matchup-mismatch score for one starter, roughly in wRC+ units.

    Positive = the offense he faces is better than the arm he brings, i.e. the
    matchup leans toward the bats. Built from three additive pieces:

      * opponent wRC+ vs his hand, relative to a league-average 100
      * his recent (last-5-start) ERA relative to a 4.20 league baseline
      * his recent K-BB% relative to league average, inverted

    The weights are deliberately blunt — this is a scouting sort key, not a
    calibrated projection, and nothing downstream consumes it.
    """
    recent = form.get("recent") or form.get("season")
    if not recent or opp_wrc is None:
        return None
    score = float(opp_wrc - 100)
    if recent.get("era") is not None:
        score += (recent["era"] - 4.20) * 8.0
    if recent.get("k_bb_pct") is not None:
        score -= (recent["k_bb_pct"] - (LG_K_PCT - LG_BB_PCT)) * 1.2
    return round(score, 1)


def build_report(slate_date, slate_file=None, role=PITCHER_ROLE,
                 window=PRIMARY_WINDOW, secondary=SECONDARY_WINDOW):
    games = load_slate(slate_date, slate_file)
    wrc, wrc_as_of = load_wrc()
    logs = _load(GAME_LOGS)
    starts_by_name = organize_starts(logs)
    apps_by_name = organize_appearances(logs)
    staff = staff_profiles(_load(PA_SPLITS))
    pens = pen_table(logs, slate_date)

    rows = []
    for game in games:
        entry = {
            "matchup": f"{game['away']} @ {game['home']}",
            "away": game["away"],
            "home": game["home"],
            "commence": game.get("commence"),
            "away_ml": game.get("away_ml"),
            "home_ml": game.get("home_ml"),
            "total": game.get("total_line"),
            "over_ml": game.get("over_ml"),
            "under_ml": game.get("under_ml"),
            "sides": {},
        }
        for side, offense in (("away", game["home"]), ("home", game["away"])):
            listed = game.get(f"{side}_pitcher")
            hand = game.get(f"{side}_hand")
            name = resolve_pitcher(listed, starts_by_name, game[side])
            form = (form_for(starts_by_name.get(name, []),
                             apps_by_name.get(name, []), slate_date)
                    if name else {})
            opp_wrc = matchup_wrc(wrc, offense, hand, window, role=role)
            recent_cell = wrc_cell(wrc, secondary, offense, hand, role=role)
            flags = role_flags(form, slate_date)
            if recent_cell and (recent_cell["pa"] or 0) < MIN_WINDOW_PA:
                # Below ~75 PA the wRC+ conversion is unstable enough to print
                # nonsense (a 9-PA window returned -66), so withhold the value
                # and surface only the sample size.
                flags.append(f"thin-{secondary}-{recent_cell['pa']}pa")
                recent_cell = dict(recent_cell, wrcplus=None)
            # The rolling ladder, thin cells value-suppressed the same way.
            wrc_windows = {}
            aligned75 = []
            for win in OFFENSE_WINDOWS:
                cell = wrc_cell(wrc, win, offense, hand, role=role)
                # The aligned-ML rule's own 75-PA floor (see ALIGNED_ML_MIN_PA);
                # captured before the display suppression below.
                aligned75.append(cell["wrcplus"] if cell
                                 and (cell["pa"] or 0) >= ALIGNED_ML_MIN_PA
                                 else None)
                if cell and (cell["pa"] or 0) < MIN_WINDOW_PA:
                    cell = dict(cell, wrcplus=None)
                wrc_windows[win] = cell
            entry["sides"][side] = {
                "pitcher": name or listed,
                "listed_as": listed,
                "resolved": bool(name),
                "hand": hand,
                "team": game[side],
                "opponent_offense": offense,
                "opp_wrc_vs_hand": opp_wrc,
                "opp_wrc_game": full_game_wrc(wrc, offense, game[side], hand, staff, window),
                "opp_wrc_recent": recent_cell["wrcplus"] if recent_cell else None,
                "opp_wrc_recent_pa": recent_cell["pa"] if recent_cell else None,
                "opp_wrc_windows": wrc_windows,
                "opp_platoon_gap": platoon_gap(wrc, offense, window, role=role),
                "form": form,
                "trend": trend(form),
                # The pen that inherits this starter's game — keyed to his own
                # team, independent of whether the probable resolved.
                "pen": pens.get(game[side]),
                "flags": flags,
                "mismatch": mismatch(form, opp_wrc),
                "aligned75": aligned75,
            }
        # Full-game offensive expectation for each club. The away starter is
        # scouted against the home offense, so sides["away"] carries the home
        # bats and vice versa.
        home_off = entry["sides"]["away"]["opp_wrc_game"]
        away_off = entry["sides"]["home"]["opp_wrc_game"]
        entry["home_offense_game"] = home_off
        entry["away_offense_game"] = away_off
        if home_off is not None and away_off is not None:
            # Run environment for the total, and which way the bats tilt.
            entry["game_offense"] = round((home_off + away_off) / 2)
            entry["offense_edge"] = away_off - home_off
        else:
            entry["game_offense"] = None
            entry["offense_edge"] = None
        # scout-ml-both-halves-aligned (tier 4), at its own 75-PA floor: one
        # offense hot-aligned (all four windows >= 110) while the other is
        # cold-aligned (all <= 90) -> back the hot side's team ML. Sides carry
        # the OPPOSING offense's ladder, so the away offense lives on the home
        # side and vice versa. SHADOW only until 25 graded plays.
        def _cls75(lad):
            if any(v is None for v in lad):
                return None
            if all(v <= 90 for v in lad):
                return "cold"
            if all(v >= 110 for v in lad):
                return "hot"
            return "mid"
        away_off_lad = entry["sides"]["home"]["aligned75"]
        home_off_lad = entry["sides"]["away"]["aligned75"]
        a_cls, h_cls = _cls75(away_off_lad), _cls75(home_off_lad)
        if {a_cls, h_cls} == {"hot", "cold"}:
            pick_side = "away" if a_cls == "hot" else "home"
            entry["aligned_ml"] = {
                "pick": game[pick_side],
                "ml": game.get(f"{pick_side}_ml"),
                "away_offense": away_off_lad,
                "home_offense": home_off_lad,
                "min_pa": ALIGNED_ML_MIN_PA,
            }
        else:
            entry["aligned_ml"] = None
        rows.append(entry)

    rows.sort(key=lambda r: r["commence"] or "")

    # Slate-wide ranking of individual starter matchups, worst spot first.
    ranked = []
    for game in rows:
        for side in ("away", "home"):
            s = game["sides"][side]
            if s["mismatch"] is None:
                continue
            ranked.append({
                "pitcher": s["pitcher"],
                "hand": s["hand"],
                "team": s["team"],
                "opponent_offense": s["opponent_offense"],
                "opp_wrc_vs_hand": s["opp_wrc_vs_hand"],
                "opp_wrc_recent": s["opp_wrc_recent"],
                "mismatch": s["mismatch"],
                "flags": s["flags"],
            })
    ranked.sort(key=lambda r: -r["mismatch"])

    return {
        "date": slate_date,
        "games": len(rows),
        "wrc_through": wrc_as_of,
        "wrc_role": role,
        "wrc_primary_window": window,
        "wrc_secondary_window": secondary,
        "wrc_offense_windows": list(OFFENSE_WINDOWS),
        "pen_window_days": dict(PEN_WINDOW_DAYS),
        "pen_min_ip": MIN_PEN_IP,
        "recent_window_starts": RECENT_N,
        "slate": rows,
        "ranked_mismatch": ranked,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _fmt(value, spec="{:.2f}", dash="--"):
    return dash if value is None else spec.format(value)


def _signed(value, spec="{:+.2f}", dash=""):
    return dash if value is None else spec.format(value)


def _win_label(window):
    """Short column tag for a window key: last7 -> L7, deadline -> dl."""
    if window.startswith("last"):
        return "L" + window[4:]
    return window[:2]


def render(report):
    lines = []
    lines.append(f"MLB slate scouting — {report['date']}  ({report['games']} games)")
    lines.append(
        f"wRC+ self-computed vs {report['wrc_role']}-only, through "
        f"{report['wrc_through']} | {report['wrc_primary_window']} "
        f"/ {report['wrc_secondary_window']} | "
        f"form window = last {report['recent_window_starts']} starts")
    lines.append("")

    for game in report["slate"]:
        lines.append(f"{game['matchup']}   "
                     f"ML {game['away_ml']:+d}/{game['home_ml']:+d}   "
                     f"total {game['total']}")
        lines.append(
            f"   [context] full-game offense: {game['away']} "
            f"{_fmt(game['away_offense_game'], '{:.0f}')} vs {game['home']} "
            f"{_fmt(game['home_offense_game'], '{:.0f}')}  ->  "
            f"env {_fmt(game['game_offense'], '{:.0f}')}, "
            f"edge {_signed(game['offense_edge'], '{:+.0f}')} "
            f"({game['away']})")
        for side in ("away", "home"):
            s = game["sides"][side]
            if not s["pitcher"]:
                lines.append(f"   {side:<5} TBD")
                continue
            season = s["form"].get("season") or {}
            recent = s["form"].get("recent") or {}
            t = s["trend"]
            flags = f"  [{', '.join(s['flags'])}]" if s["flags"] else ""
            lines.append(
                f"   {s['pitcher']} ({s['hand']}) vs {s['opponent_offense']} "
                f"vs{report['wrc_role']} {_fmt(s['opp_wrc_vs_hand'], '{:.0f}')} "
                f"({_win_label(report['wrc_secondary_window'])} "
                f"{_fmt(s['opp_wrc_recent'], '{:.0f}')}, "
                f"game {_fmt(s['opp_wrc_game'], '{:.0f}')}, "
                f"gap {_signed(s['opp_platoon_gap'], '{:+.0f}')}){flags}"
            )
            lines.append(
                f"      season {season.get('gs', 0):>2} GS  "
                f"ERA {_fmt(season.get('era'))}  K% {_fmt(season.get('k_pct'), '{:.1f}')}  "
                f"BB% {_fmt(season.get('bb_pct'), '{:.1f}')}  IP/GS {_fmt(season.get('ip_per_gs'))}"
            )
            lines.append(
                f"      last{RECENT_N}  {recent.get('gs', 0):>2} GS  "
                f"ERA {_fmt(recent.get('era'))} {_signed(t.get('era'))}  "
                f"K% {_fmt(recent.get('k_pct'), '{:.1f}')} {_signed(t.get('k_pct'), '{:+.1f}')}  "
                f"BB% {_fmt(recent.get('bb_pct'), '{:.1f}')} {_signed(t.get('bb_pct'), '{:+.1f}')}  "
                f"IP/GS {_fmt(recent.get('ip_per_gs'))}"
            )
            pen = s.get("pen") or {}
            p30, p7 = pen.get("last30") or {}, pen.get("last7") or {}
            if p30 or p7:
                rank = (f" (rk {p30['rank']}/30)"
                        if p30.get("rank") is not None else "")
                lines.append(
                    f"      pen    L30 ERA {_fmt(p30.get('era'))}{rank}  "
                    f"WHIP {_fmt(p30.get('whip'))}  "
                    f"HR9 {_fmt(p30.get('hr9'))}  ->  "
                    f"L7 ERA {_fmt(p7.get('era'))} "
                    f"({_fmt(p7.get('ip'), '{:.1f}')} IP)"
                )
            # Only for swingmen: for a pure starter these two lines repeat the
            # last-5 line above and add nothing but width.
            relief = s["form"].get("relief") or {}
            recent_all = s["form"].get("recent_all") or {}
            if relief.get("g", 0) >= SWINGMAN_MIN_G:
                lines.append(
                    f"      relief {relief.get('g', 0):>2} G   "
                    f"ERA {_fmt(relief.get('era'))}  "
                    f"K% {_fmt(relief.get('k_pct'), '{:.1f}')}  "
                    f"BB% {_fmt(relief.get('bb_pct'), '{:.1f}')}  "
                    f"IP {_fmt(relief.get('ip'), '{:.1f}')}"
                    f"   ({relief.get('from')} to {relief.get('to')})"
                )
                lines.append(
                    f"      any{RECENT_N}   {recent_all.get('g', 0):>2} G   "
                    f"ERA {_fmt(recent_all.get('era'))}  "
                    f"K% {_fmt(recent_all.get('k_pct'), '{:.1f}')}  "
                    f"BB% {_fmt(recent_all.get('bb_pct'), '{:.1f}')}  "
                    f"IP/G {_fmt(recent_all.get('ip_per_g'))}"
                    f"   ({recent_all.get('gs', 0)} of {recent_all.get('g', 0)} were starts)"
                )
            lines.append(f"      mismatch {_fmt(s['mismatch'], '{:+.1f}')}")
        lines.append("")

    lines.append("Mismatch, widest first "
                 "(+ = offense outclasses the arm):")
    for r in report["ranked_mismatch"]:
        flags = f"  [{', '.join(r['flags'])}]" if r["flags"] else ""
        lines.append(
            f"   {r['mismatch']:+6.1f}  {r['pitcher']} ({r['hand']}, {r['team']}) "
            f"vs {r['opponent_offense']} "
            f"wRC+ {_fmt(r['opp_wrc_vs_hand'], '{:.0f}')} "
            f"({_win_label(report['wrc_secondary_window'])} "
            f"{_fmt(r['opp_wrc_recent'], '{:.0f}')}){flags}"
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=date.today().isoformat(),
                        help="slate date, YYYY-MM-DD (default: today)")
    parser.add_argument("--json", action="store_true",
                        help="emit the report as JSON instead of a table")
    parser.add_argument("--window", default=PRIMARY_WINDOW,
                        help="primary wRC+ window (season, asb, deadline, "
                             "last7/15/20/30/45/60, or YYYY-MM)")
    parser.add_argument("--secondary-window", default=SECONDARY_WINDOW,
                        help="window shown alongside the primary one")
    parser.add_argument("--role", default=PITCHER_ROLE, choices=("all", "sp"),
                        help="offense split to match against: all pitchers of "
                             "the hand (default, the graded method) or "
                             "starters only")
    parser.add_argument("--slate-file",
                        help="hand-entered slate JSON, for dates the all-ML "
                             "payload does not carry yet")
    args = parser.parse_args()

    report = build_report(args.date, args.slate_file, args.role,
                          args.window, args.secondary_window)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render(report))


if __name__ == "__main__":
    main()

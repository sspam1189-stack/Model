# MLBstrikeouts/scripts/fade_list.py
# Single source of truth for the MLB pitcher "fade list" used by the
# fade-list moneyline model.
#
# The fade thesis: when a fade-list pitcher starts, bet the OPPONENT team's
# moneyline (fade the pitcher's team). This module owns the roster and the
# name-matching, mirroring the matching logic in
# PythonDashboard/js/mlb-props.js (isMLBFade / _mlbFadeNorm) so the Python
# model and the dashboard agree on who is a fade arm.
#
# Matching: an entry may be a surname ('Littell') or a full name
# ('Jared Jones'). A pitcher matches when ALL tokens of an entry are present
# in their (normalized) name, so 'Littell' -> "Zack Littell" matches and
# 'Jared Jones' stays specific (won't catch other Joneses).

import unicodedata
import re

# The roster. Keep in sync with MLB_FADE_LIST in PythonDashboard/js/mlb-props.js.
FADE_LIST = [
    "Painter", "Rocker", "Sheehan",
    "Merrill Kelly", "Gallen", "Civale",
    # David Peterson removed 2026-08-06 (user); history stays (see FADE_WINDOW).
    "David Peterson",
    "Bibee", "Springs", "Burrows", "Roupp", "Keller", "Freddy Peralta", "Canning",
    "Jacob Lopez", "Ryan Johnson", "Dustin May",
    "Grayson Rodriguez", "Bryan Woo", "Freeland", "Baz", "Noah Schultz",
    "Lowder", "Skenes",
    # Zebby Matthews moved to the fade WATCHLIST 2026-08-01 (removed from active
    # roster; fade_watch surfaces his home side 4-2 +2.62u). Re-add if it holds.
    # Added 2026-07-27 from fade-candidate analysis (forward-only from that date).
    "Paddack", "Noah Cameron", "Gausman", "Woods Richardson", "Luis Castillo",
    "Framber Valdez", "Kochanowicz", "Jack Flaherty", "Cecconi",
    "Ryne Nelson", "Feltner", "Wacha", "Bello", "Agnos",
    # Added 2026-07-27 (venue-split analysis): venue-restricted new arms.
    "Mahle", "Aaron Nola", "Trevor McDonald", "Colin Rea",
    "Jack Perkins", "Tyler Phillips",
    # Added 2026-07-27 (all-pitcher fade screen, raw fade record).
    "Logan Webb", "Lodolo", "Michael King", "Mize", "Bryce Miller",
    "Yesavage", "Imanaga", "Chandler", "McClanahan",
    # McLean removed 2026-08-17 (user); history stays (see FADE_WINDOW).
    # Tolle was removed 2026-08-19 and reverted the same day (user) --
    # never missed a start.
    "McLean", "Trevor Rogers", "Tolle",
    # Gerrit Cole removed 2026-08-09 (user); history stays (see FADE_WINDOW).
    "Gerrit Cole",
    # Added 2026-07-28 (fade-candidate analysis): all-venue, both sides green.
    "Gage Jump",
    # Promoted from the fade watchlist 2026-07-28 (season-backfilled). Singer
    # away-only; Sullivan + Kikuchi + Palmquist + Trey Gibson all-venue.
    "Brady Singer", "Sean Sullivan", "Yusei Kikuchi", "Carson Palmquist",
    "Trey Gibson",
    # Added 2026-08-13 (user): Hughes all-venue; Scherzer home-only (see
    # FADE_VENUE) -- home 9.82 ERA / home-fade 5-1 +4.32u on the watchlist.
    "Gabriel Hughes", "Max Scherzer",
    # Added 2026-07-28: away-only fade (see FADE_VENUE).
    "MacKenzie Gore",
    # Added 2026-07-31 (user request). All-venue, season-backfilled.
    "Senga",
    # Added 2026-08-01 (user). Away-only (see FADE_VENUE), season-backfilled.
    "Prielipp",
    # Added 2026-08-05 (user). Home-only (see FADE_VENUE), season-backfilled.
    "Jake Irvin",
    # Added 2026-08-25 (user). All-venue, forward-only from add date (see
    # FADE_WINDOW) -- back from a 132-day gap via relief (swingman-4g on the
    # 8/24 scout), so the pre-gap record describes a different pitcher.
    "Cristian Javier",
    # Added 2026-08-25 (user). Both away-only (see FADE_VENUE),
    # season-backfilled per the venue-split precedent (Prielipp, Irvin).
    "Jacob deGrom", "Michael Lorenzen",
]

# Per-arm venue restriction: fade the arm ONLY when his team plays at this
# venue ('home' or 'away'). Arms not listed are faded regardless of venue.
# The venue is the arm's-team side of the game (away = he starts on the road).
FADE_VENUE = {
    "Bryan Woo": "away",  # only fade when he starts on the road
    "Brady Singer": "away",  # 2026-07-28 re-add from watchlist: away 7-3 +3.22u
    "Merrill Kelly": "away",  # 2026-07-28: home 4-4 -0.42u; away 7-4 +1.86u
    # Added 2026-07-27 (venue-restricted per candidate analysis).
    # Framber Valdez / Jack Flaherty promoted to all-venue 2026-07-28
    # (green at home too, so no restriction).
    "Cecconi": "away",
    "Ryne Nelson": "away",
    "Feltner": "away",
    "Wacha": "away",
    "Jacob Lopez": "home",  # 2026-07-27: away 2-5 -5.42u; fade at home only
    # 2026-07-27 venue-split analysis: new venue-restricted adds.
    "Mahle": "away",           # home 3-5 -3.55u; away 8-0 +8.00u
    "Aaron Nola": "home",      # away 5-6 -1.90u; home 7-3 +4.76u
    "Trevor McDonald": "home", # away 3-4 -2.78u; home 5-2 +2.64u
    # 2026-07-27: existing all-venue arms leaking on one side -> restrict.
    "Freeland": "away",        # home 5-4 -1.20u; away 9-2 +4.25u
    # Gallen promoted to all-venue 2026-07-28 (home +2.10u too).
    "Civale": "home",          # away 4-4 -1.40u; home 5-2 +3.08u
    "Tyler Phillips": "away",  # home 2-3 -1.20u; away 4-1 +2.58u (small sample)
    "Bibee": "home",           # away 4-5 -2.7u; home 10-2 +9.1u
    "Springs": "home",         # away 6-4 +0.0u (flat); home 7-4 +4.2u
    "Burrows": "home",         # away 5-4 -0.0u (flat); home 6-2 +4.0u
    "Roupp": "away",           # home 5-3 +1.4u; away 8-4 +2.6u
    # Zebby Matthews moved to watchlist 2026-08-01 (see FADE_LIST note).
    "Lowder": "away",          # home 2-3 -1.9u; away 7-3 +2.8u
    "Grayson Rodriguez": "home",  # away 1-2 -1.9u; home 5-1 +3.7u
    # Added 2026-07-27 (all-pitcher screen): venue-restricted.
    "Yesavage": "away",        # away 6-2 +3.0u; home 4-4 +0.7u
    "Imanaga": "away",         # away 6-3 +3.0u; home 5-7 +0.6u
    "Chandler": "away",        # away 8-3 +4.6u; home 3-4 -1.3u
    "McClanahan": "away",      # away 7-3 +4.5u; home 1-8 -6.4u
    "McLean": "home",          # home 8-3 +7.9u; away 4-6 -1.7u
    "Trevor Rogers": "home",   # home 8-4 +4.8u; away 2-5 -4.8u
    "Gerrit Cole": "home",     # 2026-07-28: home 4-2 +2.86u; away 3-2 +1.08u (thinner)
    "Tolle": "home",           # home 6-3 +3.8u; away 3-4 -1.2u
    # 2026-07-28: Gerrit Cole promoted to all-venue -- away turned green
    # (3-1 +2.08u +49.8%) with more data, so no venue restriction (removed).
    "Lodolo": "home",          # home 6-2 +4.2u; away 3-1 +1.6u
    # 2026-07-28: restrict to stronger road side (home +8.3% weak vs away +23.4%).
    "Michael King": "away",    # home 5-5 +0.92u; away 6-3 +2.70u
    "MacKenzie Gore": "away",  # 2026-07-28: home 3-6 -2.86u; away 7-4 +2.58u (+19.6%)
    "Bryce Miller": "away",    # 2026-07-31: home 3-2 +1.78u; away 4-2 +2.56u (+42.5%) -- away only
    # 2026-07-31: date-scoped. All-venue through 7/31 (that history stays), then
    # away-only from 8/1 on (user). No pre-8/1 segment => no restriction before
    # then. Home is his best ERA split (3.86 vs 5.33 away) & lighter fade side
    # (home 6-4 +21% vs away 7-3 +35%), so restrict to the away edge going fwd.
    "Gausman": [
        {"from": "2026-08-01", "venue": "away"},
    ],
    # 2026-08-01 (user): away-only. Away ERA 6.56 vs 3.86 home, K% 27->19 on
    # the road; away fade 4-1 +2.52u (+34%) vs home 4-5 -1.50u. Away-side edge.
    "Prielipp": "away",
    # 2026-08-01 (user): date-scoped away-only from 8/1 (all-venue through 7/31,
    # 14-5 history stays). Fade P&L is almost all road: away 9-1 +8.06u (+59.5%)
    # vs home 5-4 +1.06u (+11.4%). Drops the 8/1 home BAL->PHI fade.
    "Baz": [
        {"from": "2026-08-01", "venue": "away"},
    ],
    # 2026-08-05 (user): home-only. Home ERA 7.04 vs 4.54 away; fading him at
    # home 4-1 +3.00u (+36.1%) vs away 2-5 -7.48u (-58.4%). All-venue is a
    # net loser (-21.2%) -- the edge is entirely the home side. Small sample
    # (5 home starts); revisit as it fills in.
    "Jake Irvin": "home",
    # 2026-08-13 (user): home-only. Home 9.82 ERA (22 IP) vs 3.21 away;
    # watchlist home-fade 5-1 +4.32u before promotion.
    "Max Scherzer": "home",
    # 2026-08-25 (user): road-only fades on season venue splits.
    "Jacob deGrom": "away",     # home 2.67 ERA/0.92 WHIP; away 5.01/1.37 with HR9 1.67
    "Michael Lorenzen": "away", # away 5.70/1.72; home is Coors (9.12) but priced as such
}

# Matchup fade list: fade the pitcher (bet the OPPONENT's ML) ONLY when he
# starts against one of these specific opponent teams. This is a separate
# "pitcher vs team" angle, independent of the venue-based FADE_LIST above; a
# pitcher can appear here without being on FADE_LIST. Team abbreviations use
# the props convention (NYY, LAD, KC, PHI, CWS, ...). Grade with
# scripts/fade_vs_team.py (data only -- not wired into the fade-ML model).
FADE_VS_TEAM = {
    "Anthony Kay":   ["NYY"],
    "Davis Martin":  ["NYY"],
    "Joe Ryan":      ["KC"],
    "Will Warren":   ["PHI"],
}

# Matchup fade EXCEPTIONS: do NOT fade this arm against these opponents, even
# when he is otherwise a fade (venue FADE_LIST, all-venue, etc.). Use when an
# arm owns a specific team badly enough that fading him vs them is -EV. Opp
# abbrs use the props convention. Enforced by is_fade() whenever an opponent is
# supplied, and applied to history too (those starts become no-plays), mirroring
# how FADE_VENUE / FADE_WINDOW gate the record.
FADE_EXCEPT_VS_TEAM = {
    # 2026-08-05 (user): Cameron owns MIN -- 0.47 ERA over 3 starts (19ip 1er,
    # 19K), and fading him vs MIN is 1-2 -1.16u (the one "win" was an 8ip 0er
    # gem KC lost anyway). Don't fade Cameron against MIN on any side/venue.
    "Noah Cameron":  ["MIN"],
}

# Arms NOT faded on MUTUAL games (both starters are fade arms). When a mutual
# matchup involves one of these arms, the model places NO bet on the game --
# it neither takes the dog nor falls back to a single fade of the other arm.
# The arm is still faded normally on non-mutual starts.
NO_MUTUAL_FADE = {"Sheehan"}

# Date the fade model started tracking (earliest graded slate). The season
# backfill grades from here, so arms on the list since inception have been
# faded from this date -- it's their effective "added" date.
SEASON_START = "2026-04-05"

# Per-arm fade windows (ISO YYYY-MM-DD). Every roster arm carries an "add"
# date (first date to fade); an arm may also carry a "remove" date (date to
# stop fading). The model fades a start only INSIDE [add, remove), so it knows
# when to fade and when not to:
#   "add"    - starts BEFORE it are not faded (arm not yet fade-worthy).
#   "remove" - starts ON/AFTER it are not faded (arm back to form) while
#              earlier fades stay on the record.
# "remove" may be omitted (open-ended). Windows keep history correct across
# re-grades: adding an arm mid-season doesn't retro-fade his good early starts,
# and retiring an arm stops future fades WITHOUT erasing his prior record.
# Gating only applies when the caller passes a date; a bare is_fade(name) with
# no date matches purely on the roster, unchanged.
FADE_WINDOW = {
    # Original roster -- faded since the model began tracking (season start).
    "Painter":        {"add": SEASON_START},
    "Rocker":         {"add": SEASON_START, "remove": "2026-07-28"},  # removed from 7/28
    "Sheehan":        {"add": SEASON_START},
    "Merrill Kelly":  {"add": SEASON_START},  # away-only (see FADE_VENUE)
    "Gallen":         {"add": SEASON_START},
    "Civale":         {"add": SEASON_START},
    "David Peterson": {"add": SEASON_START, "remove": "2026-08-06"},  # removed 2026-08-06 (user): 5-4 +0.82u (+7.2%) -- thinnest edge on the roster; no fades from 8/6 on, prior record stays
    "Bibee":          {"add": SEASON_START},
    "Springs":        {"add": SEASON_START},
    "Burrows":        {"add": SEASON_START},
    "Roupp":          {"add": SEASON_START},  # 2026-07-27: reactivated, away-only (see FADE_VENUE)
    "Keller":         {"add": SEASON_START, "remove": "2026-07-27"},  # removed 7/27 (-2.6u all-venue)
    "Freddy Peralta": {"add": SEASON_START, "remove": "2026-08-04"},  # renamed from "Peralta" 2026-08-04 (bare surname also caught Wandy Peralta (SD) + Sammy Peralta (COL)); removed 2026-08-04 (user request)
    "Canning":        {"add": SEASON_START},
    "Jacob Lopez":    {"add": SEASON_START},
    "Ryan Johnson":   {"add": SEASON_START, "remove": "2026-08-26"},  # removed 2026-08-26 (user): 5-3 +0.06u (+0.5%) over 8 -- dead flat, no edge; history stays
    "Freeland":       {"add": SEASON_START, "remove": "2026-08-26"},  # removed 2026-08-26 (user): away 6-3 +1.16u (+6.3%), below the Peterson bar; history stays
    "Baz":            {"add": SEASON_START},  # backfilled: faded all season
    "Noah Schultz":   {"add": SEASON_START},  # backfilled: faded all season
    "Lowder":         {"add": SEASON_START},  # backfilled: faded all season
    # Removed 7/31 (elite arm, fade was pure market-pricing, too fragile;
    # record then 11-8 +6.92u). Re-added 2026-08-20 (user): the market still
    # prices the name (-154 on 2026-08-19) while his last 5 read 5.13 ERA
    # with BB% doubled to 11.5 -- the overpricing thesis with form behind it
    # now. Walk-forward: the 8/1-8/19 gap stays no-plays (incl. the 8/19
    # DET@PIT start), first eligible fade is his next start ~8/24.
    "Skenes":         [{"add": SEASON_START, "remove": "2026-07-31"},
                       {"add": "2026-08-20"}],
    # Added mid-season: good before, fade-worthy from these dates on.
    "Dustin May":        {"add": "2026-07-18", "remove": "2026-08-13"},  # removed 2026-08-13 (user): no fades from 8/13 on, prior record (incl. 8/12 SD play) stays
    # Added 2026-08-13 (user), forward-only from that date.
    "Gabriel Hughes":    {"add": "2026-08-13"},
    "Max Scherzer":      {"add": "2026-08-13"},  # home-only (see FADE_VENUE)
    "Grayson Rodriguez": {"add": SEASON_START},  # 2026-07-27: home-only (FADE_VENUE), full season
    "Bryan Woo":         {"add": SEASON_START},  # away-only (see FADE_VENUE); backfilled full season 2026-07-28
    # Zebby Matthews: moved to watchlist 2026-08-01 (removed from active roster).
    # Added 2026-07-27 from fade-candidate analysis — backfilled to season
    # start so their full-season fade history is on the record. Venue-
    # restricted arms are faded all season but only on the required side.
    "Paddack":           {"add": SEASON_START},
    "Noah Cameron":      {"add": SEASON_START},
    "Gausman":           {"add": SEASON_START},
    "Woods Richardson":  {"add": SEASON_START},
    "Luis Castillo":     {"add": SEASON_START},
    "Framber Valdez":    {"add": SEASON_START},  # away-only (see FADE_VENUE)
    "Kochanowicz":       {"add": SEASON_START},
    "Jack Flaherty":     {"add": SEASON_START},  # away-only (see FADE_VENUE)
    "Cecconi":           {"add": SEASON_START},  # away-only (see FADE_VENUE)
    "Ryne Nelson":       {"add": SEASON_START},  # away-only (see FADE_VENUE)
    "Feltner":           {"add": SEASON_START},  # away-only (see FADE_VENUE)
    "Wacha":             {"add": SEASON_START},  # away-only (see FADE_VENUE)
    "Bello":             {"add": SEASON_START},
    "Agnos":             {"add": SEASON_START},
    "Mahle":             {"add": SEASON_START},  # away-only (see FADE_VENUE)
    "Aaron Nola":        {"add": SEASON_START},  # home-only (see FADE_VENUE)
    "Trevor McDonald":   {"add": SEASON_START},  # home-only (see FADE_VENUE)
    "Colin Rea":         {"add": SEASON_START},
    "Jack Perkins":      {"add": SEASON_START},
    "Tyler Phillips":    {"add": SEASON_START},  # away-only (see FADE_VENUE)
    # Added 2026-07-27 (all-pitcher fade screen), season-backfilled.
    "Logan Webb":        {"add": SEASON_START, "remove": "2026-08-04"},  # removed after 2026-08-03 (user): 8/3 start still faded, no fades from 8/4 on; history stays
    "Lodolo":            {"add": SEASON_START},  # home-only (see FADE_VENUE)
    "Michael King":      {"add": SEASON_START},
    "Mize":              {"add": SEASON_START, "remove": "2026-07-31"},  # removed 7/31: 2.70 ERA (elite); fade was pure market-pricing, too fragile
    "Bryce Miller":      {"add": SEASON_START},
    "Yesavage":          {"add": SEASON_START},  # away-only (see FADE_VENUE)
    "Imanaga":           {"add": SEASON_START},  # away-only (see FADE_VENUE)
    "Chandler":          {"add": SEASON_START},  # away-only (see FADE_VENUE)
    "McClanahan":        {"add": SEASON_START},  # away-only (see FADE_VENUE)
    # Removed 2026-08-17 (user): no fades from 8/17 on (drops today's NYM->SD
    # +108 play), prior record stays. Home-only fade went 5-4 +2.34u (+24.5%),
    # but that P&L is price, not hit rate -- 8 of the 9 graded bets were dogs
    # averaging about +128, and he is 2-3 over the last five while throwing
    # better than at any point in the sample (28.3% K / 3.07 ERA last 5).
    # FADE_VENUE keeps his "home" entry so the prior record still grades
    # home-only.
    "McLean":            {"add": SEASON_START, "remove": "2026-08-17"},
    "Trevor Rogers":     {"add": SEASON_START},  # home-only (see FADE_VENUE)
    # Removed 2026-08-19 on current form (2.61 ERA / 34.2% K last 5),
    # REVERTED the same day before his start (user) -- continuous fade, no
    # gap. Home-only (see FADE_VENUE); record at the flip-flop: 5-3 +2.78u.
    "Tolle":             {"add": SEASON_START},
    "Gerrit Cole":       {"add": SEASON_START, "remove": "2026-08-09"},  # removed 2026-08-09 (user): no fades from 8/9 on, prior record stays; home-only as of 2026-07-28 (see FADE_VENUE)
    # Added 2026-07-28: all-venue fade, season-backfilled (home +18%, away +42%).
    "Gage Jump":         {"add": SEASON_START, "remove": "2026-08-26"},  # removed 2026-08-26 (user): 8-6 -0.46u (-2.6%) over 14 -- the only negative arm with a real sample; history stays
    # Promoted from the fade watchlist 2026-07-28 (season-backfilled).
    "Brady Singer":      {"add": SEASON_START},  # away-only (see FADE_VENUE)
    "Sean Sullivan":     {"add": SEASON_START},
    "Yusei Kikuchi":     {"add": SEASON_START},
    "Carson Palmquist":  {"add": SEASON_START},
    "Trey Gibson":       {"add": SEASON_START},
    "MacKenzie Gore":    {"add": SEASON_START},  # away-only (see FADE_VENUE)
    # Added 2026-07-31 (user request). Season-backfilled, all-venue — no
    # venue split vetted yet.
    "Senga":             {"add": SEASON_START},
    "Prielipp":          {"add": SEASON_START},  # away-only (see FADE_VENUE), season-backfilled
    "Jake Irvin":        {"add": SEASON_START},  # home-only (see FADE_VENUE), season-backfilled 2026-08-05
    # Added 2026-08-25 (user): forward-only -- no backfill. His pre-gap
    # season was a different pitcher (132-day layoff bridged by relief work);
    # first eligible fade is his next start after 8/25.
    "Cristian Javier":   {"add": "2026-08-25"},
    # Added 2026-08-25 (user): away-only (see FADE_VENUE), season-backfilled
    # per the venue-split precedent. deGrom's road start TONIGHT (TEX @ CWS)
    # is the first live fade.
    "Jacob deGrom":      {"add": SEASON_START},
    "Michael Lorenzen":  {"add": SEASON_START},
}


def _norm(s):
    """Lower-case, strip accents, drop non-letters -> single-spaced tokens.

    Mirrors _mlbFadeNorm in mlb-props.js.
    """
    s = unicodedata.normalize("NFD", s or "").lower()
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# Pre-tokenize each entry once.
_FADE_TOKENS = [toks for toks in (_norm(e).split() for e in FADE_LIST) if toks]


def _in_window(entry, date):
    """True if ``entry`` should fade on ``date`` per its FADE_WINDOW.

    No date -> match on roster alone (True). No window for the entry -> always
    faded. Otherwise fade only within [add, remove): before ``add`` or on/after
    ``remove`` is not faded. An entry may also carry a LIST of such windows
    (an arm retired and later re-added, e.g. Skenes 2026-08-20): the date
    matches if it falls inside any window, and the gaps between windows stay
    no-plays -- mirrors the list form FADE_VENUE already supports.
    """
    if date is None:
        return True
    win = FADE_WINDOW.get(entry)
    if not win:
        return True
    windows = win if isinstance(win, list) else [win]
    for w in windows:
        add, remove = w.get("add"), w.get("remove")
        if add is not None and date < add:
            continue
        if remove is not None and date >= remove:
            continue
        return True
    return False


def matched_entry(player_name, date=None):
    """Return the FADE_LIST entry that matches ``player_name``, or None.

    A name matches an entry when every token of the entry appears in the
    player's normalized name. When ``date`` (ISO YYYY-MM-DD) is given, the
    matched entry's FADE_WINDOW must also contain ``date`` -- so an arm added
    or retired mid-season fades only on the dates it should.
    """
    name_tokens = set(_norm(player_name).split())
    if not name_tokens:
        return None
    for entry, toks in zip(FADE_LIST, _FADE_TOKENS):
        if all(t in name_tokens for t in toks):
            if not _in_window(entry, date):
                continue
            return entry
    return None


def _venue_for(entry, date=None):
    """Resolve an arm's required fade venue, supporting date-scoped rules.

    ``FADE_VENUE[entry]`` may be:
      - a string ('home'/'away') -> a flat restriction applied on every date, or
      - a list of ``{"from": ISO, "venue": "home"/"away"}`` segments in
        chronological order -> the venue of the LATEST segment whose ``from`` is
        <= ``date`` applies (so the restriction can change at a day boundary
        without retroactively re-grading earlier starts).
    Returns 'home'/'away', or None when the arm carries no venue restriction.
    A bare call (``date`` is None) resolves to the latest/current segment.
    """
    rule = FADE_VENUE.get(entry)
    if rule is None or isinstance(rule, str):
        return rule
    chosen = None
    for seg in rule:
        frm = seg.get("from")
        if date is None or frm is None or date >= frm:
            chosen = seg.get("venue")
    return chosen


def fade_reason(player_name, date=None):
    """Return WHY this arm is faded, for the today's-picks display.

    'home'/'away' when the arm carries a FADE_VENUE restriction (faded only on
    that side) on ``date``, else 'all' (faded regardless of venue). None if the
    name isn't a fade arm on ``date``. Lets the dashboard differentiate a
    venue-driven fade from an all-venue one alongside the hand-tails picks.
    """
    entry = matched_entry(player_name, date)
    if entry is None:
        return None
    return _venue_for(entry, date) or "all"


def is_no_mutual_fade(player_name):
    """True iff ``player_name`` is an arm exempt from mutual fading.

    Used only for MUTUAL games: if either starter is exempt, the model places
    no bet on that game. Identity match on the roster (no date/window gating);
    the mutual check has already confirmed both arms are fades on the date.
    """
    return matched_entry(player_name) in NO_MUTUAL_FADE


_FADE_VS_TEAM_TOKENS = {name: _norm(name).split() for name in FADE_VS_TEAM}


def fade_vs_team_teams(player_name):
    """Return the opponent-team abbrs to fade ``player_name`` against (matchup
    fade), or [] if he isn't on FADE_VS_TEAM. Token-matched like matched_entry:
    every token of the list entry must appear in the player's normalized name.
    """
    name_tokens = set(_norm(player_name).split())
    if not name_tokens:
        return []
    for name, toks in _FADE_VS_TEAM_TOKENS.items():
        if toks and all(t in name_tokens for t in toks):
            return FADE_VS_TEAM[name]
    return []


_FADE_EXCEPT_TOKENS = {name: _norm(name).split() for name in FADE_EXCEPT_VS_TEAM}


def fade_except_vs_team(player_name):
    """Return the opponent-team abbrs this arm is NOT faded against (matchup
    exception), or [] if he has none. Token-matched like fade_vs_team_teams:
    every token of the dict key must appear in the player's normalized name.
    """
    name_tokens = set(_norm(player_name).split())
    if not name_tokens:
        return []
    for name, toks in _FADE_EXCEPT_TOKENS.items():
        if toks and all(t in name_tokens for t in toks):
            return FADE_EXCEPT_VS_TEAM[name]
    return []


def is_fade(player_name, date=None, venue=None, opp=None):
    """True iff ``player_name`` is a fade arm on ``date`` (if given) and, for a
    venue-restricted arm, when his team plays at the required ``venue``.

    ``venue`` is the arm's-team side of the game ('home'/'away'), or None if
    unknown. An arm with a FADE_VENUE restriction is not faded unless ``venue``
    matches it, so an unknown venue means "don't fade" (safe default).

    ``opp`` is the opponent's team abbr (the team we'd bet). When supplied and
    the arm carries a FADE_EXCEPT_VS_TEAM exception against that opponent, he is
    NOT faded in this matchup. A None ``opp`` skips the exception check (so
    callers that don't know the opponent behave exactly as before).
    """
    entry = matched_entry(player_name, date)
    if entry is None:
        return False
    required = _venue_for(entry, date)
    if required is not None and venue != required:
        return False
    if opp is not None and opp in fade_except_vs_team(player_name):
        return False
    return True

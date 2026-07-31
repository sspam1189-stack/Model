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
    "Merrill Kelly", "Gallen", "Civale", "David Peterson",
    "Bibee", "Springs", "Burrows", "Roupp", "Keller", "Peralta", "Canning",
    "Jacob Lopez", "Ryan Johnson", "Dustin May",
    "Grayson Rodriguez", "Bryan Woo", "Freeland", "Baz", "Noah Schultz",
    "Lowder", "Zebby Matthews", "Skenes",
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
    "McLean", "Trevor Rogers", "Tolle", "Gerrit Cole",
    # Added 2026-07-28 (fade-candidate analysis): all-venue, both sides green.
    "Gage Jump",
    # Promoted from the fade watchlist 2026-07-28 (season-backfilled). Singer
    # away-only; Sullivan + Kikuchi + Palmquist + Trey Gibson all-venue.
    "Brady Singer", "Sean Sullivan", "Yusei Kikuchi", "Carson Palmquist",
    "Trey Gibson",
    # Added 2026-07-28: away-only fade (see FADE_VENUE).
    "MacKenzie Gore",
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
    "Zebby Matthews": "home",  # away 4-3 -0.2u (flat); home 4-2 +2.6u
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
    "David Peterson": {"add": SEASON_START},
    "Bibee":          {"add": SEASON_START},
    "Springs":        {"add": SEASON_START},
    "Burrows":        {"add": SEASON_START},
    "Roupp":          {"add": SEASON_START},  # 2026-07-27: reactivated, away-only (see FADE_VENUE)
    "Keller":         {"add": SEASON_START, "remove": "2026-07-27"},  # removed 7/27 (-2.6u all-venue)
    "Peralta":        {"add": SEASON_START},
    "Canning":        {"add": SEASON_START},
    "Jacob Lopez":    {"add": SEASON_START},
    "Ryan Johnson":   {"add": SEASON_START},
    "Freeland":       {"add": SEASON_START},  # backfilled: faded all season
    "Baz":            {"add": SEASON_START},  # backfilled: faded all season
    "Noah Schultz":   {"add": SEASON_START},  # backfilled: faded all season
    "Lowder":         {"add": SEASON_START},  # backfilled: faded all season
    "Skenes":         {"add": SEASON_START},  # backfilled: faded all season (market overprices PIT on his starts)
    # Added mid-season: good before, fade-worthy from these dates on.
    "Dustin May":        {"add": "2026-07-18"},
    "Grayson Rodriguez": {"add": SEASON_START},  # 2026-07-27: home-only (FADE_VENUE), full season
    "Bryan Woo":         {"add": SEASON_START},  # away-only (see FADE_VENUE); backfilled full season 2026-07-28
    "Zebby Matthews":    {"add": SEASON_START},  # 2026-07-27: home-only (FADE_VENUE), full season
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
    "Logan Webb":        {"add": SEASON_START},
    "Lodolo":            {"add": SEASON_START},  # home-only (see FADE_VENUE)
    "Michael King":      {"add": SEASON_START},
    "Mize":              {"add": SEASON_START, "remove": "2026-07-31"},  # removed 7/31: 2.70 ERA (elite); fade was pure market-pricing, too fragile
    "Bryce Miller":      {"add": SEASON_START},
    "Yesavage":          {"add": SEASON_START},  # away-only (see FADE_VENUE)
    "Imanaga":           {"add": SEASON_START},  # away-only (see FADE_VENUE)
    "Chandler":          {"add": SEASON_START},  # away-only (see FADE_VENUE)
    "McClanahan":        {"add": SEASON_START},  # away-only (see FADE_VENUE)
    "McLean":            {"add": SEASON_START},  # home-only (see FADE_VENUE)
    "Trevor Rogers":     {"add": SEASON_START},  # home-only (see FADE_VENUE)
    "Tolle":             {"add": SEASON_START},  # home-only (see FADE_VENUE)
    "Gerrit Cole":       {"add": SEASON_START},  # home-only as of 2026-07-28 (see FADE_VENUE)
    # Added 2026-07-28: all-venue fade, season-backfilled (home +18%, away +42%).
    "Gage Jump":         {"add": SEASON_START},
    # Promoted from the fade watchlist 2026-07-28 (season-backfilled).
    "Brady Singer":      {"add": SEASON_START},  # away-only (see FADE_VENUE)
    "Sean Sullivan":     {"add": SEASON_START},
    "Yusei Kikuchi":     {"add": SEASON_START},
    "Carson Palmquist":  {"add": SEASON_START},
    "Trey Gibson":       {"add": SEASON_START},
    "MacKenzie Gore":    {"add": SEASON_START},  # away-only (see FADE_VENUE)
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
    ``remove`` is not faded.
    """
    if date is None:
        return True
    win = FADE_WINDOW.get(entry)
    if not win:
        return True
    add, remove = win.get("add"), win.get("remove")
    if add is not None and date < add:
        return False
    if remove is not None and date >= remove:
        return False
    return True


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


def fade_reason(player_name, date=None):
    """Return WHY this arm is faded, for the today's-picks display.

    'home'/'away' when the arm carries a FADE_VENUE restriction (faded only on
    that side), else 'all' (faded regardless of venue). None if the name isn't a
    fade arm on ``date``. Lets the dashboard differentiate a venue-driven fade
    from an all-venue one alongside the handedness-driven hand-tails picks.
    """
    entry = matched_entry(player_name, date)
    if entry is None:
        return None
    return FADE_VENUE.get(entry, "all")


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


def is_fade(player_name, date=None, venue=None):
    """True iff ``player_name`` is a fade arm on ``date`` (if given) and, for a
    venue-restricted arm, when his team plays at the required ``venue``.

    ``venue`` is the arm's-team side of the game ('home'/'away'), or None if
    unknown. An arm with a FADE_VENUE restriction is not faded unless ``venue``
    matches it, so an unknown venue means "don't fade" (safe default).
    """
    entry = matched_entry(player_name, date)
    if entry is None:
        return False
    required = FADE_VENUE.get(entry)
    if required is not None and venue != required:
        return False
    return True

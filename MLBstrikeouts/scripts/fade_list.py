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
    "Littell", "Mikolas", "Painter", "Rocker", "Sheehan", "Jared Jones",
    "Merrill Kelly", "Aldegheri", "Gallen", "Civale", "David Peterson",
    "Bibee", "Springs", "Burrows", "Roupp", "Keller", "Peralta", "Canning",
    "Jacob Lopez", "Ryan Johnson", "Poulin", "Singer", "Dustin May",
    "Grayson Rodriguez", "Bryan Woo", "Freeland", "Baz", "Noah Schultz",
    "Lowder", "Zebby Matthews", "Skenes",
    # Added 2026-07-27 from fade-candidate analysis (forward-only from that date).
    "Paddack", "Noah Cameron", "Gausman", "Woods Richardson", "Luis Castillo",
    "Framber Valdez", "Kochanowicz", "Jack Flaherty", "Cecconi", "Detmers",
    "Ryne Nelson", "Feltner", "Wacha", "Bello", "Agnos",
    # Added 2026-07-27 (venue-split analysis): venue-restricted new arms.
    "Mahle", "Aaron Nola", "Taillon", "Trevor McDonald", "Colin Rea",
    "Jack Perkins", "Tyler Phillips",
]

# Per-arm venue restriction: fade the arm ONLY when his team plays at this
# venue ('home' or 'away'). Arms not listed are faded regardless of venue.
# The venue is the arm's-team side of the game (away = he starts on the road).
FADE_VENUE = {
    "Bryan Woo": "away",  # only fade when he starts on the road
    # Added 2026-07-27 (venue-restricted per candidate analysis).
    "Framber Valdez": "away",
    "Jack Flaherty": "away",
    "Cecconi": "away",
    "Detmers": "home",
    "Ryne Nelson": "away",
    "Feltner": "away",
    "Wacha": "away",
    "Jacob Lopez": "home",  # 2026-07-27: away 2-5 -5.42u; fade at home only
    # 2026-07-27 venue-split analysis: new venue-restricted adds.
    "Mahle": "away",           # home 3-5 -3.55u; away 8-0 +8.00u
    "Aaron Nola": "home",      # away 5-6 -1.90u; home 7-3 +4.76u
    "Taillon": "home",         # away flat +0.10u; home 5-3 +2.52u
    "Trevor McDonald": "home", # away 3-4 -2.78u; home 5-2 +2.64u
    # 2026-07-27: existing all-venue arms leaking on one side -> restrict.
    "Freeland": "away",        # home 5-4 -1.20u; away 9-2 +4.25u
    "Gallen": "away",          # home flat +0.56u; away 8-2 +5.62u
    "Civale": "home",          # away 4-4 -1.40u; home 5-2 +3.08u
    "Tyler Phillips": "away",  # home 2-3 -1.20u; away 4-1 +2.58u (small sample)
    "Bibee": "home",           # away 4-5 -2.7u; home 10-2 +9.1u
    "Springs": "home",         # away 6-4 +0.0u (flat); home 7-4 +4.2u
    "Burrows": "home",         # away 5-4 -0.0u (flat); home 6-2 +4.0u
    "Roupp": "away",           # home 5-3 +1.4u; away 8-4 +2.6u
    "Zebby Matthews": "home",  # away 4-3 -0.2u (flat); home 4-2 +2.6u
    "Lowder": "away",          # home 2-3 -1.9u; away 7-3 +2.8u
    "Grayson Rodriguez": "home",  # away 1-2 -1.9u; home 5-1 +3.7u
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
    "Littell":        {"add": SEASON_START},
    "Mikolas":        {"add": SEASON_START},
    "Painter":        {"add": SEASON_START},
    "Rocker":         {"add": SEASON_START},
    "Sheehan":        {"add": SEASON_START},
    "Merrill Kelly":  {"add": SEASON_START},
    "Aldegheri":      {"add": SEASON_START},
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
    "Poulin":         {"add": SEASON_START},
    "Singer":         {"add": SEASON_START},
    "Freeland":       {"add": SEASON_START},  # backfilled: faded all season
    "Baz":            {"add": SEASON_START},  # backfilled: faded all season
    "Noah Schultz":   {"add": SEASON_START},  # backfilled: faded all season
    "Lowder":         {"add": SEASON_START},  # backfilled: faded all season
    "Skenes":         {"add": SEASON_START},  # backfilled: faded all season (market overprices PIT on his starts)
    # Retired mid-season: faded early, back to his old self from 7/18.
    "Jared Jones":    {"add": SEASON_START, "remove": "2026-07-18"},
    # Added mid-season: good before, fade-worthy from these dates on.
    "Dustin May":        {"add": "2026-07-18"},
    "Grayson Rodriguez": {"add": SEASON_START},  # 2026-07-27: home-only (FADE_VENUE), full season
    "Bryan Woo":         {"add": "2026-07-20"},  # away-only (see FADE_VENUE)
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
    "Detmers":           {"add": SEASON_START},  # home-only (see FADE_VENUE)
    "Ryne Nelson":       {"add": SEASON_START},  # away-only (see FADE_VENUE)
    "Feltner":           {"add": SEASON_START},  # away-only (see FADE_VENUE)
    "Wacha":             {"add": SEASON_START},  # away-only (see FADE_VENUE)
    "Bello":             {"add": SEASON_START},
    "Agnos":             {"add": SEASON_START},
    "Mahle":             {"add": SEASON_START},  # away-only (see FADE_VENUE)
    "Aaron Nola":        {"add": SEASON_START},  # home-only (see FADE_VENUE)
    "Taillon":           {"add": SEASON_START},  # home-only (see FADE_VENUE)
    "Trevor McDonald":   {"add": SEASON_START},  # home-only (see FADE_VENUE)
    "Colin Rea":         {"add": SEASON_START},
    "Jack Perkins":      {"add": SEASON_START},
    "Tyler Phillips":    {"add": SEASON_START},  # away-only (see FADE_VENUE)
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


def is_no_mutual_fade(player_name):
    """True iff ``player_name`` is an arm exempt from mutual fading.

    Used only for MUTUAL games: if either starter is exempt, the model places
    no bet on that game. Identity match on the roster (no date/window gating);
    the mutual check has already confirmed both arms are fades on the date.
    """
    return matched_entry(player_name) in NO_MUTUAL_FADE


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

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
]

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
    "Roupp":          {"add": SEASON_START},
    "Keller":         {"add": SEASON_START},
    "Peralta":        {"add": SEASON_START},
    "Canning":        {"add": SEASON_START},
    "Jacob Lopez":    {"add": SEASON_START},
    "Ryan Johnson":   {"add": SEASON_START},
    "Poulin":         {"add": SEASON_START},
    "Singer":         {"add": SEASON_START},
    # Retired mid-season: faded early, back to his old self from 7/18.
    "Jared Jones":    {"add": SEASON_START, "remove": "2026-07-18"},
    # Added mid-season: good before, fade-worthy from 7/18.
    "Dustin May":     {"add": "2026-07-18"},
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


def is_fade(player_name, date=None):
    """True iff ``player_name`` is a fade arm (on ``date``, if given)."""
    return matched_entry(player_name, date) is not None

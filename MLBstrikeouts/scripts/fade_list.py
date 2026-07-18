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
    "Littell", "Mikolas", "Painter", "Rocker", "Sheehan",
    "Merrill Kelly", "Aldegheri", "Gallen", "Civale", "David Peterson",
    "Bibee", "Springs", "Burrows", "Roupp", "Keller", "Peralta", "Canning",
    "Jacob Lopez", "Ryan Johnson", "Poulin", "Singer", "Dustin May",
]

# Per-arm effective-start dates (ISO YYYY-MM-DD). An entry listed here is only
# treated as a fade arm on or after its start date, so a pitcher added
# mid-season is NOT retroactively faded on his earlier (good) starts when the
# backfill/daily grader re-walks history. Entries not listed are faded for all
# dates. Date-gating only applies when the caller passes a date; a bare
# is_fade(name) (no date) matches purely on the roster, unchanged.
FADE_START = {
    "Dustin May": "2026-07-18",  # back to fade-worthy from here; good before.
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


def matched_entry(player_name, date=None):
    """Return the FADE_LIST entry that matches ``player_name``, or None.

    A name matches an entry when every token of the entry appears in the
    player's normalized name. When ``date`` (ISO YYYY-MM-DD) is given and the
    matched entry has a FADE_START, dates before that start don't match, so
    the arm isn't faded on games that predate its effective date.
    """
    name_tokens = set(_norm(player_name).split())
    if not name_tokens:
        return None
    for entry, toks in zip(FADE_LIST, _FADE_TOKENS):
        if all(t in name_tokens for t in toks):
            start = FADE_START.get(entry)
            if date is not None and start is not None and date < start:
                continue
            return entry
    return None


def is_fade(player_name, date=None):
    """True iff ``player_name`` is a fade arm (on ``date``, if given)."""
    return matched_entry(player_name, date) is not None

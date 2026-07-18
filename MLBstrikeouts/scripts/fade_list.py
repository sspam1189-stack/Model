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


def matched_entry(player_name):
    """Return the FADE_LIST entry that matches ``player_name``, or None.

    A name matches an entry when every token of the entry appears in the
    player's normalized name.
    """
    name_tokens = set(_norm(player_name).split())
    if not name_tokens:
        return None
    for entry, toks in zip(FADE_LIST, _FADE_TOKENS):
        if all(t in name_tokens for t in toks):
            return entry
    return None


def is_fade(player_name):
    """True iff ``player_name`` is on the fade list."""
    return matched_entry(player_name) is not None

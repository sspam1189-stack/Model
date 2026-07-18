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

# Per-arm fade windows (ISO YYYY-MM-DD). For an arm listed here the model only
# fades starts INSIDE the window, so it knows when to fade and when not to:
#   "add"    - first date to fade; starts BEFORE it are not faded (arm was good
#              earlier / not yet on the list).
#   "remove" - date to stop fading; starts ON/AFTER it are not faded (arm is
#              back to form) while earlier fades stay on the record.
# Either bound may be omitted (open-ended on that side). Arms not listed here
# are faded on every date they appear. Windows keep history correct across
# re-grades: adding an arm mid-season doesn't retro-fade his good early starts,
# and retiring an arm stops future fades WITHOUT erasing his prior record.
# Gating only applies when the caller passes a date; a bare is_fade(name) with
# no date matches purely on the roster, unchanged.
FADE_WINDOW = {
    "Dustin May":  {"add": "2026-07-18"},     # fade-worthy from here; good before.
    "Jared Jones": {"remove": "2026-07-18"},  # back to his old self; stop fading.
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

# MLBstrikeouts/scripts/hand_tails.py
# LIVE handedness-conditional fade/take tracker (own ledger: mlb-hand-tails.json).
#
# Thesis (walk-forward, 2026): certain arms' game outcomes swing predictably
# when they face a lineup stacked with opposite-hand bats. Each listed arm is
# bet ONLY when the opponent lineup has >= HAND_MIN opposite-hand bats:
#   RHP -> vs HAND_MIN+ lefty bats (L or switch)
#   LHP -> vs HAND_MIN+ righty bats (R or switch)
# action:
#   "fade" -> bet the OPPONENT's moneyline (arm's team expected to lose)
#   "take" -> bet the arm's OWN team moneyline (expected to win)
#
# Tracked in its own ledger, separate from the fade-list model. Where an arm is
# on BOTH lists in opposite directions (Aaron Nola, Jacob Lopez -> fade-list
# fades home; here we TAKE on 6+ opposite lineups), hand-tails wins that game
# (see fade_overridden_by_take). Caveat: the picks were chosen in-sample, so the
# season backtest overstates edge -- this is a live paper-forward test.

import os
import json
import functools

from fade_list import _norm

HAND_MIN = 6

# entry -> (pitcher hand, action). RHP qualify vs HAND_MIN+ lefties; LHP vs righties.
HAND_TAILS = {
    # ---- RHP, FADE (vs 6+ lefties) ----
    "Scherzer": ("R", "fade"), "Brady Singer": ("R", "fade"),
    "Jack Leiter": ("R", "fade"), "Walbert Urena": ("R", "fade"),
    "Imai": ("R", "fade"),
    # ---- RHP, TAKE (vs 6+ lefties) ----
    "Nick Martinez": ("R", "take"), "Soriano": ("R", "take"),
    "Aaron Nola": ("R", "take"), "Glasnow": ("R", "take"), "Ginn": ("R", "take"),
    # ---- LHP, FADE (vs 6+ righties) ----
    "Prielipp": ("L", "fade"), "Tolle": ("L", "fade"),
    "Weathers": ("L", "fade"), "Quintana": ("L", "fade"),
    # ---- LHP, TAKE (vs 6+ righties) ----
    "Wrobleski": ("L", "take"), "Messick": ("L", "take"),
    "Jacob Lopez": ("L", "take"), "Matthew Boyd": ("L", "take"),
    "Foster Griffin": ("L", "take"), "Eduardo Rodriguez": ("L", "take"),
    "Corbin": ("L", "take"),
    # ---- Added 2026-07-27 from the shadow watchlist ----
    "Framber Valdez": ("L", "fade"), "Mike Burrows": ("R", "fade"),
    "David Peterson": ("L", "fade"), "Slade Cecconi": ("R", "fade"),
    "Joey Cantillo": ("L", "take"), "Shane Baz": ("R", "fade"),
    "Bryce Elder": ("R", "take"),
}
_TAIL_TOKENS = {e: set(_norm(e).split()) for e in HAND_TAILS}

_CACHE_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..",
    "data", "pitcher_cache", "mlb"))


def tail_entry(pitcher_name):
    """(entry, hand, action) if pitcher is on the hand-tails list, else (None, None, None)."""
    nt = set(_norm(pitcher_name).split())
    if nt:
        for e, toks in _TAIL_TOKENS.items():
            if toks and toks.issubset(nt):
                hand, action = HAND_TAILS[e]
                return e, hand, action
    return None, None, None


@functools.lru_cache(maxsize=1)
def _load_caches():
    """(bat_sides id->L/R/S, batting_orders date->team->[ids]) from the K caches."""
    def _load(name):
        p = os.path.join(_CACHE_DIR, name)
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    # find the season bat_sides file (player_bat_sides_YYYY.json)
    bs = {}
    for fn in os.listdir(_CACHE_DIR) if os.path.isdir(_CACHE_DIR) else []:
        if fn.startswith("player_bat_sides_") and fn.endswith(".json"):
            bs = _load(fn)
            break
    bo = _load("batting_orders_2026.json")
    return bs, bo


def opp_lineup_counts(opp_team, date_iso):
    """(lefty L+S, righty R+S) bats in opp_team's lineup on date_iso, or (None, None)."""
    bs, bo = _load_caches()
    lu = (bo.get(date_iso) or {}).get(opp_team)
    if not lu:
        return None, None
    lefty = sum(1 for pid in lu if bs.get(str(pid)) in ("L", "S"))
    righty = sum(1 for pid in lu if bs.get(str(pid)) in ("R", "S"))
    return lefty, righty


def qualifies(hand, opp_lefty, opp_righty):
    """True if the opponent lineup is opposite-hand-heavy enough for this arm."""
    if opp_lefty is None:
        return False
    return (opp_lefty >= HAND_MIN) if hand == "R" else (opp_righty >= HAND_MIN)


def fade_overridden_by_take(pitcher_name, date_iso, opp_team):
    """True if a fade-list fade on this arm should yield to a hand-tails TAKE.

    Fires only for arms that are (a) hand-tails TAKE and (b) facing a qualifying
    opposite-hand lineup on this date -- i.e. the rare overlap game.
    """
    _, hand, action = tail_entry(pitcher_name)
    if action != "take":
        return False
    lefty, righty = opp_lineup_counts(opp_team, date_iso)
    return qualifies(hand, lefty, righty)

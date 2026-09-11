# MLBstrikeouts/scripts/hand_tails.py
# LIVE handedness-conditional FADE tracker (own ledger: mlb-hand-tails.json).
#
# Thesis (walk-forward, 2026): certain arms' game outcomes swing predictably
# when they face a lineup stacked with opposite-hand bats. Each listed arm is
# bet ONLY when the opponent lineup has >= HAND_MIN opposite-hand bats:
#   RHP -> vs HAND_MIN+ lefty bats (L or switch)
#   LHP -> vs HAND_MIN+ righty bats (R or switch)
# action:
#   "fade" -> bet the OPPONENT's moneyline (arm's team expected to lose)
#
# 2026-07-28: FADE-ONLY. The "take" (tail) side was removed -- the handedness
# model no longer backs an arm's own team. Only handedness FADEs are tracked
# and bet. fade_overridden_by_take is now inert (no take arms) but kept so the
# fade-list model's optional call site stays valid.
#
# Tracked in its own ledger, separate from the fade-list model. Caveat: the
# picks were chosen in-sample, so the season backtest overstates edge -- this
# is a live paper-forward test.

import os
import json
import functools

from fade_list import _norm

HAND_MIN = 6

# The active fade list is MANUALLY CURATED (hand-tails-manual.json). Each listed
# arm is bet from its `since` date forward (a single open [since, None) window),
# so the ledger still grades walk-forward (no in-sample credit before `since`).
# Nothing is auto-added or auto-removed -- the user edits the manual list. The
# daily run separately computes walk-forward qualifiers and flags any NEW
# qualifier not on this list as a promotion candidate (dashboard badge); that
# detection lives in build_hand_tails_roster.py and never edits the manual list.
# tail_entry(name, date) gates bets to each arm's [since, None) window. If the
# manual file is missing, fall back to a hardcoded list so the model still runs.
_ROSTER_PATH = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "hand-tails-manual.json"))

_HARDCODED_FADES = {  # fallback only (used if the manual file is absent)
    "Brady Singer": "R", "Mike Burrows": "R", "Shane Baz": "R",
    "Merrill Kelly": "R", "Trevor McDonald": "R",
    "Framber Valdez": "L", "Nick Lodolo": "L",
}


def _load_roster():
    """{name: {"hand", "windows": [[since, until], ...]}} from the manual list. An
    arm with no `since` is treated as active from the start (grades all its
    starts). `until` retires an arm walk-forward: starts ON/AFTER it are not bet,
    while the [since, until) history keeps grading -- same convention as
    FADE_WINDOW in fade_list.py. Omit `until` for an open window.

    An arm that is retired and later re-promoted needs TWO windows, so it can
    also carry an explicit `windows` list ([[since, until|null], ...]) instead of
    the single since/until pair. That keeps the retired stretch graded while the
    arm goes live again from a later date; without it, moving `since` forward
    would silently drop the earlier history off the ledger."""
    try:
        with open(_ROSTER_PATH, encoding="utf-8") as f:
            arms = json.load(f).get("arms", {})
    except Exception:
        arms = {}
    if arms:
        out = {}
        for n, a in arms.items():
            wins = a.get("windows")
            if wins:
                wins = [[w[0] or "2000-01-01", w[1] if len(w) > 1 else None]
                        for w in wins]
            else:
                wins = [[a.get("since") or "2000-01-01", a.get("until")]]
            out[n] = {"hand": a.get("hand"), "windows": wins}
        return out
    return {n: {"hand": h, "windows": [["2000-01-01", None]]}
            for n, h in _HARDCODED_FADES.items()}


_ROSTER = _load_roster()
_TAIL_TOKENS = {n: set(_norm(n).split()) for n in _ROSTER}


def _active_on(name, date_iso):
    """True if the arm is a live fade for date_iso (inside an active [add, remove)
    window). date_iso=None asks 'currently active' (has an open window)."""
    wins = _ROSTER.get(name, {}).get("windows", [])
    if date_iso is None:
        return any(rem is None for _a, rem in wins)
    return any(add <= date_iso and (rem is None or date_iso < rem) for add, rem in wins)

_CACHE_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..",
    "data", "pitcher_cache", "mlb"))


def tail_entry(pitcher_name, date=None):
    """(entry, hand, 'fade') if pitcher is an ACTIVE fade on `date` (or currently,
    when date is None), else (None, None, None). Walk-forward: an arm matches only
    inside one of its qualified [add, remove) windows -- so a start on the day the
    record first cleared the bar is NOT bet (the arm goes live its next start)."""
    nt = set(_norm(pitcher_name).split())
    if nt:
        for name, toks in _TAIL_TOKENS.items():
            if toks and toks.issubset(nt):
                if _active_on(name, date):
                    return name, _ROSTER[name]["hand"], "fade"
                return None, None, None
    return None, None, None


def active_roster():
    """{name: (hand, 'fade')} for arms currently active (an open window)."""
    return {n: (info["hand"], "fade") for n, info in _ROSTER.items()
            if _active_on(n, None)}


# Back-compat: modules importing HAND_TAILS (watchlist exclusion, roster display)
# expect the currently-active arms.
HAND_TAILS = active_roster()


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
    # find the season bat_sides file (player_bat_sides_YYYY.json) via the same
    # helper _persist_hands writes through, so reader and writer can never pick
    # different files when more than one season is cached.
    path = _bat_sides_path()
    bs = _load(os.path.basename(path)) if path else {}
    bo = _load("batting_orders_2026.json")
    return bs, bo


@functools.lru_cache(maxsize=256)
def _load_today_lineups(date_iso):
    """{team: {"ids": [player_ids], "confirmed": bool}} from lineups_YYYYMMDD.json
    -- the lineup the props model writes for the current (not-yet-played) slate.
    `confirmed` reflects whether it is a REAL posted lineup (the props model's
    own `confirmed` flag, i.e. source 'lineup' with a full card) rather than a
    projected/default card (e.g. 'rotowire_default_vs_LHP'); the hand-tails
    grader gates bets on it. The season batting_orders cache only holds completed
    games, so the current date isn't in it; this fills that gap so today's
    hand-tails triggers fire."""
    key = str(date_iso).replace("-", "")
    p = os.path.join(_CACHE_DIR, f"lineups_{key}.json")
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    out = {}
    for team, v in (data or {}).items():
        if isinstance(v, dict):
            ids = v.get("player_ids")
            confirmed = bool(v.get("confirmed")) or v.get("source") == "lineup"
        else:
            # Legacy bare-list shape carried no confirmation metadata; a betting
            # gate should not assume a real lineup, so treat it as unconfirmed.
            ids, confirmed = v, False
        if ids:
            out[team] = {"ids": list(ids), "confirmed": confirmed}
    return out


_LIVE_HAND = {}  # pid(str) -> 'L'/'R'/'S' resolved live for batters missing from bat_sides
_PRIMED = False   # True once the whole-season prime pass below has run

# fetch_player_bat_sides() caches player_bat_sides_YYYY.json with max_age_hours=
# None ("never changes mid-season"), which is true of a player's bat side but
# NOT of the roster: every call-up, trade and debut after that file was first
# written is missing from it. The season replays in build_hand_tails_roster,
# hand_tails_watch and run_daily_hand_tails walk ~2400 games and hit
# opp_lineup_state per side, so those stragglers used to cost one /people round
# trip PER LINEUP that introduced one (measured 2026-09-11: 209 requests, 66.4s
# of a 66.8s replay, for 216 ids that all resolved) -- and the answers died with
# the process, so all three scripts paid it again on every run, six runs a day.
# _prime_hands resolves every missing id across the cached lineups in one
# batched pass and writes the results back into the season file, so the second
# run onward costs nothing.
_PEOPLE_CHUNK = 250   # ids per /people request (URL length safety)


def _bat_sides_path():
    """Path of the season bat_sides cache _load_caches() reads, or None."""
    if not os.path.isdir(_CACHE_DIR):
        return None
    for fn in sorted(os.listdir(_CACHE_DIR)):
        if fn.startswith("player_bat_sides_") and fn.endswith(".json"):
            return os.path.join(_CACHE_DIR, fn)
    return None


def _persist_hands(resolved):
    """Merge newly resolved {pid: hand} into the season bat_sides cache.

    Best-effort and additive: existing entries are never overwritten, so this
    cannot corrupt the file fetch_player_bat_sides wrote. Written via a temp
    file + replace so a crash mid-write leaves the original intact.
    """
    if not resolved:
        return
    path = _bat_sides_path()
    if not path:
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        added = {k: v for k, v in resolved.items() if str(k) not in data}
        if not added:
            return
        data.update(added)
    except Exception:
        return
    # Same write discipline as mlb_stats._save_cache: indent=2 (so the file
    # stays diffable) and a short retry, because this repo lives under a synced
    # folder that intermittently locks files mid-sync.
    import time as _time
    tmp = path + ".tmp"
    for attempt in range(5):
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, path)
            _load_caches.cache_clear()
            return
        except OSError:
            _time.sleep(0.3 * (attempt + 1))
    try:
        if os.path.exists(tmp):
            os.remove(tmp)
    except OSError:
        pass


def _all_cached_lineup_ids():
    """Every batter id appearing in any cached lineup: the per-day posted cards
    (lineups_YYYYMMDD.json) plus the season boxscore batting orders. These are
    exactly the ids opp_lineup_state can ask about during a season replay."""
    ids = set()
    try:
        for fn in os.listdir(_CACHE_DIR):
            if not (fn.startswith("lineups_") and fn.endswith(".json")):
                continue
            try:
                with open(os.path.join(_CACHE_DIR, fn), "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
            except Exception:
                continue
            for v in data.values():
                lu = v.get("player_ids") if isinstance(v, dict) else v
                for pid in (lu or []):
                    ids.add(str(pid))
    except Exception:
        pass
    _, bo = _load_caches()
    for teams in (bo or {}).values():
        for lu in (teams or {}).values():
            for pid in (lu or []):
                ids.add(str(pid))
    return ids


def _people_lookup(ids):
    """{pid: 'L'/'R'/'S'} from MLB /people, batched. Silent on failure."""
    out = {}
    try:
        import requests
    except Exception:
        return out
    ids = list(ids)
    for i in range(0, len(ids), _PEOPLE_CHUNK):
        chunk = ids[i:i + _PEOPLE_CHUNK]
        try:
            r = requests.get(
                "https://statsapi.mlb.com/api/v1/people?personIds=" + ",".join(chunk),
                timeout=15)
            for p in r.json().get("people", []):
                code = (p.get("batSide") or {}).get("code")
                if code in ("L", "R", "S"):
                    out[str(p.get("id"))] = code
        except Exception:
            continue
    return out


def _prime_hands():
    """One-shot: resolve every batter id missing from the bat_sides cache across
    all cached lineups, in batched /people calls, and persist them. Runs at most
    once per process, on the first miss."""
    global _PRIMED
    if _PRIMED:
        return
    _PRIMED = True
    bs, _ = _load_caches()
    missing = [i for i in _all_cached_lineup_ids() if i not in bs]
    if not missing:
        return
    resolved = _people_lookup(missing)
    _LIVE_HAND.update(resolved)
    _persist_hands(resolved)


def _resolve_hands(missing_ids):
    """Fill _LIVE_HAND for batter ids not in the bat_sides cache. The first miss
    primes the whole season in one batched pass (and persists it); anything the
    prime could not cover -- an id from a lineup handed in by a caller rather
    than read off the cache -- still falls back to a direct /people call.
    Best-effort: covers recent call-ups/trades so they still count."""
    ids = [str(i) for i in dict.fromkeys(missing_ids) if str(i) not in _LIVE_HAND]
    if not ids:
        return
    if not _PRIMED:
        _prime_hands()
        ids = [i for i in ids if i not in _LIVE_HAND]
        if not ids:
            return
    resolved = _people_lookup(ids)
    _LIVE_HAND.update(resolved)
    _persist_hands(resolved)


def opp_lineup_state(opp_team, date_iso):
    """(lefty L+S, righty R+S, confirmed) for opp_team's lineup on date_iso, or
    (None, None, None) if no lineup is available.

    `confirmed` distinguishes a REAL lineup -- a completed game's boxscore (past
    dates, always confirmed) or today's POSTED lineup -- from a projected/default
    card (e.g. a RotoWire default-vs-hand lineup). The hand-tails grader only
    places a bet on a confirmed lineup; a qualifying projected lineup is surfaced
    as an unconfirmed candidate instead.

    Past dates come from the season batting_orders cache; for the current slate
    (not yet in that cache) it falls back to today's posted lineup
    (lineups_YYYYMMDD.json, the same source the props model reads), whose
    confirmation flag is carried through. Batters missing from the bat_sides
    cache are resolved live via /people so newly added hitters still count.
    """
    bs, bo = _load_caches()
    # Prefer the PRE-GAME posted lineup (lineups_YYYYMMDD.json -- the same
    # confirmed card the K's CSW model uses, archived per day). That is the 9
    # the fade FIRES on, so it must also be the 9 it GRADES on. The boxscore
    # batting order includes pinch-hitters / substitutions who entered during
    # the game, so it can differ from the posted card -- grading on it would
    # erase a bet the announced lineup would have placed (and vice versa). Fall
    # back to the boxscore only when no posted card was archived for that date.
    entry = _load_today_lineups(date_iso).get(opp_team)
    if entry:
        lu, confirmed = entry["ids"], entry["confirmed"]
    else:
        lu = (bo.get(date_iso) or {}).get(opp_team)
        confirmed = True  # completed-game boxscore is what actually happened
    if not lu:
        return None, None, None
    missing = [pid for pid in lu if str(pid) not in bs and str(pid) not in _LIVE_HAND]
    if missing:
        _resolve_hands(missing)

    def side(pid):
        return bs.get(str(pid)) or _LIVE_HAND.get(str(pid))

    lefty = sum(1 for pid in lu if side(pid) in ("L", "S"))
    righty = sum(1 for pid in lu if side(pid) in ("R", "S"))
    return lefty, righty, confirmed


def opp_lineup_counts(opp_team, date_iso):
    """(lefty L+S, righty R+S) bats in opp_team's lineup -- back-compat wrapper
    over opp_lineup_state that drops the confirmed flag. Used where confirmation
    doesn't matter: the in-sample season replay in the watchlist and the inert
    take-override below."""
    lefty, righty, _ = opp_lineup_state(opp_team, date_iso)
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


def fade_conflicts_hand_fade(opp_pitcher, date_iso, fade_team):
    """True if a fade-list fade should yield because the OPPOSING starter is an
    ACTIVE hand-tails FADE on a qualifying lineup.

    Orientation: the fade-list fade bets opp_pitcher's team; the hand-tails
    fade of opp_pitcher bets fade_team (his opponent). The two point at
    opposite sides of the same game, and handedness outranks venue
    (precedence: vs-team > handedness > venue), so the venue fade yields.
    fade_team's lineup is the one opp_pitcher's hand is measured against.
    Honors FADE_EXCEPT_VS_TEAM so the override only fires where hand-tails
    would actually place its bet.
    """
    entry, hand, action = tail_entry(opp_pitcher, date_iso)
    if action != "fade":
        return False
    from fade_list import fade_except_vs_team
    if fade_team in fade_except_vs_team(opp_pitcher):
        return False
    lefty, righty = opp_lineup_counts(fade_team, date_iso)
    return qualifies(hand, lefty, righty)

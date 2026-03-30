# pyMLB/scripts/sources/pitcher_stats.py
# Fetch individual pitcher stats via pybaseball for starter/bullpen split.
# Returns starters per team and aggregated bullpen stats per team.

import os
import sys
import json
import time
import math
import logging
import datetime

_dir = os.path.dirname(os.path.abspath(__file__))
PITCHER_CACHE_DIR = os.path.normpath(os.path.join(_dir, "..", "..", "..", "data", "pitcher_cache", "mlb"))
sys.path.insert(0, os.path.join(_dir, ".."))
import defaults

logger = logging.getLogger(__name__)

_FIP_DEFAULT = 4.50


def _resolve_team_name(abbr, aliases):
    """
    Map a pybaseball team abbreviation to a canonical team name.
    Checks aliases dict first (case-insensitive), then tries substring match
    against canonical team names.
    """
    key = abbr.lower().strip()
    if key in aliases:
        return aliases[key]

    # Try case-insensitive substring match against canonical names
    for canonical in defaults.MLB_TEAMS:
        if key in canonical.lower():
            return canonical

    logger.warning("pitcher_stats: unmapped team abbreviation '%s'", abbr)
    return None


def _safe_float(val, default=None):
    """Safely coerce a value to float, returning default on NaN/None/error."""
    if val is None:
        return default
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return default
        return f
    except (TypeError, ValueError):
        return default


def _normalize_pct(val):
    """
    Normalize a percentage column to 0-1 range.
    pybaseball sometimes returns K% / BB% as 0-100 or as 0-1.
    Returns None if val is None.
    """
    if val is None:
        return None
    # If value is > 1, assume it's stored as a percentage (e.g. 24.5 means 24.5%)
    return val / 100.0 if val > 1.0 else val


def _fetch_with_retry(fetch_fn, max_retries=3, sleep_before=2):
    """
    Call fetch_fn() with a leading sleep and exponential backoff on exceptions.
    Sleeps sleep_before seconds before the first call, then 5/10/15 on retries.
    """
    time.sleep(sleep_before)
    backoff_schedule = [5, 10, 15]
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return fetch_fn()
        except Exception as exc:
            last_exc = exc
            if attempt < max_retries:
                wait = backoff_schedule[min(attempt, len(backoff_schedule) - 1)]
                logger.warning(
                    "pitcher_stats: fetch attempt %d failed (%s), retrying in %ds",
                    attempt + 1, exc, wait,
                )
                time.sleep(wait)
            else:
                logger.error("pitcher_stats: all retries exhausted: %s", exc)
    raise last_exc


def _build_pitcher_record(row, df):
    """
    Extract per-pitcher stats from a single DataFrame row (by integer index).
    Returns a dict with FIP, xFIP, SIERA, K_BB_pct, IP, GS, hand.
    """
    def get(col):
        try:
            return df.at[row, col]
        except KeyError:
            return None

    fip = _safe_float(get("FIP"), _FIP_DEFAULT)
    xfip = _safe_float(get("xFIP"), _FIP_DEFAULT)
    siera = _safe_float(get("SIERA"), _FIP_DEFAULT)

    k_raw = _safe_float(get("K%"), None)
    bb_raw = _safe_float(get("BB%"), None)
    k_pct = _normalize_pct(k_raw)
    bb_pct = _normalize_pct(bb_raw)

    if k_pct is not None and bb_pct is not None:
        k_bb_pct = k_pct - bb_pct
    else:
        k_bb_pct = 0.0

    ip = _safe_float(get("IP"), 0.0)
    gs = _safe_float(get("GS"), 0.0)
    gs = int(gs) if gs is not None else 0

    hand_raw = get("Throws")
    if hand_raw is None:
        hand_raw = get("throws")
    hand = str(hand_raw).strip().upper() if hand_raw is not None else "R"
    if hand not in ("L", "R"):
        hand = "R"

    return {
        "FIP": fip,
        "xFIP": xfip,
        "SIERA": siera,
        "K_BB_pct": k_bb_pct,
        "IP": ip,
        "GS": gs,
        "hand": hand,
    }


def _aggregate_bullpen(relievers):
    """
    Compute IP-weighted FIP and K_BB_pct aggregate for a list of reliever dicts.
    Skips pitchers with IP < 3.
    Returns {"FIP": float, "K_BB_pct": float, "IP_total": float} or None if no qualifying relievers.
    """
    total_ip = 0.0
    fip_weighted = 0.0
    kbb_weighted = 0.0

    for r in relievers:
        ip = r.get("IP", 0.0) or 0.0
        if ip < 3.0:
            continue
        fip = r.get("FIP", _FIP_DEFAULT) or _FIP_DEFAULT
        kbb = r.get("K_BB_pct", 0.0) or 0.0
        fip_weighted += fip * ip
        kbb_weighted += kbb * ip
        total_ip += ip

    if total_ip == 0.0:
        return {"FIP": _FIP_DEFAULT, "K_BB_pct": 0.0, "IP_total": 0.0}

    return {
        "FIP": round(fip_weighted / total_ip, 4),
        "K_BB_pct": round(kbb_weighted / total_ip, 4),
        "IP_total": round(total_ip, 1),
    }


def fetch_pitcher_stats(season=None, date_str=None):
    """
    Fetch individual pitcher stats via pybaseball for starter/bullpen split.

    Parameters
    ----------
    season   : int or None — season year (defaults to current year)
    date_str : str or None — YYYYMMDD cache key (defaults to today)

    Returns
    -------
    dict with keys:
        "starters" : {canonical_team_name: {pitcher_name: {FIP, xFIP, SIERA, K_BB_pct, IP, GS, hand}}}
        "bullpens"  : {canonical_team_name: {FIP, K_BB_pct, IP_total}}
    Returns {"starters": {}, "bullpens": {}} on total failure.
    """
    today = datetime.date.today()
    if season is None:
        season = today.year
    if date_str is None:
        date_str = today.strftime("%Y%m%d")

    today_str = today.strftime("%Y%m%d")
    is_today = (date_str == today_str)

    os.makedirs(PITCHER_CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(PITCHER_CACHE_DIR, f"{date_str}.json")

    # Check disk cache — permanent for past dates; always-fresh for today
    if os.path.exists(cache_path) and not is_today:
        try:
            with open(cache_path, "r") as f:
                cached = json.load(f)
            return {
                "starters": cached.get("starters", {}),
                "bullpens": cached.get("bullpens", {}),
            }
        except Exception as exc:
            logger.warning("pitcher_stats: cache read failed (%s), re-fetching", exc)

    # --- Import pybaseball ---
    try:
        from pybaseball import pitching_stats  # noqa: F401
    except ImportError:
        logger.error(
            "pitcher_stats: pybaseball is not installed. "
            "Run: pip install pybaseball"
        )
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r") as f:
                    cached = json.load(f)
                logger.warning("pitcher_stats: using stale cache for %s (pybaseball missing)", date_str)
                return {
                    "starters": cached.get("starters", {}),
                    "bullpens": cached.get("bullpens", {}),
                }
            except Exception:
                pass
        return {"starters": {}, "bullpens": {}}

    # --- Fetch from pybaseball with retry ---
    df = None
    try:
        df = _fetch_with_retry(lambda: pitching_stats(season, qual=0))
    except Exception as exc:
        logger.error("pitcher_stats: pitching_stats fetch failed: %s", exc)

    if df is None:
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r") as f:
                    cached = json.load(f)
                logger.warning("pitcher_stats: using stale cache for %s", date_str)
                return {
                    "starters": cached.get("starters", {}),
                    "bullpens": cached.get("bullpens", {}),
                }
            except Exception:
                pass
        return {"starters": {}, "bullpens": {}}

    aliases = defaults.ENGINE_CONFIG["TEAM_NAME_ALIASES"]

    # Ensure required columns exist; build safe column set
    available_cols = set(df.columns.tolist())

    # Accumulate per-team starters and relievers
    team_starters = {}   # canonical_name -> {pitcher_name: stats_dict}
    team_relievers = {}  # canonical_name -> [stats_dict, ...]

    for idx in df.index:
        # Resolve team
        team_raw = None
        for col in ("Team", "team"):
            if col in available_cols:
                try:
                    team_raw = df.at[idx, col]
                except KeyError:
                    pass
                if team_raw is not None:
                    break

        if team_raw is None:
            continue

        canonical = _resolve_team_name(str(team_raw).strip(), aliases)
        if canonical is None:
            continue

        # Pitcher name
        pitcher_name = None
        for col in ("Name", "name", "PlayerName"):
            if col in available_cols:
                try:
                    pitcher_name = str(df.at[idx, col]).strip()
                except (KeyError, TypeError):
                    pass
                if pitcher_name:
                    break
        if not pitcher_name:
            pitcher_name = f"Unknown_{idx}"

        record = _build_pitcher_record(idx, df)

        if record["GS"] > 0:
            # Starter
            if canonical not in team_starters:
                team_starters[canonical] = {}
            team_starters[canonical][pitcher_name] = {
                "FIP": record["FIP"],
                "xFIP": record["xFIP"],
                "SIERA": record["SIERA"],
                "K_BB_pct": record["K_BB_pct"],
                "IP": record["IP"],
                "GS": record["GS"],
                "hand": record["hand"],
            }
        else:
            # Reliever
            if canonical not in team_relievers:
                team_relievers[canonical] = []
            team_relievers[canonical].append(record)

    # Aggregate bullpen stats per team
    team_bullpens = {}
    all_teams = set(team_starters.keys()) | set(team_relievers.keys())
    for canonical in all_teams:
        relievers = team_relievers.get(canonical, [])
        team_bullpens[canonical] = _aggregate_bullpen(relievers)

    result = {
        "starters": team_starters,
        "bullpens": team_bullpens,
    }

    # Write cache
    try:
        payload = {
            "fetched_at": date_str,
            "season": season,
            "starters": team_starters,
            "bullpens": team_bullpens,
        }
        with open(cache_path, "w") as f:
            json.dump(payload, f, indent=2)
    except Exception as exc:
        logger.warning("pitcher_stats: cache write failed: %s", exc)

    return result

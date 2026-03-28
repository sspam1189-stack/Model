# scripts/sources/ncaa_stats.py
# ────────────────────────────────────────────────────────────────────────────
# Fetches NCAA team stats from Barttorvik (T-Rank) via curl.
#
# Barttorvik has bot protection requiring a JS verification cookie.
# We use curl with a cookie jar to handle this.
#
# Array indices: [0]=name [1]=adjO [2]=adjD [6]=GP [7]=oEFG [9]=oTO [11]=oOR [15]=adjT
# ────────────────────────────────────────────────────────────────────────────

import subprocess
import os
import sys
import json
import time
import tempfile
import random
import math
from datetime import datetime


def current_season_year():
    now = datetime.now()
    year = now.year
    month = now.month
    return year + 1 if month >= 10 else year


# Cookie file with forward slashes (works on both Windows cmd and bash)
COOKIE_FILE = os.path.join(tempfile.gettempdir(), "barttorvik_cookies.txt").replace(os.sep, "/")
NULL_DEV = "NUL" if sys.platform == "win32" else "/dev/null"
# Force Windows cmd.exe shell — Git Bash's sh.exe mangles cookie file paths
SHELL_EXE = os.environ.get("COMSPEC", None) if sys.platform == "win32" else None

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:138.0) Gecko/20100101 Firefox/138.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0",
]


def random_ua():
    return USER_AGENTS[math.floor(random.random() * len(USER_AGENTS))]


def _run_curl(cmd):
    """Run a curl command, using cmd.exe on Windows."""
    kwargs = {"timeout": 30, "capture_output": True}
    if SHELL_EXE:
        kwargs["shell"] = True
        # On Windows, run via cmd.exe
        result = subprocess.run(cmd, **kwargs)
    else:
        kwargs["shell"] = True
        result = subprocess.run(cmd, **kwargs)
    return result


# Fetch with retry: refresh cookie if needed, retry up to 5 times with longer backoff
def fetch_barttorvik_json(year):
    # Always start fresh — delete stale cookies
    try:
        os.unlink(COOKIE_FILE)
    except OSError:
        pass

    MAX_ATTEMPTS = 5
    url = f"https://barttorvik.com/trank.php?year={year}&json=1"

    for attempt in range(MAX_ATTEMPTS):
        ua = random_ua()

        # Step 1: POST js_test_submitted=1 to trank.php to get js_verified cookie
        try:
            _run_curl(
                f'curl -s -c "{COOKIE_FILE}" -d "js_test_submitted=1" "https://barttorvik.com/trank.php" -H "User-Agent: {ua}" -H "Referer: https://barttorvik.com/" -o {NULL_DEV}'
            )
        except Exception:
            pass
        time.sleep(0.5)

        # Step 2: GET the JSON with the cookie
        result = _run_curl(
            f'curl -s -b "{COOKIE_FILE}" "{url}" -H "User-Agent: {ua}" -H "Referer: https://barttorvik.com/trank.php"'
        )
        text = result.stdout.decode("utf-8", errors="replace").strip()
        if text.startswith("["):
            return text

        delay = (attempt + 1) * 8
        print(f"  [ncaa_stats] Attempt {attempt + 1}/{MAX_ATTEMPTS}: got HTML, retrying in {delay}s...")
        try:
            os.unlink(COOKIE_FILE)
        except OSError:
            pass
        time.sleep(delay)

    raise Exception("Barttorvik: failed after 5 attempts -- possible rate limit")


# Parse Barttorvik JSON array into our stats format
def parse_barttorvik_data(data):
    stats = {}
    skipped = 0

    for team in data:
        name = team[0]
        gp = int(float(team[6] or 0))
        if not name or gp < 5:
            skipped += 1
            continue

        adj_o = float(team[1] or 0)
        adj_d = float(team[2] or 0)
        o_efg = float(team[7] or 0) / 100
        o_to = float(team[9] or 0) / 100
        o_or = float(team[11] or 0) / 100
        adj_t = float(team[15] or 0)

        if not adj_o or not adj_d or not adj_t:
            skipped += 1
            continue

        stats[name] = {
            "OFF": adj_o, "DEF": adj_d,
            "TS": o_efg or 0.50, "TO": o_to or 0.18, "ORR": o_or or 0.30,
            "PACE": adj_t, "GP": gp,
        }

    return {"stats": stats, "skipped": skipped}


# Local cache: store fetched data to avoid repeat Barttorvik requests.
_SRCDIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(_SRCDIR, "..", "..", "..", "data")
CACHE_FILE = os.path.join(CACHE_DIR, "barttorvik_cache.json")
CACHE_TTL_MS = 20 * 3600 * 1000  # 20 hours -- stats update once/day


def load_cache(year):
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("year") == year and len(cached.get("stats", {})) >= 100:
            return cached
    except Exception:
        pass
    return None


def fetch_ncaa_stats(season_year=None):
    year = season_year or current_season_year()
    print(f"  [ncaa_stats] Fetching Barttorvik stats for {year}...")

    # Check local cache first (fresh = use directly)
    try:
        stat_info = os.stat(CACHE_FILE)
        age_ms = (time.time() - stat_info.st_mtime) * 1000
        if age_ms < CACHE_TTL_MS:
            cached = load_cache(year)
            if cached:
                team_count = len(cached["stats"])
                age_min = round(age_ms / 60000)
                print(f"  [ncaa_stats] Using cached data ({team_count} teams, {age_min}min old)")
                return cached["stats"]
    except OSError:
        pass

    # Try fetching fresh data
    try:
        text = fetch_barttorvik_json(year)
        data = json.loads(text)

        if not isinstance(data, list) or len(data) < 100:
            data_desc = len(data) if isinstance(data, list) else type(data).__name__
            raise Exception(f"Barttorvik: unexpected data ({data_desc} entries)")

        result = parse_barttorvik_data(data)
        stats = result["stats"]
        skipped = result["skipped"]
        count = len(stats)
        print(f"  [ncaa_stats] Got {count} teams ({skipped} skipped)")

        if count < 100:
            raise Exception(f"Barttorvik incomplete: only {count} teams")

        # Cache to disk
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({"year": year, "stats": stats, "fetchedAt": datetime.now().isoformat()}, f)
            print(f"  [ncaa_stats] Cached to {CACHE_FILE}")
        except Exception:
            pass

        return stats
    except Exception as err:
        # Fallback: use stale cache if available (better than crashing)
        stale = load_cache(year)
        if stale:
            fetched_at = stale.get("fetchedAt", "")
            try:
                age_hrs = round((time.time() - datetime.fromisoformat(fetched_at).timestamp()) / 3600)
            except Exception:
                age_hrs = "?"
            team_count = len(stale["stats"])
            print(f"  [ncaa_stats] WARNING: Fetch failed ({err}). Using stale cache ({age_hrs}h old, {team_count} teams)")
            return stale["stats"]
        raise  # no cache at all -- nothing we can do


def fetch_ncaa_stats_enhanced(date_to=None):
    season = fetch_ncaa_stats()
    print(f"  [ncaa_stats] Enhanced: {len(season)} teams (season only, no splits)")
    return {"season": season, "last10": None, "home": None, "away": None}

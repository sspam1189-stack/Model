# core/utils.py
# Shared utility functions used across all models.

import math
import re


def is_finite(x):
    """Check if x is a finite number (not None, not NaN, not inf)."""
    if x is None:
        return False
    try:
        return math.isfinite(x)
    except (TypeError, ValueError):
        return False


def r3(x):
    """Round to 3 decimal places."""
    return round(x * 1000) / 1000


def r4(x):
    """Round to 4 decimal places."""
    return round(x * 10000) / 10000


def clamp(x, lo, hi):
    """Clamp x between lo and hi."""
    return max(lo, min(hi, x))


def norm_key(s):
    """Normalize a team name to a lowercase key for comparison."""
    s = str(s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def esc(s):
    """HTML-escape a string."""
    return (str(s if s is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def fmt_num(x, digits=1):
    """Format a number with N decimal digits, or 'n/a'."""
    if isinstance(x, (int, float)) and math.isfinite(x):
        return f"{x:.{digits}f}"
    return "n/a"


def fmt_units(u):
    """Format units as +X.Xu or -X.Xu."""
    if not isinstance(u, (int, float)) or not math.isfinite(u):
        return "\u2014"
    return f"+{u:.1f}u" if u >= 0 else f"{u:.1f}u"


def win_pct(w, l):
    """Win percentage as a string."""
    total = w + l
    return f"{(w / total * 100):.1f}" if total > 0 else "n/a"


def get_tz(timezone_name="America/Chicago"):
    """Get a timezone object, with pytz fallback."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(timezone_name)
    except ImportError:
        import pytz
        return pytz.timezone(timezone_name)


def parse_spread_pick(pick):
    """Parse a spread pick string like 'Team +3.5' into components."""
    if not pick or pick == "PASS":
        return None
    m = re.match(r"(.+?)\s+([+-])(\d+(?:\.\d+)?)", pick)
    if not m:
        return None
    return {"team": m.group(1).strip(), "sign": m.group(2), "pts": float(m.group(3))}

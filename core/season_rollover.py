# core/season_rollover.py
# Start each season clean.
#
# Nothing in the pipeline used to reset season-scoped state at rollover:
#   - core/kalman_state.load_kalman_state re-initialises ONLY when "teams" is
#     empty, and meta.season is written at init but never read back
#   - apply_daily_drift inflates variance (capped at 14 days) but never decays
#     the means, so a whole offseason moved a team offset by 0.00 points
#   - backfill deliberately preserves accumulation
# so the 2025-26 Kalman offsets (NYK +8.18, ATL -4.62) would have been applied
# at full weight to the first 2026-27 pick, on rosters that no longer exist.
# The MIN_GP=15 stats burn-in makes it worse rather than better: proj_score
# returns None until both teams have 15 GP, so during burn-in no picks fire AND
# no Kalman updates happen — the stale offsets sit frozen, then land intact.
#
# This module is called explicitly from each model's run_daily with the RUN
# DATE, not from load_*(). That keeps it off the backfill path: replaying old
# dates must never trip a rollover.
#
# Every reset archives first. Nothing is destroyed.

import json
import os
import shutil


def season_label(date_key, start_month=10):
    """'20261021' -> '2026-27'. start_month is the month the season opens."""
    d = str(date_key).replace("-", "")
    y, m = int(d[:4]), int(d[4:6])
    start = y if m >= start_month else y - 1
    return f"{start}-{str(start + 1)[2:]}"


def _season_of_date(date_str, start_month):
    d = str(date_str or "").replace("-", "")
    if len(d) < 6 or not d[:6].isdigit():
        return None
    return season_label(d, start_month)


def _archive_target(path, season):
    """<dir>/archive/<base>-<season>.json, never clobbering an existing file."""
    arch_dir = os.path.join(os.path.dirname(os.path.abspath(path)), "archive")
    base = os.path.splitext(os.path.basename(path))[0]
    cand = os.path.join(arch_dir, f"{base}-{season}.json")
    n = 2
    while os.path.exists(cand):
        cand = os.path.join(arch_dir, f"{base}-{season}-{n}.json")
        n += 1
    return cand


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# -- per-kind season detection ------------------------------------------------

def _detect(kind, data, start_month):
    """Which season does this file's CONTENT belong to? None = can't tell/empty."""
    if kind == "kalman":
        if not data.get("teams") and not data.get("players"):
            return None  # already empty, nothing to roll
        meta_season = (data.get("meta") or {}).get("season")
        if meta_season:
            return meta_season
        return _season_of_date(data.get("lastDriftDate"), start_month)

    if kind == "store":
        dates = [r.get("date") for r in (data.get("runs") or []) if r.get("date")]
        return _season_of_date(max(dates), start_month) if dates else None

    if kind == "picks":
        dates = [p.get("date") for p in (data.get("props") or []) if p.get("date")]
        return _season_of_date(max(dates), start_month) if dates else None

    return None


# -- per-kind reset -----------------------------------------------------------

def _reset(kind, data, season, keep):
    if kind == "kalman":
        # Empty containers; the caller's initialize_* rebuilds from fresh stats.
        out = {k: v for k, v in data.items() if k in keep}
        if "players" in data:
            out.update({"players": {}, "processedGames": {}, "lastDriftDate": None})
        else:
            out.update({"teams": {}, "processedGames": {}, "lastDriftDate": None})
        out["meta"] = {"season": season, "created": None, "rolledFrom": True}
        return out

    if kind == "store":
        # Keep the tuned weights (they are model parameters, not season state —
        # and a playoff-era value must be restored to its pre-playoff level by
        # hand, not silently reset to defaults). Drop the season-derived fields.
        out = {k: v for k, v in data.items() if k in keep}
        out["runs"] = []
        out.pop("lastTuneDate", None)
        out.pop("residualVar", None)
        return out

    if kind == "picks":
        out = dict(data)
        out["props"] = []
        out["todayProjections"] = []
        out["tomorrowPicks"] = []
        out["tomorrowProjections"] = []
        out["tomorrowDate"] = None
        out["totalPicks"] = 0
        out["totalProjections"] = 0
        out["season"] = season
        out["summary"] = f"0 picks — new season {season}"
        return out

    return data


DEFAULT_KEEP = {
    "kalman": (),
    "store": ("weights", "weightsVar"),
    "picks": (),
}


def roll_season(date_key, targets, start_month=10, verbose=True):
    """Archive + reset any target whose content belongs to an earlier season.

    targets: [{"path": str, "kind": "kalman"|"store"|"picks", "keep": tuple?}]
    Returns the list of paths that were rolled.
    """
    season = season_label(date_key, start_month)
    rolled = []

    for t in targets:
        path, kind = os.path.normpath(t["path"]), t["kind"]
        keep = t.get("keep", DEFAULT_KEEP.get(kind, ()))
        if not os.path.exists(path):
            continue
        data = _load(path)
        if not isinstance(data, dict):
            continue

        found = _detect(kind, data, start_month)
        if not found or found >= season:
            continue  # same season, or nothing to roll — the normal case

        dest = _archive_target(path, found)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(path, dest)
        _write(path, _reset(kind, data, season, keep))
        rolled.append(path)
        if verbose:
            print(f"  [season] {os.path.basename(path)}: {found} -> {season}. "
                  f"Archived to {os.path.relpath(dest, os.path.dirname(path))}, reset for the new season.")

    if rolled and verbose:
        print(f"  [season] New season {season} — {len(rolled)} file(s) rolled over.")
    return rolled

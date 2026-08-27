# pyNFL/scripts/prob_calib.py
# Empirical probability calibration for spread / total cover probabilities.
#
# Why: the parametric t-dist pCover assumes the model's projected edge is
# real. Walk-forward testing (2023-2025, 705 games) showed the edge carries
# no out-of-sample information vs the market (OOS R^2 < 0 on the market
# residual), so parametric probabilities badly overstate confidence
# (claimed 0.627 mean pCover on picks vs 0.526 actual win rate).
#
# The honest probability comes from history:
#   resid  = actual_margin + line          (how the game landed vs the market)
#   edge   = model_margin  + line          (what the model claimed pre-game)
# Fit resid ~ alpha + beta * edge by OLS over graded history, keep the OLS
# residuals as an empirical distribution D (which carries the key-number
# mass at +-3/+-7 and the home base rate), then:
#   P(home covers | edge) = P(alpha + beta*edge + D > 0)
# With beta ~ 0 (no signal), this collapses toward the base rate — which is
# the honest answer. If the model ever develops real signal, beta grows and
# the probabilities follow.
#
# Totals use the same machinery with resid = actual_total - market_total
# and edge = proj_total - market_total.
#
# Calibration is walk-forward safe: build_prob_calibration() only sees the
# runs that exist at projection time (backfill passes the store as of that
# week; live passes the full store, which only contains past weeks).

import math

MIN_GRADED_GAMES = 150   # below this, callers fall back to parametric probs


def _ols(xs, ys):
    """
    OLS fit y = alpha + beta*x with shrinkage: beta is clamped to [0, 1]
    unless statistically significant (|beta| > 2*SE). A noisy negative
    beta would assert the model's picks are ANTI-signal, pushing pick-side
    probabilities below 0.50 — "no signal" (beta=0) is the honest prior
    until the data proves otherwise. Returns (alpha, beta, residuals).
    """
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx < 1e-9:
        beta = 0.0
    else:
        beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        # Significance check against the residual scatter
        resid0 = [y - (my + beta * (x - mx)) for x, y in zip(xs, ys)]
        sigma2 = sum(d * d for d in resid0) / max(n - 2, 1)
        se = math.sqrt(sigma2 / sxx) if sxx > 0 else float("inf")
        if not (abs(beta) > 2.0 * se):
            beta = min(1.0, max(0.0, beta))
    alpha = my - beta * mx
    resid = [y - (alpha + beta * x) for x, y in zip(xs, ys)]
    return alpha, beta, resid


def build_prob_calibration(runs, min_games=MIN_GRADED_GAMES):
    """
    Build spread/total probability calibration from graded run history.

    Parameters
    ----------
    runs : list[dict]
        Store runs (each with a "games" list). Burn-in runs are skipped.
    min_games : int
        Minimum graded games required per market; below it that market's
        entry is None and callers keep the parametric probability.

    Returns
    -------
    dict
        {"spread": {"alpha","beta","resid":[...], "n"} | None,
         "total":  {...} | None}
    """
    s_edges, s_resids = [], []
    t_edges, t_resids = [], []

    for r in runs or []:
        if r.get("burnIn"):
            continue
        for g in r.get("games", []):
            hs, as_ = g.get("homeScore"), g.get("awayScore")
            line, total = g.get("line"), g.get("total")
            proj_spread = g.get("projSpread")   # market convention: neg = home fav
            proj_total = g.get("pT")
            if not isinstance(hs, (int, float)) or not isinstance(as_, (int, float)):
                continue
            if isinstance(line, (int, float)) and isinstance(proj_spread, (int, float)):
                # model_margin = -projSpread; edge = model_margin + line
                s_edges.append(line - proj_spread)
                s_resids.append((hs - as_) + line)
            if (isinstance(total, (int, float)) and total > 0
                    and isinstance(proj_total, (int, float))):
                t_edges.append(proj_total - total)
                t_resids.append((hs + as_) - total)

    calib = {"spread": None, "total": None}
    if len(s_resids) >= min_games:
        a, b, resid = _ols(s_edges, s_resids)
        calib["spread"] = {"alpha": a, "beta": b,
                           "resid": sorted(resid), "n": len(resid)}
    if len(t_resids) >= min_games:
        a, b, resid = _ols(t_edges, t_resids)
        calib["total"] = {"alpha": a, "beta": b,
                          "resid": sorted(resid), "n": len(resid)}
    return calib


def calibrated_prob(entry, edge):
    """
    P(cover side is right | model edge), from the empirical residual
    distribution: P(alpha + beta*edge + D > 0), exact zeros counted half.

    Returns None if entry is None (caller keeps its parametric value).
    """
    if not entry:
        return None
    shift = entry["alpha"] + entry["beta"] * edge
    resid = entry["resid"]
    n = len(resid)
    if n == 0:
        return None
    above = sum(1 for d in resid if shift + d > 1e-9)
    ties = sum(1 for d in resid if abs(shift + d) <= 1e-9)
    p = (above + 0.5 * ties) / n
    # Clamp away from 0/1: finite-sample ECDF shouldn't claim certainty
    return min(0.98, max(0.02, p))

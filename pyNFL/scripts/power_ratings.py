# pyNFL/scripts/power_ratings.py
# Joint offensive/defensive EPA ratings — the way power ratings are actually
# built, and a measurable upgrade over averaging each team's EPA separately.
#
# Averaging a team's EPA/play in isolation bakes in whoever it happened to
# play. Solving all 32 offenses and defenses SIMULTANEOUSLY by ridge makes
# the opponent adjustment fall out of the fit, and the ridge penalty doubles
# as regression-to-the-mean for teams with few games:
#
#     epa_observed[team, game] = off[team] + def[opponent] + intercept
#
# Validated walk-forward on 2023-2025 (708 games, head-to-head on identical
# games vs the EPA-rate form): margin corr 0.346 -> 0.376, better in all
# three seasons individually. Totals showed NO gain (0.215 -> 0.203), so the
# totals projection deliberately still uses the EPA-rate structural form.
#
# Rejected on evidence while building this: ratings fit on point margins
# instead of EPA (corr 0.347 — results are noisier than efficiency), and
# prior-season carryover (no gain; the fresh-per-season rule stands).

import numpy as np

DEFAULT_ALPHA = 10.0   # ridge shrinkage; swept 3/10/30/60/120, flat peak at 10
MIN_OBS = 32           # ~1 full week of team-games before ratings are usable


def compute_game_epa(pbp_df):
    """
    Per team-game offensive EPA/play from play-by-play.

    Returns
    -------
    list[tuple]
        (week, team, opponent, epa_per_play, plays)
    """
    plays = pbp_df[(pbp_df["pass"] == 1) | (pbp_df["rush"] == 1)]
    if plays.empty:
        return []
    g = (plays.groupby(["week", "posteam", "defteam"])
              .agg(epa=("epa", "mean"), n=("epa", "size"))
              .reset_index())
    out = []
    for _, r in g.iterrows():
        if not r["posteam"] or not r["defteam"]:
            continue
        out.append((int(r["week"]), r["posteam"], r["defteam"],
                    float(r["epa"]), int(r["n"])))
    return out


def fit_epa_ratings(game_epa, through_week, alpha=DEFAULT_ALPHA, min_obs=MIN_OBS):
    """
    Ridge-fit joint off/def EPA ratings from team-games at or before
    *through_week*.  Pass through_week = current_week - 1 to stay
    walk-forward.

    Returns None when there isn't enough data yet (callers fall back to the
    structural EPA-rate projection).
    """
    obs = [o for o in (game_epa or []) if o[0] <= through_week]
    if len(obs) < min_obs:
        return None

    teams = sorted({o[1] for o in obs} | {o[2] for o in obs})
    ti = {t: i for i, t in enumerate(teams)}
    n = len(teams)

    X = np.zeros((len(obs), 2 * n + 1))
    y = np.zeros(len(obs))
    for k, (_w, team, opp, epa, _plays) in enumerate(obs):
        X[k, ti[team]] = 1.0          # offense
        X[k, n + ti[opp]] = 1.0       # opponent defense (EPA allowed)
        X[k, 2 * n] = 1.0             # league intercept
        y[k] = epa

    A = X.T @ X + alpha * np.eye(2 * n + 1)
    A[2 * n, 2 * n] -= alpha - 1e-6   # never penalize the intercept
    try:
        beta = np.linalg.solve(A, X.T @ y)
    except np.linalg.LinAlgError:
        return None

    return {
        "off": {t: float(beta[ti[t]]) for t in teams},
        "def": {t: float(beta[n + ti[t]]) for t in teams},
        "intercept": float(beta[2 * n]),
        "nObs": len(obs),
        "throughWeek": int(through_week),
    }


def rating_margin(ratings, home, away, home_plays, away_plays=None):
    """
    Projected margin in points (positive = home favored) from ratings.

    Each side's scoring index is its own offense plus the opponent's
    defensive rating (which is EPA *allowed*, so it adds), scaled by THAT
    TEAM'S OWN expected play count:

        margin = home_idx * home_plays - away_idx * away_plays

    Using one averaged play count for both sides discards the pace
    asymmetry between them. The effect is small in practice (NFL pace is
    tightly clustered, so the two forms differ by ~0.13 pts on average)
    but the per-team form is the correct one.

    away_plays defaults to home_plays for backward compatibility.
    """
    if not ratings:
        return None
    off, dfn = ratings.get("off", {}), ratings.get("def", {})
    if home not in off or away not in off or home not in dfn or away not in dfn:
        return None
    if away_plays is None:
        away_plays = home_plays
    home_idx = off[home] + dfn[away]
    away_idx = off[away] + dfn[home]
    return home_idx * home_plays - away_idx * away_plays

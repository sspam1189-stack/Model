# NBA Full Ratings (pyFullRatings)

Preview / replay only for now: **not in the daily run and not on the
dashboard**. Started 2026-11-15.

Same pipeline and engine as `pyFullKalmanOff` (Kalman offsets not applied,
weights frozen), with one difference: the team table the projection reads is
built by `core/ratings_model.py` instead of raw season averages.

1. **Opponent-adjusted ratings.** One regression over every game played so far
   solves each team's offence, defence and pace together, given the opponent
   and who was at home. Offence and defence count the same.
2. **Tonight's lineup.** Players are valued from their box-score production per
   minute. The roster minus players the injury report lists out or doubtful is
   compared with the lineups the team has fielded, and the gap moves the team's
   rating.
3. **Last season as the starting point.** Each team starts from last season's
   final rating (pulled 25% toward average) plus the change in roster value,
   counted as 12 games.

Back-to-backs are handled by the shared pipeline as in the other models. The
old injury adjustment and the home/away split blend are off here.

Inputs: the per-game player logs the props pipeline saves
(`data/player_cache/nba/basic_<season>_Regular_Season.json`, this season and
last) and the day's injury report. Only games before the run date are used.

Replay it through the preview workflow with `only = pyFullRatings`, or:

    python pyFull/scripts/preview_today.py --date 20261023 --to 20261113 --only pyFullRatings

## What was checked before building (walk-forward, 2026-27 through Nov 13, 167 games)

| | Average margin miss |
|---|---|
| Closing line | 10.90 |
| This model | 11.34 |
| KalmanOff | 12.84 |

Plays under the 58% / 65% rule in that test: 29-27 (-6.7u); 2u plays 5-10. No
betting edge has been shown. The model sits about 2.6 points from the line on
average, so the 58% / 65% thresholds are not calibrated for it.

One known gap for live use: the player logs are refreshed by the props run,
which follows the full-season run, so a morning run can be missing the previous
night's games.

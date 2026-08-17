"""
slate_wrc_form_sweep.py — Parameter sweep for the slate scouting report, with holdout.

Sweeps the levers in the matchup-mismatch score and the play-selection rule,
scoring every combination on a training period and then re-scoring the winner
on a held-out period it never saw. The holdout is the point: on a few hundred
picks, sweeping a handful of parameters will always produce a configuration
that looks profitable in-sample.

Levers
------
  * form window feeding the score: last-5 starts, last-3, or season
  * ERA coefficient and baseline in ``mismatch``
  * K-BB% coefficient
  * selection rule: top-N by combined mismatch, or an absolute threshold
  * whether to require clean data flags

Reads the feature table produced by ``--dump`` (which walks the season once,
rebuilding point-in-time wRC+ per slate), so sweeping is fast and the
expensive reconstruction happens only once.

Usage
-----
    python MLBstrikeouts/scripts/slate_wrc_form_sweep.py --dump features.json
    python MLBstrikeouts/scripts/slate_wrc_form_sweep.py --features features.json \
        --train-end 2026-07-10

Staking is flat 1u (see MLBstrikeouts/CLAUDE.md).
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import slate_wrc_form as S
import slate_wrc_form_backfill as B

FLAT_STAKE = 1.0
LG_K_BB = S.LG_K_PCT - S.LG_BB_PCT


def dump_features(path, start="2026-05-01"):
    """Walk every completed slate once and emit the inputs a sweep needs."""
    allml = S._load(S.ALL_ML)
    pa_rows = S._load(B.PA_SPLITS)
    logs = S._load(S.GAME_LOGS)
    starts_by_name = S.organize_starts(logs)
    apps_by_name = S.organize_appearances(logs)

    by_date, seen = defaultdict(list), set()
    for g in allml.get("games", []):
        if g.get("home_score") is None or g.get("away_score") is None:
            continue
        key = (g.get("date"), g.get("away"), g.get("home"), g.get("commence"))
        if key in seen:
            continue
        seen.add(key)
        by_date[g["date"]].append(g)

    out = []
    for d in sorted(x for x in by_date if x >= start):
        wrc = B.wrc_as_of(pa_rows, d)
        for game in by_date[d]:
            if game.get("total_line") is None:
                continue
            row = {
                "date": d,
                "matchup": f"{game['away']} @ {game['home']}",
                "total": game["total_line"],
                "runs": (game.get("home_score") or 0) + (game.get("away_score") or 0),
                "over_ml": game.get("over_ml"), "under_ml": game.get("under_ml"),
                "away_ml": game.get("away_ml"), "home_ml": game.get("home_ml"),
                "home_win": game.get("home_win"),
                "sides": {},
            }
            ok = True
            for side, offense in (("away", game["home"]), ("home", game["away"])):
                name = S.resolve_pitcher(game.get(f"{side}_pitcher"),
                                        starts_by_name, game[side])
                form = (S.form_for(starts_by_name.get(name, []),
                                   apps_by_name.get(name, []), d)
                        if name else {})
                cell = S.wrc_cell(wrc, "season", offense,
                                  game.get(f"{side}_hand"), role="all")
                if not form or not cell or not form.get("season"):
                    ok = False
                    break
                row["sides"][side] = {
                    "wrc": cell["wrcplus"],
                    "flags": S.role_flags(form, d),
                    **{w: {"era": (form[w] or {}).get("era"),
                           "kbb": (form[w] or {}).get("k_bb_pct")}
                       for w in ("season", "recent", "hot") if form.get(w)},
                }
            if ok and len(row["sides"]) == 2:
                out.append(row)

    with open(path, "w") as fh:
        json.dump(out, fh)
    print(f"wrote {len(out)} games to {path}  "
          f"({out[0]['date']} .. {out[-1]['date']})")


def score(side, window, era_c, era_base, kbb_c):
    """Recompute one starter's mismatch under a candidate parameter set."""
    w = side.get(window) or side.get("season")
    if not w or w.get("era") is None or w.get("kbb") is None:
        return None
    return ((side["wrc"] - 100)
            + (w["era"] - era_base) * era_c
            - (w["kbb"] - LG_K_BB) * kbb_c)


def american_profit(odds, stake=FLAT_STAKE):
    return stake * (odds / 100.0 if odds > 0 else 100.0 / abs(odds))


def evaluate(rows, window, era_c, era_base, kbb_c, rule, param, clean_only):
    """Run one configuration over a set of games; return (units, n, wins, losses)."""
    by_date = defaultdict(list)
    for r in rows:
        a = score(r["sides"]["away"], window, era_c, era_base, kbb_c)
        h = score(r["sides"]["home"], window, era_c, era_base, kbb_c)
        if a is None or h is None:
            continue
        if clean_only and (r["sides"]["away"]["flags"] or r["sides"]["home"]["flags"]):
            continue
        by_date[r["date"]].append((r, (a + h) / 2.0))

    units = n = wins = losses = 0
    for _d, games in by_date.items():
        games.sort(key=lambda x: -x[1])
        if rule == "top":
            picks = ([(g, "over") for g in games[:param]]
                     + [(g, "under") for g in games[-param:]]) if len(games) > param else []
        else:  # absolute-threshold rule
            picks = ([(g, "over") for g in games if g[1] >= param]
                     + [(g, "under") for g in games if g[1] <= -param])
        for (r, _c), sidebet in picks:
            runs, line = r["runs"], r["total"]
            if runs == line:
                continue  # push
            won = (runs > line) == (sidebet == "over")
            odds = r["over_ml"] if sidebet == "over" else r["under_ml"]
            if odds is None:
                continue
            n += 1
            if won:
                wins += 1
                units += american_profit(odds)
            else:
                losses += 1
                units -= FLAT_STAKE
    return units, n, wins, losses


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dump", help="write the feature table to this path and exit")
    ap.add_argument("--features", help="feature table to sweep")
    ap.add_argument("--train-end", default="2026-07-10",
                    help="last date in the training period; everything after is holdout")
    ap.add_argument("--min-picks", type=int, default=60,
                    help="ignore configs that make fewer picks than this in training")
    ap.add_argument("--show", type=int, default=12, help="how many top configs to print")
    args = ap.parse_args()

    if args.dump:
        dump_features(args.dump)
        return
    if not args.features:
        ap.error("pass --dump or --features")

    rows = S._load(args.features)
    train = [r for r in rows if r["date"] <= args.train_end]
    test = [r for r in rows if r["date"] > args.train_end]
    print(f"train {len(train)} games (.. {args.train_end})   "
          f"holdout {len(test)} games ({test[0]['date']} ..)\n")

    grid = list(itertools.product(
        ("recent", "hot", "season"),          # form window
        (4.0, 6.0, 8.0, 10.0, 12.0),          # ERA coefficient
        (3.80, 4.20, 4.60),                   # ERA baseline
        (0.0, 0.6, 1.2, 2.0),                 # K-BB% coefficient
        (("top", 1), ("top", 2), ("top", 3),
         ("thresh", 15), ("thresh", 25), ("thresh", 35)),
        (False, True),                        # clean-only
    ))

    results = []
    for window, ec, eb, kc, (rule, param), clean in grid:
        u, n, w, l = evaluate(train, window, ec, eb, kc, rule, param, clean)
        if n < args.min_picks:
            continue
        results.append({
            "cfg": (window, ec, eb, kc, rule, param, clean),
            "train_units": u, "train_n": n, "train_roi": u / n * 100,
            "train_wr": w / (w + l) * 100 if (w + l) else 0,
        })
    results.sort(key=lambda r: -r["train_roi"])
    print(f"swept {len(grid)} configs, {len(results)} met the "
          f"{args.min_picks}-pick minimum\n")

    print(f"{'window':<8}{'eraC':>5}{'base':>6}{'kbbC':>6}{'rule':>10}{'clean':>7}"
          f"{'trROI':>8}{'trN':>6}{'  ||':>5}{'teROI':>8}{'teN':>6}{'teWR':>7}")
    for r in results[:args.show]:
        window, ec, eb, kc, rule, param, clean = r["cfg"]
        tu, tn, tw, tl = evaluate(test, window, ec, eb, kc, rule, param, clean)
        troi = (tu / tn * 100) if tn else 0
        twr = (tw / (tw + tl) * 100) if (tw + tl) else 0
        print(f"{window:<8}{ec:>5.0f}{eb:>6.2f}{kc:>6.1f}"
              f"{rule + str(param):>10}{str(clean):>7}"
              f"{r['train_roi']:>+8.1f}{r['train_n']:>6}{'  ||':>5}"
              f"{troi:>+8.1f}{tn:>6}{twr:>7.1f}")

    # Does in-sample rank predict out-of-sample performance at all?
    pairs = []
    for r in results:
        window, ec, eb, kc, rule, param, clean = r["cfg"]
        tu, tn, _w, _l = evaluate(test, window, ec, eb, kc, rule, param, clean)
        if tn >= 30:
            pairs.append((r["train_roi"], tu / tn * 100))
    if len(pairs) > 2:
        import statistics as st
        a = [p[0] for p in pairs]
        b = [p[1] for p in pairs]
        ma, mb = st.mean(a), st.mean(b)
        num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
        print(f"\ncorr(train ROI, holdout ROI) across {len(pairs)} configs = "
              f"{(num / den if den else 0):+.3f}")
        print("  (near zero => in-sample tuning carries no information out of sample)")
        print(f"  mean holdout ROI across all configs: {mb:+.1f}%")


if __name__ == "__main__":
    main()

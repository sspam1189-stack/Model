# MLBstrikeouts/scripts/fade_f5_attribution.py
#
# Measures the one number the fade-F5 decision turns on: what share of the
# fade edge is produced while the FADED STARTER is on the mound, versus after
# he leaves.
#
# analyze_fade_f5.py projects F5 performance across a *range* of assumed
# attribution shares and finds the crossover (~65%) above which F5 beats the
# full game. This script estimates the actual share from data on disk, so the
# projection can be evaluated at a measured value instead of a guess.
#
# Inputs (no network):
#   data/pitcher_cache/mlb/game_logs_2026.json  : every start's line (outs, er)
#   data/odds_cache/mlb_ml/schedule_*.json      : final scores
#   data/odds_cache/mlb_ml/mlb_ml_*.json        : closing both-side ML + total
#   MLBstrikeouts/data/mlb-fade-ml.json         : the fade bets
#
# Method — decompose each game's run differential against the closing line.
# From the bet side's perspective, with o_f / o_b the faded and opposing
# starter's outs and ER_f / ER_b their earned runs:
#
#   starter window   S_sp = (ER_f - lam_bet*o_f/27) - (ER_b - lam_opp*o_b/27)
#   rest of game     S_bp = (our_rest - lam_bet*(27-o_f)/27)
#                           - (their_rest - lam_opp*(27-o_b)/27)
#   identity         S_sp + S_bp == realized margin - market expected margin
#
# The identity is asserted per game, so the split cannot silently drift. The
# attribution estimate is mean(S_sp) / mean(S_sp + S_bp).
#
# Caveats, stated because they bound the answer rather than invalidate it:
#   - Earned runs, not runs. Unearned runs (~8% league-wide) are allocated to
#     the starter by his share of the staff's outs; --unearned none instead
#     leaves them in the rest-of-game bucket, which biases attribution DOWN and
#     bounds the estimate from below. Inherited runners that score after the
#     starter exits are charged to him, biasing it UP. The effects partly offset.
#   - A starter's span is not exactly five innings, so this measures "while the
#     faded starter pitched", which is the causal window the fade list is about
#     but not literally the F5 betting window.
#   - Expected runs are spread evenly per out; innings 1-5 actually carry a bit
#     more than their share, so the starter window's expectation is slightly
#     understated (biasing attribution UP by a small amount).
#
# Usage: python -m scripts.fade_f5_attribution [--json out.json]

import argparse
import json
import os
import random
from collections import defaultdict

from scripts.analyze_fade_f5 import (
    F5_HOLD, F5_RUN_SHARE, attach_lambdas, calibrate_delta, crossover_attribution,
    devig_two_way, load_games, project_f5, pct, solve_lambdas, starter_workload,
)

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GAME_LOGS = os.path.join(REPO, "data", "pitcher_cache", "mlb", "game_logs_2026.json")
ML_CACHE = os.path.join(REPO, "data", "odds_cache", "mlb_ml")

OUTS_PER_GAME = 27.0


def load_starts():
    """Starting-pitcher lines plus whole-staff totals, per game and team.

    Returns (starts, staff) where starts[pk][team] is the starter's row and
    staff[(pk, team)] is {"er", "outs"} summed over every pitcher that team
    used. The staff totals let unearned runs be allocated by outs pitched
    instead of falling entirely into the post-starter bucket.
    """
    by_game = defaultdict(dict)
    staff = defaultdict(lambda: {"er": 0.0, "outs": 0})
    for r in json.load(open(GAME_LOGS)):
        pk, team = r.get("game_id"), r.get("team")
        if not pk or not team:
            continue
        staff[(pk, team)]["er"] += r.get("er", 0)
        staff[(pk, team)]["outs"] += r.get("outs", 0)
        if r.get("is_start"):
            by_game[pk][team] = r
    return by_game, staff


def load_finals():
    """gamePk -> {home, away, home_score, away_score} for completed games."""
    out = {}
    for fn in os.listdir(ML_CACHE):
        if not fn.startswith("schedule_"):
            continue
        for g in json.load(open(os.path.join(ML_CACHE, fn))):
            if g.get("gamePk") and g.get("home_score") is not None:
                out[g["gamePk"]] = g
    return out


def _charged(er, outs, staff, runs_allowed, unearned_mode):
    """Runs to charge a starter: his earned runs plus his share of unearned.

    Unearned runs (~8% league-wide) are invisible in `er`, so leaving them out
    pushes them into the post-starter bucket and biases attribution down. They
    are allocated by share of the staff's outs.
    """
    if unearned_mode == "none" or not staff or staff["outs"] <= 0:
        return er
    unearned = max(0.0, runs_allowed - staff["er"])
    return er + unearned * (outs / staff["outs"])


def decompose(rows, starts, staff, finals, unearned_mode="proportional"):
    """Attach the starter-window / rest-of-game edge split to each bet."""
    kept, skipped = [], defaultdict(int)
    for g in rows:
        pk = g.get("gamePk")
        sd, fin = starts.get(pk), finals.get(pk)
        if not sd or not fin:
            skipped["no_game_data"] += 1
            continue
        sp_fade, sp_bet = sd.get(g["opp"]), sd.get(g["bet"])
        if not sp_fade or not sp_bet:
            skipped["starter_unmatched"] += 1
            continue

        bet_score = fin["home_score"] if g["bet_is_home"] else fin["away_score"]
        fade_score = fin["away_score"] if g["bet_is_home"] else fin["home_score"]
        o_f, o_b = sp_fade["outs"], sp_bet["outs"]
        # Runs our offense put on the faded starter, and vice versa. The fade
        # team's staff conceded `bet_score`, so that is the runs-allowed total
        # against which its unearned runs are measured.
        er_f = _charged(sp_fade["er"], o_f, staff.get((pk, g["opp"])),
                        bet_score, unearned_mode)
        er_b = _charged(sp_bet["er"], o_b, staff.get((pk, g["bet"])),
                        fade_score, unearned_mode)

        # Market's expected runs, spread evenly across each side's 27 outs.
        e_ours_sp = g["lam_bet"] * o_f / OUTS_PER_GAME
        e_theirs_sp = g["lam_opp"] * o_b / OUTS_PER_GAME
        s_sp = (er_f - e_ours_sp) - (er_b - e_theirs_sp)

        our_rest, their_rest = bet_score - er_f, fade_score - er_b
        e_ours_rest = g["lam_bet"] * (OUTS_PER_GAME - o_f) / OUTS_PER_GAME
        e_theirs_rest = g["lam_opp"] * (OUTS_PER_GAME - o_b) / OUTS_PER_GAME
        s_bp = (our_rest - e_ours_rest) - (their_rest - e_theirs_rest)

        # The two buckets must reconstruct the total miss exactly.
        total = (bet_score - fade_score) - (g["lam_bet"] - g["lam_opp"])
        assert abs((s_sp + s_bp) - total) < 1e-9, f"split broke on {pk}"

        g.update({
            "outs_fade_sp": o_f, "outs_bet_sp": o_b,
            "er_fade_sp": er_f, "er_bet_sp": er_b,
            "bet_score": bet_score, "fade_score": fade_score,
            "s_sp": s_sp, "s_bp": s_bp, "s_total": total,
            # Starter-duel proxy for the F5 result: did our offense out-score
            # theirs against the opposing starter? Scored on raw earned runs so
            # that a genuine tie stays an exact integer tie (a push); the
            # unearned allocation above produces fractions, which would erase
            # nearly every push.
            "duel": 1 if sp_fade["er"] > sp_bet["er"] else (
                0 if sp_fade["er"] == sp_bet["er"] else -1),
        })
        kept.append(g)
    return kept, dict(skipped)


def attribution(rows):
    tot = sum(g["s_total"] for g in rows)
    return (sum(g["s_sp"] for g in rows) / tot) if tot else None


def bootstrap_attribution(rows, n=4000, seed=0):
    """Percentile CI for the attribution share (resampling games)."""
    rnd = random.Random(seed)
    k, draws = len(rows), []
    for _ in range(n):
        pick = [rows[rnd.randrange(k)] for _ in range(k)]
        a = attribution(pick)
        if a is not None:
            draws.append(a)
    draws.sort()
    return draws[int(0.05 * len(draws))], draws[int(0.95 * len(draws))]


def report(label, rows, note):
    n = len(rows)
    print("\n" + "=" * 78)
    print(f"SAMPLE: {label}  (n={n})")
    print(f"  {note}")
    print("=" * 78)

    m_sp = sum(g["s_sp"] for g in rows) / n
    m_bp = sum(g["s_bp"] for g in rows) / n
    m_tot = m_sp + m_bp
    print(f"\n  Run differential vs the closing line, per game")
    print(f"    total edge over market      {m_tot:+.3f} runs")
    print(f"    while faded starter pitched {m_sp:+.3f} runs")
    print(f"    after he left               {m_bp:+.3f} runs")

    att = attribution(rows)
    lo, hi = bootstrap_attribution(rows)
    print(f"\n  Attribution to the starter window")
    print(f"    measured                    {pct(att)}")
    print(f"    90% bootstrap CI            {pct(lo)} .. {pct(hi)}")

    # Starter-duel proxy for the F5 result.
    w = sum(1 for g in rows if g["duel"] > 0)
    p = sum(1 for g in rows if g["duel"] == 0)
    l = sum(1 for g in rows if g["duel"] < 0)
    clean = [g for g in rows if g["outs_fade_sp"] >= 15 and g["outs_bet_sp"] >= 15]
    print(f"\n  Starter-duel proxy (our runs vs their SP, theirs vs ours)")
    print(f"    all games        {w}-{l}-{p}  "
          f"win ex-push {pct(w / (w + l)) if w + l else 'n/a'}")
    if clean:
        cw = sum(1 for g in clean if g["duel"] > 0)
        cp = sum(1 for g in clean if g["duel"] == 0)
        cl = sum(1 for g in clean if g["duel"] < 0)
        print(f"    both SP 5+ IP    {cw}-{cl}-{cp}  "
              f"win ex-push {pct(cw / (cw + cl)) if cw + cl else 'n/a'}  "
              f"(n={len(clean)})")

    fg_roi = sum(g["profit"] for g in rows) / sum(g["stake"] for g in rows)
    wr = sum(1 for g in rows if g["result"] == "WIN") / n
    delta, _ = calibrate_delta(rows, wr)
    cross = crossover_attribution(rows, delta, fg_roi, F5_RUN_SHARE, F5_HOLD)

    print(f"\n  Verdict")
    print(f"    full-game ROI               {pct(fg_roi)}")
    print(f"    crossover attribution       {pct(cross)}")
    print(f"    measured attribution        {pct(att)}")
    at = {}
    for tag, a in (("measured", att), ("CI low", lo), ("CI high", hi)):
        a_cl = min(1.0, max(0.0, a))
        r = project_f5(rows, delta, a_cl, F5_RUN_SHARE, F5_HOLD)
        at[tag] = r
        print(f"    F5 ROI at {tag:<9}         {pct(r['roi'])}  "
              f"({pct(r['roi'] - fg_roi)} vs full game, "
              f"win ex-push {pct(r['f5_win_pct_ex_push'])})")
    call = "F5" if att > cross else "FULL GAME"
    print(f"\n    -> {call} is the better bet at the measured attribution")
    return {
        "label": label, "n": n, "edge_total": m_tot, "edge_starter": m_sp,
        "edge_rest": m_bp, "attribution": att, "ci90": [lo, hi],
        "crossover": cross, "fg_roi": fg_roi, "delta_runs": delta,
        "duel": {"w": w, "l": l, "push": p},
        "f5_roi": {k: v["roi"] for k, v in at.items()},
        "verdict": call,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="write results to this path")
    ap.add_argument("--unearned", choices=("proportional", "none"),
                    default="proportional",
                    help="allocate unearned runs by outs (default) or ignore them")
    args = ap.parse_args()

    fade, rows = load_games()
    attach_lambdas(rows)
    # load_games() drops gamePk; re-attach it by (date, home, away).
    keyed = {(b["date"], b["home"], b["away"]): b.get("gamePk")
             for b in fade["bets"]}
    for g in rows:
        g["gamePk"] = keyed.get((g["date"], g["bet"] if g["bet_is_home"] else g["opp"],
                                 g["opp"] if g["bet_is_home"] else g["bet"]))

    starts, staff = load_starts()
    rows, skipped = decompose(rows, starts, staff, load_finals(), args.unearned)
    rows.sort(key=lambda g: g["date"])

    print("=" * 78)
    print("FADE F5 — MEASURED ATTRIBUTION OF THE EDGE")
    print("=" * 78)
    print(f"  games decomposed: {len(rows)}" +
          (f"   skipped: {skipped}" if skipped else ""))
    print(f"  unearned runs:    {args.unearned}")

    wl = starter_workload(rows)
    if wl.get("matched"):
        print(f"  faded starter: mean {wl['mean_ip']:.2f} IP, "
              f"covers {wl['mean_f5_outs_covered']:.1f} of 15 F5 outs")

    live = [g for g in rows if g["source"] == "fanduel_api"]
    out = [
        report("LIVE / prospective picks only", live,
               "the only sample free of retroactive fade-list selection"),
        report("FULL season incl. backfill", rows,
               "inflated by lookahead; shown for direction, not for calibration"),
    ]

    if args.json:
        with open(args.json, "w") as f:
            json.dump({"samples": out, "skipped": skipped}, f, indent=2)
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()

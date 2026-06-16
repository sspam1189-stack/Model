#!/usr/bin/env python3
"""Shadow-track the OLD 0.64 pick threshold after the 2026-06-16 move to 0.68.

On 2026-06-16 the live bet/pick threshold moved 0.64 -> 0.68
(MARKET_THRESHOLDS["strikeouts"]["high"], see defaults.py). Picks in the
0.64-0.68 band are no longer bet — the engine demotes them to conf="watch"
(would_be_pick + odds + result), so they still grade but stake 0u live.

Per the user call on 2026-06-16: keep tracking performance "as if we didn't
move the threshold" so we can tell, week by week, whether cutting the
0.64-0.68 band keeps clearing the >0.20 u/pick threshold-change bar. Before
2026-06-16 the band lived in conf="high" (it WAS bet), so the two tracks are
identical pre-move and only diverge from the move date onward.

Three views per window:
  LIVE 0.68    — what we actually bet now: conf=="high" (pick OVER/UNDER).
  SHADOW 0.64  — as-if-not-moved: LIVE + watch entries with pCover in
                 [0.64, 0.68) (would_be_pick). This is the old 0.64 track.
  BAND .64-.68 — the marginal slice the move removed. Its u/pick is the
                 number that decides whether 0.68 stays correct: if the band
                 runs >= +0.20 u/pick over a real sample, the cut is costing
                 us; if it's <= 0 / noise, the cut is right.

Read-only. Units follow the house convention (risk-to-win-1u at negative
odds, risk-1u at positive odds; ROI = profit / risked), matching
compare_daily.py.

Usage:
  python track_threshold_shadow.py [--since YYYY-MM-DD] [--until YYYY-MM-DD]
                                   [--json PATH]
Defaults: since=2026-06-16 (the move date), live mlb-props.json.
"""
import sys, os, json, argparse, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_JSON = os.path.normpath(os.path.join(HERE, "..", "data", "mlb-props.json"))
MOVE_DATE = "2026-06-16"
OLD_THRESH = 0.64
NEW_THRESH = 0.68


def _units(odds, result):
    """Profit in units. risk-to-win-1u for negative odds, risk-1u for positive."""
    if odds is None or result not in ("WIN", "LOSS"):
        return 0.0
    if odds > 0:
        return (odds / 100.0) if result == "WIN" else -1.0
    return 1.0 if result == "WIN" else -(abs(odds) / 100.0)


def _risked(odds):
    """Amount risked in units, for ROI denominator."""
    if odds is None:
        return 0.0
    return 1.0 if odds > 0 else abs(odds) / 100.0


def summarize(entries, label):
    graded = [p for p in entries if p.get("result") in ("WIN", "LOSS")]
    w = sum(1 for p in graded if p["result"] == "WIN")
    l = len(graded) - w
    units = sum(_units(p.get("odds"), p.get("result")) for p in graded)
    risked = sum(_risked(p.get("odds")) for p in graded if p.get("result") in ("WIN", "LOSS"))
    n = len(graded)
    pending = len(entries) - n
    wr = (w / n * 100) if n else 0.0
    roi = (units / risked * 100) if risked else 0.0
    upp = (units / n) if n else 0.0
    return {
        "label": label, "n": n, "w": w, "l": l, "pending": pending,
        "wr": wr, "units": units, "roi": roi, "upp": upp,
    }


def fmt(s):
    return (f"  {s['label']:<14} {s['n']:>4} picks  {s['w']:>3}-{s['l']:<3} "
            f"{s['wr']:5.1f}% WR  {s['units']:+7.2f}u  {s['roi']:+6.1f}% ROI  "
            f"{s['upp']:+.3f} u/pick"
            + (f"  ({s['pending']} pending)" if s['pending'] else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=MOVE_DATE)
    ap.add_argument("--until", default=None)
    ap.add_argument("--json", default=DEFAULT_JSON)
    args = ap.parse_args()

    with open(args.json, encoding="utf-8") as f:
        props = json.load(f)["props"]

    def in_range(p):
        d = p.get("date")
        if not d:
            return False
        if d < args.since:
            return False
        if args.until and d > args.until:
            return False
        return True

    rows = [p for p in props if in_range(p)]

    live = [p for p in rows if p.get("conf") == "high" and p.get("pick") in ("OVER", "UNDER")]
    band = [p for p in rows if p.get("conf") == "watch"
            and p.get("would_be_pick") in ("OVER", "UNDER")
            and OLD_THRESH <= (p.get("pCover") or 0) < NEW_THRESH]
    shadow = live + band

    print(f"\nMLB K threshold shadow track  ({args.since} -> {args.until or 'today'})")
    print(f"  live bet cutoff {NEW_THRESH:.2f} | shadow (as-if-not-moved) cutoff {OLD_THRESH:.2f}\n")
    s_live = summarize(live, f"LIVE {NEW_THRESH:.2f}")
    s_shadow = summarize(shadow, f"SHADOW {OLD_THRESH:.2f}")
    s_band = summarize(band, "BAND .64-.68")
    print(fmt(s_live))
    print(fmt(s_shadow))
    print(fmt(s_band))

    print()
    if s_band["n"] == 0:
        print(f"  Verdict: 0 graded picks in the 0.64-0.68 band yet "
              f"({s_band['pending']} pending). Nothing to judge - keep accumulating.")
    else:
        bar = 0.20
        upp = s_band["upp"]
        if upp >= bar:
            verdict = (f"band is +{upp:.3f} u/pick >= +{bar:.2f} bar over n={s_band['n']} - "
                       f"cutting it is COSTING units; revisit the 0.68 move.")
        elif upp <= 0:
            verdict = (f"band is {upp:+.3f} u/pick (<= 0) over n={s_band['n']} - "
                       f"the cut is +EV; 0.68 looks correct.")
        else:
            verdict = (f"band is +{upp:.3f} u/pick, under the +{bar:.2f} bar over n={s_band['n']} - "
                       f"marginal; 0.68 holds unless the band sample grows and clears the bar.")
        print(f"  Verdict: {verdict}")
        if s_band["n"] < 50:
            print(f"  (n={s_band['n']} is still thin - don't re-tune on <50-pick samples.)")
        else:
            print(f"  (n={s_band['n']} is full-season; the 0.68 move was a RECENT-form call - "
                  f"use --since 2026-06-16 for the window that actually decides it.)")
    print()


if __name__ == "__main__":
    main()

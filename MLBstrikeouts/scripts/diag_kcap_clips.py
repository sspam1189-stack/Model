#!/usr/bin/env python3
"""
diag_kcap_clips.py — Show every pick where the K-rate cap clips expected_k_rate.

Patches props_engine to log clip events, runs backfill, then reports which
pitchers/matchups are being capped and by how much.

Usage:
    cd MLBstrikeouts
    python -m scripts.diag_kcap_clips
"""

import sys, os, io, re, contextlib, importlib

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ENGINE_PATH = os.path.join(os.path.dirname(__file__), "props_engine.py")

# Global list to collect clip events
clip_log = []


def main():
    with open(ENGINE_PATH, "r") as f:
        orig_engine = f.read()

    # Inject logging after the cap line — use defaults module as shared state
    old_block = (
        "        _k_cap_used = max(K_RATE_CAP_FLOOR, overall_k_pct)\n"
        "        expected_k_rate = max(K_RATE_FLOOR, min(_k_cap_used, expected_k_rate))"
    )
    new_block = (
        "        _k_cap_used = max(K_RATE_CAP_FLOOR, overall_k_pct)\n"
        "        _uncapped_kr = expected_k_rate\n"
        "        expected_k_rate = max(K_RATE_FLOOR, min(_k_cap_used, expected_k_rate))\n"
        "        if _uncapped_kr > _k_cap_used:\n"
        "            import defaults as _dmod\n"
        "            if not hasattr(_dmod, 'clip_log'): _dmod.clip_log = []\n"
        "            _dmod.clip_log.append({\n"
        '                "name": name, "opp": latest_opp,\n'
        '                "overall_k_pct": overall_k_pct,\n'
        '                "pitcher_k_rate": pitcher_k_rate,\n'
        '                "lineup_k_rate": lineup_k_rate,\n'
        '                "uncapped": _uncapped_kr,\n'
        '                "cap_used": _k_cap_used,\n'
        '                "floor": K_RATE_CAP_FLOOR,\n'
        '                "clipped_by": _uncapped_kr - _k_cap_used,\n'
        "            })"
    )

    patched = orig_engine.replace(old_block, new_block)
    if patched == orig_engine:
        print("ERROR: Could not find cap block to patch")
        return

    try:
        with open(ENGINE_PATH, "w") as f:
            f.write(patched)

        import defaults
        import props_engine
        import props_backfill
        importlib.reload(defaults)
        importlib.reload(props_engine)
        importlib.reload(props_backfill)

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            results = props_backfill.backfill()

        all_picks = results["strikeouts"]["picks"]
        actionable = [p for p in all_picks if p.get("pick") in ("OVER", "UNDER")]

        clip_log = getattr(defaults, 'clip_log', [])
        print(f"\n  Total clip events during backfill: {len(clip_log)}")
        print(f"  Total actionable picks: {len(actionable)}")

        # Summarize by pitcher
        from collections import defaultdict
        by_pitcher = defaultdict(list)
        for c in clip_log:
            by_pitcher[c["name"]].append(c)

        print(f"\n  Pitchers clipped (sorted by avg clip magnitude):\n")
        print(f"  {'Pitcher':<22} {'#clips':>6}  {'avgClip':>7}  {'maxClip':>7}  "
              f"{'seasonK%':>8}  {'avgPitcherK':>11}  {'avgLineupK':>10}  "
              f"{'avgUncapped':>11}  {'cap':>5}  {'sample matchup'}")
        print(f"  {'-' * 140}")

        sorted_pitchers = sorted(by_pitcher.items(),
                                  key=lambda x: sum(c["clipped_by"] for c in x[1]) / len(x[1]),
                                  reverse=True)

        for name, clips in sorted_pitchers:
            n = len(clips)
            avg_clip = sum(c["clipped_by"] for c in clips) / n
            max_clip = max(c["clipped_by"] for c in clips)
            avg_ok = sum(c["overall_k_pct"] for c in clips) / n
            avg_pk = sum(c["pitcher_k_rate"] for c in clips) / n
            avg_lk = sum(c["lineup_k_rate"] for c in clips) / n
            avg_uc = sum(c["uncapped"] for c in clips) / n
            avg_cap = sum(c["cap_used"] for c in clips) / n
            worst = max(clips, key=lambda c: c["clipped_by"])
            print(f"  {name:<22} {n:>6}  {avg_clip:>6.3f}  {max_clip:>6.3f}  "
                  f"{avg_ok:>7.1%}  {avg_pk:>10.1%}  {avg_lk:>9.1%}  "
                  f"{avg_uc:>10.1%}  {avg_cap:>5.1%}  "
                  f"vs {worst['opp']} (uncap={worst['uncapped']:.1%} → {worst['cap_used']:.1%})")

        # Show which of these were actionable picks that the cap may have changed
        print(f"\n  --- Clips on ACTIONABLE picks only ---\n")
        # Build a set of (name, opp) from actionable picks for matching
        actionable_keys = set()
        for p in actionable:
            actionable_keys.add(p.get("pitcher_name", p.get("name", "")))

        clipped_actionable = [c for c in clip_log if c["name"] in actionable_keys]
        print(f"  Clip events on actionable pitchers: {len(clipped_actionable)}")

    finally:
        with open(ENGINE_PATH, "w") as f:
            f.write(orig_engine)
        print(f"\n  Restored original props_engine.py")


if __name__ == "__main__":
    main()

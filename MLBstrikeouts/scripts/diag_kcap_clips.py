#!/usr/bin/env python3
"""
diag_kcap_clips.py — Show every pick where the K-rate cap clips expected_k_rate,
with full context: line, projection, actual, and what-if uncapped projection.

Usage:
    cd MLBstrikeouts
    python -m scripts.diag_kcap_clips
"""

import sys, os, io, re, contextlib, importlib

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ENGINE_PATH = os.path.join(os.path.dirname(__file__), "props_engine.py")


def main():
    with open(ENGINE_PATH, "r") as f:
        orig_engine = f.read()

    # Inject logging to capture clip events with enough info to reconstruct
    # uncapped projections. We store uncapped/capped k-rate ratio so we can
    # compute what the projection would have been.
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
        "            if not hasattr(_dmod, '_clip_log'): _dmod._clip_log = []\n"
        "            _dmod._clip_log.append({\n"
        '                "pid": pid, "name": name, "opp": latest_opp,\n'
        '                "overall_k_pct": overall_k_pct,\n'
        '                "pitcher_k_rate": pitcher_k_rate,\n'
        '                "lineup_k_rate": lineup_k_rate,\n'
        '                "uncapped_kr": _uncapped_kr,\n'
        '                "capped_kr": expected_k_rate,\n'
        '                "cap_used": _k_cap_used,\n'
        '                "floor": K_RATE_CAP_FLOOR,\n'
        '                "ratio": _uncapped_kr / expected_k_rate if expected_k_rate > 0 else 1.0,\n'
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
        clip_log = getattr(defaults, '_clip_log', [])

        # Index clips by (name, opp) for matching to picks
        from collections import defaultdict
        clips_by_key = {}
        for c in clip_log:
            clips_by_key[(c["name"], c["opp"])] = c

        # Match picks to clips
        matched = []
        for p in all_picks:
            name = p.get("player", "")
            opp = p.get("opp", "")
            key = (name, opp)
            if key in clips_by_key:
                clip = clips_by_key[key]
                uncapped_proj = p["proj"] * clip["ratio"]
                matched.append({
                    "name": clip["name"],
                    "opp": clip["opp"],
                    "pick": p.get("pick", "?"),
                    "line": p.get("line", 0),
                    "proj": p["proj"],
                    "uncapped_proj": uncapped_proj,
                    "actual": p.get("actual", "?"),
                    "won": p.get("won", None),
                    "overall_k_pct": clip["overall_k_pct"],
                    "pitcher_k_rate": clip["pitcher_k_rate"],
                    "lineup_k_rate": clip["lineup_k_rate"],
                    "uncapped_kr": clip["uncapped_kr"],
                    "capped_kr": clip["capped_kr"],
                    "odds": p.get("odds", ""),
                })

        print(f"\n  Clip events: {len(clip_log)} | Matched to picks: {len(matched)}")
        print()
        print(f"  {'Pitcher':<20} {'vs':<5} {'pick':<6} {'line':>5} {'proj':>5} "
              f"{'uncapProj':>9} {'actual':>6} {'W/L':>4}  "
              f"{'seasonK%':>8} {'pitcherK':>8} {'lineupK':>8} "
              f"{'uncapKR':>8} {'capKR':>6}")
        print(f"  {'-' * 130}")

        for m in sorted(matched, key=lambda x: x["uncapped_proj"] - x["proj"], reverse=True):
            won_str = "W" if m["won"] else "L" if m["won"] is not None else "?"
            actual_str = f"{m['actual']}" if m["actual"] != "?" else "?"
            print(f"  {m['name']:<20} {m['opp']:<5} {m['pick']:<6} "
                  f"{m['line']:>5.1f} {m['proj']:>5.1f} "
                  f"{m['uncapped_proj']:>9.1f} {actual_str:>6} {won_str:>4}  "
                  f"{m['overall_k_pct']:>7.1%} {m['pitcher_k_rate']:>7.1%} "
                  f"{m['lineup_k_rate']:>7.1%} {m['uncapped_kr']:>7.1%} "
                  f"{m['capped_kr']:>5.1%}")

        # Summary: how many would have changed pick direction if uncapped?
        print(f"\n  --- Would the pick change if uncapped? ---\n")
        for m in matched:
            capped_dir = "OVER" if m["proj"] > m["line"] else "UNDER"
            uncapped_dir = "OVER" if m["uncapped_proj"] > m["line"] else "UNDER"
            if capped_dir != uncapped_dir or m["pick"] != capped_dir:
                print(f"  {m['name']:<20} vs {m['opp']:<5}: "
                      f"capped={m['proj']:.1f} ({capped_dir}) → "
                      f"uncapped={m['uncapped_proj']:.1f} ({uncapped_dir}) | "
                      f"line={m['line']:.1f} actual={m['actual']} {'W' if m['won'] else 'L'}")

    finally:
        with open(ENGINE_PATH, "w") as f:
            f.write(orig_engine)
        print(f"\n  Restored original props_engine.py")


if __name__ == "__main__":
    main()

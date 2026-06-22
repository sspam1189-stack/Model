#!/usr/bin/env python3
"""
diag_lineup_kcap_clips.py — Count and grade every projection where LINEUP_K_CAP
clipped the opponent lineup K-rate. Compares the capped (0.25) vs uncapped (0.0)
backfill head-to-head for the SAME clipped pitcher-games: pick / projection /
W-L, and flags any game where the clip flipped the pick decision.

Joins clips to graded picks on (player, date) — unique per start. The slate date
is exposed to the clip site via a one-line backfill patch.

Usage:
    cd MLBstrikeouts
    python -m scripts.diag_lineup_kcap_clips
"""

import sys, os, io, contextlib, importlib

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

ENGINE_PATH = os.path.join(os.path.dirname(__file__), "props_engine.py")
BACKFILL_PATH = os.path.join(os.path.dirname(__file__), "props_backfill.py")

CAP = float(sys.argv[1]) if len(sys.argv) > 1 else 0.30

ENG_OLD = (
    "        from defaults import LINEUP_K_CAP\n"
    "        if LINEUP_K_CAP > 0:\n"
    "            lineup_k_rate = min(lineup_k_rate, LINEUP_K_CAP)"
)
ENG_NEW = (
    "        from defaults import LINEUP_K_CAP\n"
    "        if LINEUP_K_CAP > 0:\n"
    "            if lineup_k_rate > LINEUP_K_CAP:\n"
    "                import defaults as _dmod\n"
    "                if not hasattr(_dmod, '_lk_clip_log'): _dmod._lk_clip_log = []\n"
    "                _dmod._lk_clip_log.append({\n"
    '                    "name": name, "opp": latest_opp,\n'
    '                    "date": getattr(_dmod, "_cur_slate", ""),\n'
    '                    "uncapped_lineup_k": lineup_k_rate, "cap": LINEUP_K_CAP,\n'
    "                })\n"
    "            lineup_k_rate = min(lineup_k_rate, LINEUP_K_CAP)"
)

BF_OLD = "        projections = project_pitcher_props(\n"
BF_NEW = ("        import defaults as _dmod; _dmod._cur_slate = game_date\n"
          "        projections = project_pitcher_props(\n")


def _key(p):
    return (p.get("player", ""), p.get("date", ""))


def _run(cap):
    import defaults, props_engine, props_backfill
    importlib.reload(defaults)
    importlib.reload(props_engine)
    importlib.reload(props_backfill)
    defaults.LINEUP_K_CAP = cap
    defaults._lk_clip_log = []
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = props_backfill.backfill()
    picks = res["strikeouts"]["picks"]
    clips = list(getattr(defaults, "_lk_clip_log", []) or [])
    return picks, clips


def _units(act):
    u = 0.0
    for p in act:
        o = p.get("odds")
        if o is None:
            continue
        if p.get("won"):
            u += (o / 100.0) * 2.5 if o > 0 else 2.5
        else:
            u += -2.5 if o > 0 else (-abs(o) / 100.0) * 2.5
    return u


def main():
    eng = open(ENGINE_PATH).read()
    bf = open(BACKFILL_PATH).read()
    eng_p = eng.replace(ENG_OLD, ENG_NEW)
    bf_p = bf.replace(BF_OLD, BF_NEW)
    if eng_p == eng or bf_p == bf:
        print("ERROR: could not patch engine/backfill"); return

    try:
        open(ENGINE_PATH, "w").write(eng_p)
        open(BACKFILL_PATH, "w").write(bf_p)

        on_picks, clips = _run(CAP)
        off_picks, _ = _run(0.0)

        on_by = {_key(p): p for p in on_picks}
        off_by = {_key(p): p for p in off_picks}

        # Unique clipped (pitcher, date)
        clipped = {(c["name"], c["date"]): c for c in clips}

        rows = []
        for k, c in clipped.items():
            on = on_by.get(k)
            if on:
                rows.append((c, on, off_by.get(k)))

        is_act = lambda p: p.get("pick") in ("OVER", "UNDER")

        print(f"\n  LINEUP_K_CAP = {CAP}")
        print(f"  Clip events: {len(clips)}  |  unique pitcher-games: {len(clipped)}"
              f"  |  matched to graded picks: {len(rows)}\n")

        clipped_acts = [on for (_c, on, _o) in rows if is_act(on)]
        w = sum(1 for p in clipped_acts if p.get("won"))
        l = len(clipped_acts) - w
        print(f"  Of clipped games, {len(clipped_acts)} became actionable picks (capped):")
        print(f"    record {w}-{l}  ({(w/len(clipped_acts)*100 if clipped_acts else 0):.1f}%)"
              f"  units={_units(clipped_acts):+.1f}u (2.5u flat)\n")

        print(f"  {'Pitcher':<20} {'vs':<4} {'date':<10} {'line':>5} "
              f"{'uncapLK':>7} | {'OFF':>5} {'OFFp':>5} | {'ON':>5} {'ONp':>5} "
              f"{'act':>4} {'res':>4}")
        print("  " + "-" * 96)
        changed = []
        for c, on, off in sorted(rows, key=lambda r: -r[0]["uncapped_lineup_k"]):
            if not is_act(on) and not (off and is_act(off)):
                continue  # show rows that are/were actionable in either config
            res = "W" if on.get("won") else ("L" if on.get("won") is False else "-")
            offp = off.get("pick", "-") if off else "-"
            offv = off.get("proj") if off else None
            if off and on.get("pick") != off.get("pick"):
                changed.append((c, on, off))
            print(f"  {c['name']:<20} {c['opp']:<4} {c['date']:<10} "
                  f"{(on.get('line') or 0):>5.1f} {c['uncapped_lineup_k']:>6.1%} | "
                  f"{offp:>5} {('%.1f'%offv) if offv is not None else '-':>5} | "
                  f"{on.get('pick','-'):>5} {('%.1f'%on.get('proj')) if on.get('proj') is not None else '-':>5} "
                  f"{str(on.get('actual','?')):>4} {res:>4}")

        print(f"\n  --- Games where the clip CHANGED the pick decision ---")
        if not changed:
            print("  (none — clipping moved projections but crossed no pick threshold)")
        for c, on, off in changed:
            res = "W" if on.get("won") else ("L" if on.get("won") is False else "-")
            print(f"  {c['name']:<20} vs {c['opp']:<4} {c['date']}  line={on.get('line')}: "
                  f"OFF {off.get('pick')}({off.get('proj')}) -> ON {on.get('pick')}({on.get('proj')})"
                  f"  actual={on.get('actual')} [{res}]")

    finally:
        open(ENGINE_PATH, "w").write(eng)
        open(BACKFILL_PATH, "w").write(bf)
        print("\n  Restored props_engine.py and props_backfill.py")


if __name__ == "__main__":
    main()

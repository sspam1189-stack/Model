# scripts/_compare.py
import json

def main():
    # Compare the original backup vs recalculated history
    with open("data/history_pre_recalc_2026-03-15T15-00-39.json", "r") as f:
        orig = json.load(f)
    with open("data/history.json", "r") as f:
        curr = json.load(f)

    orig_runs = orig.get("runs", [])
    curr_runs = curr.get("runs", [])

    print("=== Original vs Recalculated ===")
    print(f"Runs: {len(orig_runs)} vs {len(curr_runs)}\n")

    # Compare weights
    print("Original weights:", json.dumps(orig.get("weights", {}))[:200])
    print("Current weights:", json.dumps(curr.get("weights", {}))[:200])
    print()

    # Count picks per run
    orig_w = orig_l = curr_w = curr_l = 0
    orig_picks = curr_picks = 0

    for i in range(len(orig_runs)):
        o_run = orig_runs[i] if i < len(orig_runs) else None
        c_run = curr_runs[i] if i < len(curr_runs) else None
        if not o_run or not c_run:
            continue

        o_w = o_l = c_w = c_l = o_picks_count = c_picks_count = 0

        for g in o_run.get("games", []):
            s_pick = g.get("sPick")
            s_conf = g.get("sConf")
            if s_pick and s_pick != "PASS" and s_conf in ("high", "elite"):
                o_picks_count += 1
                if g.get("sResult") == "WIN":
                    o_w += 1
                if g.get("sResult") == "LOSS":
                    o_l += 1

        for g in c_run.get("games", []):
            s_pick = g.get("sPick")
            s_conf = g.get("sConf")
            if s_pick and s_pick != "PASS" and s_conf in ("high", "elite"):
                c_picks_count += 1
                if g.get("sResult") == "WIN":
                    c_w += 1
                if g.get("sResult") == "LOSS":
                    c_l += 1

        orig_w += o_w
        orig_l += o_l
        orig_picks += o_picks_count
        curr_w += c_w
        curr_l += c_l
        curr_picks += c_picks_count

        if o_picks_count != c_picks_count or o_w != c_w:
            print(f"{o_run.get('dateDisplay')}: orig {o_w}-{o_l} ({o_picks_count} picks) -> recalc {c_w}-{c_l} ({c_picks_count} picks)  [{o_picks_count - c_picks_count} picks lost]")

    orig_total = orig_w + orig_l
    curr_total = curr_w + curr_l
    orig_pct = f"{orig_w / orig_total * 100:.1f}" if orig_total > 0 else "0.0"
    curr_pct = f"{curr_w / curr_total * 100:.1f}" if curr_total > 0 else "0.0"

    print(f"\nOriginal total: {orig_w}-{orig_l} ({orig_picks} picks) {orig_pct}%")
    print(f"Recalc total:   {curr_w}-{curr_l} ({curr_picks} picks) {curr_pct}%")
    print(f"Lost: {orig_picks - curr_picks} picks, {orig_w - curr_w} wins, {orig_l - curr_l} losses")

    # Check: are the lost picks from high-conf or from a threshold change?
    print("\n=== Checking what changed ===")
    # Compare probHigh thresholds
    for i in range(len(orig_runs)):
        if i >= len(curr_runs):
            break
        o_w_used = orig_runs[i].get("weightsUsed") or {}
        c_w_used = curr_runs[i].get("weightsUsed") or {}
        if not o_w_used or not c_w_used:
            continue
        if o_w_used.get("probHigh") != c_w_used.get("probHigh") or \
           o_w_used.get("probElite") != c_w_used.get("probElite"):
            print(f"{orig_runs[i].get('dateDisplay')}: probHigh {o_w_used.get('probHigh')}->{c_w_used.get('probHigh')}  probElite {o_w_used.get('probElite')}->{c_w_used.get('probElite')}")

if __name__ == "__main__":
    main()

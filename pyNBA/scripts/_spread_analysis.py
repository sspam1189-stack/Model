# scripts/_spread_analysis.py
import json

def main():
    with open("data/history.json", "r") as f:
        h = json.load(f)

    buckets = {"0-5": [], "5-10": [], "10-15": [], "15+": []}

    for r in h.get("runs", []):
        if r.get("burnIn"):
            continue
        for g in r.get("games", []):
            if g.get("sResult") not in ("WIN", "LOSS"):
                continue
            line = abs(g.get("line", 0) or 0)
            w_val = 1 if g.get("sResult") == "WIN" else 0
            if line <= 5:
                buckets["0-5"].append(w_val)
            elif line <= 10:
                buckets["5-10"].append(w_val)
            elif line <= 15:
                buckets["10-15"].append(w_val)
            else:
                buckets["15+"].append(w_val)

    print("\n=== Win Rate by Spread Size ===\n")
    for k, v in buckets.items():
        wins = sum(v)
        t = len(v)
        pct = f"{wins / t * 100:.1f}" if t > 0 else "0"
        print(f"  {k}: {wins}-{t - wins} ({pct}%)")

if __name__ == "__main__":
    main()

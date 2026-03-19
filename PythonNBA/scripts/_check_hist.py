# scripts/_check_hist.py
import json

def main():
    with open("data/history.json", "r") as f:
        h = json.load(f)
    runs = h.get("runs", [])
    print("Total runs:", len(runs))
    print("First:", runs[0].get("dateDisplay") if runs else None,
          "Last:", runs[-1].get("dateDisplay") if runs else None)
    print("Burn-in runs:", sum(1 for r in runs if r.get("burnIn")))
    print("Live runs:", sum(1 for r in runs if not r.get("burnIn")))

    # Count graded picks
    w = 0
    l = 0
    for r in runs:
        if r.get("burnIn"):
            continue
        for g in r.get("games", []):
            if g.get("sResult") == "WIN":
                w += 1
            elif g.get("sResult") == "LOSS":
                l += 1
    total = w + l
    pct = f"{w / total * 100:.1f}" if total > 0 else "0.0"
    print(f"Spread record: {w}-{l} ({pct}%)")
    print("Stored weights:", json.dumps(h.get("weights", {}))[:200])
    print("Stored residualVar:", h.get("residualVar"))

if __name__ == "__main__":
    main()

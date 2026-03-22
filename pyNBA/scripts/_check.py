# scripts/_check.py
import json

def main():
    with open("data/history.json", "r") as f:
        h = json.load(f)
    runs = h.get("runs", [])
    print("Last 5 NBA run dates:")
    for r in runs[-5:]:
        print("  ", r.get("dateDisplay") or r.get("date"))
    print("Total runs:", len(runs))

if __name__ == "__main__":
    main()

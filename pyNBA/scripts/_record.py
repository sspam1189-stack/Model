# scripts/_record.py
import json
import re

def main():
    with open("data/history.json", "r") as f:
        h = json.load(f)

    w = l = p = 0
    fav_w = fav_l = dog_w = dog_l = 0

    for r in h.get("runs", []):
        if r.get("burnIn"):
            continue
        for g in r.get("games", []):
            s_pick = g.get("sPick")
            if not s_pick or s_pick == "PASS":
                continue
            s_result = g.get("sResult")
            if not s_result:
                continue

            m = re.search(r"([+-])(\d+(?:\.\d+)?)$", s_pick)
            is_fav = m and m.group(1) == "-"
            is_dog = m and m.group(1) == "+"

            if s_result == "WIN":
                w += 1
                if is_fav:
                    fav_w += 1
                if is_dog:
                    dog_w += 1
            elif s_result == "LOSS":
                l += 1
                if is_fav:
                    fav_l += 1
                if is_dog:
                    dog_l += 1
            elif s_result == "PUSH":
                p += 1

    payout = 100 / 110
    total_units = w * payout - l
    fav_units = fav_w * payout - fav_l
    dog_units = dog_w * payout - dog_l

    total = w + l
    fav_total = fav_w + fav_l
    dog_total = dog_w + dog_l

    pct = f"{w / total * 100:.1f}" if total > 0 else "0.0"
    fav_pct = f"{fav_w / fav_total * 100:.1f}" if fav_total > 0 else "0.0"
    dog_pct = f"{dog_w / dog_total * 100:.1f}" if dog_total > 0 else "0.0"

    print(f"Overall: {w}-{l}-{p} ({pct}%) {total_units:.1f}u")
    print(f"Favs:    {fav_w}-{fav_l} ({fav_pct}%) {fav_units:.1f}u")
    print(f"Dogs:    {dog_w}-{dog_l} ({dog_pct}%) {dog_units:.1f}u")

if __name__ == "__main__":
    main()

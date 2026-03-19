# scripts/_record2.py
import json
import re

def main():
    with open("data/history.json", "r") as f:
        h = json.load(f)

    w = l = 0
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

    # Method 1: W * (100/110) - L  (what my script did)
    print("Method 1 (W x 0.909 - L):")
    print(f"  Overall: {w * (100/110) - l:.1f}u")
    print(f"  Favs:    {fav_w * (100/110) - fav_l:.1f}u")
    print(f"  Dogs:    {dog_w * (100/110) - dog_l:.1f}u")

    # Method 2: W * 1 - L * 1.1  (win 1u, lose 1.1u at -110)
    print("\nMethod 2 (W x 1 - L x 1.1):")
    print(f"  Overall: {w - l * 1.1:.1f}u")
    print(f"  Favs:    {fav_w - fav_l * 1.1:.1f}u")
    print(f"  Dogs:    {dog_w - dog_l * 1.1:.1f}u")

    # Method 3: flat 1u per pick, W - L (no juice)
    print("\nMethod 3 (W - L, no juice):")
    print(f"  Overall: {w - l}u")
    print(f"  Favs:    {fav_w - fav_l}u")
    print(f"  Dogs:    {dog_w - dog_l}u")

    # Your numbers: +41.9, +18.1, +23.8
    # 131-81 -> 131 - 81*1.1 = 131 - 89.1 = 41.9  <- THIS IS IT
    print("\nYour expected: +41.9u, +18.1u, +23.8u")
    print(f"Check: 131 - 81x1.1 = {131 - 81*1.1:.1f}")
    print(f"Check: 72 - 49x1.1 = {72 - 49*1.1:.1f}")
    print(f"Check: 59 - 32x1.1 = {59 - 32*1.1:.1f}")

    total = w + l
    fav_total = fav_w + fav_l
    dog_total = dog_w + dog_l
    pct = f"{w / total * 100:.1f}" if total > 0 else "0.0"
    fav_pct = f"{fav_w / fav_total * 100:.1f}" if fav_total > 0 else "0.0"
    dog_pct = f"{dog_w / dog_total * 100:.1f}" if dog_total > 0 else "0.0"

    print(f"\nRecord: {w}-{l} ({pct}%)")
    print(f"Favs: {fav_w}-{fav_l} ({fav_pct}%)")
    print(f"Dogs: {dog_w}-{dog_l} ({dog_pct}%)")

if __name__ == "__main__":
    main()

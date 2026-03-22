# scripts/_optvar.py
import json
import math

def normal_cdf(x):
    a1 = 0.254829592
    a2 = -0.284496736
    a3 = 1.421413741
    a4 = -1.453152027
    a5 = 1.061405429
    p = 0.3275911
    sign = -1 if x < 0 else 1
    z = abs(x) / math.sqrt(2)
    t = 1.0 / (1.0 + p * z)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-z * z)
    return 0.5 * (1.0 + sign * y)

def normal_inv_cdf(p_val):
    if p_val <= 0.001:
        return -3
    if p_val >= 0.999:
        return 3
    x = 0.0
    for _ in range(50):
        err = normal_cdf(x) - p_val
        pdf = math.exp(-x * x / 2) / math.sqrt(2 * math.pi)
        x -= err / pdf
    return x

def main():
    with open("data/history.json", "r") as f:
        h = json.load(f)

    games = []
    for r in h.get("runs", []):
        if r.get("burnIn"):
            continue
        for g in r.get("games", []):
            s_pick = g.get("sPick")
            if not s_pick or s_pick == "PASS":
                continue
            s_result = g.get("sResult")
            if s_result not in ("WIN", "LOSS"):
                continue
            p_cover = g.get("pCover")
            if not isinstance(p_cover, (int, float)) or p_cover <= 0.5 or p_cover >= 1:
                continue
            games.append({"pCover": p_cover, "win": 1 if s_result == "WIN" else 0})

    wins = sum(g["win"] for g in games)
    losses = len(games) - wins
    print(f"NBA graded picks: {len(games)}")
    print(f"Overall: {wins}-{losses} ({wins / len(games) * 100:.1f}%)")

    print("\nFactor | CalibErr | Brier  | Description")
    best_factor = 1
    best_err = 999

    factor = 1.0
    while factor <= 4.05:
        buckets = {}
        brier_sum = 0

        for g in games:
            orig_z = normal_inv_cdf(g["pCover"])
            new_p = normal_cdf(orig_z / math.sqrt(factor))
            bucket = math.floor(new_p * 20) / 20
            key = f"{bucket:.2f}"
            if key not in buckets:
                buckets[key] = {"w": 0, "l": 0}
            if g["win"]:
                buckets[key]["w"] += 1
            else:
                buckets[key]["l"] += 1
            brier_sum += (new_p - g["win"]) ** 2

        calib_err = 0
        calib_n = 0
        for k, v in buckets.items():
            n = v["w"] + v["l"]
            if n < 5:
                continue
            actual = v["w"] / n
            expected = float(k) + 0.025
            calib_err += (actual - expected) ** 2 * n
            calib_n += n

        avg_err = math.sqrt(calib_err / max(calib_n, 1))
        brier = brier_sum / len(games)
        if avg_err < best_err:
            best_err = avg_err
            best_factor = factor
        mark = " <- BEST" if factor == best_factor else ""
        print(f"{factor:<7.1f}| {avg_err:<9.4f}| {brier:.4f}{mark}")
        factor += 0.1

    print(f"\nBest factor: {best_factor:.1f} (calibErr={best_err:.4f})")

if __name__ == "__main__":
    main()

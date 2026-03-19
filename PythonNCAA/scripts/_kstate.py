#!/usr/bin/env python3
# scripts/_kstate.py — Inspect Kalman state file
import json

d = json.loads(open("data/kalman_state.json").read())
print("Top-level keys:", list(d.keys()))
for k in d:
    if not isinstance(d[k], dict) or d[k] is None:
        print(f"  {k}: {d[k]}")
    elif k in ("W", "W_var", "hyper"):
        print(f"  {k}: {json.dumps(d[k])}")
    else:
        print(f"  {k}: [object with {len(d[k])} keys]")

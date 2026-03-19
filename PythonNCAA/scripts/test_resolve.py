#!/usr/bin/env python3
# scripts/test_resolve.py — Test team name resolution
import json
import re

cache = json.loads(open("data/barttorvik_cache.json").read())
H = cache.get("stats", {})

def norm_key(s):
    s = str(s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def collapse_abbr(s):
    return s.replace(".", "")

def safe_fuzzy(a, b):
    if not a or not b: return False
    if a == b: return True
    shorter = a if len(a) < len(b) else b
    longer = b if len(a) < len(b) else a
    if len(shorter) / len(longer) < 0.85: return False
    return shorter in longer

def resolve_team(data, name):
    if not data or not name: return None
    if name in data: return name
    keys = list(data.keys())
    wanted = norm_key(name)
    wanted_collapsed = norm_key(collapse_abbr(name))
    for k in keys:
        if norm_key(k) == wanted: return k
    for k in keys:
        if norm_key(collapse_abbr(k)) == wanted_collapsed: return k
    for k in keys:
        if safe_fuzzy(norm_key(k), wanted): return k
    best_prefix, best_len = None, 0
    for k in keys:
        nk = norm_key(k)
        nkc = norm_key(collapse_abbr(k))
        match_nk = wanted.startswith(nk + " ") or wanted_collapsed.startswith(nk + " ")
        match_nkc = wanted_collapsed.startswith(nkc + " ")
        if (match_nk or match_nkc) and len(nk) >= 3:
            l = max(len(nk), len(nkc))
            if l > best_len:
                best_len = l
                best_prefix = k
    if best_prefix: return best_prefix
    return None

tests = [
    "UMBC Retrievers", "Howard Bison", "UNC Wilmington Seahawks", "Yale Bulldogs",
    "Stephen F. Austin Lumberjacks", "Tulsa Golden Hurricane", "NC State Wolfpack",
    "Texas Longhorns", "UNLV Rebels", "UC Irvine Anteaters",
    "Duke", "Kansas", "Gonzaga", "Auburn",
    "Arkansas Razorbacks", "Kansas Jayhawks", "Oregon Ducks", "Oregon St. Beavers",
    "Texas A&M Aggies", "North Texas Mean Green",
]
for t in tests:
    print(f"{t} -> {resolve_team(H, t) or 'FAIL'}")

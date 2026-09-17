#!/usr/bin/env python3
"""
rule_status.py — the single source of truth for which rules are carded.

On 2026-09-01 the same rule was carded in one place and shadowed in another:
the dashboard read JS constants (FORM_UNDER_LIVE, ALIGNED_ML_LIVE), the daily
logger read its own Python dict, and the tables carried a third `status`
field. Aligned ML ended up rendering as a CARD play on the tab while its
ledger row said SHADOW. Nothing was wrong with either surface -- there were
just three answers to one question.

So the answer lives here, once. The logger imports RULE_STATUS directly; the
dashboard reads the JSON this writes. Flipping a rule between card and shadow
is a one-line edit in this file and both surfaces follow on the next run.

  card     the rule produces real plays; entries count in the card record
  shadow   tracked, never bet; held out of the record until it earns its way in
  retired  off. The daily logger skips it entirely, so it writes no new rows;
           whatever it already settled stays in the ledger and in its season
           table, because a rule that lost money should keep saying so.

Usage:  cd MLBstrikeouts && python -m scripts.rule_status
"""
import datetime
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_PATHS = [
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "rule-status.json")),
    os.path.normpath(os.path.join(
        SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "rule-status.json")),
]

# name -> (status, display name, one-line why)
RULES = {
    "flag-plays": (
        "card", "Flag Plays",
        "Per-combo verdicts on the defect flags; swingman alone 38-23 +19.0% "
        "(p=0.030), seven combos to the under and swingman+stale-window to the "
        "over. Carded 2026-09-01."),
    "form-under": (
        "shadow", "Form under",
        "SHADOW 2026-09-16 (user), same day it came off the card and by the "
        "same reasoning as aligned-ml: keep it logging, carry no units, and "
        "let its own rows answer whether September was a break or a blip. "
        "m_sum <= -40 -> under. Carded 2026-09-01 "
        "on 84-52 +17.2% (n=136, perm p=0.005), which the full-season replay "
        "reproduces EXACTLY as its record through 8/31 -- the case was real, "
        "not a construction artifact. Then September: 3-17 -71.7% on the "
        "replay, 10-27 -18.05u live, and its edge over a blind under on the "
        "same dates ran +38 (May), +46 (Jun), +16 (Jul), -5.5 (Aug), -47.0 "
        "(Sep), landing at -3.8% across the last 100 plays. Taking the OVER "
        "on the same trigger was considered and REJECTED (user, 2026-09-16): "
        "the over side of those 156 games is 69-87 -15.1%, negative in five "
        "of six months, and its one good month is September, which was a "
        "league-wide over month -- blind over +12.5% vs blind under -21.2% "
        "over all 199 September games at 9.51 mean runs, the season high. "
        "The trigger stopped paying; the other side of it was never the "
        "answer. Settled rows stay in the ledger."),
    "better-arm-ml": (
        "card", "Better arm ML",
        "m_sum >= +40, back the lower-mismatch side, plus money only. 19-15 "
        "+26.0% (n=34); backing the favorite in the same games is -1.9% and "
        "the rule is flat outside the pool. Carded 2026-09-01."),
    "aligned-ml": (
        "shadow", "Aligned ML",
        "SHADOW 2026-09-16 (user), up from retired: a retired rule writes no "
        "rows, so the doubt about it can never resolve, and shadow costs "
        "nothing to carry. Retired 2026-09-02 (user). Hot-aligned offense vs "
        "cold-aligned at the "
        "75-PA floor. Carded 2026-09-01 on a 3-1 lifetime record (n=4) with no "
        "statistical case, on a ladder measured inert for runs. The full-season "
        "replay published the next day (scout-rules-table.json) put it at 6-7 "
        "-12.8% over 13 plays, negative in both halves (-8/-30) against a -3.3% "
        "blind baseline -- the opposite of the 3-1 that justified carding it. "
        "Settled rows stay in the ledger; it logs nothing further."),
    "form-flip-over": (
        "card", "Form flip (over)",
        "m_sum <= -40 -> OVER. Form under's own trigger, opposite side. "
        "CARDED 2026-09-17 on the user's call, asked for twice. form-under "
        "stays on shadow beside it, so the same trigger is tracked both ways "
        "and the live records settle which side is right.\n\n"
        "THE SEASON EVIDENCE IS AGAINST THIS AND IS NOT WITHDRAWN. Replayed "
        "on the same 156 games at real over prices the over side is 69-87 "
        "-15.1%, negative in five of six months (Apr -5.1, May -50.2, Jun "
        "-54.4, Jul -34.9, Aug -4.7). Its one good month is September "
        "(+66.9%) -- and September 2026 was a league-wide over month: a blind "
        "over on all 199 September games returned +12.5% against -21.2% for a "
        "blind under, at 9.51 mean runs, the season high against a 8.61-9.35 "
        "range every other month. Against that baseline the flip's edge is "
        "+50.9% in September but only +4.5% across the last 100 plays.\n\n"
        "So this is a bet that September's run environment holds. If it was "
        "environment rather than signal, it loses as soon as scoring "
        "normalises, and five negative months say it will. Read its own live "
        "record from 2026-09-17, not the backtest -- the backtest is of a bet "
        "nobody placed."),
    "mismatch-ml": (
        "card", "Mismatch ML",
        "tail m <= -45 / fade m >= +55. Carded 8/29 without a shadow period "
        "and pulled 8/30 at 1-3; revived 2026-09-01 as shadow for the 15-20 "
        "tracked plays the gate asks for, at August's +9.4% expectation. "
        "CARDED 2026-09-16 (user): the shadow period ran 40 plays, double "
        "what the gate asked, at 25-15 +3.79u +9.5% -- August's expectation "
        "hit almost exactly -- and the second half (15-5 +30.3%) carried the "
        "first (10-10 -11.3%). Shadow rows stay shadow; the card record "
        "starts here. NOTE the shadow split is lopsided: the tail half "
        "(m <= -45) is 19-8 +22.6% and the fade half (m >= +55) is 6-7 "
        "-17.8%, and plus-money sides are 5-9 -25.3% against +28.2% for "
        "minus-money. n is small either way -- this is a flag to watch, not "
        "a narrowing anyone has earned."),
    "better-arm-ml-fav": (
        "shadow", "Better arm ML (favorite half)",
        "Out of scope since the dogs-only narrowing; measured, never bet."),
}

# The non-scout systems: eight rules found 2026-09-01 by scanning the 2,066
# settled games in mlb-all-ml.json, using only what that file carries. They
# read none of the mismatch model, which is why they are grouped apart on the
# tab and in the ledger -- when one of these agrees with a scout rule it is a
# second opinion rather than the same inputs counted twice. Carded by the user
# without a shadow period. Three more are tracked on shadow -- they qualify
# and are logged, they carry no units. Full statistical case for every one,
# including those that fail their ladder, lives in scripts/allml_systems.py.
NON_SCOUT = {}
if SCRIPT_DIR not in sys.path:      # importable as `scripts.rule_status` or bare
    sys.path.insert(0, SCRIPT_DIR)
import allml_systems as _sys        # noqa: E402  (needs the path line above)

for _key in _sys.ALL_ORDER:
    _name, _market, _rule, _case = _sys.SYSTEMS[_key]
    NON_SCOUT[_key] = ("card" if _key in _sys.CARD_ORDER else "shadow",
                       _name, _rule)
RULES.update(NON_SCOUT)

# group -> which panel a rule belongs to. Everything not named here is scout.
GROUPS = {k: "non-scout" for k in NON_SCOUT}

RULE_STATUS = {k: v[0] for k, v in RULES.items()}
RULE_GROUP = {k: GROUPS.get(k, "scout") for k in RULES}


def is_card(rule):
    return RULE_STATUS.get(rule) == "card"


def main():
    blob = {
        "sport": "MLB",
        "type": "rule-status",
        "generated": datetime.datetime.now(datetime.timezone.utc)
                             .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "note": ("Single source of truth for card/shadow status. The daily "
                 "logger imports RULE_STATUS from scripts/rule_status.py; the "
                 "dashboard reads this file. Edit the script, not either "
                 "surface."),
        "rules": {k: {"status": v[0], "name": v[1], "why": v[2],
                      "group": RULE_GROUP.get(k, "scout")}
                  for k, v in RULES.items()},
    }
    for path in OUTPUT_PATHS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(blob, fh, indent=1)
    by = {}
    for k, v in RULES.items():
        by.setdefault(v[0], []).append(k)
    carded = sorted(by.get("card", []))
    print(f"rule status: {len(carded)} card ({', '.join(carded)}), "
          f"{len(by.get('shadow', []))} shadow, "
          f"{len(by.get('retired', []))} retired "
          f"({', '.join(sorted(by.get('retired', []))) or 'none'}) "
          f"-> {len(OUTPUT_PATHS)} paths")


if __name__ == "__main__":
    main()

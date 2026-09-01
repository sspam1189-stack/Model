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

  card    the rule produces real plays; entries count in the card record
  shadow  tracked, never bet; held out of the record until it earns its way in

Usage:  cd MLBstrikeouts && python -m scripts.rule_status
"""
import datetime
import json
import os

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
        "card", "Form under",
        "m_sum <= -40 -> under. 84-52 +17.2% (n=136, perm p=0.005), all bands "
        "positive, both walk-forward halves positive. Carded 2026-09-01."),
    "better-arm-ml": (
        "card", "Better arm ML",
        "m_sum >= +40, back the lower-mismatch side, plus money only. 19-15 "
        "+26.0% (n=34); backing the favorite in the same games is -1.9% and "
        "the rule is flat outside the pool. Carded 2026-09-01."),
    "aligned-ml": (
        "card", "Aligned ML",
        "Hot-aligned offense vs cold-aligned at the 75-PA floor. 3-1 lifetime "
        "(n=4) on a ladder measured inert for runs -- a structural read, not a "
        "measured edge. Carded 2026-09-01; comes off at 0-3."),
    "mismatch-ml": (
        "shadow", "Mismatch ML",
        "tail m <= -45 / fade m >= +55. Carded 8/29 without a shadow period "
        "and pulled 8/30 at 1-3; revived 2026-09-01 as shadow for the 15-20 "
        "tracked plays the gate asks for, at August's +9.4% expectation."),
    "better-arm-ml-fav": (
        "shadow", "Better arm ML (favorite half)",
        "Out of scope since the dogs-only narrowing; measured, never bet."),
}

RULE_STATUS = {k: v[0] for k, v in RULES.items()}


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
        "rules": {k: {"status": v[0], "name": v[1], "why": v[2]}
                  for k, v in RULES.items()},
    }
    for path in OUTPUT_PATHS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(blob, fh, indent=1)
    carded = [k for k, v in RULES.items() if v[0] == "card"]
    print(f"rule status: {len(carded)} card ({', '.join(sorted(carded))}), "
          f"{len(RULES) - len(carded)} shadow -> {len(OUTPUT_PATHS)} paths")


if __name__ == "__main__":
    main()

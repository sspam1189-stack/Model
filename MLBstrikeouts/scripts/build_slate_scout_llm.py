"""
MLBstrikeouts/scripts/build_slate_scout_llm.py

Claude-authored read of tonight's slate, written to ``mlb-slate-scout-llm.json``
for the "MLB Slate Scout" dashboard tab to render beneath the table.

Run by hand -- this is deliberately NOT wired into mlb-run-daily.yml. It shells
out to the Claude Code CLI (``claude -p``), which bills against the local
Max subscription rather than an API key, so there is no ANTHROPIC_API_KEY and no
per-token invoice. The cost of that choice is that it needs an interactive login
to be valid on this machine; an expired one fails with a 401 and the fix is to
re-run ``claude`` once and sign in.

Output is split into two clearly separated parts, because they carry very
different weight:

  caveats   -- which starters' rate lines cannot be read at face value, derived
               from the flags build_slate_scout already stamps (opener,
               layoff-Nd, stale-window-Nd, thin-sample, thin-<window>-Npa).
               This is the part with real information in it.
  narrative -- per-game commentary. Explicitly labelled as commentary on the
               tab, because the matchup-pressure ranking it reads returns -1.8%
               ROI over 1818 games; the closing total already prices what it
               knows. The prompt is scoped to keep this descriptive rather than
               letting it drift into picks.

Usage:
    python -m scripts.build_slate_scout_llm
    python -m scripts.build_slate_scout_llm --date 2026-08-17
    python -m scripts.build_slate_scout_llm --dry-run   # print the prompt, no call
"""

import argparse
import datetime
import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

INPUT_PATH = os.path.normpath(
    os.path.join(SCRIPT_DIR, "..", "data", "mlb-slate-scout.json"))

OUTPUT_PATHS = [
    os.path.normpath(os.path.join(SCRIPT_DIR, "..", "data", "mlb-slate-scout-llm.json")),
    os.path.normpath(os.path.join(
        SCRIPT_DIR, "..", "..", "PythonDashboard", "data", "mlb-slate-scout-llm.json")),
]

MODEL = os.environ.get("SLATE_LLM_MODEL", "claude-opus-5")
# Reading stamped flags and describing rate lines is a bounded task, not a
# reasoning-heavy one. medium keeps latency and subscription usage down; raise
# to high if the caveats come back shallow.
EFFORT = os.environ.get("SLATE_LLM_EFFORT", "medium")

# The slate JSON is passed in the prompt, so the model needs no tools at all.
# Denying them keeps it a single text turn instead of an agent loop that might
# wander off into the repo.
DISALLOWED_TOOLS = "Bash Edit Write Read Glob Grep WebFetch WebSearch Task"

SCHEMA = {
    "type": "object",
    "properties": {
        "slate_summary": {
            "type": "string",
            "description": (
                "Two or three sentences on the shape of tonight's slate: how many "
                "starters carry data flags, and where the least trustworthy lines "
                "are. Describe, do not rank."),
        },
        "caveats": {
            "type": "array",
            "description": (
                "One entry per starter whose rate lines cannot be read at face "
                "value. Omit starters whose numbers are clean."),
            "items": {
                "type": "object",
                "properties": {
                    "pitcher": {"type": "string"},
                    "team": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                        "description": (
                            "high = the displayed line describes a materially "
                            "different pitcher or period (long layoff, opener, "
                            "single-digit PA). medium = readable with care. "
                            "low = worth noting only."),
                    },
                    "flags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "The stamped flags this caveat is based on.",
                    },
                    "note": {
                        "type": "string",
                        "description": (
                            "One or two sentences: which number is misleading and "
                            "why. Cite the specific figures involved."),
                    },
                },
                "required": ["pitcher", "team", "severity", "flags", "note"],
                "additionalProperties": False,
            },
        },
        "narrative": {
            "type": "array",
            "description": "One entry per game, in the order given.",
            "items": {
                "type": "object",
                "properties": {
                    "matchup": {"type": "string"},
                    "comment": {
                        "type": "string",
                        "description": (
                            "Two to four sentences of scouting context: the "
                            "handedness matchup, how each offense has hit that "
                            "hand, where the arms' recent form sits against "
                            "season. No pick, no lean, no edge claim."),
                    },
                },
                "required": ["matchup", "comment"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["slate_summary", "caveats", "narrative"],
    "additionalProperties": False,
}

PROMPT_HEADER = """\
You are reading a scouting payload for tonight's MLB slate and producing two \
separate things for a dashboard tab.

WHAT THE DATA IS
Each game lists both starters with: the opposing offense's wRC+ against that \
starter's throwing hand (opp_wrc_vs_hand, where 100 is league average), season \
/ last-5-start (recent) / last-3-start (hot) rate lines, a trend block, a \
matchup-pressure score, and any data flags.

Flag vocabulary:
  opener            -- not a real starter; the rate line describes relief work.
  layoff-Nd         -- N days since the last start. A long layoff means the
                       last-5 line describes a different month of the season.
  stale-window-Nd   -- the last-5 window spans N days, so it is not "recent".
  thin-sample       -- too few starts for the rates to mean much.
  thin-<w>-Npa      -- the opponent wRC+ for window <w> rests on only N plate
                       appearances.

THE CRITICAL CONSTRAINT
The matchup-pressure ranking in this payload is NOT a betting signal. Backtested \
over 1818 games it returns -1.8% ROI, because the closing total already prices \
what it knows. Do not produce picks, leans, edges, "value", or any claim that one \
side is mispriced. Do not rank games by attractiveness. If you find yourself \
writing that something "looks strong" or "stands out as an opportunity", stop and \
describe the data instead.

The genuinely useful output is the caveats: saying which starters' numbers cannot \
be read at face value and why. Spend your effort there. The narrative is \
secondary context and is labelled as commentary on the tab.

Write for someone who can see the table already -- do not restate every number, \
cite only the figures that carry your point. Name pitchers as they appear in the \
payload.

SLATE PAYLOAD
"""


def build_prompt(report):
    """Prompt = fixed instructions + the slate payload, compact."""
    payload = {
        "date": report.get("date"),
        "wrc_through": report.get("wrc_through"),
        "wrc_role": report.get("wrc_role"),
        "recent_window_starts": report.get("recent_window_starts"),
        "slate": report.get("slate"),
        "ranked_pressure": report.get("ranked_pressure"),
    }
    return PROMPT_HEADER + json.dumps(payload, separators=(",", ":"))


def call_claude(prompt):
    """Shell out to the Claude Code CLI and return the parsed schema object.

    ``--json-schema`` constrains the model's answer, so the payload the tab
    renders is guaranteed to have the fields it reads. ``--output-format json``
    wraps that answer in a result envelope carrying is_error, which is how an
    auth failure surfaces (exit code is 0 even then, so check the envelope).
    """
    cmd = [
        "claude", "-p", prompt,
        "--json-schema", json.dumps(SCHEMA, separators=(",", ":")),
        "--output-format", "json",
        "--model", MODEL,
        "--effort", EFFORT,
        "--disallowed-tools", DISALLOWED_TOOLS,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(
            f"claude exited {proc.returncode}: {(proc.stderr or '').strip()[:400]}")

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"claude returned non-JSON output: {proc.stdout[:400]!r}") from exc

    result = envelope.get("result") or ""
    if envelope.get("is_error"):
        # An expired subscription login lands here, not on a non-zero exit.
        raise RuntimeError(f"claude reported an error: {result[:400]}")

    # With --json-schema the result is the schema-conforming object, but the CLI
    # may hand it back as a JSON string rather than a nested object.
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"result was not schema JSON: {result[:400]!r}") from exc
    if not isinstance(result, dict):
        raise RuntimeError(f"unexpected result type: {type(result).__name__}")
    return result, envelope


def build(date_iso, dry_run=False):
    with open(INPUT_PATH, "r", encoding="utf-8") as fh:
        report = json.load(fh)

    if date_iso and report.get("date") != date_iso:
        raise SystemExit(
            f"[slate-scout-llm] {INPUT_PATH} holds {report.get('date')}, "
            f"not {date_iso}. Run build_slate_scout first.")

    prompt = build_prompt(report)
    if dry_run:
        print(prompt)
        raise SystemExit(0)

    analysis, envelope = call_claude(prompt)

    analysis["sport"] = "MLB"
    analysis["type"] = "slate-scout-llm"
    analysis["advisory"] = True
    # Stamp the slate this read describes, so the tab can say so when the
    # analysis is older than the slate beside it (this file is written by hand,
    # so it WILL go stale between runs).
    analysis["slate_date"] = report.get("date")
    analysis["slate_generated"] = report.get("generated")
    analysis["model"] = MODEL
    analysis["effort"] = EFFORT
    analysis["generated"] = (datetime.datetime.now(datetime.timezone.utc)
                             .isoformat(timespec="seconds").replace("+00:00", "Z"))
    usage = envelope.get("usage") or {}
    analysis["usage"] = {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "duration_ms": envelope.get("duration_ms"),
    }
    return analysis


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", default=None,
                    help="Assert the slate file holds this date before calling.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the prompt and exit without calling Claude.")
    args = ap.parse_args()

    try:
        payload = build(args.date, dry_run=args.dry_run)
    except SystemExit:
        raise
    except Exception as exc:
        # Fail loud: this is a manual run, so there is someone watching, and
        # keeping a stale analysis silently is what the tab's staleness chip
        # exists to catch.
        message = str(exc)
        print(f"[slate-scout-llm] FAILED: {message}", file=sys.stderr)
        if "authenticate" in message.lower() or "401" in message:
            print("[slate-scout-llm] Run `claude` once and sign in, then retry.",
                  file=sys.stderr)
        return 1

    for path in OUTPUT_PATHS:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1)
        print(f"[slate-scout-llm] wrote {path}")

    print(f"[slate-scout-llm] slate {payload['slate_date']} - "
          f"{len(payload['caveats'])} caveats, {len(payload['narrative'])} games, "
          f"{payload['usage']['output_tokens']} output tokens")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

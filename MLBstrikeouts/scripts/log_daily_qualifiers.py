#!/usr/bin/env python3
"""
log_daily_qualifiers.py — write every rule's qualifiers into the scout ledger.

Until 2026-09-01 the scout ledger was written only by hand, in a session. That
was fine when there was one rule; with five it means a day without a session
silently leaves holes, and a shadow period that needs 15-20 tracked plays never
fills unless somebody remembers to run a script. Monday 8/31 having no mismatch
qualifiers and a skipped Monday look identical in a hand-kept ledger.

So the daily run logs them now. Every rule, card and shadow:

    Flag Plays      per-combo verdicts from flag-combo-table.json
    Form under      m_sum <= -40 -> under
    Better arm ML   m_sum >= +40, plus money only  (msum-ml-table.json)
    Aligned ML      hot-vs-cold ladder at the 75-PA floor
    Mismatch ML     tail m <= -45 / fade m >= +55   (shadow)

and, from 2026-09-01, the eight NON-SCOUT systems (scripts/allml_systems.py),
which read mlb-all-ml.json alone and none of the mismatch model:

    Hot arm dog ML    Away dog ML       Home slide ML     Pickem under
    Starter over run  Low line over     Cold arms under   Under juice

WHAT THIS DOES AND DOES NOT CLAIM. A card entry written here records that the
RULE fired at that price, not that a bet was placed -- only a person knows
that. Every auto entry carries ``"auto": true``; mark one ``"not_bet": true``
by hand (or with scout_card_log.py) when a play was missed, and the report
already holds those out of the units while keeping them in the rule's W-L.

Idempotent by (date, rule, game): the daily workflow runs six times a day and
re-running never duplicates a row or rewrites a price. The FIRST price seen is
the one kept, which matches the ledger's card-time-quote convention -- a later
run seeing a moved line does not silently regrade the entry.

Usage:  cd MLBstrikeouts && python -m scripts.log_daily_qualifiers [--dry-run]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "sources"))

import scout_card_log as LEDGER
import allml_systems as ALLSYS
from rule_status import RULE_STATUS

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.normpath(os.path.join(HERE, "..", "data"))
SCOUT = os.path.join(DATA, "mlb-slate-scout.json")
ALLML = os.path.join(DATA, "mlb-all-ml.json")
FADEML = os.path.join(DATA, "mlb-fade-ml.json")
PROPS = os.path.join(DATA, "mlb-props.json")
COMBOS = os.path.join(DATA, "flag-combo-table.json")
MSUM = os.path.join(DATA, "msum-ml-table.json")

DEFECTS = ("swingman", "layoff", "opener", "stale-window")   # canonical order
FORM_UNDER_AT = -40.0
MISMATCH_TAIL, MISMATCH_FADE = -45.0, 55.0

# Card/shadow status comes from scripts/rule_status.py -- the single source
# of truth both this logger and the dashboard read, after the two drifted
# apart on 2026-09-01 and aligned ML rendered as CARD while its ledger row
# said SHADOW.
STATUS = RULE_STATUS


def _load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _short(matchup):
    """"SD @ CIN" -> "SD/CIN", the ledger's own convention."""
    return matchup.replace(" @ ", "/")


def _num(v):
    """9.0 -> "9", 8.5 -> "8.5" -- no trailing .0 on whole numbers."""
    return f"{v:g}"


def game_ids():
    """(date, away, home, commence) -> gamePk, from the ALL-ML dataset.

    The scout ledger used to identify a game by team names alone, which is
    ambiguous on doubleheaders -- 2026-08-29 ARI @ SF was two games and an
    entry graded against the wrong one. fade-ML has stored gamePk on every
    bet since March; the scout tier now does too. `commence` is the join key
    because it is the one field both payloads carry and it separates the
    halves of a doubleheader exactly.
    """
    out = {}
    if os.path.exists(ALLML):
        for g in json.load(open(ALLML, encoding="utf-8")).get("games", []):
            if g.get("gamePk") is None:
                continue
            out[(g.get("date"), g.get("away"), g.get("home"),
                 g.get("commence"))] = g["gamePk"]
    # ALL-ML lags: it is built from settled games, so TODAY's slate is not in
    # it yet and today's entries would log without an id -- the day they most
    # need one. The fade-ML ledger carries gamePk on today's block, so take
    # ids from there too (it never disagrees; both come from the same feed).
    if os.path.exists(FADEML):
        fade = json.load(open(FADEML, encoding="utf-8"))
        for b in (fade.get("today") or []) + (fade.get("bets") or []):
            if b.get("gamePk") is None:
                continue
            out.setdefault((b.get("date"), b.get("away"), b.get("home"),
                            b.get("commence")), b["gamePk"])
    # Third source, and the only one that covers the WHOLE slate: the props
    # payload lists every probable with its game_id. Verified identical to
    # fade-ML's gamePk on all seven shared games of 2026-09-01, so it is the
    # same MLB id, not a parallel numbering. Keyed on the team pair because
    # probables carry team/opp rather than away/home.
    if os.path.exists(PROPS):
        pr = json.load(open(PROPS, encoding="utf-8"))
        pdate = pr.get("date")
        for x in (pr.get("todayProbables") or []):
            gid, t, o = x.get("game_id"), x.get("team"), x.get("opp")
            if gid is None or not t or not o:
                continue
            for a, h in ((t, o), (o, t)):
                out.setdefault((pdate, a, h, x.get("game_time")), gid)
                out.setdefault((pdate, a, h, None), gid)
    return out


def _combo(sides):
    """Canonical combo string for a game's flagged sides."""
    present = [d for d in DEFECTS
               if any(f.startswith(d) for s in sides for f in (s.get("flags") or []))]
    return "+".join(present)


def qualifiers(payload, verdicts, msum_table, ids=None):
    """Every rule's plays for this slate: (rule, side, play, price, basis)."""
    out = []
    ids = ids or {}
    dog_only = bool((msum_table or {}).get("require_dog"))
    msum_at = (msum_table or {}).get("threshold", 40.0)

    for g in payload.get("slate", []):
        sides = [(g.get("sides") or {}).get(k) or {} for k in ("away", "home")]
        flagged = [s for s in sides
                   if any(f.startswith(DEFECTS) for f in (s.get("flags") or []))]
        ms = [s.get("mismatch") for s in sides]
        msum = (ms[0] + ms[1]) if (ms[0] is not None and ms[1] is not None) else None
        total, u_ml, o_ml = g.get("total"), g.get("under_ml"), g.get("over_ml")
        gid = (ids.get((payload.get("date"), g.get("away"), g.get("home"),
                        g.get("commence")))
               or ids.get((payload.get("date"), g.get("away"), g.get("home"),
                           None)))

        # --- Flag Plays -------------------------------------------------
        if flagged and total is not None:
            combo = _combo(flagged)
            side = verdicts.get(combo, "under" if "swingman" in combo.split("+")
                                else None)
            if side in ("under", "over"):
                price = u_ml if side == "under" else o_ml
                if price is not None:
                    who = " · ".join(f"{s.get('pitcher')} "
                                     + ", ".join(f for d in DEFECTS
                                                 for f in (s.get("flags") or [])
                                                 if f.startswith(d))
                                     for s in flagged)
                    out.append({
                        "rule": "flag-plays", "combo": combo,
                "gamePk": gid, "commence": g.get("commence"),
                        "matchup": g["matchup"], "key": f"{total}",
                        "play": f"{_short(g['matchup'])} "
                                f"{'U' if side == 'under' else 'O'}{_num(total)}",
                        "market": "totals", "line": total, "price": int(price),
                        "basis": f"Verdict {side} for {combo}. {who}.",
                    })

        # --- Form under / Mismatch ML (both off the mismatch score) ------
        if msum is not None and msum <= FORM_UNDER_AT and total is not None \
                and u_ml is not None:
            out.append({
                "rule": "form-under",
                "gamePk": gid, "commence": g.get("commence"),
                "matchup": g["matchup"], "key": f"{total}",
                "play": f"{_short(g['matchup'])} U{_num(total)}",
                "market": "totals", "line": total, "price": int(u_ml),
                "flagged_overlap": bool(flagged),
                "basis": (f"m_sum {msum:+.1f} <= {FORM_UNDER_AT:+.0f}; both arms "
                          f"outclass the bats. "
                          f"{'Also flagged' if flagged else 'Unflagged'}."),
            })

        for key, s in zip(("away", "home"), sides):
            m = s.get("mismatch")
            if m is None:
                continue
            if m <= MISMATCH_TAIL:
                pick = g["away"] if key == "away" else g["home"]
                act = "tail"
            elif m >= MISMATCH_FADE:
                pick = g["home"] if key == "away" else g["away"]
                act = "fade"
            else:
                continue
            price = g.get("home_ml") if pick == g.get("home") else g.get("away_ml")
            if price is None:
                continue
            out.append({
                "rule": "mismatch-ml",
                "gamePk": gid, "commence": g.get("commence"),
                "matchup": g["matchup"], "key": pick,
                "play": f"{pick} ML (mismatch {m:+.1f})",
                "market": "h2h", "price": int(price),
                "basis": (f"{act} at L20 mismatch {m:+.1f} ({s.get('pitcher')}). "
                          f"Shadow revival; expectation +9.4%, not +17.2%."),
            })

        # --- Better arm ML ----------------------------------------------
        if msum is not None and msum >= msum_at:
            better_away = ms[0] < ms[1]
            pick = g["away"] if better_away else g["home"]
            price = g.get("away_ml") if better_away else g.get("home_ml")
            if price is not None:
                is_dog = price > 0
                if is_dog or not dog_only:
                    arm = sides[0 if better_away else 1]
                    out.append({
                        "rule": "better-arm-ml", "is_dog": is_dog,
                "gamePk": gid, "commence": g.get("commence"),
                        "matchup": g["matchup"], "key": pick,
                        "play": f"{pick} ML ({'dog' if is_dog else 'fav'}, m_sum {msum:+.1f})",
                        "market": "h2h", "price": int(price),
                        "basis": (f"Better arm {arm.get('pitcher')} "
                                  f"({ms[0 if better_away else 1]:+.1f}) vs "
                                  f"{ms[1 if better_away else 0]:+.1f}, m_sum {msum:+.1f}."),
                    })

        # --- Aligned ML --------------------------------------------------
        am = g.get("aligned_ml")
        if am and am.get("ml") is not None:
            out.append({
                "rule": "aligned-ml",
                "gamePk": gid, "commence": g.get("commence"),
                "matchup": g["matchup"], "key": am["pick"],
                "play": f"{am['pick']} ML (aligned)",
                "market": "h2h", "price": int(am["ml"]),
                "basis": (f"away {am.get('away_offense')} vs home "
                          f"{am.get('home_offense')} @{am.get('min_pa')}pa."),
            })

    for q in out:
        q["game"] = q.get("game") or None
    return out


def allml_qualifiers(date, ids=None):
    """The non-scout systems' plays for tonight, in ledger shape.

    Reads mlb-all-ml.json's `today` block rather than the scout payload, so a
    slate the scout model cannot score (no probable, no batter window) still
    logs these. gamePk comes straight off that block, so the auto-grader joins
    them exactly rather than by team names.
    """
    out = []
    try:
        plays = ALLSYS.today_plays()
    except (OSError, ValueError, KeyError):
        return out
    for p in plays:
        if date and p.get("date") and p["date"] != date:
            continue
        name = ALLSYS.SYSTEMS[p["rule"]][0]
        common = {
            "rule": p["rule"],
            "gamePk": p.get("gamePk"),
            "commence": p.get("commence"),
            "matchup": p.get("matchup"),
            "price": int(p["price"]),
            "basis": f"{name}: {p.get('why', '')}".strip(),
            "non_scout": True,
        }
        if p["market"] == "totals":
            total = p.get("total")
            if total is None:
                continue
            out.append(dict(common, market="totals", line=total,
                            key=f"{total}",
                            play=f"{_short(p['matchup'])} "
                                 f"{'U' if p['pick'] == 'under' else 'O'}"
                                 f"{_num(total)}"))
        else:
            out.append(dict(common, market="h2h", key=p["pick"],
                            play=f"{p['pick']} ML "
                                 f"({ALLSYS.short_tag(p['rule'])})"))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be logged, write nothing")
    args = ap.parse_args()

    payload = _load(SCOUT)
    date = payload.get("date")
    verdicts = (_load(COMBOS).get("verdicts") or {}) if os.path.exists(COMBOS) else {}
    msum_table = _load(MSUM) if os.path.exists(MSUM) else {}

    # Game name per play, resolved from the slate for the ledger's `game`.
    by_play = {}
    for g in payload.get("slate", []):
        by_play[g["matchup"]] = g["matchup"]

    ids = game_ids()
    qs = qualifiers(payload, verdicts, msum_table, ids)
    # The non-scout systems come off mlb-all-ml.json, not the scout payload.
    qs += allml_qualifiers(date, ids)
    blob = LEDGER._load()
    # Idempotency key: one entry per (date, rule, play). A re-run never
    # duplicates and never rewrites the price already recorded.
    def sig(e):
        """Structural identity: date + rule + game + the thing bet.

        Deliberately NOT the play text -- hand-logged rows say
        "CWS/HOU Under 8.5" where this writes "CWS @ HOU UNDER 8.5", and
        keying on text would log both."""
        game = (e.get("gamePk") or
                (e.get("game") or "").replace("/", " @ ").strip())
        if e.get("market") == "totals":
            k = str(e.get("line") or "")
        else:
            k = (e.get("play") or "").split(" ML")[0].strip()
        return (e.get("date"), e.get("rule"), e.get("market"), game, k)

    have = {sig(e) for e in blob["entries"]}

    added = []
    for q in qs:
        rule = q["rule"]
        status = STATUS.get(rule, "shadow")
        entry = {
            "date": date,
            "play": q["play"],
            "market": q["market"],
            "game": q.get("matchup"),
            "price": q["price"],
            "stake": 1.0,
            "rule": rule,
            "auto": True,
            "basis": q["basis"],
            "result": "pending",
            "profit": 0.0,
        }
        if q.get("line") is not None:
            entry["line"] = q["line"]
        for extra in ("combo", "is_dog", "flagged_overlap", "gamePk",
                      "commence", "non_scout"):
            if extra in q:
                entry[extra] = q[extra]
        if status == "shadow":
            entry["shadow"] = True
        if sig(entry) in have:
            continue
        have.add(sig(entry))
        added.append(entry)

    counts = {}
    for e in added:
        counts[e["rule"]] = counts.get(e["rule"], 0) + 1
    label = ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "nothing new"
    print(f"{date}: {len(qs)} qualifiers, {len(added)} new -> {label}")
    for e in added:
        tag = "SHADOW" if e.get("shadow") else "CARD  "
        print(f"  {tag} {e['rule']:14} {e['play'][:46]:46} {e['price']:>5}")

    if args.dry_run or not added:
        if args.dry_run:
            print("(dry run -- nothing written)")
        return
    blob["entries"].extend(added)
    blob["entries"].sort(key=lambda e: (e.get("date", ""), e.get("rule", "")))
    LEDGER._save(blob)
    print(f"wrote {len(added)} entries")


if __name__ == "__main__":
    main()

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

Idempotent by (date, rule, game, side): the daily workflow runs six times a
day and re-running never duplicates a row. It DOES re-price one. A row tracks
the market until first pitch -- the number you would actually get is the
latest quote, not whichever one the first run of the morning happened to
catch -- and locks the moment the game starts. After that the price, the
line and the play text are frozen, whatever the book does and however many
times the workflow runs, so a settled row always reads the way it was bet.
A graded row is locked too, regardless of clock.

Only market fields move (PRICE_FIELDS). Result, profit, stake, not_bet and
anything else hand-edited belong to the ledger, not the book, and are never
touched by a re-price.

Usage:  cd MLBstrikeouts && python -m scripts.log_daily_qualifiers [--dry-run]
"""
import argparse
import collections
import datetime
import json
import os
import re
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

# What a re-price is allowed to move: the market's description of the bet,
# and nothing the ledger owns. `play` carries the line in its text, so it
# moves with it or the row would read "U9" at a price quoted for 8.5.
PRICE_FIELDS = ("price", "line", "play", "basis",
                "ml_price", "under_price", "payout")

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


# " O9.5" or " Over 9.5" -- the over token in a play string, never a team
# abbreviation (those are not preceded by a space in "OAK/TEX U7.5").
_OVER_TEXT = re.compile(r"\sO(?:ver)?\s*\d")


def sig_of(e):
    """Structural identity of a ledger row: date + rule + game + thing bet.

    The idempotency key. One entry per (date, rule, market, game, side), so
    the daily workflow running six times adds each play once and never
    rewrites a price already recorded.

    The TOTAL IS NOT PART OF THE KEY (fixed 2026-09-02). It used to be, and
    a line move between two runs of the workflow therefore read as a
    different bet: on 9/2 the book moved DET/MIN from 9 to 8.5 and the
    ledger carried two pickem-under rows for one game, staking 2u on a play
    that was made once. Twelve rows across six rules that day. A rule fires
    at most once per game per market, so the game and the side are the
    identity; the total is a price, and the first price recorded is the one
    kept, exactly as it is for the moneyline.

    Deliberately NOT the play text -- hand-logged rows say "CWS/HOU Under 8.5"
    where this writes "CWS @ HOU UNDER 8.5", and keying on text would log both.
    Module level rather than nested because the season backfill
    (scripts/backfill_allml_systems.py) has to agree with it exactly; two
    copies of this would drift and duplicate the ledger.
    """
    game = (e.get("gamePk") or
            (e.get("game") or "").replace("/", " @ ").strip())
    if e.get("market") == "parlay":
        k = "parlay"
    elif e.get("market") == "totals":
        # The side, not the number: a moved total is the same bet. Prefer an
        # explicit pick; fall back to the play text, which reads either
        # "SD/CIN U9.5" or the hand-logged "SD/CIN Under 9.5".
        pick = (e.get("pick") or "").lower()
        if pick not in ("over", "under"):
            pick = "over" if _OVER_TEXT.search(e.get("play") or "") else "under"
        k = pick
    else:
        k = (e.get("play") or "").split(" ML")[0].strip()
    return (e.get("date"), e.get("rule"), e.get("market"), game, k)


def total_side(entry):
    """Which side of the total a ledger row is on, or None.

    A parlay's second leg is the under, so a parlay row conflicts with an
    over on the same game exactly as a straight under would.
    """
    mk = entry.get("market")
    if mk == "parlay":
        return "under"
    if mk != "totals":
        return None
    pick = (entry.get("pick") or "").lower()
    if pick in ("over", "under"):
        return pick
    return "over" if _OVER_TEXT.search(entry.get("play") or "") else "under"


NOTE = "Carded over and under on this game; neither side taken."


def drop_conflicting_totals(entries, date, now=None):
    """When a game carries both a carded over and a carded under, drop BOTH.

    BOTH SIDES SINCE 2026-09-03 (user). The old rule kept the under and
    passed the over. Passing the under too costs 17-12 +12.6% (+3.66u over
    29 settled plays) -- the over was already being passed, so this is a
    cost, not a saving, and the card tier's ROI only rises (+27.6% ->
    +28.0%) because that cell sits below the tier average.

    What it buys is not betting a game the board disagrees about. Over
    carded and shadow rules together, a one-over-vs-one-under game returns
    -4.1% to the under and -3.8% to the over across 150 plays: the two
    cancel to the vig. Taking the moneyline dog instead was measured and is
    worse -- -12.8% on the carded conflicts against a -3.1% blind dog, and
    -1.9% over the wider set at p=0.44, no better than the -1.5% that games
    with an UNOPPOSED total rule return. There is no side to be on here.

    Both rows are marked not_bet -- they stay in their rule's W-L, which is
    what the replay measures, and out of the units, which is what was
    actually risked. Not PUSH: these games had results.

    Only PENDING rows are SUPPRESSED, and that is enforced with _locked, not
    merely by being called with today's date (fixed 2026-09-03). The date
    filter alone was never the guarantee this docstring claimed: the daily
    workflow runs six times a day, so a late run on a day whose early games
    have already finished would have marked a settled row not_bet and taken
    a real, risked bet out of the units. Harmless while only overs were
    suppressed and they were flagged before first pitch anyway; not harmless
    once both sides and the parlay are in scope.

    Locked rows still COUNT for detection. A settled over is proof the
    conflict was real, so the pending under is still passed -- what cannot
    be done is un-betting a game that has already started.

    Returns the rows it changed.
    """
    now = now or datetime.datetime.now(datetime.timezone.utc)
    by_game = collections.defaultdict(list)
    for e in entries:
        if e.get("date") != date or e.get("shadow"):
            continue
        if total_side(e) is None:
            continue
        by_game[e.get("gamePk") or e.get("game")].append(e)

    changed = []
    for rows in by_game.values():
        sides = {total_side(e) for e in rows
                 if not e.get("not_bet") or e.get("conflict_skip")}

        if sides != {"over", "under"}:
            continue
        for e in rows:
            # Started or graded: the bet was made and stands. Detection above
            # already counted it, so its partner is still passed.
            if _locked(e, now):
                continue
            if e.get("conflict_skip") and NOTE in (e.get("basis") or ""):
                continue                       # already handled, nothing to do
            # not_bet, not PUSH (user, 2026-09-02, after seeing what PUSH did
            # to the record). A push says the number landed and there was no
            # result; these games had results, and they were 21-30. Booking
            # them as pushes deleted 30 losses against 21 wins and moved
            # starter-over-run from a true 122-89 to a flattered 101-60.
            # not_bet says what happened: the rule fired, no money on it. The
            # row keeps its real result, stays in the W-L, carries no units.
            e["not_bet"] = True
            # Its own flag, not a substring of `basis`: a re-price rewrites
            # basis from the engine and would otherwise silently drop the
            # explanation while leaving the row changed and unexplained.
            e["conflict_skip"] = True
            if NOTE not in (e.get("basis") or ""):
                e["basis"] = (e.get("basis", "") + " " + NOTE).strip()
            changed.append(e)
    return changed


def _locked(entry, now):
    """True once a row may no longer be re-priced.

    Locked at first pitch, or as soon as it has been graded. A row with no
    commence time cannot be shown to be un-started, so it locks rather than
    risk rewriting a game already in progress.
    """
    if (entry.get("result") or "pending") != "pending":
        return True
    when = entry.get("commence")
    if not when:
        return True
    try:
        t = datetime.datetime.fromisoformat(str(when).replace("Z", "+00:00"))
    except ValueError:
        return True
    if t.tzinfo is None:
        t = t.replace(tzinfo=datetime.timezone.utc)
    return now >= t


def _combo(sides):
    """Canonical combo string for a game's flagged sides."""
    present = [d for d in DEFECTS
               if any(f.startswith(d) for s in sides for f in (s.get("flags") or []))]
    return "+".join(present)


def qualifiers(payload, verdicts, msum_table, ids=None, shadow_combos=()):
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
            # A shadowed combo still qualifies and still gets logged -- it is
            # the tracking that makes shadow worth anything -- but the entry
            # is marked so it is written with no stake.
            combo_shadow = combo in shadow_combos
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
                        "shadow": combo_shadow,
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
        if p["market"] == "parlay":
            # Two legs in one row: the ledger keeps the combined American
            # price so profit_for works unchanged, plus each leg so the
            # grader can reduce to the survivor when the total pushes.
            out.append(dict(common, market="parlay", line=p.get("line"),
                            key=f"{p['pick']}|{p.get('line')}",
                            ml_price=p.get("ml_price"),
                            under_price=p.get("under_price"),
                            payout=p.get("payout"),
                            play=f"{p['pick']} ML +{p.get('ml_price')} "
                                 f"+ U{_num(p.get('line'))} "
                                 f"({p.get('payout')}x)"))
        elif p["market"] == "totals":
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
    combo_blob = _load(COMBOS) if os.path.exists(COMBOS) else {}
    verdicts = combo_blob.get("verdicts") or {}
    # Combos whose side is measured and tracked but not bet tonight.
    shadow_combos = set(combo_blob.get("verdicts_shadow") or ())
    msum_table = _load(MSUM) if os.path.exists(MSUM) else {}

    # Game name per play, resolved from the slate for the ledger's `game`.
    by_play = {}
    for g in payload.get("slate", []):
        by_play[g["matchup"]] = g["matchup"]

    ids = game_ids()
    qs = qualifiers(payload, verdicts, msum_table, ids, shadow_combos)
    # The non-scout systems come off mlb-all-ml.json, not the scout payload.
    qs += allml_qualifiers(date, ids)
    now = datetime.datetime.now(datetime.timezone.utc)
    blob = LEDGER._load()
    by_sig = {}
    for e in blob["entries"]:
        by_sig.setdefault(sig_of(e), e)

    added = []
    moved = []
    for q in qs:
        rule = q["rule"]
        status = STATUS.get(rule, "shadow")
        # A retired rule logs nothing further. Without this it would fall
        # through the `shadow` check below and be written as a CARD row.
        if status == "retired":
            continue
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
                      "commence", "non_scout", "ml_price", "under_price",
                      "payout"):
            if extra in q:
                entry[extra] = q[extra]
        # Shadow comes from either level: the rule as a whole, or this one
        # combo inside an otherwise carded rule (flag-plays/layoff).
        if status == "shadow" or q.get("shadow"):
            entry["shadow"] = True
        prior = by_sig.get(sig_of(entry))
        if prior is not None:
            # The play is already on the books. Re-price it to the market the
            # workflow is seeing now -- the number you would actually get is
            # the latest one, not whatever the first run of the morning
            # happened to catch -- and LOCK it at first pitch, so a row never
            # changes after the game it describes has started. Everything the
            # ledger owns rather than the market (result, profit, stake,
            # hand edits) is left alone.
            if _locked(prior, now):
                continue
            changed = {k: v for k, v in entry.items()
                       if k in PRICE_FIELDS and prior.get(k) != v}
            if not changed:
                continue
            prior.update(changed)
            moved.append((prior, changed))
            continue
        by_sig[sig_of(entry)] = entry
        added.append(entry)

    # Conflicts are resolved across the WHOLE day's rows, not just the ones
    # added this run: the over and the under can be logged by different runs.
    dropped = drop_conflicting_totals(blob["entries"] + added, date, now)
    for e in dropped:
        print(f"  SKIP-TOTAL {e['rule']:14} {e['play'][:40]:40} "
              f"carded over and under on this game")

    for e, changed in moved:
        bits = ", ".join(f"{k} {v}" for k, v in sorted(changed.items()))
        print(f"  REPRICE {e['rule']:14} {e['play'][:40]:40} {bits}")

    counts = {}
    for e in added:
        counts[e["rule"]] = counts.get(e["rule"], 0) + 1
    label = ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "nothing new"
    print(f"{date}: {len(qs)} qualifiers, {len(added)} new, "
          f"{len(moved)} repriced -> {label}")
    for e in added:
        tag = "SHADOW" if e.get("shadow") else "CARD  "
        print(f"  {tag} {e['rule']:14} {e['play'][:46]:46} {e['price']:>5}")

    if args.dry_run or not (added or moved or dropped):
        if args.dry_run:
            print("(dry run -- nothing written)")
        return
    blob["entries"].extend(added)
    blob["entries"].sort(key=lambda e: (e.get("date", ""), e.get("rule", "")))
    LEDGER._save(blob)
    print(f"wrote {len(added)} entries")


if __name__ == "__main__":
    main()

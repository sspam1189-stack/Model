# Draft War Room — Full Guide

Your 2026 fantasy football draft assistant. This explains what everything does, how the numbers are built, and how to actually use it on draft day — for both snake and auction leagues.

---

## Table of contents

1. [The one idea behind the whole tool](#1-the-one-idea)
2. [Getting set up](#2-getting-set-up)
3. [Where the data comes from](#3-where-the-data-comes-from)
4. [The value engine: projections → VORP → Edge](#4-the-value-engine)
5. [The survival model: "will he last?"](#5-the-survival-model)
6. [The board, column by column](#6-the-board)
7. ["Take one of these" — the recommendations](#7-take-one-of-these)
8. [The right rail](#8-the-right-rail)
9. [The tabs](#9-the-tabs)
10. [How to draft with it](#10-how-to-draft-with-it)
11. [Reading the numbers well](#11-reading-the-numbers-well)
12. [Honest limits](#12-honest-limits)
13. [Glossary](#13-glossary)
14. [Troubleshooting](#14-troubleshooting)

---

## 1. The one idea

Most draft tools hand you a ranking somebody typed in July. This one answers a live question instead: *of everyone still on the board, who should I take right now?*

The core principle is that a good pick isn't the best player available — it's the best player available **who won't come back to you.** So the tool always weighs two things together:

- **Value** — how good is he, and is he underpriced?
- **Survival** — can I wait, or will he be gone before my next pick?

A great value you can get eight rounds later is not a pick; it's a note for later. A scarce player who's about to disappear is. Everything in the tool is built around telling those two situations apart, and it re-calculates every time a pick happens.

---

## 2. Getting set up

**The file.** The tool is a single self-contained HTML file (`draft-war-room.html`). Everything — the code, the player data, the RotoBaller projections, the styling — is baked in. It has no outside dependencies, so it opens and runs on its own.

**To run it on your computer:** download the file and double-click it. It opens in your browser and works immediately.

**To run it on your iPad and connect to Sleeper:** a downloaded file won't work for live Sleeper sync on iOS (Safari blocks the calls). Instead, host the file once to get a web link:

- Easiest from a computer: drag the file onto **app.netlify.com/drop** — you get a live link in seconds. Make a free account if you want the link to survive to draft day.
- iPad-only: upload it to a file host like **sharable.link** or **tiiny.host** to get a link.

Then open that link in Safari on the iPad, tap Share → **Add to Home Screen**, and it launches full-screen like an app.

**First-time settings (top of the screen):** set your **teams**, your **draft slot**, **rounds**, and **scoring** to match your league. If your league isn't the standard 1 QB / 2 RB / 3 WR / 1 TE / 1 FLEX / 1 K / 1 DEF, open the **My team** tab and adjust the roster counts — this matters, because those counts define replacement level, which the whole value engine is built on.

**Your draft saves automatically** to whatever browser you're using, so you can refresh mid-draft and pick up where you left off. It saves *per device*, so set up and draft on the same one.

---

## 3. Where the data comes from

**ADP (average draft position)** is blended from four real sources captured in late August: Fantasy Football Calculator mock drafts (which also provide the spread/standard deviation the survival math needs), Sleeper, ESPN, and FantasyPros. The **ADP** dropdown up top lets you view any single source or the blended "Market" number. There are 221 players with bye weeks and injury/situation notes.

**Projections** come from RotoBaller and are **already baked in** — the moment you open the tool it shows real projected points, no upload needed. RotoBaller is a legitimate, model-driven source (full stat-line projections, injury discounts, correct current teams) built by a top-accuracy industry forecaster.

**Refreshing projections.** The built-in numbers are a snapshot. RotoBaller updates daily in the preseason, so close to draft day you can refresh them two ways, both in the **Sync** tab:

- **Upload a fresh CSV** — download the latest RotoBaller cheat sheet and drop it in. It auto-detects the columns and matches by name. This is the reliable, richer option.
- **Load from Sleeper** — one click pulls Sleeper's live projections instead. Convenient, but it depends on Sleeper's feed; if it comes back with a low match count, use the CSV.

Whichever you loaded last is what's active. Two players (Jayden Higgins, Antonio Williams) aren't in RotoBaller's set and fall back to ADP-implied values — they show "—" for projection.

---

## 4. The value engine

This is what turns projections into draft decisions. Three steps:

**Projected points → VORP.** Raw points don't tell you draft value, because positions run dry at different rates. So each player's value is **VORP** — Value Over Replacement Player — his projected points minus the points of the *last startable player at his position* (set by your roster settings). A running back and receiver projected for the same points aren't worth the same pick if one position is scarcer, and VORP captures that.

**VORP → Edge.** The headline number. **Edge** compares where the projections rank a player to where ADP drafts him. Positive Edge means the market is underpricing him — projections like him more than the room does. This is the one signal ADP alone can never give you: *where the crowd is wrong.* Edge is shown for skill positions only; it's suppressed (shown as "—") for kickers and defenses, where it's noise, and before you've loaded real projections.

**VORP → auction dollars.** In auctions, the same VORP is distributed across the league's total budget to produce each player's **Par** price (see the Auction section).

---

## 5. The survival model

This answers "can I wait?" For every available player it simulates every pick between now and your next turn — and the important part, the part nothing free does: it uses **each opponent's actual roster needs.** If the teams picking ahead of you are already loaded at running back, the back you want survives even though ADP says he shouldn't.

On the board this is the **Lasts to __%** column: the odds he's still there at your following pick. The faded number next to it is plain ADP survival, so when the two disagree, you're seeing exactly how much the room's shape is bending things. Low % = grab him now; high % = you can wait and spend this pick on someone scarce.

---

## 6. The board

Your main screen. Each row, left to right:

- **Player** — position rank (e.g. WR3), name, team, bye week, any risk tags (`injury`, `rookie`, `TD-reliant`), and a one-line situation note.
- **Tier** — his tier at his position (T1, T2…), and after the dot, how many players are left in that tier. Small number = the tier is about to fall off a cliff.
- **ADP** — blended average draft position.
- **Proj** — RotoBaller projected points (shows "—" if he isn't in their set).
- **Edge** — projection value vs ADP. Green = bargain, red = going too early.
- **Opp** — projected **opp**ortunity: targets for receivers/tight ends, touches for backs. This is the *volume* behind the projection — high volume means the points are real workload, not touchdown luck.
- **Lasts to __%** — survival to your next pick (bold), with plain ADP survival faded beside it.
- **MINE / ✕** — **MINE** drafts him to your team; **✕** marks that someone else took him. Both remove him from the board, assign him to the team on the clock (in snake order), and re-run every survival number.

Up top there's a filter row (ALL / QB / RB / WR / TE / FLEX / K / DST) and a search box.

---

## 7. "Take one of these"

The panel on the right rail is the model's whole brain compressed into a shortlist of your six best picks right now, each with a **+** to draft instantly. It blends four things:

- **Value** — how underpriced he is (Edge).
- **Urgency** — how likely he's gone before your next pick.
- **Scarcity** — how few comparable players remain in his tier.
- **Need** — whether he fills a starting slot you still have open.

The key rule, and the thing that makes it trustworthy: **value only counts to the degree the player might actually be gone.** A bargain who's certain to last drops down the list on purpose — you can get him later. Kickers and defenses are pushed way down so they don't clutter your early rounds.

The tags under each name are the *why* — read those, not just the order:

- **edge +8** — how much of a value he is.
- **30% lasts** — his odds of surviving to your next pick.
- **2 in tier** — how many comparable players are left.
- **board: now / board: wait** — appears when the opponent-aware read disagrees with plain ADP. "Now" means the teams ahead of you need his position, so move early; "wait" means he'll last, so spend elsewhere.

---

## 8. The right rail

- **Take one of these** — the shortlist above.
- **My roster** — your lineup as fillable slots (QB, RB, RB, WR, WR, WR, TE, FLEX, then BEN, K, DEF, driven by your roster settings). Each slot fills as you draft; empty slots read "empty" so the card doubles as your shopping list. A third running back drops into FLEX automatically, extras go to the bench, and each filled slot shows the player's bye. Bye chips turn red when three or more of your players share an off-week.
- **Tier cliffs** — for RB / WR / TE / QB, how many players remain in the current tier, so you can see a position about to drop off.
- **Your picks** — your snake pick numbers, striking through as they pass and highlighting your next one.
- **Your byes** (in the header) — an always-visible strip showing which weeks your drafted players are off, red when a week is stacked. It stays pinned at the top on every tab.

---

## 9. The tabs

**Board** — the main player list described above.

**Auction** — for salary-cap drafts. This tab has its own toolkit:

- **Par price** — each player's expected going rate (his VORP dollars, scaled by live room inflation). Treat par as your *ceiling, not a target.*
- **Budget plan** — pick a philosophy: **Balanced** (about half your budget across your top 3, deep middle) or **Stars & Scrubs** (two-thirds on 3-4 studs, then $1-2 fills). It shows your target spend and a live **pace** read — "banking cash — you can afford a stud," "on pace," or "tight — mostly $1 fills from here."
- **Budget stats** — budget left, max bid you can make (always keeps $1 back per unfilled slot), slots to fill, average $ per remaining slot, and **room inflation** (are players going over or under par).
- **Log a sale** — record what each player sold for and to which team. It tells you how many rivals can still outbid you at par ("Nobody can — he's yours cheap"), warns when you're bidding over par or past your max, and feeds everything else.
- **Who can still spend** — every team's remaining budget, open slots, max possible bid, and unmet needs. A player's real price depends on who can afford him; the flush teams are your bidding-war threats.
- **Nominate to drain / force spend** — the skill that wins auctions. Early, it suggests expensive players you don't need so rivals burn their budgets; late, it flips to cheap players to make cash-rich teams spend so you can steal $1 bench pieces.

**League** — every team's full roster and what each still needs, plus a strip of the next twelve picks and their needs. This is the input the survival model runs on, so it shows you *why* a survival number moved. You can rename teams here.

**Platform edges** — the same player often costs different picks on Sleeper vs ESPN vs Yahoo. This ranks the widest gaps so you can draft a player where he's cheapest and get him rounds later.

**My team** — your full starting lineup and bench, a complete bye-week grid, **stacks and handcuffs** (QB-receiver pairings and same-team backup RBs for players you own), and the **roster settings** where you set your league's exact starting requirements.

**Sync** — load projections (CSV upload or Sleeper), connect a Sleeper live draft, or paste-import drafted players for ESPN/Yahoo.

---

## 10. How to draft with it

**Before the draft:**

1. Set teams, slot, rounds, scoring, and your roster settings (My team tab).
2. Projections are already loaded; if it's close to draft day, refresh with a fresh RotoBaller CSV in the Sync tab.
3. If you're on Sleeper: Sync tab → enter your username → Find leagues → pick yours → turn on **auto every 4s**. It will track the whole room for you.
4. If you're not on Sleeper, you'll mark picks by hand — that's fine, just do it *in order*.

**During a snake draft:**

1. When a player is taken, click **✕** next to him (or let Sleeper auto-sync do it).
2. When you're on the clock, read the top two or three of **Take one of these**, check the tags, glance at **My roster** for needs and **Tier cliffs** for what's about to vanish.
3. Tap **MINE** on your pick. Repeat.

**During an auction draft:**

1. Pick your philosophy in the Budget plan.
2. As players sell, hit **sold** and log the price and buyer — this powers inflation, the opponent board, and the nomination helper.
3. When a player you want is up, check his **Par**, how many rivals can outbid you, and your max bid before you commit.
4. Use the **nomination helper** on your turns to nominate — early to drain, late to force spend.

---

## 11. Reading the numbers well

- **Don't chase the single biggest number in any one column.** A huge Edge on a player who 100% lasts is not a pick — grab him later and take someone scarce now. Value and survival only mean something together.
- **Trust Edge most for RB and WR.** Those positions are deep and spread out, so their Edge tracks real value. For **QB and TE**, Edge runs noisier — those positions cluster, so small point gaps become big rank swings. Read a big QB/TE Edge as "the projections like him," not an automatic green light, and lean on the tier column there.
- **The "board: now / board: wait" flag is the smart tell** — it's the opponent-aware read overriding plain ADP.
- **The recommendation is a strong default, not an order.** When your gut overrules the #1 pick, it's usually because you're weighing something the math can't see — a player you don't trust, a position you're punting. That's the tool doing its job: giving you a fast, informed starting point so your call is better, not blind.

---

## 12. Honest limits

Worth knowing so nothing surprises you:

- **Projections are one informed opinion.** Everything downstream — Edge, auction par — is only as good as RotoBaller's numbers. If they're wrong about a player, Edge is wrong about him too. Sanity-check the eye-popping ones.
- **RotoBaller is conservative on unproven youth.** It systematically projects rookies and second-year breakout candidates below where the market drafts them. So Edge will tell you to *fade* the trendy young guys — sometimes right (rookies bust), sometimes a missed league-winner. Use your own read on those.
- **The survival model assumes opponents draft to fill needs.** A pure best-player-available drafter, or a best-ball-style room, breaks that assumption, and the model will overestimate how long "no-need" positions last. In a room full of sharks, trust the Lasts numbers a little less.
- **Sleeper sync couldn't be tested end-to-end** from where the tool was built, so the first time you use it, glance at the League tab after a couple of real picks to confirm names and teams are landing right. It falls back gracefully to manual if the connection fails.
- **Saves are per device.** Set up and draft on the same one, and don't clear your browser data mid-draft.
- **Refresh the projections** with a fresh CSV if your draft is more than a few days out.

---

## 13. Glossary

- **ADP** — Average Draft Position. Where the market drafts a player.
- **VORP** — Value Over Replacement Player. Projected points above the last startable player at the position. The true currency of value.
- **Edge** — projection value rank minus ADP rank. Positive = market is underpricing him.
- **Opp** — opportunity: projected targets (WR/TE) or touches (RB). The volume behind the projection.
- **Lasts to %** — probability a player is still available at your next pick, using opponents' real needs.
- **Tier** — a group of similar-value players at a position; a tier break is a cliff where the next guy is meaningfully worse.
- **Par** (auction) — a player's expected going price. Your ceiling, not your target.
- **Inflation** (auction) — money still in the room vs value still on the board. Above zero means everyone's paying over sticker.
- **Max bid** (auction) — the most you can spend and still fill every roster slot ($1 minimum each).

---

## 14. Troubleshooting

- **Board shows "ADP-implied" / no projections** — projections didn't load. They're baked in by default, so this only happens if a save cleared them; go to Sync and upload a CSV or Load from Sleeper.
- **Sleeper "couldn't reach" / low match count** — the live feed didn't come through. Use the CSV upload for projections, and for picks, mark them by hand or use the paste-import box (works for ESPN/Yahoo too).
- **The lineup slots don't match my league** — set your exact starters in My team → roster settings; bench auto-sizes to your round count.
- **Numbers look off for QBs or kickers** — expected. Edge is intentionally suppressed for K/DST and runs noisy for QB/TE; that's a known property, not a bug.
- **Styling looks broken / warning in console** — shouldn't happen anymore; the CSS is baked in with no CDN. If it does, make sure you're opening the latest file.
- **Lost my draft after closing** — it saves to that browser only. Reopen on the same device; avoid clearing site data.

---

*Built for the 2026 season. Projections snapshot from RotoBaller; refresh before draft day for the latest.*

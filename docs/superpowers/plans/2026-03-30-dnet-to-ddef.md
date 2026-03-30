# Replace dNET with dDEF Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate offensive double-counting by replacing dNET with dDEF (defensive rating differential) across all 6 models, then clean-backfill the 4 NBA models.

**Architecture:** The change touches 2 shared engine files (Python + JS), 2 shared self-tune files, and 6 defaults configs. After the code change, Kalman state and history are reset for 4 NBA models and backfilled from season start.

**Tech Stack:** Python, Node.js (ESM), core/model_engine.py, core-js/model_engine.mjs

**Spec:** `docs/superpowers/specs/2026-03-30-dnet-to-ddef-design.md`

---

### Task 1: Update Python core engine (`core/model_engine.py`)

**Files:**
- Modify: `core/model_engine.py:265` — base score formula
- Modify: `core/model_engine.py:291,297` — weight variance calculation
- Modify: `core/model_engine.py:328` — `extract_margin_features`
- Modify: `core/model_engine.py:506` — `_features` dict in `analyze_game`

- [ ] **Step 1: Update base score formula (line 265)**

Replace:
```python
            + _r4((W["wNET"] * 0.5) * ((t_off - t_def) - (o["OFF"] - o["DEF"])))
```
With:
```python
            + _r4(W["wDEF"] * (o["DEF"] - t_def))
```

- [ ] **Step 2: Update weight variance calculation (lines 291, 297)**

Replace:
```python
            d_net = (t_off - t_def) - (o["OFF"] - o["DEF"])
```
With:
```python
            d_def = o["DEF"] - t_def
```

Replace:
```python
                + (0.5 * d_net * pace) ** 2 * (W_var.get("wNET", 0))
```
With:
```python
                + (d_def * pace) ** 2 * (W_var.get("wDEF", 0))
```

- [ ] **Step 3: Update `extract_margin_features` (line 328)**

Replace:
```python
            "dNET": _r4(0.5 * ((home_stats["OFF"] - home_stats["DEF"]) - (away_stats["OFF"] - away_stats["DEF"])) * pace),
```
With:
```python
            "dDEF": _r4((away_stats["DEF"] - home_stats["DEF"]) * pace),
```

- [ ] **Step 4: Update `_features` dict in `analyze_game` (line 506)**

Replace:
```python
            "dNET": (h_team["OFF"] - h_team["DEF"]) - (a_team["OFF"] - a_team["DEF"]),
```
With:
```python
            "dDEF": a_team["DEF"] - h_team["DEF"],
```

- [ ] **Step 5: Commit**

```bash
git add core/model_engine.py
git commit -m "feat: replace dNET with dDEF in Python model engine to fix offensive double-counting"
```

---

### Task 2: Update JS core engine (`core-js/model_engine.mjs`)

**Files:**
- Modify: `core-js/model_engine.mjs:318` — base score formula
- Modify: `core-js/model_engine.mjs:343,349` — weight variance calculation
- Modify: `core-js/model_engine.mjs:378` — `extractMarginFeatures`
- Modify: `core-js/model_engine.mjs:498` — `_features` dict

- [ ] **Step 1: Update base score formula (line 318)**

Replace:
```javascript
      (W.wNET * 0.5) * ((tOFF - tDEF) - (o.OFF - o.DEF)) +
```
With:
```javascript
      W.wDEF * (o.DEF - tDEF) +
```

- [ ] **Step 2: Update weight variance calculation (lines 343, 349)**

Replace:
```javascript
      const dNET = (tOFF - tDEF) - (o.OFF - o.DEF);
```
With:
```javascript
      const dDEF = o.DEF - tDEF;
```

Replace:
```javascript
        (0.5 * dNET * pace) ** 2 * (W_var.wNET || 0) +
```
With:
```javascript
        (dDEF * pace) ** 2 * (W_var.wDEF || 0) +
```

- [ ] **Step 3: Update `extractMarginFeatures` (line 378)**

Replace:
```javascript
      dNET: 0.5 * ((homeStats.OFF - homeStats.DEF) - (awayStats.OFF - awayStats.DEF)) * pace,
```
With:
```javascript
      dDEF: (awayStats.DEF - homeStats.DEF) * pace,
```

- [ ] **Step 4: Update `_features` dict (line 498)**

Replace:
```javascript
      dNET: (hTeam.OFF - hTeam.DEF) - (aTeam.OFF - aTeam.DEF),
```
With:
```javascript
      dDEF: aTeam.DEF - hTeam.DEF,
```

- [ ] **Step 5: Commit**

```bash
git add core-js/model_engine.mjs
git commit -m "feat: replace dNET with dDEF in JS model engine to fix offensive double-counting"
```

---

### Task 3: Update Python self-tune (`core/self_tune.py`)

**Files:**
- Modify: `core/self_tune.py:106` — `WEIGHT_KEYS`
- Modify: `core/self_tune.py:119` — feature vector `x`

- [ ] **Step 1: Update WEIGHT_KEYS (line 106)**

Replace:
```python
    WEIGHT_KEYS = ["wTS", "wTO", "wORR", "wNET", "hca"]
```
With:
```python
    WEIGHT_KEYS = ["wTS", "wTO", "wORR", "wDEF", "hca"]
```

- [ ] **Step 2: Update feature vector with backward-compat fallback (line 119)**

Replace:
```python
        x = [mf["dTS"], mf["dTO"], mf["dORR"], mf["dNET"], mf["hca"]]
```
With:
```python
        x = [mf["dTS"], mf["dTO"], mf["dORR"], mf.get("dDEF", 0), mf["hca"]]
```

The `.get("dDEF", 0)` handles old NCAA history records that still contain `dNET` instead of `dDEF`.

- [ ] **Step 3: Commit**

```bash
git add core/self_tune.py
git commit -m "feat: rename wNET/dNET to wDEF/dDEF in Python self-tune with backward compat"
```

---

### Task 4: Update JS self-tune (`core-js/self_tune.mjs`)

**Files:**
- Modify: `core-js/self_tune.mjs:60` — `WEIGHT_KEYS`
- Modify: `core-js/self_tune.mjs:69` — feature vector `x`

- [ ] **Step 1: Update WEIGHT_KEYS (line 60)**

Replace:
```javascript
    const WEIGHT_KEYS = ["wTS", "wTO", "wORR", "wNET", "hca"];
```
With:
```javascript
    const WEIGHT_KEYS = ["wTS", "wTO", "wORR", "wDEF", "hca"];
```

- [ ] **Step 2: Update feature vector with backward-compat fallback (line 69)**

Replace:
```javascript
      const x = [mf.dTS, mf.dTO, mf.dORR, mf.dNET, mf.hca];
```
With:
```javascript
      const x = [mf.dTS, mf.dTO, mf.dORR, mf.dDEF ?? 0, mf.hca];
```

- [ ] **Step 3: Commit**

```bash
git add core-js/self_tune.mjs
git commit -m "feat: rename wNET/dNET to wDEF/dDEF in JS self-tune with backward compat"
```

---

### Task 5: Update all 6 defaults files

**Files:**
- Modify: `pyNBA/scripts/defaults.py:21,62`
- Modify: `pyFull/scripts/defaults.py:21,67`
- Modify: `pyNCAA/scripts/defaults.py:11,38`
- Modify: `jsNBA/scripts/defaults.mjs:21,62`
- Modify: `jsFull/scripts/defaults.mjs:21,67`
- Modify: `jsNCAA/scripts/defaults.mjs:11,38`

For each file, make two replacements:

- [ ] **Step 1: Update DEFAULT_W in all 6 files**

Python files — replace:
```python
    "wNET": 1,          # net rating delta weight (applied at 0.5x in projScore)
```
With:
```python
    "wDEF": 0.4,        # defensive rating delta weight
```

Note: pyNCAA has a slightly different comment (`# net rating delta weight`), same change applies.

JS files — replace:
```javascript
  wNET: 1,          // net rating delta weight (applied at 0.5x in projScore)
```
With:
```javascript
  wDEF: 0.4,        // defensive rating delta weight
```

Note: jsNCAA has a slightly different comment (`// net rating delta weight`), same change applies.

- [ ] **Step 2: Update DEFAULT_W_VAR in all 6 files**

Python files (pyNBA, pyNCAA) — replace:
```python
    "wNET": 4.0,
```
With:
```python
    "wDEF": 4.0,
```

Python files (pyFull) — replace (note extra alignment spaces):
```python
    "wNET":     4.0,
```
With:
```python
    "wDEF":     4.0,
```

JS files — replace:
```javascript
  wNET:     4.0,
```
With:
```javascript
  wDEF:     4.0,
```

Note: pyNCAA/jsNCAA have different formatting (`"wNET": 4.0,` / `wNET:     4.0,` without extra spaces). Match each file's existing style.

- [ ] **Step 3: Commit**

```bash
git add pyNBA/scripts/defaults.py pyFull/scripts/defaults.py pyNCAA/scripts/defaults.py jsNBA/scripts/defaults.mjs jsFull/scripts/defaults.mjs jsNCAA/scripts/defaults.mjs
git commit -m "feat: rename wNET to wDEF (0.4) in all 6 model defaults files"
```

---

### Task 6: Archive and reset pyNBA state, run backfill

**Files:**
- Archive: `pyNBA/data/history.json`
- Reset: `pyNBA/data/kalman_state.json`

- [ ] **Step 1: Archive history**

```bash
cd pyNBA
cp data/history.json data/history_pre_ddef_$(date +%Y%m%d).json
```

- [ ] **Step 2: Reset kalman_state.json**

Reset `adj_mean` to `0` and `adj_var` to initial defaults for all teams. The simplest approach: delete the file so the backfill script initializes fresh state.

```bash
rm data/kalman_state.json
```

- [ ] **Step 3: Clear history for fresh backfill**

```bash
echo "[]" > data/history.json
```

- [ ] **Step 4: Run backfill (92 days from 2025-12-28)**

```bash
python scripts/backfill_last_n_days.py 92
```

Expected: script processes ~92 days of games, rebuilds kalman_state.json, populates history.json with graded picks.

- [ ] **Step 5: Validate Kalman state**

After backfill, check that OKC's `adj_mean` is no longer at the bottom of the league. Read `data/kalman_state.json` and verify top teams (OKC, BOS, CLE) rank in the upper half.

- [ ] **Step 6: Commit**

```bash
git add data/history.json data/kalman_state.json
git commit -m "chore(pyNBA): reset and backfill with dDEF feature (92 days from 2025-12-28)"
```

---

### Task 7: Archive and reset pyFull state, run backfill

**Files:**
- Archive: `pyFull/data/history.json`
- Reset: `pyFull/data/kalman_state.json`

- [ ] **Step 1: Archive history**

```bash
cd pyFull
cp data/history.json data/history_pre_ddef_$(date +%Y%m%d).json
```

- [ ] **Step 2: Reset kalman_state.json and history**

```bash
rm data/kalman_state.json
echo "[]" > data/history.json
```

- [ ] **Step 3: Run backfill (159 days from 2025-10-22)**

```bash
python scripts/backfill_last_n_days.py 159
```

- [ ] **Step 4: Validate Kalman state**

Same check as Task 6 Step 5 — OKC should rank in the upper half.

- [ ] **Step 5: Commit**

```bash
git add data/history.json data/kalman_state.json
git commit -m "chore(pyFull): reset and backfill with dDEF feature (159 days from 2025-10-22)"
```

---

### Task 8: Archive and reset jsNBA state, run backfill

**Files:**
- Archive: `jsNBA/data/history.json`
- Reset: `jsNBA/data/kalman_state.json`

- [ ] **Step 1: Archive history**

```bash
cd jsNBA
cp data/history.json data/history_pre_ddef_$(date +%Y%m%d).json
```

- [ ] **Step 2: Reset kalman_state.json and history**

```bash
rm data/kalman_state.json
echo "[]" > data/history.json
```

- [ ] **Step 3: Run backfill (92 days from 2025-12-28)**

```bash
node scripts/backfill_last_n_days.mjs 92
```

- [ ] **Step 4: Validate Kalman state**

Same check — OKC should rank in the upper half.

- [ ] **Step 5: Commit**

```bash
git add data/history.json data/kalman_state.json
git commit -m "chore(jsNBA): reset and backfill with dDEF feature (92 days from 2025-12-28)"
```

---

### Task 9: Archive and reset jsFull state, run backfill

**Files:**
- Archive: `jsFull/data/history.json`
- Reset: `jsFull/data/kalman_state.json`

- [ ] **Step 1: Archive history**

```bash
cd jsFull
cp data/history.json data/history_pre_ddef_$(date +%Y%m%d).json
```

- [ ] **Step 2: Reset kalman_state.json and history**

```bash
rm data/kalman_state.json
echo "[]" > data/history.json
```

- [ ] **Step 3: Run backfill (159 days from 2025-10-22)**

```bash
node scripts/backfill_last_n_days.mjs 159
```

- [ ] **Step 4: Validate Kalman state**

Same check — OKC should rank in the upper half.

- [ ] **Step 5: Commit**

```bash
git add data/history.json data/kalman_state.json
git commit -m "chore(jsFull): reset and backfill with dDEF feature (159 days from 2025-10-22)"
```

---

### Task 10: Final validation

- [ ] **Step 1: Verify no stale dNET references remain in core files**

```bash
grep -r "wNET\|dNET" core/ core-js/ pyNBA/scripts/defaults.py pyFull/scripts/defaults.py pyNCAA/scripts/defaults.py jsNBA/scripts/defaults.mjs jsFull/scripts/defaults.mjs jsNCAA/scripts/defaults.mjs
```

Expected: zero matches. (Data files like history.json will still contain old `dNET` keys — that's fine and handled by the `.get("dDEF", 0)` fallback.)

- [ ] **Step 2: Spot-check prediction quality**

Compare a few recent game predictions from the backfilled history against actuals. Check that elite team predicted margins are more conservative than before.

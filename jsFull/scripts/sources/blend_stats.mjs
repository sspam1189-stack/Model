// scripts/sources/blend_stats.mjs
// ────────────────────────────────────────────────────────────────────────────
// Blends multiple stat windows into one stats object per game.
//
// Why: season averages miss recent form (streaks, injuries, trades).
//      Home/away splits miss location-specific performance.
//      Blending captures what matters RIGHT NOW for each team in each game.
//
// Pipeline:
//   1. blendBase(season, last10, recentWeight)  → base blended stats (team-level)
//      This goes through lineup_adjust and b2b_adjust as before.
//
//   2. blendForGame(baseStats, homeSplits, awaySplits, homeTeam, awayTeam, locWeight)
//      → game-specific stats with home/away adjustments applied per-team.
//      Called in the game loop, just before analyzeGame.
// ────────────────────────────────────────────────────────────────────────────

const STAT_KEYS = ["OFF", "DEF", "TS", "TO", "ORR", "PACE"];

function blendTeam(base, overlay, weight) {
  if (!overlay) return base;
  const out = { ...base };
  for (const k of STAT_KEYS) {
    if (Number.isFinite(overlay[k]) && Number.isFinite(base[k])) {
      out[k] = Math.round((base[k] * (1 - weight) + overlay[k] * weight) * 100) / 100;
    }
  }
  return out;
}

// ── Step 1: Season + Last 10 blend ──────────────────────────────────────────
// Creates a single stats object with recent form blended in.
// recentWeight = 0 → pure season. recentWeight = 1 → pure last 10.
// Default 0.35: research-backed starting point for NBA recent form.
// Min GP for last10: if a team played < 8 of their last 10, trust season more.

export function blendBase(season, last10, recentWeight = 0.35) {
  if (!last10 || !Object.keys(last10).length) {
    console.log("  [blend] No last-10 data — using season only");
    return { ...season };
  }

  const blended = {};
  let blendCount = 0;

  for (const [team, s] of Object.entries(season)) {
    const r = last10[team];
    if (!r || r.GP < 5) {
      // Not enough recent games — use season only
      blended[team] = { ...s };
      continue;
    }

    // Scale weight by how many of last 10 were actually played
    // If GP=10, use full weight. If GP=6, reduce proportionally.
    const gpFactor = Math.min(r.GP / 10, 1);
    const effectiveWeight = recentWeight * gpFactor;

    blended[team] = blendTeam(s, r, effectiveWeight);
    blended[team].GP = s.GP;  // keep season GP for min-games check
    blendCount++;
  }

  console.log(`  [blend] Blended ${blendCount}/${Object.keys(season).length} teams (recentWeight=${recentWeight})`);
  return blended;
}


// ── Step 2: Per-game home/away adjustment ───────────────────────────────────
// Takes the base-blended stats and adjusts the home team toward their home
// performance and the away team toward their road performance.
// Returns a shallow copy with only the two game teams modified.

export function blendForGame(baseStats, homeSplits, awaySplits, homeTeam, awayTeam, locWeight = 0.25) {
  if ((!homeSplits && !awaySplits) || locWeight <= 0) return baseStats;

  const gameStats = { ...baseStats };

  // Home team: blend toward their home splits
  if (homeSplits?.[homeTeam] && homeSplits[homeTeam].GP >= 10) {
    gameStats[homeTeam] = blendTeam(
      baseStats[homeTeam],
      homeSplits[homeTeam],
      locWeight
    );
    gameStats[homeTeam].GP = baseStats[homeTeam]?.GP;
  }

  // Away team: blend toward their road splits
  if (awaySplits?.[awayTeam] && awaySplits[awayTeam].GP >= 10) {
    gameStats[awayTeam] = blendTeam(
      baseStats[awayTeam],
      awaySplits[awayTeam],
      locWeight
    );
    gameStats[awayTeam].GP = baseStats[awayTeam]?.GP;
  }

  return gameStats;
}

import { normKey } from "./utils.mjs";

const NBA_TEAM_ALIASES = {
  "la lakers":             "Los Angeles Lakers",
  "lakers":                "Los Angeles Lakers",
  "la clippers":           "LA Clippers",
  "los angeles clippers":  "LA Clippers",
  "clippers":              "Los Angeles Clippers",
  "golden state":          "Golden State Warriors",
  "warriors":              "Golden State Warriors",
  "oklahoma city":         "Oklahoma City Thunder",
  "thunder":               "Oklahoma City Thunder",
  "new orleans":           "New Orleans Pelicans",
  "pelicans":              "New Orleans Pelicans",
  "new york":              "New York Knicks",
  "knicks":                "New York Knicks",
  "san antonio":           "San Antonio Spurs",
  "spurs":                 "San Antonio Spurs",
  "portland":              "Portland Trail Blazers",
  "trail blazers":         "Portland Trail Blazers",
  "philadelphia":          "Philadelphia 76ers",
  "76ers":                 "Philadelphia 76ers",
  "sixers":                "Philadelphia 76ers",
  "minnesota":             "Minnesota Timberwolves",
  "timberwolves":          "Minnesota Timberwolves",
  "wolves":                "Minnesota Timberwolves",
  "memphis":               "Memphis Grizzlies",
  "grizzlies":             "Memphis Grizzlies",
  "charlotte":             "Charlotte Hornets",
  "hornets":               "Charlotte Hornets",
  "indiana":               "Indiana Pacers",
  "pacers":                "Indiana Pacers",
  "washington":            "Washington Wizards",
  "wizards":               "Washington Wizards",
  "orlando":               "Orlando Magic",
  "magic":                 "Orlando Magic",
  "miami":                 "Miami Heat",
  "heat":                  "Miami Heat",
  "atlanta":               "Atlanta Hawks",
  "hawks":                 "Atlanta Hawks",
  "chicago":               "Chicago Bulls",
  "bulls":                 "Chicago Bulls",
  "detroit":               "Detroit Pistons",
  "pistons":               "Detroit Pistons",
  "cleveland":             "Cleveland Cavaliers",
  "cavaliers":             "Cleveland Cavaliers",
  "cavs":                  "Cleveland Cavaliers",
  "toronto":               "Toronto Raptors",
  "raptors":               "Toronto Raptors",
  "brooklyn":              "Brooklyn Nets",
  "nets":                  "Brooklyn Nets",
  "boston":                "Boston Celtics",
  "celtics":               "Boston Celtics",
  "milwaukee":             "Milwaukee Bucks",
  "bucks":                 "Milwaukee Bucks",
  "denver":                "Denver Nuggets",
  "nuggets":               "Denver Nuggets",
  "utah":                  "Utah Jazz",
  "jazz":                  "Utah Jazz",
  "phoenix":               "Phoenix Suns",
  "suns":                  "Phoenix Suns",
  "sacramento":            "Sacramento Kings",
  "kings":                 "Sacramento Kings",
  "dallas":                "Dallas Mavericks",
  "mavericks":             "Dallas Mavericks",
  "mavs":                  "Dallas Mavericks",
  "houston":               "Houston Rockets",
  "rockets":               "Houston Rockets",
};

function expandTeamName(name, aliases) {
  const n = normKey(name);
  return aliases[n] || name;
}

function resolveTeamNBA(H, name, aliases) {
  if (!H || !name) return null;
  if (H[name]) return name;
  const keys = Object.keys(H);
  const wanted = normKey(name);

  for (const k of keys) {
    if (normKey(k) === wanted) return k;
  }
  const expanded = normKey(expandTeamName(name, aliases));
  for (const k of keys) {
    if (normKey(k) === expanded) return k;
  }
  for (const k of keys) {
    const nk = normKey(k);
    if (nk.includes(wanted) || wanted.includes(nk) || nk.includes(expanded) || expanded.includes(nk)) return k;
  }
  return null;
}

function collapseAbbr(s) {
  return s.replace(/\./g, "");
}

function safeFuzzy(a, b) {
  if (!a || !b) return false;
  if (a === b) return true;
  const shorter = a.length < b.length ? a : b;
  const longer  = a.length < b.length ? b : a;
  if (shorter.length / longer.length < 0.85) return false;
  return longer.includes(shorter);
}

function resolveTeamNCAA(H, name, aliases) {
  if (!H || !name) return null;
  if (H[name]) return name;
  const keys = Object.keys(H);
  const wanted = normKey(name);
  const wantedCollapsed = normKey(collapseAbbr(name));

  for (const k of keys) {
    if (normKey(k) === wanted) return k;
  }
  for (const k of keys) {
    if (normKey(collapseAbbr(k)) === wantedCollapsed) return k;
  }
  const expanded = normKey(expandTeamName(name, aliases));
  for (const k of keys) {
    if (normKey(k) === expanded) return k;
  }
  for (const k of keys) {
    const nk = normKey(k);
    if (safeFuzzy(nk, wanted) || safeFuzzy(nk, expanded)) return k;
  }
  let bestPrefix = null, bestLen = 0;
  for (const k of keys) {
    const nk = normKey(k);
    const nkc = normKey(collapseAbbr(k));
    const matchNk = wanted.startsWith(nk + " ") || wantedCollapsed.startsWith(nk + " ");
    const matchNkc = wantedCollapsed.startsWith(nkc + " ");
    if ((matchNk || matchNkc) && nk.length >= 3) {
      const len = Math.max(nk.length, nkc.length);
      if (len > bestLen) { bestLen = len; bestPrefix = k; }
    }
  }
  if (bestPrefix) return bestPrefix;
  return null;
}

export function computeTeamHCA(homeSplits, awaySplits, leagueHCA = 4.0) {
  if (!homeSplits || !awaySplits) return null;
  const hcaMap = {};
  for (const team of Object.keys(homeSplits)) {
    const h = homeSplits[team];
    const a = awaySplits[team];
    if (!h || !a || !h.GP || !a.GP || h.GP < 10 || a.GP < 10) continue;
    const homeNet = h.OFF - h.DEF;
    const awayNet = a.OFF - a.DEF;
    const rawHCA = (homeNet - awayNet) / 2;
    hcaMap[team] = rawHCA * 0.5 + leagueHCA * 0.5;
  }
  return hcaMap;
}

function computeH2HAdj(homeTeam, awayTeam, h2hMatchups, W, cfg) {
  if (!h2hMatchups) return null;

  const key = [homeTeam, awayTeam].sort().join("::");
  const matchup = h2hMatchups[key];
  if (!matchup || !matchup.games || matchup.games.length === 0) return null;

  const h2hWeight = W[cfg.h2hWeightKey] ?? 0.15;
  const maxAdj = cfg.h2hMaxAdj;
  const recencyDecay = cfg.h2hRecencyDecay;

  const sorted = [...matchup.games].sort((a, b) =>
    (a.date || "").localeCompare(b.date || "")
  );

  let homeWins = 0, homeLosses = 0;
  let weightedMargin = 0, totalWeight = 0, totalMargin = 0;

  for (let i = 0; i < sorted.length; i++) {
    const g = sorted[i];
    const isActualHome = g.home.team === homeTeam;
    const ourPts = isActualHome ? g.home.pts : g.away.pts;
    const theirPts = isActualHome ? g.away.pts : g.home.pts;
    const m = ourPts - theirPts;

    if (m > 0) homeWins++;
    else if (m < 0) homeLosses++;

    totalMargin += m;
    const recency = Math.pow(recencyDecay, sorted.length - 1 - i);
    weightedMargin += m * recency;
    totalWeight += recency;
  }

  const nGames = sorted.length;
  const wAvg = weightedMargin / totalWeight;
  const gameConf = Math.min(nGames / 4, 1.0);
  const adj = Math.max(-maxAdj, Math.min(maxAdj, wAvg * h2hWeight * gameConf));

  return {
    h2hAdj: Math.round(adj * 10) / 10,
    h2hGames: nGames,
    h2hRecord: `${homeWins}-${homeLosses}`,
    h2hMargin: Math.round((totalMargin / nGames) * 10) / 10,
    h2hNote: `H2H ${homeTeam} ${homeWins}-${homeLosses} (avg ${totalMargin / nGames > 0 ? "+" : ""}${(totalMargin / nGames).toFixed(1)}, adj ${adj > 0 ? "+" : ""}${adj.toFixed(1)})`,
  };
}

function formatSpreadPick(gg, spreadSide, absLine, homeFav) {
  if (spreadSide === "home") {
    return homeFav ? `${gg.home} -${absLine}` : `${gg.home} +${absLine}`;
  }
  return homeFav ? `${gg.away} +${absLine}` : `${gg.away} -${absLine}`;
}

function lineCapOk(absLine, cap, inclusive) {
  if (cap == null) return true;
  return inclusive ? absLine <= cap : absLine < cap;
}

export function createModelEngine(options = {}) {
  const {
    DEFAULT_STATS,
    DEFAULT_W,
    DEFAULT_W_VAR,
    BAYES_HYPER,
    resolver = "nba",
    teamAliases,
    resolveTeam: resolveTeamOverride,
    minGames = 15,
    paceScoringFactor = 0.991,
    totalVarMultiplier = 1.1,
    isNeutralSite,
    enableTeamHCA = false,
    enableH2H = false,
    h2hWeightKey = "h2hWeight",
    h2hMaxAdj = 4.0,
    h2hRecencyDecay = 0.85,
    bayes = {},
    legacy = {},
  } = options;

  if (!DEFAULT_STATS || !DEFAULT_W || !DEFAULT_W_VAR || !BAYES_HYPER) {
    throw new Error("createModelEngine requires DEFAULT_STATS, DEFAULT_W, DEFAULT_W_VAR, and BAYES_HYPER");
  }

  const aliases = teamAliases || (resolver === "nba" ? NBA_TEAM_ALIASES : {});
  const resolveTeam = resolveTeamOverride
    ? (H, name) => resolveTeamOverride(H, name)
    : (resolver === "ncaa"
      ? (H, name) => resolveTeamNCAA(H, name, aliases)
      : (H, name) => resolveTeamNBA(H, name, aliases)
    );

  const bayesCfg = {
    spread: {
      mode: "probHigh",
      probKey: "probHigh",
      minProb: 0.57,
      sDiffCap: 9,
      absLineCap: 12,
      absLineCapInclusive: false,
      requireLineNonZero: false,
      favLineCap: null,
      useSDiff: true,
      threshold: 0.60,
      ...(bayes.spread || {}),
    },
    totals: {
      enabled: true,
      probKey: "probOUElite",
      minProb: 0.64,
      ...(bayes.totals || {}),
    },
  };

  const legacyCfg = {
    spread: {
      minDiffKey: "sprHigh",
      minDiffFloor: null,
      diffCap: 9,
      absLineCap: 12,
      absLineCapInclusive: false,
      ...(legacy.spread || {}),
    },
    totals: {
      enabled: true,
      minDiffKey: "ouHigh",
      eliteBumpKey: "ouEliteBump",
      eliteBumpDefault: 3,
      ...(legacy.totals || {}),
    },
  };

  function loadDefaults() {
    return { DEFAULT_STATS, DEFAULT_W, DEFAULT_W_VAR, BAYES_HYPER };
  }

  function getAvgs(H) {
    const teams = Object.values(H);
    const n = teams.length || 1;
    return {
      ts: teams.reduce((s, x) => s + x.TS, 0) / n,
      to: teams.reduce((s, x) => s + x.TO, 0) / n,
      orr: teams.reduce((s, x) => s + x.ORR, 0) / n,
    };
  }

  function normalCDF(x) {
    const a1 = 0.254829592, a2 = -0.284496736, a3 = 1.421413741;
    const a4 = -1.453152027, a5 = 1.061405429, p = 0.3275911;
    const sign = x < 0 ? -1 : 1;
    const z = Math.abs(x) / Math.SQRT2;
    const t = 1.0 / (1.0 + p * z);
    const y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-z * z);
    return 0.5 * (1.0 + sign * y);
  }

  function projScore(team, opp, isHome, H, a, W, kalmanAdj = null, W_var = null, residualVar = null, teamHCA = null) {
    const r = x => Math.round(x * 10000) / 10000;
    const tKey = resolveTeam(H, team);
    const oKey = resolveTeam(H, opp);

    const t = tKey ? H[tKey] : null;
    const o = oKey ? H[oKey] : null;
    if (!t || !o) return null;

    if ((t.GP != null && t.GP < minGames) || (o.GP != null && o.GP < minGames)) return null;

    const tOFF = t.OFF;
    const tDEF = t.DEF;

    const base =
      r((tOFF + o.DEF) / 2) +
      r((t.TS - a.ts) * W.wTS) -
      r((t.TO - a.to) * W.wTO) +
      r((t.ORR - a.orr) * W.wORR) +
      r((W.wNET * 0.5) * ((tOFF - tDEF) - (o.OFF - o.DEF))) +
      W.constant;

    const pace = Math.round(((((t.PACE + o.PACE) / 2) * W.paceAdj) / 100) * 10000) / 10000;
    const hca = isHome
      ? (enableTeamHCA && teamHCA ? (teamHCA[tKey] ?? W.hca) : W.hca)
      : 0;
    let score = Math.round(base * pace * 10) / 10 + hca;

    if (kalmanAdj) {
      score += kalmanAdj.mean;
    }

    score = Math.round(score * 10) / 10;

    let variance = residualVar != null ? residualVar / 2 : BAYES_HYPER.residualVar;

    if (kalmanAdj) {
      variance += kalmanAdj.var;
    }

    if (W_var) {
      const dTS  = t.TS - a.ts;
      const dTO  = t.TO - a.to;
      const dORR = t.ORR - a.orr;
      const dNET = (tOFF - tDEF) - (o.OFF - o.DEF);

      const weightVar =
        (dTS * pace) ** 2  * (W_var.wTS  || 0) +
        (dTO * pace) ** 2  * (W_var.wTO  || 0) +
        (dORR * pace) ** 2 * (W_var.wORR || 0) +
        (0.5 * dNET * pace) ** 2 * (W_var.wNET || 0) +
        (isHome ? 1 : 0) * (W_var.hca || 0);

      variance += weightVar;
    }

    return { score, variance };
  }

  function projTotal(homeTeam, awayTeam, H, a, W) {
    const hKey = resolveTeam(H, homeTeam);
    const aKey = resolveTeam(H, awayTeam);
    if (!hKey || !aKey) return null;

    const h = H[hKey], aw = H[aKey];
    if (!h || !aw) return null;

    const totalBase = (h.OFF + aw.DEF) / 2 + (aw.OFF + h.DEF) / 2;
    const pace = (((h.PACE + aw.PACE) / 2) * (W.paceAdj || 1)) / 100 * paceScoringFactor;

    return Math.round(totalBase * pace * 10) / 10;
  }

  function extractMarginFeatures(homeStats, awayStats, avgStats, paceAdj, neutral = false) {
    const r4 = x => Math.round(x * 10000) / 10000;
    const pace = r4(((homeStats.PACE + awayStats.PACE) / 2 * paceAdj) / 100);
    return {
      dTS:  r4((homeStats.TS - awayStats.TS) * pace),
      dTO:  r4(-(homeStats.TO - awayStats.TO) * pace),
      dORR: r4((homeStats.ORR - awayStats.ORR) * pace),
      dNET: r4(0.5 * ((homeStats.OFF - homeStats.DEF) - (awayStats.OFF - awayStats.DEF)) * pace),
      hca:  neutral ? 0.0 : 1.0,
      _baseline: r4(((homeStats.OFF + awayStats.DEF) / 2 - (awayStats.OFF + homeStats.DEF) / 2) * pace),
      _pace: pace,
    };
  }

  function analyzeGame(g, H, a, W, injuryAdj = null, kalmanState = null, W_var = null, residualVar = null, extra = null) {
    const awayKey = resolveTeam(H, g.away);
    const homeKey = resolveTeam(H, g.home);
    if (!awayKey || !homeKey) return null;

    const gg = { ...g, away: awayKey, home: homeKey };

    const neutral = isNeutralSite ? !!isNeutralSite(g) : false;
    const homeFlag = !neutral;

    let homeKalman = null, awayKalman = null;
    const getAdj = kalmanState?.teams
      ? (name) => {
          const t = kalmanState.teams[name];
          return t ? { mean: t.adj_mean, var: t.adj_var } : null;
        }
      : () => null;

    homeKalman = getAdj(homeKey);
    awayKalman = getAdj(awayKey);

    const teamHCA = enableTeamHCA ? extra : null;
    const h2hMatchups = enableH2H ? extra : null;

    const aProj = projScore(gg.away, gg.home, false, H, a, W, awayKalman, W_var, residualVar, teamHCA);
    const hProj = projScore(gg.home, gg.away, homeFlag, H, a, W, homeKalman, W_var, residualVar, teamHCA);
    if (!aProj || !hProj) return null;

    const aS = aProj.score;
    const hS = hProj.score;
    const pT = Math.round((aS + hS) * 10) / 10;

    const cleanTotal = projTotal(gg.home, gg.away, H, a, W) || pT;

    let margin = hS - aS + gg.line;

    let h2h = null;
    if (enableH2H) {
      h2h = computeH2HAdj(homeKey, awayKey, h2hMatchups, W, {
        h2hWeightKey,
        h2hMaxAdj,
        h2hRecencyDecay,
      });
      if (h2h && Number.isFinite(h2h.h2hAdj)) {
        margin += h2h.h2hAdj;
      }
    }

    const sDiff = Math.abs(margin);
    const tDiff = Math.round((pT - gg.total) * 10) / 10;
    const cleanTDiff = Math.round((cleanTotal - gg.total) * 10) / 10;

    const absLine = Math.abs(gg.line);
    const homeFav = gg.line < 0;

    const marginVar = (hProj.variance || 0) + (aProj.variance || 0);
    const marginStd = Math.sqrt(Math.max(marginVar, 1));

    const pHomeCover = normalCDF(margin / marginStd);
    const pAwayCover = 1 - pHomeCover;

    const totalVar = marginVar * totalVarMultiplier;
    const totalStd = Math.sqrt(Math.max(totalVar, 1));
    const pOver  = normalCDF(cleanTDiff / totalStd);
    const pUnder = 1 - pOver;

    let sPick = "PASS";
    let sConf = "low";
    let oPick = "PASS";
    let oConf = "low";
    let pCover = null;
    let pOU = null;

    const useBayesian = kalmanState != null && W_var != null;

    if (useBayesian) {
      const bestSpreadP = Math.max(pHomeCover, pAwayCover);
      const spreadSide = pHomeCover >= pAwayCover ? "home" : "away";

      let spreadProb = bayesCfg.spread.mode === "fixed"
        ? bayesCfg.spread.threshold
        : (W[bayesCfg.spread.probKey] ?? bayesCfg.spread.minProb);

      const pickedSideIsDog = spreadSide === "home"
        ? gg.line > 0
        : gg.line < 0;
      const favLineCap = bayesCfg.spread.favLineCap;
      const lineOK = favLineCap != null ? (pickedSideIsDog ? true : absLine <= favLineCap) : true;

      const sDiffOK = bayesCfg.spread.useSDiff ? (bayesCfg.spread.sDiffCap == null || sDiff <= bayesCfg.spread.sDiffCap) : true;
      const absLineOK = lineCapOk(absLine, bayesCfg.spread.absLineCap, bayesCfg.spread.absLineCapInclusive);
      const nonZeroOK = bayesCfg.spread.requireLineNonZero ? absLine > 0 : true;

      if (bestSpreadP >= spreadProb && lineOK && sDiffOK && absLineOK && nonZeroOK) {
        sPick = formatSpreadPick(gg, spreadSide, absLine, homeFav);
        sConf = "elite";
        pCover = bestSpreadP;
      }

      if (bayesCfg.totals.enabled) {
        const bestTotalP = Math.max(pOver, pUnder);
        const totalProb = W[bayesCfg.totals.probKey] ?? bayesCfg.totals.minProb;
        if (bestTotalP >= totalProb) {
          oPick = pOver >= pUnder ? "OVER" : "UNDER";
          oConf = "elite";
          pOU = bestTotalP;
        }
      }
    } else {
      const minDiff = W[legacyCfg.spread.minDiffKey];
      const minFloor = legacyCfg.spread.minDiffFloor;
      const sDiffOK = (minFloor == null || sDiff >= minFloor) && (minDiff == null || sDiff >= minDiff);
      const sCapOK = legacyCfg.spread.diffCap == null ? true : sDiff <= legacyCfg.spread.diffCap;
      const absLineOK = lineCapOk(absLine, legacyCfg.spread.absLineCap, legacyCfg.spread.absLineCapInclusive);

      if (sDiffOK && sCapOK && absLineOK) {
        sPick = margin > 0
          ? formatSpreadPick(gg, "home", absLine, homeFav)
          : formatSpreadPick(gg, "away", absLine, homeFav);
        sConf = "elite";
      }

      if (legacyCfg.totals.enabled && Math.abs(cleanTDiff) >= W[legacyCfg.totals.minDiffKey]) {
        const ouEliteAdj = W[legacyCfg.totals.minDiffKey] + (W[legacyCfg.totals.eliteBumpKey] ?? legacyCfg.totals.eliteBumpDefault);
        if (Math.abs(cleanTDiff) >= ouEliteAdj) {
          oPick = cleanTDiff > 0 ? "OVER" : "UNDER";
          oConf = "elite";
        }
      }
    }

    const hTeam = H[homeKey], aTeam = H[awayKey];
    const _features = {
      dTS:  hTeam.TS  - aTeam.TS,
      dTO:  hTeam.TO  - aTeam.TO,
      dORR: hTeam.ORR - aTeam.ORR,
      dNET: (hTeam.OFF - hTeam.DEF) - (aTeam.OFF - aTeam.DEF),
      avgPace: (hTeam.PACE + aTeam.PACE) / 2,
    };

    const _marginFeatures = extractMarginFeatures(hTeam, aTeam, a, W.paceAdj, neutral);

    const base = {
      ...gg,
      aS,
      hS,
      pT,
      margin: Math.round(margin * 10) / 10,
      sDiff:  Math.round(sDiff * 10) / 10,
      tDiff: cleanTDiff,
      sPick,
      sConf,
      oPick,
      oConf,
      injuryNote: injuryAdj ? buildInjuryNote(injuryAdj) : null,

      pHomeCover: Math.round(pHomeCover * 1000) / 1000,
      pAwayCover: Math.round(pAwayCover * 1000) / 1000,
      pOver:      Math.round(pOver * 1000) / 1000,
      pUnder:     Math.round(pUnder * 1000) / 1000,
      pCover:     pCover ? Math.round(pCover * 1000) / 1000 : null,
      pOU:        pOU ? Math.round(pOU * 1000) / 1000 : null,
      marginVar:  Math.round(marginVar * 10) / 10,
      marginStd:  Math.round(marginStd * 10) / 10,

      _features,
      _marginFeatures,
    };

    if (enableH2H) {
      return {
        ...base,
        h2hAdj:    h2h?.h2hAdj ?? 0,
        h2hGames:  h2h?.h2hGames ?? 0,
        h2hRecord: h2h?.h2hRecord ?? null,
        h2hNote:   h2h?.h2hNote ?? null,
      };
    }

    return base;
  }

  return {
    loadDefaults,
    getAvgs,
    normalCDF,
    projScore,
    projTotal,
    extractMarginFeatures,
    analyzeGame,
  };
}

function buildInjuryNote(injuryAdj) {
  if (!injuryAdj) return null;
  const parts = [];
  if (injuryAdj.awayInjuries?.length) parts.push(`Away: ${injuryAdj.awayInjuries.map(i => `${i.player} (${i.status}/${i.tier})`).join(", ")}`);
  if (injuryAdj.homeInjuries?.length) parts.push(`Home: ${injuryAdj.homeInjuries.map(i => `${i.player} (${i.status}/${i.tier})`).join(", ")}`);
  return parts.length ? parts.join(" | ") : null;
}

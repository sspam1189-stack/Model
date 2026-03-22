import { loadDefaults as loadDefaultsNBA, getAvgs as getAvgsNBA, analyzeGame as analyzeGameNBA } from "../../NBA/scripts/model_engine.mjs";
import { loadDefaults as loadDefaultsFull, getAvgs as getAvgsFull, analyzeGame as analyzeGameFull } from "../../NBAFullseason/scripts/model_engine.mjs";
import { loadDefaults as loadDefaultsNCAA, getAvgs as getAvgsNCAA, analyzeGame as analyzeGameNCAA } from "../../NCAA/scripts/model_engine.mjs";

function buildInputs() {
  const H = {
    Home: { GP: 20, OFF: 120, DEF: 80, TS: 0.60, TO: 0.10, ORR: 0.30, PACE: 100 },
    Away: { GP: 20, OFF: 118, DEF: 82, TS: 0.58, TO: 0.11, ORR: 0.28, PACE: 98 },
  };

  const g = { home: "Home", away: "Away", line: 5, total: 150, _date: "20240201" };

  const kalmanState = {
    teams: {
      Home: { adj_mean: 0, adj_var: 1 },
      Away: { adj_mean: 0, adj_var: 1 },
    },
  };

  const h2hMatchups = {
    "Away::Home": {
      games: [
        {
          date: "20240101",
          home: { team: "Home", pts: 120 },
          away: { team: "Away", pts: 100 },
        },
      ],
    },
  };

  return { H, g, kalmanState, h2hMatchups };
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

function testJsEngines() {
  const { H, g, kalmanState, h2hMatchups } = buildInputs();

  const nbaDefaults = loadDefaultsNBA();
  const fullDefaults = loadDefaultsFull();
  const ncaaDefaults = loadDefaultsNCAA();

  const avgsNBA = getAvgsNBA(H);
  const avgsFull = getAvgsFull(H);
  const avgsNCAA = getAvgsNCAA(H);

  const resNBA = analyzeGameNBA(g, H, avgsNBA, nbaDefaults.DEFAULT_W, null, kalmanState, nbaDefaults.DEFAULT_W_VAR, 100);
  const resFull = analyzeGameFull(g, H, avgsFull, fullDefaults.DEFAULT_W, null, kalmanState, fullDefaults.DEFAULT_W_VAR, 100, h2hMatchups);
  const resNCAA = analyzeGameNCAA(g, H, avgsNCAA, ncaaDefaults.DEFAULT_W, null, kalmanState, ncaaDefaults.DEFAULT_W_VAR, 130);

  assert(resNBA, "NBA analyzeGame returned null");
  assert(resFull, "Fullseason analyzeGame returned null");
  assert(resNCAA, "NCAA analyzeGame returned null");

  assert(resNBA.oPick === "OVER", "NBA should emit total pick");
  assert(resFull.oPick === "OVER", "Fullseason should emit total pick");
  assert(resNCAA.oPick === "PASS", "NCAA totals should be disabled");

  assert(resNBA.h2hAdj === undefined, "NBA should not include H2H fields");
  assert(resFull.h2hAdj !== 0, "Fullseason should include H2H adjustment");

  console.log("JS engine QA OK");
}

testJsEngines();

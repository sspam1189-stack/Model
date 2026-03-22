import { createNcaaEngine } from "../../core-js/engines/ncaa.mjs";
import { DEFAULT_STATS, DEFAULT_W, DEFAULT_W_VAR, BAYES_HYPER } from "./defaults.mjs";
import { isTournament } from "./sources/season_type.mjs";

const engine = createNcaaEngine({
  DEFAULT_STATS,
  DEFAULT_W,
  DEFAULT_W_VAR,
  BAYES_HYPER,
  isTournament,
});

export const {
  loadDefaults,
  getAvgs,
  normalCDF,
  projScore,
  projTotal,
  extractMarginFeatures,
  analyzeGame,
} = engine;

export { computeTeamHCA } from "../../core-js/model_engine.mjs";

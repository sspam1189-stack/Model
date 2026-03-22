import { createFullseasonEngine } from "../../core-js/engines/fullseason.mjs";
import { DEFAULT_STATS, DEFAULT_W, DEFAULT_W_VAR, BAYES_HYPER } from "./defaults.mjs";

const engine = createFullseasonEngine({
  DEFAULT_STATS,
  DEFAULT_W,
  DEFAULT_W_VAR,
  BAYES_HYPER,
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
